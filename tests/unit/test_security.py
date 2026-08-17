import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.infra.security import (
    InvalidTokenError,
    TokenExpiredError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

pytestmark = pytest.mark.unit

SECRET = "test-secret-de-al-menos-32-bytes-de-largo"
ALGORITHM = "HS256"


def test_hash_password_no_devuelve_el_texto_plano() -> None:
    hashed = hash_password("correcto-caballo-batería-grapa")

    assert "correcto-caballo-batería-grapa" not in hashed


def test_verify_password_acepta_la_contrasena_correcta() -> None:
    hashed = hash_password("mi-contraseña-segura")

    assert verify_password("mi-contraseña-segura", hashed) is True


def test_verify_password_rechaza_la_incorrecta() -> None:
    hashed = hash_password("mi-contraseña-segura")

    assert verify_password("otra-cosa", hashed) is False


def test_create_and_decode_access_token_roundtrip() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, secret=SECRET, algorithm=ALGORITHM, ttl_seconds=900)

    decoded = decode_access_token(token, secret=SECRET, algorithm=ALGORITHM)

    assert decoded == user_id


def test_decode_access_token_expirado() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, secret=SECRET, algorithm=ALGORITHM, ttl_seconds=-1)

    with pytest.raises(TokenExpiredError):
        decode_access_token(token, secret=SECRET, algorithm=ALGORITHM)


def test_decode_access_token_firma_invalida() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, secret=SECRET, algorithm=ALGORITHM, ttl_seconds=900)

    with pytest.raises(InvalidTokenError):
        decode_access_token(
            token, secret="otro-secreto-completamente-distinto", algorithm=ALGORITHM
        )


def test_decode_access_token_malformado() -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token("esto-no-es-un-jwt", secret=SECRET, algorithm=ALGORITHM)


def test_decode_access_token_sin_sub_valido() -> None:
    now = datetime.now(UTC)
    token = jwt.encode({"iat": now, "exp": now + timedelta(minutes=5)}, SECRET, algorithm=ALGORITHM)

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, secret=SECRET, algorithm=ALGORITHM)


def test_decode_access_token_con_sub_no_uuid() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": "no-soy-un-uuid", "iat": now, "exp": now + timedelta(minutes=5)},
        SECRET,
        algorithm=ALGORITHM,
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, secret=SECRET, algorithm=ALGORITHM)


def test_generate_refresh_token_no_se_repite() -> None:
    tokens = {generate_refresh_token() for _ in range(100)}

    assert len(tokens) == 100


def test_hash_refresh_token_es_determinista_y_no_es_el_token() -> None:
    token = generate_refresh_token()

    assert hash_refresh_token(token) == hash_refresh_token(token)
    assert hash_refresh_token(token) != token
