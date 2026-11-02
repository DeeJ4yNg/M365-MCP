"""Inbound Entra ID token validation and authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token

from m365_mcp.config import AuthSettings
from m365_mcp.errors import AuthorizationError


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """Authenticated actor used for audit and approval binding."""

    actor: str
    authentication_mode: str
    roles: frozenset[str]
    tenant_id: str | None = None


def build_auth_provider(settings: AuthSettings) -> JWTVerifier | None:
    """Build the FastMCP inbound JWT verifier."""

    if settings.mode == "none":
        return None
    return JWTVerifier(
        jwks_uri=settings.jwks_uri,
        issuer=settings.issuer,
        audience=settings.audience,
        algorithm="RS256",
    )


def current_caller(settings: AuthSettings, *, write: bool) -> CallerIdentity:
    """Read and authorize the caller of the current MCP tool request."""

    if settings.mode == "none":
        if not settings.allow_insecure_dev:
            raise AuthorizationError(
                "authentication_required", "Inbound authentication is disabled"
            )
        role = "M365.MCP.ReadWrite" if write else "M365.MCP.Read"
        return CallerIdentity(
            actor="insecure-dev",
            authentication_mode="none",
            roles=frozenset({role}),
        )
    token = get_access_token()
    if token is None:
        raise AuthorizationError("authentication_required", "A bearer token is required")
    claims: dict[str, Any] = dict(token.claims or {})
    return authorize_claims(claims, settings, write=write)


def authorize_claims(
    claims: dict[str, Any], settings: AuthSettings, *, write: bool
) -> CallerIdentity:
    """Pure authorization function, separately testable from FastMCP context."""

    roles_raw = claims.get("roles", [])
    if isinstance(roles_raw, str):
        roles = frozenset({roles_raw})
    elif isinstance(roles_raw, list):
        roles = frozenset(str(role) for role in roles_raw)
    else:
        roles = frozenset()
    required = settings.required_write_roles if write else settings.required_read_roles
    if not roles.intersection(required):
        raise AuthorizationError(
            "insufficient_role",
            "The caller lacks an MCP app role required for this operation",
            {"required_any_of": required},
        )
    actor = str(
        claims.get("azp")
        or claims.get("appid")
        or claims.get("client_id")
        or claims.get("sub")
        or ""
    ).strip()
    if not actor:
        raise AuthorizationError(
            "missing_actor", "The token has no stable application or subject identifier"
        )
    if settings.allowed_client_ids and actor not in settings.allowed_client_ids:
        raise AuthorizationError(
            "caller_not_allowed", "The calling application is not on the allow-list"
        )
    tenant_id = str(claims.get("tid") or "").strip() or None
    if settings.tenant_id and tenant_id != settings.tenant_id:
        raise AuthorizationError(
            "tenant_mismatch", "The caller token was issued for a different tenant"
        )
    return CallerIdentity(
        actor=actor,
        authentication_mode="jwt",
        roles=roles,
        tenant_id=tenant_id,
    )
