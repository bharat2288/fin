"""The balance sheet: what the household owns and owes at a month's end.

A balance is the latest anchor on or before the date, moved by the rows dated
after the anchor up to the date. With no such anchor there is no balance; none
is ever guessed. Balances are whole minor units of the account's currency,
signed as the household sees them: cash and things owned positive, anything
owed negative. A row's amount is positive for money out.

    bank, card   the anchor less the account's own rows since, each row
                 counted on its day in the statement it printed on (ROW_DAY)
    card split   (the DBS Vantage card, card_balance.py) one balance, on the
    by holder    main cardholder's account: its anchor less the rows of every
                 cardholder's account since. A cardholder's account that is
                 part of it shows no balance of its own and is not left out:
                 its line says which balance counts it, once
    shared       (shared.py: HDFC 6324) the household's part: the latest figure
                 supplied less the account's rows since that are not "not
                 ours". Its statement balances are the whole account's: they
                 are tied against every row and never carried. With no figure
                 there is no balance, and the line says why
    bank with    (Home Reno, a fixed deposit: no statement fin imports) the
    no statement supplied figure plus the transfer and movement rows naming
                 it since, on the household's accounts in its currency: money
                 out of the other account is money into this one (ruling 4)
    loan         the supplied figure plus the movement rows naming the loan
                 since (an instalment is money out, and brings what is owed
                 toward zero), less the interest worked out for those days
                 when a later figure has worked it out (loan_interest.py):
                 between two figures what is owed falls by the principal
                 repaid, and after the latest by the instalments in full
    holding      the supplied figure plus the movement rows naming the holding
                 since (a sale is money in to the bank, and reduces it)
    company      the household's money in it: its opening figure if it has
                 one, plus, on accounts the household owns and dated after the
                 figure: the spending and refund rows in the company's book
                 (paid for it) and the movement rows naming it (money out is
                 capital, money in is paid back). With no figure it is counted
                 from the day the sheet starts, and the line says so.

A person is no balance on the sheet (personal-ledger 23, the switch): what is
lent to or owed to a person is the expectations named for them, in Expect
back's sections. Since the contract the sheet reads no person anchor at all
(the anchors are kept): the expect-back conversion works out a person's old
line itself (convert_expect_back.old_line), to prove its records against.

An account kept in another currency (the rupee account) shows its balance in
its own currency and, beside it, its value in SGD at the saved rate for the
date: that day's rate, or failing it the latest earlier one saved in the same
month, said on the line (`valued`). Its SGD value joins the totals. A rate
dated before the month is no rate for it (ruling 4 of the trend pages): with
no saved rate in the month on or before the date the balance is shown in its
own currency only and left out of the totals, and the sheet says so. The SGD
value is decimal arithmetic rounded half-even to the cent.

Currency change for the month is, for each such account, its balance at the
start of the month (the previous month's end) valued at the end rate, less
the same balance valued at the start rate, each rate saved in its own month.

Expect back's three sections (books_expect.py: Expected back, Waiting for its
row, To pay back) ride on the sheet under `expect`, one call, and are in net
worth: Expected back is ours, Waiting for its row and To pay back are owed.

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
import card_balance
import flow
import loan_interest
import money
import rates
import shared
import statement_period

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

# The section for the household's money in companies, and the kinds it lists.
# A balance here needs no anchor: with none it is counted from the day the
# sheet starts. Persons left it at the switch (personal-ledger 23): their money
# is Expect back's sections.
COUNTERPARTIES = ("companies", "Companies (our money in them)", ("company",))
SINCE_START = f"since {START.day} {START:%b %Y}"
# The flows of a row a company's book counts as paid for it.
PAID_FOR_FLOWS = ("expense", "refund")

# The day a row counts on, as SQL over the row `t` and its statement record
# `s` (ruling 1: a row belongs to the statement it printed on). A row filed
# under a printed statement counts inside that statement's period: after the
# account's previous printed statement closed, and on or before this one
# closed. A late-posting row, dated before the previous statement closed,
# counts on the first day after it, so it never moves a period already
# closed; the balance on a statement's closing day is that statement's
# closing balance. A row on a calendar-month record (a source with no
# balance) counts on its own date. Within its statement's period a row
# counts on its own date, so a month-end between two statements is moved by
# the rows dated up to it.
#
# The previous statement closed on the later of the account's previous printed
# record and its previous statement balance (anchor). The anchor covers the
# history from before rows were filed by statement: a converted database
# holds only month records, but its statement balances are anchored, and the
# first statement imported afterwards must not reach back into a period one
# of them already closed.
#
# Which closings those are has one home, statement_period.PREVIOUS_CLOSINGS:
# a statement's period, which coverage and missing months read, starts the day
# after the same closing.
ROW_DAY = (
    "(CASE WHEN s.printed = 1 THEN MIN(s.statement_date, MAX(t.date, COALESCE(("
    "SELECT date(MAX(closed), '+1 day') FROM ("
    + statement_period.PREVIOUS_CLOSINGS.format(account="s.account_id", closing="s.statement_date")
    + ")), t.date))) ELSE t.date END)"
)

# Kinds whose balance moves by the account's own rows; the others move by the
# movement rows that name them as their other side.
OWN_ROW_KINDS = account_kind.STATEMENT_KINDS

# How an anchor's source is worded on the sheet.
SOURCE_LABELS = {anchors.STATEMENT: "statement", anchors.SUPPLIED: "your figure"}

NO_FIGURE = "no figure"
SHARED_NO_FIGURE = ("shared: no figure for the household's part; its statement balance "
                    "is the whole account's")

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


def _anchor_before(conn: sqlite3.Connection, account_id: int, on: str, inclusive: bool,
                   source: str | None = None):
    """The account's latest anchor on or before a day, or strictly before it;
    of one source only, where `source` is given."""
    return conn.execute(
        "SELECT date, amount, source FROM anchors WHERE account_id = ? AND "
        + ("date <= ?" if inclusive else "date < ?")
        + (" AND source = ?" if source else "")
        + " ORDER BY date DESC LIMIT 1",
        (account_id, on, *((source,) if source else ())),
    ).fetchone()


def _balance_anchor(conn: sqlite3.Connection, account_id: int, on: str):
    """The anchor a balance on a day rests on: the latest on or before it;
    on a shared account the latest figure supplied, the household's part (a
    statement balance there is the whole account's)."""
    source = anchors.SUPPLIED if shared.is_shared(conn, account_id) else None
    return _anchor_before(conn, account_id, on, inclusive=True, source=source)


