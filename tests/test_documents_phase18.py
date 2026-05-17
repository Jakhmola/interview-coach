"""Phase 18 backend additions:

- Auto profile-build on CV upload (background task scheduled).
- POST /documents/{cv_id}/rebuild-profile (idempotent, single-flight, 202).
- embedding_status field on GET /documents and GET /documents/{id}.
- DELETE /documents/{cv_id} refuses with 409 cv_in_use if active sessions exist.

These tests stub the underlying ``profile_builder.build_profile`` and the
RAG embedding helper so unit tests stay fast and hermetic.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def patched_bg_tasks() -> AsyncIterator[dict[str, AsyncMock]]:
    """Patch the two background helpers so tests can assert on call counts
    without actually doing embedding / LLM work."""
    embed = AsyncMock()
    build = AsyncMock()
    with (
        patch(
            "interview_coach.api.documents.routes._embed_in_background",
            new=embed,
        ),
        patch(
            "interview_coach.api.documents.routes._profile_build_in_background",
            new=build,
        ),
    ):
        yield {"embed": embed, "build": build}


# --- profile build scheduled on CV upload -----------------------------


async def test_upload_cv_schedules_profile_build(
    client: AsyncClient,
    auth_token: str,
    sample_pdf: bytes,
    patched_bg_tasks: dict[str, AsyncMock],
) -> None:
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    await _drain_background()
    assert patched_bg_tasks["embed"].await_count == 1
    assert patched_bg_tasks["build"].await_count == 1


async def test_upload_project_doc_does_not_build_profile(
    client: AsyncClient,
    auth_token: str,
    sample_docx: bytes,
    patched_bg_tasks: dict[str, AsyncMock],
) -> None:
    DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "project_doc"},
        files={"file": ("proj.docx", sample_docx, DOCX_CT)},
    )
    assert r.status_code == 201, r.text
    await _drain_background()
    assert patched_bg_tasks["embed"].await_count == 0
    assert patched_bg_tasks["build"].await_count == 0


# --- rebuild-profile endpoint -----------------------------------------


async def test_rebuild_profile_endpoint(
    client: AsyncClient,
    auth_token: str,
    sample_pdf: bytes,
    patched_bg_tasks: dict[str, AsyncMock],
) -> None:
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    cv_id = r.json()["id"]
    await _drain_background()
    assert patched_bg_tasks["build"].await_count == 1

    r2 = await client.post(
        f"/documents/{cv_id}/rebuild-profile",
        headers=_auth(auth_token),
    )
    assert r2.status_code == 202, r2.text
    assert r2.json() == {"status": "scheduled"}
    await _drain_background()
    assert patched_bg_tasks["build"].await_count == 2


async def test_rebuild_profile_404_on_unknown_doc(
    client: AsyncClient,
    auth_token: str,
) -> None:
    r = await client.post(
        f"/documents/{uuid.uuid4()}/rebuild-profile",
        headers=_auth(auth_token),
    )
    assert r.status_code == 404


async def test_rebuild_profile_rejects_project_doc(
    client: AsyncClient,
    auth_token: str,
    sample_docx: bytes,
) -> None:
    DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "project_doc"},
        files={"file": ("proj.docx", sample_docx, DOCX_CT)},
    )
    assert r.status_code == 201
    doc_id = r.json()["id"]

    r2 = await client.post(
        f"/documents/{doc_id}/rebuild-profile",
        headers=_auth(auth_token),
    )
    assert r2.status_code == 400


# --- embedding_status field ------------------------------------------


async def test_embedding_status_present_on_upload(
    client: AsyncClient,
    auth_token: str,
    sample_pdf: bytes,
    patched_bg_tasks: dict[str, AsyncMock],
) -> None:
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    body = r.json()
    assert body.get("embedding_status") == "pending"


async def test_embedding_status_n_a_for_unmapped_project_doc(
    client: AsyncClient,
    auth_token: str,
    sample_docx: bytes,
    patched_bg_tasks: dict[str, AsyncMock],
) -> None:
    DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "project_doc"},
        files={"file": ("p.docx", sample_docx, DOCX_CT)},
    )
    assert r.json().get("embedding_status") == "n_a"


async def test_list_documents_includes_embedding_status(
    client: AsyncClient,
    auth_token: str,
    sample_pdf: bytes,
    patched_bg_tasks: dict[str, AsyncMock],
) -> None:
    await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    r = await client.get("/documents", headers=_auth(auth_token))
    assert r.status_code == 200
    items = r.json()
    assert items, "expected at least one document"
    for d in items:
        assert "embedding_status" in d


# --- delete-CV guard --------------------------------------------------


async def test_delete_cv_blocked_by_active_session(
    client: AsyncClient,
    auth_token: str,
    sample_pdf: bytes,
    db_session,  # noqa: ANN001 — sqlite async session from conftest
    patched_bg_tasks: dict[str, AsyncMock],
) -> None:
    from interview_coach.db import repos

    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    cv_id = r.json()["id"]

    me = await client.get("/auth/me", headers=_auth(auth_token))
    user_id = uuid.UUID(me.json()["id"])

    j = await client.post(
        "/jobs",
        headers=_auth(auth_token),
        json={"text": "Senior backend engineer at TestCo"},
    )
    job_id = uuid.UUID(j.json()["id"])

    await repos.create_session(
        db_session,
        user_id=user_id,
        job_id=job_id,
        round_type="behavioral_star",
        n_questions=1,
    )

    d = await client.delete(f"/documents/{cv_id}", headers=_auth(auth_token))
    assert d.status_code == 409
    assert d.json()["detail"] == "cv_in_use"


# --- delete-CV also drops profile ------------------------------------


async def test_delete_cv_drops_profile(
    client: AsyncClient,
    auth_token: str,
    sample_pdf: bytes,
    db_session,  # noqa: ANN001
    patched_bg_tasks: dict[str, AsyncMock],
) -> None:
    """When the CV is deleted, the profile that was grounded in it must
    also be dropped — otherwise the wizard sees stale profile_ready=True
    and skips the upload step on the next visit."""
    from interview_coach.db import repos

    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    cv_id = r.json()["id"]

    me = await client.get("/auth/me", headers=_auth(auth_token))
    user_id = uuid.UUID(me.json()["id"])

    # Seed a profile row directly (simulating profile_builder having run).
    await repos.upsert_profile(
        db_session,
        user_id=user_id,
        profile_json={"summary": "x"},
        source_doc_ids=[cv_id],
        model_name="test",
    )
    assert await repos.get_profile(db_session, user_id) is not None

    d = await client.delete(f"/documents/{cv_id}", headers=_auth(auth_token))
    assert d.status_code == 204

    db_session.expire_all()
    assert await repos.get_profile(db_session, user_id) is None


# --- helpers ---------------------------------------------------------


async def _drain_background() -> None:
    """Yield once so any ``asyncio.create_task(...)`` scheduled inside the
    handler gets a chance to start (and complete, since our patched
    AsyncMocks are instant). One ``asyncio.sleep(0)`` is enough."""
    import asyncio

    await asyncio.sleep(0)
    await asyncio.sleep(0)
