"""Unit tests for `interview_coach.rag.client.EmbeddingClient`.

Uses `httpx.MockTransport` so no network and no real `embedder` service
is needed — the surface we care about is the HTTP wire contract and the
retry / failure-mapping behavior.
"""

from __future__ import annotations

import httpx
import pytest

from interview_coach.rag.client import (
    EXPECTED_DIM,
    EXPECTED_MODEL_NAME,
    EmbeddingClient,
    EmbedderUnavailable,
)
from interview_coach.rag.model_lock import (
    EmbedderModelMismatch,
    assert_embedder_model,
)


def _client_with_transport(handler) -> EmbeddingClient:  # noqa: ANN001
    """Build an EmbeddingClient whose underlying httpx uses `handler`."""
    client = EmbeddingClient(base_url="http://test-embedder", timeout_s=1.0, retries=2)
    client._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),
        base_url="http://test-embedder",
        timeout=1.0,
    )
    return client


async def test_model_info_happy() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"
        assert req.url.path == "/model"
        return httpx.Response(
            200, json={"name": EXPECTED_MODEL_NAME, "dim": EXPECTED_DIM}
        )

    client = _client_with_transport(handler)
    info = await client.model_info()
    assert info == {"name": EXPECTED_MODEL_NAME, "dim": EXPECTED_DIM}
    await client.aclose()


async def test_model_lock_pass() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"name": EXPECTED_MODEL_NAME, "dim": EXPECTED_DIM}
        )

    client = _client_with_transport(handler)
    await assert_embedder_model(client)  # no raise
    await client.aclose()


async def test_model_lock_name_mismatch() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"name": "other/model", "dim": EXPECTED_DIM}
        )

    client = _client_with_transport(handler)
    with pytest.raises(EmbedderModelMismatch):
        await assert_embedder_model(client)
    await client.aclose()


async def test_model_lock_dim_mismatch() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"name": EXPECTED_MODEL_NAME, "dim": 768}
        )

    client = _client_with_transport(handler)
    with pytest.raises(EmbedderModelMismatch):
        await assert_embedder_model(client)
    await client.aclose()


async def test_embed_passages_happy() -> None:
    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/embed"
        body = req.read()
        captured["body"] = body
        return httpx.Response(
            200,
            json={
                "vectors": [[0.1, 0.2], [0.3, 0.4]],
                "model": EXPECTED_MODEL_NAME,
                "dim": EXPECTED_DIM,
            },
        )

    client = _client_with_transport(handler)
    out = await client.embed_passages(["a", "b"])
    assert out == [[0.1, 0.2], [0.3, 0.4]]
    body = captured["body"].decode()  # type: ignore[union-attr]
    assert '"task":"retrieval.passage"' in body.replace(" ", "")
    await client.aclose()


async def test_embed_query_unwraps_single_vector() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "vectors": [[1.0, 2.0, 3.0]],
                "model": EXPECTED_MODEL_NAME,
                "dim": EXPECTED_DIM,
            },
        )

    client = _client_with_transport(handler)
    out = await client.embed_query("hello")
    assert out == [1.0, 2.0, 3.0]
    await client.aclose()


async def test_embed_passages_empty_short_circuits() -> None:
    def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("HTTP should not be called for empty input")

    client = _client_with_transport(handler)
    out = await client.embed_passages([])
    assert out == []
    await client.aclose()


async def test_retry_on_5xx_then_success() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="loading")
        return httpx.Response(
            200,
            json={
                "vectors": [[0.0, 0.0]],
                "model": EXPECTED_MODEL_NAME,
                "dim": EXPECTED_DIM,
            },
        )

    client = _client_with_transport(handler)
    out = await client.embed_query("x")
    assert out == [0.0, 0.0]
    assert calls["n"] == 2
    await client.aclose()


async def test_retry_exhausted_raises_embedder_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="still loading")

    client = _client_with_transport(handler)
    with pytest.raises(EmbedderUnavailable):
        await client.embed_query("x")
    await client.aclose()


async def test_4xx_does_not_retry_and_raises_http_error() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(422, json={"detail": "bad task"})

    client = _client_with_transport(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.embed_query("x")
    assert calls["n"] == 1  # no retry on 4xx
    await client.aclose()
