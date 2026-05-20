"""Phase 25 (B11) — embedding_status honors last_embed_attempt_at.

Repro: doc upload fails to embed → after 60s grace, status reports
``failed``. User clicks retry-embed; status should immediately flip
back to ``pending`` so the UI doesn't look broken. The fix tracks the
last attempt timestamp on the doc row and treats a recent attempt as
``pending`` regardless of the doc's own age.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from interview_coach.api.documents.routes import _embedding_status_for
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


async def _make_old_cv(db: async_sessionmaker, user: User) -> Document:
    """CV doc created ~10 minutes ago so it's past the grace window."""
    async with db() as s:
        doc = await repos.create_document(
            s,
            user_id=user.id,
            kind="cv",
            filename="resume.pdf",
            content_type="application/pdf",
            byte_size=10,
            raw_text="Alice resume body.",
        )
        # Force created_at into the past so the age-based fallback fires.
        await s.execute(
            Document.__table__.update()
            .where(Document.id == doc.id)
            .values(created_at=datetime.now(UTC) - timedelta(minutes=10))
        )
        await s.commit()
        await s.refresh(doc)
        return doc


async def test_old_doc_with_no_chunks_reports_failed(
    db: async_sessionmaker, alice: User
) -> None:
    doc = await _make_old_cv(db, alice)
    async with db() as s:
        status = await _embedding_status_for(doc, s)
    assert status == "failed"


async def test_recent_retry_attempt_flips_status_to_pending(
    db: async_sessionmaker, alice: User
) -> None:
    """The retry-embed path stamps ``last_embed_attempt_at = now()`` and
    we should immediately see ``pending`` instead of the stale ``failed``."""
    doc = await _make_old_cv(db, alice)
    async with db() as s:
        await repos.mark_embed_attempt(s, doc.id)
    async with db() as s:
        fresh = await repos.get_document(s, doc.id, alice.id)
        assert fresh is not None
        status = await _embedding_status_for(fresh, s)
    assert status == "pending"


async def test_old_retry_attempt_still_failed(
    db: async_sessionmaker, alice: User
) -> None:
    """An attempt timestamp from outside the grace window doesn't
    paper over a real failure — we go back to ``failed``."""
    doc = await _make_old_cv(db, alice)
    async with db() as s:
        await s.execute(
            Document.__table__.update()
            .where(Document.id == doc.id)
            .values(last_embed_attempt_at=datetime.now(UTC) - timedelta(minutes=10))
        )
        await s.commit()
    async with db() as s:
        fresh = await repos.get_document(s, doc.id, alice.id)
        assert fresh is not None
        status = await _embedding_status_for(fresh, s)
    assert status == "failed"


async def test_chunks_present_always_ready(
    db: async_sessionmaker, alice: User
) -> None:
    """Existence of chunks short-circuits — last_embed_attempt_at doesn't
    matter once we've succeeded once."""
    doc = await _make_old_cv(db, alice)
    async with db() as s:
        s.add(
            models.GroundingChunk(
                user_id=alice.id,
                document_id=doc.id,
                source_doc_kind="cv",
                chunk_index=0,
                text="chunk",
                n_tokens=1,
                embedding=[0.0] * 1024,
                model_name="test",
            )
        )
        await s.commit()
        status = await _embedding_status_for(doc, s)
    assert status == "ready"


async def test_project_doc_unmapped_still_n_a_even_with_attempt(
    db: async_sessionmaker, alice: User
) -> None:
    """Mapping-derived n_a wins over a stray embed attempt — unmapped
    project_docs never embed until apply_mapping runs."""
    async with db() as s:
        doc = await repos.create_document(
            s,
            user_id=alice.id,
            kind="project_doc",
            filename="p.md",
            content_type="text/markdown",
            byte_size=10,
            raw_text="x",
        )
    async with db() as s:
        await repos.mark_embed_attempt(s, doc.id)
    async with db() as s:
        fresh = await repos.get_document(s, doc.id, alice.id)
        assert fresh is not None
        status = await _embedding_status_for(fresh, s)
    assert status == "n_a"


async def test_repos_mark_embed_attempt_sets_timestamp(
    db: async_sessionmaker, alice: User
) -> None:
    async with db() as s:
        doc = await repos.create_document(
            s,
            user_id=alice.id,
            kind="cv",
            filename="r.pdf",
            content_type="application/pdf",
            byte_size=1,
            raw_text="r",
        )
        assert doc.last_embed_attempt_at is None
    async with db() as s:
        await repos.mark_embed_attempt(s, doc.id)
    async with db() as s:
        fresh = await repos.get_document(s, doc.id, alice.id)
        assert fresh is not None
        assert fresh.last_embed_attempt_at is not None
        ts: Any = fresh.last_embed_attempt_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        assert (datetime.now(UTC) - ts).total_seconds() < 5
