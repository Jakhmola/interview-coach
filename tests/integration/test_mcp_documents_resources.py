"""End-to-end Resources test for the documents MCP server.

Spawns the real stdio subprocess via `MultiServerMCPClient`, lists resource
templates and reads a project_doc URI. Uses an in-memory SQLite DB shared
between the subprocess and the test by overriding `DATABASE_URL` to a
file-backed SQLite path in a tmpdir.

Skipped unless `INTEGRATION=1` is set — the real subprocess + DB take a
few seconds and we want `make test` to stay fast.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

if os.environ.get("INTEGRATION") != "1":
    pytest.skip(
        "Set INTEGRATION=1 to run; spawns a real stdio MCP subprocess.",
        allow_module_level=True,
    )


@pytest.fixture
async def seeded_db(tmp_path: Path) -> AsyncIterator[tuple[str, uuid.UUID, uuid.UUID]]:
    """Create a file-backed SQLite DB with one user + one project_doc.
    Returns (database_url, user_id, doc_id)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from interview_coach.db import models, repos

    db_file = tmp_path / "spike.sqlite"
    url = f"sqlite+aiosqlite:///{db_file}"

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        user = await repos.create_user(s, "alice@example.com", "hash")
        doc = await repos.create_document(
            s,
            user_id=user.id,
            kind="project_doc",
            filename="proj.md",
            content_type="text/markdown",
            byte_size=11,
            raw_text="project body",
        )
    await engine.dispose()
    yield url, user.id, doc.id


async def test_resource_read_returns_doc_text(
    seeded_db: tuple[str, uuid.UUID, uuid.UUID],
) -> None:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    url, user_id, doc_id = seeded_db
    env = dict(os.environ)
    env["DATABASE_URL"] = url  # NB: app config reads asyncpg by default — override

    client = MultiServerMCPClient(
        {
            "documents": {
                "command": sys.executable,
                "args": ["-m", "interview_coach.mcp.servers.documents_server"],
                "transport": "stdio",
                "env": env,
            }
        }
    )

    async with client.session("documents") as session:
        templates = await session.list_resource_templates()
        uris = [t.uriTemplate for t in templates.resourceTemplates]
        assert any("project_doc://" in u for u in uris)

        result = await session.read_resource(
            f"project_doc://{user_id}/{doc_id}",
        )
        bodies = [c.text for c in result.contents if hasattr(c, "text")]
        assert "project body" in "".join(bodies)
