#!/usr/bin/env bash
# Install Blockvase kiosk autostart for Raspberry Pi OS Desktop.
# - XDG: ~/.config/autostart/*.desktop (LXSession / some sessions)
# - labwc: ~/.config/labwc/autostart (shell script: default compositor on Pi OS Bookworm+)
# Without the labwc hook, the kiosk often never starts after reboot.
#
# Usage: ./ensure-desktop-kiosk-autostart.sh <unix_user> <project_dir>
# Example: sudo ./ensure-desktop-kiosk-autostart.sh blockvase /home/blockvase/blockvase

set -euo pipefail

SERVICE_USER="${1:?user}"
PROJECT_DIR="${2:?project dir}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo $0 $*"
  exit 1
fi

HOME_REAL="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"
if [[ -z "${HOME_REAL}" || ! -d "${HOME_REAL}" ]]; then
  echo "Home directory for ${SERVICE_USER} not found."
  exit 1
fi

DESKTOP_AUTOSTART="${HOME_REAL}/.config/autostart"
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 755 "${DESKTOP_AUTOSTART}"

cat <<DESKTOP | tee "${DESKTOP_AUTOSTART}/blockvase-kiosk.desktop" >/dev/null
[Desktop Entry]
Type=Application
Name=Blockvase Kiosk
Comment=Fullscreen kiosk for blockvase /display
Exec=${PROJECT_DIR}/scripts/kiosk-desktop.sh
X-GNOME-Autostart-enabled=true
StartupNotify=false
DESKTOP
chown "${SERVICE_USER}:${SERVICE_USER}" "${DESKTOP_AUTOSTART}/blockvase-kiosk.desktop"
chmod 644 "${DESKTOP_AUTOSTART}/blockvase-kiosk.desktop"

# --- OS UI over kiosk: keyring prompts (e.g. when NM saves Wi-Fi) and notification toasts ---
# Override GNOME Keyring "secrets" autostart: avoids "unlock keyring" / libsecret prompts when
# NetworkManager stores Wi-Fi credentials or Chromium asks to save passwords in the keyring.
if [[ -x /usr/bin/gnome-keyring-daemon ]] && [[ ! -f "${DESKTOP_AUTOSTART}/gnome-keyring-secrets.desktop" ]]; then
  cat <<'KEYRING' | tee "${DESKTOP_AUTOSTART}/gnome-keyring-secrets.desktop" >/dev/null
[Desktop Entry]
Type=Application
Name=Secret Storage Service
Comment=Blockvase kiosk: disable gnome-keyring secrets autostart (prevents keyring popups over kiosk)
Exec=/bin/true
Hidden=true
X-GNOME-Autostart-enabled=false
KEYRING
  chown "${SERVICE_USER}:${SERVICE_USER}" "${DESKTOP_AUTOSTART}/gnome-keyring-secrets.desktop"
  chmod 644 "${DESKTOP_AUTOSTART}/gnome-keyring-secrets.desktop"
fi

# mako (notification daemon on many Wayland setups): dnd + invisible so banners don't cover Chromium
if command -v mako >/dev/null 2>&1; then
  MAKO_DIR="${HOME_REAL}/.config/mako"
  install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 755 "${MAKO_DIR}"
  MAKO_CFG="${MAKO_DIR}/config"
  if [[ ! -f "${MAKO_CFG}" ]]; then
    cat <<'MAKO' | tee "${MAKO_CFG}" >/dev/null
# blockvase-kiosk: hide desktop notifications over fullscreen Chromium
[global]
max-visible=0

[mode=dnd]
invisible=1
MAKO
    chown "${SERVICE_USER}:${SERVICE_USER}" "${MAKO_CFG}"
  elif ! grep -q 'blockvase-kiosk' "${MAKO_CFG}" 2>/dev/null; then
    {
      echo ""
      echo "# blockvase-kiosk"
      echo "[mode=dnd]"
      echo "invisible=1"
    } | tee -a "${MAKO_CFG}" >/dev/null
    chown "${SERVICE_USER}:${SERVICE_USER}" "${MAKO_CFG}"
  fi
fi

# labwc (default on Raspberry Pi OS Bookworm+): reads ~/.config/labwc/autostart as a shell script.
if [[ -x /usr/bin/labwc ]]; then
  LABWC_DIR="${HOME_REAL}/.config/labwc"
  LABWC_AUTOSTART="${LABWC_DIR}/autostart"
  install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 755 "${LABWC_DIR}"
  if [[ ! -f "${LABWC_AUTOSTART}" ]]; then
    printf '%s\n' '#!/bin/sh' | tee "${LABWC_AUTOSTART}" >/dev/null
    chown "${SERVICE_USER}:${SERVICE_USER}" "${LABWC_AUTOSTART}"
  fi
  if ! grep -q 'blockvase-kiosk' "${LABWC_AUTOSTART}" 2>/dev/null; then
    {
      echo ""
      echo "# Blockvase kiosk (added by blockvase ensure-desktop-kiosk-autostart.sh)"
      echo "# Small delay so NetworkManager + blockvase.service are up; kiosk-desktop.sh also waits for HTTP."
      echo "( sleep 8 ; ${PROJECT_DIR}/scripts/kiosk-desktop.sh ) &"
    } | tee -a "${LABWC_AUTOSTART}" >/dev/null
    chown "${SERVICE_USER}:${SERVICE_USER}" "${LABWC_AUTOSTART}"
  fi
  chmod 755 "${LABWC_AUTOSTART}" 2>/dev/null || chmod 644 "${LABWC_AUTOSTART}"
  chown "${SERVICE_USER}:${SERVICE_USER}" "${LABWC_AUTOSTART}"
fi

echo "Desktop kiosk autostart installed for ${SERVICE_USER}."
echo "  XDG: ${DESKTOP_AUTOSTART}/blockvase-kiosk.desktop"
if [[ -f "${DESKTOP_AUTOSTART}/gnome-keyring-secrets.desktop" ]]; then
  echo "  keyring: gnome-keyring secrets autostart overridden (reduces keyring popups)"
fi
if [[ -f "${HOME_REAL}/.config/mako/config" ]] && grep -q 'blockvase-kiosk' "${HOME_REAL}/.config/mako/config" 2>/dev/null; then
  echo "  mako: notification config adjusted (reboot or relogin; kiosk runs makoctl mode dnd)"
fi
if [[ -x /usr/bin/labwc ]]; then
  echo "  labwc: ${HOME_REAL}/.config/labwc/autostart"
fi
