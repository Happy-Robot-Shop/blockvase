#!/usr/bin/env bash
# Install Bitcoin Knots as a local full archival node.
# Release: https://github.com/bitcoinknots/bitcoin/releases/tag/v29.3.knots20260508
#
# - Downloads the matching *-linux-gnu.tar.gz for this CPU from the GitHub release API
# - Installs binaries under /opt/bitcoin-knots, symlinks in /usr/local/bin
# - Creates user bitcoin, datadir /var/lib/bitcoind, config /etc/bitcoin/bitcoin.conf
# - JSON-RPC only on 127.0.0.1:8332; merges rpc user/password into Blockvase data/config.json
#
# Usage: sudo ./scripts/install-bitcoin-knots.sh [BLOCKVASE_PROJECT_DIR]
# Re-run is safe: keeps existing /etc/bitcoin/bitcoin.conf unless BLOCKVASE_BITCOIN_RECONF=1.
# RDTS (v29.3+): fresh installs include consensusrules=rdts; existing configs get it appended if missing.
#   Set BLOCKVASE_SKIP_RDTS_CONSENSUS=1 to skip appending consensusrules=rdts on upgrades.

set -euo pipefail

REPO="bitcoinknots/bitcoin"
TAG="v29.3.knots20260508"
PROJECT_DIR="${1:-}"
if [[ -z "${PROJECT_DIR}" ]]; then
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0 $*"
  exit 1
fi

if ! command -v curl >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
  echo "Install curl and jq first (apt install -y curl jq)"
  exit 1
fi

TAG_API_ENC="${TAG//+/%2B}"
API_URL="https://api.github.com/repos/${REPO}/releases/tags/${TAG_API_ENC}"

case "$(uname -m)" in
  aarch64) GLOB="aarch64-linux-gnu" ;;
  x86_64) GLOB="x86_64-linux-gnu" ;;
  armv7l|armv6l|armv7) GLOB="arm-linux-gnueabihf" ;;
  *)
    echo "Unsupported CPU architecture: $(uname -m)"
    exit 1
    ;;
esac

echo "Resolving release asset for ${GLOB} (non-debug tarball)..."
# Releases ship both *-linux-gnu.tar.gz and *-linux-gnu-debug.tar.gz; the debug archive has a
# different layout: use the standard release binary tarball only.
ASSET_URL="$(curl -sL --fail "${API_URL}" | jq -r --arg g "$GLOB" '
  [.assets[]
    | select(.name | test($g))
    | select(.name | endswith(".tar.gz"))
    | select(.name | contains("-debug") | not)
  ]
  | if length > 0 then .[0].browser_download_url else empty end
')"
if [[ -z "${ASSET_URL}" || "${ASSET_URL}" == "null" ]]; then
  echo "Could not find a non-debug .tar.gz asset for ${GLOB} in ${TAG}. Check ${API_URL}"
  exit 1
fi
echo "Downloading: ${ASSET_URL##*/}"

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
curl -sL --fail -o "${TMP}/knots.tar.gz" "${ASSET_URL}"
tar -xzf "${TMP}/knots.tar.gz" -C "${TMP}"
SUB="$(find "${TMP}" -maxdepth 1 -type d -name 'bitcoin-*' | head -1)"
if [[ -n "${SUB}" && -x "${SUB}/bin/bitcoind" ]]; then
  :
else
  # Some tarballs nest differently; locate bitcoind under the extract dir.
  BD="$(find "${TMP}" -type f -name bitcoind \( -perm -111 -o -executable \) 2>/dev/null | head -1)"
  if [[ -n "${BD}" && -x "${BD}" ]]; then
    SUB="$(cd "$(dirname "${BD}")/.." && pwd)"
  fi
fi
if [[ -z "${SUB}" || ! -x "${SUB}/bin/bitcoind" ]]; then
  echo "Extracted tarball layout unexpected; expected .../bin/bitcoind (got listing in ${TMP})"
  find "${TMP}" -maxdepth 3 -type f 2>/dev/null | head -20 || true
  exit 1
fi

rm -rf /opt/bitcoin-knots
mv "${SUB}" /opt/bitcoin-knots
ln -sf /opt/bitcoin-knots/bin/bitcoind /usr/local/bin/bitcoind
ln -sf /opt/bitcoin-knots/bin/bitcoin-cli /usr/local/bin/bitcoin-cli
chmod +x /usr/local/bin/bitcoind /usr/local/bin/bitcoin-cli

if ! id -u bitcoin &>/dev/null; then
  useradd --system --home /var/lib/bitcoind --create-home bitcoin
else
  mkdir -p /var/lib/bitcoind
  chown bitcoin:bitcoin /var/lib/bitcoind
fi

