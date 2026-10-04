"""Environment-driven application settings."""

from functools import lru_cache
from pathlib import Path

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

    # Explicit browser-origin allowlist. A wildcard is deliberately not
    # supported: the event stream and mission APIs are not designed to be
    # callable from an arbitrary page. Configure additional origins with
    # CORS_ORIGINS as a JSON list, for example
    # CORS_ORIGINS='["http://localhost:5173","http://127.0.0.1:5173"]'.
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    # CORSMiddleware only negotiates preflight for ordinary HTTP requests.
    # The mission event stream is a WebSocket upgrade and is not governed
    # by this setting; serve the frontend through a proxy for that route.
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
    # Real road-graph routing (Phase 5). A coordinate further than this from the
    # nearest road node is refused rather than snapped.
    route_max_snap_meters: float = Field(default=250.0, gt=0)
    # Upper bound on generated candidates, including the shortest path.
    route_max_candidates: int = Field(default=4, ge=1, le=20)
    # Cost multiplier applied to already-used edges when generating an
    # alternative route, so alternatives leave the primary's streets.
    route_alternative_penalty: float = Field(default=2.0, ge=1.0)
    # MINIMIZE_TRAVEL_TIME uses stored length_m / speed_limit_kmh;
    # MINIMIZE_DISTANCE uses stored length_m only.
    route_objective: str = Field(default="MINIMIZE_TRAVEL_TIME")
    # Road network reference data. The key selects which imported network
    # routing and resilience use; no network is created at startup and an
    # absent network leaves the existing development behaviour untouched.
    road_network_key: str = "osm_urban_v1"
    # Importing OSM data is an explicit operator action and is never
    # performed by application startup or by an Alembic migration.
    road_network_import_enabled: bool = False
    # AI intelligence layer. Disabled by default: with no imported road network
    # and no trained artifact the layer has nothing to add, and every prediction
    # resolves to the deterministic predictors instead.
    sentinel_ai_enabled: bool = False
    # Absolute path to the directory holding the trained artifact
    # (sentinel_models.pkl). A relative path is refused at load time, because an
    # artifact resolved against the working directory would change meaning with
    # the process launch directory. Unset or absent means deterministic
    # baseline_fallback, which is the state of this repository.
    ai_model_path: str | None = None
    # Reported alongside AI predictions so a stored record can be traced to the
    # artifact version that produced it.
    ai_model_version: str = "none"
    sumo_binary: str = "sumo"
    sumo_config_path: str | None = None
    sumo_network_id: str | None = None
    simulation_development_network_id: str = "sentinel-development-test-network"
    simulation_fixture_path: str = str(
        Path(__file__).resolve().parents[1]
        / "simulation"
        / "scenarios"
        / "development_fixture.json"
    )
    simulation_max_signal_preemption_seconds: int = Field(default=30, ge=1, le=300)
    simulation_approach_distance_meters: float = Field(default=150, gt=0)

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