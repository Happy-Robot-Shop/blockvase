#!/usr/bin/env bash
# Blockvase setup Wi-Fi: uses the same NetworkManager stack as Raspberry Pi OS Desktop
# (the panel / nmtui / nm-connection-editor all talk to NetworkManager; nmcli is the CLI).
# Bookworm and later: NetworkManager is the default.
#
# - setup incomplete: hotspot on wlan0 (shared IPv4 → 192.168.4.1), same mechanism as
#   "Create a wireless hotspot" in the desktop UI.
# - setup complete: connect to the user's SSID with a saved profile (autoconnect), same as
#   choosing a network in the Wi-Fi menu.
#
# Modes:
#   ensure: clone-safety then AP or client Wi-Fi from config
#           (home Wi-Fi connect failure -> setup AP + QR recovery)
#   check-online: if setup is complete but wlan is down, enter Wi-Fi recovery
#   start|stop|status
#   prepare-clone: reset setup for imaging (keeps blockchain); then start AP
#   after-factory-reset: clear mining runtime + stale Wi-Fi client profiles

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_JSON="${PROJECT_DIR}/data/config.json"
AP_GATEWAY="192.168.4.1"
AP_CIDR="${AP_GATEWAY}/24"
WLAN_IFACE="${BLOCKVASE_WLAN_IFACE:-wlan0}"
HOTSPOT_CONN="blockvase-hotspot"
CLIENT_CONN="blockvase-home"
CLONE_SAFETY="${PROJECT_DIR}/scripts/clone-safety.sh"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Must run as root"
  exit 1
fi

if ! command -v nmcli &>/dev/null; then
  echo "NetworkManager (nmcli) not found. On Raspberry Pi OS Desktop, install: sudo apt install network-manager"
  exit 1
fi

read_cfg() {
  # Use app.config so AP SSID matches Flask (/api/ap-info) and setup_token is created
  # before first use (blockvase-ap.service runs before blockvase.service).
  PROJECT_DIR="${PROJECT_DIR}" CONFIG_JSON="${CONFIG_JSON}" python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["PROJECT_DIR"])
from app.config import ap_broadcast_ssid, load_config

path = Path(os.environ["CONFIG_JSON"])
if not path.exists():
    from app.config import hardware_ap_suffix

    ap_ssid = f"blockvase-{hardware_ap_suffix()}"
    print("false")
    print(ap_ssid)
    print("blockvase1234")
    print("blockvase")
    print("")
    print("")
    sys.exit(0)

cfg = load_config()
setup_complete = bool(cfg.get("setup_complete", False))
ap_ssid = ap_broadcast_ssid(cfg)
ap_password = "blockvase1234"
ssid = str(cfg.get("wifi_ssid", ""))
password = str(cfg.get("wifi_password", ""))
name = str(cfg.get("device_name", "blockvase"))
name = "".join(c if c.isalnum() or c == "-" else "-" for c in name.lower())
name = "-".join(filter(None, name.split("-"))) or "blockvase"
device_name = name[:63]

print("true" if setup_complete else "false")
print(ap_ssid)
print(ap_password)
print(device_name)
print(ssid)
print(password)
PY
}

nm_ensure_ready() {
  nmcli networking on 2>/dev/null || true
  nmcli radio wifi on 2>/dev/null || true
}

ensure_hotspot_connection() {
  local ap_ssid="$1"
  local ap_password="$2"

  if nmcli -t -f NAME connection show 2>/dev/null | grep -qx "${HOTSPOT_CONN}"; then
    nmcli connection modify "${HOTSPOT_CONN}" \
      wifi.ssid "${ap_ssid}" \
      802-11-wireless.mode ap \
      wifi-sec.key-mgmt wpa-psk \
      wifi-sec.psk "${ap_password}" \
      wifi-sec.psk-flags 0 \
      ipv4.addresses "${AP_CIDR}" \
      ipv4.method shared
  else
    nmcli connection add type wifi ifname "${WLAN_IFACE}" con-name "${HOTSPOT_CONN}" \
      autoconnect no ssid "${ap_ssid}"
    nmcli connection modify "${HOTSPOT_CONN}" \
      802-11-wireless.mode ap \
      802-11-wireless.band bg \
      wifi-sec.key-mgmt wpa-psk \
      wifi-sec.psk "${ap_password}" \
      wifi-sec.psk-flags 0 \
      ipv4.method shared \
      ipv4.addresses "${AP_CIDR}"
  fi
}

start_hotspot() {
  local ap_ssid="$1"
  local ap_password="$2"

  for ((i = 0; i < 30; i++)); do
    if nmcli -t -f DEVICE device status 2>/dev/null | grep -qx "${WLAN_IFACE}"; then
      break
    fi
    sleep 1
  done
  if ! nmcli -t -f DEVICE device status 2>/dev/null | grep -qx "${WLAN_IFACE}"; then
    echo "ERROR: ${WLAN_IFACE} not found"
    exit 1
  fi

  rfkill unblock wlan 2>/dev/null || true
  nm_ensure_ready
  # Avoid racing a stale client profile while bringing the hotspot up.
  nmcli connection down "${CLIENT_CONN}" 2>/dev/null || true
  ensure_hotspot_connection "${ap_ssid}" "${ap_password}"
  for i in 1 2 3 4 5; do
    if nmcli connection up "${HOTSPOT_CONN}" ifname "${WLAN_IFACE}" 2>/dev/null; then
      break
    fi
    sleep 2
  done
}

