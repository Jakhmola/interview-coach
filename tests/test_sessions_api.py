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
        json={"job_id": seeds["job_id"], "round_type": "resume_walkthrough"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["round_type"] == "resume_walkthrough"
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
        json={"job_id": seeds["job_id"], "round_type": "resume_walkthrough"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == expected_detail


async def test_create_session_unknown_job(client: AsyncClient, auth_token: str) -> None:
    r = await client.post(
        "/sessions",
        headers=_auth(auth_token),
        json={
            "job_id": "00000000-0000-0000-0000-000000000000",
            "round_type": "resume_walkthrough",
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
    assert detail["turns"] == []


async def test_abandon(client: AsyncClient, auth_token: str, db_session: AsyncSession) -> None:
    seeds = await _seed_prereqs(db_session, auth_token, client)
    sid = (
        await client.post(
            "/sessions",
            headers=_auth(auth_token),
            json={"job_id": seeds["job_id"], "round_type": "resume_walkthrough"},
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
            json={"job_id": seeds["job_id"], "round_type": "resume_walkthrough"},
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

    The SSE routes pass through `stream_question` / `stream_evaluation` and
    Phase 10's `graph_nodes`, all of which open `AsyncSessionLocal()`
    directly (not via `get_db`). FastAPI `dependency_overrides` from
    conftest don't reach them.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from interview_coach.agents import graph_nodes

    bind = db_session.bind
    factory = async_sessionmaker(bind, expire_on_commit=False)
    monkeypatch.setattr(question_generator, "AsyncSessionLocal", factory)
    monkeypatch.setattr(evaluator, "AsyncSessionLocal", factory)
    monkeypatch.setattr(graph_nodes, "AsyncSessionLocal", factory)


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


async def _read_sse(client: AsyncClient, url: str, token: str) -> list[tuple[str, Any]]:
    """Drive the SSE response and parse it into (event, data) tuples."""
    events: list[tuple[str, Any]] = []
    async with client.stream("POST", url, headers=_auth(token)) as r:
        assert r.status_code == 200, await r.aread()
        assert r.headers["content-type"].startswith("text/event-stream")
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


async def test_next_question_streams_and_persists(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeds = await _seed_prereqs(db_session, auth_token, client)
    sid = (
        await client.post(
            "/sessions",
            headers=_auth(auth_token),
            json={"job_id": seeds["job_id"], "round_type": "resume_walkthrough"},
        )
    ).json()["id"]

    _patch_node_session_factory(monkeypatch, db_session)
    _patch_streaming_llm(
        monkeypatch,
        [
            '{"question": "Tell me ',
            'about ownership.", "anchors": ["specifics", "outcome", "lesson"]}',
        ],
    )

    events = await _read_sse(client, f"/sessions/{sid}/next_question", auth_token)
    tokens = [d for ev, d in events if ev == "token"]
    assert "".join(tokens) == "Tell me about ownership."
    done = next(d for ev, d in events if ev == "done")
    assert "question_id" in done
    assert done["turn_index"] == 0

    detail = (await client.get(f"/sessions/{sid}", headers=_auth(auth_token))).json()
    assert len(detail["turns"]) == 1
    assert detail["turns"][0]["question"] == "Tell me about ownership."
    assert detail["turns"][0]["anchors_json"] == ["specifics", "outcome", "lesson"]


async def test_next_question_locks_until_answered(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeds = await _seed_prereqs(db_session, auth_token, client)
    sid = (
        await client.post(
            "/sessions",
            headers=_auth(auth_token),
            json={"job_id": seeds["job_id"], "round_type": "resume_walkthrough"},
        )
    ).json()["id"]

    _patch_node_session_factory(monkeypatch, db_session)
    _patch_streaming_llm(monkeypatch, ['{"question": "Q", "anchors": ["a", "b", "c"]}'])
    await _read_sse(client, f"/sessions/{sid}/next_question", auth_token)

    # Second call before the answer arrives → 409.
    r = await client.post(
        f"/sessions/{sid}/next_question",
        headers={**_auth(auth_token), "Accept": "text/event-stream"},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "previous_turn_unanswered"


async def test_next_question_404(client: AsyncClient, auth_token: str) -> None:
    r = await client.post(
        "/sessions/00000000-0000-0000-0000-000000000000/next_question",
        headers=_auth(auth_token),
    )
    assert r.status_code == 404


async def test_next_question_session_complete(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When n_questions=1 and that turn is answered, next_question → 409."""
    import uuid as _uuid

    seeds = await _seed_prereqs(db_session, auth_token, client)
    sid = (
        await client.post(
            "/sessions",
            headers=_auth(auth_token),
            json={
                "job_id": seeds["job_id"],
                "round_type": "resume_walkthrough",
                "n_questions": 1,
            },
        )
    ).json()["id"]

    # Seed an already-answered turn directly.
    await repos.create_turn(
        db_session,
        session_id=_uuid.UUID(sid),
        turn_index=0,
        question="Q",
        anchors=["a"],
    )
    from sqlalchemy import update

    from interview_coach.db.models import TurnRow

    await db_session.execute(
        update(TurnRow).where(TurnRow.session_id == _uuid.UUID(sid)).values(answer="my answer")
    )
    await db_session.commit()

    r = await client.post(
        f"/sessions/{sid}/next_question",
        headers=_auth(auth_token),
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "session_complete"


# --- Phase 9: answer / evaluator ---


async def _start_session_with_one_unanswered_turn(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    *,
    n_questions: int = 3,
) -> str:
    """Helper: seed prereqs, create session, stream one question. Returns
    the session id. The latest turn has a question but no answer."""
    seeds = await _seed_prereqs(db_session, auth_token, client)
    sid = (
        await client.post(
            "/sessions",
            headers=_auth(auth_token),
            json={
                "job_id": seeds["job_id"],
                "round_type": "resume_walkthrough",
                "n_questions": n_questions,
            },
        )
    ).json()["id"]
    _patch_node_session_factory(monkeypatch, db_session)
    _patch_streaming_llm(
        monkeypatch,
        ['{"question": "Walk me through your last project.", "anchors": ["a", "b", "c"]}'],
        target=question_generator,
    )
    await _read_sse(client, f"/sessions/{sid}/next_question", auth_token)
    return sid


async def test_answer_streams_evaluation_and_persists(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = await _start_session_with_one_unanswered_turn(client, auth_token, db_session, monkeypatch)
    _patch_streaming_llm(
        monkeypatch,
        [
            '{"score": 7, "feedback": "Strong on tradeoffs',
            ' but missed metrics.", "model_answer": "When I led..."}',
        ],
        target=evaluator,
    )

    events: list[tuple[str, Any]] = []
    async with client.stream(
        "POST",
        f"/sessions/{sid}/answer",
        headers=_auth(auth_token),
        json={"answer": "I'd start by clarifying requirements."},
    ) as r:
        assert r.status_code == 200, await r.aread()
        assert r.headers["content-type"].startswith("text/event-stream")
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

    score_events = [d for ev, d in events if ev == "score"]
    feedback_tokens = [d for ev, d in events if ev == "feedback_token"]
    model_answer_tokens = [d for ev, d in events if ev == "model_answer_token"]
    done = next(d for ev, d in events if ev == "done")

    assert len(score_events) == 1
    assert score_events[0]["score"] == 7
    assert "".join(feedback_tokens) == "Strong on tradeoffs but missed metrics."
    assert "".join(model_answer_tokens) == "When I led..."
    assert done["session_status"] == "active"
    assert done["n_remaining"] == 2

    # Persistence check.
    detail = (await client.get(f"/sessions/{sid}", headers=_auth(auth_token))).json()
    turn = detail["turns"][0]
    assert turn["answer"] == "I'd start by clarifying requirements."
    assert turn["score"] == 7
    assert turn["feedback"] == "Strong on tradeoffs but missed metrics."
    assert turn["model_answer"] == "When I led..."


async def test_answer_completes_session_on_last_turn(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1-question session — answering it flips status to complete."""
    sid = await _start_session_with_one_unanswered_turn(
        client, auth_token, db_session, monkeypatch, n_questions=1
    )
    _patch_streaming_llm(
        monkeypatch,
        ['{"score": 5, "feedback": "ok", "model_answer": "x"}'],
        target=evaluator,
    )

    events: list[tuple[str, Any]] = []
    async with client.stream(
        "POST",
        f"/sessions/{sid}/answer",
        headers=_auth(auth_token),
        json={"answer": "my answer"},
    ) as r:
        assert r.status_code == 200
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

    done = next(d for ev, d in events if ev == "done")
    assert done["session_status"] == "complete"
    assert done["n_remaining"] == 0

    detail = (await client.get(f"/sessions/{sid}", headers=_auth(auth_token))).json()
    assert detail["status"] == "complete"


async def test_answer_empty_400(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = await _start_session_with_one_unanswered_turn(client, auth_token, db_session, monkeypatch)
    r = await client.post(
        f"/sessions/{sid}/answer",
        headers=_auth(auth_token),
        json={"answer": "   "},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "empty_answer"


async def test_answer_no_active_turn_409(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Submitting before any question has been generated → 409."""
    seeds = await _seed_prereqs(db_session, auth_token, client)
    sid = (
        await client.post(
            "/sessions",
            headers=_auth(auth_token),
            json={"job_id": seeds["job_id"], "round_type": "resume_walkthrough"},
        )
    ).json()["id"]

    r = await client.post(
        f"/sessions/{sid}/answer",
        headers=_auth(auth_token),
        json={"answer": "hi"},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "no_active_turn"


async def test_answer_already_evaluated_409(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-submitting on an already-evaluated turn → 409."""
    sid = await _start_session_with_one_unanswered_turn(client, auth_token, db_session, monkeypatch)
    _patch_streaming_llm(
        monkeypatch,
        ['{"score": 6, "feedback": "ok", "model_answer": "x"}'],
        target=evaluator,
    )
    # First submit succeeds.
    async with client.stream(
        "POST",
        f"/sessions/{sid}/answer",
        headers=_auth(auth_token),
        json={"answer": "first"},
    ) as r:
        assert r.status_code == 200
        async for _line in r.aiter_lines():
            pass

    # Second submit on same turn rejected.
    r = await client.post(
        f"/sessions/{sid}/answer",
        headers=_auth(auth_token),
        json={"answer": "second"},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "turn_already_evaluated"


async def test_answer_session_404(client: AsyncClient, auth_token: str) -> None:
    r = await client.post(
        "/sessions/00000000-0000-0000-0000-000000000000/answer",
        headers=_auth(auth_token),
        json={"answer": "hi"},
    )
    assert r.status_code == 404


async def test_answer_resume_after_partial_evaluation(
    client: AsyncClient,
    auth_token: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idempotent submit when the answer was saved but eval didn't finish.

    Simulates a client reload mid-stream — the turn has answer != None and
    score == None. A retry should re-run evaluation, not 409.
    """
    sid = await _start_session_with_one_unanswered_turn(client, auth_token, db_session, monkeypatch)
    # Manually save an answer without evaluation.
    detail = (await client.get(f"/sessions/{sid}", headers=_auth(auth_token))).json()
    turn_id = detail["turns"][0]["id"]
    import uuid as _uuid

    await repos.update_turn_answer(db_session, _uuid.UUID(turn_id), "saved earlier")

    _patch_streaming_llm(
        monkeypatch,
        ['{"score": 8, "feedback": "ok", "model_answer": "x"}'],
        target=evaluator,
    )

    async with client.stream(
        "POST",
        f"/sessions/{sid}/answer",
        headers=_auth(auth_token),
        json={"answer": "ignored — server uses saved one"},
    ) as r:
        assert r.status_code == 200
        async for _line in r.aiter_lines():
            pass

    detail = (await client.get(f"/sessions/{sid}", headers=_auth(auth_token))).json()
    turn = detail["turns"][0]
    assert turn["answer"] == "saved earlier"
    assert turn["score"] == 8


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
    # CV was seeded here).
    assert [d["node"] for d in skipped] == [
        "profile_builder",
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
