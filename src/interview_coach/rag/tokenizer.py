"""Tokenizer-only loader for the Jina v3 chunker.

Phase 17: the embedding model moves out of the api container into the
`embedder` sidecar, but chunking still needs to count tokens with the
*exact* tokenizer the embedder uses, so chunk boundaries match what the
sidecar sees. Loading just `AutoTokenizer.from_pretrained(MODEL_NAME,
trust_remote_code=True)` keeps `transformers` in the api closure (~150MB)
without pulling `torch` / `sentence-transformers` (~1.5GB combined).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

MODEL_NAME = "jinaai/jina-embeddings-v3"

_TOKENIZER: Any | None = None
_LOAD_LOCK: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _LOAD_LOCK
    if _LOAD_LOCK is None:
        _LOAD_LOCK = asyncio.Lock()
    return _LOAD_LOCK


async def get_tokenizer() -> Any:
    """Returns a loaded `PreTrainedTokenizerBase`. Loads on first call."""
    global _TOKENIZER
    if _TOKENIZER is not None:
        return _TOKENIZER
    async with _get_lock():
        if _TOKENIZER is not None:
            return _TOKENIZER
        logger.info("Loading tokenizer-only for %s", MODEL_NAME)
        from transformers import AutoTokenizer

        loop = asyncio.get_running_loop()
        _TOKENIZER = await loop.run_in_executor(
            None,
            lambda: AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True),
        )
        logger.info("Tokenizer loaded")
        return _TOKENIZER
