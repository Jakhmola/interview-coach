"""Phase 6 end-to-end agent smoke against real Ollama and a live Postgres
api running under docker compose. Opt-in via INTEGRATION=1.

What it does:
- Registers a fresh user via the api.
- Uploads a small generated PDF as the CV.
- Submits a pasted JD.
- Calls ProfileBuilder.build_profile and JobAnalyzer.analyze_job.
- Asserts non-empty extracted fields (skills, must_have_skills).

Slow: ~3-8 minutes depending on host. Skipped by default so `make test` stays fast.
"""

from __future__ import annotations

import io
import os
import uuid
from typing import Any

import httpx
import pytest
from reportlab.pdfgen import canvas

from interview_coach.agents.nodes.company_researcher import research_company
from interview_coach.agents.nodes.evaluator import stream_thread_evaluation
from interview_coach.agents.nodes.job_analyzer import analyze_job
from interview_coach.agents.nodes.profile_builder import build_profile
from interview_coach.agents.nodes.question_generator import stream_open_thread

API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION") != "1",
    reason="Set INTEGRATION=1 to run; requires docker stack up + ollama on host with qwen3:8b.",
)


def _make_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, line in enumerate(text.split("\n")):
        c.drawString(72, 720 - i * 14, line[:90])
    c.showPage()
    c.save()
    return buf.getvalue()


CV_TEXT = """Alice Engineer
Senior Backend Engineer
6 years of Python, FastAPI, Postgres.
Worked at Acme building async APIs.
Project: rewrote sync stack to asyncio (40% latency drop).
BS Computer Science, State University, 2014-2018."""

JD_TEXT = """Senior Backend Engineer at Globex.
Required: Python, FastAPI, Postgres, async programming.
Nice to have: Kubernetes, Kafka.
You will: design and own backend services, mentor mid-level engineers,
collaborate with product, write production-grade code.
We value ownership, clear written communication, and pragmatism."""

# A JD that names a real, well-indexed public company so the CompanyResearcher
# loop has something to find. Used only by the company-research integration test.
JD_TEXT_REAL_COMPANY = """Member of Technical Staff at Anthropic.
You will help build safe, beneficial AI systems alongside the research team.
Required: strong Python, distributed systems experience, ML familiarity.
Nice to have: experience with LLMs, evaluations, or applied research.
We value clear written communication, technical rigor, and a focus on safety."""


async def _setup(jd_text: str = JD_TEXT) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (user_id, job_id) after seeding via the live API."""
    async with httpx.AsyncClient(timeout=30.0) as http:
        email = f"agent-int-{uuid.uuid4()}@test.com"
        r = await http.post(
            f"{API_URL}/auth/register",
            json={"email": email, "password": "hunter22a"},
        )
        r.raise_for_status()
        body = r.json()
        token = body["access_token"]
        user_id = uuid.UUID(body["user"]["id"])

        r = await http.post(
            f"{API_URL}/documents",
            headers={"Authorization": f"Bearer {token}"},
            data={"kind": "cv"},
            files={"file": ("alice_cv.pdf", _make_pdf(CV_TEXT), "application/pdf")},
        )
        r.raise_for_status()

        r = await http.post(
            f"{API_URL}/jobs",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": jd_text},
        )
        r.raise_for_status()
        job_id = uuid.UUID(r.json()["id"])

    return user_id, job_id


async def test_profile_builder_real_ollama() -> None:
    user_id, _ = await _setup()
    profile = await build_profile(user_id)
    assert profile.summary
    assert profile.skills, f"expected non-empty skills, got {profile!r}"


async def test_job_analyzer_real_ollama() -> None:
    user_id, job_id = await _setup()
    analysis = await analyze_job(job_id, user_id)
    assert analysis.title
    assert analysis.must_have_skills, f"expected must_have_skills, got {analysis!r}"


async def test_company_researcher_real() -> None:
    """End-to-end CompanyResearcher: real JobAnalyzer → real Tavily → real LLM.

    Requires TAVILY_API_KEY in the api environment + a recognizable company
    in the JD. Asserts non-empty mission and at least one product, plus a
    persisted row.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from interview_coach.config import settings
    from interview_coach.db import repos

    user_id, job_id = await _setup(JD_TEXT_REAL_COMPANY)

    analysis = await analyze_job(job_id, user_id)
    assert analysis.company_name, (
        f"phase 6 must populate company_name for this JD; got {analysis!r}"
    )

    snapshot = await research_company(job_id, user_id)
    assert snapshot.mission, f"empty mission, got {snapshot!r}"
    assert snapshot.products, f"expected at least one product, got {snapshot!r}"

    # Verify the row landed.
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        row = await repos.get_company_snapshot_by_job(s, job_id)
    await engine.dispose()
    assert row is not None
    assert row.source_urls, "expected at least one source URL on the persisted snapshot"