def _tie_source(conn: sqlite3.Connection, account_id: int) -> str | None:
    """The anchors a statement balance ties against: any, or on a shared
    account only the statement balances (a figure there is the household's
    part, not the account's)."""
    return anchors.STATEMENT if shared.is_shared(conn, account_id) else None


# A row the household's part of a shared account is not moved by, as SQL
# over the row `t`.
OURS_ONLY = f"COALESCE(t.flow_type, 'expense') != '{flow.NOT_OURS}'"


def _own_rows(conn: sqlite3.Connection, account_id: int, after: str, upto: str,
              ours_only: bool = False) -> tuple[int, int]:
    """How many rows move the account's balance that count after one day up
    to another (ROW_DAY), and what they add up to: its own, and on a card
    split by cardholder every cardholder's account's (card_balance.py).
    `ours_only` leaves out the rows that are not ours (a shared account's
    part); a tie counts every row."""
    members = card_balance.members(conn, account_id)
    held = ", ".join("?" for _ in members)
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(t.amount_minor), 0) FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        f"WHERE s.account_id IN ({held}) AND {ROW_DAY} > ? AND {ROW_DAY} <= ?"
        + (f" AND {OURS_ONLY}" if ours_only else ""),
        (*members, after, upto),
    ).fetchone()
    return row[0], row[1]


