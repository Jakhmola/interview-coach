"""Phase 14 — model-answer faithfulness baseline.

For each (fixture, round_type) pair, drive `stream_question` end-to-end
against the live local LLM, feed a placeholder candidate answer, then
drive `stream_evaluation`, capturing the model_answer text. Score it
with the `model_answer_faithfulness` GEval metric and append a row to
`tests/integration/eval/model_answer_results.csv`.

Skipped unless INTEGRATION=1.

Note: retrieval is best-effort. When the test uses the in-memory SQLite
harness, `retrieve_grounding` fails silently inside the evaluator and
the model-answer call runs with `grounding=[]`. That's the expected
phase-14 baseline path; pgvector retrieval is exercised by the smoke
test in the plan, not this harness.
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

from interview_coach.agents.nodes import evaluator, question_generator
from interview_coach.db import models, repos
from tests.integration.eval.conftest import load_fixture
from tests.integration.eval.metrics import model_answer_faithfulness

logger = logging.getLogger(__name__)

PHASE_TAG = os.environ.get("EVAL_PHASE_TAG", "14-baseline")
RESULTS_CSV = Path(__file__).parent / "model_answer_results.csv"
N_QUESTIONS = 3  # Lighter than question-quality (3*2 LLM calls per turn here).
ROUND_TYPES = ["resume_walkthrough", "behavioral_star"]
PLACEHOLDER_ANSWER = "I don't really know."

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
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(question_generator, "AsyncSessionLocal", factory)
    monkeypatch.setattr(evaluator, "AsyncSessionLocal", factory)
    try:
        yield factory
    finally:
        await engine.dispose()


def _csv_append(row: dict[str, Any]) -> None:
    new_file = not RESULTS_CSV.exists()
    fieldnames = [
        "timestamp",
        "phase",
        "fixture",
        "round_type",
        "n_turns",
        "model_answer_faithfulness",
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
    async with factory() as s:
        user = await repos.create_user(s, f"{fixture['slug']}-ma@eval.local", "x")
        # Persist the profile so the evaluator's profile load returns
        # something meaningful even on the SQLite harness.
        await repos.upsert_profile(
            s,
            user_id=user.id,
            profile_json=fixture["profile"],
            source_doc_ids=[],
            model_name="(eval-fixture)",
        )
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


async def _run_one_turn(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    factory: async_sessionmaker[AsyncSession],
) -> str:
    """Generate a question, save a placeholder answer, evaluate, return model_answer."""
    turn_id: uuid.UUID | None = None
    async for kind, data in question_generator.stream_question(
        session_id=session_id, user_id=user_id
    ):
        if kind == "done":
            turn_id = uuid.UUID(data["question_id"])
    assert turn_id is not None

    async with factory() as s:
        await repos.update_turn_answer(s, turn_id, PLACEHOLDER_ANSWER)

    model_answer = ""
    async for kind, data in evaluator.stream_evaluation(
        session_id=session_id, user_id=user_id, turn_id=turn_id
    ):
        if kind == "model_answer_token":
            model_answer += data
    return model_answer.strip()


@pytest.mark.parametrize("fixture_slug", _fixture_slugs())
@pytest.mark.parametrize("round_type", ROUND_TYPES)
async def test_model_answer_quality(
    fixture_slug: str,
    round_type: str,
    harness_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_fixture(fixture_slug)
    user_id, session_id = await _seed_session(harness_db, fixture, round_type)
    _patch_load_context(monkeypatch, fixture)

    t0 = time.monotonic()
    answers: list[str] = []
    for i in range(N_QUESTIONS):
        ma = await _run_one_turn(session_id=session_id, user_id=user_id, factory=harness_db)
        if not ma:
            raise AssertionError(f"empty model_answer at turn {i}")
        answers.append(ma)
    elapsed = time.monotonic() - t0
    logger.info(
        "eval[%s/%s]: %d turns in %.1fs (mean %.1fs/turn)",
        fixture_slug,
        round_type,
        len(answers),
        elapsed,
        elapsed / len(answers),
    )

    # Phase 14 retrieval is degraded on the SQLite harness (no pgvector),
    # so grounding_texts is empty here — faithfulness measures only that
    # specifics in `model_answer` are anchored in the profile.
    scores = [
        model_answer_faithfulness(ma, fixture["profile"], grounding_texts=[]) for ma in answers
    ]
    avg = sum(scores) / len(scores)
    logger.info("eval[%s/%s]: model_answer_faithfulness=%.3f", fixture_slug, round_type, avg)

    assert avg >= 0.0  # baseline only

    _csv_append(
        {
            "timestamp": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "phase": PHASE_TAG,
            "fixture": fixture_slug,
            "round_type": round_type,
            "n_turns": len(answers),
            "model_answer_faithfulness": f"{avg:.4f}",
        }
    )

    out_dir = RESULTS_CSV.parent / "model_answers"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{PHASE_TAG}__{fixture_slug}__{round_type}.json"
    out.write_text(
        json.dumps(
            {
                "phase": PHASE_TAG,
                "fixture": fixture_slug,
                "round_type": round_type,
                "model_answers": answers,
                "metrics": {"model_answer_faithfulness": avg},
            },
            indent=2,
            ensure_ascii=False,
        )
    )