async def test_open_thread_real() -> None:
    """End-to-end: real ProfileBuilder + JobAnalyzer + CompanyResearcher → real
    streaming thread-open. Asserts the streamed question matches what was
    persisted as the thread's root message and that anchors are non-empty."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from interview_coach.config import settings
    from interview_coach.db import repos

    user_id, job_id = await _setup(JD_TEXT_REAL_COMPANY)

    await build_profile(user_id)
    await analyze_job(job_id, user_id)
    await research_company(job_id, user_id)

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        sess = await repos.create_session(
            s,
            user_id=user_id,
            job_id=job_id,
            round_type="experience_deep_dive",
            n_questions=3,
        )
    session_id = sess.id

    streamed = ""
    opened: dict[str, Any] | None = None
    async for kind, data in stream_open_thread(session_id=session_id, user_id=user_id):
        if kind == "token":
            streamed += data
        elif kind == "opened":
            opened = data

    assert streamed.strip(), "expected non-empty streamed question"
    assert opened is not None
    assert opened["thread_index"] == 0

    async with factory() as s:
        threads = list(await repos.list_threads_for_session(s, session_id))
        msgs = list(await repos.list_messages_for_thread(s, uuid.UUID(opened["thread_id"])))
    await engine.dispose()

    assert len(threads) == 1
    assert threads[0].status == "open"
    assert threads[0].anchors_json, "expected non-empty anchors"
    assert msgs[0].text == streamed, "streamed text and persisted root message must match"


async def test_thread_close_eval_real() -> None:
    """End-to-end Phase 34: profile → analyze → research → open thread →
    candidate answer → thread-close evaluation → assert persisted
    score/feedback/model_answer and the session flips to 'complete'
    (n_questions=1)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from interview_coach.config import settings
    from interview_coach.db import repos

    user_id, job_id = await _setup(JD_TEXT_REAL_COMPANY)

    await build_profile(user_id)
    await analyze_job(job_id, user_id)
    await research_company(job_id, user_id)

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        sess = await repos.create_session(
            s,
            user_id=user_id,
            job_id=job_id,
            round_type="experience_deep_dive",
            n_questions=1,
        )
    session_id = sess.id

    opened: dict[str, Any] | None = None
    async for kind, data in stream_open_thread(session_id=session_id, user_id=user_id):
        if kind == "opened":
            opened = data
    assert opened is not None
    thread_id = uuid.UUID(opened["thread_id"])

    canned_answer = (
        "I'd start by clarifying the requirements with stakeholders, then "
        "split the work into deliverable milestones. Specifically, I'd "
        "prototype the core path first to de-risk the unknowns, measure "
        "performance against an explicit budget, and iterate from there. "
        "I've done this on async refactors before and it kept us on time."
    )
    async with factory() as s:
        seq = await repos.count_messages_for_thread(s, thread_id)
        await repos.append_message(
            s, thread_id=thread_id, seq=seq, role="candidate", text=canned_answer
        )

    score: int | None = None
    feedback = ""
    model_answer = ""
    final: dict[str, Any] | None = None
    async for kind, data in stream_thread_evaluation(
        session_id=session_id,
        user_id=user_id,
        thread_id=thread_id,
        thread_index=opened["thread_index"],
        anchors=opened["anchors"],
        grounding=opened["grounding"],
        focus_key=opened["focus_key"],
        round_type="experience_deep_dive",
    ):
        if kind == "score":
            score = data
        elif kind == "feedback_token":
            feedback += data
        elif kind == "model_answer_token":
            model_answer += data
        elif kind == "evaluation_done":
            final = data

    assert score is not None and 1 <= score <= 10
    assert feedback.strip(), "expected non-empty feedback"
    assert model_answer.strip(), "expected non-empty model answer"
    assert final is not None
    assert final["session_status"] == "complete"

    async with factory() as s:
        thread = await repos.get_thread(s, thread_id)
        sess_fresh = await repos.get_session(s, session_id, user_id)
    await engine.dispose()

    assert thread is not None
    assert thread.status == "closed"
    assert thread.score == score
    assert thread.feedback == feedback
    assert thread.model_answer == model_answer
    assert sess_fresh is not None
    assert sess_fresh.status == "complete"


