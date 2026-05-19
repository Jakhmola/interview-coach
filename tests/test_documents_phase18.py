"""Phase 18 + 21.1 — surviving subset of the original document tests.

Phase 21.1 removed:
- Auto profile-build on CV upload (prep_graph is the sole owner).
- POST /documents/{cv_id}/rebuild-profile (call prep_graph with
  force_refresh or delete + re-upload the CV instead).

What remains and still matters:
- CV upload still schedules background CV embedding (RAG corpus).
- project_doc upload does NOT schedule embedding (deferred to apply_mapping).
- ``embedding_status`` is computed correctly on upload.
- DELETE /documents/{cv_id} refuses with 409 ``cv_in_use`` if active sessions
  exist; deleting a CV also drops the profile.
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
async def patched_embed() -> AsyncIterator[AsyncMock]:
    """Patch the background embedding helper so tests can assert on call
    counts without hitting the embedder sidecar."""
    embed = AsyncMock()
    with patch("interview_coach.api.documents.routes._embed_in_background", new=embed):
        yield embed


# --- CV upload: embedding scheduled, profile build NOT scheduled ------


async def test_upload_cv_schedules_embedding_only(
    client: AsyncClient,
    auth_token: str,
    sample_pdf: bytes,
    patched_embed: AsyncMock,
) -> None:
    """Phase 21.1: ``upload_document`` schedules only the RAG embedding
    task. Profile-building is owned by prep_graph — verifying we removed
    the background trigger so the two paths can't race each other."""
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    await _drain_background()
    assert patched_embed.await_count == 1


async def test_upload_project_doc_schedules_nothing(
    client: AsyncClient,
    auth_token: str,
    sample_docx: bytes,
    patched_embed: AsyncMock,
) -> None:
    """project_doc embedding is deferred until ``apply_mapping`` runs so
    chunks carry the user-confirmed project_title. Upload should not
    schedule any background task."""
    DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    r = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "project_doc"},
        files={"file": ("proj.docx", sample_docx, DOCX_CT)},
    )
    assert r.status_code == 201, r.text
    await _drain_background()
    assert patched_embed.await_count == 0


# --- embedding_status field ------------------------------------------


async def test_embedding_status_present_on_upload(
    client: AsyncClient,
    auth_token: str,
    sample_pdf: bytes,
    patched_embed: AsyncMock,
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
    patched_embed: AsyncMock,
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
    patched_embed: AsyncMock,
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
    patched_embed: AsyncMock,
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
    detail = d.json()["detail"]
    # Phase 22: structured body carrying the offending session ids so
    # Manage can render per-session Abandon buttons.
    assert detail["code"] == "cv_in_use"
    sessions = await repos.list_sessions_for_user(db_session, user_id)
    assert detail["blocking_session_ids"] == [str(sessions[0].id)]


async def test_delete_cv_drops_profile(
    client: AsyncClient,
    auth_token: str,
    sample_pdf: bytes,
    db_session,  # noqa: ANN001
    patched_embed: AsyncMock,
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
    """Yield twice so any ``asyncio.create_task(...)`` scheduled inside the
    handler gets a chance to start (and complete, since our patched
    AsyncMocks are instant)."""
    import asyncio

    await asyncio.sleep(0)
    await asyncio.sleep(0)
