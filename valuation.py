"""One path from a row's amount to S$ (fin RUL-022: worked out when read).

Every row keeps its own amount in its account's currency. A reader that adds
rows up in S$ values each foreign row at the rate of its own day: the latest
rate saved on or before that day in its month, or failing one the first saved
later in its month (a book holding month-end rates only values a month's rows
at its month-end rate, as it did before). A rate from another month is no rate
for it, as balance_sheet's ruling 4 says. A row in S$ is its own amount. A
foreign row with no such rate has no S$ value: it is left out of the sum,
never guessed, and the reader may say how many were (`unvalued_count`).
A row a confirmed cross-currency pair holds is valued at the pair's real
rate instead (fx_pair.py; RUL-022): the SQL function takes the row's id as a
fourth argument, and a reader that passes it gets that rate. A row whose
wording prints the bank's own rate ("SGD@ 0.7400000" on a DBS US$ row,
"SGD500.0@74.52" on an RFX remittance) is valued at that rate the same way,
when it is within WORDING_BAND of the saved rate of its day: a printed rate
read the wrong way up, or off a balance line, is no rate for the row, and
with no saved rate to check it against the row is left unvalued. A pair's
rate comes before a printed one. Nothing of it is stored.

The value is worked out in SQL through `sgd_value(amount_minor, currency,
day)`, a function `attach` gives a connection, over a snapshot of the saved
rates taken when it is attached: one rule for the dashboard, the trend pages,
Books and chat. Decimal arithmetic, rounded half-even to the cent
(money.convert_minor); no float touches a rate.

`fetch_missing` saves the European Central Bank rate for every day a foreign
row is on that has none of its own, in one request per currency
(rates.save_range): an import asks it for the accounts it brought rows to,
and Home's Fetch the rates for every account. Nothing here fetches by
itself: a reader only reads.
"""

from __future__ import annotations

import bisect
import re
import sqlite3
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

import money
import rates
import trend_series


def _snapshot(conn: sqlite3.Connection) -> dict:
    """{currency: ([days], [Decimal rates])}, days ascending; under REAL,
    {row id: (currency, Decimal rate)} for the rows a confirmed pair holds;
    and under BANK, the same for the rows whose wording prints the bank's
    rate (bank_rates)."""
    out: dict = {REAL: _real_rates(conn)}
    for row in conn.execute("SELECT pair, date, rate FROM rates ORDER BY pair, date"):
        currency, _, quote = row[0].partition("/")
        if quote != rates.QUOTE:
            continue
        days, values = out.setdefault(currency, ([], []))
        days.append(row[1])
        values.append(Decimal(row[2]))
    out[BANK] = _bank_rates(conn, out)
    return out


# The keys a snapshot holds the real rates and the printed ones under: never
# a currency code.
REAL = "real"
BANK = "bank"

# A bank's own rate printed in a wording, as units of the row's currency for
# one S$: "SGD@ 0.7400000" (DBS, US$ moved to S$), "SGD500.0@74.52" (an RFX
# remittance into rupees).
_PRINTED_RATE = re.compile(r"SGD\s*(?:[0-9][0-9,]*(?:\.[0-9]+)?)?\s*@\s*([0-9]+(?:\.[0-9]+)?)")
# How far a printed rate may be from the saved rate of its day, as a share of
# it, and still be the row's. A bank's margin is a few per cent at most; a
# rate read the wrong way up is off by a third or more.
WORDING_BAND = Decimal("0.05")
# The digits a rate worked out from a printed one keeps (fx_pair's).
RATE_PLACES = Decimal(1).scaleb(-20)


def wording_rate(description: str | None) -> Decimal | None:
    """The bank's rate a wording prints, as units of the row's currency for
    one S$, or None."""
    found = _PRINTED_RATE.search((description or "").upper())
    if found is None:
        return None
    rate = Decimal(found.group(1))
    return rate if rate > 0 else None


