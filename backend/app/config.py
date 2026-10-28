"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the API process.

    Secrets and deployment-specific values are intentionally supplied through the
    environment rather than committed source files.
    """

    app_name: str = "EvidenceForge API"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://localhost:5432/evidenceforge"

    # Opt-in generation provider (DISABLED by default). Local-only; see
    # app/questionnaires/generation/local_ollama_provider.py and
    # docs/llm-generation-decision-2026-10-25.md.
    generation_provider_enabled: bool = False
    generation_provider_base_url: str = ""
    generation_provider_model_id: str = ""
    generation_provider_timeout_seconds: float = 30.0
    generation_provider_max_retries: int = 2
    generation_provider_allow_private_hosts: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
