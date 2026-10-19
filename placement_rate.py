"""The rate a set-off across currencies is placed at (personal-ledger 23,
Shared rules: cross-currency).

A payment placed against an expectation kept in another currency converts at
the rate of the **payment's month**:

- a month that has ended uses its own month-end rate: the latest rate saved on
  or before its last day, a rate dated before its first day counting as no
  rate (fin-surfaces 15's rule). With none it is refused, naming the month,
  and the caller offers Fetch through the existing rates fetch;
- the month still running has no month-end rate yet, so it uses the latest
  rate saved on or before the day of placing, and says so. The placement is
  never restated when the month's own rate arrives; the month check's spread
  term carries the difference.

Rates are kept as FOREIGN/SGD, so a S$ payment into a rupee expectation goes
through money's one inverse and a rupee payment into a S$ one through
money.convert_minor; nothing here computes 1/rate.

Pure reads: only saved rates are read. Nothing is fetched and nothing is
written; the route that stores a set-off keeps both amounts, the rate text and
its date from what `place` returns.
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import date

import money
import rates

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


class NoRate(ValueError):
    """No saved rate the rule allows. Nothing was written. `pair`, `month`
    (YYYY-MM, the payment's) and `fetch_on` (the day a Fetch would save a
    rate for: an ended month's last day, or the day of placing) say what is
    missing."""

    def __init__(self, message: str, pair: str, month: str, fetch_on: str):
        super().__init__(message)
        self.pair = pair
        self.month = month
        self.fetch_on = fetch_on


def _month_end(day: str) -> str:
    year, month = int(day[:4]), int(day[5:7])
    return f"{day[:7]}-{calendar.monthrange(year, month)[1]:02d}"


def _month_name(day: str) -> str:
    return _MONTH_NAMES[int(day[5:7]) - 1]


def _day_shown(day: str) -> str:
    """'2026-10-19' as '19 Oct 2026', in words that never hang on the locale."""
    d = date.fromisoformat(day)
    return f"{d.day} {_MONTH_NAMES[d.month - 1][:3]} {d.year}"


def rate_for(conn: sqlite3.Connection, currency: str, paid_on: str, placed_on: str) -> dict:
    """The rate a payment made on `paid_on` is placed at on `placed_on`
    against an amount kept in `currency` (or paid in it), the other side being
    S$. Days are YYYY-MM-DD.

    Returns pair, rate (the saved decimal text), value (its Decimal), date
    (the day the rate is of), month (the payment's, YYYY-MM), running (whether
    that month had not ended on the day of placing) and note (None for an
    ended month; for the running month, "October has no rate yet" when the
    rate used is from before it, otherwise "October has not ended").

    Raises NoRate when the rule allows no saved rate, and rates.InvalidRate
    for a currency fin keeps no rate for or a day not written as YYYY-MM-DD.
    """
    pair = rates.pair_for(currency)
    paid_on, placed_on = rates.checked_date(paid_on), rates.checked_date(placed_on)
    month, first, last = paid_on[:7], f"{paid_on[:7]}-01", _month_end(paid_on)
    running = placed_on <= last

    if running:
        found = rates.on_or_before(conn, pair, placed_on)
        if found is None:
            raise NoRate(
                f"no saved {pair} rate on or before {_day_shown(placed_on)}. Fetch it.",
                pair, month, placed_on,
            )
        name = _month_name(paid_on)
        note = f"{name} has no rate yet" if found["date"] < first else f"{name} has not ended"
    else:
        found = rates.on_or_before(conn, pair, last)
        if found is None or found["date"] < first:
            raise NoRate(
                f"no saved {pair} rate for {_month_name(paid_on)} {paid_on[:4]}. Fetch it.",
                pair, month, last,
            )
        note = None

    return {
        "pair": pair,
        "rate": found["rate"],
        "value": found["value"],
        "date": found["date"],
        "month": month,
        "running": running,
        "note": note,
    }


def place(conn: sqlite3.Connection, amount_minor: int, source: str, target: str,
          paid_on: str, placed_on: str) -> dict:
    """A payment's amount (whole minor units of `source`, the payment's
    currency) as whole minor units of `target`, the expectation's, at the
    rate `rate_for` picks. Rounded half-even to the target's minor unit.

    Returns amount_minor (in `target`) and rate (rate_for's answer, or None
    when both currencies are one and nothing converts). Raises what rate_for
    raises, and rates.InvalidRate when neither currency is S$.
    """
    if source == target:
        if source not in money.MINOR_UNIT_DIGITS:
            raise money.UnknownCurrency(source)
        if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
            raise ValueError("an amount is a whole number of minor units")
        return {"amount_minor": amount_minor, "rate": None}
    if source == rates.QUOTE:
        rate = rate_for(conn, target, paid_on, placed_on)
        converted = money.convert_minor_inverse(amount_minor, rate["value"], source, target)
    elif target == rates.QUOTE:
        rate = rate_for(conn, source, paid_on, placed_on)
        converted = money.convert_minor(amount_minor, rate["value"], source, target)
    else:
        raise rates.InvalidRate(f"no rate is kept between {source!r} and {target!r}")
    return {"amount_minor": converted, "rate": rate}
