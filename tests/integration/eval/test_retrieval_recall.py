"""Phase 24 — hybrid retrieval recall@k baseline.

For each fixture's `cv.txt`, ingest the chunks (real chunker + embedder
sidecar + Postgres), then for every (query, expected_substrings) entry
in the fixture's `gold.json` run the query under three modes — `vector`,
`bm25`, `hybrid` — and compute `recall@k` for `k in {4, 10}`.

Phase gate: `recall@4(hybrid) ≥ max(recall@4(vector), recall@4(bm25)) - 0.01`.
A one-percentage-point slack absorbs tie-break noise.

Skipped unless `INTEGRATION=1` and the docker stack is up (api db +
embedder reachable). Appends rows to `retrieval_recall_results.csv` so
follow-up phases can read the baseline.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.rag import get_embedding_client
from interview_coach.rag.chunking import chunk_text
from interview_coach.rag.client import EXPECTED_MODEL_NAME
from interview_coach.rag.retrieval import retrieve_grounding
from interview_coach.rag.tokenizer import get_tokenizer

logger = logging.getLogger(__name__)

PHASE_TAG = os.environ.get("EVAL_PHASE_TAG", "24-hybrid")
RESULTS_CSV = Path(__file__).parent / "retrieval_recall_results.csv"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

K_VALUES = (4, 10)
MODES = ("vector", "bm25", "hybrid")

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION") != "1",
    reason="Set INTEGRATION=1 to run; requires docker stack (db + embedder) up.",
)


def _fixture_slugs() -> list[str]:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(
        p.name
        for p in FIXTURES_DIR.iterdir()
        if p.is_dir() and (p / "cv.txt").exists() and (p / "gold.json").exists()
    )


async def _seed_chunks(*, fixture_slug: str) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """Seed one user + one CV document + grounding_chunks. Yields (user_id, doc_id);
    cleans up on teardown so reruns are idempotent.
    """
    cv_text = (FIXTURES_DIR / fixture_slug / "cv.txt").read_text()

    async with AsyncSessionLocal() as s:
        user = await repos.create_user(
            s, f"{fixture_slug}-recall-{uuid.uuid4().hex[:8]}@eval.local", "x"
        )
        doc = await repos.create_document(
            s,
            user_id=user.id,
            kind="cv",
            filename=f"{fixture_slug}-cv.txt",
            content_type="text/plain",
            byte_size=len(cv_text.encode("utf-8")),
            raw_text=cv_text,
        )

    tokenizer = await get_tokenizer()
    chunks = chunk_text(cv_text, tokenizer=tokenizer, project_title=None)
    assert chunks, f"fixture {fixture_slug} produced 0 chunks"

    client = await get_embedding_client()
    vectors = await client.embed_passages([c.text for c in chunks])
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
        await repos.insert_grounding_chunks(
            s,
            user_id=user.id,
            document_id=doc.id,
            source_doc_kind="cv",
            chunks=payload,
            model_name=EXPECTED_MODEL_NAME,
        )

    try:
        yield user.id, doc.id
    finally:
        # Cascade deletes the document and chunks via FK ondelete=CASCADE.
        from sqlalchemy import text as _text

        async with AsyncSessionLocal() as s:
            await s.execute(_text("DELETE FROM users WHERE id = :uid"), {"uid": str(user.id)})
            await s.commit()
        # pytest-asyncio creates a fresh event loop per test (function scope),
        # but the module-level engine pool + embedder client are bound to the
        # current loop's transport. Reset both so the next parametrized run
        # gets a clean pool on its own loop.
        from interview_coach.db import session as session_mod
        from interview_coach.rag import reset_embedding_client

        await session_mod.engine.dispose()
        await reset_embedding_client()


def _chunk_matches(chunk_text: str, substrings: list[str]) -> bool:
    """Substring match — case-insensitive AND across all expected substrings."""
    low = chunk_text.lower()
    return all(sub.lower() in low for sub in substrings)


def _recall_at_k(hit_texts: list[str], expected: list[str], k: int) -> float:
    """1.0 if any of the top-k hits matches all expected substrings, else 0.0.

    Per-query metric only — averaging happens at the fixture level. A
    binary-per-query recall is the right call for our few-shot harness:
    expected_substrings names ONE chunk, not a set.
    """
    for t in hit_texts[:k]:
        if _chunk_matches(t, expected):
            return 1.0
    return 0.0


async def _run_mode(
    *,
    mode: str,
    user_id: uuid.UUID,
    query: str,
    k: int,
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Drive `retrieve_grounding` in a specific mode and return the hit texts.

    `bm25` mode is implemented by hybrid mode with vector branch monkey-patched
    to return [] — that exercises the same fusion / score-floor path the
    production code uses, just starved of vector evidence.
    """
    from interview_coach.rag import hybrid as hybrid_mod
    from interview_coach.rag import retrieval as retrieval_mod

    if mode == "vector":
        monkeypatch.setattr(retrieval_mod.settings, "retrieval_mode", "vector")
        hits = await retrieve_grounding(user_id=user_id, query=query, k=k, source_kinds=("cv",))
        return [h.text for h in hits]

    if mode == "bm25":
        # Hybrid path but force the vector branch empty.
        monkeypatch.setattr(retrieval_mod.settings, "retrieval_mode", "hybrid")

        async def _no_vector(**_: Any) -> list[Any]:
            return []

        monkeypatch.setattr(hybrid_mod, "_vector_search_raw", _no_vector)
        hits = await retrieve_grounding(user_id=user_id, query=query, k=k, source_kinds=("cv",))
        return [h.text for h in hits]

    assert mode == "hybrid"
    monkeypatch.setattr(retrieval_mod.settings, "retrieval_mode", "hybrid")
    hits = await retrieve_grounding(user_id=user_id, query=query, k=k, source_kinds=("cv",))
    return [h.text for h in hits]