def _own_rows_split(conn: sqlite3.Connection, account_id: int, after: str, upto: str) -> dict:
    """The same rows as _own_rows, kept apart: money in and money out, each
    as a positive amount, and the rows' ids (an account's page lists its rows
    under the statement they tie into)."""
    members = card_balance.members(conn, account_id)
    held = ", ".join("?" for _ in members)
    rows = conn.execute(
        "SELECT t.id, t.amount_minor FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        f"WHERE s.account_id IN ({held}) AND {ROW_DAY} > ? AND {ROW_DAY} <= ? ORDER BY t.id",
        (*members, after, upto),
    ).fetchall()
    return {
        "in_minor": -sum(r[1] for r in rows if r[1] < 0),
        "out_minor": sum(r[1] for r in rows if r[1] > 0),
        "row_ids": [r[0] for r in rows],
    }


def _naming_rows(conn: sqlite3.Connection, account_id: int, after: str, upto: str) -> tuple[int, int, int]:
    """The movement rows naming the account as their other side, dated after
    one day up to another: how many, what they add up to, and how many of
    them are money in."""
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(t.amount_minor), 0), "
        "COALESCE(SUM(CASE WHEN t.amount_minor < 0 THEN 1 ELSE 0 END), 0) "
        "FROM transactions t JOIN statements s ON t.statement_id = s.id "
        f"WHERE t.other_side_id = ? AND t.flow_type = ? AND {ROW_DAY} > ? AND {ROW_DAY} <= ?",
        (account_id, flow.MOVEMENT, after, upto),
    ).fetchone()
    return row[0], row[1], row[2]


# The flows of a row on another account that move a bank account holding no
# statement: money moved into it or out of it (ruling 4).
INTO_UNSTATED_FLOWS = ("transfer", flow.MOVEMENT)


def _holds_statements(conn: sqlite3.Connection, account_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM statements WHERE account_id = ? LIMIT 1", (account_id,)
    ).fetchone() is not None


def _moves_into(conn: sqlite3.Connection, account_id: int, currency: str | None,
                after: str, upto: str) -> tuple[int, int]:
    """The transfer and movement rows on the household's other accounts, in
    this account's currency, that name it as their other side, dated after
    one day up to another: how many, and what they add up to (positive is
    money out of the other account, so into this one)."""
    flows = ", ".join("?" for _ in INTO_UNSTATED_FLOWS)
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(t.amount_minor), 0) FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON a.id = s.account_id "
        f"WHERE t.other_side_id = ? AND t.flow_type IN ({flows}) AND a.owner = ? "
        f"AND COALESCE(a.currency, ?) = ? AND s.account_id != ? AND {ROW_DAY} > ? AND {ROW_DAY} <= ?",
        (account_id, *INTO_UNSTATED_FLOWS, account_kind.HOUSEHOLD,
         TOTAL_CURRENCY, currency or TOTAL_CURRENCY, account_id, after, upto),
    ).fetchone()
    return row[0], row[1]


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
    earlier = _anchor_before(conn, account_id, anchor["date"], inclusive=False,
                             source=_tie_source(conn, account_id))
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


def _moved(conn: sqlite3.Connection, account_id: int, kind: str, anchor, upto: str,
           currency: str | None = None):
    """An anchor carried to a day by the rows since: (the balance, how many
    rows, how they are worded)."""
    if kind == "bank" and not _holds_statements(conn, account_id):
        count, total = _moves_into(conn, account_id, currency, anchor["date"], upto)
        return anchor["amount"] + total, count, f"+ {_counted(count, 'transfer', 'transfers')}"
    if kind in OWN_ROW_KINDS:
        ours = shared.is_shared(conn, account_id)
        count, total = _own_rows(conn, account_id, anchor["date"], upto, ours_only=ours)
        return anchor["amount"] - total, count, f"+ {_counted(count, 'row of ours', 'rows of ours') if ours else _counted(count, 'row', 'rows')}"
    count, total, money_in = _naming_rows(conn, account_id, anchor["date"], upto)
    if kind == "loan":
        since = _counted(count, "instalment", "instalments")
        # Between two figures the later one has worked out the interest, and
        # what is owed falls only by the principal repaid.
        interest = loan_interest.interest_between(conn, account_id, anchor["date"], upto)
        if interest:
            since += f", less {anchors.format_amount(interest, currency)} interest worked out"
        return anchor["amount"] + total - interest, count, since
    if money_in == count:
        since = _counted(count, "sale", "sales")
    else:
        since = _counted(count, "row", "rows")
    return anchor["amount"] + total, count, since


