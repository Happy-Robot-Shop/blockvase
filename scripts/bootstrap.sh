#!/usr/bin/env bash
# Blockvase bootstrap: Raspberry Pi OS 64-bit (Bookworm / Trixie or later), Desktop or Lite image.
# Configures kiosk-only boot: multi-user.target + blockvase-kiosk.service (startx/Chromium on :0, vt7).
# blockvase-kiosk.service uses SupplementaryGroups=tty video input plus StandardInput=tty and TTYPath=/dev/tty7
# so Xorg can open the virtual console (see systemd template). The full desktop is not used; see restore-desktop-boot.sh.
#
# Solo mining (DATUM Gateway + PiAxe-miner: scripts/install-mining-stack.sh after Bitcoin Knots):
#   configure-mining-uart-console.sh runs first: enable_uart, i2c_arm, dtoverlay=pwm (PiAxe buck), serial0 console cleanup
#   BLOCKVASE_SKIP_MINING_STACK=1: skip building datum_gateway and PiAxe-miner systemd units
#   Miner service is enabled by default (board/ASIC monitoring). Hashing via DATUM
#   starts after the user saves a payout address on the Settings page.
# Mining simulation (real DATUM/PiAxe stack, CPU replaces BM1366 only):
#   Toggle is persisted in Settings (mining_simulation_enabled in data/config.json; default false).
#   Enables config.blockvase.simulate.yml via blockvase-miner-refresh-env.sh; dashboard always reads PiAxe-miner REST.
#
# Optional (boot look & feel: all use scripts/disable-boot-splash.sh):
#   BLOCKVASE_DISABLE_BOOT_SPLASH=1: hide Pi rainbow (config.txt) + drop Plymouth "splash" from cmdline.txt
#   BLOCKVASE_SILENT_BOOT=1: aggressive kernel/systemd quiet tokens (see disable-boot-splash.sh)
#   BLOCKVASE_KERNEL_QUIET=1: moderate quiet tokens (ignored if BLOCKVASE_SILENT_BOOT=1)
# You can set SILENT_BOOT or KERNEL_QUIET without DISABLE_BOOT_SPLASH; the script still runs and edits firmware.
# Firmware pause before Linux (scripts/set-firmware-boot-delay.sh): default BLOCKVASE_FIRMWARE_BOOT_DELAY_SEC=6;
#   set to 0 to omit boot_delay / remove existing boot_delay lines from config.txt during bootstrap.
# Firmware display orientation (scripts/set-display-rotation.sh: config.txt display_rotate).
#   On many Pi Bookworm/Trixie + KMS setups, firmware rotation has no visible effect once X/Chromium starts;
#   kiosk rotation is handled in scripts/kiosk-session.sh via BLOCKVASE_KIOSK_XRANDR_ROTATE (see systemd/blockvase-kiosk.service).
#   Default BLOCKVASE_DISPLAY_ROTATION=skip avoids stacking firmware+xrandr. Set ccw90 here only if you also need bootloader/console rotated.
#   Other: cw90, cw180: reboot after bootstrap. Use BLOCKVASE_DISPLAY_ROTATION=skip to omit this step.
# Installs systemd/blockvase-switch-to-kiosk-vt.service (chvt 7 before kiosk) with other units: no extra env needed.
# Bitcoin chain corruption: blockvase-chain-guard.timer runs scripts/chain-guard.sh (~3 min) to auto-start
#   -reindex-chainstate and remove the override when sync completes (see scripts/reindex-chainstate.sh).
# Clone safety: scripts/clone-safety.sh is the single identity/expand engine.
#   Bootstrap calls it after config exists. On every boot, blockvase-ap.service runs
#   ap-mode.sh ensure → clone-safety. Fingerprint mismatch (cloned image) refreshes
#   machine-id/SSH/hostname/leases and expands root; it does not wipe Bitcoin data.

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="${SUDO_USER:-$USER}"

if [[ "${SERVICE_USER}" == "root" ]]; then
  if id -u blockvase >/dev/null 2>&1; then
    SERVICE_USER="blockvase"
  else
    echo "Refusing to install as root without a blockvase user."
    echo "Create user blockvase (or rename pi with ./nvme-clone-tools/rename-user-to-blockvase.sh),"
    echo "then run: sudo -u blockvase ./scripts/bootstrap.sh   # or: sudo ./scripts/bootstrap.sh as blockvase"
    exit 1
  fi
