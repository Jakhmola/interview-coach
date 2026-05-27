"""Shared result type for web-search providers.

`SearchResult` is the single shape every search helper returns; it's imported
by `tavily.py` and `company_researcher`.
"""

from __future__ import annotations

from typing import TypedDict


class SearchResult(TypedDict):
    url: str
    title: str
    content: str
    score: float