def balance_on(conn: sqlite3.Connection, account, on: str) -> int | None:
    """An account's balance on a day, in whole minor units of its currency,
    or None when it has no anchor on or before the day."""
    anchor = _balance_anchor(conn, account["id"], on)
    if anchor is None:
        return None
    return _moved(conn, account["id"], account["type"], anchor, on, account["currency"])[0]


NO_RATE_KEPT = "a currency fin keeps no rate for"


def _no_rate(currency: str, on: str, not_before: str | None = None) -> str:
    pair = rates.pair_for(currency)
    if not_before is None:
        return f"no saved {pair} rate on or before {on}"
    return f"no saved {pair} rate from {not_before} to {on}"


def _rate_on(conn: sqlite3.Connection, currency: str, on: str, not_before: str | None) -> dict | None:
    """The saved rate a figure on a day uses: the latest on or before the
    day, and with `not_before` none dated before that day (ruling 4: a rate
    from before the month is no rate for it). None when there is none."""
    found = rates.on_or_before(conn, rates.pair_for(currency), on)
    if found is None or (not_before is not None and found["date"] < not_before):
        return None
    return found


def _month_start(on: str) -> str:
    """The first day of the month a day (YYYY-MM-DD) is in: the earliest a
    rate for a figure on that day may be dated."""
    return f"{on[:7]}-01"


def valued(conn: sqlite3.Connection, amount_minor: int, currency: str, upto: str,
           not_before: str | None = None) -> dict:
    """An amount in whole minor units of `currency`, valued in the currency of
    the totals at the latest saved rate on or before `upto` (a day,
    YYYY-MM-DD). With `not_before`, a rate dated before that day counts as no
    rate: the trend pages and the balance sheet pass the month's first day.

    Returns `value_minor` (None when it cannot be valued), `rate` (the rate
    used as a line shows it: pair, rate, date, asked, exact, source, text;
    None for an amount already in the currency of the totals or with no rate)
    and `left_out` (why it has no value, or None). The value is decimal
    arithmetic rounded half-even to the cent. Only saved rates are read;
    nothing is ever fetched, and with none nothing is guessed.
    """
    if currency == TOTAL_CURRENCY:
        return {"value_minor": amount_minor, "rate": None, "left_out": None}
    if currency not in money.MINOR_UNIT_DIGITS:
        return {"value_minor": None, "rate": None, "left_out": f"in {currency}, {NO_RATE_KEPT}"}
    found = _rate_on(conn, currency, upto, not_before)
    if found is None:
        return {
            "value_minor": None,
            "rate": None,
            "left_out": f"in {currency}, {_no_rate(currency, upto, not_before)}",
        }
    return {
        "value_minor": money.convert_minor(amount_minor, found["value"], currency, TOTAL_CURRENCY),
        "rate": _rate_shown(currency, found),
        "left_out": None,
    }


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
    whole = card_balance.part_of_ids(conn).get(account["id"])
    if whole is not None:
        return _part_line(conn, account, whole, currency, name_of)
    is_shared = shared.is_shared(conn, account["id"])
    anchor = _balance_anchor(conn, account["id"], upto)
    # On a shared account the tie is the statement's, whatever figure the
    # balance rests on.
    tied = (_anchor_before(conn, account["id"], upto, inclusive=True, source=anchors.STATEMENT)
            if is_shared else anchor)
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
        "check": _check(conn, account["id"], kind, currency, tied),
        "in_total": False,
        "left_out": SHARED_NO_FIGURE if is_shared else NO_FIGURE,
        # A shared account's balance is the household's part (shared.py).
        "shared": is_shared,
    }
    if anchor is None:
        return line

    balance, count, since = _moved(conn, account["id"], kind, anchor, upto, currency)
    line.update(
        _valued(conn, balance, currency, upto),
        rests_on=_rests_on(anchor, as_at, month),
        rows_since=count,
        since=since if count else None,
    )
    return line


