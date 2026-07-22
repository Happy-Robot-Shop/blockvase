from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import qrcode
from flask import Flask, jsonify, make_response, redirect, render_template, request, send_from_directory, url_for
from qrcode.image.svg import SvgImage
from waitress import serve
from werkzeug.security import check_password_hash, generate_password_hash

from .config import BASE_DIR, CONFIG_PATH, DEFAULT_CONFIG, _apply_local_rpc, ap_broadcast_ssid, load_config, save_config
from .mining_metrics import fetch_mining_metrics
from .state import StateManager


app = Flask(__name__, template_folder="../templates", static_folder="../static")
state = StateManager()
_log = logging.getLogger("blockvase")
MINING_PAYOUT_PATH = Path("/etc/blockvase/solo_mining_address")
UPDATE_STATUS_PATH = Path("/var/lib/blockvase/update-status.json")
DEVICE_UPDATE_SCRIPT = BASE_DIR / "scripts" / "device-update.sh"
ADMIN_COOKIE_NAME = "blockvase_admin"
ADMIN_COOKIE_MAX_AGE = 60 * 60 * 24 * 30
_UPDATE_STALE_RUNNING_SEC = 2 * 60 * 60
_UPDATE_SUCCESS_HOLD_SEC = 12
_UPDATE_FAILED_HOLD_SEC = 120
_UPDATE_CHECK_INTERVAL_SEC = int(os.getenv("BLOCKVASE_UPDATE_CHECK_SEC", str(30 * 60)))
_UPDATE_CHECK_FETCH_TIMEOUT_SEC = 90
_update_check_lock = threading.Lock()
_update_check_thread: threading.Thread | None = None
_update_availability: dict[str, Any] = {
    "update_available": False,
    "commits_behind": 0,
    "branch": None,
    "local_sha": None,
    "remote_sha": None,
    "checked_at": None,
    "check_error": None,
}


@app.before_request
def _start_request_timer():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.netloc != request.host:
                return _json_err("Cross-origin requests are not allowed", 403)
    # Per-request latency tracing to distinguish AP/network delays from handler time.
    request._blockvase_start = time.perf_counter()  # type: ignore[attr-defined]


@app.after_request
def _log_request_timing(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'; "
        "base-uri 'self'; form-action 'self'",
    )
    if request.path.startswith("/api/") or request.path in ("/settings", "/setup"):
        response.headers.setdefault("Cache-Control", "no-store")

    enabled = os.getenv("BLOCKVASE_REQUEST_TIMING", "false").lower() == "true"
    if not enabled:
        return response
    started = getattr(request, "_blockvase_start", None)
    if started is None:
        return response
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    print(
        f"[req] {request.method} {request.path} -> {response.status_code} "
        f"in {elapsed_ms:.1f}ms"
    )
    return response


@app.route("/media/<path:filename>")
def serve_media(filename: str):
    return send_from_directory(BASE_DIR / "media", filename)


state.start()


def _safe_device_name(name: str) -> str:
    out = "".join(c if c.isalnum() or c in "- " else "-" for c in name.strip().lower())
    out = "-".join(filter(None, out.replace(" ", "-").split("-")))
    return (out or "blockvase")[:19]


