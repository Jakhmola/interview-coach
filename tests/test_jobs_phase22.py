"""Phase 22 — jobs surface: upload-time dedup and re-analyze.

* ``POST /jobs`` with identical text collapses onto the existing row
  with HTTP 200 (and the same goes for the URL path on either a
  matching URL or a matching body hash).
* ``PATCH /jobs/{id}`` replaces ``raw_text``, clears ``parsed_json``,
  refreshes ``content_hash``, and drops the company snapshot. The next
  ``/prepare`` therefore re-runs the analyzer + researcher rather than
  serving a stale cache.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_paste_dedup_returns_existing_row_with_200(
    client: AsyncClient, auth_token: str
) -> None:
    body = {"text": "Staff platform engineer at TestCo, Postgres-heavy."}

    r1 = await client.post("/jobs", headers=_auth(auth_token), json=body)
    assert r1.status_code == 201, r1.text
    first_id = r1.json()["id"]

    r2 = await client.post("/jobs", headers=_auth(auth_token), json=body)
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == first_id


async def test_distinct_text_still_creates_new_job(client: AsyncClient, auth_token: str) -> None:
    r1 = await client.post("/jobs", headers=_auth(auth_token), json={"text": "Backend role A"})
    r2 = await client.post("/jobs", headers=_auth(auth_token), json={"text": "Backend role B"})
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


async def test_url_dedup_matches_normalized_source_url(
    client: AsyncClient,
    auth_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-submitting the same URL (with cosmetic case/slash differences)
    returns the existing row without re-fetching. We assert no re-fetch
    by raising in the fetch stub on the second call."""
    from interview_coach.api.jobs import routes as job_routes

    calls = {"n": 0}

    async def fake_fetch_url_text(url: str, _key: str | None) -> str:
        calls["n"] += 1
        return "Fetched JD body for testco role."

    monkeypatch.setattr(job_routes, "fetch_url_text", fake_fetch_url_text)
    monkeypatch.setattr(job_routes.settings, "tavily_api_key", "test-key", raising=False)

    r1 = await client.post(
        "/jobs", headers=_auth(auth_token), json={"url": "https://Jobs.TestCo.com/SWE/"}
    )
    assert r1.status_code == 201, r1.text
    first_id = r1.json()["id"]
    assert calls["n"] == 1

    r2 = await client.post(
        "/jobs", headers=_auth(auth_token), json={"url": "https://jobs.testco.com/swe"}
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == first_id
    assert calls["n"] == 1  # no second fetch


async def test_patch_clears_parsed_json_and_snapshot(
    client: AsyncClient,
    auth_token: str,
    db_session,  # noqa: ANN001
) -> None:
    from interview_coach.db import repos

    r = await client.post("/jobs", headers=_auth(auth_token), json={"text": "Original JD body."})
    job_id = uuid.UUID(r.json()["id"])

    me = await client.get("/auth/me", headers=_auth(auth_token))
    user_id = uuid.UUID(me.json()["id"])

    # Seed parsed_json + snapshot so we can prove they're cleared.
    await repos.update_job_parsed_json(db_session, job_id, user_id, {"role_title": "old"})
    await repos.upsert_company_snapshot(
        db_session,
        job_id=job_id,
        company_name="TestCo",
        snapshot_json={"mission": "old"},
        source_urls=[],
        model_name="test",
    )

    p = await client.patch(
        f"/jobs/{job_id}",
        headers=_auth(auth_token),
        json={"text": "Edited JD body, typo fixed."},
    )
    assert p.status_code == 200, p.text
    assert p.json()["parsed_json"] is None
    assert "Edited JD body" in p.json()["raw_text"]

    # Snapshot must be gone too.
    snap = await repos.get_company_snapshot_by_job(db_session, job_id)
    assert snap is None


async def test_patch_404_for_unknown_job(client: AsyncClient, auth_token: str) -> None:
    r = await client.patch(
        f"/jobs/{uuid.uuid4()}",
        headers=_auth(auth_token),
        json={"text": "anything"},
    )
    assert r.status_code == 404
