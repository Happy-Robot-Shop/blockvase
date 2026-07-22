#!/usr/bin/env bash
# NetworkManager dispatcher: retry saved Wi-Fi, then soft-recover (setup AP + QR)
# while keeping credentials and continuing reconnect attempts.
# Installed to /etc/NetworkManager/dispatcher.d/99-blockvase-wifi by bootstrap.
set -euo pipefail

IFACE="${1:-}"
ACTION="${2:-}"
WLAN_IFACE="${BLOCKVASE_WLAN_IFACE:-wlan0}"
AP_MODE_SCRIPT="__PROJECT_DIR__/scripts/ap-mode.sh"

[[ "${IFACE}" == "${WLAN_IFACE}" ]] || exit 0

case "${ACTION}" in
  down|down-pre|connectivity-change|dhcp4-change|dhcp6-change)
    ;;
  *)
    exit 0
    ;;
esac

[[ -x "${AP_MODE_SCRIPT}" ]] || exit 0
# Background so NM dispatcher is not blocked for the grace sleep inside check-online.
"${AP_MODE_SCRIPT}" check-online >/dev/null 2>&1 &
exit 0
