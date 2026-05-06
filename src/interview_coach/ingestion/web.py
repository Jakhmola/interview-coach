from typing import TypedDict

import httpx

from interview_coach.ingestion.errors import FetchFailed, KeyMissing

TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class SearchResult(TypedDict):
    url: str
    title: str
    content: str
    score: float


async def fetch_url_text(url: str, api_key: str | None) -> str:
    """Fetch and extract readable text from a URL via Tavily.

    Raises:
        KeyMissing: api_key is None or empty.
        FetchFailed: network error, non-2xx response, or empty extraction.
    """
    if not api_key:
        raise KeyMissing("Tavily API key not configured")

    payload = {
        "urls": [url],
        "extract_depth": "basic",
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(TAVILY_EXTRACT_URL, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise FetchFailed(f"Network error contacting Tavily: {e}") from e

    if r.status_code != 200:
        raise FetchFailed(f"Tavily returned {r.status_code}: {r.text[:200]}")

    try:
        data = r.json()
    except ValueError as e:
        raise FetchFailed("Tavily returned non-JSON response") from e

    results = data.get("results") or []
    if not results:
        failed = data.get("failed_results") or []
        if failed:
            err = failed[0].get("error", "unknown error")
            raise FetchFailed(f"Tavily failed to extract: {err}")
        raise FetchFailed("Tavily returned no results")

    content = results[0].get("raw_content") or ""
    text = content.strip()
    if not text:
        raise FetchFailed("Tavily extracted empty content (page may require JS or login)")
    return text


async def tavily_search(
    query: str,
    api_key: str | None,
    *,
    max_results: int = 5,
) -> list[SearchResult]:
    """Search the web via Tavily; returns ranked results.

    Raises:
        KeyMissing: api_key is None or empty.
        FetchFailed: network error, non-2xx response, or non-JSON body.
    """
    if not api_key:
        raise KeyMissing("Tavily API key not configured")

    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(TAVILY_SEARCH_URL, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise FetchFailed(f"Network error contacting Tavily: {e}") from e

    if r.status_code != 200:
        raise FetchFailed(f"Tavily search returned {r.status_code}: {r.text[:200]}")

    try:
        data = r.json()
    except ValueError as e:
        raise FetchFailed("Tavily search returned non-JSON response") from e

    out: list[SearchResult] = []
    for item in data.get("results") or []:
        url = item.get("url")
        if not url:
            continue
        out.append(
            SearchResult(
                url=url,
                title=item.get("title") or "",
                content=item.get("content") or "",
                score=float(item.get("score") or 0.0),
            )
        )
    return out
