#!/usr/bin/env bash
# Hide Raspberry Pi boot/reboot visuals:
#   1) Firmware "rainbow" GPU splash: disable_splash=1 in config.txt
#   2) Plymouth: remove "splash" from cmdline, add plymouth.enable=0, mask shutdown/reboot plymouth units
#      (otherwise a theme image can still appear briefly *during* reboot/shutdown even without "splash")
#
# Backups: config is only appended; cmdline.txt.bak.blockvase is created before editing.
# Reboot to apply. Undo: restore cmdline backup; remove disable_splash line from config.txt;
#   sudo systemctl unmask plymouth-reboot.service plymouth-poweroff.service (and others printed below).
#
# Optional env:
#   BLOCKVASE_KERNEL_QUIET=1: moderate: quiet + systemd.show_status + loglevel=3
#   BLOCKVASE_SILENT_BOOT=1: stronger: lower loglevel, hide penguin logo, udev noise, hide VT cursor
#     (still not 100% silent: firmware may flash briefly; use SSH for recovery if you mask getty).
#
# Usage: sudo ./scripts/disable-boot-splash.sh

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

FW=/boot/firmware
if [[ ! -d "${FW}" ]]; then
  FW=/boot
fi

CONFIG="${FW}/config.txt"
CMDLINE="${FW}/cmdline.txt"

if [[ ! -f "${CONFIG}" ]]; then
  echo "No ${CONFIG} found: not Raspberry Pi OS firmware layout?"
  exit 1
fi

if grep -qE '^[[:space:]]*disable_splash=1' "${CONFIG}" 2>/dev/null; then
  echo "disable_splash=1 already set in ${CONFIG}"
else
  {
    echo ""
    echo "# Added by blockvase disable-boot-splash.sh: hide rainbow GPU splash"
    echo "disable_splash=1"
  } >>"${CONFIG}"
  echo "Wrote disable_splash=1 to ${CONFIG}"
fi

if [[ ! -f "${CMDLINE}" ]]; then
  echo "No ${CMDLINE}; rainbow disabled only. Reboot."
  exit 0
fi

cp -a "${CMDLINE}" "${CMDLINE}.bak.blockvase"
# Drop "splash" tokens (Plymouth); keep "quiet" unless you edit cmdline yourself.
sed -i \
  -e 's/[[:space:]]\{1,\}splash[[:space:]]\{1,\}/ /g' \
  -e 's/^[[:space:]]\{1,\}splash[[:space:]]\{1,\}//' \
  -e 's/[[:space:]]\{1,\}splash[[:space:]]*$//' \
  "${CMDLINE}"
sed -i 's/  */ /g' "${CMDLINE}"
sed -i 's/[[:space:]]*$//' "${CMDLINE}"

append_cmdline_token() {
  local tok="$1"
  if ! grep -qF "${tok}" "${CMDLINE}"; then
    sed -i "\$s/\$/ ${tok}/" "${CMDLINE}"
  fi
}

# Tell kernel / init not to start Plymouth (boot) and skip initrd plymouth if present.
append_cmdline_token "plymouth.enable=0"
append_cmdline_token "rd.plymouth=0"

mask_plymouth_shutdown_units() {
  command -v systemctl >/dev/null 2>&1 || return 0
  local u masked=0
  # Shutdown/reboot path only: avoids masking plymouth-start (can break desktop dependency chains).
  for u in plymouth-reboot.service plymouth-poweroff.service plymouth-halt.service plymouth-kexec.service; do
    if systemctl cat "${u}" &>/dev/null; then
      systemctl stop "${u}" 2>/dev/null || true
      systemctl mask "${u}" 2>/dev/null || true
      echo "Masked systemd unit: ${u}"
      masked=$((masked + 1))
    fi
  done
  if [[ "${masked}" -eq 0 ]]; then
    echo "No Plymouth reboot/poweroff units found (Plymouth not installed or different layout). Cmdline tokens still help."
  fi
}

mask_plymouth_shutdown_units
sed -i 's/  */ /g' "${CMDLINE}"
sed -i 's/[[:space:]]*$//' "${CMDLINE}"

if [[ "${BLOCKVASE_SILENT_BOOT:-}" == "1" ]]; then
  echo "BLOCKVASE_SILENT_BOOT=1: appending aggressive quiet tokens to cmdline..."
  for tok in quiet systemd.show_status=no rd.systemd.show_status=no udev.log-priority=3 logo.nologo vt.global_cursor_default=0; do
    append_cmdline_token "${tok}"
  done
  # Prefer a single loglevel= (stronger than KERNEL_QUIET’s loglevel=3)
  if grep -qE 'loglevel=[0-9]+' "${CMDLINE}"; then
    sed -i 's/loglevel=[0-9]*/loglevel=0/g' "${CMDLINE}"
  else
    append_cmdline_token "loglevel=0"
  fi
  sed -i 's/  */ /g' "${CMDLINE}"
elif [[ "${BLOCKVASE_KERNEL_QUIET:-}" == "1" ]]; then
  echo "BLOCKVASE_KERNEL_QUIET=1: appending kernel/systemd quiet tokens to cmdline..."
  for tok in quiet systemd.show_status=no rd.systemd.show_status=no loglevel=3; do
    append_cmdline_token "${tok}"
  done
  sed -i 's/  */ /g' "${CMDLINE}"
fi

echo "Updated ${CMDLINE} (backup: ${CMDLINE}.bak.blockvase)"
echo "Reboot to apply: sudo reboot"
