import pytest
from pydantic import ValidationError

from app.config import PLACEHOLDER_SECRET, Settings

pytestmark = pytest.mark.unit


def test_cors_origins_acepta_lista_separada_por_comas() -> None:
    settings = Settings(CORS_ORIGINS="http://a.test, http://b.test")

    assert settings.CORS_ORIGINS == ["http://a.test", "http://b.test"]


def test_local_arranca_con_el_secreto_de_ejemplo() -> None:
    settings = Settings(APP_ENV="local")

    assert settings.JWT_SECRET.get_secret_value() == PLACEHOLDER_SECRET
    assert settings.is_production is False


def test_prod_rechaza_el_secreto_de_ejemplo() -> None:
    with pytest.raises(ValidationError, match="valor de ejemplo"):
        Settings(APP_ENV="prod")


def test_prod_rechaza_un_secreto_corto() -> None:
    with pytest.raises(ValidationError, match="32 caracteres"):
        Settings(APP_ENV="prod", JWT_SECRET="corto")


def test_prod_acepta_un_secreto_fuerte() -> None:
    settings = Settings(APP_ENV="prod", JWT_SECRET="x" * 48)

    assert settings.is_production is True


def test_sasl_exige_credenciales() -> None:
    with pytest.raises(ValidationError, match="KAFKA_SASL_USERNAME"):
        Settings(KAFKA_SECURITY_PROTOCOL="SASL_SSL")


def test_sasl_con_credenciales_es_valido() -> None:
    settings = Settings(
        KAFKA_SECURITY_PROTOCOL="SASL_SSL",
        KAFKA_SASL_MECHANISM="PLAIN",
        KAFKA_SASL_USERNAME="clave",
        KAFKA_SASL_PASSWORD="secreto",
    )

    assert settings.KAFKA_SASL_USERNAME == "clave"


def test_limites_de_mesa_incoherentes() -> None:
    with pytest.raises(ValidationError, match="TABLE_MIN_BET_MINOR"):
        Settings(TABLE_MIN_BET_MINOR=1000, TABLE_MAX_BET_MINOR=100)


def test_el_secreto_no_se_filtra_al_serializar() -> None:
    settings = Settings(APP_ENV="prod", JWT_SECRET="x" * 48)

    assert "x" * 48 not in str(settings)
    assert "x" * 48 not in repr(settings)


def test_prod_rechaza_cors_comodin() -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(APP_ENV="prod", JWT_SECRET="x" * 48, CORS_ORIGINS="*")


def test_local_acepta_cors_comodin() -> None:
    settings = Settings(APP_ENV="local", CORS_ORIGINS="*")

    assert settings.CORS_ORIGINS == ["*"]


def test_jwt_previous_secrets_vacio_por_defecto() -> None:
    settings = Settings()

    assert settings.JWT_PREVIOUS_SECRETS == []


def test_jwt_previous_secrets_acepta_lista_separada_por_comas() -> None:
    settings = Settings(JWT_PREVIOUS_SECRETS="secreto-uno-de-al-menos-32-bytes,secreto-dos-de-32b")

    assert [s.get_secret_value() for s in settings.JWT_PREVIOUS_SECRETS] == [
        "secreto-uno-de-al-menos-32-bytes",
        "secreto-dos-de-32b",
    ]


def test_prod_rechaza_un_secreto_previo_de_ejemplo() -> None:
    with pytest.raises(ValidationError, match="valor de ejemplo"):
        Settings(APP_ENV="prod", JWT_SECRET="x" * 48, JWT_PREVIOUS_SECRETS=PLACEHOLDER_SECRET)


def test_prod_rechaza_un_secreto_previo_corto() -> None:
    with pytest.raises(ValidationError, match="32 caracteres"):
        Settings(APP_ENV="prod", JWT_SECRET="x" * 48, JWT_PREVIOUS_SECRETS="corto")
