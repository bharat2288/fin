"""Conversion step: parties as a link with a role (fin-redesign 09, expand).

The design evaluation (§2) made a party a link, not a new home: the merchant
row is the party and its rules are its aliases. This step gives an existing
database the two columns that takes, in the shape schema.sql declares, both
empty: a merchant's `role` (party.ROLES) and an account's `party_id`. It puts
the services and accounts history triggers in place with them, so a role or
a link is recorded and an Undo puts it back. No merchant says a role and no
account names a party afterwards: the links are the operator's to make, one
change each (spec case 12). No row, merchant, account or figure changes.

It runs after flow-rules (conversion.CHAIN), and only through the conversion
runner, so it sits behind a backup, in one transaction, and a second run
changes nothing: the columns are there.

    python convert_party_link.py <path to database>
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import conversion
import history

NAME = "party-link"

# As schema.sql declares them: table -> (column, declaration).
_COLUMNS = {
    "services": ("role", "role TEXT"),
    "accounts": ("party_id", "party_id INTEGER REFERENCES services(id)"),
}
# Read by the steps before this one: a book reaching them has neither column
# yet, so a rebuild or a trigger made from schema.sql leaves them out until
# this step adds them.
ADDS_COLUMNS = {table: (column,) for table, (column, _) in _COLUMNS.items()}

_TRIGGER_NAME = re.compile(r"CREATE TRIGGER IF NOT EXISTS (history_\w+_(?:insert|update|delete)) ")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def has_shape(conn: sqlite3.Connection) -> bool:
    """Whether a merchant can say a role and an account name a party."""
    return all(column in _columns(conn, table) for table, (column, _) in _COLUMNS.items())


def _apply(conn: sqlite3.Connection) -> None:
    for table, (column, declaration) in _COLUMNS.items():
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {declaration}")
        # The history records every column: the triggers made without it
        # are replaced by schema.sql's.
        for statement in history.table_triggers(table):
            conn.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME.match(statement).group(1)}")
            conn.execute(statement)


def _measure(conn: sqlite3.Connection) -> dict:
    shaped = has_shape(conn)
    return {
        **conversion.measure(conn),
        "transactions": [tuple(r) for r in conn.execute("SELECT * FROM transactions ORDER BY id")],
        "rules": [tuple(r) for r in conn.execute("SELECT * FROM merchant_rules ORDER BY id")],
        # Each merchant and account as it was, the new column left out.
        "services": [tuple(r[c] for c in r.keys() if c != "role")
                     for r in _rows(conn, "SELECT * FROM services ORDER BY id")],
        "accounts": [tuple(r[c] for c in r.keys() if c != "party_id")
                     for r in _rows(conn, "SELECT * FROM accounts ORDER BY id")],
        "said": conn.execute(
            "SELECT (SELECT COUNT(*) FROM services WHERE role IS NOT NULL)"
            " + (SELECT COUNT(*) FROM accounts WHERE party_id IS NOT NULL)").fetchone()[0] if shaped else 0,
    }


def _rows(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    held = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.row_factory = held


def _invariant(before: dict, after: dict) -> list[str]:
    problems = conversion.unchanged(before, after)
    for part, said in (("transactions", "a row"), ("rules", "a merchant rule"),
                       ("services", "a merchant"), ("accounts", "an account")):
        if before[part] != after[part]:
            problems.append(f"{said} changed")
    if after["said"]:
        problems.append(f"{after['said']} merchants or accounts say a role or a party")
    return problems


STEP = conversion.Step(name=NAME, apply=_apply, is_applied=has_shape, invariant=_invariant, measure=_measure)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_party_link.py <path to database>")
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
        # Counts only: a name, a wording or an amount is never printed.
        print(f"backup: {report['backup']}")
        print(f"rows before: {report['before']['rows']}")
        print(f"rows after:  {report['after']['rows']}")
        print("no merchant says a role and no account names a party yet; no row was changed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
