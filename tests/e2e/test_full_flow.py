"""Recorrido de punta a punta descrito en `plan/theclub-api-PLAN.md`
("Verificación de punta a punta", paso 5): registrar → anotar el hash del
seed → depositar → conectar al WS → apostar → ver el resultado por HTTP y
por WS → rotar el seed → recalcular el resultado a mano con el server_seed
revelado y comprobar que coincide.

A diferencia de `tests/integration/test_ws.py` y `tests/integration/
test_roulette.py`, que prueban cada pieza por separado con dobles y casos
de borde, este archivo no busca cobertura de casos límite — comprueba que
la cadena completa, tal como la recorrería un jugador real, no se rompe en
ningún punto de unión.
"""

import asyncio
import hashlib
import uuid
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient
from httpx_ws import AsyncWebSocketSession, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

from app.config import Settings
from app.domain.fairness import SeedMaterial, derive_outcome
from app.domain.roulette.table import POCKET_COUNT
from app.main import create_app

pytestmark = pytest.mark.e2e

DEPOSIT_MINOR = 100_000
STAKE_MINOR = 1_000


async def test_recorrido_completo_de_punta_a_punta(integration_settings: Settings) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Registro.
            register = await client.post(
                "/api/v1/auth/register",
                json={"email": f"{uuid.uuid4()}@example.com", "password": "contraseña-larga"},
            )
            assert register.status_code == 201
            access_token = register.json()["access_token"]
            headers = {"Authorization": f"Bearer {access_token}"}

            # 2. GET /fairness/current — se anota ANTES de apostar: es lo
            # que el jugador podría publicar por su cuenta para acusar a la
            # casa de manipular el resultado después de los hechos.
            fairness_before = await client.get("/api/v1/roulette/fairness/current", headers=headers)
            assert fairness_before.status_code == 200
            server_seed_hash_before = fairness_before.json()["server_seed_hash"]
            client_seed = fairness_before.json()["client_seed"]
            assert fairness_before.json()["nonce"] == 0

            # 3. Depositar.
            deposit = await client.post(
                "/api/v1/wallet/deposit",
                json={"amount_minor": DEPOSIT_MINOR},
                headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            assert deposit.status_code == 200
            assert deposit.json()["balance_minor"] == DEPOSIT_MINOR

            # 4. Conectar al WS y, con la conexión ya abierta, apostar.
            ws: AsyncWebSocketSession
            async with aconnect_ws(f"http://test/api/v1/ws?token={access_token}", client) as ws:
                round_response = await client.post(
                    "/api/v1/roulette/rounds",
                    json={
                        "bets": [{"bet_type": "red", "selection": {}, "stake_minor": STAKE_MINOR}]
                    },
                    headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                )
                assert round_response.status_code == 201
                round_body = round_response.json()

                ws_message: dict[str, Any] = await asyncio.wait_for(ws.receive_json(), timeout=5)

            # 5. El resultado es el mismo por los dos canales.
            assert ws_message["type"] == "round.settled"
            assert ws_message["round_id"] == round_body["round_id"]
            assert ws_message["outcome"] == round_body["outcome"]
            assert ws_message["balance_minor"] == round_body["balance_minor"]

            balance_after_bet = await client.get("/api/v1/wallet/balance", headers=headers)
            assert balance_after_bet.json()["balance_minor"] == round_body["balance_minor"]
            assert round_body["balance_minor"] == DEPOSIT_MINOR + round_body["net_minor"]

            # 6. Rotar: revela el server_seed que produjo el giro de arriba.
            rotate = await client.post("/api/v1/roulette/fairness/rotate", headers=headers)
            assert rotate.status_code == 200
            rotate_body = rotate.json()

    # 7. Commit-reveal: el hash revelado coincide con el publicado *antes*
    # de girar — si no coincidiera, la casa podría haber cambiado de
    # server_seed a mitad de partida sin que nadie lo notara.
    assert rotate_body["revealed_server_seed_hash"] == server_seed_hash_before
    assert (
        hashlib.sha256(bytes.fromhex(rotate_body["revealed_server_seed"])).hexdigest()
        == server_seed_hash_before
    )

    # 8. Recalcular el resultado a mano, exactamente como lo haría un
    # jugador que desconfía del backend: mismo client_seed visto en el
    # paso 2, mismo server_seed ya revelado, nonce=1 (el primer giro
    # consumido por este par de semillas — ver `SeedPairRepository.
    # consume_nonce`).
    seed = SeedMaterial(
        server_seed=bytes.fromhex(rotate_body["revealed_server_seed"]),
        client_seed=client_seed,
        nonce=1,
    )
    assert derive_outcome(seed, modulus=POCKET_COUNT) == round_body["outcome"]
