"""Saved rates: what one unit of a currency was worth in SGD on a date.

A saved rate is (pair, date, rate, source, when fetched), one per pair and
date. The pair is written "INR/SGD": how many SGD one rupee is worth. The rate
is kept as decimal text and read as a Decimal; no float touches it.

fin fetches the European Central Bank's daily reference rate for a date from
a public source, saves it, and from then on reads only its saved copy: a past
month's figure does not change by itself and needs no network. The operator
may overwrite a rate. A date with no rate of its own uses the latest earlier
saved rate, and the caller is told which; with none, nothing is guessed.

`_http_get` is the only thing here, or anywhere in fin's ledger, that reaches
outside for a rate. It sends the date and the two currency codes and nothing
of the operator's.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import money

# The currency rates are quoted in: the one the totals are in.
QUOTE = "SGD"

# The Frankfurter API serves the European Central Bank's daily reference
# rates. A day with no rate of its own (a weekend, a holiday) is answered
# with the prior business day's, and the answer says which day that is.
SOURCE_URL = "https://api.frankfurter.dev/v1/{on}?base={base}&symbols={quote}"
FETCHED_SOURCE = "European Central Bank reference rate of {effective}, from api.frankfurter.dev"
OPERATOR_SOURCE = "entered by the operator"
TIMEOUT_SECONDS = 10
# The source answers 403 to Python's default agent ("Python-urllib/3.x"), so
# the request names fin.
USER_AGENT = "fin/1 (personal finance; +https://fin.bhasuri.ai)"

_COLUMNS = ("pair", "date", "rate", "source", "fetched_at")


class InvalidRate(ValueError):
    """The currency, the date or the rate is not one fin can save."""


class FetchFailed(Exception):
    """The source could not be reached or did not answer with a rate. Nothing
    was saved. The message is fixed: it never carries what the source said."""


def pair_for(currency) -> str:
    """The pair a currency is valued by, "INR/SGD". Only a currency with a
    declared minor unit, other than the one the totals are in, has one."""
    if (
        not isinstance(currency, str)
        or currency not in money.MINOR_UNIT_DIGITS
        or currency == QUOTE
    ):
        raise InvalidRate(f"no rate is kept for {currency!r}")
    return f"{currency}/{QUOTE}"


def checked_date(value) -> str:
    """A day as a rate is saved under: YYYY-MM-DD, and a real day."""
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise InvalidRate("date must be a real day written as YYYY-MM-DD") from None
    return value


def checked_rate(value) -> str:
    """A rate as it is saved: plain decimal text, above zero. A float is
    refused, and so is anything not written as digits and a point."""
    if not isinstance(value, str):
        raise InvalidRate("rate must be a decimal written as text, such as 0.0153")
    text = value.strip()
    try:
        rate = Decimal(text)
    except InvalidOperation:
        raise InvalidRate("rate must be a decimal written as text, such as 0.0153") from None
    if not rate.is_finite() or rate <= 0 or not text.replace(".", "", 1).isdigit():
        raise InvalidRate("rate must be a plain decimal above zero, such as 0.0153")
    return text


# --- the one thing that reaches outside -----------------------------------------

def _http_get(url: str) -> str:
    """The body of a public page. Tests replace this."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8")


def fetch_reference_rate(base: str, quote: str, on: str) -> tuple[str, str]:
    """The reference rate for a day from the public source: (rate as the
    decimal text the source printed, the day the rate is of). The day differs
    from the one asked for when that was a weekend or a holiday."""
    body = _http_get(SOURCE_URL.format(on=on, base=base, quote=quote))
    # Numbers are kept as the text they were written in, never read as floats.
    answer = json.loads(body, parse_float=str, parse_int=str)
    return answer["rates"][quote], answer["date"]


RANGE_URL = "https://api.frankfurter.dev/v1/{start}..{end}?base={base}&symbols={quote}"
# How far before the first day a range asks from: a first day after a long
# holiday still finds the business day before it.
RANGE_LEAD_DAYS = 7
RANGE_SPAN_DAYS = 90            # days asked for in one request at most


def fetch_reference_range(base: str, quote: str, start: str, end: str) -> dict:
    """The reference rates of every business day from `start` to `end` from
    the public source: {day: rate as the decimal text the source printed}."""
    body = _http_get(RANGE_URL.format(start=start, end=end, base=base, quote=quote))
    answer = json.loads(body, parse_float=str, parse_int=str)
    return {day: by_quote[quote] for day, by_quote in answer["rates"].items()}


# --- the saved copy -------------------------------------------------------------

def _row(row) -> dict | None:
    return None if row is None else dict(zip(_COLUMNS, row))


def saved(conn: sqlite3.Connection, pair: str, on: str) -> dict | None:
    """The rate saved for exactly this pair and day, or None."""
    return _row(conn.execute(
        "SELECT pair, date, rate, source, fetched_at FROM rates WHERE pair = ? AND date = ?",
        (pair, on),
    ).fetchone())


