"""Duplicate rows (fin RUL-026): a row may be taken out of the book only when
it has a twin, another row on the same account, the same day and the same
amount. Wording is not compared: the same payment can be worded two ways.

A twin pair is listed with the statement each row came in on. Two printed
statements whose periods both cover the day, one row on each, is one payment
read twice (one import holding both statements counted it twice, before the
import counted a row by the statement that printed it). One statement
printing both may be two real payments: the operator checks the statement.

Removing a twin is one change in Changes, and its Undo puts the row back as
it was, with its id. A row another record rests on is never removed: an
expect-back mark, a set-off, a refund link, a set-aside, a folio link or a
cross-currency pair. Its twin can be removed instead, or the record taken off
first. A row fin worked out from figures (loan interest) is no twin: it is
replaced whole when its figures change.

app.py registers the blueprint with one line and merges SUMMARIES into the
change history's words with another.

  GET  /api/duplicates?account_id=&tx_id=          {twins: [...]}, newest day first
  POST /api/transactions/<id>/remove-duplicate     one change

A refusal is a 4xx with {"error": words} and writes nothing.
"""

from __future__ import annotations

import sqlite3

from flask import Blueprint, g, jsonify, request

import expect_back
import loan_interest
import statement_period
from db import get_db

NOT_FOUND = "no such row"
NO_TWIN = ("{row} has no twin: no other row on {account} on {day} for {amount}. "
           "Only a row with a twin can be removed; nothing was changed.")

# What a record resting on a row is called, for the refusal. (table, the
# columns naming a row, words): a table here names rows by id, some with no
# foreign key (folio_link, fx_pair), so taking one out would leave it
# pointing at nothing.
_RESTING = (
    ("expectation", ("row_id",), "is expected back or to pay back"),
    ("set_off", ("payment_row_id",), "is set off against what is expected back"),
    ("refund_link", ("refund_row_id", "purchase_row_id"), "is linked as a refund"),
    ("dismissal", ("row_id", "purchase_row_id"), "was set aside (not a payback, or a refund unlinked)"),
    ("fx_pair", ("out_row_id", "in_row_id"), "is in a cross-currency pair"),
)

# Rows on an imported statement: fin's own worked-out loan interest is not one.
_IMPORTED = (
    "FROM transactions t JOIN statements s ON s.id = t.statement_id "
    "JOIN accounts a ON a.id = s.account_id "
    "WHERE COALESCE(s.filename, '') != ? "
)


def _row(conn: sqlite3.Connection, tx_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT t.id, t.date, t.description, t.amount_minor, t.flow_type, t.book, t.type_id, t.statement_id, "
        "s.account_id, a.name AS account, COALESCE(a.currency, 'SGD') AS currency, "
        "s.statement_date, s.period_start, s.printed " + _IMPORTED + "AND t.id = ?",
        (loan_interest.DERIVED_STATEMENT, tx_id),
    ).fetchone()


def _twin_rows(conn: sqlite3.Connection, account_id: int, day: str, amount_minor: int) -> list[sqlite3.Row]:
    ids = [r[0] for r in conn.execute(
        "SELECT t.id " + _IMPORTED + "AND s.account_id = ? AND t.date = ? AND t.amount_minor = ? ORDER BY t.id",
        (loan_interest.DERIVED_STATEMENT, account_id, day, amount_minor),
    )]
    return [_row(conn, i) for i in ids]


def resting_on(conn: sqlite3.Connection, tx_id: int) -> str | None:
    """What rests on a row, in words ("is expected back"), or None."""
    if conn.execute("SELECT 1 FROM folio_link WHERE subject = 'row' AND subject_id = ?", (tx_id,)).fetchone():
        return "carries a folio link"
    for table, columns, words in _RESTING:
        where = " OR ".join(f"{c} = ?" for c in columns)
        if conn.execute(f"SELECT 1 FROM {table} WHERE {where}", (tx_id,) * len(columns)).fetchone():
            return words
    return None


def _named(row) -> str:
    return f"{row['description']} (#{row['id']})"


def _amount(row) -> str:
    return expect_back.full(abs(row["amount_minor"]), row["currency"])


def _day(day: str) -> str:
    return loan_interest.day_shown(day)


