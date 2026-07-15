#!/usr/bin/env bash
# Set Raspberry Pi firmware boot_delay in config.txt (pause after GPU firmware, before Linux).
# See: https://www.raspberrypi.com/documentation/computers/legacy_config_txt.html
#
# Usage: sudo ./scripts/set-firmware-boot-delay.sh [seconds]
# Env:   BLOCKVASE_FIRMWARE_BOOT_DELAY_SEC: used when [seconds] is omitted (default 6)
#
# Reboot to apply. Undo: edit config.txt and remove boot_delay=, or run with 0:
#   sudo BLOCKVASE_FIRMWARE_BOOT_DELAY_SEC=0 ./scripts/set-firmware-boot-delay.sh
#
# Backup: config.txt.bak.blockvase (created before each edit).

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

SEC="${1:-${BLOCKVASE_FIRMWARE_BOOT_DELAY_SEC:-6}}"
if ! [[ "${SEC}" =~ ^[0-9]+$ ]] || [[ "${SEC}" -gt 255 ]]; then
  echo "Invalid seconds: ${SEC} (want integer 0 to 255)"
  exit 1
fi

FW=/boot/firmware
if [[ ! -d "${FW}" ]]; then
  FW=/boot
fi

CONFIG="${FW}/config.txt"

if [[ ! -f "${CONFIG}" ]]; then
  echo "No ${CONFIG} found: skip firmware boot delay (not Raspberry Pi OS firmware layout?)"
  exit 0
fi

cp -a "${CONFIG}" "${CONFIG}.bak.blockvase"

# Drop prior blockvase-managed boot_delay block (idempotent across re-runs).
sed -i '/^# Added by blockvase set-firmware-boot-delay\.sh/d' "${CONFIG}"
sed -i '/^[[:space:]]*boot_delay=/d' "${CONFIG}"

if [[ "${SEC}" -gt 0 ]]; then
  {
    echo ""
    echo "# Added by blockvase set-firmware-boot-delay.sh: firmware pause before Linux"
    echo "boot_delay=${SEC}"
  } >>"${CONFIG}"
  echo "Set boot_delay=${SEC} in ${CONFIG} (backup: ${CONFIG}.bak.blockvase)"
  echo "Reboot to apply: sudo reboot"
else
  echo "Removed boot_delay from ${CONFIG} (backup: ${CONFIG}.bak.blockvase)"
fi
