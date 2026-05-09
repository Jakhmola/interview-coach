"""Pure-text token-window chunker.

We aim for 400-token windows with 50-token overlap. Tokens are counted with
the same tokenizer the Jina v3 model uses, fetched once via the embedder
singleton. Each chunk is decoded back to a string so it's also human-readable
when stored / inspected.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TextChunk:
    chunk_index: int
    text: str
    n_tokens: int


def chunk_text(
    text: str,
    *,
    tokenizer,  # transformers.PreTrainedTokenizerBase  # noqa: ANN001
    window: int = 400,
    overlap: int = 50,
) -> list[TextChunk]:
    """Split `text` into overlapping token-windows.

    Empty / whitespace-only input yields an empty list (caller decides what
    to do — typically skip the doc).
    """
    text = text.strip()
    if not text:
        return []

    enc = tokenizer(text, add_special_tokens=False, return_tensors=None)
    ids: list[int] = list(enc["input_ids"])
    if not ids:
        return []

    if window <= 0:
        raise ValueError("window must be > 0")
    if overlap < 0 or overlap >= window:
        raise ValueError("overlap must be in [0, window)")

    step = window - overlap
    chunks: list[TextChunk] = []
    start = 0
    idx = 0
    while start < len(ids):
        end = min(start + window, len(ids))
        slice_ids = ids[start:end]
        chunk_text = tokenizer.decode(slice_ids, skip_special_tokens=True).strip()
        if chunk_text:
            chunks.append(TextChunk(chunk_index=idx, text=chunk_text, n_tokens=len(slice_ids)))
            idx += 1
        if end == len(ids):
            break
        start += step
    return chunks
