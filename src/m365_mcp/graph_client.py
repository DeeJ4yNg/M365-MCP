"""Certificate-authenticated Microsoft Graph transport with safe retries."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import msal
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from m365_mcp.config import GraphServiceSettings
from m365_mcp.errors import M365Error


class GraphError(M365Error):
    """Sanitized Graph failure suitable for an MCP client."""


@dataclass(slots=True)
class CertificateCredential:
    """MSAL certificate client credential loaded from a PFX file."""

    client_id: str
    tenant_id: str
    private_key_pem: str
    public_certificate_pem: str
    thumbprint: str

    @classmethod
    def from_pfx(
        cls, *, client_id: str, tenant_id: str, path: str, password: str | None
    ) -> CertificateCredential:
        pfx_path = Path(path)
        try:
            pfx_bytes = pfx_path.read_bytes()
            private_key, certificate, _ = pkcs12.load_key_and_certificates(
                pfx_bytes,
                password.encode("utf-8") if password else None,
                backend=default_backend(),
            )
        except OSError as exc:
            raise GraphError(
                "certificate_unavailable",
                "The Graph certificate file cannot be read",
                {"path": str(pfx_path)},
            ) from exc
        except ValueError as exc:
            raise GraphError(
                "certificate_invalid",
                "The Graph certificate file or password is invalid",
            ) from exc
        if private_key is None or certificate is None:
            raise GraphError(
                "certificate_invalid", "The PFX must contain a private key and certificate"
            )
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        public_pem = certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
        # Entra/MSAL certificate client assertions require the SHA-1 certificate thumbprint.
        thumbprint = certificate.fingerprint(hashes.SHA1()).hex().upper()  # noqa: S303
        return cls(
            client_id=client_id,
            tenant_id=tenant_id,
            private_key_pem=private_pem,
            public_certificate_pem=public_pem,
            thumbprint=thumbprint,
        )


@dataclass(slots=True)
class GraphResult:
    data: Any
    status_code: int
    request_id: str | None = None
    pages: int = 1
    truncated: bool = False
    next_link: str | None = None
    content_type: str | None = None


@dataclass(slots=True)
class _TokenCache:
    access_token: str = ""
    expires_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class GraphClient:
    """HTTP client for fixed declarative Graph operations."""

    RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        settings: GraphServiceSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        credential: CertificateCredential | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = settings.graph_base_url.rstrip("/")
        self._scope = [settings.graph_scope]
        self._credential = credential or CertificateCredential.from_pfx(
            client_id=settings.client_id,
            tenant_id=settings.tenant_id,
            path=settings.certificate_path,
            password=(
                settings.certificate_password.get_secret_value()
                if settings.certificate_password
                else None
            ),
        )
        self._msal: msal.ConfidentialClientApplication | None = None
        self._token = _TokenCache()
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.limits.request_timeout_seconds),
            transport=transport,
            follow_redirects=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "m365-graph-mcp/0.2",
            },
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _access_token(self) -> str:
        async with self._token.lock:
            now = time.time()
            if self._token.access_token and self._token.expires_at - 120 > now:
                return self._token.access_token
            try:
                if self._msal is None:
                    self._msal = msal.ConfidentialClientApplication(
                        client_id=self._credential.client_id,
                        authority=(
                            f"https://login.microsoftonline.com/{self._credential.tenant_id}"
                        ),
                        client_credential={
                            "private_key": self._credential.private_key_pem,
                            "thumbprint": self._credential.thumbprint,
                            "public_certificate": self._credential.public_certificate_pem,
                        },
                    )
                result = await asyncio.to_thread(
                    self._msal.acquire_token_for_client, scopes=self._scope
                )
            except Exception as exc:
                raise GraphError(
                    "graph_token_unavailable",
                    "Microsoft Entra did not issue a Graph app-only token",
                    retryable=True,
                ) from exc
            token = result.get("access_token") if isinstance(result, dict) else None
            if not token:
                error = (
                    result.get("error", "unknown_error")
                    if isinstance(result, dict)
                    else "unknown_error"
                )
                raise GraphError(
                    "graph_token_rejected",
                    "Microsoft Entra rejected the Graph app-only token request",
                    {"entra_error": str(error)[:120]},
                )
            self._token.access_token = str(token)
            self._token.expires_at = now + int(result.get("expires_in", 3600))
            return self._token.access_token

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        response_mode: str = "json",
        correlation_id: str | None = None,
    ) -> GraphResult:
        if not path.startswith("/") or path.startswith("//"):
            raise GraphError("unsafe_graph_path", "Graph requests must use a relative fixed path")
        url = f"{self.base_url}{path}"
        method_upper = method.upper()
        if method_upper not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
            raise GraphError("unsafe_graph_method", f"Unsupported Graph method: {method}")
        headers = {"client-request-id": correlation_id} if correlation_id else {}
        if params and any(key in params for key in ("$search", "$count")):
            headers["ConsistencyLevel"] = "eventual"
        token = await self._access_token()
        headers["Authorization"] = f"Bearer {token}"
        query = self._prepare_query(params or {})
        attempts = 0
        max_attempts = 3 if method_upper == "GET" else 1
        while True:
            attempts += 1
            try:
                response = await self._http.request(
                    method_upper,
                    url,
                    params=query,
                    json=json_body,
                    headers=headers,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempts < max_attempts:
                    await self._sleep_backoff(attempts)
                    continue
                raise GraphError(
                    "graph_unavailable",
                    "Microsoft Graph could not be reached",
                    retryable=True,
                ) from exc
            if response.status_code in self.RETRYABLE_STATUS and attempts < max_attempts:
                await self._sleep_backoff(attempts, response.headers.get("retry-after"))
                continue
            return self._build_result(response, response_mode=response_mode)

    async def get_paginated(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_pages: int,
        response_mode: str = "json",
        correlation_id: str | None = None,
    ) -> GraphResult:
        result = await self.request(
            "GET",
            path,
            params=params,
            response_mode=response_mode,
            correlation_id=correlation_id,
        )
        if response_mode != "json" or not isinstance(result.data, dict):
            return result
        items = result.data.get("value")
        next_link = result.data.get("@odata.nextLink")
        last_data = result.data
        pages = 1
        while isinstance(items, list) and next_link and pages < max_pages:
            if not self._is_safe_next_link(str(next_link)):
                raise GraphError(
                    "unsafe_graph_next_link",
                    "Microsoft Graph returned an unexpected pagination URL",
                )
            page = await self._request_absolute_get(str(next_link), correlation_id=correlation_id)
            pages += 1
            if not isinstance(page.data, dict):
                break
            page_items = page.data.get("value")
            if isinstance(page_items, list):
                items.extend(page_items)
            last_data = page.data
            next_link = page.data.get("@odata.nextLink")
            if page.truncated:
                result.truncated = True
                next_link = None
                break
        result.data = (
            {
                **last_data,
                "value": items,
            }
            if isinstance(items, list)
            else last_data
        )
        result.pages = pages
        result.next_link = str(next_link) if next_link else None
        return result

    async def _request_absolute_get(self, url: str, *, correlation_id: str | None) -> GraphResult:
        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}"}
        if correlation_id:
            headers["client-request-id"] = correlation_id
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._http.get(url, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                await self._sleep_backoff(attempt + 1)
                continue
            if response.status_code in self.RETRYABLE_STATUS and attempt < 2:
                await self._sleep_backoff(attempt + 1, response.headers.get("retry-after"))
                continue
            return self._build_result(response, response_mode="json")
        raise GraphError(
            "graph_unavailable", "Microsoft Graph pagination request failed", retryable=True
        ) from last_exc

    def _is_safe_next_link(self, url: str) -> bool:
        parsed = urlsplit(url)
        base = urlsplit(self.base_url)
        return (
            parsed.scheme == base.scheme
            and parsed.netloc == base.netloc
            and parsed.path.startswith(base.path.rstrip("/") + "/")
        )

    def _build_result(self, response: httpx.Response, *, response_mode: str) -> GraphResult:
        request_id = response.headers.get("request-id") or response.headers.get("client-request-id")
        content_type = response.headers.get("content-type", "").split(";")[0]
        max_bytes = self.settings.limits.max_result_bytes
        raw = response.content
        truncated = len(raw) > max_bytes
        if truncated:
            raw = raw[:max_bytes]
        if response.status_code >= 300:
            graph_code, graph_message = self._graph_error(raw)
            raise GraphError(
                "graph_request_failed",
                f"Microsoft Graph returned HTTP {response.status_code}",
                {
                    "status": response.status_code,
                    "graph_error": graph_code,
                    "graph_message": graph_message,
                    "request_id": request_id,
                },
                retryable=response.status_code in self.RETRYABLE_STATUS,
            )
        if not raw:
            data: Any = None
        elif response_mode == "text" or (
            content_type and not content_type.endswith("json") and content_type != "text/json"
        ):
            data = raw.decode("utf-8", errors="replace")
        else:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = raw.decode("utf-8", errors="replace")
        return GraphResult(
            data=data,
            status_code=response.status_code,
            request_id=request_id,
            truncated=truncated,
            content_type=content_type or None,
        )

    @staticmethod
    def _graph_error(raw: bytes) -> tuple[str, str]:
        try:
            payload = json.loads(raw)
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            if isinstance(error, dict):
                return (
                    str(error.get("code", "unknown"))[:120],
                    str(error.get("message", ""))[:500],
                )
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        return "unknown", raw[:300].decode("utf-8", errors="replace")

    @staticmethod
    def _prepare_query(params: dict[str, Any]) -> dict[str, str]:
        query: dict[str, str] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                query[key] = "true" if value else "false"
            elif isinstance(value, list):
                query[key] = ",".join(str(item) for item in value)
            else:
                query[key] = str(value)
        return query

    async def _sleep_backoff(self, attempt: int, retry_after: str | None = None) -> None:
        delay = 0.0
        if retry_after:
            try:
                delay = min(float(retry_after), 30.0)
            except ValueError:
                delay = 0.0
        if delay <= 0:
            jitter = secrets.randbelow(250) / 1000
            delay = min(2 ** (attempt - 1), 8.0) + jitter
        await asyncio.sleep(delay)