mkdir -p /etc/bitcoin
CONF="/etc/bitcoin/bitcoin.conf"
RPC_USER="blockvase"
if [[ -f "${CONF}" ]] && [[ "${BLOCKVASE_BITCOIN_RECONF:-}" != "1" ]]; then
  echo "Keeping existing ${CONF} (set BLOCKVASE_BITCOIN_RECONF=1 to replace)"
  RPC_USER="$(grep -E '^[[:space:]]*rpcuser=' "${CONF}" | head -1 | cut -d= -f2- | tr -d ' \r' || echo blockvase)"
  RPC_PASS="$(grep -E '^[[:space:]]*rpcpassword=' "${CONF}" | head -1 | cut -d= -f2- | tr -d ' \r' || true)"
else
  RPC_PASS="$(openssl rand -hex 32)"
  umask 027
  cat >"${CONF}" <<EOF
# Installed by blockvase install-bitcoin-knots.sh: ${TAG}
server=1
txindex=1
prune=0
dbcache=512
maxconnections=64

# Knots v29.3+: explicit RDTS adoption (see release notes / bitcoinknots.org/learn/2026-rdts)
consensusrules=rdts

# JSON-RPC: local only (Blockvase Flask app)
rpcbind=127.0.0.1
rpcallowip=127.0.0.1
rpcuser=${RPC_USER}
rpcpassword=${RPC_PASS}
EOF
  umask 022
  chown root:bitcoin "${CONF}"
  chmod 640 "${CONF}"
fi

# Upgrades from older Knots: daemon expects explicit RDTS confirmation (no GUI prompt).
if [[ "${BLOCKVASE_SKIP_RDTS_CONSENSUS:-}" != "1" ]] && [[ -f "${CONF}" ]] &&
  ! grep -qE '^[[:space:]]*consensusrules[[:space:]]*=' "${CONF}"; then
  printf '\n# Knots v29.3+: RDTS softfork adoption\nconsensusrules=rdts\n' >>"${CONF}"
fi

UNIT_SRC="${PROJECT_DIR}/systemd/bitcoind.service"
if [[ -f "${UNIT_SRC}" ]]; then
  cp -a "${UNIT_SRC}" /etc/systemd/system/bitcoind.service
else
  echo "WARNING: ${UNIT_SRC} missing; install systemd/bitcoind.service manually"
fi

CONFIG_JSON="${PROJECT_DIR}/data/config.json"
if [[ -f "${CONFIG_JSON}" ]] && command -v jq >/dev/null 2>&1; then
  RPC_PASS_VAL="${RPC_PASS:-}"
  if [[ -z "${RPC_PASS_VAL}" ]] && [[ -f "${CONF}" ]]; then
    RPC_PASS_VAL="$(grep -E '^[[:space:]]*rpcpassword=' "${CONF}" | head -1 | cut -d= -f2- | tr -d ' \r')"
  fi
  RPC_USER_VAL="${RPC_USER}"
  if [[ -f "${CONF}" ]]; then
    u="$(grep -E '^[[:space:]]*rpcuser=' "${CONF}" | head -1 | cut -d= -f2- | tr -d ' \r')"
    [[ -n "${u}" ]] && RPC_USER_VAL="${u}"
  fi
  TMPJ="$(mktemp)"
  jq --arg u "${RPC_USER_VAL}" --arg p "${RPC_PASS_VAL}" \
    '.rpc.host = "127.0.0.1" | .rpc.port = 8332 | .rpc.user = $u | .rpc.password = $p | .rpc.use_https = false' \
    "${CONFIG_JSON}" >"${TMPJ}"
  mv "${TMPJ}" "${CONFIG_JSON}"
  BV_USER="$(stat -c '%U' "${PROJECT_DIR}" 2>/dev/null || echo blockvase)"
  # Must be writable by the blockvase service user; do not swallow chown failures.
  if ! chown "${BV_USER}:${BV_USER}" "${CONFIG_JSON}"; then
    echo "ERROR: chown ${BV_USER} ${CONFIG_JSON} failed, portal cannot save settings." >&2
    exit 1
  fi
  chmod 600 "${CONFIG_JSON}" 2>/dev/null || true
  echo "Updated ${CONFIG_JSON} rpc block for local Knots."
fi

GATE_SCRIPT="${PROJECT_DIR}/scripts/bitcoind-network-gate.sh"
if [[ -x "${GATE_SCRIPT}" ]]; then
  "${GATE_SCRIPT}" prepare-start || true
elif [[ -f "${GATE_SCRIPT}" ]]; then
  chmod +x "${GATE_SCRIPT}"
  "${GATE_SCRIPT}" prepare-start || true
fi

systemctl daemon-reload
systemctl enable bitcoind.service
systemctl restart bitcoind.service || systemctl start bitcoind.service || true

echo "Bitcoin Knots installed. Status: systemctl status bitcoind.service"
echo "CLI: sudo -u bitcoin bitcoin-cli -conf=${CONF} getblockchaininfo"
echo "P2P sync is gated until home Wi-Fi is configured (see bitcoind-network-gate.sh)."
