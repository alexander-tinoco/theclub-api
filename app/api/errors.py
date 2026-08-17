"""Traduce excepciones de servicio a respuestas HTTP.

Las rutas dejan que estas excepciones se propaguen tal cual — no hace falta
un try/except en cada endpoint; FastAPI las intercepta con los handlers
registrados aquí.
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.infra.security import InvalidTokenError, TokenExpiredError
from app.services.auth import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    RefreshTokenReusedError,
    UserSuspendedError,
)

ExceptionHandler = Callable[[Request, Exception], Awaitable[Response]]

_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    EmailAlreadyExistsError: (status.HTTP_409_CONFLICT, "el email ya está registrado"),
    InvalidCredentialsError: (status.HTTP_401_UNAUTHORIZED, "credenciales inválidas"),
    UserSuspendedError: (status.HTTP_403_FORBIDDEN, "la cuenta está suspendida"),
    TokenExpiredError: (status.HTTP_401_UNAUTHORIZED, "el token ha caducado"),
    InvalidTokenError: (status.HTTP_401_UNAUTHORIZED, "token inválido"),
    RefreshTokenReusedError: (status.HTTP_401_UNAUTHORIZED, "sesión revocada"),
}


def _make_handler(status_code: int, detail: str) -> ExceptionHandler:
    async def handler(request: Request, exc: Exception) -> Response:
        return JSONResponse(status_code=status_code, content={"detail": detail})

    return handler


def register_error_handlers(app: FastAPI) -> None:
    for exc_type, (status_code, detail) in _ERROR_MAP.items():
        app.add_exception_handler(exc_type, _make_handler(status_code, detail))
