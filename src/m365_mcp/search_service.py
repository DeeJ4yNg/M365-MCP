"""Hybrid lexical and semantic retrieval for the governed action catalog."""

from __future__ import annotations

import asyncio
import logging
import math
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import numpy as np
from numpy.typing import NDArray

from m365_mcp.catalog import ActionCatalog
from m365_mcp.config import SearchSettings
from m365_mcp.errors import M365Error
from m365_mcp.models import ActionDefinition
from m365_mcp.search_lexicon import LEXICON_VERSION, normalize_text

logger = logging.getLogger("m365_mcp.search")
_MAX_EMBEDDING_RESPONSE_BYTES = 10_000_000


class EmbeddingError(M365Error):
    """Embedding service unavailable or returned an invalid response."""


class EmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def probe(self) -> bool: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class SearchResponse:
    actions: list[dict[str, Any]]
    mode: str
    semantic_ready: bool
    warning: str | None = None


@dataclass(slots=True)
class SearchStatus:
    configured_mode: str
    effective_mode: str
    semantic_ready: bool
    action_vectors: int
    dimension: int | None
    model: str
    fallback_enabled: bool
    lexicon_version: str = LEXICON_VERSION
    error: str | None = None
    warning: str | None = None


class OpenAIEmbeddingProvider:
    """Minimal client for TEI/vLLM/OpenAI-compatible /v1/embeddings."""

    def __init__(
        self,
        settings: SearchSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        headers = {
            "Accept": "application/json",
            "User-Agent": "m365-mcp/0.2",
        }
        if settings.embedding_api_key:
            headers["Authorization"] = f"Bearer {settings.embedding_api_key.get_secret_value()}"
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds),
            headers=headers,
            transport=transport,
            trust_env=False,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        expected_dimension = self.settings.embedding_dimension
        for start in range(0, len(texts), self.settings.batch_size):
            batch = list(texts[start : start + self.settings.batch_size])
            try:
                response = await self._http.post(
                    self.settings.embedding_url,
                    json={
                        "model": self.settings.embedding_model,
                        "input": batch,
                        "encoding_format": "float",
                    },
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise EmbeddingError(
                    "embedding_unavailable",
                    "The embedding service could not be reached",
                    retryable=True,
                ) from exc
            if response.status_code >= 400:
                raise EmbeddingError(
                    "embedding_request_failed",
                    f"The embedding service returned HTTP {response.status_code}",
                    {
                        "status": response.status_code,
                        "retryable_status": response.status_code in {429, 500, 502, 503, 504},
                    },
                    retryable=response.status_code in {429, 500, 502, 503, 504},
                )
            if len(response.content) > _MAX_EMBEDDING_RESPONSE_BYTES:
                raise EmbeddingError(
                    "embedding_response_too_large",
                    "The embedding service response exceeded the safety limit",
                )
            try:
                payload = response.json()
                records = self._parse_records(payload, expected=len(batch))
            except (ValueError, TypeError, KeyError) as exc:
                raise EmbeddingError(
                    "embedding_invalid_response",
                    "The embedding service returned an invalid response",
                ) from exc
            for record in records:
                vector = record["embedding"]
                if expected_dimension is not None and len(vector) != expected_dimension:
                    raise EmbeddingError(
                        "embedding_dimension_mismatch",
                        "The embedding dimension does not match configuration",
                        {"expected": expected_dimension, "actual": len(vector)},
                    )
                expected_dimension = len(vector)
                vectors.append(vector)
        return vectors

    def _parse_records(self, payload: Any, *, expected: int) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ValueError("response must be an object")
        if self.settings.verify_response_model:
            returned_model = payload.get("model")
            if returned_model and returned_model != self.settings.embedding_model:
                raise EmbeddingError(
                    "embedding_model_mismatch",
                    "The embedding service loaded a different model than configured",
                    {
                        "expected": self.settings.embedding_model,
                        "actual": returned_model,
                    },
                )
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise ValueError("embedding data count mismatch")
        records: list[dict[str, Any]] = []
        seen: set[int] = set()
        for record in data:
            if not isinstance(record, dict):
                raise ValueError("embedding record must be an object")
            index = record.get("index")
            vector = record.get("embedding")
            if not isinstance(index, int) or index in seen:
                raise ValueError("embedding index invalid")
            if not isinstance(vector, list) or not vector:
                raise ValueError("embedding vector invalid")
            for value in vector:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("embedding value is not numeric")
                if not math.isfinite(float(value)):
                    raise ValueError("embedding value is not finite")
            seen.add(index)
            records.append({"index": index, "embedding": [float(value) for value in vector]})
        if seen != set(range(expected)):
            raise ValueError("embedding indices incomplete")
        records.sort(key=lambda item: item["index"])
        return records

    async def probe(self) -> bool:
        if not self.settings.embedding_health_url:
            return True
        try:
            response = await asyncio.wait_for(
                self._http.get(self.settings.embedding_health_url),
                timeout=min(self.settings.timeout_seconds, 2.0),
            )
        except (TimeoutError, httpx.HTTPError):
            return False
        return response.status_code == 200

    async def close(self) -> None:
        await self._http.aclose()


class HybridSearchService:
    """Combine deterministic lexical scores with remote semantic similarity."""

    def __init__(
        self,
        catalog: ActionCatalog,
        settings: SearchSettings,
        *,
        provider: EmbeddingProvider | None = None,
    ) -> None:
        self.catalog = catalog
        self.settings = settings
        self.provider = (
            provider
            if provider is not None
            else (OpenAIEmbeddingProvider(settings) if settings.mode == "hybrid" else None)
        )
        self._matrix: NDArray[np.float32] | None = None
        self._action_names: list[str] = []
        self._action_index: dict[str, int] = {}
        self._dimension: int | None = None
        self._initialized = False
        self._fallback_active = False
        self._initialization_error: str | None = None
        self._last_runtime_error: str | None = None
        self._init_lock = asyncio.Lock()
        self._cache_lock = asyncio.Lock()
        self._query_cache: OrderedDict[str, NDArray[np.float32]] = OrderedDict()

    @property
    def configured_mode(self) -> str:
        return self.settings.mode

    @property
    def semantic_ready(self) -> bool:
        return self.settings.mode == "hybrid" and self._initialized and not self._fallback_active

    @property
    def search_available(self) -> bool:
        if self.settings.mode == "lexical":
            return True
        if self._initialized:
            return True
        return bool(self.settings.fallback_to_lexical and self._initialization_error)

    @property
    def effective_mode(self) -> str:
        if self.settings.mode == "lexical":
            return "lexical"
        if self.semantic_ready:
            return "hybrid"
        return "lexical_fallback"

    def status(self) -> SearchStatus:
        return SearchStatus(
            configured_mode=self.configured_mode,
            effective_mode=self.effective_mode,
            semantic_ready=self.semantic_ready,
            action_vectors=len(self._action_names),
            dimension=self._dimension,
            model=self.settings.embedding_model,
            fallback_enabled=self.settings.fallback_to_lexical,
            error=self._initialization_error or self._last_runtime_error,
            warning=(
                "Semantic search is unavailable; lexical fallback is active."
                if self.effective_mode == "lexical_fallback"
                else None
            ),
        )

    async def initialize(self) -> None:
        if self.settings.mode == "lexical" or self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            try:
                actions = sorted(self.catalog.actions.values(), key=lambda item: item.name)
                texts = [self._passage_text(action) for action in actions]
                vectors = await self.provider.embed(texts) if self.provider else []
                if len(vectors) != len(actions) or not vectors:
                    raise EmbeddingError(
                        "embedding_vector_count_mismatch",
                        "The embedding service returned the wrong number of action vectors",
                    )
                matrix = np.asarray(vectors, dtype=np.float32)
                if matrix.ndim != 2 or matrix.shape[0] != len(actions):
                    raise EmbeddingError(
                        "embedding_vector_shape_invalid",
                        "The action embedding matrix has an invalid shape",
                    )
                if not np.isfinite(matrix).all():
                    raise EmbeddingError(
                        "embedding_vector_invalid",
                        "The action embedding matrix contains non-finite values",
                    )
                norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                if np.any(norms == 0):
                    raise EmbeddingError(
                        "embedding_vector_invalid",
                        "The embedding service returned a zero-length vector",
                    )
                matrix = matrix / norms
                dimension = int(matrix.shape[1])
                if (
                    self.settings.embedding_dimension is not None
                    and dimension != self.settings.embedding_dimension
                ):
                    raise EmbeddingError(
                        "embedding_dimension_mismatch",
                        "The action embedding dimension does not match configuration",
                        {
                            "expected": self.settings.embedding_dimension,
                            "actual": dimension,
                        },
                    )
                self._matrix = matrix
                self._action_names = [action.name for action in actions]
                self._action_index = {name: index for index, name in enumerate(self._action_names)}
                self._dimension = dimension
                self._initialized = True
                self._fallback_active = False
                self._initialization_error = None
                logger.info(
                    "Semantic action index initialized",
                    extra={
                        "audit_event": {
                            "event": "semantic_index_ready",
                            "server": self.catalog.server,
                            "actions": len(actions),
                            "dimension": dimension,
                            "model": self.settings.embedding_model,
                            "lexicon_version": LEXICON_VERSION,
                        }
                    },
                )
            except Exception as exc:
                code = exc.code if isinstance(exc, M365Error) else "embedding_initialization_failed"
                self._initialization_error = str(code)
                self._initialized = False
                self._fallback_active = self.settings.fallback_to_lexical
                logger.error(
                    "Semantic action index initialization failed",
                    extra={
                        "audit_event": {
                            "event": "semantic_index_error",
                            "server": self.catalog.server,
                            "error_code": code,
                            "fallback": self._fallback_active,
                        }
                    },
                    exc_info=True,
                )

    async def search(
        self,
        intent: str,
        *,
        limit: int = 10,
        domain: str | None = None,
        operation_type: str | None = None,
        include_disabled: bool = False,
    ) -> SearchResponse:
        if self.settings.mode == "lexical":
            return self._lexical_response(
                intent,
                limit=limit,
                domain=domain,
                operation_type=operation_type,
                include_disabled=include_disabled,
                mode="lexical",
            )
        if not self._initialized:
            if self.settings.fallback_to_lexical:
                return self._lexical_response(
                    intent,
                    limit=limit,
                    domain=domain,
                    operation_type=operation_type,
                    include_disabled=include_disabled,
                    mode="lexical_fallback",
                    warning="Semantic index is unavailable; lexical fallback is active.",
                )
            raise EmbeddingError(
                "embedding_unavailable",
                "Semantic search is unavailable",
                {"reason": self._initialization_error or "not_initialized"},
            )

        candidates = self.catalog.lexical_candidates(
            intent,
            domain=domain,
            operation_type=operation_type,
            include_disabled=include_disabled,
        )
        if not candidates:
            return SearchResponse([], "hybrid", self.semantic_ready)
        try:
            query_vector = await self._query_vector(intent)
        except Exception as exc:
            if self.settings.fallback_to_lexical:
                self._last_runtime_error = (
                    exc.code if isinstance(exc, M365Error) else "embedding_query_failed"
                )
                return self._lexical_response(
                    intent,
                    limit=limit,
                    domain=domain,
                    operation_type=operation_type,
                    include_disabled=include_disabled,
                    mode="lexical_fallback",
                    warning="Semantic query failed; lexical fallback is active.",
                    semantic_ready=False,
                )
            raise
        self._last_runtime_error = None
        assert self._matrix is not None
        indices = np.asarray(
            [self._action_index[action.name] for action, _ in candidates], dtype=np.int32
        )
        similarities = self._matrix[indices] @ query_vector
        semantic_norm = np.clip(
            (similarities - self.settings.semantic_floor)
            / (self.settings.semantic_ceiling - self.settings.semantic_floor),
            0.0,
            1.0,
        )
        lexical_norm = np.asarray(
            [
                1.0 - math.exp(-max(score, 0.0) / self.settings.lexical_scale)
                for _, score in candidates
            ],
            dtype=np.float32,
        )
        total_weight = self.settings.semantic_weight + self.settings.lexical_weight
        semantic_weight = self.settings.semantic_weight / total_weight
        lexical_weight = self.settings.lexical_weight / total_weight
        final_scores = semantic_weight * semantic_norm + lexical_weight * lexical_norm
        normalized_intent = self._normalize_name(intent)
        ranked: list[tuple[ActionDefinition, float, float]] = []
        for index, (action, _lexical_score) in enumerate(candidates):
            final = float(final_scores[index])
            if normalized_intent in {
                self._normalize_name(action.name),
                self._normalize_name(action.name.removeprefix(f"{self.catalog.server}_")),
            }:
                final = max(final, 0.99)
            if final < self.settings.min_score:
                continue
            ranked.append((action, final, float(semantic_norm[index])))
        ranked.sort(key=lambda item: (-item[1], item[0].name))
        return SearchResponse(
            actions=[
                {
                    **self.catalog.public_action(action),
                    "score": round(final, 4),
                }
                for action, final, _ in ranked[:limit]
            ],
            mode="hybrid",
            semantic_ready=True,
        )

    async def probe(self) -> bool:
        if self.settings.mode == "lexical":
            return True
        if self.provider is None:
            return False
        return await self.provider.probe()

    async def close(self) -> None:
        if self.provider is not None:
            await self.provider.close()

    def _lexical_response(
        self,
        intent: str,
        *,
        limit: int,
        domain: str | None,
        operation_type: str | None,
        include_disabled: bool,
        mode: str,
        warning: str | None = None,
        semantic_ready: bool | None = None,
    ) -> SearchResponse:
        actions = self.catalog.search(
            intent,
            limit=limit,
            domain=domain,
            operation_type=operation_type,
            include_disabled=include_disabled,
        )
        return SearchResponse(
            actions,
            mode,
            self.semantic_ready if semantic_ready is None else semantic_ready,
            warning,
        )

    async def _query_vector(self, intent: str) -> NDArray[np.float32]:
        text = self._query_text(intent)
        async with self._cache_lock:
            cached = self._query_cache.get(text)
            if cached is not None:
                self._query_cache.move_to_end(text)
                return cached
        if self.provider is None:
            raise EmbeddingError("embedding_unavailable", "No embedding provider is configured")
        vectors = await self.provider.embed([text])
        if len(vectors) != 1:
            raise EmbeddingError(
                "embedding_vector_count_mismatch",
                "The embedding service returned the wrong number of query vectors",
            )
        vector = np.asarray(vectors[0], dtype=np.float32)
        if vector.ndim != 1 or not np.isfinite(vector).all() or not np.any(vector):
            raise EmbeddingError("embedding_vector_invalid", "The query embedding is invalid")
        if self._dimension is not None and vector.shape[0] != self._dimension:
            raise EmbeddingError(
                "embedding_dimension_mismatch",
                "The query embedding dimension does not match the action index",
            )
        vector = vector / np.linalg.norm(vector)
        async with self._cache_lock:
            if self.settings.cache_size > 0:
                self._query_cache[text] = vector
                self._query_cache.move_to_end(text)
                while len(self._query_cache) > self.settings.cache_size:
                    self._query_cache.popitem(last=False)
        return vector

    def _query_text(self, intent: str) -> str:
        query = f"{self.settings.query_prefix}{intent.strip()}"
        if self.settings.query_instruction:
            return f"Instruct: {self.settings.query_instruction}\nQuery: {query}"
        return query

    def _passage_text(self, action: ActionDefinition) -> str:
        body = (
            f"Domain: {action.domain}\n"
            f"Action: {action.name}\n"
            f"Description: {action.description}\n"
            f"Keywords: {', '.join(action.keywords)}"
        )
        return f"{self.settings.passage_prefix}{body}"

    @staticmethod
    def _normalize_name(value: str) -> str:
        return normalize_text(value)
