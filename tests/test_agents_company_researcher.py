"""CompanyResearcher unit tests with mocked Tavily and LLM.

Phase 21: MCP removed. ``_patch_loader`` is now a compat shim that
writes ``parsed_json`` directly to the per-test SQLite row, since
``company_researcher`` reads through ``repos.get_job``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from interview_coach.agents.nodes import company_researcher
from interview_coach.agents.schemas import CompanySnapshot
from interview_coach.db import models, repos
from interview_coach.db.models import Job, User
from interview_coach.ingestion.errors import KeyMissing
from interview_coach.ingestion.web import SearchResult


@pytest.fixture
async def agent_session(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(company_researcher, "AsyncSessionLocal", factory)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def alice(agent_session: AsyncSession) -> User:
    return await repos.create_user(agent_session, "alice@example.com", "x")


@pytest.fixture
async def analyzed_job(agent_session: AsyncSession, alice: User) -> Job:
    job = await repos.create_job(
        agent_session,
        user_id=alice.id,
        source="pasted",
        raw_text="JD body for Acme.",
    )
    await repos.update_job_parsed_json(
        agent_session,
        job.id,
        alice.id,
        {
            "title": "Senior Backend Engineer",
            "seniority": "senior",
            "must_have_skills": ["python"],
            "nice_to_have_skills": [],
            "responsibilities": [],
            "behavioral_signals": [],
            "company_name": "Acme",
        },
    )
    return job


def _fake_snapshot() -> CompanySnapshot:
    return CompanySnapshot(
        mission="Acme builds rockets.",
        products=["rockets", "boosters"],
        recent_news=["Launched Falcon-9 successor."],
        values_and_signals=["high autonomy", "engineering-led"],
    )


def _patch_llm(monkeypatch: pytest.MonkeyPatch, snapshot: CompanySnapshot) -> AsyncMock:
    """Wires `chat_model().with_structured_output(...)` to return `snapshot`."""
    fake_llm = AsyncMock()
    fake_llm.ainvoke = AsyncMock(return_value=snapshot)

    def fake_chat_model(**_: object) -> Any:
        m = AsyncMock()
        m.with_structured_output = lambda _schema, **_kwargs: fake_llm
        return m

    monkeypatch.setattr(company_researcher, "chat_model", fake_chat_model)
    return fake_llm


def _patch_settings_key(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    """Patch `settings.tavily_api_key` as seen from inside the node module."""
    monkeypatch.setattr(company_researcher.settings, "tavily_api_key", value)


async def _patch_loader(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any] | None = None,
    *,
    exc: Exception | None = None,
) -> None:
    """Phase 21 compat shim — async because we now write straight to the
    per-test SQLite session (company_researcher reads via repos.get_job).

    ``payload`` carries ``{"job_id", "user_id", "parsed"}``. ``exc=...``
    is now a no-op — callers that want JobNotAnalyzed should pass a
    fresh UUID that doesn't exist in the DB.
    """
    if payload is None:
        return
    factory = company_researcher.AsyncSessionLocal
    async with factory() as s:
        # repos.update_job_parsed_json normally expects a non-empty dict.
        # An empty dict here keeps ``parsed_json`` falsy on the row, which
        # is what the "not analyzed" branch in research_company checks.
        await repos.update_job_parsed_json(
            s, payload["job_id"], payload["user_id"], payload.get("parsed") or {}
        )


def _job_payload(job: Job, *, parsed: dict[str, Any] | None) -> dict[str, Any]:
    """Identifiers the Phase-21 ``_patch_loader`` writes back to the DB."""
    return {
        "job_id": job.id,
        "user_id": job.user_id,
        "parsed": parsed,
    }


async def test_research_company_happy_path(
    agent_session: AsyncSession,
    alice: User,
    analyzed_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings_key(monkeypatch, "fake-key")
    await _patch_loader(
        monkeypatch,
        _job_payload(
            analyzed_job,
            parsed={"company_name": "Acme", "title": "Senior Backend Engineer"},
        ),
    )

    search_calls: list[str] = []

    async def fake_search(
        query: str, _key: str | None, *, max_results: int = 5
    ) -> list[SearchResult]:
        search_calls.append(query)
        if "news" in query:
            return [
                SearchResult(
                    url="https://news.example/acme", title="Acme news", content="", score=0.7
                )
            ]
        return [
            SearchResult(url="https://acme.example", title="Acme home", content="", score=0.95),
            SearchResult(url="https://acme.example", title="Dup", content="", score=0.95),
            SearchResult(url="https://wiki.example/acme", title="Wiki", content="", score=0.3),
        ]

    fetched: list[str] = []

    async def fake_fetch(url: str, _key: str | None) -> str:
        fetched.append(url)
        return f"page content for {url}"

    monkeypatch.setattr(company_researcher, "tavily_search", fake_search)
    monkeypatch.setattr(company_researcher, "fetch_url_text", fake_fetch)

    fake_llm = _patch_llm(monkeypatch, _fake_snapshot())

    result = await company_researcher.research_company(analyzed_job.id, alice.id)

    assert isinstance(result, CompanySnapshot)
    assert result.mission == "Acme builds rockets."
    assert search_calls == ["Acme company overview", "Acme recent news"]
    # Top 2 unique URLs by score: acme.example (0.95) + news.example/acme (0.7).
    assert fetched == ["https://acme.example", "https://news.example/acme"]
    fake_llm.ainvoke.assert_awaited_once()

    factory = company_researcher.AsyncSessionLocal
    async with factory() as fresh:
        row = await repos.get_company_snapshot_by_job(fresh, analyzed_job.id)
    assert row is not None
    assert row.company_name == "Acme"
    assert row.snapshot_json["mission"] == "Acme builds rockets."
    assert row.source_urls == ["https://acme.example", "https://news.example/acme"]


async def test_research_company_cache_hit(
    agent_session: AsyncSession,
    alice: User,
    analyzed_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing snapshot for the job short-circuits Tavily + LLM."""
    await repos.upsert_company_snapshot(
        agent_session,
        job_id=analyzed_job.id,
        company_name="Acme",
        snapshot_json=_fake_snapshot().model_dump(),
        source_urls=["https://cached.example"],
        model_name="qwen3-8b",
    )

    _patch_settings_key(monkeypatch, "fake-key")
    await _patch_loader(
        monkeypatch,
        _job_payload(analyzed_job, parsed={"company_name": "Acme"}),
    )

    async def boom_search(*_a: object, **_kw: object) -> list[SearchResult]:
        raise AssertionError("search should not be called on cache hit")

    async def boom_fetch(*_a: object, **_kw: object) -> str:
        raise AssertionError("extract should not be called on cache hit")

    def boom_chat(**_: object) -> Any:
        raise AssertionError("LLM should not be called on cache hit")

    monkeypatch.setattr(company_researcher, "tavily_search", boom_search)
    monkeypatch.setattr(company_researcher, "fetch_url_text", boom_fetch)
    monkeypatch.setattr(company_researcher, "chat_model", boom_chat)

    result = await company_researcher.research_company(analyzed_job.id, alice.id)
    assert result.mission == "Acme builds rockets."


