"""Ensamblado de la aplicación FastAPI."""

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

from app.api.errors import register_error_handlers
from app.api.health import ReadinessRegistry
from app.api.health import router as health_router
from app.api.rate_limit import limiter
from app.api.request_context import RequestContextMiddleware
from app.api.v1.router import build_api_v1_router
from app.config import Settings, get_settings
from app.events.outbox_cleanup import purge_loop
from app.events.relay import relay_loop
from app.infra.db import create_engine, create_session_factory
from app.infra.kafka import check_kafka, create_producer
from app.infra.logging import configure_logging
from app.ws.broadcaster import InMemoryBroadcaster
from app.ws.connections import ConnectionRegistry
from app.ws.rate_limit import WsConnectRateLimiter

logger = logging.getLogger(__name__)


async def _check_database(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Arranque y apagado de recursos de larga vida.

    A diferencia del engine de Postgres, el productor de Kafka *no* se puede
    construir en `create_app()`: `AIOKafkaProducer` exige un event loop
    corriendo incluso para instanciarse, no solo para conectar — así que
    tanto su creación como el `start()` viven aquí, junto con el registro del
    check `"kafka"` (a diferencia de `"database"`, que sí se registra en
    `create_app()`). Un test que no arranca el lifespan simplemente no ve
    `"kafka"` en `/ready` — igual que antes de que esta fase existiera.
    """
    settings: Settings = app.state.settings
    logger.info(
        "Arrancando %s v%s (env=%s)", settings.APP_NAME, settings.APP_VERSION, settings.APP_ENV
    )

    producer = create_producer(settings)
    await producer.start()
    app.state.kafka_producer = producer
    app.state.readiness.register("kafka", lambda: check_kafka(producer, settings))

    relay_task = asyncio.create_task(relay_loop(app.state.db_session_factory, producer, settings))
    purge_task = asyncio.create_task(purge_loop(app.state.db_session_factory, settings))

    yield

    # Avisar a los clientes WS antes de tirar el resto de la infraestructura:
    # sus handlers no dependen de Postgres/Kafka en su propio cierre, pero
    # "decirles que nos vamos" antes es el orden que tiene sentido igual.
    await app.state.ws_connections.close_all()

    for task in (relay_task, purge_task):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await producer.stop()

    await app.state.db_engine.dispose()
    logger.info("Apagando %s", settings.APP_NAME)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.LOG_LEVEL)

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
        # La documentación interactiva no se publica en staging/prod.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.state.settings = settings
    # readiness, engine y session_factory se crean aquí y no en el lifespan:
    # así los tests que no lo arrancan siguen teniendo un estado válido.
    app.state.readiness = ReadinessRegistry()
    engine = create_engine(settings.DATABASE_URL, pool_size=settings.DB_POOL_SIZE)
    app.state.db_engine = engine
    app.state.db_session_factory = create_session_factory(engine)
    app.state.readiness.register("database", lambda: _check_database(engine))

    # Igual que readiness/engine: puros, sin IO, así que viven aquí y no en
    # el lifespan — los tests que no lo arrancan también los necesitan.
    app.state.ws_broadcaster = InMemoryBroadcaster()
    app.state.ws_connections = ConnectionRegistry(max_connections=settings.WS_MAX_CONNECTIONS)
    app.state.ws_connect_rate_limiter = WsConnectRateLimiter(
        max_attempts=settings.WS_CONNECT_RATE_LIMIT_ATTEMPTS,
        window_seconds=settings.WS_CONNECT_RATE_LIMIT_WINDOW_S,
    )

    app.state.limiter = limiter
    # slowapi tipa su handler específicamente para RateLimitExceeded, no para
    # el Exception genérico que espera Starlette — desajuste de varianza
    # conocido entre las dos librerías, no un error real.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_middleware(SlowAPIMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # El último `add_middleware` queda más afuera en tiempo de ejecución
    # (`Starlette.add_middleware` inserta cada uno al *principio* de su
    # lista interna) — así ve la duración total real y el código de estado
    # final de cualquier petición, sin importar en qué capa se generó la
    # respuesta.
    app.add_middleware(RequestContextMiddleware)

    register_error_handlers(app)

    app.include_router(health_router)
    app.include_router(build_api_v1_router(settings.API_V1_PREFIX))

    return app


app = create_app()
