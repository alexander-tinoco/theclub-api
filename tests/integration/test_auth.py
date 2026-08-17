"""Phase 4's DoD, against a real Postgres: expired token, invalid
signature, reused refresh (revokes the family), duplicate email, and that
the password hash never shows up in a response.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import limiter
from app.config import Settings
from app.infra.security import (
    TokenExpiredError,
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from app.main import create_app
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.services import auth as auth_service

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    # The Limiter is a module-level singleton (main.py needs the same
    # instance as the routers to register the middleware) — without
    # resetting its in-memory storage, counters accumulate across tests.
    limiter.reset()


@pytest.fixture
async def client(integration_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def _credentials(email: str | None = None) -> dict[str, str]:
    return {"email": email or f"{uuid.uuid4()}@example.com", "password": "a-long-password"}


async def test_register_returns_tokens(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json=_credentials())

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]


async def test_register_duplicate_email(client: AsyncClient) -> None:
    payload = _credentials()

    await client.post("/api/v1/auth/register", json=payload)
    response = await client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 409


async def test_login_success(client: AsyncClient) -> None:
    payload = _credentials()
    await client.post("/api/v1/auth/register", json=payload)

    response = await client.post("/api/v1/auth/login", json=payload)

    assert response.status_code == 200


async def test_login_invalid_credentials(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/login", json=_credentials())

    assert response.status_code == 401


async def test_login_suspended_user(client: AsyncClient, db_session: AsyncSession) -> None:
    payload = _credentials()
    await client.post("/api/v1/auth/register", json=payload)

    user = await UserRepository(db_session).get_by_email(payload["email"])
    assert user is not None
    user.status = "suspended"
    await db_session.commit()

    response = await client.post("/api/v1/auth/login", json=payload)

    assert response.status_code == 403


async def test_refresh_with_an_unknown_token(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "a-token-that-never-existed"}
    )

    assert response.status_code == 401


async def test_me_requires_a_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401


async def test_me_with_a_valid_token(client: AsyncClient) -> None:
    payload = _credentials()
    register = await client.post("/api/v1/auth/register", json=payload)
    access_token = register.json()["access_token"]

    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )

    assert response.status_code == 200
    assert response.json()["email"] == payload["email"]


async def test_me_with_a_user_suspended_after_the_token_was_issued(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _credentials()
    register = await client.post("/api/v1/auth/register", json=payload)
    access_token = register.json()["access_token"]

    user = await UserRepository(db_session).get_by_email(payload["email"])
    assert user is not None
    user.status = "suspended"
    await db_session.commit()

    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )

    assert response.status_code == 403


async def test_me_with_a_token_for_a_nonexistent_user(
    client: AsyncClient, integration_settings: Settings
) -> None:
    # A JWT with a valid signature but for a user_id that never existed (or
    # that got deleted after the token was issued).
    ghost_token = create_access_token(
        uuid.uuid4(),
        secret=integration_settings.JWT_SECRET.get_secret_value(),
        algorithm=integration_settings.JWT_ALGORITHM,
        ttl_seconds=900,
    )

    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {ghost_token}"}
    )

    assert response.status_code == 401


async def test_me_with_an_invalid_signature(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-valid-jwt"}
    )

    assert response.status_code == 401


async def test_me_with_a_token_signed_with_a_rotated_previous_secret(
    client: AsyncClient, integration_settings: Settings
) -> None:
    # The "old" secret signs a token for a real user already registered
    # with the normal app (same database) — simulates an access token
    # issued right before rotating JWT_SECRET, which should keep working
    # as long as JWT_PREVIOUS_SECRETS keeps it around.
    register = await client.post("/api/v1/auth/register", json=_credentials())
    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {register.json()['access_token']}"},
    )
    user_id = uuid.UUID(me.json()["id"])

    old_secret = "the-secret-before-rotating-at-least-32-bytes"
    old_token = create_access_token(
        user_id, secret=old_secret, algorithm=integration_settings.JWT_ALGORITHM, ttl_seconds=900
    )
    rotated_settings = integration_settings.model_copy(
        # `model_copy(update=...)` doesn't revalidate against the field's
        # type (unlike a normal `Settings(...)` construction) — it needs
        # to already get the `SecretStr` that `JWT_PREVIOUS_SECRETS`
        # expects, not a plain `str`.
        update={"JWT_PREVIOUS_SECRETS": [SecretStr(old_secret)]}
    )
    app = create_app(rotated_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as rotated_client:
            response = await rotated_client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {old_token}"}
            )

    assert response.status_code == 200
    assert response.json()["id"] == str(user_id)


async def test_me_with_an_expired_token(
    client: AsyncClient, integration_settings: Settings
) -> None:
    expired = create_access_token(
        uuid.uuid4(),
        secret=integration_settings.JWT_SECRET.get_secret_value(),
        algorithm=integration_settings.JWT_ALGORITHM,
        ttl_seconds=-1,
    )

    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401


async def test_refresh_rotates_the_token(client: AsyncClient) -> None:
    register = await client.post("/api/v1/auth/register", json=_credentials())
    old_refresh = register.json()["refresh_token"]

    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})

    assert response.status_code == 200
    assert response.json()["refresh_token"] != old_refresh


async def test_reused_refresh_revokes_the_whole_family(client: AsyncClient) -> None:
    register = await client.post("/api/v1/auth/register", json=_credentials())
    original_refresh = register.json()["refresh_token"]

    first_rotation = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original_refresh}
    )
    assert first_rotation.status_code == 200
    rotated_refresh = first_rotation.json()["refresh_token"]

    # Reusing the original (already rotated) token is the sign of a possible theft.
    reused = await client.post("/api/v1/auth/refresh", json={"refresh_token": original_refresh})
    assert reused.status_code == 401

    # In response, the ENTIRE family gets revoked -- even the token that
    # was legitimate after the rotation stops working.
    legit_but_revoked = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": rotated_refresh}
    )
    assert legit_but_revoked.status_code == 401


async def test_expired_refresh(db_session: AsyncSession, integration_settings: Settings) -> None:
    user = await UserRepository(db_session).create(
        email=f"{uuid.uuid4()}@example.com", password_hash="x"
    )
    raw_token = generate_refresh_token()
    await RefreshTokenRepository(db_session).create(
        user_id=user.id,
        family_id=uuid.uuid4(),
        token_hash=hash_refresh_token(raw_token),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    await db_session.commit()

    with pytest.raises(TokenExpiredError):
        await auth_service.refresh_access_token(
            db_session, integration_settings, raw_refresh_token=raw_token
        )


async def test_password_hash_never_appears_in_a_response(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _credentials()
    register_response = await client.post("/api/v1/auth/register", json=payload)
    login_response = await client.post("/api/v1/auth/login", json=payload)
    me_response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {register_response.json()['access_token']}"},
    )

    user = await UserRepository(db_session).get_by_email(payload["email"])
    assert user is not None

    for response in (register_response, login_response, me_response):
        assert user.password_hash not in response.text
        assert "password_hash" not in response.text
        assert "password" not in response.json()


async def test_login_has_a_rate_limit(client: AsyncClient) -> None:
    payload = _credentials()

    responses = [await client.post("/api/v1/auth/login", json=payload) for _ in range(6)]

    assert responses[-1].status_code == 429


async def test_logout_revokes_the_refresh_token(client: AsyncClient) -> None:
    register = await client.post("/api/v1/auth/register", json=_credentials())
    tokens = register.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    logout = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers=headers
    )
    assert logout.status_code == 204

    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 401


async def test_logout_revokes_the_whole_family_not_just_the_current_token(
    client: AsyncClient,
) -> None:
    register = await client.post("/api/v1/auth/register", json=_credentials())
    original = register.json()
    headers = {"Authorization": f"Bearer {original['access_token']}"}

    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
    )
    rotated_refresh = rotated.json()["refresh_token"]

    logout = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": rotated_refresh}, headers=headers
    )
    assert logout.status_code == 204

    refresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": rotated_refresh})
    assert refresh.status_code == 401


async def test_logout_is_idempotent(client: AsyncClient) -> None:
    register = await client.post("/api/v1/auth/register", json=_credentials())
    tokens = register.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    body = {"refresh_token": tokens["refresh_token"]}

    first = await client.post("/api/v1/auth/logout", json=body, headers=headers)
    second = await client.post("/api/v1/auth/logout", json=body, headers=headers)

    assert first.status_code == second.status_code == 204


async def test_logout_with_an_unknown_token_is_not_an_error(client: AsyncClient) -> None:
    register = await client.post("/api/v1/auth/register", json=_credentials())
    headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

    response = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": "this-never-existed"}, headers=headers
    )

    assert response.status_code == 204


async def test_logout_with_another_users_refresh_token_does_not_revoke_it(
    client: AsyncClient,
) -> None:
    victim = await client.post("/api/v1/auth/register", json=_credentials())
    victim_refresh = victim.json()["refresh_token"]

    attacker = await client.post("/api/v1/auth/register", json=_credentials())
    attacker_headers = {"Authorization": f"Bearer {attacker.json()['access_token']}"}

    # The attacker is authenticated as themself, but sends the victim's
    # refresh token in the body -- they shouldn't be able to end the
    # victim's session.
    logout = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": victim_refresh}, headers=attacker_headers
    )
    assert logout.status_code == 204  # idempotent: reveals nothing, not an error

    refresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": victim_refresh})
    assert refresh.status_code == 200  # the victim's token is still alive


async def test_logout_requires_an_access_token(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/logout", json={"refresh_token": "x"})

    assert response.status_code == 401
