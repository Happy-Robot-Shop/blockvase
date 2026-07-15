#!/usr/bin/env bash
# Disable the "Welcome to Raspberry Pi" first-run desktop wizard (piwiz).
# This is separate from boot splash (Plymouth / rainbow): see disable-boot-splash.sh.
#
# Typical location: /etc/xdg/autostart/piwiz.desktop
# Reboot not required; takes effect on next graphical login. If the wizard is already
# open, close it or reboot once.
#
# Restore: copy the .bak.blockvase file back to piwiz.desktop (see messages below).
#
# Usage: sudo ./scripts/disable-pi-welcome-wizard.sh

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

AUTOSTART_DIR="/etc/xdg/autostart"
REMOVED=0

disable_one() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  if [[ ! -f "${f}.bak.blockvase" ]]; then
    cp -a "${f}" "${f}.bak.blockvase"
  fi
  rm -f "${f}"
  echo "Removed (backup: ${f}.bak.blockvase): ${f}"
  REMOVED=1
}

if [[ -d "${AUTOSTART_DIR}" ]]; then
  # Standard Raspberry Pi OS desktop wizard
  disable_one "${AUTOSTART_DIR}/piwiz.desktop"
  # Some images ship variants
  shopt -s nullglob
  for cand in "${AUTOSTART_DIR}"/piwiz*.desktop; do
    [[ "$cand" == *".bak.blockvase" ]] && continue
    [[ "$cand" == "${AUTOSTART_DIR}/piwiz.desktop" ]] && continue
    disable_one "$cand"
  done
  shopt -u nullglob
fi

if [[ "${REMOVED}" -eq 0 ]]; then
  echo "No piwiz autostart .desktop found under ${AUTOSTART_DIR} (already removed or desktop not installed)."
else
  echo "Welcome wizard will not start on next login. To undo: sudo cp ${AUTOSTART_DIR}/piwiz.desktop.bak.blockvase ${AUTOSTART_DIR}/piwiz.desktop"
fi
