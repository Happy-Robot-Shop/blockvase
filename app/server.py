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
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import qrcode
from flask import Flask, jsonify, make_response, redirect, render_template, request, send_from_directory, url_for
from qrcode.image.svg import SvgImage
from waitress import serve
from werkzeug.security import check_password_hash, generate_password_hash

from .config import (
    BASE_DIR,
    CONFIG_PATH,
    DEFAULT_CONFIG,
    _apply_local_rpc,
    ap_broadcast_ssid,
    generate_ap_password,
    load_config,
    save_config,
    seal_secret,
    unseal_secret,
)
from .mining_metrics import fetch_mining_metrics
from .mining_wallet import (
    address_is_legacy_mining_mine,
    address_is_mine,
    address_is_spend_mine,
    export_spend_wallet_descriptors,
    format_btc_decimal,
    legacy_mining_wallet_balances,
    new_mining_payout_address,
    new_spend_receive_address,
    node_sync_status,
    parse_btc_amount,
    send_from_spend_wallet,
    spend_wallet_balances,
    spend_wallet_recent_transactions,
    validate_bitcoin_address,
)
from .state import StateManager
from .tls_cert import read_cert_pem, tls_status
from .totp import generate_secret, otpauth_uri, verify_totp


app = Flask(__name__, template_folder="../templates", static_folder="../static")
app.config["TEMPLATES_AUTO_RELOAD"] = True
state = StateManager()
_log = logging.getLogger("blockvase")
if not _log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s blockvase: %(message)s"))
    _log.addHandler(_handler)
    _log.setLevel(logging.INFO)
    _log.propagate = False
REBOOT_BIN = "/usr/sbin/reboot"
MINING_PAYOUT_PATH = Path("/etc/blockvase/solo_mining_address")
UPDATE_STATUS_PATH = Path("/var/lib/blockvase/update-status.json")
# Prefer root-owned install path from bootstrap (NOPASSWD targets); fall back to repo copy.
_LIB_DIR = Path("/usr/lib/blockvase")
DEVICE_UPDATE_SCRIPT = (
    _LIB_DIR / "device-update.sh"
    if (_LIB_DIR / "device-update.sh").is_file()
    else BASE_DIR / "scripts" / "device-update.sh"
)
ADMIN_COOKIE_NAME = "blockvase_admin"
ADMIN_COOKIE_MAX_AGE = 60 * 60 * 24 * 30
# Login / step-up brute-force protection (in-process; resets on portal restart).
_AUTH_FAIL_WINDOW_SEC = 300
_AUTH_FAIL_MAX = 8
_AUTH_LOCKOUT_SEC = 300
_auth_fail_lock = threading.Lock()
_auth_failures: dict[str, list[float]] = {}


def _privileged_script(name: str) -> Path:
    lib = _LIB_DIR / name
    if lib.is_file():
        return lib
    return BASE_DIR / "scripts" / name
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
# Short-lived password-OK tokens waiting for TOTP (in-memory only).
_pending_2fa_lock = threading.Lock()
_pending_2fa: dict[str, dict[str, Any]] = {}
_PENDING_2FA_TTL_SEC = 5 * 60
_mining_payout_ensure_lock = threading.Lock()


def _normalize_http_host(host: str | None) -> str:
    """Lowercase host and strip default ports so localhost vs localhost:80 match.

    Also normalizes IPv6 literals: [::1]:80 → [::1].
    """
    h = (host or "").strip().lower()
    if h.startswith("["):
        # [::1]:80 / [2001:db8::1]:443
        if h.endswith("]:80"):
            return h[:-3]
        if h.endswith("]:443"):
            return h[:-4]
        return h
    if h.endswith(":80"):
        return h[:-3]
    if h.endswith(":443"):
        return h[:-4]
    return h


def _request_origin_host() -> str | None:
    origin = request.headers.get("Origin")
    if origin:
        return urlparse(origin).netloc or None
    referer = request.headers.get("Referer")
    if referer:
        return urlparse(referer).netloc or None
    return None


def _https_redirect_exempt(path: str) -> bool:
    """Paths that must stay reachable over HTTP when prefer-HTTPS is on (cert recovery)."""
    if path.startswith("/api/tls") or path.startswith("/api/admin-auth"):
        return True
    # Settings/setup over HTTP so users can turn redirect off if a client has not trusted the cert.
    if path in {"/settings", "/setup"} or path.startswith("/static/") or path.startswith("/media/"):
        return True
    return False


@app.before_request
def _start_request_timer():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        # Require same-origin Origin or Referer on mutating requests (CSRF).
        peer = _normalize_http_host(_request_origin_host())
        host = _normalize_http_host(request.host)
        if not peer or peer != host:
            return _json_err("Cross-origin requests are not allowed", 403)
    # Opt-in HTTP→HTTPS redirect (Settings). Never redirect TLS recovery endpoints.
    if request.method == "GET" and not request.is_secure and not _https_redirect_exempt(request.path):
        cfg = load_config()
        if bool(cfg.get("https_redirect")) and tls_status(cfg).get("tls_ready"):
            host = request.host.split(":")[0] or "localhost"
            target = f"https://{host}{request.full_path}"
            if target.endswith("?"):
                target = target[:-1]
            return redirect(target, code=302)
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


