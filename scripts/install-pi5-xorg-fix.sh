#!/usr/bin/env bash
# Install X.Org config for Raspberry Pi 5 so startx/blockvase-kiosk works.
# Fixes: "Cannot run in framebuffer mode. Please specify busIDs"
# Run: sudo ./scripts/install-pi5-xorg-fix.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC="${PROJECT_DIR}/xorg-conf/99-vc4.conf"
DEST="/etc/X11/xorg.conf.d/99-vc4.conf"

[[ -f "$SRC" ]] || { echo "Config not found: $SRC"; exit 1; }
mkdir -p "$(dirname "$DEST")"
cp "$SRC" "$DEST"
echo "Installed $DEST. Restart blockvase-kiosk: sudo systemctl restart blockvase-kiosk.service"