async def test_force_refresh_bypasses_cache(
    agent_session: AsyncSession,
    alice: User,
    analyzed_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await repos.upsert_company_snapshot(
        agent_session,
        job_id=analyzed_job.id,
        company_name="Acme",
        snapshot_json={
            "mission": "stale",
            "products": [],
            "recent_news": [],
            "values_and_signals": [],
        },
        source_urls=["https://old.example"],
        model_name="qwen3-8b",
    )

    _patch_settings_key(monkeypatch, "fake-key")
    await _patch_loader(
        monkeypatch,
        _job_payload(analyzed_job, parsed={"company_name": "Acme"}),
    )

    async def fake_search(_q: str, _k: str | None, *, max_results: int = 5) -> list[SearchResult]:
        return [SearchResult(url="https://fresh.example", title="t", content="", score=1.0)]

    async def fake_fetch(_url: str, _k: str | None) -> str:
        return "fresh content"

    monkeypatch.setattr(company_researcher, "tavily_search", fake_search)
    monkeypatch.setattr(company_researcher, "fetch_url_text", fake_fetch)
    _patch_llm(monkeypatch, _fake_snapshot())

    result = await company_researcher.research_company(
        analyzed_job.id, alice.id, force_refresh=True
    )
    assert result.mission == "Acme builds rockets."

    factory = company_researcher.AsyncSessionLocal
    async with factory() as fresh:
        row = await repos.get_company_snapshot_by_job(fresh, analyzed_job.id)
    assert row is not None
    assert row.snapshot_json["mission"] == "Acme builds rockets."
    assert row.source_urls == ["https://fresh.example"]


async def test_company_name_missing(
    agent_session: AsyncSession,
    alice: User,
    analyzed_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings_key(monkeypatch, "fake-key")
    await _patch_loader(
        monkeypatch,
        _job_payload(analyzed_job, parsed={"company_name": None, "title": "X"}),
    )

    with pytest.raises(company_researcher.CompanyNameMissing):
        await company_researcher.research_company(analyzed_job.id, alice.id)


async def test_job_not_analyzed(
    agent_session: AsyncSession,
    alice: User,
    analyzed_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings_key(monkeypatch, "fake-key")
    await _patch_loader(
        monkeypatch,
        _job_payload(analyzed_job, parsed=None),
    )

    with pytest.raises(company_researcher.JobNotAnalyzed):
        await company_researcher.research_company(analyzed_job.id, alice.id)


async def test_missing_tavily_key_surfaces(
    agent_session: AsyncSession,
    alice: User,
    analyzed_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing key bubbles up as KeyMissing from tavily_search (real helper)."""
    _patch_settings_key(monkeypatch, None)
    await _patch_loader(
        monkeypatch,
        _job_payload(analyzed_job, parsed={"company_name": "Acme"}),
    )

    with pytest.raises(KeyMissing):
        await company_researcher.research_company(analyzed_job.id, alice.id)


async def test_no_search_hits(
    agent_session: AsyncSession,
    alice: User,
    analyzed_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings_key(monkeypatch, "fake-key")
    await _patch_loader(
        monkeypatch,
        _job_payload(analyzed_job, parsed={"company_name": "Acme"}),
    )

    async def empty_search(*_a: object, **_kw: object) -> list[SearchResult]:
        return []

    monkeypatch.setattr(company_researcher, "tavily_search", empty_search)

    with pytest.raises(company_researcher.NoSearchHits):
        await company_researcher.research_company(analyzed_job.id, alice.id)


async def test_all_extracts_fail(
    agent_session: AsyncSession,
    alice: User,
    analyzed_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings_key(monkeypatch, "fake-key")
    await _patch_loader(
        monkeypatch,
        _job_payload(analyzed_job, parsed={"company_name": "Acme"}),
    )

    async def fake_search(_q: str, _k: str | None, *, max_results: int = 5) -> list[SearchResult]:
        return [SearchResult(url="https://broken.example", title="t", content="", score=1.0)]

    from interview_coach.ingestion.errors import FetchFailed as _FF

    async def fake_fetch(_url: str, _k: str | None) -> str:
        raise _FF("simulated extract failure")

    monkeypatch.setattr(company_researcher, "tavily_search", fake_search)
    monkeypatch.setattr(company_researcher, "fetch_url_text", fake_fetch)

    with pytest.raises(company_researcher.NoUsablePages):
        await company_researcher.research_company(analyzed_job.id, alice.id)


async def test_research_unknown_job(
    agent_session: AsyncSession,
    alice: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings_key(monkeypatch, "fake-key")
    await _patch_loader(
        monkeypatch,
        exc=company_researcher.JobNotAnalyzed("nope"),
    )

    with pytest.raises(company_researcher.JobNotAnalyzed):
        await company_researcher.research_company(uuid.uuid4(), alice.id)
