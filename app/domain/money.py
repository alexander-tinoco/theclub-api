"""Dinero como enteros en unidades menores (céntimos). Nunca floats.

`Money` es una caja de seguridad: envolver un entero aquí impide que un `10.5`
(con decimales) o una suma entre divisas distintas se cuele sin que el
programa lo rechace en el momento exacto en que ocurre.
"""

from dataclasses import dataclass
from functools import total_ordering


@total_ordering
@dataclass(frozen=True, slots=True)
class Money:
    minor: int
    currency: str = "EUR"

    def __post_init__(self) -> None:
        if isinstance(self.minor, bool) or not isinstance(self.minor, int):
            raise TypeError(f"Money solo acepta int, nunca float ni bool: {self.minor!r}")

    @classmethod
    def zero(cls, currency: str = "EUR") -> Money:
        return cls(0, currency)

    def _check_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(f"no se puede operar {self.currency} con {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check_same_currency(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check_same_currency(other)
        return Money(self.minor - other.minor, self.currency)

    def __mul__(self, factor: int) -> Money:
        if isinstance(factor, bool) or not isinstance(factor, int):
            raise TypeError(f"Money solo se multiplica por int: {factor!r}")
        return Money(self.minor * factor, self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: Money) -> bool:
        self._check_same_currency(other)
        return self.minor < other.minor
