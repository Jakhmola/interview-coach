"""MCP server exposing web search + extract tools, backed by the registry.

Run as: `python -m interview_coach.mcp.servers.web_server`

Per Phase 16 boundary rules: this is a thin shell over `providers/*`.
No business logic, no DB writes, no caching.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from interview_coach.providers.registry import get_fetch_provider, get_search_provider

mcp = FastMCP("web")


@mcp.tool()
async def web_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search the web for the given query; returns ranked results.

    Each result has `url`, `title`, `content`, `score`. Provider chosen by
    `settings.web_search_provider` (default: tavily).
    """
    provider = get_search_provider()
    hits = await provider.search(query, max_results=max_results)
    # `SearchResult` is a TypedDict; cast to dict for JSON serialization.
    return [dict(h) for h in hits]


@mcp.tool()
async def web_fetch(url: str) -> str:
    """Fetch and extract readable text from a URL.

    Provider chosen by `settings.web_fetch_provider` (default: tavily).
    Raises if the provider's API key is missing or the fetch fails.
    """
    provider = get_fetch_provider()
    return await provider.fetch_text(url)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
