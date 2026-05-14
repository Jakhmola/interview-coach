"""Provider registry — picks a concrete implementation from `settings`.

Today only Tavily is wired up. Adding crawl4ai later means one file under
`providers/` plus one branch in each getter. Callers ask for
`get_search_provider()` / `get_fetch_provider()` and stay decoupled from
the concrete class.
"""

from __future__ import annotations

from interview_coach.config import settings
from interview_coach.providers.base import WebFetchProvider, WebSearchProvider
from interview_coach.providers.tavily import TavilyFetch, TavilySearch


def get_search_provider() -> WebSearchProvider:
    name = settings.web_search_provider
    if name == "tavily":
        return TavilySearch(settings.tavily_api_key)
    raise ValueError(f"unknown web_search_provider: {name!r}")


def get_fetch_provider() -> WebFetchProvider:
    name = settings.web_fetch_provider
    if name == "tavily":
        return TavilyFetch(settings.tavily_api_key)
    raise ValueError(f"unknown web_fetch_provider: {name!r}")
