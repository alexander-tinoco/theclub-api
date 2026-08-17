"""Money as integers in minor units (cents). Never floats.

`Money` is a safety box: wrapping an integer here stops a `10.5` (with
decimals) or an addition between different currencies from slipping through
without the program rejecting it at the exact moment it happens.
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
            raise TypeError(f"Money only accepts int, never float or bool: {self.minor!r}")

    @classmethod
    def zero(cls, currency: str = "EUR") -> Money:
        return cls(0, currency)

    def _check_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(f"can't operate {self.currency} with {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check_same_currency(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check_same_currency(other)
        return Money(self.minor - other.minor, self.currency)

    def __mul__(self, factor: int) -> Money:
        if isinstance(factor, bool) or not isinstance(factor, int):
            raise TypeError(f"Money can only be multiplied by int: {factor!r}")
        return Money(self.minor * factor, self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: Money) -> bool:
        self._check_same_currency(other)
        return self.minor < other.minor
