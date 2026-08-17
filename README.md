# theclub-api

Motor de juego de **The Club**: resuelve rondas de ruleta, gestiona balances y publica cada
evento a Kafka para que `theclub-data` lo procese.

El plan completo del proyecto está en [`plan/theclub-api-PLAN.md`](plan/theclub-api-PLAN.md). El
contrato de eventos y el borrador de API hacia `theclub-web`/`theclub-data` está en
[`contracts/`](contracts/).

## Requisitos

- [uv](https://docs.astral.sh/uv/) (gestiona también el intérprete de Python)
- Docker y Docker Compose

## Arranque

```bash
uv sync                 # crea .venv con Python 3.14 y las dependencias
cp .env.example .env    # opcional: en local todo tiene defaults
make up                 # postgres + redpanda + console + api
```

Comprobación rápida:

```bash
curl localhost:8010/health   # {"status":"ok",...}
curl localhost:8010/ready    # {"status":"ready","checks":{"database":"ok"}}
```

| Servicio | URL | Variable para cambiar el puerto |
|---|---|---|
| API | http://localhost:8010 | `API_PORT` |
| Documentación interactiva | http://localhost:8010/docs | — |
| Consola de Redpanda | http://localhost:8090 | `CONSOLE_PORT` |
| Postgres | `localhost:5432` (`theclub` / `theclub`) | `POSTGRES_PORT` |
| Kafka desde el host | `localhost:19092` | `KAFKA_PORT` |
| Kafka dentro de compose | `redpanda:9092` | — |

Los puertos publicados en el host se eligieron para no chocar con los 8000/8080 que
suelen ocupar otros proyectos. Si alguno te estorba, cámbialo en tu `.env`.

Para desarrollar con recarga automática sin reconstruir la imagen:

```bash
make dev
```

## Comandos

```bash
make test        # todos los tests
make test-unit   # solo los que no necesitan servicios levantados
make lint        # ruff (lint + formato)
make typecheck   # mypy en modo estricto
make check       # lo mismo que exige el CI
make reset       # tira los servicios y BORRA los volúmenes

make db-upgrade   # aplica las migraciones pendientes
make db-downgrade # revierte la última migración
make db-revision m="mensaje"  # autogenera una migración a partir de los modelos
```

## Endpoints

- `GET /health` — *liveness*. No consulta ninguna dependencia; si responde, el proceso vive.
- `GET /ready` — *readiness*. Ejecuta los checks registrados y devuelve `503` si alguno falla.
  Desde la Fase 3 registra `database` (un `SELECT 1` contra Postgres); la Fase 6 añadirá `kafka`.

## Arquitectura

`app/domain/` es el único paquete que no depende de nada externo (sin FastAPI, sin
SQLAlchemy, sin IO) — es matemática pura sobre dinero, fairness y ruleta. Todo lo demás
depende de él, nunca al revés:

```mermaid
flowchart TB
    subgraph API["app/api — HTTP"]
        health["health.py<br/>/health · /ready"]
    end

    subgraph EVENTS["app/events — contratos hacia Kafka"]
        schemas["schemas.py<br/>EventEnvelope, BetPlacedData,<br/>RoundSettledData, WalletTransactionData"]
    end

    subgraph DOMAIN["app/domain — nucleo puro, sin IO"]
        money["money.py<br/>Money"]
        fairness["fairness.py<br/>SeedMaterial · derive_outcome"]
        table["roulette/table.py<br/>BetType · BetSpec · geometria de la mesa"]
        bets["roulette/bets.py<br/>PlacedBet · validate_bet"]
        engine["roulette/engine.py<br/>spin · resolve_bets"]
    end

    subgraph PERSISTENCE["app/models + app/repositories + app/infra"]
        models["models/*<br/>User · Wallet · LedgerEntry · SeedPair<br/>Round · Bet · IdempotencyKey · OutboxEvent"]
        repos["repositories/*<br/>WalletRepository · LedgerRepository"]
        db_infra["infra/db.py<br/>engine async · unit_of_work"]
    end

    subgraph KAFKA["Fase 6 — pendiente"]
        kafka[("Kafka / Redpanda")]
    end

    postgres[("Postgres")]

    EVENTS --> DOMAIN
    API --> PERSISTENCE
    API -. Fase 5 .-> DOMAIN
    EVENTS -. Fase 6 .-> KAFKA

    repos --> models
    repos --> db_infra
    db_infra --> postgres

    bets --> table
    bets --> money
    engine --> bets
    engine --> fairness
    engine --> money
```

Las líneas punteadas son integraciones que todavía no existen (se activan en la fase
indicada); las sólidas ya están escritas y probadas.

### El dominio de la ruleta

```mermaid
classDiagram
    class Money {
        +int minor
        +str currency
        +zero(currency) Money
    }

    class SeedMaterial {
        +bytes server_seed
        +str client_seed
        +int nonce
    }

    class BetType {
        <<enumeration>>
        STRAIGHT
        SPLIT
        STREET
        CORNER
        LINE
        DOZEN
        COLUMN
        RED
        BLACK
        ODD
        EVEN
        HIGH
        LOW
    }

    class BetSpec {
        +int coverage
        +int payout_ratio
    }

    class PlacedBet {
        +BetType bet_type
        +Selection selection
        +Money stake
    }

    class ResolvedBet {
        +PlacedBet bet
        +bool won
        +Money payout
    }

    PlacedBet --> BetType
    PlacedBet --> Money : stake
    ResolvedBet --> PlacedBet : bet
    ResolvedBet --> Money : payout
    BetType --> BetSpec : BET_SPECS
```

`payout = stake * (payout_ratio + 1)` si gana — el `+1` es la devolución del propio
stake, que se debita por adelantado para toda apuesta (gane o pierda). Para las 13
apuestas se cumple `coverage * (payout_ratio + 1) == 36`: es la forma matemática de que
la ventaja de la casa (2.70%) sea idéntica sin importar qué se apueste.

### Cómo se resuelve un giro

```mermaid
sequenceDiagram
    participant S as Servicio Fase 5
    participant B as roulette bets.py
    participant E as roulette engine.py
    participant F as fairness.py

    S->>B: PlacedBet(bet_type, selection, stake)
    S->>B: validate_bet(bet, min_bet, max_bet)
    B-->>S: OK o InvalidBetError

    S->>E: spin(seed)
    E->>F: derive_outcome(seed, modulus=37)
    F-->>E: outcome (0-36)
    E-->>S: outcome

    S->>E: resolve_bets(bets, outcome)
    E->>B: covered_numbers(bet_type, selection)
    B-->>E: numeros cubiertos
    E-->>S: [ResolvedBet(won, payout), ...]
```

### Provably fair — commit-reveal

```mermaid
sequenceDiagram
    participant J as Jugador
    participant A as theclub-api

    Note over A: genera server_seed (32 bytes)
    A->>J: hash_seed(server_seed)
    Note over J,A: el hash se publica antes de que exista ninguna apuesta

    loop cada giro: nonce = 0, 1, 2...
        J->>A: apuesta + client_seed
        A->>A: derive_outcome(seed, modulus=37)
        A->>J: resultado
    end

    Note over A: al rotar de semilla
    A->>J: revela server_seed
    J->>J: sha256(server_seed) == hash publicado?
    J->>J: recalcula cada giro con el algoritmo publico
```

### Persistencia

- **Sin columna de bloqueo optimista en `wallets`.** El plan original la incluía, pero
  `debit`/`credit` son una sola sentencia `UPDATE ... WHERE ... RETURNING` — sin lectura
  previa, sin carrera que un `version` deba prevenir. El repositorio no expone (ni
  expondrá) un `set_balance`, que es lo único que sí necesitaría uno.
- **`status`/`kind` como `String` + `CHECK`, no `ENUM` nativo de Postgres** — añadir un
  valor nuevo es una migración normal, no un `ALTER TYPE` con las restricciones
  históricas de Postgres para tipos enumerados.
- **Convención de nombres en `models/base.py`** para que `alembic revision --autogenerate`
  reconozca constraints entre entornos en vez de generarles nombres aleatorios.
- **Índice único parcial en `seed_pairs`** (`WHERE status = 'active'`): la base de datos
  garantiza sola que un usuario nunca tenga dos semillas activas a la vez.
- **Índice parcial en `outbox`** (`WHERE published_at IS NULL`): la consulta del relay
  (Fase 6) sigue siendo barata aunque la tabla acumule millones de filas ya publicadas.

## Calidad y testing

### Metodología

No es TDD estricto (rojo-verde-refactor). El flujo real es: diseño en prosa primero
(estructura de archivos, firmas, decisiones — sin código), luego implementación y tests
juntos en el mismo paso, con revisión antes de cada commit. La disciplina que aporta el
TDD clásico —decidir la interfaz desde el punto de vista de quien la usa, antes de saber
cómo se implementa— la cubre aquí la fase de diseño explícito en vez del test que falla
primero.

### Clean code — con matices, no como eslogan

A favor: `app/domain/` es honestamente puro (nada de IO, comprobado por el hecho de que
1M de giros simulados corren en ~4s sin Docker); funciones cortas; nombres que no
necesitan comentario al lado; los 13 tipos de apuesta viven en un solo sitio
(`table.py`) y todo lo demás los reutiliza en vez de duplicarlos.

Compromisos conocidos, no maquillados: `covered_numbers()` en `bets.py` es la función
más densa del proyecto (tres formas de validar selección en una sola función); los
tests de dominio importan símbolos privados (`_FIXED_SELECTIONS` y similares) del
módulo que prueban, que es whitebox testing aceptado pero no "clean" en sentido
estricto; y `test_roulette_engine.py` reutiliza un generador de datos definido en
`test_roulette_bets.py` en vez de vivir en un módulo de fixtures separado.

Una trampa real que encontramos en la Fase 3, documentada para que no se repita: Python
3.14 difiere la evaluación de anotaciones (PEP 649), así que `ruff --fix` propone quitar
las comillas de forward-refs como `Mapped["Wallet"]`. Es **seguro** cuando las dos clases
viven en el mismo archivo (`Round`/`Bet` en `round.py`) porque para cuando SQLAlchemy
configura los mappers, el módulo ya terminó de cargar y el nombre existe. Es **un
`NameError` en tiempo de configuración de mappers** cuando la clase referenciada solo se
importa bajo `TYPE_CHECKING` en otro módulo (`User`/`Wallet` entre sí) — lo comprobamos
con un script aislado antes de decidir qué líneas llevan `# noqa: UP037` a propósito.

### Migraciones excluidas del lint

`alembic/versions/` está fuera de `ruff` (`extend-exclude`) y fuera de `mypy` (no incluido
en `files`). Alembic las genera con su propio estilo (`Union[...]`, comillas simples) y
reescribirlas a mano cada vez que se regeneran no aporta nada — `alembic/env.py`, que sí
escribimos a mano, sigue linteado normalmente.

### Cobertura

```
TOTAL   529 stmts   5 miss   58 branch   4 partial   98% cover
```

Sin umbral mínimo en CI todavía (`pytest-cov` está configurado; el `--cov-fail-under`
se decide en la Fase 9). Los dos huecos que quedan están identificados, no son
descuido — el de `app/main.py` que había en la Fase 0 (el `lifespan` sin ejercitar
porque `ASGITransport` no lo dispara) se cerró en esta fase con `asgi-lifespan`, tal
como quedó prometido:

| Dónde | Qué falta cubrir | Por qué |
|---|---|---|
| `app/domain/fairness.py` | La rama de rejection sampling que pide un HMAC extra | Probabilidad ~10⁻⁹ de que ocurra; forzarla exigiría mockear `hmac` para un caso de valor dudoso |
| `app/api/health.py` | El timeout de un readiness check | Necesitaría un check que duerma de verdad; el camino de "check que falla" sí está cubierto |

## Estado

Completadas: Fase 0 (fundación), Fase 1 (contratos de eventos), Fase 2 (dominio de
fairness y ruleta), Fase 3 (persistencia). Siguiente: Fase 4 — autenticación (JWT,
argon2id, refresh tokens con rotación).
