#!/usr/bin/env bash
# Remove Blockvase desktop autostart hooks (XDG + labwc) installed by
# ensure-desktop-kiosk-autostart.sh. Used when switching to kiosk-only boot (startx).
#
# Usage: sudo ./remove-desktop-kiosk-autostart.sh <unix_user>
# Example: sudo ./remove-desktop-kiosk-autostart.sh blockvase

set -euo pipefail

SERVICE_USER="${1:?user}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo $0 $*"
  exit 1
fi

HOME_REAL="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"
if [[ -z "${HOME_REAL}" || ! -d "${HOME_REAL}" ]]; then
  echo "Home directory for ${SERVICE_USER} not found."
  exit 1
fi

rm -f "${HOME_REAL}/.config/autostart/blockvase-kiosk.desktop"
echo "Removed (if present): ${HOME_REAL}/.config/autostart/blockvase-kiosk.desktop"

LABWC="${HOME_REAL}/.config/labwc/autostart"
if [[ -f "${LABWC}" ]] && grep -q 'blockvase-kiosk' "${LABWC}" 2>/dev/null; then
  LABWC="${LABWC}" python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["LABWC"])
lines = path.read_text(encoding="utf-8").splitlines()
out = []
i = 0
marker = "# Blockvase kiosk (added by blockvase ensure-desktop-kiosk-autostart.sh)"
while i < len(lines):
    line = lines[i]
    if line.strip() == marker:
        i += 1
        while i < len(lines):
            if "kiosk-desktop.sh" in lines[i] and "&" in lines[i]:
                i += 1
                break
            i += 1
        continue
    out.append(line)
    i += 1
path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
PY
  chown "${SERVICE_USER}:${SERVICE_USER}" "${LABWC}"
  echo "Cleaned: ${LABWC}"
fi

echo "Desktop autostart hooks for Blockvase kiosk removed."
