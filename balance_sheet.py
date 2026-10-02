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
    company      the household's money in it: its opening figure if it has
                 one, plus, on accounts the household owns and dated after the
                 figure: the spending and refund rows in the company's book
                 (paid for it) and the movement rows naming it (money out is
                 capital, money in is paid back). With no figure it is counted
                 from the day the sheet starts, and the line says so.
    person       the same, from the movement rows naming the person

An account kept in another currency (the rupee account) shows its balance in
its own currency and, beside it, its value in SGD at the saved rate for the
date: that day's rate, or failing it the latest earlier saved one, said on the
line. Its SGD value joins the totals. With no saved rate on or before the date
it is shown in its own currency only and left out of the totals, and the sheet
says so. The SGD value is decimal arithmetic rounded half-even to the cent.

Currency change for the month is, for each such account, its balance at the
start of the month (the previous month's end) valued at the end rate, less
the same balance valued at the start rate.

Everything here is worked out on read; nothing is stored.
"""

from __future__ import annotations

import calendar
import re
import sqlite3
from datetime import date, timedelta

import account_kind
import anchors
import book_type
import flow
import money
import rates

# No balance and no net worth is shown for a date before this.
START = date(2026, 1, 1)
# The currency the totals are in. An account in another currency is shown in
# its own, and joins the totals at its saved rate.
TOTAL_CURRENCY = rates.QUOTE

# (name, heading, the account kind it lists, whether its lines are owed), in
# the order the sheet shows them.
SECTIONS = (
    ("cash", "Cash and deposits", "bank", False),
    ("cards", "Cards (owed)", "card", True),
    ("loans", "Loans (owed)", "loan", True),
    ("holdings", "Holdings", "holding", False),
)

# The section for the household's money in companies and with people, and the
# kinds it lists. A balance here needs no anchor: with none it is counted from
# the day the sheet starts.
COUNTERPARTIES = ("companies", "Companies and people (our money in them)", ("company", "person"))
SINCE_START = f"since {START.day} {START:%b %Y}"
# The flows of a row a company's book counts as paid for it.
PAID_FOR_FLOWS = ("expense", "refund")

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


def _moved(conn: sqlite3.Connection, account_id: int, kind: str, anchor, upto: str):
    """An anchor carried to a day by the rows since: (the balance, how many
    rows, how they are worded)."""
    if kind in OWN_ROW_KINDS:
        count, total = _own_rows(conn, account_id, anchor["date"], upto)
        return anchor["amount"] - total, count, f"+ {_counted(count, 'row', 'rows')}"
    count, total, money_in = _naming_rows(conn, account_id, anchor["date"], upto)
    if kind == "loan":
        since = _counted(count, "instalment", "instalments")
    elif money_in == count:
        since = _counted(count, "sale", "sales")
    else:
        since = _counted(count, "row", "rows")
    return anchor["amount"] + total, count, since


def balance_on(conn: sqlite3.Connection, account, on: str) -> int | None:
    """An account's balance on a day, in whole minor units of its currency,
    or None when it has no anchor on or before the day."""
    anchor = _anchor_before(conn, account["id"], on, inclusive=True)
    if anchor is None:
        return None
    return _moved(conn, account["id"], account["type"], anchor, on)[0]


def _no_rate(currency: str, on: str) -> str:
    return f"no saved {rates.pair_for(currency)} rate on or before {on}"


def _rate_shown(currency: str, rate: dict) -> dict:
    """A saved rate as a line shows it: the rate, the day it was saved for,
    and whether that is the day asked for."""
    text = f"1 {currency} = {anchors.CURRENCY_SIGNS[TOTAL_CURRENCY]} {rate['rate']}, rate of {rate['date']}"
    if not rate["exact"]:
        text += f", the latest saved on or before {rate['asked']}"
    return {
        "pair": rate["pair"],
        "rate": rate["rate"],
        "date": rate["date"],
        "asked": rate["asked"],
        "exact": rate["exact"],
        "source": rate["source"],
        "text": text,
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
        # What the balance is worth in the currency of the totals, and for an
        # account in another currency the rate that says so.
        "value_minor": None,
        "value": None,
        "rate": None,
        "rests_on": None,
        "rows_since": 0,
        "since": None,
        "check": _check(conn, account["id"], kind, currency, anchor),
        "in_total": False,
        "left_out": NO_FIGURE,
    }
    if anchor is None:
        return line

    balance, count, since = _moved(conn, account["id"], kind, anchor, upto)
    line.update(
        _valued(conn, balance, currency, upto),
        rests_on=_rests_on(anchor, as_at, month),
        rows_since=count,
        since=since if count else None,
    )
    return line


def _valued(conn: sqlite3.Connection, balance: int, currency: str, upto: str) -> dict:
    """A balance as a line shows it: in its own currency, and what it is worth
    in the currency of the totals, with the rate that says so or why it is
    left out of them."""
    if currency == TOTAL_CURRENCY:
        value, rate, left_out = balance, None, None
    elif currency not in money.MINOR_UNIT_DIGITS:
        value, rate, left_out = None, None, f"in {currency}, a currency fin keeps no rate for"
    else:
        found = rates.on_or_before(conn, rates.pair_for(currency), upto)
        if found is None:
            value, rate, left_out = None, None, f"in {currency}, {_no_rate(currency, upto)}"
        else:
            value = money.convert_minor(balance, found["value"], currency, TOTAL_CURRENCY)
            rate, left_out = _rate_shown(currency, found), None
    return {
        "balance_minor": balance,
        "balance": anchors.format_amount(balance, currency),
        "value_minor": value,
        "value": None if value is None else anchors.format_amount(value, TOTAL_CURRENCY),
        "rate": rate,
        "in_total": value is not None,
        "left_out": left_out,
    }


def _rests_on(anchor, as_at: date, month: str) -> dict:
    return {
        "date": anchor["date"],
        "source": anchor["source"],
        "label": SOURCE_LABELS[anchor["source"]],
        "age_days": (as_at - date.fromisoformat(anchor["date"])).days,
        "in_month": anchor["date"][:7] == month,
    }


def _counterparty_line(conn: sqlite3.Connection, account, as_at: date, month: str, name_of) -> dict:
    """A company's or a person's line: the household's money in it, and what
    that is made of.

    The balance is the opening figure if there is one (the latest figure on or
    before the day), plus the rows dated after it up to the day, on accounts
    the household owns: paid for it, plus capital, less paid back. With no
    figure the rows are counted from the day the sheet starts. Only a company
    that is a book has rows paid for it; a person has movements alone.

    A row on an account kept in another currency than this one's is not
    counted, and the line says how many: amounts in two currencies are never
    added as if they were one.
    """
    kind = account["type"]
    currency = account["currency"] or TOTAL_CURRENCY
    upto = as_at.isoformat()
    anchor = _anchor_before(conn, account["id"], upto, inclusive=True)
    after = anchor["date"] if anchor is not None else (START - timedelta(days=1)).isoformat()
    # The book whose spending is this company's costs; a person, or a company
    # that is no book, has none.
    name = account["name"]
    is_book = kind == "company" and name in book_type.BOOK_NAMES and name != book_type.DEFAULT_BOOK
    book = name if is_book else None

    flows = ", ".join("?" for _ in PAID_FOR_FLOWS)
    paid_for_row = f"(t.flow_type IN ({flows}) AND t.book = ?)"
    naming_row = "(t.flow_type = ? AND t.other_side_id = ?)"
    same_currency = "COALESCE(a.currency, ?) = ?"
    paid_for_params = [*PAID_FOR_FLOWS, book]
    naming_params = [flow.MOVEMENT, account["id"]]
    currency_params = [TOTAL_CURRENCY, currency]
    row = conn.execute(
        "SELECT "
        f"COALESCE(SUM(CASE WHEN {same_currency} THEN 1 ELSE 0 END), 0), "
        f"COALESCE(SUM(CASE WHEN {same_currency} AND {paid_for_row} THEN t.amount_minor ELSE 0 END), 0), "
        f"COALESCE(SUM(CASE WHEN {same_currency} AND {naming_row} AND t.amount_minor > 0 "
        "THEN t.amount_minor ELSE 0 END), 0), "
        f"COALESCE(SUM(CASE WHEN {same_currency} AND {naming_row} AND t.amount_minor < 0 "
        "THEN -t.amount_minor ELSE 0 END), 0), "
        "COUNT(*) "
        "FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        "WHERE a.owner = ? AND t.date > ? AND t.date <= ? "
        f"AND ({paid_for_row} OR {naming_row})",
        [
            *currency_params,
            *currency_params, *paid_for_params,
            *currency_params, *naming_params,
            *currency_params, *naming_params,
            account_kind.HOUSEHOLD, after, upto,
            *paid_for_params, *naming_params,
        ],
    ).fetchone()
    count, paid_for, capital, paid_back, found = row
    opening = anchor["amount"] if anchor is not None else None
    balance = (opening or 0) + paid_for + capital - paid_back
    not_counted = found - count

    def shown(amount: int) -> str:
        return anchors.format_amount(amount, currency)

    parts = [f"opening {shown(opening)}"] if opening is not None else []
    if kind == "company":
        parts += [f"paid for it {shown(paid_for)}", f"capital {shown(capital)}"]
    else:
        parts.append(f"lent {shown(capital)}")
    parts.append(f"paid back {shown(paid_back)}")

    if not count:
        since = None
    elif anchor is not None:
        since = f"+ {_counted(count, 'row', 'rows')}"
    else:
        since = f"{count} {'row' if count == 1 else 'rows'}"

    line = {
        "account_id": account["id"],
        "name": name_of(name),
        "kind": kind,
        "currency": currency,
        "rests_on": _rests_on(anchor, as_at, month) if anchor is not None else None,
        # What the balance is counted from when it rests on no figure.
        "since_label": None if anchor is not None else SINCE_START,
        "rows_since": count,
        "since": since,
        "check": None,
        "made_of": {
            "opening_minor": opening,
            "opening": None if opening is None else shown(opening),
            "paid_for_minor": paid_for,
            "paid_for": shown(paid_for),
            "capital_minor": capital,
            "capital": shown(capital),
            "paid_back_minor": paid_back,
            "paid_back": shown(paid_back),
            "text": " · ".join(parts),
        },
        "rows_not_counted": not_counted,
        "note": (
            f"{not_counted} {'row' if not_counted == 1 else 'rows'} on an account in another "
            f"currency {'is' if not_counted == 1 else 'are'} not counted"
        ) if not_counted else None,
    }
    line.update(_valued(conn, balance, currency, upto))
    return line


def _currency_change(conn: sqlite3.Connection, accounts: list, as_at: date, name_of) -> dict:
    """Currency change for the month ending on `as_at`: for each account kept
    in another currency, its balance at the start of the month valued at the
    end rate, less the same balance valued at the start rate. Each valuation
    is rounded to the cent on its own, as the sheet shows it, so the figure
    is the difference of two figures the sheets show.

    An account with no balance at the start, or no saved rate for either day,
    has no figure: none is guessed, the total is then None, and the note says
    which account and why.
    """
    start = (as_at.replace(day=1) - timedelta(days=1)).isoformat()
    end = as_at.isoformat()
    lines = []
    for account in accounts:
        currency = account["currency"] or TOTAL_CURRENCY
        if currency == TOTAL_CURRENCY:
            continue
        line = {
            "account_id": account["id"],
            "name": name_of(account["name"]),
            "currency": currency,
            "opening_minor": None,
            "opening": None,
            "start": None,
            "end": None,
            "at_start_minor": None,
            "at_end_minor": None,
            "change_minor": None,
            "change": None,
            "why": None,
        }
        lines.append(line)
        if currency not in money.MINOR_UNIT_DIGITS:
            line["why"] = f"in {currency}, a currency fin keeps no rate for"
            continue
        opening = balance_on(conn, account, start)
        if opening is None:
            line["why"] = f"no balance on {start}"
            continue
        line.update(opening_minor=opening, opening=anchors.format_amount(opening, currency))
        pair = rates.pair_for(currency)
        start_rate = rates.on_or_before(conn, pair, start)
        end_rate = rates.on_or_before(conn, pair, end)
        if start_rate is None or end_rate is None:
            line["why"] = _no_rate(currency, start if start_rate is None else end)
            continue
        at_start = money.convert_minor(opening, start_rate["value"], currency, TOTAL_CURRENCY)
        at_end = money.convert_minor(opening, end_rate["value"], currency, TOTAL_CURRENCY)
        line.update(
            start=_rate_shown(currency, start_rate),
            end=_rate_shown(currency, end_rate),
            at_start_minor=at_start,
            at_end_minor=at_end,
            change_minor=at_end - at_start,
            change=anchors.format_amount(at_end - at_start, TOTAL_CURRENCY),
        )

    missing = [line for line in lines if line["change_minor"] is None]
    total = None if missing else sum(line["change_minor"] for line in lines)
    return {
        "from": start,
        "to": end,
        "minor": total,
        "text": None if total is None else anchors.format_amount(total, TOTAL_CURRENCY),
        "lines": lines,
        "note": (
            "Currency change not worked out: "
            + "; ".join(f"{line['name']} ({line['why']})" for line in missing)
        ) if missing else None,
    }


def sheet(conn: sqlite3.Connection, month: str, name_of=lambda name: name) -> dict:
    """The household's balance sheet at the end of a month written YYYY-MM.

    Lists the household's own bank, card, loan and holding accounts that are
    not archived, then its money in each company and with each person.
    `name_of` is how an account's name is shown. Raises NotShown
    for a month that is not one, or that ends before the sheet starts.
    """
    as_at = month_end(month)
    sections = []
    listed = []
    for name, heading, kind, owed in SECTIONS:
        accounts = conn.execute(
            "SELECT id, name, type, currency FROM accounts "
            "WHERE type = ? AND owner = ? AND COALESCE(status, 'active') != 'archived' "
            "ORDER BY name, id",
            (kind, account_kind.HOUSEHOLD),
        ).fetchall()
        listed += accounts
        lines = [_line(conn, account, as_at, month, name_of) for account in accounts]
        total = sum(line["value_minor"] for line in lines if line["in_total"])
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

    name, heading, kinds = COUNTERPARTIES
    accounts = conn.execute(
        "SELECT id, name, type, currency FROM accounts "
        f"WHERE type IN ({', '.join('?' for _ in kinds)}) AND owner = ? "
        "AND COALESCE(status, 'active') != 'archived' ORDER BY name, id",
        (*kinds, account_kind.HOUSEHOLD),
    ).fetchall()
    lines = [_counterparty_line(conn, account, as_at, month, name_of) for account in accounts]
    total = sum(line["value_minor"] for line in lines if line["in_total"])
    sections.append({
        "name": name,
        "heading": heading,
        "owed": False,
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
        "currency_change": _currency_change(conn, listed, as_at, name_of),
        "note": (
            "Left out of the total: "
            + "; ".join(f"{entry['name']} ({entry['why']})" for entry in left_out)
        ) if left_out else None,
    }
