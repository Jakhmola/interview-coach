"""Numeric parity test: real Jina v3 in-process vs the embedder service.

Gated behind the `embedder` marker because it cold-loads the ~500MB
model. Run with:

    uv run pytest -m embedder services/embedder/tests/test_parity.py

The Phase 17 contract is that vectors produced over the wire are equal
(within a tight float tolerance) to vectors produced by the same model
call in-process, so existing `grounding_chunks` rows stay valid and any
future swap-in must hit the same bar. JSON serialization can introduce
ulp-level drift on float64 → text → float64 conversion, so we allow
`atol=1e-6`.
"""

from __future__ import annotations

import math
from typing import Any

import httpx
import numpy as np
import pytest
from embedder.main import _HOLDER, EMBEDDING_DIM, MODEL_NAME, _load_model, app

FIXTURE_PASSAGES = [
    "I led the migration of our payment service from Python 3.10 to 3.13.",
    "Designed and shipped a vector retrieval pipeline backed by pgvector.",
    "Mentored two junior engineers through their first incident review.",
    "Wrote the public-facing changelog and ran the launch livestream.",
    "Built a feature-flag system that survived a 6x traffic spike.",
]
FIXTURE_QUERY = "Tell me about a time you led a technical migration."


def _encode_direct(texts: list[str], task: str) -> np.ndarray:
    model: Any = _HOLDER.model
    assert model is not None, "load the model before calling _encode_direct"
    return model.encode(
        texts,
        task=task,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )


@pytest.mark.embedder
async def test_parity_passages() -> None:
    await _load_model()
    direct = _encode_direct(FIXTURE_PASSAGES, "retrieval.passage")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/embed",
            json={"texts": FIXTURE_PASSAGES, "task": "retrieval.passage"},
        )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["model"] == MODEL_NAME
    assert payload["dim"] == EMBEDDING_DIM

    wire = np.array(payload["vectors"], dtype=np.float32)
    assert wire.shape == direct.shape

    delta = np.abs(wire - direct.astype(np.float32)).max()
    assert delta < 1e-6, f"parity drift {delta} exceeds 1e-6"

    # L2-norm sanity: both should be unit vectors.
    for row in wire:
        assert math.isclose(float(np.linalg.norm(row)), 1.0, abs_tol=1e-3)


@pytest.mark.embedder
async def test_parity_query() -> None:
    await _load_model()
    direct = _encode_direct([FIXTURE_QUERY], "retrieval.query")[0]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/embed",
            json={"texts": [FIXTURE_QUERY], "task": "retrieval.query"},
        )
    assert resp.status_code == 200, resp.text
    wire = np.array(resp.json()["vectors"][0], dtype=np.float32)

    assert wire.shape == direct.shape
    delta = np.abs(wire - direct.astype(np.float32)).max()
    assert delta < 1e-6, f"parity drift {delta} exceeds 1e-6"


@pytest.mark.embedder
async def test_query_passage_tasks_produce_different_vectors() -> None:
    """The whole point of Jina v3's task LoRA is that retrieval.query and
    retrieval.passage produce *different* embeddings for the same text.
    A regression that silently lost the `task` argument would be
    impossible to detect from the L2 norm alone — this test guards it.
    """
    await _load_model()
    text = "Designed and shipped a vector retrieval pipeline backed by pgvector."
    v_q = _encode_direct([text], "retrieval.query")[0]
    v_p = _encode_direct([text], "retrieval.passage")[0]
    # Both are unit-norm, so cosine == dot.
    cos = float(np.dot(v_q, v_p))
    # They should be similar (same text!) but NOT identical.
    assert 0.5 < cos < 0.999, f"query/passage adapters look broken (cos={cos:.4f})"
