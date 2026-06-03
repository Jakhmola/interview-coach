"""Interviewer/conductor unit tests with mocked LLM streaming (Phase 34).

Two entry points are exercised:

* :func:`stream_open_thread` — opens a thread (focus pick + optional repo
  grounding + root question), persists the ``Thread`` + its root ``Message``.
* :func:`stream_conduct` — one conductor step on an open thread, emitting the
  interviewer's next move (probe/clarify/nudge/advance).
"""

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
from interview_coach.rag.retrieval import GroundingHit


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
                    "highlights": [
                        {
                            "text": "Rewrote sync stack to async, 40% latency drop.",
                            "tech_stack": [],
                            "description": None,
                            "urls": [],
                            "source_document_ids": [],
                        }
                    ],
                }
            ],
            "projects": [
                {
                    "name": "AsyncAPI",
                    "description": "Internal high-throughput API gateway.",
                    "tech": ["python", "fastapi"],
                    "role": "tech lead",
                    "urls": [],
                    "source": "project_doc",
                    "source_document_ids": [],
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
    agent_session: AsyncSession, alice: User, job: Job, *, round_type: str, n_questions: int = 5
) -> SessionRow:
    return await repos.create_session(
        agent_session,
        user_id=alice.id,
        job_id=job.id,
        round_type=round_type,
        n_questions=n_questions,
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


def _pin_focus(monkeypatch: pytest.MonkeyPatch, predicate: Any) -> None:
    """Pin the weighted focus sample to the first candidate matching ``predicate``
    (the picker is randomised in production)."""
    monkeypatch.setattr(
        question_generator.random.Random,
        "choices",
        lambda self, population, weights=None, k=1: [next(t for t in population if predicate(t))],
    )


async def _collect(agen: AsyncIterator[tuple[str, Any]]) -> list[tuple[str, Any]]:
    return [ev async for ev in agen]


async def _seed_open_thread(
    agent_session: AsyncSession,
    sess: SessionRow,
    *,
    thread_index: int = 0,
    anchors: list[str] | None = None,
    focus_key: str | None = "project:AsyncAPI",
    focus_label: str | None = "AsyncAPI",
    question: str = "Walk me through AsyncAPI.",
    answer: str | None = "I rewrote the sync stack to async.",
) -> Any:
    anchors = anchors if anchors is not None else ["specifics", "tradeoffs"]
    thread = await repos.create_thread(
        agent_session,
        session_id=sess.id,
        thread_index=thread_index,
        anchors=anchors,
        focus_key=focus_key,
        focus_label=focus_label,
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
    return thread


# --- stream_open_thread ----------------------------------------------------


async def test_open_thread_experience_streams_and_persists(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    seeded_snapshot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = await _make_session(agent_session, alice, seeded_job, round_type="experience_deep_dive")
    _pin_focus(monkeypatch, lambda t: "AsyncAPI" in t.label)
    captured = _patch_streaming_llm(
        monkeypatch,
        [
            '{"question": "Walk me through ',
            'the AsyncAPI rewrite.", "anchors": ["specific tradeoff", ',
            '"measurable impact", "candidate vs team"]}',
        ],
    )

    events = await _collect(
        question_generator.stream_open_thread(session_id=sess.id, user_id=alice.id)
    )
    kinds = [k for k, _ in events]
    by_kind = {k: d for k, d in events}

    # Envelope order: move → token(s) → move_done → opened.
    assert kinds[0] == "move"
    assert "opened" in kinds
    move = by_kind["move"]
    assert move["kind"] == "question"
    assert move["thread_index"] == 0
    assert uuid.UUID(move["message_id"])  # parseable

    streamed = "".join(d for k, d in events if k == "token")
    assert streamed == "Walk me through the AsyncAPI rewrite."

    move_done = by_kind["move_done"]
    assert move_done["anchors"] == ["specific tradeoff", "measurable impact", "candidate vs team"]

    opened = by_kind["opened"]
    assert opened["thread_index"] == 0
    assert opened["focus_key"] == "project:AsyncAPI"
    assert "AsyncAPI" in (opened["focus_label"] or "")
    assert opened["anchors"] == ["specific tradeoff", "measurable impact", "candidate vs team"]
    assert opened["grounding"] == []  # project_doc focus → not repo-backed

    # Persisted Thread + root Message (seq=0, interviewer/question).
    factory = question_generator.AsyncSessionLocal
    async with factory() as fresh:
        threads = list(await repos.list_threads_for_session(fresh, sess.id))
        msgs = list(await repos.list_messages_for_thread(fresh, uuid.UUID(opened["thread_id"])))
    assert len(threads) == 1
    assert threads[0].status == "open"
    assert threads[0].anchors_json == [
        "specific tradeoff",
        "measurable impact",
        "candidate vs team",
    ]
    assert len(msgs) == 1
    assert msgs[0].seq == 0
    assert msgs[0].role == "interviewer"
    assert msgs[0].kind == "question"
    assert msgs[0].text == "Walk me through the AsyncAPI rewrite."
    assert str(msgs[0].id) == move["message_id"]

    # Framing fields + chosen focus_target ride in the user message.
    [system_msg, user_msg] = captured[0]
    assert "AsyncAPI" in user_msg.content
    assert '"company": "Acme"' in user_msg.content
    assert '"role": "Senior Backend Engineer"' in user_msg.content
    assert '"focus_target":' in user_msg.content
    assert "Acme" in system_msg.content


async def test_open_thread_behavioral_threads_focus_target(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    seeded_snapshot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = await _make_session(agent_session, alice, seeded_job, round_type="behavioral_star")
    _pin_focus(monkeypatch, lambda t: t.label == "ownership")
    captured = _patch_streaming_llm(
        monkeypatch,
        [
            '{"question": "Tell me about a time you took ownership.", ',
            '"anchors": ["explicit conflict", "outcome", "lessons"]}',
        ],
    )

    events = await _collect(
        question_generator.stream_open_thread(session_id=sess.id, user_id=alice.id)
    )
    streamed = "".join(d for k, d in events if k == "token")
    assert streamed == "Tell me about a time you took ownership."

    [_sys, user_msg] = captured[0]
    assert '"focus_target": "ownership"' in user_msg.content

    # Thread records the chosen focus key + label.
    factory = question_generator.AsyncSessionLocal
    async with factory() as fresh:
        threads = list(await repos.list_threads_for_session(fresh, sess.id))
    assert threads[0].focus_key == "ownership"
    assert threads[0].focus_label == "ownership"


async def test_open_thread_behavioral_falls_back_to_company_signals(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    _pin_focus(monkeypatch, lambda t: True)
    captured = _patch_streaming_llm(monkeypatch, ['{"question": "X", "anchors": ["a", "b", "c"]}'])

    await _collect(question_generator.stream_open_thread(session_id=sess.id, user_id=alice.id))

    [_sys, user_msg] = captured[0]
    assert '"focus_target": "written-doc culture"' in user_msg.content


async def test_open_thread_prereqs_missing_profile(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_snapshot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = await _make_session(agent_session, alice, seeded_job, round_type="experience_deep_dive")
    _patch_streaming_llm(monkeypatch, ['{"question": "X", "anchors": ["a"]}'])

    with pytest.raises(question_generator.GenerationPrereqsMissing) as exc:
        await _collect(question_generator.stream_open_thread(session_id=sess.id, user_id=alice.id))
    assert "profile_missing" in str(exc.value)


async def test_open_thread_session_complete_rejected(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    seeded_snapshot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All topics already closed (count_closed_threads >= n_questions) → reject."""
    sess = await _make_session(
        agent_session, alice, seeded_job, round_type="experience_deep_dive", n_questions=1
    )
    closed = await repos.create_thread(
        agent_session, session_id=sess.id, thread_index=0, anchors=["a"], focus_key="x"
    )
    await repos.close_thread(agent_session, closed.id, score=5, feedback="ok", model_answer="ok")
    _patch_streaming_llm(monkeypatch, ['{"question": "X", "anchors": ["a"]}'])

    with pytest.raises(ValueError, match="session_complete"):
        await _collect(question_generator.stream_open_thread(session_id=sess.id, user_id=alice.id))


async def test_open_thread_session_not_found(
    agent_session: AsyncSession,
    alice: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_streaming_llm(monkeypatch, ['{"question": "x", "anchors": ["a"]}'])
    with pytest.raises(ValueError, match="session_not_found"):
        await _collect(
            question_generator.stream_open_thread(session_id=uuid.uuid4(), user_id=alice.id)
        )


async def test_open_thread_prior_topics_threaded_into_prompt(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    seeded_profile: None,
    seeded_snapshot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prior (closed) thread's focus_label is fed forward as a prior_topic so
    the next question avoids re-asking the same topic."""
    sess = await _make_session(agent_session, alice, seeded_job, round_type="experience_deep_dive")
    prior = await repos.create_thread(
        agent_session,
        session_id=sess.id,
        thread_index=0,
        anchors=["a"],
        focus_key="highlight:0:0",
        focus_label="Old Topic Label",
    )
    await repos.append_message(
        agent_session, thread_id=prior.id, seq=0, role="interviewer", kind="question", text="Q"
    )
    await repos.close_thread(agent_session, prior.id, score=5, feedback="ok", model_answer="ok")

    _pin_focus(monkeypatch, lambda t: "AsyncAPI" in t.label)
    captured = _patch_streaming_llm(
        monkeypatch, ['{"question": "Followup", "anchors": ["a", "b", "c"]}']
    )

    events = await _collect(
        question_generator.stream_open_thread(session_id=sess.id, user_id=alice.id)
    )
    by_kind = {k: d for k, d in events}
    # New thread takes the next index after the closed one.
    assert by_kind["opened"]["thread_index"] == 1

    [_sys, user_msg] = captured[0]
    assert "Old Topic Label" in user_msg.content


async def test_open_thread_repo_backed_focus_retrieves_grounding(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repo-backed (github) experience focus triggers ONE code retrieval at
    thread-open; the hits ride into the question prompt and the carried
    grounding (reused later by the conductor + thread-close model answer)."""
    sess = await _make_session(agent_session, alice, seeded_job, round_type="experience_deep_dive")
    doc_id = str(uuid.uuid4())
    profile = {
        "summary": "Eng.",
        "skills": [],
        "experiences": [],
        "projects": [
            {
                "name": "RepoProj",
                "description": "My open-source gateway.",
                "tech": ["python", "fastapi"],
                "role": None,
                "urls": [],
                "source": "github",
                "source_document_ids": [doc_id],
            }
        ],
        "education": [],
    }
    job = {"title": "Senior Backend Engineer", "must_have_skills": ["python", "fastapi"]}
    company = {"mission": "x", "values_and_signals": []}

    captured_retrieval: dict[str, Any] = {}

    async def fake_retrieve(**kwargs: Any) -> list[GroundingHit]:
        captured_retrieval.update(kwargs)
        return [
            GroundingHit(
                text="def handler(): return 200",
                document_id=uuid.UUID(doc_id),
                source_doc_kind="github_repo",
                chunk_index=0,
                score=0.9,
                filename="app.py",
            )
        ]

    monkeypatch.setattr(question_generator, "retrieve_grounding", fake_retrieve)
    _pin_focus(monkeypatch, lambda t: t.repo_backed)
    captured = _patch_streaming_llm(
        monkeypatch, ['{"question": "How does app.py route?", "anchors": ["a", "b", "c"]}']
    )

    events = await _collect(
        question_generator.stream_open_thread(
            session_id=sess.id, user_id=alice.id, profile=profile, job=job, company=company
        )
    )
    by_kind = {k: d for k, d in events}

    assert captured_retrieval.get("retries") == 1
    assert captured_retrieval.get("source_kinds") == ("github_repo",)
    assert by_kind["opened"]["grounding"] == [
        {"source": "app.py", "text": "def handler(): return 200"}
    ]
    [_sys, user_msg] = captured[0]
    assert "def handler(): return 200" in user_msg.content


# --- stream_conduct --------------------------------------------------------


async def _conduct(
    sess: SessionRow,
    thread: Any,
    alice: User,
    *,
    round_type: str = "experience_deep_dive",
    anchors: list[str] | None = None,
    focus_label: str | None = "AsyncAPI",
    grounding: list[dict[str, Any]] | None = None,
    job: dict[str, Any] | None = None,
) -> list[tuple[str, Any]]:
    return await _collect(
        question_generator.stream_conduct(
            session_id=sess.id,
            user_id=alice.id,
            thread_id=thread.id,
            thread_index=thread.thread_index,
            anchors=anchors if anchors is not None else ["specifics", "tradeoffs"],
            focus_label=focus_label,
            grounding=grounding or [],
            round_type=round_type,
            job=job if job is not None else {"company_name": "Acme", "title": "Senior Backend Eng"},
        )
    )


async def test_conduct_probe_streams_and_persists(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = await _make_session(agent_session, alice, seeded_job, round_type="experience_deep_dive")
    thread = await _seed_open_thread(agent_session, sess)
    _patch_streaming_llm(
        monkeypatch,
        ['{"action": "probe", "message": "What was the hardest tradeoff?"}'],
    )

    events = await _conduct(sess, thread, alice)
    kinds = [k for k, _ in events]
    by_kind = {k: d for k, d in events}

    assert "advance" not in kinds
    assert by_kind["move"]["kind"] == "probe"
    assert by_kind["move"]["thread_index"] == 0
    streamed = "".join(d for k, d in events if k == "token")
    assert streamed == "What was the hardest tradeoff?"
    assert by_kind["conducted"]["action"] == "probe"
    assert by_kind["conducted"]["message_id"] == by_kind["move"]["message_id"]

    # Persisted as an interviewer message at seq=2 with kind=probe.
    factory = question_generator.AsyncSessionLocal
    async with factory() as fresh:
        msgs = list(await repos.list_messages_for_thread(fresh, thread.id))
    assert len(msgs) == 3
    assert msgs[2].seq == 2
    assert msgs[2].role == "interviewer"
    assert msgs[2].kind == "probe"
    assert msgs[2].text == "What was the hardest tradeoff?"


async def test_conduct_advance_emits_advance_only(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = await _make_session(agent_session, alice, seeded_job, round_type="experience_deep_dive")
    thread = await _seed_open_thread(agent_session, sess)
    _patch_streaming_llm(monkeypatch, ['{"action": "advance", "message": ""}'])

    events = await _conduct(sess, thread, alice)
    kinds = [k for k, _ in events]

    assert kinds == ["advance"]
    # No follow-up message persisted (the thread just closes).
    factory = question_generator.AsyncSessionLocal
    async with factory() as fresh:
        n = await repos.count_messages_for_thread(fresh, thread.id)
    assert n == 2


async def test_conduct_coerces_disallowed_nudge_to_probe(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """behavioral_star disallows nudge → a model-returned nudge is coerced to probe."""
    sess = await _make_session(agent_session, alice, seeded_job, round_type="behavioral_star")
    thread = await _seed_open_thread(agent_session, sess)
    _patch_streaming_llm(monkeypatch, ['{"action": "nudge", "message": "Take your time."}'])

    events = await _conduct(sess, thread, alice, round_type="behavioral_star")
    by_kind = {k: d for k, d in events}

    assert by_kind["move"]["kind"] == "probe"
    assert by_kind["conducted"]["action"] == "probe"
    factory = question_generator.AsyncSessionLocal
    async with factory() as fresh:
        msgs = list(await repos.list_messages_for_thread(fresh, thread.id))
    assert msgs[2].kind == "probe"


async def test_conduct_empty_message_uses_fallback(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = await _make_session(agent_session, alice, seeded_job, round_type="experience_deep_dive")
    thread = await _seed_open_thread(agent_session, sess)
    _patch_streaming_llm(monkeypatch, ['{"action": "probe", "message": ""}'])

    await _conduct(sess, thread, alice)

    factory = question_generator.AsyncSessionLocal
    async with factory() as fresh:
        msgs = list(await repos.list_messages_for_thread(fresh, thread.id))
    assert msgs[2].text == "Can you walk me through that in a bit more detail?"


async def test_conduct_payload_includes_transcript_and_allowed_actions(
    agent_session: AsyncSession,
    alice: User,
    seeded_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = await _make_session(agent_session, alice, seeded_job, round_type="experience_deep_dive")
    thread = await _seed_open_thread(agent_session, sess)
    captured = _patch_streaming_llm(
        monkeypatch, ['{"action": "clarify", "message": "Do you mean the gateway?"}']
    )

    await _conduct(
        sess,
        thread,
        alice,
        grounding=[{"source": "x.py", "text": "GROUNDMARK"}],
    )

    [_sys, user_msg] = captured[0]
    content = user_msg.content
    assert "Walk me through AsyncAPI." in content  # transcript root question
    assert "I rewrote the sync stack to async." in content  # transcript answer
    assert "allowed_actions" in content
    assert "GROUNDMARK" in content  # carried grounding reused
    assert '"focus_target": "AsyncAPI"' in content
