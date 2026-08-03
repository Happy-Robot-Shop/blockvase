"""Read portal TLS status / public CA certificate for the HTTP UI."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


TLS_DIR = Path("/etc/blockvase/tls")
CA_CRT_PATH = TLS_DIR / "ca.crt"
CRT_PATH = TLS_DIR / "portal.crt"
KEY_PATH = TLS_DIR / "portal.key"
META_PATH = TLS_DIR / "meta.json"


def tls_files_ready() -> bool:
    return CA_CRT_PATH.is_file() and CRT_PATH.is_file() and KEY_PATH.is_file()


def read_ca_pem() -> str | None:
    """Public CA cert that clients should install/trust."""
    if not CA_CRT_PATH.is_file():
        return None
    try:
        text = CA_CRT_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    if "BEGIN CERTIFICATE" not in text:
        return None
    return text


def read_cert_pem() -> str | None:
    """Backward-compatible alias: download endpoint serves the CA, not the leaf."""
    return read_ca_pem()


def tls_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or {}
    meta: dict[str, Any] = {}
    if META_PATH.is_file():
        try:
            loaded = json.loads(META_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, json.JSONDecodeError):
            meta = {}
    ready = tls_files_ready()
    device = str(cfg.get("device_name") or meta.get("device_name") or "blockvase")
    host_local = f"{device}.local"
    fp = str(meta.get("ca_fingerprint_sha256") or meta.get("fingerprint_sha256") or "")
    return {
        "tls_ready": ready,
        "https_redirect": bool(cfg.get("https_redirect")),
        "device_name": device,
        "https_url": f"https://{host_local}/",
        "http_url": f"http://{host_local}/",
        "trust_model": str(meta.get("trust_model") or ("device_ca" if ready else "")),
        "download": "ca.crt",
        "fingerprint_sha256": fp,
        "ca_fingerprint_sha256": fp,
        "not_after": str(meta.get("ca_not_after") or meta.get("not_after") or ""),
        "sans": list(meta.get("sans") or []) if isinstance(meta.get("sans"), list) else [],
        "cert_path": str(CA_CRT_PATH) if ready else "",
        "leaf_cert_path": str(CRT_PATH) if ready else "",
    }
