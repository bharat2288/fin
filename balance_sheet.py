"""The balance sheet: what the household owns and owes at a month's end.

A balance is the latest anchor on or before the date, moved by the rows dated
after the anchor up to the date. With no such anchor there is no balance; none
is ever guessed. Balances are whole minor units of the account's currency,
signed as the household sees them: cash and things owned positive, anything
owed negative. A row's amount is positive for money out.

    bank, card   the anchor less the account's own rows since
    loan         the supplied figure plus the movement rows naming the loan
                 since (an instalment is money out, and brings what is owed
                 toward zero)
    holding      the supplied figure plus the movement rows naming the holding
                 since (a sale is money in to the bank, and reduces it)

Everything here is worked out on read; nothing is stored.
"""

from __future__ import annotations

import calendar
import re
import sqlite3
from datetime import date

import account_kind
import anchors
import flow

# No balance and no net worth is shown for a date before this.
START = date(2026, 1, 1)
# The currency the totals are in. An account in another currency is shown in
# its own and left out of the totals.
TOTAL_CURRENCY = "SGD"

# (name, heading, the account kind it lists, whether its lines are owed), in
# the order the sheet shows them.
SECTIONS = (
    ("cash", "Cash and deposits", "bank", False),
    ("cards", "Cards (owed)", "card", True),
    ("loans", "Loans (owed)", "loan", True),
    ("holdings", "Holdings", "holding", False),
)

# Kinds whose balance moves by the account's own rows; the others move by the
# movement rows that name them as their other side.
OWN_ROW_KINDS = account_kind.STATEMENT_KINDS

# How an anchor's source is worded on the sheet.
SOURCE_LABELS = {anchors.STATEMENT: "statement", anchors.SUPPLIED: "your figure"}

NO_FIGURE = "no figure"

_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


class NotShown(ValueError):
    """The month asked for is not one the sheet can show."""


def month_end(month) -> date:
    """The last day of a month written YYYY-MM. Refuses anything else, and
    any month that ends before the sheet starts."""
    if not isinstance(month, str) or not _MONTH.fullmatch(month):
        raise NotShown("month must be written as YYYY-MM, such as 2026-08")
    year, number = int(month[:4]), int(month[5:])
    if year < 1:
        raise NotShown("month must be written as YYYY-MM, such as 2026-08")
    end = date(year, number, calendar.monthrange(year, number)[1])
    if end < START:
        raise NotShown(
            "The balance sheet starts on 1 January 2026; nothing is shown for an earlier date"
        )
    return end


def _anchor_before(conn: sqlite3.Connection, account_id: int, on: str, inclusive: bool):
    """The account's latest anchor on or before a day, or strictly before it."""
    return conn.execute(
        "SELECT date, amount, source FROM anchors WHERE account_id = ? AND "
        + ("date <= ?" if inclusive else "date < ?")
        + " ORDER BY date DESC LIMIT 1",
        (account_id, on),
    ).fetchone()


def _own_rows(conn: sqlite3.Connection, account_id: int, after: str, upto: str) -> tuple[int, int]:
    """How many rows the account holds dated after one day up to another, and
    what they add up to."""
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(t.amount_minor), 0) FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "WHERE s.account_id = ? AND t.date > ? AND t.date <= ?",
        (account_id, after, upto),
    ).fetchone()
    return row[0], row[1]


def _naming_rows(conn: sqlite3.Connection, account_id: int, after: str, upto: str) -> tuple[int, int, int]:
    """The movement rows naming the account as their other side, dated after
    one day up to another: how many, what they add up to, and how many of
    them are money in."""
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount_minor), 0), "
        "COALESCE(SUM(CASE WHEN amount_minor < 0 THEN 1 ELSE 0 END), 0) "
        "FROM transactions WHERE other_side_id = ? AND flow_type = ? AND date > ? AND date <= ?",
        (account_id, flow.MOVEMENT, after, upto),
    ).fetchone()
    return row[0], row[1], row[2]


def _counted(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many} since"


