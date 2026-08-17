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

SECRET = "test-secret-of-at-least-32-bytes-long"
ALGORITHM = "HS256"


def test_hash_password_does_not_return_the_plaintext() -> None:
    hashed = hash_password("correct-horse-battery-staple")

    assert "correct-horse-battery-staple" not in hashed


def test_verify_password_accepts_the_correct_password() -> None:
    hashed = hash_password("my-secure-password")

    assert verify_password("my-secure-password", hashed) is True


def test_verify_password_rejects_the_wrong_one() -> None:
    hashed = hash_password("my-secure-password")

    assert verify_password("something-else", hashed) is False


def test_create_and_decode_access_token_roundtrip() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, secret=SECRET, algorithm=ALGORITHM, ttl_seconds=900)

    decoded = decode_access_token(token, secret=SECRET, algorithm=ALGORITHM)

    assert decoded == user_id


def test_decode_access_token_expired() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, secret=SECRET, algorithm=ALGORITHM, ttl_seconds=-1)

    with pytest.raises(TokenExpiredError):
        decode_access_token(token, secret=SECRET, algorithm=ALGORITHM)


def test_decode_access_token_invalid_signature() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, secret=SECRET, algorithm=ALGORITHM, ttl_seconds=900)

    with pytest.raises(InvalidTokenError):
        decode_access_token(
            token, secret="a-completely-different-other-secret", algorithm=ALGORITHM
        )


def test_decode_access_token_accepts_a_rotated_previous_secret() -> None:
    old_secret = "the-old-secret-of-at-least-32-bytes"
    user_id = uuid.uuid4()
    token = create_access_token(user_id, secret=old_secret, algorithm=ALGORITHM, ttl_seconds=900)

    decoded = decode_access_token(
        token, secret=SECRET, algorithm=ALGORITHM, previous_secrets=[old_secret]
    )

    assert decoded == user_id


def test_decode_access_token_rejects_if_no_secret_matches() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, secret=SECRET, algorithm=ALGORITHM, ttl_seconds=900)

    with pytest.raises(InvalidTokenError):
        decode_access_token(
            token,
            secret="a-completely-different-other-secret",
            algorithm=ALGORITHM,
            previous_secrets=["nor-is-this-other-secret-either-x"],
        )


def test_decode_access_token_expired_under_a_previous_secret() -> None:
    old_secret = "the-old-secret-of-at-least-32-bytes"
    user_id = uuid.uuid4()
    token = create_access_token(user_id, secret=old_secret, algorithm=ALGORITHM, ttl_seconds=-1)

    with pytest.raises(TokenExpiredError):
        decode_access_token(
            token, secret=SECRET, algorithm=ALGORITHM, previous_secrets=[old_secret]
        )


def test_decode_access_token_malformed() -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token("this-is-not-a-jwt", secret=SECRET, algorithm=ALGORITHM)


def test_decode_access_token_with_no_valid_sub() -> None:
    now = datetime.now(UTC)
    token = jwt.encode({"iat": now, "exp": now + timedelta(minutes=5)}, SECRET, algorithm=ALGORITHM)

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, secret=SECRET, algorithm=ALGORITHM)


def test_decode_access_token_with_a_non_uuid_sub() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": "not-a-uuid", "iat": now, "exp": now + timedelta(minutes=5)},
        SECRET,
        algorithm=ALGORITHM,
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, secret=SECRET, algorithm=ALGORITHM)


def test_generate_refresh_token_does_not_repeat() -> None:
    tokens = {generate_refresh_token() for _ in range(100)}

    assert len(tokens) == 100


def test_hash_refresh_token_is_deterministic_and_is_not_the_token() -> None:
    token = generate_refresh_token()

    assert hash_refresh_token(token) == hash_refresh_token(token)
    assert hash_refresh_token(token) != token
