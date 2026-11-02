from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import sys
import time

import httpx
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastmcp import Client
from jwt.algorithms import RSAAlgorithm
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from tests.helpers import clean_process_env, powershell_action, write_catalog_files
from tests.test_streamable_http import _fake_pwsh


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _token(private_key, *, roles: list[str], audience: str, issuer: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": issuer,
            "aud": audience,
            "sub": "agent-subject",
            "azp": "agent-client",
            "tid": "00000000-0000-0000-0000-000000000001",
            "roles": roles,
            "iat": now,
            "nbf": now,
            "exp": now + 300,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_entra_jwt_ingress_and_app_role(tmp_path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})

    async def jwks(request):
        return JSONResponse({"keys": [jwk]})

    jwks_port = _free_port()
    jwks_server = uvicorn.Server(
        uvicorn.Config(
            Starlette(routes=[Route("/jwks", jwks)]),
            host="127.0.0.1",
            port=jwks_port,
            log_level="error",
            lifespan="off",
        )
    )
    jwks_task = asyncio.create_task(jwks_server.serve())
    issuer = "https://issuer.test"
    audience = "api://m365-powershell-mcp"
    await asyncio.sleep(0.1)

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
            "MCP_AUTH_MODE": "jwt",
            "MCP_ALLOW_INSECURE_DEV": "false",
            "MCP_POWERSHELL_AUTH_MODE": "jwt",
            "MCP_POWERSHELL_ENTRY_TENANT_ID": "00000000-0000-0000-0000-000000000001",
            "MCP_POWERSHELL_AUDIENCE": audience,
            "MCP_POWERSHELL_ISSUER": issuer,
            "MCP_POWERSHELL_JWKS_URI": f"http://127.0.0.1:{jwks_port}/jwks",
            "MCP_POWERSHELL_ALLOWED_CLIENT_IDS": "agent-client",
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
                    if (await http.get(f"{base_url}/healthz")).status_code == 200:
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

        good = _token(key, roles=["M365.MCP.Read"], audience=audience, issuer=issuer)
        async with Client(f"{base_url}/mcp", auth=good) as client:
            result = await client.call_tool(
                "execute_read",
                {"action_id": action["name"], "params": {"identity": "alice@contoso.com"}},
            )
            assert result.data["ok"] is True

        no_role = _token(key, roles=[], audience=audience, issuer=issuer)
        async with Client(f"{base_url}/mcp", auth=no_role) as client:
            denied = await client.call_tool(
                "execute_read",
                {"action_id": action["name"], "params": {"identity": "alice@contoso.com"}},
                raise_on_error=False,
            )
            assert denied.is_error is True
            assert "insufficient_role" in denied.content[0].text
    finally:
        if process.returncode is None:
            process.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=10)
        if process.returncode is None:
            process.kill()
            await process.wait()
        jwks_server.should_exit = True
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(jwks_task, timeout=10)
