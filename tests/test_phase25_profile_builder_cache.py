"""Phase 25 (B2) — profile_builder cache key correctness.

The cache check in ``node_profile_builder`` used to compare against the
user's *full* document list. That flips to a miss the instant a
project_doc lands on disk, *before* its mapping is applied — forcing a
full LLM re-extract on every project_doc upload.

The fix narrows the cache key to the same shape ``build_profile``
writes into ``source_doc_ids``: the CV plus every project_doc whose
mapping has been confirmed. These tests pin that behavior across the
four interesting cycles:

* CV-only → second prep is a cache hit.
* CV + unmapped project_doc → still a cache hit (the bug).
* CV + mapped project_doc → cache hit (was working, regression guard).
* CV replaced → cache miss (was working, regression guard).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_coach.agents import graph_nodes
from interview_coach.agents.nodes import doc_intake, profile_builder
from interview_coach.db import models, repos
from interview_coach.db.models import Document, User


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


async def _make_cv(db: async_sessionmaker, user: User) -> Document:
    async with db() as s:
        return await repos.create_document(
            s,
            user_id=user.id,
            kind="cv",
            filename="resume.pdf",
            content_type="application/pdf",
            byte_size=10,
            raw_text="Alice resume body.",
        )


async def _make_project_doc(db: async_sessionmaker, user: User, *, title: str) -> Document:
    async with db() as s:
        return await repos.create_document(
            s,
            user_id=user.id,
            kind="project_doc",
            filename=f"{title}.md",
            content_type="text/markdown",
            byte_size=20,
            raw_text=f"Project {title} body.",
        )


def _simple_profile() -> dict[str, Any]:
    return {
        "summary": "x",
        "skills": [],
        "experiences": [
            {
                "company": "Acme",
                "role": "Eng",
                "start": None,
                "end": None,
                "highlights": [
                    {
                        "text": "Did a thing",
                        "tech_stack": [],
                        "description": None,
                        "urls": [],
                        "source_document_ids": [],
                    }
                ],
            }
        ],
        "projects": [],
    }


async def _noop(*_a: Any, **_k: Any) -> None:
    return None


class _Writer:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)


async def _run_profile_builder(
    *, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch, db: async_sessionmaker
) -> list[dict[str, Any]]:
    """Invoke ``node_profile_builder`` against the in-memory db and return
    the events its writer received. The LLM-driven ``build_profile`` is
    stubbed — any call to it indicates a cache miss."""
    writer = _Writer()
    monkeypatch.setattr(graph_nodes, "AsyncSessionLocal", db)
    monkeypatch.setattr(graph_nodes, "get_stream_writer", lambda: writer)

    async def fake_build_profile(_uid: uuid.UUID):
        writer({"event": "_BUILD_CALLED"})
        # Return a minimal Profile so the node can serialize a result.
        from interview_coach.agents.schemas import Profile

        return Profile.model_validate(_simple_profile())

    monkeypatch.setattr(graph_nodes, "build_profile", fake_build_profile)
    await graph_nodes.node_profile_builder({"user_id": str(user_id)})  # type: ignore[arg-type]
    return writer.events


async def _seed_profile(
    db: async_sessionmaker, *, user_id: uuid.UUID, source_doc_ids: list[uuid.UUID]
) -> None:
    async with db() as s:
        await repos.upsert_profile(
            s,
            user_id=user_id,
            profile_json=_simple_profile(),
            source_doc_ids=sorted(str(x) for x in source_doc_ids),
            model_name="qwen3-8b",
        )


async def test_cv_only_second_prep_is_cache_hit(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = await _make_cv(db, alice)
    await _seed_profile(db, user_id=alice.id, source_doc_ids=[cv.id])

    events = await _run_profile_builder(user_id=alice.id, monkeypatch=monkeypatch, db=db)
    assert any(e.get("event") == "node_skipped" for e in events), events
    assert not any(e.get("event") == "_BUILD_CALLED" for e in events), events


async def test_unmapped_project_doc_still_cache_hits(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug: uploading a project_doc (mapping not yet applied) used to
    force profile_builder to re-LLM. After the fix, the cache should
    still hit because unmapped project_docs aren't part of the cache
    key."""
    cv = await _make_cv(db, alice)
    await _make_project_doc(db, alice, title="A")
    await _seed_profile(db, user_id=alice.id, source_doc_ids=[cv.id])

    events = await _run_profile_builder(user_id=alice.id, monkeypatch=monkeypatch, db=db)
    assert any(e.get("event") == "node_skipped" for e in events), events
    assert not any(e.get("event") == "_BUILD_CALLED" for e in events), events


async def test_mapped_project_doc_cache_hits(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv = await _make_cv(db, alice)
    proj = await _make_project_doc(db, alice, title="A")
    # Persist a mapping row so the project_doc is now in the cache key.
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
    await _seed_profile(db, user_id=alice.id, source_doc_ids=[cv.id, proj.id])

    events = await _run_profile_builder(user_id=alice.id, monkeypatch=monkeypatch, db=db)
    assert any(e.get("event") == "node_skipped" for e in events), events


async def test_cv_replace_misses_cache(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacing the CV must invalidate — the new CV has a new id."""
    old_cv = await _make_cv(db, alice)
    await _seed_profile(db, user_id=alice.id, source_doc_ids=[old_cv.id])

    # Replace CV (``create_document`` for kind='cv' deletes the prior row).
    new_cv = await _make_cv(db, alice)
    assert new_cv.id != old_cv.id

    # Need the LLM stub for the miss path (build_profile would normally run).
    monkeypatch.setattr(profile_builder, "AsyncSessionLocal", db)
    monkeypatch.setattr(doc_intake, "AsyncSessionLocal", db)
    monkeypatch.setattr(doc_intake, "embed_and_store_document", _noop)

    events = await _run_profile_builder(user_id=alice.id, monkeypatch=monkeypatch, db=db)
    assert any(e.get("event") == "_BUILD_CALLED" for e in events), events
