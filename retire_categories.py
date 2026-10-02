"""Conversion step: drop the category tree from a database whose rows already
carry book and type.

This is the contract half of the split. Nothing reads a category any more, so
the four category columns and the `categories` table go, and the merchants
gain their "review each time" mark. It changes no label: book and type are
what the book-and-type step (convert_book_type.py) left, and that step must
have run first. It runs only through the conversion runner
(conversion.run_step), so it sits behind a backup, in one transaction.

SQLite cannot drop a column that a foreign key names, and each category
column is one, so the four tables are rebuilt in the shape schema.sql
declares: a new table is made from that declaration, every row is copied
across, the old table is dropped and the new one takes its name.

    python retire_categories.py <path to database>
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import conversion
import convert_book_type

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# The tables that carried a category, and the column each one loses. Frozen:
# table and column names in the statements below come from here, from
# schema.sql and from the database's own table definitions, and nowhere else.
RETIRED = (
    ("transactions", "category_id"),
    ("services", "category_id"),
    ("merchant_rules", "category_override_id"),
    ("subscriptions", "category_id"),
)
CATEGORIES = "categories"
# What the merchants gain in the same rebuild.
REVIEW_MARK = "review_each_time"

_CREATE_TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", re.DOTALL)
_CREATE_INDEX = re.compile(r"CREATE INDEX IF NOT EXISTS \w+ ON \w+\(\w+\);")


class BookAndTypeNotApplied(Exception):
    """The book-and-type step has not run, or rows have arrived since that it
    has not labelled. Dropping the categories now would lose their labels."""


class ColumnNotInSchema(Exception):
    """A table holds a column schema.sql does not declare and this step does
    not retire. Rebuilding the table would drop it, so the step stops."""


# --- reading the declared shape -------------------------------------------


def _declared_tables() -> dict[str, str]:
    """Each table schema.sql declares, to the body of its CREATE TABLE."""
    return {name: body for name, body in _CREATE_TABLE.findall(SCHEMA_PATH.read_text())}


def _declared_indexes() -> list[str]:
    return [statement.rstrip(";") for statement in _CREATE_INDEX.findall(SCHEMA_PATH.read_text())]


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


# --- the step -------------------------------------------------------------


def _is_applied(conn: sqlite3.Connection) -> bool:
    if CATEGORIES in _tables(conn):
        return False
    if any(column in _columns(conn, table) for table, column in RETIRED):
        return False
    return REVIEW_MARK in _columns(conn, "services")


def _rebuild(conn: sqlite3.Connection, table: str, retired: str, body: str) -> None:
    """Remake `table` in its declared shape, keeping every row and its id."""
    new = f"{table}_new"
    conn.execute(f"CREATE TABLE {new} ({body}\n)")
    old_columns, new_columns = _columns(conn, table), _columns(conn, new)
    undeclared = [c for c in old_columns if c not in new_columns and c != retired]
    if undeclared:
        raise ColumnNotInSchema(f"{table}: {', '.join(undeclared)}")
    kept = ", ".join(c for c in new_columns if c in old_columns)
    # The next id the old table would have handed out is kept, so an id is
    # never given to a second row.
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)).fetchone()
    conn.execute(f"INSERT INTO {new} ({kept}) SELECT {kept} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {new} RENAME TO {table}")
    if sequence:
        conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table, sequence[0]))


def _apply(conn: sqlite3.Connection) -> None:
    if not convert_book_type.STEP.is_applied(conn):
        raise BookAndTypeNotApplied()
    declared = _declared_tables()
    for table, retired in RETIRED:
        _rebuild(conn, table, retired, declared[table])
    conn.execute(f"DROP TABLE IF EXISTS {CATEGORIES}")
    # Dropping a table drops its indexes; put back the declared ones.
    for statement in _declared_indexes():
        conn.execute(statement)


def _invariant(before: dict, after: dict) -> list[str]:
    """No row count and no account total moves; the categories table goes."""
    rows = {table: n for table, n in before["rows"].items() if table != CATEGORIES}
    return conversion.unchanged({**before, "rows": rows}, after)


STEP = conversion.Step(
    name="retire-categories", apply=_apply, is_applied=_is_applied, invariant=_invariant
)


# --- what the step will take away -----------------------------------------


def losses(conn: sqlite3.Connection) -> dict:
    """What a database about to lose its categories holds that book and type
    do not carry over. Read before the step runs; all zero on a database with
    no categories left."""
    counts = {"non_spending_rows_with_a_category": 0, "subscriptions_unlike_their_merchant": 0}
    if CATEGORIES not in _tables(conn):
        return counts
    counts["non_spending_rows_with_a_category"] = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE category_id IS NOT NULL"
        " AND COALESCE(flow_type, 'expense') NOT IN ('expense', 'refund')"
    ).fetchone()[0]
    if {"book", "type_id"} <= set(_columns(conn, "subscriptions")):
        counts["subscriptions_unlike_their_merchant"] = conn.execute(
            "SELECT COUNT(*) FROM subscriptions s LEFT JOIN services m ON m.id = s.service_id"
            " WHERE s.book IS NOT m.book OR s.type_id IS NOT m.type_id"
        ).fetchone()[0]
    return counts


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python retire_categories.py <path to database>")
        return 2
    path = Path(argv[0]).resolve()
    if not path.is_file():
        print(f"no database at {path}")
        return 1

    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        ready = convert_book_type.STEP.is_applied(conn)
        lost = losses(conn) if ready else None
    finally:
        conn.close()
    if not ready:
        print(
            "the book-and-type step has not run on this database, or rows have arrived "
            "since that it has not labelled. Run it first:\n"
            f"  python convert_book_type.py {argv[0]}"
        )
        return 1

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
        print(f"account totals (cents) before: {report['before']['account_totals_cents']}")
        print(f"account totals (cents) after:  {report['after']['account_totals_cents']}")
        print("held only in the backup from here on:")
        print(
            f"  {lost['non_spending_rows_with_a_category']:5d}  rows that are not spending "
            "(income, transfers, payments) and had a category"
        )
        print(
            f"  {lost['subscriptions_unlike_their_merchant']:5d}  subscriptions whose own "
            "label was not their merchant's (they now show the merchant's)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