def _ensure_portal_tls(device_name: str | None = None, *, force: bool = False) -> None:
    """Create/refresh device TLS cert + nginx site via root-owned helper."""
    if os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() != "true":
        return
    script = _privileged_script("ensure-portal-tls.sh")
    if not script.is_file():
        return
    cmd = ["sudo", "-n", str(script)]
    if force:
        cmd.append("--force")
    if device_name:
        cmd.extend(["--hostname", _safe_device_name(device_name)])
    try:
        result = subprocess.run(
            cmd,
            timeout=60,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            _log.warning(
                "ensure-portal-tls failed (rc=%s): stdout=%r stderr=%r",
                result.returncode,
                result.stdout,
                result.stderr,
            )
    except (subprocess.TimeoutExpired, OSError) as ex:
        _log.warning("ensure-portal-tls error: %s", ex)


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
    # Reissue portal cert SANs for <name>.local when hostname changes.
    _ensure_portal_tls(safe, force=False)


def _invoke_reboot() -> None:
    """Request an immediate reboot via the NOPASSWD sudoers path."""
    try:
        subprocess.Popen(["sudo", REBOOT_BIN], start_new_session=True)
        _log.info("reboot requested via sudo %s", REBOOT_BIN)
    except OSError as ex:
        _log.exception("reboot request failed: %s", ex)
        raise


def _schedule_reboot_after_save(delay_sec: float = 8.0) -> bool:
    """Reboot the device shortly after HTTP responds (Save & Reboot). Returns True if scheduled."""
    if os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() != "true":
        _log.info("reboot after save-all skipped: ENABLE_SYSTEM_ACTIONS is not true")
        return False

    def _run() -> None:
        time.sleep(delay_sec)
        try:
            _invoke_reboot()
        except OSError:
            pass

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


def _constant_time_equal(a: str, b: str) -> bool:
    """Length-safe compare_digest wrapper (mismatched lengths → False, not ValueError)."""
    if not a or not b:
        return False
    if len(a) != len(b):
        # Compare against self-length digest material so timing stays flat-ish.
        secrets.compare_digest(a, a)
        return False
    return secrets.compare_digest(a, b)


def _is_token_valid(cfg: dict[str, Any], token: str | None) -> bool:
    supplied = str(token or "")
    if not supplied:
        return False
    expected_session = str(cfg.get("session_token", "") or "")
    if expected_session and _constant_time_equal(supplied, expected_session):
        return True
    # setup_token is admin-equivalent only during first-boot / Wi-Fi recovery UI.
    if _needs_setup_ui(cfg):
        expected_setup = str(cfg.get("setup_token", "") or "")
        if expected_setup and _constant_time_equal(supplied, expected_setup):
            return True
    return False


def _auth_client_key() -> str:
    return str(request.remote_addr or "unknown")


def _auth_fail_bucket(key: str) -> list[float]:
    now = time.time()
    cutoff = now - max(_AUTH_FAIL_WINDOW_SEC, _AUTH_LOCKOUT_SEC)
    with _auth_fail_lock:
        stamps = [t for t in _auth_failures.get(key, []) if t >= cutoff]
        if stamps:
            _auth_failures[key] = stamps
        else:
            _auth_failures.pop(key, None)
        return list(stamps)


def _auth_rate_limited(key: str | None = None):
    """Return a 429 (response, status) when the client is locked out; otherwise None."""
    client = key or _auth_client_key()
    stamps = _auth_fail_bucket(client)
    if len(stamps) < _AUTH_FAIL_MAX:
        return None
    oldest_in_burst = min(stamps[-_AUTH_FAIL_MAX:])
    retry_after = int(max(1, _AUTH_LOCKOUT_SEC - (time.time() - oldest_in_burst)))
    response = jsonify(
        {
            "success": False,
            "error": f"Too many failed authentication attempts. Try again in {retry_after}s.",
        }
    )
    response.headers["Retry-After"] = str(retry_after)
    return response, 429


def _record_auth_failure(key: str | None = None) -> None:
    client = key or _auth_client_key()
    now = time.time()
    with _auth_fail_lock:
        stamps = [t for t in _auth_failures.get(client, []) if t >= now - max(_AUTH_FAIL_WINDOW_SEC, _AUTH_LOCKOUT_SEC)]
        stamps.append(now)
        _auth_failures[client] = stamps


def _clear_auth_failures(key: str | None = None) -> None:
    client = key or _auth_client_key()
    with _auth_fail_lock:
        _auth_failures.pop(client, None)


def _has_admin_credentials(cfg: dict[str, Any]) -> bool:
    return bool(str(cfg.get("admin_username", "") or "") and str(cfg.get("admin_password_hash", "") or ""))


def _is_admin_password_valid(cfg: dict[str, Any], username: str, password: str) -> bool:
    expected_username = str(cfg.get("admin_username", "") or "")
    password_hash = str(cfg.get("admin_password_hash", "") or "")
    if not expected_username or not password_hash or not username or not password:
        return False
    if not _constant_time_equal(username, expected_username):
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


def _issue_session_token(cfg: dict[str, Any]) -> str:
    token = secrets.token_urlsafe(32)
    cfg["session_token"] = token
    return token


def _totp_enabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("totp_enabled")) and bool(unseal_secret(str(cfg.get("totp_secret_enc", "") or "")))


def _totp_secret(cfg: dict[str, Any]) -> str:
    return unseal_secret(str(cfg.get("totp_secret_enc", "") or ""))


def _purge_pending_2fa() -> None:
    now = time.time()
    with _pending_2fa_lock:
        dead = [k for k, v in _pending_2fa.items() if float(v.get("exp", 0)) < now]
        for k in dead:
            _pending_2fa.pop(k, None)


def _create_pending_2fa(username: str) -> str:
    _purge_pending_2fa()
    token = secrets.token_urlsafe(24)
    with _pending_2fa_lock:
        _pending_2fa[token] = {
            "username": username,
            "exp": time.time() + _PENDING_2FA_TTL_SEC,
        }
    return token


def _consume_pending_2fa(token: str) -> str | None:
    _purge_pending_2fa()
    with _pending_2fa_lock:
        entry = _pending_2fa.pop(str(token or ""), None)
    if not entry:
        return None
    return str(entry.get("username") or "")


