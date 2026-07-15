#!/usr/bin/env bash
# Probe BM1366 / Pi UART using piaxe-miner venv and config.yml.
# Temporarily stops blockvase-miner.service if it was running (GPIO pins on Pi 5),
# runs the probe, then starts it again.
set -euo pipefail

MINER_UNIT="blockvase-miner.service"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PY="${PROJECT_DIR}/piaxe-miner/.venv/bin/python3"
PY_HELPER="${SCRIPT_DIR}/check-asic-response.py"

stopped_for_probe=0

miner_cleanup_restore() {
  local save_rc=$?
  if [[ "${stopped_for_probe}" -eq 1 ]]; then
    if ! sudo systemctl start "${MINER_UNIT}"; then
      echo "Warning: could not restart ${MINER_UNIT} (mining may stay stopped). Try: sudo systemctl start ${MINER_UNIT}" >&2
    fi
    echo "Started ${MINER_UNIT} again (was running before check)." >&2
  fi
  exit "${save_rc}"
}

trap miner_cleanup_restore EXIT

if command -v systemctl >/dev/null 2>&1 &&
  [[ "$(systemctl is-active "${MINER_UNIT}" 2>/dev/null || true)" == "active" ]]; then
  echo "Stopping ${MINER_UNIT} briefly for GPIO access..." >&2
  sudo systemctl stop "${MINER_UNIT}"
  stopped_for_probe=1
fi

if [[ -x "${VENV_PY}" ]]; then
  "${VENV_PY}" "${PY_HELPER}" "$@"
else
  echo "Miner venv missing at ${VENV_PY}; run scripts/install-mining-stack.sh or create the venv." >&2
  python3 "${PY_HELPER}" "$@"
fi
