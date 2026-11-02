#!/usr/bin/env bash
# Generate the write-approval signing keypair.
# The private key belongs in the change approval system, not in the MCP runtime.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/secrets"
mkdir -p "$OUT"
chmod 700 "$OUT"
umask 077

PRIVATE="$OUT/confirmation_signing.pem"
PUBLIC="$OUT/confirmation_public.pem"

if [[ -e "$PRIVATE" || -e "$PUBLIC" ]]; then
  echo "refusing to overwrite existing confirmation keys" >&2
  exit 1
fi

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$PRIVATE"
openssl pkey -in "$PRIVATE" -pubout -out "$PUBLIC"
chmod 600 "$PRIVATE" "$PUBLIC"

echo "created:"
echo "  private signing key: $PRIVATE"
echo "  public verify key:   $PUBLIC"
echo "Move the private key to the approval/change system. Only mount the public key"
echo "into the MCP servers."
