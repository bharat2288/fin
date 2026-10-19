"""Refund links (spec 23, E7): a refund row tied to the purchase it refunds.

A linker in pairing.py's shape. The rule:
  - a refund that may be linked is a money-in row of flow `refund` with no
    link yet: a link made by hand, or by an earlier run, is never moved;
  - its purchase is a money-out `expense` row of the same merchant (the two
    rows' merchant ids when both carry one, else their cleaned text with the
    refund words taken out), in the same currency, dated on the refund's day
    or in the WINDOW_DAYS before it, whose amount less the refunds already
    linked to it is at least the refund, and with no `refund_link` dismissal
    for the pair (an Unlink writes one, so a re-run never relinks it);
  - exactly one fit links (`auto`); two or more link nothing and are asked in
    the Queue (asks()); none leaves a plain refund.
A link uses up what is left on its purchase, which can leave another refund
with one fit where it had two; a run goes on until a pass links nothing, so
a second run links nothing more.

A linked refund whose label was not set by hand takes its purchase's book and
type. The linker runs inside the import confirm's change (link_on_import) and
on demand (refund_link_api.py); hand_link serves the Queue's ask. Nothing
here commits: the caller's route owns the transaction and the change. Amounts
are whole minor units of the row's account currency, signed as rows are.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

import book_type
import expect_back
import flow
import suggest

# How many days before a refund its purchase may be dated.
WINDOW_DAYS = 90
AUTO = "auto"
HAND = "hand"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_REFUND_WORDS = re.compile(
    r"(?<![A-Z0-9])(?:" + "|".join(re.escape(k) for k in flow.REFUND_KEYWORDS) + r")(?![A-Z0-9])"
)

# One row as the linker reads it. A purchase also carries what is left of it
# once the refunds linked to it are taken off (left_minor).
_LEFT = ("t.amount_minor + COALESCE((SELECT SUM(r.amount_minor) FROM refund_link l "
         "JOIN transactions r ON r.id = l.refund_row_id WHERE l.purchase_row_id = t.id), 0)")
_ROWS = (
    "SELECT t.id, t.date, t.description, t.amount_minor, t.service_id, t.book, t.type_id, "
    f"t.flow_type, COALESCE(a.currency, 'SGD') AS currency, a.name AS account_name, {_LEFT} AS left_minor "
    "FROM transactions t JOIN statements s ON s.id = t.statement_id "
    "JOIN accounts a ON a.id = s.account_id "
)


class Refused(ValueError):
    """The link was refused; nothing was written. The text is the operator's."""


def merchant_key(description: str | None) -> str:
    """A row's merchant as its text says it: cleaned as the type suggestion
    cleans it, with the refund words taken out."""
    text = _REFUND_WORDS.sub(" ", suggest.clean(description))
    return re.sub(r"\s+", " ", text).strip(" *-/.")


def day_words(iso: str) -> str:
    """'20 Jul' for 2026-07-20."""
    on = date.fromisoformat(iso[:10])
    return f"{on.day} {_MONTHS[on.month - 1]}"


def _dated(rows) -> list[dict]:
    found = []
    for r in rows:
        try:
            on = date.fromisoformat(r["date"])
        except (TypeError, ValueError):
            continue  # a row with no readable date is nobody's refund or purchase
        found.append({**dict(r), "day": on, "key": merchant_key(r["description"])})
    return found


def _unlinked_refunds(conn: sqlite3.Connection) -> list[dict]:
    """The refunds still looking for their purchase, oldest first."""
    return _dated(conn.execute(
        _ROWS + "WHERE t.flow_type = 'refund' AND t.amount_minor < 0 "
        "AND NOT EXISTS (SELECT 1 FROM refund_link l WHERE l.refund_row_id = t.id) "
        "ORDER BY t.date, t.id"
    ).fetchall())


def _purchases(conn: sqlite3.Connection) -> list[dict]:
    """Every purchase with something left to refund, oldest first."""
    return _dated(conn.execute(
        _ROWS + f"WHERE t.flow_type = 'expense' AND t.amount_minor > 0 AND {_LEFT} > 0 ORDER BY t.date, t.id"
    ).fetchall())


def _dismissed(conn: sqlite3.Connection) -> set[tuple[int, int]]:
    return {(r[0], r[1]) for r in conn.execute(
        "SELECT row_id, purchase_row_id FROM dismissal WHERE kind = 'refund_link'")}


def _same_merchant(refund: dict, purchase: dict) -> bool:
    if refund["service_id"] is not None and purchase["service_id"] is not None:
        return refund["service_id"] == purchase["service_id"]
    return bool(refund["key"]) and refund["key"] == purchase["key"]


def _fits(refund: dict, purchases: list[dict], dismissed: set) -> list[dict]:
    """The purchases a refund could belong to, oldest first."""
    return [
        p for p in purchases
        if p["currency"] == refund["currency"]
        and 0 <= (refund["day"] - p["day"]).days <= WINDOW_DAYS
        and p["left_minor"] >= -refund["amount_minor"]
        and (refund["id"], p["id"]) not in dismissed
        and _same_merchant(refund, p)
    ]


