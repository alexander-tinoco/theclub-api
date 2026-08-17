"""Traduce excepciones de servicio a respuestas HTTP.

Las rutas dejan que estas excepciones se propaguen tal cual — no hace falta
un try/except en cada endpoint; FastAPI las intercepta con los handlers
registrados aquí.
"""

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.domain.roulette.bets import InvalidBetError
from app.infra.security import InvalidTokenError, TokenExpiredError
from app.repositories.wallets import InsufficientFundsError
from app.services.auth import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    RefreshTokenReusedError,
    UserSuspendedError,
)
from app.services.exceptions import DataIntegrityError
from app.services.idempotency import IdempotencyInProgressError, IdempotencyKeyConflictError

logger = logging.getLogger(__name__)

ExceptionHandler = Callable[[Request, Exception], Awaitable[Response]]

_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    EmailAlreadyExistsError: (status.HTTP_409_CONFLICT, "el email ya está registrado"),
    InvalidCredentialsError: (status.HTTP_401_UNAUTHORIZED, "credenciales inválidas"),
    UserSuspendedError: (status.HTTP_403_FORBIDDEN, "la cuenta está suspendida"),
    TokenExpiredError: (status.HTTP_401_UNAUTHORIZED, "el token ha caducado"),
    InvalidTokenError: (status.HTTP_401_UNAUTHORIZED, "token inválido"),
    RefreshTokenReusedError: (status.HTTP_401_UNAUTHORIZED, "sesión revocada"),
    InvalidBetError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "apuesta inválida"),
    InsufficientFundsError: (status.HTTP_409_CONFLICT, "saldo insuficiente"),
    IdempotencyKeyConflictError: (
        status.HTTP_409_CONFLICT,
        "Idempotency-Key ya usada con un cuerpo de petición distinto",
    ),
    IdempotencyInProgressError: (
        status.HTTP_409_CONFLICT,
        "ya hay una petición con esta misma Idempotency-Key en curso",
    ),
}


def _make_handler(status_code: int, detail: str) -> ExceptionHandler:
    async def handler(request: Request, exc: Exception) -> Response:
        return JSONResponse(status_code=status_code, content={"detail": detail})

    return handler


async def _handle_data_integrity_error(request: Request, exc: Exception) -> Response:
    # A diferencia del resto del mapa, esto nunca lo provoca el cliente — es
    # un invariante de negocio roto en algún otro sitio del sistema. Se
    # registra con nivel error para que no pase desapercibido en logs.
    logger.error("DataIntegrityError en %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "error interno"}
    )


def register_error_handlers(app: FastAPI) -> None:
    for exc_type, (status_code, detail) in _ERROR_MAP.items():
        app.add_exception_handler(exc_type, _make_handler(status_code, detail))
    app.add_exception_handler(DataIntegrityError, _handle_data_integrity_error)