def _part_line(conn: sqlite3.Connection, account, whole: int, currency: str, name_of) -> dict:
    """The line of a cardholder's account that is part of its card's one
    balance: no balance of its own (any anchor on it is not read), counted in
    the balance account's line, and never left out."""
    whole_name = conn.execute("SELECT name FROM accounts WHERE id = ?", (whole,)).fetchone()[0]
    return {
        "account_id": account["id"],
        "name": name_of(account["name"]),
        "kind": account["type"],
        "currency": currency,
        "balance_minor": None,
        "balance": f"counted in {name_of(whole_name)}",
        "value_minor": None,
        "value": None,
        "rate": None,
        "rests_on": None,
        "rows_since": 0,
        "since": None,
        "check": None,
        "in_total": False,
        "left_out": None,
        "counted_in": whole,
    }


def _valued(conn: sqlite3.Connection, balance: int, currency: str, upto: str) -> dict:
    """A balance as a line shows it: in its own currency, and what it is worth
    in the currency of the totals at a rate saved in the month (`valued`),
    with the rate that says so or why it is left out of them."""
    shown = valued(conn, balance, currency, upto, not_before=_month_start(upto))
    value = shown["value_minor"]
    return {
        "balance_minor": balance,
        "balance": anchors.format_amount(balance, currency),
        "value_minor": value,
        "value": None if value is None else anchors.format_amount(value, TOTAL_CURRENCY),
        "rate": shown["rate"],
        "in_total": value is not None,
        "left_out": shown["left_out"],
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
    """A company's line: the household's money in it, and what that is made
    of. A person has no line (persons left the sheet at the switch, and since
    the contract their anchors are not read here): ValueError.

    The balance is the opening figure if there is one (the latest figure on or
    before the day), plus the rows dated after it up to the day, on accounts
    the household owns: paid for it, plus capital, less paid back. With no
    figure the rows are counted from the day the sheet starts. Only a company
    that is a book has rows paid for it.

    A row on an account kept in another currency than this one's is not
    counted, and the line says how many: amounts in two currencies are never
    added as if they were one.
    """
    kind = account["type"]
    if kind not in COUNTERPARTIES[2]:
        raise ValueError(f"no counterparty line for an account of kind {kind}")
    currency = account["currency"] or TOTAL_CURRENCY
    upto = as_at.isoformat()
    anchor = _anchor_before(conn, account["id"], upto, inclusive=True)
    after = anchor["date"] if anchor is not None else (START - timedelta(days=1)).isoformat()
    # The book whose spending is this company's costs; a company that is no
    # book has none.
    name = account["name"]
    book = book_type.company_book_named(conn, name)

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
        f"WHERE a.owner = ? AND {ROW_DAY} > ? AND {ROW_DAY} <= ? "
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
    parts += [f"paid for it {shown(paid_for)}", f"capital {shown(capital)}", f"paid back {shown(paid_back)}"]

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

    An account with no balance at the start, or no rate saved in its month on
    or before either day, has no figure: none is guessed, the total is then None, and the note says
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
            line["why"] = f"in {currency}, {NO_RATE_KEPT}"
            continue
        opening = balance_on(conn, account, start)
        if opening is None:
            line["why"] = f"no balance on {start}"
            continue
        line.update(opening_minor=opening, opening=anchors.format_amount(opening, currency))
        # Each end at a rate saved in its own month, as each month's sheet
        # values it (ruling 4).
        start_rate = _rate_on(conn, currency, start, _month_start(start))
        end_rate = _rate_on(conn, currency, end, _month_start(end))
        if start_rate is None or end_rate is None:
            on = start if start_rate is None else end
            line["why"] = _no_rate(currency, on, _month_start(on))
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


def _marked(line: dict, account) -> dict:
    """A line marked with whether its account is archived: shown apart if the
    screen likes, never left out of a total."""
    line["archived"] = account["status"] == "archived"
    return line


def sheet(conn: sqlite3.Connection, month: str, name_of=lambda name: name,
          today: date | None = None) -> dict:
    """The household's balance sheet at the end of a month written YYYY-MM.

    Lists the household's own bank, card, loan and holding accounts, then its
    money in each company. An archived account is listed
    and counted like any other, its line marked `archived`: archiving changes
    what is shown, never the figure (ruling 6).
    `name_of` is how an account's name is shown. Raises NotShown
    for a month that is not one, or that ends before the sheet starts.

    Under `expect` it carries expect back's three sections (books_expect.py)
    as at the month's end, their ages counted to `today` while the month runs
    (today's date when not given). Expected back is in net worth and what is
    owed (Waiting for its row, To pay back) comes off it; an item with no
    rate is left out and said, as a line with no figure is.
    """
    import books_expect  # imported here: it reads this module

    as_at = month_end(month)
    sections = []
    listed = []
    for name, heading, kind, owed in SECTIONS:
        accounts = conn.execute(
            "SELECT id, name, type, currency, status FROM accounts "
            "WHERE type = ? AND owner = ? ORDER BY name, id",
            (kind, account_kind.HOUSEHOLD),
        ).fetchall()
        listed += accounts
        lines = [
            _marked(_line(conn, account, as_at, month, name_of), account) for account in accounts
        ]
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
                for line in lines if not line["in_total"] and not line.get("counted_in")
            ],
        })

    name, heading, kinds = COUNTERPARTIES
    accounts = conn.execute(
        "SELECT id, name, type, currency, status FROM accounts "
        f"WHERE type IN ({', '.join('?' for _ in kinds)}) AND owner = ? ORDER BY name, id",
        (*kinds, account_kind.HOUSEHOLD),
    ).fetchall()
    lines = [
        _marked(_counterparty_line(conn, account, as_at, month, name_of), account)
        for account in accounts
    ]
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

    expect = books_expect.sections(conn, as_at.isoformat(), today=today or date.today())
    net_worth = sum(section["total_minor"] for section in sections) + books_expect.net_minor(expect)
    left_out = [entry for section in sections for entry in section["left_out"]] + books_expect.left_out(expect)
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
        "expect": expect,
        "note": (
            "Left out of the total: "
            + "; ".join(f"{entry['name']} ({entry['why']})" for entry in left_out)
        ) if left_out else None,
    }


