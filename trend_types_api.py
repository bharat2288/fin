"""R2, GET /api/trend/types: Types over time (fin-surfaces 40; spec 15, Server
routes R2; cases 11, 39, 40, 42, 43, 50, 51, 55, 57, 63).

One book's spending as one line per type with rows in the span: a point per
month with its net in S$, rupees valued at the month-end rate merchant by
merchant through trend_series.valued, as R1's type page values them (a rate
dated before the month is no rate), and anything
that cannot be valued named in `left_out`, never guessed. A line has a point
for every month, also past 24 months: quarters are for bars.

The top four types by net take the four colour slots. A slot belongs to the
type, not the rank: slots are dealt on the 12M ranking ending at the same
month, a type in the span's top four keeps its 12M slot, and a type entering
the top four takes the lowest free slot (case 57). The answer is the same
whichever span was opened first, so no state is kept.

A month is missing for the book when an account carrying the book's expense
or refund rows in the span should have sent a statement and has not (S3).
A missing month's point has no net; its estimate is the straight line
between the known months either side, never at either end. Rows that did
arrive in a missing month still count in the type's net (and so in the
ranking); an estimate counts in nothing.

One book per answer: books never share a line (case 43). Built on scope.py
and the trend modules, never app.py. Opening it never fetches.
"""

from __future__ import annotations

import datetime
import sqlite3

from flask import jsonify, request

import account_kind
import balance_sheet
import book_type
import db
import missing
import money
import trend_series
import trend_span
from scope import book_expr

CURRENCY = balance_sheet.TOTAL_CURRENCY
SLOTS = 4
REFERENCE_SPAN = "12M"
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December")


class NoSuchBook(LookupError):
    """A book fin does not keep: the route answers 404 with this reason."""


def _today() -> datetime.date:
    """Today, as the route reads it. Tests pin it."""
    return datetime.date.today()


def _months(plan: dict) -> list[str]:
    """Every month of a resolved span, in order."""
    return [month for bucket in plan["buckets"] for month in bucket["months"]]


def _month_name(month: str) -> str:
    return f"{_MONTH_NAMES[int(month[5:7]) - 1]} {month[:4]}"


def _scope(conn: sqlite3.Connection, book: str, start: str | None,
           end: str) -> tuple[list[int], str | None]:
    """The accounts carrying `book`'s expense or refund rows from `start` to
    `end` (every month up to `end` when `start` is None, as on All), and the
    month of the book's first such row. One query, whatever the span. Which
    accounts a book counts is trend_series's rule, read from there."""
    found = conn.execute(
        f"""
        SELECT s.account_id,
               MIN(strftime('%Y-%m', t.date)) AS first_month,
               SUM(CASE WHEN strftime('%Y-%m', t.date) >= :start THEN 1 ELSE 0 END) AS in_span
        FROM transactions t
        JOIN statements s ON s.id = t.statement_id
        JOIN accounts a ON a.id = s.account_id
        WHERE t.flow_type IN ('expense', 'refund')
          AND strftime('%Y-%m', t.date) <= :end
          AND {book_expr('t')} = :book
          AND {trend_series._ACCOUNTS_COUNTED}
        GROUP BY s.account_id
        """,
        {"start": start or "", "end": end, "book": book,
         "household": book_type.DEFAULT_BOOK, "household_owner": account_kind.HOUSEHOLD},
    ).fetchall()
    accounts = [r["account_id"] for r in found if r["in_span"]]
    first = min((r["first_month"] for r in found), default=None)
    return accounts, first


def _in_sgd(conn: sqlite3.Connection, parts: dict, month: str, left_out: list) -> int:
    """One type's month in S$: the sum of its merchants' cells (merchant ->
    currency -> {spent, back, net}), each valued by trend_series.valued, the
    rule R1's type page values the same cells by, so the two agree to the
    cent. What cannot be valued is appended to `left_out`, not counted."""
    net = 0
    for cells in parts.values():
        for currency, cell in cells.items():
            got = trend_series.valued(conn, cell, currency, month)
            if got["net"] is not None:
                net += got["net"]
            elif currency in money.MINOR_UNIT_DIGITS:
                left_out.append(("no_rate", currency, month, cell["net"],
                                 f"no saved rate for {_month_name(month)}"))
            else:
                left_out.append(("no_rate_kept", currency, month, cell["net"], got["left_out"]))
    return net


