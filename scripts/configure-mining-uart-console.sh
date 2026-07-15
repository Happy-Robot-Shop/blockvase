#!/usr/bin/env bash
# Prepare Raspberry Pi GPIO header UART/I2C for the PiAxe BM1366 board.
#
# Non-simulated mining needs exclusive access to /dev/serial0. Raspberry Pi OS
# images often boot with `console=serial0,115200` and a serial-getty on the same
# device, which can make BM1366 discovery report zero chips. This script removes
# that console, masks common serial getty units, and ensures boot config keeps
# UART/I2C enabled for the mining HAT.

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

CONFIG_TXT=""
for p in /boot/firmware/config.txt /boot/config.txt; do
  if [[ -f "${p}" ]]; then
    CONFIG_TXT="${p}"
    break
  fi
done

CMDLINE_TXT=""
for p in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
  if [[ -f "${p}" ]]; then
    CMDLINE_TXT="${p}"
    break
  fi
done

ensure_config_line() {
  local line="$1"
  local key="${line%%=*}"

  [[ -z "${CONFIG_TXT}" ]] && return 0
  if grep -Eq "^[[:space:]]*${key}=" "${CONFIG_TXT}"; then
    sed -i -E "s|^[[:space:]]*${key}=.*|${line}|" "${CONFIG_TXT}"
  elif grep -Eq "^[[:space:]]*#${key}=" "${CONFIG_TXT}"; then
    sed -i -E "s|^[[:space:]]*#${key}=.*|${line}|" "${CONFIG_TXT}"
  else
    {
      echo ""
      echo "# Added by Blockvase: PiAxe mining HAT UART/I2C"
      echo "${line}"
    } >>"${CONFIG_TXT}"
  fi
}

ensure_overlay_line() {
  local line="$1"
  [[ -z "${CONFIG_TXT}" ]] && return 0
  if grep -Fq "${line}" "${CONFIG_TXT}"; then
    return 0
  fi
  if grep -Fq "#${line}" "${CONFIG_TXT}"; then
    sed -i -E "s|^[[:space:]]*#${line}|${line}|" "${CONFIG_TXT}"
    return 0
  fi
  {
    echo ""
    echo "# Added by Blockvase: PiAxe buck converter PWM (GPIO18 / physical pin 12)"
    echo "${line}"
  } >>"${CONFIG_TXT}"
}

if [[ -n "${CONFIG_TXT}" ]]; then
  ensure_config_line "enable_uart=1"
  ensure_config_line "dtparam=i2c_arm=on"
  ensure_overlay_line "dtoverlay=pwm"
  echo "Ensured UART/I2C/PWM boot params in ${CONFIG_TXT}"
fi

if [[ -n "${CMDLINE_TXT}" ]] && grep -Eq '(^|[[:space:]])console=serial0(,115200)?([[:space:]]|$)' "${CMDLINE_TXT}"; then
  cp -a "${CMDLINE_TXT}" "${CMDLINE_TXT}.bak-before-mining-uart"
  python3 - "$CMDLINE_TXT" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
parts = p.read_text(encoding="utf-8").split()
parts = [x for x in parts if not (x == "console=serial0" or x.startswith("console=serial0,"))]
p.write_text(" ".join(parts) + "\n", encoding="utf-8")
PY
  echo "Removed serial0 console from ${CMDLINE_TXT}; reboot once for full kernel detach."
fi

for unit in serial-getty@ttyAMA10.service serial-getty@serial0.service; do
  systemctl disable --now "${unit}" 2>/dev/null || true
  systemctl mask "${unit}" 2>/dev/null || true
done

echo "Serial getty disabled/masked for serial0 mining UART."
