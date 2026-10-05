"""The month check: do the month's rows account for the change in net worth?

For a month: net worth at the previous month-end, plus income, less spending
(with the loan interest worked out for the month), plus currency change, is
what net worth should be at the month-end. The balance sheet says what it is.
The difference is unexplained: a row missing or mislabelled, a transfer still
waiting for review, a figure that moved. Beside it stand the month's
transfers waiting on the review list and the lines whose check is not "ties",
so the operator knows where to look. Worked out on read; nothing is stored.

What counts, so that a month whose rows are all there and correctly labelled
comes out at exactly nothing unexplained:

    the rows      on the accounts net worth is made of: the household's own
                  bank accounts and cards, archived or not (ruling 6), that
                  count after the previous month-end up to this one: a row of
                  a printed statement on its day in that statement's period,
                  as the balances count it (balance_sheet.ROW_DAY, ruling 1).
    income        every income row on them.
    spending      spending and refund rows in the Household book on them,
                  plus the loan interest worked out for the month. A company's
                  costs paid from a household card are not household spending:
                  they move the company's balance, which is inside net worth,
                  so subtracting them again would count them twice.
    other rows    a transfer or card payoff between two household accounts,
                  a movement naming a loan, a holding, a company or a person,
                  moves money between two lines of net worth and is neither.
                  A transfer waiting for review counts as nothing, and so
                  shows up as unexplained beside the review list.

An account kept in another currency (the rupee account) is in the check when
its currency change can be worked out: it has a balance at the previous
month-end and a saved rate for both ends, so it is in net worth at both. Its
income and spending are valued at the month-end rate, as the sheet values its
balance; the two figures are split so that they add up exactly to the change
the rows make in the account's SGD value (as the loan interest's months add
up to the whole), never off by a cent of rounding. Otherwise the account is
left out of both sides: its value is taken out of net worth at each end, its
rows are not counted, and the check says so.

January 2026 has no previous month-end on the sheet: nothing is shown before
1 January 2026, so there is no net worth to start from, and no unexplained
figure is given.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import account_kind
import anchors
import balance_sheet
import book_type
import loan_interest
import money
import rates

CURRENCY = balance_sheet.TOTAL_CURRENCY
INCOME_FLOWS = ("income",)
SPENDING_FLOWS = ("expense", "refund")
# The statuses of a line's check that are not "ties".
NOT_TIES = ("off", "not_checked")


def _shown(minor: int | None) -> str | None:
    return None if minor is None else anchors.format_amount(minor, CURRENCY)


def _rows_by_account(conn: sqlite3.Connection, after: str, upto: str) -> dict:
    """The household's income and spending on each of the accounts net worth
    is made of, dated after one day up to another, in whole minor units of the
    account's currency, both as positive figures for money in and money out:
    {account id: (income, spending)}."""
    income = ", ".join("?" for _ in INCOME_FLOWS)
    spending = ", ".join("?" for _ in SPENDING_FLOWS)
    rows = conn.execute(
        "SELECT a.id, "
        f"COALESCE(SUM(CASE WHEN t.flow_type IN ({income}) THEN -t.amount_minor ELSE 0 END), 0), "
        f"COALESCE(SUM(CASE WHEN t.flow_type IN ({spending}) AND COALESCE(t.book, ?) = ? "
        "THEN t.amount_minor ELSE 0 END), 0) "
        "FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        "WHERE a.owner = ? AND a.type IN (?, ?) "
        f"AND {balance_sheet.ROW_DAY} > ? AND {balance_sheet.ROW_DAY} <= ? "
        "GROUP BY a.id",
        (
            *INCOME_FLOWS, *SPENDING_FLOWS, book_type.DEFAULT_BOOK, book_type.DEFAULT_BOOK,
            account_kind.HOUSEHOLD, *account_kind.STATEMENT_KINDS, after, upto,
        ),
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _line_values(shown: dict) -> dict:
    """Each line of a sheet by its account id."""
    return {line["account_id"]: line for section in shown["sections"] for line in section["lines"]}


def check(conn: sqlite3.Connection, month: str, end_sheet: dict, name_of=lambda name: name,
          waiting: dict | None = None) -> dict:
    """The month check for a month written YYYY-MM, given that month's
    balance sheet. `waiting` is the month's transfers waiting for review
    (count, and money out and money in apart, as positive figures), which
    stand beside the check; it is not part of the arithmetic."""
    as_at = balance_sheet.month_end(month)
    start = as_at.replace(day=1) - timedelta(days=1)
    after, upto = start.isoformat(), as_at.isoformat()

    by_account = _rows_by_account(conn, after, upto)
    currency_of = {
        r["id"]: r["currency"] or CURRENCY
        for r in conn.execute("SELECT id, currency FROM accounts")
    }

    income = sum(i for acct, (i, _) in by_account.items() if currency_of[acct] == CURRENCY)
    spending = sum(s for acct, (_, s) in by_account.items() if currency_of[acct] == CURRENCY)
    interest = loan_interest.month_figures(conn, month)["interest_minor"]
    spending += interest

    # Accounts in another currency: in the check at the month-end rate, or
    # left out of both sides.
    currency_change = 0
    left_out = []
    for line in end_sheet["currency_change"]["lines"]:
        acct = line["account_id"]
        held_income, held_spending = by_account.get(acct, (0, 0))
        if line["change_minor"] is None:
            left_out.append({"account_id": acct, "name": line["name"], "why": line["why"]})
            continue
        currency_change += line["change_minor"]
        rate = rates.on_or_before(conn, rates.pair_for(line["currency"]), upto)["value"]

        def value(balance: int) -> int:
            return money.convert_minor(balance, rate, line["currency"], CURRENCY)

        opening = line["opening_minor"]
        with_income = opening + held_income
        after_spending = with_income - held_spending
        income += value(with_income) - value(opening)
        spending += value(with_income) - value(after_spending)

    actual = end_sheet["net_worth_minor"]
    end_lines = _line_values(end_sheet)
    for entry in left_out:
        line = end_lines.get(entry["account_id"])
        if line is not None and line.get("in_total"):
            actual -= line["value_minor"]

    if start < balance_sheet.START:
        opening_worth = expected = unexplained = None
        why = (
            f"{as_at:%B %Y} is the first month on the balance sheet: there is no net worth "
            f"at {start.day} {start:%b %Y} to start from, so nothing is said to be unexplained."
        )
    else:
        start_sheet = balance_sheet.sheet(conn, f"{start:%Y-%m}", name_of)
        opening_worth = start_sheet["net_worth_minor"]
        start_lines = _line_values(start_sheet)
        for entry in left_out:
            line = start_lines.get(entry["account_id"])
            if line is not None and line.get("in_total"):
                opening_worth -= line["value_minor"]
        expected = opening_worth + income - spending + currency_change
        unexplained = actual - expected
        why = None

    not_tying = [
        {
            "account_id": line["account_id"],
            "name": line["name"],
            "status": line["check"]["status"],
            "difference_minor": line["check"]["difference_minor"],
            "currency": line["currency"],
            "date": line["rests_on"]["date"] if line["rests_on"] else None,
            "text": line["check"]["text"],
        }
        for section in end_sheet["sections"] for line in section["lines"]
        if line.get("check") and line["check"]["status"] in NOT_TIES
    ]

    waiting = waiting or {"count": 0, "out_count": 0, "out_minor": 0, "in_count": 0, "in_minor": 0}
    return {
        "month": month,
        "from": after,
        "to": upto,
        "currency": CURRENCY,
        "available": unexplained is not None,
        "why": why,
        "opening_minor": opening_worth,
        "opening": _shown(opening_worth),
        "income_minor": income,
        "income": _shown(income),
        "spending_minor": spending,
        "spending": _shown(spending),
        "interest_minor": interest,
        "interest": _shown(interest),
        "currency_change_minor": currency_change,
        "currency_change": _shown(currency_change),
        "expected_minor": expected,
        "expected": _shown(expected),
        "actual_minor": actual,
        "actual": _shown(actual),
        "unexplained_minor": unexplained,
        "unexplained": _shown(unexplained),
        "left_out": [{"name": e["name"], "why": e["why"]} for e in left_out],
        "note": (
            "Left out of both sides of the check: "
            + "; ".join(f"{e['name']} ({e['why']})" for e in left_out)
        ) if left_out else None,
        "review": {
            "count": waiting["count"],
            "out_count": waiting["out_count"],
            "out_minor": waiting["out_minor"],
            "out": _shown(waiting["out_minor"]),
            "in_count": waiting["in_count"],
            "in_minor": waiting["in_minor"],
            "in": _shown(waiting["in_minor"]),
        },
        "not_tying": not_tying,
    }