def _left_out(items: list, months: list[str]) -> list[dict]:
    """What was not counted in the span, summed per kind, currency and month."""
    span = set(months)
    summed: dict = {}
    for kind, currency, month, amount, reason in items:
        if month not in span:
            continue
        item = summed.setdefault((month, currency, kind), {
            "kind": kind, "currency": currency, "amount_minor": 0,
            "month": month, "reason": reason})
        item["amount_minor"] += amount
    return [summed[key] for key in sorted(summed)]


def _ranked(nets: dict) -> list:
    """Type keys by net, largest first, then by name."""
    return sorted(nets, key=lambda key: (-nets[key], key))


def _slots(span_top: list, reference_top: list) -> dict:
    """Colour slots for the span's top four: a type in the reference top four
    keeps its slot there; each entrant, in rank order, takes the lowest free."""
    slots = {key: reference_top.index(key) + 1 for key in span_top if key in reference_top}
    free = [slot for slot in range(1, SLOTS + 1) if slot not in slots.values()]
    for key in span_top:
        if key not in slots:
            slots[key] = free.pop(0)
    return slots


def types_over_time(conn: sqlite3.Connection, book: str = book_type.DEFAULT_BOOK,
                    span: str | None = None, to: str | None = None, *,
                    today: datetime.date) -> dict:
    """The R2 answer for one book over one span ending at `to` ('YYYY-MM',
    default this month). Raises trend_span.SpanError (400) for a bad span or
    month, and NoSuchBook (404) for a book fin does not keep."""
    if book not in book_type.book_names(conn):
        raise NoSuchBook(f"book {book} is not in fin")
    reference = trend_span.resolve(REFERENCE_SPAN, to, today=today)  # checks `to`
    end = reference["end"]
    known_start = None if span == "All" else trend_span.resolve(span, to, today=today)["start"]
    accounts, first = _scope(conn, book, known_start, end)
    plan = trend_span.resolve(span, to, today=today, first_month=first)
    months, reference_months = _months(plan), _months(reference)

    # One grouped read over the span and the 12M the slots are dealt on.
    window = sorted(set(months) | set(reference_months))
    series = trend_series.series(conn, ("book", book), window[0], end, "type",
                                 split_by="merchant")["series"]
    not_counted: list = []
    lines: dict = {}  # type key -> month -> net in S$, for months with rows
    for month in window:
        for key, parts in series[month].get(book, {}).items():
            lines.setdefault(key, {})[month] = _in_sgd(conn, parts, month, not_counted)

    def nets(span_months: list[str]) -> dict:
        return {key: sum(by_month[m] for m in span_months if m in by_month)
                for key, by_month in lines.items()
                if any(m in by_month for m in span_months)}

    span_nets = nets(months)
    order = _ranked(span_nets)
    span_top = order[:SLOTS]
    slots = _slots(span_top, _ranked(nets(reference_months))[:SLOTS])

    type_ids = book_type.spending_type_ids(conn)
    current = f"{today.year:04d}-{today.month:02d}"
    gone = set(missing.missing_months(conn, accounts, months, today))

    types = []
    for key in order:
        drawn = [None if m in gone else lines[key].get(m, 0) for m in months]
        # The month under way is a figure so far, not a month end: an
        # estimate never reaches to it, so a gap just before it stays open.
        filled = missing.estimate([None if m == current else v for m, v in zip(months, drawn)])
        types.append({
            "id": type_ids.get(key),
            "name": key,
            "slot": slots.get(key),
            "net": span_nets[key],
            "points": [{
                "month": m,
                "net": drawn[i],
                "estimate": filled[i] if m in gone else None,
                "missing": m in gone,
                "so_far": m == current,
            } for i, m in enumerate(months)],
        })

    return {
        "book": book,
        "span": plan["span"],
        "to": end,
        "currency": CURRENCY,
        "months": months,
        "types": types,
        "top": [type_ids.get(key) for key in span_top],
        "missing": {book: sorted(gone)} if gone else {},
        "left_out": _left_out(not_counted, months),
    }


def _types_view():
    try:
        with db.get_db() as conn:
            return jsonify(types_over_time(
                conn,
                request.args.get("book") or book_type.DEFAULT_BOOK,
                request.args.get("span") or None,
                request.args.get("to") or None,
                today=_today(),
            ))
    except trend_span.SpanError as err:
        return jsonify({"error": str(err)}), err.status
    except NoSuchBook as err:
        return jsonify({"error": str(err)}), 404


def register(bp) -> None:
    """Add GET /api/trend/types to the trend blueprint."""
    bp.add_url_rule("/api/trend/types", "types", _types_view, methods=["GET"])