def refusal(conn: sqlite3.Connection, row, twins: list) -> str | None:
    """Why this row may not be removed, in words, or None."""
    others = [t for t in twins if t["id"] != row["id"]]
    if not others:
        return NO_TWIN.format(row=_named(row), account=row["account"], day=_day(row["date"]), amount=_amount(row))
    resting = resting_on(conn, row["id"])
    if resting is None:
        return None
    free = [t for t in others if resting_on(conn, t["id"]) is None]
    instead = (f" Remove its twin #{free[0]['id']} instead, or take that off first."
               if free else " Take that off first.")
    return f"{_named(row)} {resting}, so it stays.{instead} Nothing was changed."


def _statement(conn: sqlite3.Connection, row) -> dict:
    return {
        "id": row["statement_id"],
        "from": statement_period.start(conn, row["account_id"], row["statement_date"], row["period_start"],
                                       row["printed"]),
        "to": statement_period.end(row["statement_date"], row["printed"]),
        "printed": bool(row["printed"]),
    }


def _reading(rows: list[dict]) -> str:
    statements = {r["statement"]["id"] for r in rows}
    if len(statements) == 1:
        if rows[0]["statement"]["printed"]:
            return ("One statement printed both: they may be two real payments. "
                    "Check the statement before removing one.")
        return "Both came from one file with no balances: check it before removing one."
    if all(r["statement"]["printed"] for r in rows):
        return ("Each is on a different one of two statements whose periods both cover the day: "
                "most likely one payment the import read twice.")
    return "They came in from different files: check them before removing one."


def _group(conn: sqlite3.Connection, rows: list) -> dict:
    first = rows[0]
    shown = [{
        "id": r["id"],
        "description": r["description"],
        "flow_type": r["flow_type"],
        "book": r["book"],
        "type_id": r["type_id"],
        "statement": _statement(conn, r),
        "refusal": refusal(conn, r, rows),
    } for r in rows]
    return {
        "account_id": first["account_id"],
        "account": first["account"],
        "currency": first["currency"],
        "date": first["date"],
        "amount_minor": first["amount_minor"],
        "amount": _amount(first),
        "rows": shown,
        "statements": len({r["statement_id"] for r in rows}),
        "reading": _reading(shown),
    }


def groups(conn: sqlite3.Connection, account_id: int | None = None, tx_id: int | None = None) -> list[dict]:
    """Every set of twins, newest day first: on one account, or a row's own."""
    if tx_id is not None:
        row = _row(conn, tx_id)
        if row is None:
            return []
        rows = _twin_rows(conn, row["account_id"], row["date"], row["amount_minor"])
        return [_group(conn, rows)] if len(rows) > 1 else []
    sql = ("SELECT s.account_id, t.date, t.amount_minor " + _IMPORTED
           + ("AND s.account_id = ? " if account_id is not None else "")
           + "GROUP BY s.account_id, t.date, t.amount_minor HAVING COUNT(*) > 1 "
             "ORDER BY t.date DESC, s.account_id, t.amount_minor DESC")
    args = (loan_interest.DERIVED_STATEMENT,) + ((account_id,) if account_id is not None else ())
    return [_group(conn, _twin_rows(conn, *key)) for key in conn.execute(sql, args).fetchall()]


bp = Blueprint("duplicates", __name__)

# endpoint (without the blueprint's "duplicates." prefix) -> what the change
# did; the route names its row in its own summary.
SUMMARIES: dict[str, str] = {
    "duplicates_remove": "Removed a duplicate row",
}


@bp.route("/api/duplicates")
def duplicates_list():
    with get_db() as conn:
        return jsonify({"twins": groups(conn, request.args.get("account_id", type=int),
                                        request.args.get("tx_id", type=int))})


@bp.route("/api/transactions/<int:tx_id>/remove-duplicate", methods=["POST"])
def duplicates_remove(tx_id: int):
    """Take a row with a twin out of the book: one change, undone whole."""
    with get_db() as conn:
        row = _row(conn, tx_id)
        if row is None:
            return jsonify({"error": NOT_FOUND}), 404
        twins = _twin_rows(conn, row["account_id"], row["date"], row["amount_minor"])
        why = refusal(conn, row, twins)
        if why is not None:
            return jsonify({"error": why}), 409
        stays = next(t for t in twins if t["id"] != tx_id)
        conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        entry_id = g.get("change_entry")
        if entry_id is not None:
            conn.execute(
                "UPDATE change_entries SET summary = ? WHERE id = ? AND summary = ''",
                (f"Removed a duplicate row: {row['description']}, {_amount(row)} on {_day(row['date'])} "
                 f"(its twin #{stays['id']} stays)", entry_id),
            )
        conn.commit()
    return jsonify({"removed": tx_id, "twin": stays["id"]})
