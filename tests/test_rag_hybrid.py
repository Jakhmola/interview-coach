"""Unit tests for the hybrid-retrieval RRF fusion + dispatcher behavior.

The SQL branches (`_bm25_search` / `_vector_search_raw`) are exercised by
the integration eval harness and the Phase-24 smoke test against pgvector;
this file covers the pure-Python pieces (RRF fusion math, dispatcher
mode selection, BM25-only fallback when the embedder is down).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from interview_coach.rag import hybrid
from interview_coach.rag.retrieval import GroundingHit


def _hit(
    *,
    doc: uuid.UUID,
    idx: int,
    cosine: float | None = None,
    bm25: float | None = None,
) -> GroundingHit:
    return GroundingHit(
        text=f"chunk {idx}",
        document_id=doc,
        source_doc_kind="project_doc",
        chunk_index=idx,
        score=0.0,
        filename="doc.md",
        cosine_score=cosine,
        bm25_score=bm25,
    )


def test_rrf_fuse_overlap_chunk_outranks_single_modality() -> None:
    """A chunk that appears in both lists must rank above a chunk that
    appears in only one list at the same rank — that's the whole point
    of RRF.
    """
    doc = uuid.uuid4()
    both = _hit(doc=doc, idx=0, bm25=1.0)  # rank 1 in bm25
    only_bm25 = _hit(doc=doc, idx=1, bm25=0.9)  # rank 2 in bm25
    only_vector = _hit(doc=doc, idx=2, cosine=0.8)  # rank 2 in vector
    both_vec = _hit(doc=doc, idx=0, cosine=0.9)  # rank 1 in vector (same chunk as `both`)

    fused = hybrid._rrf_fuse(
        bm25_hits=[both, only_bm25],
        vector_hits=[both_vec, only_vector],
        rrf_k=60,
        k=10,
    )

    assert len(fused) == 3
    assert (fused[0].document_id, fused[0].chunk_index) == (doc, 0)
    # The merged hit carries both component scores
    assert fused[0].cosine_score == 0.9
    assert fused[0].bm25_score == 1.0


def test_rrf_fuse_truncates_to_k() -> None:
    doc = uuid.uuid4()
    bm25_hits = [_hit(doc=doc, idx=i, bm25=1.0 - 0.01 * i) for i in range(10)]
    fused = hybrid._rrf_fuse(bm25_hits=bm25_hits, vector_hits=[], rrf_k=60, k=4)
    assert len(fused) == 4
    # Top result must be rank-1 bm25 hit
    assert fused[0].chunk_index == 0


def test_rrf_fuse_sets_final_score_to_fused_value() -> None:
    """`GroundingHit.score` should be the fused RRF score, not the leftover
    placeholder 0.0, so the downstream score-floor still has something to bite.
    """
    doc = uuid.uuid4()
    hits = [_hit(doc=doc, idx=0, bm25=1.0)]
    fused = hybrid._rrf_fuse(bm25_hits=hits, vector_hits=[], rrf_k=60, k=4)
    expected = 1.0 / (60 + 1)
    assert abs(fused[0].score - expected) < 1e-9


async def test_hybrid_search_bm25_only_when_qvec_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """qvec=None (embedder down) must skip the vector branch entirely and
    return whatever BM25 finds — that's the graceful-degradation contract.
    """
    doc = uuid.uuid4()

    async def fake_bm25(**_: Any) -> list[GroundingHit]:
        return [_hit(doc=doc, idx=0, bm25=0.7), _hit(doc=doc, idx=1, bm25=0.4)]

    async def fake_vector(**_: Any) -> list[GroundingHit]:
        raise AssertionError("vector branch must not run when qvec is None")

    monkeypatch.setattr(hybrid, "_bm25_search", fake_bm25)
    monkeypatch.setattr(hybrid, "_vector_search_raw", fake_vector)

    hits, stats = await hybrid.hybrid_search(
        qvec=None,
        query_text="anything",
        user_id=uuid.uuid4(),
        source_kinds=("project_doc",),
        document_ids=(),
        k=4,
    )

    assert len(hits) == 2
    assert stats == {"n_bm25_hits": 2, "n_vector_hits": 0, "n_fused_hits": 2}
    assert all(h.bm25_score is not None and h.cosine_score is None for h in hits)


async def test_hybrid_search_filters_vector_branch_by_min_cosine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cosine floor drops weak vector hits *before* fusion; BM25-only
    hits with no cosine signal must still survive.
    """
    doc = uuid.uuid4()

    async def fake_bm25(**_: Any) -> list[GroundingHit]:
        return [_hit(doc=doc, idx=99, bm25=0.9)]

    async def fake_vector(**_: Any) -> list[GroundingHit]:
        return [
            _hit(doc=doc, idx=0, cosine=0.8),  # passes
            _hit(doc=doc, idx=1, cosine=0.3),  # filtered (< 0.5)
        ]

    monkeypatch.setattr(hybrid, "_bm25_search", fake_bm25)
    monkeypatch.setattr(hybrid, "_vector_search_raw", fake_vector)

    hits, stats = await hybrid.hybrid_search(
        qvec=[0.0] * 1024,
        query_text="anything",
        user_id=uuid.uuid4(),
        source_kinds=("project_doc",),
        document_ids=(),
        k=10,
        min_cosine_score=0.5,
    )

    chunk_indices = {h.chunk_index for h in hits}
    assert 0 in chunk_indices  # vector hit that passed the floor
    assert 99 in chunk_indices  # BM25-only hit — must survive
    assert 1 not in chunk_indices  # below-floor cosine hit dropped
    assert stats["n_bm25_hits"] == 1
    assert stats["n_vector_hits"] == 1


