"""Shared dependencies for the API layer."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.request_context import bind_canonical, user_id_var
from app.config import Settings
from app.infra.db import unit_of_work
from app.infra.security import InvalidTokenError, decode_access_token
from app.models.user import User
from app.repositories.users import UserRepository
from app.services.auth import UserSuspendedError
from app.ws.broadcaster import Broadcaster


def get_app_settings(request: Request) -> Settings:
    """*This* application's settings.

    Read from `app.state` and not from `get_settings()`'s global cache so
    an app created with explicit settings (tests, mainly) behaves exactly
    as configured.
    """
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, wrapped in `unit_of_work`: commits if the
    handler finishes cleanly, rolls back if any exception is raised.
    """
    async with unit_of_work(request.app.state.db_session_factory) as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """The session factory itself, not an already-open session — needed by
    endpoints that go through `run_idempotent` (place_bet, deposit), which
    open several independent transactions within the same request.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.db_session_factory
    return factory


SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


async def get_current_user(request: Request, session: SessionDep, settings: SettingsDep) -> User:
    """Authenticated user from the `Authorization: Bearer <jwt>` header.

    Reloaded from the database on every request (trusting the JWT's `sub`
    alone isn't enough) so a user suspended *after* the token was issued
    loses access before it expires.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise InvalidTokenError

    token = auth_header.removeprefix("Bearer ")
    user_id = decode_access_token(
        token,
        secret=settings.JWT_SECRET.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
        previous_secrets=[s.get_secret_value() for s in settings.JWT_PREVIOUS_SECRETS],
    )

    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        raise InvalidTokenError
    if user.status == "suspended":
        raise UserSuspendedError
    user_id_var.set(str(user.id))
    # `bind_canonical` in addition to `user_id_var`, not instead of it:
    # `SlowAPIMiddleware` inherits from `BaseHTTPMiddleware`, which runs the
    # rest of the request in a separate task — a `ContextVar.set()` in
    # there is never seen by `RequestContextMiddleware` (outside that task)
    # when building the canonical line, but mutating `bind_canonical`'s
    # dict is, because that dict is the same shared object, not a
    # contextvar reassignment.
    bind_canonical(user_id=str(user.id))
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def get_broadcaster(request: Request) -> Broadcaster:
    broadcaster: Broadcaster = request.app.state.ws_broadcaster
    return broadcaster


BroadcasterDep = Annotated[Broadcaster, Depends(get_broadcaster)]
