#!/usr/bin/env bash
# After a GitHub PR merge (often unsigned tip), create an empty commit signed
# with the Blockvase OTA key so device-update verify-commit succeeds.
#
# Usage:
#   scripts/ota-sign-tip.sh ["optional message"]
# Then: git push origin HEAD:main
set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${_SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

KEY="${BLOCKVASE_OTA_SIGNING_KEY:-$HOME/.blockvase-secrets/ota-signing}"
if [[ ! -f "${KEY}" ]]; then
  echo "ERROR: OTA signing key not found at ${KEY}" >&2
  echo "       Set BLOCKVASE_OTA_SIGNING_KEY or place the key at the default path." >&2
  exit 1
fi

MSG="${1:-ota: sign release tip for device updates}"

git -c gpg.format=ssh \
  -c "user.signingkey=${KEY}" \
  commit --allow-empty -S -m "${MSG}"

echo "Signed tip: $(git rev-parse HEAD)"
echo "Verify locally:"
echo "  scripts/verify-ota-update.sh HEAD"
echo "Push when ready (admin bypass required if main is PR-protected):"
echo "  git push origin HEAD:main"
