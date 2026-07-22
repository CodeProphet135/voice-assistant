from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root .env is the single source of truth (see .env.example); resolved
# absolutely so it loads regardless of the process's cwd (e.g. `cd backend`
# in the dev-backend Makefile target).
_REPO_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_REPO_ROOT_ENV, extra="ignore")

    # LLM
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    openai_reasoning_effort: str = "minimal"
    openai_max_output_tokens: int = 1024

    # Speech
    deepgram_api_key: str = ""
    deepgram_stt_model: str = "nova-3"
    deepgram_tts_model: str = "aura-2-thalia-en"

    # Database
    database_url: str = "postgresql+asyncpg://va:va@localhost:5433/voice_assistant"

    # Event recording: batch-write retry/backoff, and self-disable + periodic
    # reachability re-probe after repeated batch-write failures (mirrors the
    # one-shot probe EventRecorder.start() already does).
    event_write_max_attempts: int = 3
    event_write_retry_base_seconds: float = 0.2
    event_recorder_max_consecutive_failures: int = 3
    event_recorder_reprobe_seconds: float = 5.0

    # Observability
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "voice-assistant"


settings = Settings()
