"""Top-k cosine retrieval over `grounding_chunks`.

Postgres-only path — uses pgvector's `<=>` cosine-distance operator. The
default `source_kinds` is the set of "candidate-deep" corpora the model
answer is allowed to draw from. Phase 14.1: optional ``document_ids`` filter
lets the evaluator scope retrieval to the project_doc(s) the question was
about, when the question generator pre-picked a focus with doc provenance.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import bindparam, text

from interview_coach.db.session import AsyncSessionLocal
from interview_coach.observability.langfuse import span
from interview_coach.rag import get_embedding_client
from interview_coach.rag.client import EmbedderUnavailable


@dataclass
class GroundingHit:
    text: str
    document_id: uuid.UUID
    source_doc_kind: str
    chunk_index: int
    score: float
    filename: str


MIN_GROUNDING_SCORE = 0.5
"""Cosine-similarity floor for a chunk to be considered grounding.

L2-normalized vectors → cosine sim in [-1, 1]. Empirically Jina v3
``retrieval.query`` ↔ ``retrieval.passage`` matches a relevant chunk at
~0.55–0.75. A floor of 0.5 keeps the truly-on-topic chunks and drops noise
that would otherwise pollute the model-answer prompt when the question is
about something the corpus doesn't cover.
"""


async def retrieve_grounding(
    *,
    user_id: uuid.UUID,
    query: str,
    k: int = 4,
    source_kinds: tuple[str, ...] = ("project_doc",),
    document_ids: tuple[uuid.UUID, ...] = (),
    min_score: float = MIN_GROUNDING_SCORE,
) -> list[GroundingHit]:
    """Embed the query, then return the top-k chunks belonging to `user_id`
    whose `source_doc_kind` is in `source_kinds`. When `document_ids` is
    non-empty, results are further scoped to chunks from those specific docs
    (used when the question generator pinned a focus to a project_doc).

    Hits with cosine similarity below ``min_score`` are dropped so the
    model-answer prompt never gets fed weakly-related chunks.

    Empty input string or no matching rows return `[]`.
    """
    if not query.strip() or not source_kinds:
        return []

    with span(
        "rag.retrieve_grounding",
        input={
            "query": query,
            "k": k,
            "source_kinds": list(source_kinds),
            "document_ids": [str(d) for d in document_ids],
            "min_score": min_score,
        },
        metadata={"user_id": str(user_id)},
    ) as obs:
        client = await get_embedding_client()
        try:
            qvec = await client.embed_query(query)
        except EmbedderUnavailable:
            # Degrade gracefully: the evaluator can still produce a
            # non-grounded model answer rather than 500-ing the turn.
            return []
        raw_hits = await _vector_search(
            qvec=qvec,
            user_id=user_id,
            source_kinds=source_kinds,
            document_ids=document_ids,
            k=k,
        )
        hits = [h for h in raw_hits if h.score >= min_score]
        if obs is not None:
            try:
                obs.update(
                    output={
                        "n_hits": len(hits),
                        "n_dropped_below_min_score": len(raw_hits) - len(hits),
                        "hits": [
                            {
                                "filename": h.filename,
                                "source_doc_kind": h.source_doc_kind,
                                "chunk_index": h.chunk_index,
                                "score": round(h.score, 4),
                            }
                            for h in hits
                        ],
                    }
                )
            except Exception:  # noqa: BLE001
                pass
        return hits


async def _vector_search(
    *,
    qvec: list[float],
    user_id: uuid.UUID,
    source_kinds: tuple[str, ...],
    document_ids: tuple[uuid.UUID, ...],
    k: int,
) -> list[GroundingHit]:
    doc_clause = ""
    if document_ids:
        doc_clause = "           AND gc.document_id = ANY(:doc_ids)\n"

    sql = text(
        f"""
        SELECT gc.text AS text,
               gc.document_id AS document_id,
               gc.source_doc_kind AS source_doc_kind,
               gc.chunk_index AS chunk_index,
               d.filename AS filename,
               1 - (gc.embedding <=> CAST(:qvec AS vector)) AS score
          FROM grounding_chunks gc
          JOIN documents d ON d.id = gc.document_id
         WHERE gc.user_id = :uid
           AND gc.source_doc_kind = ANY(:kinds)
{doc_clause}         ORDER BY gc.embedding <=> CAST(:qvec AS vector)
         LIMIT :k
        """
    ).bindparams(bindparam("kinds", expanding=False))

    params: dict[str, object] = {
        "qvec": _to_pgvector_literal(qvec),
        "uid": str(user_id),
        "kinds": list(source_kinds),
        "k": k,
    }
    if document_ids:
        params["doc_ids"] = [str(d) for d in document_ids]

    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, params)
        rows = result.mappings().all()

    return [
        GroundingHit(
            text=r["text"],
            document_id=r["document_id"],
            source_doc_kind=r["source_doc_kind"],
            chunk_index=r["chunk_index"],
            score=float(r["score"]),
            filename=r["filename"],
        )
        for r in rows
    ]


def _to_pgvector_literal(vec: list[float]) -> str:
    """pgvector accepts the `'[1,2,3]'` text literal cast to `vector`."""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
