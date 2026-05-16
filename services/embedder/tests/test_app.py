"""Live tests for the embedder service. Gated behind the `embedder` marker
because they download and load the ~500MB Jina v3 weights.
"""

from __future__ import annotations

import math

import httpx
import pytest

from embedder.main import EMBEDDING_DIM, MODEL_NAME, _load_model, app


@pytest.mark.embedder
async def test_model_lock_and_embed_roundtrip() -> None:
    """Cold-load the model, then verify /model and /embed."""
    await _load_model()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        info = await client.get("/model")
        assert info.status_code == 200
        body = info.json()
        assert body == {"name": MODEL_NAME, "dim": EMBEDDING_DIM}

        for task in ("retrieval.passage", "retrieval.query"):
            resp = await client.post(
                "/embed",
                json={"texts": ["hello world"], "task": task},
            )
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            assert payload["model"] == MODEL_NAME
            assert payload["dim"] == EMBEDDING_DIM
            vec = payload["vectors"][0]
            assert len(vec) == EMBEDDING_DIM
            norm = math.sqrt(sum(x * x for x in vec))
            assert math.isclose(norm, 1.0, abs_tol=1e-3)


@pytest.mark.embedder
async def test_healthz_after_load() -> None:
    await _load_model()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "model_loaded": True}
