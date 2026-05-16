"""Startup-time assertion that the embedder sidecar's model matches.

Phase 17: existing `grounding_chunks` rows are tagged with
``model_name = "jinaai/jina-embeddings-v3"`` and vector dim 1024.
Booting against any other model silently corrupts retrieval, so the api
refuses to start if `/model` reports a mismatch.
"""

from __future__ import annotations

import logging

from interview_coach.rag.client import (
    EXPECTED_DIM,
    EXPECTED_MODEL_NAME,
    EmbeddingClient,
)

logger = logging.getLogger(__name__)


class EmbedderModelMismatch(RuntimeError):
    """Raised when the embedder reports a model name or dim that doesn't
    match what `grounding_chunks` was written with."""


async def assert_embedder_model(client: EmbeddingClient) -> None:
    """Compare `GET /model` against the api's expected name and dim.

    Raises `EmbedderModelMismatch` on a name/dim mismatch. Network errors
    propagate as-is — the caller (FastAPI lifespan) should decide whether
    to fail boot or log-and-continue.
    """
    info = await client.model_info()
    name = info.get("name")
    dim = info.get("dim")
    if name != EXPECTED_MODEL_NAME or dim != EXPECTED_DIM:
        raise EmbedderModelMismatch(
            f"embedder reports name={name!r} dim={dim!r}; "
            f"expected name={EXPECTED_MODEL_NAME!r} dim={EXPECTED_DIM!r}"
        )
    logger.info("embedder model lock OK: name=%s dim=%d", name, dim)