stop_hotspot() {
  nmcli connection down "${HOTSPOT_CONN}" 2>/dev/null || true
}

# Create/update profile with PSK stored in the system connection file (psk-flags=0).
# Avoids the empty-secret failure seen after `nmcli device wifi connect` on headless boots.
connect_to_wifi() {
  local ssid="$1"
  local password="$2"
  if [[ -z "${ssid}" ]]; then
    return 0
  fi

  nmcli connection delete "${HOTSPOT_CONN}" 2>/dev/null || true

  python3 - "${ssid}" "${password}" "${WLAN_IFACE}" "${CLIENT_CONN}" <<'PY'
import subprocess
import sys

ssid, password, iface, cname = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

subprocess.run(["nmcli", "connection", "delete", cname], capture_output=True)

add_cmd = [
    "nmcli",
    "connection",
    "add",
    "type",
    "wifi",
    "ifname",
    iface,
    "con-name",
    cname,
    "ssid",
    ssid,
    "autoconnect",
    "yes",
]
r = subprocess.run(add_cmd, capture_output=True, text=True)
if r.returncode != 0:
    sys.stderr.write(r.stderr or r.stdout or "nmcli connection add failed\n")
    sys.exit(r.returncode or 1)

mod = [
    "nmcli",
    "connection",
    "modify",
    cname,
    "connection.autoconnect",
    "yes",
    "connection.interface-name",
    iface,
    "802-11-wireless.mode",
    "infrastructure",
    "802-11-wireless.ssid",
    ssid,
    "wifi-sec.key-mgmt",
    "wpa-psk",
    "wifi-sec.psk-flags",
    "0",
]
if password:
    mod.extend(["wifi-sec.psk", password])
r = subprocess.run(mod, capture_output=True, text=True)
if r.returncode != 0:
    sys.stderr.write(r.stderr or r.stdout or "nmcli connection modify failed\n")
    sys.exit(r.returncode or 1)

# Scan briefly so WPA1/WPA2 APs are visible, then activate the saved profile.
subprocess.run(["nmcli", "device", "wifi", "rescan", "ifname", iface], capture_output=True)
r = subprocess.run(
    ["nmcli", "connection", "up", cname, "ifname", iface],
    capture_output=True,
    text=True,
)
if r.stderr:
    print(r.stderr, file=sys.stderr, end="")
if r.stdout:
    print(r.stdout, end="")
if r.returncode != 0:
    sys.exit(r.returncode)
PY
}

run_clone_safety_if_present() {
  if [[ -x "${CLONE_SAFETY}" ]]; then
    PROJECT_DIR="${PROJECT_DIR}" CONFIG_JSON="${CONFIG_JSON}" \
      CLIENT_CONN="${CLIENT_CONN}" HOTSPOT_CONN="${HOTSPOT_CONN}" \
      "${CLONE_SAFETY}" run || true
  fi
}

wlan_is_connected() {
  local state
  state="$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: -v d="${WLAN_IFACE}" '$1==d{print $2; exit}')"
  [[ "${state}" == "connected" ]]
}

