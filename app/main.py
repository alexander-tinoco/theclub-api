"""Ensamblado de la aplicación FastAPI."""

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
from app.api.v1.router import build_api_v1_router
from app.config import Settings, get_settings
from app.infra.db import create_engine, create_session_factory

logger = logging.getLogger(__name__)


async def _check_database(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Arranque y apagado de recursos de larga vida.

    El engine se crea en `create_app()`, no aquí — así los tests que no
    arrancan el lifespan (`ASGITransport` a secas) siguen teniendo un
    `db_session_factory` funcional. Este bloque solo loguea y cierra el
    engine al apagar. Fase 6 añadirá aquí el productor de Kafka y el relay
    del outbox, con su propio check en `app.state.readiness`.
    """
    settings: Settings = app.state.settings
    logger.info(
        "Arrancando %s v%s (env=%s)", settings.APP_NAME, settings.APP_VERSION, settings.APP_ENV
    )

    yield

    await app.state.db_engine.dispose()
    logger.info("Apagando %s", settings.APP_NAME)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

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

    register_error_handlers(app)

    app.include_router(health_router)
    app.include_router(build_api_v1_router(settings.API_V1_PREFIX))

    return app


app = create_app()