def _save_admin_credentials(cfg: dict[str, Any], username: str, password: str) -> str:
    username = username.strip()
    err = _validate_admin_credentials(username, password)
    if err:
        return err
    cfg["admin_username"] = username
    cfg["admin_password_hash"] = generate_password_hash(password)
    _issue_session_token(cfg)
    return ""


def _set_admin_cookie(response, cfg: dict[str, Any]):
    # Prefer session_token after password login; setup_token remains for QR first-boot.
    token = str(cfg.get("session_token", "") or "") or str(cfg.get("setup_token", "") or "")
    if token:
        response.set_cookie(
            ADMIN_COOKIE_NAME,
            token,
            max_age=ADMIN_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Strict",
            # Portal is HTTP by design; Secure only when already on HTTPS (e.g. reverse proxy).
            secure=bool(request.is_secure),
        )
    return response


def _step_up_fields(body: dict[str, Any] | None = None) -> dict[str, str]:
    """Normalize step-up fields from JSON (supports current_* aliases for credential change)."""
    body = body or {}
    username = str(
        body.get("username")
        or body.get("current_username")
        or body.get("currentUsername")
        or ""
    ).strip()
    password = str(
        body.get("password")
        or body.get("current_password")
        or body.get("currentPassword")
        or ""
    )
    totp_code = str(body.get("totp_code") or body.get("code") or body.get("totpCode") or "").strip()
    return {"username": username, "password": password, "totp_code": totp_code}


def _require_step_up_password(cfg: dict[str, Any], body: dict[str, Any] | None = None):
    """Re-check admin password (+ TOTP when enabled) for high-risk actions."""
    limited = _auth_rate_limited()
    if limited:
        return limited
    fields = _step_up_fields(body)
    username = fields["username"]
    password = fields["password"]
    if not _has_admin_credentials(cfg):
        return _json_err("Admin credentials are not configured", 403)
    if not _is_admin_password_valid(cfg, username, password):
        _record_auth_failure()
        return _json_err("Invalid username or password", 403)
    if _totp_enabled(cfg):
        if not verify_totp(_totp_secret(cfg), fields["totp_code"]):
            _record_auth_failure()
            return _json_err("Invalid authenticator code", 403)
    _clear_auth_failures()
    return None


def _require_step_up_when_configured(cfg: dict[str, Any], body: dict[str, Any] | None = None):
    """Step-up when admin credentials exist; allow setup-token-only first-boot paths otherwise."""
    if not _has_admin_credentials(cfg):
        return None
    return _require_step_up_password(cfg, body)


def _require_credential_change_step_up(cfg: dict[str, Any], body: dict[str, Any] | None = None):
    """Step-up for changing username/password: always verify against the *current* admin user."""
    if not _has_admin_credentials(cfg):
        return None
    body = body or {}
    step_body = {
        "username": str(cfg.get("admin_username", "") or ""),
        "password": str(
            body.get("currentPassword")
            or body.get("current_password")
            or ""
        ),
        "totp_code": str(body.get("totp_code") or body.get("totpCode") or body.get("code") or ""),
    }
    return _require_step_up_password(cfg, step_body)


def _ap_password(cfg: dict[str, Any]) -> str:
    pw = str(cfg.get("ap_password", "") or "").strip()
    if not pw or pw == "blockvase1234":
        pw = generate_ap_password()
        cfg["ap_password"] = pw
        save_config(cfg)
    return pw


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


def _mining_payout_source(cfg: dict[str, Any], address: str) -> str:
    explicit = str(cfg.get("mining_payout_source", "") or "").strip().lower()
    if explicit in {"node", "custom"}:
        return explicit
    if not address:
        return ""
    try:
        if address_is_mine(state.rpc, cfg.get("rpc") or {}, address):
            return "node"
    except Exception:
        pass
    return "custom"


def _apply_mining_payout_address(
    cfg: dict[str, Any], address: str, *, source: str, message: str | None = None
) -> Any:
    """Persist payout via set-mining-payout.sh. Returns a Flask JSON response."""
    address = address.strip()
    if not address:
        return _json_err("Mining payout address is required.")
    if address.startswith("-"):
        return _json_err("Mining payout address is invalid.")

    rpc_cfg = cfg.get("rpc") or {}
    if not validate_bitcoin_address(state.rpc, rpc_cfg, address):
        return _json_err("That does not look like a valid Bitcoin address.")

    source = "node" if source == "node" else "custom"

    if os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() != "true":
        cfg["mining_payout_address"] = address
        cfg["mining_payout_source"] = source
        save_config(cfg)
        return _json_ok(
            address=address,
            source=source,
            message=message
            or "Address saved, but mining services were not updated because system actions are disabled.",
            applied=False,
        )

    script = _privileged_script("set-mining-payout.sh")
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
    cfg["mining_payout_source"] = source
    save_config(cfg)
    return _json_ok(
        address=address,
        source=source,
        message=message or "Mining payout address saved.",
        applied=True,
    )


