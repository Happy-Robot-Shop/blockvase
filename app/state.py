from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bitcoin_rpc import BitcoinRpcClient
from .config import DATA_DIR, load_config

_log = logging.getLogger("blockvase")

PORTAL_STATS_PATH = DATA_DIR / "portal_stats.json"


@dataclass
class RuntimeState:
    metrics: dict[str, Any] = field(default_factory=lambda: {"connected": False})
    # Lifetime network tip advances since first boot (persisted).
    new_blocks_seen: int = 0
    simulated_block_pending: bool = False
    simulated_miner_block_pending: bool = False
    last_miner_blocks_found: int | None = None
    last_update_ts: int = 0
    poll_seconds: int = 5
    lock: threading.Lock = field(default_factory=threading.Lock)


def _load_portal_stats() -> dict[str, Any]:
    try:
        raw = PORTAL_STATS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError, json.JSONDecodeError) as ex:
        _log.warning("portal stats load failed: %s", ex)
    return {}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def reset_portal_stats() -> None:
    """Clear lifetime portal counters (factory reset)."""
    try:
        if PORTAL_STATS_PATH.exists():
            PORTAL_STATS_PATH.unlink()
    except OSError as ex:
        _log.warning("portal stats reset failed: %s", ex)


class StateManager:
    def __init__(self) -> None:
        self.state = RuntimeState()
        self.rpc = BitcoinRpcClient()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load_lifetime_stats()

    def _load_lifetime_stats(self) -> None:
        data = _load_portal_stats()
        try:
            seen = int(data.get("new_blocks_seen", 0) or 0)
        except (TypeError, ValueError):
            seen = 0
        if seen < 0:
            seen = 0
        self.state.new_blocks_seen = seen

    def _persist_lifetime_stats_unlocked(self) -> None:
        try:
            _atomic_write_json(
                PORTAL_STATS_PATH,
                {
                    "new_blocks_seen": int(self.state.new_blocks_seen),
                    "updated_at": int(time.time()),
                },
            )
        except OSError as ex:
            _log.warning("portal stats save failed: %s", ex)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="blockvase-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def mark_block_event(self) -> None:
        with self.state.lock:
            self.state.simulated_block_pending = True

    def mark_miner_block_event(self) -> None:
        with self.state.lock:
            self.state.simulated_miner_block_pending = True

    def consume_simulated_block(self) -> bool:
        """Return True if a simulated block was pending, and clear the flag."""
        with self.state.lock:
            was = self.state.simulated_block_pending
            self.state.simulated_block_pending = False
            return was

    def consume_miner_block_event(self, miner_blocks_found: int | None = None) -> bool:
        """Return True if the local miner reported/faked a newly found block."""
        with self.state.lock:
            simulated = self.state.simulated_miner_block_pending
            self.state.simulated_miner_block_pending = False

            detected = False
            if miner_blocks_found is not None:
                previous = self.state.last_miner_blocks_found
                if previous is None:
                    self.state.last_miner_blocks_found = miner_blocks_found
                elif miner_blocks_found > previous:
                    detected = True
                    self.state.last_miner_blocks_found = miner_blocks_found

            return simulated or detected

    def get_metrics(self) -> dict[str, Any]:
        with self.state.lock:
            merged = dict(self.state.metrics)
            # Keep blocks_found key for existing /api/stats + settings UI.
            merged["blocks_found"] = self.state.new_blocks_seen
            merged["new_blocks_seen"] = self.state.new_blocks_seen
            merged["last_update_time"] = self.state.last_update_ts
            return merged

    def _poll_loop(self) -> None:
        previous_height = None
        while not self._stop.is_set():
            now = int(time.time())
            try:
                cfg = load_config()
                rpc_cfg = cfg.get("rpc", {})
                metrics = self.rpc.collect_metrics(rpc_cfg)
                height = metrics.get("blocks")
                with self.state.lock:
                    if previous_height is not None and isinstance(height, int) and height > previous_height:
                        self.state.new_blocks_seen += height - previous_height
                        self._persist_lifetime_stats_unlocked()
                    previous_height = height if isinstance(height, int) else previous_height
                    self.state.metrics = metrics
                    self.state.last_update_ts = now
            except Exception:
                cli_ver = ""
                try:
                    cli_ver = self.rpc.local_cli_version_string()
                except Exception:
                    pass
                with self.state.lock:
                    prev = dict(self.state.metrics)
                    if prev.get("connected"):
                        # Keep last good snapshot when RPC is briefly unavailable (reindex, load, etc.).
                        prev["metrics_stale"] = True
                        self.state.metrics = prev
                    else:
                        self.state.metrics = {
                            "connected": False,
                            "rpc_status_code": getattr(self.rpc.last_error, "http_status", None),
                            "rpc_error_body": getattr(self.rpc.last_error, "body_snippet", None),
                            "node_version": cli_ver,
                        }
                    self.state.last_update_ts = now
            self._stop.wait(self.state.poll_seconds)
