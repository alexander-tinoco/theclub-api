"""FastAPI application assembly."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.cors import CORSMiddleware

from app.api.errors import UnhandledExceptionMiddleware, register_error_handlers
from app.api.health import ReadinessRegistry
from app.api.health import router as health_router
from app.api.middleware import MaxBodySizeMiddleware
from app.api.rate_limit import limiter
from app.api.request_context import RequestContextMiddleware
from app.api.v1.router import build_api_v1_router
from app.config import Settings, get_settings
from app.events.outbox_cleanup import purge_loop
from app.events.relay import relay_loop
from app.infra.db import create_engine, create_session_factory
from app.infra.kafka import check_kafka, create_producer
from app.infra.logging import configure_logging
from app.infra.metrics import ws_connections_limit
from app.infra.redis import check_redis, create_redis_client
from app.ws.broadcaster import InMemoryBroadcaster
from app.ws.connections import ConnectionRegistry
from app.ws.rate_limit import WsConnectRateLimiter

logger = logging.getLogger(__name__)


async def _check_database(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown of long-lived resources.

    Unlike the Postgres engine, the Kafka producer *can't* be built in
    `create_app()`: `AIOKafkaProducer` requires a running event loop just
    to be instantiated, not only to connect — so both its creation and its
    `start()` live here, along with registering the `"kafka"` check (unlike
    `"database"`, which does get registered in `create_app()`). A test that
    doesn't start the lifespan simply doesn't see `"kafka"` in `/ready` —
    same as before this phase existed.
    """
    settings: Settings = app.state.settings
    logger.info(
        "Starting %s v%s (env=%s)", settings.APP_NAME, settings.APP_VERSION, settings.APP_ENV
    )

    producer = create_producer(settings)
    await producer.start()
    app.state.kafka_producer = producer
    app.state.readiness.register("kafka", lambda: check_kafka(producer, settings))

    relay_task = asyncio.create_task(relay_loop(app.state.db_session_factory, producer, settings))
    purge_task = asyncio.create_task(purge_loop(app.state.db_session_factory, settings))

    yield

    # Notify WS clients before tearing down the rest of the
    # infrastructure: their handlers don't depend on Postgres/Kafka for
    # their own shutdown, but "telling them we're leaving" first is still
    # the order that makes sense.
    await app.state.ws_connections.close_all()

    for task in (relay_task, purge_task):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await producer.stop()

    await app.state.redis.aclose()
    await app.state.db_engine.dispose()
    logger.info("Shutting down %s", settings.APP_NAME)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.LOG_LEVEL)

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
        # Interactive docs aren't published in staging/prod.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.state.settings = settings
    # readiness, engine, and session_factory are created here, not in the
    # lifespan: that way tests that don't start it still get valid state.
    app.state.readiness = ReadinessRegistry()
    engine = create_engine(settings.DATABASE_URL, pool_size=settings.DB_POOL_SIZE)
    app.state.db_engine = engine
    app.state.db_session_factory = create_session_factory(engine)
    app.state.readiness.register("database", lambda: _check_database(engine))

    # Same as readiness/engine: pure, no IO, so they live here and not in
    # the lifespan — tests that don't start it need them too.
    redis_client = create_redis_client(settings)
    app.state.redis = redis_client
    app.state.readiness.register("redis", lambda: check_redis(redis_client))

    app.state.ws_broadcaster = InMemoryBroadcaster()
    app.state.ws_connections = ConnectionRegistry(max_connections=settings.WS_MAX_CONNECTIONS)
    ws_connections_limit.set(settings.WS_MAX_CONNECTIONS)
    app.state.ws_connect_rate_limiter = WsConnectRateLimiter(
        redis_client,
        max_attempts=settings.WS_CONNECT_RATE_LIMIT_ATTEMPTS,
        window_seconds=settings.WS_CONNECT_RATE_LIMIT_WINDOW_S,
    )

    app.state.limiter = limiter
    # slowapi types its handler specifically for RateLimitExceeded, not for
    # the generic Exception Starlette expects — a known variance mismatch
    # between the two libraries, not a real error.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # Order matters, and it's counterintuitive: `Starlette.add_middleware`
    # inserts every new middleware at the *start* of its internal list
    # (`user_middleware.insert(0, ...)`), so the *last* one added here ends
    # up being the *outermost* at runtime (sees the request first, the
    # response last) — verified with a throwaway script before trusting
    # this, because the naive reading ("the first one I add is the first
    # to see everything") is backwards.
    #
    # From innermost to outermost:
    #   UnhandledExceptionMiddleware — right next to the router; if
    #     something unmapped blows up, it generates the response *before*
    #     exiting CORS, so it does carry its headers (Starlette routes
    #     handlers registered for `Exception`/500 via
    #     `add_exception_handler` to `ServerErrorMiddleware`, the outermost
    #     layer of all — outside CORS and RequestContext; that's why this
    #     is middleware, not just another exception handler).
    #   SlowAPIMiddleware, CORSMiddleware, MaxBodySizeMiddleware — each can
    #     cut the request short with its own response (429, invalid CORS,
    #     413).
    #   RequestContextMiddleware — the outermost: measures the real total
    #     duration and sees the final status code no matter which layer
    #     generated the response, so its canonical line and the
    #     `X-Request-ID` header cover *every* request, including one
    #     rejected by any of the layers below.
    app.add_middleware(UnhandledExceptionMiddleware)
    app.add_middleware(SlowAPIMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        # Restricted to what the API actually uses — there was never a real
        # reason for the wildcard, no route accepts other methods/headers.
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    app.add_middleware(MaxBodySizeMiddleware, max_body_bytes=settings.MAX_REQUEST_BODY_BYTES)
    app.add_middleware(RequestContextMiddleware)

    register_error_handlers(app)

    app.include_router(health_router)
    app.include_router(build_api_v1_router(settings.API_V1_PREFIX))

    return app


app = create_app()
