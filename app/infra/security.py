"""Password hashing (argon2id) and access-token JWTs.

Refresh tokens are NOT issued here as JWTs: they're an opaque random string
(`generate_refresh_token`) of which only the SHA-256 hash is stored. Since
the database has to be checked on every refresh anyway to know whether it's
revoked, a JWT adds nothing — and it could leak metadata in its payload
even while signed. SHA-256 and not argon2 here on purpose: argon2 is
expensive to resist brute force over low-entropy secrets (passwords); a
256-bit random token doesn't need that.
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
    """The JWT has a valid signature but has already expired."""


class InvalidTokenError(Exception):
    """Invalid signature, malformed, or missing claims."""


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
    """`secret` first, `previous_secrets` after, in order: that way a token
    signed with an already-rotated secret is still accepted while it stays
    in the list, without slowing down verification of a freshly issued
    token (the common case). With HS256 a signature never "matches by
    accident" under a secret that isn't its own, so the first time a
    candidate produces `ExpiredSignatureError` it's because *that* was the
    right secret — it stops right there, no point trying the rest.
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
