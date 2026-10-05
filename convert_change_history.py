"""Conversion step: the change history.

Gives an existing database what fin-surfaces 01 ruled: one history of every
change, undoable. Adds schema.sql's history block (the two tables and the
triggers on every tracked table, history.py). No existing row is touched and
nothing is recorded for the past: the history starts empty. It runs only
through the conversion runner (conversion.run_step), so it sits behind a
backup, in one transaction, and a second run changes nothing.

    python convert_change_history.py <path to database>
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import conversion
import db
import history


_CREATE_TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+) \(.*?\n\);", re.DOTALL)


def _declared_tables() -> dict[str, str]:
    """Each table's CREATE statement, as schema.sql declares it."""
    return {m.group(1): m.group(0) for m in _CREATE_TABLE.finditer(db.SCHEMA_PATH.read_text())}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _refusal(conn: sqlite3.Connection) -> str | None:
    """A tracked table that exists without a column its triggers record."""
    declared = sqlite3.connect(":memory:")
    try:
        declared.executescript(history.without_history(db.SCHEMA_PATH.read_text()))
        present = _tables(conn)
        for table in history.TRACKED:
            if table not in present:
                continue
            wanted = [r[1] for r in declared.execute(f"PRAGMA table_info({table})")]
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            missing = [c for c in wanted if c not in have]
            if missing:
                return f"{table} has no {', '.join(missing)}; the history records every column"
    finally:
        declared.close()
    return None


def _apply(conn: sqlite3.Connection) -> None:
    # A tracked table a start has not made yet (fin makes some empty on its
    # first start) is made here, as declared, so its triggers have a table.
    declared, present = _declared_tables(), _tables(conn)
    for table in history.TRACKED:
        if table not in present:
            conn.execute(declared[table])
    for statement in history.schema_statements():
        conn.execute(statement)


STEP = conversion.Step(
    name="change-history", apply=_apply, is_applied=history.has_shape, refusal=_refusal
)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_change_history.py <path to database>")
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
        print(f"backup: {report['backup']}")
        print(f"rows before: {report['before']['rows']}")
        print(f"rows after:  {report['after']['rows']}")
        print("the change history starts empty; every change from now on is recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
