"""Application configuration: 12-factor, everything via environment variables.

The variables consumed by later phases (database, Kafka, JWT, table limits)
are already declared here even though Phase 0 only uses a few of them. The
goal is that neither `.env.example` nor `docker-compose.yml` has to change
in every phase.
"""

from functools import lru_cache
from typing import Annotated, Any, Literal, Self

from pydantic import BeforeValidator, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinel value: starting up in staging/prod with this secret is a fatal error.
# >= 32 bytes on purpose: below that, PyJWT emits InsecureKeyLengthWarning on
# every HS256 signature, even in local/test.
PLACEHOLDER_SECRET = "change-me-in-production-000000000"  # noqa: S105

Environment = Literal["local", "test", "staging", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
SecurityProtocol = Literal["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"]


def _split_csv(value: Any) -> Any:
    """Accepts `a,b,c` in addition to the JSON list pydantic-settings expects."""
    if isinstance(value, str) and not value.strip().startswith("["):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


CsvList = Annotated[list[str], BeforeValidator(_split_csv)]
CsvSecretList = Annotated[list[SecretStr], BeforeValidator(_split_csv)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- application ---------------------------------------------------------
    APP_ENV: Environment = "local"
    APP_NAME: str = "theclub-api"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: LogLevel = "INFO"
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: CsvList = ["http://localhost:3000"]

    # --- database (Phase 3) ---------------------------------------------
    DATABASE_URL: str = "postgresql+psycopg://theclub:theclub@localhost:5432/theclub"
    DB_POOL_SIZE: int = Field(default=5, ge=1)

    # --- Redis (Phase 8) -------------------------------------------------------
    # Rate limiting state (global and /ws): in-process memory is lost on every
    # redeploy — an attacker who synced their attempts with one would slip
    # past it. With Redis, the state survives the process that uses it.
    REDIS_URL: str = "redis://localhost:6389/0"

    # --- Kafka / Redpanda (Phase 6) ------------------------------------------
    # These five fields are what let you move from local Redpanda to
    # Confluent Cloud by changing only the .env, without touching code.
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:19092"
    KAFKA_SECURITY_PROTOCOL: SecurityProtocol = "PLAINTEXT"
    KAFKA_SASL_MECHANISM: str | None = None
    KAFKA_SASL_USERNAME: str | None = None
    KAFKA_SASL_PASSWORD: SecretStr | None = None
    KAFKA_TOPIC_PREFIX: str = "theclub"

    # --- authentication (Phase 4) ---------------------------------------------
    JWT_SECRET: SecretStr = SecretStr(PLACEHOLDER_SECRET)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_SECONDS: int = Field(default=900, ge=60)
    REFRESH_TOKEN_TTL_SECONDS: int = Field(default=60 * 60 * 24 * 14, ge=300)
    # JWT_SECRET rotation (Phase 8): the old secret moves here when rotated.
    # Signing only ever uses JWT_SECRET; verification tries JWT_SECRET and
    # then these, in order, so an access token issued seconds before a
    # rotation doesn't invalidate active sessions — a secret only needs to
    # stay here while unexpired tokens signed with it still exist (at most
    # ACCESS_TOKEN_TTL_SECONDS after it's retired).
    JWT_PREVIOUS_SECRETS: CsvSecretList = []

    # --- game and outbox (Phases 5 and 6) ---------------------------------------
    OUTBOX_POLL_INTERVAL_MS: int = Field(default=500, ge=50)
    OUTBOX_RETENTION_HOURS: int = Field(default=24 * 7, ge=1)
    OUTBOX_CLEANUP_INTERVAL_S: int = Field(default=3600, ge=60)
    TABLE_MIN_BET_MINOR: int = Field(default=100, ge=1)
    TABLE_MAX_BET_MINOR: int = Field(default=500_000, ge=1)
    IDEMPOTENCY_KEY_TTL_HOURS: int = Field(default=24, ge=1)

    # --- WebSocket (Phase 7) --------------------------------------------------
    WS_MAX_CONNECTIONS: int = Field(default=1000, ge=1)
    WS_HEARTBEAT_INTERVAL_S: float = Field(default=20.0, gt=0)
    WS_HEARTBEAT_TIMEOUT_S: float = Field(default=45.0, gt=0)
    WS_CONNECT_RATE_LIMIT_ATTEMPTS: int = Field(default=10, ge=1)
    WS_CONNECT_RATE_LIMIT_WINDOW_S: float = Field(default=60.0, gt=0)

    # --- hardening (Phase 8) ---------------------------------------------------
    MAX_REQUEST_BODY_BYTES: int = Field(default=1_000_000, ge=1024)

    @property
    def is_production(self) -> bool:
        return self.APP_ENV in ("staging", "prod")

    @model_validator(mode="after")
    def _check_bet_limits(self) -> Self:
        if self.TABLE_MIN_BET_MINOR > self.TABLE_MAX_BET_MINOR:
            raise ValueError("TABLE_MIN_BET_MINOR cannot exceed TABLE_MAX_BET_MINOR")
        return self

    @model_validator(mode="after")
    def _check_sasl_credentials(self) -> Self:
        needs_sasl = self.KAFKA_SECURITY_PROTOCOL.startswith("SASL")
        if needs_sasl and not (self.KAFKA_SASL_USERNAME and self.KAFKA_SASL_PASSWORD):
            raise ValueError(
                f"KAFKA_SECURITY_PROTOCOL={self.KAFKA_SECURITY_PROTOCOL} requires "
                "KAFKA_SASL_USERNAME and KAFKA_SASL_PASSWORD"
            )
        return self

    @model_validator(mode="after")
    def _check_production_secrets(self) -> Self:
        """In staging/prod the app doesn't start up with example or weak secrets.

        Includes `JWT_PREVIOUS_SECRETS`: whoever knows it can forge a token
        that `decode_access_token` accepts just like the current one — it's
        not a "read-only" secret, so it demands the same strength.
        """
        if not self.is_production:
            return self
        for secret_value in (
            self.JWT_SECRET,
            *self.JWT_PREVIOUS_SECRETS,
        ):
            secret = secret_value.get_secret_value()
            if secret == PLACEHOLDER_SECRET:
                raise ValueError(
                    f"JWT_SECRET/JWT_PREVIOUS_SECRETS is still the example value "
                    f"with APP_ENV={self.APP_ENV}"
                )
            if len(secret) < 32:
                raise ValueError(
                    "JWT_SECRET and JWT_PREVIOUS_SECRETS must be at least 32 "
                    "characters outside local/test"
                )
        return self

    @model_validator(mode="after")
    def _check_cors_wildcard_in_production(self) -> Self:
        """`CORSMiddleware` runs with `allow_credentials=True` — a wildcard
        origin combined with credentials is exactly the combination browsers
        already reject, and if it somehow "worked" it would be a real hole.
        Better for the app to refuse to start than to ship a broken (or
        wide-open) CORS setup discovered in production.
        """
        if self.is_production and "*" in self.CORS_ORIGINS:
            raise ValueError("CORS_ORIGINS cannot be '*' outside local/test")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Single, cached instance. Usable as a FastAPI dependency."""
    return Settings()
