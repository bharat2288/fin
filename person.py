"""People: a person outside the household is a person account on the
Household's sheet. One find-or-create, shared by the review route's "a loan to
a person" and expect back's "+ Someone new" (spec 23, E1), so a name is never
made twice: an existing account of that name, in any case, is reused.

Nothing here commits; the caller's route owns the transaction and the change.
"""

from __future__ import annotations

import sqlite3

import account_kind

PERSON = account_kind.checked_kind("person")


def checked_name(name) -> str:
    """A person's name as typed, trimmed; refused when there is none."""
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        raise ValueError("a person needs a name")
    return name


def find_person(conn: sqlite3.Connection, name: str) -> int | None:
    """The Household person account of this name, case aside, or None."""
    held = conn.execute(
        "SELECT id FROM accounts WHERE UPPER(name) = ? AND type = ? AND owner = ? ORDER BY id LIMIT 1",
        (name.upper(), PERSON, account_kind.HOUSEHOLD),
    ).fetchone()
    return held[0] if held else None


def create_person(conn: sqlite3.Connection, name: str) -> int:
    """A new Household person account named `name`."""
    return conn.execute(
        "INSERT INTO accounts (name, short_name, type, owner) VALUES (?, ?, ?, ?)",
        (name, name, PERSON, account_kind.HOUSEHOLD),
    ).lastrowid


def find_or_create_person(conn: sqlite3.Connection, name) -> tuple[int, bool]:
    """(the person account's id, whether it was made now)."""
    name = checked_name(name)
    held = find_person(conn, name)
    if held is not None:
        return held, False
    return create_person(conn, name), True
