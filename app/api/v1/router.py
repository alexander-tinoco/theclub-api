"""Ensambla el router de /api/v1 con el prefijo configurado en Settings."""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router


def build_api_v1_router(prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix)
    router.include_router(auth_router)
    return router
