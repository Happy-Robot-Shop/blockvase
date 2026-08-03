#!/usr/bin/env bash
# Ensure portal TLS material under /etc/blockvase/tls and refresh nginx site.
#
# Trust model: device private CA + leaf server cert.
# Clients install ca.crt (and enable full trust on iOS). nginx serves portal.crt/key.
#
# Usage (root):
#   ensure-portal-tls.sh              # create if missing / SANs stale; reload nginx
#   ensure-portal-tls.sh --force      # regenerate CA + leaf
#   ensure-portal-tls.sh --hostname NAME
#   ensure-portal-tls.sh --disable-https-site  # HTTP-only nginx (no cert yet)
#   ensure-portal-tls.sh --skip-nginx-reload  # write cert/site only (caller starts nginx later)
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

FORCE=0
DISABLE_HTTPS=0
SKIP_NGINX_RELOAD=0
HOSTNAME_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --disable-https-site) DISABLE_HTTPS=1; shift ;;
    --skip-nginx-reload) SKIP_NGINX_RELOAD=1; shift ;;
    --hostname)
      HOSTNAME_OVERRIDE="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

TLS_DIR="/etc/blockvase/tls"
CA_CRT="${TLS_DIR}/ca.crt"
CA_KEY="${TLS_DIR}/ca.key"
CRT="${TLS_DIR}/portal.crt"
KEY="${TLS_DIR}/portal.key"
META="${TLS_DIR}/meta.json"
NGINX_SITE="/etc/nginx/sites-available/blockvase"
NGINX_LINK="/etc/nginx/sites-enabled/blockvase"
PROJECT_DIR="/home/blockvase/blockvase"
if [[ -f /etc/blockvase/project-dir ]]; then
  PROJECT_DIR="$(tr -d '[:space:]' </etc/blockvase/project-dir)"
fi
TEMPLATE="${PROJECT_DIR}/deploy/nginx-blockvase.conf"
BACKEND="127.0.0.1:8080"

safe_name() {
  local n="$1"
  n="$(echo "${n}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/-+/-/g; s/^-|-$//g')"
  n="${n:0:19}"
  [[ -n "${n}" ]] || n="blockvase"
  echo "${n}"
}

device_name() {
  if [[ -n "${HOSTNAME_OVERRIDE}" ]]; then
    safe_name "${HOSTNAME_OVERRIDE}"
    return
  fi
  local cfg="${PROJECT_DIR}/data/config.json"
  if [[ -f "${cfg}" ]] && command -v python3 >/dev/null 2>&1; then
    local n
    n="$(python3 - "${cfg}" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
try:
    cfg = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    print("blockvase")
else:
    print(str(cfg.get("device_name") or "blockvase"))
PY
)"
    safe_name "${n}"
    return
  fi
  safe_name "$(hostname -s 2>/dev/null || echo blockvase)"
}

NAME="$(device_name)"
DNS_LOCAL="${NAME}.local"
CA_CN="Blockvase ${NAME} Portal CA"

tls_material_ok() {
  [[ -f "${CA_CRT}" && -f "${CA_KEY}" && -f "${CRT}" && -f "${KEY}" ]] || return 1
  # Leaf must be signed by our CA (not a legacy self-signed leaf).
  openssl verify -CAfile "${CA_CRT}" "${CRT}" >/dev/null 2>&1 || return 1
  local text
  text="$(openssl x509 -in "${CRT}" -noout -text 2>/dev/null || true)"
  [[ "${text}" == *"${DNS_LOCAL}"* ]] || return 1
  [[ "${text}" == *"192.168.4.1"* ]] || return 1
  [[ "${text}" == *"DNS:localhost"* || "${text}" == *"localhost"* ]] || return 1
  return 0
}

