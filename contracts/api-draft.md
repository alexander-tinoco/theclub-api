# Borrador de API — REST + WebSocket

Borrador de referencia para que `theclub-web` empiece a maquetar contra algo estable. No
es un OpenAPI generado — eso llega cuando existan los endpoints de verdad (Fases 4, 5 y
7), momento en el que este archivo deja de ser la fuente de la verdad y pasa a serlo
`openapi.json` (generado desde la app, verificado en CI en la Fase 9).

Prefijo común: `/api/v1`. Autenticación: `Authorization: Bearer <access_token>` salvo
donde se indique lo contrario.

## Auth (Fase 4)

| Verbo | Ruta | Auth | Qué hace |
|---|---|---|---|
| POST | `/auth/register` | no | Crea usuario + wallet en cero |
| POST | `/auth/login` | no | Devuelve access + refresh token |
| POST | `/auth/refresh` | no (refresh token en body) | Rota el refresh token; revoca toda la familia si se reusa uno viejo |
| GET | `/auth/me` | sí | Datos del usuario autenticado |

## Wallet (Fase 5)

| Verbo | Ruta | Auth | Qué hace |
|---|---|---|---|
| GET | `/wallet/balance` | sí | Balance actual, en céntimos |
| GET | `/wallet/transactions` | sí | Historial del ledger, paginado por cursor |
| POST | `/wallet/deposit` | sí | Depósito simulado (sin pasarela real) |

## Ruleta (Fase 5)

| Verbo | Ruta | Auth | Qué hace |
|---|---|---|---|
| GET | `/roulette/fairness/current` | sí | Hash del server seed activo + client seed |
| POST | `/roulette/fairness/rotate` | sí | Revela el seed anterior, activa uno nuevo |
| POST | `/roulette/rounds` | sí, + `Idempotency-Key` obligatorio | Coloca una o más apuestas, resuelve la ronda y devuelve el resultado |
| GET | `/roulette/rounds` | sí | Historial de rondas, paginado por cursor |

## WebSocket (Fase 7)

| Ruta | Auth | Qué emite |
|---|---|---|
| `/ws` | token en query string o subprotocolo | `round.settled`, `balance.updated` — la misma forma de `data` que sus eventos de Kafka homónimos |
