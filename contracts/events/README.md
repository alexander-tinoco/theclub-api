# Eventos — convenciones

## Versionado

`.v1` aparece tres veces por evento y las tres deben coincidir: en el nombre del archivo
de schema (`bet-placed.v1.schema.json`), en el nombre del topic de Kafka
(`theclub.bets.placed.v1`) y en el campo `event_version` del envelope. Un cambio que
rompa a un consumidor existente (renombrar un campo, quitarlo, cambiar su tipo) exige
subir a `.v2` en los tres sitios a la vez — el topic `.v1` se congela y deja de
escribirse, nunca se reescribe con una forma nueva. Un cambio aditivo (campo nuevo
opcional) no exige subir versión.

## Particionado

Los tres topics se particionan por `user_id` (viene dentro de `data`, no en el
envelope). Esto preserva el orden de los eventos de un mismo usuario dentro de una
partición — importante porque `round.settled` y `wallet.transaction` de una misma
jugada deben leerse en el orden en que ocurrieron.

## Deduplicación

`event_id` se genera una única vez, al insertar la fila en la tabla `outbox` (Fase 6), y
no cambia entre reintentos. Es la clave que debe usar cualquier consumidor para
deduplicar — el patrón outbox garantiza *at-least-once*, no *exactly-once*, así que un
evento puede llegar dos veces tras un crash del relay a medio publicar.

`idempotency_key` es un campo distinto: es el `Idempotency-Key` HTTP de la petición que
originó el evento (Fase 5). Sirve para *correlacionar* varios eventos que vinieron de la
misma petición del usuario (en los tres ejemplos de este directorio comparten el mismo
valor, porque nacen de la misma llamada a `POST /roulette/rounds`), no para deduplicar.
Puede ser `null` si el evento no nace de una petición HTTP directa.

## Sin PII

Los eventos no llevan email ni ningún dato personal más allá del `user_id` (UUID). Si en
algún momento hiciera falta un dato personal en el stream, eso es una razón para subir
de versión y discutirlo explícitamente, no para añadir un campo sin más.
