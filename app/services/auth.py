"""Casos de uso de autenticación: registrar, entrar, refrescar sesión.

Orquesta repositorios + `app/infra/security.py`. No sabe nada de HTTP —
`app/api/errors.py` traduce estas excepciones a respuestas concretas.
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
    """Ya existe un usuario con ese email."""


class InvalidCredentialsError(Exception):
    """Email desconocido o contraseña incorrecta — mismo error para los dos
    casos, para no revelar con un mensaje distinto qué emails existen."""


class UserSuspendedError(Exception):
    """La cuenta existe pero `status == 'suspended'`."""


class RefreshTokenReusedError(Exception):
    """Un refresh token ya rotado (`revoked_at` no nulo) volvió a presentarse:
    señal de robo. Se revoca toda la familia como respuesta."""


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

    # Sin esto no hay hash publicado y no se puede apostar de forma
    # verificable: el primer par de semillas se crea aquí, no de forma
    # perezosa en el primer giro, para que el hash exista desde el registro.
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
        # Commit explícito: `get_session` envuelve el request en una única
        # transacción que se revierte al propagar una excepción, y aquí el
        # efecto (revocar la familia) debe sobrevivir precisamente al error
        # que estamos a punto de lanzar — es la respuesta al robo, no un
        # detalle incidental que valga la pena perder.
        await session.commit()
        raise RefreshTokenReusedError

    if stored.expires_at < now:
        raise TokenExpiredError

    await tokens_repo.revoke(stored.id, revoked_at=now)
    return await _issue_tokens(
        session, settings, user_id=stored.user_id, family_id=stored.family_id
    )


async def logout(session: AsyncSession, *, user_id: uuid.UUID, raw_refresh_token: str) -> None:
    """Revoca la familia completa del refresh token — no solo esa fila, toda
    la sesión que empezó con el login o refresh que la originó.

    Idempotente a propósito: si el token no existe, ya caducó, o pertenece a
    otro usuario, no es un error — el resultado que el cliente quiere
    ("que esta sesión ya no sirva") ya se cumple igual. Comparar
    `stored.user_id == user_id` evita además que alguien cierre la sesión de
    otra cuenta pasando un refresh token ajeno en el cuerpo.
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
