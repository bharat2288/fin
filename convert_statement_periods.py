"""Conversion step: statement periods (fin-redesign 04, spec case 11).

Gives an existing database the first day each statement covers beside its
closing day: statements gain `period_start`, in the shape schema.sql declares,
and the statements history triggers are put in place with it. Every existing
record is given the start known on its own; what kind of record it is decides
how (statement_period.py):

- a calendar-month record (printed = 0: rows from a source that states no
  balance, filed by their own month): the 1st of its month, whatever day it
  is dated;
- derived loan interest (loan_interest.DERIVED_STATEMENT on a loan): the day
  after the loan's figure before its closing day, the period it was worked out
  over; the 1st of its month when there is none;
- a statement as printed (printed = 1, written by the import of a statement
  that states its balances): none is stored. No import before this step kept
  the opening day its parser read, so its start is read, whenever it is asked
  for, from the closing before it (statement_period.start), exactly as for a
  statement imported from now on that states no opening day. The command
  reports how those read.

No transaction row, no other column of a statement and no total changes; the
step's invariant reads every row before and after. It runs only through the
conversion runner (conversion.run_step), so it sits behind a backup, in one
transaction, and a second run changes nothing: it is applied once the column
is there. Like every step it records no change entry: the runner closes one
the book was left holding open before it runs, so no Undo takes a start back.

    python convert_statement_periods.py <path to database>
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import conversion
import history
import loan_interest
import statement_period

NAME = "statement-periods"
COLUMN = "period_start"
# As schema.sql declares the column.
_COLUMN_SQL = f"{COLUMN} TEXT"
# Read by the change-history step: a book reaching it has no period start
# yet, so its statements triggers leave it out until this step adds it.
ADDS_COLUMNS = {"statements": (COLUMN,)}

# How a record's start was told, in the order the command reports them.
MONTH_RECORD = "month records from the 1st"
LOAN_FIGURE = "loan interest from the figure before"
FROM_PREVIOUS = "printed, read from the previous closing"
PRINTED_FIRST = "printed, read from the 1st"
HOW = (MONTH_RECORD, LOAN_FIGURE, FROM_PREVIOUS, PRINTED_FIRST)

_TRIGGER_NAME = re.compile(r"CREATE TRIGGER IF NOT EXISTS (history_statements_(?:insert|update|delete)) ")


def has_shape(conn: sqlite3.Connection) -> bool:
    """Whether a statement record can say the first day it covers."""
    return COLUMN in {r[1] for r in conn.execute("PRAGMA table_info(statements)")}


def planned(conn: sqlite3.Connection) -> list[tuple[str | None, str, int]]:
    """(start to store, how it was told, statement id) for every statement
    record, read from the records themselves and never from a start already
    held. A printed statement's start to store is None: it is read, not
    stored (statement_period.start)."""
    records = conn.execute(
        "SELECT s.id, s.account_id, s.statement_date, s.printed, s.filename, a.type"
        " FROM statements s LEFT JOIN accounts a ON a.id = s.account_id"
        " ORDER BY s.account_id, s.statement_date, s.id"
    ).fetchall()
    out = []
    for sid, account_id, closing, printed, filename, kind in records:
        first = statement_period.first_of_month(closing)
        if printed:
            told_from = statement_period.previous_closing(conn, account_id, closing)
            out.append((None, FROM_PREVIOUS if told_from else PRINTED_FIRST, sid))
        elif filename == loan_interest.DERIVED_STATEMENT and kind == loan_interest.LOAN:
            before = conn.execute(
                "SELECT MAX(date) FROM anchors WHERE account_id = ? AND date < ?", (account_id, closing)
            ).fetchone()[0]
            out.append((statement_period.day_after(before) if before else first, LOAN_FIGURE, sid))
        else:
            out.append((first, MONTH_RECORD, sid))
    return out


def told(conn: sqlite3.Connection) -> dict[str, int]:
    """How many records each way of telling their start covers."""
    counts = Counter(how for _, how, _ in planned(conn))
    return {how: counts.get(how, 0) for how in HOW}


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute(f"ALTER TABLE statements ADD COLUMN {_COLUMN_SQL}")
    # The history records every column: the triggers the change-history step
    # made without it are replaced by schema.sql's.
    for statement in history.table_triggers("statements"):
        conn.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME.match(statement).group(1)}")
        conn.execute(statement)
    conn.executemany(
        f"UPDATE statements SET {COLUMN} = ? WHERE id = ? AND {COLUMN} IS NULL",
        [(start, sid) for start, _, sid in planned(conn) if start is not None],
    )


def _digest(conn: sqlite3.Connection, sql: str) -> str:
    """A digest of what `sql` reads, so the measure holds no row's contents."""
    out = hashlib.sha256()
    for row in conn.execute(sql):
        out.update(repr(tuple(row)).encode())
    return out.hexdigest()


def _measure(conn: sqlite3.Connection) -> dict:
    return {
        **conversion.measure(conn),
        "transactions": _digest(conn, "SELECT * FROM transactions ORDER BY id"),
        "statements": _digest(
            conn, "SELECT id, account_id, statement_date, filename, imported_at, printed FROM statements ORDER BY id"
        ),
        # A printed statement's start is read, not stored: every other
        # record stores its own.
        "without_start": conn.execute(
            f"SELECT COUNT(*) FROM statements WHERE printed = 0 AND {COLUMN} IS NULL"
        ).fetchone()[0] if has_shape(conn) else None,
    }


def _invariant(before: dict, after: dict) -> list[str]:
    problems = conversion.unchanged(before, after)
    if before["transactions"] != after["transactions"]:
        problems.append("transaction rows changed")
    if before["statements"] != after["statements"]:
        problems.append("statement records changed beyond their period start")
    if after["without_start"]:
        problems.append(f"{after['without_start']} month or loan-interest records have no period start")
    return problems


STEP = conversion.Step(
    name=NAME, apply=_apply, is_applied=has_shape, invariant=_invariant, measure=_measure,
)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_statement_periods.py <path to database>")
        return 2
    path = Path(argv[0]).resolve()
    if not path.is_file():
        print(f"no database at {path}")
        return 1

    try:
        report = conversion.run_step(path, STEP)
    except (conversion.ConversionRefused, conversion.ConversionFailed) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print(f"step {report['step']}: {report['status']}")
    if report["backup"]:
        # Counts only: a name, a date or an amount is never printed.
        print(f"backup: {report['backup']}")
        print(f"rows before: {report['before']['rows']}")
        print(f"rows after:  {report['after']['rows']}")
        copy = conversion.read_copy(path)
        try:
            counts = told(copy)
        finally:
            copy.close()
        print("period starts: " + "; ".join(f"{how}: {n}" for how, n in counts.items()))
        print("no transaction row was changed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