write_meta() {
  python3 - "${CA_CRT}" "${CRT}" "${META}" "${NAME}" <<'PY'
import hashlib, json, subprocess, sys
from datetime import datetime, timezone

ca_crt, leaf_crt, meta_path, name = sys.argv[1:5]

def sha256_der(path: str) -> str:
    der = subprocess.check_output(["openssl", "x509", "-in", path, "-outform", "DER"])
    return hashlib.sha256(der).hexdigest()

def not_after(path: str) -> str:
    dates = subprocess.check_output(
        ["openssl", "x509", "-in", path, "-noout", "-dates"],
        text=True,
    )
    for line in dates.splitlines():
        if line.startswith("notAfter="):
            return line.split("=", 1)[1].strip()
    return ""

def sans(path: str) -> list[str]:
    text = subprocess.check_output(
        ["openssl", "x509", "-in", path, "-noout", "-ext", "subjectAltName"],
        text=True,
        stderr=subprocess.DEVNULL,
    )
    out = []
    for line in text.splitlines():
        if "DNS:" in line or "IP Address:" in line or "IP:" in line:
            for part in line.replace("IP Address:", "IP:").split(","):
                part = part.strip()
                if part.startswith("DNS:") or part.startswith("IP:"):
                    out.append(part)
    return out

payload = {
    "device_name": name,
    "trust_model": "device_ca",
    "download": "ca.crt",
    "fingerprint_sha256": sha256_der(ca_crt),
    "ca_fingerprint_sha256": sha256_der(ca_crt),
    "leaf_fingerprint_sha256": sha256_der(leaf_crt),
    "not_after": not_after(leaf_crt),
    "ca_not_after": not_after(ca_crt),
    "sans": sans(leaf_crt),
    "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
open(meta_path, "w", encoding="utf-8").write(json.dumps(payload, indent=2) + "\n")
PY
  chmod 644 "${META}"
}

generate_cert() {
  mkdir -p "${TLS_DIR}"
  chmod 755 "${TLS_DIR}"
  local ca_conf leaf_conf csr
  ca_conf="$(mktemp)"
  leaf_conf="$(mktemp)"
  csr="$(mktemp)"

  cat >"${ca_conf}" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = ext

[dn]
CN = ${CA_CN}
O = Blockvase
OU = Portal CA

[ext]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
EOF

  cat >"${leaf_conf}" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = ext

[dn]
CN = ${DNS_LOCAL}
O = Blockvase
OU = Portal

[ext]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt
subjectKeyIdentifier = hash

[alt]
DNS.1 = ${DNS_LOCAL}
DNS.2 = localhost
DNS.3 = ${NAME}
IP.1 = 127.0.0.1
IP.2 = 192.168.4.1
EOF

  # Long-lived device CA; shorter leaf (reissued when hostname/SANs change).
  openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
    -keyout "${CA_KEY}" -out "${CA_CRT}" -config "${ca_conf}" >/dev/null 2>&1

  openssl req -new -newkey rsa:2048 -nodes \
    -keyout "${KEY}" -out "${csr}" -config "${leaf_conf}" >/dev/null 2>&1

  openssl x509 -req -in "${csr}" -CA "${CA_CRT}" -CAkey "${CA_KEY}" -CAcreateserial \
    -out "${CRT}" -days 825 -sha256 -extfile "${leaf_conf}" -extensions ext >/dev/null 2>&1

  rm -f "${ca_conf}" "${leaf_conf}" "${csr}" "${TLS_DIR}/ca.srl"

  chmod 600 "${CA_KEY}"
  chmod 644 "${CA_CRT}"
  chmod 640 "${KEY}"
  chmod 644 "${CRT}"
  chown root:root "${CA_KEY}" "${CA_CRT}" "${CRT}"
  if getent group www-data >/dev/null 2>&1; then
    chown root:www-data "${KEY}"
  else
    chown root:root "${KEY}"
  fi

  write_meta
  echo "ensure-portal-tls: issued device CA + leaf for ${DNS_LOCAL}"
}

install_nginx_site() {
  if [[ ! -f "${TEMPLATE}" ]]; then
    echo "ensure-portal-tls: missing nginx template ${TEMPLATE}" >&2
    return 1
  fi
  if ! command -v nginx >/dev/null 2>&1; then
    echo "ensure-portal-tls: nginx not installed; skipping site install" >&2
    return 0
  fi
  local enable_https=0
  if [[ "${DISABLE_HTTPS}" -eq 0 && -f "${CRT}" && -f "${KEY}" && -f "${CA_CRT}" ]]; then
    enable_https=1
  fi
  python3 - "${TEMPLATE}" "${NGINX_SITE}" "${BACKEND}" "${CRT}" "${KEY}" "${enable_https}" <<'PY'
import sys
from pathlib import Path
src, dst, backend, crt, key, enable = sys.argv[1:7]
lines = Path(src).read_text(encoding="utf-8").splitlines(keepends=True)
out = []
skip = False
for line in lines:
    if "#__HTTPS_BEGIN__" in line:
        if enable != "1":
            skip = True
        continue
    if "#__HTTPS_END__" in line:
        skip = False
        continue
    if skip:
        continue
    out.append(line)
text = "".join(out)
text = text.replace("__BACKEND__", backend)
text = text.replace("__TLS_CRT__", crt)
text = text.replace("__TLS_KEY__", key)
Path(dst).write_text(text, encoding="utf-8")
PY
  chmod 644 "${NGINX_SITE}"
  mkdir -p /etc/nginx/sites-enabled
  ln -sfn "${NGINX_SITE}" "${NGINX_LINK}"
  rm -f /etc/nginx/sites-enabled/default
  if ! nginx -t >/tmp/blockvase-nginx-t.out 2>&1; then
    echo "ensure-portal-tls: nginx -t failed; left config at ${NGINX_SITE}" >&2
    cat /tmp/blockvase-nginx-t.out >&2 || true
    return 1
  fi
  echo "ensure-portal-tls: nginx site written (https=${enable_https})"
  if [[ "${SKIP_NGINX_RELOAD}" -eq 1 ]]; then
    echo "ensure-portal-tls: skipped nginx reload (--skip-nginx-reload)"
    return 0
  fi
  systemctl enable nginx.service >/dev/null 2>&1 || true
  if systemctl is-active --quiet nginx.service; then
    if systemctl reload nginx.service; then
      echo "ensure-portal-tls: nginx reloaded"
      return 0
    fi
  fi
  if systemctl restart nginx.service; then
    echo "ensure-portal-tls: nginx restarted"
    return 0
  fi
  echo "ensure-portal-tls: WARNING: nginx failed to start (is :80/:443 free? restart blockvase first)" >&2
  systemctl status nginx.service --no-pager -l 2>&1 | head -20 >&2 || true
  return 1
}

mkdir -p /etc/blockvase
if [[ "${FORCE}" -eq 1 ]] || ! tls_material_ok; then
  generate_cert
else
  echo "ensure-portal-tls: existing CA + leaf OK for ${DNS_LOCAL}"
  [[ -f "${META}" ]] || write_meta
fi

install_nginx_site
