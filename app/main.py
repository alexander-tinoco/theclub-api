"""Ensamblado de la aplicación FastAPI."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.cors import CORSMiddleware

from app.api.health import ReadinessRegistry
from app.api.health import router as health_router
from app.config import Settings, get_settings
from app.infra.db import create_engine

logger = logging.getLogger(__name__)


async def _check_database(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Arranque y apagado de recursos de larga vida.

    Fase 6 añadirá aquí el productor de Kafka y el relay del outbox, con su
    propio check en `app.state.readiness`.
    """
    settings: Settings = app.state.settings
    logger.info(
        "Arrancando %s v%s (env=%s)", settings.APP_NAME, settings.APP_VERSION, settings.APP_ENV
    )

    engine = create_engine(settings.DATABASE_URL, pool_size=settings.DB_POOL_SIZE)
    app.state.db_engine = engine
    app.state.readiness.register("database", lambda: _check_database(engine))

    yield

    await engine.dispose()
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
    # Se crea aquí y no en el lifespan: así los tests que no arrancan el
    # lifespan siguen teniendo un registro válido.
    app.state.readiness = ReadinessRegistry()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)

    return app


app = create_app()
