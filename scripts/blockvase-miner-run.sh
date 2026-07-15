#!/usr/bin/env bash
# Run vendored piaxe-miner: solo mining via local DATUM Gateway → Bitcoin Knots (GBT), not a public stratum pool.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MINER_DIR="${PROJECT_DIR}/piaxe-miner"
VENV_PY="${MINER_DIR}/.venv/bin/python3"

if [[ ! -d "${MINER_DIR}" ]]; then
  echo "blockvase-miner: missing ${MINER_DIR}" >&2
  exit 1
fi

cd "${MINER_DIR}"

if [[ ! -f config.yml ]]; then
  echo "blockvase-miner: no config.yml: copy config.blockvase.yml or config.yml.example and edit." >&2
  exit 1
fi

PY=python3
if [[ -x "${VENV_PY}" ]]; then
  PY="${VENV_PY}"
fi

# Stratum v1 → local DATUM Gateway (bundled datum_gateway/, default listen_port 23334).
# Empty USER is allowed: pyminer stays up for board/ASIC monitoring until a payout is saved.
URL="${BLOCKVASE_MINER_STRATUM_URL:-stratum+tcp://127.0.0.1:23334}"
USER="${BLOCKVASE_MINER_USER:-}"
PASS="${BLOCKVASE_MINER_PASS:-x}"
CONFIG="${BLOCKVASE_MINER_CONFIG:-config.yml}"

if [[ -z "${USER}" ]]; then
  echo "blockvase-miner: no payout address yet; starting in monitoring mode (hashing waits for Settings)."
fi

BACKOFF="${BLOCKVASE_MINER_RESTART_SEC:-45}"

while true; do
  set +e
  if [[ -n "${USER}" ]]; then
    "${PY}" pyminer.py -c "${CONFIG}" -o "${URL}" -u "${USER}" -p "${PASS}"
  else
    "${PY}" pyminer.py -c "${CONFIG}" -o "${URL}" -u "" -p "${PASS}"
  fi
  rc=$?
  set -e
  echo "blockvase-miner: pyminer exited rc=${rc}; sleeping ${BACKOFF}s" >&2
  sleep "${BACKOFF}"
done
