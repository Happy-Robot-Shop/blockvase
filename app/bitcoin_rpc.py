from __future__ import annotations

import heapq
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

import requests
from requests.auth import HTTPBasicAuth


@dataclass
class RpcErrorState:
    http_status: int | None = None
    message: str = ""
    body_snippet: str = ""


class BitcoinRpcClient:
    def __init__(self) -> None:
        self.last_error = RpcErrorState()
        self._external_fee_cache_ts = 0.0
        self._external_fee_cache: tuple[float, float, float] | None = None
        self._thread_local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            self._thread_local.session = session
        return session

    def call(self, rpc_cfg: dict[str, Any], method: str, params: list[Any] | None = None) -> Any:
        params = params or []
        scheme = "https" if rpc_cfg.get("use_https") else "http"
        url = f"{scheme}://{rpc_cfg['host']}:{rpc_cfg['port']}"

        payload = {"jsonrpc": "1.0", "id": "blockvase", "method": method, "params": params}
        timeout = int(rpc_cfg.get("timeout_seconds", 8))

        self.last_error = RpcErrorState()
        try:
            resp = self._session().post(
                url,
                json=payload,
                auth=HTTPBasicAuth(rpc_cfg.get("user", ""), rpc_cfg.get("password", "")),
                timeout=timeout,
            )
            if resp.status_code != 200:
                self.last_error = RpcErrorState(
                    http_status=resp.status_code,
                    message=f"HTTP {resp.status_code}",
                    body_snippet=resp.text[:160],
                )
                raise RuntimeError(self.last_error.message)

            body = resp.json()
            if body.get("error"):
                self.last_error = RpcErrorState(
                    http_status=200,
                    message=str(body["error"]),
                    body_snippet=str(body["error"])[:160],
                )
                raise RuntimeError(self.last_error.message)
            return body.get("result")
        except requests.RequestException as exc:
            self.last_error = RpcErrorState(message=str(exc), body_snippet=str(exc)[:160])
            raise RuntimeError(str(exc)) from exc

    def local_cli_version_string(self) -> str:
        """Full client version from ``bitcoin-cli --version`` (e.g. v29.3.knots20260508)."""
        candidates: list[str] = []
        for p in ("/usr/local/bin/bitcoin-cli", shutil.which("bitcoin-cli") or ""):
            if not p:
                continue
            rp = os.path.realpath(p)
            if rp in candidates:
                continue
            if os.path.isfile(rp) and os.access(rp, os.X_OK):
                candidates.append(rp)
        for cli in candidates:
            try:
                r = subprocess.run(
                    [cli, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                line = (r.stdout or "").strip().split("\n", 1)[0]
                if not line:
                    continue
                m = re.search(r"\bversion\s+(.+)$", line, flags=re.IGNORECASE)
                return (m.group(1).strip() if m else line.strip())
            except (OSError, subprocess.TimeoutExpired):
                continue
        return ""

    def collect_metrics(self, rpc_cfg: dict[str, Any]) -> dict[str, Any]:
        cli_ver = self.local_cli_version_string()
        best_height = int(self.call(rpc_cfg, "getblockcount"))
        chain = self.call(rpc_cfg, "getblockchaininfo")
        mempool = self.call(rpc_cfg, "getmempoolinfo")
        network = self.call(rpc_cfg, "getnetworkinfo")
        mining = self.call(rpc_cfg, "getmininginfo")

        def _fee_to_satvb(resp: dict) -> float:
            """Convert estimatesmartfee response to sat/vB. Handles feerate (BTC/kvB) and fee_rate (sat/vB)."""
            if not isinstance(resp, dict):
                return 0.0
            if "fee_rate" in resp and resp["fee_rate"] is not None:
                return float(resp["fee_rate"])
            feerate = resp.get("feerate")
            if feerate is None:
                return 0.0
            return float(feerate) * 1e5

        def _est_smartfee(conf_target: int, mode: str = "conservative") -> float:
            try:
                return _fee_to_satvb(self.call(rpc_cfg, "estimatesmartfee", [conf_target, mode]))
            except Exception:
                try:
                    return _fee_to_satvb(self.call(rpc_cfg, "estimatesmartfee", [conf_target]))
                except Exception:
                    return 0.0

        def _mempool_fee_tiers() -> tuple[float, float, float] | None:
            """Derive low/medium/high tiers from live mempool fee distribution.
            Uses cumulative-vsize cut points within the projected next block."""
            raw = self.call(rpc_cfg, "getrawmempool", [True])
            if not isinstance(raw, dict):
                return None
            items: list[tuple[float, int]] = []
            for info in raw.values():
                if not isinstance(info, dict):
                    continue
                vsize = int(info.get("vsize", info.get("size", 0)))
                if vsize <= 0:
                    continue
                fees = info.get("fees", {}) or {}
                fee_btc: float
                size_for_rate = vsize
                if isinstance(fees, dict) and fees.get("ancestor") is not None:
                    anc_size = int(info.get("ancestorsize", info.get("ancestorvsize", vsize)) or vsize)
                    if anc_size > 0:
                        fee_btc = float(fees["ancestor"])
                        size_for_rate = anc_size
                    else:
                        fee_btc = float(fees.get("base") or info.get("fee") or 0)
                elif isinstance(fees, dict):
                    fee_btc = float(fees.get("base") or fees.get("modified") or info.get("fee") or 0)
                else:
                    fee_btc = float(info.get("fee", 0) or 0)
                if fee_btc <= 0:
                    continue
                sat_per_vb = (fee_btc * 1e8) / size_for_rate
                items.append((sat_per_vb, vsize))
            if not items:
                return None
            items.sort(key=lambda x: -x[0])
            VB_PER_BLOCK = 1_000_000
            projected_block_vb = min(sum(vb for _, vb in items), VB_PER_BLOCK)
            # More aggressive bid for high, moderate for medium, near tail for low.
            high_target = max(1, int(projected_block_vb * 0.35))
            medium_target = max(1, int(projected_block_vb * 0.55))
            low_target = max(1, int(projected_block_vb * 0.80))
            cum_vb = 0
            high_fee: float | None = None
            medium_fee: float | None = None
            low_fee: float | None = None
            for sat_vb, vb in items:
                cum_vb += vb
                if high_fee is None and cum_vb >= high_target:
                    high_fee = sat_vb
                if medium_fee is None and cum_vb >= medium_target:
                    medium_fee = sat_vb
                if low_fee is None and cum_vb >= low_target:
                    low_fee = sat_vb
                if high_fee is not None and medium_fee is not None and low_fee is not None:
                    break
            fallback = items[-1][0]
            high_fee = high_fee if high_fee is not None else fallback
            medium_fee = medium_fee if medium_fee is not None else fallback
            low_fee = low_fee if low_fee is not None else fallback
            return (low_fee, medium_fee, high_fee)

        def _external_fee_tiers() -> tuple[float, float, float] | None:
            """Fetch fee tiers from public endpoints with fast fallback and short cache."""
            import time

            def _sanitize(low: float, medium: float, high: float) -> tuple[float, float, float] | None:
                if low <= 0 or medium <= 0 or high <= 0:
                    return None
                medium = max(medium, low)
                high = max(high, medium)
                return (low, medium, high)

            def _from_mempool_blocks(payload: Any) -> tuple[float, float, float] | None:
                if not isinstance(payload, list) or not payload:
                    return None
                first = payload[0] if isinstance(payload[0], dict) else {}
                second = payload[1] if len(payload) > 1 and isinstance(payload[1], dict) else {}
                fee_range = first.get("feeRange", []) if isinstance(first, dict) else []
                median_1 = float(first.get("medianFee", 0) or 0)
                median_2 = float(second.get("medianFee", 0) or 0)
                low = median_2 if median_2 > 0 else (float(fee_range[1]) if len(fee_range) > 1 else median_1)
                medium = median_1 if median_1 > 0 else (float(fee_range[2]) if len(fee_range) > 2 else 0.0)
                high = float(fee_range[4]) if len(fee_range) > 4 else (float(fee_range[-1]) if fee_range else medium)
                return _sanitize(low, medium, high)

            def _from_recommended(payload: Any) -> tuple[float, float, float] | None:
                if not isinstance(payload, dict):
                    return None
                low = float(payload.get("hourFee") or payload.get("economyFee") or payload.get("minimumFee") or 0)
                medium = float(payload.get("halfHourFee") or payload.get("hourFee") or low or 0)
                high = float(payload.get("fastestFee") or payload.get("halfHourFee") or medium or 0)
                return _sanitize(low, medium, high)

            def _from_blockstream(payload: Any) -> tuple[float, float, float] | None:
                if not isinstance(payload, dict):
                    return None
                rates: dict[int, float] = {}
                for k, v in payload.items():
                    try:
                        rates[int(k)] = float(v)
                    except (TypeError, ValueError):
                        continue
                if not rates:
                    return None

                def _pick(target: int) -> float:
                    if target in rates:
                        return rates[target]
                    higher = sorted([k for k in rates if k > target])
                    if higher:
                        return rates[higher[0]]
                    lower = sorted([k for k in rates if k < target], reverse=True)
                    if lower:
                        return rates[lower[0]]
                    return 0.0

                low = _pick(6)
                medium = _pick(3)
                high = _pick(2) or _pick(1)
                return _sanitize(low, medium, high)

            now = time.time()
            if self._external_fee_cache is not None and now - self._external_fee_cache_ts < 30:
                return self._external_fee_cache
            providers: list[tuple[str, Any]] = [
                ("https://mempool.space/api/v1/fees/mempool-blocks", _from_mempool_blocks),
                ("https://mempool.emzy.de/api/v1/fees/mempool-blocks", _from_mempool_blocks),
                ("https://mempool.space/api/v1/fees/recommended", _from_recommended),
                ("https://mempool.emzy.de/api/v1/fees/recommended", _from_recommended),
                ("https://blockstream.info/api/fee-estimates", _from_blockstream),
            ]
            for url, parser in providers:
                try:
                    resp = self._session().get(url, timeout=3)
                    resp.raise_for_status()
                    parsed = parser(resp.json())
                    if parsed is not None:
                        self._external_fee_cache = parsed
                        self._external_fee_cache_ts = now
                        return parsed
                except Exception:
                    continue
            return None

        fee_low_satvb: float
        fee_medium_satvb: float
        fee_high_satvb: float
        fee_source = "node"
        try:
            mempool_tiers = _external_fee_tiers()
            if mempool_tiers is not None:
                fee_source = "external"
            else:
                mempool_tiers = _mempool_fee_tiers()
                if mempool_tiers is not None:
                    fee_source = "local_mempool"
        except Exception:
            mempool_tiers = None
        esf_6 = _est_smartfee(6)
        esf_3 = _est_smartfee(3)
        esf_1 = _est_smartfee(1)
        min_fee_satvb = max(0.1, float(mempool.get("mempoolminfee", 0) or 0) * 1e5)
        if mempool_tiers is not None:
            fee_low_satvb, fee_medium_satvb, mempool_high = mempool_tiers
        else:
            fee_low_satvb, fee_medium_satvb, mempool_high = esf_6, esf_3, None
        if mempool_high is not None and esf_1 > (min_fee_satvb * 1.05):
            # Only apply the smartfee cap when it is meaningfully above floor.
            # If smartfee is pinned to min relay fee, use mempool estimate.
            fee_high_satvb = min(mempool_high, esf_1)
        elif mempool_high is not None:
            fee_high_satvb = mempool_high
        else:
            fee_high_satvb = esf_1

        recent_blocks: list[dict[str, Any]] = []
        for i in range(4):
            h = best_height - i
            if h < 0:
                break
            block_hash = self.call(rpc_cfg, "getblockhash", [h])
            blk = self.call(rpc_cfg, "getblock", [block_hash])
            recent_blocks.append(
                {
                    "height": blk.get("height", h),
                    "timestamp": blk.get("time", 0),
                    "tx_count": len(blk.get("tx", [])),
                    "size": blk.get("size", 0),
                    "hash": blk.get("hash", ""),
                }
            )

        subver = network.get("subversion")
        node_version = cli_ver
        if not node_version and isinstance(subver, str) and subver.strip():
            parts = [p for p in subver.split("/") if p.strip()]
            node_version = parts[0].strip() if parts else subver.strip("/").strip()

        return {
            "connected": True,
            "blocks": best_height,
            "headers": int(chain.get("headers", best_height) or best_height),
            "initialblockdownload": bool(chain.get("initialblockdownload", False)),
            "node_version": node_version,
            "difficulty": chain.get("difficulty", 0),
            "size_on_disk": chain.get("size_on_disk", 0),
            "verificationprogress": chain.get("verificationprogress", 0),
            "chain": chain.get("chain", "unknown"),
            "pruned": chain.get("pruned", False),
            "mempool_tx": mempool.get("size", 0),
            "mempool_size": mempool.get("bytes", 0),
            "mempool_bytes": mempool.get("bytes", 0),
            "mempool_minfee": mempool.get("mempoolminfee", 0),
            "connections": network.get("connections", 0),
            "networkhashps": mining.get("networkhashps", 0),
            "blocks_until_retarget": 2016 - (best_height % 2016),
            "difficulty_change": mining.get("difficulty", 0),
            "fee_low": fee_low_satvb,
            "fee_medium": fee_medium_satvb,
            "fee_high": fee_high_satvb,
            "fee_source": fee_source,
            "recent_blocks": recent_blocks,
        }

    def get_sync_snapshot(self, rpc_cfg: dict[str, Any]) -> dict[str, Any]:
        """Live chain sync fields for /display IBD overlay (minimal RPC)."""
        chain = self.call(rpc_cfg, "getblockchaininfo")
        network = self.call(rpc_cfg, "getnetworkinfo")
        best_height = int(chain.get("blocks", 0) or 0)
        headers = int(chain.get("headers", best_height) or 0)
        return {
            "connected": True,
            "blocks": best_height,
            "headers": headers,
            "initialblockdownload": bool(chain.get("initialblockdownload", False)),
            "verificationprogress": float(chain.get("verificationprogress", 0) or 0),
            "size_on_disk": int(chain.get("size_on_disk", 0) or 0),
            "connections": int(network.get("connections", 0) or 0),
        }

    def get_mempool_txs(self, rpc_cfg: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
        """Fetch mempool txs for treemap visualization, sorted by max(fee_sats, vsize) descending.

        When ``limit`` is set, only that many largest entries are returned (treemap cell sizes stay
        large enough for the same flash animation as the interactive /mempool view).
        """
        raw = self.call(rpc_cfg, "getrawmempool", [True])
        if not isinstance(raw, dict):
            return []
        entries = []
        for txid, info in raw.items():
            fee_btc = 0.0
            if isinstance(info, dict):
                fees = info.get("fees", {}) or {}
                fee_btc = float(fees.get("base", info.get("fee", 0)) or 0)
            fee_sats = int(round(fee_btc * 1e8))
            vsize = int(info.get("vsize", 0)) if isinstance(info, dict) else 0
            if fee_sats <= 0 and vsize <= 0:
                continue
            size_val = max(fee_sats, vsize)
            entries.append({"txid": txid, "fee_sats": fee_sats, "vsize": vsize, "size": size_val})
        if limit is not None and limit > 0:
            return heapq.nlargest(limit, entries, key=lambda x: x["size"])
        entries.sort(key=lambda x: -x["size"])
        return entries

    def _enrich_vin_prevouts(self, rpc_cfg: dict[str, Any], tx: dict[str, Any]) -> None:
        """Fill missing input amounts (mempool txs often omit prevout from getrawtransaction)."""
        parent_cache: dict[str, dict[str, Any]] = {}
        for vin in tx.get("vin") or []:
            if not isinstance(vin, dict) or vin.get("coinbase"):
                continue
            prevout = vin.get("prevout")
            if isinstance(prevout, dict) and prevout.get("value") is not None:
                continue
            spent_txid = vin.get("txid")
            spent_vout = vin.get("vout")
            if spent_txid is None or spent_vout is None:
                continue

            enriched: dict[str, Any] | None = None
            try:
                utxo = self.call(rpc_cfg, "gettxout", [spent_txid, spent_vout])
                if isinstance(utxo, dict) and utxo.get("value") is not None:
                    enriched = {
                        "value": utxo.get("value"),
                        "scriptPubKey": utxo.get("scriptPubKey") or {},
                    }
            except Exception:
                pass

            if enriched is None or enriched.get("value") is None:
                try:
                    if spent_txid not in parent_cache:
                        parent = self.call(rpc_cfg, "getrawtransaction", [spent_txid, True])
                        parent_cache[spent_txid] = parent if isinstance(parent, dict) else {}
                    parent = parent_cache.get(spent_txid) or {}
                    vouts = parent.get("vout") or []
                    idx = int(spent_vout)
                    if 0 <= idx < len(vouts) and isinstance(vouts[idx], dict):
                        pv = vouts[idx]
                        enriched = {
                            "value": pv.get("value"),
                            "scriptPubKey": pv.get("scriptPubKey") or {},
                        }
                except Exception:
                    pass

            if not enriched or enriched.get("value") is None:
                continue
            base = prevout if isinstance(prevout, dict) else {}
            spk = {**(base.get("scriptPubKey") or {}), **(enriched.get("scriptPubKey") or {})}
            vin["prevout"] = {**base, **enriched, "scriptPubKey": spk}

    def get_tx_details(self, rpc_cfg: dict[str, Any], txid: str) -> dict[str, Any] | None:
        """Fetch decoded transaction with vin/vout for inputs/outputs graph (verbosity 2 = prevout)."""
        for verbose in (2, True):
            try:
                tx = self.call(rpc_cfg, "getrawtransaction", [txid, verbose])
                if not isinstance(tx, dict):
                    return None
                self._enrich_vin_prevouts(rpc_cfg, tx)
                return tx
            except Exception:
                if verbose is True:
                    return None
                continue
        return None

