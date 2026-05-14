"""Unit tests for the web_server MCP tool functions.

Calls `web_search` / `web_fetch` directly (FastMCP keeps the underlying
coroutine callable). The MCP stdio transport is exercised in
`tests/integration/test_mcp_web_server.py` (when the integration suite runs).
"""

from __future__ import annotations

import pytest

from interview_coach.mcp.servers import web_server
from interview_coach.providers.base import SearchResult


class _StubSearch:
    name = "stub"

    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        self.calls.append((query, max_results))
        return self.results


class _StubFetch:
    name = "stub"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[str] = []

    async def fetch_text(self, url: str) -> str:
        self.calls.append(url)
        return self.text


async def test_web_search_returns_list_of_dicts(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubSearch(
        [
            SearchResult(url="https://a.example", title="A", content="aa", score=0.9),
            SearchResult(url="https://b.example", title="B", content="bb", score=0.5),
        ]
    )
    monkeypatch.setattr(web_server, "get_search_provider", lambda: stub)

    result = await web_server.web_search("acme", max_results=3)
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["url"] == "https://a.example"
    assert result[0]["title"] == "A"
    assert result[0]["score"] == 0.9
    assert stub.calls == [("acme", 3)]


async def test_web_search_default_max_results(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubSearch([])
    monkeypatch.setattr(web_server, "get_search_provider", lambda: stub)
    await web_server.web_search("acme")
    assert stub.calls == [("acme", 5)]


async def test_web_fetch_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubFetch("body text")
    monkeypatch.setattr(web_server, "get_fetch_provider", lambda: stub)
    result = await web_server.web_fetch("https://example.com")
    assert result == "body text"
    assert stub.calls == ["https://example.com"]
