"""ProfileBuilder unit tests with mocked MCP loader and mocked LLM."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from interview_coach.agents.nodes import profile_builder
from interview_coach.agents.schemas import (
    Education,
    Experience,
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
                highlights=["Built async API"],
            )
        ],
        projects=[
            ProjectItem(name="Migrate to async", description="Rewrote sync stack", tech=["asyncio"])
        ],
        education=[Education(school="State", degree="BS CS", start="2014", end="2018")],
    )


async def test_build_profile_persists(
    agent_session: AsyncSession,
    alice: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_docs = [
        {
            "id": "doc-1",
            "kind": "cv",
            "filename": "alice.pdf",
            "raw_text": "Alice Engineer ... 6 years of Python ...",
        }
    ]

    async def fake_loader(uid: str) -> list[dict]:
        assert uid == str(alice.id)
        return fake_docs

    monkeypatch.setattr(profile_builder, "_load_user_docs", fake_loader)

    fake_llm = AsyncMock()
    fake_llm.ainvoke = AsyncMock(return_value=_fake_profile())

    def fake_chat_model(*, temperature: float = 0.0):
        m = AsyncMock()
        m.with_structured_output = lambda _schema, **_kwargs: fake_llm
        return m

    monkeypatch.setattr(profile_builder, "chat_model", fake_chat_model)

    result = await profile_builder.build_profile(alice.id)

    assert isinstance(result, Profile)
    assert result.skills == ["python", "fastapi", "postgres"]

    # Assert persisted
    row = await repos.get_profile(agent_session, alice.id)
    assert row is not None
    assert row.profile_json["skills"] == ["python", "fastapi", "postgres"]
    assert row.source_doc_ids == ["doc-1"]


async def test_build_profile_replaces_existing(
    agent_session: AsyncSession,
    alice: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rebuilding for the same user replaces (does not duplicate)."""

    monkeypatch.setattr(
        profile_builder,
        "_load_user_docs",
        AsyncMock(return_value=[{"id": "d1", "kind": "cv", "filename": "a.pdf", "raw_text": "x"}]),
    )

    fake_llm = AsyncMock()
    fake_llm.ainvoke = AsyncMock(return_value=_fake_profile())

    def fake_chat_model(**_: object):
        m = AsyncMock()
        m.with_structured_output = lambda _schema, **_kwargs: fake_llm
        return m

    monkeypatch.setattr(profile_builder, "chat_model", fake_chat_model)

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


async def test_build_profile_no_docs(
    agent_session: AsyncSession,
    alice: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(profile_builder, "_load_user_docs", AsyncMock(return_value=[]))
    with pytest.raises(profile_builder.NoDocumentsError):
        await profile_builder.build_profile(alice.id)


def test_format_docs_truncates_long_text() -> None:
    big = "x" * (profile_builder.MAX_DOC_CHARS + 100)
    out = profile_builder._format_docs([{"kind": "cv", "filename": "huge.pdf", "raw_text": big}])
    assert "[truncated]" in out
    assert len(out) < len(big) + 200
