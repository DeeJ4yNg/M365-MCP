"""Framework-neutral Agent Runtime connectivity smoke test."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import httpx
from fastmcp import Client


async def verify_server(name: str, url: str, token: str) -> dict[str, Any]:
    base_url = url.removesuffix("/mcp")
    async with httpx.AsyncClient(timeout=15) as http:
        health = await http.get(f"{base_url}/healthz")
        health.raise_for_status()
    async with Client(url, auth=token) as client:
        tools = await client.list_tools()
        result = await client.call_tool(
            "get_tool_inventory",
            {"limit": 3, "include_disabled": True},
            raise_on_error=True,
        )
        search = await client.call_tool(
            "search_actions",
            {"intent": "service health", "limit": 3, "include_disabled": True},
            raise_on_error=True,
        )
    tool_names = sorted(tool.name for tool in tools)
    expected = {
        "describe_action",
        "execute_read",
        "execute_write",
        "get_tool_inventory",
        "search_actions",
    }
    if set(tool_names) != expected:
        raise RuntimeError(f"{name} exposed unexpected tools: {tool_names}")
    return {
        "server": name,
        "transport": "streamable-http",
        "health": health.json(),
        "tool_names": tool_names,
        "inventory": {
            "server": result.data.get("server"),
            "total_matching": result.data.get("total_matching"),
            "returned": result.data.get("returned"),
            "status_counts": result.data.get("status_counts"),
        },
        "search": {
            "mode": search.data.get("search_mode"),
            "semantic_ready": search.data.get("semantic_ready"),
            "count": search.data.get("count"),
        },
    }


async def run() -> int:
    targets = {
        "graph": {
            "url": os.environ.get("MCP_GRAPH_URL", "http://graph-mcp:8000/mcp"),
            "token": os.environ.get("MCP_GRAPH_BEARER_TOKEN", "").strip(),
        },
        "powershell": {
            "url": os.environ.get("MCP_POWERSHELL_URL", "http://powershell-mcp:8001/mcp"),
            "token": os.environ.get("MCP_POWERSHELL_BEARER_TOKEN", "").strip(),
        },
    }
    missing = [name for name, target in targets.items() if not target["token"]]
    if missing:
        print(
            "Separate audience-scoped tokens are required for: "
            + ", ".join(sorted(missing))
            + ". Obtain each app-only Entra token in the Agent Runtime and pass it in.",
            file=sys.stderr,
        )
        return 2
    summary = []
    for name, target in targets.items():
        summary.append(await verify_server(name, target["url"], target["token"]))
    print(json.dumps({"ok": True, "servers": summary}, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
