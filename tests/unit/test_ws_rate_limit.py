import pytest

from app.ws import rate_limit as rate_limit_module
from app.ws.rate_limit import WsConnectRateLimiter

pytestmark = pytest.mark.unit


def test_permite_hasta_el_maximo_de_intentos() -> None:
    limiter = WsConnectRateLimiter(max_attempts=3, window_seconds=60)

    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True


def test_rechaza_al_superar_el_maximo_dentro_de_la_ventana() -> None:
    limiter = WsConnectRateLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        limiter.allow("1.2.3.4")

    assert limiter.allow("1.2.3.4") is False


def test_cada_ip_tiene_su_propio_contador() -> None:
    limiter = WsConnectRateLimiter(max_attempts=1, window_seconds=60)

    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("5.6.7.8") is True
    assert limiter.allow("1.2.3.4") is False


def test_los_intentos_fuera_de_la_ventana_no_cuentan(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = WsConnectRateLimiter(max_attempts=1, window_seconds=60)
    times = iter([100.0, 200.0])
    monkeypatch.setattr("app.ws.rate_limit.time.monotonic", lambda: next(times))

    assert limiter.allow("1.2.3.4") is True  # t=100
    assert limiter.allow("1.2.3.4") is True  # t=200, la ventana de 60s ya pasó


def test_una_ip_que_no_puede_registrar_ningun_intento_no_deja_entrada_vacia() -> None:
    # max_attempts=0 fuerza `allowed=False` desde el primer intento, con la
    # lista de timestamps ya vacía tras el filtro — el caso que ejercita el
    # `pop` en vez de guardar `[]` colgado para siempre.
    limiter = WsConnectRateLimiter(max_attempts=0, window_seconds=60)

    assert limiter.allow("1.2.3.4") is False
    assert "1.2.3.4" not in limiter._attempts


def test_supera_el_tope_de_ips_rastreadas_descarta_la_mas_vieja(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rate_limit_module, "MAX_TRACKED_IPS", 2)
    limiter = WsConnectRateLimiter(max_attempts=5, window_seconds=60)

    limiter.allow("1.1.1.1")
    limiter.allow("2.2.2.2")
    limiter.allow("3.3.3.3")

    assert "1.1.1.1" not in limiter._attempts
    assert "2.2.2.2" in limiter._attempts
    assert "3.3.3.3" in limiter._attempts
