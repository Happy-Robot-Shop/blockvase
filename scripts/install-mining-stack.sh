#!/usr/bin/env bash
# Solo mining stack: build/install DATUM Gateway, PiAxe-miner venv, systemd units, Knots blocknotify, /etc/blockvase configs.
# Intended to run from bootstrap as root after install-bitcoin-knots.sh (needs /etc/bitcoin/bitcoin.conf).
#
# Env:
#   BLOCKVASE_SKIP_MINING_STACK: set to 1 in bootstrap to skip this entire script.
#
set -euo pipefail

PROJECT_DIR="${1:?project dir}"
SERVICE_USER="${2:?service user}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "install-mining-stack.sh must run as root."
  exit 1
fi

if [[ "${BLOCKVASE_SKIP_MINING_STACK:-}" == "1" ]]; then
  echo "Skipping mining stack (BLOCKVASE_SKIP_MINING_STACK=1)."
  exit 0
fi

BITCOIN_CONF="/etc/bitcoin/bitcoin.conf"
if [[ ! -f "${BITCOIN_CONF}" ]]; then
  echo "ERROR: ${BITCOIN_CONF} missing: run install-bitcoin-knots.sh first."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
echo "       [mining] OS packages (DATUM build deps, miner runtime)..."
apt-get install -y \
  build-essential \
  cmake \
  pkgconf \
  libcurl4-openssl-dev \
  libjansson-dev \
  libsodium-dev \
  libmicrohttpd-dev \
  psmisc \
  python3-rpi-lgpio

DATUM_SRC="${PROJECT_DIR}/datum_gateway"
if [[ ! -f "${DATUM_SRC}/CMakeLists.txt" ]]; then
  echo "ERROR: ${DATUM_SRC}/CMakeLists.txt missing."
  exit 1
fi

BUILD_DIR="${DATUM_SRC}/build-blockvase"
echo "       [mining] Building datum_gateway..."
cmake -S "${DATUM_SRC}" -B "${BUILD_DIR}" -DCMAKE_BUILD_TYPE=Release
cmake --build "${BUILD_DIR}" -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
install -m 0755 "${BUILD_DIR}/datum_gateway" /usr/local/bin/datum_gateway

RPC_USER="$(grep -E '^[[:space:]]*rpcuser=' "${BITCOIN_CONF}" | head -1 | cut -d= -f2- | tr -d ' \r' || true)"
RPC_PASS="$(grep -E '^[[:space:]]*rpcpassword=' "${BITCOIN_CONF}" | head -1 | cut -d= -f2- | tr -d ' \r' || true)"
if [[ -z "${RPC_USER}" || -z "${RPC_PASS}" ]]; then
  echo "ERROR: Could not read rpcuser/rpcpassword from ${BITCOIN_CONF}"
  exit 1
fi

mkdir -p /etc/blockvase

if ! grep -qF 'http://127.0.0.1:7152/NOTIFY' "${BITCOIN_CONF}" 2>/dev/null; then
  echo "" >>"${BITCOIN_CONF}"
  echo "# Blockvase: wake DATUM Gateway on new blocks (solo mining)" >>"${BITCOIN_CONF}"
  echo "blocknotify=curl -fsS -o /dev/null http://127.0.0.1:7152/NOTIFY" >>"${BITCOIN_CONF}"
  echo "       [mining] Appended blocknotify for DATUM Gateway to ${BITCOIN_CONF}"
fi

MINER_DIR="${PROJECT_DIR}/piaxe-miner"
if [[ ! -d "${MINER_DIR}" ]]; then
  echo "ERROR: ${MINER_DIR} missing."
  exit 1
fi

echo "       [mining] PiAxe-miner Python venv..."
# --system-site-packages: Pi 5+ needs distro RPi/GPIO shim (python3-rpi-lgpio); do not pip-install RPi.GPIO (wrong SoC pins).
rm -rf "${MINER_DIR}/.venv"
python3 -m venv --system-site-packages "${MINER_DIR}/.venv"
# shellcheck source=/dev/null
source "${MINER_DIR}/.venv/bin/activate"
pip install --upgrade pip wheel
pip install -r "${MINER_DIR}/requirements.txt"

if [[ ! -f "${MINER_DIR}/config.yml" ]]; then
  cp "${MINER_DIR}/config.blockvase.yml" "${MINER_DIR}/config.yml"
fi

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${MINER_DIR}/.venv" "${MINER_DIR}/config.yml"

TMP_D="$(mktemp)"
TMP_M="$(mktemp)"
sed "s|__SERVICE_USER__|${SERVICE_USER}|g" "${PROJECT_DIR}/systemd/datum-gateway.service" >"${TMP_D}"
sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g; s|__SERVICE_USER__|${SERVICE_USER}|g" \
  "${PROJECT_DIR}/systemd/blockvase-miner.service" >"${TMP_M}"
cp "${TMP_D}" /etc/systemd/system/datum-gateway.service
cp "${TMP_M}" /etc/systemd/system/blockvase-miner.service
rm -f "${TMP_D}" "${TMP_M}"

chmod +x "${PROJECT_DIR}/scripts/set-mining-payout.sh"

systemctl daemon-reload
systemctl restart bitcoind.service || true
ADDRESS_FILE="/etc/blockvase/solo_mining_address"
# Miner is always enabled: board/ASIC monitoring stays up even without a payout.
# DATUM requires a valid pool_address, so hashing starts only after Settings saves one.
BLOCKVASE_SERVICE_USER="${SERVICE_USER}" "${PROJECT_DIR}/scripts/blockvase-miner-refresh-env.sh"
systemctl enable --now blockvase-miner.service
if [[ -s "${ADDRESS_FILE}" ]]; then
  EXISTING_ADDR="$(tr -d '[:space:]' <"${ADDRESS_FILE}")"
  echo "       [mining] Existing payout address found; enabling DATUM + hashing."
  "${PROJECT_DIR}/scripts/set-mining-payout.sh" "${EXISTING_ADDR}"
else
  systemctl disable --now datum-gateway.service 2>/dev/null || true
  echo "       [mining] Miner enabled (board monitoring). Save a payout address in Settings to start hashing."
fi
