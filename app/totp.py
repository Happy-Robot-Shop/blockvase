"""Minimal TOTP (RFC 6238) helpers — no third-party dependency."""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time


_B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def generate_secret(num_bytes: int = 20) -> str:
    import secrets

    raw = secrets.token_bytes(num_bytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _normalize_secret(secret: str) -> bytes:
    cleaned = "".join(ch for ch in secret.strip().upper() if ch in _B32_ALPHABET)
    pad = "=" * ((8 - len(cleaned) % 8) % 8)
    return base64.b32decode(cleaned + pad, casefold=True)


def totp_code(secret: str, for_time: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = _normalize_secret(secret)
    counter = int((time.time() if for_time is None else for_time) // step)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % (10 ** digits):0{digits}d}"


def verify_totp(secret: str, code: str, window: int = 1, step: int = 30, digits: int = 6) -> bool:
    supplied = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(supplied) != digits:
        return False
    now = time.time()
    for skew in range(-window, window + 1):
        expected = totp_code(secret, for_time=now + skew * step, step=step, digits=digits)
        if hmac.compare_digest(supplied, expected):
            return True
    return False


def otpauth_uri(secret: str, account_name: str, issuer: str = "Blockvase") -> str:
    from urllib.parse import quote

    label = quote(f"{issuer}:{account_name}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits=6&period=30"
    )
