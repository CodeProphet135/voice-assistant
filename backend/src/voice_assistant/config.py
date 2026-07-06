from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    database_url: str = "postgresql+asyncpg://va:va@localhost:5432/voice_assistant"

    # Observability
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "voice-assistant"


settings = Settings()
