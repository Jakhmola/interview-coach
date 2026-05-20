"""Hybrid retrieval: BM25 (Postgres FTS) + vector (pgvector) via RRF.

The vector branch is the same `1 - cosine_distance` ranking used by the
legacy `_vector_search` helper. The BM25 branch uses
`websearch_to_tsquery('english', :q)` (forgiving query parser — handles
plain words, AND/OR/-, quoted phrases) and `ts_rank_cd(text_tsv, q)` for
scoring. Both branches read `candidate_k` rows; results are fused with
Reciprocal Rank Fusion (`fused = sum(1 / (rrf_k + rank_in_modality))`).

Score-floor nuance
------------------

`retrieve_grounding` historically applied `MIN_GROUNDING_SCORE = 0.5`
against raw cosine similarity. In hybrid mode that floor doesn't make
sense on the RRF score (its range is unrelated to cosine). Instead, the
floor is applied *before* fusion to vector-branch hits only — a chunk
whose only signal is BM25 (the exact-token catch hybrid is meant to add)
is allowed through unconditionally, because the lexical match is itself
evidence the chunk is relevant.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text

from interview_coach.db.session import AsyncSessionLocal
from interview_coach.rag.retrieval import GroundingHit


async def hybrid_search(
    *,
    qvec: list[float] | None,
    query_text: str,
    user_id: uuid.UUID,
    source_kinds: tuple[str, ...],
    document_ids: tuple[uuid.UUID, ...],
    k: int,
    candidate_k: int = 20,
    rrf_k: int = 60,
    min_cosine_score: float = 0.0,
) -> tuple[list[GroundingHit], dict[str, int]]:
    """Run BM25 + vector in parallel and fuse with RRF. Returns (hits, stats).

    `qvec=None` means embedder is down — degrade to BM25-only (better than
    returning []). `min_cosine_score` is the cosine floor applied to the
    vector branch *before* fusion; pass 0.0 to disable.
    """
    if qvec is None:
        bm25_hits = await _bm25_search(
            query_text=query_text,
            user_id=user_id,
            source_kinds=source_kinds,
            document_ids=document_ids,
            k=candidate_k,
        )
        fused = _rrf_fuse(bm25_hits=bm25_hits, vector_hits=[], rrf_k=rrf_k, k=k)
        return fused, {
            "n_bm25_hits": len(bm25_hits),
            "n_vector_hits": 0,
            "n_fused_hits": len(fused),
        }

    bm25_hits, vector_hits = await asyncio.gather(
        _bm25_search(
            query_text=query_text,
            user_id=user_id,
            source_kinds=source_kinds,
            document_ids=document_ids,
            k=candidate_k,
        ),
        _vector_search_raw(
            qvec=qvec,
            user_id=user_id,
            source_kinds=source_kinds,
            document_ids=document_ids,
            k=candidate_k,
        ),
    )

    if min_cosine_score > 0.0:
        vector_hits = [h for h in vector_hits if (h.cosine_score or 0.0) >= min_cosine_score]

    fused = _rrf_fuse(bm25_hits=bm25_hits, vector_hits=vector_hits, rrf_k=rrf_k, k=k)
    return fused, {
        "n_bm25_hits": len(bm25_hits),
        "n_vector_hits": len(vector_hits),
        "n_fused_hits": len(fused),
    }


async def _bm25_search(
    *,
    query_text: str,
    user_id: uuid.UUID,
    source_kinds: tuple[str, ...],
    document_ids: tuple[uuid.UUID, ...],
    k: int,
) -> list[GroundingHit]:
    """Postgres FTS branch. Returns `GroundingHit` with `bm25_score` populated.

    `websearch_to_tsquery` is forgiving: empty strings produce an empty
    tsquery that matches nothing, so no extra guard needed. Filters out
    rows where the tsquery doesn't actually match (rank > 0).
    """
    if not query_text.strip():
        return []

    doc_clause = ""
    if document_ids:
        doc_clause = "           AND gc.document_id = ANY(:doc_ids)\n"

    sql = text(
        f"""
        SELECT gc.text AS text,
               gc.document_id AS document_id,
               gc.source_doc_kind AS source_doc_kind,
               gc.chunk_index AS chunk_index,
               gc.id AS chunk_id,
               d.filename AS filename,
               ts_rank_cd(gc.text_tsv, q) AS score
          FROM grounding_chunks gc
          JOIN documents d ON d.id = gc.document_id,
               websearch_to_tsquery('english', :q) AS q
         WHERE gc.user_id = :uid
           AND gc.source_doc_kind = ANY(:kinds)
           AND gc.text_tsv @@ q
{doc_clause}         ORDER BY score DESC
         LIMIT :k
        """
    )

    params: dict[str, object] = {
        "q": query_text,
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
            score=0.0,  # placeholder — final score is the fused RRF score
            filename=r["filename"],
            cosine_score=None,
            bm25_score=float(r["score"]),
        )
        for r in rows
    ]


async def _vector_search_raw(
    *,
    qvec: list[float],
    user_id: uuid.UUID,
    source_kinds: tuple[str, ...],
    document_ids: tuple[uuid.UUID, ...],
    k: int,
) -> list[GroundingHit]:
    """pgvector branch — same SQL as `retrieval._vector_search` but populates
    `cosine_score` instead of `score` so fusion stays unambiguous about which
    component contributed what.
    """
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
    )

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
            score=0.0,  # placeholder — final score is the fused RRF score
            filename=r["filename"],
            cosine_score=float(r["score"]),
            bm25_score=None,
        )
        for r in rows
    ]


def _rrf_fuse(
    *,
    bm25_hits: list[GroundingHit],
    vector_hits: list[GroundingHit],
    rrf_k: int,
    k: int,
) -> list[GroundingHit]:
    """Reciprocal Rank Fusion of two ranked hit lists.

    Each hit's contribution is `1 / (rrf_k + rank_in_modality)` (1-indexed
    rank). A chunk appearing in both lists sums both contributions. Output
    is sorted desc by fused score, truncated to `k`.

    Identity is (document_id, chunk_index) — that is the existing unique
    key on `grounding_chunks` and avoids assuming we have the chunk UUID.
    """
    by_id: dict[tuple[uuid.UUID, int], GroundingHit] = {}
    fused_score: dict[tuple[uuid.UUID, int], float] = {}

    def _accumulate(hits: list[GroundingHit], get_score: object) -> None:  # noqa: ARG001
        for rank, h in enumerate(hits, start=1):
            key = (h.document_id, h.chunk_index)
            contribution = 1.0 / (rrf_k + rank)
            fused_score[key] = fused_score.get(key, 0.0) + contribution
            if key in by_id:
                # Merge component scores from the second-modality copy.
                existing = by_id[key]
                if h.cosine_score is not None:
                    existing.cosine_score = h.cosine_score
                if h.bm25_score is not None:
                    existing.bm25_score = h.bm25_score
            else:
                by_id[key] = h

    _accumulate(bm25_hits, None)
    _accumulate(vector_hits, None)

    ordered = sorted(by_id.keys(), key=lambda key: fused_score[key], reverse=True)
    out: list[GroundingHit] = []
    for key in ordered[:k]:
        hit = by_id[key]
        hit.score = fused_score[key]
        out.append(hit)
    return out


def _to_pgvector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
