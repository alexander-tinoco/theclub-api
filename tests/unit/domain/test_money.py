import pytest

from app.domain.money import Money

pytestmark = pytest.mark.unit


def test_zero() -> None:
    assert Money.zero() == Money(0, "EUR")


def test_rechaza_float() -> None:
    with pytest.raises(TypeError, match="nunca float"):
        Money(10.5)  # type: ignore[arg-type]


def test_rechaza_bool() -> None:
    # bool es subtipo de int para mypy (por eso no hace falta type: ignore aquí),
    # pero en runtime __post_init__ lo rechaza explícitamente.
    with pytest.raises(TypeError, match="nunca float ni bool"):
        Money(True)


def test_suma_misma_divisa() -> None:
    assert Money(100) + Money(50) == Money(150)


def test_suma_divisas_distintas_falla() -> None:
    with pytest.raises(ValueError, match="EUR con USD"):
        Money(100, "EUR") + Money(50, "USD")


def test_resta() -> None:
    assert Money(150) - Money(50) == Money(100)


def test_multiplicacion_por_entero() -> None:
    assert Money(500) * 36 == Money(18000)
    assert 36 * Money(500) == Money(18000)


def test_multiplicacion_por_float_falla() -> None:
    with pytest.raises(TypeError, match="solo se multiplica por int"):
        Money(500) * 1.5  # type: ignore[operator]


def test_orden() -> None:
    assert Money(50) < Money(100)
    assert Money(100) <= Money(100)
    assert Money(150) > Money(100)
    assert not (Money(50) > Money(100))


def test_orden_entre_divisas_distintas_falla() -> None:
    with pytest.raises(ValueError, match="EUR con USD"):
        _ = Money(50, "EUR") < Money(50, "USD")


def test_es_inmutable() -> None:
    money = Money(100)
    with pytest.raises(AttributeError):
        money.minor = 200  # type: ignore[misc]
