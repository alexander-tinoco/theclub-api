"""Liveness and readiness.

These are two different things, and it pays not to mix them up:

- `/health` (liveness) touches no dependency at all. If it responds, the
  process is alive. This is what Docker watches to decide whether to
  restart the container.
- `/ready` (readiness) runs the registered checks and returns 503 if any
  fail. In Phase 0 the registry is empty; Phase 3 adds Postgres and Phase 6
  adds Kafka **without touching this file**.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app.api.deps import SettingsDep
from app.infra.metrics import render_latest

logger = logging.getLogger(__name__)

#: A check fails by raising an exception; it returns nothing.
ReadinessCheck = Callable[[], Awaitable[None]]

#: A hung dependency must not hang `/ready`.
CHECK_TIMEOUT_SECONDS = 2.0

router = APIRouter(tags=["health"])


class ReadinessRegistry:
    """Readiness checks that later phases register incrementally.

    Lives on `app.state.readiness`, not as a module global, so each
    application created in a test gets its own.
    """

    def __init__(self) -> None:
        self._checks: dict[str, ReadinessCheck] = {}

    def register(self, name: str, check: ReadinessCheck) -> None:
        if name in self._checks:
            raise ValueError(f"A readiness check is already registered as {name!r}")
        self._checks[name] = check

    async def run(self) -> dict[str, str]:
        """Runs every check in parallel. Returns name -> status."""
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
            logger.warning("Readiness check %r timed out", name)
            return "timeout"
        except Exception:
            logger.warning("Readiness check %r failed", name, exc_info=True)
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
    responses={503: {"model": ReadyResponse, "description": "Some dependency isn't responding"}},
)
async def ready(request: Request, response: Response) -> ReadyResponse:
    registry: ReadinessRegistry = request.app.state.readiness
    checks = await registry.run()
    healthy = all(state == "ok" for state in checks.values())
    response.status_code = status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ready" if healthy else "degraded", checks=checks)


@router.get("/metrics", summary="Prometheus metrics")
async def metrics() -> Response:
    """No authentication: in a real deployment this is restricted at the
    network level (not exposed to the internet, only to Prometheus's
    scraper), not with an application token — that's the standard
    convention for this endpoint.
    """
    body, content_type = render_latest()
    return Response(content=body, media_type=content_type)
