"""Phase 26 — prep cache verdicts.

Three layers of coverage:

* **Pure** — ``decide_profile_cache`` / ``decide_job_cache`` /
  ``decide_company_cache`` / ``is_degraded_snapshot`` are pure decisions;
  test them directly with no DB or LLM in the way.
* **Repo** — ``current_profile_doc_ids`` is the single Profile-document-set
  formula; pin it against in-memory sqlite.
* **Integration** — a transiently-degraded company snapshot re-attempts
  research on the next prep (the OD-1 self-heal); a structurally-degraded
  one (``CompanyNameMissing``) does not.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_coach.agents import graph_nodes
from interview_coach.agents.prep_cache import (
    SkipVerdict,
    decide_company_cache,
    decide_job_cache,
    decide_profile_cache,
    is_degraded_snapshot,
)
from interview_coach.db import models, repos
from interview_coach.db.models import User

# --- pure: decide_profile_cache --------------------------------------


def test_profile_missing_profile_is_miss() -> None:
    v = decide_profile_cache(profile_exists=False, stored_doc_ids=None, current_doc_ids=["a"])
    assert v == SkipVerdict.miss("missing")


def test_profile_equal_sets_is_hit() -> None:
    v = decide_profile_cache(
        profile_exists=True, stored_doc_ids=["a", "b"], current_doc_ids=["a", "b"]
    )
    assert v == SkipVerdict.hit("cached")


def test_profile_differing_sets_is_stale() -> None:
    v = decide_profile_cache(profile_exists=True, stored_doc_ids=["a"], current_doc_ids=["a", "b"])
    assert v == SkipVerdict.miss("stale")


def test_profile_cache_is_order_and_dup_insensitive() -> None:
    v = decide_profile_cache(
        profile_exists=True,
        stored_doc_ids=["b", "a", "a"],
        current_doc_ids=["a", "b"],
    )
    assert v == SkipVerdict.hit("cached")


def test_profile_empty_both_is_hit() -> None:
    v = decide_profile_cache(profile_exists=True, stored_doc_ids=None, current_doc_ids=[])
    assert v == SkipVerdict.hit("cached")


# --- pure: decide_job_cache ------------------------------------------


def test_job_parsed_present_is_hit() -> None:
    assert decide_job_cache(parsed_json={"title": "Eng"}) == SkipVerdict.hit("already_analyzed")


def test_job_empty_is_miss() -> None:
    assert decide_job_cache(parsed_json=None) == SkipVerdict.miss("missing")
    assert decide_job_cache(parsed_json={}) == SkipVerdict.miss("missing")


# --- pure: decide_company_cache + is_degraded_snapshot ---------------


def test_company_force_refresh_is_miss() -> None:
    assert decide_company_cache(snapshot_json={"mission": "x"}, force_refresh=True) == (
        SkipVerdict.miss("forced")
    )


def test_company_none_is_miss() -> None:
    assert decide_company_cache(snapshot_json=None, force_refresh=False) == (
        SkipVerdict.miss("missing")
    )


def test_company_clean_snapshot_is_hit() -> None:
    assert decide_company_cache(snapshot_json={"mission": "x"}, force_refresh=False) == (
        SkipVerdict.hit("cached")
    )


@pytest.mark.parametrize("reason", ["NoSearchHits", "NoUsablePages"])
def test_company_transient_degraded_is_miss(reason: str) -> None:
    """OD-1: transient soft-fails re-attempt research on the next prep."""
    snap = {"mission": "", "_degraded": reason}
    assert decide_company_cache(snapshot_json=snap, force_refresh=False) == (
        SkipVerdict.miss("degraded")
    )


def test_company_structural_degraded_stays_hit() -> None:
    """OD-1: ``CompanyNameMissing`` is structural — re-running can't help,
    so it stays a cache hit until the user re-analyzes the JD."""
    snap = {"mission": "", "_degraded": "CompanyNameMissing"}
    assert decide_company_cache(snapshot_json=snap, force_refresh=False) == (
        SkipVerdict.hit("cached")
    )


def test_is_degraded_snapshot() -> None:
    assert is_degraded_snapshot({"_degraded": "NoSearchHits"}) is True
    assert is_degraded_snapshot({"mission": "x"}) is False
    assert is_degraded_snapshot(None) is False


# --- repo: current_profile_doc_ids -----------------------------------


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


@pytest.fixture
async def alice(db: async_sessionmaker) -> User:
    async with db() as s:
        return await repos.create_user(s, "alice@example.com", "x")


async def _make_doc(db: async_sessionmaker, user: User, *, kind: str, name: str) -> Any:
    async with db() as s:
        return await repos.create_document(
            s,
            user_id=user.id,
            kind=kind,
            filename=name,
            content_type="text/plain",
            byte_size=10,
            raw_text="body",
        )


async def test_current_profile_doc_ids_is_cv_union_confirmed_mappings(
    db: async_sessionmaker, alice: User
) -> None:
    cv = await _make_doc(db, alice, kind="cv", name="cv.pdf")
    proj = await _make_doc(db, alice, kind="project_doc", name="proj.md")

    # Unmapped project_doc does NOT contribute to the Profile document set.
    async with db() as s:
        assert await repos.current_profile_doc_ids(s, alice.id) == [str(cv.id)]

    # Confirm the mapping → the project_doc now contributes.
    async with db() as s:
        await repos.replace_document_mappings(
            s,
            document_id=proj.id,
            user_id=alice.id,
            rows=[
                {
                    "mapping_kind": "project",
                    "experience_idx": None,
                    "highlight_idx": None,
                    "project_idx": None,
                    "extracted_json": {},
                }
            ],
        )
    async with db() as s:
        assert await repos.current_profile_doc_ids(s, alice.id) == sorted(
            [str(cv.id), str(proj.id)]
        )


# --- integration: degraded company snapshot self-heal ----------------


class _Writer:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)


async def _seed_degraded_company(
    db: async_sessionmaker, *, user: User, degraded_reason: str
) -> uuid.UUID:
    async with db() as s:
        job = await repos.create_job(s, user_id=user.id, source="pasted", raw_text="JD body")
        await repos.update_job_parsed_json(
            s, job.id, user.id, {"title": "Eng", "company_name": "Acme"}
        )
        await repos.upsert_company_snapshot(
            s,
            job_id=job.id,
            company_name="Acme",
            snapshot_json={"mission": "", "_degraded": degraded_reason},
            source_urls=[],
            model_name="placeholder",
        )
    return job.id


async def _run_company_node(
    *,
    db: async_sessionmaker,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[bool], list[dict[str, Any]]]:
    """Run ``node_company_researcher`` against the in-memory db. Returns
    (research_calls, writer_events). A non-empty ``research_calls`` means
    the node re-attempted research instead of serving the placeholder."""
    from interview_coach.agents.schemas import CompanySnapshot

    writer = _Writer()
    research_calls: list[bool] = []

    async def fake_research(*_a: Any, force_refresh: bool = False, **_kw: Any) -> CompanySnapshot:
        research_calls.append(force_refresh)
        return CompanySnapshot(mission="fresh", products=[], recent_news=[], values_and_signals=[])

    monkeypatch.setattr(graph_nodes, "AsyncSessionLocal", db)
    monkeypatch.setattr(graph_nodes, "get_stream_writer", lambda: writer)
    monkeypatch.setattr(graph_nodes, "research_company", fake_research)

    await graph_nodes.node_company_researcher(
        {"user_id": str(user_id), "job_id": str(job_id), "force_refresh": False}  # type: ignore[arg-type]
    )
    return research_calls, writer.events


async def test_transient_degraded_company_reattempts_research(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = await _seed_degraded_company(db, user=alice, degraded_reason="NoSearchHits")
    research_calls, events = await _run_company_node(
        db=db, user_id=alice.id, job_id=job_id, monkeypatch=monkeypatch
    )
    assert research_calls == [False], "transient degraded snapshot should self-heal"
    assert not any(e.get("event") == "node_skipped" for e in events), events


async def test_structural_degraded_company_does_not_reattempt(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = await _seed_degraded_company(db, user=alice, degraded_reason="CompanyNameMissing")
    research_calls, events = await _run_company_node(
        db=db, user_id=alice.id, job_id=job_id, monkeypatch=monkeypatch
    )
    assert research_calls == [], "structural degraded snapshot must stay a cache hit"
    skipped = [e for e in events if e.get("event") == "node_skipped"]
    assert skipped and skipped[0]["reason"] == "cached", events
