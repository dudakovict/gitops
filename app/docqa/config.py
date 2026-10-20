"""Runtime configuration. Everything comes from the environment; nothing is read from files."""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOCQA_", extra="ignore")

    env: str = Field(default="dev", description="Deployment environment name, used as a telemetry attribute.")

    # Connections. DATABASE_URL has no prefix on purpose: it is the conventional name.
    database_url: SecretStr = Field(validation_alias="DATABASE_URL")   # contains the DB password
    google_api_key: SecretStr = Field(validation_alias="GOOGLE_API_KEY")

    # Bearer token required on every endpoint except health checks.
    api_token: SecretStr

    # Models (Gemini API; both have a free tier)
    llm_model: str = "gemini-3.6-flash"   # free tier; one generation behind the newest, which tends to return 503 "high demand"
    llm_max_tokens: int = 4096
    embedding_model: str = "gemini-embedding-2"
    embedding_dim: int = 1536      # requested output size; pgvector's HNSW index allows up to 2000
    embedding_batch_size: int = 100

    # Chunking and retrieval
    chunk_size: int = 1200
    chunk_overlap: int = 150
    top_k: int = 8

    # Uploads
    max_upload_mb: int = 200

    # Worker
    worker_poll_seconds: float = 2.0
    job_max_attempts: int = 3
    job_stale_minutes: int = 15

    # Telemetry: standard OTEL_* variables are read by the SDK itself.
    otel_enabled: bool = True

    @field_validator("google_api_key", "api_token")
    @classmethod
    def _non_empty_secret(cls, v: SecretStr, info) -> SecretStr:
        if not v.get_secret_value().strip():
            raise ValueError(f"{info.field_name} must not be empty")
        return v

    @field_validator("database_url", mode="before")
    @classmethod
    def _require_psycopg_async(cls, v):
        raw = v.get_secret_value() if isinstance(v, SecretStr) else str(v)
        if raw.startswith("postgresql://"):
            raw = "postgresql+psycopg://" + raw.removeprefix("postgresql://")
        if not raw.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must be a postgresql+psycopg:// URL")
        return SecretStr(raw)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
