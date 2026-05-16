"""Unit tests that exercise the FastAPI app without downloading the model.

The real model load is gated behind the `embedder` marker in test_app.py.
These tests inject a fake model into the singleton holder so the app
surface (routing, status codes, schema validation) is covered on every
CI run.
"""

from __future__ import annotations

import math

import httpx
import numpy as np
import pytest
from embedder import main as app_module
from embedder.main import EMBEDDING_DIM, MODEL_NAME, app


class _FakeModel:
    """Deterministic stand-in for SentenceTransformer.

    Encodes each input string into a 1024-d vector seeded by its hash;
    output is L2-normalized so the contract matches the real path.
    """

    def encode(
        self,
        texts: list[str],
        *,
        task: str,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
    ) -> np.ndarray:
        assert normalize_embeddings is True
        assert convert_to_numpy is True
        assert task in ("retrieval.passage", "retrieval.query")
        rows = []
        for t in texts:
            rng = np.random.default_rng(abs(hash((task, t))) % (2**32))
            v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
            v /= np.linalg.norm(v) + 1e-12
            rows.append(v)
        return np.stack(rows, axis=0)


@pytest.fixture
def fake_model_loaded(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(app_module._HOLDER, "model", _FakeModel())
    yield
    monkeypatch.setattr(app_module._HOLDER, "model", None)


async def test_healthz_503_when_not_loaded() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json() == {"ok": False, "model_loaded": False}


async def test_model_503_when_not_loaded() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/model")
    assert resp.status_code == 503


async def test_embed_503_when_not_loaded() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/embed",
            json={"texts": ["hi"], "task": "retrieval.query"},
        )
    assert resp.status_code == 503


async def test_embed_happy_path(fake_model_loaded: None) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/embed",
            json={
                "texts": ["a", "b"],
                "task": "retrieval.passage",
            },
        )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["model"] == MODEL_NAME
    assert payload["dim"] == EMBEDDING_DIM
    assert len(payload["vectors"]) == 2
    for vec in payload["vectors"]:
        assert len(vec) == EMBEDDING_DIM
        assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, abs_tol=1e-4)


async def test_embed_validates_task(fake_model_loaded: None) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/embed",
            json={"texts": ["hi"], "task": "classification"},
        )
    assert resp.status_code == 422


async def test_embed_rejects_empty_texts(fake_model_loaded: None) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/embed",
            json={"texts": [], "task": "retrieval.query"},
        )
    assert resp.status_code == 422
