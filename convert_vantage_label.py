"""Conversion step: move the Vantage card's header-label rows to its balance account.

Before the card was one balance (Q3, ruled 2026-10-05), an import filed the
card's own rows (bill payments before the first cardholder's block, fees after
the last SUB-TOTAL) on an account named after the card's header, e.g.
"DBS VANTAGE VISA INFINITE 7436" (parse_dbs._normalize_card_header). The card
now files them on its balance account (card_balance.BALANCE_ACCOUNTS), so a
re-import would put the same rows there a second time. This step moves every
row on the header-label account (matched by its exact name, never by last
four: the cardholder accounts share the card's digits) onto the balance
account, then removes the emptied label account, or archives it when an
anchor is still recorded on it.

Rows keep their ids and every column but their statement: a label statement
is merged into the balance account's record for the same day (a printed
statement) or month (a month record), as an import files a row
(ingest.ensure_statement), or moved to the balance account when it has none.
Anything that names the label as its other side or its account (a row's
other_side_id, a subscription) is pointed at the balance account.

It refuses, before anything is written, when the balance account is missing
or holds another currency, and when a row on the label matches a row already
on the balance account (same date, amount and description): each such pair
is listed and nothing moves, so no row is ever doubled.

It runs before change-history in the chain (conversion.CHAIN). The history's
triggers record only while an entry is open, which a conversion never opens,
so the move is not an undoable entry wherever it sits; before change-history
it also never runs on a book with triggers it did not start with.

    python convert_vantage_label.py <path to database>
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import card_balance
import conversion
import ingest


def _pairs(conn: sqlite3.Connection) -> list[tuple[str, list[int], int | None]]:
    """(label name, ids of accounts of exactly that name, id of the balance
    account or None) for each declared header label."""
    out = []
    for four, label in sorted(card_balance.HEADER_LABELS.items()):
        ids = [r[0] for r in conn.execute("SELECT id FROM accounts WHERE name = ? ORDER BY id", (label,))]
        target = conn.execute(
            "SELECT id FROM accounts WHERE name = ?", (card_balance.BALANCE_ACCOUNTS[four],)
        ).fetchone()
        out.append((label, ids, target[0] if target else None))
    return out


def _rows_on(conn: sqlite3.Connection, account_ids: list[int]) -> list[tuple]:
    if not account_ids:
        return []
    marks = ", ".join("?" * len(account_ids))
    return conn.execute(
        "SELECT t.id, t.date, t.amount_minor, t.description FROM transactions t "
        f"JOIN statements s ON s.id = t.statement_id WHERE s.account_id IN ({marks}) ORDER BY t.id",
        account_ids,
    ).fetchall()


def waiting(conn: sqlite3.Connection) -> int:
    """How many rows sit on a header-label account."""
    return sum(len(_rows_on(conn, ids)) for _, ids, _ in _pairs(conn))


def conflicts(conn: sqlite3.Connection) -> list[str]:
    """Each label row that matches a row already on its balance account."""
    out = []
    for label, ids, target in _pairs(conn):
        if target is None:
            continue
        held = {}
        for row_id, day, amount, text in _rows_on(conn, [target]):
            held.setdefault((day, amount, text), row_id)
        for row_id, day, amount, text in _rows_on(conn, ids):
            if (day, amount, text) in held:
                out.append(
                    f"row {row_id} on {label!r} ({day}, {amount} minor units, {text!r}) "
                    f"matches row {held[(day, amount, text)]} already on the balance account"
                )
    return out


def _refusal(conn: sqlite3.Connection) -> str | None:
    for label, ids, target in _pairs(conn):
        if not ids:
            continue
        if target is None:
            four = next(f for f, name in card_balance.HEADER_LABELS.items() if name == label)
            return (
                f"the balance account {card_balance.BALANCE_ACCOUNTS[four]!r} the rows on "
                f"{label!r} belong on is missing"
            )
        currencies = {
            r[0] or "SGD"
            for r in conn.execute(
                f"SELECT currency FROM accounts WHERE id IN ({', '.join('?' * (len(ids) + 1))})",
                ids + [target],
            )
        }
        if len(currencies) > 1:
            return f"{label!r} and its balance account hold different currencies"
    found = conflicts(conn)
    if found:
        return (
            f"{len(found)} row{'s' if len(found) != 1 else ''} on the header-label account "
            "already sit on the balance account; nothing moves, so none is doubled: "
            + "; ".join(found)
        )
    return None


def _is_applied(conn: sqlite3.Connection) -> bool:
    # Applied when no account of a label's name is left, or the one left is
    # archived and holds no statement.
    for _, ids, _ in _pairs(conn):
        for account_id in ids:
            status = conn.execute("SELECT status FROM accounts WHERE id = ?", (account_id,)).fetchone()[0]
            holds = conn.execute(
                "SELECT 1 FROM statements WHERE account_id = ? LIMIT 1", (account_id,)
            ).fetchone()
            if holds or status != "archived":
                return False
    return True


def _move_statement(conn: sqlite3.Connection, stmt_id: int, day: str, printed: int, target: int) -> None:
    if printed:
        same = conn.execute(
            "SELECT id, printed FROM statements WHERE account_id = ? AND statement_date = ?",
            (target, day),
        ).fetchone()
        if same and same[1]:
            _merge(conn, stmt_id, same[0])
            return
        if same:
            # A month record on the closing day moves to a free day of its
            # month, keeping its rows, as ingest.ensure_statement does.
            conn.execute(
                "UPDATE statements SET statement_date = ? WHERE id = ?",
                (ingest._free_day_in_month(conn, target, day[:7]), same[0]),
            )
        conn.execute("UPDATE statements SET account_id = ? WHERE id = ?", (target, stmt_id))
        return
    month = conn.execute(
        "SELECT id FROM statements WHERE account_id = ? AND printed = 0 AND statement_date LIKE ? "
        "ORDER BY statement_date LIMIT 1",
        (target, f"{day[:7]}-%"),
    ).fetchone()
    if month:
        _merge(conn, stmt_id, month[0])
        return
    taken = conn.execute(
        "SELECT 1 FROM statements WHERE account_id = ? AND statement_date = ?", (target, day)
    ).fetchone()
    on = ingest._free_day_in_month(conn, target, day[:7]) if taken else day
    conn.execute(
        "UPDATE statements SET account_id = ?, statement_date = ? WHERE id = ?", (target, on, stmt_id)
    )


def _merge(conn: sqlite3.Connection, stmt_id: int, into: int) -> None:
    conn.execute("UPDATE transactions SET statement_id = ? WHERE statement_id = ?", (into, stmt_id))
    conn.execute("DELETE FROM statements WHERE id = ?", (stmt_id,))


def _apply(conn: sqlite3.Connection) -> None:
    for _, ids, target in _pairs(conn):
        for account_id in ids:
            for stmt_id, day, printed in conn.execute(
                "SELECT id, statement_date, printed FROM statements WHERE account_id = ? ORDER BY id",
                (account_id,),
            ).fetchall():
                _move_statement(conn, stmt_id, day, printed, target)
            conn.execute(
                "UPDATE transactions SET other_side_id = ? WHERE other_side_id = ?", (target, account_id)
            )
            conn.execute(
                "UPDATE subscriptions SET account_id = ? WHERE account_id = ?", (target, account_id)
            )
            anchored = conn.execute(
                "SELECT 1 FROM anchors WHERE account_id = ? LIMIT 1", (account_id,)
            ).fetchone()
            if anchored:
                # An anchor is the label's own record of a balance; it is kept,
                # on an account that no longer shows.
                conn.execute("UPDATE accounts SET status = 'archived' WHERE id = ?", (account_id,))
            else:
                conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def _measure(conn: sqlite3.Connection) -> dict:
    out = conversion.measure(conn)
    out["pairs"] = [(ids, target) for _, ids, target in _pairs(conn)]
    return out


def _invariant(before: dict, after: dict) -> list[str]:
    problems = []
    for table in sorted(set(before["rows"]) | set(after["rows"])):
        if table in ("statements", "accounts"):
            # A merged statement and a removed label account go.
            continue
        was, now = before["rows"].get(table), after["rows"].get(table)
        if was != now:
            problems.append(f"row count of {table} changed: {was} -> {now}")
    was_totals, now_totals = before["account_totals_cents"], after["account_totals_cents"]
    moved = set()
    for ids, target in before["pairs"]:
        if not ids:
            continue
        moved.update(ids)
        moved.add(target)
        pooled = sum(was_totals.get(a) or 0 for a in ids + [target])
        if (now_totals.get(target) or 0) != pooled:
            problems.append(
                f"balance account {target} does not hold the label's rows and its own: "
                f"{now_totals.get(target)} against {pooled} cents"
            )
        for a in ids:
            if now_totals.get(a):
                problems.append(f"label account {a} still holds rows")
    for account_id in set(was_totals) | set(now_totals):
        if account_id in moved:
            continue
        if was_totals.get(account_id) != now_totals.get(account_id):
            whose = "rows on no account" if account_id is None else f"account {account_id}"
            problems.append(
                f"total of {whose} changed: {was_totals.get(account_id)} -> {now_totals.get(account_id)} cents"
            )
    return problems


STEP = conversion.Step(
    name="vantage-header-label",
    apply=_apply,
    is_applied=_is_applied,
    invariant=_invariant,
    measure=_measure,
    refusal=_refusal,
)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_vantage_label.py <path to database>")
        return 2
    path = Path(argv[0]).resolve()
    if not path.is_file():
        print(f"no database at {path}")
        return 1

    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        to_move = waiting(conn) if _has_shape(conn) else 0
    finally:
        conn.close()

    try:
        report = conversion.run_step(path, STEP)
    except (conversion.ConversionRefused, conversion.ConversionFailed) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print(f"step {report['step']}: {report['status']}")
    if report["backup"]:
        print(f"backup: {report['backup']}")
        print(f"rows before: {report['before']['rows']}")
        print(f"rows after:  {report['after']['rows']}")
        print(f"rows moved to the balance account: {to_move}")
    else:
        print("rows moved to the balance account: 0 (no header-label account holds a row)")
    return 0


def _has_shape(conn: sqlite3.Connection) -> bool:
    """Whether the rows carry whole minor units, so they can be read."""
    return "amount_minor" in {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
