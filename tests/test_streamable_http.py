from __future__ import annotations

import asyncio
import contextlib
import socket
import stat
import sys
from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from tests.helpers import clean_process_env, powershell_action, write_catalog_files


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _fake_pwsh(tmp_path: Path) -> Path:
    script = tmp_path / "fake-pwsh"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "print('__M365_RESULT__' + json.dumps({\n"
        "    'ok': True,\n"
        "    'result': [{'Identity': request['parameters']['Identity'],\n"
        "                'CorrelationId': request['correlation_id']}],\n"
        "}))\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


@pytest.mark.integration
@pytest.mark.asyncio
async def test_streamable_http_end_to_end(tmp_path) -> None:
    action = powershell_action()
    paths = write_catalog_files(
        tmp_path,
        "powershell",
        [action],
        enabled=[action["name"]],
        approved=[action["name"]],
    )
    port = _free_port()
    certificate_path = tmp_path / "dummy.pfx"
    certificate_path.write_bytes(b"placeholder")
    env = clean_process_env(
        tmp_path,
        {
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
            "MCP_AUTH_MODE": "none",
            "MCP_ALLOW_INSECURE_DEV": "true",
            "MCP_ALLOWED_HOSTS": "127.0.0.1,localhost",
            "MCP_ENABLED_TOOLS_PATH": str(paths["enabled"]),
            "MCP_REVIEW_REGISTRY_PATH": str(paths["review"]),
            "MCP_POWERSHELL_CATALOG_PATH": str(paths["catalog"]),
            "MCP_CONFIRMATION_ENABLED": "false",
            "M365_TENANT_ID": "00000000-0000-0000-0000-000000000001",
            "M365_POWERSHELL_CLIENT_ID": "00000000-0000-0000-0000-000000000002",
            "M365_ORGANIZATION": "contoso.onmicrosoft.com",
            "M365_POWERSHELL_CERTIFICATE_PATH": str(certificate_path),
            "M365_PWSH_PATH": str(_fake_pwsh(tmp_path)),
        },
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "m365_mcp.powershell.server",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(timeout=1) as http:
            for _ in range(100):
                if process.returncode is not None:
                    stdout, stderr = await process.communicate()
                    raise AssertionError(
                        f"server exited early: {stdout.decode()} {stderr.decode()}"
                    )
                with contextlib.suppress(httpx.HTTPError):
                    response = await http.get(f"{base_url}/healthz")
                    if response.status_code == 200:
                        break
                await asyncio.sleep(0.05)
            else:
                if process.returncode is None:
                    process.terminate()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=5)
                stdout, stderr = await process.communicate()
                raise AssertionError(
                    "server did not become healthy\n"
                    f"stdout:\n{stdout.decode()}\n"
                    f"stderr:\n{stderr.decode()}"
                )

        async with Client(f"{base_url}/mcp") as client:
            tools = await client.list_tools()
            assert len(tools) == 5
            result = await client.call_tool(
                "execute_read",
                {
                    "action_id": action["name"],
                    "params": {"identity": "alice@contoso.com"},
                },
            )
            assert result.data["ok"] is True
            assert result.data["data"][0]["Identity"] == "alice@contoso.com"
            assert result.data["correlation_id"]
    finally:
        if process.returncode is None:
            process.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=10)
        if process.returncode is None:
            process.kill()
            await process.wait()
