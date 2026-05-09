"""Top-k cosine retrieval over `grounding_chunks`.

Postgres-only path — uses pgvector's `<=>` cosine-distance operator. The
default `source_kinds` is the set of "candidate-deep" corpora the model
answer is allowed to draw from. Phase 14: `('project_doc',)`. Phase 15
will widen the default to `('project_doc', 'github')` here in this file —
no schema migration needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import bindparam, text

from interview_coach.db.session import AsyncSessionLocal
from interview_coach.observability.langfuse import span
from interview_coach.rag.embeddings import embed_query


@dataclass
class GroundingHit:
    text: str
    document_id: uuid.UUID
    source_doc_kind: str
    chunk_index: int
    score: float
    filename: str


async def retrieve_grounding(
    *,
    user_id: uuid.UUID,
    query: str,
    k: int = 4,
    source_kinds: tuple[str, ...] = ("project_doc",),
) -> list[GroundingHit]:
    """Embed the query, then return the top-k chunks belonging to `user_id`
    whose `source_doc_kind` is in `source_kinds`. Empty input string or no
    matching rows return `[]`.
    """
    if not query.strip() or not source_kinds:
        return []

    with span(
        "rag.retrieve_grounding",
        input={"query": query, "k": k, "source_kinds": list(source_kinds)},
        metadata={"user_id": str(user_id)},
    ) as obs:
        qvec = await embed_query(query)
        hits = await _vector_search(qvec=qvec, user_id=user_id, source_kinds=source_kinds, k=k)
        if obs is not None:
            try:
                obs.update(
                    output={
                        "n_hits": len(hits),
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
    k: int,
) -> list[GroundingHit]:
    sql = text(
        """
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
         ORDER BY gc.embedding <=> CAST(:qvec AS vector)
         LIMIT :k
        """
    ).bindparams(bindparam("kinds", expanding=False))

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sql,
            {
                "qvec": _to_pgvector_literal(qvec),
                "uid": str(user_id),
                "kinds": list(source_kinds),
                "k": k,
            },
        )
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
