from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://pulso:pulso@db/pulso"
    secret_key: str = "dev-secret-change-in-production"
    debug: bool = False

    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    resend_api_key: str = ""

    sentry_client_secret: str = ""
    github_webhook_secret: str = ""
    # Token de la API de Sentry (Issue&Event: Read [+ Write para resolver]) y org slug,
    # para traer el stack trace de un incidente y resolverlo en Sentry desde el MCP.
    sentry_api_token: str = ""
    sentry_org: str = ""

    job_poll_interval_seconds: int = 10
    job_lease_seconds: int = 300


settings = Settings()