# Drop back into setup AP mode so the kiosk shows the QR and the user can fix Wi-Fi.
enter_wifi_recovery() {
  echo "ap-mode: Wi-Fi recovery: enabling setup AP and setup QR"
  python3 - "$CONFIG_JSON" <<'PY'
import json, secrets, sys
from pathlib import Path
path = Path(sys.argv[1])
cfg = {}
if path.exists():
    cfg = json.loads(path.read_text(encoding="utf-8"))
cfg["setup_complete"] = False
# Keep wifi_ssid so the setup form can prefill; clear password so a bad PSK is re-entered.
cfg["wifi_password"] = ""
if not str(cfg.get("setup_token") or "").strip():
    cfg["setup_token"] = secrets.token_urlsafe(16)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print("setup_complete=false (wifi recovery)")
PY
  local owner="blockvase"
  if id -u blockvase >/dev/null 2>&1; then
    owner="blockvase"
  elif [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    owner="${SUDO_USER}"
  fi
  chown "${owner}:${owner}" "$CONFIG_JSON" 2>/dev/null || true
  chmod 600 "$CONFIG_JSON" 2>/dev/null || true

  nmcli connection delete "${CLIENT_CONN}" 2>/dev/null || true
  mapfile -t cfg < <(read_cfg)
  stop_hotspot
  sleep 1
  start_hotspot "${cfg[1]}" "${cfg[2]}"
}

# Used by NetworkManager dispatcher / wifi-watch timer.
check_online() {
  mapfile -t cfg < <(read_cfg)
  local setup_complete="${cfg[0]}"
  [[ "${setup_complete}" == "true" ]] || return 0

  if wlan_is_connected; then
    return 0
  fi

  echo "ap-mode: ${WLAN_IFACE} down while setup_complete; waiting 20s for reconnect..."
  sleep 20
  if wlan_is_connected; then
    echo "ap-mode: ${WLAN_IFACE} reconnected"
    return 0
  fi

  enter_wifi_recovery
}

after_factory_reset() {
  echo "ap-mode: post factory-reset cleanup"
  if [[ -x "${CLONE_SAFETY}" ]]; then
    PROJECT_DIR="${PROJECT_DIR}" CONFIG_JSON="${CONFIG_JSON}" \
      "${CLONE_SAFETY}" clear-mining || true
  fi
  nmcli connection delete "${CLIENT_CONN}" 2>/dev/null || true
  nmcli connection delete "${HOTSPOT_CONN}" 2>/dev/null || true
}

# Install Wi-Fi recovery timer + NetworkManager dispatcher (also done by bootstrap).
install_wifi_watchers() {
  local unit_src="${PROJECT_DIR}/systemd/blockvase-wifi-watch.service"
  local timer_src="${PROJECT_DIR}/systemd/blockvase-wifi-watch.timer"
  local disp_src="${PROJECT_DIR}/scripts/nm-dispatcher-wifi-recovery.sh"
  local tmp

  if [[ -f "${unit_src}" && -f "${timer_src}" ]]; then
    tmp="$(mktemp)"
    sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g" "${unit_src}" >"${tmp}"
    cp "${tmp}" /etc/systemd/system/blockvase-wifi-watch.service
    cp "${timer_src}" /etc/systemd/system/blockvase-wifi-watch.timer
    rm -f "${tmp}"
    systemctl daemon-reload
    systemctl enable --now blockvase-wifi-watch.timer
    echo "ap-mode: enabled blockvase-wifi-watch.timer"
  fi

  if [[ -f "${disp_src}" ]]; then
    tmp="$(mktemp)"
    sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g" "${disp_src}" >"${tmp}"
    mkdir -p /etc/NetworkManager/dispatcher.d
    cp "${tmp}" /etc/NetworkManager/dispatcher.d/99-blockvase-wifi
    chmod 755 /etc/NetworkManager/dispatcher.d/99-blockvase-wifi
    rm -f "${tmp}"
    echo "ap-mode: installed NetworkManager dispatcher 99-blockvase-wifi"
  fi
}

main() {
  local mode="${1:-ensure}"

  case "${mode}" in
    prepare-clone)
      if [[ -x "${CLONE_SAFETY}" ]]; then
        PROJECT_DIR="${PROJECT_DIR}" CONFIG_JSON="${CONFIG_JSON}" \
          CLIENT_CONN="${CLIENT_CONN}" HOTSPOT_CONN="${HOTSPOT_CONN}" \
          "${CLONE_SAFETY}" prepare-clone
      else
        echo "ERROR: ${CLONE_SAFETY} missing"
        exit 1
      fi
      # Bring up setup AP so this master is ready to verify before imaging.
      mapfile -t cfg < <(read_cfg)
      stop_hotspot
      sleep 1
      start_hotspot "${cfg[1]}" "${cfg[2]}"
      install_wifi_watchers
      ;;
    install-wifi-watchers)
      install_wifi_watchers
      ;;
    ensure)
      run_clone_safety_if_present
      mapfile -t cfg < <(read_cfg)
      local setup_complete="${cfg[0]}"
      local ap_ssid="${cfg[1]}"
      local ap_password="${cfg[2]}"
      local wifi_ssid="${cfg[4]}"
      local wifi_password="${cfg[5]}"
      if [[ "${setup_complete}" == "false" ]]; then
        stop_hotspot
        sleep 2
        start_hotspot "${ap_ssid}" "${ap_password}"
      else
        stop_hotspot
        sleep 2
        nm_ensure_ready
        if [[ -z "${wifi_ssid}" ]]; then
          echo "ap-mode: setup_complete but no wifi_ssid; entering recovery"
          enter_wifi_recovery
        elif ! connect_to_wifi "${wifi_ssid}" "${wifi_password}"; then
          echo "ap-mode: home Wi-Fi connect failed; entering recovery"
          enter_wifi_recovery
        else
          sleep 3
          if ! wlan_is_connected; then
            echo "ap-mode: home Wi-Fi not associated after connect; entering recovery"
            enter_wifi_recovery
          fi
        fi
      fi
      ;;
    check-online)
      check_online
      ;;
    after-factory-reset)
      after_factory_reset
      ;;
    start)
      mapfile -t cfg < <(read_cfg)
      start_hotspot "${cfg[1]}" "${cfg[2]}"
      ;;
    stop)
      stop_hotspot
      ;;
    status)
      nmcli connection show
      nmcli device status
      ;;
    *)
      echo "Usage: $0 {ensure|check-online|start|stop|status|prepare-clone|after-factory-reset|install-wifi-watchers}"
      exit 2
      ;;
  esac
}

main "$@"
