#!/usr/bin/env bash
# Gate Bitcoin Knots P2P sync until home Wi-Fi is configured.
#
# allow_sync when setup_complete=true AND wifi_ssid is non-empty.
# Until then: bitcoind is started with -networkactive=0 (localhost RPC stays up; no IBD/peers).
# Ethernet-only / AP-setup masters can keep a compact chain for imaging.
#
# Writes /etc/bitcoin/blockvase-network-gate.args (read by bitcoind.service ExecStart).
# Does not edit bitcoin.conf (avoids corrupting RPC settings).
#
# Usage (root):
#   bitcoind-network-gate.sh apply          # update args + runtime if bitcoind is up
#   bitcoind-network-gate.sh prepare-start  # args only (systemd ExecStartPre)
#   bitcoind-network-gate.sh status

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_JSON="${BLOCKVASE_CONFIG_JSON:-${PROJECT_DIR}/data/config.json}"
BITCOIN_CONF="${BITCOIN_CONF:-/etc/bitcoin/bitcoin.conf}"
BITCOIN_DATADIR="${BITCOIN_DATADIR:-/var/lib/bitcoind}"
BITCOIN_CLI="${BITCOIN_CLI:-/usr/local/bin/bitcoin-cli}"
GATE_ARGS="${BITCOIN_GATE_ARGS:-/etc/bitcoin/blockvase-network-gate.args}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Must run as root"
  exit 1
fi

wifi_allows_sync() {
  CONFIG_JSON="${CONFIG_JSON}" python3 - <<'PY'
import json, os
from pathlib import Path
path = Path(os.environ["CONFIG_JSON"])
if not path.exists():
    print("false")
    raise SystemExit(0)
try:
    cfg = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("false")
    raise SystemExit(0)
ssid = str(cfg.get("wifi_ssid") or "").strip()
complete = bool(cfg.get("setup_complete"))
print("true" if (complete and ssid) else "false")
PY
}

write_gate_args() {
  local allow="$1"
  local args="-networkactive=0"
  [[ "${allow}" == "true" ]] && args="-networkactive=1"

  mkdir -p "$(dirname "${GATE_ARGS}")"
  local tmp
  tmp="$(mktemp)"
  # Single token, no quotes — consumed unquoted by bitcoind.service ExecStart bash -c.
  printf '%s\n' "${args}" >"${tmp}"
  chown root:bitcoin "${tmp}" 2>/dev/null || chown root:root "${tmp}"
  chmod 644 "${tmp}"
  mv "${tmp}" "${GATE_ARGS}"
  echo "bitcoind-network-gate: ${GATE_ARGS} -> ${args} (allow_sync=${allow})"
}

# Remove legacy conf markers from an older gate implementation (if present).
strip_legacy_conf_markers() {
  [[ -f "${BITCOIN_CONF}" ]] || return 0
  if ! grep -q 'BEGIN-BLOCKVASE-NETWORK-GATE' "${BITCOIN_CONF}" 2>/dev/null; then
    return 0
  fi
  local tmp
  tmp="$(mktemp)"
  awk '
    $0 == "# BEGIN-BLOCKVASE-NETWORK-GATE" { skip=1; next }
    $0 == "# END-BLOCKVASE-NETWORK-GATE" { skip=0; next }
    !skip { print }
  ' "${BITCOIN_CONF}" >"${tmp}"
  if grep -qE '^[[:space:]]*rpcuser=' "${tmp}"; then
    chown --reference="${BITCOIN_CONF}" "${tmp}" 2>/dev/null || true
    chmod --reference="${BITCOIN_CONF}" "${tmp}" 2>/dev/null || true
    mv "${tmp}" "${BITCOIN_CONF}"
    echo "bitcoind-network-gate: removed legacy networkactive block from ${BITCOIN_CONF}"
  else
    rm -f "${tmp}"
    echo "bitcoind-network-gate: warning: left legacy markers in ${BITCOIN_CONF} (incomplete block)"
  fi
  rm -f /etc/bitcoin/blockvase-network-gate.env 2>/dev/null || true
}

apply_runtime() {
  local allow="$1"
  if [[ ! -x "${BITCOIN_CLI}" ]]; then
    return 0
  fi
  if ! systemctl is-active --quiet bitcoind.service 2>/dev/null; then
    return 0
  fi
  local flag="false"
  [[ "${allow}" == "true" ]] && flag="true"
  if sudo -u bitcoin "${BITCOIN_CLI}" \
    -conf="${BITCOIN_CONF}" \
    -datadir="${BITCOIN_DATADIR}" \
    setnetworkactive "${flag}" >/dev/null 2>&1; then
    echo "bitcoind-network-gate: runtime setnetworkactive ${flag}"
  else
    echo "bitcoind-network-gate: warning: setnetworkactive ${flag} failed (bitcoind may still be starting)"
  fi
}

mode="${1:-apply}"
# Never fail ExecStartPre open-ended: if config/python is broken, keep P2P off.
allow="$(wifi_allows_sync 2>/dev/null || true)"
[[ "${allow}" == "true" ]] || allow="false"

case "${mode}" in
  prepare-start)
    strip_legacy_conf_markers || true
    write_gate_args "${allow}"
    ;;
  apply)
    strip_legacy_conf_markers || true
    write_gate_args "${allow}"
    apply_runtime "${allow}"
    ;;
  status)
    echo "allow_sync=${allow}"
    if [[ -f "${GATE_ARGS}" ]]; then
      echo -n "gate_args="
      cat "${GATE_ARGS}"
    else
      echo "gate_args=missing (${GATE_ARGS})"
    fi
    if systemctl is-active --quiet bitcoind.service 2>/dev/null; then
      # RPC can be unavailable briefly while the chain index loads after restart.
      local_info=""
      local_info="$(sudo -u bitcoin "${BITCOIN_CLI}" -conf="${BITCOIN_CONF}" -datadir="${BITCOIN_DATADIR}" \
        getnetworkinfo 2>/dev/null || true)"
      if [[ -n "${local_info}" ]]; then
        python3 -c 'import sys,json; d=json.loads(sys.stdin.read()); print("runtime_networkactive=", d.get("networkactive")); print("connections=", d.get("connections"))' <<<"${local_info}" \
          || echo "runtime_networkactive=unknown"
      else
        echo "runtime_networkactive=unavailable (bitcoind warming up)"
      fi
    else
      echo "bitcoind=inactive"
    fi
    ;;
  *)
    echo "Usage: $0 {apply|prepare-start|status}"
    exit 2
    ;;
esac
