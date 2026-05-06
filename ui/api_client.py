import json
import os
from collections.abc import Iterator
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


# --- sessions ---


def create_session(token: str, job_id: str, round_type: str, n_questions: int = 5) -> dict:
    with _client(token) as c:
        return _unwrap(
            c.post(
                "/sessions",
                json={
                    "job_id": job_id,
                    "round_type": round_type,
                    "n_questions": n_questions,
                },
            )
        )


def list_sessions(token: str) -> list[dict]:
    with _client(token) as c:
        return _unwrap(c.get("/sessions"))


def get_session_detail(token: str, session_id: str) -> dict:
    with _client(token) as c:
        return _unwrap(c.get(f"/sessions/{session_id}"))


def abandon_session(token: str, session_id: str) -> dict:
    with _client(token) as c:
        return _unwrap(c.post(f"/sessions/{session_id}/abandon"))


class StreamResult:
    """Captures non-token side-channel events from the SSE stream so the
    page can act on them after `st.write_stream` consumes the iterator."""

    def __init__(self) -> None:
        self.done: dict[str, Any] | None = None
        self.error: dict[str, Any] | None = None


def stream_next_question(token: str, session_id: str, result: StreamResult) -> Iterator[str]:
    """Yield question-text chunks for `st.write_stream`. Mutates `result` with
    the `done` / `error` payloads so the caller can find `question_id` after.

    Errors during the HTTP handshake (4xx/5xx) raise `ApiError`. Errors
    *during* the stream surface as an `error` SSE event captured into
    `result.error` — we yield nothing for those.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
    }
    # Long timeout: the model can take 10s+ to TTFT on cold start.
    with httpx.Client(base_url=API_BASE_URL, headers=headers, timeout=300.0) as c:
        with c.stream("POST", f"/sessions/{session_id}/next_question") as r:
            if not r.is_success:
                # Drain the body so the error message is readable.
                body = b"".join(r.iter_bytes()).decode("utf-8", errors="replace")
                try:
                    detail = json.loads(body).get("detail", body)
                except Exception:
                    detail = body
                raise ApiError(r.status_code, detail)

            event = "message"
            for raw in r.iter_lines():
                line = raw if isinstance(raw, str) else raw.decode("utf-8")
                if line == "":
                    event = "message"
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event = line[len("event:") :].strip()
                    continue
                if line.startswith("data:"):
                    payload = line[len("data:") :].strip()
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        data = payload
                    if event == "token" and isinstance(data, str):
                        yield data
                    elif event == "done" and isinstance(data, dict):
                        result.done = data
                    elif event == "error" and isinstance(data, dict):
                        result.error = data
