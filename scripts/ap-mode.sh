#!/usr/bin/env bash
# Blockvase setup Wi-Fi: uses the same NetworkManager stack as Raspberry Pi OS Desktop
# (the panel / nmtui / nm-connection-editor all talk to NetworkManager; nmcli is the CLI).
# Bookworm and later: NetworkManager is the default.
#
# - setup incomplete: hotspot on wlan0 (shared IPv4 → 192.168.4.1), same mechanism as
#   "Create a wireless hotspot" in the desktop UI.
# - setup complete: connect to the user's SSID with a saved profile (autoconnect), same as
#   choosing a network in the Wi-Fi menu.
# - home Wi-Fi drop: soft recovery (wifi_recovery=true) shows setup QR/hotspot but keeps
#   credentials and keeps retrying the saved network; reconnect clears recovery.
#
# Modes:
#   ensure: clone-safety then AP or client Wi-Fi from config
#           (home Wi-Fi connect failure -> soft recovery AP + QR; exit 1)
#   check-online: if setup is complete but wlan is down, retry then soft-recover;
#                 while in recovery, keep retrying saved network
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
NETWORK_GATE="${PROJECT_DIR}/scripts/bitcoind-network-gate.sh"
WIFI_LOCK="/run/blockvase-ap.lock"
# Active reconnect budget before showing setup recovery UI, then keep retrying in recovery.
RECONNECT_ATTEMPTS="${BLOCKVASE_WIFI_RECONNECT_ATTEMPTS:-4}"
RECONNECT_SLEEP_SEC="${BLOCKVASE_WIFI_RECONNECT_SLEEP_SEC:-10}"
RECOVERY_RETRY_ATTEMPTS="${BLOCKVASE_WIFI_RECOVERY_RETRY_ATTEMPTS:-3}"

apply_bitcoind_network_gate() {
  if [[ -x "${NETWORK_GATE}" ]]; then
    "${NETWORK_GATE}" apply || echo "ap-mode: warning: bitcoind network gate apply failed"
  elif [[ -f "${NETWORK_GATE}" ]]; then
    echo "ap-mode: warning: ${NETWORK_GATE} exists but is not executable"
  else
    echo "ap-mode: warning: ${NETWORK_GATE} missing"
  fi
}

if [[ "${EUID}" -ne 0 ]]; then
  echo "Must run as root"
  exit 1
fi

if ! command -v nmcli &>/dev/null; then
  echo "NetworkManager (nmcli) not found. On Raspberry Pi OS Desktop, install: sudo apt install network-manager"
  exit 1
fi

acquire_wifi_lock() {
  # $1 = "block" (default) or "skip"
  local mode="${1:-block}"
  mkdir -p "$(dirname "${WIFI_LOCK}")"
  exec 9>"${WIFI_LOCK}"
  if [[ "${mode}" == "skip" ]]; then
    if ! flock -n 9; then
      echo "ap-mode: another Wi-Fi operation in progress; skipping"
      return 1
    fi
  else
    flock 9
  fi
  return 0
}

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
    print("false")
    sys.exit(0)

cfg = load_config()
setup_complete = bool(cfg.get("setup_complete", False))
wifi_recovery = bool(cfg.get("wifi_recovery", False))
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
print("true" if wifi_recovery else "false")
PY
}

