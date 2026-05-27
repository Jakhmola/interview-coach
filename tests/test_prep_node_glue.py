"""Phase 30 (Part A) — the prep-node verdict→event glue has its own seam.

``emit_verdict`` (``agents/prep_events.py``) is the one piece of
verdict→lifecycle-event mapping shared by the three linear prep nodes.
These tests pin its two outcomes at the node boundary:

* cache hit → ``node_skipped`` with the verdict's skip reason + cached payload,
* cache miss → ``node_started`` (run reason) then ``node_done`` + fresh payload.

The company node's *degraded* path is its own contract — covered by
``test_phase22_company_research_degrade.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_coach.agents import graph_nodes
from interview_coach.agents.nodes.job_analyzer import JobNotFoundError
from interview_coach.db import models, repos


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


class _FakeWriter:
    """Captures the writer dicts the node emits for assertion."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)


class _FakeBuilt:
    """Stand-in for a Profile / JobAnalysis result — only ``model_dump``
    is touched by the node."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return self._payload


def _wire(monkeypatch: pytest.MonkeyPatch, db: async_sessionmaker) -> _FakeWriter:
    monkeypatch.setattr(graph_nodes, "AsyncSessionLocal", db)
    writer = _FakeWriter()
    monkeypatch.setattr(graph_nodes, "get_stream_writer", lambda: writer)
    return writer


# --- profile_builder --------------------------------------------------


async def test_profile_cache_hit_skips_and_returns_cached(
    db: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with db() as s:
        user = await repos.create_user(s, "alice@example.com", "x")
        cv = await repos.create_document(
            s,
            user_id=user.id,
            kind="cv",
            filename="cv.pdf",
            content_type="application/pdf",
            byte_size=1,
            raw_text="CV body.",
        )
        # Stored doc set == current Profile document set → "cached".
        doc_ids = await repos.current_profile_doc_ids(s, user.id)
        assert doc_ids == [str(cv.id)]
        await repos.upsert_profile(
            s,
            user_id=user.id,
            profile_json={"summary": "cached"},
            source_doc_ids=doc_ids,
            model_name="test",
        )

    writer = _wire(monkeypatch, db)
    # build_profile must never be called on a hit; trip a loud failure if it is.
    monkeypatch.setattr(graph_nodes, "build_profile", _unexpected_call("build_profile"))

    out = await graph_nodes.node_profile_builder({"user_id": str(user.id)})

    assert out == {"profile": {"summary": "cached"}}
    skipped = [e for e in writer.events if e["event"] == "node_skipped"]
    assert len(skipped) == 1
    assert skipped[0] == {"event": "node_skipped", "node": "profile_builder", "reason": "cached"}


async def test_profile_miss_runs_and_emits_started_then_done(
    db: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with db() as s:
        user = await repos.create_user(s, "alice@example.com", "x")

    writer = _wire(monkeypatch, db)

    async def fake_build_profile(_user_id: uuid.UUID) -> _FakeBuilt:
        return _FakeBuilt({"summary": "fresh"})

    monkeypatch.setattr(graph_nodes, "build_profile", fake_build_profile)

    out = await graph_nodes.node_profile_builder({"user_id": str(user.id)})

    assert out == {"profile": {"summary": "fresh"}}
    names = [e["event"] for e in writer.events]
    assert names == ["node_started", "node_done"]
    assert writer.events[0] == {
        "event": "node_started",
        "node": "profile_builder",
        "reason": "missing",
    }
    assert writer.events[1]["node"] == "profile_builder"
    assert writer.events[1]["outcome"] == "ok"


# --- job_analyzer -----------------------------------------------------


async def test_job_cache_hit_skips_and_returns_cached(
    db: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = {"title": "Eng", "must_have_skills": []}
    async with db() as s:
        user = await repos.create_user(s, "alice@example.com", "x")
        job = await repos.create_job(s, user_id=user.id, source="pasted", raw_text="JD.")
        await repos.update_job_parsed_json(s, job.id, user.id, parsed)

    writer = _wire(monkeypatch, db)
    monkeypatch.setattr(graph_nodes, "analyze_job", _unexpected_call("analyze_job"))

    out = await graph_nodes.node_job_analyzer({"user_id": str(user.id), "job_id": str(job.id)})

    assert out == {"job": parsed}
    skipped = [e for e in writer.events if e["event"] == "node_skipped"]
    assert skipped == [
        {"event": "node_skipped", "node": "job_analyzer", "reason": "already_analyzed"}
    ]


async def test_job_miss_runs_and_emits_started_then_done(
    db: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with db() as s:
        user = await repos.create_user(s, "alice@example.com", "x")
        job = await repos.create_job(s, user_id=user.id, source="pasted", raw_text="JD.")

    writer = _wire(monkeypatch, db)

    async def fake_analyze_job(_job_id: uuid.UUID, _user_id: uuid.UUID) -> _FakeBuilt:
        return _FakeBuilt({"title": "Eng"})

    monkeypatch.setattr(graph_nodes, "analyze_job", fake_analyze_job)

    out = await graph_nodes.node_job_analyzer({"user_id": str(user.id), "job_id": str(job.id)})

    assert out == {"job": {"title": "Eng"}}
    names = [e["event"] for e in writer.events]
    assert names == ["node_started", "node_done"]
    assert writer.events[0]["reason"] == "missing"


async def test_job_not_found_emits_error_and_raises(
    db: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with db() as s:
        user = await repos.create_user(s, "alice@example.com", "x")

    writer = _wire(monkeypatch, db)

    with pytest.raises(JobNotFoundError):
        await graph_nodes.node_job_analyzer({"user_id": str(user.id), "job_id": str(uuid.uuid4())})

    errors = [e for e in writer.events if e["event"] == "error"]
    assert errors == [{"event": "error", "node": "job_analyzer", "code": "job_not_found"}]


def _unexpected_call(name: str) -> Any:
    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(f"{name} should not run on a cache hit")

    return _boom