def per_unit(per_sgd: Decimal) -> Decimal:
    """S$ for one unit, from units for one S$, at the places a rate is kept."""
    with localcontext() as exact:
        exact.prec = 60
        return (Decimal(1) / per_sgd).quantize(RATE_PLACES, rounding=ROUND_HALF_EVEN)


# Rows in another currency whose wording may print a rate.
_PRINTED_ROWS = """
    SELECT t.id, a.currency, t.date, t.description FROM transactions t
    JOIN statements s ON s.id = t.statement_id
    JOIN accounts a ON a.id = s.account_id
    WHERE COALESCE(a.currency, 'SGD') <> 'SGD' AND t.description LIKE '%@%'
"""


def _bank_rates(conn: sqlite3.Connection, held: dict) -> dict:
    """{row id: (currency, Decimal rate)}: the rows in another currency whose
    wording prints a rate within WORDING_BAND of the saved rate of their day,
    each at its printed rate as S$ for one unit. A book with no rows yet
    holds none."""
    try:
        found = conn.execute(_PRINTED_ROWS).fetchall()
    except sqlite3.OperationalError:
        return {}
    out = {}
    for row_id, currency, day, description in found:
        per_sgd = wording_rate(description)
        if per_sgd is None or currency not in money.MINOR_UNIT_DIGITS or not day:
            continue
        saved = day_rate(held, currency, day)
        if saved is None:
            continue  # nothing to check it against: never guessed
        rate = per_unit(per_sgd)
        if abs(rate - saved) / saved <= WORDING_BAND:
            out[row_id] = (currency, rate)
    return out


def bank_rates(conn: sqlite3.Connection) -> dict:
    """{row id: (currency, Decimal rate)}: each row valued at the bank's rate
    its wording prints, as S$ for one unit, over the rates saved now."""
    return _snapshot(conn)[BANK]


def _real_rates(conn: sqlite3.Connection) -> dict:
    """{row id: (currency, Decimal rate)}: the in rows of confirmed pairs. A
    book with no fx_pair table yet holds none."""
    try:
        found = conn.execute("SELECT in_row_id, currency, rate FROM fx_pair WHERE state = 'confirmed'")
    except sqlite3.OperationalError:
        return {}
    return {row[0]: (row[1], Decimal(row[2])) for row in found}


def day_rate(held: dict, currency: str, day: str) -> Decimal | None:
    """The saved rate a foreign row of `day` is valued at: the latest on or
    before it in its month, else the first later in its month, else None."""
    days, values = held.get(currency, ((), ()))
    at = bisect.bisect_right(days, day[:10]) - 1
    if at < 0 or days[at] < f"{day[:7]}-01":
        # None on or before the day in its month: the first later in it.
        at += 1
        if at >= len(days) or days[at][:7] != day[:7]:
            return None
    return values[at]


def _value(held: dict, amount_minor, currency, day, row_id=None):
    if amount_minor is None:
        return None
    currency = currency or rates.QUOTE
    if currency == rates.QUOTE:
        return amount_minor
    if currency not in money.MINOR_UNIT_DIGITS:
        return None
    real = None
    if row_id is not None:
        real = held.get(REAL, {}).get(row_id) or held.get(BANK, {}).get(row_id)
    if real is not None and real[0] == currency:
        return money.convert_minor(int(amount_minor), real[1], currency, rates.QUOTE)
    if not day:
        return None
    rate = day_rate(held, currency, day)
    if rate is None:
        return None
    return money.convert_minor(int(amount_minor), rate, currency, rates.QUOTE)


def attach(conn: sqlite3.Connection) -> None:
    """Give the connection `sgd_value(amount_minor, currency, day[, row_id])`,
    over the rates saved when it is attached (and the real rates of confirmed
    pairs, read by the row's id when it is given): every connection fin opens is attached
    (db.get_connection). Attach again after saving a rate on the same
    connection. A book with no rates table yet reads it at the first call."""
    held: dict = {}
    try:
        held["rates"] = _snapshot(conn)
    except sqlite3.OperationalError:
        pass

    def sgd_value(amount, currency, day, row_id=None):
        if "rates" not in held:
            held["rates"] = _snapshot(conn)
        return _value(held["rates"], amount, currency, day, row_id)

    conn.create_function("sgd_value", 3, sgd_value, deterministic=True)
    conn.create_function("sgd_value", 4, sgd_value, deterministic=True)


