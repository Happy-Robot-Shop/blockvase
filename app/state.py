from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .bitcoin_rpc import BitcoinRpcClient
from .config import load_config


@dataclass
class RuntimeState:
    metrics: dict[str, Any] = field(default_factory=lambda: {"connected": False})
    blocks_found: int = 0
    simulated_block_pending: bool = False
    simulated_miner_block_pending: bool = False
    last_miner_blocks_found: int | None = None
    last_update_ts: int = 0
    poll_seconds: int = 5
    lock: threading.Lock = field(default_factory=threading.Lock)


class StateManager:
    def __init__(self) -> None:
        self.state = RuntimeState()
        self.rpc = BitcoinRpcClient()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

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
            self.state.blocks_found += 1
            self.state.simulated_block_pending = True

    def mark_miner_block_event(self) -> None:
        with self.state.lock:
            self.state.blocks_found += 1
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
                    self.state.blocks_found += miner_blocks_found - previous
                    self.state.last_miner_blocks_found = miner_blocks_found

            return simulated or detected

    def get_metrics(self) -> dict[str, Any]:
        with self.state.lock:
            merged = dict(self.state.metrics)
            merged["blocks_found"] = self.state.blocks_found
            merged["last_update_time"] = self.state.last_update_ts
            return merged

    def _poll_loop(self) -> None:
        previous_height = None
        while not self._stop.is_set():
            cfg = load_config()
            rpc_cfg = cfg.get("rpc", {})
            now = int(time.time())
            try:
                metrics = self.rpc.collect_metrics(rpc_cfg)
                height = metrics.get("blocks")
                with self.state.lock:
                    if previous_height is not None and isinstance(height, int) and height > previous_height:
                        self.state.blocks_found += height - previous_height
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
                            "rpc_status_code": self.rpc.last_error.http_status,
                            "rpc_error_body": self.rpc.last_error.body_snippet,
                            "node_version": cli_ver,
                        }
                    self.state.last_update_ts = now
            self._stop.wait(self.state.poll_seconds)