fi

if [[ "${SERVICE_USER}" != "blockvase" ]]; then
  echo "Bootstrap requires the service user to be 'blockvase' (got '${SERVICE_USER}')."
  echo "Create/rename the user, then re-run as that user:"
  echo "  ./nvme-clone-tools/rename-user-to-blockvase.sh"
  echo "  sudo ./scripts/bootstrap.sh"
  exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

run_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

echo "[1/8] Installing OS packages..."
${SUDO} apt-get update -y
${SUDO} apt-get install -y \
  avahi-daemon \
  cloud-guest-utils \
  e2fsprogs \
  gdisk \
  python3 \
  python3-venv \
  python3-pip \
  python3-xdg \
  iw \
  curl \
  jq \
  xdg-utils \
  x11-xserver-utils \
  network-manager \
  xserver-xorg \
  xinit \
  openbox \
  unclutter \
  xdotool

# Chromium: Bookworm uses chromium-browser; Trixie often only has chromium (see kiosk-session.sh paths).
CHROMIUM_PKG=""
for pkg in chromium-browser chromium; do
  if ${SUDO} apt-get install -y "$pkg" 2>/dev/null; then
    CHROMIUM_PKG="$pkg"
    break
  fi
done
if [[ -z "${CHROMIUM_PKG}" ]]; then
  echo "ERROR: Could not install chromium-browser or chromium. Install one manually, then re-run bootstrap."
  exit 1
fi
echo "Installed browser package: ${CHROMIUM_PKG}"

echo "[2/8] Creating virtual environment..."
if [[ ! -x "${PROJECT_DIR}/.venv/bin/python3" ]]; then
  python3 -m venv "${PROJECT_DIR}/.venv"
fi
source "${PROJECT_DIR}/.venv/bin/activate"
pip install --upgrade pip
pip install -r "${PROJECT_DIR}/requirements.txt"

echo "[3/8] Preparing runtime directories..."
mkdir -p "${PROJECT_DIR}/data" "${PROJECT_DIR}/logs"
${SUDO} mkdir -p /var/lib/blockvase
${SUDO} chmod 755 /var/lib/blockvase
# Xorg on a VT: group tty on /dev/tty7, plus video/input/render for DRM. The kiosk unit sets
# SupplementaryGroups= and TTYPath=/dev/tty7 (startx needs a controlling console from systemd).
for g in tty video input render; do
  if getent group "$g" >/dev/null 2>&1; then
    ${SUDO} usermod -aG "$g" "${SERVICE_USER}" 2>/dev/null || true
  fi
done

# Ensure config exists with setup_complete: false (shows setup QR on display)
CONFIG_JSON="${PROJECT_DIR}/data/config.json"
if [[ ! -f "${CONFIG_JSON}" ]]; then
  echo '{"device_name":"blockvase","theme":"default","display_offset_x":0,"wifi_ssid":"","wifi_password":"","setup_complete":false,"setup_token":"","admin_username":"","admin_password_hash":"","mining_payout_address":"","mining_simulation_enabled":false,"rpc":{"host":"127.0.0.1","port":8332,"user":"","password":"","use_https":false,"timeout_seconds":8}}' \
    > "${CONFIG_JSON}"
  chown "${SERVICE_USER}:${SERVICE_USER}" "${CONFIG_JSON}" 2>/dev/null || true
fi

echo "       -> Running clone-safety (identity + expand when needed)..."
chmod +x "${PROJECT_DIR}/scripts/clone-safety.sh" 2>/dev/null || true
if [[ -x "${PROJECT_DIR}/scripts/clone-safety.sh" ]]; then
  run_root env PROJECT_DIR="${PROJECT_DIR}" CONFIG_JSON="${CONFIG_JSON}" \
    "${PROJECT_DIR}/scripts/clone-safety.sh" run || true
else
  echo "       -> WARNING: scripts/clone-safety.sh missing; skipping identity/expand"
fi

