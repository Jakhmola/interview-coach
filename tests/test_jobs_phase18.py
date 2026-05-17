"""Phase 18: DELETE /jobs/{id} now refuses with 409 job_in_use when an
active session references the job."""

from __future__ import annotations

import uuid

from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_delete_job_blocked_by_active_session(
    client: AsyncClient,
    auth_token: str,
    db_session,  # noqa: ANN001 — sqlite async session from conftest
) -> None:
    from interview_coach.db import repos

    j = await client.post(
        "/jobs",
        headers=_auth(auth_token),
        json={"text": "Staff platform engineer at TestCo"},
    )
    assert j.status_code == 201
    job_id = uuid.UUID(j.json()["id"])

    me = await client.get("/auth/me", headers=_auth(auth_token))
    user_id = uuid.UUID(me.json()["id"])

    await repos.create_session(
        db_session,
        user_id=user_id,
        job_id=job_id,
        round_type="behavioral_star",
        n_questions=1,
    )

    d = await client.delete(f"/jobs/{job_id}", headers=_auth(auth_token))
    assert d.status_code == 409
    assert d.json()["detail"] == "job_in_use"

    # After abandoning the session, delete succeeds.
    sessions = await repos.list_sessions_for_user(db_session, user_id)
    sess_id = sessions[0].id
    await repos.update_session_status(db_session, sess_id, user_id, "abandoned")

    d2 = await client.delete(f"/jobs/{job_id}", headers=_auth(auth_token))
    assert d2.status_code == 204


async def test_delete_job_succeeds_when_no_active_session(
    client: AsyncClient,
    auth_token: str,
) -> None:
    j = await client.post(
        "/jobs",
        headers=_auth(auth_token),
        json={"text": "Senior data scientist at TestCo"},
    )
    job_id = j.json()["id"]
    d = await client.delete(f"/jobs/{job_id}", headers=_auth(auth_token))
    assert d.status_code == 204