def _ensure_node_mining_payout(cfg: dict[str, Any] | None = None) -> str | None:
    """Ensure a node payout address exists in the portal spend wallet.

    - Empty payout → generate spend-wallet address.
    - Legacy mining-wallet payout (pre-split) → migrate to a spend address.
    - Custom / already-spend address → leave unchanged (never overwrite custom).
    - Classify failure (`unknown`) → no-op (do not mint).

    Prefers the on-disk DATUM payout file, then config. Persist a newly minted
    spend address into config *before* applying so a failed apply does not remint
    on the next sync tick.

    Wallet address creation is IBD-safe. set-mining-payout.sh defers DATUM until the
    node is synced enough for block templates.
    """
    with _mining_payout_ensure_lock:
        cfg = load_config()
        rpc_cfg = cfg.get("rpc") or {}
        file_addr = ""
        try:
            file_addr = MINING_PAYOUT_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        cfg_addr = str(cfg.get("mining_payout_address", "") or "").strip()

        def _classify(addr: str) -> str:
            if not addr:
                return "empty"
            try:
                if address_is_spend_mine(state.rpc, rpc_cfg, addr):
                    return "spend"
                if address_is_legacy_mining_mine(state.rpc, rpc_cfg, addr):
                    return "legacy"
            except Exception:
                return "unknown"
            return "custom"

        def _apply(address: str, *, source: str) -> str | None:
            with app.app_context():
                resp = _apply_mining_payout_address(
                    cfg, address, source=source, message=None
                )
            if isinstance(resp, tuple):
                _log.warning("failed to apply node mining payout for %s", address)
                return None
            return address

        file_kind = _classify(file_addr)
        cfg_kind = _classify(cfg_addr)

        if file_kind == "spend":
            return file_addr
        if file_kind == "custom":
            return file_addr
        if file_kind == "unknown":
            # RPC glitch — do not rotate payouts.
            return file_addr or cfg_addr or None

        # Preserve custom config addresses (push to file if needed; never mint over them).
        if cfg_kind == "custom":
            if file_kind in {"empty", "legacy"}:
                return _apply(cfg_addr, source="custom") or cfg_addr
            return cfg_addr
        if cfg_kind == "unknown":
            return file_addr or cfg_addr or None

        # Reuse spend address already in config (covers failed-apply remint case).
        if cfg_kind == "spend":
            if file_addr == cfg_addr:
                return cfg_addr
            if file_kind == "legacy":
                _log.info(
                    "re-applying spend-wallet payout from config (file still legacy %s…)",
                    file_addr[:12],
                )
            elif file_kind == "empty":
                _log.info("applying spend-wallet payout from config (payout file empty)")
            return _apply(cfg_addr, source="node") or cfg_addr

        # Mint only when file is legacy, or both sides are empty/legacy.
        should_mint = file_kind == "legacy" or (
            file_kind == "empty" and cfg_kind in {"empty", "legacy"}
        )
        if not should_mint:
            return file_addr or cfg_addr or None

        if file_kind == "legacy":
            _log.info(
                "migrating legacy mining-wallet payout %s… to portal spend wallet",
                file_addr[:12],
            )
        try:
            address = new_mining_payout_address(state.rpc, rpc_cfg)
        except Exception as ex:
            _log.warning("could not generate mining payout from spend wallet: %s", ex)
            return file_addr or cfg_addr or None

        # Persist before privileged apply so retries do not mint endlessly.
        cfg["mining_payout_address"] = address
        cfg["mining_payout_source"] = "node"
        save_config(cfg)
        return _apply(address, source="node") or address


def _mining_status_fields(cfg: dict[str, Any], address: str) -> dict[str, Any]:
    sync = node_sync_status(state.rpc, cfg.get("rpc") or {})
    source = _mining_payout_source(cfg, address)
    ready = bool(address) and bool(sync.get("ready"))
    return {
        "address": address,
        "source": source,
        "from_node": source == "node",
        "initialblockdownload": bool(sync.get("initialblockdownload")),
        "mining_ready": ready,
        "blocks": int(sync.get("blocks") or 0),
        "headers": int(sync.get("headers") or 0),
        "verificationprogress": float(sync.get("verificationprogress") or 0),
    }


def _datum_gateway_active() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "datum-gateway.service"],
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _ensure_datum_when_synced() -> None:
    """Start DATUM once IBD finishes if a payout address is already configured."""
    if os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() != "true":
        return
    cfg = load_config()
    address = _current_mining_payout_address(cfg).strip()
    if not address or address.startswith("--"):
        return
    sync = node_sync_status(state.rpc, cfg.get("rpc") or {})
    if not sync.get("ready"):
        return
    if _datum_gateway_active():
        return
    script = _privileged_script("set-mining-payout.sh")
    if not script.is_file():
        return
    # New helper supports --ensure-services; older installs treat unknown args as the
    # address string — never pass the flag unless the script advertises it.
    try:
        supports_ensure = "--ensure-services" in script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        supports_ensure = False
    cmd = ["sudo", str(script), "--ensure-services"] if supports_ensure else ["sudo", str(script), address]
    try:
        subprocess.run(
            cmd,
            timeout=90,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        _log.warning("ensure mining services after sync failed: %s", ex)


_mining_sync_thread: threading.Thread | None = None


def _start_mining_sync_thread() -> None:
    global _mining_sync_thread
    if _mining_sync_thread and _mining_sync_thread.is_alive():
        return

    def _loop() -> None:
        # First pass after startup; then every minute.
        while True:
            try:
                cfg = load_config()
                if _is_setup_complete(cfg) and not _is_wifi_recovery(cfg):
                    # Empty payout → create; legacy mining-wallet payout → migrate to spend.
                    _ensure_node_mining_payout(cfg)
                _ensure_datum_when_synced()
            except Exception as ex:
                _log.warning("mining sync thread: %s", ex)
            time.sleep(60)

    _mining_sync_thread = threading.Thread(
        target=_loop, name="blockvase-mining-sync", daemon=True
    )
    _mining_sync_thread.start()


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


@app.get("/wallet")
def wallet_page():
    cfg = load_config()
    if _needs_setup_ui(cfg):
        return redirect(url_for("setup_page", token=cfg.get("setup_token", "")))
    response = make_response(render_template("wallet.html", theme=_theme()))
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
            "totp_enabled": _totp_enabled(cfg),
        }
    )


