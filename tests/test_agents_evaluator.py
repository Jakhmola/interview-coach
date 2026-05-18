"""Evaluator unit tests with mocked LLM streaming."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from interview_coach.agents.nodes import evaluator
from interview_coach.agents.streaming_json import StreamingJsonError
from interview_coach.db import models, repos
from interview_coach.db.models import Job, SessionRow, User


@pytest.fixture
async def agent_session(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(evaluator, "AsyncSessionLocal", factory)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def alice(agent_session: AsyncSession) -> User:
    return await repos.create_user(agent_session, "alice@example.com", "x")


@pytest.fixture
async def seeded_job(agent_session: AsyncSession, alice: User) -> Job:
    return await repos.create_job(
        agent_session,
        user_id=alice.id,
        source="pasted",
        raw_text="Senior backend engineer.",
    )


@pytest.fixture
async def seeded_profile(agent_session: AsyncSession, alice: User) -> None:
    await repos.upsert_profile(
        agent_session,
        user_id=alice.id,
        profile_json={
            "summary": "Backend engineer.",
            "skills": ["python"],
            "experiences": [],
            "projects": [],
            "education": [],
        },
        source_doc_ids=[],
        model_name="qwen3-8b",
    )


async def _make_session_with_turn(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    *,
    n_questions: int = 3,
    turn_index: int = 0,
    answer: str | None = "I'd start by clarifying requirements.",
) -> tuple[SessionRow, uuid.UUID]:
    sess = await repos.create_session(
        agent_session,
        user_id=alice.id,
        job_id=seeded_job.id,
        round_type="resume_walkthrough",
        n_questions=n_questions,
    )
    turn = await repos.create_turn(
        agent_session,
        session_id=sess.id,
        turn_index=turn_index,
        question="Walk me through your last project.",
        anchors=["specifics", "tradeoffs", "outcome"],
    )
    if answer is not None:
        await repos.update_turn_answer(agent_session, turn.id, answer)
    return sess, turn.id


def _patch_streaming_llm(monkeypatch: pytest.MonkeyPatch, deltas: list[str]) -> None:
    class _FakeChunk:
        def __init__(self, content: str) -> None:
            self.content = content

    class _FakeBound:
        async def astream(self, _messages: list[Any]) -> AsyncIterator[Any]:
            for d in deltas:
                yield _FakeChunk(d)

    def fake_chat_model(**_: object) -> Any:
        m = AsyncMock()
        m.bind = lambda **_kwargs: _FakeBound()
        return m

    monkeypatch.setattr(evaluator, "chat_model", fake_chat_model)


async def test_happy_path_streams_persists_and_completes_session(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3-question session, evaluating turn_index=2 → session flips to complete."""
    sess, turn_id = await _make_session_with_turn(
        agent_session, alice, seeded_job, n_questions=3, turn_index=2
    )
    # Two earlier (already-evaluated) turns to satisfy the "this is the last" check.
    for i in range(2):
        prev = await repos.create_turn(
            agent_session,
            session_id=sess.id,
            turn_index=i,
            question=f"Q{i}",
            anchors=["a"],
        )
        await repos.update_turn_answer(agent_session, prev.id, "x")
        await repos.update_turn_evaluation(
            agent_session, prev.id, score=5, feedback="ok", model_answer="ok"
        )
    # The fixture inserted turn_index=2 first; its index/answer are correct.

    _patch_streaming_llm(
        monkeypatch,
        [
            '{"score": 8, "feedback": "Strong on tradeoffs',
            ' but missed metrics.", "model_answer": "When I led the rewrite, I..."}',
        ],
    )

    score: int | None = None
    feedback = ""
    model_answer = ""
    final: dict[str, Any] | None = None

    async for kind, data in evaluator.stream_evaluation(
        session_id=sess.id, user_id=alice.id, turn_id=turn_id
    ):
        if kind == "score":
            score = data
        elif kind == "feedback_token":
            feedback += data
        elif kind == "model_answer_token":
            model_answer += data
        elif kind == "done":
            final = data

    assert score == 8
    assert feedback == "Strong on tradeoffs but missed metrics."
    assert model_answer == "When I led the rewrite, I..."
    assert final is not None
    assert final["session_status"] == "complete"
    assert final["n_remaining"] == 0

    # Persistence: turn updated, session flipped.
    factory = evaluator.AsyncSessionLocal
    async with factory() as fresh:
        turn = await repos.get_turn(fresh, turn_id)
        sess_fresh = await repos.get_session(fresh, sess.id, alice.id)
    assert turn is not None
    assert turn.score == 8
    assert turn.feedback == "Strong on tradeoffs but missed metrics."
    assert turn.model_answer == "When I led the rewrite, I..."
    assert sess_fresh is not None
    assert sess_fresh.status == "complete"