set_wifi_flags() {
  # Args: setup_complete_or_empty wifi_recovery_or_empty clear_password(0|1)
  local setup_complete_flag="${1:-}"
  local wifi_recovery_flag="${2:-}"
  local clear_password="${3:-0}"
  SETUP_COMPLETE_FLAG="${setup_complete_flag}" \
  WIFI_RECOVERY_FLAG="${wifi_recovery_flag}" \
  CLEAR_PASSWORD="${clear_password}" \
  CONFIG_JSON="${CONFIG_JSON}" python3 - <<'PY'
import json, os, secrets, sys
from pathlib import Path

path = Path(os.environ["CONFIG_JSON"])
cfg = {}
if path.exists():
    cfg = json.loads(path.read_text(encoding="utf-8"))

sc = os.environ.get("SETUP_COMPLETE_FLAG", "")
wr = os.environ.get("WIFI_RECOVERY_FLAG", "")
if sc in ("true", "false"):
    cfg["setup_complete"] = sc == "true"
if wr in ("true", "false"):
    cfg["wifi_recovery"] = wr == "true"
if os.environ.get("CLEAR_PASSWORD") == "1":
    cfg["wifi_password"] = ""
if not str(cfg.get("setup_token") or "").strip():
    cfg["setup_token"] = secrets.token_urlsafe(16)

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(
    f"setup_complete={cfg.get('setup_complete')} wifi_recovery={cfg.get('wifi_recovery')}"
)
PY
  local owner="blockvase"
  if id -u blockvase >/dev/null 2>&1; then
    owner="blockvase"
  elif [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    owner="${SUDO_USER}"
  fi
  chown "${owner}:${owner}" "$CONFIG_JSON" 2>/dev/null || true
  chmod 600 "$CONFIG_JSON" 2>/dev/null || true
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

client_profile_exists() {
  nmcli -t -f NAME connection show 2>/dev/null | grep -qx "${CLIENT_CONN}"
}

# Create/update profile with PSK stored in the system connection file (psk-flags=0).
# Avoids the empty-secret failure seen after `nmcli device wifi connect` on headless boots.
ensure_client_profile() {
  local ssid="$1"
  local password="$2"
  if [[ -z "${ssid}" ]]; then
    return 1
  fi

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
PY
}

activate_client() {
  nmcli device wifi rescan ifname "${WLAN_IFACE}" 2>/dev/null || true
  nmcli connection up "${CLIENT_CONN}" ifname "${WLAN_IFACE}"
}

connect_to_wifi() {
  local ssid="$1"
  local password="$2"
  local attempts="${3:-1}"
  local i
  if [[ -z "${ssid}" ]]; then
    return 1
  fi

  nmcli connection delete "${HOTSPOT_CONN}" 2>/dev/null || true
  nm_ensure_ready
  ensure_client_profile "${ssid}" "${password}" || return 1

  for ((i = 1; i <= attempts; i++)); do
    echo "ap-mode: connecting to '${ssid}' (attempt ${i}/${attempts})"
    if activate_client 2>/dev/null; then
      sleep 2
      if wlan_is_connected; then
        return 0
      fi
    fi
    sleep 3
  done
  return 1
}

try_reconnect_saved() {
  local ssid="$1"
  local password="$2"
  local attempts="${3:-${RECONNECT_ATTEMPTS}}"
  local i

  if [[ -z "${ssid}" ]]; then
    return 1
  fi

  nm_ensure_ready
  stop_hotspot 2>/dev/null || true

  if ! client_profile_exists; then
    echo "ap-mode: recreating saved client profile for '${ssid}'"
    ensure_client_profile "${ssid}" "${password}" || return 1
  fi

  for ((i = 1; i <= attempts; i++)); do
    echo "ap-mode: reconnect attempt ${i}/${attempts} to '${ssid}'"
    if activate_client 2>/dev/null; then
      sleep 2
      if wlan_is_connected; then
        echo "ap-mode: reconnected to '${ssid}'"
        return 0
      fi
    fi
    # Keep trying even if nmcli up fails (AP temporarily gone).
    sleep "${RECONNECT_SLEEP_SEC}"
  done
  return 1
}

run_clone_safety_if_present() {
  if [[ -x "${CLONE_SAFETY}" ]]; then
    PROJECT_DIR="${PROJECT_DIR}" CONFIG_JSON="${CONFIG_JSON}" \
      CLIENT_CONN="${CLIENT_CONN}" HOTSPOT_CONN="${HOTSPOT_CONN}" \
      "${CLONE_SAFETY}" run || true
  fi
}

wlan_is_connected() {
  # True only when the home client profile is active (not the setup hotspot).
  local state
  state="$(nmcli -t -f NAME,DEVICE,STATE connection show --active 2>/dev/null \
    | awk -F: -v n="${CLIENT_CONN}" -v d="${WLAN_IFACE}" '$1==n && ($2==d || $2==""){print $3; exit}')"
  [[ "${state}" == "activated" ]]
}

hotspot_is_up() {
  local state
  state="$(nmcli -t -f NAME,DEVICE,STATE connection show --active 2>/dev/null | awk -F: -v n="${HOTSPOT_CONN}" '$1==n{print $3; exit}')"
  [[ "${state}" == "activated" ]]
}

# Soft recovery: show setup QR/hotspot, keep setup_complete + password, keep client profile.
enter_wifi_recovery() {
  local ap_ssid="$1"
  local ap_password="$2"
  local wifi_ssid="${3:-}"

  echo "ap-mode: Wi-Fi soft recovery: enabling setup AP/QR while keeping saved credentials"
  if [[ -z "${wifi_ssid}" ]]; then
    # No saved network — fall back to first-boot style incomplete setup.
    set_wifi_flags "false" "false" 0
  else
    # Keep setup_complete=true so a reconnect can leave recovery without re-onboarding.
    set_wifi_flags "true" "true" 0
  fi

  # Keep CLIENT_CONN for later reconnect; only bring it down for the hotspot.
  nmcli connection down "${CLIENT_CONN}" 2>/dev/null || true
  mapfile -t cfg < <(read_cfg)
  stop_hotspot
  sleep 1
  start_hotspot "${ap_ssid:-${cfg[1]}}" "${ap_password:-${cfg[2]}}"
  apply_bitcoind_network_gate
}

exit_wifi_recovery() {
  echo "ap-mode: leaving Wi-Fi recovery (home network is up)"
  set_wifi_flags "" "false" 0
  stop_hotspot 2>/dev/null || true
  apply_bitcoind_network_gate
}

# Used by NetworkManager dispatcher / wifi-watch timer.
check_online() {
  if ! acquire_wifi_lock skip; then
    return 0
  fi

  mapfile -t cfg < <(read_cfg)
  local setup_complete="${cfg[0]}"
  local ap_ssid="${cfg[1]}"
  local ap_password="${cfg[2]}"
  local wifi_ssid="${cfg[4]}"
  local wifi_password="${cfg[5]}"
  local wifi_recovery="${cfg[6]}"

  # First-boot / incomplete setup: nothing to reconnect.
  if [[ "${setup_complete}" != "true" && "${wifi_recovery}" != "true" ]]; then
    return 0
  fi

  if wlan_is_connected && ! hotspot_is_up; then
    if [[ "${wifi_recovery}" == "true" ]]; then
      exit_wifi_recovery
    fi
    return 0
  fi

  # Soft recovery already showing setup: keep retrying the saved network.
  if [[ "${wifi_recovery}" == "true" ]]; then
    if [[ -z "${wifi_ssid}" ]]; then
      return 0
    fi
    echo "ap-mode: in wifi_recovery; retrying saved network '${wifi_ssid}'"
    if try_reconnect_saved "${wifi_ssid}" "${wifi_password}" "${RECOVERY_RETRY_ATTEMPTS}"; then
      exit_wifi_recovery
      return 0
    fi
    # Restore hotspot for phone setup if reconnect failed.
    if ! hotspot_is_up; then
      echo "ap-mode: reconnect failed; restoring setup hotspot"
      start_hotspot "${ap_ssid}" "${ap_password}"
    fi
    return 0
  fi

  # setup_complete but link down: actively reconnect before showing setup UI.
  if [[ -z "${wifi_ssid}" ]]; then
    echo "ap-mode: setup_complete but no wifi_ssid; entering recovery"
    enter_wifi_recovery "${ap_ssid}" "${ap_password}" ""
    return 0
  fi

  echo "ap-mode: ${WLAN_IFACE} down while setup_complete; actively reconnecting to '${wifi_ssid}'..."
  if try_reconnect_saved "${wifi_ssid}" "${wifi_password}" "${RECONNECT_ATTEMPTS}"; then
    return 0
  fi

  echo "ap-mode: reconnect budget exhausted; entering soft recovery (setup QR) while keeping credentials"
  enter_wifi_recovery "${ap_ssid}" "${ap_password}" "${wifi_ssid}"
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
      apply_bitcoind_network_gate
      ;;
    install-wifi-watchers)
      install_wifi_watchers
      ;;
    ensure)
      acquire_wifi_lock block || true
      run_clone_safety_if_present
      mapfile -t cfg < <(read_cfg)
      local setup_complete="${cfg[0]}"
      local ap_ssid="${cfg[1]}"
      local ap_password="${cfg[2]}"
      local wifi_ssid="${cfg[4]}"
      local wifi_password="${cfg[5]}"
      local wifi_recovery="${cfg[6]}"
      local ensure_rc=0

      if [[ "${setup_complete}" == "false" && "${wifi_recovery}" != "true" ]]; then
        stop_hotspot
        sleep 2
        start_hotspot "${ap_ssid}" "${ap_password}"
      else
        stop_hotspot
        sleep 2
        nm_ensure_ready
        if [[ -z "${wifi_ssid}" ]]; then
          echo "ap-mode: no wifi_ssid; entering recovery"
          enter_wifi_recovery "${ap_ssid}" "${ap_password}" ""
          ensure_rc=1
        elif ! connect_to_wifi "${wifi_ssid}" "${wifi_password}" 5; then
          echo "ap-mode: home Wi-Fi connect failed after retries; entering soft recovery"
          enter_wifi_recovery "${ap_ssid}" "${ap_password}" "${wifi_ssid}"
          ensure_rc=1
        else
          sleep 2
          if ! wlan_is_connected; then
            echo "ap-mode: home Wi-Fi not associated after connect; entering soft recovery"
            enter_wifi_recovery "${ap_ssid}" "${ap_password}" "${wifi_ssid}"
            ensure_rc=1
          else
            # Successful client join — clear any prior soft recovery.
            set_wifi_flags "true" "false" 0
          fi
        fi
      fi
      apply_bitcoind_network_gate
      exit "${ensure_rc}"
      ;;
    check-online)
      check_online
      ;;
    after-factory-reset)
      after_factory_reset
      apply_bitcoind_network_gate
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
