"""MCP client bootstrap. Phase 6+ agents use `get_tools()` to obtain
LangChain-compatible tool objects backed by the MCP servers configured here.

Lazy: nothing happens until first call. Suitable for an `api` process where
agents may never run during a given session.
"""

from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

_client: MultiServerMCPClient | None = None
_tools: list[BaseTool] | None = None


def _server_config() -> dict:
    env = dict(os.environ)
    return {
        "documents": {
            "command": "python",
            "args": ["-m", "interview_coach.mcp.servers.documents_server"],
            "transport": "stdio",
            # Pass the parent env explicitly so DATABASE_URL etc. reach the
            # subprocess. langchain-mcp-adapters does not auto-inherit env.
            "env": env,
        },
        "web": {
            "command": "python",
            "args": ["-m", "interview_coach.mcp.servers.web_server"],
            "transport": "stdio",
            # web_server needs TAVILY_API_KEY; pass the parent env explicitly
            # (same mechanism as the documents server).
            "env": env,
        },
    }


def build_mcp_client() -> MultiServerMCPClient:
    """Get (or build) the MCP client. Singleton at module scope."""
    global _client
    if _client is None:
        _client = MultiServerMCPClient(_server_config())
    return _client


async def get_tools() -> list[BaseTool]:
    """Fetch LangChain tools from all configured MCP servers."""
    global _tools
    if _tools is None:
        client = build_mcp_client()
        _tools = await client.get_tools()
    return _tools


async def reset_client() -> None:
    """Drop the cached client + tools. Used by api lifespan shutdown and by tests."""
    global _client, _tools
    _client = None
    _tools = None


def decode_tool_result(result: Any) -> list[Any]:
    """Decode a langchain-mcp-adapters tool result.

    Tools that return Python objects come back as a list of MCP TextContent
    blocks (each `{"type": "text", "text": "<json>", ...}`). FastMCP serializes
    each item separately, so a tool that returned `list[dict]` of N items
    yields N text blocks. This helper turns the blocks back into plain Python
    values via `json.loads` and returns them as a list.

    Convention: callers of singular-result tools (`get_document`, `get_job`)
    should take `decode_tool_result(...)[0]` (or `None` if empty).
    """
    if not isinstance(result, list):
        return [result]
    decoded: list[Any] = []
    for block in result:
        if isinstance(block, dict) and block.get("type") == "text":
            decoded.append(json.loads(block["text"]))
        else:
            decoded.append(block)
    return decoded
