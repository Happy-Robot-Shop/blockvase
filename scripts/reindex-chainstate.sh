#!/usr/bin/env bash
# Recover bitcoind when chainstate is corrupt:
#   Error initializing block database.
#   Please restart with -reindex or -reindex-chainstate to recover.
#
# Usage:
#   sudo ./scripts/reindex-chainstate.sh start    # one-time repair (keeps block files)
#   sudo ./scripts/reindex-chainstate.sh status   # watch progress
#   sudo ./scripts/reindex-chainstate.sh finish   # remove -reindex-chainstate after sync OK
#
# Automatic recovery: blockvase-chain-guard.timer runs chain-guard.sh (detect + start/finish).
set -euo pipefail

DROPIN_DIR="/etc/systemd/system/bitcoind.service.d"
DROPIN_FILE="${DROPIN_DIR}/reindex-chainstate.conf"
BITCOIND="/usr/local/bin/bitcoind"
CONF="/etc/bitcoin/bitcoin.conf"
DATADIR="/var/lib/bitcoind"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0 $*"
  exit 1
fi

cmd="${1:-}"

case "${cmd}" in
  start)
    avail_g="$(df -BG "${DATADIR}" | awk 'NR==2 {gsub(/G/,"",$4); print $4}')"
    if [[ -n "${avail_g}" && "${avail_g}" -lt 15 ]]; then
      echo "WARNING: less than 15 GB free on $(df -h "${DATADIR}" | awk 'NR==2 {print $1}')."
      echo "         Reindex-chainstate usually needs headroom; free disk space if possible."
      echo ""
    fi

    echo "Stopping bitcoind..."
    systemctl stop bitcoind.service || true

    mkdir -p "${DROPIN_DIR}"
    cat >"${DROPIN_FILE}" <<EOF
# Added by blockvase/scripts/reindex-chainstate.sh: remove with: reindex-chainstate.sh finish
[Service]
ExecStart=
ExecStart=${BITCOIND} -conf=${CONF} -datadir=${DATADIR} -reindex-chainstate
EOF

    systemctl daemon-reload
    systemctl start bitcoind.service
    echo ""
    echo "bitcoind started with -reindex-chainstate."
    echo "This rebuilds UTXO set from existing block files (hours on Pi, not days)."
    echo ""
    echo "Watch progress:"
    echo "  sudo $0 status"
    echo "  journalctl -u bitcoind.service -f"
    echo ""
    echo "When fully synced and stable, remove the one-time flag:"
    echo "  sudo $0 finish"
    ;;

  status)
    if [[ -f "${DROPIN_FILE}" ]]; then
      echo "Reindex mode: ACTIVE (${DROPIN_FILE})"
    else
      echo "Reindex mode: not active (normal bitcoind ExecStart)"
    fi
    echo ""
    systemctl is-active bitcoind.service || true
    df -h "${DATADIR}" | awk 'NR==1 || NR==2'
    echo ""
    journalctl -u bitcoind.service -n 25 --no-pager | grep -iE 'reindex|chainstate|progress|error|done|Shutdown|LoadBlockIndex|UpdateTip|verification' || \
      journalctl -u bitcoind.service -n 15 --no-pager
    ;;

  finish)
    if [[ ! -f "${DROPIN_FILE}" ]]; then
      echo "No reindex drop-in present; bitcoind is already on normal ExecStart."
      exit 0
    fi
    echo "Stopping bitcoind and removing -reindex-chainstate override..."
    systemctl stop bitcoind.service || true
    rm -f "${DROPIN_FILE}"
    rmdir "${DROPIN_DIR}" 2>/dev/null || true
    systemctl daemon-reload
    systemctl start bitcoind.service
    echo "bitcoind restarted normally."
    ;;

  *)
    echo "Usage: sudo $0 {start|status|finish}"
    exit 1
    ;;
esac
