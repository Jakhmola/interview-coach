import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from interview_coach import __version__
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


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
