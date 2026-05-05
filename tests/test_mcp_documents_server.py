"""Unit tests for the documents_server MCP tool functions.

These call the tool functions directly (FastMCP keeps the underlying coroutine
callable). The MCP transport itself is exercised in scripts/mcp_smoke.py.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from interview_coach.db import models, repos
from interview_coach.db.models import User
from interview_coach.mcp.servers import documents_server


@pytest.fixture
async def mcp_session(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    """Test session that the MCP tools will use via the patched factory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(documents_server, "AsyncSessionLocal", factory)

    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def user(mcp_session: AsyncSession) -> User:
    return await repos.create_user(mcp_session, "alice@example.com", "fake-hash")


async def test_list_documents_empty(mcp_session: AsyncSession, user: User) -> None:
    result = await documents_server.list_documents(str(user.id))
    assert result == []


async def test_list_documents_metadata_only(mcp_session: AsyncSession, user: User) -> None:
    await repos.create_document(
        mcp_session,
        user_id=user.id,
        kind="cv",
        filename="alice_cv.pdf",
        content_type="application/pdf",
        byte_size=1024,
        raw_text="Alice Engineer",
    )

    result = await documents_server.list_documents(str(user.id))
    assert len(result) == 1
    item = result[0]
    assert item["filename"] == "alice_cv.pdf"
    assert item["kind"] == "cv"
    assert item["char_count"] == len("Alice Engineer")
    assert "raw_text" not in item


async def test_get_document_returns_full(mcp_session: AsyncSession, user: User) -> None:
    doc = await repos.create_document(
        mcp_session,
        user_id=user.id,
        kind="cv",
        filename="cv.pdf",
        content_type="application/pdf",
        byte_size=10,
        raw_text="hello world",
    )
    result = await documents_server.get_document(str(doc.id), str(user.id))
    assert result is not None
    assert result["raw_text"] == "hello world"
    assert result["filename"] == "cv.pdf"


async def test_get_document_isolation(mcp_session: AsyncSession, user: User) -> None:
    other = await repos.create_user(mcp_session, "bob@example.com", "fake-hash")
    doc = await repos.create_document(
        mcp_session,
        user_id=user.id,
        kind="cv",
        filename="cv.pdf",
        content_type="application/pdf",
        byte_size=10,
        raw_text="alice's data",
    )
    result = await documents_server.get_document(str(doc.id), str(other.id))
    assert result is None


async def test_list_jobs(mcp_session: AsyncSession, user: User) -> None:
    await repos.create_job(
        mcp_session,
        user_id=user.id,
        source="pasted",
        raw_text="We need a backend engineer.",
    )
    result = await documents_server.list_jobs(str(user.id))
    assert len(result) == 1
    assert result[0]["source"] == "pasted"
    assert result[0]["preview"].startswith("We need")
    assert "raw_text" not in result[0]


async def test_get_job_returns_full(mcp_session: AsyncSession, user: User) -> None:
    job = await repos.create_job(
        mcp_session,
        user_id=user.id,
        source="url",
        raw_text="full job text",
        source_url="https://example.com/jd",
    )
    result = await documents_server.get_job(str(job.id), str(user.id))
    assert result is not None
    assert result["raw_text"] == "full job text"
    assert result["source_url"] == "https://example.com/jd"


async def test_get_job_isolation(mcp_session: AsyncSession, user: User) -> None:
    other = await repos.create_user(mcp_session, "bob@example.com", "fake-hash")
    job = await repos.create_job(
        mcp_session,
        user_id=user.id,
        source="pasted",
        raw_text="alice's jd",
    )
    result = await documents_server.get_job(str(job.id), str(other.id))
    assert result is None


async def test_fetch_url_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    async def fake_fetch(url: str, key: str | None) -> str:
        captured["url"] = url
        captured["key"] = key
        return "fetched body"

    monkeypatch.setattr(documents_server, "fetch_url_text", fake_fetch)
    monkeypatch.setattr(documents_server.settings, "tavily_api_key", "test-key")

    result = await documents_server.fetch_url("https://example.com/jd")
    assert result == "fetched body"
    assert captured == {"url": "https://example.com/jd", "key": "test-key"}
