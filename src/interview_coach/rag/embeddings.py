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
import os
from typing import Any

from interview_coach.observability.langfuse import span

logger = logging.getLogger(__name__)

MODEL_NAME = "jinaai/jina-embeddings-v3"
EMBEDDING_DIM = 1024


# Cap CPU usage so the host (IDE, browser, etc.) keeps headroom. Defaults
# to leaving ~4 cores free on the box. Override via EMBED_THREADS env.
def _resolve_thread_cap() -> int:
    raw = os.environ.get("EMBED_THREADS")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    cpu = os.cpu_count() or 4
    return max(1, cpu - 4)


_THREAD_CAP = _resolve_thread_cap()
# Apply BEFORE torch/HF import so the BLAS/OMP backends pick these up.
os.environ.setdefault("OMP_NUM_THREADS", str(_THREAD_CAP))
os.environ.setdefault("MKL_NUM_THREADS", str(_THREAD_CAP))
os.environ.setdefault("OPENBLAS_NUM_THREADS", str(_THREAD_CAP))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

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
        logger.info(
            "Loading Jina v3 model %s (cold load, ~5–10s; thread cap=%d)",
            MODEL_NAME,
            _THREAD_CAP,
        )
        # Heavy import deferred until first use.
        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(_THREAD_CAP)
        try:
            torch.set_num_interop_threads(max(1, _THREAD_CAP // 2))
        except RuntimeError:
            # set_num_interop_threads must be called before any parallel work;
            # if torch was already touched elsewhere this raises — non-fatal.
            pass

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