@app.post("/api/admin-auth/login")
def admin_auth_login():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    limited = _auth_rate_limited()
    if limited:
        return limited
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not _is_admin_password_valid(cfg, username, password):
        _record_auth_failure()
        return _json_err("Invalid username or password", 403)
    if _totp_enabled(cfg):
        pending = _create_pending_2fa(username)
        return _json_ok(
            needs_2fa=True,
            pending_token=pending,
            message="Enter the 6-digit code from your authenticator app.",
        )
    _clear_auth_failures()
    _issue_session_token(cfg)
    save_config(cfg)
    response = _json_ok(needs_2fa=False, message="Authenticated")
    return _set_admin_cookie(response, cfg)


@app.post("/api/admin-auth/login/2fa")
def admin_auth_login_2fa():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    limited = _auth_rate_limited()
    if limited:
        return limited
    if not _totp_enabled(cfg):
        return _json_err("Two-factor authentication is not enabled", 400)
    pending = str(body.get("pending_token", "") or "")
    code = str(body.get("code", "") or "")
    username = _consume_pending_2fa(pending)
    if not username:
        return _json_err("Login challenge expired. Sign in again.", 403)
    if username != str(cfg.get("admin_username", "") or ""):
        _record_auth_failure()
        return _json_err("Invalid two-factor code", 403)
    if not verify_totp(_totp_secret(cfg), code):
        _record_auth_failure()
        # Re-issue pending so a mistyped code does not force full re-login immediately.
        new_pending = _create_pending_2fa(username)
        return jsonify(
            {
                "success": False,
                "error": "Invalid two-factor code",
                "needs_2fa": True,
                "pending_token": new_pending,
            }
        ), 403
    _clear_auth_failures()
    _issue_session_token(cfg)
    save_config(cfg)
    response = _json_ok(message="Authenticated")
    return _set_admin_cookie(response, cfg)


@app.post("/api/admin-auth/credentials")
def admin_auth_credentials():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    # Changing existing credentials requires the current password (+ TOTP when enabled).
    step_err = _require_credential_change_step_up(cfg, body)
    if step_err:
        return step_err
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    err = _save_admin_credentials(cfg, username, password)
    if err:
        return _json_err(err)
    save_config(cfg)
    response = _json_ok(username=username, message="Admin credentials saved")
    return _set_admin_cookie(response, cfg)


@app.post("/api/admin-auth/2fa/begin")
def admin_auth_2fa_begin():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    if not _has_admin_credentials(cfg):
        return _json_err("Set an admin username and password before enabling 2FA.")
    if _totp_enabled(cfg):
        return _json_err("Two-factor authentication is already enabled. Disable it first to re-enroll.")
    secret = generate_secret()
    cfg["totp_pending_secret_enc"] = seal_secret(secret)
    save_config(cfg)
    account = str(cfg.get("admin_username", "") or "admin")
    device = str(cfg.get("device_name", "") or "blockvase")
    uri = otpauth_uri(secret, account_name=f"{account}@{device}", issuer="Blockvase")
    img = qrcode.make(uri, image_factory=SvgImage)
    stream = BytesIO()
    img.save(stream)
    return _json_ok(
        secret=secret,
        otpauth_url=uri,
        qr_svg=stream.getvalue().decode("utf-8"),
        message="Scan the QR with your authenticator app, then confirm with a code.",
    )


@app.post("/api/admin-auth/2fa/confirm")
def admin_auth_2fa_confirm():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    pending = unseal_secret(str(cfg.get("totp_pending_secret_enc", "") or ""))
    if not pending:
        return _json_err("No 2FA enrollment in progress. Start setup again.")
    code = str(body.get("code", "") or "")
    if not verify_totp(pending, code):
        return _json_err("Invalid authenticator code. Check the time on your phone and try again.")
    cfg["totp_secret_enc"] = seal_secret(pending)
    cfg["totp_pending_secret_enc"] = ""
    cfg["totp_enabled"] = True
    # Force re-login with 2FA on other sessions.
    _issue_session_token(cfg)
    save_config(cfg)
    response = _json_ok(totp_enabled=True, message="Two-factor authentication enabled.")
    return _set_admin_cookie(response, cfg)


@app.post("/api/admin-auth/2fa/disable")
def admin_auth_2fa_disable():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    if not _totp_enabled(cfg):
        return _json_ok(totp_enabled=False, message="Two-factor authentication is already off.")
    password = str(body.get("password", "") or "")
    code = str(body.get("code", "") or "")
    username = str(cfg.get("admin_username", "") or "")
    if not _is_admin_password_valid(cfg, username, password):
        return _json_err("Admin password is incorrect", 403)
    if not verify_totp(_totp_secret(cfg), code):
        return _json_err("Invalid authenticator code", 403)
    cfg["totp_enabled"] = False
    cfg["totp_secret_enc"] = ""
    cfg["totp_pending_secret_enc"] = ""
    save_config(cfg)
    return _json_ok(totp_enabled=False, message="Two-factor authentication disabled.")


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
    address = _current_mining_payout_address(cfg)
    # Do not block Settings on wallet/DATUM work during IBD — kick a background attempt
    # for empty payout or legacy mining-wallet → spend-wallet migration.
    needs_ensure = not bool(address.strip())
    if address.strip() and _is_setup_complete(cfg) and not _is_wifi_recovery(cfg):
        try:
            rpc_cfg = cfg.get("rpc") or {}
            needs_ensure = address_is_legacy_mining_mine(
                state.rpc, rpc_cfg, address
            ) and not address_is_spend_mine(state.rpc, rpc_cfg, address)
        except Exception:
            needs_ensure = False
    if needs_ensure and _is_setup_complete(cfg) and not _is_wifi_recovery(cfg):
        threading.Thread(
            target=_ensure_node_mining_payout,
            kwargs={"cfg": cfg},
            name="blockvase-mining-payout-init",
            daemon=True,
        ).start()
    return jsonify(_mining_status_fields(cfg, address))


