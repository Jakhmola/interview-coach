"""QuestionGenerator unit tests with mocked LLM streaming."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from interview_coach.agents.nodes import question_generator
from interview_coach.db import models, repos
from interview_coach.db.models import Job, SessionRow, User


@pytest.fixture
async def agent_session(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(question_generator, "AsyncSessionLocal", factory)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def alice(agent_session: AsyncSession) -> User:
    return await repos.create_user(agent_session, "alice@example.com", "x")


@pytest.fixture
async def seeded_job(agent_session: AsyncSession, alice: User) -> Job:
    job = await repos.create_job(
        agent_session,
        user_id=alice.id,
        source="pasted",
        raw_text="Senior backend engineer at Acme.",
    )
    await repos.update_job_parsed_json(
        agent_session,
        job.id,
        alice.id,
        {
            "title": "Senior Backend Engineer",
            "seniority": "senior",
            "must_have_skills": ["python", "fastapi"],
            "nice_to_have_skills": ["kubernetes"],
            "responsibilities": ["Own backend services."],
            "behavioral_signals": ["ownership", "mentorship"],
            "company_name": "Acme",
        },
    )
    return job


@pytest.fixture
async def seeded_profile(agent_session: AsyncSession, alice: User) -> None:
    await repos.upsert_profile(
        agent_session,
        user_id=alice.id,
        profile_json={
            "summary": "Backend engineer with FastAPI experience.",
            "skills": ["python", "fastapi", "postgres"],
            "experiences": [
                {
                    "company": "Globex",
                    "role": "Senior SWE",
                    "start": "2021",
                    "end": "present",
                    "highlights": ["Rewrote sync stack to async, 40% latency drop."],
                }
            ],
            "projects": [
                {
                    "name": "AsyncAPI",
                    "description": "Internal high-throughput API gateway.",
                    "tech": ["python", "fastapi"],
                    "role": "tech lead",
                }
            ],
            "education": [],
        },
        source_doc_ids=["doc-1"],
        model_name="qwen3-8b",
    )


@pytest.fixture
async def seeded_snapshot(agent_session: AsyncSession, seeded_job: Job) -> None:
    await repos.upsert_company_snapshot(
        agent_session,
        job_id=seeded_job.id,
        company_name="Acme",
        snapshot_json={
            "mission": "Acme builds rockets.",
            "products": ["rockets"],
            "recent_news": [],
            "values_and_signals": ["high autonomy"],
        },
        source_urls=["https://acme.example"],
        model_name="qwen3-8b",
    )


async def _make_session(
    agent_session: AsyncSession, alice: User, job: Job, *, round_type: str
) -> SessionRow:
    return await repos.create_session(
        agent_session,
        user_id=alice.id,
        job_id=job.id,
        round_type=round_type,
        n_questions=5,
    )


def _patch_streaming_llm(monkeypatch: pytest.MonkeyPatch, deltas: list[str]) -> list[list[Any]]:
    """Wires `chat_model().bind(...)` to an object whose `astream` yields fake
    chunks. Returns a captured-messages list the test can inspect."""
    captured_messages: list[list[Any]] = []

    class _FakeChunk:
        def __init__(self, content: str) -> None:
            self.content = content

    class _FakeBound:
        async def astream(self, messages: list[Any]) -> AsyncIterator[Any]:
            captured_messages.append(messages)
            for d in deltas:
                yield _FakeChunk(d)

    def fake_chat_model(**_: object) -> Any:
        m = AsyncMock()
        m.bind = lambda **_kwargs: _FakeBound()
        return m

    monkeypatch.setattr(question_generator, "chat_model", fake_chat_model)
    return captured_messages


async def test_generate_resume_walkthrough_streams_and_persists(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    seeded_snapshot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = await _make_session(agent_session, alice, seeded_job, round_type="resume_walkthrough")
    captured = _patch_streaming_llm(
        monkeypatch,
        [
            '{"question": "Walk me through ',
            'the AsyncAPI rewrite.", "anchors": ["specific tradeoff", ',
            '"measurable impact", "candidate vs team"]}',
        ],
    )

    streamed = ""
    final: dict[str, Any] | None = None
    async for kind, data in question_generator.stream_question(
        session_id=sess.id, user_id=alice.id
    ):
        if kind == "token":
            streamed += data
        elif kind == "done":
            final = data

    assert streamed == "Walk me through the AsyncAPI rewrite."
    assert final is not None
    assert "question_id" in final
    assert final["turn_index"] == 0

    # Persisted turn matches the streamed text byte-for-byte.
    factory = question_generator.AsyncSessionLocal
    async with factory() as fresh:
        turns = await repos.list_turns_for_session(fresh, sess.id)
    assert len(turns) == 1
    assert turns[0].question == "Walk me through the AsyncAPI rewrite."
    assert turns[0].anchors_json == [
        "specific tradeoff",
        "measurable impact",
        "candidate vs team",
    ]

    # User message includes profile context (the project name) and round_type.
    [system_msg, user_msg] = captured[0]
    assert "AsyncAPI" in user_msg.content
    assert '"round_type": "resume_walkthrough"' in user_msg.content


async def test_generate_behavioral_threads_focus_signal(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    seeded_snapshot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = await _make_session(agent_session, alice, seeded_job, round_type="behavioral_star")
    monkeypatch.setattr("random.choice", lambda xs: xs[0])  # deterministic: "ownership"
    captured = _patch_streaming_llm(
        monkeypatch,
        [
            '{"question": "Tell me about a time you took ownership.", ',
            '"anchors": ["explicit conflict", "outcome", "lessons"]}',
        ],
    )

    streamed = ""
    async for kind, data in question_generator.stream_question(
        session_id=sess.id, user_id=alice.id
    ):
        if kind == "token":
            streamed += data

    assert streamed == "Tell me about a time you took ownership."
    [_sys, user_msg] = captured[0]
    assert '"focus_signal": "ownership"' in user_msg.content

    # Metadata records the chosen signal.
    factory = question_generator.AsyncSessionLocal
    async with factory() as fresh:
        turns = await repos.list_turns_for_session(fresh, sess.id)
    assert turns[0].metadata_json == {"focus_signal": "ownership"}


async def test_behavioral_falls_back_to_company_signals(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When job.parsed_json.behavioral_signals is empty, use the company snapshot's."""
    # Replace the JD analysis to remove behavioral signals.
    await repos.update_job_parsed_json(
        agent_session,
        seeded_job.id,
        alice.id,
        {
            "title": "Senior Backend Engineer",
            "seniority": "senior",
            "must_have_skills": ["python"],
            "nice_to_have_skills": [],
            "responsibilities": [],
            "behavioral_signals": [],
            "company_name": "Acme",
        },
    )
    await repos.upsert_company_snapshot(
        agent_session,
        job_id=seeded_job.id,
        company_name="Acme",
        snapshot_json={
            "mission": "Acme builds rockets.",
            "products": [],
            "recent_news": [],
            "values_and_signals": ["written-doc culture"],
        },
        source_urls=[],
        model_name="qwen3-8b",
    )
    sess = await _make_session(agent_session, alice, seeded_job, round_type="behavioral_star")
    monkeypatch.setattr("random.choice", lambda xs: xs[0])
    captured = _patch_streaming_llm(
        monkeypatch,
        ['{"question": "X", "anchors": ["a", "b", "c"]}'],
    )

    async for _ in question_generator.stream_question(session_id=sess.id, user_id=alice.id):
        pass

    [_sys, user_msg] = captured[0]
    assert '"focus_signal": "written-doc culture"' in user_msg.content


