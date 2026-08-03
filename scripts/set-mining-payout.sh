#!/usr/bin/env bash
# Apply the solo mining payout address to DATUM Gateway + PiAxe-miner runtime config.
#
# Usage:
#   set-mining-payout.sh <bitcoin-address>
#   set-mining-payout.sh --ensure-services
#       Re-read /etc/blockvase/solo_mining_address and start DATUM only when the
#       node is synced enough for getblocktemplate (not during IBD / catch-up).
set -euo pipefail

BITCOIN_CONF="/etc/bitcoin/bitcoin.conf"
BITCOIN_DATADIR="${BITCOIN_DATADIR:-/var/lib/bitcoind}"
BITCOIN_CLI="${BITCOIN_CLI:-/usr/local/bin/bitcoin-cli}"
DATUM_CONFIG="/etc/blockvase/datum_gateway_config.json"
MINER_ENV="/etc/blockvase/miner.env"
ADDRESS_FILE="/etc/blockvase/solo_mining_address"
SERVICE_USER="${BLOCKVASE_SERVICE_USER:-${SUDO_USER:-blockvase}}"
if [[ -z "${SERVICE_USER}" || "${SERVICE_USER}" == "root" ]]; then
  SERVICE_USER="blockvase"
fi

MODE="set"
ADDRESS="${1:-}"
if [[ "${ADDRESS}" == "--ensure-services" ]]; then
  MODE="ensure"
  ADDRESS=""
fi

ADDRESS="${ADDRESS#"${ADDRESS%%[![:space:]]*}"}"
ADDRESS="${ADDRESS%"${ADDRESS##*[![:space:]]}"}"
ADDRESS="${ADDRESS//$'\r'/}"

if [[ "${MODE}" == "set" && -z "${ADDRESS}" ]]; then
  echo "Usage: $0 <bitcoin-address>" >&2
  echo "       $0 --ensure-services" >&2
  exit 2
fi

if [[ ! -f "${BITCOIN_CONF}" ]]; then
  echo "Missing ${BITCOIN_CONF}" >&2
  exit 1
fi

RPC_USER="$(grep -E '^[[:space:]]*rpcuser=' "${BITCOIN_CONF}" | head -1 | cut -d= -f2- | tr -d ' \r' || true)"
RPC_PASS="$(grep -E '^[[:space:]]*rpcpassword=' "${BITCOIN_CONF}" | head -1 | cut -d= -f2- | tr -d ' \r' || true)"
if [[ -z "${RPC_USER}" || -z "${RPC_PASS}" ]]; then
  echo "Could not read rpcuser/rpcpassword from ${BITCOIN_CONF}" >&2
  exit 1
fi

node_ready_for_mining() {
  # Address generation is IBD-safe; DATUM needs a synced tip for GBT.
  local json ibd blocks headers
  json="$("${BITCOIN_CLI}" -conf="${BITCOIN_CONF}" -datadir="${BITCOIN_DATADIR}" \
    getblockchaininfo 2>/dev/null || true)"
  if [[ -z "${json}" ]]; then
    return 1
  fi
  ibd="$(python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print("1" if d.get("initialblockdownload") else "0")' <<<"${json}" 2>/dev/null || echo 1)"
  blocks="$(python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print(int(d.get("blocks") or 0))' <<<"${json}" 2>/dev/null || echo 0)"
  headers="$(python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print(int(d.get("headers") or 0))' <<<"${json}" 2>/dev/null || echo 0)"
  if [[ "${ibd}" == "1" ]]; then
    return 1
  fi
  if [[ "${blocks}" -le 0 ]]; then
    return 1
  fi
  # Still catching headers (common right after enabling P2P).
  if [[ "${headers}" -gt 0 && $((blocks + 2)) -lt "${headers}" ]]; then
    return 1
  fi
  return 0
}

write_datum_config() {
  local addr="$1"
  if [[ -f "${DATUM_CONFIG}" ]]; then
    local tmp
    tmp="$(mktemp)"
    jq --arg addr "${addr}" '.mining.pool_address = $addr' "${DATUM_CONFIG}" >"${tmp}"
    cat "${tmp}" >"${DATUM_CONFIG}"
    rm -f "${tmp}"
  else
    jq --null-input \
      --arg u "${RPC_USER}" \
      --arg p "${RPC_PASS}" \
      --arg addr "${addr}" \
      '{
        bitcoind: {
          rpcuser: $u,
          rpcpassword: $p,
          rpcurl: "http://127.0.0.1:8332",
          notify_fallback: true
        },
        stratum: { listen_addr: "127.0.0.1", listen_port: 23334 },
        mining: {
          pool_address: $addr,
          coinbase_tag_primary: "Blockvase",
          coinbase_tag_secondary: "DATUM solo",
          coinbase_unique_id: 4242
        },
        api: { admin_password: "", listen_addr: "127.0.0.1", listen_port: 7152, modify_conf: false },
        logger: {
          log_to_console: true,
          log_to_file: false,
          log_file: "/var/log/datum_gateway.log",
          log_rotate_daily: true,
          log_level_console: 2,
          log_level_file: 1
        },
        datum: {
          pool_host: "",
          pool_pass_workers: true,
          pool_pass_full_users: true,
          pooled_mining_only: false
        }
      }' >"${DATUM_CONFIG}"
  fi
  chown "root:${SERVICE_USER}" "${DATUM_CONFIG}" 2>/dev/null || chown root:root "${DATUM_CONFIG}"
  chmod 0640 "${DATUM_CONFIG}"
}

