#!/usr/bin/env bash
# Boot straight into the Blockvase X11 kiosk (startx + kiosk-session.sh) with no
# full desktop (no labwc, panel, or login greeter). First graphical output is Chromium.
#
# This is how Raspberry Pi OS Lite behaves after bootstrap. On Desktop images, the
# desktop is started by the display manager (usually lightdm). We switch the default
# target to multi-user, disable the display manager, remove desktop autostart hooks,
# and enable blockvase-kiosk.service.
#
# Recovery: use SSH or a serial console, or boot with a keyboard and log in on tty2+
# (getty). You can run scripts/restore-desktop-boot.sh to go back (if lightdm is installed).
#
# Usage: sudo ./enable-kiosk-only-boot.sh [--yes] [unix_user]
# Default user: blockvase, or first of blockvase/pi if present.
#   --yes   non-interactive (for automation)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
YES=0
if [[ "${1:-}" == "--yes" ]]; then
  YES=1
  shift
fi
SERVICE_USER="${1:-}"
if [[ -z "${SERVICE_USER}" ]]; then
  if id -u blockvase &>/dev/null; then
    SERVICE_USER="blockvase"
  elif id -u pi &>/dev/null; then
    SERVICE_USER="pi"
  else
    echo "Pass a user: sudo $0 blockvase"
    exit 1
  fi
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo."
  exit 1
fi

echo "This will:"
echo "  - set default boot target to multi-user (no graphical login / no full desktop)"
echo "  - stop and disable the display manager (e.g. lightdm)"
echo "  - enable systemd blockvase-kiosk.service (startx on :0)"
echo "  - remove Blockvase desktop autostart (kiosk-desktop / labwc hooks)"
echo ""
echo "You should have SSH or another way to recover if HDMI output fails."
if [[ "${YES}" -ne 1 ]]; then
  read -r -p "Continue? [y/N] " ans || true
  if [[ "${ans,,}" != "y" && "${ans,,}" != "yes" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

# 1) Stop graphical login / desktop
DM_UNIT=""
if [[ -L /etc/systemd/system/display-manager.service ]]; then
  DM_UNIT="$(basename "$(readlink -f /etc/systemd/system/display-manager.service)")"
fi
if [[ -n "${DM_UNIT}" ]] && systemctl cat "${DM_UNIT}" &>/dev/null; then
  systemctl disable --now "${DM_UNIT}" 2>/dev/null || true
  echo "Disabled display manager: ${DM_UNIT}"
else
  for u in lightdm gdm gdm3 sddm lxdm; do
    if systemctl cat "${u}.service" &>/dev/null; then
      systemctl disable --now "${u}.service" 2>/dev/null || true
      echo "Disabled: ${u}.service"
    fi
  done
fi

# 2) Boot to multi-user by default (no graphical.target)
systemctl set-default multi-user.target
echo "Default target: multi-user.target"

# 3) Kiosk via startx (same on Desktop and Lite images after bootstrap)
systemctl daemon-reload
systemctl enable --now blockvase-kiosk.service
echo "Enabled (and started): blockvase-kiosk.service"

# 4) Remove desktop-only autostart so we do not launch kiosk-desktop twice after a future desktop restore
if [[ -x "${PROJECT_DIR}/scripts/remove-desktop-kiosk-autostart.sh" ]]; then
  "${PROJECT_DIR}/scripts/remove-desktop-kiosk-autostart.sh" "${SERVICE_USER}"
fi

# 5) Hide "Welcome to Raspberry Pi" first-run wizard if the desktop image installed piwiz
if [[ -x "${PROJECT_DIR}/scripts/disable-pi-welcome-wizard.sh" ]]; then
  "${PROJECT_DIR}/scripts/disable-pi-welcome-wizard.sh" || true
fi

echo ""
echo "Done. Reboot to apply: sudo reboot"
echo "After reboot, HDMI should show the Blockvase kiosk without the full desktop first."
