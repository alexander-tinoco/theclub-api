"""Liveness y readiness.

Son dos cosas distintas y conviene no mezclarlas:

- `/health` (liveness) no toca ninguna dependencia. Si responde, el proceso
  está vivo. Es lo que mira Docker para decidir si reiniciar el contenedor.
- `/ready` (readiness) ejecuta los checks registrados y devuelve 503 si alguno
  falla. En la Fase 0 el registro está vacío; la Fase 3 añade Postgres y la
  Fase 6 añade Kafka **sin tocar este archivo**.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app.api.deps import SettingsDep

logger = logging.getLogger(__name__)

#: Un check falla lanzando una excepción; no devuelve nada.
ReadinessCheck = Callable[[], Awaitable[None]]

#: Una dependencia colgada no debe colgar `/ready`.
CHECK_TIMEOUT_SECONDS = 2.0

router = APIRouter(tags=["health"])


class ReadinessRegistry:
    """Checks de readiness que las fases posteriores van registrando.

    Vive en `app.state.readiness`, no como global de módulo, para que cada
    aplicación creada en un test tenga el suyo.
    """

    def __init__(self) -> None:
        self._checks: dict[str, ReadinessCheck] = {}

    def register(self, name: str, check: ReadinessCheck) -> None:
        if name in self._checks:
            raise ValueError(f"Ya hay un check de readiness registrado como {name!r}")
        self._checks[name] = check

    async def run(self) -> dict[str, str]:
        """Ejecuta todos los checks en paralelo. Devuelve nombre → estado."""
        if not self._checks:
            return {}
        names = list(self._checks)
        results = await asyncio.gather(*(self._run_one(name, self._checks[name]) for name in names))
        return dict(zip(names, results, strict=True))

    @staticmethod
    async def _run_one(name: str, check: ReadinessCheck) -> str:
        try:
            await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("Check de readiness %r agotó el tiempo de espera", name)
            return "timeout"
        except Exception:
            logger.warning("Check de readiness %r falló", name, exc_info=True)
            return "fail"
        return "ok"


class HealthResponse(BaseModel):
    status: Literal["ok"]
    name: str
    version: str
    env: str


class ReadyResponse(BaseModel):
    status: Literal["ready", "degraded"]
    checks: dict[str, str]


@router.get("/health", response_model=HealthResponse, summary="Liveness")
async def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        name=settings.APP_NAME,
        version=settings.APP_VERSION,
        env=settings.APP_ENV,
    )


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness",
    responses={503: {"model": ReadyResponse, "description": "Alguna dependencia no responde"}},
)
async def ready(request: Request, response: Response) -> ReadyResponse:
    registry: ReadinessRegistry = request.app.state.readiness
    checks = await registry.run()
    healthy = all(state == "ok" for state in checks.values())
    response.status_code = status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ready" if healthy else "degraded", checks=checks)
