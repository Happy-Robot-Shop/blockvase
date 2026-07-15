"""Portal mining metrics read from PiAxe-miner REST (`GET /influx/stats`).

Settings → mining simulation points the miner at `config.blockvase.simulate.yml` (CPU
nonce search); this module always proxies live REST, there is no portal-side fake miner.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

_log = logging.getLogger("blockvase")


def zero_mining_payload(reason: str | None = None) -> dict[str, Any]:
    return {
        "available": False,
        "asic_active": False,
        "hashrate_ghs": 0.0,
        "hashrate_hs": 0.0,
        "temperature_c": None,
        "difficulty": 0.0,
        "valid_shares": 0,
        "invalid_shares": 0,
        "accepted": 0,
        "not_accepted": 0,
        "pool_errors": 0,
        "uptime_sec": 0,
        "total_uptime_sec": 0,
        "blocks_found": 0,
        "total_blocks_found": 0,
        "best_difficulty": 0.0,
        "total_best_difficulty": 0.0,
        "duplicate_hashes": 0,
        "source": "piaxe-miner",
        "unavailable_reason": reason,
    }


def _num(raw: Any, default: float = 0.0) -> float:
    try:
        if raw is None:
            return default
        return float(raw)
    except (TypeError, ValueError):
        return default


def _int(raw: Any, default: int = 0) -> int:
    try:
        if raw is None:
            return default
        return int(raw)
    except (TypeError, ValueError):
        return default


def fetch_mining_metrics(timeout_override: float | None = None) -> dict[str, Any]:
    """
    Pull live counters from piaxe-miner REST (GET /influx/stats).
    On any failure (no daemon, wrong port, ASIC never initialized), returns zeros, never raises.
    """
    url = os.getenv("BLOCKVASE_PIAXE_MINER_STATS_URL", "http://127.0.0.1:5000/influx/stats").strip()
    if not url:
        return zero_mining_payload("BLOCKVASE_PIAXE_MINER_STATS_URL is empty")

    if timeout_override is None:
        try:
            timeout = float(os.getenv("BLOCKVASE_PIAXE_MINER_TIMEOUT", "2.5"))
        except ValueError:
            timeout = 2.5
    else:
        timeout = timeout_override

    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as ex:
        _log.debug("mining stats HTTP error: %s", ex)
        return zero_mining_payload(f"HTTP {ex.code}")
    except urllib.error.URLError as ex:
        _log.debug("mining stats unreachable: %s", ex.reason)
        return zero_mining_payload("miner REST unreachable")
    except Exception as ex:
        _log.debug("mining stats fetch failed: %s", ex)
        return zero_mining_payload("fetch failed")

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as ex:
        _log.debug("mining stats JSON parse failed: %s", ex)
        return zero_mining_payload("invalid JSON")

    if not isinstance(data, dict):
        return zero_mining_payload("unexpected payload")

    # hashing_speed from piaxe-miner is GH/s (see miner.BM1366Miner.hash_rate).
    ghs = _num(data.get("hashing_speed"))
    hs = ghs * 1e9

    accepted = _int(data.get("accepted"))
    valid_shares = _int(data.get("valid_shares"))

    asic_active = ghs > 1e-9 or accepted > 0 or valid_shares > 0

    temp = data.get("temperature")
    temp_c: float | None
    try:
        temp_c = float(temp) if temp is not None else None
    except (TypeError, ValueError):
        temp_c = None

    return {
        "available": True,
        "asic_active": asic_active,
        "hashrate_ghs": ghs,
        "hashrate_hs": hs,
        "temperature_c": temp_c,
        "difficulty": _num(data.get("difficulty")),
        "valid_shares": valid_shares,
        "invalid_shares": _int(data.get("invalid_shares")),
        "accepted": accepted,
        "not_accepted": _int(data.get("not_accepted")),
        "pool_errors": _int(data.get("pool_errors")),
        "uptime_sec": _int(data.get("uptime")),
        "total_uptime_sec": _int(data.get("total_uptime")),
        "blocks_found": _int(data.get("blocks_found")),
        "total_blocks_found": _int(data.get("total_blocks_found")),
        "best_difficulty": _num(data.get("best_difficulty")),
        "total_best_difficulty": _num(data.get("total_best_difficulty")),
        "duplicate_hashes": _int(data.get("duplicate_hashes")),
        "source": "piaxe-miner",
        "unavailable_reason": None,
    }
