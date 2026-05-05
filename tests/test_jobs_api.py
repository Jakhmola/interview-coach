import pytest
from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_jobs_require_auth(client: AsyncClient) -> None:
    r = await client.post("/jobs", json={"text": "hello"})
    assert r.status_code == 401
    r = await client.get("/jobs")
    assert r.status_code == 401


async def test_post_text_happy_path(client: AsyncClient, auth_token: str) -> None:
    r = await client.post(
        "/jobs",
        headers=_auth(auth_token),
        json={"text": "We are hiring a Senior Backend Engineer..."},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["source"] == "pasted"
    assert body["source_url"] is None
    assert "Senior Backend Engineer" in body["raw_text"]
    assert body["char_count"] == len(body["raw_text"])
    assert body["parsed_json"] is None


async def test_post_text_strips_whitespace(client: AsyncClient, auth_token: str) -> None:
    r = await client.post(
        "/jobs",
        headers=_auth(auth_token),
        json={"text": "   trimmed   "},
    )
    assert r.status_code == 201
    assert r.json()["raw_text"] == "trimmed"


async def test_post_text_empty(client: AsyncClient, auth_token: str) -> None:
    r = await client.post("/jobs", headers=_auth(auth_token), json={"text": "   "})
    assert r.status_code == 400


async def test_post_text_too_large(client: AsyncClient, auth_token: str) -> None:
    big = "x" * (50_001)
    r = await client.post("/jobs", headers=_auth(auth_token), json={"text": big})
    assert r.status_code == 413


async def test_post_neither_field(client: AsyncClient, auth_token: str) -> None:
    r = await client.post("/jobs", headers=_auth(auth_token), json={})
    assert r.status_code == 422


async def test_post_both_fields(client: AsyncClient, auth_token: str) -> None:
    r = await client.post(
        "/jobs",
        headers=_auth(auth_token),
        json={"text": "x", "url": "https://example.com"},
    )
    assert r.status_code == 422


async def test_post_url_without_key(client: AsyncClient, auth_token: str) -> None:
    r = await client.post(
        "/jobs",
        headers=_auth(auth_token),
        json={"url": "https://example.com/jd"},
    )
    assert r.status_code == 503
    assert "tavily" in r.json()["detail"].lower()


async def test_post_url_with_key_happy_path(
    client: AsyncClient, auth_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interview_coach.api.jobs import routes as jobs_routes

    monkeypatch.setattr(jobs_routes.settings, "tavily_api_key", "test-key")

    async def fake_fetch(url: str, api_key: str | None) -> str:
        assert api_key == "test-key"
        return "We need a Backend Engineer who knows FastAPI."

    monkeypatch.setattr(jobs_routes, "fetch_url_text", fake_fetch)

    r = await client.post(
        "/jobs",
        headers=_auth(auth_token),
        json={"url": "https://example.com/jd"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["source"] == "url"
    assert body["source_url"] == "https://example.com/jd"
    assert "Backend Engineer" in body["raw_text"]


async def test_post_url_fetch_failed(
    client: AsyncClient, auth_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interview_coach.api.jobs import routes as jobs_routes
    from interview_coach.ingestion.errors import FetchFailed

    monkeypatch.setattr(jobs_routes.settings, "tavily_api_key", "test-key")

    async def fake_fetch(url: str, api_key: str | None) -> str:
        raise FetchFailed("upstream timeout")

    monkeypatch.setattr(jobs_routes, "fetch_url_text", fake_fetch)

    r = await client.post(
        "/jobs",
        headers=_auth(auth_token),
        json={"url": "https://example.com/jd"},
    )
    assert r.status_code == 502


async def test_list_get_delete(client: AsyncClient, auth_token: str) -> None:
    r = await client.post("/jobs", headers=_auth(auth_token), json={"text": "JD one body"})
    job_id = r.json()["id"]

    r = await client.get("/jobs", headers=_auth(auth_token))
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert "raw_text" not in items[0]
    assert items[0]["preview"].startswith("JD one body")
    assert items[0]["char_count"] == len("JD one body")

    r = await client.get(f"/jobs/{job_id}", headers=_auth(auth_token))
    assert r.status_code == 200
    assert r.json()["raw_text"] == "JD one body"

    r = await client.delete(f"/jobs/{job_id}", headers=_auth(auth_token))
    assert r.status_code == 204
    r = await client.get(f"/jobs/{job_id}", headers=_auth(auth_token))
    assert r.status_code == 404


async def test_isolation_between_users(
    client: AsyncClient, auth_token: str, second_user_token: str
) -> None:
    r = await client.post("/jobs", headers=_auth(auth_token), json={"text": "alice's JD"})
    a_id = r.json()["id"]

    r = await client.get("/jobs", headers=_auth(second_user_token))
    assert r.json() == []
    r = await client.get(f"/jobs/{a_id}", headers=_auth(second_user_token))
    assert r.status_code == 404
    r = await client.delete(f"/jobs/{a_id}", headers=_auth(second_user_token))
    assert r.status_code == 404
