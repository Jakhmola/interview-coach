from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    log_level: str = "INFO"
    api_port: int = 8000
    database_url: str = (
        "postgresql+asyncpg://interview_coach:interview_coach@db:5432/interview_coach"
    )

    jwt_secret: str = "change-me-in-prod"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    tavily_api_key: str | None = None

    # OpenAI-compatible LLM endpoint (we use llama.cpp's llama-server locally;
    # any OpenAI-compatible server works: vLLM, LM Studio, OpenAI proper, etc.)
    llm_base_url: str = "http://host.docker.internal:8080/v1"
    llm_api_key: str | None = None  # ignored by local servers; required by OpenAI proper
    model_name: str = "qwen3-8b"


settings = Settings()
