import httpx
import pytest

from interview_coach.ingestion.errors import FetchFailed, KeyMissing
from interview_coach.ingestion.web import TAVILY_EXTRACT_URL, fetch_url_text


async def test_fetch_url_text_missing_key() -> None:
    with pytest.raises(KeyMissing):
        await fetch_url_text("https://example.com", None)
    with pytest.raises(KeyMissing):
        await fetch_url_text("https://example.com", "")


async def test_fetch_url_text_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "results": [{"url": "https://example.com", "raw_content": "Job description body"}],
                "failed_results": [],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("interview_coach.providers.tavily.httpx.AsyncClient", fake_async_client)

    text = await fetch_url_text("https://example.com", "test-key")
    assert text == "Job description body"
    assert captured["url"] == TAVILY_EXTRACT_URL
    assert captured["headers"]["authorization"] == "Bearer test-key"


async def test_fetch_url_text_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream error")

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("interview_coach.providers.tavily.httpx.AsyncClient", fake_async_client)

    with pytest.raises(FetchFailed):
        await fetch_url_text("https://example.com", "test-key")


async def test_fetch_url_text_failed_results(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [],
                "failed_results": [{"url": "https://example.com", "error": "timeout"}],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("interview_coach.providers.tavily.httpx.AsyncClient", fake_async_client)

    with pytest.raises(FetchFailed, match="timeout"):
        await fetch_url_text("https://example.com", "test-key")


async def test_fetch_url_text_empty_content(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"url": "https://example.com", "raw_content": "   "}]},
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("interview_coach.providers.tavily.httpx.AsyncClient", fake_async_client)

    with pytest.raises(FetchFailed, match="empty"):
        await fetch_url_text("https://example.com", "test-key")
