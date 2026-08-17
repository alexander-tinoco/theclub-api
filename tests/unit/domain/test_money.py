import pytest

from app.domain.money import Money

pytestmark = pytest.mark.unit


def test_zero() -> None:
    assert Money.zero() == Money(0, "EUR")


def test_rejects_float() -> None:
    with pytest.raises(TypeError, match="never float"):
        Money(10.5)  # type: ignore[arg-type]


def test_rejects_bool() -> None:
    # bool is a subtype of int for mypy (which is why no type: ignore is
    # needed here), but at runtime __post_init__ explicitly rejects it.
    with pytest.raises(TypeError, match="never float or bool"):
        Money(True)


def test_add_same_currency() -> None:
    assert Money(100) + Money(50) == Money(150)


def test_add_different_currencies_fails() -> None:
    with pytest.raises(ValueError, match="EUR with USD"):
        Money(100, "EUR") + Money(50, "USD")


def test_subtract() -> None:
    assert Money(150) - Money(50) == Money(100)


def test_multiply_by_integer() -> None:
    assert Money(500) * 36 == Money(18000)
    assert 36 * Money(500) == Money(18000)


def test_multiply_by_float_fails() -> None:
    with pytest.raises(TypeError, match="can only be multiplied by int"):
        Money(500) * 1.5  # type: ignore[operator]


def test_ordering() -> None:
    assert Money(50) < Money(100)
    assert Money(100) <= Money(100)
    assert Money(150) > Money(100)
    assert not (Money(50) > Money(100))


def test_ordering_between_different_currencies_fails() -> None:
    with pytest.raises(ValueError, match="EUR with USD"):
        _ = Money(50, "EUR") < Money(50, "USD")


def test_is_immutable() -> None:
    money = Money(100)
    with pytest.raises(AttributeError):
        money.minor = 200  # type: ignore[misc]
