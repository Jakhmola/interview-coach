"""ProfileBuilder unit tests with a mocked LLM (Phase 14.1: CV-only)."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from interview_coach.agents.nodes import profile_builder
from interview_coach.agents.schemas import (
    Education,
    Experience,
    Highlight,
    Profile,
    ProjectItem,
)
from interview_coach.db import models, repos
from interview_coach.db.models import User


@pytest.fixture
async def agent_session(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(profile_builder, "AsyncSessionLocal", factory)

    async with factory() as s:
        yield s

    await engine.dispose()


@pytest.fixture
async def alice(agent_session: AsyncSession) -> User:
    return await repos.create_user(agent_session, "alice@example.com", "x")


def _fake_profile() -> Profile:
    return Profile(
        summary="Backend engineer with 6 years of Python.",
        skills=["python", "fastapi", "postgres"],
        experiences=[
            Experience(
                company="Acme",
                role="Senior Engineer",
                start="2021",
                end="present",
                highlights=[Highlight(text="Built async API")],
            )
        ],
        projects=[
            ProjectItem(name="Migrate to async", description="Rewrote sync stack", tech=["asyncio"])
        ],
        education=[Education(school="State", degree="BS CS", start="2014", end="2018")],
    )


async def _seed_cv(session: AsyncSession, user: User) -> models.Document:
    return await repos.create_document(
        session,
        user_id=user.id,
        kind="cv",
        filename="alice.pdf",
        content_type="application/pdf",
        byte_size=42,
        raw_text="Alice Engineer ... 6 years of Python ...",
    )


async def test_build_profile_persists(
    agent_session: AsyncSession,
    alice: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cv = await _seed_cv(agent_session, alice)

    async def fake_chat_model_structured(_schema, _messages, *, temperature, **_overrides):
        return _fake_profile()

    monkeypatch.setattr(profile_builder, "chat_model_structured", fake_chat_model_structured)

    result = await profile_builder.build_profile(alice.id)

    assert isinstance(result, Profile)
    assert result.skills == ["python", "fastapi", "postgres"]
    # Highlight is now a structured object.
    assert result.experiences[0].highlights[0].text == "Built async API"

    row = await repos.get_profile(agent_session, alice.id)
    assert row is not None
    assert row.profile_json["skills"] == ["python", "fastapi", "postgres"]
    assert row.source_doc_ids == [str(cv.id)]


async def test_build_profile_replaces_existing(
    agent_session: AsyncSession,
    alice: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rebuilding for the same user replaces (does not duplicate)."""
    await _seed_cv(agent_session, alice)

    async def fake_chat_model_structured(_schema, _messages, *, temperature, **_overrides):
        return _fake_profile()

    monkeypatch.setattr(profile_builder, "chat_model_structured", fake_chat_model_structured)

    await profile_builder.build_profile(alice.id)
    await profile_builder.build_profile(alice.id)

    rows = (
        (
            await agent_session.execute(
                __import__("sqlalchemy")
                .select(models.ProfileRow)
                .where(models.ProfileRow.user_id == alice.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_build_profile_no_cv(
    agent_session: AsyncSession,
    alice: User,
) -> None:
    with pytest.raises(profile_builder.NoDocumentsError):
        await profile_builder.build_profile(alice.id)
