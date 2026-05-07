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

logging.basicConfig(
    level=settings.log_level,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: open the checkpointer connection on this loop and compile
    # both graphs once. The compiled graphs are stashed on app.state so
    # the route layer can pick them up without re-compiling per request.
    async with open_checkpointer(settings.graph_db_path) as checkpointer:
        app.state.prep_graph = build_prep_graph()
        app.state.interview_graph = build_interview_graph(checkpointer)
        try:
            yield
        finally:
            # Shutdown: drop cached MCP client (if any).
            from interview_coach.mcp.client import reset_client

            await reset_client()


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
