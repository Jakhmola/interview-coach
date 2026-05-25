"""HTTP client for the `embedder` sidecar.

Same surface as the in-process `embed_passages` / `embed_query` that lived
in `interview_coach.rag.embeddings` before Phase 17, so callers swap with
a one-line import change.

The client is intended to be a long-lived singleton:
- FastAPI lifespan builds one and stores it on `app.state.embedding_client`.
- MCP subprocesses (no `app.state`) build their own via `from_settings()`.
- Tests can monkeypatch the two `embed_*` methods or stub the httpx
  transport via `respx` (see tests/test_embedding_client.py).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from interview_coach.config import settings
from interview_coach.observability.langfuse import span
from interview_coach.rag.model_identity import EMBEDDING_DIM, EMBEDDING_MODEL_NAME

logger = logging.getLogger(__name__)

EXPECTED_MODEL_NAME = EMBEDDING_MODEL_NAME
EXPECTED_DIM = EMBEDDING_DIM

Task = Literal["retrieval.passage", "retrieval.query"]


class EmbedderUnavailable(RuntimeError):
    """Raised when the embedder service can't be reached or returns 5xx
    after exhausting retries. Callers that can tolerate degraded RAG
    should catch this; callers that can't (ingestion) let it propagate.
    """


class EmbeddingClient:
    """Thin httpx wrapper around the embedder sidecar."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 60.0,
        retries: int = 3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._retries = max(1, retries)
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls) -> EmbeddingClient:
        return cls(
            base_url=settings.embedder_url,
            timeout_s=settings.embedder_timeout_s,
            retries=settings.embedder_retries,
        )

    async def _get(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=self._timeout_s,
                )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def model_info(self) -> dict:
        """Returns `{"name": ..., "dim": ...}`. Used at boot for the
        same-model lock. Single attempt — we want loud failure if the
        sidecar contract drifted."""
        client = await self._get()
        resp = await client.get("/model")
        resp.raise_for_status()
        return resp.json()

    async def _embed(
        self, texts: list[str], task: Task, *, retries: int | None = None
    ) -> list[list[float]]:
        """Embed ``texts``. Per-call ``retries`` override the instance
        default — used by hot-path callers (e.g. evaluator's grounding
        retrieval) that prefer fast graceful degradation over piling
        retries on an already-overloaded embedder. ``None`` means use
        ``self._retries`` (set from ``settings.embedder_retries`` at
        construction).
        """
        if not texts:
            return []
        retry_attempts = max(1, retries if retries is not None else self._retries)
        retrying = AsyncRetrying(
            stop=stop_after_attempt(retry_attempts),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=5.0),
            retry=retry_if_exception_type(
                (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)
            ),
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    client = await self._get()
                    resp = await client.post(
                        "/embed",
                        json={"texts": texts, "task": task},
                    )
                    if resp.status_code >= 500:
                        # 503 from a still-loading sidecar should trigger
                        # the retry path as well.
                        raise httpx.RemoteProtocolError(
                            f"embedder {resp.status_code}: {resp.text[:200]}",
                            request=resp.request,
                        )
                    resp.raise_for_status()
                    payload = resp.json()
                    return payload["vectors"]
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            raise EmbedderUnavailable(str(e)) from e
        # Unreachable — AsyncRetrying either returns or raises.
        return []  # pragma: no cover

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with span(
            "embed.passages",
            input={"n_texts": len(texts), "total_chars": sum(len(t) for t in texts)},
            metadata={"model": EXPECTED_MODEL_NAME, "task": "retrieval.passage"},
        ):
            return await self._embed(texts, "retrieval.passage")

    async def embed_query(self, text: str, *, retries: int | None = None) -> list[float]:
        """Embed a query for retrieval. ``retries`` overrides the instance
        default; callers in the per-turn hot path (evaluator) pass 1 so
        a transiently slow embedder fails fast and the model-answer call
        falls back to no-grounding (already a graceful path in
        ``retrieve_grounding``)."""
        with span(
            "embed.query",
            input={"query": text},
            metadata={"model": EXPECTED_MODEL_NAME, "task": "retrieval.query"},
        ):
            vectors = await self._embed([text], "retrieval.query", retries=retries)
            return vectors[0] if vectors else []
