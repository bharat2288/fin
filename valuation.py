"""One path from a row's amount to S$ (fin RUL-022: worked out when read).

Every row keeps its own amount in its account's currency. A reader that adds
rows up in S$ values each foreign row at the rate of its own day: the latest
rate saved on or before that day in its month, or failing one the first saved
later in its month (a book holding month-end rates only values a month's rows
at its month-end rate, as it did before). A rate from another month is no rate
for it, as balance_sheet's ruling 4 says. A row in S$ is its own amount. A
foreign row with no such rate has no S$ value: it is left out of the sum,
never guessed, and the reader may say how many were (`unvalued_count`).

The value is worked out in SQL through `sgd_value(amount_minor, currency,
day)`, a function `attach` gives a connection, over a snapshot of the saved
rates taken when it is attached: one rule for the dashboard, the trend pages,
Books and chat. Decimal arithmetic, rounded half-even to the cent
(money.convert_minor); no float touches a rate.

`fetch_missing` saves the European Central Bank rate for every day a foreign
row is on that has none of its own, in one request per currency
(rates.save_range). Nothing here fetches by itself: a reader only reads.
"""

from __future__ import annotations

import bisect
import sqlite3
from datetime import date
from decimal import Decimal

import money
import rates
import trend_series


def _snapshot(conn: sqlite3.Connection) -> dict:
    """{currency: ([days], [Decimal rates])}, days ascending."""
    out: dict = {}
    for row in conn.execute("SELECT pair, date, rate FROM rates ORDER BY pair, date"):
        currency, _, quote = row[0].partition("/")
        if quote != rates.QUOTE:
            continue
        days, values = out.setdefault(currency, ([], []))
        days.append(row[1])
        values.append(Decimal(row[2]))
    return out


def _value(held: dict, amount_minor, currency, day):
    if amount_minor is None:
        return None
    currency = currency or rates.QUOTE
    if currency == rates.QUOTE:
        return amount_minor
    if currency not in money.MINOR_UNIT_DIGITS or not day:
        return None
    days, values = held.get(currency, ((), ()))
    at = bisect.bisect_right(days, day[:10]) - 1
    if at < 0 or days[at] < f"{day[:7]}-01":
        # None on or before the day in its month: the first later in it.
        at += 1
        if at >= len(days) or days[at][:7] != day[:7]:
            return None
    return money.convert_minor(int(amount_minor), values[at], currency, rates.QUOTE)


def attach(conn: sqlite3.Connection) -> None:
    """Give the connection `sgd_value(amount_minor, currency, day)`, over the
    rates saved when it is attached: every connection fin opens is attached
    (db.get_connection). Attach again after saving a rate on the same
    connection. A book with no rates table yet reads it at the first call."""
    held: dict = {}
    try:
        held["rates"] = _snapshot(conn)
    except sqlite3.OperationalError:
        pass

    def sgd_value(amount, currency, day):
        if "rates" not in held:
            held["rates"] = _snapshot(conn)
        return _value(held["rates"], amount, currency, day)

    conn.create_function("sgd_value", 3, sgd_value, deterministic=True)


def value_minor(conn: sqlite3.Connection, amount_minor: int, currency: str, day: str) -> int | None:
    """One amount on one day in S$ by the same rule, or None without a rate."""
    return _value(_snapshot(conn), amount_minor, currency, day)


def unvalued_count(conn: sqlite3.Connection, start: str, end: str, where: str = "", params=()) -> int:
    """How many spending lines (expense or refund) from `start` to `end`
    (days, inclusive) no saved rate values: left out of every S$ figure.
    `where` narrows them by the line (t) or its account (s). The connection
    must be attached."""
    row = conn.execute(
        f"SELECT COUNT(*) FROM {trend_series.counted_from()} JOIN accounts ua ON ua.id = t.account_id"
        " LEFT JOIN services svc ON svc.id = t.service_id"
        " WHERE t.flow_type IN ('expense', 'refund') AND t.date >= ? AND t.date <= ?"
        " AND sgd_value(t.amount_minor, ua.currency, t.date) IS NULL" + where,
        (start, end, *params),
    ).fetchone()
    return row[0]


# Foreign rows a spending or review figure could count: a row on an account
# kept in a currency other than S$.
_FOREIGN_DAYS = """
    SELECT DISTINCT a.currency, t.date FROM transactions t
    JOIN statements s ON s.id = t.statement_id
    JOIN accounts a ON a.id = s.account_id
    WHERE COALESCE(a.currency, 'SGD') <> 'SGD' AND t.date IS NOT NULL
"""


def missing_days(conn: sqlite3.Connection) -> dict:
    """{currency: [days]}: each day a foreign row is on that no saved rate
    values (none on or before it in its month), oldest first."""
    held = _snapshot(conn)
    out: dict = {}
    for currency, day in conn.execute(_FOREIGN_DAYS + " ORDER BY a.currency, t.date"):
        if currency in money.MINOR_UNIT_DIGITS and _value(held, 0, currency, day) is None:
            out.setdefault(currency, []).append(day)
    return out


def fetch_missing(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """Save the ECB rate for every day in missing_days, one request per
    currency for the span of its days. Returns {saved, currencies, failed}:
    how many rates were saved, and the currencies whose fetch failed (nothing
    of theirs is saved). Does not commit."""
    saved, failed = 0, []
    for currency, days in missing_days(conn).items():
        try:
            saved += rates.save_range(conn, currency, days, today=today)
        except rates.FetchFailed:
            failed.append(currency)
    return {"saved": saved, "currencies": sorted(missing_days(conn)), "failed": failed}
