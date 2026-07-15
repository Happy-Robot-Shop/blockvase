#!/usr/bin/env bash
# AP / Wi-Fi diagnostics: Raspberry Pi OS Desktop + NetworkManager
# Run: sudo ~/blockvase/scripts/ap-debug.sh

set -euo pipefail
WLAN="${BLOCKVASE_WLAN_IFACE:-wlan0}"

echo "=== blockvase networking (NetworkManager) ==="
echo
echo "1. NetworkManager:"
systemctl is-active NetworkManager 2>/dev/null && systemctl --no-pager status NetworkManager 2>/dev/null | head -8 || echo "  not running"
echo
echo "2. Devices:"
nmcli device status 2>/dev/null || true
echo
echo "3. Saved connections (blockvase):"
nmcli -f NAME,TYPE,DEVICE connection show 2>/dev/null | grep -E "NAME|blockvase" || true
echo
echo "4. ${WLAN} address:"
ip -4 addr show "${WLAN}" 2>/dev/null || true
echo
echo "5. Restart AP / client: sudo systemctl restart blockvase-ap"
echo "   Password (hotspot): blockvase1234"
