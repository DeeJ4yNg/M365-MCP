"""Fixed-cmdlet PowerShell worker runner."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
from pathlib import Path
from typing import Any

from m365_mcp.catalog import ActionCatalog
from m365_mcp.config import PowerShellServiceSettings
from m365_mcp.errors import CatalogError, M365Error
from m365_mcp.models import ActionDefinition, PowerShellRequestSpec

_RESULT_PREFIX = "__M365_RESULT__"


class PowerShellError(M365Error):
    """PowerShell worker failure safe to return to an MCP caller."""


class PowerShellExecutor:
    """Runs exactly one catalog-defined cmdlet in a fresh pwsh process."""

    def __init__(
        self,
        settings: PowerShellServiceSettings,
        catalog: ActionCatalog,
        *,
        worker_path: str | None = None,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.worker_path = Path(
            worker_path or settings.worker_path or Path(__file__).with_name("worker.ps1")
        )
        if not self.worker_path.is_file():
            raise PowerShellError(
                "worker_missing",
                "The PowerShell worker script is missing",
                {"path": str(self.worker_path)},
            )
        if shutil.which(settings.pwsh_path) is None:
            raise PowerShellError(
                "powershell_unavailable",
                "PowerShell 7 executable was not found",
                {"path": settings.pwsh_path},
            )
        if not Path(settings.certificate_path).is_file():
            raise PowerShellError(
                "certificate_unavailable",
                "The PowerShell certificate file is missing",
                {"path": settings.certificate_path},
            )
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)

    async def close(self) -> None:
        return None

    async def execute(
        self,
        action: ActionDefinition,
        params: dict[str, Any],
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        if action.powershell is None:
            raise CatalogError("action_not_implemented", f"{action.name} has no PowerShell command")
        mapped = self._map_parameters(action.powershell, params)
        request = {
            "action": action.name,
            "parameters": mapped,
            "correlation_id": correlation_id,
        }
        async with self._semaphore:
            envelope = await self._run_worker(request, correlation_id=correlation_id)
        if not envelope.get("ok"):
            error = envelope.get("error", {})
            raise PowerShellError(
                str(error.get("code", "powershell_command_failed")),
                str(error.get("message", "PowerShell command failed"))[:1500],
            )
        result = envelope.get("result")
        payload = {
            "action": action.name,
            "data": result,
            "meta": {
                "service": action.powershell.service,
                "cmdlet": action.powershell.cmdlet,
                "result_count": len(result) if isinstance(result, list) else None,
            },
        }
        encoded = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
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
                        "service": action.powershell.service,
                        "cmdlet": action.powershell.cmdlet,
                        "truncated": True,
                    },
                }
            raise CatalogError(
                "result_too_large",
                "The PowerShell result exceeded the server response limit; narrow the query.",
                {
                    "returned_bytes": len(encoded),
                    "limit_bytes": self.settings.limits.max_result_bytes,
                },
            )
        return payload

    @staticmethod
    def _map_parameters(request: PowerShellRequestSpec, params: dict[str, Any]) -> dict[str, Any]:
        mapped: dict[str, Any] = {}
        for source, target in request.parameters.items():
            if source in params and params[source] is not None:
                mapped[target] = params[source]
        for target, value in request.constant_parameters.items():
            mapped[target] = value
        return mapped

    async def _run_worker(self, request: dict[str, Any], *, correlation_id: str) -> dict[str, Any]:
        env = os.environ.copy()
        if self.settings.certificate_password:
            env["M365_CERTIFICATE_PASSWORD"] = self.settings.certificate_password.get_secret_value()
        env.update(
            {
                "M365_TENANT_ID": self.settings.tenant_id,
                "M365_CLIENT_ID": self.settings.client_id,
                "M365_CERTIFICATE_PATH": self.settings.certificate_path,
                "M365_ORGANIZATION": self.settings.organization,
                "M365_POWERSHELL_CLIENT_ID": self.settings.client_id,
                "M365_POWERSHELL_CERTIFICATE_PATH": self.settings.certificate_path,
                "M365_PS_MAX_OUTPUT_BYTES": str(self.settings.limits.max_result_bytes),
            }
        )
        env.pop("MCP_CONFIRMATION_HS256_SECRET", None)
        env.pop("MCP_CONFIRMATION_REDIS_URL", None)
        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                self.settings.pwsh_path,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(self.worker_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload),
                timeout=self.settings.worker_timeout_seconds,
            )
        except TimeoutError as exc:
            await self._terminate(process)
            raise PowerShellError(
                "powershell_timeout",
                "The PowerShell command exceeded its execution timeout",
                retryable=False,
            ) from exc
        except FileNotFoundError as exc:
            raise PowerShellError(
                "powershell_unavailable", "PowerShell 7 executable was not found"
            ) from exc
        envelope = self._parse_envelope(stdout)
        if envelope is None:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()[:1200]
            raise PowerShellError(
                "powershell_protocol_error",
                "The PowerShell worker returned no valid result envelope",
                {
                    "exit_code": process.returncode,
                    "stderr": stderr_text,
                },
            )
        return envelope

    @staticmethod
    def _parse_envelope(stdout: bytes) -> dict[str, Any] | None:
        if len(stdout) > 3_000_000:
            return None
        text = stdout.decode("utf-8", errors="replace")
        for line in reversed(text.splitlines()):
            if not line.startswith(_RESULT_PREFIX):
                continue
            try:
                parsed = json.loads(line[len(_RESULT_PREFIX) :])
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process | None) -> None:
        if process is None or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        try:
            await asyncio.wait_for(process.communicate(), timeout=5)
        except TimeoutError:
            return