async def test_retrieve_grounding_dispatches_to_hybrid_when_mode_hybrid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`retrieve_grounding` must call `hybrid_search` and not the legacy
    `_vector_search` when `retrieval_mode='hybrid'`.
    """
    from interview_coach.rag import retrieval as retrieval_mod

    monkeypatch.setattr(retrieval_mod.settings, "retrieval_mode", "hybrid")

    called = {"hybrid": False, "vector": False}

    async def fake_hybrid_search(**_: Any) -> tuple[list[GroundingHit], dict[str, int]]:
        called["hybrid"] = True
        return [], {"n_bm25_hits": 0, "n_vector_hits": 0, "n_fused_hits": 0}

    async def fake_vector_search(**_: Any) -> list[GroundingHit]:
        called["vector"] = True
        return []

    monkeypatch.setattr(retrieval_mod, "_vector_search", fake_vector_search)
    # Patch the lazy import inside retrieve_grounding by injecting into the
    # hybrid module before the call.
    monkeypatch.setattr(hybrid, "hybrid_search", fake_hybrid_search)

    async def fake_get_client() -> Any:
        class _C:
            async def embed_query(self, *_a: Any, **_k: Any) -> list[float]:
                return [0.0] * 1024

        return _C()

    monkeypatch.setattr(retrieval_mod, "get_embedding_client", fake_get_client)

    await retrieval_mod.retrieve_grounding(user_id=uuid.uuid4(), query="anything")

    assert called["hybrid"] is True
    assert called["vector"] is False


async def test_retrieve_grounding_vector_mode_returns_empty_when_embedder_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy contract preserved under `retrieval_mode='vector'`."""
    from interview_coach.rag import retrieval as retrieval_mod
    from interview_coach.rag.client import EmbedderUnavailable

    monkeypatch.setattr(retrieval_mod.settings, "retrieval_mode", "vector")

    async def fake_get_client() -> Any:
        class _C:
            async def embed_query(self, *_a: Any, **_k: Any) -> list[float]:
                raise EmbedderUnavailable("down")

        return _C()

    monkeypatch.setattr(retrieval_mod, "get_embedding_client", fake_get_client)

    hits = await retrieval_mod.retrieve_grounding(user_id=uuid.uuid4(), query="anything")
    assert hits == []
