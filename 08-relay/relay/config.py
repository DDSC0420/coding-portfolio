"""Runtime configuration, read from environment variables (and an optional .env file).

Every service reads only the settings it needs. Defaults are safe for local
development: everything binds to localhost and demo-only features are off.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineSettings(BaseSettings):
    """Settings for the control API and the workers (they share the engine database)."""

    model_config = SettingsConfigDict(env_prefix="RELAY_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://relay:relay@127.0.0.1:55432/relay_engine"

    # Leases (database time). A killed worker's step is recovered roughly
    # lease_seconds + recovery_interval_seconds after its last renewal.
    lease_seconds: float = Field(default=15.0, gt=0, le=600)
    recovery_interval_seconds: float = Field(default=2.0, gt=0, le=60)
    recovery_batch_size: int = Field(default=50, ge=1, le=1000)

    # Retries: exponential backoff with equal jitter (relay/engine/retries.py)
    retry_base_seconds: float = Field(default=1.0, gt=0, le=60)
    retry_max_delay_seconds: float = Field(default=30.0, gt=0, le=600)

    # Worker loop
    worker_name: str = "worker-local"
    worker_concurrency: int = Field(default=4, ge=1, le=32)
    poll_interval_seconds: float = Field(default=0.5, gt=0, le=30)
    worker_heartbeat_seconds: float = Field(default=2.0, gt=0, le=60)
    worker_offline_after_seconds: float = Field(default=10.0, gt=0)

    # Outbound HTTP to the demo CRM
    crm_base_url: str = "http://127.0.0.1:8100"
    crm_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    # Bounds
    max_batch_size: int = Field(default=500, ge=1, le=5000)
    max_request_bytes: int = Field(default=256_000, ge=1_000)

    # Business rule: the approval gate (relay/steps/approval.py)
    approval_threshold_gbp: int = 50_000


class CrmSettings(BaseSettings):
    """Settings for the demo CRM service, which has its own database."""

    model_config = SettingsConfigDict(env_prefix="CRM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://relay:relay@127.0.0.1:55433/relay_crm"
    max_request_bytes: int = Field(default=64_000, ge=1_000)
    # Enables the /demo/faults endpoints. Never enable outside a local demo.
    demo_mode: bool = False


@lru_cache
def engine_settings() -> EngineSettings:
    return EngineSettings()


@lru_cache
def crm_settings() -> CrmSettings:
    return CrmSettings()
