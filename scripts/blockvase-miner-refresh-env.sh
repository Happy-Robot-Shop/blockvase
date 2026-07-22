#!/usr/bin/env bash
# Rewrite /etc/blockvase/miner.env from project config + solo mining address.
# Does not touch DATUM JSON (use set-mining-payout.sh for full payout application).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADDRESS_FILE="/etc/blockvase/solo_mining_address"
MINER_ENV="/etc/blockvase/miner.env"
SERVICE_USER="${BLOCKVASE_SERVICE_USER:-${SUDO_USER:-blockvase}}"
if [[ -z "${SERVICE_USER}" || "${SERVICE_USER}" == "root" ]]; then
  SERVICE_USER="blockvase"
fi

RESTART="${1:-}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

ADDRESS=""
if [[ -f "${ADDRESS_FILE}" ]]; then
  ADDRESS="$(tr -d '\r\n' <"${ADDRESS_FILE}")"
fi

MINER_CFG="config.blockvase.yml"

mkdir -p /etc/blockvase

if [[ -z "${ADDRESS}" ]]; then
  cat >"${MINER_ENV}" <<EOF
# Solo mining env (Blockvase). No payout yet: miner runs board monitoring only.
BLOCKVASE_MINER_STRATUM_URL=stratum+tcp://127.0.0.1:23334
BLOCKVASE_MINER_USER=
BLOCKVASE_MINER_PASS=x
BLOCKVASE_MINER_CONFIG=${MINER_CFG}
EOF
else
  cat >"${MINER_ENV}" <<EOF
# Written by blockvase-miner-refresh-env.sh: local DATUM Gateway.
BLOCKVASE_MINER_STRATUM_URL=stratum+tcp://127.0.0.1:23334
BLOCKVASE_MINER_USER=${ADDRESS}.blockvase
BLOCKVASE_MINER_PASS=x
BLOCKVASE_MINER_CONFIG=${MINER_CFG}
EOF
fi

chown "root:${SERVICE_USER}" "${MINER_ENV}" 2>/dev/null || chown root:root "${MINER_ENV}" 2>/dev/null || true
chmod 0640 "${MINER_ENV}" 2>/dev/null || true

if [[ "${RESTART}" == "--restart-miner" ]]; then
  systemctl restart blockvase-miner.service
fi
