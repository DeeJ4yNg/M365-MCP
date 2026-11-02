from __future__ import annotations

import pytest

from m365_mcp.auth import authorize_claims
from m365_mcp.config import AuthSettings
from m365_mcp.errors import AuthorizationError


def _settings(**overrides):
    values = {
        "mode": "jwt",
        "tenant_id": "tenant-1",
        "audience": "api://m365-graph-mcp",
        "allowed_client_ids": ["agent-1"],
    }
    values.update(overrides)
    return AuthSettings(**values)


def test_read_and_write_roles_are_distinct() -> None:
    read = authorize_claims(
        {"roles": ["M365.MCP.Read"], "azp": "agent-1", "tid": "tenant-1"},
        _settings(),
        write=False,
    )
    assert read.actor == "agent-1"
    with pytest.raises(AuthorizationError, match="lacks an MCP app role"):
        authorize_claims(
            {"roles": ["M365.MCP.Read"], "azp": "agent-1", "tid": "tenant-1"},
            _settings(),
            write=True,
        )


def test_write_role_also_permits_read() -> None:
    caller = authorize_claims(
        {"roles": ["M365.MCP.ReadWrite"], "appid": "agent-1", "tid": "tenant-1"},
        _settings(),
        write=True,
    )
    assert "M365.MCP.ReadWrite" in caller.roles


def test_client_allowlist_and_tenant_are_enforced() -> None:
    with pytest.raises(AuthorizationError, match="not on the allow-list"):
        authorize_claims(
            {"roles": ["M365.MCP.Read"], "azp": "agent-2", "tid": "tenant-1"},
            _settings(),
            write=False,
        )
    with pytest.raises(AuthorizationError, match="different tenant"):
        authorize_claims(
            {"roles": ["M365.MCP.Read"], "azp": "agent-1", "tid": "tenant-2"},
            _settings(),
            write=False,
        )


def test_insecure_mode_must_be_explicit() -> None:
    with pytest.raises(ValueError, match="MCP_ALLOW_INSECURE_DEV"):
        AuthSettings(mode="none")
    dev = AuthSettings(mode="none", allow_insecure_dev=True)
    assert dev.mode == "none"
