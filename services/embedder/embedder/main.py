"""FastAPI app for the embedder sidecar.

Owns a single `SentenceTransformer("jinaai/jina-embeddings-v3",
trust_remote_code=True)` instance. Mirrors the encode call that lived in
`interview_coach.rag.embeddings` before Phase 17 (same task, same
normalization, same numpy conversion) so vectors stay bit-for-bit
identical.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from embedder.schemas import (
    EmbedRequest,
    EmbedResponse,
    HealthResponse,
    ModelInfo,
)

logger = logging.getLogger(__name__)

MODEL_NAME = "jinaai/jina-embeddings-v3"
EMBEDDING_DIM = 1024


def _resolve_thread_cap() -> int:
    raw = os.environ.get("EMBED_THREADS")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    cpu = os.cpu_count() or 4
    return max(1, cpu - 4)


_THREAD_CAP = _resolve_thread_cap()
# Apply BEFORE torch / HF import so the BLAS / OMP backends pick these up.
os.environ.setdefault("OMP_NUM_THREADS", str(_THREAD_CAP))
os.environ.setdefault("MKL_NUM_THREADS", str(_THREAD_CAP))
os.environ.setdefault("OPENBLAS_NUM_THREADS", str(_THREAD_CAP))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


class _ModelHolder:
    """Mutable singleton state. Set during lifespan startup."""

    model: Any | None = None
    load_error: BaseException | None = None


_HOLDER = _ModelHolder()


def _load_model_sync() -> Any:
    """Blocking model load — invoked from a thread via run_in_executor.

    Threading model: ``EMBED_THREADS`` (and the BLAS env vars we set
    alongside it at module import) caps the *intra-op* parallelism — how
    many threads each BLAS matmul uses. We pin *inter-op* threads to 1
    so torch can't also dispatch independent ops in parallel; pipeline
    parallelism doesn't help a single-model sidecar and just lets the
    process fan out N×M threads under load, which the OS scheduler then
    has to fight through the cgroup ``cpus`` quota. Pinning interop=1
    makes the wall-clock spike at the start of each ``encode()`` match
    the cgroup budget instead of overshooting it.

    ``set_num_interop_threads`` raises ``RuntimeError`` if torch has
    already started its thread pool (e.g. a previous import); a no-op
    in that path is correct because the previously-set value sticks.
    """
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(_THREAD_CAP)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass
    return SentenceTransformer(MODEL_NAME, trust_remote_code=True)


async def _load_model() -> None:
    logger.info("Loading %s (thread cap=%d)", MODEL_NAME, _THREAD_CAP)
    loop = asyncio.get_running_loop()
    try:
        _HOLDER.model = await loop.run_in_executor(None, _load_model_sync)
        logger.info("Model loaded")
    except BaseException as e:  # noqa: BLE001
        _HOLDER.load_error = e
        logger.exception("Model load failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Kick load in the background so /healthz can report progress.
    task = asyncio.create_task(_load_model())
    try:
        yield
    finally:
        if not task.done():
            task.cancel()


app = FastAPI(title="embedder", lifespan=lifespan)


@app.get("/healthz", response_model=HealthResponse)
async def healthz() -> JSONResponse:
    loaded = _HOLDER.model is not None
    body = HealthResponse(ok=loaded, model_loaded=loaded).model_dump()
    status = 200 if loaded else 503
    return JSONResponse(body, status_code=status)


@app.get("/model", response_model=ModelInfo)
async def model_info() -> ModelInfo:
    if _HOLDER.model is None:
        raise HTTPException(status_code=503, detail="model not loaded yet")
    return ModelInfo(name=MODEL_NAME, dim=EMBEDDING_DIM)


@app.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest) -> EmbedResponse:
    model = _HOLDER.model
    if model is None:
        raise HTTPException(status_code=503, detail="model not loaded yet")
    loop = asyncio.get_running_loop()
    texts = req.texts
    task = req.task
    vectors = await loop.run_in_executor(
        None,
        lambda: model.encode(
            texts,
            task=task,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ),
    )
    return EmbedResponse(
        vectors=[v.tolist() for v in vectors],
        model=MODEL_NAME,
        dim=EMBEDDING_DIM,
    )
