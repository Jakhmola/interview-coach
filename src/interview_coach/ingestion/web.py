"""DEPRECATED — re-export shim. Use `interview_coach.providers.tavily` directly.

TODO(phase-17): delete this file after callers repoint. Kept for one release
so existing imports (tests, older branches) keep working without a touch.
"""

from interview_coach.providers.base import SearchResult
from interview_coach.providers.tavily import (
    TAVILY_EXTRACT_URL,
    TAVILY_SEARCH_URL,
    fetch_url_text,
    tavily_search,
)

__all__ = [
    "SearchResult",
    "TAVILY_EXTRACT_URL",
    "TAVILY_SEARCH_URL",
    "fetch_url_text",
    "tavily_search",
]
