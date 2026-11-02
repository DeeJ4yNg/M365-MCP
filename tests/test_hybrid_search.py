from __future__ import annotations

import json
from collections.abc import Sequence

import httpx
import pytest
import respx
from fastmcp import Client
from pydantic import SecretStr, ValidationError

from m365_mcp.catalog import ActionCatalog
from m365_mcp.config import SearchSettings
from m365_mcp.search_service import (
    EmbeddingError,
    HybridSearchService,
    OpenAIEmbeddingProvider,
)
from m365_mcp.server_common import build_runtime
from tests.helpers import graph_action, graph_settings, make_catalog, write_catalog_files


class FakeProvider:
    def __init__(
        self,
        *,
        query_vector: list[float],
        action_vectors: dict[str, list[float]],
    ) -> None:
        self.query_vector = query_vector
        self.action_vectors = action_vectors
        self.embed_calls = 0
        self.closed = False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.embed_calls += 1
        vectors: list[list[float]] = []
        for text in texts:
            if "Action:" not in text:
                vectors.append(self.query_vector)
                continue
            marker = next(
                (name for name in self.action_vectors if f"Action: {name}" in text),
                None,
            )
            if marker is None:
                vectors.append([0.0, 1.0])
            else:
                vectors.append(self.action_vectors[marker])
        return vectors

    async def probe(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


class FailingProvider:
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingError("embedding_unavailable", "offline")

    async def probe(self) -> bool:
        return False

    async def close(self) -> None:
        return None


def _hybrid_settings(*, fallback: bool = False) -> SearchSettings:
    return SearchSettings(
        mode="hybrid",
        embedding_url="http://embedding.test/v1/embeddings",
        embedding_model="fake-embedding",
        embedding_dimension=2,
        semantic_weight=0.9,
        lexical_weight=0.1,
        semantic_floor=0.0,
        semantic_ceiling=1.0,
        min_score=0.05,
        fallback_to_lexical=fallback,
    )


def _catalog(tmp_path):
    lexical = graph_action()
    semantic = graph_action(
        name="reset_test_device",
        operation="write",
        method="POST",
        path="/devices/{device_id}/reset",
        properties={"device_id": {"type": "string", "description": "Device ID."}},
        required=["device_id"],
        risk="high",
    )
    return make_catalog(
        tmp_path,
        "graph",
        [lexical, semantic],
        enabled=[lexical["name"], semantic["name"]],
        approved=[lexical["name"], semantic["name"]],
    )


@pytest.mark.asyncio
async def test_semantic_signal_can_rerank_lexical_candidate(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    provider = FakeProvider(
        query_vector=[1.0, 0.0],
        action_vectors={
            "list_test_users": [0.0, 1.0],
            "reset_test_device": [1.0, 0.0],
        },
    )
    service = HybridSearchService(catalog, _hybrid_settings(), provider=provider)
    await service.initialize()
    result = await service.search("list users", limit=5)
    assert result.mode == "hybrid"
    assert result.semantic_ready is True
    assert result.actions[0]["name"] == "reset_test_device"
    assert result.actions[1]["name"] == "list_test_users"
    assert len(service.status().model) > 0
    assert service.status().action_vectors == 2


@pytest.mark.asyncio
async def test_query_cache_and_no_match_threshold(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    provider = FakeProvider(
        query_vector=[1.0, 0.0],
        action_vectors={
            "list_test_users": [0.0, 1.0],
            "reset_test_device": [0.0, 1.0],
        },
    )
    service = HybridSearchService(catalog, _hybrid_settings(), provider=provider)
    await service.initialize()
    await service.search("list users")
    calls_after_first = provider.embed_calls
    await service.search("list users")
    assert provider.embed_calls == calls_after_first

    no_match = await service.search("zzzz nonsense")
    assert no_match.actions == []
    await service.close()
    assert provider.closed is True


@pytest.mark.asyncio
async def test_search_tool_exposes_hybrid_mode(tmp_path) -> None:
    actions = [
        graph_action(),
        graph_action(
            name="reset_test_device",
            operation="write",
            method="POST",
            path="/devices/{device_id}/reset",
            properties={"device_id": {"type": "string", "description": "Device ID."}},
            required=["device_id"],
            risk="high",
        ),
    ]
    paths = write_catalog_files(
        tmp_path,
        "graph",
        actions,
        enabled=[item["name"] for item in actions],
        approved=[item["name"] for item in actions],
    )
    settings = graph_settings(tmp_path, paths)
    catalog = ActionCatalog(
        "graph",
        str(paths["catalog"]),
        str(paths["enabled"]),
        str(paths["review"]),
    )

    class NoopExecutor:
        async def execute(self, action, params, *, correlation_id):
            return {}

        async def close(self):
            return None

    provider = FakeProvider(
        query_vector=[1.0, 0.0],
        action_vectors={
            "list_test_users": [0.0, 1.0],
            "reset_test_device": [1.0, 0.0],
        },
    )
    searcher = HybridSearchService(catalog, _hybrid_settings(), provider=provider)
    await searcher.initialize()
    runtime = build_runtime(
        settings,
        catalog=catalog,
        executor=NoopExecutor(),
        searcher=searcher,
    )
    try:
        async with Client(runtime.mcp) as client:
            result = await client.call_tool("search_actions", {"intent": "list users", "limit": 5})
            assert result.data["search_mode"] == "hybrid"
            assert result.data["semantic_ready"] is True
            assert result.data["actions"][0]["name"] == "reset_test_device"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_initialization_failure_has_explicit_fallback(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    service = HybridSearchService(
        catalog,
        _hybrid_settings(fallback=True),
        provider=FailingProvider(),
    )
    await service.initialize()
    assert service.effective_mode == "lexical_fallback"
    result = await service.search("list users")
    assert result.mode == "lexical_fallback"
    assert result.actions[0]["name"] == "list_test_users"
    assert "unavailable" in (result.warning or "")


@pytest.mark.asyncio
async def test_initialization_failure_without_fallback_is_unavailable(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    service = HybridSearchService(
        catalog,
        _hybrid_settings(fallback=False),
        provider=FailingProvider(),
    )
    await service.initialize()
    with pytest.raises(EmbeddingError, match="Semantic search is unavailable"):
        await service.search("list users")


def test_hybrid_mode_requires_embedding_url() -> None:
    with pytest.raises(ValidationError, match="MCP_SEARCH_EMBEDDING_URL"):
        SearchSettings(mode="hybrid")


@pytest.mark.asyncio
@respx.mock
async def test_openai_embedding_provider_batches_and_orders_results() -> None:
    route = respx.post("http://embedding.test/v1/embeddings").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "model": "fake-embedding",
                    "data": [
                        {"index": 1, "embedding": [0.0, 2.0]},
                        {"index": 0, "embedding": [2.0, 0.0]},
                    ],
                },
            ),
            httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [0.0, 3.0]}]},
            ),
        ]
    )
    settings = _hybrid_settings()
    settings = settings.model_copy(
        update={
            "batch_size": 2,
            "embedding_api_key": SecretStr("internal-token"),
        }
    )
    provider = OpenAIEmbeddingProvider(settings)
    vectors = await provider.embed(["a", "b", "c"])
    assert len(vectors) == 3
    assert vectors[0] == [2.0, 0.0]
    assert vectors[1] == [0.0, 2.0]
    assert vectors[2] == [0.0, 3.0]
    assert route.call_count == 2
    assert route.calls[0].request.headers["authorization"] == "Bearer internal-token"
    first_payload = json.loads(route.calls[0].request.content)
    assert first_payload["input"] == ["a", "b"]
    assert first_payload["model"] == "fake-embedding"
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_openai_embedding_provider_rejects_bad_dimensions() -> None:
    respx.post("http://embedding.test/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]},
        )
    )
    provider = OpenAIEmbeddingProvider(_hybrid_settings())
    with pytest.raises(EmbeddingError, match="dimension"):
        await provider.embed(["a"])
    await provider.close()
