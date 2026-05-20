"""Phase 25 (B3) — apply_mapping awaits embed_and_store_document.

Before Phase 25, ``apply_mapping`` fired embedding as
``asyncio.create_task(...)`` and returned. The prep_graph's
``mapping_applied`` event then fired *before* chunks landed in
``grounding_chunks``, so the user could start practicing and pull a
model-answer grounded on an empty / stale chunk set for the doc they
just confirmed.

This test pins the new contract: ``apply_mapping`` does not return
until ``embed_and_store_document`` has completed.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_coach.agents.nodes import doc_intake
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


async def _make_project_doc(db: async_sessionmaker, user: User) -> Document:
    async with db() as s:
        return await repos.create_document(
            s,
            user_id=user.id,
            kind="project_doc",
            filename="proj.md",
            content_type="text/markdown",
            byte_size=20,
            raw_text="Project body.",
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


async def test_apply_mapping_awaits_embed(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If apply_mapping returns before embed finishes, the
    embed-finished sentinel won't have flipped yet. Force the embed
    stub to defer past a yielded scheduling point; if apply_mapping
    fails to await, the assertion below catches it."""
    monkeypatch.setattr(doc_intake, "AsyncSessionLocal", db)

    cv = await _make_cv(db, alice)
    proj = await _make_project_doc(db, alice)
    async with db() as s:
        await repos.upsert_profile(
            s,
            user_id=alice.id,
            profile_json=_simple_profile(),
            source_doc_ids=[str(cv.id)],
            model_name="qwen3-8b",
        )

    embed_finished = asyncio.Event()
    embed_observed_doc: list[uuid.UUID] = []

    async def slow_embed(doc_id: uuid.UUID) -> int:
        # Yield once so the test scheduler can race; then set the flag.
        await asyncio.sleep(0)
        embed_observed_doc.append(doc_id)
        embed_finished.set()
        return 1

    monkeypatch.setattr(doc_intake, "embed_and_store_document", slow_embed)

    await doc_intake.apply_mapping(
        document_id=proj.id,
        user_id=alice.id,
        rows=[{"mapping_kind": "highlight", "experience_idx": 0, "highlight_idx": 0}],
        extracted={"tech_stack": ["python"], "description": "did stuff", "urls": []},
        project_title="Proj",
    )

    # The contract: by the time apply_mapping returns, embed has
    # completed for this doc. Fire-and-forget (old behavior) would fail
    # this — the event would still be unset.
    assert embed_finished.is_set(), "apply_mapping returned before embed completed"
    assert embed_observed_doc == [proj.id]


async def test_apply_mapping_swallows_embed_failure(
    db: async_sessionmaker, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flaky embedder must not roll back the mapping rows — the user's
    HITL decision is preserved and they can retry via /documents/{id}/embed."""
    monkeypatch.setattr(doc_intake, "AsyncSessionLocal", db)

    cv = await _make_cv(db, alice)
    proj = await _make_project_doc(db, alice)
    async with db() as s:
        await repos.upsert_profile(
            s,
            user_id=alice.id,
            profile_json=_simple_profile(),
            source_doc_ids=[str(cv.id)],
            model_name="qwen3-8b",
        )

    async def boom(_doc_id: uuid.UUID) -> int:
        raise RuntimeError("embedder is on fire")

    monkeypatch.setattr(doc_intake, "embed_and_store_document", boom)

    n = await doc_intake.apply_mapping(
        document_id=proj.id,
        user_id=alice.id,
        rows=[{"mapping_kind": "highlight", "experience_idx": 0, "highlight_idx": 0}],
        extracted={"tech_stack": ["python"], "description": "did stuff", "urls": []},
        project_title="Proj",
    )
    assert n == 1

    async with db() as s:
        rows = await repos.list_document_mappings(s, proj.id)
    assert len(rows) == 1
