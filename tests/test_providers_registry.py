"""Provider registry tests — concrete classes for known names; error otherwise."""

from __future__ import annotations

import pytest

from interview_coach.providers import registry
from interview_coach.providers.tavily import TavilyFetch, TavilySearch


def test_get_search_provider_returns_tavily_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry.settings, "web_search_provider", "tavily")
    monkeypatch.setattr(registry.settings, "tavily_api_key", "test-key")
    provider = registry.get_search_provider()
    assert isinstance(provider, TavilySearch)
    assert provider.name == "tavily"


def test_get_fetch_provider_returns_tavily_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry.settings, "web_fetch_provider", "tavily")
    monkeypatch.setattr(registry.settings, "tavily_api_key", "test-key")
    provider = registry.get_fetch_provider()
    assert isinstance(provider, TavilyFetch)
    assert provider.name == "tavily"


def test_get_search_provider_unknown_name_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry.settings, "web_search_provider", "magic-crawler")
    with pytest.raises(ValueError, match="magic-crawler"):
        registry.get_search_provider()


def test_get_fetch_provider_unknown_name_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry.settings, "web_fetch_provider", "magic-crawler")
    with pytest.raises(ValueError, match="magic-crawler"):
        registry.get_fetch_provider()
