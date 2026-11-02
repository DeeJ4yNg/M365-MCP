"""Typed exceptions shared by both MCP servers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class M365Error(Exception):
    """A sanitized, actionable error returned to an MCP client."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def to_public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(slots=True)
class CatalogError(M365Error):
    """Invalid catalog or policy configuration."""


@dataclass(slots=True)
class AuthorizationError(M365Error):
    """Caller does not have the required role or client identity."""


@dataclass(slots=True)
class ActionNotFoundError(M365Error):
    """Unknown or non-exposed action identifier."""
