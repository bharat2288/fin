"""Conversion step: movements, the other side, and the review list.

Gives an existing database what the ledger's flows need:

- the rows gain an other side, in the shape schema.sql declares;
- existing rows are reclassified by the rules the classifier in flow.py now
  applies at import (ticket 05):
    - a PayNow receipt from Moom becomes a movement naming Moom;
    - a receipt the bank describes as salary, and a receipt from Kalesh,
      becomes income;
    - a loan instalment becomes a movement naming its loan;
    - a receipt from Independent Reserve becomes a movement naming the
      crypto holding;
    - an unlabelled transfer on a household bank account that counts as
      spending or income today goes to the review list.

It never touches a row whose flow was set by hand, a spending row in a
company's book, or anything about a row but its flow and its other side. It
runs only through the conversion runner (conversion.run_step), so it sits
behind a backup, in one transaction, and a second run changes nothing.

    python convert_movements.py <path to database>
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

import book_type
import conversion
import flow

# As schema.sql declares the column.
_OTHER_SIDE_COLUMN = "other_side_id INTEGER REFERENCES accounts(id)"
# The flows that count as spending or income today: only a row carrying one of
# them is moved to the review list.
_COUNTED_TODAY = ("expense", "income")


class AccountKindsNotConverted(Exception):
    """The accounts have no owner yet: convert_account_kinds.py runs first."""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def has_shape(conn: sqlite3.Connection) -> bool:
    """Whether the rows can name their other side."""
    return "other_side_id" in _columns(conn, "transactions")


def planned(conn: sqlite3.Connection) -> list[tuple[int, str, str, int | None]]:
    """The rows the rules reclassify: (id, flow now, flow to be, other side to be).

    Empty when every row already carries what the rules give it.
    """
    conn.row_factory = sqlite3.Row
    ctx = flow.build_context(conn)
    other_side = "t.other_side_id" if has_shape(conn) else "NULL"
    rows = conn.execute(
        f"""
        SELECT t.id, t.description, t.amount_sgd, t.service_id, t.type_id, t.book,
               COALESCE(t.flow_type, 'expense') AS flow_type, {other_side} AS other_side_id,
               a.type AS account_kind, a.owner AS account_owner
        FROM transactions t
        JOIN statements s ON s.id = t.statement_id
        JOIN accounts a ON a.id = s.account_id
        WHERE COALESCE(t.flow_type_manual, 0) = 0
        ORDER BY t.id
        """
    ).fetchall()

    changes = []
    for r in rows:
        held = (r["flow_type"], r["other_side_id"])
        # A company's cost keeps its flow as spending: its book says whose it is.
        if held[0] in ("expense", "refund") and r["book"] not in (None, book_type.DEFAULT_BOOK):
            continue
        wording = {"description": r["description"], "amount_sgd": r["amount_sgd"]}
        new = flow.classify_row(
            {
                **wording,
                "account_kind": r["account_kind"],
                "account_owner": r["account_owner"],
                "service_id": r["service_id"],
                "labelled": r["service_id"] is not None or r["type_id"] is not None,
            },
            ctx,
        )
        # Only the ledger's rules are applied here: a row the wording alone
        # classifies the same way is left with the flow it has.
        if new == (flow.classify_flow(wording, ctx), None) or new == held:
            continue
        if new[0] == flow.REVIEW and held[0] not in _COUNTED_TODAY:
            continue
        changes.append((r["id"], held[0], new[0], new[1]))
    return changes


def _is_applied(conn: sqlite3.Connection) -> bool:
    if not has_shape(conn):
        return False
    if "owner" not in _columns(conn, "accounts"):
        return False
    return not planned(conn)


def _apply(conn: sqlite3.Connection) -> None:
    if "owner" not in _columns(conn, "accounts"):
        raise AccountKindsNotConverted()
    if not has_shape(conn):
        conn.execute(f"ALTER TABLE transactions ADD COLUMN {_OTHER_SIDE_COLUMN}")
    for tx_id, _, new_flow, other_side in planned(conn):
        conn.execute(
            "UPDATE transactions SET flow_type = ?, other_side_id = ? WHERE id = ?"
            " AND COALESCE(flow_type_manual, 0) = 0",
            (flow.checked_flow(new_flow), other_side, tx_id),
        )


STEP = conversion.Step(name="movements", apply=_apply, is_applied=_is_applied)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_movements.py <path to database>")
        return 2
    path = Path(argv[0]).resolve()
    if not path.is_file():
        print(f"no database at {path}")
        return 1

    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        has_owner = "owner" in _columns(conn, "accounts")
        # Counts only: a description or an amount is never printed.
        tally = Counter((was, to) for _, was, to, _ in planned(conn)) if has_owner else Counter()
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
        print(f"account totals in cents before: {report['before']['account_totals_cents']}")
        print(f"account totals in cents after:  {report['after']['account_totals_cents']}")
        for (was, to), count in sorted(tally.items()):
            print(f"{was} -> {to}: {count}")
        print(f"{sum(tally.values())} rows reclassified; rows with a flow set by hand were not touched")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
