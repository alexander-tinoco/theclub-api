# Plan de acción — theclub-api

## Contexto

The Club es una plataforma de casino construida como proyecto de portafolio, dividida en 3 repositorios que se conectan entre sí:

- **theclub-web**: la interfaz que juega el usuario.
- **theclub-api** (este repo): el backend que calcula resultados de juego y publica eventos a Kafka.
- **theclub-data**: el pipeline de datos (Kafka → S3 → Databricks) que convierte esos eventos en analítica de negocio.

`theclub-api` es el motor de juego: calcula resultados, gestiona balances y sesiones, y publica cada evento a Kafka para que `theclub-data` lo procese. Hoy el repositorio está **vacío** (sin commits), igual que `theclub-web` y `theclub-data`. Por lo tanto este repo no solo se construye a sí mismo: **define los contratos** (REST, WebSocket y esquema de eventos) de los que dependen los otros dos. Un cambio tardío en el esquema de eventos rompe el pipeline de datos, así que los contratos se congelan y versionan temprano.

### Stack

Python + FastAPI · PostgreSQL · SQLAlchemy 2.0 + Alembic · aiokafka · JWT · pytest · Docker + GitHub Actions.

### Decisiones tomadas

| Pregunta abierta | Decisión |
|---|---|
| Kafka gestionado o local | **Redpanda en docker-compose** (API compatible con Kafka), con la config aislada para migrar a Confluent Cloud cambiando solo variables de entorno |
| Un topic o varios | **Un topic por tipo de evento**, con nombres `theclub.<dominio>.<evento>.v1` |
| REST o GraphQL | **REST + WebSocket** |
| Hosting de la BD | **Postgres en Docker** para dev y tests; Neon cuando toque desplegar |
| Juegos del MVP | **Solo ruleta europea**, hecha a fondo |
| RNG | **Provably fair**: HMAC-SHA256 sobre `server_seed + client_seed + nonce`, con commit-reveal |
| Deploy | **Sin deploy por ahora**; CI en GitHub Actions (lint, tipos, tests, build de imagen) |
| Gestor de dependencias | **uv** (`uv.lock` versionado, `uv sync --frozen` en CI) |
| Versión de Python | **3.14**, gestionada por uv en local y en la imagen Docker. Riesgo asumido: si alguna dependencia binaria no trae wheel para 3.14 (candidatas: `psycopg` en Fase 3, `aiokafka` en Fase 6), el arreglo es bajar a 3.13 en `.python-version` y el Dockerfile — no toca código |

### Fuera de alcance (explícito)

Tragamonedas, blackjack, dinero real o pasarela de pago, Avro + Schema Registry, despliegue en la nube, multi-instancia con Redis. Todo eso queda como fase posterior y el diseño deja el hueco preparado, pero no se construye ahora.

---

## Ciclo de trabajo (estilo swarm-forge, ejecutado por nosotros dos)

No hay multiagentes. Cada fase la recorremos secuencialmente y **tú pides un rol a la vez**. El valor está en no mezclar roles: cuando pides código no se discute diseño, y cuando pides revisión no se escribe funcionalidad nueva.

| Rol | Qué se pide | Qué entrega | Qué NO hace |
|---|---|---|---|
| **Arquitecto** | "Diseña la fase N" | Estructura de archivos, firmas de funciones, esquema de tablas o de eventos, decisiones y trade-offs | No escribe implementación |
| **Implementador** | "Implementa X según el diseño" | Código de producción de un módulo acotado, siguiendo el diseño acordado | No inventa alcance ni escribe los tests |
| **Tester** | "Escribe los tests de X" | Tests unitarios, de propiedades e integración, incluyendo casos límite y de fallo | No modifica el código de producción para que pasen |
| **Revisor** | "Revisa la fase N" o `/code-review` | Bugs de corrección, condiciones de carrera, huecos de seguridad, simplificaciones | No aplica cambios sin que se le pida |
| **Integrador** | "Cierra la fase N" | Migración Alembic, actualización de contratos/OpenAPI, README, commit limpio | No arranca la fase siguiente |

