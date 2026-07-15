#!/usr/bin/env bash
# Quick diagnostics when the HDMI kiosk does not appear after boot.
# Run on the Pi: bash ~/blockvase/scripts/kiosk-debug.sh
set -euo pipefail
echo "=== systemctl default target ==="
systemctl get-default 2>/dev/null || true
echo ""
echo "=== /dev/tty7 (X needs a free VT + tty group) ==="
ls -l /dev/tty7 2>/dev/null || true
echo ""
echo "=== blockvase.service ==="
systemctl status blockvase.service --no-pager -l 2>/dev/null || true
echo ""
echo "=== blockvase-kiosk.service (last 50 log lines) ==="
systemctl status blockvase-kiosk.service --no-pager -l 2>/dev/null || true
echo ""
journalctl -u blockvase-kiosk.service -n 50 --no-pager 2>/dev/null || true
echo ""
echo "=== kiosk browser log (if any) ==="
for u in "${SUDO_USER:-$USER}" blockvase pi; do
  home="$(getent passwd "$u" 2>/dev/null | cut -d: -f6)"
  if [[ -n "${home}" && -f "${home}/logs/kiosk-browser.log" ]]; then
    echo "--- ${home}/logs/kiosk-browser.log (tail) ---"
    tail -30 "${home}/logs/kiosk-browser.log"
    break
  fi
done
