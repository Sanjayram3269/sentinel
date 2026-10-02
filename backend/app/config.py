"""Environment-driven application settings."""

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables and an optional .env."""

    app_name: str = "SENTINEL Backend"
    app_env: str = "development"
    app_version: str = "0.1.0"
    database_url: str = (
        "postgresql+asyncpg://sentinel:sentinel_dev_password@localhost:15432/sentinel"
    )
    redis_url: str = "redis://localhost:6379/0"
    debug: bool = False
    route_weight_eta: float = Field(default=0.25, ge=0, le=1)
    route_weight_distance: float = Field(default=0.15, ge=0, le=1)
    route_weight_risk: float = Field(default=0.15, ge=0, le=1)
    route_weight_failure: float = Field(default=0.20, ge=0, le=1)
    route_weight_congestion: float = Field(default=0.10, ge=0, le=1)
    route_weight_hazard: float = Field(default=0.15, ge=0, le=1)
    route_failure_threshold: float = Field(default=0.70, ge=0, le=1)
    route_hazard_threshold: float = Field(default=0.80, ge=0, le=1)
    route_deviation_threshold_meters: float = Field(default=100, gt=0)
    route_min_diversity: float = Field(default=0.30, ge=0, le=1)

    @model_validator(mode="after")
    def require_positive_route_weights(self) -> "Settings":
        if sum(
            (
                self.route_weight_eta,
                self.route_weight_distance,
                self.route_weight_risk,
                self.route_weight_failure,
                self.route_weight_congestion,
                self.route_weight_hazard,
            )
        ) <= 0:
            raise ValueError("at least one route scoring weight must be positive")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()