echo "[4/8] Making scripts executable..."
chmod +x "${PROJECT_DIR}/scripts/run.sh"
chmod +x "${PROJECT_DIR}/scripts/bootstrap.sh"
chmod +x "${PROJECT_DIR}/scripts/kiosk-session.sh"
[[ -f "${PROJECT_DIR}/scripts/ap-mode.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/ap-mode.sh"
[[ -f "${PROJECT_DIR}/scripts/clone-safety.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/clone-safety.sh"
[[ -f "${PROJECT_DIR}/scripts/nm-dispatcher-wifi-recovery.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/nm-dispatcher-wifi-recovery.sh"
[[ -f "${PROJECT_DIR}/scripts/ap-debug.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/ap-debug.sh"
[[ -f "${PROJECT_DIR}/scripts/install-pi5-xorg-fix.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/install-pi5-xorg-fix.sh"
[[ -f "${PROJECT_DIR}/scripts/kiosk-desktop.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/kiosk-desktop.sh"
[[ -f "${PROJECT_DIR}/scripts/ensure-desktop-kiosk-autostart.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/ensure-desktop-kiosk-autostart.sh"
[[ -f "${PROJECT_DIR}/scripts/remove-desktop-kiosk-autostart.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/remove-desktop-kiosk-autostart.sh"
[[ -f "${PROJECT_DIR}/scripts/enable-kiosk-only-boot.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/enable-kiosk-only-boot.sh"
[[ -f "${PROJECT_DIR}/scripts/restore-desktop-boot.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/restore-desktop-boot.sh"
[[ -f "${PROJECT_DIR}/scripts/kiosk-debug.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/kiosk-debug.sh"
[[ -f "${PROJECT_DIR}/scripts/disable-boot-splash.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/disable-boot-splash.sh"
[[ -f "${PROJECT_DIR}/scripts/set-firmware-boot-delay.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/set-firmware-boot-delay.sh"
[[ -f "${PROJECT_DIR}/scripts/set-display-rotation.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/set-display-rotation.sh"
[[ -f "${PROJECT_DIR}/scripts/disable-pi-welcome-wizard.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/disable-pi-welcome-wizard.sh"
[[ -f "${PROJECT_DIR}/scripts/install-bitcoin-knots.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/install-bitcoin-knots.sh"
[[ -f "${PROJECT_DIR}/scripts/install-mining-stack.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/install-mining-stack.sh"
[[ -f "${PROJECT_DIR}/scripts/configure-mining-uart-console.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/configure-mining-uart-console.sh"
[[ -f "${PROJECT_DIR}/scripts/mining-software-preflight.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/mining-software-preflight.sh"
[[ -f "${PROJECT_DIR}/scripts/blockvase-miner-run.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/blockvase-miner-run.sh"
[[ -f "${PROJECT_DIR}/scripts/set-mining-payout.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/set-mining-payout.sh"
[[ -f "${PROJECT_DIR}/scripts/blockvase-miner-refresh-env.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/blockvase-miner-refresh-env.sh"
[[ -f "${PROJECT_DIR}/scripts/reindex-chainstate.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/reindex-chainstate.sh"
[[ -f "${PROJECT_DIR}/scripts/chain-guard.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/chain-guard.sh"
[[ -f "${PROJECT_DIR}/scripts/check-asic.sh" ]] && chmod +x "${PROJECT_DIR}/scripts/check-asic.sh"

echo "[5/8] Allowing Python to bind to port 80 (so blockvase.local works)..."
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python3"
if [[ -f "${PYTHON_BIN}" ]]; then
  PYTHON_REAL="$(readlink -f "${PYTHON_BIN}" 2>/dev/null || realpath "${PYTHON_BIN}" 2>/dev/null || echo "${PYTHON_BIN}")"
  ${SUDO:+${SUDO} }setcap 'cap_net_bind_service=+ep' "${PYTHON_REAL}" 2>/dev/null || true
fi

echo "[6/8] Configuring Xorg for kiosk..."
${SUDO} mkdir -p /etc/X11
cat <<'EOF' | ${SUDO} tee /etc/X11/Xwrapper.config >/dev/null
allowed_users=anybody
needs_root_rights=yes
EOF
# Raspberry Pi 5: fix "Cannot run in framebuffer mode" so startx/blockvase-kiosk works
if [[ -f "${PROJECT_DIR}/xorg-conf/99-vc4.conf" ]]; then
  ${SUDO} mkdir -p /etc/X11/xorg.conf.d
  ${SUDO} cp "${PROJECT_DIR}/xorg-conf/99-vc4.conf" /etc/X11/xorg.conf.d/99-vc4.conf
fi

echo "[7/8] Installing systemd units..."
SERVICE_UID="$(getent passwd "${SERVICE_USER}" | cut -d: -f3)"
if [[ -z "${SERVICE_UID}" ]]; then
  echo "ERROR: could not resolve UID for ${SERVICE_USER}"
  exit 1
fi
TMP_BACKEND="$(mktemp)"
TMP_KIOSK="$(mktemp)"
TMP_AP="$(mktemp)"
TMP_CHAIN_GUARD="$(mktemp)"
sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g; s|__SERVICE_USER__|${SERVICE_USER}|g" \
  "${PROJECT_DIR}/systemd/blockvase.service" > "${TMP_BACKEND}"
sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g; s|__SERVICE_USER__|${SERVICE_USER}|g; s|__USER_UID__|${SERVICE_UID}|g" \
  "${PROJECT_DIR}/systemd/blockvase-kiosk.service" > "${TMP_KIOSK}"
sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g; s|__SERVICE_USER__|${SERVICE_USER}|g" \
  "${PROJECT_DIR}/systemd/blockvase-ap.service" > "${TMP_AP}"
sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
  "${PROJECT_DIR}/systemd/blockvase-chain-guard.service" > "${TMP_CHAIN_GUARD}"

${SUDO} cp "${TMP_BACKEND}" /etc/systemd/system/blockvase.service
${SUDO} cp "${TMP_KIOSK}" /etc/systemd/system/blockvase-kiosk.service
${SUDO} cp "${TMP_AP}" /etc/systemd/system/blockvase-ap.service
${SUDO} cp "${PROJECT_DIR}/systemd/blockvase-switch-to-kiosk-vt.service" /etc/systemd/system/blockvase-switch-to-kiosk-vt.service
${SUDO} cp "${PROJECT_DIR}/systemd/bitcoind.service" /etc/systemd/system/bitcoind.service
${SUDO} cp "${TMP_CHAIN_GUARD}" /etc/systemd/system/blockvase-chain-guard.service
${SUDO} cp "${PROJECT_DIR}/systemd/blockvase-chain-guard.timer" /etc/systemd/system/blockvase-chain-guard.timer
TMP_WIFI_WATCH="$(mktemp)"
sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
  "${PROJECT_DIR}/systemd/blockvase-wifi-watch.service" > "${TMP_WIFI_WATCH}"
${SUDO} cp "${TMP_WIFI_WATCH}" /etc/systemd/system/blockvase-wifi-watch.service
${SUDO} cp "${PROJECT_DIR}/systemd/blockvase-wifi-watch.timer" /etc/systemd/system/blockvase-wifi-watch.timer
rm -f "${TMP_BACKEND}" "${TMP_KIOSK}" "${TMP_AP}" "${TMP_CHAIN_GUARD}" "${TMP_WIFI_WATCH}"

if [[ ! -f "${PROJECT_DIR}/systemd/blockvase-chain-guard.service" ]] || [[ ! -f "${PROJECT_DIR}/systemd/blockvase-chain-guard.timer" ]]; then
  echo "WARNING: blockvase-chain-guard systemd units missing; automatic chain recovery will not run."
fi

# NetworkManager dispatcher: Wi-Fi drop -> setup AP + QR (also covered by wifi-watch timer).
if [[ -f "${PROJECT_DIR}/scripts/nm-dispatcher-wifi-recovery.sh" ]]; then
  TMP_NM_DISP="$(mktemp)"
  sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
    "${PROJECT_DIR}/scripts/nm-dispatcher-wifi-recovery.sh" > "${TMP_NM_DISP}"
  ${SUDO} mkdir -p /etc/NetworkManager/dispatcher.d
  ${SUDO} cp "${TMP_NM_DISP}" /etc/NetworkManager/dispatcher.d/99-blockvase-wifi
  ${SUDO} chmod 755 /etc/NetworkManager/dispatcher.d/99-blockvase-wifi
  rm -f "${TMP_NM_DISP}"
fi

# Firmware pause before Linux (config.txt boot_delay). Override: BLOCKVASE_FIRMWARE_BOOT_DELAY_SEC=0 to skip/remove.
if [[ -x "${PROJECT_DIR}/scripts/set-firmware-boot-delay.sh" ]]; then
  FW_DELAY="${BLOCKVASE_FIRMWARE_BOOT_DELAY_SEC:-6}"
  echo "       → Firmware boot delay (${FW_DELAY}s in config.txt)..."
  if [[ "${EUID}" -eq 0 ]]; then
    BLOCKVASE_FIRMWARE_BOOT_DELAY_SEC="${FW_DELAY}" "${PROJECT_DIR}/scripts/set-firmware-boot-delay.sh"
  else
    ${SUDO} env BLOCKVASE_FIRMWARE_BOOT_DELAY_SEC="${FW_DELAY}" "${PROJECT_DIR}/scripts/set-firmware-boot-delay.sh"
  fi
fi

if [[ -x "${PROJECT_DIR}/scripts/set-display-rotation.sh" ]]; then
  DISPLAY_ROT="${BLOCKVASE_DISPLAY_ROTATION:-skip}"
  if [[ "${DISPLAY_ROT}" != "skip" ]]; then
    echo "       → Firmware display orientation (${DISPLAY_ROT})..."
    if [[ "${EUID}" -eq 0 ]]; then
      BLOCKVASE_DISPLAY_ROTATION="${DISPLAY_ROT}" "${PROJECT_DIR}/scripts/set-display-rotation.sh"
    else
      ${SUDO} env BLOCKVASE_DISPLAY_ROTATION="${DISPLAY_ROT}" "${PROJECT_DIR}/scripts/set-display-rotation.sh"
    fi
  fi
else
  echo "WARNING: set-display-rotation.sh missing; display orientation unchanged."
fi

if [[ -x "${PROJECT_DIR}/scripts/install-bitcoin-knots.sh" ]]; then
  echo "       → Bitcoin Knots (local archival node, JSON-RPC on 127.0.0.1:8332)..."
  if [[ "${EUID}" -eq 0 ]]; then
    "${PROJECT_DIR}/scripts/install-bitcoin-knots.sh" "${PROJECT_DIR}"
  else
    ${SUDO} "${PROJECT_DIR}/scripts/install-bitcoin-knots.sh" "${PROJECT_DIR}"
  fi
else
  echo "WARNING: install-bitcoin-knots.sh missing; bitcoind not installed."
fi

if [[ "${BLOCKVASE_SKIP_MINING_STACK:-}" == "1" ]]; then
  echo "       → Skipping mining stack (BLOCKVASE_SKIP_MINING_STACK=1)."
elif [[ -x "${PROJECT_DIR}/scripts/install-mining-stack.sh" ]]; then
  echo "       → Solo mining stack (DATUM Gateway + PiAxe-miner → local Knots)..."
  echo "       → PiAxe boot prep (UART, I2C, PWM overlay, serial console)..."
  if [[ -x "${PROJECT_DIR}/scripts/configure-mining-uart-console.sh" ]]; then
    if [[ "${EUID}" -eq 0 ]]; then
      "${PROJECT_DIR}/scripts/configure-mining-uart-console.sh"
    else
      ${SUDO} "${PROJECT_DIR}/scripts/configure-mining-uart-console.sh"
    fi
  fi
  if [[ "${EUID}" -eq 0 ]]; then
    "${PROJECT_DIR}/scripts/install-mining-stack.sh" "${PROJECT_DIR}" "${SERVICE_USER}"
  else
    ${SUDO} "${PROJECT_DIR}/scripts/install-mining-stack.sh" "${PROJECT_DIR}" "${SERVICE_USER}"
  fi
else
  echo "WARNING: install-mining-stack.sh missing; mining stack not installed."
fi

echo "[8/8] Enabling services..."
${SUDO} systemctl daemon-reload
${SUDO} loginctl enable-linger "${SERVICE_USER}" || true
${SUDO} systemctl enable --now NetworkManager 2>/dev/null || true
echo "${SERVICE_USER} ALL=(ALL) NOPASSWD: ${PROJECT_DIR}/scripts/ap-mode.sh" | ${SUDO} tee /etc/sudoers.d/blockvase-ap >/dev/null
echo "${SERVICE_USER} ALL=(ALL) NOPASSWD: ${PROJECT_DIR}/scripts/set-mining-payout.sh" | ${SUDO} tee /etc/sudoers.d/blockvase-mining-payout >/dev/null
echo "${SERVICE_USER} ALL=(ALL) NOPASSWD: ${PROJECT_DIR}/scripts/blockvase-miner-refresh-env.sh" | ${SUDO} tee /etc/sudoers.d/blockvase-miner-env >/dev/null
echo "${SERVICE_USER} ALL=(ALL) NOPASSWD: ${PROJECT_DIR}/scripts/device-update.sh" | ${SUDO} tee /etc/sudoers.d/blockvase-device-update >/dev/null
echo "${SERVICE_USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop blockvase-miner.service, /usr/bin/systemctl start blockvase-miner.service" | ${SUDO} tee /etc/sudoers.d/blockvase-check-asic >/dev/null
{
  echo "${SERVICE_USER} ALL=(ALL) NOPASSWD: /usr/sbin/reboot"
  echo "${SERVICE_USER} ALL=(ALL) NOPASSWD: /sbin/reboot"
  echo "${SERVICE_USER} ALL=(ALL) NOPASSWD: /usr/bin/hostnamectl"
} | ${SUDO} tee /etc/sudoers.d/blockvase-reboot >/dev/null
${SUDO} chmod 440 /etc/sudoers.d/blockvase-ap /etc/sudoers.d/blockvase-mining-payout /etc/sudoers.d/blockvase-miner-env /etc/sudoers.d/blockvase-device-update /etc/sudoers.d/blockvase-check-asic /etc/sudoers.d/blockvase-reboot
${SUDO} chmod 755 "${PROJECT_DIR}/scripts/device-update.sh" 2>/dev/null || true
# Drop obsolete iptables port-redirect sudoers if present from older bootstraps.
${SUDO} rm -f /etc/sudoers.d/blockvase-port-redirect
if ${SUDO} command -v ufw >/dev/null 2>&1 && ${SUDO} ufw status | grep -q "Status: active"; then
  ${SUDO} ufw allow in on wlan0 proto udp to any port 67 || true
  ${SUDO} ufw allow in on wlan0 proto udp to any port 68 || true
  ${SUDO} ufw allow in on wlan0 proto tcp to any port 80 || true
fi
${SUDO} systemctl enable --now avahi-daemon.service 2>/dev/null || true
${SUDO} systemctl enable --now blockvase-ap.service
${SUDO} systemctl enable --now blockvase.service
if [[ -f "${PROJECT_DIR}/systemd/blockvase-chain-guard.timer" ]]; then
  ${SUDO} systemctl enable --now blockvase-chain-guard.timer
  ${SUDO} systemctl start blockvase-chain-guard.service 2>/dev/null || true
fi
if [[ -f /etc/systemd/system/blockvase-wifi-watch.timer ]]; then
  ${SUDO} systemctl enable --now blockvase-wifi-watch.timer
fi

# Kiosk-only: no full desktop on HDMI: disable display manager, multi-user boot, startx + Chromium.
if [[ -x "${PROJECT_DIR}/scripts/enable-kiosk-only-boot.sh" ]]; then
  ${SUDO} "${PROJECT_DIR}/scripts/enable-kiosk-only-boot.sh" --yes "${SERVICE_USER}"
else
  echo "WARNING: enable-kiosk-only-boot.sh not found; enabling blockvase-kiosk.service only."
  ${SUDO} systemctl enable --now blockvase-kiosk.service || true
fi

# Optional: boot splash + quiet cmdline (edits /boot/firmware or /boot config.txt + cmdline.txt; reboot to apply)
if [[ -x "${PROJECT_DIR}/scripts/disable-boot-splash.sh" ]] && {
  [[ "${BLOCKVASE_DISABLE_BOOT_SPLASH:-}" == "1" ]] ||
    [[ "${BLOCKVASE_SILENT_BOOT:-}" == "1" ]] ||
    [[ "${BLOCKVASE_KERNEL_QUIET:-}" == "1" ]];
}; then
  echo "[Optional] disable-boot-splash.sh (env: DISABLE_BOOT_SPLASH=${BLOCKVASE_DISABLE_BOOT_SPLASH:-0} SILENT_BOOT=${BLOCKVASE_SILENT_BOOT:-0} KERNEL_QUIET=${BLOCKVASE_KERNEL_QUIET:-0})..."
  run_splash() {
    if [[ "${EUID}" -eq 0 ]]; then
      if [[ "${BLOCKVASE_SILENT_BOOT:-}" == "1" ]]; then
        BLOCKVASE_SILENT_BOOT=1 "${PROJECT_DIR}/scripts/disable-boot-splash.sh"
      elif [[ "${BLOCKVASE_KERNEL_QUIET:-}" == "1" ]]; then
        BLOCKVASE_KERNEL_QUIET=1 "${PROJECT_DIR}/scripts/disable-boot-splash.sh"
      else
        "${PROJECT_DIR}/scripts/disable-boot-splash.sh"
      fi
    else
      if [[ "${BLOCKVASE_SILENT_BOOT:-}" == "1" ]]; then
        ${SUDO} env BLOCKVASE_SILENT_BOOT=1 "${PROJECT_DIR}/scripts/disable-boot-splash.sh"
      elif [[ "${BLOCKVASE_KERNEL_QUIET:-}" == "1" ]]; then
        ${SUDO} env BLOCKVASE_KERNEL_QUIET=1 "${PROJECT_DIR}/scripts/disable-boot-splash.sh"
      else
        ${SUDO} "${PROJECT_DIR}/scripts/disable-boot-splash.sh"
      fi
    fi
  }
  run_splash
fi

echo
echo "Setup complete."
echo "Web portal: http://$(hostname -I | awk '{print $1}')"
echo "            http://$(hostname).local (after device name is set)"
echo "Service status: sudo systemctl status blockvase.service"
echo ""
echo "Bitcoin: bitcoind.service (Knots full node: journalctl -u bitcoind.service)"
echo "  Auto chain corruption recovery: blockvase-chain-guard.timer (scripts/chain-guard.sh every ~3 min)"
echo "  Chain guard status: sudo ${PROJECT_DIR}/scripts/chain-guard.sh status"
echo "  Manual reindex: sudo ${PROJECT_DIR}/scripts/reindex-chainstate.sh {start|status|finish}"
echo "Mining (solo): datum-gateway.service + blockvase-miner.service (DATUM → Stratum :23334 → PiAxe)"
echo "  Miner runs by default (graceful if ASIC/board is missing). Save a payout address in Settings to start hashing."
echo "  Optional CPU simulated-ASIC tests: enable mining simulation under Settings → Solo Mining (DATUM/bitcoind unchanged)."
echo "Kiosk: blockvase-kiosk.service (startx, vt7, ~/logs/kiosk-browser.log)"
echo "  Also: blockvase-switch-to-kiosk-vt.service (chvt 7 before kiosk, blank HDMI until X)"
echo "  Check: sudo systemctl status blockvase-kiosk.service  (expect active/running)"
echo "  If the HDMI kiosk fails: bash ${PROJECT_DIR}/scripts/kiosk-debug.sh"
echo "  Reboot after bootstrap if the display still shows a login/desktop: sudo reboot"
echo "  Restore full Pi desktop (optional): sudo ${PROJECT_DIR}/scripts/restore-desktop-boot.sh ${SERVICE_USER} ${PROJECT_DIR}"
echo "  Hide rainbow / Plymouth / tune cmdline: sudo ${PROJECT_DIR}/scripts/disable-boot-splash.sh && sudo reboot"
echo "    Or: sudo env BLOCKVASE_DISABLE_BOOT_SPLASH=1 ${PROJECT_DIR}/scripts/bootstrap.sh"
echo "    Quieter boot (cmdline): add BLOCKVASE_SILENT_BOOT=1 or BLOCKVASE_KERNEL_QUIET=1 to that env (see README)"
echo "    Firmware boot delay: sudo ${PROJECT_DIR}/scripts/set-firmware-boot-delay.sh 6   (or BLOCKVASE_FIRMWARE_BOOT_DELAY_SEC=0 on bootstrap to skip)"
echo "    Firmware display rotate (often unused once X/KMS kiosk runs, see BLOCKVASE_KIOSK_XRANDR_ROTATE in kiosk unit): sudo ${PROJECT_DIR}/scripts/set-display-rotation.sh ccw90"
