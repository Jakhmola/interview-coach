"""Phase 22 — company research is best-effort, not a setup blocker.

If the JD has no extractable company name (or Tavily comes up empty),
``node_company_researcher`` no longer terminates the prep_graph stream
with an error. Instead it persists a placeholder snapshot, emits a
``node_done`` with ``degraded: true``, and lets the user proceed to
interview. The placeholder embeds the degrade reason in the
``snapshot_json`` so Manage can surface a "company info incomplete"
state later.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_coach.agents import graph_nodes
from interview_coach.agents.nodes import company_researcher as researcher_mod
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


async def _seed_job(
    db: async_sessionmaker, *, company_name: str | None
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a user + JD with the given (possibly empty) analyzed company_name."""
    async with db() as s:
        user = await repos.create_user(s, "alice@example.com", "x")
        job = await repos.create_job(
            s,
            user_id=user.id,
            source="pasted",
            raw_text="JD body.",
        )
        parsed: dict[str, Any] = {
            "title": "Eng",
            "seniority": "senior",
            "must_have_skills": [],
            "nice_to_have_skills": [],
            "responsibilities": [],
            "behavioral_signals": [],
            "company_name": company_name,
        }
        await repos.update_job_parsed_json(s, job.id, user.id, parsed)
    return user.id, job.id


class _FakeWriter:
    """Captures the writer dicts the node emits for assertion."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)


async def _run_node(
    monkeypatch: pytest.MonkeyPatch,
    db: async_sessionmaker,
    *,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    raises: Exception,
) -> _FakeWriter:
    """Run ``node_company_researcher`` with ``research_company`` stubbed
    to raise ``raises``. Returns the captured writer events."""
    # Point both graph_nodes and the company_researcher node at the
    # in-memory db. graph_nodes opens its own AsyncSessionLocal for
    # the placeholder upsert; researcher_mod is the function the node
    # imports as ``research_company``.
    monkeypatch.setattr(graph_nodes, "AsyncSessionLocal", db)

    async def fake_research_company(*_args: Any, **_kwargs: Any) -> Any:
        raise raises

    monkeypatch.setattr(graph_nodes, "research_company", fake_research_company)

    fake_writer = _FakeWriter()
    monkeypatch.setattr(graph_nodes, "get_stream_writer", lambda: fake_writer)

    out = await graph_nodes.node_company_researcher(
        {
            "user_id": str(user_id),
            "job_id": str(job_id),
            "force_refresh": False,
        }
    )
    # Sanity: graph reports prep_done so /prepare/status can flip can_start.
    assert out["prep_done"] is True
    assert out["next_step"] == "END"
    return fake_writer


async def test_company_name_missing_degrades_softly(
    db: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, job_id = await _seed_job(db, company_name=None)
    writer = await _run_node(
        monkeypatch,
        db,
        user_id=user_id,
        job_id=job_id,
        raises=researcher_mod.CompanyNameMissing("no name"),
    )

    # No fatal error event; instead a node_done with degraded=True.
    assert not any(e["event"] == "error" for e in writer.events)
    done = [e for e in writer.events if e["event"] == "node_done"]
    assert len(done) == 1
    assert done[0]["degraded"] is True
    assert done[0]["code"] == "CompanyNameMissing"

    # A placeholder snapshot was persisted so prepare_status flips
    # company_researched=True and the user can proceed.
    async with db() as s:
        snap = await repos.get_company_snapshot_by_job(s, job_id)
    assert snap is not None
    assert snap.company_name == "Unknown company"  # no analyzed name to use
    assert snap.snapshot_json.get("_degraded") == "CompanyNameMissing"
    assert snap.snapshot_json.get("mission") == ""


async def test_no_search_hits_degrades_softly(
    db: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, job_id = await _seed_job(db, company_name="Acme Co")
    writer = await _run_node(
        monkeypatch,
        db,
        user_id=user_id,
        job_id=job_id,
        raises=researcher_mod.NoSearchHits("zero results"),
    )

    assert not any(e["event"] == "error" for e in writer.events)
    done = next(e for e in writer.events if e["event"] == "node_done")
    assert done["degraded"] is True
    assert done["code"] == "NoSearchHits"

    # Analyzed company_name carries through onto the placeholder row.
    async with db() as s:
        snap = await repos.get_company_snapshot_by_job(s, job_id)
    assert snap is not None
    assert snap.company_name == "Acme Co"
    assert snap.snapshot_json.get("_degraded") == "NoSearchHits"


async def test_no_usable_pages_degrades_softly(
    db: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, job_id = await _seed_job(db, company_name="Beta Inc")
    writer = await _run_node(
        monkeypatch,
        db,
        user_id=user_id,
        job_id=job_id,
        raises=researcher_mod.NoUsablePages("all pages failed"),
    )

    done = next(e for e in writer.events if e["event"] == "node_done")
    assert done["degraded"] is True
    assert done["code"] == "NoUsablePages"

    async with db() as s:
        snap = await repos.get_company_snapshot_by_job(s, job_id)
    assert snap is not None
    assert snap.snapshot_json.get("_degraded") == "NoUsablePages"


async def test_job_not_analyzed_still_fatal(
    db: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``JobNotAnalyzed`` indicates an upstream pipeline bug — the job
    analyzer didn't run before company research. That's not a content
    issue we can degrade past; surface it loudly so it doesn't get
    silently swallowed."""
    user_id, job_id = await _seed_job(db, company_name="X")

    monkeypatch.setattr(graph_nodes, "AsyncSessionLocal", db)

    async def fake_research(*_args: Any, **_kwargs: Any) -> Any:
        raise researcher_mod.JobNotAnalyzed("missing parsed_json")

    monkeypatch.setattr(graph_nodes, "research_company", fake_research)

    fake_writer = _FakeWriter()
    monkeypatch.setattr(graph_nodes, "get_stream_writer", lambda: fake_writer)

    with pytest.raises(researcher_mod.JobNotAnalyzed):
        await graph_nodes.node_company_researcher(
            {
                "user_id": str(user_id),
                "job_id": str(job_id),
                "force_refresh": False,
            }
        )

    # An error event WAS emitted (the node tells the route layer)
    # before propagating.
    assert any(e["event"] == "error" for e in fake_writer.events)
    # No placeholder was persisted — fatal means fatal.
    async with db() as s:
        snap = await repos.get_company_snapshot_by_job(s, job_id)
    assert snap is None
