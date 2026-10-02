"""Environment-driven application settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables and an optional .env."""

    app_name: str = "SENTINEL Backend"
    app_env: str = "development"
    app_version: str = "0.1.0"
    database_url: str = (
        "postgresql+asyncpg://sentinel:sentinel_dev_password@localhost:5432/sentinel"
    )
    redis_url: str = "redis://localhost:6379/0"
    debug: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()