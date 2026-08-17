"""Dependencias compartidas de la capa API."""

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
    """Settings de *esta* aplicación.

    Se leen de `app.state` y no del caché global de `get_settings()` para que
    una app creada con settings explícitos (los tests, sobre todo) se comporte
    exactamente como está configurada.
    """
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Una sesión por petición, envuelta en `unit_of_work`: commit si el
    handler termina bien, rollback si levanta cualquier excepción.
    """
    async with unit_of_work(request.app.state.db_session_factory) as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """La fábrica de sesiones en sí, no una sesión ya abierta — la necesitan
    los endpoints que pasan por `run_idempotent` (place_bet, deposit), que
    abren varias transacciones independientes dentro de la misma petición.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.db_session_factory
    return factory


SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


async def get_current_user(request: Request, session: SessionDep, settings: SettingsDep) -> User:
    """Usuario autenticado a partir del header `Authorization: Bearer <jwt>`.

    Se recarga desde la base en cada petición (no basta con confiar en el
    `sub` del JWT) para que un usuario suspendido *después* de emitido el
    token deje de poder usarlo antes de que caduque.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise InvalidTokenError

    token = auth_header.removeprefix("Bearer ")
    user_id = decode_access_token(
        token, secret=settings.JWT_SECRET.get_secret_value(), algorithm=settings.JWT_ALGORITHM
    )

    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        raise InvalidTokenError
    if user.status == "suspended":
        raise UserSuspendedError
    user_id_var.set(str(user.id))
    # `bind_canonical` además de `user_id_var`, no en vez de: `SlowAPIMiddleware`
    # hereda de `BaseHTTPMiddleware`, que corre el resto de la petición en una
    # tarea aparte — un `ContextVar.set()' ahí adentro nunca lo ve
    # `RequestContextMiddleware` (por fuera de esa tarea) al armar la línea
    # canónica, pero mutar el dict de `bind_canonical` sí, porque ese dict es
    # el mismo objeto compartido, no una reasignación de contextvar.
    bind_canonical(user_id=str(user.id))
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def get_broadcaster(request: Request) -> Broadcaster:
    broadcaster: Broadcaster = request.app.state.ws_broadcaster
    return broadcaster


BroadcasterDep = Annotated[Broadcaster, Depends(get_broadcaster)]