def _side(conn: sqlite3.Connection, account, balance_minor) -> str:
    """Which of a party's two balances a line is: "held" for the household's
    part of a shared account, whatever its sign (a part below nil is owed to
    the bank, not to the party); otherwise by the sign of the balance, and
    by the account's kind when it is nil or there is none."""
    if shared.is_shared(conn, account["id"]):
        return "held"
    if balance_minor:
        return "owed" if balance_minor < 0 else "held"
    return "owed" if account["type"] in account_kind.OWED_KINDS else "held"


def party_lines(conn: sqlite3.Connection, party_id: int, month: str, name_of=lambda name: name) -> dict:
    """A party's balances with the household at a month's end (RUL-020): the
    line of each account joined to it, as the sheet draws it, each marked
    `side` "owed" (what the household owes them) or "held" (the household's
    money they hold; _side), and the net in the currency of the totals. Only
    the household's accounts count: one a company book owns is its own. A line
    left out of the totals (no figure, no rate) is left out of the net and
    said. A person account has no line (persons left the sheet). With no
    line, the net is None. Raises NotShown as `sheet` does."""
    as_at = month_end(month)
    lines = []
    for account in conn.execute(
        "SELECT id, name, type, currency, status FROM accounts WHERE party_id = ? AND type != 'person' "
        "AND owner = ? ORDER BY name, id", (party_id, account_kind.HOUSEHOLD),
    ).fetchall():
        if account["type"] in COUNTERPARTIES[2]:
            line = _counterparty_line(conn, account, as_at, month, name_of)
        else:
            line = _line(conn, account, as_at, month, name_of)
        line["side"] = _side(conn, account, line["balance_minor"])
        lines.append(_marked(line, account))
    counted = [line["value_minor"] for line in lines if line["in_total"]]
    net = sum(counted) if counted else None
    return {
        "month": month,
        "as_at": as_at.isoformat(),
        "currency": TOTAL_CURRENCY,
        "lines": lines,
        "net_minor": net,
        "net": None if net is None else (
            "square" if net == 0
            else f"we owe {anchors.format_amount(-net, TOTAL_CURRENCY)}, net" if net < 0
            else f"ours, {anchors.format_amount(net, TOTAL_CURRENCY)} net"),
        "left_out": [{"name": line["name"], "why": line["left_out"]}
                     for line in lines if not line["in_total"] and not line.get("counted_in")],
    }