async def test_prereqs_missing_profile(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_snapshot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Profile not built yet → typed error."""
    sess = await _make_session(agent_session, alice, seeded_job, round_type="resume_walkthrough")
    _patch_streaming_llm(monkeypatch, ['{"question": "X", "anchors": ["a"]}'])

    with pytest.raises(question_generator.GenerationPrereqsMissing) as exc:
        async for _ in question_generator.stream_question(session_id=sess.id, user_id=alice.id):
            pass
    assert "profile_missing" in str(exc.value)


async def test_session_complete_rejected(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    seeded_snapshot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking for a question past n_questions raises."""
    sess = await repos.create_session(
        agent_session,
        user_id=alice.id,
        job_id=seeded_job.id,
        round_type="resume_walkthrough",
        n_questions=1,
    )
    # Insert one already-answered turn.
    await repos.create_turn(
        agent_session,
        session_id=sess.id,
        turn_index=0,
        question="Q",
        anchors=["a"],
    )
    _patch_streaming_llm(monkeypatch, ['{"question": "X", "anchors": ["a"]}'])

    with pytest.raises(ValueError, match="session_complete"):
        async for _ in question_generator.stream_question(session_id=sess.id, user_id=alice.id):
            pass


async def test_session_not_found(
    agent_session: AsyncSession,
    alice: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_streaming_llm(monkeypatch, ['{"question": "x", "anchors": ["a"]}'])
    with pytest.raises(ValueError, match="session_not_found"):
        async for _ in question_generator.stream_question(
            session_id=uuid.uuid4(), user_id=alice.id
        ):
            pass


async def test_prior_turns_threaded_into_prompt(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    seeded_snapshot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = await _make_session(agent_session, alice, seeded_job, round_type="resume_walkthrough")
    await repos.create_turn(
        agent_session,
        session_id=sess.id,
        turn_index=0,
        question="Walk me through AsyncAPI.",
        anchors=["scope"],
    )
    # Manually fill in answer so the next-question generator doesn't see an
    # unanswered prior turn (the API layer enforces that; node trusts caller).
    factory = question_generator.AsyncSessionLocal
    async with factory() as s:
        from sqlalchemy import update

        from interview_coach.db.models import TurnRow

        await s.execute(
            update(TurnRow)
            .where(TurnRow.session_id == sess.id, TurnRow.turn_index == 0)
            .values(answer="I rewrote the sync stack to asyncio.")
        )
        await s.commit()

    captured = _patch_streaming_llm(
        monkeypatch, ['{"question": "Followup", "anchors": ["a", "b", "c"]}']
    )

    async for _ in question_generator.stream_question(session_id=sess.id, user_id=alice.id):
        pass

    [_sys, user_msg] = captured[0]
    assert "Walk me through AsyncAPI." in user_msg.content
    assert "rewrote the sync stack" in user_msg.content