def valuer(conn: sqlite3.Connection):
    """`value(amount_minor, currency, day, row_id=None)` by the same rule,
    over the rates saved now: for a reader in Python that values many rows."""
    held = _snapshot(conn)
    return lambda amount_minor, currency, day, row_id=None: _value(held, amount_minor, currency, day, row_id)


def value_minor(conn: sqlite3.Connection, amount_minor: int, currency: str, day: str) -> int | None:
    """One amount on one day in S$ by the same rule, or None without a rate."""
    return valuer(conn)(amount_minor, currency, day)


def unvalued_count(conn: sqlite3.Connection, start: str, end: str, where: str = "", params=(),
                   owner: str | None = None) -> int:
    """How many spending rows (expense or refund) from `start` to `end`
    (days, inclusive) no saved rate values: left out of every S$ figure.
    `where` narrows them by the line (t), its account (s, a) or its merchant
    (svc); `owner`, to rows on accounts that owner holds, as the Home tiles
    count. The connection must be attached."""
    row = conn.execute(
        f"SELECT COUNT(DISTINCT t.id) FROM {trend_series.counted_from()} JOIN accounts a ON a.id = t.account_id"
        " LEFT JOIN services svc ON svc.id = t.service_id"
        " WHERE t.flow_type IN ('expense', 'refund') AND t.date >= ? AND t.date <= ?"
        " AND sgd_value(t.amount_minor, a.currency, t.date, t.id) IS NULL"
        + (" AND a.owner = ?" if owner else "") + where,
        (start, end, *([owner] if owner else []), *params),
    ).fetchone()
    return row[0]


# Foreign rows a spending or review figure could count: a row on an account
# kept in a currency other than S$.
_FOREIGN_DAYS = """
    SELECT DISTINCT a.currency, t.date FROM transactions t
    JOIN statements s ON s.id = t.statement_id
    JOIN accounts a ON a.id = s.account_id
    WHERE COALESCE(a.currency, 'SGD') <> 'SGD'
      AND t.date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
"""


def missing_days(conn: sqlite3.Connection, accounts=None) -> dict:
    """{currency: [days]}: each day a foreign row is on that no saved rate
    values (none on or before it in its month), oldest first; with
    `accounts` (ids), only the rows on those."""
    held = _snapshot(conn)
    out: dict = {}
    where, params = "", ()
    if accounts is not None:
        params = tuple(sorted(accounts))
        where = f" AND a.id IN ({', '.join('?' * len(params))})" if params else " AND 0"
    for currency, day in conn.execute(_FOREIGN_DAYS + where + " ORDER BY a.currency, t.date", params):
        day = day[:10]
        if currency in money.MINOR_UNIT_DIGITS and _value(held, 0, currency, day) is None \
                and day not in out.get(currency, ()):
            out.setdefault(currency, []).append(day)
    return out


def fetch_missing(conn: sqlite3.Connection, today: date | None = None, accounts=None) -> dict:
    """Save the ECB rate for every day in missing_days (with `accounts`, of
    the rows on those), one request per currency for the span of its days.
    Returns {saved, currencies, failed}: how many rates were saved, the
    currencies still short of a rate, and those whose fetch failed (nothing
    of theirs is saved; the others' rates are). Does not commit."""
    saved, failed = 0, []
    for currency, days in missing_days(conn, accounts).items():
        try:
            saved += rates.save_range(conn, currency, days, today=today)
        except rates.FetchFailed:
            failed.append(currency)
    return {"saved": saved, "currencies": sorted(missing_days(conn, accounts)), "failed": failed}