def _begin(conn: sqlite3.Connection) -> None:
    """Hold the write lock from the first read, so what is left on a purchase
    is read in the transaction that writes the link."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def link(conn: sqlite3.Connection, link_id: int) -> dict:
    return dict(conn.execute(
        "SELECT id, refund_row_id, purchase_row_id, source, made_at FROM refund_link WHERE id = ?",
        (link_id,)).fetchone())


def _write(conn: sqlite3.Connection, refund: dict, purchase: dict, source: str) -> dict:
    """The link, and the purchase's book and type on a refund not labelled by
    hand (cat_source 'manual')."""
    link_id = conn.execute(
        "INSERT INTO refund_link (refund_row_id, purchase_row_id, source) VALUES (?, ?, ?)",
        (refund["id"], purchase["id"], source),
    ).lastrowid
    book = purchase["book"] or book_type.DEFAULT_BOOK
    conn.execute(
        "UPDATE transactions SET book = ?, type_id = ? WHERE id = ? "
        "AND COALESCE(cat_source, 'auto') != 'manual' AND (book IS NOT ? OR type_id IS NOT ?)",
        (book, purchase["type_id"], refund["id"], book, purchase["type_id"]),
    )
    return link(conn, link_id)


def link_refunds(conn: sqlite3.Connection) -> list[dict]:
    """Link every refund that has exactly one purchase that fits, in the
    caller's open change; returns the links made, in the order made."""
    _begin(conn)
    purchases = _purchases(conn)
    dismissed = _dismissed(conn)
    refunds = _unlinked_refunds(conn)
    made: list[dict] = []
    while True:
        linked = []
        for refund in refunds:
            fits = _fits(refund, purchases, dismissed)
            if len(fits) != 1:
                continue
            made.append(_write(conn, refund, fits[0], AUTO))
            fits[0]["left_minor"] += refund["amount_minor"]
            linked.append(refund["id"])
        if not linked:
            return made
        refunds = [r for r in refunds if r["id"] not in linked]


def link_on_import(conn: sqlite3.Connection) -> list[dict]:
    """The import confirm's run, inside the import's change: link_refunds."""
    return link_refunds(conn)


def _shown(r: dict) -> dict:
    return {"id": r["id"], "description": r["description"], "date": r["date"],
            "account_name": r["account_name"], "amount_minor": r["amount_minor"], "currency": r["currency"]}


def asks(conn: sqlite3.Connection) -> list[dict]:
    """The refunds two or more purchases fit, each with those purchases: the
    Queue's "Refund of which … order?". One read each of refunds, purchases
    and dismissals, whatever their number."""
    purchases = _purchases(conn)
    dismissed = _dismissed(conn)
    found = []
    for refund in _unlinked_refunds(conn):
        fits = _fits(refund, purchases, dismissed)
        if len(fits) >= 2:
            found.append({"kind": "which", "refund": _shown(refund),
                          "purchases": [{**_shown(p), "left_minor": p["left_minor"]} for p in fits]})
    return found


def link_of(conn: sqlite3.Connection, row_id: int) -> dict:
    """A refund row's link and its purchase, for the row sheet's 'refund of
    20 Jul': {refund_link, purchase, words}, each None when it has none."""
    found = conn.execute("SELECT id, purchase_row_id FROM refund_link WHERE refund_row_id = ?",
                         (row_id,)).fetchone()
    if found is None:
        return {"refund_link": None, "purchase": None, "words": None}
    purchase = dict(conn.execute(_ROWS + "WHERE t.id = ?", (found["purchase_row_id"],)).fetchone())
    return {"refund_link": link(conn, found["id"]), "purchase": _shown(purchase),
            "words": f"refund of {day_words(purchase['date'])}"}


def _one(conn: sqlite3.Connection, row_id: int) -> dict | None:
    found = _dated(conn.execute(_ROWS + "WHERE t.id = ?", (row_id,)).fetchall())
    return found[0] if found else None


def hand_link(conn: sqlite3.Connection, refund_row_id: int, purchase_row_id: int) -> dict:
    """Link a refund to the purchase the operator picked (the Queue's ask).
    Refused, writing nothing, when the refund is not one or already has a
    link, the purchase is not one, is in another currency or dated after the
    refund, or has less left to refund than the refund."""
    _begin(conn)
    refund = _one(conn, refund_row_id)
    if refund is None:
        raise Refused("no such refund row")
    purchase = _one(conn, purchase_row_id)
    if purchase is None:
        raise Refused("no such purchase row")
    if refund["flow_type"] != "refund" or refund["amount_minor"] >= 0:
        raise Refused(f"{refund['description']} is not a refund")
    held = conn.execute("SELECT t.date FROM refund_link l JOIN transactions t ON t.id = l.purchase_row_id "
                        "WHERE l.refund_row_id = ?", (refund["id"],)).fetchone()
    if held is not None:
        raise Refused(f"{refund['description']} is already a refund of {day_words(held[0])}: unlink it first")
    if purchase["flow_type"] != "expense" or purchase["amount_minor"] <= 0:
        raise Refused(f"{purchase['description']} is not a purchase: a refund links to money out spending")
    if purchase["currency"] != refund["currency"]:
        raise Refused(f"{purchase['description']} is in {purchase['currency']}, the refund in {refund['currency']}")
    if purchase["day"] > refund["day"]:
        raise Refused(f"{purchase['description']} of {day_words(purchase['date'])} is after the refund")
    if -refund["amount_minor"] > purchase["left_minor"]:
        raise Refused(f"{purchase['description']} of {day_words(purchase['date'])} has "
                      f"{expect_back.full(purchase['left_minor'], purchase['currency'])} left to refund")
    return _write(conn, refund, purchase, HAND)
