"""A statement's period: the days it covers (fin-redesign 04, spec case 10).

A statement record holds its closing day (statement_date) and the first day it
covers. A statement as printed covers its first day to its closing day, so one
printed 1 Jul to 31 Aug covers July and August. A calendar-month record covers
its whole month, whatever day it is dated.

The first day is stored (period_start) only where it is known on its own: the
opening day a statement's parser read, a calendar-month record's 1st, derived
loan interest's day after the figure before (loan_interest.py). A printed
statement that does not say its first day stores none: its start is read, when
asked (start), from the closing before it, so it never goes stale when the
statement before it is imported later, undone or deleted. A record holding no
start that is not printed (one an undo puts back from a change recorded before
the column) starts on the 1st of its month.

The closing before a statement is the later of the account's previous printed
closing day and its previous statement balance (an anchor from a statement),
as balance_sheet.ROW_DAY counts rows by (PREVIOUS_CLOSINGS, its one home). It
tells the start only when it is in the statement's closing month or the month
before; otherwise (the account's first statement, or one after a gap) it
cannot be told, and the statement starts on the 1st of its closing month.

Coverage and missing months read periods (held_months, read by missing.py and
the coverage route): a month is held for an account when one of its statements
closes in it, or a statement's period contains the whole month. So a statement
printed 1 Jul to 31 Aug holds July and August, and one printed 16 Jul to
15 Aug holds August alone: a statement missing from a cycle that does not start
on the 1st is still missing.

The missed-bill rule (bills.py) reads the days themselves: periods gives each
record's first and last day (end: a printed statement's closing day, a
calendar-month record's month end), and covers says whether they hold every
day of a span, so a bill due in a month with no statement waits for it.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

# The days a statement's account closed a statement before it, as SQL over
# {account} and {closing}: its printed statements' closing days and its
# statement balances. Read here and by balance_sheet.ROW_DAY.
PREVIOUS_CLOSINGS = (
    "SELECT p.statement_date AS closed FROM statements p "
    "WHERE p.account_id = {account} AND p.printed = 1 "
    "AND p.statement_date < {closing} "
    "UNION ALL SELECT n.date FROM anchors n "
    "WHERE n.account_id = {account} AND n.source = 'statement' "
    "AND n.date < {closing}"
)


def first_of_month(day: str) -> str:
    """The 1st of the month of `day` (YYYY-MM-DD)."""
    return f"{day[:7]}-01"


def day_after(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def _month_before(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def _next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + 1:04d}-01" if m == 12 else f"{y:04d}-{m + 1:02d}"


def previous_closing(conn: sqlite3.Connection, account_id: int, closing: str) -> str | None:
    """The day the account closed the statement before one closing on
    `closing` (PREVIOUS_CLOSINGS), when it falls in the closing's month or the
    month before; None when there is none so near (the account's first
    statement, or one after a gap)."""
    before = conn.execute(
        f"SELECT MAX(closed) FROM ({PREVIOUS_CLOSINGS.format(account='?', closing='?')})",
        (account_id, closing, account_id, closing),
    ).fetchone()[0]
    if before is not None and before[:7] in (closing[:7], _month_before(closing[:7])):
        return before
    return None


def start(conn: sqlite3.Connection, account_id: int, statement_date: str,
          period_start: str | None, printed) -> str:
    """The first day a statement record covers: the start it holds; for a
    printed statement holding none, the day after the closing before it
    (previous_closing), otherwise the 1st of its closing month; for any other
    record holding none, the 1st of its month."""
    if period_start:
        return period_start
    if printed:
        before = previous_closing(conn, account_id, statement_date)
        if before:
            return day_after(before)
    return first_of_month(statement_date)


def held_months(conn: sqlite3.Connection, account_id: int, statement_date: str,
                period_start: str | None, printed) -> list[str]:
    """The months ("YYYY-MM") a statement record holds, oldest first: the
    month it closes in, and every month its period contains whole."""
    first = start(conn, account_id, statement_date, period_start, printed)
    closes_in = statement_date[:7]
    held = []
    month = first[:7] if first.endswith("-01") else _next_month(first[:7])
    while month < closes_in:
        held.append(month)
        month = _next_month(month)
    # A month record covers its month; a printed statement closes in its own,
    # whether or not its period contains it whole.
    return [*held, closes_in]


def _last_of_month(day: str) -> str:
    y, m = int(day[:4]), int(day[5:7])
    following = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return (following - timedelta(days=1)).isoformat()


def end(statement_date: str, printed) -> str:
    """The last day a statement record covers: a printed statement's closing
    day; a calendar-month record's month end, whatever day it is dated."""
    return statement_date if printed else _last_of_month(statement_date)


def periods(conn: sqlite3.Connection, account_id: int) -> list[tuple[str, str]]:
    """The (first day, last day) each of the account's statement records
    covers (start, end), earliest first."""
    held = conn.execute(
        "SELECT statement_date, period_start, printed FROM statements WHERE account_id = ?",
        (account_id,),
    ).fetchall()
    return sorted(
        (start(conn, account_id, closing, first, printed), end(closing, printed))
        for closing, first, printed in held
    )


def covers(spans: list[tuple[str, str]], first: str, last: str) -> bool:
    """Whether the periods `spans` (as periods gives them) hold every day from
    `first` to `last`, with no day between them outside a statement."""
    need = first
    for since, until in spans:
        if since > need:
            break
        if until >= need:
            need = day_after(until)
            if need > last:
                return True
    return need > last
