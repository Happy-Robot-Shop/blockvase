#!/usr/bin/env bash
# Apply the solo mining payout address to DATUM Gateway + PiAxe-miner runtime config.
set -euo pipefail

ADDRESS="${1:-}"
ADDRESS="${ADDRESS#"${ADDRESS%%[![:space:]]*}"}"
ADDRESS="${ADDRESS%"${ADDRESS##*[![:space:]]}"}"
ADDRESS="${ADDRESS//$'\r'/}"
if [[ -z "${ADDRESS}" ]]; then
  echo "Usage: $0 <bitcoin-address>" >&2
  exit 2
fi

BITCOIN_CONF="/etc/bitcoin/bitcoin.conf"
DATUM_CONFIG="/etc/blockvase/datum_gateway_config.json"
MINER_ENV="/etc/blockvase/miner.env"
ADDRESS_FILE="/etc/blockvase/solo_mining_address"
SERVICE_USER="${BLOCKVASE_SERVICE_USER:-${SUDO_USER:-blockvase}}"
if [[ -z "${SERVICE_USER}" || "${SERVICE_USER}" == "root" ]]; then
  SERVICE_USER="blockvase"
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

mkdir -p /etc/blockvase
printf '%s\n' "${ADDRESS}" >"${ADDRESS_FILE}"
chown "root:${SERVICE_USER}" "${ADDRESS_FILE}" 2>/dev/null || chown root:root "${ADDRESS_FILE}"
chmod 0640 "${ADDRESS_FILE}"

if [[ -f "${DATUM_CONFIG}" ]]; then
  tmp="$(mktemp)"
  jq --arg addr "${ADDRESS}" '.mining.pool_address = $addr' "${DATUM_CONFIG}" >"${tmp}"
  cat "${tmp}" >"${DATUM_CONFIG}"
  rm -f "${tmp}"
else
  jq --null-input \
    --arg u "${RPC_USER}" \
    --arg p "${RPC_PASS}" \
    --arg addr "${ADDRESS}" \
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BLOCKVASE_SERVICE_USER="${SERVICE_USER}" "${SCRIPT_DIR}/blockvase-miner-refresh-env.sh"

# A configured payout means real mining is allowed to run after reboot too.
systemctl enable --now datum-gateway.service blockvase-miner.service
systemctl restart datum-gateway.service blockvase-miner.service
