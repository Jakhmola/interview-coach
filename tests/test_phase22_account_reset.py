"""Phase 22 — ``POST /auth/me/reset`` (option b) wipes everything the
user owns while keeping the ``users`` row + auth token intact, so the
user remains logged in with an empty account ready to re-onboard.

Scope of the cascade:
  * documents, jobs, profile, sessions deleted directly
  * grounding_chunks, document_mappings, company_snapshots, threads,
    messages cascade via ``users.id`` FK
  * langgraph checkpoint threads for ``prep:{user}:{job}`` and
    ``interview:{session_id}`` are best-effort cleaned (Phase 34)

Failure modes covered:
  * typed-email guard: empty / wrong / wrong-case-still-matches
  * checkpoint saver missing / raising → DB scrub still completes
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class _FakeSaver:
    """Records ``adelete_thread`` calls. Phase 34: the reset path deletes one
    ``interview:{session_id}`` thread per session directly (no per-turn
    enumeration), so this fake just needs to capture the ids it's handed."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)


async def test_reset_wipes_data_keeps_user_and_token(
    client: AsyncClient,
    auth_token: str,
    db_session,  # noqa: ANN001
) -> None:
    """Happy path: seed CV + JD + session, hit reset, verify everything
    owned is gone AND the same token still resolves to ``/auth/me``."""
    from interview_coach.api.main import app
    from interview_coach.db import repos
    from tests.conftest import make_pdf

    me = await client.get("/auth/me", headers=_auth(auth_token))
    assert me.status_code == 200
    user_id = uuid.UUID(me.json()["id"])
    email = me.json()["email"]

    await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", make_pdf("Alice CV"), "application/pdf")},
    )
    j = await client.post("/jobs", headers=_auth(auth_token), json={"text": "Backend role."})
    job_id = uuid.UUID(j.json()["id"])
    sess = await repos.create_session(
        db_session,
        user_id=user_id,
        job_id=job_id,
        round_type="behavioral_star",
        n_questions=1,
    )

    fake = _FakeSaver()
    prior = app.state.checkpointer
    app.state.checkpointer = fake
    try:
        r = await client.post(
            "/auth/me/reset",
            headers=_auth(auth_token),
            json={"confirm_email": email},
        )
        assert r.status_code == 204, r.text
    finally:
        app.state.checkpointer = prior

    # Owned data gone.
    docs = await client.get("/documents", headers=_auth(auth_token))
    jobs = await client.get("/jobs", headers=_auth(auth_token))
    assert docs.status_code == 200 and docs.json() == []
    assert jobs.status_code == 200 and jobs.json() == []
    async with type(db_session)(bind=db_session.bind) as s:
        profile = await repos.get_profile(s, user_id)
        remaining_sessions = await repos.list_all_session_ids_for_user(s, user_id)
    assert profile is None
    assert remaining_sessions == []

    # User row + token still valid.
    me2 = await client.get("/auth/me", headers=_auth(auth_token))
    assert me2.status_code == 200
    assert me2.json()["id"] == str(user_id)
    assert me2.json()["email"] == email

    # Checkpoint threads cleaned: prep + per-session interview thread.
    assert f"prep:{user_id}:{job_id}" in fake.deleted
    assert f"interview:{sess.id}" in fake.deleted


async def test_reset_rejects_wrong_email(
    client: AsyncClient,
    auth_token: str,
) -> None:
    """confirm_email must match — otherwise 400 and nothing is touched."""
    from tests.conftest import make_pdf

    await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", make_pdf("Alice CV"), "application/pdf")},
    )

    r = await client.post(
        "/auth/me/reset",
        headers=_auth(auth_token),
        json={"confirm_email": "someone-else@example.com"},
    )
    assert r.status_code == 400

    # Data still there.
    docs = await client.get("/documents", headers=_auth(auth_token))
    assert len(docs.json()) == 1


async def test_reset_accepts_email_case_insensitively(
    client: AsyncClient,
    auth_token: str,
) -> None:
    """A user who typed ``Alice@Example.com`` shouldn't get a 400 when
    their stored email is ``alice@example.com``. The guard exists to
    catch fat-fingered typos, not punish capitalization."""
    me = await client.get("/auth/me", headers=_auth(auth_token))
    email = me.json()["email"]

    r = await client.post(
        "/auth/me/reset",
        headers=_auth(auth_token),
        json={"confirm_email": email.upper()},
    )
    assert r.status_code == 204, r.text


async def test_reset_survives_missing_or_flaky_checkpointer(
    client: AsyncClient,
    auth_token: str,
    db_session,  # noqa: ANN001
) -> None:
    """DB scrub is the source of truth. If the saver is missing / raises,
    the user's data must still be wiped — orphan threads can be GC'd
    separately."""
    from interview_coach.api.main import app
    from interview_coach.db import repos
    from tests.conftest import make_pdf

    class _Boom:
        async def adelete_thread(self, _tid: str) -> None:
            raise RuntimeError("saver dead")

    me = await client.get("/auth/me", headers=_auth(auth_token))
    email = me.json()["email"]
    user_id = uuid.UUID(me.json()["id"])

    await client.post(
        "/documents",
        headers=_auth(auth_token),
        data={"kind": "cv"},
        files={"file": ("cv.pdf", make_pdf("CV"), "application/pdf")},
    )
    await client.post("/jobs", headers=_auth(auth_token), json={"text": "JD."})

    prior = app.state.checkpointer
    app.state.checkpointer = _Boom()
    try:
        r = await client.post(
            "/auth/me/reset",
            headers=_auth(auth_token),
            json={"confirm_email": email},
        )
        assert r.status_code == 204, r.text
    finally:
        app.state.checkpointer = prior

    async with type(db_session)(bind=db_session.bind) as s:
        assert await repos.get_profile(s, user_id) is None
        assert await repos.list_job_ids_for_user(s, user_id) == []
