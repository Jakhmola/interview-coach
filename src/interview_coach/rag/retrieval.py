"""Top-k retrieval over `grounding_chunks` — vector or hybrid (BM25+vector).

Phase 24: this module is the dispatcher. The pure pgvector cosine path is
kept here (`_vector_search`); the hybrid (BM25 + vector with RRF) path
lives in `rag.hybrid`. Which path runs is controlled by
`settings.retrieval_mode` (env: `RETRIEVAL_MODE`), defaulting to `hybrid`
with `vector` as a one-release safety net.

The default `source_kinds` is the set of "candidate-deep" corpora the model
answer is allowed to draw from. Phase 14.1: optional ``document_ids`` filter
lets the evaluator scope retrieval to the project_doc(s) the question was
about, when the question generator pre-picked a focus with doc provenance.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, text

from interview_coach.config import settings
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
    # Phase 24: hybrid retrieval telemetry. In `vector` mode `cosine_score`
    # equals `score` and `bm25_score` is None; in `hybrid` mode `score` is
    # the fused RRF score and the two component scores carry the underlying
    # signals (either may be None when the hit only fired in one branch).
    cosine_score: float | None = None
    bm25_score: float | None = None


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
    retries: int | None = None,
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

    mode = (settings.retrieval_mode or "hybrid").lower()
    if mode not in ("hybrid", "vector"):
        mode = "hybrid"

    with span(
        "rag.retrieve_grounding",
        input={
            "query": query,
            "k": k,
            "source_kinds": list(source_kinds),
            "document_ids": [str(d) for d in document_ids],
            "min_score": min_score,
            "mode": mode,
        },
        metadata={"user_id": str(user_id)},
    ) as obs:
        client = await get_embedding_client()
        qvec: list[float] | None
        try:
            qvec = await client.embed_query(query, retries=retries)
        except EmbedderUnavailable:
            qvec = None

        if mode == "vector":
            if qvec is None:
                # Legacy behavior: vector-only can't recover from a dead embedder.
                _update_obs(obs, output={"n_hits": 0, "mode": mode, "embedder_down": True})
                return []
            raw_hits = await _vector_search(
                qvec=qvec,
                user_id=user_id,
                source_kinds=source_kinds,
                document_ids=document_ids,
                k=k,
            )
            hits = [h for h in raw_hits if h.score >= min_score]
            _update_obs(
                obs,
                output={
                    "n_hits": len(hits),
                    "n_dropped_below_min_score": len(raw_hits) - len(hits),
                    "mode": mode,
                    "hits": _telemetry_hits(hits),
                },
            )
            return hits

        # mode == "hybrid"
        from interview_coach.rag.hybrid import hybrid_search

        hits, stats = await hybrid_search(
            qvec=qvec,
            query_text=query,
            user_id=user_id,
            source_kinds=source_kinds,
            document_ids=document_ids,
            k=k,
            candidate_k=settings.hybrid_candidate_k,
            rrf_k=settings.rrf_k,
            min_cosine_score=min_score,
        )
        _update_obs(
            obs,
            output={
                "n_hits": len(hits),
                "mode": mode,
                "embedder_down": qvec is None,
                "hits": _telemetry_hits(hits),
                **stats,
            },
        )
        return hits


def _telemetry_hits(hits: list[GroundingHit]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for h in hits:
        row: dict[str, object] = {
            "filename": h.filename,
            "source_doc_kind": h.source_doc_kind,
            "chunk_index": h.chunk_index,
            "score": round(h.score, 4),
        }
        if h.cosine_score is not None:
            row["cosine_score"] = round(h.cosine_score, 4)
        if h.bm25_score is not None:
            row["bm25_score"] = round(h.bm25_score, 4)
        out.append(row)
    return out


def _update_obs(obs: object, *, output: dict[str, object]) -> None:
    if obs is None:
        return
    try:
        obs.update(output=output)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


async def _query_chunks_by_vector(
    *,
    qvec: list[float],
    user_id: uuid.UUID,
    source_kinds: tuple[str, ...],
    document_ids: tuple[uuid.UUID, ...],
    k: int,
) -> Sequence[Mapping[str, Any]]:
    """Single owner of the ``grounding_chunks`` pgvector cosine query.

    Returns the raw row mappings (``score`` is ``1 - cosine_distance``). The
    vector-only path (``_vector_search``) and the hybrid path
    (``hybrid._vector_search_raw``) each map these rows to ``GroundingHit``
    their own way — the cosine lands in ``score`` vs. ``cosine_score``.
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
        return result.mappings().all()


async def _vector_search(
    *,
    qvec: list[float],
    user_id: uuid.UUID,
    source_kinds: tuple[str, ...],
    document_ids: tuple[uuid.UUID, ...],
    k: int,
) -> list[GroundingHit]:
    rows = await _query_chunks_by_vector(
        qvec=qvec,
        user_id=user_id,
        source_kinds=source_kinds,
        document_ids=document_ids,
        k=k,
    )
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
