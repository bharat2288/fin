"""Missing months and straight-line estimates (fin-surfaces 27; spec 15, S3).

A month is missing for a set of accounts when one of them should have sent a
statement that month and it has not been imported. "Should have sent" is
stat-cards' rule: the account is active, of a statement kind, and the month is
after its first imported statement and before the current month. A statement
holds the month it closes in and every month its period contains whole
(statement_period.held_months), so one covering July and August holds both. The current month is never
missing; it is "so far". Rows that did arrive in a missing month do not make
it present: whether a month is missing depends on statements only.

The caller chooses the account set (for a book on a page: the accounts
carrying that book's rows of the page's subject in the span) and passes today,
so nothing here reads the clock.

An estimate is the straight line between the nearest known months either
side of a gap. It is drawn only, never counted, and never made at either end
of a line.
"""

from __future__ import annotations

import datetime
import sqlite3
from fractions import Fraction

import account_kind
import statement_period


def missing_months(
    conn: sqlite3.Connection,
    account_ids: list[int],
    months: list[str],
    today: datetime.date,
) -> list[str]:
    """The months of `months` ("YYYY-MM", in the caller's order) in which an
    account of `account_ids` should have sent a statement and none is
    imported."""
    ids = list(dict.fromkeys(account_ids))
    if not ids or not months:
        return []
    current = f"{today.year:04d}-{today.month:02d}"
    marks = ", ".join("?" for _ in ids)
    kinds = ", ".join("?" for _ in account_kind.STATEMENT_KINDS)
    held = conn.execute(
        f"""
        SELECT s.account_id, s.statement_date, s.period_start, s.printed
        FROM statements s
        JOIN accounts a ON a.id = s.account_id
        WHERE s.account_id IN ({marks})
          AND (a.status = 'active' OR a.status IS NULL)
          AND a.type IN ({kinds})
        """,
        (*ids, *account_kind.STATEMENT_KINDS),
    ).fetchall()

    imported: dict[int, set[str]] = {}
    for account_id, statement_date, period_start, printed in held:
        imported.setdefault(account_id, set()).update(
            statement_period.held_months(conn, account_id, statement_date, period_start, printed)
        )

    def owed(month: str) -> bool:
        return any(
            min(seen) < month < current and month not in seen
            for seen in imported.values()
        )

    return [month for month in months if owed(month)]


def estimate(points: list[int | None]) -> list[int | None]:
    """The line `points` (minor units, None where there is no value) with each
    interior gap filled on the straight line between the known values either
    side, rounded half-even to a minor unit. A gap at either end stays None.
    The input is not changed."""
    line = list(points)
    known = [i for i, value in enumerate(line) if value is not None]
    for left, right in zip(known, known[1:]):
        span = right - left
        low, high = line[left], line[right]
        for i in range(left + 1, right):
            line[i] = round(low + Fraction(high - low) * (i - left) / span)
    return line