def listed(conn: sqlite3.Connection, pair: str | None = None) -> list[dict]:
    """Every saved rate, newest first; with a pair, that pair's."""
    if pair is None:
        rows = conn.execute(
            "SELECT pair, date, rate, source, fetched_at FROM rates ORDER BY date DESC, pair"
        )
    else:
        rows = conn.execute(
            "SELECT pair, date, rate, source, fetched_at FROM rates WHERE pair = ? "
            "ORDER BY date DESC",
            (pair,),
        )
    return [_row(r) for r in rows]


def on_or_before(conn: sqlite3.Connection, pair: str, on: str) -> dict | None:
    """The rate a figure on a day uses: that day's, or failing it the latest
    saved for an earlier day. `exact` says which; `value` is the Decimal.
    None when no rate is saved on or before the day."""
    found = _row(conn.execute(
        "SELECT pair, date, rate, source, fetched_at FROM rates "
        "WHERE pair = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (pair, on),
    ).fetchone())
    if found is None:
        return None
    found.update(asked=on, exact=found["date"] == on, value=Decimal(found["rate"]))
    return found


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def overwrite(conn: sqlite3.Connection, currency, on, rate) -> dict:
    """Save the rate the operator entered for a day, in place of any held.
    Does not commit."""
    pair, on, rate = pair_for(currency), checked_date(on), checked_rate(rate)
    conn.execute(
        "INSERT INTO rates (pair, date, rate, source, fetched_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (pair, date) DO UPDATE SET "
        "rate = excluded.rate, source = excluded.source, fetched_at = excluded.fetched_at",
        (pair, on, rate, OPERATOR_SOURCE, _now()),
    )
    return saved(conn, pair, on)


def ensure(conn: sqlite3.Connection, currency, on, today: date | None = None) -> tuple[dict, bool]:
    """The saved rate for a day, fetched and saved first if none is held.
    Returns (the rate, whether it was fetched now). A rate already saved is
    returned as it is and the source is not asked. Does not commit.

    Raises InvalidRate for a currency or a day no rate can be kept for (a day
    that has not come has no rate yet), and FetchFailed when the source did
    not give a usable rate; nothing is saved then.
    """
    pair, on = pair_for(currency), checked_date(on)
    held = saved(conn, pair, on)
    if held is not None:
        return held, False
    if date.fromisoformat(on) > (today or date.today()):
        raise InvalidRate("that day has not come; there is no rate for it yet")

    answered = None
    try:
        rate, effective = fetch_reference_rate(currency, QUOTE, on)
        answered = (checked_rate(rate), checked_date(effective))
        if answered[1] > on:
            answered = None
    except Exception:
        answered = None
    # Raised outside the except block: what the source or the network said is
    # not carried on the refusal.
    if answered is None:
        raise FetchFailed("The rate could not be fetched; nothing was saved")

    conn.execute(
        "INSERT INTO rates (pair, date, rate, source, fetched_at) VALUES (?, ?, ?, ?, ?)",
        (pair, on, answered[0], FETCHED_SOURCE.format(effective=answered[1]), _now()),
    )
    return saved(conn, pair, on), True


def _days_between(earlier: str, later: str) -> int:
    return date.fromisoformat(later).toordinal() - date.fromisoformat(earlier).toordinal()


def save_range(conn: sqlite3.Connection, currency, days: list[str], today: date | None = None) -> int:
    """Save a rate for each of `days` that has none saved, from one request
    per span of at most RANGE_SPAN_DAYS: each day's own reference rate, or
    failing it (a weekend, a holiday) the latest business day's before it,
    as `ensure` would get, never one more than RANGE_LEAD_DAYS older. A day
    that has not come is skipped. Returns how many were saved. Raises
    FetchFailed when the source gives no usable answer; nothing of this
    currency is saved then. Does not commit."""
    pair = pair_for(currency)
    last_day = (today or date.today()).isoformat()
    wanted = sorted({checked_date(d) for d in days if checked_date(d) <= last_day
                     and saved(conn, pair, d) is None})
    if not wanted:
        return 0
    spans: list[list[str]] = []
    for day in wanted:
        if spans and _days_between(spans[-1][0], day) <= RANGE_SPAN_DAYS:
            spans[-1].append(day)
        else:
            spans.append([day])
    found: dict = {}
    for span in spans:
        lead = date.fromordinal(date.fromisoformat(span[0]).toordinal() - RANGE_LEAD_DAYS).isoformat()
        try:
            got = fetch_reference_range(currency, QUOTE, lead, span[-1])
            found.update((checked_date(d), checked_rate(r)) for d, r in got.items())
        except Exception:
            found = {}
            break
    by_day = sorted(found.items())
    # Raised outside the except block: what the source said is not carried.
    if not by_day:
        raise FetchFailed("The rates could not be fetched; nothing was saved")

    count, at, fetched = 0, -1, _now()
    for day in wanted:
        while at + 1 < len(by_day) and by_day[at + 1][0] <= day:
            at += 1
        if at < 0 or _days_between(by_day[at][0], day) > RANGE_LEAD_DAYS:
            continue
        effective, rate = by_day[at]
        conn.execute(
            "INSERT INTO rates (pair, date, rate, source, fetched_at) VALUES (?, ?, ?, ?, ?)",
            (pair, day, rate, FETCHED_SOURCE.format(effective=effective), fetched),
        )
        count += 1
    return count
