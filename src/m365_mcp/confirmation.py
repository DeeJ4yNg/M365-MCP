"""Short-lived, single-use, parameter-bound confirmation tokens.

A write approval is signed outside the MCP server. The server verifies the
signature, audience, tenant caller, action, exact parameter digest and expiry,
then reserves the token's jti before calling Microsoft 365. Reserving before the
upstream call intentionally makes retries require a fresh approval.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import jwt
from redis.asyncio import Redis

from m365_mcp.config import ConfirmationSettings
from m365_mcp.errors import M365Error

_JTI_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


class ConfirmationError(M365Error):
    """Approval token rejected or already consumed."""


@dataclass(frozen=True, slots=True)
class Confirmation:
    jti: str
    approver: str
    actor: str
    action: str
    params_sha256: str
    expires_at: int


class ReplayStore(Protocol):
    async def reserve(self, jti: str, ttl_seconds: int) -> bool: ...

    async def close(self) -> None: ...


class RedisReplayStore:
    """Atomic distributed replay protection for multi-replica deployments."""

    def __init__(self, url: str) -> None:
        self._redis = Redis.from_url(url, encoding="utf-8", decode_responses=True)

    async def reserve(self, jti: str, ttl_seconds: int) -> bool:
        try:
            result = await self._redis.set(
                f"m365-mcp:confirmation:{jti}",
                "used",
                nx=True,
                ex=max(ttl_seconds, 1),
            )
        except Exception as exc:
            raise ConfirmationError(
                "replay_store_unavailable",
                "The confirmation replay store is unavailable; write was not executed",
                retryable=True,
            ) from exc
        return bool(result)

    async def close(self) -> None:
        await self._redis.aclose()


class SqliteReplayStore:
    """Single-host replay protection for development and single replicas."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS used_confirmations ("
                "jti TEXT PRIMARY KEY, expires_at INTEGER NOT NULL)"
            )

    async def reserve(self, jti: str, ttl_seconds: int) -> bool:
        return await asyncio.to_thread(self._reserve_sync, jti, ttl_seconds)

    def _reserve_sync(self, jti: str, ttl_seconds: int) -> bool:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute("DELETE FROM used_confirmations WHERE expires_at < ?", (now,))
            cursor = connection.execute(
                "INSERT OR IGNORE INTO used_confirmations(jti, expires_at) VALUES (?, ?)",
                (jti, now + max(ttl_seconds, 1)),
            )
            return cursor.rowcount == 1

    async def close(self) -> None:
        return None


