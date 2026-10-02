"""Conversion step: accounts of every kind.

Gives an existing database what the ledger's accounts need:

- the accounts gain an owner (the household unless marked otherwise) and the
  database gains the anchors table, both in the shape schema.sql declares;
- today's credit-card and debit accounts become cards;
- the Kalesh business account and card are marked as Kalesh's;
- Moom and Kalesh (companies), the home, the rented property, the car and the
  crypto holding (holdings), and the home loan and the auto loan (loans) are
  created with no figure. One that is already there is left alone.

It touches no transaction row. It runs only through the conversion runner
(conversion.run_step), so it sits behind a backup, in one transaction.

    python convert_account_kinds.py <path to database>
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import account_kind
import conversion

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# The old account types that are cards now.
BECOME_CARDS = ("credit_card", "debit")
CARD = "card"

# How an account of Kalesh's is known: its name or short name says Kalesh, or
# its name is the one the Kalesh business-statement parser gives.
KALESH = "Kalesh"
KALESH_NAME = "%KALESH%"
KALESH_PARSER_NAME = "DBS BUSINESS%"

# (name, kind). Created with no figure, owned by the household: a company
# account is the household's money in that company.
CREATED = (
    ("Moom", "company"),
    ("Kalesh", "company"),
    ("Home", "holding"),
    ("Rented property", "holding"),
    ("Car", "holding"),
    ("Crypto held outside fin", "holding"),
    ("UOB home loan", "loan"),
    ("DBS auto loan", "loan"),
)

_ANCHORS_TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS anchors \((.*?)\n\);", re.DOTALL)
# As schema.sql declares the column.
_OWNER_COLUMN = "owner TEXT NOT NULL DEFAULT 'Household'"


class UndeclaredKind(Exception):
    """An account carries a type that is neither an old one this step
    converts nor a declared kind. The step stops for the operator to look."""


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _account_columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}


def has_shape(conn: sqlite3.Connection) -> bool:
    """Whether the accounts carry an owner and the anchors table exists."""
    return "anchors" in _tables(conn) and "owner" in _account_columns(conn)


def _missing(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (name, kind)
        for name, kind in CREATED
        if not conn.execute(
            "SELECT 1 FROM accounts WHERE name = ? AND type = ?", (name, kind)
        ).fetchone()
    ]


def _is_applied(conn: sqlite3.Connection) -> bool:
    if not has_shape(conn):
        return False
    if conn.execute(
        "SELECT 1 FROM accounts WHERE type IN (?, ?)", BECOME_CARDS
    ).fetchone():
        return False
    return not _missing(conn)


def _apply(conn: sqlite3.Connection) -> None:
    if "owner" not in _account_columns(conn):
        conn.execute(f"ALTER TABLE accounts ADD COLUMN {_OWNER_COLUMN}")
    if "anchors" not in _tables(conn):
        body = _ANCHORS_TABLE.search(SCHEMA_PATH.read_text()).group(1)
        conn.execute(f"CREATE TABLE anchors ({body}\n)")

    conn.execute("UPDATE accounts SET type = ? WHERE type IN (?, ?)", (CARD, *BECOME_CARDS))
    marks = ", ".join("?" for _ in account_kind.KIND_NAMES)
    if conn.execute(
        f"SELECT 1 FROM accounts WHERE type NOT IN ({marks})", account_kind.KIND_NAMES
    ).fetchone():
        raise UndeclaredKind()

    conn.execute(
        "UPDATE accounts SET owner = ? WHERE type IN (?, ?)"
        " AND (UPPER(name) LIKE ? OR UPPER(short_name) LIKE ? OR UPPER(name) LIKE ?)",
        (KALESH, *account_kind.STATEMENT_KINDS, KALESH_NAME, KALESH_NAME, KALESH_PARSER_NAME),
    )

    for name, kind in _missing(conn):
        conn.execute(
            "INSERT INTO accounts (name, short_name, type, owner) VALUES (?, ?, ?, ?)",
            (name, name, account_kind.checked_kind(kind), account_kind.HOUSEHOLD),
        )


def _invariant(before: dict, after: dict) -> list[str]:
    """No account total moves and no row count moves, except that the accounts
    grow by at most the ones this step creates."""
    problems = conversion.unchanged(
        {**before, "rows": {t: n for t, n in before["rows"].items() if t != "accounts"}},
        {**after, "rows": {t: n for t, n in after["rows"].items() if t != "accounts"}},
    )
    was, now = before["rows"].get("accounts", 0), after["rows"].get("accounts", 0)
    if not was <= now <= was + len(CREATED):
        problems.append(f"row count of accounts changed: {was} -> {now}")
    return problems


STEP = conversion.Step(
    name="account-kinds", apply=_apply, is_applied=_is_applied, invariant=_invariant
)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_account_kinds.py <path to database>")
        return 2
    path = Path(argv[0]).resolve()
    if not path.is_file():
        print(f"no database at {path}")
        return 1

    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        old_cards = conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE type IN (?, ?)", BECOME_CARDS
        ).fetchone()[0]
        to_create = [name for name, _ in _missing(conn)]
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
        print(f"{old_cards} credit-card and debit accounts are now cards")
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            # Short names only: a full name can carry an account number.
            kalesh = [r[0] for r in conn.execute(
                "SELECT short_name FROM accounts WHERE owner = ? ORDER BY id", (KALESH,)
            )]
        finally:
            conn.close()
        print(f"owned by Kalesh: {', '.join(kalesh) if kalesh else 'none'}")
        print("every other account is the household's; change an owner in the accounts master")
        print(f"created with no figure: {', '.join(to_create) if to_create else 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
