"""El DoD de la Fase 4, contra Postgres real: token expirado, firma inválida,
refresh reusado (revoca la familia), email duplicado, y que el hash de
contraseña nunca aparece en una respuesta.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import limiter
from app.config import Settings
from app.infra.security import (
    TokenExpiredError,
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from app.main import create_app
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.services import auth as auth_service

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    # El Limiter es un singleton a nivel de módulo (main.py necesita la misma
    # instancia que los routers para registrar el middleware) — sin resetear
    # su almacenamiento en memoria, los contadores se acumulan entre tests.
    limiter.reset()


@pytest.fixture
async def client(integration_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def _credentials(email: str | None = None) -> dict[str, str]:
    return {"email": email or f"{uuid.uuid4()}@example.com", "password": "contraseña-larga"}


async def test_register_devuelve_tokens(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json=_credentials())

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]


async def test_register_email_duplicado(client: AsyncClient) -> None:
    payload = _credentials()

    await client.post("/api/v1/auth/register", json=payload)
    response = await client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 409


async def test_login_correcto(client: AsyncClient) -> None:
    payload = _credentials()
    await client.post("/api/v1/auth/register", json=payload)

    response = await client.post("/api/v1/auth/login", json=payload)

    assert response.status_code == 200


async def test_login_credenciales_invalidas(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/login", json=_credentials())

    assert response.status_code == 401


async def test_login_usuario_suspendido(client: AsyncClient, db_session: AsyncSession) -> None:
    payload = _credentials()
    await client.post("/api/v1/auth/register", json=payload)

    user = await UserRepository(db_session).get_by_email(payload["email"])
    assert user is not None
    user.status = "suspended"
    await db_session.commit()

    response = await client.post("/api/v1/auth/login", json=payload)

    assert response.status_code == 403


async def test_refresh_con_token_desconocido(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "un-token-que-nunca-existio"}
    )

    assert response.status_code == 401


async def test_me_requiere_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401


async def test_me_con_token_valido(client: AsyncClient) -> None:
    payload = _credentials()
    register = await client.post("/api/v1/auth/register", json=payload)
    access_token = register.json()["access_token"]

    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )

    assert response.status_code == 200
    assert response.json()["email"] == payload["email"]


async def test_me_con_usuario_suspendido_despues_de_emitido_el_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _credentials()
    register = await client.post("/api/v1/auth/register", json=payload)
    access_token = register.json()["access_token"]

    user = await UserRepository(db_session).get_by_email(payload["email"])
    assert user is not None
    user.status = "suspended"
    await db_session.commit()

    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )

    assert response.status_code == 403


async def test_me_con_token_de_usuario_inexistente(
    client: AsyncClient, integration_settings: Settings
) -> None:
    # Un JWT con firma válida pero para un user_id que nunca existió (o que
    # se borró después de emitirse el token).
    ghost_token = create_access_token(
        uuid.uuid4(),
        secret=integration_settings.JWT_SECRET.get_secret_value(),
        algorithm=integration_settings.JWT_ALGORITHM,
        ttl_seconds=900,
    )

    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {ghost_token}"}
    )

    assert response.status_code == 401


async def test_me_con_firma_invalida(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer no-soy-un-jwt-valido"}
    )

    assert response.status_code == 401


async def test_me_con_token_expirado(client: AsyncClient, integration_settings: Settings) -> None:
    expired = create_access_token(
        uuid.uuid4(),
        secret=integration_settings.JWT_SECRET.get_secret_value(),
        algorithm=integration_settings.JWT_ALGORITHM,
        ttl_seconds=-1,
    )

    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401


async def test_refresh_rota_el_token(client: AsyncClient) -> None:
    register = await client.post("/api/v1/auth/register", json=_credentials())
    old_refresh = register.json()["refresh_token"]

    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})

    assert response.status_code == 200
    assert response.json()["refresh_token"] != old_refresh


async def test_refresh_reusado_revoca_toda_la_familia(client: AsyncClient) -> None:
    register = await client.post("/api/v1/auth/register", json=_credentials())
    original_refresh = register.json()["refresh_token"]

    first_rotation = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original_refresh}
    )
    assert first_rotation.status_code == 200
    rotated_refresh = first_rotation.json()["refresh_token"]

    # Reusar el token original (ya rotado) es la señal de un posible robo.
    reused = await client.post("/api/v1/auth/refresh", json={"refresh_token": original_refresh})
    assert reused.status_code == 401

    # Como respuesta, TODA la familia queda revocada -- incluso el token que
    # sí era el legítimo tras la rotación deja de servir.
    legit_but_revoked = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": rotated_refresh}
    )
    assert legit_but_revoked.status_code == 401


async def test_refresh_expirado(db_session: AsyncSession, integration_settings: Settings) -> None:
    user = await UserRepository(db_session).create(
        email=f"{uuid.uuid4()}@example.com", password_hash="x"
    )
    raw_token = generate_refresh_token()
    await RefreshTokenRepository(db_session).create(
        user_id=user.id,
        family_id=uuid.uuid4(),
        token_hash=hash_refresh_token(raw_token),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    await db_session.commit()

    with pytest.raises(TokenExpiredError):
        await auth_service.refresh_access_token(
            db_session, integration_settings, raw_refresh_token=raw_token
        )


async def test_password_hash_nunca_aparece_en_una_respuesta(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    payload = _credentials()
    register_response = await client.post("/api/v1/auth/register", json=payload)
    login_response = await client.post("/api/v1/auth/login", json=payload)
    me_response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {register_response.json()['access_token']}"},
    )

    user = await UserRepository(db_session).get_by_email(payload["email"])
    assert user is not None

    for response in (register_response, login_response, me_response):
        assert user.password_hash not in response.text
        assert "password_hash" not in response.text
        assert "password" not in response.json()


async def test_login_tiene_rate_limit(client: AsyncClient) -> None:
    payload = _credentials()

    responses = [await client.post("/api/v1/auth/login", json=payload) for _ in range(6)]

    assert responses[-1].status_code == 429


async def test_logout_revoca_el_refresh_token(client: AsyncClient) -> None:
    register = await client.post("/api/v1/auth/register", json=_credentials())
    tokens = register.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    logout = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers=headers
    )
    assert logout.status_code == 204

    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 401


async def test_logout_revoca_toda_la_familia_no_solo_el_token_actual(client: AsyncClient) -> None:
    register = await client.post("/api/v1/auth/register", json=_credentials())
    original = register.json()
    headers = {"Authorization": f"Bearer {original['access_token']}"}

    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
    )
    rotated_refresh = rotated.json()["refresh_token"]

    logout = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": rotated_refresh}, headers=headers
    )
    assert logout.status_code == 204

    refresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": rotated_refresh})
    assert refresh.status_code == 401


async def test_logout_es_idempotente(client: AsyncClient) -> None:
    register = await client.post("/api/v1/auth/register", json=_credentials())
    tokens = register.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    body = {"refresh_token": tokens["refresh_token"]}

    first = await client.post("/api/v1/auth/logout", json=body, headers=headers)
    second = await client.post("/api/v1/auth/logout", json=body, headers=headers)

    assert first.status_code == second.status_code == 204


async def test_logout_con_token_desconocido_no_es_error(client: AsyncClient) -> None:
    register = await client.post("/api/v1/auth/register", json=_credentials())
    headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

    response = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": "esto-nunca-existio"}, headers=headers
    )

    assert response.status_code == 204


async def test_logout_con_refresh_token_de_otro_usuario_no_lo_revoca(
    client: AsyncClient,
) -> None:
    victim = await client.post("/api/v1/auth/register", json=_credentials())
    victim_refresh = victim.json()["refresh_token"]

    attacker = await client.post("/api/v1/auth/register", json=_credentials())
    attacker_headers = {"Authorization": f"Bearer {attacker.json()['access_token']}"}

    # El atacante está autenticado como sí mismo, pero manda el refresh token
    # de la víctima en el cuerpo -- no debería poder cerrarle la sesión.
    logout = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": victim_refresh}, headers=attacker_headers
    )
    assert logout.status_code == 204  # idempotente: no revela nada, no es un error

    refresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": victim_refresh})
    assert refresh.status_code == 200  # el token de la víctima sigue vivo


async def test_logout_requiere_access_token(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/logout", json={"refresh_token": "x"})

    assert response.status_code == 401
