"""Hashing de contraseñas (argon2id) y JWT de access token.

Los refresh tokens NO se emiten aquí como JWT: son un string aleatorio opaco
(`generate_refresh_token`) del que solo se guarda el hash SHA-256. Como de
todas formas hay que consultar la base de datos en cada refresh para saber si
está revocado, un JWT no aporta nada — y sí puede filtrar metadata en su
payload aunque esté firmado. SHA-256 y no argon2 aquí a propósito: argon2 es
caro para resistir fuerza bruta sobre secretos de baja entropía (contraseñas);
un token aleatorio de 256 bits no la necesita.
"""

import hashlib
import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

REFRESH_TOKEN_BYTES = 32

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


class TokenExpiredError(Exception):
    """El JWT tiene una firma válida pero ya caducó."""


class InvalidTokenError(Exception):
    """Firma inválida, formato incorrecto, o claims que faltan."""


def create_access_token(
    user_id: uuid.UUID, *, secret: str, algorithm: str, ttl_seconds: int
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(
    token: str, *, secret: str, algorithm: str, previous_secrets: Sequence[str] = ()
) -> uuid.UUID:
    """`secret` primero, `previous_secrets` después, en orden: así un token
    firmado con un secreto ya rotado se sigue aceptando mientras siga en la
    lista, sin que eso retrase la verificación de un token recién emitido
    (el caso común). Con HS256 la firma nunca "matchea por accidente" bajo
    un secreto que no es el suyo, así que la primera vez que un candidato
    produce `ExpiredSignatureError` es porque *ese* era el secreto correcto
    — ahí se corta, no tiene sentido seguir probando los demás.
    """
    payload = None
    for candidate in (secret, *previous_secrets):
        try:
            payload = jwt.decode(token, candidate, algorithms=[algorithm])
            break
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError from exc
        except jwt.InvalidTokenError:
            continue
    if payload is None:
        raise InvalidTokenError

    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise InvalidTokenError
    try:
        return uuid.UUID(sub)
    except ValueError as exc:
        raise InvalidTokenError from exc


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
