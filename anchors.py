"""Anchors: an account's balance on a date.

An anchor is (account, date, amount, source kind, note). The amount is a whole
number of minor units (cents, paise) of the account's currency, signed as the
household sees it: cash and things owned positive, anything owed negative.

There is one anchor per account and date. The same amount again is accepted
and changes nothing; a different amount is refused. Every writer of a new
anchor goes through `record`. A supplied anchor (one the operator typed) can
be corrected through `replace` or removed through `remove`; a statement's
anchor is the statement's fact and is never changed. None of them commits:
the caller owns the transaction.
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
CURRENCY_SIGNS = {"SGD": "S$", "INR": "Rs", "USD": "US$"}
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


class NotSupplied(ValueError):
    """The anchor is a statement's closing balance, not a figure the operator
    typed: it is never corrected or removed by hand."""


def held_by_id(conn: sqlite3.Connection, anchor_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, account_id, date, amount, source, note FROM anchors WHERE id = ?",
        (anchor_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(zip(("id", "account_id", "date", "amount", "source", "note"), row))


def _supplied(conn: sqlite3.Connection, anchor_id: int) -> dict | None:
    held = held_by_id(conn, anchor_id)
    if held is not None and held["source"] != SUPPLIED:
        raise NotSupplied("a statement's balance is never changed by hand; only a figure you entered is")
    return held


def replace(
    conn: sqlite3.Connection,
    anchor_id: int,
    on: str,
    amount_minor: int,
    note: str | None,
) -> dict | None:
    """Correct a supplied anchor: its date, amount and note. Returns the
    anchor as now held, or None when there is no such anchor.

    Raises NotSupplied for a statement's anchor, and AnchorConflict when the
    account already has another anchor on the new date. Does not commit.
    """
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise InvalidAnchor("amount must be a whole number of minor units")
    on = checked_date(on)
    held = _supplied(conn, anchor_id)
    if held is None:
        return None
    other = _held(conn, held["account_id"], on)
    if other is not None and other["id"] != anchor_id:
        raise AnchorConflict(other)
    conn.execute(
        "UPDATE anchors SET date = ?, amount = ?, note = ? WHERE id = ?",
        (on, amount_minor, note, anchor_id),
    )
    return held_by_id(conn, anchor_id)


def remove(conn: sqlite3.Connection, anchor_id: int) -> dict | None:
    """Remove a supplied anchor. Returns the anchor as it was, or None when
    there is no such anchor. Raises NotSupplied for a statement's anchor.
    Does not commit."""
    held = _supplied(conn, anchor_id)
    if held is not None:
        conn.execute("DELETE FROM anchors WHERE id = ?", (anchor_id,))
    return held


def currency_refusal(conn: sqlite3.Connection, account_id: int, currency: str,
                     terms_sent: bool = False) -> str | None:
    """Why an account may not change to another currency, or None: a figure
    the operator supplied is held in its currency now, and would be read in
    the new one (S$ 1,000.00 as Rs 1,000.00). A statement balance is the
    statement's, and is no bar: setting an account that took a statement in
    the wrong currency right is how that is mended."""
    held = conn.execute("SELECT currency FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if held is None or (held[0] or "SGD") == currency:
        return None
    import loan_terms  # it reads figures from this module

    if not terms_sent and loan_terms.held(conn, account_id) is not None:
        return (f"This loan's hire-purchase terms are typed in {held[0] or 'SGD'}; they would be read in "
                f"{currency}. Enter its terms again in the new currency in the same save. Nothing was saved.")
    if conn.execute("SELECT 1 FROM anchors WHERE account_id = ? AND source = ? LIMIT 1",
                    (account_id, SUPPLIED)).fetchone() is None:
        return None
    return (f"This account holds figures typed in {held[0] or 'SGD'}; they would be read in {currency}. "
            "Delete its figures first, then enter them again in the new currency. Nothing was saved.")


def currency_refusal_to_undo(conn: sqlite3.Connection, entry_id: int) -> str | None:
    """Why undoing a change entry would read a figure in another currency
    than it was typed in, or None: putting back an account's currency while
    it holds figures typed since (currency_refusal), or putting back a figure
    on an account whose currency has changed since."""
    import json

    for tbl, row_id, before, after in conn.execute(
        "SELECT tbl, row_id, before, after FROM change_rows WHERE entry_id = ? AND tbl IN ('accounts', 'anchors')",
        (entry_id,),
    ):
        old = json.loads(before) if before else None
        new = json.loads(after) if after else None
        if tbl == "accounts":
            if old and new and (old.get("currency") or "SGD") != (new.get("currency") or "SGD"):
                why = currency_refusal(conn, row_id, old.get("currency") or "SGD")
                if why:
                    return why.replace("Nothing was saved.", "Nothing was undone.")
            continue
        if old is None or old.get("source") != SUPPLIED:
            continue  # the undo takes a figure out, or it is a statement's
        # The account's currency when the figure was typed: as the first
        # later change to the account found it, else as it is now.
        first = conn.execute(
            "SELECT before FROM change_rows WHERE tbl = 'accounts' AND row_id = ? AND entry_id > ? "
            "AND op = 'update' ORDER BY entry_id, id LIMIT 1", (old["account_id"], entry_id),
        ).fetchone()
        now = conn.execute("SELECT currency FROM accounts WHERE id = ?", (old["account_id"],)).fetchone()
        if first is None or not first[0] or now is None:
            continue
        was = json.loads(first[0]).get("currency") or "SGD"
        if (now[0] or "SGD") != was:
            return (f"This figure was typed in {was}, and its account is kept in {now[0]} since; "
                    "putting it back would read it in the new currency. Enter it again instead. "
                    "Nothing was undone.")
    return None

