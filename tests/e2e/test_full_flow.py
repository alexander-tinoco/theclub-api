"""End-to-end run described in `plan/theclub-api-PLAN.md`
("End-to-end verification", step 5): register → note the seed hash →
deposit → connect to the WS → bet → see the result over HTTP and WS →
rotate the seed → recompute the result by hand with the revealed
server_seed and check it matches.

Unlike `tests/integration/test_ws.py` and
`tests/integration/test_roulette.py`, which test each piece separately
with doubles and edge cases, this file isn't after edge-case coverage — it
checks that the full chain, the way a real player would walk it, doesn't
break at any seam.
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


async def test_full_end_to_end_flow(integration_settings: Settings) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Register.
            register = await client.post(
                "/api/v1/auth/register",
                json={"email": f"{uuid.uuid4()}@example.com", "password": "a-long-password"},
            )
            assert register.status_code == 201
            access_token = register.json()["access_token"]
            headers = {"Authorization": f"Bearer {access_token}"}

            # 2. GET /fairness/current — noted BEFORE betting: this is what
            # the player could publish on their own to call out the house
            # for manipulating the outcome after the fact.
            fairness_before = await client.get("/api/v1/roulette/fairness/current", headers=headers)
            assert fairness_before.status_code == 200
            server_seed_hash_before = fairness_before.json()["server_seed_hash"]
            client_seed = fairness_before.json()["client_seed"]
            assert fairness_before.json()["nonce"] == 0

            # 3. Deposit.
            deposit = await client.post(
                "/api/v1/wallet/deposit",
                json={"amount_minor": DEPOSIT_MINOR},
                headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            assert deposit.status_code == 200
            assert deposit.json()["balance_minor"] == DEPOSIT_MINOR

            # 4. Connect to the WS and, with the connection already open, bet.
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

            # 5. The result is the same over both channels.
            assert ws_message["type"] == "round.settled"
            assert ws_message["round_id"] == round_body["round_id"]
            assert ws_message["outcome"] == round_body["outcome"]
            assert ws_message["balance_minor"] == round_body["balance_minor"]

            balance_after_bet = await client.get("/api/v1/wallet/balance", headers=headers)
            assert balance_after_bet.json()["balance_minor"] == round_body["balance_minor"]
            assert round_body["balance_minor"] == DEPOSIT_MINOR + round_body["net_minor"]

            # 6. Rotate: reveals the server_seed that produced the spin above.
            rotate = await client.post("/api/v1/roulette/fairness/rotate", headers=headers)
            assert rotate.status_code == 200
            rotate_body = rotate.json()

    # 7. Commit-reveal: the revealed hash matches the one published
    # *before* spinning — if it didn't match, the house could have swapped
    # server_seed mid-game without anyone noticing.
    assert rotate_body["revealed_server_seed_hash"] == server_seed_hash_before
    assert (
        hashlib.sha256(bytes.fromhex(rotate_body["revealed_server_seed"])).hexdigest()
        == server_seed_hash_before
    )

    # 8. Recompute the result by hand, exactly like a player who doesn't
    # trust the backend would: same client_seed seen in step 2, same
    # already-revealed server_seed, nonce=1 (the first spin consumed by
    # this seed pair — see `SeedPairRepository.consume_nonce`).
    seed = SeedMaterial(
        server_seed=bytes.fromhex(rotate_body["revealed_server_seed"]),
        client_seed=client_seed,
        nonce=1,
    )
    assert derive_outcome(seed, modulus=POCKET_COUNT) == round_body["outcome"]
