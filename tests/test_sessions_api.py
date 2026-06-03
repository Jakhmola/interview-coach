"""API tests for /sessions including SSE streaming."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.agents.nodes import evaluator, question_generator
from interview_coach.db import repos


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_prereqs(
    db_session: AsyncSession,
    auth_token: str,
    client: AsyncClient,
    *,
    skip: str | None = None,
) -> dict[str, Any]:
    """Seed user / job (analyzed) / profile / company snapshot. Returns IDs.

    `skip` lets a single test omit one prereq to assert the 400 path:
    ``"profile"``, ``"job_parsed"``, ``"snapshot"``.
    """
    me = await client.get("/auth/me", headers=_auth(auth_token))
    user_id = me.json()["id"]

    r = await client.post(
        "/jobs",
        headers=_auth(auth_token),
        json={"text": "Senior backend engineer at Acme."},
    )
    job_id = r.json()["id"]

    if skip != "job_parsed":
        await repos.update_job_parsed_json(
            db_session,
            __import__("uuid").UUID(job_id),
            __import__("uuid").UUID(user_id),
            {
                "title": "Senior Backend Engineer",
                "seniority": "senior",
                "must_have_skills": ["python"],
                "nice_to_have_skills": [],
                "responsibilities": [],
                "behavioral_signals": ["ownership"],
                "company_name": "Acme",
            },
        )

    if skip != "profile":
        await repos.upsert_profile(
            db_session,
            user_id=__import__("uuid").UUID(user_id),
            profile_json={
                "summary": "x",
                "skills": ["python"],
                "experiences": [],
                "projects": [],
                "education": [],
            },
            source_doc_ids=[],
            model_name="qwen3-8b",
        )

    if skip != "snapshot":
        await repos.upsert_company_snapshot(
            db_session,
            job_id=__import__("uuid").UUID(job_id),
            company_name="Acme",
            snapshot_json={
                "mission": "rockets",
                "products": [],
                "recent_news": [],
                "values_and_signals": [],
            },
            source_urls=[],
            model_name="qwen3-8b",
        )

    return {"user_id": user_id, "job_id": job_id}


# --- create / list / detail ---


async def test_create_session_happy_path(
    client: AsyncClient, auth_token: str, db_session: AsyncSession
) -> None:
    seeds = await _seed_prereqs(db_session, auth_token, client)
    r = await client.post(
        "/sessions",
        headers=_auth(auth_token),
        json={"job_id": seeds["job_id"], "round_type": "experience_deep_dive"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["round_type"] == "experience_deep_dive"
    assert body["status"] == "active"
    assert body["n_questions"] == 5


@pytest.mark.parametrize(
    ("skip", "expected_detail"),
    [
        ("profile", "profile_missing"),
        ("job_parsed", "job_not_analyzed"),
        ("snapshot", "company_snapshot_missing"),
    ],
)
async def test_create_session_missing_prereq(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    skip: str,
    expected_detail: str,
) -> None:
    seeds = await _seed_prereqs(db_session, auth_token, client, skip=skip)
    r = await client.post(
        "/sessions",
        headers=_auth(auth_token),
        json={"job_id": seeds["job_id"], "round_type": "experience_deep_dive"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == expected_detail


async def test_create_session_unknown_job(client: AsyncClient, auth_token: str) -> None:
    r = await client.post(
        "/sessions",
        headers=_auth(auth_token),
        json={
            "job_id": "00000000-0000-0000-0000-000000000000",
            "round_type": "experience_deep_dive",
        },
    )
    assert r.status_code == 404


async def test_list_and_detail(
    client: AsyncClient, auth_token: str, db_session: AsyncSession
) -> None:
    seeds = await _seed_prereqs(db_session, auth_token, client)
    created = await client.post(
        "/sessions",
        headers=_auth(auth_token),
        json={"job_id": seeds["job_id"], "round_type": "behavioral_star"},
    )
    sid = created.json()["id"]

    r = await client.get("/sessions", headers=_auth(auth_token))
    assert r.status_code == 200
    assert any(s["id"] == sid for s in r.json())

    r = await client.get(f"/sessions/{sid}", headers=_auth(auth_token))
    assert r.status_code == 200
    detail = r.json()
    assert detail["id"] == sid
    assert detail["threads"] == []


async def test_abandon(client: AsyncClient, auth_token: str, db_session: AsyncSession) -> None:
    seeds = await _seed_prereqs(db_session, auth_token, client)
    sid = (
        await client.post(
            "/sessions",
            headers=_auth(auth_token),
            json={"job_id": seeds["job_id"], "round_type": "experience_deep_dive"},
        )
    ).json()["id"]

    r = await client.post(f"/sessions/{sid}/abandon", headers=_auth(auth_token))
    assert r.status_code == 200
    assert r.json()["status"] == "abandoned"


async def test_abandon_does_not_downgrade_complete_session(
    client: AsyncClient, auth_token: str, db_session: AsyncSession
) -> None:
    seeds = await _seed_prereqs(db_session, auth_token, client)
    import uuid as _uuid

    sid_str = (
        await client.post(
            "/sessions",
            headers=_auth(auth_token),
            json={"job_id": seeds["job_id"], "round_type": "experience_deep_dive"},
        )
    ).json()["id"]
    await repos.update_session_status(
        db_session, _uuid.UUID(sid_str), _uuid.UUID(seeds["user_id"]), "complete"
    )

    r = await client.post(f"/sessions/{sid_str}/abandon", headers=_auth(auth_token))
    assert r.status_code == 200
    assert r.json()["status"] == "complete"


# --- preparation status ---


async def _seed_status_case(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    *,
    skip: str | None = None,
) -> dict[str, str]:
    import uuid as _uuid

    seeds = await _seed_user_and_job(client, auth_token, db_session, with_doc=skip != "cv")
    user_id = _uuid.UUID(seeds["user_id"])
    job_id = _uuid.UUID(seeds["job_id"])

    if skip != "profile":
        await repos.upsert_profile(
            db_session,
            user_id=user_id,
            profile_json={
                "summary": "Backend engineer focused on reliable APIs.",
                "skills": ["python", "fastapi"],
                "experiences": [],
                "projects": [],
                "education": [],
            },
            source_doc_ids=[],
            model_name="qwen3-8b",
        )
    if skip != "job_parsed":
        await repos.update_job_parsed_json(
            db_session,
            job_id,
            user_id,
            {
                "title": "Senior Backend Engineer",
                "seniority": "senior",
                "must_have_skills": ["python", "postgres"],
                "nice_to_have_skills": [],
                "responsibilities": ["Build reliable APIs."],
                "behavioral_signals": ["ownership"],
                "company_name": "Acme",
            },
        )
    if skip != "snapshot":
        await repos.upsert_company_snapshot(
            db_session,
            job_id=job_id,
            company_name="Acme",
            snapshot_json={
                "mission": "Build dependable tools.",
                "products": ["Workbench"],
                "recent_news": [],
                "values_and_signals": ["ownership"],
            },
            source_urls=["https://example.com"],
            model_name="qwen3-8b",
        )
    return seeds


@pytest.mark.parametrize(
    ("skip", "missing_key"),
    [
        ("profile", "profile"),
        ("job_parsed", "job_analysis"),
        ("snapshot", "company_research"),
    ],
)
async def test_prepare_status_reports_missing_artifact(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    skip: str,
    missing_key: str,
) -> None:
    seeds = await _seed_status_case(client, auth_token, db_session, skip=skip)

    r = await client.get(
        "/sessions/prepare/status",
        headers=_auth(auth_token),
        params={"job_id": seeds["job_id"]},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["can_start"] is False
    assert missing_key in body["missing"]


async def test_prepare_status_reports_missing_cv(
    client: AsyncClient, auth_token: str, db_session: AsyncSession
) -> None:
    seeds = await _seed_status_case(client, auth_token, db_session, skip="cv")

    r = await client.get(
        "/sessions/prepare/status",
        headers=_auth(auth_token),
        params={"job_id": seeds["job_id"]},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_cv"] is False
    assert "cv" in body["missing"]
    assert body["can_start"] is False


async def test_prepare_status_ready_includes_compact_artifacts(
    client: AsyncClient, auth_token: str, db_session: AsyncSession
) -> None:
    """Phase 21: detail payload is opt-in via ?detail=true so SetupPage's
    poll loop doesn't ship the full profile/job/company every 4 s.
    """
    seeds = await _seed_status_case(client, auth_token, db_session)

    # Default response drops the detail payload.
    r = await client.get(
        "/sessions/prepare/status",
        headers=_auth(auth_token),
        params={"job_id": seeds["job_id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["can_start"] is True
    assert body["missing"] == []
    assert body["profile"] is None
    assert body["job"] is None
    assert body["company"] is None

    # Opt-in detail includes the full payloads.
    r2 = await client.get(
        "/sessions/prepare/status",
        headers=_auth(auth_token),
        params={"job_id": seeds["job_id"], "detail": "true"},
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["profile"]["summary"] == "Backend engineer focused on reliable APIs."
    assert body2["job"]["title"] == "Senior Backend Engineer"
    assert body2["company"]["company_name"] == "Acme"
    assert body2["company"]["snapshot"]["mission"] == "Build dependable tools."


# --- streaming ---


def _patch_node_session_factory(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> None:
    """Point all agent layers' AsyncSessionLocal at the test's in-memory engine.

    The ``/message`` SSE route drives ``interview_graph`` whose nodes
    (``stream_open_thread`` / ``stream_conduct`` / ``stream_thread_evaluation``
    via ``graph_nodes``) open ``AsyncSessionLocal()`` directly (not via
    ``get_db``). FastAPI ``dependency_overrides`` from conftest don't reach them.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from interview_coach.agents import graph_nodes
    from interview_coach.agents.nodes import github_ingest

    bind = db_session.bind
    factory = async_sessionmaker(bind, expire_on_commit=False)
    monkeypatch.setattr(question_generator, "AsyncSessionLocal", factory)
    monkeypatch.setattr(evaluator, "AsyncSessionLocal", factory)
    monkeypatch.setattr(graph_nodes, "AsyncSessionLocal", factory)
    # Phase 32: the github segment opens its own AsyncSessionLocal; with the
    # test user carrying no github_handle it skips after one read.
    monkeypatch.setattr(github_ingest, "AsyncSessionLocal", factory)


