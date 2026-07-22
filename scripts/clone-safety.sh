#!/usr/bin/env bash
# Clone safety for Blockvase devices. Run as root.
#
# On each boot (via ap-mode ensure):
#   - Compare /var/lib/blockvase/device-identity.env to current hardware/root-disk fingerprint
#   - If fingerprint changed (cloned image): refresh machine-id/SSH/hostname/leases,
#     clear stale Wi-Fi client profiles, expand root to fill the new drive
#   - Always record the current fingerprint afterward
#
# Does NOT touch /var/lib/bitcoind, admin credentials, or Wi-Fi settings in config.json
# except regenerating setup_token while setup_complete is false.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CONFIG_JSON="${CONFIG_JSON:-${PROJECT_DIR}/data/config.json}"
STATE_FILE="/var/lib/blockvase/device-identity.env"
CLIENT_CONN="${CLIENT_CONN:-blockvase-home}"
HOTSPOT_CONN="${HOTSPOT_CONN:-blockvase-hotspot}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "clone-safety: must run as root"
  exit 1
fi

root_base_device() {
  local source="$1" pkname
  pkname="$(lsblk -no PKNAME "$source" 2>/dev/null | head -1 || true)"
  if [[ -n "$pkname" ]]; then
    readlink -f "/dev/${pkname}"
  else
    readlink -f "$source" 2>/dev/null || true
  fi
}

root_part_number() {
  local source="$1" partn name
  # lsblk PARTN is often space-padded (e.g. " 2"); growpart requires a bare integer.
  partn="$(lsblk -no PARTN "$source" 2>/dev/null | head -1 | tr -d '[:space:]' || true)"
  if [[ -n "$partn" ]]; then
    printf "%s" "$partn"
    return 0
  fi
  name="$(basename "$source")"
  if [[ "$name" == *p[0-9]* ]]; then
    printf "%s" "${name##*p}"
  else
    printf "%s" "${name##*[!0-9]}"
  fi
}

disk_partition_table_type() {
  local disk="$1" pttype
  pttype="$(lsblk -no PTTYPE "$disk" 2>/dev/null | head -1 | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]' || true)"
  if [[ -n "$pttype" ]]; then
    printf "%s" "$pttype"
    return 0
  fi
  # Fallback: sfdisk dump "label: gpt|dos"
  pttype="$(sfdisk -d "$disk" 2>/dev/null | awk -F': ' '/^label:/{print tolower($2); exit}' | tr -d '[:space:]' || true)"
  printf "%s" "${pttype:-unknown}"
}

