"""Declarative Graph action executor."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from m365_mcp.catalog import ActionCatalog
from m365_mcp.config import GraphServiceSettings
from m365_mcp.errors import CatalogError
from m365_mcp.graph_client import GraphClient, GraphResult
from m365_mcp.models import ActionDefinition, GraphRequestSpec


class GraphExecutor:
    def __init__(
        self,
        settings: GraphServiceSettings,
        catalog: ActionCatalog,
        client: GraphClient,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.client = client

    async def close(self) -> None:
        await self.client.close()

    async def execute(
        self,
        action: ActionDefinition,
        params: dict[str, Any],
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        if action.graph is None:
            raise CatalogError("action_not_implemented", f"{action.name} has no Graph request")
        request_spec = action.graph
        path = self._render_path(request_spec, params)
        query = {
            query_name: params[source]
            for query_name, source in request_spec.query.items()
            if source in params
        }
        body = self._build_body(request_spec, params)
        if request_spec.method == "GET":
            result = await self.client.get_paginated(
                path,
                params=query,
                max_pages=self.settings.limits.max_pages,
                response_mode=request_spec.response,
                correlation_id=correlation_id,
            )
        else:
            result = await self.client.request(
                request_spec.method,
                path,
                params=query or None,
                json_body=body,
                response_mode=request_spec.response,
                correlation_id=correlation_id,
            )
        return self._bounded_result(action, result)

    @staticmethod
    def _render_path(request: GraphRequestSpec, params: dict[str, Any]) -> str:
        path = request.path
        for name, value in params.items():
            placeholder = "{" + name + "}"
            if placeholder not in path:
                continue
            if isinstance(value, (dict, list)) or value is None:
                raise CatalogError(
                    "invalid_path_parameter",
                    f"Path parameter {name} must be a non-null scalar",
                )
            path = path.replace(placeholder, quote(str(value), safe=""))
        if "{" in path or "}" in path:
            raise CatalogError(
                "invalid_path_parameter", "Not all Graph path placeholders were resolved"
            )
        return path

    @staticmethod
    def _build_body(request: GraphRequestSpec, params: dict[str, Any]) -> Any:
        if request.body == {"payload": "payload"}:
            return params.get("payload", {})
        if request.body_const:
            body = GraphExecutor._substitute_constants(request.body_const, params)
            if not isinstance(body, dict):
                raise CatalogError("invalid_body_constant", "body_const must resolve to an object")
            for field_name, source in request.body.items():
                if source in params:
                    body[field_name] = params[source]
            return body or None
        if request.body:
            return {
                field_name: params[source]
                for field_name, source in request.body.items()
                if source in params
            }
        return None

    @classmethod
    def _substitute_constants(cls, value: Any, params: dict[str, Any]) -> Any:
        if isinstance(value, str):

            def replace(match: re.Match[str]) -> str:
                return str(params[match.group(1)])

            return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)
        if isinstance(value, dict):
            return {key: cls._substitute_constants(item, params) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._substitute_constants(item, params) for item in value]
        return value

    def _bounded_result(self, action: ActionDefinition, result: GraphResult) -> dict[str, Any]:
        payload = {
            "action": action.name,
            "data": result.data,
            "meta": {
                "status_code": result.status_code,
                "request_id": result.request_id,
                "pages": result.pages,
                "truncated": result.truncated,
                "next_link": result.next_link,
                "content_type": result.content_type,
            },
        }
        try:
            encoded = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise CatalogError(
                "unserializable_response", "Graph response could not be serialized"
            ) from exc
        if len(encoded) > self.settings.limits.max_result_bytes:
            if action.operation_type == "write":
                return {
                    "action": action.name,
                    "data": {
                        "completed": True,
                        "response_omitted": True,
                        "note": "Write completed but its response exceeded the output limit.",
                    },
                    "meta": {
                        "status_code": result.status_code,
                        "request_id": result.request_id,
                        "truncated": True,
                    },
                }
            raise CatalogError(
                "result_too_large",
                f"{action.name} returned {len(encoded)} bytes, exceeding the server limit. "
                "Narrow the filter, select fewer fields, or reduce the requested page size.",
                {
                    "returned_bytes": len(encoded),
                    "limit_bytes": self.settings.limits.max_result_bytes,
                },
            )
        return payload
