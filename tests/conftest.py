import io
import os
from collections.abc import AsyncIterator

import pytest
from docx import Document as DocxDocument
from httpx import ASGITransport, AsyncClient
from reportlab.pdfgen import canvas
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from interview_coach.api.main import app
from interview_coach.db import models
from interview_coach.db.session import get_db


@pytest.fixture(autouse=True)
def _scrub_langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never pick up a real Langfuse key from the developer's `.env`.
    Any test that wants tracing enabled must monkeypatch the vars explicitly.

    Bypassed when INTEGRATION=1 — those runs are explicitly opt-in and want
    the real external surface, so a developer with Langfuse configured can
    confirm trace wiring against their dashboard.
    """
    if os.environ.get("INTEGRATION") == "1":
        return
    for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(k, raising=False)
    # Reset the lazy-init flag so a previous test that enabled Langfuse
    # doesn't leak its cached client into the next test.
    from interview_coach.observability import langfuse as obs

    monkeypatch.setattr(obs, "_client_initialized", False)


@pytest.fixture(autouse=True)
def _scrub_tavily_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must not pick up a real Tavily key from the developer's `.env`.
    Any test that wants a key must monkeypatch it explicitly.

    Bypassed when INTEGRATION=1 — those tests are explicitly opt-in and want
    the real external services (Tavily, llama-server) wired up.
    """
    if os.environ.get("INTEGRATION") == "1":
        return
    from interview_coach.config import settings

    monkeypatch.setattr(settings, "tavily_api_key", None)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # Phase 10: routes read compiled graphs off `app.state`. The FastAPI
    # lifespan (which builds them in production) doesn't run under
    # ASGITransport, so we install in-memory equivalents here.
    from langgraph.checkpoint.memory import MemorySaver

    from interview_coach.agents.graph import build_interview_graph, build_prep_graph

    # Phase 21: prep_graph is now checkpointed. Share the same MemorySaver
    # across both graphs (matches production's shared AsyncSqliteSaver).
    checkpointer = MemorySaver()
    # Phase 22: also expose the bare saver — DELETE /jobs/{id} cleans
    # the per-job prep checkpoint via ``adelete_thread`` and reads it
    # off ``app.state.checkpointer``.
    app.state.checkpointer = checkpointer
    app.state.prep_graph = build_prep_graph(checkpointer)
    app.state.interview_graph = build_interview_graph(checkpointer)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# --- Document fixture builders ---


def make_pdf(text: str = "Hello, world.") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def make_docx(text: str = "Hello, world.") -> bytes:
    doc = DocxDocument()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def sample_pdf() -> bytes:
    return make_pdf("Alice Engineer\nSenior Software Engineer\nPython, Postgres, FastAPI.")


@pytest.fixture
def sample_docx() -> bytes:
    return make_docx("Project: Interview Coach\nBuilt a multi-agent interview practice tool.")


# --- Auth helper ---


@pytest.fixture
async def auth_token(client: AsyncClient) -> str:
    r = await client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "hunter22a"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


@pytest.fixture
async def second_user_token(client: AsyncClient) -> str:
    r = await client.post(
        "/auth/register",
        json={"email": "bob@example.com", "password": "hunter22a"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]
