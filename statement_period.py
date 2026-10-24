"""A statement's period: the days it covers (fin-redesign 04, spec case 10).

A statement record holds its closing day (statement_date) and, beside it, the
first day it covers (period_start). A statement as printed covers its period
start to its closing day, so one printed 1 Jul to 31 Aug covers July and
August. A calendar-month record covers its whole month, whatever day it is
dated. A record with no period start (one an undo puts back from a change
recorded before the column) starts on the 1st of its month.

Coverage and missing months read periods: a month is held for an account when
a period of one of its statements reaches into it.

Where a printed statement does not say its first day, it starts the day after
the account's previous printed closing day when that is in the statement's
closing month or the month before; otherwise (its first statement, or one after
a gap) it cannot be told, and it starts on the 1st of its closing month.
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import date, timedelta


def first_of_month(day: str) -> str:
    """The 1st of the month of `day` (YYYY-MM-DD)."""
    return f"{day[:7]}-01"


def day_after(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def bounds(statement_date: str, period_start: str | None, printed) -> tuple[str, str]:
    """The first and last day a statement record covers."""
    start = period_start or first_of_month(statement_date)
    if printed:
        return start, statement_date
    y, m = int(statement_date[:4]), int(statement_date[5:7])
    return start, f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"


def months(statement_date: str, period_start: str | None, printed) -> list[str]:
    """The months ("YYYY-MM") a statement record's period reaches into,
    oldest first."""
    start, end = bounds(statement_date, period_start, printed)
    if start > end:
        start = first_of_month(end)
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= end[:7]:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _month_before(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def previous_closing(conn: sqlite3.Connection, account_id: int, closing: str) -> str | None:
    """The account's printed closing day before `closing`, when it falls in
    the closing's month or the month before; None when there is none so near
    (the account's first printed statement, or one after a gap)."""
    before = conn.execute(
        "SELECT MAX(statement_date) FROM statements WHERE account_id = ? AND printed = 1 AND statement_date < ?",
        (account_id, closing),
    ).fetchone()[0]
    if before is not None and before[:7] in (closing[:7], _month_before(closing[:7])):
        return before
    return None


def printed_start(conn: sqlite3.Connection, account_id: int, closing: str) -> str:
    """The first day of a printed statement closing on `closing` that does
    not say its own: the day after the previous closing (previous_closing),
    otherwise the 1st of the closing's month (see the module's doc)."""
    before = previous_closing(conn, account_id, closing)
    return day_after(before) if before else first_of_month(closing)