def _check(conn: sqlite3.Connection, account_id: int, kind: str, currency: str, anchor) -> dict | None:
    """Whether the rows fin holds account for the statement balance a line
    rests on: ties, off by the exact amount, or not checked, with the reason.

    The rows between the anchor before and this one must carry the one to the
    other. A supplied figure is never checked: it is the fact.
    """
    if kind not in OWN_ROW_KINDS:
        return None

    def not_checked(why: str) -> dict:
        return {"status": "not_checked", "difference_minor": None, "text": f"not checked ({why})"}

    if anchor is None:
        return not_checked("no statement balance held")
    if anchor["source"] != anchors.STATEMENT:
        return None
    earlier = _anchor_before(conn, account_id, anchor["date"], inclusive=False)
    if earlier is None:
        return not_checked("no earlier balance to check against")
    _, between = _own_rows(conn, account_id, earlier["date"], anchor["date"])
    # What the rows held make of the earlier balance, less what is stated.
    difference = earlier["amount"] - between - anchor["amount"]
    if difference == 0:
        return {"status": "ties", "difference_minor": 0, "text": "ties"}
    return {
        "status": "off",
        "difference_minor": difference,
        "text": f"off by {anchors.format_amount(abs(difference), currency)}",
    }


def _line(conn: sqlite3.Connection, account, as_at: date, month: str, name_of) -> dict:
    kind = account["type"]
    currency = account["currency"] or TOTAL_CURRENCY
    upto = as_at.isoformat()
    anchor = _anchor_before(conn, account["id"], upto, inclusive=True)
    line = {
        "account_id": account["id"],
        "name": name_of(account["name"]),
        "kind": kind,
        "currency": currency,
        "balance_minor": None,
        "balance": NO_FIGURE,
        "rests_on": None,
        "rows_since": 0,
        "since": None,
        "check": _check(conn, account["id"], kind, currency, anchor),
        "in_total": False,
        "left_out": NO_FIGURE,
    }
    if anchor is None:
        return line

    if kind in OWN_ROW_KINDS:
        count, total = _own_rows(conn, account["id"], anchor["date"], upto)
        balance = anchor["amount"] - total
        since = f"+ {_counted(count, 'row', 'rows')}"
    else:
        count, total, money_in = _naming_rows(conn, account["id"], anchor["date"], upto)
        balance = anchor["amount"] + total
        if kind == "loan":
            since = _counted(count, "instalment", "instalments")
        elif money_in == count:
            since = _counted(count, "sale", "sales")
        else:
            since = _counted(count, "row", "rows")

    in_total = currency == TOTAL_CURRENCY
    line.update(
        balance_minor=balance,
        balance=anchors.format_amount(balance, currency),
        rests_on={
            "date": anchor["date"],
            "source": anchor["source"],
            "label": SOURCE_LABELS[anchor["source"]],
            "age_days": (as_at - date.fromisoformat(anchor["date"])).days,
            "in_month": anchor["date"][:7] == month,
        },
        rows_since=count,
        since=since if count else None,
        in_total=in_total,
        left_out=None if in_total else f"in {currency}, not valued in {TOTAL_CURRENCY} yet",
    )
    return line


def sheet(conn: sqlite3.Connection, month: str, name_of=lambda name: name) -> dict:
    """The household's balance sheet at the end of a month written YYYY-MM.

    Lists the household's own bank, card, loan and holding accounts that are
    not archived. `name_of` is how an account's name is shown. Raises NotShown
    for a month that is not one, or that ends before the sheet starts.
    """
    as_at = month_end(month)
    sections = []
    for name, heading, kind, owed in SECTIONS:
        accounts = conn.execute(
            "SELECT id, name, type, currency FROM accounts "
            "WHERE type = ? AND owner = ? AND COALESCE(status, 'active') != 'archived' "
            "ORDER BY name, id",
            (kind, account_kind.HOUSEHOLD),
        ).fetchall()
        lines = [_line(conn, account, as_at, month, name_of) for account in accounts]
        total = sum(line["balance_minor"] for line in lines if line["in_total"])
        sections.append({
            "name": name,
            "heading": heading,
            "owed": owed,
            "lines": lines,
            "total_minor": total,
            "total": anchors.format_amount(total, TOTAL_CURRENCY),
            "left_out": [
                {"name": line["name"], "why": line["left_out"]}
                for line in lines if not line["in_total"]
            ],
        })

    net_worth = sum(section["total_minor"] for section in sections)
    left_out = [entry for section in sections for entry in section["left_out"]]
    return {
        "month": month,
        "as_at": as_at.isoformat(),
        "starts": START.isoformat(),
        "currency": TOTAL_CURRENCY,
        "sections": sections,
        "net_worth_minor": net_worth,
        "net_worth": anchors.format_amount(net_worth, TOTAL_CURRENCY),
        "left_out": left_out,
        "note": (
            "Left out of the total: "
            + "; ".join(f"{entry['name']} ({entry['why']})" for entry in left_out)
        ) if left_out else None,
    }
