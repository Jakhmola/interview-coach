"""JobAnalyzer unit tests with direct repo read (Phase 21: MCP removed)
and mocked LLM."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from interview_coach.agents.nodes import job_analyzer
from interview_coach.agents.schemas import JobAnalysis, Seniority
from interview_coach.db import models, repos
from interview_coach.db.models import Job, User


@pytest.fixture
async def agent_session(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(job_analyzer, "AsyncSessionLocal", factory)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def alice(agent_session: AsyncSession) -> User:
    return await repos.create_user(agent_session, "alice@example.com", "x")


@pytest.fixture
async def alice_job(agent_session: AsyncSession, alice: User) -> Job:
    return await repos.create_job(
        agent_session,
        user_id=alice.id,
        source="pasted",
        raw_text="We are hiring a senior backend engineer with FastAPI experience.",
    )


def _fake_analysis() -> JobAnalysis:
    return JobAnalysis(
        title="Senior Backend Engineer",
        seniority=Seniority.senior,
        must_have_skills=["fastapi", "python"],
        nice_to_have_skills=["kubernetes"],
        responsibilities=["Design and own backend services."],
        behavioral_signals=["ownership"],
        company_name="Acme",
    )


async def test_analyze_job_persists_into_parsed_json(
    agent_session: AsyncSession,
    alice: User,
    alice_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_chat_model_structured(_schema, _messages, *, temperature, **_overrides):
        return _fake_analysis()

    monkeypatch.setattr(job_analyzer, "chat_model_structured", fake_chat_model_structured)

    result = await job_analyzer.analyze_job(alice_job.id, alice.id)

    assert isinstance(result, JobAnalysis)
    assert result.must_have_skills == ["fastapi", "python"]

    # Read in a fresh session to avoid the fixture session's identity-map cache.
    factory = job_analyzer.AsyncSessionLocal
    async with factory() as fresh:
        job = await repos.get_job(fresh, alice_job.id, alice.id)
    assert job is not None
    assert job.parsed_json is not None
    assert job.parsed_json["must_have_skills"] == ["fastapi", "python"]
    assert job.parsed_json["seniority"] == "senior"


async def test_analyze_job_not_found(
    agent_session: AsyncSession,
    alice: User,
) -> None:
    import uuid as _uuid

    # No row in DB for this id ⇒ direct repos.get_job returns None ⇒
    # analyze_job raises JobNotFoundError.
    with pytest.raises(job_analyzer.JobNotFoundError):
        await job_analyzer.analyze_job(_uuid.uuid4(), alice.id)
