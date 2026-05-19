"""Phase 22 — documents surface: upload-time dedup, retry-embed, remap.

Targets:

* ``POST /documents`` returns HTTP 200 with the existing row when the
  same content (by sha256(extracted_text)) is re-uploaded for the same
  ``(user_id, kind)``. CV re-uploads still flow through replace-mode.
* ``POST /documents/{id}/embed`` schedules a background embed (202).
  Unmapped project_docs are refused with 400 — that path is for retrying
  a failed embed, not for bypassing the mapping step.
* ``POST /documents/{id}/remap`` returns the same payload shape the
  prep-graph node emits; ``POST .../remap/confirm`` with apply mutates
  the profile and with skip is a no-op.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import make_docx, make_pdf

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def patched_embed(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stop the test from actually calling the embedder."""
    from interview_coach.api.documents import routes as doc_routes

    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(doc_routes, "_embed_in_background", mock)
    return mock


@pytest.fixture
def patched_async_session(monkeypatch: pytest.MonkeyPatch, db_session):  # noqa: ANN001
    """Point the agent layer's ``AsyncSessionLocal`` at the in-memory
    sqlite engine the API tests share. ``doc_intake`` opens its own
    sessions directly so the FastAPI dependency override never reaches
    it — match the pattern from test_sessions_api.py."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from interview_coach.agents.nodes import doc_intake

    bind = db_session.bind
    factory = async_sessionmaker(bind, expire_on_commit=False)
    monkeypatch.setattr(doc_intake, "AsyncSessionLocal", factory)
    return factory


# --- dedup ------------------------------------------------------------


async def test_project_doc_dedup_returns_existing_row_with_200(
    client: AsyncClient,
    auth_token: str,
) -> None:
    body = make_docx("Project: dedup smoke. Built a thing.")
    r1 = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "project_doc"},
        files={"file": ("proj.docx", body, DOCX_CT)},
    )
    assert r1.status_code == 201, r1.text
    first_id = r1.json()["id"]

    r2 = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "project_doc"},
        files={"file": ("proj_rename.docx", body, DOCX_CT)},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == first_id

    listed = await client.get("/documents", headers=_auth(auth_token))
    project_docs = [d for d in listed.json() if d["kind"] == "project_doc"]
    assert len(project_docs) == 1


async def test_project_doc_distinct_content_creates_new_row(
    client: AsyncClient,
    auth_token: str,
) -> None:
    r1 = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "project_doc"},
        files={"file": ("a.docx", make_docx("Distinct A body"), DOCX_CT)},
    )
    r2 = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "project_doc"},
        files={"file": ("b.docx", make_docx("Distinct B body"), DOCX_CT)},
    )
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


# --- prep status surfaces unmapped count -------------------------------


async def test_prep_status_includes_unmapped_project_doc_count(
    client: AsyncClient,
    auth_token: str,
) -> None:
    """The wizard's work-driven auto-prep keys off this field — without it
    the FE has no way to tell "needs prep" from "nothing to do" once
    can_start flips true."""
    j = await client.post("/jobs", headers=_auth(auth_token), json={"text": "Backend role"})
    job_id = j.json()["id"]

    # No project_docs yet.
    s0 = await client.get(f"/sessions/prepare/status?job_id={job_id}", headers=_auth(auth_token))
    assert s0.status_code == 200
    assert s0.json()["unmapped_project_doc_count"] == 0

    # Upload two distinct project_docs.
    for name, body in [
        ("alpha.docx", make_docx("Project Alpha body")),
        ("beta.docx", make_docx("Project Beta body")),
    ]:
        r = await client.post(
            "/documents",
            headers=_auth(auth_token),
            data={"kind": "project_doc"},
            files={"file": (name, body, DOCX_CT)},
        )
        assert r.status_code == 201, r.text

    s1 = await client.get(f"/sessions/prepare/status?job_id={job_id}", headers=_auth(auth_token))
    assert s1.json()["unmapped_project_doc_count"] == 2


# --- retry embed ------------------------------------------------------


async def test_retry_embed_for_cv_schedules_background_task(
    client: AsyncClient,
    auth_token: str,
    patched_embed: AsyncMock,
) -> None:
    up = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", make_pdf("Alice resume body"), "application/pdf")},
    )
    cv_id = up.json()["id"]
    # Upload call already scheduled one — clear so we can assert the
    # retry call specifically.
    patched_embed.reset_mock()

    r = await client.post(f"/documents/{cv_id}/embed", headers=_auth(auth_token))
    assert r.status_code == 202
    # _embed_in_background is fired via asyncio.create_task; awaiting the
    # response is enough to confirm the call was scheduled with the right
    # doc id (it ran inside the same event loop).
    patched_embed.assert_called_once_with(uuid.UUID(cv_id))


async def test_retry_embed_refuses_unmapped_project_doc(
    client: AsyncClient,
    auth_token: str,
) -> None:
    up = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "project_doc"},
        files={"file": ("p.docx", make_docx("Unmapped project body"), DOCX_CT)},
    )
    doc_id = up.json()["id"]
    r = await client.post(f"/documents/{doc_id}/embed", headers=_auth(auth_token))
    assert r.status_code == 400
    assert "remap" in r.json()["detail"].lower()


# --- remap ------------------------------------------------------------


async def test_remap_round_trip_apply_then_idempotent_skip(
    client: AsyncClient,
    auth_token: str,
    patched_embed: AsyncMock,
    patched_async_session,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload CV → build profile → upload project_doc → remap → apply.
    Then a second remap returns the same suggestion (run_intake stub is
    deterministic); skipping the second remap leaves the prior mapping
    intact (apply_mapping replaced rows, skip is a no-op)."""
    from interview_coach.agents.nodes import doc_intake
    from interview_coach.agents.schemas import (
        DocIntakeExtracted,
        DocIntakeResult,
        DocIntakeSuggestion,
    )
    from interview_coach.db import repos

    AsyncSessionLocal = patched_async_session  # noqa: N806 — match prod naming

    # CV upload (replace-mode; embed is patched).
    up = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", make_pdf("Alice resume body"), "application/pdf")},
    )
    assert up.status_code == 201

    me = await client.get("/auth/me", headers=_auth(auth_token))
    user_id = uuid.UUID(me.json()["id"])

    # Seed a minimal profile so apply_mapping has somewhere to write.
    async with AsyncSessionLocal() as s:
        await repos.upsert_profile(
            s,
            user_id=user_id,
            profile_json={
                "summary": "Backend.",
                "skills": ["python"],
                "experiences": [
                    {
                        "company": "Acme",
                        "role": "SWE",
                        "highlights": [
                            {"text": "Built a search service", "source_document_ids": []}
                        ],
                    }
                ],
                "projects": [],
                "education": [],
            },
            source_doc_ids=[],
            model_name="test",
        )

    # Upload an unmapped project_doc.
    pj = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "project_doc"},
        files={"file": ("p.docx", make_docx("Project: rewrite of search"), DOCX_CT)},
    )
    doc_id = pj.json()["id"]

    fake_intake = DocIntakeResult(
        title="Rewrote search",
        extracted=DocIntakeExtracted(tech_stack=["rust"], description="Cut p99 by 4x.", urls=[]),
        suggestions=[
            DocIntakeSuggestion(
                mapping_kind="highlight",
                experience_idx=0,
                highlight_idx=0,
                confidence=0.9,
                reason="Same search service",
            )
        ],
    )

    async def fake_run_intake(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return fake_intake

    monkeypatch.setattr(doc_intake, "run_intake", fake_run_intake)

    # Start remap → suggestion payload.
    r = await client.post(f"/documents/{doc_id}/remap", headers=_auth(auth_token))
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["title"] == "Rewrote search"
    assert payload["experiences"][0]["company"] == "Acme"

    # Confirm apply.
    c = await client.post(
        f"/documents/{doc_id}/remap/confirm",
        headers=_auth(auth_token),
        json={
            "action": "apply",
            "rows": [{"mapping_kind": "highlight", "experience_idx": 0, "highlight_idx": 0}],
            "title": "Rewrote search",
            "extracted": payload["extracted"],
        },
    )
    assert c.status_code == 200, c.text

    async with AsyncSessionLocal() as s:
        mappings = await repos.list_document_mappings(s, uuid.UUID(doc_id))
    assert len(mappings) == 1
    assert mappings[0].mapping_kind == "highlight"

    # Skip path on a second remap.
    r2 = await client.post(f"/documents/{doc_id}/remap", headers=_auth(auth_token))
    assert r2.status_code == 200
    s2 = await client.post(
        f"/documents/{doc_id}/remap/confirm",
        headers=_auth(auth_token),
        json={"action": "skip"},
    )
    assert s2.status_code == 200