start_or_defer_datum() {
  # Miner can monitor the board anytime; DATUM waits for a synced node.
  systemctl enable blockvase-miner.service >/dev/null 2>&1 || true
  systemctl restart blockvase-miner.service >/dev/null 2>&1 || systemctl start blockvase-miner.service >/dev/null 2>&1 || true
  systemctl enable datum-gateway.service >/dev/null 2>&1 || true

  if node_ready_for_mining; then
    systemctl restart datum-gateway.service >/dev/null 2>&1 || systemctl start datum-gateway.service >/dev/null 2>&1 || true
    echo "set-mining-payout: DATUM started (node synced)"
    return 0
  fi

  systemctl stop datum-gateway.service >/dev/null 2>&1 || true
  echo "set-mining-payout: payout saved; DATUM deferred until IBD/catch-up finishes"
  return 0
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer sibling refresh script; fall back to installed path.
REFRESH_SCRIPT="${SCRIPT_DIR}/blockvase-miner-refresh-env.sh"
if [[ ! -x "${REFRESH_SCRIPT}" && -x /usr/lib/blockvase/blockvase-miner-refresh-env.sh ]]; then
  REFRESH_SCRIPT="/usr/lib/blockvase/blockvase-miner-refresh-env.sh"
fi

if [[ "${MODE}" == "ensure" ]]; then
  if [[ ! -s "${ADDRESS_FILE}" ]]; then
    echo "set-mining-payout: no payout address configured; nothing to ensure"
    exit 0
  fi
  ADDRESS="$(tr -d '[:space:]' <"${ADDRESS_FILE}")"
  if [[ -z "${ADDRESS}" ]]; then
    echo "set-mining-payout: empty payout address file"
    exit 0
  fi
  write_datum_config "${ADDRESS}"
  BLOCKVASE_SERVICE_USER="${SERVICE_USER}" "${REFRESH_SCRIPT}" || true
  start_or_defer_datum
  exit 0
fi

mkdir -p /etc/blockvase
printf '%s\n' "${ADDRESS}" >"${ADDRESS_FILE}"
chown "root:${SERVICE_USER}" "${ADDRESS_FILE}" 2>/dev/null || chown root:root "${ADDRESS_FILE}"
chmod 0640 "${ADDRESS_FILE}"

write_datum_config "${ADDRESS}"
BLOCKVASE_SERVICE_USER="${SERVICE_USER}" "${REFRESH_SCRIPT}"
start_or_defer_datum