**Regla de avance:** no se pasa a la fase siguiente hasta que la actual cumple su *Definition of Done* (cada fase trae la suya abajo). Si una fase revela que una decisión previa estaba mal, se actualiza este documento **antes** de seguir escribiendo código.

**Orden dentro de cada fase:** Arquitecto → Implementador → Tester → Revisor → Integrador. Para fases pequeñas se puede fusionar Implementador y Tester en una sola petición ("implementa X con sus tests"), pero Revisor siempre va aparte.

---

## Arquitectura

### Estructura de carpetas

```
theclub-api/
├── app/
│   ├── main.py                  # ensamblado FastAPI, lifespan, routers
│   ├── config.py                # Settings con pydantic-settings (12-factor)
│   ├── api/
│   │   ├── deps.py              # inyección: sesión DB, usuario actual, idempotencia
│   │   ├── errors.py            # excepciones de dominio → respuestas HTTP
│   │   └── v1/
│   │       ├── auth.py          # /register, /login, /refresh, /me
│   │       ├── wallet.py        # /balance, /transactions, /deposit
│   │       ├── roulette.py      # /bets, /rounds, /fairness
│   │       └── ws.py            # /ws — resultados y balance en vivo
│   ├── domain/                  # NÚCLEO PURO: sin IO, sin SQLAlchemy, sin FastAPI
│   │   ├── money.py             # tipo Money sobre enteros (unidades menores)
│   │   ├── fairness.py          # commit-reveal, HMAC, muestreo sin sesgo
│   │   └── roulette/
│   │       ├── table.py         # 37 casillas, tipos de apuesta, payouts
│   │       ├── bets.py          # validación y resolución de apuestas
│   │       └── engine.py        # spin(seed_material) -> Resultado
│   ├── models/                  # SQLAlchemy 2.0 (Mapped/mapped_column)
│   ├── repositories/            # acceso a datos; sin lógica de negocio
│   ├── services/                # casos de uso: place_bet, deposit, register…
│   ├── events/
│   │   ├── schemas.py           # envelope + payloads (Pydantic)
│   │   ├── outbox.py            # escritura del outbox dentro de la transacción
│   │   └── relay.py             # publicador outbox → Kafka
│   └── infra/
│       ├── db.py                # engine async, sesión, unit of work
│       ├── kafka.py             # productor (aiokafka), retry, cierre limpio
│       └── security.py          # hashing argon2, emisión/validación JWT
├── contracts/                   # ARTEFACTO COMPARTIDO con los otros dos repos
│   ├── openapi.json             # generado, versionado en git
│   └── events/*.schema.json     # JSON Schema por tipo de evento
├── alembic/versions/
├── tests/{unit,integration,e2e}/
├── plan/                        # este documento
├── docker-compose.yml           # postgres + redpanda + consola
├── Dockerfile
└── .github/workflows/ci.yml
```

**Regla dura:** `app/domain/` no importa nada de `app/models`, `app/infra` ni FastAPI. Es matemática pura, testeable sin levantar nada. Esto es lo que permite simular millones de giros en un test para verificar el RTP.

### Modelo de datos

Postgres. Dinero en **`BIGINT` de unidades menores (céntimos)** — nunca float, nunca `Numeric` con redondeo ambiguo. Todos los timestamps en `TIMESTAMPTZ` UTC.