def tie_lines(conn: sqlite3.Connection, account) -> list[dict]:
    """An account's tie lines, newest first, for its page: for each statement
    balance with an anchor before it, that earlier balance, what the rows
    between add up to (as the change they make: money out lowers it), the
    balance stated and the difference, worked out as _check works it out,
    with money in and money out apart and the ids of the rows between.
    A supplied figure is the fact and is never checked; it is listed with
    `status` "your figure". The first statement balance is "not checked".
    On a shared account (shared.py) a figure is the household's part, listed
    with `part` and never tied against: each statement balance ties to the
    one before it."""
    kind = account["type"]
    is_shared = shared.is_shared(conn, account["id"])
    held = conn.execute(
        "SELECT date, amount, source FROM anchors WHERE account_id = ? ORDER BY date",
        (account["id"],),
    ).fetchall()
    lines = []
    earlier = None
    for anchor in held:
        line = {
            "date": anchor["date"],
            "source": anchor["source"],
            "label": SOURCE_LABELS[anchor["source"]],
            "closing_minor": anchor["amount"],
            "opening_minor": None,
            "opening_date": None,
            "rows": None,
            "rows_minor": None,
            "difference_minor": None,
            "in_minor": None,
            "out_minor": None,
            "row_ids": [],
        }
        if anchor["source"] == anchors.SUPPLIED and is_shared:
            # The household's part of a shared account: a figure beside the
            # statements, never tied to them nor they to it.
            line["status"] = "your figure"
            line["part"] = True
            lines.append(line)
            continue
        if anchor["source"] == anchors.SUPPLIED and earlier is not None and kind in OWN_ROW_KINDS:
            # A figure is the fact and is never checked; what the rows since
            # the balance before it moved is shown beside it, and what moved
            # with no row is the rest.
            count, between = _own_rows(conn, account["id"], earlier["date"], anchor["date"])
            line.update(
                status="your figure",
                opening_minor=earlier["amount"],
                opening_date=earlier["date"],
                rows=count,
                rows_minor=-between,
                **_own_rows_split(conn, account["id"], earlier["date"], anchor["date"]),
            )
            line["moved_minor"] = anchor["amount"] - (earlier["amount"] - between)
        elif anchor["source"] != anchors.STATEMENT or kind not in OWN_ROW_KINDS:
            line["status"] = "your figure" if anchor["source"] == anchors.SUPPLIED else "not_checked"
        elif earlier is None:
            line["status"] = "not_checked"
        else:
            count, between = _own_rows(conn, account["id"], earlier["date"], anchor["date"])
            difference = earlier["amount"] - between - anchor["amount"]
            line.update(
                opening_minor=earlier["amount"],
                opening_date=earlier["date"],
                rows=count,
                rows_minor=-between,
                difference_minor=difference,
                status="ties" if difference == 0 else "off",
                **_own_rows_split(conn, account["id"], earlier["date"], anchor["date"]),
            )
        lines.append(line)
        earlier = anchor
    return list(reversed(lines))
