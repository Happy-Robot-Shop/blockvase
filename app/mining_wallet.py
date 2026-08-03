"""Local Knots wallet helpers for solo-mining payout addresses."""
from __future__ import annotations

from typing import Any

from .bitcoin_rpc import BitcoinRpcClient

WALLET_NAME = "blockvase"
ADDRESS_LABEL = "mining-payout"
# Wallet RPC can be slow while bitcoind is under IBD / reindex load.
WALLET_RPC_TIMEOUT_SEC = 120


def wallet_rpc_cfg(rpc_cfg: dict[str, Any]) -> dict[str, Any]:
    """Copy RPC config with a longer timeout for wallet create/load/address calls."""
    out = dict(rpc_cfg or {})
    out["timeout_seconds"] = max(int(out.get("timeout_seconds") or 8), WALLET_RPC_TIMEOUT_SEC)
    return out


def node_sync_status(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any]) -> dict[str, Any]:
    """
    Whether the node can usefully serve mining templates.

    Address generation does NOT require sync; DATUM/GBT does.
    If chain info is unavailable, treat as not-ready (defer DATUM).
    """
    cfg = dict(rpc_cfg or {})
    # Prefer a modest timeout so Settings stays responsive; unknown => not ready.
    cfg["timeout_seconds"] = min(int(cfg.get("timeout_seconds") or 8), 15)
    try:
        info = rpc.call(cfg, "getblockchaininfo")
    except RuntimeError as ex:
        return {
            "ready": False,
            "initialblockdownload": True,
            "blocks": 0,
            "headers": 0,
            "verificationprogress": 0.0,
            "error": str(ex),
        }
    if not isinstance(info, dict):
        return {
            "ready": False,
            "initialblockdownload": True,
            "blocks": 0,
            "headers": 0,
            "verificationprogress": 0.0,
            "error": "invalid getblockchaininfo",
        }
    ibd = bool(info.get("initialblockdownload", True))
    blocks = int(info.get("blocks") or 0)
    headers = int(info.get("headers") or 0)
    # Catch-up: still downloading headers/blocks even if IBD flag just flipped.
    catching_up = headers > 0 and blocks + 2 < headers
    ready = (not ibd) and (not catching_up) and blocks > 0
    return {
        "ready": ready,
        "initialblockdownload": ibd or catching_up,
        "blocks": blocks,
        "headers": headers,
        "verificationprogress": float(info.get("verificationprogress") or 0),
        "error": "",
    }


def ensure_mining_wallet(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any]) -> str:
    """Load or create the device mining wallet. Works during IBD. Returns wallet name."""
    cfg = wallet_rpc_cfg(rpc_cfg)
    loaded = rpc.call(cfg, "listwallets") or []
    if WALLET_NAME in loaded:
        return WALLET_NAME

    # Wallet may exist on disk but not be loaded after restart.
    try:
        rpc.call(cfg, "loadwallet", [WALLET_NAME])
        return WALLET_NAME
    except RuntimeError:
        pass

    # createwallet(name, disable_private_keys=False, blank=False, passphrase="",
    #              avoid_reuse=False, descriptors=True, load_on_startup=True)
    rpc.call(
        cfg,
        "createwallet",
        [WALLET_NAME, False, False, "", False, True, True],
    )
    return WALLET_NAME


def new_mining_payout_address(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any]) -> str:
    """Ensure wallet exists and return a fresh bech32 receive address (IBD-safe)."""
    ensure_mining_wallet(rpc, rpc_cfg)
    cfg = wallet_rpc_cfg(rpc_cfg)
    addr = rpc.call(
        cfg,
        "getnewaddress",
        [ADDRESS_LABEL, "bech32"],
        wallet=WALLET_NAME,
    )
    if not isinstance(addr, str) or not addr.strip():
        raise RuntimeError("getnewaddress returned an empty address")
    return addr.strip()


def address_is_mine(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], address: str) -> bool:
    """True if address belongs to the local mining wallet."""
    if not address:
        return False
    try:
        ensure_mining_wallet(rpc, rpc_cfg)
        info = rpc.call(
            wallet_rpc_cfg(rpc_cfg),
            "getaddressinfo",
            [address],
            wallet=WALLET_NAME,
        )
    except RuntimeError:
        return False
    return bool(isinstance(info, dict) and info.get("ismine"))


def validate_bitcoin_address(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], address: str) -> bool:
    try:
        info = rpc.call(wallet_rpc_cfg(rpc_cfg), "validateaddress", [address])
    except RuntimeError:
        return False
    return bool(isinstance(info, dict) and info.get("isvalid"))


def wallet_balances(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any]) -> dict[str, float]:
    """Trusted / untrusted / immature balances in BTC for the device wallet."""
    ensure_mining_wallet(rpc, rpc_cfg)
    cfg = wallet_rpc_cfg(rpc_cfg)
    try:
        balances = rpc.call(cfg, "getbalances", wallet=WALLET_NAME)
        if isinstance(balances, dict) and isinstance(balances.get("mine"), dict):
            mine = balances["mine"]
            return {
                "trusted": float(mine.get("trusted") or 0),
                "untrusted_pending": float(mine.get("untrusted_pending") or 0),
                "immature": float(mine.get("immature") or 0),
            }
    except RuntimeError:
        pass
    bal = float(rpc.call(cfg, "getbalance", ["*", 0, True], wallet=WALLET_NAME) or 0)
    return {"trusted": bal, "untrusted_pending": 0.0, "immature": 0.0}


def wallet_recent_transactions(
    rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], *, count: int = 15
) -> list[dict[str, Any]]:
    ensure_mining_wallet(rpc, rpc_cfg)
    cfg = wallet_rpc_cfg(rpc_cfg)
    rows = rpc.call(cfg, "listtransactions", ["*", int(count), 0, True], wallet=WALLET_NAME) or []
    out: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "txid": str(row.get("txid") or ""),
                "category": str(row.get("category") or ""),
                "amount": float(row.get("amount") or 0),
                "confirmations": int(row.get("confirmations") or 0),
                "time": int(row.get("time") or row.get("timereceived") or 0),
                "address": str(row.get("address") or ""),
                "fee": float(row["fee"]) if row.get("fee") is not None else None,
            }
        )
    return out


def send_from_wallet(
    rpc: BitcoinRpcClient,
    rpc_cfg: dict[str, Any],
    *,
    address: str,
    amount_btc: float,
    subtract_fee_from_amount: bool = False,
) -> str:
    """Send BTC from the device wallet. Returns txid. Raises RuntimeError on failure."""
    if amount_btc <= 0:
        raise RuntimeError("Amount must be greater than zero")
    if not validate_bitcoin_address(rpc, rpc_cfg, address):
        raise RuntimeError("Destination address is not valid")
    ensure_mining_wallet(rpc, rpc_cfg)
    cfg = wallet_rpc_cfg(rpc_cfg)
    # sendtoaddress btc_address amount comment comment_to subtractfeefromamount
    txid = rpc.call(
        cfg,
        "sendtoaddress",
        [address, amount_btc, "", "", bool(subtract_fee_from_amount)],
        wallet=WALLET_NAME,
    )
    if not isinstance(txid, str) or not txid.strip():
        raise RuntimeError("sendtoaddress did not return a transaction id")
    return txid.strip()
