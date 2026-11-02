from __future__ import annotations

import time

import pytest

from m365_mcp.confirmation import (
    ConfirmationError,
    ConfirmationVerifier,
    SqliteReplayStore,
    canonical_params_sha256,
    mint_confirmation_token,
)
from tests.helpers import confirmation_settings

SECRET = "test-secret-that-is-at-least-32-characters-long"


def _settings(tmp_path, *, audience: str = "m365-graph-mcp"):
    settings = confirmation_settings(tmp_path)
    return settings.model_copy(update={"audience": audience, "algorithm": "HS256"})


def _token(action: str = "set_test_user", params=None, actor: str = "agent-client", **overrides):
    values = params or {"user_id": "u1", "display_name": "Alice"}
    arguments = {
        "action": action,
        "params": values,
        "actor": actor,
        "approver": "change-123",
        "server_name": "graph",
        "algorithm": "HS256",
        "private_key": SECRET,
        "issuer": "test-change-system",
        "audience": "m365-graph-mcp",
        "ttl_seconds": 300,
    }
    arguments.update(overrides)
    return mint_confirmation_token(**arguments)


@pytest.mark.asyncio
async def test_confirmation_is_bound_and_single_use(tmp_path) -> None:
    verifier = ConfirmationVerifier(_settings(tmp_path), "graph")
    token = _token()
    confirmation = await verifier.verify_and_consume(
        token,
        action="set_test_user",
        params={"user_id": "u1", "display_name": "Alice"},
        actor="agent-client",
    )
    assert confirmation.approver == "change-123"
    with pytest.raises(ConfirmationError) as replay:
        await verifier.verify_and_consume(
            token,
            action="set_test_user",
            params={"user_id": "u1", "display_name": "Alice"},
            actor="agent-client",
        )
    assert replay.value.code == "confirmation_replayed"
    await verifier.close()


@pytest.mark.asyncio
async def test_confirmation_rejects_parameter_or_actor_changes(tmp_path) -> None:
    verifier = ConfirmationVerifier(_settings(tmp_path), "graph")
    token = _token()
    with pytest.raises(ConfirmationError, match="different parameters"):
        verifier.verify(
            token,
            action="set_test_user",
            params={"user_id": "u1", "display_name": "Mallory"},
            actor="agent-client",
        )
    with pytest.raises(ConfirmationError, match="different calling application"):
        verifier.verify(
            token,
            action="set_test_user",
            params={"user_id": "u1", "display_name": "Alice"},
            actor="another-agent",
        )
    await verifier.close()


def test_confirmation_rejects_wrong_action_and_expiry(tmp_path) -> None:
    verifier = ConfirmationVerifier(_settings(tmp_path), "graph")
    with pytest.raises(ConfirmationError, match="different action"):
        verifier.verify(
            _token(),
            action="delete_test_user",
            params={"user_id": "u1", "display_name": "Alice"},
            actor="agent-client",
        )
    expired = _token(now=int(time.time()) - 1000, ttl_seconds=10)
    with pytest.raises(ConfirmationError, match="invalid, expired"):
        verifier.verify(
            expired,
            action="set_test_user",
            params={"user_id": "u1", "display_name": "Alice"},
            actor="agent-client",
        )
    too_long = _token(ttl_seconds=901)
    with pytest.raises(ConfirmationError, match="lifetime exceeds"):
        verifier.verify(
            too_long,
            action="set_test_user",
            params={"user_id": "u1", "display_name": "Alice"},
            actor="agent-client",
        )


def test_canonical_digest_is_order_independent() -> None:
    assert canonical_params_sha256({"b": 2, "a": 1}) == canonical_params_sha256({"a": 1, "b": 2})
    assert canonical_params_sha256({"a": 1}) != canonical_params_sha256({"a": True})


@pytest.mark.asyncio
async def test_sqlite_replay_store_is_atomic(tmp_path) -> None:
    store = SqliteReplayStore(str(tmp_path / "replay.sqlite3"))
    assert await store.reserve("jti-12345678", 30) is True
    assert await store.reserve("jti-12345678", 30) is False


def test_hs256_requires_explicit_dev_opt_in(tmp_path) -> None:
    settings = confirmation_settings(tmp_path).model_copy(
        update={"allow_hs256": False, "algorithm": "HS256"}
    )
    with pytest.raises(ConfirmationError, match="MCP_CONFIRMATION_ALLOW_HS256"):
        ConfirmationVerifier(settings, "graph")
