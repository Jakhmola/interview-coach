"""Thread-close evaluator unit tests with mocked LLM streaming (Phase 34).

A thread is evaluated **once at close** over its whole transcript. The node
makes two sequential LLM calls (judge → model-answer); both are mocked here.
The combined-JSON fake works for both: the judge call parses ``score`` +
``feedback`` and the model-answer call parses ``model_answer`` from the same
object, each ignoring the other's keys.
"""

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


async def _open_thread_with_answer(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    *,
    n_questions: int = 3,
    thread_index: int = 0,
    question: str = "Walk me through your last project.",
    anchors: list[str] | None = None,
    answer: str | None = "I'd start by clarifying requirements.",
    focus_key: str | None = "project:AsyncAPI",
    n_prior_closed: int = 0,
) -> tuple[SessionRow, uuid.UUID]:
    """Create a session with ``n_prior_closed`` already-closed threads plus one
    open thread (root question seq=0 + optional candidate answer seq=1)."""
    anchors = anchors if anchors is not None else ["specifics", "tradeoffs", "outcome"]
    sess = await repos.create_session(
        agent_session,
        user_id=alice.id,
        job_id=seeded_job.id,
        round_type="experience_deep_dive",
        n_questions=n_questions,
    )
    for i in range(n_prior_closed):
        prior = await repos.create_thread(
            agent_session,
            session_id=sess.id,
            thread_index=i,
            anchors=["a"],
            focus_key=f"prior:{i}",
        )
        await repos.append_message(
            agent_session, thread_id=prior.id, seq=0, role="interviewer", kind="question", text="Q"
        )
        await repos.close_thread(agent_session, prior.id, score=5, feedback="ok", model_answer="ok")

    thread = await repos.create_thread(
        agent_session,
        session_id=sess.id,
        thread_index=thread_index,
        anchors=anchors,
        focus_key=focus_key,
    )
    await repos.append_message(
        agent_session,
        thread_id=thread.id,
        seq=0,
        role="interviewer",
        kind="question",
        text=question,
    )
    if answer is not None:
        await repos.append_message(
            agent_session, thread_id=thread.id, seq=1, role="candidate", text=answer
        )
    return sess, thread.id


def _patch_streaming_llm(monkeypatch: pytest.MonkeyPatch, deltas: list[str]) -> None:
    """Every ``chat_model()`` call returns the same deltas — the combined JSON
    feeds both the judge and the model-answer parse."""

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


def _patch_streaming_llm_per_call(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]
) -> list[list[Any]]:
    """Each element of ``calls`` is the deltas for the Nth ``chat_model()``
    invocation (judge first, model-answer second). Returns a list that captures
    the messages passed to each call's ``astream``."""
    state = {"i": 0}
    captured: list[list[Any]] = []

    class _FakeChunk:
        def __init__(self, content: str) -> None:
            self.content = content

    class _FakeBound:
        def __init__(self, deltas: list[str]) -> None:
            self._deltas = deltas

        async def astream(self, messages: list[Any]) -> AsyncIterator[Any]:
            captured.append(messages)
            for d in self._deltas:
                yield _FakeChunk(d)

    def fake_chat_model(**_: object) -> Any:
        idx = min(state["i"], len(calls) - 1)
        deltas = calls[idx]
        state["i"] += 1
        m = AsyncMock()
        m.bind = lambda **_kwargs: _FakeBound(deltas)
        return m

    monkeypatch.setattr(evaluator, "chat_model", fake_chat_model)
    return captured


async def _drain(
    sess: SessionRow, thread_id: uuid.UUID, alice: User, **kwargs: Any
) -> dict[str, Any]:
    """Run stream_thread_evaluation, collecting events into a flat dict."""
    out: dict[str, Any] = {
        "score": None,
        "feedback": "",
        "model_answer": "",
        "model_answer_error": None,
        "evaluation_done": None,
        "wrap": None,
        "evaluation_start": None,
    }
    defaults: dict[str, Any] = {
        "thread_index": 0,
        "anchors": ["specifics", "tradeoffs", "outcome"],
        "grounding": [],
        "focus_key": "project:AsyncAPI",
        "round_type": "experience_deep_dive",
    }
    defaults.update(kwargs)
    async for kind, data in evaluator.stream_thread_evaluation(
        session_id=sess.id, user_id=alice.id, thread_id=thread_id, **defaults
    ):
        if kind == "evaluation":
            out["evaluation_start"] = data
        elif kind == "score":
            out["score"] = data
        elif kind == "feedback_token":
            out["feedback"] += data
        elif kind == "model_answer_token":
            out["model_answer"] += data
        elif kind == "model_answer_error":
            out["model_answer_error"] = data
        elif kind == "evaluation_done":
            out["evaluation_done"] = data
        elif kind == "wrap":
            out["wrap"] = data
    return out


