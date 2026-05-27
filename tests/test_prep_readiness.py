"""Phase 30 (Part B) — ``repos.prep_readiness`` owns the readiness rule.

The "ready to practice?" rollup used to live inline in
``GET /sessions/prepare/status``. These tests pin the rule at the repo
boundary (sqlite-backed, like the other repo tests):

* the ``missing[]`` list for each partial state,
* ``can_start`` flipping only when nothing is missing,
* a *degraded* company snapshot still counting as researched,
* the unmapped-project-doc count,
* a missing job → ``None`` (the route maps that to 404).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.db import repos
from interview_coach.db.models import Job, User


async def _seed(
    session: AsyncSession,
    *,
    cv: bool = False,
    profile: bool = False,
    analyzed: bool = False,
    snapshot: bool = False,
    degraded: bool = False,
    n_unmapped: int = 0,
) -> tuple[User, Job]:
    user = await repos.create_user(session, "alice@example.com", "x")
    if cv:
        await repos.create_document(
            session,
            user_id=user.id,
            kind="cv",
            filename="cv.pdf",
            content_type="application/pdf",
            byte_size=1,
            raw_text="CV",
        )
    if profile:
        await repos.upsert_profile(
            session,
            user_id=user.id,
            profile_json={"summary": "p"},
            source_doc_ids=[],
            model_name="t",
        )
    job = await repos.create_job(session, user_id=user.id, source="pasted", raw_text="JD")
    if analyzed:
        await repos.update_job_parsed_json(session, job.id, user.id, {"title": "Eng"})
    if snapshot:
        snapshot_json = (
            {"mission": "", "_degraded": "NoSearchHits"} if degraded else {"mission": "m"}
        )
        await repos.upsert_company_snapshot(
            session,
            job_id=job.id,
            company_name="Acme",
            snapshot_json=snapshot_json,
            source_urls=[],
            model_name="t",
        )
    for i in range(n_unmapped):
        await repos.create_document(
            session,
            user_id=user.id,
            kind="project_doc",
            filename=f"p{i}.pdf",
            content_type="application/pdf",
            byte_size=1,
            raw_text="proj",
        )
    return user, job


@pytest.mark.parametrize(
    ("kwargs", "expected_missing"),
    [
        ({}, ["cv", "profile", "job_analysis", "company_research"]),
        ({"cv": True}, ["profile", "job_analysis", "company_research"]),
        ({"cv": True, "profile": True}, ["job_analysis", "company_research"]),
        ({"cv": True, "profile": True, "analyzed": True}, ["company_research"]),
        ({"cv": True, "profile": True, "analyzed": True, "snapshot": True}, []),
    ],
)
async def test_missing_list_and_can_start(
    db_session: AsyncSession,
    kwargs: dict[str, bool],
    expected_missing: list[str],
) -> None:
    user, job = await _seed(db_session, **kwargs)
    r = await repos.prep_readiness(db_session, user.id, job.id)
    assert r is not None
    assert r.missing == expected_missing
    assert r.can_start is (expected_missing == [])


async def test_degraded_snapshot_counts_as_researched(db_session: AsyncSession) -> None:
    user, job = await _seed(
        db_session, cv=True, profile=True, analyzed=True, snapshot=True, degraded=True
    )
    r = await repos.prep_readiness(db_session, user.id, job.id)
    assert r is not None
    # A placeholder row exists, so research is "done" even though degraded.
    assert r.company_researched is True
    assert "company_research" not in r.missing
    assert r.can_start is True


async def test_unmapped_project_doc_count(db_session: AsyncSession) -> None:
    user, job = await _seed(db_session, cv=True, profile=True, n_unmapped=2)
    r = await repos.prep_readiness(db_session, user.id, job.id)
    assert r is not None
    assert r.unmapped_project_doc_count == 2


async def test_missing_job_returns_none(db_session: AsyncSession) -> None:
    user = await repos.create_user(db_session, "alice@example.com", "x")
    r = await repos.prep_readiness(db_session, user.id, uuid.uuid4())
    assert r is None
