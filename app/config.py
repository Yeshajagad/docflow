"""
Env-driven configuration.

Why a single Settings object instead of scattering os.getenv() calls
everywhere: every other module (database, scheduler, heartbeat) imports
`settings` and reads typed, validated values. If DATABASE_URL is missing
or HEARTBEAT_TTL_SECONDS isn't a valid int, we find out at startup with a
clear error - not three files deep during a request.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database ---
    database_url: str = "sqlite:///./docflow.db"

    # --- Redis (heartbeats). None => in-memory fallback used instead. ---
    redis_url: str | None = None

    # --- Scheduling ---
    default_scheduling_policy: str = "best_fit"  # first_fit | best_fit | fair_share

    # --- Heartbeats / failover ---
    heartbeat_ttl_seconds: int = 15
    reconcile_interval_seconds: int = 5
    max_retries: int = 3

    # --- Worker identity (each worker process/container sets its own) ---
    node_id: str = "node-1"


settings = Settings()
