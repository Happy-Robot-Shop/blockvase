#!/usr/bin/env bash
# Non-simulated mining preflight for Blockvase.
# This intentionally avoids changing the system. It reports Pi/software blockers
# separately from board-level LM75/BM1366 failures.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

failures=0
warnings=0

pass() { printf 'PASS: %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*"; warnings=$((warnings + 1)); }
fail() { printf 'FAIL: %s\n' "$*"; failures=$((failures + 1)); }
info() { printf 'INFO: %s\n' "$*"; }

cmdline_file=""
for p in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
  [[ -f "${p}" ]] && cmdline_file="${p}" && break
done

config_file=""
for p in /boot/firmware/config.txt /boot/config.txt; do
  [[ -f "${p}" ]] && config_file="${p}" && break
done

if [[ -n "${cmdline_file}" ]]; then
  if awk '{for (i=1; i<=NF; i++) if ($i ~ /^console=serial0(,|$)/) found=1} END {exit found ? 0 : 1}' "${cmdline_file}"; then
    fail "${cmdline_file} still has console=serial0; BM1366 UART will be contaminated until removed and rebooted."
  else
    pass "kernel cmdline does not attach console=serial0"
  fi
else
  warn "could not find cmdline.txt"
fi

if [[ -n "${config_file}" ]]; then
  if awk '/^[[:space:]]*enable_uart=1([[:space:]]|$)/ {found=1} END {exit found ? 0 : 1}' "${config_file}"; then
    pass "enable_uart=1 present in ${config_file}"
  else
    warn "enable_uart=1 not present in ${config_file}; serial0 exists now, but add it for stable boot config."
  fi

  if awk '/^[[:space:]]*dtparam=i2c_arm=on([[:space:]]|$)/ {found=1} END {exit found ? 0 : 1}' "${config_file}"; then
    pass "dtparam=i2c_arm=on present in ${config_file}"
  else
    warn "dtparam=i2c_arm=on not present in ${config_file}; I2C adapters exist now, but add it for stable boot config."
  fi

  if grep -Fq 'dtoverlay=pwm' "${config_file}"; then
    pass "dtoverlay=pwm present in ${config_file} (PiAxe buck converter)"
  else
    fail "dtoverlay=pwm missing from ${config_file}; PiAxe buck PWM may not work until added and rebooted."
  fi
else
  warn "could not find config.txt"
fi

serial_target="$(readlink -f /dev/serial0 2>/dev/null || true)"
if [[ -n "${serial_target}" && -e "${serial_target}" ]]; then
  pass "/dev/serial0 resolves to ${serial_target}"
else
  fail "/dev/serial0 is missing; PiAxe config serial_port=/dev/serial0 cannot work."
fi

if systemctl is-active --quiet serial-getty@ttyAMA10.service 2>/dev/null ||
  systemctl is-active --quiet serial-getty@serial0.service 2>/dev/null; then
  fail "serial getty is active on the mining UART"
else
  pass "serial getty is not active on ttyAMA10/serial0"
fi

if [[ -n "${serial_target}" ]]; then
  users="$(fuser "${serial_target}" /dev/serial0 2>/dev/null || true)"
  if [[ -n "${users//[[:space:]]/}" ]]; then
    warn "something currently has ${serial_target}/serial0 open: ${users}"
  else
    pass "no process is currently holding /dev/serial0"
  fi
fi

if groups blockvase 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i=="dialout") d=1; else if ($i=="gpio") g=1; else if ($i=="i2c") c=1} END {exit (d&&g&&c) ? 0 : 1}'; then
  pass "blockvase user is in dialout/gpio/i2c"
else
  fail "blockvase user is missing one of dialout/gpio/i2c"
fi

if command -v i2cdetect >/dev/null 2>&1; then
  adapters="$(i2cdetect -l 2>/dev/null | awk '{print $1}' | paste -sd ',' -)"
  if [[ -n "${adapters}" ]]; then
    pass "I2C adapters visible: ${adapters}"
  else
    fail "no I2C adapters visible"
  fi
else
  warn "i2cdetect missing"
fi

if [[ -x "${PROJECT_DIR}/scripts/check-lm75-i2c.py" ]]; then
  if python3 "${PROJECT_DIR}/scripts/check-lm75-i2c.py" --quiet >/tmp/blockvase-lm75-preflight.out 2>/tmp/blockvase-lm75-preflight.err; then
    pass "LM75 responded at configured/default address"
    sed 's/^/  /' /tmp/blockvase-lm75-preflight.out
  else
    fail "LM75 did not respond to a real SMBus read at 0x48"
    sed 's/^/  /' /tmp/blockvase-lm75-preflight.err
  fi
fi

if systemctl is-active --quiet datum-gateway.service 2>/dev/null; then
  pass "datum-gateway.service is active"
else
  warn "datum-gateway.service is not active"
fi

if systemctl is-active --quiet blockvase-miner.service 2>/dev/null; then
  info "blockvase-miner.service is active; check-asic.sh will stop/start it for GPIO probing."
else
  info "blockvase-miner.service is not active."
fi

if command -v ss >/dev/null 2>&1; then
  ss_out="$(ss -tln 2>/dev/null || true)"
  if awk '$4 ~ /127\.0\.0\.1:23334$/ {found=1} END {exit found ? 0 : 1}' <<<"${ss_out}"; then
    pass "DATUM Stratum listener is up on 127.0.0.1:23334"
  else
    warn "DATUM Stratum listener not found on 127.0.0.1:23334"
  fi
  if awk '$4 ~ /127\.0\.0\.1:7152$/ {found=1} END {exit found ? 0 : 1}' <<<"${ss_out}"; then
    pass "DATUM NOTIFY listener is up on 127.0.0.1:7152"
  else
    warn "DATUM NOTIFY listener not found on 127.0.0.1:7152"
  fi
fi

printf '\nSummary: %d failure(s), %d warning(s)\n' "${failures}" "${warnings}"
if [[ "${failures}" -gt 0 ]]; then
  exit 2
fi
exit 0
