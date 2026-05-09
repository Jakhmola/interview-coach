"""Embed-and-store orchestration for a single document.

Idempotent: existing chunks for the document are deleted first, then the
new chunks are inserted in one transaction-ish sequence. Used both by the
upload route (best-effort, errors logged but not propagated to the user)
and by `scripts/backfill_grounding.py` (errors propagate).
"""

from __future__ import annotations

import logging
import uuid

from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.rag.chunking import chunk_text
from interview_coach.rag.embeddings import MODEL_NAME, embed_passages, get_model

logger = logging.getLogger(__name__)


async def embed_and_store_document(document_id: uuid.UUID) -> int:
    """Re-embed the document with `document_id`. Returns chunk count.

    Workflow:
      1. Load the document row.
      2. Token-window chunk the raw text.
      3. Encode each chunk with Jina v3 (`retrieval.passage`).
      4. Delete prior chunks for this doc (idempotency), then insert.
    """
    from interview_coach.db.models import Document

    async with AsyncSessionLocal() as s:
        doc = await s.get(Document, document_id)
    if doc is None:
        raise ValueError(f"document {document_id} not found")

    model = await get_model()
    chunks = chunk_text(doc.raw_text, tokenizer=model.tokenizer)
    if not chunks:
        logger.info("doc %s produced 0 chunks; skipping", document_id)
        # Still wipe stale chunks if any — keeps the table consistent.
        async with AsyncSessionLocal() as s:
            await repos.delete_grounding_chunks_for_document(s, document_id)
        return 0

    vectors = await embed_passages([c.text for c in chunks])
    payload = [
        {
            "chunk_index": c.chunk_index,
            "text": c.text,
            "n_tokens": c.n_tokens,
            "embedding": v,
        }
        for c, v in zip(chunks, vectors, strict=True)
    ]

    async with AsyncSessionLocal() as s:
        await repos.delete_grounding_chunks_for_document(s, document_id)
        n = await repos.insert_grounding_chunks(
            s,
            user_id=doc.user_id,
            document_id=doc.id,
            source_doc_kind=doc.kind,
            chunks=payload,
            model_name=MODEL_NAME,
        )

    logger.info("embedded %d chunks for doc %s (kind=%s)", n, document_id, doc.kind)
    return n
