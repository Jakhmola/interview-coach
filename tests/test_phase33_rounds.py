"""Phase 33 — round-type system tests.

Covers the strategy registry, the focus picker's new ``jd_skills`` /
repo-backed dispatch, the question-gen-time repo grounding (injection +
fail-fast degrade), the evaluator's answer-grounding dispatch (technical /
behavioral skip retrieval and pick the right model-answer prompt), and the
sessions CHECK constraint on the three round-type values.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from interview_coach.agents.nodes import evaluator, question_generator
from interview_coach.agents.nodes.question_generator import _pick_focus_target
from interview_coach.agents.prompts import (
    MODEL_ANSWER_BEHAVIORAL_SYSTEM,
    MODEL_ANSWER_TECHNICAL_SYSTEM,
)
from interview_coach.agents.rounds import (
    AnswerGrounding,
    FocusMode,
    get_round_strategy,
)
from interview_coach.db import models, repos
from interview_coach.db.models import Job, User
from interview_coach.rag.retrieval import GroundingHit

# --- registry ---------------------------------------------------------------


def test_registry_returns_each_round_policies() -> None:
    exp = get_round_strategy("experience_deep_dive")
    assert exp.focus is FocusMode.experience_projects
    assert exp.qgen_grounding is True
    assert exp.answer_grounding is AnswerGrounding.rag_docs

    tech = get_round_strategy("technical_challenge")
    assert tech.focus is FocusMode.jd_skills
    assert tech.qgen_grounding is False
    assert tech.answer_grounding is AnswerGrounding.none_authoritative

    beh = get_round_strategy("behavioral_star")
    assert beh.focus is FocusMode.behavioral_signals
    assert beh.qgen_grounding is False
    assert beh.answer_grounding is AnswerGrounding.none_hypothetical


def test_registry_unknown_round_raises() -> None:
    with pytest.raises(ValueError, match="resume_walkthrough"):
        get_round_strategy("resume_walkthrough")


# --- picker dispatch: jd_skills ---------------------------------------------


def _pick_skill(job: dict[str, Any]) -> Any:
    return _pick_focus_target(
        focus=FocusMode.jd_skills,
        profile={},
        job_analysis=job,
        company_snapshot={},
        prior_focus_counts={},
        rng=random.Random(0),
    )


def test_jd_skills_picks_must_have_skill() -> None:
    picked = _pick_skill({"must_have_skills": ["kubernetes"], "title": "SRE"})
    assert picked is not None
    assert picked.key == "skill:kubernetes"
    assert picked.label == "kubernetes"
    assert picked.document_ids == []
    assert picked.repo_backed is False


def test_jd_skills_falls_back_to_nice_to_have() -> None:
    picked = _pick_skill(
        {"must_have_skills": [], "nice_to_have_skills": ["terraform"], "title": "SRE"}
    )
    assert picked is not None
    assert picked.key == "skill:terraform"


def test_jd_skills_falls_back_to_title() -> None:
    picked = _pick_skill(
        {"must_have_skills": [], "nice_to_have_skills": [], "title": "Staff Platform Engineer"}
    )
    assert picked is not None
    assert picked.key == "skill:Staff Platform Engineer"


def test_jd_skills_returns_none_when_nothing_to_pick() -> None:
    picked = _pick_skill({"must_have_skills": [], "nice_to_have_skills": [], "title": ""})
    assert picked is None


# --- picker dispatch: repo-backed flag --------------------------------------


def _pick_only_project(project: dict[str, Any]) -> Any:
    return _pick_focus_target(
        focus=FocusMode.experience_projects,
        profile={"experiences": [], "projects": [project]},
        job_analysis={"must_have_skills": ["fastapi"]},
        company_snapshot={},
        prior_focus_counts={},
        rng=random.Random(0),
    )


def test_github_project_focus_is_repo_backed() -> None:
    picked = _pick_only_project(
        {
            "name": "GhRepo",
            "description": "A FastAPI service.",
            "tech": ["fastapi"],
            "source": "github",
            "source_document_ids": ["d1"],
        }
    )
    assert picked is not None
    assert picked.key == "project:GhRepo"
    assert picked.repo_backed is True
    assert picked.document_ids == ["d1"]


def test_project_doc_focus_is_not_repo_backed() -> None:
    picked = _pick_only_project(
        {
            "name": "DocProj",
            "description": "A FastAPI service.",
            "tech": ["fastapi"],
            "source": "project_doc",
            "source_document_ids": ["d2"],
        }
    )
    assert picked is not None
    assert picked.repo_backed is False


# --- DB-backed fixtures (mirror the question_generator / evaluator tests) ----


@pytest.fixture
async def db(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(question_generator, "AsyncSessionLocal", factory)
    monkeypatch.setattr(evaluator, "AsyncSessionLocal", factory)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def alice(db: AsyncSession) -> User:
    return await repos.create_user(db, "alice@example.com", "x")


@pytest.fixture
async def seeded_job(db: AsyncSession, alice: User) -> Job:
    job = await repos.create_job(db, user_id=alice.id, source="pasted", raw_text="JD.")
    await repos.update_job_parsed_json(
        db,
        job.id,
        alice.id,
        {
            "title": "Senior Backend Engineer",
            "seniority": "senior",
            "must_have_skills": ["fastapi", "pgvector"],
            "nice_to_have_skills": [],
            "responsibilities": [],
            "behavioral_signals": ["ownership"],
            "company_name": "Acme",
        },
    )
    return job


@pytest.fixture
async def seeded_snapshot(db: AsyncSession, seeded_job: Job) -> None:
    await repos.upsert_company_snapshot(
        db,
        job_id=seeded_job.id,
        company_name="Acme",
        snapshot_json={
            "mission": "Acme builds rockets.",
            "products": [],
            "recent_news": [],
            "values_and_signals": ["ownership"],
        },
        source_urls=[],
        model_name="qwen3-8b",
    )


REPO_DOC_ID = uuid.uuid4()


@pytest.fixture
async def seeded_github_profile(db: AsyncSession, alice: User) -> None:
    await repos.upsert_profile(
        db,
        user_id=alice.id,
        profile_json={
            "summary": "Backend engineer.",
            "skills": ["python", "fastapi"],
            "experiences": [],
            "projects": [
                {
                    "name": "GhRepo",
                    "description": "A FastAPI + pgvector service.",
                    "tech": ["fastapi", "pgvector"],
                    "urls": ["https://github.com/u/ghrepo"],
                    "source": "github",
                    "source_document_ids": [str(REPO_DOC_ID)],
                }
            ],
            "education": [],
        },
        source_doc_ids=[str(REPO_DOC_ID)],
        model_name="qwen3-8b",
    )


def _patch_streaming_llm(
    monkeypatch: pytest.MonkeyPatch, mod: Any, deltas: list[str]
) -> list[list[Any]]:
    captured: list[list[Any]] = []

    class _FakeChunk:
        def __init__(self, content: str) -> None:
            self.content = content

    class _FakeBound:
        async def astream(self, messages: list[Any]) -> AsyncIterator[Any]:
            captured.append(messages)
            for d in deltas:
                yield _FakeChunk(d)

    def fake_chat_model(**_: object) -> Any:
        m = AsyncMock()
        m.bind = lambda **_kwargs: _FakeBound()
        return m

    monkeypatch.setattr(mod, "chat_model", fake_chat_model)
    return captured


def _pin_focus_to(monkeypatch: pytest.MonkeyPatch, needle: str) -> None:
    """Force the weighted sample onto the FocusPick whose label contains needle."""
    monkeypatch.setattr(
        question_generator.random.Random,
        "choices",
        lambda self, population, weights=None, k=1: [
            next(t for t in population if needle in t.label)
        ],
    )


# --- question-gen-time grounding (experience round, repo-backed focus) -------


async def test_qgen_grounding_injects_repo_code_for_github_focus(
    db: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_github_profile: None,
    seeded_snapshot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = await repos.create_session(
        db, user_id=alice.id, job_id=seeded_job.id, round_type="experience_deep_dive", n_questions=5
    )
    _pin_focus_to(monkeypatch, "GhRepo")

    captured_retrieve: dict[str, Any] = {}

    async def fake_retrieve(**kwargs: Any) -> list[GroundingHit]:
        captured_retrieve.update(kwargs)
        return [
            GroundingHit(
                text="app = FastAPI(); engine = create_async_engine(...)",
                document_id=REPO_DOC_ID,
                source_doc_kind="github_repo",
                chunk_index=0,
                score=0.9,
                filename="ghrepo/main.py",
            )
        ]

    monkeypatch.setattr(question_generator, "retrieve_grounding", fake_retrieve)
    captured_msgs = _patch_streaming_llm(
        monkeypatch,
        question_generator,
        ['{"question": "Why async engine?", "anchors": ["a", "b", "c"]}'],
    )

    async for _ in question_generator.stream_question(session_id=sess.id, user_id=alice.id):
        pass

    # Retrieval was scoped to the repo corpus, single-attempt, k=3, pinned doc.
    assert captured_retrieve["source_kinds"] == ("github_repo",)
    assert captured_retrieve["k"] == 3
    assert captured_retrieve["retries"] == 1
    assert captured_retrieve["document_ids"] == (REPO_DOC_ID,)

    # The repo passage rides into the user message under `code_grounding`.
    [_sys, user_msg] = captured_msgs[0]
    assert "code_grounding" in user_msg.content
    assert "ghrepo/main.py" in user_msg.content


async def test_qgen_grounding_degrades_to_narrative_on_embedder_failure(
    db: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_github_profile: None,
    seeded_snapshot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = await repos.create_session(
        db, user_id=alice.id, job_id=seeded_job.id, round_type="experience_deep_dive", n_questions=5
    )
    _pin_focus_to(monkeypatch, "GhRepo")

    async def boom(**_: Any) -> list[GroundingHit]:
        raise RuntimeError("embedder down")

    monkeypatch.setattr(question_generator, "retrieve_grounding", boom)
    captured_msgs = _patch_streaming_llm(
        monkeypatch,
        question_generator,
        ['{"question": "Walk me through GhRepo.", "anchors": ["a", "b", "c"]}'],
    )

    streamed = ""
    async for kind, data in question_generator.stream_question(
        session_id=sess.id, user_id=alice.id
    ):
        if kind == "token":
            streamed += data

    # Question still streams; no code_grounding key (degraded to narrative).
    assert streamed == "Walk me through GhRepo."
    [_sys, user_msg] = captured_msgs[0]
    assert "code_grounding" not in user_msg.content


# --- evaluator answer-grounding dispatch ------------------------------------


def _patch_eval_llm_capturing(monkeypatch: pytest.MonkeyPatch) -> list[list[Any]]:
    """Judge + model-answer each get the same combined JSON blob; capture the
    messages of each call so we can assert which model-answer prompt was used."""
    return _patch_streaming_llm(
        monkeypatch,
        evaluator,
        ['{"score": 7, "feedback": "ok", "model_answer": "Here is a strong answer."}'],
    )


async def _seed_answered_turn(
    db: AsyncSession, alice: User, job: Job, *, round_type: str, focus_key: str
) -> tuple[uuid.UUID, uuid.UUID]:
    sess = await repos.create_session(
        db, user_id=alice.id, job_id=job.id, round_type=round_type, n_questions=1
    )
    turn = await repos.create_turn(
        db,
        session_id=sess.id,
        turn_index=0,
        question="A question.",
        anchors=["a", "b", "c"],
        metadata={"focus_key": focus_key},
    )
    await repos.update_turn_answer(db, turn.id, "My answer.")
    return sess.id, turn.id


@pytest.fixture
async def eval_profile(db: AsyncSession, alice: User) -> None:
    await repos.upsert_profile(
        db,
        user_id=alice.id,
        profile_json={
            "summary": "Engineer.",
            "skills": ["python"],
            "experiences": [],
            "projects": [],
            "education": [],
        },
        source_doc_ids=[],
        model_name="qwen3-8b",
    )


@pytest.mark.parametrize(
    ("round_type", "focus_key", "expected_prompt"),
    [
        ("technical_challenge", "skill:fastapi", MODEL_ANSWER_TECHNICAL_SYSTEM),
        ("behavioral_star", "ownership", MODEL_ANSWER_BEHAVIORAL_SYSTEM),
    ],
)
async def test_evaluator_skips_retrieval_and_picks_prompt_for_ungrounded_rounds(
    db: AsyncSession,
    alice: User,
    seeded_job: Job,
    eval_profile: None,
    monkeypatch: pytest.MonkeyPatch,
    round_type: str,
    focus_key: str,
    expected_prompt: str,
) -> None:
    session_id, turn_id = await _seed_answered_turn(
        db, alice, seeded_job, round_type=round_type, focus_key=focus_key
    )

    retrieval_called = False

    async def spy_retrieve(**_: Any) -> list[GroundingHit]:
        nonlocal retrieval_called
        retrieval_called = True
        return []

    monkeypatch.setattr(evaluator, "retrieve_grounding", spy_retrieve)
    captured = _patch_eval_llm_capturing(monkeypatch)

    async for _ in evaluator.stream_evaluation(
        session_id=session_id, user_id=alice.id, turn_id=turn_id
    ):
        pass

    # Ungrounded rounds never touch retrieval...
    assert retrieval_called is False
    # ...and the model-answer call uses the round's own prompt. captured[0] is
    # the judge call, captured[1] is the model-answer call.
    assert len(captured) == 2
    assert captured[1][0].content == expected_prompt


# --- sessions CHECK constraint ----------------------------------------------


async def test_round_type_check_accepts_three_values_rejects_old() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        user = await repos.create_user(s, "u@example.com", "x")
        job = await repos.create_job(s, user_id=user.id, source="pasted", raw_text="jd")
        for rt in ("experience_deep_dive", "technical_challenge", "behavioral_star"):
            sess = await repos.create_session(
                s, user_id=user.id, job_id=job.id, round_type=rt, n_questions=1
            )
            assert sess.round_type == rt

    async with factory() as s:
        with pytest.raises(IntegrityError):
            await repos.create_session(
                s, user_id=user.id, job_id=job.id, round_type="resume_walkthrough", n_questions=1
            )

    await engine.dispose()
