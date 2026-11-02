"""Runtime configuration loaded from environment variables.

Secrets are deliberately represented as SecretStr and never logged. Configuration
is validated at process startup so a misconfigured production container fails
closed instead of serving unauthenticated tools.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ServerName = Literal["graph", "powershell"]


class AuthSettings(BaseModel):
    """Inbound MCP caller authentication."""

    mode: Literal["jwt", "none"] = "jwt"
    tenant_id: str = ""
    audience: str = ""
    issuer: str = ""
    jwks_uri: str = ""
    required_read_roles: list[str] = Field(
        default_factory=lambda: ["M365.MCP.Read", "M365.MCP.ReadWrite"]
    )
    required_write_roles: list[str] = Field(default_factory=lambda: ["M365.MCP.ReadWrite"])
    allowed_client_ids: list[str] = Field(default_factory=list)
    allow_insecure_dev: bool = False

    @model_validator(mode="after")
    def _validate_mode(self) -> AuthSettings:
        if self.mode == "none":
            if not self.allow_insecure_dev:
                raise ValueError(
                    "MCP_AUTH_MODE=none requires MCP_ALLOW_INSECURE_DEV=true and must "
                    "never be used in production"
                )
            return self
        missing = [
            name
            for name, value in (
                ("MCP_ENTRY_TENANT_ID", self.tenant_id),
                ("MCP_ENTRY_AUDIENCE", self.audience),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"missing required JWT settings: {', '.join(missing)}")
        if not self.issuer:
            self.issuer = f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"
        if not self.jwks_uri:
            self.jwks_uri = (
                f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"
            )
        if not self.required_read_roles or not self.required_write_roles:
            raise ValueError("inbound role lists must not be empty")
        return self


class LimitSettings(BaseModel):
    """Response and upstream request limits."""

    max_result_bytes: int = Field(default=1_000_000, ge=16_384, le=20_000_000)
    max_pages: int = Field(default=5, ge=1, le=50)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)


class SearchSettings(BaseModel):
    """Lexical or hybrid action retrieval against a remote embedding service."""

    mode: Literal["lexical", "hybrid"] = "lexical"
    embedding_url: str = ""
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dimension: int | None = Field(default=None, ge=1, le=16_384)
    embedding_health_url: str = ""
    query_prefix: str = ""
    passage_prefix: str = ""
    query_instruction: str = ""
    batch_size: int = Field(default=32, ge=1, le=256)
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    semantic_weight: float = Field(default=0.55, ge=0, le=1)
    lexical_weight: float = Field(default=0.45, ge=0, le=1)
    semantic_floor: float = Field(default=0.30, ge=-1, le=1)
    semantic_ceiling: float = Field(default=0.95, ge=-1, le=1)
    min_score: float = Field(default=0.05, ge=0, le=1)
    lexical_scale: float = Field(default=25.0, gt=0, le=200)
    cache_size: int = Field(default=256, ge=0, le=4096)
    fallback_to_lexical: bool = False
    verify_response_model: bool = False

    @model_validator(mode="after")
    def _validate_hybrid(self) -> SearchSettings:
        if self.mode == "lexical":
            return self
        if not self.embedding_url:
            raise ValueError("MCP_SEARCH_EMBEDDING_URL is required when MCP_SEARCH_MODE=hybrid")
        parsed = urlparse(self.embedding_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCP_SEARCH_EMBEDDING_URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError(
                "MCP_SEARCH_EMBEDDING_URL must not contain credentials; "
                "use MCP_SEARCH_EMBEDDING_API_KEY"
            )
        if self.embedding_health_url:
            health = urlparse(self.embedding_health_url)
            if health.scheme not in {"http", "https"} or not health.netloc:
                raise ValueError("MCP_SEARCH_EMBEDDING_HEALTH_URL must be an absolute HTTP(S) URL")
        if not self.embedding_model.strip():
            raise ValueError("MCP_SEARCH_EMBEDDING_MODEL must not be empty")
        if self.semantic_weight <= 0 and self.lexical_weight <= 0:
            raise ValueError("at least one hybrid search weight must be greater than zero")
        if self.semantic_ceiling <= self.semantic_floor:
            raise ValueError("MCP_SEARCH_SEMANTIC_CEILING must be greater than the semantic floor")
        if len(self.query_instruction) > 2048:
            raise ValueError("MCP_SEARCH_QUERY_INSTRUCTION is too long")
        return self


class ConfirmationSettings(BaseModel):
    """Out-of-band write approval verification and replay protection."""

    enabled: bool = True
    algorithm: Literal["RS256", "HS256"] = "RS256"
    public_key_path: str = ""
    hs256_secret: SecretStr | None = None
    allow_hs256: bool = False
    issuer: str = "m365-tool-approval"
    audience: str = ""
    leeway_seconds: int = Field(default=15, ge=0, le=120)
    max_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    redis_url: str = ""
    sqlite_path: str = "/tmp/m365-mcp/confirmations.sqlite3"  # noqa: S108

    @model_validator(mode="after")
    def _validate_key_material(self) -> ConfirmationSettings:
        if not self.enabled:
            return self
        if self.algorithm == "HS256":
            if not self.allow_hs256:
                raise ValueError(
                    "MCP_CONFIRMATION_ALLOW_HS256=true is required for HS256; "
                    "use RS256 in production"
                )
            if not self.hs256_secret or len(self.hs256_secret.get_secret_value()) < 32:
                raise ValueError("HS256 confirmation secret must contain at least 32 characters")
        elif not self.public_key_path:
            raise ValueError(
                "MCP_CONFIRMATION_PUBLIC_KEY_PATH is required when confirmation is enabled"
            )
        return self


class BaseServiceSettings(BaseSettings):
    """Shared process settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    server_name: ServerName
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"
    catalog_path: str = ""
    enabled_tools_path: str = "/app/config/enabledTools.json"
    review_registry_path: str = "/app/config/review-registry.json"
    limits: LimitSettings = Field(default_factory=LimitSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    confirmation: ConfirmationSettings = Field(default_factory=ConfirmationSettings)
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)

    @field_validator("allowed_hosts", "allowed_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


class GraphServiceSettings(BaseServiceSettings):
    """Graph server settings, including its certificate app identity."""

    server_name: Literal["graph"] = "graph"
    port: int = 8000
    tenant_id: str = ""
    client_id: str = ""
    certificate_path: str = "/run/secrets/graph.pfx"
    certificate_password: SecretStr | None = None
    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    graph_scope: str = "https://graph.microsoft.com/.default"

    @model_validator(mode="after")
    def _validate_upstream(self) -> GraphServiceSettings:
        missing = [
            name
            for name, value in (
                ("M365_TENANT_ID", self.tenant_id),
                ("M365_GRAPH_CLIENT_ID", self.client_id),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"missing required Graph settings: {', '.join(missing)}")
        allowed_hosts = ("https://graph.microsoft.com/",)
        if not self.graph_base_url.startswith(allowed_hosts):
            raise ValueError("M365_GRAPH_BASE_URL must use the Microsoft Graph commercial cloud")
        return self


class PowerShellServiceSettings(BaseServiceSettings):
    """PowerShell server settings."""

    server_name: Literal["powershell"] = "powershell"
    port: int = 8001
    tenant_id: str = ""
    client_id: str = ""
    certificate_path: str = "/run/secrets/powershell.pfx"
    certificate_password: SecretStr | None = None
    organization: str = ""
    sharepoint_admin_url: str = ""
    pwsh_path: str = "pwsh"
    worker_path: str = ""
    worker_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    max_concurrency: int = Field(default=4, ge=1, le=32)

    @model_validator(mode="after")
    def _validate_upstream(self) -> PowerShellServiceSettings:
        missing = [
            name
            for name, value in (
                ("M365_TENANT_ID", self.tenant_id),
                ("M365_POWERSHELL_CLIENT_ID", self.client_id),
                ("M365_ORGANIZATION", self.organization),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"missing required PowerShell settings: {', '.join(missing)}")
        if self.sharepoint_admin_url and not self.sharepoint_admin_url.startswith("https://"):
            raise ValueError("M365_SHAREPOINT_ADMIN_URL must use HTTPS")
        return self


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def load_settings(server: ServerName) -> BaseServiceSettings:
    """Load settings for one server from process environment."""

    from dotenv import load_dotenv

    load_dotenv(override=False)
    if server == "graph":
        return GraphServiceSettings(
            host=_env("MCP_HOST", "127.0.0.1"),
            port=int(_env("MCP_PORT", "8000")),
            log_level=_env("MCP_LOG_LEVEL", "INFO"),
            catalog_path=_env("MCP_GRAPH_CATALOG_PATH", ""),
            enabled_tools_path=_env("MCP_ENABLED_TOOLS_PATH", "/app/config/enabledTools.json"),
            review_registry_path=_env(
                "MCP_REVIEW_REGISTRY_PATH", "/app/config/review-registry.json"
            ),
            limits=LimitSettings(
                max_result_bytes=int(_env("MCP_MAX_RESULT_BYTES", "1000000")),
                max_pages=int(_env("MCP_MAX_PAGES", "5")),
                request_timeout_seconds=float(_env("MCP_REQUEST_TIMEOUT_SECONDS", "30")),
            ),
            search=_load_search("graph"),
            auth=_load_auth("MCP_GRAPH"),
            confirmation=_load_confirmation("graph"),
            allowed_hosts=_csv(_env("MCP_ALLOWED_HOSTS", "")),
            allowed_origins=_csv(_env("MCP_ALLOWED_ORIGINS", "")),
            tenant_id=_env("M365_TENANT_ID", ""),
            client_id=_env("M365_GRAPH_CLIENT_ID", ""),
            certificate_path=_env("M365_GRAPH_CERTIFICATE_PATH", "/run/secrets/graph.pfx"),
            certificate_password=_secret("M365_GRAPH_CERTIFICATE_PASSWORD"),
            graph_base_url=_env("M365_GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0"),
            graph_scope=_env("M365_GRAPH_SCOPE", "https://graph.microsoft.com/.default"),
        )
    return PowerShellServiceSettings(
        host=_env("MCP_HOST", "127.0.0.1"),
        port=int(_env("MCP_PORT", "8001")),
        log_level=_env("MCP_LOG_LEVEL", "INFO"),
        catalog_path=_env("MCP_POWERSHELL_CATALOG_PATH", ""),
        enabled_tools_path=_env("MCP_ENABLED_TOOLS_PATH", "/app/config/enabledTools.json"),
        review_registry_path=_env("MCP_REVIEW_REGISTRY_PATH", "/app/config/review-registry.json"),
        limits=LimitSettings(
            max_result_bytes=int(_env("MCP_MAX_RESULT_BYTES", "1000000")),
            max_pages=int(_env("MCP_MAX_PAGES", "5")),
            request_timeout_seconds=float(_env("MCP_REQUEST_TIMEOUT_SECONDS", "30")),
        ),
        search=_load_search("powershell"),
        auth=_load_auth("MCP_POWERSHELL"),
        confirmation=_load_confirmation("powershell"),
        allowed_hosts=_csv(_env("MCP_ALLOWED_HOSTS", "")),
        allowed_origins=_csv(_env("MCP_ALLOWED_ORIGINS", "")),
        tenant_id=_env("M365_TENANT_ID", ""),
        client_id=_env("M365_POWERSHELL_CLIENT_ID", ""),
        certificate_path=_env("M365_POWERSHELL_CERTIFICATE_PATH", "/run/secrets/powershell.pfx"),
        certificate_password=_secret("M365_POWERSHELL_CERTIFICATE_PASSWORD"),
        organization=_env("M365_ORGANIZATION", ""),
        sharepoint_admin_url=_env("M365_SHAREPOINT_ADMIN_URL", ""),
        pwsh_path=_env("M365_PWSH_PATH", "pwsh"),
        worker_path=_env("M365_PS_WORKER_PATH", ""),
        worker_timeout_seconds=float(_env("M365_PS_WORKER_TIMEOUT_SECONDS", "120")),
        max_concurrency=int(_env("M365_PS_MAX_CONCURRENCY", "4")),
    )


def _env(name: str, default: str) -> str:
    import os

    return os.environ.get(name, default).strip()


def _raw_env(name: str, default: str) -> str:
    import os

    return os.environ.get(name, default)


def _load_search(server: ServerName) -> SearchSettings:
    server_prefix = f"MCP_{server.upper()}_SEARCH"
    shared_prefix = "MCP_SEARCH"
    mode = _env(f"{server_prefix}_MODE", _env(f"{shared_prefix}_MODE", "lexical"))
    return SearchSettings(
        mode=mode,  # type: ignore[arg-type]
        embedding_url=_env(f"{shared_prefix}_EMBEDDING_URL", ""),
        embedding_api_key=_secret(f"{shared_prefix}_EMBEDDING_API_KEY"),
        embedding_model=_env(f"{shared_prefix}_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
        embedding_dimension=(
            int(dimension)
            if (dimension := _env(f"{shared_prefix}_EMBEDDING_DIMENSION", ""))
            else None
        ),
        embedding_health_url=_env(f"{shared_prefix}_EMBEDDING_HEALTH_URL", ""),
        query_prefix=_raw_env(f"{shared_prefix}_QUERY_PREFIX", ""),
        passage_prefix=_raw_env(f"{shared_prefix}_PASSAGE_PREFIX", ""),
        query_instruction=_env(f"{shared_prefix}_QUERY_INSTRUCTION", ""),
        batch_size=int(_env(f"{shared_prefix}_BATCH_SIZE", "32")),
        timeout_seconds=float(_env(f"{shared_prefix}_TIMEOUT_SECONDS", "10")),
        semantic_weight=float(_env(f"{shared_prefix}_SEMANTIC_WEIGHT", "0.55")),
        lexical_weight=float(_env(f"{shared_prefix}_LEXICAL_WEIGHT", "0.45")),
        semantic_floor=float(_env(f"{shared_prefix}_SEMANTIC_FLOOR", "0.30")),
        semantic_ceiling=float(_env(f"{shared_prefix}_SEMANTIC_CEILING", "0.95")),
        min_score=float(_env(f"{shared_prefix}_MIN_SCORE", "0.05")),
        lexical_scale=float(_env(f"{shared_prefix}_LEXICAL_SCALE", "25")),
        cache_size=int(_env(f"{shared_prefix}_CACHE_SIZE", "256")),
        fallback_to_lexical=(
            _env(f"{shared_prefix}_FALLBACK_TO_LEXICAL", "false").lower() == "true"
        ),
        verify_response_model=(
            _env(f"{shared_prefix}_VERIFY_RESPONSE_MODEL", "false").lower() == "true"
        ),
    )


def _secret(name: str) -> SecretStr | None:
    """Read a secret directly or from a Docker secret file via NAME_FILE."""

    import os

    value = os.environ.get(name)
    file_name = os.environ.get(f"{name}_FILE")
    if file_name:
        try:
            value = Path(file_name).read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise ValueError(f"cannot read secret file for {name}: {file_name}") from exc
    return SecretStr(value) if value else None


def _load_auth(prefix: str) -> AuthSettings:
    return AuthSettings(
        mode=_env(f"{prefix}_AUTH_MODE", _env("MCP_AUTH_MODE", "jwt")),  # type: ignore[arg-type]
        tenant_id=_env(f"{prefix}_ENTRY_TENANT_ID", _env("MCP_ENTRY_TENANT_ID", "")),
        audience=_env(f"{prefix}_AUDIENCE", _env("MCP_AUDIENCE", "")),
        issuer=_env(f"{prefix}_ISSUER", _env("MCP_ISSUER", "")),
        jwks_uri=_env(f"{prefix}_JWKS_URI", _env("MCP_JWKS_URI", "")),
        required_read_roles=_csv(_env(f"{prefix}_READ_ROLES", "M365.MCP.Read,M365.MCP.ReadWrite")),
        required_write_roles=_csv(_env(f"{prefix}_WRITE_ROLES", "M365.MCP.ReadWrite")),
        allowed_client_ids=_csv(
            _env(f"{prefix}_ALLOWED_CLIENT_IDS", _env("MCP_ALLOWED_CLIENT_IDS", ""))
        ),
        allow_insecure_dev=_env("MCP_ALLOW_INSECURE_DEV", "false").lower() == "true",
    )


def _load_confirmation(server: ServerName) -> ConfirmationSettings:
    return ConfirmationSettings(
        enabled=_env("MCP_CONFIRMATION_ENABLED", "true").lower() == "true",
        algorithm=_env("MCP_CONFIRMATION_ALGORITHM", "RS256"),  # type: ignore[arg-type]
        public_key_path=_env("MCP_CONFIRMATION_PUBLIC_KEY_PATH", ""),
        hs256_secret=_secret("MCP_CONFIRMATION_HS256_SECRET"),
        allow_hs256=_env("MCP_CONFIRMATION_ALLOW_HS256", "false").lower() == "true",
        issuer=_env("MCP_CONFIRMATION_ISSUER", "m365-tool-approval"),
        audience=_env(
            "MCP_CONFIRMATION_AUDIENCE",
            f"m365-{server}-mcp" if server == "graph" else "m365-powershell-mcp",
        ),
        leeway_seconds=int(_env("MCP_CONFIRMATION_LEEWAY_SECONDS", "15")),
        max_ttl_seconds=int(_env("MCP_CONFIRMATION_MAX_TTL_SECONDS", "900")),
        redis_url=_env("MCP_CONFIRMATION_REDIS_URL", ""),
        sqlite_path=_env(
            "MCP_CONFIRMATION_SQLITE_PATH",
            "/tmp/m365-mcp/confirmations.sqlite3",  # noqa: S108
        ),
    )


def load_enabled_tools(path: str, server: ServerName) -> set[str]:
    """Read the server-specific enabledTools allow-list.

    A missing file fails closed by returning an empty set. Unknown keys in the
    file are rejected to catch typos that would silently enable nothing.
    """

    config_path = Path(path)
    if not config_path.exists():
        return set()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("enabledTools config must be a JSON object")
    enabled = raw.get("enabledTools", raw)
    if not isinstance(enabled, dict):
        raise ValueError("enabledTools must map server names to arrays")
    unknown_servers = set(enabled) - {"graph", "powershell"}
    if unknown_servers:
        raise ValueError(f"unknown server keys in enabledTools: {sorted(unknown_servers)}")
    values = enabled.get(server, [])
    if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
        raise ValueError(f"enabledTools.{server} must be an array of action names")
    return set(values)


@lru_cache(maxsize=1)
def project_root() -> Path:
    return Path(__file__).resolve().parents[2]
