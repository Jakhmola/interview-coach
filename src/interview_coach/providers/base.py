"""Provider protocols for external-world I/O.

These define a single seam that future providers (crawl4ai, GitHub, etc.)
will conform to. Internal app code can call providers directly; MCP
servers wrap them as tools for future LLM/external consumers.
"""

from __future__ import annotations

from typing import Protocol, TypedDict


class SearchResult(TypedDict):
    url: str
    title: str
    content: str
    score: float


class WebSearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]: ...


class WebFetchProvider(Protocol):
    name: str

    async def fetch_text(self, url: str) -> str: ...
