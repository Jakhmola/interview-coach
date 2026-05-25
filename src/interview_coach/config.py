from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = two levels above this file (src/interview_coach/config.py).
# Anchoring the env_file path here keeps Settings() consistent regardless of CWD
# (pytest from subdirs, IDE runners, scripts run from /tmp, etc.).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_PROJECT_ROOT / ".env", extra="ignore")

    log_level: str = "INFO"
    api_port: int = 8000
    database_url: str = (
        "postgresql+asyncpg://interview_coach:interview_coach@db:5432/interview_coach"
    )

    jwt_secret: str = "change-me-in-prod"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    tavily_api_key: str | None = None

    # OpenAI-compatible LLM endpoint. In compose, the api service reaches the
    # `llama` container over the docker network; the .env file overrides this
    # to localhost for host-side runs (pytest, scripts).
    llm_base_url: str = "http://llama:8080/v1"
    llm_api_key: str | None = None  # ignored by local servers; required by OpenAI proper
    model_name: str = "qwen3-8b"

    # SQLite file holding the LangGraph checkpointer state for the
    # interview_graph. In compose, this lives on the `graph_data` named
    # volume mounted at /data; on the host (pytest, scripts) it can be
    # overridden via .env to a workspace path or `:memory:`.
    graph_db_path: str = "/data/graph_checkpoints.sqlite"

    # Phase 17: embeddings sidecar. `embedder_url` is the base URL the
    # api uses to reach the service; in compose it's `http://embedder:8001`.
    # `embedder_timeout_s` covers worst-case cold-load on first request.
    embedder_url: str = "http://embedder:8001"
    embedder_timeout_s: float = 60.0
    embedder_retries: int = 3

    # Phase 24: hybrid retrieval (BM25 + vector with RRF). `retrieval_mode`
    # is the kill-switch — set to `"vector"` to fall back to pure pgvector
    # search; `"hybrid"` (default) fuses BM25 and vector via Reciprocal
    # Rank Fusion. `rrf_k=60` is the literature default; `hybrid_candidate_k`
    # is how deep each branch reads before fusion.
    retrieval_mode: str = "hybrid"
    rrf_k: int = 60
    hybrid_candidate_k: int = 20


settings = Settings()
