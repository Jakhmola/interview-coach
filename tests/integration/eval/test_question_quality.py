"""Phase 12a — question-quality baseline harness.

For each (fixture, round_type) pair, drive the real `stream_question` against
the live local LLM, collect 5 generated questions, compute three metrics
(distinctness, profile_groundedness, jd_relevance), and append a row to
`tests/integration/eval/results.csv` tagged with the phase name.

Skipped unless INTEGRATION=1 (~8–17 min for a full run on CPU/GPU).

Iterating on the metric definitions? Run a single fixture-round:

    INTEGRATION=1 uv run pytest tests/integration/eval -k 'backend_senior and resume' -v
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from interview_coach.agents.nodes import question_generator
from interview_coach.db import models, repos
from tests.integration.eval.conftest import load_fixture
from tests.integration.eval.metrics import (
    distinctness,
    jd_relevance,
    profile_groundedness,
)

logger = logging.getLogger(__name__)

PHASE_TAG = os.environ.get("EVAL_PHASE_TAG", "12a-baseline")
RESULTS_CSV = Path(__file__).parent / "results.csv"
N_QUESTIONS = 5
ROUND_TYPES = ["resume_walkthrough", "behavioral_star"]

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION") != "1",
    reason="Set INTEGRATION=1 to run; requires the local LLM reachable.",
)


def _fixture_slugs() -> list[str]:
    base = Path(__file__).parent / "fixtures"
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and (p / "profile.json").exists())


@pytest.fixture
async def harness_db(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """In-memory SQLite + monkeypatched `AsyncSessionLocal`."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(question_generator, "AsyncSessionLocal", factory)
    try:
        yield factory
    finally:
        await engine.dispose()


def _csv_append(row: dict[str, Any]) -> None:
    """Append a row to results.csv, creating the file with a header if absent."""
    new_file = not RESULTS_CSV.exists()
    fieldnames = [
        "timestamp",
        "phase",
        "fixture",
        "round_type",
        "distinctness",
        "profile_groundedness",
        "jd_relevance",
    ]
    with RESULTS_CSV.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in fieldnames})


async def _seed_session(
    factory: async_sessionmaker[AsyncSession],
    fixture: dict[str, Any],
    round_type: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert user + job + session rows so the node's DB reads succeed."""
    async with factory() as s:
        user = await repos.create_user(s, f"{fixture['slug']}@eval.local", "x")
        job = await repos.create_job(
            s,
            user_id=user.id,
            source="pasted",
            raw_text=f"(fixture {fixture['slug']})",
        )
        await repos.update_job_parsed_json(s, job.id, user.id, fixture["job"])
        sess = await repos.create_session(
            s,
            user_id=user.id,
            job_id=job.id,
            round_type=round_type,
            n_questions=N_QUESTIONS,
        )
    return user.id, sess.id


def _patch_load_context(monkeypatch: pytest.MonkeyPatch, fixture: dict[str, Any]) -> None:
    """Bypass the DB-backed context loader; serve fixture data instead.

    `prior_turns` is intentionally re-derived from the live DB on each call
    so the model sees the questions it has already produced this session
    (matching production behavior).
    """
    real_factory_attr = "AsyncSessionLocal"

    async def fake_load(session_row: Any) -> dict[str, Any]:
        factory = getattr(question_generator, real_factory_attr)
        async with factory() as s:
            turns = await repos.list_turns_for_session(s, session_row.id)
        return {
            "profile": fixture["profile"],
            "job_analysis": fixture["job"],
            "company_snapshot": fixture["company"],
            "prior_turns": [{"question": t.question, "answer": t.answer or ""} for t in turns],
        }

    monkeypatch.setattr(question_generator, "_load_context", fake_load)


async def _generate_n_questions(*, session_id: uuid.UUID, user_id: uuid.UUID, n: int) -> list[str]:
    questions: list[str] = []
    for i in range(n):
        t0 = time.monotonic()
        text = ""
        async for kind, data in question_generator.stream_question(
            session_id=session_id, user_id=user_id
        ):
            if kind == "token":
                text += data
        elapsed = time.monotonic() - t0
        logger.info("eval: q%d generated in %.1fs (%d chars)", i, elapsed, len(text))
        if not text.strip():
            raise AssertionError(f"empty question generated at index {i}")
        questions.append(text.strip())
    return questions


@pytest.mark.parametrize("fixture_slug", _fixture_slugs())
@pytest.mark.parametrize("round_type", ROUND_TYPES)
async def test_question_quality(
    fixture_slug: str,
    round_type: str,
    harness_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_fixture(fixture_slug)
    user_id, session_id = await _seed_session(harness_db, fixture, round_type)
    _patch_load_context(monkeypatch, fixture)

    t0 = time.monotonic()
    questions = await _generate_n_questions(session_id=session_id, user_id=user_id, n=N_QUESTIONS)
    gen_elapsed = time.monotonic() - t0
    logger.info(
        "eval[%s/%s]: %d questions in %.1fs (mean %.1fs/q)",
        fixture_slug,
        round_type,
        len(questions),
        gen_elapsed,
        gen_elapsed / len(questions),
    )

    # Distinctness: one number for the whole set.
    d = distinctness(questions)

    # Per-question groundedness / JD-relevance, averaged over the set.
    g_scores = [profile_groundedness(q, fixture["profile"], fixture["cv_text"]) for q in questions]
    j_scores = [jd_relevance(q, fixture["job"]) for q in questions]
    g = sum(g_scores) / len(g_scores)
    j = sum(j_scores) / len(j_scores)

    logger.info(
        "eval[%s/%s]: distinctness=%.3f profile_groundedness=%.3f jd_relevance=%.3f",
        fixture_slug,
        round_type,
        d,
        g,
        j,
    )

    # Soft assertions only — 12a establishes the baseline; later phases assert deltas.
    assert d >= 0.0
    assert g >= 0.0
    assert j >= 0.0

    _csv_append(
        {
            "timestamp": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "phase": PHASE_TAG,
            "fixture": fixture_slug,
            "round_type": round_type,
            "distinctness": f"{d:.4f}",
            "profile_groundedness": f"{g:.4f}",
            "jd_relevance": f"{j:.4f}",
        }
    )

    # Also dump the questions next to results.csv for spot-checking.
    questions_dir = RESULTS_CSV.parent / "questions"
    questions_dir.mkdir(exist_ok=True)
    out = questions_dir / f"{PHASE_TAG}__{fixture_slug}__{round_type}.json"
    out.write_text(
        json.dumps(
            {
                "phase": PHASE_TAG,
                "fixture": fixture_slug,
                "round_type": round_type,
                "questions": questions,
                "metrics": {
                    "distinctness": d,
                    "profile_groundedness": g,
                    "jd_relevance": j,
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    )
