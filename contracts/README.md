# contracts/

Este directorio es el contrato público de `theclub-api` hacia los otros dos repos del
proyecto. Si estás en `theclub-web` o `theclub-data`, esto es lo que debes leer — no
hace falta entrar al código de `app/`.

- [`api-draft.md`](api-draft.md) — endpoints REST y WebSocket que expondrá la API
  (borrador; se vuelve `openapi.json` generado de verdad a partir de la Fase 5).
- [`events/`](events/) — los tres eventos que se publican a Kafka: su JSON Schema, un
  ejemplo válido de cada uno, y las convenciones de versionado, particionado y
  deduplicación en [`events/README.md`](events/README.md).

Regla dura: un cambio a un schema de evento que no sea puramente aditivo exige subir de
versión (ver `events/README.md`). Nunca se reescribe un schema `.v1` ya publicado con
una forma distinta.
