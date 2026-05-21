"""Phase 25 (B17) — /sessions/prepare returns 409 prep_in_progress
when the prior run is paused on an interrupt.

Repro: tab 1 opens setup and the prep_graph pauses on the mapping
HITL interrupt. Tab 2 (or a stray double-click) POSTs /prepare for
the same job. Without the guard, the second POST nukes the
checkpointer thread and tab 1's interrupt is gone — the user has to
re-run prep from scratch and re-walk every mapping.

The fix: peek at ``prep_graph.aget_state(...)``; if ``state.next`` is
non-empty, refuse with 409 unless ``force_refresh=true``.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import make_docx, make_pdf

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def patched_embed(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    from interview_coach.api.documents import routes as doc_routes

    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(doc_routes, "_embed_in_background", mock)
    return mock


@pytest.fixture
def patched_agent_session(monkeypatch: pytest.MonkeyPatch, db_session):  # noqa: ANN001
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from interview_coach.agents import graph_nodes
    from interview_coach.agents.nodes import doc_intake, profile_builder

    bind = db_session.bind
    factory = async_sessionmaker(bind, expire_on_commit=False)
    monkeypatch.setattr(doc_intake, "AsyncSessionLocal", factory)
    monkeypatch.setattr(graph_nodes, "AsyncSessionLocal", factory)
    monkeypatch.setattr(profile_builder, "AsyncSessionLocal", factory)
    return factory


async def test_prepare_returns_409_when_paused_on_interrupt(
    client: AsyncClient,
    auth_token: str,
    patched_embed: AsyncMock,
    patched_agent_session,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interview_coach.agents import graph_nodes
    from interview_coach.agents.schemas import DocIntakeExtracted, DocIntakeResult

    AsyncSessionLocal = patched_agent_session  # noqa: N806

    # CV + JD + unmapped project_doc so the prep_graph will pause on
    # the mapping interrupt.
    up = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", make_pdf("Alice"), "application/pdf")},
    )
    assert up.status_code == 201

    me = await client.get("/auth/me", headers=_auth(auth_token))
    user_id = uuid.UUID(me.json()["id"])

    # Seed a profile so node_profile_builder cache-hits and the run
    # gets straight to the mapping loop.
    from interview_coach.db import repos

    async with AsyncSessionLocal() as s:
        cv_doc = next(d for d in await repos.list_documents_for_user(s, user_id) if d.kind == "cv")
        await repos.upsert_profile(
            s,
            user_id=user_id,
            profile_json={
                "summary": "x",
                "skills": [],
                "experiences": [
                    {
                        "company": "Acme",
                        "role": "SWE",
                        "highlights": [{"text": "h", "source_document_ids": []}],
                    }
                ],
                "projects": [],
                "education": [],
            },
            source_doc_ids=[str(cv_doc.id)],
            model_name="test",
        )

    jr = await client.post(
        "/jobs", headers=_auth(auth_token), json={"text": "Senior Engineer at Acme."}
    )
    assert jr.status_code == 201
    job_id = jr.json()["id"]

    # Seed job parsed_json + company snapshot so the only stop in the
    # graph is the mapping interrupt.
    async with AsyncSessionLocal() as s:
        await repos.update_job_parsed_json(
            s,
            uuid.UUID(job_id),
            user_id,
            {
                "title": "Eng",
                "seniority": "senior",
                "must_have_skills": [],
                "nice_to_have_skills": [],
                "responsibilities": [],
                "behavioral_signals": [],
                "company_name": "Acme",
            },
        )
        await repos.upsert_company_snapshot(
            s,
            job_id=uuid.UUID(job_id),
            company_name="Acme",
            snapshot_json={
                "mission": "x",
                "products": [],
                "recent_news": [],
                "values_and_signals": [],
            },
            source_urls=[],
            model_name="test",
        )

    await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "project_doc"},
        files={"file": ("p.docx", make_docx("Project body"), DOCX_CT)},
    )

    async def fake_intake(*_a: Any, **_k: Any):
        return DocIntakeResult(
            title="t",
            extracted=DocIntakeExtracted(tech_stack=[], description=None, urls=[]),
            suggestions=[],
        )

    monkeypatch.setattr(graph_nodes, "run_intake", fake_intake)

    # Drive the first prep — it should pause on the mapping interrupt.
    async with client.stream(
        "POST",
        "/sessions/prepare",
        headers=_auth(auth_token),
        json={"job_id": job_id, "force_refresh": False},
    ) as r1:
        assert r1.status_code == 200
        async for _ in r1.aiter_bytes():
            pass

    # Second prepare on the same job — graph is paused on interrupt.
    r2 = await client.post(
        "/sessions/prepare",
        headers=_auth(auth_token),
        json={"job_id": job_id, "force_refresh": False},
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["detail"] == "prep_in_progress"

    # force_refresh=true bypasses the guard (explicit override).
    async with client.stream(
        "POST",
        "/sessions/prepare",
        headers=_auth(auth_token),
        json={"job_id": job_id, "force_refresh": True},
    ) as r3:
        assert r3.status_code == 200
        async for _ in r3.aiter_bytes():
            pass


async def test_prepare_no_409_after_clean_finish(
    client: AsyncClient,
    auth_token: str,
    patched_embed: AsyncMock,
    patched_agent_session,  # noqa: ANN001
) -> None:
    """No project_docs → prep_graph runs straight to END → second prep
    POST is allowed (the prior thread is finished, not paused)."""
    from interview_coach.db import repos

    AsyncSessionLocal = patched_agent_session  # noqa: N806

    up = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", make_pdf("Alice"), "application/pdf")},
    )
    assert up.status_code == 201

    me = await client.get("/auth/me", headers=_auth(auth_token))
    user_id = uuid.UUID(me.json()["id"])

    async with AsyncSessionLocal() as s:
        cv_doc = next(d for d in await repos.list_documents_for_user(s, user_id) if d.kind == "cv")
        await repos.upsert_profile(
            s,
            user_id=user_id,
            profile_json={
                "summary": "x",
                "skills": [],
                "experiences": [],
                "projects": [],
                "education": [],
            },
            source_doc_ids=[str(cv_doc.id)],
            model_name="test",
        )

    jr = await client.post(
        "/jobs", headers=_auth(auth_token), json={"text": "Senior Engineer at Acme."}
    )
    job_id = jr.json()["id"]

    async with AsyncSessionLocal() as s:
        await repos.update_job_parsed_json(
            s,
            uuid.UUID(job_id),
            user_id,
            {
                "title": "Eng",
                "seniority": "senior",
                "must_have_skills": [],
                "nice_to_have_skills": [],
                "responsibilities": [],
                "behavioral_signals": [],
                "company_name": "Acme",
            },
        )
        await repos.upsert_company_snapshot(
            s,
            job_id=uuid.UUID(job_id),
            company_name="Acme",
            snapshot_json={
                "mission": "x",
                "products": [],
                "recent_news": [],
                "values_and_signals": [],
            },
            source_urls=[],
            model_name="test",
        )

    async with client.stream(
        "POST",
        "/sessions/prepare",
        headers=_auth(auth_token),
        json={"job_id": job_id, "force_refresh": False},
    ) as r1:
        assert r1.status_code == 200
        async for _ in r1.aiter_bytes():
            pass

    # Run finished cleanly (state.next is empty); a second prep is fine.
    async with client.stream(
        "POST",
        "/sessions/prepare",
        headers=_auth(auth_token),
        json={"job_id": job_id, "force_refresh": False},
    ) as r2:
        assert r2.status_code == 200
