import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from interview_coach import __version__
from interview_coach.agents.graph import (
    build_interview_graph,
    build_prep_graph,
    open_checkpointer,
)
from interview_coach.api.auth import router as auth_router
from interview_coach.api.documents import router as documents_router
from interview_coach.api.jobs import router as jobs_router
from interview_coach.api.sessions import router as sessions_router
from interview_coach.config import settings
from interview_coach.observability.langfuse import flush_langfuse, langfuse_enabled
from interview_coach.rag.client import EmbeddingClient
from interview_coach.rag.model_lock import assert_embedder_model

logging.basicConfig(
    level=settings.log_level,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: open the checkpointer connection on this loop and compile
    # both graphs once. The compiled graphs are stashed on app.state so
    # the route layer can pick them up without re-compiling per request.
    async with open_checkpointer(settings.graph_db_path) as checkpointer:
        # Phase 21: both graphs share the same AsyncSqliteSaver instance.
        # prep_graph uses thread_id "prep:{user_id}:{job_id}";
        # interview_graph uses "{session_id}:turn_{n}". Distinct prefixes
        # mean the two namespaces never collide.
        app.state.prep_graph = build_prep_graph(checkpointer)
        app.state.interview_graph = build_interview_graph(checkpointer)

        # Phase 17: stand up the embedder client and lock the model name +
        # dim. Mismatch on either ⇒ refuse to boot — existing
        # `grounding_chunks` rows assume Jina v3 / 1024-d.
        embedding_client = EmbeddingClient.from_settings()
        await assert_embedder_model(embedding_client)
        app.state.embedding_client = embedding_client

        if langfuse_enabled():
            logger.info("Langfuse tracing is enabled for this api process")
        try:
            yield
        finally:
            # Shutdown: flush Langfuse + close embedder HTTP client.
            await flush_langfuse()
            await embedding_client.aclose()


app = FastAPI(title="interview-coach API", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(jobs_router)
app.include_router(sessions_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
