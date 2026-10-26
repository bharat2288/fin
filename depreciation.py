"""Depreciation: what a car is worth, worked out each month from its terms.

fin RUL-029. A car held as a holding may carry its terms: what it cost, the
day it was had from (`start`), its life in whole years, and what it is worth
at the end of that life (the residual, nil unless given). Straight line: the
cost less the residual is written off evenly over the life's months, one row
a month, dated the month's last day.

    the first month   by the days held in it: the days from `start` to the
                      month's end over the month's days, of a month's share
    the months between  a month's share, (cost - residual) / months, half up
    the last month    the month the life ends in (the day before `start`'s
                      anniversary): what is left, so the rows add up to the
                      cost less the residual exactly

Every amount is whole minor units, half up; a month whose share comes to
nothing has no row.

Worked out on read: nothing is stored but the terms, and no job runs. A
month's row is there once the month is read, as loan interest's are once a
figure is entered (loan_interest.py), though these are never written: they
sit on the car alone, outside every bank tie and every row a spending total
adds up. The balance sheet reads the car's value from them (`value_on`: the
cost less the rows dated to the day, none before `start`), and the month
check takes the month's rows on their own line (`between`), outside Household
spending, so the fall in the car's value is explained. A figure typed for
the car is kept and not read while it holds terms; it takes no new one.

The terms are stored in one row per holding (the depreciation_terms table,
history tracked, made by a start: history.ADDED_ON_START), set through the
account's own route. Changing them works every month out again, and Undo
puts the old ones back. A book not started since the table came reads as
holding no terms.

Cars only, in S$ only, for now (RUL-029): the asset vocabulary is one word.
"""

from __future__ import annotations

import sqlite3
from calendar import monthrange
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import money

HOLDING = "holding"
CURRENCY = "SGD"

# (name, description). What fin depreciates; a new asset is a new value here.
ASSETS = (
    ("car", "a car: written off evenly over its life in years, to its value at the end"),
)
ASSET_NAMES = tuple(name for name, _ in ASSETS)

_COLUMNS = ("asset", "cost_minor", "start", "years", "residual_minor")
MAX_YEARS = 50
# The source a line resting on terms shows (balance_sheet.py).
SOURCE = "terms"


class TermsRefused(ValueError):
    """Depreciation terms that cannot be held, in the operator's words. Nothing was written."""


def _half_up(value: Fraction) -> int:
    """A fraction of a minor unit to the nearest whole one, half up."""
    return int((Decimal(value.numerator) / Decimal(value.denominator)).quantize(
        Decimal(1), rounding=ROUND_HALF_UP))


def _amount(value, field: str, currency: str, *, nothing_ok: bool = False) -> int:
    try:
        minor = money.parse_minor(str(value), currency)
    except (ValueError, money.UnknownCurrency):
        raise TermsRefused(f"{field} is not an amount; nothing was saved") from None
    if minor < 0:
        raise TermsRefused(f"{field} is not an amount; nothing was saved")
    if minor == 0 and not nothing_ok:
        raise TermsRefused(f"{field} must be more than nothing; nothing was saved")
    return minor


def checked(data, currency: str) -> dict:
    """Terms as sent (`asset`, car; `cost`; `start`, YYYY-MM-DD; `years`, a
    whole number; optionally `residual`; amounts as text in whole units of
    the holding's currency) as they are stored. Refused (TermsRefused) when
    any is missing or out of range."""
    if not isinstance(data, dict):
        raise TermsRefused("depreciation terms are the cost, the day it was had from and its life "
                           "in years; nothing was saved")
    if currency != CURRENCY:
        raise TermsRefused(f"depreciation terms are held in S$ only for now; this holding is kept "
                           f"in {currency}; nothing was saved")
    asset = data.get("asset", ASSET_NAMES[0])
    if asset not in ASSET_NAMES:
        raise TermsRefused(f"fin depreciates cars only for now, not {asset!r}; nothing was saved")
    cost = _amount(data.get("cost"), "the cost", currency)
    residual_sent = data.get("residual")
    residual = 0 if residual_sent in (None, "") else _amount(
        residual_sent, "the value at the end of its life", currency, nothing_ok=True)
    if residual >= cost:
        raise TermsRefused("the value at the end of its life must be less than its cost; nothing was saved")
    years = data.get("years")
    if isinstance(years, str) and years.strip().isdigit():
        years = int(years)
    if not isinstance(years, int) or isinstance(years, bool) or not 1 <= years <= MAX_YEARS:
        raise TermsRefused(f"the years are a whole number from 1 to {MAX_YEARS}; nothing was saved")
    start = data.get("start")
    try:
        if not isinstance(start, str) or date.fromisoformat(start).isoformat() != start:
            raise ValueError
    except ValueError:
        raise TermsRefused("the day it was had from is a day, YYYY-MM-DD; nothing was saved") from None
    return {"asset": asset, "cost_minor": cost, "start": start, "years": years, "residual_minor": residual}


