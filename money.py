"""Money as whole minor units, and the standard codes of currencies.

An amount is an integer count of the minor units of its currency (cents for
SGD). Where a figure has to be rounded to its minor unit the arithmetic is
decimal, rounded half-even; no float rounding is used.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal

# How many decimal places the minor unit of each currency an account may be
# kept in has: cents for SGD, paise for INR. A currency not listed here is not
# converted by guessing.
MINOR_UNIT_DIGITS = {"SGD": 2, "INR": 2}

# What the minor unit of each is called, where a refusal has to name it.
MINOR_UNIT_NAMES = {"SGD": "cents", "INR": "paise"}

# A float this far from a whole minor unit carries a real fraction of one, and
# is not just the noise float arithmetic leaves behind.
_NOISE = Decimal("0.000001")


class UnknownCurrency(ValueError):
    """The currency has no declared minor unit."""


def _in_minor_units(amount: float, currency: str) -> Decimal:
    if currency not in MINOR_UNIT_DIGITS:
        raise UnknownCurrency(currency)
    # repr gives the shortest decimal text that reads back as this float, so
    # 12.3 is taken as 12.30 and not as the binary fraction nearest to it.
    return Decimal(repr(float(amount))).scaleb(MINOR_UNIT_DIGITS[currency])


def to_minor(amount: float, currency: str = "SGD") -> int:
    """A float amount as whole minor units, rounded half-even."""
    return int(_in_minor_units(amount, currency).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def is_whole_minor(amount: float, currency: str = "SGD") -> bool:
    """Whether a float amount is a whole number of minor units, float noise
    aside. False means to_minor rounds a real fraction away."""
    exact = _in_minor_units(amount, currency)
    return abs(exact - exact.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)) <= _NOISE


def exact_minor(amount: float, currency: str = "SGD") -> int:
    """A float amount a spreadsheet cell holds, in whole minor units. A figure
    finer than the minor unit is refused, not rounded."""
    if not is_whole_minor(amount, currency):
        raise ValueError("not a whole number of minor units")
    return to_minor(amount, currency)


def parse_minor(text: str, currency: str = "SGD") -> int:
    """An amount as a statement prints it ("1,234.56") in whole minor units.
    The text is read as a decimal and never as a float. A figure finer than
    the minor unit is refused, not rounded."""
    if currency not in MINOR_UNIT_DIGITS:
        raise UnknownCurrency(currency)
    try:
        exact = Decimal(text.strip().replace(",", "")).scaleb(MINOR_UNIT_DIGITS[currency])
    except ArithmeticError:
        raise ValueError("not an amount") from None
    if not exact.is_finite() or exact != exact.to_integral_value():
        raise ValueError("not a whole number of minor units")
    return int(exact)


def from_minor(minor: int, currency: str = "SGD") -> float:
    """Whole minor units as the decimal number a payload carries (1230 is
    12.3). The result is for showing: it is never stored, added or compared."""
    if currency not in MINOR_UNIT_DIGITS:
        raise UnknownCurrency(currency)
    return float(Decimal(minor).scaleb(-MINOR_UNIT_DIGITS[currency]))


def mean_minor(total: int, count: int) -> int:
    """The average of `count` amounts adding up to `total`, in whole minor
    units, rounded half-even."""
    return int((Decimal(total) / Decimal(count)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def convert_minor(amount_minor: int, rate: Decimal, source: str, target: str) -> int:
    """An amount in one currency as whole minor units of another, at a rate
    that says how many units of the target one unit of the source is worth.
    The arithmetic is decimal and the result is rounded half-even to the
    target's minor unit. `rate` is a Decimal, never a float."""
    if source not in MINOR_UNIT_DIGITS:
        raise UnknownCurrency(source)
    if target not in MINOR_UNIT_DIGITS:
        raise UnknownCurrency(target)
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise ValueError("an amount is a whole number of minor units")
    if not isinstance(rate, Decimal):
        raise ValueError("a rate is a decimal, never a float")
    units = Decimal(amount_minor).scaleb(-MINOR_UNIT_DIGITS[source])
    exact = (units * rate).scaleb(MINOR_UNIT_DIGITS[target])
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


# --- currency codes ---------------------------------------------------------

# The names statements print for a currency, to its standard three-letter
# code. Compared with case, spaces and punctuation ignored, so "U. S. DOLLAR"
# and "U.S. Dollar" are one name.
_CURRENCY_NAMES = {
    "US DOLLAR": "USD",
    "US DOLLARS": "USD",
    "UNITED STATES DOLLAR": "USD",
    "AUSTRALIAN DOLLAR": "AUD",
    "EURO": "EUR",
    "POUND STERLING": "GBP",
    "STERLING POUND": "GBP",
    "BRITISH POUND": "GBP",
    "INDIAN RUPEE": "INR",
    "RUPIAH": "IDR",
    "INDONESIAN RUPIAH": "IDR",
    "BAHT": "THB",
    "THAI BAHT": "THB",
    "RINGGIT": "MYR",
    "MALAYSIAN RINGGIT": "MYR",
    "YEN": "JPY",
    "JAPANESE YEN": "JPY",
    "HONG KONG DOLLAR": "HKD",
    "NEW ZEALAND DOLLAR": "NZD",
    "NEW TAIWAN DOLLAR": "TWD",
    "TAIWAN DOLLAR": "TWD",
    "KOREAN WON": "KRW",
    "SOUTH KOREAN WON": "KRW",
    "WON": "KRW",
    "CHINESE YUAN": "CNY",
    "YUAN RENMINBI": "CNY",
    "RENMINBI": "CNY",
    "PHILIPPINE PESO": "PHP",
    "VIETNAMESE DONG": "VND",
    "DONG": "VND",
    "CANADIAN DOLLAR": "CAD",
    "SWISS FRANC": "CHF",
    "SWEDISH KRONA": "SEK",
    "NORWEGIAN KRONE": "NOK",
    "DANISH KRONE": "DKK",
    "UAE DIRHAM": "AED",
    "SINGAPORE DOLLAR": "SGD",
}

_NOT_A_LETTER = re.compile(r"[^A-Z]")


def _name_key(name: str) -> str:
    return _NOT_A_LETTER.sub("", name.upper())


_CODE_BY_NAME = {_name_key(name): code for name, code in _CURRENCY_NAMES.items()}
CURRENCY_CODES = frozenset(_CODE_BY_NAME.values())


def currency_code(name: str | None) -> str | None:
    """The standard three-letter code for a currency as a statement names it,
    or None when the name is not one this table knows. A code is its own name."""
    if not name:
        return None
    key = _name_key(name)
    if key in CURRENCY_CODES:
        return key
    return _CODE_BY_NAME.get(key)