- **`users`** — `id` (UUID), `email` (único, citext), `password_hash` (argon2id), `status`, `created_at`.
- **`wallets`** — `user_id` (único), `balance_minor` (BIGINT, `CHECK >= 0`), `currency`, `version` (bloqueo optimista).
- **`ledger_entries`** — libro append-only, la fuente de verdad del dinero: `wallet_id`, `amount_minor` (con signo), `balance_after_minor`, `kind` (`deposit|bet_stake|bet_payout|adjustment`), `ref_type`, `ref_id`, `created_at`. El balance del wallet es una caché de la suma del ledger; un test de invariante lo comprueba.
- **`seed_pairs`** — provably fair: `user_id`, `server_seed` (revelado solo al rotar), `server_seed_hash` (público), `client_seed`, `nonce` (contador), `status` (`active|revealed`), `revealed_at`.
- **`rounds`** — una ronda de ruleta: `id`, `user_id`, `seed_pair_id`, `nonce`, `outcome` (0–36), `status`, `created_at`, `settled_at`.
- **`bets`** — `round_id`, `bet_type`, `selection` (JSONB), `stake_minor`, `payout_minor`, `status`.
- **`idempotency_keys`** — `key`, `user_id`, `request_hash`, `response_body`, `status`, `expires_at`. Único por `(user_id, key)`.
- **`outbox`** — `id`, `topic`, `key`, `payload` (JSONB), `headers`, `created_at`, `published_at`, `attempts`, `last_error`. Índice parcial sobre `published_at IS NULL`.

### Los tres invariantes que sostienen todo

1. **El dinero nunca se pierde ni se duplica.** Cada movimiento es una entrada en `ledger_entries` dentro de la misma transacción que actualiza `wallets`. El débito usa `UPDATE wallets SET balance_minor = balance_minor - :stake WHERE id = :id AND balance_minor >= :stake RETURNING balance_minor` — atómico en una sentencia, sin lectura previa, así dos apuestas simultáneas no pueden sobregirar. Si afecta 0 filas → `InsufficientFunds`.
2. **Un evento se publica si y solo si su transacción hizo commit.** Nada de escribir en Postgres y publicar en Kafka en paralelo (*dual write*): si Kafka falla después del commit, `theclub-data` pierde un evento para siempre. En su lugar, **patrón outbox**: el servicio inserta el evento en la tabla `outbox` en la *misma* transacción, y un relay en background lo publica y lo marca. Kafka caído = eventos acumulados, cero pérdida, se drenan al volver.
3. **Un resultado es reproducible y verificable.** `outcome = f(server_seed, client_seed, nonce)`, determinista. Cualquiera puede recalcularlo con los datos que exponemos tras el reveal.

### Provably fair — cómo funciona

1. Al registrarse (o al rotar), se genera `server_seed` de 32 bytes con `secrets.token_bytes`. Se guarda, y se publica solo `sha256(server_seed)`.
2. El cliente puede fijar su `client_seed`; si no, se le asigna uno.
3. Cada giro consume `nonce` (incremental, único por par de semillas).
4. `digest = HMAC-SHA256(key=server_seed, msg=f"{client_seed}:{nonce}")`.
5. Del digest se deriva un entero uniforme en 0–36 con **rejection sampling**: se toman 4 bytes, si el valor cae en el rango sesgado del módulo se avanza a los siguientes 4 bytes. Nunca `int(digest) % 37` a secas — introduce un sesgo medible que un test de uniformidad detecta.
6. Al rotar semillas se revela el `server_seed` anterior; el jugador verifica que su hash coincide y recalcula sus giros.

`GET /fairness/current` devuelve hash y client seed; `POST /fairness/rotate` revela el anterior y activa uno nuevo.

### Ruleta europea

37 casillas (0–36), un solo cero. Payouts exactos, todos con **ventaja de casa de 2.70%** (RTP 97.30%):

| Apuesta | Cobertura | Paga | Apuesta | Cobertura | Paga |
|---|---|---|---|---|---|
| Straight (pleno) | 1 | 35:1 | Dozen | 12 | 2:1 |
| Split | 2 | 17:1 | Column | 12 | 2:1 |
| Street | 3 | 11:1 | Red/Black | 18 | 1:1 |
| Corner | 4 | 8:1 | Odd/Even | 18 | 1:1 |
| Line | 6 | 5:1 | High/Low | 18 | 1:1 |

