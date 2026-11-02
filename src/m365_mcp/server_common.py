"""Shared FastMCP registration for the Graph and PowerShell servers."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

from m365_mcp import __version__
from m365_mcp.audit import audit_span, configure_logging
from m365_mcp.auth import current_caller
from m365_mcp.catalog import ActionCatalog
from m365_mcp.config import BaseServiceSettings
from m365_mcp.confirmation import ConfirmationError, ConfirmationVerifier
from m365_mcp.errors import M365Error
from m365_mcp.search_service import HybridSearchService

logger = logging.getLogger("m365_mcp.server")

READ_INSTRUCTIONS = (
    "This server exposes governed Microsoft 365 operations. "
    "Use search_actions to discover an action identifier and its exact input schema. "
    "execute_read performs read-only actions. execute_write requires a separately "
    "issued single-use confirmation token bound to the action and parameters. "
    "Actions not listed by search_actions are not available."
)


class SensitiveResponseMiddleware:
    """Add no-store and defensive browser headers to every HTTP response."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Any) -> None:
            if message.get("type") == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"cache-control"
                ]
                headers.extend(
                    [
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class ActionExecutor(Protocol):
    async def execute(
        self, action: Any, params: dict[str, Any], *, correlation_id: str
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class ServerRuntime:
    settings: BaseServiceSettings
    catalog: ActionCatalog
    executor: ActionExecutor
    searcher: HybridSearchService
    confirmation: ConfirmationVerifier | None
    confirmation_error: str | None = None
    mcp: FastMCP | None = None
    extra_closers: list[Any] = field(default_factory=list)

    async def startup(self) -> None:
        await self.searcher.initialize()

    async def close(self) -> None:
        await self.searcher.close()
        if self.confirmation is not None:
            await self.confirmation.close()
        close = getattr(self.executor, "close", None)
        if close is not None:
            await close()
        for closer in self.extra_closers:
            await closer()


def build_runtime(
    settings: BaseServiceSettings,
    *,
    catalog: ActionCatalog,
    executor: ActionExecutor,
    searcher: HybridSearchService | None = None,
    confirmation: ConfirmationVerifier | None = None,
    confirmation_error: str | None = None,
    lifespan: Any = None,
) -> ServerRuntime:
    runtime = ServerRuntime(
        settings=settings,
        catalog=catalog,
        executor=executor,
        searcher=searcher or HybridSearchService(catalog, settings.search),
        confirmation=confirmation,
        confirmation_error=confirmation_error,
    )
    mcp = FastMCP(
        name=f"m365-{settings.server_name}-mcp",
        version=__version__,
        instructions=READ_INSTRUCTIONS,
        auth=_auth_provider(settings),
        mask_error_details=True,
        lifespan=lifespan,
    )
    _register_tools(mcp, runtime)
    _register_health_routes(mcp, runtime)
    runtime.mcp = mcp
    return runtime


def create_confirmation(
    settings: BaseServiceSettings,
) -> tuple[ConfirmationVerifier | None, str | None]:
    """Instantiate write approval verification without making read-only startup fail."""

    if not settings.confirmation.enabled:
        return None, "confirmation_disabled"
    try:
        return ConfirmationVerifier(settings.confirmation, settings.server_name), None
    except M365Error as exc:
        logger.warning(
            "Write confirmation is not ready; write actions will fail closed",
            extra={"audit_event": {"error_code": exc.code, "server": settings.server_name}},
        )
        return None, exc.code


def assert_write_startup_safe(runtime: ServerRuntime) -> None:
    enabled = runtime.catalog.policy.enabled
    write_enabled = [
        name for name in enabled if runtime.catalog.actions[name].operation_type == "write"
    ]
    if write_enabled and runtime.confirmation is None:
        raise M365Error(
            "confirmation_not_configured",
            "Write actions are enabled but confirmation verification is unavailable",
            {"enabled_write_actions": sorted(write_enabled)},
        )


def _auth_provider(settings: BaseServiceSettings) -> Any:
    from m365_mcp.auth import build_auth_provider

    return build_auth_provider(settings.auth)


def _tool_error(exc: Exception, correlation_id: str) -> ToolError:
    if isinstance(exc, M365Error):
        payload = exc.to_public_dict()
    elif isinstance(exc, PermissionError):
        payload = {
            "code": "action_disabled",
            "message": str(exc),
            "retryable": False,
        }
    else:
        logger.exception("Unexpected MCP tool failure", extra={"correlation_id": correlation_id})
        payload = {
            "code": "internal_error",
            "message": "The MCP server could not complete the action",
            "retryable": False,
        }
    payload["correlation_id"] = correlation_id
    return ToolError(json.dumps(payload, ensure_ascii=False))


def _register_tools(mcp: FastMCP, runtime: ServerRuntime) -> None:
    settings = runtime.settings
    catalog = runtime.catalog

    @mcp.tool(
        name="search_actions",
        title="Search Microsoft 365 actions",
        description=(
            "Find Microsoft 365 business actions by intent, domain, or operation type. "
            "Results include the exact parameter schema and review/availability state. "
            "Returns only enabled actions unless include_disabled is true."
        ),
        annotations={
            "title": "Search Microsoft 365 actions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def search_actions(
        intent: str,
        limit: int = 10,
        domain: str | None = None,
        operation_type: Literal["read", "write"] | None = None,
        include_disabled: bool = False,
    ) -> dict[str, Any]:
        """Search the reviewed M365 action catalog.

        Args:
            intent: Natural-language description of the intended operation.
            limit: Maximum number of actions to return, from 1 to 20.
            domain: Optional domain filter such as Entra ID, Intune, Exchange, Teams or 终端.
            operation_type: Optional read or write filter.
            include_disabled: Include actions awaiting review or absent from enabledTools.
        """

        correlation_id = str(uuid.uuid4())
        try:
            current_caller(settings.auth, write=False)
            bounded_limit = max(1, min(limit, 20))
            outcome = await runtime.searcher.search(
                intent,
                limit=bounded_limit,
                domain=domain,
                operation_type=operation_type,
                include_disabled=include_disabled,
            )
            result: dict[str, Any] = {
                "actions": outcome.actions,
                "count": len(outcome.actions),
                "enabled_actions": len(catalog.policy.enabled),
                "search_mode": outcome.mode,
                "semantic_ready": outcome.semantic_ready,
                "correlation_id": correlation_id,
                "hint": (
                    "No enabled actions matched the intent. Refine the query, "
                    "add a domain filter, or inspect the inventory."
                    if not outcome.actions and not include_disabled
                    else "Refine the intent or inspect the returned candidates."
                ),
            }
            if outcome.warning:
                result["warning"] = outcome.warning
            return result
        except Exception as exc:
            raise _tool_error(exc, correlation_id) from exc

    @mcp.tool(
        name="describe_action",
        title="Describe a Microsoft 365 action",
        description=(
            "Return complete inventory metadata and input schema for one action ID, "
            "including review, implementation and enabled state. This does not execute it."
        ),
        annotations={
            "title": "Describe a Microsoft 365 action",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def describe_action(action_id: str) -> dict[str, Any]:
        """Return full metadata for one action.

        Args:
            action_id: Stable action name returned by search_actions.
        """

        correlation_id = str(uuid.uuid4())
        try:
            current_caller(settings.auth, write=False)
            return {
                **catalog.describe(action_id),
                "correlation_id": correlation_id,
            }
        except Exception as exc:
            raise _tool_error(exc, correlation_id) from exc

    @mcp.tool(
        name="execute_read",
        title="Execute a read-only Microsoft 365 action",
        description=(
            "Execute one enabled read-only action with schema-validated parameters. "
            "Returns upstream data plus correlation and pagination metadata."
        ),
        annotations={
            "title": "Execute a read-only Microsoft 365 action",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def execute_read(
        action_id: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a read-only action.

        Args:
            action_id: Read action name from search_actions.
            params: Parameters matching the action's input_schema.
        """

        correlation_id = str(uuid.uuid4())
        values = params or {}
        try:
            caller = current_caller(settings.auth, write=False)
            action = catalog.require_action(action_id, operation="read")
            catalog.validate_params(action, values)
        except Exception as exc:
            raise _tool_error(exc, correlation_id) from exc
        try:
            with audit_span(
                server=settings.server_name,
                action=action.name,
                operation="read",
                risk=action.risk_level,
                actor=caller.actor,
                correlation_id=correlation_id,
            ):
                result = await runtime.executor.execute(
                    action, values, correlation_id=correlation_id
                )
                return {
                    "ok": True,
                    "correlation_id": correlation_id,
                    **result,
                }
        except Exception as exc:
            raise _tool_error(exc, correlation_id) from exc

    @mcp.tool(
        name="execute_write",
        title="Execute a confirmed Microsoft 365 write action",
        description=(
            "Execute one enabled write action after verifying a single-use approval token "
            "bound to the exact action ID and canonical parameter digest. "
            "The token is consumed before the upstream call and cannot be retried."
        ),
        annotations={
            "title": "Execute a confirmed Microsoft 365 write action",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def execute_write(
        action_id: str,
        params: dict[str, Any] | None = None,
        confirmation_token: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Execute a reviewed write action with an approval token.

        Args:
            action_id: Write action name from search_actions.
            params: Parameters matching the action's input_schema.
            confirmation_token: Signed JWT issued by the change approval system.
            reason: Short business reason recorded in the audit log.
        """

        correlation_id = str(uuid.uuid4())
        values = params or {}
        try:
            caller = current_caller(settings.auth, write=True)
            action = catalog.require_action(action_id, operation="write")
            catalog.validate_params(action, values)
            if runtime.confirmation is None:
                raise ConfirmationError(
                    "confirmation_unavailable",
                    "Write confirmation verification is not configured on this server",
                )
            if not confirmation_token:
                raise ConfirmationError(
                    "confirmation_required", "A confirmation_token is required for writes"
                )
        except Exception as exc:
            raise _tool_error(exc, correlation_id) from exc
        try:
            with audit_span(
                server=settings.server_name,
                action=action.name,
                operation="write",
                risk=action.risk_level,
                actor=caller.actor,
                correlation_id=correlation_id,
            ) as span:
                if reason:
                    span.add(reason=reason[:500])
                confirmation = await runtime.confirmation.verify_and_consume(
                    confirmation_token,
                    action=action.name,
                    params=values,
                    actor=caller.actor,
                )
                span.add(approver=confirmation.approver)
                result = await runtime.executor.execute(
                    action, values, correlation_id=correlation_id
                )
                return {
                    "ok": True,
                    "correlation_id": correlation_id,
                    "approval": {
                        "approver": confirmation.approver,
                        "single_use": True,
                    },
                    **result,
                }
        except Exception as exc:
            raise _tool_error(exc, correlation_id) from exc

    @mcp.tool(
        name="get_tool_inventory",
        title="Get the Microsoft 365 tool inventory",
        description=(
            "Return the server's complete action inventory, including implementation, "
            "review, risk, permissions and enabled state. Read-only and bounded by limit."
        ),
        annotations={
            "title": "Get the Microsoft 365 tool inventory",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_tool_inventory(
        limit: int = 50,
        include_disabled: bool = True,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded inventory snapshot.

        Args:
            limit: Maximum inventory records, from 1 to 200.
            include_disabled: Include unreviewed or disabled actions.
            domain: Optional domain filter such as Entra ID, Intune or Exchange.
        """

        correlation_id = str(uuid.uuid4())
        try:
            current_caller(settings.auth, write=False)
            bounded_limit = max(1, min(limit, 200))
            actions = catalog.inventory(include_disabled=include_disabled)
            if domain:
                actions = [
                    action for action in actions if catalog.domain_matches(action["domain"], domain)
                ]
            by_status: dict[str, int] = {}
            for action in actions:
                by_status[action["implementation_status"]] = (
                    by_status.get(action["implementation_status"], 0) + 1
                )
            return {
                "server": settings.server_name,
                "version": __version__,
                "total_matching": len(actions),
                "returned": min(len(actions), bounded_limit),
                "status_counts": by_status,
                "correlation_id": correlation_id,
                "actions": actions[:bounded_limit],
            }
        except Exception as exc:
            raise _tool_error(exc, correlation_id) from exc


def _register_health_routes(mcp: FastMCP, runtime: ServerRuntime) -> None:
    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": runtime.settings.server_name})

    @mcp.custom_route("/readyz", methods=["GET"])
    async def readyz(request: Request) -> JSONResponse:
        checks: dict[str, Any] = {
            "catalog": False,
            "enabledTools": False,
            "reviewRegistry": False,
            "writeConfirmation": runtime.confirmation is not None,
        }
        try:
            enabled = runtime.catalog.policy.refresh(force=True)
            runtime.catalog.reviews.refresh(force=True)
            checks["catalog"] = len(runtime.catalog.actions) > 0
            checks["enabledTools"] = runtime.catalog.policy.path.is_file()
            checks["reviewRegistry"] = runtime.catalog.reviews.path.is_file()
            checks["enabled_count"] = len(enabled)
            checks["write_ready"] = runtime.confirmation is not None
            if runtime.confirmation_error:
                checks["write_error"] = runtime.confirmation_error
        except M365Error as exc:
            checks["enabledToolsError"] = exc.code

        search_status = runtime.searcher.status()
        semantic_available = search_status.semantic_ready
        if search_status.configured_mode == "hybrid":
            try:
                semantic_available = semantic_available and await runtime.searcher.probe()
            except Exception:
                semantic_available = False
        if search_status.configured_mode == "hybrid" and not semantic_available:
            search_mode = "lexical_fallback" if search_status.fallback_enabled else "unavailable"
            search_available = search_status.fallback_enabled
        else:
            search_mode = "hybrid" if semantic_available else search_status.effective_mode
            search_available = (
                runtime.searcher.search_available
                if search_status.effective_mode != "hybrid"
                else semantic_available
            )
        checks["searchConfiguredMode"] = search_status.configured_mode
        checks["searchMode"] = search_mode
        checks["semanticReady"] = semantic_available
        checks["semanticActions"] = search_status.action_vectors
        checks["semanticDimension"] = search_status.dimension
        checks["searchLexiconVersion"] = search_status.lexicon_version
        checks["searchAvailable"] = search_available
        if search_status.error:
            checks["searchError"] = search_status.error
        if search_status.warning:
            checks["searchWarning"] = search_status.warning

        ready = bool(
            checks["catalog"]
            and checks["enabledTools"]
            and checks["reviewRegistry"]
            and checks["searchAvailable"]
        )
        response_status = "ready" if ready else "not_ready"
        if ready and search_status.configured_mode == "hybrid" and not semantic_available:
            response_status = "degraded"
        return JSONResponse(
            {
                "status": response_status,
                "server": runtime.settings.server_name,
                "version": __version__,
                "checks": checks,
            },
            status_code=200 if ready else 503,
        )


def configure_server_logging(settings: BaseServiceSettings) -> None:
    configure_logging(settings.log_level)
