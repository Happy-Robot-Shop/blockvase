#!/usr/bin/env bash
# Verify origin remote + signed tip before applying a device update.
# Usage:
#   scripts/verify-ota-update.sh <ref>   e.g. origin/main
#   /usr/lib/blockvase/verify-ota-update.sh <ref>
#
# Prefer root-owned allowlists under /etc/blockvase/ (installed by bootstrap)
# so a compromised service user cannot rewrite trusted remotes/signers.
set -euo pipefail

REF="${1:?ref required (e.g. origin/main)}"

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${_SCRIPT_DIR}/../app/server.py" ]]; then
  PROJECT_DIR="$(cd "${_SCRIPT_DIR}/.." && pwd)"
elif [[ -f /etc/blockvase/project-dir ]]; then
  PROJECT_DIR="$(tr -d '[:space:]' </etc/blockvase/project-dir)"
else
  PROJECT_DIR="/home/blockvase/blockvase"
fi

cd "${PROJECT_DIR}"

pick_allowlist() {
  local name="$1"
  if [[ -f "/etc/blockvase/${name}" ]]; then
    echo "/etc/blockvase/${name}"
  elif [[ -f "${PROJECT_DIR}/security/${name}" ]]; then
    echo "${PROJECT_DIR}/security/${name}"
  else
    echo ""
  fi
}

ALLOWED_REMOTES="$(pick_allowlist "ota-allowed-remotes.txt")"
ALLOWED_SIGNERS="$(pick_allowlist "ota-allowed-signers")"

if [[ -z "${ALLOWED_REMOTES}" ]]; then
  echo "ERROR: missing ota-allowed-remotes.txt under /etc/blockvase or ${PROJECT_DIR}/security" >&2
  exit 1
fi

ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
if [[ -z "${ORIGIN_URL}" ]]; then
  echo "ERROR: git remote 'origin' is not configured" >&2
  exit 1
fi

normalize_url() {
  local u="$1"
  u="${u%.git}"
  u="${u%/}"
  echo "${u}"
}

ORIG_N="$(normalize_url "${ORIGIN_URL}")"
OK_REMOTE=0
while IFS= read -r line || [[ -n "${line}" ]]; do
  [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
  if [[ "$(normalize_url "${line}")" == "${ORIG_N}" ]]; then
    OK_REMOTE=1
    break
  fi
done <"${ALLOWED_REMOTES}"

if [[ "${OK_REMOTE}" -ne 1 ]]; then
  echo "ERROR: origin remote '${ORIGIN_URL}' is not in ${ALLOWED_REMOTES}" >&2
  exit 1
fi

TIP="$(git rev-parse --verify "${REF}^{commit}" 2>/dev/null || true)"
if [[ -z "${TIP}" ]]; then
  echo "ERROR: cannot resolve commit for ${REF}" >&2
  exit 1
fi

if [[ -z "${ALLOWED_SIGNERS}" || ! -f "${ALLOWED_SIGNERS}" ]]; then
  echo "ERROR: missing ota-allowed-signers under /etc/blockvase or ${PROJECT_DIR}/security" >&2
  exit 1
fi

# Require at least one active signer line.
if ! grep -qE '^[^#[:space:]]' "${ALLOWED_SIGNERS}"; then
  echo "ERROR: ${ALLOWED_SIGNERS} has no active signing keys" >&2
  exit 1
fi

export GIT_CONFIG_COUNT=2
export GIT_CONFIG_KEY_0="gpg.ssh.allowedSignersFile"
export GIT_CONFIG_VALUE_0="${ALLOWED_SIGNERS}"
export GIT_CONFIG_KEY_1="gpg.format"
export GIT_CONFIG_VALUE_1="ssh"

if ! git verify-commit "${TIP}" >/tmp/blockvase-ota-verify.out 2>&1; then
  echo "ERROR: tip ${TIP} (${REF}) is not signed by a trusted OTA key." >&2
  echo "       Push must be signed with the Blockvase OTA signing key (SSH)." >&2
  cat /tmp/blockvase-ota-verify.out >&2 || true
  exit 1
fi

echo "OTA verify OK: remote=${ORIGIN_URL} tip=${TIP} remotes_file=${ALLOWED_REMOTES} signers_file=${ALLOWED_SIGNERS}"
cat /tmp/blockvase-ota-verify.out || true
rm -f /tmp/blockvase-ota-verify.out
