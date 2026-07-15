#!/usr/bin/env bash
# Undo enable-kiosk-only-boot.sh: boot into graphical.target with the display manager
# and disable systemd blockvase-kiosk.service (startx). Re-install desktop kiosk autostart.
#
# Usage:
#   sudo ./restore-desktop-boot.sh [--yes] [unix_user] [project_dir]
# Example:
#   sudo ./restore-desktop-boot.sh blockvase /home/blockvase/blockvase

set -euo pipefail

YES=0
if [[ "${1:-}" == "--yes" ]]; then
  YES=1
  shift
fi
SERVICE_USER="${1:-}"
PROJECT_DIR="${2:-}"

if [[ -z "${SERVICE_USER}" ]]; then
  if id -u blockvase &>/dev/null; then
    SERVICE_USER="blockvase"
  elif id -u pi &>/dev/null; then
    SERVICE_USER="pi"
  else
    echo "Usage: sudo $0 <user> [project_dir]"
    exit 1
  fi
fi

if [[ -z "${PROJECT_DIR}" ]]; then
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo."
  exit 1
fi

if [[ "${YES}" -ne 1 ]]; then
  read -r -p "Re-enable full desktop boot and disable startx kiosk service? [y/N] " ans || true
  if [[ "${ans,,}" != "y" && "${ans,,}" != "yes" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

systemctl disable --now blockvase-kiosk.service 2>/dev/null || true
echo "Disabled: blockvase-kiosk.service"

systemctl set-default graphical.target
echo "Default target: graphical.target"

# Re-enable display manager if we know which one existed
if [[ -L /etc/systemd/system/display-manager.service ]]; then
  DM_UNIT="$(basename "$(readlink -f /etc/systemd/system/display-manager.service)")"
  if [[ -n "${DM_UNIT}" ]] && [[ -f "/lib/systemd/system/${DM_UNIT}" || -f "/usr/lib/systemd/system/${DM_UNIT}" ]]; then
    systemctl enable "${DM_UNIT}"
    echo "Enabled: ${DM_UNIT}"
  fi
else
  for u in lightdm gdm gdm3 sddm lxdm; do
    if [[ -f "/lib/systemd/system/${u}.service" ]] || [[ -f "/usr/lib/systemd/system/${u}.service" ]]; then
      systemctl enable "${u}.service"
      echo "Enabled: ${u}.service"
      break
    fi
  done
fi

if [[ -x "${PROJECT_DIR}/scripts/ensure-desktop-kiosk-autostart.sh" ]]; then
  "${PROJECT_DIR}/scripts/ensure-desktop-kiosk-autostart.sh" "${SERVICE_USER}" "${PROJECT_DIR}"
fi

systemctl daemon-reload
echo ""
echo "Reboot recommended: sudo reboot"
echo "After reboot you should get the normal Pi desktop; Blockvase kiosk runs via autostart again."
