"""Ensambla el router de /api/v1 con el prefijo configurado en Settings."""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.roulette import router as roulette_router
from app.api.v1.wallet import router as wallet_router


def build_api_v1_router(prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix)
    router.include_router(auth_router)
    router.include_router(roulette_router)
    router.include_router(wallet_router)
    return router
