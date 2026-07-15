#!/usr/bin/env bash
# Automatic bitcoind chain/chainstate corruption recovery.
#
# Watches bitcoind logs for fatal database corruption, starts -reindex-chainstate
# via reindex-chainstate.sh, and removes the one-time override once the chain
# is fully synced again.
#
# Usage:
#   sudo ./scripts/chain-guard.sh auto     # normal mode (systemd timer)
#   sudo ./scripts/chain-guard.sh status   # human-readable state
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REINDEX_SCRIPT="${SCRIPT_DIR}/reindex-chainstate.sh"
DROPIN_FILE="/etc/systemd/system/bitcoind.service.d/reindex-chainstate.conf"
STATE_DIR="/var/lib/blockvase"
STATE_FILE="${STATE_DIR}/chain-guard.state"
COOLDOWN_SEC="${BLOCKVASE_CHAIN_GUARD_COOLDOWN_SEC:-43200}"
BITCOIN_CLI="${BITCOIN_CLI:-/usr/local/bin/bitcoin-cli}"
CONF="${BITCOIN_CONF:-/etc/bitcoin/bitcoin.conf}"
JOURNAL_SINCE="${BLOCKVASE_CHAIN_GUARD_JOURNAL_SINCE:-3 hours ago}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0 $*"
  exit 1
fi

log() {
  local msg="$*"
  logger -t blockvase-chain-guard "${msg}" 2>/dev/null || true
  echo "$(date -Is) ${msg}"
}

read_state() {
  [[ -f "${STATE_FILE}" ]] || return 0
  # shellcheck disable=SC1090
  source "${STATE_FILE}" 2>/dev/null || true
}

write_state() {
  mkdir -p "${STATE_DIR}"
  cat >"${STATE_FILE}" <<EOF
last_auto_reindex=${last_auto_reindex:-0}
EOF
}

is_reindex_active() {
  [[ -f "${DROPIN_FILE}" ]]
}

bitcoind_active() {
  systemctl is-active --quiet bitcoind.service
}

rpc_ok() {
  bitcoind_active || return 1
  command -v "${BITCOIN_CLI}" >/dev/null 2>&1 || return 1
  sudo -u bitcoin "${BITCOIN_CLI}" -conf="${CONF}" getblockchaininfo >/dev/null 2>&1
}

chain_info_json() {
  sudo -u bitcoin "${BITCOIN_CLI}" -conf="${CONF}" getblockchaininfo 2>/dev/null
}

chain_fully_synced() {
  if ! rpc_ok; then
    return 1
  fi
  chain_info_json | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
vp = float(d.get("verificationprogress", 0) or 0)
ibd = bool(d.get("initialblockdownload", True))
blocks = int(d.get("blocks", 0) or 0)
headers = int(d.get("headers", blocks) or blocks)
ok = (not ibd) and vp >= 0.99999 and (headers - blocks) <= 1
sys.exit(0 if ok else 1)
'
}

detect_corruption() {
  # Ignore stale corruption lines once RPC is healthy again (e.g. after recovery).
  if rpc_ok; then
    return 0
  fi
  journalctl -u bitcoind.service --since "${JOURNAL_SINCE}" --no-pager 2>/dev/null | \
    grep -iE \
      'Error initializing block database|Please restart with -reindex|Corrupted block database detected|Fatal LevelDB|Chainstate db corruption|database corruption detected' | \
    grep -viE 'reindexing chainstate|Reindexing blocks|started reindex' | \
    tail -1
}

cooldown_active() {
  read_state
  local now last
  now="$(date +%s)"
  last="${last_auto_reindex:-0}"
  if [[ "${last}" =~ ^[0-9]+$ && $((now - last)) -lt "${COOLDOWN_SEC}" ]]; then
    return 0
  fi
  return 1
}

auto_finish_if_ready() {
  if ! is_reindex_active; then
    return 0
  fi
  if ! chain_fully_synced; then
    return 0
  fi
  log "Chain fully synced after reindex-chainstate; restoring normal bitcoind startup"
  "${REINDEX_SCRIPT}" finish
  read_state
  last_auto_reindex=0
  write_state
}

auto_start_if_corrupt() {
  if is_reindex_active; then
    return 0
  fi
  if [[ -z "$(detect_corruption || true)" ]]; then
    return 0
  fi
  if cooldown_active; then
    log "Chain corruption detected, but auto-reindex cooldown is still active"
    return 0
  fi
  if [[ ! -x "${REINDEX_SCRIPT}" ]]; then
    log "Chain corruption detected, but ${REINDEX_SCRIPT} is missing"
    return 1
  fi
  log "Chain corruption detected; starting automatic -reindex-chainstate recovery"
  "${REINDEX_SCRIPT}" start
  last_auto_reindex="$(date +%s)"
  write_state
}

cmd_status() {
  echo "Chain guard status"
  echo "  Reindex override: $(is_reindex_active && echo ACTIVE || echo inactive)"
  echo "  bitcoind: $(systemctl is-active bitcoind.service 2>/dev/null || echo unknown)"
  if is_reindex_active && chain_fully_synced; then
    echo "  Chain: synced (ready to auto-finish reindex override)"
  elif rpc_ok; then
    chain_info_json | python3 -c '
import json, sys
d = json.load(sys.stdin)
vp = float(d.get("verificationprogress", 0) or 0)
print(
  "  Chain: ibd={} verification={:.2f}% blocks={} headers={}".format(
    d.get("initialblockdownload"), vp * 100, d.get("blocks"), d.get("headers")
  )
)
' || echo "  Chain: RPC unavailable"
  else
    echo "  Chain: node not running"
  fi
  if match="$(detect_corruption || true)"; then
    echo "  Corruption signal: ${match}"
  else
    echo "  Corruption signal: none in journal since ${JOURNAL_SINCE}"
  fi
  if cooldown_active; then
    read_state
    echo "  Auto-reindex cooldown: active (last=${last_auto_reindex:-0})"
  else
    echo "  Auto-reindex cooldown: inactive"
  fi
}

cmd_auto() {
  auto_finish_if_ready
  auto_start_if_corrupt
}

case "${1:-}" in
  auto) cmd_auto ;;
  status) cmd_status ;;
  *)
    echo "Usage: sudo $0 {auto|status}"
    exit 1
    ;;
esac
