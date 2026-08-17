"""Prometheus metrics. `/metrics` is explicitly "optional" in the plan, and
originally there was no Prometheus/Grafana in the docker-compose to consume
them — implemented anyway because exposing them correctly is cheap with
`prometheus_client`, and it's what a real deployment would need the day a
monitoring stack actually points here. Labels deliberately kept low
cardinality: `path` is the route's *declared* path
(`/api/v1/roulette/rounds`), not the URL with interpolated IDs.
"""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

http_requests_total = Counter(
    "http_requests_total",
    "Completed HTTP requests",
    ["method", "path", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"],
)

ws_connections_active = Gauge(
    "ws_connections_active",
    "WebSocket connections active right now",
)

#: Set once at startup, from `settings.WS_MAX_CONNECTIONS` (see
#: `create_app`) -- so Prometheus's `WsConnectionsNearLimit` alert can
#: compare `ws_connections_active` against the real limit without also
#: hardcoding that number in `theclub-api.rules.yml`, where it would
#: silently drift the day someone changes one without the other.
ws_connections_limit = Gauge(
    "ws_connections_limit",
    "Configured cap on simultaneous WebSocket connections (WS_MAX_CONNECTIONS)",
)

ws_connections_total = Counter(
    "ws_connections_total",
    "WebSocket connections accepted since the process started",
)

bets_placed_total = Counter(
    "bets_placed_total",
    "Roulette rounds resolved",
    ["won"],
)


def render_latest() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
