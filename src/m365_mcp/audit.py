"""Structured audit logging without tenant payloads or secrets."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

_AUDIT_LOGGER = logging.getLogger("m365_mcp.audit")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "audit_event", None)
        if isinstance(event, dict):
            base.update(event)
        if record.exc_info:
            base["exception"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("msal").setLevel(logging.WARNING)


@dataclass(slots=True)
class AuditSpan:
    """One auditable MCP tool invocation."""

    action: str
    operation: str
    risk: str
    actor: str
    correlation_id: str
    server: str
    started: float = field(default_factory=time.monotonic)
    fields: dict[str, Any] = field(default_factory=dict)

    def add(self, **values: Any) -> None:
        self.fields.update(values)

    def emit(self, outcome: str, *, error_code: str | None = None) -> None:
        event: dict[str, Any] = {
            "event": "mcp_tool_execution",
            "server": self.server,
            "action": self.action,
            "operation": self.operation,
            "risk": self.risk,
            "actor": self.actor,
            "correlation_id": self.correlation_id,
            "outcome": outcome,
            "duration_ms": round((time.monotonic() - self.started) * 1000, 1),
            **self.fields,
        }
        if error_code:
            event["error_code"] = error_code
        level = logging.INFO if outcome == "success" else logging.WARNING
        _AUDIT_LOGGER.log(level, "MCP tool execution", extra={"audit_event": event})


@contextmanager
def audit_span(
    *,
    server: str,
    action: str,
    operation: str,
    risk: str,
    actor: str,
    correlation_id: str,
) -> Iterator[AuditSpan]:
    span = AuditSpan(
        action=action,
        operation=operation,
        risk=risk,
        actor=actor,
        correlation_id=correlation_id,
        server=server,
    )
    try:
        yield span
    except Exception as exc:
        code = getattr(exc, "code", "unexpected_error")
        span.emit("error", error_code=str(code))
        raise
    else:
        span.emit("success")