async def test_resumability_real(tmp_path: Any) -> None:
    """Phase 34: drive interview_graph against an AsyncSqliteSaver backed by a
    real SQLite file. The graph owns the whole session loop on one
    ``interview:{session_id}`` thread. We open the thread (interrupt at the
    candidate gate), then **close + reopen the saver** between every resume
    (simulating an api restart), feeding a canned answer until the session
    wraps — proving the loop survives restarts off the persisted checkpoint.
    """
    from langgraph.types import Command
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from interview_coach.agents.graph import build_interview_graph, open_checkpointer
    from interview_coach.config import settings
    from interview_coach.db import repos

    user_id, job_id = await _setup(JD_TEXT_REAL_COMPANY)

    await build_profile(user_id)
    await analyze_job(job_id, user_id)
    await research_company(job_id, user_id)

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        sess = await repos.create_session(
            s,
            user_id=user_id,
            job_id=job_id,
            round_type="experience_deep_dive",
            n_questions=1,
        )
    session_id = sess.id

    db_path = str(tmp_path / "graph.sqlite")
    thread_cfg = {"configurable": {"thread_id": f"interview:{session_id}"}}
    initial_state = {
        "user_id": str(user_id),
        "session_id": str(session_id),
        "round_type": "experience_deep_dive",
        "n_questions": 1,
        "thread_id": None,
        "thread_index": 0,
        "anchors": [],
        "focus_key": None,
        "focus_label": None,
        "focus_document_ids": [],
        "grounding": [],
        "followups_used": 0,
        "pending_message": None,
        "next_move": None,
    }

    # First "process": open the thread → stream question → interrupt at the gate.
    streamed_question = ""
    async with open_checkpointer(db_path) as saver:
        graph = build_interview_graph(saver)
        async for chunk in graph.astream(initial_state, config=thread_cfg, stream_mode="custom"):
            if chunk.get("event") == "token":
                streamed_question += chunk["data"]
    assert streamed_question.strip(), "question must stream out before we 'restart'"

    # The Thread + root question were persisted by stream_open_thread.
    async with factory() as s:
        threads = list(await repos.list_threads_for_session(s, session_id))
    assert len(threads) == 1

    canned_answer = (
        "I'd clarify scope, prototype the riskiest path, measure against an "
        "explicit budget, then iterate. I've done this on async refactors "
        "before — kept us on time and surfaced an issue early."
    )

    # Subsequent "processes": fresh saver from the same file each time, resume
    # with the candidate's answer until the session wraps. The conductor may
    # probe a few times before advancing; the per-thread follow-up budget bounds
    # the loop so this terminates.
    saw_wrap = False
    score: int | None = None
    for _ in range(6):
        async with open_checkpointer(db_path) as saver:
            graph = build_interview_graph(saver)
            state = await graph.aget_state(thread_cfg)
            if not state.next:  # reached END on a prior resume
                break
            async for chunk in graph.astream(
                Command(resume={"message": canned_answer}),
                config=thread_cfg,
                stream_mode="custom",
            ):
                event = chunk.get("event")
                if event == "score":
                    score = chunk["data"]
                elif event == "wrap":
                    saw_wrap = True
        if saw_wrap:
            break

    assert saw_wrap, "session never wrapped within the follow-up budget"
    assert score is not None and 1 <= score <= 10

    async with factory() as s:
        threads = list(await repos.list_threads_for_session(s, session_id))
        sess_fresh = await repos.get_session(s, session_id, user_id)
    await engine.dispose()

    assert threads[0].status == "closed"
    assert threads[0].score == score
    assert sess_fresh is not None
    assert sess_fresh.status == "complete"
