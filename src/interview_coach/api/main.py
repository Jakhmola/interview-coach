import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from interview_coach import __version__
from interview_coach.api.auth import router as auth_router
from interview_coach.api.documents import router as documents_router
from interview_coach.api.jobs import router as jobs_router
from interview_coach.config import settings

logging.basicConfig(
    level=settings.log_level,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)

app = FastAPI(title="interview-coach API", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(jobs_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