def _has_table(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'depreciation_terms'"
    ).fetchone() is not None


def held(conn: sqlite3.Connection, account_id: int) -> dict | None:
    """The terms a holding holds, or None."""
    if not _has_table(conn):
        return None
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM depreciation_terms WHERE account_id = ?", (account_id,)
    ).fetchone()
    return None if row is None else dict(zip(_COLUMNS, tuple(row)))


def all_held(conn: sqlite3.Connection) -> dict[int, dict]:
    """Every holding's terms, by account id."""
    if not _has_table(conn):
        return {}
    return {r[0]: dict(zip(_COLUMNS, tuple(r)[1:])) for r in conn.execute(
        f"SELECT account_id, {', '.join(_COLUMNS)} FROM depreciation_terms")}


def write(conn: sqlite3.Connection, account_id: int, terms: dict | None) -> bool:
    """Hold these terms for the holding, or none (None). Returns whether
    anything changed. Does not commit."""
    if held(conn, account_id) == terms:
        return False
    if terms is None:
        conn.execute("DELETE FROM depreciation_terms WHERE account_id = ?", (account_id,))
    elif held(conn, account_id) is None:
        conn.execute(
            f"INSERT INTO depreciation_terms (account_id, {', '.join(_COLUMNS)}) "
            f"VALUES (?, {', '.join('?' for _ in _COLUMNS)})",
            (account_id, *(terms[c] for c in _COLUMNS)),
        )
    else:
        conn.execute(
            f"UPDATE depreciation_terms SET {', '.join(f'{c} = ?' for c in _COLUMNS)} WHERE account_id = ?",
            (*(terms[c] for c in _COLUMNS), account_id),
        )
    return True


def ends(terms: dict) -> str:
    """The last day of the life: the day before `start`'s anniversary after
    the years (28 February's for a start on 29 February)."""
    start = date.fromisoformat(terms["start"])
    year = start.year + terms["years"]
    anniversary = start.replace(year=year, day=min(start.day, monthrange(year, start.month)[1]))
    return (anniversary - timedelta(days=1)).isoformat()


def _share(terms: dict) -> Fraction:
    """A month's share of what is written off, exactly."""
    return Fraction(terms["cost_minor"] - terms["residual_minor"], 12 * terms["years"])


def monthly_minor(terms: dict) -> int:
    """A whole month's row, half up."""
    return _half_up(_share(terms))


def schedule(terms: dict) -> list[dict]:
    """Every row the terms give, oldest first: {month, date, amount_minor},
    dated the month's last day. They add up to the cost less the residual."""
    start, last = date.fromisoformat(terms["start"]), date.fromisoformat(ends(terms))
    left = terms["cost_minor"] - terms["residual_minor"]
    rows = []
    y, m = start.year, start.month
    while (y, m) <= (last.year, last.month):
        days = monthrange(y, m)[1]
        if (y, m) == (last.year, last.month):
            amount = left
        elif (y, m) == (start.year, start.month):
            amount = min(left, _half_up(_share(terms) * (days - start.day + 1) / days))
        else:
            amount = min(left, monthly_minor(terms))
        left -= amount
        if amount:
            rows.append({"month": f"{y:04d}-{m:02d}", "date": date(y, m, days).isoformat(),
                         "amount_minor": amount})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return rows


def between(terms: dict, after: str, upto: str) -> int:
    """The depreciation dated after one day up to another."""
    return sum(r["amount_minor"] for r in schedule(terms) if after < r["date"] <= upto)


def to_date(terms: dict, on: str) -> int:
    """The depreciation dated up to a day."""
    return sum(r["amount_minor"] for r in schedule(terms) if r["date"] <= on)


def value_on(terms: dict, on: str) -> int | None:
    """What the asset is worth on a day: its cost less the depreciation dated
    to it; None before the day it was had from."""
    if on < terms["start"]:
        return None
    return terms["cost_minor"] - to_date(terms, on)


def shown(terms: dict | None) -> dict | None:
    """Terms as an account shows them: as stored, with a month's row and the
    life's last day."""
    if terms is None:
        return None
    return {**terms, "monthly_minor": monthly_minor(terms), "ends": ends(terms)}


def day_shown(iso: str) -> str:
    """A day as the operator reads it: "11 Jun 2025"."""
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b} {d.year}"
