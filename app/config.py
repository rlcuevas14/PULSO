from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default inseguro del secreto de sesión. Si aparece en producción (debug=False),
# el arranque DEBE abortar — un secret_key conocido permite forjar cookies de sesión.
_INSECURE_SECRET = "dev-secret-change-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # NOTA (CFG-1): la variable que MANDA es DATABASE_URL (URL completa de SQLAlchemy).
    # El docker-compose la arma a partir de DB_PASSWORD; .env.example documenta DB_PASSWORD
    # como conveniencia para ese armado, pero la app lee DATABASE_URL. Si defines ambas,
    # DATABASE_URL gana. Ver .env.example.
    database_url: str = "postgresql+asyncpg://pulso:pulso@db/pulso"
    secret_key: str = _INSECURE_SECRET
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

    @model_validator(mode="after")
    def _fail_fast_on_insecure_secret(self) -> "Settings":
        """SEC-05 / CFG-1: aborta el arranque si corremos en prod con el secreto default.

        En producción (debug=False) un secret_key conocido permite forjar cookies de
        sesión firmadas → cualquiera podría suplantar a un administrador. Preferimos
        fallar ruidoso al arrancar antes que servir tráfico inseguro.
        """
        if not self.debug and self.secret_key == _INSECURE_SECRET:
            raise ValueError(
                "SECRET_KEY usa el valor default inseguro en producción (DEBUG=false). "
                "Genera uno con: python -c \"import secrets; print(secrets.token_hex(32))\" "
                "y configúralo en la variable de entorno SECRET_KEY."
            )
        return self


settings = Settings()
