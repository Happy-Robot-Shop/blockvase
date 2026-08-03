"""Local Knots wallet helpers for mining payouts and portal spend/receive."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .bitcoin_rpc import BitcoinRpcClient

WALLET_NAME = "blockvase"
SPEND_WALLET_NAME = "blockvase-spend"
ADDRESS_LABEL = "mining-payout"
SPEND_ADDRESS_LABEL = "spend-receive"
# Wallet RPC can be slow while bitcoind is under IBD / reindex load.
WALLET_RPC_TIMEOUT_SEC = 120
_BTC_AMOUNT_RE = re.compile(r"^\d+(\.\d{1,8})?$")
_SAT_QUANT = Decimal("0.00000001")


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


def _ensure_named_wallet(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], name: str) -> str:
    cfg = wallet_rpc_cfg(rpc_cfg)
    loaded = rpc.call(cfg, "listwallets") or []
    if name in loaded:
        return name
    try:
        rpc.call(cfg, "loadwallet", [name])
        return name
    except RuntimeError:
        pass
    # createwallet(name, disable_private_keys=False, blank=False, passphrase="",
    #              avoid_reuse=False, descriptors=True, load_on_startup=True)
    rpc.call(
        cfg,
        "createwallet",
        [name, False, False, "", False, True, True],
    )
    return name


def ensure_mining_wallet(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any]) -> str:
    """Load or create the device mining wallet. Works during IBD. Returns wallet name."""
    return _ensure_named_wallet(rpc, rpc_cfg, WALLET_NAME)


def ensure_spend_wallet(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any]) -> str:
    """Load or create the portal spend/receive wallet. Returns wallet name."""
    return _ensure_named_wallet(rpc, rpc_cfg, SPEND_WALLET_NAME)


def _load_existing_wallet(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], name: str) -> bool:
    """Load wallet if it already exists on disk. Never creates. Returns True if loaded."""
    cfg = wallet_rpc_cfg(rpc_cfg)
    loaded = rpc.call(cfg, "listwallets") or []
    if name in loaded:
        return True
    try:
        listing = rpc.call(cfg, "listwalletdir") or {}
        wallets = listing.get("wallets") if isinstance(listing, dict) else None
        names: set[str] = set()
        if isinstance(wallets, list):
            for row in wallets:
                if isinstance(row, dict) and row.get("name") is not None:
                    names.add(str(row.get("name")))
                elif isinstance(row, str):
                    names.add(row)
        if names and name not in names:
            return False
    except RuntimeError:
        # Older nodes / restricted RPC — fall through to loadwallet probe.
        pass
    try:
        rpc.call(cfg, "loadwallet", [name])
        return True
    except RuntimeError:
        return False


def _new_address_in_wallet(
    rpc: BitcoinRpcClient,
    rpc_cfg: dict[str, Any],
    *,
    wallet_name: str,
    label: str,
) -> str:
    cfg = wallet_rpc_cfg(rpc_cfg)
    addr = rpc.call(
        cfg,
        "getnewaddress",
        [label, "bech32"],
        wallet=wallet_name,
    )
    if not isinstance(addr, str) or not addr.strip():
        raise RuntimeError("getnewaddress returned an empty address")
    return addr.strip()


def new_mining_payout_address(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any]) -> str:
    """Fresh bech32 payout address in the portal spend wallet (IBD-safe).

    Default solo-mining rewards land in the same wallet users send/receive from.
    Settings can still override with any external address.
    """
    ensure_spend_wallet(rpc, rpc_cfg)
    return _new_address_in_wallet(
        rpc, rpc_cfg, wallet_name=SPEND_WALLET_NAME, label=ADDRESS_LABEL
    )


def new_spend_receive_address(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any]) -> str:
    """Fresh bech32 receive address in the portal spend wallet."""
    ensure_spend_wallet(rpc, rpc_cfg)
    return _new_address_in_wallet(
        rpc, rpc_cfg, wallet_name=SPEND_WALLET_NAME, label=SPEND_ADDRESS_LABEL
    )


def _address_ismine_in_wallet(
    rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], address: str, wallet_name: str
) -> bool:
    try:
        info = rpc.call(
            wallet_rpc_cfg(rpc_cfg),
            "getaddressinfo",
            [address],
            wallet=wallet_name,
        )
    except RuntimeError:
        return False
    return bool(isinstance(info, dict) and info.get("ismine"))


def address_is_spend_mine(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], address: str) -> bool:
    """True if address belongs to the portal spend wallet."""
    if not address:
        return False
    try:
        ensure_spend_wallet(rpc, rpc_cfg)
        return _address_ismine_in_wallet(rpc, rpc_cfg, address, SPEND_WALLET_NAME)
    except RuntimeError:
        return False


def address_is_legacy_mining_mine(
    rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], address: str
) -> bool:
    """True if address belongs to the legacy mining-only wallet.

    Does not create the legacy wallet — missing wallet means not mine.
    """
    if not address:
        return False
    try:
        if not _load_existing_wallet(rpc, rpc_cfg, WALLET_NAME):
            return False
        return _address_ismine_in_wallet(rpc, rpc_cfg, address, WALLET_NAME)
    except RuntimeError:
        return False


def address_is_mine(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], address: str) -> bool:
    """True if address belongs to the spend wallet (or legacy mining wallet)."""
    return address_is_spend_mine(rpc, rpc_cfg, address) or address_is_legacy_mining_mine(
        rpc, rpc_cfg, address
    )


def legacy_mining_wallet_balances(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any]) -> dict[str, str]:
    """Balances in the legacy mining wallet (may hold pre-split funds).

    Does not create the legacy wallet if it was wiped / never existed.
    """
    zero = {
        "trusted": "0.00000000",
        "untrusted_pending": "0.00000000",
        "immature": "0.00000000",
    }
    try:
        if not _load_existing_wallet(rpc, rpc_cfg, WALLET_NAME):
            return zero
    except RuntimeError:
        return zero
    return _wallet_balances_for(rpc, rpc_cfg, WALLET_NAME)


def validate_bitcoin_address(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], address: str) -> bool:
    try:
        info = rpc.call(wallet_rpc_cfg(rpc_cfg), "validateaddress", [address])
    except RuntimeError:
        return False
    return bool(isinstance(info, dict) and info.get("isvalid"))


def format_btc_decimal(value: Decimal | float | int | str) -> str:
    """Normalize a BTC amount to an 8-decimal string (no float round-trip)."""
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "0.00000000"
    return format(d.quantize(_SAT_QUANT), "f")


def parse_btc_amount(raw: Any) -> Decimal:
    """Parse a user/API BTC amount as Decimal.

    Accepts decimal strings or Decimal. Rejects float (binary rounding) and bool.
    Plain ints are allowed (exact whole BTC) for non-JS clients.
    """
    if isinstance(raw, bool):
        raise RuntimeError("Amount must be a decimal string")
    if isinstance(raw, float):
        raise RuntimeError("Amount must be a decimal string, not a floating-point number")
    if isinstance(raw, Decimal):
        amount = raw
    elif isinstance(raw, int):
        amount = Decimal(raw)
    else:
        s = str(raw if raw is not None else "").strip()
        if not s or not _BTC_AMOUNT_RE.match(s):
            raise RuntimeError("Amount must be a positive BTC value with up to 8 decimal places")
        try:
            amount = Decimal(s)
        except InvalidOperation as ex:
            raise RuntimeError("Amount is not a valid BTC value") from ex
    if amount <= 0:
        raise RuntimeError("Amount must be greater than zero")
    quantized = amount.quantize(_SAT_QUANT)
    if amount != quantized:
        raise RuntimeError("Amount must have at most 8 decimal places")
    return quantized


def _wallet_balances_for(
    rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], wallet_name: str
) -> dict[str, str]:
    cfg = wallet_rpc_cfg(rpc_cfg)
    try:
        balances = rpc.call(cfg, "getbalances", wallet=wallet_name)
        if isinstance(balances, dict) and isinstance(balances.get("mine"), dict):
            mine = balances["mine"]
            return {
                "trusted": format_btc_decimal(mine.get("trusted") or 0),
                "untrusted_pending": format_btc_decimal(mine.get("untrusted_pending") or 0),
                "immature": format_btc_decimal(mine.get("immature") or 0),
            }
    except RuntimeError:
        pass
    bal = rpc.call(cfg, "getbalance", ["*", 0, True], wallet=wallet_name) or 0
    return {
        "trusted": format_btc_decimal(bal),
        "untrusted_pending": "0.00000000",
        "immature": "0.00000000",
    }


def _wallet_recent_transactions_for(
    rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], wallet_name: str, *, count: int = 15
) -> list[dict[str, Any]]:
    cfg = wallet_rpc_cfg(rpc_cfg)
    rows = rpc.call(cfg, "listtransactions", ["*", int(count), 0, True], wallet=wallet_name) or []
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
                "amount": format_btc_decimal(row.get("amount") or 0),
                "confirmations": int(row.get("confirmations") or 0),
                "time": int(row.get("time") or row.get("timereceived") or 0),
                "address": str(row.get("address") or ""),
                "fee": format_btc_decimal(row["fee"]) if row.get("fee") is not None else None,
            }
        )
    return out


def spend_wallet_balances(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any]) -> dict[str, str]:
    """Trusted / untrusted / immature balances (8-dp strings) for the spend wallet."""
    ensure_spend_wallet(rpc, rpc_cfg)
    return _wallet_balances_for(rpc, rpc_cfg, SPEND_WALLET_NAME)


def spend_wallet_recent_transactions(
    rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any], *, count: int = 15
) -> list[dict[str, Any]]:
    ensure_spend_wallet(rpc, rpc_cfg)
    return _wallet_recent_transactions_for(rpc, rpc_cfg, SPEND_WALLET_NAME, count=count)


def send_from_spend_wallet(
    rpc: BitcoinRpcClient,
    rpc_cfg: dict[str, Any],
    *,
    address: str,
    amount_btc: str | Decimal,
    subtract_fee_from_amount: bool = False,
) -> str:
    """Send BTC from the portal spend wallet. Returns txid. Raises RuntimeError on failure."""
    amount = parse_btc_amount(amount_btc)
    address = (address or "").strip()
    if not address:
        raise RuntimeError("Destination address is not valid")
    if not validate_bitcoin_address(rpc, rpc_cfg, address):
        raise RuntimeError("Destination address is not valid")
    ensure_spend_wallet(rpc, rpc_cfg)
    balances = spend_wallet_balances(rpc, rpc_cfg)
    trusted = Decimal(balances["trusted"])
    if amount > trusted:
        raise RuntimeError(
            f"Amount exceeds available balance ({balances['trusted']} BTC)"
        )
    # When the fee is paid on top of the amount, leave room for the miner fee.
    if not subtract_fee_from_amount and amount >= trusted:
        raise RuntimeError(
            "Amount leaves no room for the network fee. "
            "Lower the amount or enable “Subtract fee from amount”."
        )
    cfg = wallet_rpc_cfg(rpc_cfg)
    amount_str = format_btc_decimal(amount)
    # sendtoaddress accepts a decimal string to avoid binary float rounding.
    txid = rpc.call(
        cfg,
        "sendtoaddress",
        [address, amount_str, "", "", bool(subtract_fee_from_amount)],
        wallet=SPEND_WALLET_NAME,
    )
    if not isinstance(txid, str) or not txid.strip():
        raise RuntimeError("sendtoaddress did not return a transaction id")
    return txid.strip()


def export_spend_wallet_descriptors(rpc: BitcoinRpcClient, rpc_cfg: dict[str, Any]) -> str:
    """
    Export private descriptors for the spend wallet as a text backup.

    Caller must enforce step-up admin auth before invoking.
    """
    ensure_spend_wallet(rpc, rpc_cfg)
    cfg = wallet_rpc_cfg(rpc_cfg)
    result = rpc.call(cfg, "listdescriptors", [True], wallet=SPEND_WALLET_NAME)
    if not isinstance(result, dict):
        raise RuntimeError("listdescriptors returned an unexpected response")
    descriptors = result.get("descriptors")
    if not isinstance(descriptors, list) or not descriptors:
        raise RuntimeError("No descriptors available to export")
    lines = [
        "# Blockvase spend wallet descriptor backup",
        f"# wallet={SPEND_WALLET_NAME}",
        "# Keep this file offline and secret. Anyone with it can spend your funds.",
        "",
    ]
    for row in descriptors:
        if not isinstance(row, dict):
            continue
        desc = str(row.get("desc") or "").strip()
        if not desc:
            continue
        active = "active" if row.get("active") else "inactive"
        internal = "internal" if row.get("internal") else "external"
        lines.append(f"# {active} {internal}")
        lines.append(desc)
        lines.append("")
    exported = [ln for ln in lines if ln and not ln.startswith("#")]
    if not exported or not any("(" in ln for ln in exported):
        raise RuntimeError("Descriptor export was empty")
    return "\n".join(lines).strip() + "\n"
