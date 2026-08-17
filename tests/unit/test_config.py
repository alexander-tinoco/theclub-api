import pytest
from pydantic import ValidationError

from app.config import PLACEHOLDER_SECRET, Settings

pytestmark = pytest.mark.unit


def test_cors_origins_accepts_a_comma_separated_list() -> None:
    settings = Settings(CORS_ORIGINS="http://a.test, http://b.test")

    assert settings.CORS_ORIGINS == ["http://a.test", "http://b.test"]


def test_local_starts_up_with_the_example_secret() -> None:
    settings = Settings(APP_ENV="local")

    assert settings.JWT_SECRET.get_secret_value() == PLACEHOLDER_SECRET
    assert settings.is_production is False


def test_prod_rejects_the_example_secret() -> None:
    with pytest.raises(ValidationError, match="example value"):
        Settings(APP_ENV="prod")


def test_prod_rejects_a_short_secret() -> None:
    with pytest.raises(ValidationError, match="32 characters"):
        Settings(APP_ENV="prod", JWT_SECRET="short")


def test_prod_accepts_a_strong_secret() -> None:
    settings = Settings(APP_ENV="prod", JWT_SECRET="x" * 48)

    assert settings.is_production is True


def test_sasl_requires_credentials() -> None:
    with pytest.raises(ValidationError, match="KAFKA_SASL_USERNAME"):
        Settings(KAFKA_SECURITY_PROTOCOL="SASL_SSL")


def test_sasl_with_credentials_is_valid() -> None:
    settings = Settings(
        KAFKA_SECURITY_PROTOCOL="SASL_SSL",
        KAFKA_SASL_MECHANISM="PLAIN",
        KAFKA_SASL_USERNAME="key",
        KAFKA_SASL_PASSWORD="secret",
    )

    assert settings.KAFKA_SASL_USERNAME == "key"


def test_inconsistent_table_limits() -> None:
    with pytest.raises(ValidationError, match="TABLE_MIN_BET_MINOR"):
        Settings(TABLE_MIN_BET_MINOR=1000, TABLE_MAX_BET_MINOR=100)


def test_the_secret_never_leaks_when_serialized() -> None:
    settings = Settings(APP_ENV="prod", JWT_SECRET="x" * 48)

    assert "x" * 48 not in str(settings)
    assert "x" * 48 not in repr(settings)


def test_prod_rejects_cors_wildcard() -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(APP_ENV="prod", JWT_SECRET="x" * 48, CORS_ORIGINS="*")


def test_local_accepts_cors_wildcard() -> None:
    settings = Settings(APP_ENV="local", CORS_ORIGINS="*")

    assert settings.CORS_ORIGINS == ["*"]


def test_jwt_previous_secrets_empty_by_default() -> None:
    settings = Settings()

    assert settings.JWT_PREVIOUS_SECRETS == []


def test_jwt_previous_secrets_accepts_a_comma_separated_list() -> None:
    settings = Settings(JWT_PREVIOUS_SECRETS="secret-one-of-at-least-32-bytes,secret-two-of-32b")

    assert [s.get_secret_value() for s in settings.JWT_PREVIOUS_SECRETS] == [
        "secret-one-of-at-least-32-bytes",
        "secret-two-of-32b",
    ]


def test_prod_rejects_an_example_previous_secret() -> None:
    with pytest.raises(ValidationError, match="example value"):
        Settings(APP_ENV="prod", JWT_SECRET="x" * 48, JWT_PREVIOUS_SECRETS=PLACEHOLDER_SECRET)


def test_prod_rejects_a_short_previous_secret() -> None:
    with pytest.raises(ValidationError, match="32 characters"):
        Settings(APP_ENV="prod", JWT_SECRET="x" * 48, JWT_PREVIOUS_SECRETS="short")
