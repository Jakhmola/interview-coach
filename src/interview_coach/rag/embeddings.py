"""Jina v3 embedding singleton.

Lazy: the model (~500MB) loads on first use, not at FastAPI startup. We
pre-emptively settle for the safe-fallback chunk-then-encode path described
in the Phase 14 plan; true late-chunking would need token-level pooling
which the sentence-transformers wrapper around Jina v3 doesn't expose
cleanly without surgery. This is the explicit safe path.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from interview_coach.observability.langfuse import span

logger = logging.getLogger(__name__)

MODEL_NAME = "jinaai/jina-embeddings-v3"
EMBEDDING_DIM = 1024

_MODEL: Any | None = None
_LOAD_LOCK: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _LOAD_LOCK
    if _LOAD_LOCK is None:
        _LOAD_LOCK = asyncio.Lock()
    return _LOAD_LOCK


async def get_model() -> Any:
    """Returns a loaded `SentenceTransformer`. Loads on first call."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    async with _get_lock():
        if _MODEL is not None:
            return _MODEL
        logger.info("Loading Jina v3 model %s (cold load, ~5–10s)", MODEL_NAME)
        # Heavy import deferred until first use.
        from sentence_transformers import SentenceTransformer

        loop = asyncio.get_running_loop()
        model = await loop.run_in_executor(
            None,
            lambda: SentenceTransformer(MODEL_NAME, trust_remote_code=True),
        )
        _MODEL = model
        logger.info("Jina v3 loaded")
        return _MODEL


def get_tokenizer(model: Any) -> Any:
    """Pull the underlying HF tokenizer from a loaded SentenceTransformer."""
    return model.tokenizer


async def embed_passages(texts: list[str]) -> list[list[float]]:
    """Encode a batch of chunk strings as `retrieval.passage` vectors.

    Returns L2-normalized 1024-d float lists. Empty input → empty output.
    """
    if not texts:
        return []
    with span(
        "embed.passages",
        input={"n_texts": len(texts), "total_chars": sum(len(t) for t in texts)},
        metadata={"model": MODEL_NAME, "task": "retrieval.passage"},
    ):
        model = await get_model()
        loop = asyncio.get_running_loop()
        vectors = await loop.run_in_executor(
            None,
            lambda: model.encode(
                texts,
                task="retrieval.passage",
                normalize_embeddings=True,
                convert_to_numpy=True,
            ),
        )
        return [v.tolist() for v in vectors]


async def embed_query(text: str) -> list[float]:
    """Encode a single retrieval query."""
    with span(
        "embed.query",
        input={"query": text},
        metadata={"model": MODEL_NAME, "task": "retrieval.query"},
    ):
        model = await get_model()
        loop = asyncio.get_running_loop()
        vec = await loop.run_in_executor(
            None,
            lambda: model.encode(
                [text],
                task="retrieval.query",
                normalize_embeddings=True,
                convert_to_numpy=True,
            )[0],
        )
        return vec.tolist()