El 0 no es rojo ni negro, ni par ni impar, ni alto ni bajo: pierde todas las apuestas externas (sin *la partage*, se documenta la elección). La tabla de rojos es un conjunto literal, no una fórmula. Una ronda admite varias apuestas: se validan todas, se suma el stake total, se debita una vez y se resuelve cada una.

### Contrato de eventos hacia Kafka

Topics (uno por tipo, particionados por `user_id` para preservar el orden por usuario):

- `theclub.bets.placed.v1`
- `theclub.rounds.settled.v1`
- `theclub.wallet.transactions.v1`

Envelope común en todos:

```json
{
  "event_id": "uuid",
  "event_type": "round.settled",
  "event_version": 1,
  "occurred_at": "2026-08-16T12:00:00Z",
  "producer": "theclub-api",
  "idempotency_key": "…",
  "data": { }
}
```

Reglas: `event_id` permite deduplicar aguas abajo; `occurred_at` es hora de negocio (no de publicación); montos siempre como enteros en unidades menores con su `currency`; nada de PII más allá del `user_id` (UUID) — sin emails en el stream. JSON para el MVP, pero los JSON Schema viven en `contracts/events/` y **el CI falla si el schema cambia sin subir la versión**. Ese es el hueco por donde entra Avro + Schema Registry más adelante sin drama.

---

## Fases

Cada fase termina con su *Definition of Done* cumplida y un commit.

### Fase 0 — Fundación
Estructura del repo, `pyproject.toml` (uv o poetry), `ruff` + `mypy` estricto, `docker-compose.yml` (Postgres + Redpanda + consola), `Dockerfile` multi-stage, `Settings` con pydantic-settings, `/health` y `/ready`, `.env.example`, `pytest` configurado.
**DoD:** `docker compose up` levanta todo; `GET /health` responde 200; `pytest` corre (aunque sea con 1 test trivial).

### Fase 1 — Contratos (antes que cualquier lógica)
JSON Schema de los tres eventos en `contracts/events/`, modelos Pydantic del envelope, y borrador de los endpoints REST/WS documentado. Es lo primero porque `theclub-web` y `theclub-data` no pueden empezar sin esto.
**DoD:** schemas validados con ejemplos; documento de contrato legible por los otros dos repos.

### Fase 2 — Dominio puro: fairness + ruleta
`app/domain/` completo: `Money`, commit-reveal, HMAC, muestreo sin sesgo, tabla de ruleta, validación y resolución de apuestas. Cero IO.
**DoD:** tests unitarios de payouts para los 13 tipos de apuesta; test de reproducibilidad (mismas semillas → mismo resultado); test de uniformidad sobre ≥1M de giros (chi-cuadrado); test de RTP que converge a 97.30% ± margen; property-based con `hypothesis` para que ningún payout sea negativo ni desborde.

### Fase 3 — Persistencia
Modelos SQLAlchemy 2.0 async, migración inicial de Alembic, repositorios, unit of work.
**DoD:** `alembic upgrade head` y `downgrade base` limpios; test de invariante ledger↔balance; test de concurrencia: N apuestas simultáneas sobre el mismo wallet nunca dejan el balance negativo.

### Fase 4 — Autenticación
Registro/login con argon2id, JWT access (corto) + refresh (rotativo, con revocación), `get_current_user`, rate limiting en los endpoints de auth.
**DoD:** tests de token expirado, firma inválida, refresh reusado (debe revocar la familia), email duplicado, y que el hash de contraseña jamás aparezca en una respuesta ni en un log.

### Fase 5 — Caso de uso de apuesta (el corazón)
`POST /rounds` con `Idempotency-Key` obligatorio: valida apuestas → debita atómicamente → deriva resultado → calcula payout → acredita → escribe en outbox → responde. Todo en una transacción. Más `/balance`, `/transactions`, `/rounds` (historial paginado por cursor), `/deposit` (simulado).
**DoD:** tests de fondos insuficientes, stake ≤ 0, apuesta malformada, límites de mesa, reintento con la misma `Idempotency-Key` (misma respuesta, sin doble cobro), y misma clave con cuerpo distinto (409).

