"""Failure-mode tests: what happens when the embedder sidecar is down.

The contract is asymmetric:
- Ingestion must surface the failure (the upload route catches it as
  best-effort, but the function itself should raise).
- Retrieval must degrade gracefully — return [] so the evaluator can
  still produce a non-grounded model answer rather than 500-ing.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from interview_coach.rag import reset_embedding_client
from interview_coach.rag.client import EmbedderUnavailable, EmbeddingClient


def _broken_client(status: int = 503) -> EmbeddingClient:
    """Build an EmbeddingClient whose underlying httpx always returns 5xx."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="down")

    client = EmbeddingClient(base_url="http://broken", timeout_s=0.5, retries=2)
    client._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),
        base_url="http://broken",
        timeout=0.5,
    )
    return client


@pytest.fixture
async def install_broken_client(monkeypatch: pytest.MonkeyPatch):
    """Force `get_embedding_client()` to return a broken client for the
    duration of the test, then reset the singleton.
    """
    import interview_coach.rag as rag_pkg

    broken = _broken_client()

    async def _fake() -> EmbeddingClient:
        return broken

    monkeypatch.setattr(rag_pkg, "get_embedding_client", _fake)
    yield broken
    await broken.aclose()
    await reset_embedding_client()


async def test_retrieve_grounding_returns_empty_when_embedder_down(
    install_broken_client: EmbeddingClient,
) -> None:
    import interview_coach.rag.retrieval as retrieval_mod

    async def _fake_client() -> EmbeddingClient:
        return install_broken_client

    # The module imports `get_embedding_client` at module load — patch
    # the binding inside the retrieval module too.
    object.__setattr__(retrieval_mod, "get_embedding_client", _fake_client)

    hits = await retrieval_mod.retrieve_grounding(
        user_id=uuid.uuid4(),
        query="anything",
    )
    assert hits == []


async def test_embed_passages_raises_when_embedder_down(
    install_broken_client: EmbeddingClient,
) -> None:
    with pytest.raises(EmbedderUnavailable):
        await install_broken_client.embed_passages(["chunk one", "chunk two"])