@app.post("/api/mining-payout")
def set_mining_payout():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    step_err = _require_step_up_when_configured(cfg, body)
    if step_err:
        return step_err

    address = str(body.get("address", "")).strip()
    source = "custom"
    try:
        if address_is_mine(state.rpc, cfg.get("rpc") or {}, address):
            source = "node"
    except Exception:
        pass
    resp = _apply_mining_payout_address(cfg, address, source=source)
    if isinstance(resp, tuple):
        return resp
    # Enrich success payload with IBD / mining-ready flags.
    sync_fields = _mining_status_fields(load_config(), address)
    data = resp.get_json() if hasattr(resp, "get_json") else {}
    if not isinstance(data, dict):
        data = {}
    msg = data.get("message") or "Mining payout address saved."
    if sync_fields.get("initialblockdownload"):
        msg = (
            "Payout address saved. The node is still syncing (IBD); "
            "solo hashing starts automatically when sync finishes."
        )
    return _json_ok(
        applied=bool(data.get("applied", True)),
        message=msg,
        **{k: sync_fields[k] for k in sync_fields},
    )


@app.post("/api/mining-payout/generate")
def generate_mining_payout():
    """Create a fresh receive address in the portal spend wallet and apply it as payout."""
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    step_err = _require_step_up_when_configured(cfg, body)
    if step_err:
        return step_err
    with _mining_payout_ensure_lock:
        try:
            # Works during IBD — only needs wallet RPC, not a synced tip.
            address = new_mining_payout_address(state.rpc, cfg.get("rpc") or {})
        except Exception as ex:
            _log.exception("generate mining payout failed")
            return _json_err(f"Could not generate address from this node: {ex}", 500)
        # Persist before apply so a failed helper does not remint on the next ensure tick.
        cfg = load_config()
        cfg["mining_payout_address"] = address
        cfg["mining_payout_source"] = "node"
        save_config(cfg)
        sync = node_sync_status(state.rpc, cfg.get("rpc") or {})
        msg = "Generated a new payout address from this node’s wallet and applied it."
        if sync.get("initialblockdownload"):
            msg = (
                "Generated a payout address from this node’s wallet. "
                "The node is still syncing (IBD); solo hashing starts when sync finishes."
            )
        resp = _apply_mining_payout_address(cfg, address, source="node", message=msg)
        if isinstance(resp, tuple):
            return resp
        sync_fields = _mining_status_fields(load_config(), address)
        data = resp.get_json() if hasattr(resp, "get_json") else {}
        if not isinstance(data, dict):
            data = {}
        return _json_ok(
            applied=bool(data.get("applied", True)),
            message=data.get("message") or msg,
            **{k: sync_fields[k] for k in sync_fields},
        )


def _wallet_snapshot(cfg: dict[str, Any], *, receive_address: str | None = None) -> dict[str, Any]:
    """Spend-wallet snapshot. Does not mint addresses — pass receive_address from /receive."""
    rpc_cfg = cfg.get("rpc") or {}
    sync = node_sync_status(state.rpc, rpc_cfg)
    balances = spend_wallet_balances(state.rpc, rpc_cfg)
    txs = spend_wallet_recent_transactions(state.rpc, rpc_cfg, count=15)
    legacy = legacy_mining_wallet_balances(state.rpc, rpc_cfg)
    legacy_total = (
        Decimal(legacy.get("trusted") or "0")
        + Decimal(legacy.get("untrusted_pending") or "0")
        + Decimal(legacy.get("immature") or "0")
    )
    return {
        "receive_address": (receive_address or "").strip(),
        "trusted": balances.get("trusted", "0.00000000"),
        "untrusted_pending": balances.get("untrusted_pending", "0.00000000"),
        "immature": balances.get("immature", "0.00000000"),
        "transactions": txs,
        "initialblockdownload": bool(sync.get("initialblockdownload")),
        "mining_ready": bool(sync.get("ready")),
        "blocks": int(sync.get("blocks") or 0),
        "headers": int(sync.get("headers") or 0),
        "totp_enabled": _totp_enabled(cfg),
        "wallet_kind": "spend",
        "legacy_mining_balance": format_btc_decimal(legacy_total) if legacy_total > 0 else "",
    }


@app.get("/api/wallet")
def get_wallet():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    try:
        return jsonify(_wallet_snapshot(cfg))
    except Exception as ex:
        _log.exception("wallet status failed")
        return _json_err(f"Could not load wallet: {ex}", 500)


@app.post("/api/wallet/receive")
def wallet_new_receive():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    try:
        address = new_spend_receive_address(state.rpc, cfg.get("rpc") or {})
        snap = _wallet_snapshot(cfg, receive_address=address)
        return _json_ok(**snap)
    except Exception as ex:
        _log.exception("wallet receive address failed")
        return _json_err(f"Could not generate receive address: {ex}", 500)


@app.get("/api/wallet/receive-qr.svg")
def wallet_receive_qr():
    cfg = load_config()
    token_err = _require_admin_token(cfg)
    if token_err:
        return token_err
    address = str(request.args.get("address", "") or "").strip()
    rpc_cfg = cfg.get("rpc") or {}
    if not address or not validate_bitcoin_address(state.rpc, rpc_cfg, address):
        return _json_err("Valid address required", 400)
    # Only emit QRs for addresses we control (blocks phishing via arbitrary QR).
    if not address_is_spend_mine(state.rpc, rpc_cfg, address):
        return _json_err("Address is not from this device’s wallet", 400)
    payload = f"bitcoin:{address}"
    img = qrcode.make(payload, image_factory=SvgImage)
    buf = BytesIO()
    img.save(buf)
    response = make_response(buf.getvalue())
    response.headers["Content-Type"] = "image/svg+xml"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/wallet/send")
