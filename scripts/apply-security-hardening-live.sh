#!/usr/bin/env bash
# One-shot live apply for the security hardening changes (requires root).
# Usage: sudo scripts/apply-security-hardening-live.sh
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="blockvase"

mkdir -p /usr/lib/blockvase /etc/blockvase /var/lib/blockvase
echo "${PROJECT_DIR}" >/etc/blockvase/project-dir
chmod 644 /etc/blockvase/project-dir

for s in ap-mode.sh device-update.sh set-mining-payout.sh blockvase-miner-refresh-env.sh verify-ota-update.sh ensure-portal-tls.sh; do
  if [[ -f "${PROJECT_DIR}/scripts/${s}" ]]; then
    install -o root -g root -m 755 "${PROJECT_DIR}/scripts/${s}" "/usr/lib/blockvase/${s}"
  fi
done
if [[ -f "${PROJECT_DIR}/security/ota-allowed-remotes.txt" ]]; then
  install -o root -g root -m 644 \
    "${PROJECT_DIR}/security/ota-allowed-remotes.txt" /etc/blockvase/ota-allowed-remotes.txt
fi
if [[ -f "${PROJECT_DIR}/security/ota-allowed-signers" ]]; then
  install -o root -g root -m 644 \
    "${PROJECT_DIR}/security/ota-allowed-signers" /etc/blockvase/ota-allowed-signers
fi
chmod +x "${PROJECT_DIR}/scripts/verify-ota-update.sh" 2>/dev/null || true
chmod +x "${PROJECT_DIR}/scripts/ensure-portal-tls.sh" 2>/dev/null || true
# Appliances must not keep the OTA private signing key.
rm -f "/home/${SERVICE_USER}/.blockvase-secrets/ota-signing" 2>/dev/null || true
# Portal TLS + nginx package (site/reload happens after Waitress moves off :80).
if command -v apt-get >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y nginx openssl >/dev/null 2>&1 || true
fi
# Drop blanket sudo from %sudo; keep NOPASSWD appliance rules below.
if id -nG "${SERVICE_USER}" 2>/dev/null | tr ' ' '\n' | grep -qx sudo; then
  gpasswd -d "${SERVICE_USER}" sudo 2>/dev/null || true
fi

