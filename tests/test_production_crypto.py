from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from m365_mcp.config import ConfirmationSettings
from m365_mcp.confirmation import ConfirmationError, ConfirmationVerifier, mint_confirmation_token
from m365_mcp.graph_client import CertificateCredential


def _rsa_material(tmp_path: Path) -> tuple[str, str, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    pfx = tmp_path / "service.pfx"
    pfx.write_bytes(
        pkcs12.serialize_key_and_certificates(
            name=b"service",
            key=key,
            cert=certificate,
            cas=None,
            encryption_algorithm=serialization.BestAvailableEncryption(b"test-password"),
        )
    )
    public_key_path = tmp_path / "confirmation_public.pem"
    public_key_path.write_text(public_pem, encoding="utf-8")
    return private_pem, public_pem, public_key_path


def test_pfx_is_compatible_with_msal_credential(tmp_path) -> None:
    _rsa_material(tmp_path)
    credential = CertificateCredential.from_pfx(
        client_id="client",
        tenant_id="tenant",
        path=str(tmp_path / "service.pfx"),
        password="test-password",
    )
    assert credential.private_key_pem.startswith("-----BEGIN PRIVATE KEY-----")
    assert credential.public_certificate_pem.startswith("-----BEGIN CERTIFICATE-----")
    assert len(credential.thumbprint) == 40
    assert credential.thumbprint == credential.thumbprint.upper()


@pytest.mark.asyncio
async def test_production_rs256_confirmation(tmp_path) -> None:
    private_pem, _, public_path = _rsa_material(tmp_path)
    settings = ConfirmationSettings(
        enabled=True,
        algorithm="RS256",
        public_key_path=str(public_path),
        issuer="prod-approval",
        audience="m365-graph-mcp",
        sqlite_path=str(tmp_path / "replay.sqlite3"),
    )
    verifier = ConfirmationVerifier(settings, "graph")
    token = mint_confirmation_token(
        action="entra_update_user",
        params={"user_id": "u1"},
        actor="agent-client",
        approver="CHG-1",
        server_name="graph",
        algorithm="RS256",
        private_key=private_pem,
        issuer="prod-approval",
        audience="m365-graph-mcp",
    )
    result = await verifier.verify_and_consume(
        token,
        action="entra_update_user",
        params={"user_id": "u1"},
        actor="agent-client",
    )
    assert result.approver == "CHG-1"


def test_confirmation_rejects_alg_none_and_wrong_key(tmp_path) -> None:
    _, _, public_path = _rsa_material(tmp_path)
    settings = ConfirmationSettings(
        enabled=True,
        algorithm="RS256",
        public_key_path=str(public_path),
        issuer="prod-approval",
        audience="m365-graph-mcp",
        sqlite_path=str(tmp_path / "replay.sqlite3"),
    )
    verifier = ConfirmationVerifier(settings, "graph")
    unsigned = jwt.encode(
        {
            "iss": "prod-approval",
            "aud": "m365-graph-mcp",
            "sub": "CHG-1",
            "iat": 1,
            "nbf": 1,
            "exp": 9999999999,
            "jti": "abcdefgh",
            "action": "entra_update_user",
            "params_sha256": "0" * 64,
            "actor": "agent-client",
        },
        None,
        algorithm="none",
    )
    with pytest.raises(ConfirmationError, match="unexpected algorithm"):
        verifier.verify(
            unsigned,
            action="entra_update_user",
            params={"user_id": "u1"},
            actor="agent-client",
        )

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    token = mint_confirmation_token(
        action="entra_update_user",
        params={"user_id": "u1"},
        actor="agent-client",
        approver="CHG-1",
        server_name="graph",
        algorithm="RS256",
        private_key=other_pem,
        issuer="prod-approval",
        audience="m365-graph-mcp",
    )
    with pytest.raises(ConfirmationError, match="invalid"):
        verifier.verify(
            token,
            action="entra_update_user",
            params={"user_id": "u1"},
            actor="agent-client",
        )
