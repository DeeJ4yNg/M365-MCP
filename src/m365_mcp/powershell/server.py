"""PowerShell MCP Streamable HTTP server."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn

from m365_mcp.catalog import ActionCatalog
from m365_mcp.config import PowerShellServiceSettings, load_settings
from m365_mcp.powershell.executor import PowerShellExecutor
from m365_mcp.server_common import (
    SensitiveResponseMiddleware,
    ServerRuntime,
    assert_write_startup_safe,
    build_runtime,
    configure_server_logging,
    create_confirmation,
)

logger = logging.getLogger("m365_mcp.powershell")


def create_runtime(
    settings: PowerShellServiceSettings | None = None,
    *,
    executor: Any | None = None,
) -> ServerRuntime:
    """Build the PowerShell server with optional injected executor for tests."""

    settings = settings or load_settings("powershell")  # type: ignore[assignment]
    if not isinstance(settings, PowerShellServiceSettings):
        raise TypeError("create_runtime requires PowerShellServiceSettings")
    configure_server_logging(settings)
    catalog = ActionCatalog(
        "powershell",
        settings.catalog_path,
        settings.enabled_tools_path,
        settings.review_registry_path,
    )
    action_executor = executor or PowerShellExecutor(settings, catalog)
    confirmation, confirmation_error = create_confirmation(settings)

    @asynccontextmanager
    async def lifespan(server: Any):
        try:
            await runtime.startup()
            yield {}
        finally:
            await runtime.close()

    runtime = build_runtime(
        settings,
        catalog=catalog,
        executor=action_executor,
        confirmation=confirmation,
        confirmation_error=confirmation_error,
        lifespan=lifespan,
    )
    assert_write_startup_safe(runtime)
    return runtime


def create_app(runtime: ServerRuntime):
    if runtime.mcp is None:
        raise RuntimeError("runtime has no MCP server")
    return SensitiveResponseMiddleware(
        runtime.mcp.http_app(
            path="/mcp",
            stateless_http=True,
            allowed_hosts=runtime.settings.allowed_hosts or None,
            allowed_origins=runtime.settings.allowed_origins or None,
        )
    )


def main() -> None:
    runtime = create_runtime()
    settings = runtime.settings
    app = create_app(runtime)
    logger.info(
        "Starting Microsoft 365 PowerShell MCP server",
        extra={
            "audit_event": {
                "event": "server_start",
                "server": "powershell",
                "host": settings.host,
                "port": settings.port,
                "actions": len(runtime.catalog.actions),
                "enabled": len(runtime.catalog.policy.enabled),
            }
        },
    )
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
        server_header=False,
    )


if __name__ == "__main__":
    main()