async def test_non_final_turn_keeps_session_active(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess, turn_id = await _make_session_with_turn(
        agent_session, alice, seeded_job, n_questions=3, turn_index=0
    )
    _patch_streaming_llm(monkeypatch, ['{"score": 6, "feedback": "ok", "model_answer": "x"}'])

    final: dict[str, Any] | None = None
    async for kind, data in evaluator.stream_evaluation(
        session_id=sess.id, user_id=alice.id, turn_id=turn_id
    ):
        if kind == "done":
            final = data

    assert final is not None
    assert final["session_status"] == "active"
    assert final["n_remaining"] == 2

    factory = evaluator.AsyncSessionLocal
    async with factory() as fresh:
        sess_fresh = await repos.get_session(fresh, sess.id, alice.id)
    assert sess_fresh is not None
    assert sess_fresh.status == "active"


async def test_score_out_of_range_raises(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess, turn_id = await _make_session_with_turn(agent_session, alice, seeded_job)
    _patch_streaming_llm(monkeypatch, ['{"score": 11, "feedback": "x", "model_answer": "y"}'])

    with pytest.raises(StreamingJsonError):
        async for _ in evaluator.stream_evaluation(
            session_id=sess.id, user_id=alice.id, turn_id=turn_id
        ):
            pass


async def test_turn_not_found(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess, _ = await _make_session_with_turn(agent_session, alice, seeded_job)
    _patch_streaming_llm(monkeypatch, ['{"score": 5, "feedback": "x", "model_answer": "y"}'])

    with pytest.raises(evaluator.TurnNotFound):
        async for _ in evaluator.stream_evaluation(
            session_id=sess.id, user_id=alice.id, turn_id=uuid.uuid4()
        ):
            pass


async def test_turn_not_answered(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess, turn_id = await _make_session_with_turn(agent_session, alice, seeded_job, answer=None)
    _patch_streaming_llm(monkeypatch, ['{"score": 5, "feedback": "x", "model_answer": "y"}'])

    with pytest.raises(evaluator.TurnNotAnswered):
        async for _ in evaluator.stream_evaluation(
            session_id=sess.id, user_id=alice.id, turn_id=turn_id
        ):
            pass


async def test_already_evaluated_blocked(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess, turn_id = await _make_session_with_turn(agent_session, alice, seeded_job)
    await repos.update_turn_evaluation(
        agent_session, turn_id, score=5, feedback="ok", model_answer="ok"
    )
    _patch_streaming_llm(monkeypatch, ['{"score": 7, "feedback": "x", "model_answer": "y"}'])

    with pytest.raises(evaluator.TurnNotFound):
        async for _ in evaluator.stream_evaluation(
            session_id=sess.id, user_id=alice.id, turn_id=turn_id
        ):
            pass


async def test_anchors_and_question_threaded_into_prompt(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The evaluator's user message must include the question, anchors, and answer
    so the LLM can score against the rubric."""
    sess, turn_id = await _make_session_with_turn(agent_session, alice, seeded_job)

    captured: list[list[Any]] = []

    class _FakeChunk:
        def __init__(self, content: str) -> None:
            self.content = content

    class _FakeBound:
        async def astream(self, messages: list[Any]) -> AsyncIterator[Any]:
            captured.append(messages)
            yield _FakeChunk('{"score": 7, "feedback": "x", "model_answer": "y"}')

    def fake_chat_model(**_: object) -> Any:
        m = AsyncMock()
        m.bind = lambda **_kwargs: _FakeBound()
        return m

    monkeypatch.setattr(evaluator, "chat_model", fake_chat_model)

    async for _ in evaluator.stream_evaluation(
        session_id=sess.id, user_id=alice.id, turn_id=turn_id
    ):
        pass

    [_sys, user_msg] = captured[0]
    assert "Walk me through your last project." in user_msg.content
    assert "specifics" in user_msg.content
    assert "tradeoffs" in user_msg.content
    assert "I'd start by clarifying requirements." in user_msg.content


async def test_retrieve_for_turn_uses_single_attempt(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 21 follow-up: the evaluator's in-turn grounding retrieval
    must pass ``retries=1`` to ``retrieve_grounding``. Default 3 retries
    pile up wall-clock on an already-overloaded embedder; retrieval
    failure already degrades gracefully to no-grounding, so failing fast
    is strictly better than the old behaviour.
    """
    sess, turn_id = await _make_session_with_turn(agent_session, alice, seeded_job)

    _patch_streaming_llm(monkeypatch, ['{"score": 5, "feedback": "x", "model_answer": "y"}'])

    captured: dict[str, Any] = {}

    async def fake_retrieve_grounding(**kwargs: Any) -> list[Any]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(evaluator, "retrieve_grounding", fake_retrieve_grounding)

    async for _ in evaluator.stream_evaluation(
        session_id=sess.id, user_id=alice.id, turn_id=turn_id
    ):
        pass

    assert captured.get("retries") == 1
