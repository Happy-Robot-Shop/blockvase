#!/usr/bin/env bash
set -euo pipefail

URL="${BLOCKVASE_KIOSK_URL:-http://127.0.0.1/display}"
MODE="${BLOCKVASE_DISPLAY_MODE:-auto}"
LOG_FILE="${HOME}/logs/kiosk-browser.log"
# Cap unbounded Chromium appends (default 5 MiB; override with BLOCKVASE_KIOSK_LOG_MAX_BYTES).
LOG_MAX_BYTES="${BLOCKVASE_KIOSK_LOG_MAX_BYTES:-5242880}"
PROFILE_DIR="${XDG_RUNTIME_DIR:-/tmp}/blockvase-kiosk-chromium"

rotate_kiosk_log() {
  local size
  mkdir -p "$(dirname "${LOG_FILE}")"
  [[ -f "${LOG_FILE}" ]] || return 0
  size="$(wc -c <"${LOG_FILE}" 2>/dev/null | tr -d '[:space:]' || echo 0)"
  [[ "${size}" =~ ^[0-9]+$ ]] || return 0
  if (( size > LOG_MAX_BYTES )); then
    mv -f "${LOG_FILE}" "${LOG_FILE}.1" 2>/dev/null || true
    : >"${LOG_FILE}"
  fi
}

# Rotation under X11/KMS: reliable on Pi Bookworm/Trixie where firmware display_rotate often has no effect.
# BLOCKVASE_KIOSK_XRANDR_ROTATE=left|right|inverted|none (left = 90° counter-clockwise)
# Fallback: BLOCKVASE_DISPLAY_ROTATION (same keywords as bootstrap) if kiosk var unset.

# Find chromium (Raspberry Pi uses /usr/bin, Ubuntu may use /snap/bin)
CHROMIUM="${BLOCKVASE_CHROMIUM:-}"
if [[ -z "${CHROMIUM}" ]]; then
  for p in /usr/bin/chromium /usr/bin/chromium-browser /snap/bin/chromium; do
    if [[ -x "${p}" ]]; then CHROMIUM="${p}"; break; fi
  done
fi
if [[ -z "${CHROMIUM}" ]]; then
  rotate_kiosk_log
  echo "chromium not found. Install: sudo apt install chromium  # or chromium-browser on older OS" >>"${LOG_FILE}" 2>&1
  exec sleep infinity
fi

apply_kiosk_display_xrandr() {
  command -v xrandr >/dev/null 2>&1 || return 0

  ROT_EFFECTIVE="${BLOCKVASE_KIOSK_XRANDR_ROTATE:-}"
  ROT_EFFECTIVE="$(echo "${ROT_EFFECTIVE}" | tr '[:upper:]' '[:lower:]')"
  if [[ -z "${ROT_EFFECTIVE}" && -n "${BLOCKVASE_DISPLAY_ROTATION:-}" ]]; then
    local disp
    disp="$(echo "${BLOCKVASE_DISPLAY_ROTATION}" | tr '[:upper:]' '[:lower:]')"
    case "${disp}" in
      ccw90 | cw270 | left | portrait | portrait_ccw | portrait-ccw)
        ROT_EFFECTIVE=left ;;
      cw90 | cw_90 | 90cw | clockwise90 | 90 | right)
        ROT_EFFECTIVE=right ;;
      cw180 | cw_180 | 180 | flip | inverted-alt)
        ROT_EFFECTIVE=inverted ;;
    esac
  fi

  local XR_ROT=""
  case "${ROT_EFFECTIVE}" in
    "" | none | off | normal | 0 | false)
      XR_ROT="" ;;
    left | ccw90)
      XR_ROT=left ;;
    right | cw90)
      XR_ROT=right ;;
    inverted | cw180 | 180)
      XR_ROT=inverted ;;
    *)
      echo "WARNING: unknown rotation '${ROT_EFFECTIVE}', use left|right|inverted|none (BLOCKVASE_KIOSK_XRANDR_ROTATE)" >&2
      XR_ROT=""
      ;;
  esac

  local OUTS=()
  local line
  while IFS= read -r line; do
    if [[ "${line}" =~ ^([A-Za-z][A-Za-z0-9._-]*)[[:space:]]+connected ]]; then
      OUTS+=("${BASH_REMATCH[1]}")
    fi
  done < <(xrandr --query 2>/dev/null || true)

  if [[ "${#OUTS[@]}" -eq 0 ]]; then
    local cand
    for cand in HDMI-1 HDMI-2 DSI-2 HDMI-A-1 HDMI-A-2; do
      if xrandr --query 2>/dev/null | grep -qE "^${cand//./\\.}[[:space:]]+connected"; then
        OUTS+=("${cand}")
      fi
    done
  fi

  if [[ "${#OUTS[@]}" -eq 0 ]]; then
    echo "WARNING: xrandr: no connected output found (display mode / rotation skipped)" >&2
    return 0
  fi

  local output
  for output in "${OUTS[@]}"; do
    if [[ "${MODE}" == "auto" ]]; then
      xrandr --output "${output}" --auto >/dev/null 2>&1 || true
    else
      xrandr --output "${output}" --mode "${MODE}" >/dev/null 2>&1 || true
    fi
    if [[ -n "${XR_ROT}" ]]; then
      xrandr --output "${output}" --rotate "${XR_ROT}" >/dev/null 2>&1 || \
        echo "WARNING: xrandr failed: --output ${output} --rotate ${XR_ROT}" >&2
    fi
  done
}