hardware_suffix() {
  local iface mac fp
  for iface in wlan0 wlan1 eth0 end0; do
    [[ -r "/sys/class/net/${iface}/address" ]] || continue
    mac="$(tr -d ':' <"/sys/class/net/${iface}/address" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
    [[ ${#mac} -ge 6 ]] && { printf "%s" "${mac: -6}"; return 0; }
  done
  fp="$(current_hardware_fingerprint 2>/dev/null || true)"
  [[ -n "$fp" ]] && { printf "%s" "${fp:0:6}"; return 0; }
  printf "%s" "$(openssl rand -hex 3 2>/dev/null || echo "$RANDOM")"
}

current_hardware_fingerprint() {
  {
    awk -F ': ' '/^Serial/ {print "cpu_serial="$2; exit}' /proc/cpuinfo 2>/dev/null || true
    local iface path mac
    for path in /sys/class/net/*; do
      [[ -e "$path" ]] || continue
      iface="$(basename "$path")"
      [[ "$iface" == "lo" ]] && continue
      [[ "$(readlink -f "$path" 2>/dev/null)" == *"/virtual/"* ]] && continue
      [[ -r "$path/address" ]] || continue
      mac="$(tr -d ':' <"$path/address" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
      [[ -n "$mac" ]] && echo "mac:${iface}=${mac}"
    done
    local root_source root_base root_name serial id_serial
    root_source="$(findmnt -n -o SOURCE / 2>/dev/null | head -1 || true)"
    if [[ -n "$root_source" && -b "$root_source" ]]; then
      root_base="$(root_base_device "$root_source")"
      root_name="$(basename "$root_base")"
      echo "root_base=${root_name}"
      serial="$(cat "/sys/block/${root_name}/device/serial" 2>/dev/null || true)"
      [[ -n "$serial" ]] && echo "root_serial=${serial}"
      id_serial="$(udevadm info --query=property --name="$root_base" 2>/dev/null | awk -F= '/^ID_SERIAL=/ {print $2; exit}' || true)"
      [[ -n "$id_serial" ]] && echo "root_id_serial=${id_serial}"
    fi
  } | sort | sha256sum | awk '{print $1}'
}

is_setup_complete_json() {
  local config="$1"
  python3 - "$config" <<'PY'
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        cfg = json.load(f)
except Exception:
    print("false")
else:
    print("true" if cfg.get("setup_complete") else "false")
PY
}

refresh_setup_token_if_unconfigured() {
  local config="$1"
  [[ -f "$config" ]] || return 0
  python3 - "$config" <<'PY'
import json, secrets, sys
path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
except Exception:
    sys.exit(0)
if cfg.get("setup_complete"):
    sys.exit(0)
cfg["setup_token"] = secrets.token_urlsafe(16)
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, sort_keys=True)
    f.write("\n")
PY
  local owner="blockvase"
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    owner="${SUDO_USER}"
  elif id -u blockvase >/dev/null 2>&1; then
    owner="blockvase"
  fi
  chown "${owner}:${owner}" "$config" 2>/dev/null || true
}

clear_stale_wifi_client_profiles() {
  # Remove cloned Wi-Fi client profiles so AP mode is not racing a bad PSK.
  # Keep ethernet. Hotspot is recreated by ap-mode when setup is incomplete.
  nmcli connection delete "${CLIENT_CONN}" 2>/dev/null || true
  nmcli connection delete "${HOTSPOT_CONN}" 2>/dev/null || true
  local name typ
  while IFS=: read -r name typ; do
    [[ "$typ" == "802-11-wireless" ]] || continue
    [[ -n "$name" ]] || continue
    nmcli connection delete "$name" 2>/dev/null || true
  done < <(nmcli -t -f NAME,TYPE connection show 2>/dev/null || true)
  rm -f /var/lib/dhcp/dhclient.*.leases /var/lib/NetworkManager/*.lease /var/lib/NetworkManager/internal-* 2>/dev/null || true
}

disk_unallocated_bytes() {
  # Kernel view of free space after the last partition (bytes). More reliable than
  # growpart dry-run alone when a smaller GPT image was cloned onto a larger disk.
  local disk="$1"
  local disk_name part_end max_end name start size
  disk_name="$(basename "$disk")"
  [[ -r "/sys/block/${disk_name}/size" ]] || { echo 0; return 0; }
  max_end="$(cat "/sys/block/${disk_name}/size")"
  part_end=0
  for name in "/sys/block/${disk_name}/${disk_name}"p*; do
    [[ -e "$name/start" && -e "$name/size" ]] || continue
    start="$(cat "$name/start")"
    size="$(cat "$name/size")"
    if (( start + size > part_end )); then
      part_end=$((start + size))
    fi
  done
  if (( max_end > part_end )); then
    echo $(((max_end - part_end) * 512))
  else
    echo 0
  fi
}

expand_root_to_fill_disk() {
  local root_source root_base part_num fs_type free_bytes grow_out rc
  root_source="$(findmnt -n -o SOURCE / 2>/dev/null | head -1 || true)"
  [[ -n "$root_source" && -b "$root_source" ]] || { echo "clone-safety: root expand skipped (no root device)"; return 0; }
  root_base="$(root_base_device "$root_source")"
  part_num="$(root_part_number "$root_source")"
  fs_type="$(findmnt -n -o FSTYPE / 2>/dev/null | head -1 || true)"
  [[ -n "$root_base" && -b "$root_base" && -n "$part_num" ]] || return 0

  free_bytes="$(disk_unallocated_bytes "$root_base")"
  echo "clone-safety: ${root_base} has ~$((free_bytes / 1024 / 1024 / 1024))GiB unallocated after partitions"

  if (( free_bytes > 1024 * 1024 * 1024 )); then
    local pttype
    pttype="$(disk_partition_table_type "$root_base")"
    echo "clone-safety: partition table type on ${root_base}: ${pttype}"

    # Cloning a smaller GPT image onto a larger NVMe leaves the backup GPT header
    # at the old end of disk; growpart then reports NOCHANGE despite free space.
    # Skip on MBR/dos (Raspberry Pi OS images are typically dos).
    if [[ "$pttype" == "gpt" ]] && command -v sgdisk >/dev/null 2>&1; then
      echo "clone-safety: relocating GPT backup header to end of ${root_base}..."
      sgdisk -e "$root_base" 2>&1 || echo "clone-safety: WARNING: sgdisk -e failed (continuing)"
      partprobe "$root_base" 2>/dev/null || true
      udevadm settle 2>/dev/null || true
    fi

    echo "clone-safety: expanding root partition ${part_num} on ${root_base}..."
    rc=0
    grow_out="$(growpart -v "$root_base" "$part_num" 2>&1)" || rc=$?
    echo "$grow_out"
    if [[ "$rc" -ne 0 ]] && ! echo "$grow_out" | grep -qE '^(CHANGED|CHANGE):'; then
      echo "clone-safety: growpart failed (rc=${rc}); trying sfdisk fallback..."
      if echo ", +" | sfdisk -N "$part_num" --no-reread "$root_base" 2>&1; then
        echo "clone-safety: sfdisk grew partition ${part_num}"
      else
        echo "clone-safety: ERROR: could not grow partition ${part_num} on ${root_base}"
      fi
    fi
    partprobe "$root_base" 2>/dev/null || true
    udevadm settle 2>/dev/null || true
    # Re-read partition node after table update
    sleep 1
    root_source="$(findmnt -n -o SOURCE / 2>/dev/null | head -1 || true)"
  else
    echo "clone-safety: root partition already uses available device space"
  fi

  if [[ "$fs_type" == "ext4" && -n "$root_source" && -b "$root_source" ]]; then
    local dev_bytes fs_blocks fs_block_size fs_bytes
    dev_bytes="$(blockdev --getsize64 "$root_source" 2>/dev/null || echo 0)"
    fs_blocks="$(tune2fs -l "$root_source" 2>/dev/null | awk -F: '/Block count:/ {gsub(/ /, "", $2); print $2; exit}')"
    fs_block_size="$(tune2fs -l "$root_source" 2>/dev/null | awk -F: '/Block size:/ {gsub(/ /, "", $2); print $2; exit}')"
    if [[ "$dev_bytes" =~ ^[0-9]+$ && "$fs_blocks" =~ ^[0-9]+$ && "$fs_block_size" =~ ^[0-9]+$ ]]; then
      fs_bytes=$((fs_blocks * fs_block_size))
      if (( dev_bytes > fs_bytes + 16777216 )); then
        echo "clone-safety: expanding ext4 filesystem to fill root partition..."
        resize2fs "$root_source" || echo "clone-safety: ERROR: resize2fs failed on ${root_source}"
      else
        echo "clone-safety: root filesystem already fills the partition"
      fi
    else
      echo "clone-safety: running resize2fs on ${root_source} (size probes inconclusive)..."
      resize2fs "$root_source" 2>&1 || true
    fi
  fi

  free_bytes="$(disk_unallocated_bytes "$root_base")"
  echo "clone-safety: after expand, ~$((free_bytes / 1024 / 1024 / 1024))GiB still unallocated on ${root_base}"
  df -h / 2>/dev/null || true
}

record_fingerprint() {
  local current_fp="$1"
  local managed_hostname="${2:-}"
  mkdir -p /var/lib/blockvase
  {
    printf "fingerprint=%q\n" "$current_fp"
    printf "managed_hostname=%q\n" "$managed_hostname"
  } > "${STATE_FILE}.tmp"
  mv "${STATE_FILE}.tmp" "$STATE_FILE"
  chmod 600 "$STATE_FILE" 2>/dev/null || true
}

run_clone_safety() {
  local current_fp previous_fp previous_managed_hostname current_hostname new_hostname suffix
  local refresh_reason="" do_expand=0

  current_fp="$(current_hardware_fingerprint)"
  previous_fp=""
  previous_managed_hostname=""
  if [[ -f "$STATE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE" 2>/dev/null || true
    previous_fp="${fingerprint:-}"
    previous_managed_hostname="${managed_hostname:-}"
  fi

  if [[ "${BLOCKVASE_FORCE_DEVICE_IDENTITY_REFRESH:-}" == "1" ]]; then
    refresh_reason="forced"
    do_expand=1
  elif [[ -n "$previous_fp" && "$previous_fp" != "$current_fp" ]]; then
    refresh_reason="hardware/storage fingerprint changed"
    do_expand=1
  elif [[ -z "$previous_fp" && -f "$CONFIG_JSON" && "$(is_setup_complete_json "$CONFIG_JSON")" == "false" ]]; then
    # Unconfigured image with no fingerprint: refresh identity once, but do NOT
    # full-expand: the clone master may still be on a larger physical disk.
    refresh_reason="unconfigured first boot"
    do_expand=0
  fi

  suffix="$(hardware_suffix)"
  new_hostname="blockvase-${suffix}"
  current_hostname="$(hostnamectl --static 2>/dev/null || hostname 2>/dev/null || echo "")"

  if [[ -n "$refresh_reason" ]]; then
    echo "clone-safety: refreshing device identity (${refresh_reason})"
    if command -v systemd-machine-id-setup >/dev/null 2>&1; then
      rm -f /etc/machine-id /var/lib/dbus/machine-id
      systemd-machine-id-setup || true
      if command -v dbus-uuidgen >/dev/null 2>&1; then
        dbus-uuidgen --ensure=/var/lib/dbus/machine-id || true
      fi
    fi
    if command -v ssh-keygen >/dev/null 2>&1 && [[ -d /etc/ssh ]]; then
      rm -f /etc/ssh/ssh_host_*
      ssh-keygen -A || true
    fi
    clear_stale_wifi_client_profiles
    refresh_setup_token_if_unconfigured "$CONFIG_JSON"

    if [[ -z "$current_hostname" || "$current_hostname" == "raspberrypi" || "$current_hostname" == "blockvase" || "$current_hostname" == "ubuntu" || "$current_hostname" == "$previous_managed_hostname" || "$current_hostname" =~ ^blockvase-[0-9a-f]{6,12}$ ]]; then
      echo "$new_hostname" > /etc/hostname
      hostnamectl set-hostname "$new_hostname" 2>/dev/null || true
      sed -i "s/127.0.1.1.*/127.0.1.1\t${new_hostname}/" /etc/hosts 2>/dev/null || true
      previous_managed_hostname="$new_hostname"
    else
      echo "clone-safety: preserving custom hostname: ${current_hostname}"
    fi
  else
    if [[ -z "$previous_fp" ]]; then
      echo "clone-safety: recording device identity fingerprint for future clone detection"
    else
      echo "clone-safety: device identity matches this hardware/storage"
    fi
  fi

  if [[ "$do_expand" -eq 1 ]]; then
    expand_root_to_fill_disk
  fi

  record_fingerprint "$current_fp" "$previous_managed_hostname"
}

# Clear payout so clones/factory-reset cannot keep a master address.
# DATUM stops (needs a valid pool_address). Miner stays enabled for board monitoring.
clear_mining_runtime() {
  echo "clone-safety: clearing mining payout runtime"
  systemctl disable --now datum-gateway.service 2>/dev/null || true
  systemctl stop blockvase-miner.service 2>/dev/null || true
  rm -f /etc/blockvase/solo_mining_address /etc/blockvase/miner.env
  if [[ -f /etc/blockvase/datum_gateway_config.json ]] && command -v jq >/dev/null 2>&1; then
    local tmp
    tmp="$(mktemp)"
    if jq '.mining.pool_address = ""' /etc/blockvase/datum_gateway_config.json >"$tmp" 2>/dev/null; then
      cat "$tmp" >/etc/blockvase/datum_gateway_config.json
    fi
    rm -f "$tmp"
  else
    rm -f /etc/blockvase/datum_gateway_config.json
  fi
  if [[ -x "${PROJECT_DIR}/scripts/blockvase-miner-refresh-env.sh" ]]; then
    BLOCKVASE_SERVICE_USER=blockvase "${PROJECT_DIR}/scripts/blockvase-miner-refresh-env.sh" || true
  fi
  systemctl enable blockvase-miner.service 2>/dev/null || true
  systemctl start blockvase-miner.service 2>/dev/null || true
}

# prepare-clone-source: reset setup for imaging; never touches bitcoin datadir.
prepare_clone_source() {
  echo "clone-safety: preparing clone source (preserving /var/lib/bitcoind)"
  clear_stale_wifi_client_profiles
  clear_mining_runtime

  python3 - "$CONFIG_JSON" <<'PY'
import json, secrets, sys
from pathlib import Path
path = Path(sys.argv[1])
cfg = {}
if path.exists():
    cfg = json.loads(path.read_text(encoding="utf-8"))
cfg["setup_complete"] = False
cfg["wifi_recovery"] = False
cfg["wifi_ssid"] = ""
cfg["wifi_password"] = ""
cfg["setup_token"] = secrets.token_urlsafe(16)
cfg["admin_username"] = ""
cfg["admin_password_hash"] = ""
cfg["mining_payout_address"] = ""
cfg.pop("mining_simulation_enabled", None)
cfg.setdefault("device_name", "blockvase")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print("setup_complete=false")
print("wifi credentials cleared")
print("admin credentials cleared")
print("mining payout cleared")
print("setup_token regenerated")
PY
  local owner="blockvase"
  if id -u blockvase >/dev/null 2>&1; then
    owner="blockvase"
  elif [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    owner="${SUDO_USER}"
  fi
  chown "${owner}:${owner}" "$CONFIG_JSON" 2>/dev/null || true
  chmod 600 "$CONFIG_JSON" 2>/dev/null || true

  # Record fingerprint NOW so rebooting this master before imaging does not
  # full-expand the drive. Clones on a different disk/serial will mismatch.
  record_fingerprint "$(current_hardware_fingerprint)" ""
  echo "blockvase" > /etc/hostname
  hostnamectl set-hostname blockvase 2>/dev/null || true
  sed -i "s/127.0.1.1.*/127.0.1.1\tblockvase/" /etc/hosts 2>/dev/null || true

  echo "clone-safety: prepare-clone complete"
  echo "  - blockchain datadir left untouched"
  echo "  - mining payout cleared (miner stays enabled for board monitoring)"
  echo "  - setup will show AP/QR on next boot of this image / clones"
  echo "  - power off cleanly, then image this drive"
}

mode="${1:-run}"
case "$mode" in
  run|ensure)
    run_clone_safety
    ;;
  prepare-clone)
    prepare_clone_source
    ;;
  clear-mining)
    clear_mining_runtime
    ;;
  *)
    echo "Usage: $0 {run|prepare-clone|clear-mining}"
    exit 2
    ;;
esac
