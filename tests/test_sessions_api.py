"""API tests for /sessions including SSE streaming."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.agents.nodes import question_generator
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


# --- streaming ---


def _patch_node_session_factory(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> None:
    """Point question_generator.AsyncSessionLocal at the test's in-memory engine.

    The SSE route passes through `stream_question`, which calls
    `AsyncSessionLocal()` directly (not via `get_db`), so the FastAPI
    `dependency_overrides` we set up in conftest don't reach it.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    bind = db_session.bind
    factory = async_sessionmaker(bind, expire_on_commit=False)
    monkeypatch.setattr(question_generator, "AsyncSessionLocal", factory)


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

    monkeypatch.setattr(question_generator, "chat_model", fake_chat_model)


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