def _csv_append(row: dict[str, Any]) -> None:
    new_file = not RESULTS_CSV.exists()
    fieldnames = [
        "timestamp",
        "phase",
        "fixture",
        "mode",
        "recall_at_4",
        "recall_at_10",
        "n_queries",
    ]
    with RESULTS_CSV.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in fieldnames})


@pytest.mark.parametrize("fixture_slug", _fixture_slugs())
async def test_hybrid_recall_at_least_max_single_modality(
    fixture_slug: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gold = json.loads((FIXTURES_DIR / fixture_slug / "gold.json").read_text())
    queries: list[dict[str, Any]] = gold["queries"]

    seed = _seed_chunks(fixture_slug=fixture_slug)
    user_id, _doc_id = await seed.__anext__()
    try:
        # Use the largest k requested for retrieval; compute recall@4 + recall@10
        # from the same hit list per query+mode.
        max_k = max(K_VALUES)
        per_mode: dict[str, dict[int, list[float]]] = {
            mode: {k: [] for k in K_VALUES} for mode in MODES
        }

        for q in queries:
            for mode in MODES:
                hit_texts = await _run_mode(
                    mode=mode,
                    user_id=user_id,
                    query=q["query"],
                    k=max_k,
                    monkeypatch=monkeypatch,
                )
                for k in K_VALUES:
                    per_mode[mode][k].append(_recall_at_k(hit_texts, q["expected_substrings"], k))

        means: dict[str, dict[int, float]] = {
            mode: {k: sum(v) / len(v) if v else 0.0 for k, v in by_k.items()}
            for mode, by_k in per_mode.items()
        }

        # Print a comparison block so `pytest -s` shows it.
        logger.info(
            "retrieval_recall[%s]: %s",
            fixture_slug,
            {mode: {f"@{k}": round(means[mode][k], 3) for k in K_VALUES} for mode in MODES},
        )

        # Append one row per mode for the CSV
        now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        for mode in MODES:
            _csv_append(
                {
                    "timestamp": now,
                    "phase": PHASE_TAG,
                    "fixture": fixture_slug,
                    "mode": mode,
                    "recall_at_4": f"{means[mode][4]:.4f}",
                    "recall_at_10": f"{means[mode][10]:.4f}",
                    "n_queries": len(queries),
                }
            )

        # Phase gate: hybrid must not regress vs. the better single modality.
        best_single = max(means["vector"][4], means["bm25"][4])
        assert means["hybrid"][4] >= best_single - 0.01, (
            f"hybrid recall@4 ({means['hybrid'][4]:.3f}) regressed vs. "
            f"max(vector={means['vector'][4]:.3f}, bm25={means['bm25'][4]:.3f})"
        )
    finally:
        try:
            await seed.__anext__()
        except StopAsyncIteration:
            pass
