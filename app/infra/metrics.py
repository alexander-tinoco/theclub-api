"""Métricas Prometheus. `/metrics` es explícitamente "opcional" en el plan
y hoy no hay Prometheus/Grafana en el docker-compose que las consuma — se
implementa igual porque exponerlas correctamente es barato con
`prometheus_client`, y es lo que un despliegue real necesitaría el día que
sí haya un stack de monitoreo apuntando aquí. Etiquetas mantenidas a
propósito de baja cardinalidad: `path` es el path *declarado* de la ruta
(`/api/v1/roulette/rounds`), no la URL con IDs interpolados.
"""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

http_requests_total = Counter(
    "http_requests_total",
    "Peticiones HTTP completadas",
    ["method", "path", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "Duración de peticiones HTTP",
    ["method", "path"],
)

ws_connections_active = Gauge(
    "ws_connections_active",
    "Conexiones WebSocket activas ahora mismo",
)

ws_connections_total = Counter(
    "ws_connections_total",
    "Conexiones WebSocket aceptadas desde que arrancó el proceso",
)

bets_placed_total = Counter(
    "bets_placed_total",
    "Rondas de ruleta resueltas",
    ["won"],
)


def render_latest() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