async def test_happy_path_streams_persists_and_completes_session(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3-question session, evaluating the last (thread_index=2) open thread with
    two prior closed threads → session flips to complete + wrap fires."""
    sess, thread_id = await _open_thread_with_answer(
        agent_session, alice, seeded_job, n_questions=3, thread_index=2, n_prior_closed=2
    )
    _patch_streaming_llm(
        monkeypatch,
        [
            '{"score": 8, "feedback": "Strong on tradeoffs',
            ' but missed metrics.", "model_answer": "When I led the rewrite, I..."}',
        ],
    )

    out = await _drain(sess, thread_id, alice, thread_index=2)

    assert out["evaluation_start"] == {"thread_index": 2}
    assert out["score"] == 8
    assert out["feedback"] == "Strong on tradeoffs but missed metrics."
    assert out["model_answer"] == "When I led the rewrite, I..."
    assert out["evaluation_done"] == {
        "thread_index": 2,
        "session_status": "complete",
        "n_remaining": 0,
    }
    assert out["wrap"] == {"session_status": "complete"}

    # Persistence: thread closed with eval, session flipped.
    factory = evaluator.AsyncSessionLocal
    async with factory() as fresh:
        thread = await repos.get_thread(fresh, thread_id)
        sess_fresh = await repos.get_session(fresh, sess.id, alice.id)
    assert thread is not None
    assert thread.status == "closed"
    assert thread.score == 8
    assert thread.feedback == "Strong on tradeoffs but missed metrics."
    assert thread.model_answer == "When I led the rewrite, I..."
    assert sess_fresh is not None
    assert sess_fresh.status == "complete"


async def test_non_final_thread_keeps_session_active(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess, thread_id = await _open_thread_with_answer(
        agent_session, alice, seeded_job, n_questions=3, thread_index=0
    )
    _patch_streaming_llm(monkeypatch, ['{"score": 6, "feedback": "ok", "model_answer": "x"}'])

    out = await _drain(sess, thread_id, alice, thread_index=0)

    assert out["evaluation_done"] == {
        "thread_index": 0,
        "session_status": "active",
        "n_remaining": 2,
    }
    assert out["wrap"] is None

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
    sess, thread_id = await _open_thread_with_answer(agent_session, alice, seeded_job)
    _patch_streaming_llm(monkeypatch, ['{"score": 11, "feedback": "x", "model_answer": "y"}'])

    with pytest.raises(StreamingJsonError):
        await _drain(sess, thread_id, alice)


async def test_thread_not_found(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess, _ = await _open_thread_with_answer(agent_session, alice, seeded_job)
    _patch_streaming_llm(monkeypatch, ['{"score": 5, "feedback": "x", "model_answer": "y"}'])

    with pytest.raises(evaluator.ThreadNotFound):
        await _drain(sess, uuid.uuid4(), alice)


async def test_already_closed_thread_blocked(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-evaluating a closed thread is refused (idempotency guard)."""
    sess, thread_id = await _open_thread_with_answer(agent_session, alice, seeded_job)
    await repos.close_thread(agent_session, thread_id, score=5, feedback="ok", model_answer="ok")
    _patch_streaming_llm(monkeypatch, ['{"score": 7, "feedback": "x", "model_answer": "y"}'])

    with pytest.raises(evaluator.ThreadNotFound):
        await _drain(sess, thread_id, alice)


async def test_transcript_threaded_into_judge_prompt(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The judge user message must include the root question, the anchors, and
    the candidate's answer so the LLM scores against the rubric over the whole
    transcript."""
    sess, thread_id = await _open_thread_with_answer(agent_session, alice, seeded_job)
    captured = _patch_streaming_llm_per_call(
        monkeypatch,
        [
            ['{"score": 7, "feedback": "x"}'],
            ['{"model_answer": "y"}'],
        ],
    )

    await _drain(sess, thread_id, alice)

    [_sys, judge_user] = captured[0]
    assert "Walk me through your last project." in judge_user.content
    assert "specifics" in judge_user.content
    assert "tradeoffs" in judge_user.content
    assert "I'd start by clarifying requirements." in judge_user.content


async def test_carried_grounding_reused_in_model_answer_no_retrieval(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 34: the evaluator does NO retrieval. The grounding retrieved once
    at thread-open is carried in and reused in the model-answer call only."""
    assert not hasattr(evaluator, "retrieve_grounding")

    sess, thread_id = await _open_thread_with_answer(agent_session, alice, seeded_job)
    captured = _patch_streaming_llm_per_call(
        monkeypatch,
        [
            ['{"score": 7, "feedback": "x"}'],
            ['{"model_answer": "y"}'],
        ],
    )
    marker = "UNIQUE_GROUNDING_MARKER def handler()"
    grounding = [{"source": "app.py", "text": marker}]

    await _drain(sess, thread_id, alice, grounding=grounding)

    [_jsys, judge_user] = captured[0]
    [_msys, ma_user] = captured[1]
    # Grounding rides the model-answer call, not the judge call.
    assert marker in ma_user.content
    assert marker not in judge_user.content


async def test_model_answer_failure_persists_partial(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the model-answer call fails, score+feedback persist (model_answer
    stays NULL) and ``model_answer_error`` is emitted — the partial-eval path."""
    sess, thread_id = await _open_thread_with_answer(
        agent_session, alice, seeded_job, n_questions=1, thread_index=0
    )
    # Judge call succeeds; model-answer call streams nothing → StreamingJsonError
    # inside _run_model_answer_call, caught by the node.
    _patch_streaming_llm_per_call(
        monkeypatch,
        [
            ['{"score": 9, "feedback": "great"}'],
            [],
        ],
    )

    out = await _drain(sess, thread_id, alice, thread_index=0)

    assert out["score"] == 9
    assert out["feedback"] == "great"
    assert out["model_answer_error"] is not None
    assert out["evaluation_done"]["session_status"] == "complete"

    factory = evaluator.AsyncSessionLocal
    async with factory() as fresh:
        thread = await repos.get_thread(fresh, thread_id)
    assert thread is not None
    assert thread.status == "closed"
    assert thread.score == 9
    assert thread.feedback == "great"
    assert thread.model_answer is None
