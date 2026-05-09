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


class EvaluationStreamResult:
    """Captures structured side-channel events from the evaluator SSE stream.

    The Streamlit page consumes this in three passes:

    1. ``feedback_tokens()`` — generator yielding strings until ``feedback_done``.
       Pass it to ``st.write_stream``.
    2. ``model_answer_tokens()`` — same shape, yields until ``model_answer_done``.
    3. After the full stream completes, ``score`` and ``done`` are populated.

    Internally we open one HTTP request and feed all three iterators from the
    same underlying line stream (so we don't issue three separate POSTs).
    """

    def __init__(self) -> None:
        self.score: int | None = None
        self.done: dict[str, Any] | None = None
        self.error: dict[str, Any] | None = None
        self.model_answer_unavailable: bool = False
        self._raw_lines: Iterator[str] | None = None
        self._client: httpx.Client | None = None
        self._response: Any = None
        self._stream_cm: Any = None

    def _open(self, token: str, session_id: str, answer: str) -> None:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "text/event-stream",
        }
        self._client = httpx.Client(base_url=API_BASE_URL, headers=headers, timeout=300.0)
        cm = self._client.stream("POST", f"/sessions/{session_id}/answer", json={"answer": answer})
        self._response = cm.__enter__()
        self._stream_cm = cm
        if not self._response.is_success:
            body = b"".join(self._response.iter_bytes()).decode("utf-8", errors="replace")
            self._stream_cm.__exit__(None, None, None)
            self._client.close()
            try:
                detail = json.loads(body).get("detail", body)
            except Exception:
                detail = body
            raise ApiError(self._response.status_code, detail)
        self._raw_lines = (
            (raw if isinstance(raw, str) else raw.decode("utf-8"))
            for raw in self._response.iter_lines()
        )

    def finish(self) -> None:
        """Close the underlying HTTP connection. Safe to call multiple times."""
        if self._stream_cm is not None:
            self._stream_cm.__exit__(None, None, None)
            self._stream_cm = None
        if self._client is not None:
            self._client.close()
            self._client = None

    def _events(self) -> Iterator[tuple[str, Any]]:
        """Decode the raw SSE line stream into (event, data) tuples."""
        if self._raw_lines is None:
            return
        event = "message"
        for line in self._raw_lines:
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
                yield (event, data)

    def consume_feedback_tokens(self) -> Iterator[str]:
        """Yields feedback chunks until `feedback_done`. Captures `score`
        events that arrive interleaved (score is emitted before feedback in
        practice but we don't depend on order)."""
        for event, data in self._events():
            if event == "score" and isinstance(data, dict):
                self.score = data.get("score")
            elif event == "feedback_token" and isinstance(data, str):
                yield data
            elif event == "feedback_done":
                return
            elif event == "error" and isinstance(data, dict):
                self.error = data
                return

    def consume_model_answer_tokens(self) -> Iterator[str]:
        for event, data in self._events():
            if event == "model_answer_token" and isinstance(data, str):
                yield data
            elif event == "model_answer_done":
                return
            elif event == "model_answer_error":
                # Phase 14: judge succeeded but the model-answer call failed;
                # the partial-persist path is taken server-side.
                self.model_answer_unavailable = True
                return
            elif event == "done" and isinstance(data, dict):
                self.done = data
                return
            elif event == "error" and isinstance(data, dict):
                self.error = data
                return

    def consume_remaining(self) -> None:
        """Drain anything after model_answer_done (typically just `done`)."""
        for event, data in self._events():
            if event == "done" and isinstance(data, dict):
                self.done = data
            elif event == "model_answer_error":
                self.model_answer_unavailable = True
            elif event == "error" and isinstance(data, dict):
                self.error = data


def submit_answer(token: str, session_id: str, answer: str) -> EvaluationStreamResult:
    """Open the SSE stream for `POST /sessions/{id}/answer`. The caller drives
    the three consume_* methods on the result; remember to call .finish() to
    close the underlying connection (or use it as a context manager).
    """
    result = EvaluationStreamResult()
    result._open(token, session_id, answer)
    return result


# --- prepare ---


def prepare_session(token: str, job_id: str, force_refresh: bool = False) -> Iterator[dict]:
    """Consume the SSE stream from POST /sessions/prepare.

    Yields ``{"event": <name>, "data": <dict>}`` per SSE frame. The page
    can render a per-node progress table from these. Pre-stream errors
    raise ApiError.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
    }
    body = {"job_id": job_id, "force_refresh": force_refresh}
    with httpx.Client(base_url=API_BASE_URL, headers=headers, timeout=600.0) as c:
        with c.stream("POST", "/sessions/prepare", json=body) as r:
            if not r.is_success:
                payload = b"".join(r.iter_bytes()).decode("utf-8", errors="replace")
                try:
                    detail = json.loads(payload).get("detail", payload)
                except Exception:
                    detail = payload
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
                    payload_str = line[len("data:") :].strip()
                    try:
                        data = json.loads(payload_str)
                    except json.JSONDecodeError:
                        data = {"raw": payload_str}
                    yield {"event": event, "data": data}
