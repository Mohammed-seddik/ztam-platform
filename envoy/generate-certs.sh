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

# crt: 644 (readable by Envoy container user); key: 600 (owner only — never world-readable)
chmod 600 "$CERT_DIR/server.key"
chmod 644 "$CERT_DIR/server.crt"
echo "Certificates written to $CERT_DIR"
echo "NOTE: Replace with a CA-signed certificate (e.g. Let's Encrypt) in production."
