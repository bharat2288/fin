"""Anchors: an account's balance on a date.

An anchor is (account, date, amount, source kind, note). The amount is a whole
number of minor units (cents, paise) of the account's currency, signed as the
household sees it: cash and things owned positive, anything owed negative.

There is one anchor per account and date. The same amount again is accepted
and changes nothing; a different amount is refused. Every writer of an anchor
goes through `record`, which does not commit: the caller owns the transaction.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation

import money

# (name, description). Declared once; `record` validates against it.
SOURCES = (
    ("statement", "the closing balance a statement states"),
    ("supplied", "a figure the operator entered"),
)

SOURCE_NAMES = tuple(name for name, _ in SOURCES)
STATEMENT = "statement"
SUPPLIED = "supplied"

# The currency sign a figure is shown with; any other code is shown as itself.
CURRENCY_SIGNS = {"SGD": "S$", "INR": "Rs"}
DEFAULT_CURRENCY = "SGD"


class InvalidAnchor(ValueError):
    """The anchor's date, amount or source is not one fin can store."""


class AnchorConflict(Exception):
    """The account already has an anchor on that date with a different amount.

    `existing` is the anchor already held; nothing was changed.
    """

    def __init__(self, existing: dict):
        super().__init__("a different amount is already held for this account and date")
        self.existing = existing


def to_minor_units(amount, currency: str | None = None) -> int:
    """An amount typed in whole units ("902,500.00") as whole minor units of
    the account's currency.

    Exact or refused: text or a whole number only, never a float, and nothing
    finer than one minor unit. The reading is money.parse_minor's, the one a
    figure printed on a statement gets, so a typed figure and a printed one
    are the same number.
    """
    if isinstance(amount, bool) or not isinstance(amount, (str, int)):
        raise InvalidAnchor("amount must be a figure written as text, such as 902500.00")
    text = str(amount).replace(",", "").strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise InvalidAnchor("amount must be a figure written as text, such as 902500.00") from None
    if not value.is_finite() or "E" in text.upper():
        raise InvalidAnchor("amount must be a plain figure, such as 902500.00")
    try:
        return money.parse_minor(text, currency or DEFAULT_CURRENCY)
    except money.UnknownCurrency:
        raise InvalidAnchor(f"the account's currency, {currency}, has no declared minor unit") from None
    except ValueError:
        raise InvalidAnchor("amount has more decimal places than the currency has") from None


def format_amount(amount_minor: int, currency: str | None) -> str:
    """A stored amount as the operator reads it: "S$ -902,500.00"."""
    code = currency or DEFAULT_CURRENCY
    digits = money.MINOR_UNIT_DIGITS.get(code, 2)
    units, minor = divmod(abs(amount_minor), 10 ** digits)
    sign = "-" if amount_minor < 0 else ""
    return f"{CURRENCY_SIGNS.get(code, code)} {sign}{units:,}.{minor:0{digits}d}"


def checked_date(value) -> str:
    """A day as an anchor stores it: YYYY-MM-DD, and a real day."""
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise InvalidAnchor("date must be a real day written as YYYY-MM-DD") from None
    return value


def _held(conn: sqlite3.Connection, account_id: int, on: str) -> dict | None:
    row = conn.execute(
        "SELECT id, account_id, date, amount, source, note FROM anchors"
        " WHERE account_id = ? AND date = ?",
        (account_id, on),
    ).fetchone()
    if row is None:
        return None
    return dict(zip(("id", "account_id", "date", "amount", "source", "note"), row))


def record(
    conn: sqlite3.Connection,
    account_id: int,
    on: str,
    amount_minor: int,
    source: str,
    note: str | None = None,
) -> tuple[dict, bool]:
    """Write an anchor. Returns (the anchor held, whether it was written now).

    The same amount for an account and date already anchored returns the
    anchor held, unchanged, and False. A different amount raises
    AnchorConflict. Does not commit.
    """
    if source not in SOURCE_NAMES:
        raise InvalidAnchor(f"unknown anchor source: {source!r}")
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise InvalidAnchor("amount must be a whole number of minor units")
    on = checked_date(on)

    held = _held(conn, account_id, on)
    if held is not None:
        if held["amount"] != amount_minor:
            raise AnchorConflict(held)
        return held, False

    conn.execute(
        "INSERT INTO anchors (account_id, date, amount, source, note) VALUES (?, ?, ?, ?, ?)",
        (account_id, on, amount_minor, source, note),
    )
    return _held(conn, account_id, on), True
