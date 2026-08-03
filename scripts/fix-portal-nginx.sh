#!/usr/bin/env bash
# Recover when Waitress moved to :8080 but nginx failed to bind :80 (race).
# Usage: sudo scripts/fix-portal-nginx.sh
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

PROJECT_DIR="/home/blockvase/blockvase"
if [[ -f /etc/blockvase/project-dir ]]; then
  PROJECT_DIR="$(tr -d '[:space:]' </etc/blockvase/project-dir)"
fi

systemctl daemon-reload
systemctl restart blockvase.service
for _i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if ss -tln | grep -q '127.0.0.1:8080'; then
    break
  fi
  sleep 0.5
done
# Ensure nothing but nginx should own :80.
if ss -tlnp | grep ':80 ' | grep -vq nginx; then
  echo "WARNING: non-nginx process still listening on :80:" >&2
  ss -tlnp | grep ':80 ' >&2 || true
fi

if [[ -x /usr/lib/blockvase/ensure-portal-tls.sh ]]; then
  install -o root -g root -m 755 \
    "${PROJECT_DIR}/scripts/ensure-portal-tls.sh" /usr/lib/blockvase/ensure-portal-tls.sh
  /usr/lib/blockvase/ensure-portal-tls.sh
else
  "${PROJECT_DIR}/scripts/ensure-portal-tls.sh"
fi

systemctl enable nginx.service >/dev/null 2>&1 || true
systemctl restart nginx.service

echo "blockvase: $(systemctl is-active blockvase.service)"
echo "nginx:     $(systemctl is-active nginx.service)"
ss -tln | grep -E ':80|:443|:8080' || true
curl -sS -o /dev/null -w "http:%{http_code}\n" http://127.0.0.1/api/tls/status || true
curl -skS -o /dev/null -w "https:%{http_code}\n" https://127.0.0.1/api/tls/status || true
