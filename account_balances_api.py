"""R3: GET /api/accounts/<id>/balances?span=&to=&cur= (fin-surfaces 41; spec
15, Server routes R3). The account page's chart: the month-end balance as a
line, an estimate where a statement is missing, and money in and out per
month beneath it, on the same months.

Composed from the pieces, re-implementing none of them:

    account_balances.balances (S4)   balance at each calendar month end (None
                                     before the first anchor and before the
                                     balance sheet starts, with its note),
                                     money in and out, the whole card for a
                                     split card's part
    missing.missing_months (S3)      which months a statement is owed and not
                                     imported, today passed in
    missing.estimate (S3)            the straight line across a missing month
    balance_sheet.valued (S2)        a rupee figure in S$ at the month's saved
                                     rate, none dated before the month
    trend_span.resolve               span and to, and their 400 reasons

What this route decides itself:

    - A missing month has no balance: balance_on there lacks the month's rows.
      It gets an estimate instead, between the nearest known month ends either
      side, never at either end, and never counted.
    - The month under way is "so far": its bars draw, it has no balance point
      and is never missing (its statement is not due yet). A month after it
      has no balance either.
    - The head card is the last month end in the span with a balance, never a
      statement still to come (case 54).
    - cur is the account's own currency (the default) or S$. In S$ each
      figure is valued at its own month's rate; a month with no saved rate
      has no S$ figures, and its note says why. Each month carries its rate,
      in the contract fixtures' shape {pair, date, exact, text} with text the
      bare rate, whichever currency is shown.

A GET read: it opens the database for reading only, and never fetches a rate.
"""

from __future__ import annotations

import calendar
import datetime

from flask import jsonify, request

import account_balances
import balance_sheet
import card_balance
import missing
import money
import trend_span

TOTAL = balance_sheet.TOTAL_CURRENCY
SWITCH_NOTE = "S$ at each month's end-of-month rate"
GONE = "This account is no longer in fin"
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def today() -> datetime.date:
    """The day the route reads as today. Tests set it; nothing else does."""
    return datetime.date.today()


class _Refused(Exception):
    def __init__(self, reason: str, status: int):
        super().__init__(reason)
        self.reason, self.status = reason, status


def _month_end(month: str) -> str:
    year, number = int(month[:4]), int(month[5:7])
    return f"{month}-{calendar.monthrange(year, number)[1]:02d}"


def _month_name(month: str) -> str:
    return f"{_MONTHS[int(month[5:7]) - 1]} {month[:4]}"


def _rate(shown: dict | None) -> dict | None:
    """The balance sheet's rate as the contract fixtures carry it."""
    if shown is None:
        return None
    return {"pair": shown["pair"], "date": shown["date"], "exact": shown["exact"],
            "text": shown["rate"]}


def _first_month(conn, members: list[int]) -> str | None:
    """The month of the first row of the accounts charted: where All starts."""
    held = ", ".join("?" for _ in members)
    first = conn.execute(
        "SELECT MIN(t.date) FROM transactions t JOIN statements s ON s.id = t.statement_id "
        f"WHERE s.account_id IN ({held})",
        members,
    ).fetchone()[0]
    return first[:7] if first else None


def _cur(asked: str | None, currency: str) -> str:
    if asked is None:
        return currency
    allowed = [TOTAL] if currency == TOTAL else [TOTAL, currency]
    if asked not in allowed:
        raise _Refused(f"cur must be {' or '.join(allowed)} for this account", 400)
    return asked


def _in_sgd(conn, figure: int | None, currency: str, month: str) -> dict:
    """A figure of `month` valued in S$ at the month's saved rate: S2, with a
    rate dated before the month counted as no rate."""
    return balance_sheet.valued(conn, 0 if figure is None else figure, currency,
                                _month_end(month), not_before=f"{month}-01")


def _no_sgd(currency: str, month: str, left_out: str) -> str:
    if currency in money.MINOR_UNIT_DIGITS:
        return f"No S$ figure: no saved rate for {_month_name(month)}"
    return f"No S$ figure: {left_out}"


def read(conn, account_id: int, *, span: str | None, to: str | None,
         cur: str | None, on: datetime.date) -> dict:
    """The R3 answer for one account, as of the day `on`."""
    account = conn.execute(
        "SELECT id, name, type, currency FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if account is None:
        raise _Refused(GONE, 404)
    currency = account["currency"] or TOTAL
    whole = card_balance.part_of_ids(conn).get(account_id, account_id)
    try:
        plan = trend_span.resolve(span, to, today=on,
                                  first_month=_first_month(conn, card_balance.members(conn, whole)))
    except trend_span.SpanError as refused:
        raise _Refused(str(refused), refused.status) from None
    shown_in = _cur(cur, currency)

    months = [m for bucket in plan["buckets"] for m in bucket["months"]]
    charted = account_balances.balances(
        conn, {"id": account["id"], "type": account["type"], "currency": currency}, months)
    owed = set(missing.missing_months(
        conn, card_balance.members(conn, charted["charted_id"]), months, on))
    current = f"{on.year:04d}-{on.month:02d}"

    lines = []
    for point in charted["months"]:
        month = point["month"]
        balance = None if month in owed or month >= current else point["balance"]
        figures = {"balance": balance, "in": point["in"], "out": point["out"]}
        notes = [point["note"]] if point["note"] else []
        rate = None
        if currency != TOTAL:
            valued = _in_sgd(conn, 0, currency, month)
            rate = _rate(valued["rate"])
            if shown_in == TOTAL:
                if valued["value_minor"] is None:
                    figures = dict.fromkeys(figures)
                    notes.append(_no_sgd(currency, month, valued["left_out"]))
                else:
                    figures = {name: None if value is None
                               else _in_sgd(conn, value, currency, month)["value_minor"]
                               for name, value in figures.items()}
        lines.append({
            "month": month,
            "balance": figures["balance"],
            "estimate": None,
            "in": figures["in"],
            "out": figures["out"],
            "missing": month in owed,
            "so_far": month == current,
            "rate": rate,
            "note": ". ".join(notes) or None,
        })

    drawn = missing.estimate([line["balance"] for line in lines])
    for line, value in zip(lines, drawn):
        if line["missing"]:
            line["estimate"] = value

    known = [line for line in lines if line["balance"] is not None]
    head = ({"balance": known[-1]["balance"], "date": _month_end(known[-1]["month"])}
            if known else {"balance": None, "date": None})
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": currency,
                    "type": account["type"], "part_of": charted["card"]},
        "span": plan["span"],
        "to": plan["end"],
        "cur": shown_in,
        "months": lines,
        "head": head,
        "note": None if currency == TOTAL else SWITCH_NOTE,
    }


def balances_view(acct_id: int):
    from db import get_db

    try:
        with get_db() as conn:
            payload = read(conn, acct_id,
                           span=request.args.get("span") or None,
                           to=request.args.get("to") or None,
                           cur=request.args.get("cur") or None,
                           on=today())
    except _Refused as refused:
        return jsonify({"error": refused.reason}), refused.status
    return jsonify(payload)


def register(bp) -> None:
    """Add R3 to the trend blueprint."""
    bp.add_url_rule("/api/accounts/<int:acct_id>/balances", "account_balances",
                    balances_view, methods=["GET"])
