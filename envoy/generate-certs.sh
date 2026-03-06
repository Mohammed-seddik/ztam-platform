#!/usr/bin/env bash
# Regenerate the self-signed TLS certificate for Envoy.
# Replace with a CA-signed cert (e.g. Let's Encrypt) in production.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT_DIR="$SCRIPT_DIR/certs"
mkdir -p "$CERT_DIR"

openssl req -x509 -newkey rsa:4096 \
  -keyout "$CERT_DIR/server.key" \
  -out    "$CERT_DIR/server.crt" \
  -sha256 -days 825 -nodes \
  -subj "/C=DZ/ST=Algiers/L=Algiers/O=ZTAM Platform/OU=Security/CN=ztam.local" \
  -addext "subjectAltName=DNS:ztam.local,DNS:localhost,IP:127.0.0.1"

# 644 so Envoy's container user can read them; private key stays host-only on real deployments
chmod 644 "$CERT_DIR/server.key" "$CERT_DIR/server.crt"
echo "Certificates written to $CERT_DIR"
echo "NOTE: Replace with a CA-signed certificate (e.g. Let's Encrypt) in production."
