"""Unit tests for the documents_server MCP tool functions.

These call the tool functions directly (FastMCP keeps the underlying coroutine
callable). The MCP transport itself is exercised in scripts/mcp_smoke.py.

Phase 16: only `get_job` and `search_grounding` remain. `list_documents`,
`get_document`, `list_jobs`, and `fetch_url` were removed — the web
extract path now lives in the `web` MCP server.
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
