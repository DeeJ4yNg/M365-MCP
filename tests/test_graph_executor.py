from __future__ import annotations

from typing import Any

import pytest

from m365_mcp.config import AuthSettings, ConfirmationSettings, GraphServiceSettings, LimitSettings
from m365_mcp.errors import CatalogError
from m365_mcp.graph.executor import GraphExecutor
from m365_mcp.graph_client import GraphResult
from tests.helpers import graph_action, make_catalog


class FakeGraphClient:
    def __init__(self, result: GraphResult | None = None) -> None:
        self.result = result or GraphResult(data={"value": []}, status_code=200)
        self.calls: list[tuple[str, Any]] = []

    async def get_paginated(self, path: str, **kwargs: Any) -> GraphResult:
        self.calls.append((f"GET {path}", kwargs))
        return self.result

    async def request(self, method: str, path: str, **kwargs: Any) -> GraphResult:
        self.calls.append((f"{method} {path}", kwargs))
        return self.result

    async def close(self) -> None:
        return None


def _settings(tmp_path) -> GraphServiceSettings:
    return GraphServiceSettings(
        tenant_id="tenant",
        client_id="client",
        certificate_path="/unused",
        catalog_path=str(tmp_path / "catalog.json"),
        enabled_tools_path=str(tmp_path / "enabled.json"),
        review_registry_path=str(tmp_path / "reviews.json"),
        auth=AuthSettings(mode="none", allow_insecure_dev=True),
        confirmation=ConfirmationSettings(
            enabled=False,
            algorithm="HS256",
            hs256_secret="test-secret-that-is-at-least-32-characters-long",
            allow_hs256=True,
        ),
        limits=LimitSettings(max_result_bytes=16_384, max_pages=3),
    )


@pytest.mark.asyncio
async def test_read_maps_path_query_and_hides_request_spec(tmp_path) -> None:
    action = graph_action(
        properties={
            "user_id": {"type": "string", "description": "User id."},
            "limit": {"type": "integer", "minimum": 1, "description": "Page size."},
        },
        required=["user_id"],
        path="/users/{user_id}/messages",
        query={"$top": "limit"},
    )
    catalog = make_catalog(
        tmp_path, "graph", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    client = FakeGraphClient()
    executor = GraphExecutor(_settings(tmp_path), catalog, client)  # type: ignore[arg-type]
    result = await executor.execute(
        catalog.actions[action["name"]],
        {"user_id": "a/b", "limit": 5},
        correlation_id="corr-1",
    )
    path, kwargs = client.calls[0]
    assert path == "GET /users/a%2Fb/messages"
    assert kwargs["params"] == {"$top": 5}
    assert result["data"] == {"value": []}
    assert "graph" not in result


@pytest.mark.asyncio
async def test_write_maps_body_and_constants(tmp_path) -> None:
    action = graph_action(
        name="update_test_user",
        operation="write",
        method="PATCH",
        path="/users/{user_id}",
        body={"displayName": "display_name", "@odata.type": "odata_type"},
        body_const={"accountEnabled": True},
        properties={
            "user_id": {"type": "string", "description": "User id."},
            "display_name": {"type": "string", "description": "Display name."},
            "odata_type": {"type": "string", "description": "OData type."},
        },
        required=["user_id", "display_name", "odata_type"],
    )
    catalog = make_catalog(
        tmp_path, "graph", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    client = FakeGraphClient()
    executor = GraphExecutor(_settings(tmp_path), catalog, client)  # type: ignore[arg-type]
    await executor.execute(
        catalog.actions[action["name"]],
        {"user_id": "u1", "display_name": "Alice", "odata_type": "#microsoft.graph.user"},
        correlation_id="corr-2",
    )
    _, kwargs = client.calls[0]
    # Mapped fields are layered over constant defaults.
    assert kwargs["json_body"]["accountEnabled"] is True
    assert kwargs["json_body"]["displayName"] == "Alice"


@pytest.mark.asyncio
async def test_write_response_too_large_is_still_a_success(tmp_path) -> None:
    action = graph_action(
        name="update_test_user",
        operation="write",
        method="PATCH",
        path="/users/{user_id}",
        properties={"user_id": {"type": "string", "description": "User id."}},
        required=["user_id"],
    )
    catalog = make_catalog(
        tmp_path, "graph", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    client = FakeGraphClient(GraphResult(data={"blob": "x" * 20_000}, status_code=200))
    executor = GraphExecutor(_settings(tmp_path), catalog, client)  # type: ignore[arg-type]
    result = await executor.execute(
        catalog.actions[action["name"]], {"user_id": "u1"}, correlation_id="corr-3"
    )
    assert result["data"]["completed"] is True
    assert result["data"]["response_omitted"] is True


@pytest.mark.asyncio
async def test_read_response_too_large_requires_narrowing(tmp_path) -> None:
    action = graph_action()
    catalog = make_catalog(
        tmp_path, "graph", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    client = FakeGraphClient(GraphResult(data={"blob": "x" * 20_000}, status_code=200))
    executor = GraphExecutor(_settings(tmp_path), catalog, client)  # type: ignore[arg-type]
    with pytest.raises(CatalogError) as exc:
        await executor.execute(
            catalog.actions[action["name"]], {"limit": 1}, correlation_id="corr-4"
        )
    assert exc.value.code == "result_too_large"


@pytest.mark.asyncio
async def test_body_const_placeholder_is_substituted(tmp_path) -> None:
    action = graph_action(
        name="add_group_member_test",
        operation="write",
        method="POST",
        path="/groups/{group_id}/members/$ref",
        body_const={"@odata.id": "https://graph.microsoft.com/v1.0/directoryObjects/{member_id}"},
        properties={
            "group_id": {"type": "string", "description": "Group id."},
            "member_id": {"type": "string", "description": "Member id."},
        },
        required=["group_id", "member_id"],
    )
    catalog = make_catalog(
        tmp_path, "graph", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    client = FakeGraphClient(GraphResult(data=None, status_code=204))
    executor = GraphExecutor(_settings(tmp_path), catalog, client)  # type: ignore[arg-type]
    await executor.execute(
        catalog.actions[action["name"]],
        {"group_id": "g1", "member_id": "u1"},
        correlation_id="corr-body",
    )
    _, kwargs = client.calls[0]
    assert kwargs["json_body"] == {
        "@odata.id": "https://graph.microsoft.com/v1.0/directoryObjects/u1"
    }


@pytest.mark.asyncio
async def test_payload_body_is_passed_through(tmp_path) -> None:
    action = graph_action(
        name="create_test_policy",
        operation="write",
        method="POST",
        path="/deviceManagement/configurationPolicies",
        body={"payload": "payload"},
        properties={"payload": {"type": "object", "description": "Graph payload."}},
        required=["payload"],
        risk="medium",
    )
    catalog = make_catalog(
        tmp_path, "graph", [action], enabled=[action["name"]], approved=[action["name"]]
    )
    client = FakeGraphClient(GraphResult(data={"id": "p1"}, status_code=201))
    executor = GraphExecutor(_settings(tmp_path), catalog, client)  # type: ignore[arg-type]
    await executor.execute(
        catalog.actions[action["name"]],
        {"payload": {"name": "Baseline"}},
        correlation_id="corr-5",
    )
    _, kwargs = client.calls[0]
    assert kwargs["json_body"] == {"name": "Baseline"}
