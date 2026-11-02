from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from m365_mcp.catalog import ActionCatalog
from m365_mcp.config import (
    AuthSettings,
    ConfirmationSettings,
    GraphServiceSettings,
    LimitSettings,
    PowerShellServiceSettings,
)


def graph_action(
    name: str = "list_test_users",
    *,
    operation: str = "read",
    method: str = "GET",
    path: str = "/users",
    query: dict[str, str] | None = None,
    body: dict[str, str] | None = None,
    body_const: dict[str, Any] | None = None,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    risk: str = "low",
) -> dict[str, Any]:
    props = properties or {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Maximum number of records.",
        }
    }
    return {
        "name": name,
        "domain": "Entra ID",
        "backend": "graph",
        "description": "Test action for a governed Graph operation.",
        "required_permissions": ["User.Read.All"]
        if operation == "read"
        else ["User.ReadWrite.All"],
        "operation_type": operation,
        "risk_level": risk,
        "implementation_status": "implemented",
        "review_status": "pending",
        "enabled": False,
        "keywords": ["user", "test"],
        "docs_url": "https://learn.microsoft.com/en-us/graph/api/user-list",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
            "required": required or [],
        },
        "graph": {
            "method": method,
            "path": path,
            "query": query or {},
            "body": body or {},
            "body_const": body_const or {},
            "response": "json",
        },
    }


def powershell_action(
    name: str = "get_test_mailbox",
    *,
    operation: str = "read",
    cmdlet: str = "Get-EXOMailbox",
    service: str = "exchange",
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    parameters: dict[str, str] | None = None,
    constants: dict[str, Any] | None = None,
) -> dict[str, Any]:
    props = properties or {
        "identity": {
            "type": "string",
            "minLength": 1,
            "description": "Mailbox identity.",
        }
    }
    return {
        "name": name,
        "domain": "Exchange Online",
        "backend": "powershell",
        "description": "Test action for a governed PowerShell operation.",
        "required_permissions": ["Exchange.ManageAsApp"],
        "operation_type": operation,
        "risk_level": "low" if operation == "read" else "medium",
        "implementation_status": "implemented",
        "review_status": "pending",
        "enabled": False,
        "keywords": ["mailbox", "exchange", "test"],
        "docs_url": "https://learn.microsoft.com/en-us/powershell/module/exchange/get-exomailbox",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
            "required": required or (["identity"] if "identity" in props else []),
        },
        "powershell": {
            "service": service,
            "cmdlet": cmdlet,
            "parameters": parameters or {"identity": "Identity"},
            "constant_parameters": constants or {},
        },
    }


def write_catalog_files(
    tmp_path: Path,
    backend: str,
    actions: list[dict[str, Any]],
    *,
    enabled: list[str] | None = None,
    approved: list[str] | None = None,
    statuses: dict[str, str] | None = None,
) -> dict[str, Path]:
    catalog_path = tmp_path / "catalogs" / f"{backend}.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(actions), encoding="utf-8")
    config_dir = tmp_path / "config"
    if not config_dir.exists():
        config_dir.mkdir(parents=True)
    enabled_path = config_dir / "enabledTools.json"
    enabled_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "enabledTools": {
                    "graph": enabled or [] if backend == "graph" else [],
                    "powershell": enabled or [] if backend == "powershell" else [],
                },
            }
        ),
        encoding="utf-8",
    )
    registry_path = config_dir / "review-registry.json"
    chosen = set(approved or [])
    decisions: dict[str, Any] = {}
    for action in actions:
        status = (statuses or {}).get(
            action["name"], "approved" if action["name"] in chosen else "pending"
        )
        if status == "pending":
            continue
        decisions[action["name"]] = {
            "status": status,
            "reviewed_by": "reviewer@example.com",
            "reviewed_at": "2026-10-31T12:00:00Z",
            "evidence": "CHG-TEST-001",
        }
    registry_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "reviews": {backend: decisions},
            }
        ),
        encoding="utf-8",
    )
    return {
        "catalog": catalog_path,
        "enabled": enabled_path,
        "review": registry_path,
        "config_dir": config_dir,
    }


def make_catalog(
    tmp_path: Path, backend: str, actions: list[dict[str, Any]], **kwargs: Any
) -> ActionCatalog:
    paths = write_catalog_files(tmp_path, backend, actions, **kwargs)
    return ActionCatalog(
        backend,  # type: ignore[arg-type]
        str(paths["catalog"]),
        str(paths["enabled"]),
        str(paths["review"]),
    )


def clean_process_env(tmp_path: Path, overrides: dict[str, str]) -> dict[str, str]:
    """Run integration servers without host sandbox or secret environment."""

    process_tmp = tmp_path / "process-tmp"
    process_tmp.mkdir(exist_ok=True)
    home = tmp_path / "process-home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(process_tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    env.update(overrides)
    return env


def insecure_auth() -> AuthSettings:
    return AuthSettings(mode="none", allow_insecure_dev=True)


def graph_settings(tmp_path: Path, paths: dict[str, Path]) -> GraphServiceSettings:
    return GraphServiceSettings(
        tenant_id="00000000-0000-0000-0000-000000000001",
        client_id="00000000-0000-0000-0000-000000000002",
        catalog_path=str(paths["catalog"]),
        enabled_tools_path=str(paths["enabled"]),
        review_registry_path=str(paths["review"]),
        auth=insecure_auth(),
        confirmation=confirmation_settings(tmp_path, enabled=False),
        limits=LimitSettings(max_result_bytes=16_384, max_pages=2),
    )


def powershell_settings(
    tmp_path: Path, paths: dict[str, Path], pwsh_path: str = "pwsh"
) -> PowerShellServiceSettings:
    certificate_path = tmp_path / "powershell-test.pfx"
    certificate_path.write_bytes(b"placeholder")
    return PowerShellServiceSettings(
        tenant_id="00000000-0000-0000-0000-000000000001",
        client_id="00000000-0000-0000-0000-000000000002",
        organization="contoso.onmicrosoft.com",
        certificate_path=str(certificate_path),
        catalog_path=str(paths["catalog"]),
        enabled_tools_path=str(paths["enabled"]),
        review_registry_path=str(paths["review"]),
        auth=insecure_auth(),
        confirmation=confirmation_settings(tmp_path, enabled=False),
        pwsh_path=pwsh_path,
        limits=LimitSettings(max_result_bytes=16_384, max_pages=2),
    )


def confirmation_settings(
    tmp_path: Path,
    *,
    enabled: bool = True,
    audience: str = "m365-graph-mcp",
) -> ConfirmationSettings:
    return ConfirmationSettings(
        enabled=enabled,
        algorithm="HS256",
        hs256_secret="test-secret-that-is-at-least-32-characters-long",
        allow_hs256=True,
        issuer="test-change-system",
        audience=audience,
        sqlite_path=str(tmp_path / "replay.sqlite3"),
    )