def wallet_send():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    step_err = _require_step_up_password(cfg, body)
    if step_err:
        return step_err
    address = str(body.get("address", "") or "").strip()
    try:
        amount = parse_btc_amount(body.get("amount"))
    except RuntimeError as ex:
        return _json_err(str(ex), 400)
    subtract = bool(body.get("subtract_fee_from_amount"))
    sync = node_sync_status(state.rpc, cfg.get("rpc") or {})
    if sync.get("initialblockdownload"):
        return _json_err(
            "Node is still syncing (IBD). Wait until sync finishes before sending.",
            400,
        )
    try:
        txid = send_from_spend_wallet(
            state.rpc,
            cfg.get("rpc") or {},
            address=address,
            amount_btc=amount,
            subtract_fee_from_amount=subtract,
        )
    except Exception as ex:
        _log.exception("wallet send failed")
        return _json_err(str(ex), 400)
    return _json_ok(txid=txid, message="Transaction broadcast.")


@app.post("/api/wallet/backup")
def wallet_backup():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    step_err = _require_step_up_password(cfg, body)
    if step_err:
        return step_err
    try:
        text = export_spend_wallet_descriptors(state.rpc, cfg.get("rpc") or {})
    except Exception as ex:
        _log.exception("wallet backup failed")
        return _json_err(f"Could not export wallet backup: {ex}", 500)
    response = _json_ok(
        backup=text,
        warning=(
            "This backup can spend all funds in the portal spend wallet. "
            "Store it offline and never share it. This includes keys for default "
            "node mining payout addresses in the same wallet."
        ),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


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
        was_setup_complete = _is_setup_complete(cfg)
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
            # After first setup, changing username/password requires current password (+ TOTP).
            step_err = _require_credential_change_step_up(cfg, body)
            if step_err:
                return step_err
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
            # After leaving first-boot with admin creds, rotate QR setup_token so old
            # setup URLs stop granting admin; session_token remains for this browser.
            if _has_admin_credentials(cfg):
                cfg["setup_token"] = secrets.token_urlsafe(16)
                if not cfg.get("session_token"):
                    _issue_session_token(cfg)

        save_config(cfg)
        _sync_hostname(cfg["device_name"])

        # First-time setup: create a Knots wallet address for solo mining payouts.
        if home_wifi and not was_setup_complete and not _current_mining_payout_address(cfg):
            threading.Thread(
                target=_ensure_node_mining_payout,
                name="blockvase-mining-payout-init",
                daemon=True,
            ).start()

        reboot_scheduled = False
        wifi_switch_started = False
        if cfg.get("setup_complete"):
            # Switch Wi-Fi first, then reboot. A fixed 8s reboot was racing the
            # NetworkManager handoff and left clones with setup_complete=true,
            # no AP, and no working client profile.
            def _switch_then_reboot() -> None:
                time.sleep(2)  # let client receive HTTP response before AP goes down
                ap_script = _privileged_script("ap-mode.sh")
                ok = False
                if ap_script.exists():
                    # First AP→client handoff is flaky on some radios; retry once
                    # before accepting soft-recovery (setup QR, no reboot).
                    for attempt in (1, 2):
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
                            if ok:
                                _log.info(
                                    "ap-mode ensure succeeded on attempt %s", attempt
                                )
                                break
                            _log.error(
                                "ap-mode ensure failed attempt %s (rc=%s): stdout=%r stderr=%r",
                                attempt,
                                result.returncode,
                                result.stdout,
                                result.stderr,
                            )
                        except (subprocess.TimeoutExpired, OSError) as ex:
                            _log.exception("ap-mode ensure attempt %s: %s", attempt, ex)
                        if attempt == 1:
                            time.sleep(4)
                if not ok:
                    # Soft recovery: keep credentials, show setup QR, do not reboot.
                    _log.error(
                        "Wi-Fi join failed after retries; staying in soft recovery "
                        "(setup QR). Credentials are saved — reboot once radio joins."
                    )
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
                    _invoke_reboot()
                except OSError as ex:
                    _log.exception("scheduled reboot after wifi switch failed: %s", ex)

            threading.Thread(target=_switch_then_reboot, daemon=True).start()
            wifi_switch_started = True
            # Do not claim reboot is scheduled — soft recovery may skip it.
            reboot_scheduled = False
        else:
            reboot_scheduled = _schedule_reboot_after_save(8.0)
        response = _json_ok(
            message=(
                "Settings saved. Switching to Wi-Fi; device reboots if join succeeds..."
                if wifi_switch_started
                else "Settings saved."
            ),
            deviceName=cfg["device_name"],
            rebootScheduled=reboot_scheduled,
            wifiSwitchStarted=wifi_switch_started,
        )
        if _is_token_valid(cfg, _request_admin_token()):
            return _set_admin_cookie(response, cfg)
        return response
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
    """PiAxe-miner REST metrics (graceful zeros if unreachable)."""
    cfg = load_config()
    data = fetch_mining_metrics()
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
    try:
        _invoke_reboot()
    except OSError as ex:
        _log.exception("reboot API failed: %s", ex)
        return _json_err("Reboot request failed", 500)
    return _json_ok(message="Reboot requested")


@app.post("/api/factory-reset")
def factory_reset():
    body = request.get_json(force=True, silent=True) or {}
    cfg_before = load_config()
    token_err = _require_admin_token(cfg_before, body)
    if token_err:
        return token_err
    step_err = _require_step_up_when_configured(cfg_before, body)
    if step_err:
        return step_err
    if os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() != "true":
        return _json_err(
            "Factory reset requires ENABLE_SYSTEM_ACTIONS=true (and sudoers for reboot)"
        )
    preserved_rpc = json.loads(json.dumps(cfg_before.get("rpc", {})))
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["setup_token"] = ""
    cfg["session_token"] = ""
    cfg["ap_password"] = ""
    cfg["totp_enabled"] = False
    cfg["totp_secret_enc"] = ""
    cfg["totp_pending_secret_enc"] = ""
    cfg["mining_payout_address"] = ""
    cfg["mining_payout_source"] = ""
    cfg["https_redirect"] = False
    cfg["rpc"] = json.loads(json.dumps(DEFAULT_CONFIG["rpc"]))
    cfg["rpc"].update(preserved_rpc)
    _apply_local_rpc(cfg)
    save_config(cfg)
    # New device identity cert after wipe (old client trust becomes invalid).
    _ensure_portal_tls(force=True)
    ap_script = _privileged_script("ap-mode.sh")
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
    try:
        _invoke_reboot()
    except OSError as ex:
        _log.exception("factory-reset reboot failed: %s", ex)
        return _json_err("Reset applied but reboot failed; power-cycle the device.", 500)
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
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    step_err = _require_step_up_when_configured(cfg, body)
    if step_err:
        return step_err
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
    # Only expose AP PSK / setup URL while the setup AP UI is active.
    if not show_setup:
        return jsonify(
            {
                "ap_mode": False,
                "wifi_recovery": False,
                "ssid": "",
                "password": "",
                "settings_url": "",
                "wifi_qr_payload": "",
                "ap_clients": 0,
            }
        )
    ap_password = _ap_password(cfg)
    wifi_qr_payload = f"WIFI:T:WPA;S:{ap_ssid};P:{ap_password};;"
    return jsonify(
        {
            "ap_mode": True,
            "wifi_recovery": _is_wifi_recovery(cfg),
            "ssid": ap_ssid,
            "password": ap_password,
            "settings_url": _setup_url(cfg),
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
        ap_password = _ap_password(cfg)
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
_start_mining_sync_thread()


@app.get("/api/tls/status")
def api_tls_status():
    cfg = load_config()
    status = tls_status(cfg)
    status["request_is_secure"] = bool(request.is_secure)
    status["show_http_banner"] = bool(
        status.get("tls_ready") and (not request.is_secure) and (not status.get("https_redirect"))
    )
    return jsonify(status)


@app.get("/api/tls/cert.crt")
def api_tls_cert_download():
    """Download the device CA (install/trust this on each client phone/computer)."""
    pem = read_cert_pem()
    if not pem:
        return _json_err("Portal TLS CA certificate is not available yet", 404)
    cfg = load_config()
    name = _safe_device_name(str(cfg.get("device_name") or "blockvase"))
    response = make_response(pem)
    response.headers["Content-Type"] = "application/x-x509-ca-cert"
    response.headers["Content-Disposition"] = f'attachment; filename="{name}-portal-ca.crt"'
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/tls/regenerate")
def api_tls_regenerate():
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    token_err = _require_admin_token(cfg, body)
    if token_err:
        return token_err
    step_err = _require_step_up_when_configured(cfg, body)
    if step_err:
        return step_err
    cfg["https_redirect"] = False
    save_config(cfg)
    _ensure_portal_tls(str(cfg.get("device_name") or "blockvase"), force=True)
    status = tls_status(load_config())
    if not status.get("tls_ready"):
        return _json_err("Could not regenerate TLS certificate (is ensure-portal-tls installed?)", 500)
    return _json_ok(
        message=(
            "New device CA and portal certificate created. "
            "Re-install the CA on every client device and enable full trust (iOS). "
            "HTTPS redirect was turned off."
        ),
        **status,
    )


@app.post("/api/tls/https-redirect")
def api_tls_https_redirect():
    """Enable/disable prefer-HTTPS redirect. Disabling over HTTP needs password only (recovery)."""
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    enabled = bool(body.get("enabled"))
    if enabled:
        token_err = _require_admin_token(cfg, body)
        if token_err:
            return token_err
        step_err = _require_step_up_when_configured(cfg, body)
        if step_err:
            return step_err
        if not tls_status(cfg).get("tls_ready"):
            return _json_err("Install/generate the portal certificate before enabling HTTPS redirect", 400)
    else:
        # Recovery path: allow turning redirect off with admin password even if Secure cookie missing.
        if _is_token_valid(cfg, _request_admin_token()):
            step_err = _require_step_up_when_configured(cfg, body)
            if step_err:
                return step_err
        else:
            step_err = _require_step_up_password(cfg, body)
            if step_err:
                return step_err
    cfg["https_redirect"] = enabled
    save_config(cfg)
    return _json_ok(
        https_redirect=enabled,
        message=(
            "HTTP will redirect to HTTPS. Keep a way to open Settings if a client has not trusted the cert yet."
            if enabled
            else "HTTPS redirect disabled. Portal stays available over HTTP."
        ),
        **tls_status(cfg),
    )


if __name__ == "__main__":
    # Behind nginx by default (loopback). Direct bind still supported for dev.
    host = os.getenv("BLOCKVASE_HOST", "127.0.0.1")
    port = int(os.getenv("BLOCKVASE_PORT", "8080"))
    serve(
        app,
        host=host,
        port=port,
        trusted_proxy="127.0.0.1",
        trusted_proxy_count=1,
        trusted_proxy_headers="x-forwarded-for x-forwarded-host x-forwarded-proto x-forwarded-port",
    )

