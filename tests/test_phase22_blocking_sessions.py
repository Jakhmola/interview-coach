"""Phase 22 — blocking-sessions 409 body + prep-checkpoint cleanup on
``DELETE /jobs/{id}``.

The phase18 tests cover the single-session case; here we exercise:

* multiple active sessions all surface in the 409 body
* ``DELETE /jobs/{id}`` calls ``adelete_thread`` for the per-job prep
  checkpoint, swallowing any failure from the saver.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_delete_job_409_lists_all_blocking_sessions(
    client: AsyncClient,
    auth_token: str,
    db_session,  # noqa: ANN001
) -> None:
    from interview_coach.db import repos

    j = await client.post(
        "/jobs", headers=_auth(auth_token), json={"text": "Staff role at TestCo."}
    )
    job_id = uuid.UUID(j.json()["id"])
    me = await client.get("/auth/me", headers=_auth(auth_token))
    user_id = uuid.UUID(me.json()["id"])

    expected: list[uuid.UUID] = []
    for _ in range(3):
        s = await repos.create_session(
            db_session,
            user_id=user_id,
            job_id=job_id,
            round_type="behavioral_star",
            n_questions=1,
        )
        expected.append(s.id)

    d = await client.delete(f"/jobs/{job_id}", headers=_auth(auth_token))
    assert d.status_code == 409
    detail = d.json()["detail"]
    assert detail["code"] == "job_in_use"
    returned = {uuid.UUID(s) for s in detail["blocking_session_ids"]}
    assert returned == set(expected)


async def test_delete_job_calls_adelete_thread(
    client: AsyncClient,
    auth_token: str,
) -> None:
    """Replace the app's checkpointer with a fake that records the
    thread id. The route should call ``adelete_thread("prep:{user}:{job}")``
    on successful delete."""
    from interview_coach.api.main import app

    class _FakeSaver:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def adelete_thread(self, thread_id: str) -> None:
            self.deleted.append(thread_id)

    fake = _FakeSaver()
    prior = app.state.checkpointer
    app.state.checkpointer = fake
    try:
        j = await client.post("/jobs", headers=_auth(auth_token), json={"text": "Delete-me JD."})
        job_id = j.json()["id"]
        me = await client.get("/auth/me", headers=_auth(auth_token))
        user_id = me.json()["id"]

        d = await client.delete(f"/jobs/{job_id}", headers=_auth(auth_token))
        assert d.status_code == 204
        assert fake.deleted == [f"prep:{user_id}:{job_id}"]
    finally:
        app.state.checkpointer = prior


async def test_delete_job_swallows_checkpoint_cleanup_failure(
    client: AsyncClient,
    auth_token: str,
) -> None:
    """If the saver raises during cleanup, the delete must still return
    204 — a leaked checkpoint thread is a smaller bug than a 500 on a
    user-visible delete."""
    from interview_coach.api.main import app

    class _ExplodingSaver:
        async def adelete_thread(self, _thread_id: str) -> None:
            raise RuntimeError("saver offline")

    prior = app.state.checkpointer
    app.state.checkpointer = _ExplodingSaver()
    try:
        j = await client.post(
            "/jobs", headers=_auth(auth_token), json={"text": "Still-deleted JD."}
        )
        job_id = j.json()["id"]
        d = await client.delete(f"/jobs/{job_id}", headers=_auth(auth_token))
        assert d.status_code == 204
    finally:
        app.state.checkpointer = prior


async def test_delete_cv_409_lists_blocking_sessions(
    client: AsyncClient,
    auth_token: str,
    db_session,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
    sample_pdf: bytes,
) -> None:
    """The CV-side 409 ships the same structured body."""
    from unittest.mock import AsyncMock

    from interview_coach.api.documents import routes as doc_routes
    from interview_coach.db import repos

    monkeypatch.setattr(doc_routes, "_embed_in_background", AsyncMock(return_value=None))

    cv = await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", sample_pdf, "application/pdf")},
    )
    cv_id = cv.json()["id"]

    j = await client.post(
        "/jobs", headers=_auth(auth_token), json={"text": "Backend role at TestCo."}
    )
    job_id = uuid.UUID(j.json()["id"])
    me = await client.get("/auth/me", headers=_auth(auth_token))
    user_id = uuid.UUID(me.json()["id"])

    s = await repos.create_session(
        db_session,
        user_id=user_id,
        job_id=job_id,
        round_type="behavioral_star",
        n_questions=1,
    )

    d = await client.delete(f"/documents/{cv_id}", headers=_auth(auth_token))
    assert d.status_code == 409
    detail = d.json()["detail"]
    assert detail["code"] == "cv_in_use"
    assert detail["blocking_session_ids"] == [str(s.id)]