cat >/etc/sudoers.d/blockvase-ap <<EOF
${SERVICE_USER} ALL=(ALL) NOPASSWD: /usr/lib/blockvase/ap-mode.sh
EOF
cat >/etc/sudoers.d/blockvase-mining-payout <<EOF
${SERVICE_USER} ALL=(ALL) NOPASSWD: /usr/lib/blockvase/set-mining-payout.sh
EOF
cat >/etc/sudoers.d/blockvase-miner-env <<EOF
${SERVICE_USER} ALL=(ALL) NOPASSWD: /usr/lib/blockvase/blockvase-miner-refresh-env.sh
EOF
cat >/etc/sudoers.d/blockvase-device-update <<EOF
${SERVICE_USER} ALL=(ALL) NOPASSWD: /usr/lib/blockvase/device-update.sh
EOF
cat >/etc/sudoers.d/blockvase-portal-tls <<EOF
${SERVICE_USER} ALL=(ALL) NOPASSWD: /usr/lib/blockvase/ensure-portal-tls.sh
EOF
cat >/etc/sudoers.d/blockvase-check-asic <<EOF
${SERVICE_USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop blockvase-miner.service, /usr/bin/systemctl start blockvase-miner.service, /usr/bin/systemctl restart blockvase-miner.service
EOF
chmod 440 /etc/sudoers.d/blockvase-ap /etc/sudoers.d/blockvase-mining-payout \
  /etc/sudoers.d/blockvase-miner-env /etc/sudoers.d/blockvase-device-update \
  /etc/sudoers.d/blockvase-portal-tls /etc/sudoers.d/blockvase-check-asic

install -o root -g root -m 644 "${PROJECT_DIR}/systemd/bitcoind.service" /etc/systemd/system/bitcoind.service

TMP_UNIT="$(mktemp)"
sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g; s|__SERVICE_USER__|${SERVICE_USER}|g" \
  "${PROJECT_DIR}/systemd/blockvase.service" >"${TMP_UNIT}"
install -o root -g root -m 644 "${TMP_UNIT}" /etc/systemd/system/blockvase.service
rm -f "${TMP_UNIT}"

# Drop setcap on system Python; portal binds :80 via AmbientCapabilities.
for p in /usr/bin/python3 /usr/bin/python3.13 /usr/bin/python3.12; do
  [[ -f "${p}" ]] && setcap -r "${p}" 2>/dev/null || true
done
if [[ -x "${PROJECT_DIR}/.venv/bin/python3" ]]; then
  REAL="$(readlink -f "${PROJECT_DIR}/.venv/bin/python3" 2>/dev/null || true)"
  [[ -n "${REAL}" ]] && setcap -r "${REAL}" 2>/dev/null || true
fi

if getent group bitcoin >/dev/null 2>&1; then
  usermod -aG bitcoin "${SERVICE_USER}" 2>/dev/null || true
fi

mkdir -p "${PROJECT_DIR}/data"
chown "${SERVICE_USER}:${SERVICE_USER}" "${PROJECT_DIR}/data"
chmod 755 "${PROJECT_DIR}/data"
[[ -f "${PROJECT_DIR}/data/config.json" ]] && chown "${SERVICE_USER}:${SERVICE_USER}" "${PROJECT_DIR}/data/config.json"
[[ -f "${PROJECT_DIR}/data/config.json" ]] && chmod 600 "${PROJECT_DIR}/data/config.json"

# Recover Wi-Fi PSK from legacy root-owned wifi.secret into sealed config field.
if [[ -f "${PROJECT_DIR}/data/wifi.secret" ]]; then
  WIFI_PW="$(tr -d '\r\n' <"${PROJECT_DIR}/data/wifi.secret" || true)"
  sudo -u "${SERVICE_USER}" env PROJECT_DIR="${PROJECT_DIR}" WIFI_PW="${WIFI_PW}" python3 - <<'PY'
import os, sys
sys.path.insert(0, os.environ["PROJECT_DIR"])
from app.config import load_config, save_config
cfg = load_config()
pw = os.environ.get("WIFI_PW", "")
if pw:
    cfg["wifi_password"] = pw
save_config(cfg)
print("wifi password sealed into config.json")
PY
  rm -f "${PROJECT_DIR}/data/wifi.secret"
fi

# Ensure ap_password / session fields exist.
sudo -u "${SERVICE_USER}" env PROJECT_DIR="${PROJECT_DIR}" python3 - <<'PY'
import os, sys
sys.path.insert(0, os.environ["PROJECT_DIR"])
from app.config import load_config, save_config
cfg = load_config()
save_config(cfg)
print("config normalized")
PY

visudo -c
systemctl daemon-reload
# Waitress must leave :80 before nginx can bind (loopback :8080 only).
systemctl restart blockvase.service
# Brief wait so the old :80 listener is gone.
for _i in 1 2 3 4 5 6 7 8 9 10; do
  if ! ss -tln | grep -q ':80 '; then
    break
  fi
  # Still bound — only OK if it is already nginx.
  if systemctl is-active --quiet nginx.service; then
    break
  fi
  sleep 0.5
done
if [[ -x /usr/lib/blockvase/ensure-portal-tls.sh ]]; then
  /usr/lib/blockvase/ensure-portal-tls.sh || true
elif [[ -x "${PROJECT_DIR}/scripts/ensure-portal-tls.sh" ]]; then
  "${PROJECT_DIR}/scripts/ensure-portal-tls.sh" || true
fi
systemctl enable nginx.service >/dev/null 2>&1 || true
systemctl restart nginx.service || true
if ! systemctl is-active --quiet nginx.service; then
  echo "ERROR: nginx is not active. Portal may only be reachable on 127.0.0.1:8080." >&2
  systemctl status nginx.service --no-pager -l 2>&1 | head -25 >&2 || true
fi
systemctl restart blockvase-ap.service || true
# Miner picks up rest.py debug=False + thermal trip helper on next restart.
systemctl restart blockvase-miner.service || true

echo "Live security hardening applied."
echo "  - /usr/lib/blockvase scripts + sudoers"
echo "  - root-owned OTA allowlists under /etc/blockvase"
echo "  - appliance OTA private key removed (if present)"
echo "  - nginx :80/:443 → Waitress 127.0.0.1:8080 + portal TLS cert"
echo "  - root-owned bitcoind.service"
echo "  - setcap removed from system Python (if present)"
echo "  - wifi.secret migrated into wifi_password_enc"
echo "  - nginx active: $(systemctl is-active nginx.service 2>/dev/null || echo unknown)"
