"""Translates service exceptions into HTTP responses.

Routes let these exceptions propagate as-is — no try/except needed in each
endpoint; FastAPI intercepts them with the handlers registered here.
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
    EmailAlreadyExistsError: (status.HTTP_409_CONFLICT, "email already registered"),
    InvalidCredentialsError: (status.HTTP_401_UNAUTHORIZED, "invalid credentials"),
    UserSuspendedError: (status.HTTP_403_FORBIDDEN, "account is suspended"),
    TokenExpiredError: (status.HTTP_401_UNAUTHORIZED, "token has expired"),
    InvalidTokenError: (status.HTTP_401_UNAUTHORIZED, "invalid token"),
    RefreshTokenReusedError: (status.HTTP_401_UNAUTHORIZED, "session revoked"),
    InvalidBetError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid bet"),
    InsufficientFundsError: (status.HTTP_409_CONFLICT, "insufficient funds"),
    IdempotencyKeyConflictError: (
        status.HTTP_409_CONFLICT,
        "Idempotency-Key already used with a different request body",
    ),
    IdempotencyInProgressError: (
        status.HTTP_409_CONFLICT,
        "a request with this same Idempotency-Key is already in progress",
    ),
}


def _make_handler(status_code: int, detail: str) -> ExceptionHandler:
    async def handler(request: Request, exc: Exception) -> Response:
        return JSONResponse(status_code=status_code, content={"detail": detail})

    return handler


async def _handle_data_integrity_error(request: Request, exc: Exception) -> Response:
    # Unlike the rest of the map, the client never triggers this — it's a
    # broken business invariant somewhere else in the system. Logged at
    # error level so it doesn't go unnoticed.
    logger.error("DataIntegrityError at %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "internal error"}
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """Final safety net: any exception not in `_ERROR_MAP` is, by
    definition, a real bug — not something the client could have triggered
    on purpose. Logged with the full stack trace (ERROR level), and the
    response never includes `str(exc)` or anything about the exception:
    just a generic detail, to avoid leaking internals.
    """
    logger.error("Unhandled exception at %s", request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "internal error"}
    )


class UnhandledExceptionMiddleware:
    """Catches `Exception` as ASGI middleware, not as
    `app.add_exception_handler(Exception, ...)`.

    Starlette routes handlers registered for the literal `Exception` (or
    `500`) key to `ServerErrorMiddleware` — the *outermost* layer of all,
    outside `CORSMiddleware` and `RequestContextMiddleware`. A response
    generated there never carries CORS headers or `X-Request-ID`, and that
    request's canonical log line ends up with `status_code: null`, because
    the `send` that produces it never goes through either one's
    `send_wrapper`. Catching the exception here, at the *innermost* layer
    (right next to the router, added after `CORSMiddleware` in
    `create_app()`), makes both outer layers see the real response.
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
                # Something was already sent — injecting a new response on
                # top would break the ASGI protocol. Nothing to do but
                # re-raise, same as Starlette does in that case.
                raise
            response = await _handle_unexpected_error(Request(scope, receive), exc)
            await response(scope, receive, send)


def register_error_handlers(app: FastAPI) -> None:
    for exc_type, (status_code, detail) in _ERROR_MAP.items():
        app.add_exception_handler(exc_type, _make_handler(status_code, detail))
    app.add_exception_handler(DataIntegrityError, _handle_data_integrity_error)
