from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    log_level: str = "INFO"
    api_port: int = 8000
    database_url: str = (
        "postgresql+asyncpg://interview_coach:interview_coach@db:5432/interview_coach"
    )


settings = Settings()
