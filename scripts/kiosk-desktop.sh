#!/usr/bin/env bash
# Kiosk inside Raspberry Pi OS Desktop (Wayland/labwc or X11).
# Use this when the graphical session already uses the HDMI output: do not use startx.
set -euo pipefail

URL="${BLOCKVASE_KIOSK_URL:-http://127.0.0.1/display}"
LOG_FILE="${HOME}/logs/kiosk-desktop.log"
# Cap unbounded Chromium appends (default 5 MiB; override with BLOCKVASE_KIOSK_LOG_MAX_BYTES).
LOG_MAX_BYTES="${BLOCKVASE_KIOSK_LOG_MAX_BYTES:-5242880}"
mkdir -p "$(dirname "${LOG_FILE}")"
mkdir -p "${HOME}/.cache"

rotate_kiosk_log() {
  local size
  [[ -f "${LOG_FILE}" ]] || return 0
  size="$(wc -c <"${LOG_FILE}" 2>/dev/null | tr -d '[:space:]' || echo 0)"
  [[ "${size}" =~ ^[0-9]+$ ]] || return 0
  if (( size > LOG_MAX_BYTES )); then
    mv -f "${LOG_FILE}" "${LOG_FILE}.1" 2>/dev/null || true
    : >"${LOG_FILE}"
  fi
}

rotate_kiosk_log

# Single instance: XDG autostart + labwc autostart may both fire on some setups.
exec 9>"${HOME}/.cache/blockvase-kiosk.lock"
if ! flock -n 9; then
  echo "$(date -Iseconds) kiosk-desktop: another instance is running, exiting" >>"${LOG_FILE}"
  exit 0
fi

CHROMIUM="${BLOCKVASE_CHROMIUM:-}"
if [[ -z "${CHROMIUM}" ]]; then
  for p in /usr/bin/chromium /usr/bin/chromium-browser; do
    if [[ -x "${p}" ]]; then CHROMIUM="${p}"; break; fi
  done
fi
if [[ -z "${CHROMIUM}" ]]; then
  echo "chromium not found" >>"${LOG_FILE}"
  exit 1
fi

# Reduce OS dialogs/notifications stacking over the kiosk (keyring, NM toasts, etc.)
suppress_os_over_kiosk() {
  # NetworkManager / GNOME notification settings (schemas may be absent on minimal images)
  if command -v gsettings >/dev/null; then
    gsettings set org.gnome.nm-applet disable-connected-notifications true 2>/dev/null || true
    gsettings set org.gnome.nm-applet disable-disconnected-notifications true 2>/dev/null || true
    gsettings set org.gnome.desktop.notifications show-banners false 2>/dev/null || true
  fi
  # mako (Wayland): enable do-not-disturb-style mode if the daemon is present
  if command -v makoctl >/dev/null; then
    makoctl mode -a dnd 2>/dev/null || makoctl mode -s dnd 2>/dev/null || true
    makoctl reload 2>/dev/null || true
    makoctl dismiss -a 2>/dev/null || true
  fi
}

suppress_os_over_kiosk

# Chromium Ozone: on Pi OS Desktop + labwc, both WAYLAND_DISPLAY and DISPLAY (XWayland) are usually set.
# Native Wayland Chromium ignores unclutter (X11-only), so the mouse stays visible. Prefer X11 via XWayland
# when DISPLAY exists so we can hide the cursor with unclutter (same as kiosk-session.sh on Lite).
# Set BLOCKVASE_KIOSK_WAYLAND=1 to force native Wayland if you need it (cursor hide may not apply).
CHROMIUM_EXTRA=()
if [[ "${BLOCKVASE_KIOSK_WAYLAND:-0}" == "1" ]] && [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
  CHROMIUM_EXTRA+=(--ozone-platform=wayland --enable-features=UseOzonePlatform)
elif [[ -n "${WAYLAND_DISPLAY:-}" && -n "${DISPLAY:-}" ]]; then
  CHROMIUM_EXTRA+=(--ozone-platform=x11)
elif [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
  CHROMIUM_EXTRA+=(--ozone-platform=wayland --enable-features=UseOzonePlatform)
fi
# Avoid libsecret / gnome-keyring "save password" prompts from Chromium itself
CHROMIUM_EXTRA+=(--password-store=basic)
CHROMIUM_EXTRA+=(--disable-features=PasswordManager,PasswordManagerOnboarding)

# Hide mouse pointer over the kiosk (requires X11 / XWayland + DISPLAY)
if command -v unclutter >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
  unclutter -idle 1 -root >>"${LOG_FILE}" 2>&1 &
fi

{
  echo "$(date -Iseconds) kiosk-desktop starting url=${URL} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-} DISPLAY=${DISPLAY:-} chromium_extra=${CHROMIUM_EXTRA[*]}"
} >>"${LOG_FILE}"

# Wait for Flask (same as kiosk-session; AP mode + slow SD can delay bind to :80)
for _ in $(seq 1 90); do
  if curl -sf -o /dev/null --connect-timeout 1 http://127.0.0.1/ 2>/dev/null; then
    break
  fi
  sleep 2
done

# Session may finish starting while we waited; re-apply (mako/NM toasts can appear late)
suppress_os_over_kiosk

# Relaunch if the browser exits (crash / OOM)
while true; do
  rotate_kiosk_log
  "${CHROMIUM}" \
    "${CHROMIUM_EXTRA[@]}" \
    --kiosk \
    --incognito \
    --noerrdialogs \
    --disable-infobars \
    --disable-gpu \
    --force-device-scale-factor=1 \
    --overscroll-history-navigation=0 \
    --check-for-update-interval=31536000 \
    "${URL}" >>"${LOG_FILE}" 2>&1 || true
  sleep 2
done
