"""Shared seeding helpers for the trend-pages build (fin-surfaces 15, ticket 24).

Every lane imports this file and none edits it. A lane that needs more adds a
helper in its own test file. Each helper takes the open `conn` fixture and
commits, so a test reads the result back through a function or a route.

Amounts are whole minor units (cents, paise), the same unit the database
stores. A refund is a negative row. Months are 'YYYY-MM'; days 'YYYY-MM-DD'.

Book names are free text here. The trend spec's mockup calls the household's
two companies Larkspur and Tessel; fin's own books are Household, Moom and
Kalesh (book_type.py). A test that goes through code which validates a book
uses the real names.

Nothing here opens a database: tests get `conn` from tests/conftest.py, whose
guard fails any test that touches the real file.
"""

from __future__ import annotations

import calendar
import sqlite3

import book_type
import db


def month_end(month: str) -> str:
    """The calendar last day of 'YYYY-MM' (not balance_sheet.month_end, which
    refuses anything before 1 Jan 2026)."""
    year, mon = int(month[:4]), int(month[5:7])
    return f"{month}-{calendar.monthrange(year, mon)[1]:02d}"


def _last_id(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def type_id(conn: sqlite3.Connection, name: str) -> int:
    """A spending type's id from its display name ('Dining', 'Fitness > Golf')."""
    return book_type.spending_type_ids(conn)[name]


def account(
    conn: sqlite3.Connection,
    name: str,
    *,
    type: str = "card",
    currency: str = "SGD",
    owner: str = book_type.DEFAULT_BOOK,
    status: str = "active",
    last_four: str | None = None,
) -> int:
    """An account. `type` is its kind (card, bank, ...; account_kind.py)."""
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four, currency, status, owner)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, name.replace(" ", "-"), type, last_four, currency, status, owner),
    )
    conn.commit()
    return _last_id(conn)


def statement(conn: sqlite3.Connection, account_id: int, month: str) -> int:
    """A statement for `month` ('YYYY-MM'), dated its calendar last day: a
    statement belongs to the month of its statement_date. Any month."""
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, ?)",
        (account_id, month_end(month), f"sample-{month}.csv"),
    )
    conn.commit()
    return _last_id(conn)


def merchant(
    conn: sqlite3.Connection,
    name: str,
    book: str | None = None,
    type_name: str | None = None,
    *,
    hidden: bool = False,
) -> int:
    """A merchant (a `services` row) with its default book and type;
    `hidden` keeps it out of expense views."""
    conn.execute(
        "INSERT INTO services (name, book, type_id, exclude_from_expense_views)"
        " VALUES (?, ?, ?, ?)",
        (name, book, type_id(conn, type_name) if type_name else None, int(hidden)),
    )
    conn.commit()
    return _last_id(conn)


def rule(conn: sqlite3.Connection, pattern: str, service_id: int) -> int:
    """A confirmed 'contains' rule sending a description pattern to a merchant."""
    conn.execute(
        "INSERT INTO merchant_rules (pattern, service_id, match_type, confidence)"
        " VALUES (?, ?, 'contains', 'confirmed')",
        (pattern, service_id),
    )
    conn.commit()
    db.invalidate_rules_cache()
    return _last_id(conn)


def row(
    conn: sqlite3.Connection,
    statement_id: int,
    description: str,
    amount_minor: int,
    *,
    service_id: int | None = None,
    book: str | None = None,
    type_name: str | None = None,
    flow: str = "expense",
    day: str = "2026-04-10",
    cat_source: str = "auto",
) -> int:
    """A row of `amount_minor` (positive = money out, negative = a refund) in
    its account's currency. A book of None is stored NULL and reads as
    Household."""
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor,"
        " book, type_id, service_id, cat_source, flow_type)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (statement_id, day, description, amount_minor, book,
         type_id(conn, type_name) if type_name else None, service_id, cat_source, flow),
    )
    conn.commit()
    return _last_id(conn)


def seed_rate(conn: sqlite3.Connection, pair: str, day: str, value: str) -> None:
    """Save a rate (decimal text, e.g. 'INR/SGD', '2026-09-30', '0.0155'),
    as the operator would enter one. Nothing is ever fetched."""
    conn.execute(
        "INSERT INTO rates (pair, date, rate, source, fetched_at) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (pair, date) DO UPDATE SET rate = excluded.rate",
        (pair, day, value, "entered by the operator", "2026-10-19 00:00:00"),
    )
    conn.commit()


def anchor(
    conn: sqlite3.Connection,
    account_id: int,
    day: str,
    amount_minor: int,
    *,
    source: str = "statement",
) -> None:
    """A balance anchor: the account's balance on `day`, cash positive,
    anything owed negative."""
    conn.execute(
        "INSERT INTO anchors (account_id, date, amount, source) VALUES (?, ?, ?, ?)",
        (account_id, day, amount_minor, source),
    )
    conn.commit()