def canonical_params_sha256(params: dict[str, Any]) -> str:
    """Stable digest shared by the approval signer and executor."""

    canonical = json.dumps(
        params,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class ConfirmationVerifier:
    """Verify and atomically consume one write approval."""

    def __init__(
        self,
        settings: ConfirmationSettings,
        server_name: str,
        *,
        replay_store: ReplayStore | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not settings.enabled:
            raise ConfirmationError(
                "confirmation_disabled",
                "Write confirmation must remain enabled; disable is unsupported",
            )
        if settings.algorithm == "HS256":
            if not settings.allow_hs256:
                raise ConfirmationError(
                    "confirmation_hs256_disallowed",
                    "MCP_CONFIRMATION_ALLOW_HS256=true is required for HS256",
                )
            if not settings.hs256_secret or len(settings.hs256_secret.get_secret_value()) < 32:
                raise ConfirmationError(
                    "confirmation_hs256_weak",
                    "HS256 confirmation secret must contain at least 32 characters",
                )
        self.settings = settings
        self.server_name = server_name
        self._clock = clock or time.time
        self._public_key: str | None = None
        if settings.algorithm == "RS256":
            path = Path(settings.public_key_path)
            try:
                self._public_key = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ConfirmationError(
                    "confirmation_key_unavailable",
                    "The confirmation verification public key cannot be read",
                    {"path": str(path)},
                ) from exc
        if replay_store is not None:
            self._store = replay_store
        elif settings.redis_url:
            self._store = RedisReplayStore(settings.redis_url)
        else:
            self._store = SqliteReplayStore(settings.sqlite_path)

    async def verify_and_consume(
        self,
        token: str,
        *,
        action: str,
        params: dict[str, Any],
        actor: str,
    ) -> Confirmation:
        confirmation = self.verify(token, action=action, params=params, actor=actor)
        ttl = confirmation.expires_at - int(self._clock())
        if ttl <= 0:
            raise ConfirmationError("confirmation_expired", "The confirmation has expired")
        reserved = await self._store.reserve(confirmation.jti, ttl)
        if not reserved:
            raise ConfirmationError(
                "confirmation_replayed", "The confirmation token was already used"
            )
        return confirmation

    def verify(
        self,
        token: str,
        *,
        action: str,
        params: dict[str, Any],
        actor: str,
    ) -> Confirmation:
        if not isinstance(token, str) or token.count(".") != 2:
            raise ConfirmationError(
                "confirmation_invalid", "A signed confirmation token is required"
            )
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != self.settings.algorithm:
                raise ConfirmationError(
                    "confirmation_algorithm_mismatch",
                    "The confirmation token uses an unexpected algorithm",
                )
            key: str = (
                self.settings.hs256_secret.get_secret_value()
                if self.settings.algorithm == "HS256" and self.settings.hs256_secret
                else (self._public_key or "")
            )
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[self.settings.algorithm],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                leeway=self.settings.leeway_seconds,
                options={
                    "require": [
                        "exp",
                        "iat",
                        "jti",
                        "iss",
                        "aud",
                        "sub",
                        "action",
                        "params_sha256",
                        "actor",
                    ]
                },
            )
        except ConfirmationError:
            raise
        except jwt.PyJWTError as exc:
            raise ConfirmationError(
                "confirmation_invalid",
                "The confirmation token is invalid, expired or has the wrong audience",
            ) from exc
        jti = str(claims.get("jti", ""))
        if not _JTI_RE.fullmatch(jti):
            raise ConfirmationError("confirmation_invalid", "The confirmation jti is invalid")
        if claims.get("action") != action:
            raise ConfirmationError(
                "confirmation_action_mismatch",
                "The confirmation token was issued for a different action",
            )
        expected_digest = canonical_params_sha256(params)
        if claims.get("params_sha256") != expected_digest:
            raise ConfirmationError(
                "confirmation_params_mismatch",
                "The confirmation token was issued for different parameters",
            )
        if claims.get("actor") != actor:
            raise ConfirmationError(
                "confirmation_actor_mismatch",
                "The confirmation token was issued for a different calling application",
            )
        approver = str(claims.get("sub", "")).strip()
        if not approver:
            raise ConfirmationError(
                "confirmation_invalid", "The confirmation token has no approver subject"
            )
        try:
            issued_at = int(claims["iat"])
            expires_at = int(claims["exp"])
        except (TypeError, ValueError) as exc:
            raise ConfirmationError(
                "confirmation_invalid", "The confirmation timestamps are invalid"
            ) from exc
        if expires_at - issued_at > self.settings.max_ttl_seconds:
            raise ConfirmationError(
                "confirmation_ttl_exceeded",
                "The confirmation token lifetime exceeds the configured maximum",
                {"max_ttl_seconds": self.settings.max_ttl_seconds},
            )
        return Confirmation(
            jti=jti,
            approver=approver,
            actor=actor,
            action=action,
            params_sha256=expected_digest,
            expires_at=expires_at,
        )

    async def close(self) -> None:
        await self._store.close()


def mint_confirmation_token(
    *,
    action: str,
    params: dict[str, Any],
    actor: str,
    approver: str,
    server_name: str,
    algorithm: str,
    private_key: str,
    issuer: str,
    audience: str,
    ttl_seconds: int = 300,
    now: int | None = None,
) -> str:
    """Mint a token for tests or a deployment-local approval utility.

    Production change systems should implement the same claims independently.
    """

    issued_at = int(now if now is not None else time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": approver,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": issued_at + ttl_seconds,
        "jti": f"{server_name}:{action}:{secrets.token_urlsafe(24)}",
        "action": action,
        "params_sha256": canonical_params_sha256(params),
        "actor": actor,
    }
    return jwt.encode(claims, private_key, algorithm=algorithm)
