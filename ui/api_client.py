import os
from typing import Any

import httpx

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


def _client(token: str | None = None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=API_BASE_URL, headers=headers, timeout=30.0)


def _unwrap(r: httpx.Response) -> Any:
    if r.is_success:
        if r.status_code == 204:
            return None
        return r.json()
    detail: str
    try:
        detail = r.json().get("detail", r.text)
    except Exception:
        detail = r.text
    raise ApiError(r.status_code, detail)


# --- auth ---


def healthz() -> dict[str, Any]:
    with _client() as c:
        return _unwrap(c.get("/healthz"))


def register(email: str, password: str) -> dict[str, Any]:
    with _client() as c:
        return _unwrap(c.post("/auth/register", json={"email": email, "password": password}))


def login(email: str, password: str) -> dict[str, Any]:
    with _client() as c:
        return _unwrap(c.post("/auth/login", json={"email": email, "password": password}))


def me(token: str) -> dict[str, Any]:
    with _client(token) as c:
        return _unwrap(c.get("/auth/me"))


# --- documents ---


def upload_document(token: str, kind: str, filename: str, content_type: str, data: bytes) -> dict:
    with _client(token) as c:
        r = c.post(
            "/documents",
            data={"kind": kind},
            files={"file": (filename, data, content_type)},
        )
    return _unwrap(r)


def list_documents(token: str) -> list[dict]:
    with _client(token) as c:
        return _unwrap(c.get("/documents"))


def get_document(token: str, document_id: str) -> dict:
    with _client(token) as c:
        return _unwrap(c.get(f"/documents/{document_id}"))


def delete_document(token: str, document_id: str) -> None:
    with _client(token) as c:
        _unwrap(c.delete(f"/documents/{document_id}"))


# --- jobs ---


def submit_job_text(token: str, text: str) -> dict:
    with _client(token) as c:
        return _unwrap(c.post("/jobs", json={"text": text}))


def submit_job_url(token: str, url: str) -> dict:
    with _client(token) as c:
        return _unwrap(c.post("/jobs", json={"url": url}))


def list_jobs(token: str) -> list[dict]:
    with _client(token) as c:
        return _unwrap(c.get("/jobs"))


def get_job(token: str, job_id: str) -> dict:
    with _client(token) as c:
        return _unwrap(c.get(f"/jobs/{job_id}"))


def delete_job(token: str, job_id: str) -> None:
    with _client(token) as c:
        _unwrap(c.delete(f"/jobs/{job_id}"))
