"""Dependencias compartidas de la capa API.

Las fases posteriores añaden aquí la sesión de base de datos, el usuario
autenticado y el guardián de idempotencia.
"""

from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings


def get_app_settings(request: Request) -> Settings:
    """Settings de *esta* aplicación.

    Se leen de `app.state` y no del caché global de `get_settings()` para que
    una app creada con settings explícitos (los tests, sobre todo) se comporte
    exactamente como está configurada.
    """
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