unclutter -idle 1 -root &
# Do not use openbox-session: it runs openbox-autostart → XDG autostart (polkit-mate,
# lxpolkit, …). Those agents call logind for "the session for this pid"; startx from
# systemd has no graphical logind session, so they log "No session for pid" to the journal.
# Kiosk does not need interactive polkit prompts; use plain openbox + session D-Bus only.
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]] && command -v dbus-launch >/dev/null 2>&1; then
  eval "$(dbus-launch --sh-syntax)"
  export DBUS_SESSION_BUS_ADDRESS
fi
export GTK_USE_PORTAL=0
export XDG_CURRENT_DESKTOP=Openbox
openbox --startup /bin/true &
sleep 2

if command -v xsetroot >/dev/null 2>&1; then
  xsetroot -solid black 2>/dev/null || true
fi

# Disable display sleep / screen blanking.
if command -v xset >/dev/null 2>&1; then
  xset s off -dpms s noblank 2>/dev/null || true
fi

# Configure outputs + optional rotation under X/KMS (firmware display_rotate is often ineffective here).
apply_kiosk_display_xrandr

rotate_kiosk_log

# Wait for the actual kiosk URL before first launch. Chromium can otherwise
# come up as a featureless gray window if it races X/DBus/Flask during boot.
for i in $(seq 1 45); do
  if curl -fsS -o /dev/null --connect-timeout 1 --max-time 2 "${URL}" 2>/dev/null; then
    break
  fi
  sleep 2
done

# Keep X alive and relaunch browser if it exits.
# Do not use the OS password store (GNOME Keyring / libsecret): without a normal graphical
# logind session, dbus can still activate gnome-keyring and show "choose password for keyring".
while true; do
  rotate_kiosk_log
  rm -rf "${PROFILE_DIR}"
  mkdir -p "${PROFILE_DIR}"
  echo "$(date -Is) launching chromium kiosk: ${URL}" >>"${LOG_FILE}"
  "${CHROMIUM}" \
    --kiosk \
    --user-data-dir="${PROFILE_DIR}" \
    --no-first-run \
    --no-default-browser-check \
    --disable-session-crashed-bubble \
    --disable-background-networking \
    --disable-sync \
    --disable-component-update \
    --disable-extensions \
    --disable-features=UsePortal,Translate,MediaRouter,OptimizationHints,AutofillServerCommunication \
    --password-store=basic \
    --noerrdialogs \
    --disable-infobars \
    --disable-gpu \
    --use-gl=swiftshader \
    --disable-accelerated-2d-canvas \
    --disable-gpu-compositing \
    --force-device-scale-factor=1 \
    --default-background-color=000000 \
    --overscroll-history-navigation=0 \
    --check-for-update-interval=31536000 \
    "${URL}" >>"${LOG_FILE}" 2>&1 || true
  sleep 1
done

