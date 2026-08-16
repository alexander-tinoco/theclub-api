"""Configuración de la aplicación: 12-factor, todo por variables de entorno.

Aquí están declaradas ya las variables que consumirán las fases posteriores
(base de datos, Kafka, JWT, límites de mesa) aunque la Fase 0 solo use unas
pocas. El objetivo es que ni `.env.example` ni `docker-compose.yml` tengan que
cambiar en cada fase.
"""

from functools import lru_cache
from typing import Annotated, Any, Literal, Self

from pydantic import BeforeValidator, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Valor centinela: arrancar en staging/prod con este secreto es un error fatal.
PLACEHOLDER_SECRET = "cambiame-en-produccion"  # noqa: S105

Environment = Literal["local", "test", "staging", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
SecurityProtocol = Literal["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"]


def _split_csv(value: Any) -> Any:
    """Acepta `a,b,c` además de la lista JSON que pydantic-settings espera."""
    if isinstance(value, str) and not value.strip().startswith("["):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


CsvList = Annotated[list[str], BeforeValidator(_split_csv)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- aplicación ---------------------------------------------------------
    APP_ENV: Environment = "local"
    APP_NAME: str = "theclub-api"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: LogLevel = "INFO"
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: CsvList = ["http://localhost:3000"]

    # --- base de datos (Fase 3) ---------------------------------------------
    DATABASE_URL: str = "postgresql+psycopg://theclub:theclub@localhost:5432/theclub"
    DB_POOL_SIZE: int = Field(default=5, ge=1)

    # --- Kafka / Redpanda (Fase 6) ------------------------------------------
    # Estos cinco campos son los que permiten pasar de Redpanda local a
    # Confluent Cloud cambiando solo el .env, sin tocar código.
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:19092"
    KAFKA_SECURITY_PROTOCOL: SecurityProtocol = "PLAINTEXT"
    KAFKA_SASL_MECHANISM: str | None = None
    KAFKA_SASL_USERNAME: str | None = None
    KAFKA_SASL_PASSWORD: SecretStr | None = None
    KAFKA_TOPIC_PREFIX: str = "theclub"

    # --- autenticación (Fase 4) ---------------------------------------------
    JWT_SECRET: SecretStr = SecretStr(PLACEHOLDER_SECRET)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_SECONDS: int = Field(default=900, ge=60)
    REFRESH_TOKEN_TTL_SECONDS: int = Field(default=60 * 60 * 24 * 14, ge=300)

    # --- juego y outbox (Fases 5 y 6) ---------------------------------------
    OUTBOX_POLL_INTERVAL_MS: int = Field(default=500, ge=50)
    TABLE_MIN_BET_MINOR: int = Field(default=100, ge=1)
    TABLE_MAX_BET_MINOR: int = Field(default=500_000, ge=1)

    @property
    def is_production(self) -> bool:
        return self.APP_ENV in ("staging", "prod")

    @model_validator(mode="after")
    def _check_bet_limits(self) -> Self:
        if self.TABLE_MIN_BET_MINOR > self.TABLE_MAX_BET_MINOR:
            raise ValueError("TABLE_MIN_BET_MINOR no puede superar a TABLE_MAX_BET_MINOR")
        return self

    @model_validator(mode="after")
    def _check_sasl_credentials(self) -> Self:
        needs_sasl = self.KAFKA_SECURITY_PROTOCOL.startswith("SASL")
        if needs_sasl and not (self.KAFKA_SASL_USERNAME and self.KAFKA_SASL_PASSWORD):
            raise ValueError(
                f"KAFKA_SECURITY_PROTOCOL={self.KAFKA_SECURITY_PROTOCOL} exige "
                "KAFKA_SASL_USERNAME y KAFKA_SASL_PASSWORD"
            )
        return self

    @model_validator(mode="after")
    def _check_production_secrets(self) -> Self:
        """En staging/prod la app no arranca con secretos de ejemplo o débiles."""
        if not self.is_production:
            return self
        secret = self.JWT_SECRET.get_secret_value()
        if secret == PLACEHOLDER_SECRET:
            raise ValueError(
                f"JWT_SECRET sigue siendo el valor de ejemplo con APP_ENV={self.APP_ENV}"
            )
        if len(secret) < 32:
            raise ValueError("JWT_SECRET debe tener al menos 32 caracteres fuera de local/test")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instancia única, cacheada. Usable como dependencia de FastAPI."""
    return Settings()