def _sync_hostname(device_name: str) -> None:
    """Set Linux hostname from device name so mDNS (hostname.local) matches the portal."""
    if os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() != "true":
        return
    safe = _safe_device_name(device_name)
    # Hostname labels are max 63 chars; device name is already capped at 19.
    try:
        result = subprocess.run(
            ["sudo", "hostnamectl", "set-hostname", safe],
            timeout=20,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            _log.warning(
                "hostnamectl set-hostname failed (rc=%s): stdout=%r stderr=%r",
                result.returncode,
                result.stdout,
                result.stderr,
            )
    except (OSError, subprocess.SubprocessError) as ex:
        _log.warning("hostnamectl: %s", ex)


def _schedule_reboot_after_save(delay_sec: float = 8.0) -> bool:
    """Reboot the device shortly after HTTP responds (Save & Reboot). Returns True if scheduled."""
    if os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() != "true":
        _log.info("reboot after save-all skipped: ENABLE_SYSTEM_ACTIONS is not true")
        return False

    def _run() -> None:
        time.sleep(delay_sec)
        try:
            subprocess.Popen(["sudo", "reboot"])
        except OSError as ex:
            _log.exception("scheduled reboot after save-all failed: %s", ex)

    threading.Thread(target=_run, daemon=True).start()
    return True


def _json_ok(**kwargs: Any):
    return jsonify({"success": True, **kwargs})


def _json_err(message: str, status: int = 400):
    return jsonify({"success": False, "error": message}), status


def _is_setup_complete(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("setup_complete"))


def _is_wifi_recovery(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("wifi_recovery"))


def _needs_setup_ui(cfg: dict[str, Any]) -> bool:
    """True for first-boot setup or soft Wi-Fi recovery (show QR / hotspot UI)."""
    return (not _is_setup_complete(cfg)) or _is_wifi_recovery(cfg)


def _parse_iso_utc(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _read_update_status() -> dict[str, Any]:
    """Public update status for portal/kiosk overlays."""
    now = time.time()
    out: dict[str, Any] = {
        "status": "idle",
        "message": "",
        "started_at": None,
        "finished_at": None,
        "updating": False,
        "show_overlay": False,
    }
    raw: Any = None
    try:
        raw = json.loads(UPDATE_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = None

    if isinstance(raw, dict):
        status = str(raw.get("status") or "idle").strip().lower()
        message = str(raw.get("message") or "")
        started_at = raw.get("started_at")
        finished_at = raw.get("finished_at")
        started_ts = _parse_iso_utc(started_at)
        finished_ts = _parse_iso_utc(finished_at)

        if status == "running" and started_ts is not None and (now - started_ts) > _UPDATE_STALE_RUNNING_SEC:
            status = "failed"
            message = message or "Update timed out. Check /var/lib/blockvase/device-update.log"
            finished_at = finished_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        show_overlay = False
        if status == "running":
            show_overlay = True
        elif status == "success" and finished_ts is not None and (now - finished_ts) < _UPDATE_SUCCESS_HOLD_SEC:
            show_overlay = True
        elif status == "failed" and finished_ts is not None and (now - finished_ts) < _UPDATE_FAILED_HOLD_SEC:
            show_overlay = True

        out.update(
            {
                "status": status,
                "message": message,
                "started_at": started_at,
                "finished_at": finished_at,
                "updating": status == "running",
                "show_overlay": show_overlay,
            }
        )

    out.update(_get_update_availability())
    if out.get("status") == "success" and out.get("update_available"):
        # Fresh pull just finished; clear indicator until the next remote check.
        _set_update_availability(update_available=False, commits_behind=0, check_error=None)
        out["update_available"] = False
        out["commits_behind"] = 0
    return out


def _git_run(*args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(BASE_DIR), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _get_update_availability() -> dict[str, Any]:
    with _update_check_lock:
        return dict(_update_availability)


def _set_update_availability(**kwargs: Any) -> None:
    with _update_check_lock:
        _update_availability.update(kwargs)


def _refresh_update_availability(*, fetch: bool = True) -> dict[str, Any]:
    """Compare local HEAD to origin/<branch>. Optionally git fetch first."""
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not (BASE_DIR / ".git").is_dir():
        _set_update_availability(
            update_available=False,
            commits_behind=0,
            branch=None,
            local_sha=None,
            remote_sha=None,
            checked_at=checked_at,
            check_error="Not a git checkout",
        )
        return _get_update_availability()

    try:
        if fetch:
            fetched = _git_run(
                "fetch",
                "--prune",
                "origin",
                timeout=_UPDATE_CHECK_FETCH_TIMEOUT_SEC,
            )
            if fetched.returncode != 0:
                err = (fetched.stderr or fetched.stdout or "git fetch failed").strip()
                _set_update_availability(
                    checked_at=checked_at,
                    check_error=err[:240],
                )
                return _get_update_availability()

        branch_p = _git_run("rev-parse", "--abbrev-ref", "HEAD")
        branch = (branch_p.stdout or "").strip()
        if branch_p.returncode != 0 or not branch or branch == "HEAD":
            _set_update_availability(
                update_available=False,
                commits_behind=0,
                branch=None,
                checked_at=checked_at,
                check_error="Detached HEAD or unknown branch",
            )
            return _get_update_availability()

        local_p = _git_run("rev-parse", "HEAD")
        remote_ref = f"origin/{branch}"
        remote_p = _git_run("rev-parse", remote_ref)
        if local_p.returncode != 0 or remote_p.returncode != 0:
            _set_update_availability(
                update_available=False,
                commits_behind=0,
                branch=branch,
                local_sha=(local_p.stdout or "").strip() or None,
                remote_sha=None,
                checked_at=checked_at,
                check_error=f"Missing remote ref {remote_ref}",
            )
            return _get_update_availability()

        local_sha = (local_p.stdout or "").strip()
        remote_sha = (remote_p.stdout or "").strip()
        behind_p = _git_run("rev-list", "--count", f"HEAD..{remote_ref}")
        try:
            commits_behind = int((behind_p.stdout or "0").strip() or "0")
        except ValueError:
            commits_behind = 0
        if behind_p.returncode != 0:
            commits_behind = 0

        _set_update_availability(
            update_available=commits_behind > 0,
            commits_behind=max(0, commits_behind),
            branch=branch,
            local_sha=local_sha,
            remote_sha=remote_sha,
            checked_at=checked_at,
            check_error=None,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        _log.warning("update availability check failed: %s", ex)
        _set_update_availability(
            checked_at=checked_at,
            check_error=str(ex)[:240],
        )
    return _get_update_availability()


def _update_check_loop() -> None:
    # Short delay so Waitress can bind before the first network fetch.
    time.sleep(15)
    while True:
        try:
            _refresh_update_availability(fetch=True)
        except Exception as ex:
            _log.warning("update check loop error: %s", ex)
        time.sleep(max(60, _UPDATE_CHECK_INTERVAL_SEC))


def _start_update_check_thread() -> None:
    global _update_check_thread
    if _update_check_thread and _update_check_thread.is_alive():
        return
    _update_check_thread = threading.Thread(
        target=_update_check_loop,
        name="blockvase-update-check",
        daemon=True,
    )
    _update_check_thread.start()


def _is_token_valid(cfg: dict[str, Any], token: str | None) -> bool:
    expected = str(cfg.get("setup_token", "") or "")
    supplied = str(token or "")
    return bool(expected and supplied and secrets.compare_digest(supplied, expected))


def _has_admin_credentials(cfg: dict[str, Any]) -> bool:
    return bool(str(cfg.get("admin_username", "") or "") and str(cfg.get("admin_password_hash", "") or ""))


def _is_admin_password_valid(cfg: dict[str, Any], username: str, password: str) -> bool:
    expected_username = str(cfg.get("admin_username", "") or "")
    password_hash = str(cfg.get("admin_password_hash", "") or "")
    if not expected_username or not password_hash or not username or not password:
        return False
    if not secrets.compare_digest(username, expected_username):
        return False
    try:
        return check_password_hash(password_hash, password)
    except ValueError:
        return False


def _validate_admin_credentials(username: str, password: str) -> str:
    if not username:
        return "Admin username is required."
    if len(username) > 64 or any(ord(ch) < 32 for ch in username):
        return "Admin username must be 64 characters or less and cannot contain control characters."
    if len(password) < 8:
        return "Admin password must be at least 8 characters."
    if len(password) > 256:
        return "Admin password must be 256 characters or less."
    return ""


def _save_admin_credentials(cfg: dict[str, Any], username: str, password: str) -> str:
    username = username.strip()
    err = _validate_admin_credentials(username, password)
    if err:
        return err
    cfg["admin_username"] = username
    cfg["admin_password_hash"] = generate_password_hash(password)
    return ""


def _set_admin_cookie(response, cfg: dict[str, Any]):
    token = str(cfg.get("setup_token", "") or "")
    if token:
        response.set_cookie(
            ADMIN_COOKIE_NAME,
            token,
            max_age=ADMIN_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
        )
    return response


def _detect_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return socket.gethostname()


def _setup_url(cfg: dict[str, Any]) -> str:
    host = "192.168.4.1" if _needs_setup_ui(cfg) else _detect_ip()
    port = os.getenv("BLOCKVASE_PORT", "80")
    return f"http://{host}:{port}/setup?token={cfg.get('setup_token', '')}"


def _ap_client_count() -> int:
    """Count currently connected AP clients. Uses iw with NetworkManager hotspot."""
    iface = os.getenv("BLOCKVASE_WLAN_IFACE", "wlan0")
    try:
        result = subprocess.run(
            ["iw", "dev", iface, "station", "dump"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if result.returncode != 0 or not result.stdout:
        return 0
    return sum(1 for ln in result.stdout.splitlines() if ln.strip().startswith("Station "))


def _current_mining_payout_address(cfg: dict[str, Any]) -> str:
    try:
        addr = MINING_PAYOUT_PATH.read_text(encoding="utf-8").strip()
        if addr:
            return addr
    except OSError:
        pass
    return str(cfg.get("mining_payout_address", "") or "")


def _request_admin_token(body: dict[str, Any] | None = None) -> str:
    body = body or {}
    return (
        request.args.get("token")
        or request.headers.get("X-Setup-Token", "")
        or request.cookies.get(ADMIN_COOKIE_NAME, "")
        or str(body.get("token", ""))
    )


def _require_admin_token(cfg: dict[str, Any], body: dict[str, Any] | None = None):
    token = _request_admin_token(body)
    if not _is_token_valid(cfg, token):
        return _json_err("Admin token required", 403)
    return None


def _theme():
    return load_config().get("theme", "default")


@app.get("/")
def index():
    cfg = load_config()
    if _needs_setup_ui(cfg):
        return redirect(url_for("display"))
    return render_template("index.html", theme=_theme())


@app.get("/settings")
def settings():
    cfg = load_config()
    if _needs_setup_ui(cfg):
        return redirect(url_for("setup_page", token=cfg.get("setup_token", "")))
    response = make_response(render_template("settings.html", theme=_theme()))
    if _is_token_valid(cfg, _request_admin_token()):
        return _set_admin_cookie(response, cfg)
    return response


@app.get("/setup")
def setup_page():
    cfg = load_config()
    token = request.args.get("token", "")
    if not _is_token_valid(cfg, token):
        return "Invalid setup token. Scan the on-device QR code.", 403
    response = make_response(render_template("settings.html", theme=_theme()))
    return _set_admin_cookie(response, cfg)


@app.get("/api/admin-auth/status")
def admin_auth_status():
    cfg = load_config()
    authenticated = _is_token_valid(cfg, _request_admin_token())
    return jsonify(
        {
            "authenticated": authenticated,
            "credentials_configured": _has_admin_credentials(cfg),
            "username": str(cfg.get("admin_username", "") or "") if authenticated else "",
        }
    )


@app.post("/api/admin-auth/login")
def admin_auth_login():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not _is_admin_password_valid(cfg, username, password):
        return _json_err("Invalid username or password", 403)
    response = _json_ok(message="Authenticated")
    return _set_admin_cookie(response, cfg)


@app.post("/api/admin-auth/credentials")
def admin_auth_credentials():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    err = _save_admin_credentials(cfg, username, password)
    if err:
        return _json_err(err)
    save_config(cfg)
    response = _json_ok(username=username, message="Admin credentials saved")
    return _set_admin_cookie(response, cfg)


@app.get("/display")
def display():
    """Device kiosk: no hover, no tooltips; canvas treemap renders the full mempool."""
    return render_template(
        "display.html",
        interactive=False,
        title="Blockvase Display",
        theme=_theme(),
        mempool_tx_limit=None,
    )


@app.get("/mempool")
def mempool():
    """Web view: hover tooltips and block highlight.

    embed=1 (portal iframe): same canvas renderer, no default transaction cap.
    """
    limit = None
    if (raw_limit := request.args.get("limit", type=int)) is not None:
        limit = max(1, min(raw_limit, 10_000))
    return render_template(
        "display.html",
        interactive=True,
        title="Blockvase Mempool",
        theme=_theme(),
        mempool_tx_limit=limit,
    )


@app.get("/api/rpc")
def get_rpc():
    """Local Bitcoin Knots only; credentials from /etc/bitcoin/bitcoin.conf (not editable in UI)."""
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    rpc = cfg["rpc"].copy()
    rpc.pop("password", None)
    rpc["host"] = "127.0.0.1"
    rpc["port"] = 8332
    rpc["use_https"] = False
    rpc["connected"] = state.get_metrics().get("connected", False)
    rpc["local_node"] = True
    return jsonify(rpc)


@app.post("/api/rpc")
def set_rpc():
    """RPC is fixed to localhost Knots; use bootstrap / install-bitcoin-knots.sh."""
    return _json_err("Bitcoin RPC is configured on this device (local Bitcoin Knots).", 405)


@app.get("/api/device-name")
def get_device_name():
    return jsonify({"name": load_config().get("device_name", DEFAULT_CONFIG["device_name"])})


@app.post("/api/device-name")
def set_device_name():
    body = request.get_json(force=True, silent=True) or {}
    name = _safe_device_name(str(body.get("name", "")))
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    try:
        cfg["device_name"] = name
        save_config(cfg)
        _sync_hostname(name)
        return _json_ok(name=name, message="Device name saved")
    except PermissionError:
        _log.exception("device-name: cannot write %s", CONFIG_PATH)
        return _json_err(
            "Cannot save (config not writable). Run: sudo chown blockvase:blockvase " + str(CONFIG_PATH),
            500,
        )
    except OSError as ex:
        _log.exception("device-name: save failed")
        return _json_err("Could not save device name: %s" % ex, 500)


@app.get("/api/theme")
def get_theme():
    return jsonify({"theme": load_config().get("theme", "default")})


@app.post("/api/theme")
def set_theme():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    theme = str(body.get("theme", "default")).lower().strip()
    if theme not in ("default", "ocean"):
        theme = "default"
    cfg["theme"] = theme
    save_config(cfg)
    return _json_ok(theme=theme, message="Theme saved")


@app.get("/api/mining-payout")
def get_mining_payout():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    return jsonify({"address": _current_mining_payout_address(cfg)})


@app.post("/api/mining-payout")
def set_mining_payout():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err

    address = str(body.get("address", "")).strip()
    if not address:
        return _json_err("Mining payout address is required.")

    if os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() != "true":
        cfg["mining_payout_address"] = address
        save_config(cfg)
        return _json_ok(
            address=address,
            message="Address saved, but mining services were not updated because system actions are disabled.",
            applied=False,
        )

    script = BASE_DIR / "scripts" / "set-mining-payout.sh"
    if not script.exists():
        return _json_err("Mining payout helper is not installed.", 500)

    try:
        result = subprocess.run(
            ["sudo", str(script), address],
            timeout=60,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        _log.exception("set-mining-payout failed")
        return _json_err(f"Could not update mining payout address: {ex}", 500)

    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "Mining payout helper failed.").strip()
        return _json_err(msg[-500:], 400)

    cfg["mining_payout_address"] = address
    save_config(cfg)
    return _json_ok(address=address, message="Mining payout address saved.", applied=True)


@app.get("/api/mining-simulation")
def get_mining_simulation():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    return jsonify({"mining_simulation_enabled": bool(cfg.get("mining_simulation_enabled"))})


@app.post("/api/mining-simulation")
def set_mining_simulation():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err

    enabled = body.get("mining_simulation_enabled")
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
    if not isinstance(enabled, bool):
        return _json_err("mining_simulation_enabled must be true or false", 400)

    cfg["mining_simulation_enabled"] = enabled
    try:
        save_config(cfg)
    except PermissionError:
        _log.exception("mining-simulation: cannot write %s", CONFIG_PATH)
        return _json_err(
            "Cannot save (config not writable). Run: sudo chown blockvase:blockvase " + str(CONFIG_PATH),
            500,
        )
    except OSError as ex:
        _log.exception("mining-simulation: save failed")
        return _json_err("Could not save mining simulation setting: %s" % ex, 500)

    refresh_script = BASE_DIR / "scripts/blockvase-miner-refresh-env.sh"
    miner_refreshed = False
    refresh_error = ""
    actions_on = os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() == "true"
    if actions_on and refresh_script.is_file():
        try:
            result = subprocess.run(
                ["sudo", "-n", str(refresh_script), "--restart-miner"],
                timeout=120,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            miner_refreshed = result.returncode == 0
            if result.returncode != 0:
                refresh_error = (result.stderr or result.stdout or "").strip()
                _log.warning(
                    "blockvase-miner-refresh-env failed rc=%s stderr=%s",
                    result.returncode,
                    refresh_error[:400],
                )
        except (OSError, subprocess.SubprocessError) as ex:
            refresh_error = str(ex)
            _log.warning("Could not refresh miner env: %s", ex)

    msg = "Mining simulation setting saved."
    if enabled and not (cfg.get("mining_payout_address") or "").strip():
        msg += " Save a Bitcoin payout address too; hashing starts once a payout is configured."
    if actions_on:
        if miner_refreshed:
            msg += " blockvase-miner.service was restarted with simulate or production YAML."
        elif not refresh_script.is_file():
            msg += " (blockvase-miner-refresh-env.sh not found, miner env was not updated)."
        elif "password" in refresh_error.lower() or "sudo" in refresh_error.lower():
            msg += (
                " passwordless sudo is not installed for the miner refresh helper. "
                "Re-run scripts/bootstrap.sh or install /etc/sudoers.d/blockvase-miner-env, then save again."
            )
        else:
            detail = (" " + refresh_error[:180]) if refresh_error else ""
            msg += " systemd refresh failed: check journalctl and run the refresh script manually." + detail
    else:
        msg += " ENABLE_SYSTEM_ACTIONS is not true, restart blockvase-miner manually to apply miner.yml."

    return _json_ok(
        mining_simulation_enabled=enabled,
        miner_service_reconfigured=miner_refreshed,
        refresh_error=refresh_error,
        message=msg,
    )


@app.get("/api/display-offset")
def get_display_offset():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    return jsonify({"display_offset_x": int(cfg.get("display_offset_x", 0))})


@app.post("/api/display-offset")
def set_display_offset():
    body = request.get_json(force=True, silent=True) or {}
    try:
        offset = int(body.get("display_offset_x", 0))
    except (TypeError, ValueError):
        return _json_err("display_offset_x must be an integer")
    if not -200 <= offset <= 200:
        return _json_err("display_offset_x must be between -200 and 200")
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    cfg["display_offset_x"] = offset
    save_config(cfg)
    return _json_ok(message="Display offset saved")


@app.get("/api/wifi")
def get_wifi():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    return jsonify({"ssid": cfg.get("wifi_ssid", ""), "has_password": bool(cfg.get("wifi_password", ""))})


@app.post("/api/save-all")
def save_all():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err

    try:
        cfg["device_name"] = _safe_device_name(
            str(body.get("deviceName", cfg.get("device_name") or "blockvase"))
        )
        cfg["wifi_ssid"] = str(body.get("ssid", cfg.get("wifi_ssid", "")))
        if body.get("password") is not None:
            cfg["wifi_password"] = str(body.get("password", ""))
        home_wifi = bool((cfg.get("wifi_ssid") or "").strip())
        admin_username = str(body.get("adminUsername", "")).strip()
        admin_password = str(body.get("adminPassword", ""))
        if admin_username or admin_password:
            err = _save_admin_credentials(cfg, admin_username, admin_password)
            if err:
                return _json_err(err)
        elif not _is_setup_complete(cfg) and home_wifi and not _has_admin_credentials(cfg):
            return _json_err("Set an admin username and password before completing Wi-Fi setup.")
        # Bitcoin Knots JSON-RPC is localhost-only; credentials come from /etc/bitcoin/bitcoin.conf (bootstrap).
        _apply_local_rpc(cfg)
        # Setup complete = saved home Wi-Fi SSID; Bitcoin node is local (no RPC fields in UI).
        if home_wifi:
            cfg["setup_complete"] = True
            cfg["wifi_recovery"] = False

        save_config(cfg)
        _sync_hostname(cfg["device_name"])

        reboot_scheduled = False
        if cfg.get("setup_complete"):
            # Switch Wi-Fi first, then reboot. A fixed 8s reboot was racing the
            # NetworkManager handoff and left clones with setup_complete=true,
            # no AP, and no working client profile.
            def _switch_then_reboot() -> None:
                time.sleep(2)  # let client receive HTTP response before AP goes down
                ap_script = BASE_DIR / "scripts" / "ap-mode.sh"
                ok = False
                if ap_script.exists():
                    try:
                        result = subprocess.run(
                            ["sudo", str(ap_script), "ensure"],
                            timeout=180,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                        )
                        ok = result.returncode == 0
                        if not ok:
                            _log.error(
                                "ap-mode ensure failed (rc=%s): stdout=%r stderr=%r",
                                result.returncode,
                                result.stdout,
                                result.stderr,
                            )
                    except (subprocess.TimeoutExpired, OSError) as ex:
                        _log.exception("ap-mode ensure: %s", ex)
                if not ok:
                    # Soft recovery: keep credentials, show setup QR, do not reboot.
                    try:
                        cfg_retry = load_config()
                        if (cfg_retry.get("wifi_ssid") or "").strip():
                            cfg_retry["wifi_recovery"] = True
                            # Keep setup_complete so reconnect / later save can leave recovery.
                        else:
                            cfg_retry["setup_complete"] = False
                            cfg_retry["wifi_recovery"] = False
                        save_config(cfg_retry)
                        if ap_script.exists():
                            subprocess.run(
                                ["sudo", str(ap_script), "ensure"],
                                timeout=180,
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                check=False,
                            )
                    except (OSError, subprocess.SubprocessError) as ex:
                        _log.exception("failed to enter soft recovery after wifi error: %s", ex)
                    return
                if os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() != "true":
                    _log.info("reboot after wifi switch skipped: ENABLE_SYSTEM_ACTIONS is not true")
                    return
                time.sleep(2)
                try:
                    subprocess.Popen(["sudo", "reboot"])
                except OSError as ex:
                    _log.exception("scheduled reboot after wifi switch failed: %s", ex)

            threading.Thread(target=_switch_then_reboot, daemon=True).start()
            reboot_scheduled = os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() == "true"
        else:
            reboot_scheduled = _schedule_reboot_after_save(8.0)
        return _json_ok(
            message="Settings saved. Switching to Wi-Fi..." if cfg.get("setup_complete") else "Settings saved.",
            deviceName=cfg["device_name"],
            rebootScheduled=reboot_scheduled,
        )
    except PermissionError:
        _log.exception("save-all: cannot write %s", CONFIG_PATH)
        return _json_err(
            "Cannot save settings (config file not writable by the web service). "
            "SSH into the Pi and run: sudo chown blockvase:blockvase "
            + str(CONFIG_PATH),
            500,
        )
    except OSError as ex:
        _log.exception("save-all: OS error writing config")
        return _json_err("Could not save settings: %s" % ex, 500)
    except Exception:
        _log.exception("save-all failed")
        return _json_err("Could not save settings (internal error). Check journalctl -u blockvase.service.", 500)


@app.get("/api/blockchain-info")
def blockchain_info():
    return jsonify(state.get_metrics())


@app.get("/api/mining")
def mining_stats():
    """PiAxe-miner REST metrics (graceful zeros if unreachable). Dashboard adds hardware_simulated from Settings."""
    cfg = load_config()
    data = fetch_mining_metrics()
    data["hardware_simulated"] = bool(cfg.get("mining_simulation_enabled"))
    data["payout_configured"] = bool(_current_mining_payout_address(cfg).strip())
    return jsonify(data)


@app.get("/api/display-sync")
def display_sync():
    """Live RPC snapshot for /display IBD overlay (not cached poller state)."""
    cfg = load_config()
    rpc_cfg = cfg.get("rpc", {})
    if not rpc_cfg.get("host") or not rpc_cfg.get("user"):
        return jsonify({"connected": False})
    try:
        return jsonify(state.rpc.get_sync_snapshot(rpc_cfg))
    except Exception:
        return jsonify({"connected": False})


@app.get("/api/mempool-txs")
def mempool_txs():
    cfg = load_config()
    rpc_cfg = cfg.get("rpc", {})
    if not rpc_cfg.get("host") or not rpc_cfg.get("user"):
        return jsonify({"connected": False, "txs": []})
    try:
        limit = request.args.get("limit", type=int)
        if limit is not None:
            limit = max(1, min(limit, 10_000))
        txs = state.rpc.get_mempool_txs(rpc_cfg, limit=limit)
        # Fetch chain status fresh so mempool confirmation animation stays in sync
        # and can distinguish live blocks from rapid IBD/catch-up height changes.
        cached = state.get_metrics()
        block_height = int(cached.get("blocks", 0) or 0)
        chain_info = None
        try:
            chain_info = state.rpc.call(rpc_cfg, "getblockchaininfo")
            block_height = int(chain_info.get("blocks", block_height) or block_height)
        except Exception:
            pass
        headers = block_height
        # Conservative until chain_info confirms: avoids kiosk "Mempool empty" when
        # getblockchaininfo times out under heavy sync load.
        initialblockdownload = True
        verificationprogress = float(cached.get("verificationprogress", 0) or 0)
        if isinstance(chain_info, dict):
            headers = int(chain_info.get("headers", block_height) or block_height)
            initialblockdownload = bool(chain_info.get("initialblockdownload", False))
            verificationprogress = float(chain_info.get("verificationprogress", verificationprogress) or 0)
        simulated = state.consume_simulated_block()
        try:
            mining = fetch_mining_metrics(timeout_override=0.2)
            miner_blocks = int(mining.get("total_blocks_found") or mining.get("blocks_found") or 0)
        except Exception:
            miner_blocks = None
        miner_block = state.consume_miner_block_event(miner_blocks)
        return jsonify(
            {
                "connected": True,
                "txs": txs,
                "blocks": block_height,
                "headers": headers,
                "initialblockdownload": initialblockdownload,
                "verificationprogress": verificationprogress,
                "simulated_block": simulated,
                "miner_block": miner_block,
            }
        )
    except Exception:
        return jsonify({"connected": False, "txs": []})


@app.get("/api/tx/<txid>")
def tx_details(txid: str):
    """Fetch decoded transaction for inputs/outputs graph."""
    cfg = load_config()
    rpc_cfg = cfg.get("rpc", {})
    if not rpc_cfg.get("host") or not rpc_cfg.get("user"):
        return jsonify({"error": "RPC not configured"}), 400
    tx = state.rpc.get_tx_details(rpc_cfg, txid)
    if not tx:
        return jsonify({"error": "Transaction not found"}), 404
    return jsonify(tx)


def _get_free_memory() -> str:
    """Return free memory string (Pi equivalent of ESP32 free heap)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    mb = kb // 1024
                    return f"{mb} MB"
    except (OSError, ValueError):
        pass
    return "N/A"


@app.get("/api/stats")
def stats():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    m = state.get_metrics()
    ip_or_host = _detect_ip() if _is_setup_complete(cfg) else socket.gethostname()
    recent_blocks = m.get("recent_blocks", [])
    largest_block = 0
    if isinstance(recent_blocks, list):
        for block in recent_blocks:
            if isinstance(block, dict):
                try:
                    largest_block = max(largest_block, int(block.get("size", 0) or 0))
                except (TypeError, ValueError):
                    continue
    return jsonify(
        {
            "uptime": f"{int(time.monotonic() // 60)} min",
            "freeHeap": _get_free_memory(),
            "largestBlock": largest_block,
            "wifiStatus": (
                "Wi-Fi recovery (setup AP)"
                if _is_wifi_recovery(cfg)
                else ("AP setup mode" if not _is_setup_complete(cfg) else "Wi-Fi client mode")
            ),
            "ipAddress": ip_or_host,
            "bitcoinNode": "Connected" if m.get("connected") else "Disconnected",
            "nodeVersion": (m.get("node_version") or "").strip(),
            "rpcNode": f"{cfg['rpc']['host']}:{cfg['rpc']['port']}" if cfg["rpc"].get("host") else "-",
            "rpcConnected": m.get("connected", False),
            "rpcStatusCode": m.get("rpc_status_code"),
            "rpcErrorBody": m.get("rpc_error_body"),
            "blockHeight": m.get("blocks", 0),
            "blocksFound": m.get("blocks_found", 0),
        }
    )


@app.post("/api/simulate-block")
def simulate_block():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    state.mark_block_event()
    return _json_ok(message="Simulated block event")


@app.post("/api/simulate-miner-block")
def simulate_miner_block():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    state.mark_miner_block_event()
    return _json_ok(message="Simulated miner block event")


@app.post("/api/reboot")
def reboot():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    if os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() != "true":
        return _json_err("System actions disabled (set ENABLE_SYSTEM_ACTIONS=true)")
    subprocess.Popen(["sudo", "reboot"])
    return _json_ok(message="Reboot requested")


@app.post("/api/factory-reset")
def factory_reset():
    cfg_before = load_config()
    token_err = _require_admin_token(cfg_before)
    if token_err:
        return token_err
    if os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() != "true":
        return _json_err(
            "Factory reset requires ENABLE_SYSTEM_ACTIONS=true (and sudoers for reboot)"
        )
    preserved_rpc = json.loads(json.dumps(cfg_before.get("rpc", {})))
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["setup_token"] = ""
    cfg["mining_payout_address"] = ""
    cfg["mining_simulation_enabled"] = False
    cfg["rpc"] = json.loads(json.dumps(DEFAULT_CONFIG["rpc"]))
    cfg["rpc"].update(preserved_rpc)
    _apply_local_rpc(cfg)
    save_config(cfg)
    ap_script = BASE_DIR / "scripts" / "ap-mode.sh"
    if ap_script.exists():
        try:
            subprocess.run(
                ["sudo", str(ap_script), "after-factory-reset"],
                timeout=120,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as ex:
            _log.exception("factory-reset mining/wifi cleanup failed: %s", ex)
    subprocess.Popen(["sudo", "reboot"])
    return _json_ok(message="Factory reset complete. Rebooting...")


@app.get("/api/device-update")
def device_update_status():
    # Settings can request a fresh fetch without blocking the response.
    if str(request.args.get("refresh", "")).lower() in {"1", "true", "yes"}:
        threading.Thread(
            target=_refresh_update_availability,
            kwargs={"fetch": True},
            name="blockvase-update-check-once",
            daemon=True,
        ).start()
    return jsonify(_read_update_status())


@app.post("/api/device-update")
def device_update_start():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    if os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() != "true":
        return _json_err("System actions disabled (set ENABLE_SYSTEM_ACTIONS=true)")
    if not DEVICE_UPDATE_SCRIPT.is_file():
        return _json_err("device-update.sh is missing", 500)
    current = _read_update_status()
    if current.get("status") == "running":
        return _json_err("Device update already in progress", 409)
    try:
        proc = subprocess.Popen(
            ["sudo", "-n", str(DEVICE_UPDATE_SCRIPT)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as ex:
        _log.exception("device-update start failed: %s", ex)
        return _json_err(f"Could not start update: {ex}", 500)

    deadline = time.time() + 4.0
    while time.time() < deadline:
        st = _read_update_status()
        if st.get("status") == "running":
            return _json_ok(
                message="Device update started",
                status="running",
                updating=True,
                show_overlay=True,
            )
        if st.get("status") == "failed":
            return _json_err(st.get("message") or "Device update failed", 500)
        rc = proc.poll()
        if rc is not None:
            err_txt = ""
            try:
                err_txt = (proc.stderr.read() if proc.stderr else "") or ""
            except OSError:
                err_txt = ""
            hint = (err_txt or "").strip()
            if rc != 0 and ("password" in hint.lower() or "a password is required" in hint.lower() or not hint):
                return _json_err(
                    "Could not start update (sudoers for device-update.sh missing). "
                    "Re-run scripts/bootstrap.sh once, then retry.",
                    500,
                )
            return _json_err(hint or f"device-update.sh exited with code {rc}", 500)
        time.sleep(0.2)

    if proc.poll() is None:
        return _json_ok(
            message="Device update started",
            status="running",
            updating=True,
            show_overlay=True,
        )
    return _json_err("Device update did not report running state", 500)


@app.get("/api/ap-mode")
def ap_mode():
    cfg = load_config()
    return jsonify(
        {
            "ap_mode": _needs_setup_ui(cfg),
            "wifi_recovery": _is_wifi_recovery(cfg),
            "setup_complete": _is_setup_complete(cfg),
        }
    )


@app.get("/api/validate-qr-token")
def validate_qr():
    cfg = load_config()
    token = request.args.get("token", "")
    return jsonify({"valid": _is_token_valid(cfg, token)})


@app.get("/api/setup-status")
def setup_status():
    cfg = load_config()
    setup_complete = _is_setup_complete(cfg)
    wifi_recovery = _is_wifi_recovery(cfg)
    show_setup = _needs_setup_ui(cfg)
    update = _read_update_status()
    return jsonify(
        {
            "setup_complete": setup_complete and not wifi_recovery,
            "configured": setup_complete,
            "wifi_recovery": wifi_recovery,
            "show_setup": show_setup,
            "setup_url": _setup_url(cfg) if show_setup else "",
            "device_name": cfg.get("device_name", "blockvase"),
            "ap_mode": show_setup,
            "update": update,
            "updating": bool(update.get("updating")),
            "update_show_overlay": bool(update.get("show_overlay")),
        }
    )


@app.get("/api/ap-info")
def ap_info():
    cfg = load_config()
    show_setup = _needs_setup_ui(cfg)
    ap_ssid = ap_broadcast_ssid(cfg)
    ap_password = "blockvase1234"
    wifi_qr_payload = f"WIFI:T:WPA;S:{ap_ssid};P:{ap_password};;"
    return jsonify(
        {
            "ap_mode": show_setup,
            "wifi_recovery": _is_wifi_recovery(cfg),
            "ssid": ap_ssid,
            "password": ap_password,
            "settings_url": _setup_url(cfg) if show_setup else "",
            "wifi_qr_payload": wifi_qr_payload,
            "ap_clients": _ap_client_count(),
        }
    )


@app.get("/api/setup-qr.svg")
def setup_qr():
    cfg = load_config()
    # Allow QR during soft Wi-Fi recovery even though setup_complete stays true.
    if not _needs_setup_ui(cfg) and not _is_token_valid(cfg, _request_admin_token()):
        return "Setup QR unavailable after setup.", 403
    kind = request.args.get("kind", "settings")
    if kind == "connect":
        ap_ssid = ap_broadcast_ssid(cfg)
        ap_password = "blockvase1234"
        payload = f"WIFI:T:WPA;S:{ap_ssid};P:{ap_password};;"
    else:
        payload = _setup_url(cfg)

    img = qrcode.make(payload, image_factory=SvgImage)
    stream = BytesIO()
    img.save(stream)
    response = make_response(stream.getvalue())
    response.headers["Content-Type"] = "image/svg+xml"
    return response


_start_update_check_thread()


if __name__ == "__main__":
    host = os.getenv("BLOCKVASE_HOST", "0.0.0.0")
    port = int(os.getenv("BLOCKVASE_PORT", "80"))
    serve(app, host=host, port=port)

