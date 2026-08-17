"""Authentication use cases: register, log in, refresh a session.

Orchestrates repositories + `app/infra/security.py`. Knows nothing about
HTTP — `app/api/errors.py` translates these exceptions into concrete
responses.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.fairness import hash_seed, new_server_seed
from app.infra.security import (
    InvalidTokenError,
    TokenExpiredError,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.seed_pairs import SeedPairRepository
from app.repositories.users import UserRepository
from app.services.fairness import generate_client_seed


class EmailAlreadyExistsError(Exception):
    """A user with that email already exists."""


class InvalidCredentialsError(Exception):
    """Unknown email or wrong password — same error for both cases, so a
    different message doesn't reveal which emails exist."""


class UserSuspendedError(Exception):
    """The account exists but `status == 'suspended'`."""


class RefreshTokenReusedError(Exception):
    """An already-rotated refresh token (`revoked_at` not null) was
    presented again: a sign of theft. The entire family is revoked in
    response."""


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str


async def register_user(
    session: AsyncSession, settings: Settings, *, email: str, password: str
) -> tuple[User, TokenPair]:
    users = UserRepository(session)
    if await users.get_by_email(email) is not None:
        raise EmailAlreadyExistsError(email)

    user = await users.create(email=email, password_hash=hash_password(password))
    session.add(Wallet(user_id=user.id, balance_minor=0))

    # Without this there's no published hash and no way to bet verifiably:
    # the first seed pair is created here, not lazily on the first spin, so
    # the hash exists from registration onward.
    server_seed = new_server_seed()
    await SeedPairRepository(session).create_active(
        user_id=user.id,
        server_seed=server_seed,
        server_seed_hash=hash_seed(server_seed),
        client_seed=generate_client_seed(),
    )

    await session.flush()

    tokens = await _issue_tokens(session, settings, user_id=user.id, family_id=uuid.uuid4())
    return user, tokens


async def login(
    session: AsyncSession, settings: Settings, *, email: str, password: str
) -> tuple[User, TokenPair]:
    user = await UserRepository(session).get_by_email(email)
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentialsError
    if user.status == "suspended":
        raise UserSuspendedError

    tokens = await _issue_tokens(session, settings, user_id=user.id, family_id=uuid.uuid4())
    return user, tokens


async def refresh_access_token(
    session: AsyncSession, settings: Settings, *, raw_refresh_token: str
) -> TokenPair:
    tokens_repo = RefreshTokenRepository(session)
    stored = await tokens_repo.get_by_hash(hash_refresh_token(raw_refresh_token))

    if stored is None:
        raise InvalidTokenError

    now = datetime.now(UTC)

    if stored.revoked_at is not None:
        await tokens_repo.revoke_family(stored.family_id, revoked_at=now)
        # Explicit commit: `get_session` wraps the request in a single
        # transaction that rolls back when an exception propagates, and
        # here the effect (revoking the family) must survive precisely the
        # error we're about to raise — it's the response to the theft, not
        # an incidental detail worth losing.
        await session.commit()
        raise RefreshTokenReusedError

    if stored.expires_at < now:
        raise TokenExpiredError

    await tokens_repo.revoke(stored.id, revoked_at=now)
    return await _issue_tokens(
        session, settings, user_id=stored.user_id, family_id=stored.family_id
    )


async def logout(session: AsyncSession, *, user_id: uuid.UUID, raw_refresh_token: str) -> None:
    """Revokes the refresh token's entire family — not just that row, the
    whole session that started with the login or refresh that originated it.

    Idempotent on purpose: if the token doesn't exist, already expired, or
    belongs to another user, it's not an error — the outcome the client
    wants ("this session no longer works") already holds either way.
    Comparing `stored.user_id == user_id` also stops someone from logging
    out another account's session by passing someone else's refresh token
    in the body.
    """
    stored = await RefreshTokenRepository(session).get_by_hash(
        hash_refresh_token(raw_refresh_token)
    )
    if stored is None or stored.user_id != user_id:
        return

    await RefreshTokenRepository(session).revoke_family(
        stored.family_id, revoked_at=datetime.now(UTC)
    )


async def _issue_tokens(
    session: AsyncSession, settings: Settings, *, user_id: uuid.UUID, family_id: uuid.UUID
) -> TokenPair:
    access_token = create_access_token(
        user_id,
        secret=settings.JWT_SECRET.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
        ttl_seconds=settings.ACCESS_TOKEN_TTL_SECONDS,
    )
    raw_refresh_token = generate_refresh_token()
    await RefreshTokenRepository(session).create(
        user_id=user_id,
        family_id=family_id,
        token_hash=hash_refresh_token(raw_refresh_token),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.REFRESH_TOKEN_TTL_SECONDS),
    )
    return TokenPair(access_token=access_token, refresh_token=raw_refresh_token)
