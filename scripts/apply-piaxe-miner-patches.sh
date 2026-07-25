#!/usr/bin/env bash
# Apply Blockvase patches to piaxe-miner (vendored or freshly cloned upstream tree).
# Idempotent: already-applied patches are skipped (-N --forward).
set -euo pipefail

MINER_DIR="${1:?piaxe-miner directory}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_DIR="${SCRIPT_DIR}/patches/piaxe-miner"

if [[ ! -d "${MINER_DIR}" ]]; then
  echo "apply-piaxe-miner-patches: ${MINER_DIR} missing"
  exit 1
fi

if [[ ! -d "${PATCH_DIR}" ]]; then
  exit 0
fi

shopt -s nullglob
patches=("${PATCH_DIR}"/*.patch)
shopt -u nullglob

if ((${#patches[@]} == 0)); then
  exit 0
fi

for patch_file in "${patches[@]}"; do
  echo "       [mining] patch $(basename "${patch_file}")"
  patch -p1 -d "${MINER_DIR}" -N --forward <"${patch_file}" || true
done

if ! grep -q 'mining.configure' "${MINER_DIR}/pyminer.py" 2>/dev/null; then
  echo "WARNING: pyminer.py missing DATUM version-rolling (mining.configure); shares may be rejected as H-not-zero."
  exit 1
fi