def _patch_streaming_llm(
    monkeypatch: pytest.MonkeyPatch, deltas: list[str], *, target: Any = None
) -> None:
    """Patch ``chat_model`` on the given target (default: question_generator).

    Pass ``target=evaluator`` for the answer-route tests.
    """
    if target is None:
        target = question_generator

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

    monkeypatch.setattr(target, "chat_model", fake_chat_model)


async def _read_message_sse(
    client: AsyncClient,
    sid: str,
    token: str,
    *,
    body: dict[str, Any] | None = None,
) -> list[tuple[str, Any]]:
    """POST the candidate message (or empty body to open) and parse the SSE."""
    return await _read_sse_with_body(
        client, f"/sessions/{sid}/message", token, body if body is not None else {}
    )


async def _create_message_session(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    *,
    round_type: str = "experience_deep_dive",
    n_questions: int = 5,
) -> str:
    seeds = await _seed_prereqs(db_session, auth_token, client)
    return (
        await client.post(
            "/sessions",
            headers=_auth(auth_token),
            json={
                "job_id": seeds["job_id"],
                "round_type": round_type,
                "n_questions": n_questions,
            },
        )
    ).json()["id"]


async def test_message_opens_thread_and_streams_question(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty body opens the session: the interviewer opens the first thread,
    streams the root question, and the run pauses at the candidate gate."""
    sid = await _create_message_session(client, auth_token, db_session)
    _patch_node_session_factory(monkeypatch, db_session)
    _patch_streaming_llm(
        monkeypatch,
        [
            '{"question": "Tell me ',
            'about ownership.", "anchors": ["specifics", "outcome", "lesson"]}',
        ],
    )

    events = await _read_message_sse(client, sid, auth_token, body={})

    move = next(d for ev, d in events if ev == "move")
    assert move["kind"] == "question"
    assert move["thread_index"] == 0
    tokens = [d for ev, d in events if ev == "token"]
    assert "".join(tokens) == "Tell me about ownership."
    move_done = next(d for ev, d in events if ev == "move_done")
    assert move_done["anchors"] == ["specifics", "outcome", "lesson"]
    # The thread is still open — no evaluation yet.
    assert not any(ev == "score" for ev, _ in events)

    detail = (await client.get(f"/sessions/{sid}", headers=_auth(auth_token))).json()
    assert len(detail["threads"]) == 1
    thread = detail["threads"][0]
    assert thread["status"] == "open"
    assert thread["anchors_json"] == ["specifics", "outcome", "lesson"]
    assert len(thread["messages"]) == 1
    msg = thread["messages"][0]
    assert msg["role"] == "interviewer"
    assert msg["kind"] == "question"
    assert msg["text"] == "Tell me about ownership."


async def test_message_resume_conducts_probe(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Answering an open thread runs the conductor; a ``probe`` move streams
    back and the thread stays open (candidate answer + probe persisted)."""
    sid = await _create_message_session(client, auth_token, db_session)
    _patch_node_session_factory(monkeypatch, db_session)
    _patch_streaming_llm(monkeypatch, ['{"question": "Q1?", "anchors": ["a", "b", "c"]}'])
    await _read_message_sse(client, sid, auth_token, body={})

    # The candidate answers → the conductor probes deeper.
    _patch_streaming_llm(
        monkeypatch, ['{"action": "probe", "message": "What tradeoff did you make?"}']
    )
    events = await _read_message_sse(
        client, sid, auth_token, body={"message": "I rewrote the stack."}
    )

    move = next(d for ev, d in events if ev == "move")
    assert move["kind"] == "probe"
    tokens = [d for ev, d in events if ev == "token"]
    assert "".join(tokens) == "What tradeoff did you make?"
    assert not any(ev == "score" for ev, _ in events)

    detail = (await client.get(f"/sessions/{sid}", headers=_auth(auth_token))).json()
    msgs = detail["threads"][0]["messages"]
    assert [m["role"] for m in msgs] == ["interviewer", "candidate", "interviewer"]
    assert [m["kind"] for m in msgs] == ["question", None, "probe"]
    assert msgs[1]["text"] == "I rewrote the stack."
    assert msgs[2]["text"] == "What tradeoff did you make?"


async def test_message_resume_advances_evaluates_and_completes(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conductor ``advance`` closes the thread → evaluation streams; with
    n_questions=1 the session wraps complete."""
    sid = await _create_message_session(client, auth_token, db_session, n_questions=1)
    _patch_node_session_factory(monkeypatch, db_session)
    _patch_streaming_llm(monkeypatch, ['{"question": "Q1?", "anchors": ["a", "b", "c"]}'])
    await _read_message_sse(client, sid, auth_token, body={})

    # The candidate answers → conductor advances → thread-close evaluation runs.
    _patch_streaming_llm(
        monkeypatch, ['{"action": "advance", "message": ""}'], target=question_generator
    )
    _patch_streaming_llm(
        monkeypatch,
        ['{"score": 7, "feedback": "good ', 'work.", "model_answer": "When I led..."}'],
        target=evaluator,
    )
    events = await _read_message_sse(client, sid, auth_token, body={"message": "my answer"})

    assert any(ev == "evaluation" for ev, _ in events)
    score = next(d for ev, d in events if ev == "score")
    assert score == 7
    feedback_tokens = [d for ev, d in events if ev == "feedback_token"]
    assert "".join(feedback_tokens) == "good work."
    model_answer_tokens = [d for ev, d in events if ev == "model_answer_token"]
    assert "".join(model_answer_tokens) == "When I led..."
    eval_done = next(d for ev, d in events if ev == "evaluation_done")
    assert eval_done["session_status"] == "complete"
    assert eval_done["n_remaining"] == 0
    assert any(ev == "wrap" for ev, _ in events)

    detail = (await client.get(f"/sessions/{sid}", headers=_auth(auth_token))).json()
    assert detail["status"] == "complete"
    thread = detail["threads"][0]
    assert thread["status"] == "closed"
    assert thread["score"] == 7
    assert thread["feedback"] == "good work."
    assert thread["model_answer"] == "When I led..."


async def test_message_empty_on_paused_session_400(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once a thread is open and awaiting an answer, an empty message → 400."""
    sid = await _create_message_session(client, auth_token, db_session)
    _patch_node_session_factory(monkeypatch, db_session)
    _patch_streaming_llm(monkeypatch, ['{"question": "Q1?", "anchors": ["a", "b", "c"]}'])
    await _read_message_sse(client, sid, auth_token, body={})

    r = await client.post(
        f"/sessions/{sid}/message",
        headers=_auth(auth_token),
        json={"message": "   "},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "empty_message"


async def test_message_session_404(client: AsyncClient, auth_token: str) -> None:
    r = await client.post(
        "/sessions/00000000-0000-0000-0000-000000000000/message",
        headers=_auth(auth_token),
        json={},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "session_not_found"


async def test_message_on_inactive_session_409(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
) -> None:
    """A message to an abandoned (non-active) session → 409."""
    sid = await _create_message_session(client, auth_token, db_session)
    await client.post(f"/sessions/{sid}/abandon", headers=_auth(auth_token))

    r = await client.post(
        f"/sessions/{sid}/message",
        headers=_auth(auth_token),
        json={},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "session_status_abandoned"


# --- Phase 10: /sessions/prepare ---


async def _seed_user_and_job(
    client: AsyncClient, auth_token: str, db_session: AsyncSession, *, with_doc: bool = True
) -> dict[str, str]:
    me = await client.get("/auth/me", headers=_auth(auth_token))
    user_id = me.json()["id"]
    if with_doc:
        # The prep route insists on at least one doc for profile_builder.
        from interview_coach.db.models import Document

        doc = Document(
            user_id=__import__("uuid").UUID(user_id),
            kind="cv",
            filename="alice.pdf",
            content_type="application/pdf",
            byte_size=10,
            raw_text="Alice Engineer",
        )
        db_session.add(doc)
        await db_session.commit()
    r = await client.post(
        "/jobs",
        headers=_auth(auth_token),
        json={"text": "Senior backend engineer at Acme."},
    )
    return {"user_id": user_id, "job_id": r.json()["id"]}


async def test_prepare_runs_all_three_nodes(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty caches → 3× node_started + node_done + final done."""
    seeds = await _seed_user_and_job(client, auth_token, db_session)
    _patch_node_session_factory(monkeypatch, db_session)

    # Stub out the underlying agent functions so we don't hit LLM/Tavily.
    from interview_coach.agents import graph_nodes

    class _P:
        def model_dump(self) -> dict[str, Any]:
            return {
                "summary": "x",
                "skills": [],
                "experiences": [],
                "projects": [],
                "education": [],
            }

    class _A:
        def model_dump(self) -> dict[str, Any]:
            return {
                "title": "x",
                "seniority": "senior",
                "must_have_skills": [],
                "nice_to_have_skills": [],
                "responsibilities": [],
                "behavioral_signals": [],
                "company_name": "Acme",
            }

    class _S:
        def model_dump(self) -> dict[str, Any]:
            return {"mission": "x", "products": [], "recent_news": [], "values_and_signals": []}

    async def fbp(*_a: Any, **_k: Any) -> _P:
        return _P()

    async def faj(*_a: Any, **_k: Any) -> _A:
        return _A()

    async def frc(*_a: Any, **_k: Any) -> _S:
        return _S()

    monkeypatch.setattr(graph_nodes, "build_profile", fbp)
    monkeypatch.setattr(graph_nodes, "analyze_job", faj)
    monkeypatch.setattr(graph_nodes, "research_company", frc)

    events = await _read_sse_with_body(
        client, "/sessions/prepare", auth_token, body={"job_id": seeds["job_id"]}
    )

    started = [d for ev, d in events if ev == "node_started"]
    done = [d for ev, d in events if ev == "node_done"]
    assert [d["node"] for d in started] == [
        "profile_builder",
        "job_analyzer",
        "company_researcher",
    ]
    assert [d["node"] for d in done] == [
        "profile_builder",
        "job_analyzer",
        "company_researcher",
    ]
    final = next(d for ev, d in events if ev == "done")
    assert final == {"job_id": seeds["job_id"], "ready": True}


async def test_prepare_skips_when_all_cached(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-populated profile / parsed_json / snapshot → 3× node_skipped."""
    import uuid as _uuid

    seeds = await _seed_user_and_job(client, auth_token, db_session)
    _patch_node_session_factory(monkeypatch, db_session)
    user_id = _uuid.UUID(seeds["user_id"])
    job_id = _uuid.UUID(seeds["job_id"])

    # Cache the user's profile keyed off the actual doc list.
    docs = await repos.list_documents_for_user(db_session, user_id)
    await repos.upsert_profile(
        db_session,
        user_id=user_id,
        profile_json={
            "summary": "x",
            "skills": [],
            "experiences": [],
            "projects": [],
            "education": [],
        },
        source_doc_ids=[str(d.id) for d in docs],
        model_name="qwen3-8b",
    )
    await repos.update_job_parsed_json(
        db_session,
        job_id,
        user_id,
        {
            "title": "x",
            "seniority": "senior",
            "must_have_skills": [],
            "nice_to_have_skills": [],
            "responsibilities": [],
            "behavioral_signals": [],
            "company_name": "Acme",
        },
    )
    await repos.upsert_company_snapshot(
        db_session,
        job_id=job_id,
        company_name="Acme",
        snapshot_json={"mission": "x", "products": [], "recent_news": [], "values_and_signals": []},
        source_urls=[],
        model_name="qwen3-8b",
    )

    events = await _read_sse_with_body(
        client, "/sessions/prepare", auth_token, body={"job_id": seeds["job_id"]}
    )

    skipped = [d for ev, d in events if ev == "node_skipped"]
    # Phase 21.1: prepare_mapping_suggestion (emits node="doc_mapping")
    # short-circuits when the user has no unmapped project_docs (only a
    # CV was seeded here). Phase 32: the github segment skips too (the
    # seeded user has no github_handle and no ingested repos).
    assert [d["node"] for d in skipped] == [
        "profile_builder",
        "github",
        "doc_mapping",
        "job_analyzer",
        "company_researcher",
    ]
    assert any(ev == "done" for ev, _ in events)


async def test_prepare_404_unknown_job(
    client: AsyncClient, auth_token: str, db_session: AsyncSession
) -> None:
    r = await client.post(
        "/sessions/prepare",
        headers=_auth(auth_token),
        json={"job_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "job_not_found"


async def test_prepare_400_no_documents(
    client: AsyncClient, auth_token: str, db_session: AsyncSession
) -> None:
    seeds = await _seed_user_and_job(client, auth_token, db_session, with_doc=False)
    r = await client.post(
        "/sessions/prepare",
        headers=_auth(auth_token),
        json={"job_id": seeds["job_id"]},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "no_documents"


async def _read_sse_with_body(
    client: AsyncClient, url: str, token: str, body: dict[str, Any]
) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    async with client.stream("POST", url, headers=_auth(token), json=body) as r:
        assert r.status_code == 200, await r.aread()
        event = "message"
        async for line in r.aiter_lines():
            if line == "":
                event = "message"
                continue
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
                continue
            if line.startswith("data:"):
                payload = line[len("data:") :].strip()
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    data = payload
                events.append((event, data))
    return events
