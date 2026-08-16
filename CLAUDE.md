# theclub-api — guía de trabajo con Claude Code

## Qué es este repo

Motor de juego de The Club (portafolio): resuelve rondas de ruleta, gestiona balances
y publica eventos a Kafka. El plan completo, con contexto, decisiones y fases, vive en
[`plan/theclub-api-PLAN.md`](plan/theclub-api-PLAN.md) — léelo antes de tocar nada si
hace falta contexto de por qué existe una pieza.

## Ciclo de trabajo por roles (sin multiagentes)

Cada fase del plan se recorre pidiendo un rol a la vez. No mezclar roles dentro de una
misma petición salvo que el usuario lo pida explícitamente:

| Rol | Se pide con | Entrega | No hace |
|---|---|---|---|
| **Arquitecto** | "Diseña la fase N" | Estructura de archivos, firmas, esquema de tablas/eventos, decisiones y trade-offs | No escribe implementación |
| **Implementador** | "Implementa X" | Código de producción de un módulo acotado | No inventa alcance, no escribe tests salvo que se fusione con Tester |
| **Tester** | "Escribe los tests de X" | Unitarios, de propiedades, integración; casos límite y de fallo | No modifica producción para que los tests pasen |
| **Revisor** | "Revisa la fase N" o `/code-review` | Bugs, condiciones de carrera, huecos de seguridad, simplificaciones | No aplica cambios sin que se le pida |
| **Integrador** | "Cierra la fase N" | Migración Alembic, contratos/OpenAPI, README, commit | No arranca la fase siguiente |

Orden dentro de una fase: Arquitecto → Implementador → Tester → Revisor → Integrador.
No se avanza de fase hasta cumplir su *Definition of Done* (definida en el plan). Si algo
revela que una decisión previa estaba mal, se actualiza `plan/theclub-api-PLAN.md`
**antes** de seguir escribiendo código.

## Reglas de commits

- **Nunca** añadir co-autoría de Claude ni el trailer `Co-Authored-By` — los commits van
  a nombre del usuario únicamente.
- **Nunca** commitear sin que el usuario lo pida explícitamente.
- Después de cada commit, explicar en el chat, en prosa (no solo el diff):
  - **Qué** archivos se tocaron y **qué representa** cada uno.
  - **Por qué** existen esas piezas — la motivación o el problema que resuelven.
  - **Cómo funcionan** — el mecanismo, no solo una paráfrasis del nombre de la función.
  - Esta explicación es obligatoria incluso si el commit parece pequeño.

## Convenciones del proyecto

- Dinero: siempre `BIGINT` en unidades menores (céntimos), nunca `float`.
- `app/domain/` es núcleo puro: sin IO, sin SQLAlchemy, sin FastAPI. Si un archivo ahí
  necesita importar de `app/models` o `app/infra`, está en el paquete equivocado.
- Eventos a Kafka vía patrón outbox — nunca dual-write (escribir en Postgres y publicar
  en Kafka como pasos separados sin transacción compartida).
- Topics versionados `.v1`; cambiar un schema de evento exige subir versión.
- `/health` = liveness, sin tocar dependencias. `/ready` = readiness, corre checks
  registrados en `app.state.readiness` (ver `app/api/health.py`).

## Comandos

```bash
make up          # levanta postgres + redpanda + console + api
make dev          # api local con recarga, sin rebuild de imagen
make test         # todos los tests
make test-unit    # solo los que no requieren servicios levantados
make check         # lint + typecheck + test (lo que exige CI)
```

Puertos por defecto (parametrizables en `.env` para no chocar con otros proyectos
locales): API `8010`, consola de Redpanda `8090`, Postgres `5432`, Kafka `19092`.
