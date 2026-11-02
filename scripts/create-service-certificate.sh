#!/usr/bin/env bash
# Generate a self-signed RSA certificate and PFX for a Graph, PowerShell or Agent app.
# Usage: ./scripts/create-service-certificate.sh graph|powershell|agent
set -euo pipefail

SERVER="${1:-}"
if [[ "$SERVER" != "graph" && "$SERVER" != "powershell" && "$SERVER" != "agent" ]]; then
  echo "usage: $0 graph|powershell|agent" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/secrets"
mkdir -p "$OUT"
chmod 700 "$OUT"
umask 077

SUBJECT="CN=m365-mcp-${SERVER}"
KEY="$OUT/${SERVER}.key"
CRT="$OUT/${SERVER}.crt"
PFX="$OUT/${SERVER}.pfx"
PASSWORD_FILE="$OUT/${SERVER}.pfx.password"

if [[ -e "$PFX" ]]; then
  echo "refusing to overwrite $PFX; rotate explicitly after reviewing dependencies" >&2
  exit 1
fi
if [[ ! -f "$PASSWORD_FILE" ]]; then
  openssl rand -base64 48 > "$PASSWORD_FILE"
  chmod 600 "$PASSWORD_FILE"
fi

openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
  -keyout "$KEY" -out "$CRT" -subj "/$SUBJECT" \
  -addext "keyUsage=digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=clientAuth"

openssl pkcs12 -export \
  -inkey "$KEY" \
  -in "$CRT" \
  -out "$PFX" \
  -name "$SUBJECT" \
  -passout "file:$PASSWORD_FILE"

chmod 600 "$KEY" "$CRT" "$PFX"
echo "created:"
echo "  private key:      $KEY"
echo "  public cert:      $CRT"
echo "  PFX:              $PFX"
echo "  PFX password:     $PASSWORD_FILE"
echo
echo "Upload only $CRT to Entra ID > App registrations > Certificates & secrets."
if [[ "$SERVER" == "agent" ]]; then
  echo "Keep the Agent PFX in the Agent Runtime secret store, not in the MCP containers."
fi
echo "Move the private key and PFX into your secret manager. Do not commit secrets/."