### Fase 6 — Kafka
Productor `aiokafka` con `acks=all`, `enable_idempotence`, reintentos y cierre limpio. Relay del outbox como tarea de background con backoff exponencial y bloqueo `FOR UPDATE SKIP LOCKED` (seguro con varias instancias).
**DoD:** test de integración con Redpanda real: apuesta → los 3 eventos aparecen en sus topics con la forma del schema; test de Kafka caído → los eventos quedan en outbox y se drenan al restaurarlo, sin duplicar dinero.

### Fase 7 — WebSocket
`/ws` autenticado por token, con canal por usuario, heartbeat ping/pong, límite de conexiones y cierre ordenado. Emite `round.settled` y `balance.updated`.
**DoD:** test e2e: cliente WS conectado recibe el resultado tras el POST; token inválido → cierre 4401; el broadcaster en memoria queda tras una interfaz para poder cambiarlo por Redis pub/sub sin tocar los handlers.

### Fase 8 — Endurecimiento y observabilidad
Logging estructurado JSON con `request_id` y `user_id`, manejador global de excepciones que nunca filtra internals, CORS restringido, rate limiting global, límites de tamaño de payload, `/metrics` opcional. Revisión de seguridad completa.
**DoD:** `/code-review` y `/security-review` sin hallazgos altos; ningún secreto en el repo; los logs no contienen semillas activas ni tokens.

### Fase 9 — CI
GitHub Actions: ruff, mypy, pytest con cobertura sobre servicios de Postgres + Redpanda, build del Dockerfile, verificación de que `openapi.json` y los JSON Schema commiteados están al día, y `alembic check` contra deriva de modelos.
**DoD:** CI en verde en un PR; README con arranque en menos de 5 minutos.

---

## Escenarios de fallo que el diseño cubre

| Escenario | Respuesta del diseño |
|---|---|
| Doble clic / reintento de red en una apuesta | `Idempotency-Key` obligatoria; se devuelve la respuesta original |
| Dos apuestas concurrentes con saldo justo | `UPDATE … WHERE balance >= stake` atómico + `CHECK >= 0` en la tabla |
| Kafka caído | Outbox acumula; el juego sigue funcionando; se drena al volver |
| Crash entre commit y publicación | El relay recoge el outbox al reiniciar |
| Consumidor de `theclub-data` que reprocesa | `event_id` permite deduplicar; los eventos son inmutables |
| Cambio en el esquema de un evento | Topics versionados `.v1` + CI que exige subir versión |
| Sesgo en el RNG | Rejection sampling + test de chi-cuadrado en CI |
| Usuario acusa de manipulación | Commit-reveal: recalcula el resultado él mismo |
| Refresh token robado | Rotación con detección de reuso → revoca toda la familia |
| Deriva entre modelos y migraciones | `alembic check` en CI |
| Varias instancias de la API | Relay con `SKIP LOCKED`; WS tras interfaz para Redis pub/sub |

---

## Verificación de punta a punta

1. `docker compose up -d` → Postgres + Redpanda arriba.
2. `alembic upgrade head` → esquema creado.
3. `pytest -m "not integration"` → dominio puro, rápido, incluye RTP y uniformidad.
4. `pytest -m integration` → BD y Kafka reales.
5. Manual: registrar usuario → `GET /fairness/current` (anotar hash) → depositar → conectar al WS → apostar → ver resultado por HTTP y por WS → `POST /fairness/rotate` → recalcular el resultado a mano con el seed revelado y comprobar que coincide.
6. Consola de Redpanda (`localhost:8090`) → los tres topics con sus mensajes.
7. Reiniciar el contenedor de Redpanda a mitad de una tanda de apuestas → verificar que ningún evento se pierde y que ningún balance queda mal.

---

## Primer paso

Fase 0, rol **Arquitecto**: proponer la estructura concreta de archivos y el `docker-compose.yml` antes de escribir código.
