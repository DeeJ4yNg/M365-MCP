from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastmcp import Client

from m365_mcp.confirmation import mint_confirmation_token
from m365_mcp.graph.server import create_app as create_graph_app
from m365_mcp.graph.server import create_runtime as create_graph_runtime
from m365_mcp.graph_client import GraphResult
from m365_mcp.powershell.server import create_runtime as create_powershell_runtime
from tests.helpers import (
    confirmation_settings,
    graph_action,
    graph_settings,
    powershell_action,
    powershell_settings,
    write_catalog_files,
)

HS_SECRET = "test-secret-that-is-at-least-32-characters-long"


class FakeGraphClient:
    async def get_paginated(self, path: str, **kwargs: Any) -> GraphResult:
        return GraphResult(data={"value": [{"id": "u1", "displayName": "Alice"}]}, status_code=200)

    async def request(self, method: str, path: str, **kwargs: Any) -> GraphResult:
        return GraphResult(
            data={"id": "u1", "displayName": kwargs["json_body"]["displayName"]}, status_code=200
        )

    async def close(self) -> None:
        return None


class FakePowerShellExecutor:
    async def execute(
        self, action: Any, params: dict[str, Any], *, correlation_id: str
    ) -> dict[str, Any]:
        return {
            "action": action.name,
            "data": [{"Identity": params["identity"]}],
            "meta": {"service": "exchange"},
        }

    async def close(self) -> None:
        return None


def _graph_fixture(tmp_path):
    read = graph_action()
    write = graph_action(
        name="update_test_user",
        operation="write",
        method="PATCH",
        path="/users/{user_id}",
        body={"displayName": "display_name"},
        properties={
            "user_id": {"type": "string", "description": "User id."},
            "display_name": {"type": "string", "description": "Display name."},
        },
        required=["user_id", "display_name"],
        risk="medium",
    )
    paths = write_catalog_files(
        tmp_path,
        "graph",
        [read, write],
        enabled=[read["name"], write["name"]],
        approved=[read["name"], write["name"]],
    )
    settings = graph_settings(tmp_path, paths).model_copy(
        update={
            "confirmation": confirmation_settings(tmp_path, enabled=True, audience="m365-graph-mcp")
        }
    )
    return settings, paths, read, write


@pytest.mark.asyncio
async def test_in_memory_read_and_write_governance(tmp_path) -> None:
    settings, _, read, write = _graph_fixture(tmp_path)
    runtime = create_graph_runtime(settings, client=FakeGraphClient())
    try:
        async with Client(runtime.mcp) as client:
            tools = await client.list_tools()
            by_name = {tool.name: tool for tool in tools}
            assert set(by_name) == {
                "search_actions",
                "describe_action",
                "execute_read",
                "execute_write",
                "get_tool_inventory",
            }
            assert by_name["execute_read"].annotations.readOnlyHint is True
            assert by_name["execute_write"].annotations.destructiveHint is True
            for tool in tools:
                assert tool.title
                assert tool.annotations.title

            search = await client.call_tool(
                "search_actions", {"intent": "list users", "include_disabled": False}
            )
            assert search.data["count"] >= 1
            assert search.data["actions"][0]["enabled"] is True
            assert search.data["search_mode"] == "lexical"

            read_result = await client.call_tool(
                "execute_read", {"action_id": read["name"], "params": {"limit": 1}}
            )
            assert read_result.data["ok"] is True
            assert read_result.data["data"]["value"][0]["id"] == "u1"

            missing = await client.call_tool(
                "execute_write",
                {
                    "action_id": write["name"],
                    "params": {"user_id": "u1", "display_name": "Alice"},
                },
                raise_on_error=False,
            )
            assert missing.is_error is True
            assert "confirmation_required" in missing.content[0].text

            token = mint_confirmation_token(
                action=write["name"],
                params={"user_id": "u1", "display_name": "Alice"},
                actor="insecure-dev",
                approver="CHG-123",
                server_name="graph",
                algorithm="HS256",
                private_key=HS_SECRET,
                issuer="test-change-system",
                audience="m365-graph-mcp",
            )
            written = await client.call_tool(
                "execute_write",
                {
                    "action_id": write["name"],
                    "params": {"user_id": "u1", "display_name": "Alice"},
                    "confirmation_token": token,
                    "reason": "integration test",
                },
            )
            assert written.data["ok"] is True
            assert written.data["approval"]["approver"] == "CHG-123"

            replay = await client.call_tool(
                "execute_write",
                {
                    "action_id": write["name"],
                    "params": {"user_id": "u1", "display_name": "Alice"},
                    "confirmation_token": token,
                },
                raise_on_error=False,
            )
            assert replay.is_error is True
            assert "confirmation_replayed" in replay.content[0].text
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_health_routes_and_origin_guard(tmp_path) -> None:
    settings, _, _, _ = _graph_fixture(tmp_path)
    runtime = create_graph_runtime(settings, client=FakeGraphClient())
    app = create_graph_app(runtime)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            health = await client.get("/healthz")
            assert health.status_code == 200
            assert health.json()["server"] == "graph"
            assert health.headers["cache-control"] == "no-store"
            assert health.headers["x-content-type-options"] == "nosniff"
            ready = await client.get("/readyz")
            assert ready.status_code == 200
            assert ready.json()["checks"]["writeConfirmation"] is True
            assert ready.json()["checks"]["searchMode"] == "lexical"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_powershell_server_uses_same_governance(tmp_path) -> None:
    action = powershell_action()
    paths = write_catalog_files(
        tmp_path,
        "powershell",
        [action],
        enabled=[action["name"]],
        approved=[action["name"]],
    )
    settings = powershell_settings(tmp_path, paths).model_copy(
        update={"confirmation": confirmation_settings(tmp_path, enabled=False)}
    )
    runtime = create_powershell_runtime(settings, executor=FakePowerShellExecutor())
    try:
        async with Client(runtime.mcp) as client:
            result = await client.call_tool(
                "execute_read",
                {"action_id": action["name"], "params": {"identity": "alice@contoso.com"}},
            )
            assert result.data["ok"] is True
            assert result.data["data"][0]["Identity"] == "alice@contoso.com"
    finally:
        await runtime.close()
