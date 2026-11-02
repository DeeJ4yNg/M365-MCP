from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from m365_mcp.config import AuthSettings, ConfirmationSettings, GraphServiceSettings, LimitSettings
from m365_mcp.errors import M365Error
from m365_mcp.graph_client import CertificateCredential, GraphClient


def _settings(**overrides) -> GraphServiceSettings:
    values = {
        "tenant_id": "tenant",
        "client_id": "client",
        "certificate_path": "/unused.pfx",
        "auth": AuthSettings(mode="none", allow_insecure_dev=True),
        "confirmation": ConfirmationSettings(
            enabled=False,
            algorithm="HS256",
            hs256_secret="test-secret-that-is-at-least-32-characters-long",
            allow_hs256=True,
        ),
        "limits": LimitSettings(max_result_bytes=16_384, max_pages=3),
    }
    values.update(overrides)
    return GraphServiceSettings(**values)


def _credential() -> CertificateCredential:
    return CertificateCredential(
        client_id="client",
        tenant_id="tenant",
        private_key_pem="unused",
        public_certificate_pem="unused",
        thumbprint="ABC",
    )


@pytest.mark.asyncio
@respx.mock
async def test_get_retries_429_and_returns_metadata() -> None:
    route = respx.get("https://graph.microsoft.com/v1.0/users").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}),
            httpx.Response(
                200,
                json={"value": [{"id": "u1"}]},
                headers={"request-id": "req-1"},
            ),
        ]
    )
    client = GraphClient(_settings(), credential=_credential())
    client._access_token = AsyncMock(return_value="token")  # type: ignore[method-assign]
    client._sleep_backoff = AsyncMock()  # type: ignore[method-assign]
    result = await client.request("GET", "/users", params={"$top": 1})
    assert result.data == {"value": [{"id": "u1"}]}
    assert result.request_id == "req-1"
    assert result.status_code == 200
    assert route.call_count == 2
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_write_is_never_retried() -> None:
    route = respx.post("https://graph.microsoft.com/v1.0/users").mock(
        return_value=httpx.Response(503, json={"error": {"code": "busy"}})
    )
    client = GraphClient(_settings(), credential=_credential())
    client._access_token = AsyncMock(return_value="token")  # type: ignore[method-assign]
    with pytest.raises(M365Error) as exc:
        await client.request("POST", "/users", json_body={"displayName": "A"})
    assert exc.value.details["graph_error"] == "busy"
    assert route.call_count == 1
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_pagination_aggregates_same_origin_links() -> None:
    base = "https://graph.microsoft.com/v1.0"

    def page(request: httpx.Request) -> httpx.Response:
        if "skiptoken" in str(request.url):
            return httpx.Response(200, json={"value": [{"id": "u2"}]})
        return httpx.Response(
            200,
            json={
                "value": [{"id": "u1"}],
                "@odata.nextLink": f"{base}/users?$skiptoken=abc",
            },
        )

    respx.get(f"{base}/users").mock(side_effect=page)
    client = GraphClient(_settings(), credential=_credential())
    client._access_token = AsyncMock(return_value="token")  # type: ignore[method-assign]
    result = await client.get_paginated("/users", max_pages=2)
    assert [item["id"] for item in result.data["value"]] == ["u1", "u2"]
    assert result.pages == 2
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_pagination_rejects_foreign_next_link() -> None:
    base = "https://graph.microsoft.com/v1.0"
    respx.get(f"{base}/users").mock(
        return_value=httpx.Response(
            200,
            json={"value": [], "@odata.nextLink": "https://attacker.example/steal"},
        )
    )
    client = GraphClient(_settings(), credential=_credential())
    client._access_token = AsyncMock(return_value="token")  # type: ignore[method-assign]
    with pytest.raises(M365Error, match="unexpected pagination URL"):
        await client.get_paginated("/users", max_pages=2)
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_text_response_and_truncation() -> None:
    response_text = "csv-data" * 3000
    respx.get("https://graph.microsoft.com/v1.0/reports/test").mock(
        return_value=httpx.Response(200, text=response_text, headers={"content-type": "text/csv"})
    )
    client = GraphClient(_settings(), credential=_credential())
    client._access_token = AsyncMock(return_value="token")  # type: ignore[method-assign]
    result = await client.request("GET", "/reports/test", response_mode="text")
    assert result.truncated is True
    assert isinstance(result.data, str)
    assert len(result.data.encode()) <= 16_384
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_query_serialization() -> None:
    route = respx.get("https://graph.microsoft.com/v1.0/users").mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    client = GraphClient(_settings(), credential=_credential())
    client._access_token = AsyncMock(return_value="token")  # type: ignore[method-assign]
    await client.request(
        "GET", "/users", params={"$top": 5, "$count": True, "$select": ["id", "mail"]}
    )
    request = route.calls[0].request
    assert request.url.params["$top"] == "5"
    assert request.url.params["$count"] == "true"
    assert request.url.params["$select"] == "id,mail"
    assert request.headers["ConsistencyLevel"] == "eventual"
    await client.close()


def test_pfx_loading_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(M365Error) as exc:
        CertificateCredential.from_pfx(
            client_id="c",
            tenant_id="t",
            path=str(tmp_path / "missing.pfx"),
            password=None,
        )
    assert exc.value.code == "certificate_unavailable"
