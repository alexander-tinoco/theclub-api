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
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


async def _handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """Red de seguridad final: cualquier excepción que no esté en `_ERROR_MAP`
    es, por definición, un bug de verdad — no algo que el cliente pudo
    provocar a propósito. Se registra con el stack trace completo (nivel
    ERROR) y la respuesta nunca incluye `str(exc)` ni nada de la excepción:
    solo un detail genérico, para no filtrar internals.
    """
    logger.error("Excepción no manejada en %s", request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "error interno"}
    )


class UnhandledExceptionMiddleware:
    """Atrapa `Exception` como middleware ASGI, no como
    `app.add_exception_handler(Exception, ...)`.

    Starlette envía los handlers registrados para la clave literal
    `Exception` (o `500`) a `ServerErrorMiddleware` — la capa *más externa*
    de todas, por fuera de `CORSMiddleware` y de `RequestContextMiddleware`.
    Una respuesta generada ahí nunca lleva los headers de CORS ni
    `X-Request-ID`, y la línea canónica de esa petición queda con
    `status_code: null`, porque el `send` que la produce nunca pasa por el
    `send_wrapper` de ninguno de los dos. Atrapar la excepción aquí, en la
    capa *más interna* (pegada al router, añadida después de `CORSMiddleware`
    en `create_app()`), hace que ambas capas sí vean la respuesta real.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if response_started:
                # Ya se mandó algo — inyectar una respuesta nueva encima
                # rompería el protocolo ASGI. No queda otra que relanzar,
                # igual que hace Starlette en el mismo caso.
                raise
            response = await _handle_unexpected_error(Request(scope, receive), exc)
            await response(scope, receive, send)


def register_error_handlers(app: FastAPI) -> None:
    for exc_type, (status_code, detail) in _ERROR_MAP.items():
        app.add_exception_handler(exc_type, _make_handler(status_code, detail))
    app.add_exception_handler(DataIntegrityError, _handle_data_integrity_error)
