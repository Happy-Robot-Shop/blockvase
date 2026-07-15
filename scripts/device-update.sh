#!/usr/bin/env bash
# Pull latest Blockvase from git origin and re-run bootstrap.
# Intended via: sudo scripts/device-update.sh (portal Update device action).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="/var/lib/blockvase"
STATE_FILE="${STATE_DIR}/update-status.json"
LOG_FILE="${STATE_DIR}/device-update.log"
LOCK_FILE="${STATE_DIR}/device-update.lock"
SERVICE_USER="blockvase"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ${0}" >&2
  exit 1
fi

mkdir -p "${STATE_DIR}"
chmod 755 "${STATE_DIR}"

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Device update already in progress." >&2
  exit 1
fi

iso_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

write_state() {
  local status="$1"
  local message="${2:-}"
  local started_at="${3:-}"
  local finished_at="${4:-}"
  python3 - "${STATE_FILE}" "${status}" "${message}" "${started_at}" "${finished_at}" <<'PY'
import json, sys
path, status, message, started_at, finished_at = sys.argv[1:6]
payload = {
    "status": status,
    "message": message,
    "started_at": started_at or None,
    "finished_at": finished_at or None,
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)
    f.write("\n")
PY
  chmod 644 "${STATE_FILE}"
}

STARTED_AT="$(iso_now)"
write_state "running" "Starting device update..." "${STARTED_AT}" ""

mkdir -p "$(dirname "${LOG_FILE}")"
exec > >(tee -a "${LOG_FILE}") 2>&1
echo "===== device-update $(iso_now) ====="

fail() {
  local msg="$1"
  echo "ERROR: ${msg}" >&2
  write_state "failed" "${msg}" "${STARTED_AT}" "$(iso_now)"
  exit 1
}

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  fail "Service user ${SERVICE_USER} not found."
fi

if [[ ! -d "${PROJECT_DIR}/.git" ]]; then
  fail "Not a git checkout: ${PROJECT_DIR}"
fi

write_state "running" "Pulling latest code..." "${STARTED_AT}" ""
cd "${PROJECT_DIR}"
if ! sudo -u "${SERVICE_USER}" git fetch --prune origin; then
  fail "git fetch failed. Check network and origin remote."
fi
BRANCH="$(sudo -u "${SERVICE_USER}" git rev-parse --abbrev-ref HEAD)"
if [[ -z "${BRANCH}" || "${BRANCH}" == "HEAD" ]]; then
  fail "Could not determine git branch (detached HEAD?)."
fi
if ! sudo -u "${SERVICE_USER}" git pull --ff-only origin "${BRANCH}"; then
  fail "git pull --ff-only failed. Resolve local changes or network, then retry."
fi

if [[ ! -x "${PROJECT_DIR}/scripts/bootstrap.sh" ]]; then
  fail "bootstrap.sh missing or not executable."
fi

write_state "running" "Running bootstrap (this can take several minutes)..." "${STARTED_AT}" ""
# Run as root so bootstrap can install packages and rewrite units; it targets SERVICE_USER=blockvase.
if ! "${PROJECT_DIR}/scripts/bootstrap.sh"; then
  fail "bootstrap.sh failed. See ${LOG_FILE}."
fi

write_state "running" "Restarting Blockvase services..." "${STARTED_AT}" ""
systemctl daemon-reload || true
systemctl restart blockvase-ap.service || true
systemctl restart blockvase.service || true
systemctl restart blockvase-kiosk.service || true

write_state "success" "Update complete." "${STARTED_AT}" "$(iso_now)"
echo "Device update finished successfully."
