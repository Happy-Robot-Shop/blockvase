from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"


# JSON-RPC always targets local Bitcoin Knots (see scripts/install-bitcoin-knots.sh); credentials live in
# /etc/bitcoin/bitcoin.conf and are merged in load_config().
DEFAULT_CONFIG: dict[str, Any] = {
    "device_name": "blockvase",
    "theme": "default",
    "display_offset_x": 0,
    "wifi_ssid": "",
    "wifi_password": "",
    "setup_complete": False,
    # Soft offline recovery: show setup QR/hotspot while keeping setup_complete + Wi-Fi secrets
    # so the device can keep retrying the saved network and leave setup when it reconnects.
    "wifi_recovery": False,
    "setup_token": "",
    "admin_username": "",
    "admin_password_hash": "",
    "mining_payout_address": "",
    "mining_simulation_enabled": False,
    "rpc": {
        "host": "127.0.0.1",
        "port": 8332,
        "user": "",
        "password": "",
        "use_https": False,
        "timeout_seconds": 8,
    },
}

BITCOIN_CONF_PATH = Path("/etc/bitcoin/bitcoin.conf")


def rpc_credentials_from_bitcoind_conf() -> dict[str, str]:
    """Parse rpcuser/rpcpassword from bitcoind config (install-bitcoin-knots.sh)."""
    out: dict[str, str] = {}
    if not BITCOIN_CONF_PATH.exists():
        return out
    try:
        text = BITCOIN_CONF_PATH.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k == "rpcuser":
            out["user"] = v
        elif k == "rpcpassword":
            out["password"] = v
    return out


def _apply_local_rpc(merged: dict[str, Any]) -> None:
    """Force localhost JSON-RPC; overlay credentials from /etc/bitcoin/bitcoin.conf when present."""
    merged["rpc"]["host"] = "127.0.0.1"
    merged["rpc"]["port"] = 8332
    merged["rpc"]["use_https"] = False
    file_cred = rpc_credentials_from_bitcoind_conf()
    if file_cred.get("user"):
        merged["rpc"]["user"] = file_cred["user"]
    if file_cred.get("password"):
        merged["rpc"]["password"] = file_cred["password"]


_lock = threading.Lock()


def hardware_ap_suffix() -> str:
    """Stable 6-char id from wlan/eth MAC or machine-id (for AP SSID when token missing)."""
    for name in ("wlan0", "wlan1", "eth0"):
        p = Path(f"/sys/class/net/{name}/address")
        if not p.exists():
            continue
        try:
            mac = p.read_text(encoding="ascii").strip().replace(":", "")
        except OSError:
            continue
        if len(mac) >= 6:
            return mac[-6:].lower()
    try:
        mid = Path("/etc/machine-id").read_text(encoding="ascii").strip()
        if len(mid) >= 6:
            return mid[-6:]
    except OSError:
        pass
    return "init"


def ap_broadcast_ssid(cfg: dict[str, Any]) -> str:
    """SSID for setup-mode AP: blockvase-<6 chars> so multiple units in one room differ."""
    t = str(cfg.get("setup_token", "") or "").strip()
    if len(t) >= 6:
        return f"blockvase-{t[:6]}"
    return f"blockvase-{hardware_ap_suffix()}"


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    _ensure_dirs()
    if not CONFIG_PATH.exists():
        save_config(json.loads(json.dumps(DEFAULT_CONFIG)))

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update({k: v for k, v in raw.items() if k != "rpc"})
    merged["rpc"].update(raw.get("rpc", {}))
    _apply_local_rpc(merged)
    if not merged.get("setup_token"):
        merged["setup_token"] = secrets.token_urlsafe(16)
        save_config(merged)
    return merged


def save_config(config: dict[str, Any]) -> None:
    _ensure_dirs()
    _apply_local_rpc(config)
    with _lock:
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, sort_keys=True)
            f.write("\n")
        try:
            CONFIG_PATH.chmod(0o600)
        except OSError:
            pass

