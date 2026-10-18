"""Book prefill: the book a row's picker shows first, and why (fin-surfaces 10).

First match wins:
- `last`: the operator's last hand-set choice for this merchant on this
  account: the most recent hand-set row (by date, then id) of the same
  merchant on the same account, other than the row itself;
- `account`: the account's owner, when it is a company and not Household;
- `type`: the book the row's type proposes (book_type.proposed_book), the
  row's own type else its merchant's. None means the type asks (Software &
  AI tools), or the row has no type at all.

This amends book_type's "proposed by its type, never by the paying account"
for a picker's first value: company cards mostly carry company costs, and a
household card may carry a company's (spec 08, Book prefill). It only reads;
resolve still writes the book the screen sends.
"""

from __future__ import annotations

import sqlite3

import book_type

LAST = "last"
ACCOUNT = "account"
TYPE = "type"

# SQLite's bound-variable limit is 999 on older builds; read in slices below it.
_CHUNK = 900


def _chunks(ids: list[int]):
    for i in range(0, len(ids), _CHUNK):
        yield ids[i:i + _CHUNK]


def book_prefills(conn: sqlite3.Connection, tx_ids) -> dict[int, dict]:
    """Each known row's prefill, in a fixed number of reads however many rows:
    {tx_id: {book, reason, account, account_kind}}. `account` is the paying
    account's name, unmasked; an unknown id is left out."""
    ids = list(dict.fromkeys(int(i) for i in tx_ids))
    rows = []
    for chunk in _chunks(ids):
        rows += conn.execute(
            "SELECT t.id, t.service_id, s.account_id, a.name AS account, a.type AS kind, a.owner,"
            " COALESCE(t.type_id, svc.type_id) AS type_id"
            " FROM transactions t JOIN statements s ON s.id = t.statement_id"
            " JOIN accounts a ON a.id = s.account_id"
            " LEFT JOIN services svc ON svc.id = t.service_id"
            f" WHERE t.id IN ({','.join('?' * len(chunk))})",
            chunk,
        ).fetchall()

    # The two latest hand-set rows of each (merchant, account) the rows sit
    # on: the latest, and the one before it for when the latest is the row.
    latest: dict[tuple[int, int], list[tuple[int, str]]] = {}
    services = sorted({r["service_id"] for r in rows if r["service_id"] is not None})
    for chunk in _chunks(services):
        for h in conn.execute(
            "SELECT t.id, t.service_id, s.account_id, COALESCE(t.book, ?) AS book"
            " FROM transactions t JOIN statements s ON s.id = t.statement_id"
            f" WHERE t.cat_source = 'manual' AND t.service_id IN ({','.join('?' * len(chunk))})"
            " ORDER BY t.date DESC, t.id DESC",
            (book_type.DEFAULT_BOOK, *chunk),
        ):
            held = latest.setdefault((h["service_id"], h["account_id"]), [])
            if len(held) < 2:
                held.append((h["id"], h["book"]))

    type_names = book_type.spending_type_names(conn)
    out = {}
    for r in rows:
        last = next((book for hid, book in latest.get((r["service_id"], r["account_id"]), [])
                     if hid != r["id"]), None)
        if last is not None:
            book, reason = last, LAST
        elif r["owner"] and r["owner"] != book_type.DEFAULT_BOOK and r["owner"] in book_type.BOOK_NAMES:
            book, reason = r["owner"], ACCOUNT
        else:
            name = type_names.get(r["type_id"]) if r["type_id"] is not None else None
            book, reason = (book_type.proposed_book(name) if name else None), TYPE
        out[r["id"]] = {"book": book, "reason": reason, "account": r["account"], "account_kind": r["kind"]}
    return out


def book_prefill(conn: sqlite3.Connection, tx_id: int) -> tuple[str | None, str] | None:
    """One row's prefill as (book, reason); None for a row fin does not hold."""
    hit = book_prefills(conn, [tx_id]).get(int(tx_id))
    return (hit["book"], hit["reason"]) if hit else None
