#!/usr/bin/env python3
"""Acquire an app-only Entra token for an Agent Runtime smoke test."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import msal
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", default=os.environ.get("M365_TENANT_ID", ""))
    parser.add_argument("--client-id", default=os.environ.get("SMOKE_CLIENT_ID", ""))
    parser.add_argument("--certificate", default=os.environ.get("SMOKE_CERTIFICATE_PATH", ""))
    parser.add_argument(
        "--certificate-password-file",
        default=os.environ.get("SMOKE_CERTIFICATE_PASSWORD_FILE", ""),
    )
    parser.add_argument(
        "--scope",
        default=os.environ.get("SMOKE_SCOPE", "api://m365-graph-mcp/.default"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    missing = [
        name
        for name, value in (
            ("tenant-id", args.tenant_id),
            ("client-id", args.client_id),
            ("certificate", args.certificate),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"missing required options: {', '.join(missing)}")
    password = None
    if args.certificate_password_file:
        password = Path(args.certificate_password_file).read_text(encoding="utf-8").rstrip("\r\n")
    private_key, certificate, _ = pkcs12.load_key_and_certificates(
        Path(args.certificate).read_bytes(),
        password.encode("utf-8") if password else None,
    )
    if private_key is None or certificate is None:
        raise SystemExit("certificate must contain an RSA private key and public certificate")
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    # Entra/MSAL certificate client assertions require the SHA-1 certificate thumbprint.
    thumbprint = certificate.fingerprint(hashes.SHA1()).hex().upper()  # noqa: S303
    application = msal.ConfidentialClientApplication(
        client_id=args.client_id,
        authority=f"https://login.microsoftonline.com/{args.tenant_id}",
        client_credential={
            "private_key": private_pem,
            "thumbprint": thumbprint,
            "public_certificate": public_pem,
        },
    )
    result = application.acquire_token_for_client(scopes=[args.scope])
    token = result.get("access_token") if isinstance(result, dict) else None
    if not token:
        error = result.get("error", "unknown") if isinstance(result, dict) else "unknown"
        raise SystemExit(f"token request failed: {error}")
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
