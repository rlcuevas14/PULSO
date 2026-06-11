from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://pulso:pulso@db/pulso"
    secret_key: str = "dev-secret-change-in-production"
    debug: bool = False

    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    resend_api_key: str = ""

    job_poll_interval_seconds: int = 10
    job_lease_seconds: int = 300


settings = Settings()
