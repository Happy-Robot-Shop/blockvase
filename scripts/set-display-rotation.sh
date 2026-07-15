#!/usr/bin/env bash
# Set Raspberry Pi firmware display_rotate in config.txt (applies early: firmware + Linux framebuffer/console).
# 90° counter-clockwise ⇔ hardware display_rotate=3 (270° clockwise in firmware numbering).
#
# See Raspberry Pi firmware config.txt (display_rotate / hdmi_* overrides).
#
# Usage: sudo ./scripts/set-display-rotation.sh [normal|cw90|cw180|ccw90|cw270]
# Env (used when argv omitted): BLOCKVASE_DISPLAY_ROTATION: same keywords
#
# Reboot after changes. Undo: sudo ./scripts/set-display-rotation.sh normal
#
# Backup: config.txt.bak.blockvase (before each edit)

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

ROT="${1:-${BLOCKVASE_DISPLAY_ROTATION:-normal}}"
ROT="$(echo "${ROT}" | tr '[:upper:]' '[:lower:]')"
unset VAL
case "${ROT}" in
  normal | 0 | off | none)
    VAL=""
    ;;
  cw90 | 90cw | clockwise90 | cw_90 | 90)
    VAL="1"
    ;;
  cw180 | 180cw | 180 | flip)
    VAL="2"
    ;;
  ccw90 | ccw | 90ccw | counterclockwise90 | cw270 | 270cw | 270 | left | portrait-ccw | portrait_ccw | portrait)
    VAL="3"
    ;;
  *)
    echo "Unknown rotation '${ROT}'. Use: normal cw90 cw180 ccw90 (or cw270)"
    exit 1
    ;;
esac

FW=/boot/firmware
if [[ ! -d "${FW}" ]]; then
  FW=/boot
fi

CONFIG="${FW}/config.txt"

if [[ ! -f "${CONFIG}" ]]; then
  echo "No ${CONFIG} found: skip display rotation (not Raspberry Pi firmware layout?)"
  exit 0
fi

cp -a "${CONFIG}" "${CONFIG}.bak.blockvase"

# Remove Blockvase block only: do not delete unrelated display_rotate= lines elsewhere in config.txt.
BLK_START='# <<< blockvase display rotation start >>>'
BLK_END='# <<< blockvase display rotation end >>>'
sed -i "/^${BLK_START//\//\\/}\$/,/^${BLK_END//\//\\/}\$/d" "${CONFIG}"

# Migrate older one-line banner + display_rotate emitted by Blockvase.
sed -i '/^# Added by blockvase set-display-rotation\.sh/d' "${CONFIG}"

if [[ -n "${VAL}" ]]; then
  {
    echo ""
    echo "${BLK_START}"
    echo "# Rotation: ${ROT} (display_rotate=${VAL}), see raspberrypi.com/documentation/computers/config_txt.html"
    echo "display_rotate=${VAL}"
    echo "${BLK_END}"
  } >>"${CONFIG}"
  echo "Set display_rotate=${VAL} in ${CONFIG} (requested: ${ROT}; backup ${CONFIG}.bak.blockvase)"
  echo "Reboot to apply: sudo reboot"
else
  echo "Removed Blockvase display rotation block from ${CONFIG} (backup ${CONFIG}.bak.blockvase)"
fi
