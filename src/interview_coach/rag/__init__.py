"""RAG layer: chunking, embedding-over-HTTP client, retrieval, ingest."""

from __future__ import annotations

import asyncio

from interview_coach.rag.client import EmbeddingClient

_FALLBACK_CLIENT: EmbeddingClient | None = None
_LOCK: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


async def get_embedding_client() -> EmbeddingClient:
    """Return a long-lived `EmbeddingClient`.

    Lifetime:
      - During a FastAPI request, the lifespan-created client lives on
        `app.state.embedding_client`. That path doesn't go through here.
      - For background tasks scheduled inside the api process and for
        callers that don't have an `app.state` handle (e.g. the MCP
        subprocess, `scripts/backfill_grounding.py`), this module
        memoizes one client per process built from `settings`.

    The fallback singleton is closed implicitly at process exit; long-lived
    services (api lifespan) should close their own client on shutdown
    rather than relying on this accessor.
    """
    global _FALLBACK_CLIENT
    if _FALLBACK_CLIENT is not None:
        return _FALLBACK_CLIENT
    async with _lock():
        if _FALLBACK_CLIENT is None:
            _FALLBACK_CLIENT = EmbeddingClient.from_settings()
    return _FALLBACK_CLIENT


async def reset_embedding_client() -> None:
    """Test-only: drop the cached fallback client (and close it)."""
    global _FALLBACK_CLIENT
    if _FALLBACK_CLIENT is not None:
        await _FALLBACK_CLIENT.aclose()
        _FALLBACK_CLIENT = None
