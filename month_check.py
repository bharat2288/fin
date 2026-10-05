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

Only the accounts in net worth at both month-ends are in the check. One with
no balance at either end (a card with no statement balance, an account before
its first figure, one in a currency with no saved rate) is left out of both
sides: its value is taken out of net worth where it is in it, its rows are not
counted, and the check names it. Money moved between an account in the check
and one left out (paying off such a card, a row on such a card in a company's
book) changes the checked net worth and is neither income nor spending: it is
stated as `outside`, money into the check positive.

Currency change in the check is the sheet's (each such account's opening
balance revalued) plus the spread on the month's conversions between it and
the accounts kept in SGD (ruling 5): the rupees a conversion moved, valued at
the month-end rate, less the SGD the other side moved at the bank's rate. A
conversion is counted when its two legs name each other's account.

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
import flow
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


# The flows of a row that move money between two lines of net worth, and so
# cross the check's edge when one of the two is left out of it.
MOVE_FLOWS = ("transfer", "payment", "movement")


def _why_out(line: dict | None, on: str) -> str:
    """Why a line is not in net worth on a day."""
    if line is None or line["balance_minor"] is None:
        return f"no balance on {on}"
    return line.get("left_out") or f"not in net worth on {on}"


def _crossing(conn: sqlite3.Connection, after: str, upto: str, in_check: set,
              out_of_check: set, lines: dict) -> tuple[int, set]:
    """Money moved in the month between the accounts in the check and the
    lines of the sheet left out of it, in whole cents, positive for money
    into the check; and the accounts left out that such a move touched.

    Out of the check: a transfer, payment or movement on a bank account or
    card in the check that names a line left out as its other side (a card
    with no statement balance being paid off). Into it: a row on an account
    left out that moves a line in the check which moves only by the rows
    naming it (a loan, a holding, a company or a person, a bank account with
    no statement), counted as that line counts it.
    """
    window = f"{balance_sheet.ROW_DAY} > ? AND {balance_sheet.ROW_DAY} <= ?"
    total = 0
    touched: set = set()
    if not in_check:
        return 0, touched
    checked = ", ".join("?" for _ in in_check)
    moves = ", ".join("?" for _ in MOVE_FLOWS)
    if out_of_check:
        left = ", ".join("?" for _ in out_of_check)
        for other, amount in conn.execute(
            "SELECT t.other_side_id, t.amount_minor FROM transactions t "
            "JOIN statements s ON t.statement_id = s.id "
            f"WHERE s.account_id IN ({checked}) AND t.flow_type IN ({moves}) "
            f"AND t.other_side_id IN ({left}) AND {window}",
            (*in_check, *MOVE_FLOWS, *out_of_check, after, upto),
        ):
            # Money out of the checked account (positive) leaves the check.
            total -= amount
            touched.add(other)

    def entering(condition: str, params: tuple) -> None:
        nonlocal total
        for source, amount in conn.execute(
            "SELECT s.account_id, t.amount_minor FROM transactions t "
            "JOIN statements s ON t.statement_id = s.id "
            "JOIN accounts a ON a.id = s.account_id "
            f"WHERE s.account_id NOT IN ({checked}) AND {window} AND ({condition})",
            (*in_check, after, upto, *params),
        ):
            # Money out of the left-out account (positive) moves the line up.
            total += amount
            touched.add(source)

    names = dict(conn.execute("SELECT id, name FROM accounts"))
    for acct in sorted(in_check):
        line = lines[acct]
        kind, currency = line["kind"], line["currency"]
        if kind in ("loan", "holding"):
            entering("t.flow_type = ? AND t.other_side_id = ?", (flow.MOVEMENT, acct))
        elif kind == "bank" and conn.execute(
            "SELECT 1 FROM statements WHERE account_id = ? LIMIT 1", (acct,)
        ).fetchone() is None:
            flows = ", ".join("?" for _ in balance_sheet.INTO_UNSTATED_FLOWS)
            entering(
                f"t.flow_type IN ({flows}) AND t.other_side_id = ? AND a.owner = ? "
                "AND COALESCE(a.currency, ?) = ?",
                (*balance_sheet.INTO_UNSTATED_FLOWS, acct, account_kind.HOUSEHOLD, CURRENCY, currency),
            )
        elif kind in ("company", "person"):
            # Only a company that is a book has rows paid for it, as its line counts them.
            name = names[acct]
            is_book = name in book_type.BOOK_NAMES and name != book_type.DEFAULT_BOOK
            book = name if kind == "company" and is_book else None
            paid = ", ".join("?" for _ in balance_sheet.PAID_FOR_FLOWS)
            entering(
                "a.owner = ? AND COALESCE(a.currency, ?) = ? AND ("
                f"(t.flow_type IN ({paid}) AND t.book = ?) OR (t.flow_type = ? AND t.other_side_id = ?))",
                (account_kind.HOUSEHOLD, CURRENCY, currency, *balance_sheet.PAID_FOR_FLOWS, book,
                 flow.MOVEMENT, acct),
            )
    return total, touched


def _conversions(conn: sqlite3.Connection, acct: int, after: str, upto: str,
                 in_check: set, currency_of: dict) -> tuple[int, int]:
    """The month's conversions between an account kept in another currency
    and the accounts in the check kept in the currency of the totals: what
    they moved on this account, in its minor units (positive is money out of
    it), and what they moved on the other side, in cents (positive is money
    out of the other account, into this one).

    A conversion is two rows that name each other's account as their other
    side (each leg labelled, on the review list or at import). The legs with
    one other account are taken together; where only one side names the
    other, nothing is taken, and the conversion stays unexplained in full.
    """
    window = f"{balance_sheet.ROW_DAY} > ? AND {balance_sheet.ROW_DAY} <= ?"
    moves = ", ".join("?" for _ in MOVE_FLOWS)
    here = dict(conn.execute(
        "SELECT t.other_side_id, SUM(t.amount_minor) FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        f"WHERE s.account_id = ? AND t.flow_type IN ({moves}) AND t.other_side_id IS NOT NULL "
        f"AND {window} GROUP BY t.other_side_id",
        (acct, *MOVE_FLOWS, after, upto),
    ))
    there = dict(conn.execute(
        "SELECT s.account_id, SUM(t.amount_minor) FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        f"WHERE t.other_side_id = ? AND s.account_id != ? AND t.flow_type IN ({moves}) "
        f"AND {window} GROUP BY s.account_id",
        (acct, acct, *MOVE_FLOWS, after, upto),
    ))
    paired = [
        other for other in here
        if other in there and other in in_check and currency_of.get(other) == CURRENCY
    ]
    return sum(here[o] for o in paired), sum(there[o] for o in paired)


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

    end_lines = _line_values(end_sheet)
    if start < balance_sheet.START:
        start_sheet = start_lines = None
    else:
        start_sheet = balance_sheet.sheet(conn, f"{start:%Y-%m}", name_of)
        start_lines = _line_values(start_sheet)

    # The accounts in the check: those in net worth at both ends (M8). An
    # account with no balance at one end (a card with no statement balance,
    # an account before its first figure, one in a currency with no saved
    # rate) is left out of both sides: its rows are not counted and its value
    # is taken out of net worth at the end where it is in it.
    changes = {line["account_id"]: line for line in end_sheet["currency_change"]["lines"]}

    def in_total(lines: dict | None, acct: int) -> bool:
        return lines is None or bool(lines.get(acct, {}).get("in_total"))

    in_check = {
        acct for acct in end_lines
        if in_total(end_lines, acct) and in_total(start_lines, acct)
        and (acct not in changes or changes[acct]["change_minor"] is not None)
    }
    out_of_check = (set(end_lines) | set(start_lines or {})) - in_check

    by_account = {
        acct: figures for acct, figures in _rows_by_account(conn, after, upto).items()
        if acct in in_check
    }
    currency_of = {
        r["id"]: r["currency"] or CURRENCY
        for r in conn.execute("SELECT id, currency FROM accounts")
    }

    income = sum(i for acct, (i, _) in by_account.items() if currency_of[acct] == CURRENCY)
    spending = sum(s for acct, (_, s) in by_account.items() if currency_of[acct] == CURRENCY)
    interest = sum(
        loan_interest.interest_between(conn, acct, after, upto)
        for acct, line in end_lines.items()
        if line["kind"] == loan_interest.LOAN and acct in in_check
    )
    spending += interest

    # Money moved between an account in the check and one left out of it:
    # it changes the checked net worth and is neither income nor spending.
    outside, touched = _crossing(conn, after, upto, in_check, out_of_check, end_lines)

    # Accounts in another currency: in the check at the month-end rate, or
    # left out of both sides.
    currency_change = 0
    spreads = []
    for line in end_sheet["currency_change"]["lines"]:
        acct = line["account_id"]
        if acct not in in_check:
            continue
        held_income, held_spending = by_account.get(acct, (0, 0))
        currency_change += line["change_minor"]
        rate = rates.on_or_before(conn, rates.pair_for(line["currency"]), upto)["value"]

        def value(balance: int) -> int:
            return money.convert_minor(balance, rate, line["currency"], CURRENCY)

        opening = line["opening_minor"]
        with_income = opening + held_income
        after_spending = with_income - held_spending
        income += value(with_income) - value(opening)
        spending += value(with_income) - value(after_spending)

        # Conversions to and from accounts in the currency of the totals
        # (ruling 5): what the rupee side moved, valued at the month-end
        # rate, less what the other side moved at the bank's rate, is the
        # spread, and it is currency change.
        moved, paid = _conversions(conn, acct, after, upto, in_check, currency_of)
        spread = value(after_spending - moved) - value(after_spending) - paid
        currency_change += spread
        spreads.append({
            "account_id": acct,
            "name": line["name"],
            "revaluation_minor": line["change_minor"],
            "spread_minor": spread,
            "spread": _shown(spread),
        })

    # What is left out, and why: every account in another currency whose
    # change has no figure, every account in net worth at one end only, and
    # every other account left out that the month's rows touch.
    with_rows = set(_rows_by_account(conn, after, upto)) | touched
    left_out = []
    for acct in sorted(out_of_check, key=lambda a: (end_lines.get(a) or start_lines[a])["name"]):
        end_line = end_lines.get(acct)
        start_line = (start_lines or {}).get(acct)
        at_one_end = bool(end_line and end_line["in_total"]) != bool(start_line and start_line["in_total"])
        if acct in changes and changes[acct]["change_minor"] is None:
            why = changes[acct]["why"]
        elif not (at_one_end or acct in with_rows):
            continue
        elif end_line is None or not end_line["in_total"]:
            why = _why_out(end_line, upto)
        else:
            why = _why_out(start_line, after)
        name = (end_line or start_line)["name"]
        left_out.append({"account_id": acct, "name": name, "why": why})

    actual = end_sheet["net_worth_minor"]
    for acct in out_of_check:
        line = end_lines.get(acct)
        if line is not None and line.get("in_total"):
            actual -= line["value_minor"]

    if start_sheet is None:
        opening_worth = expected = unexplained = None
        why = (
            f"{as_at:%B %Y} is the first month on the balance sheet: there is no net worth "
            f"at {start.day} {start:%b %Y} to start from, so nothing is said to be unexplained."
        )
    else:
        opening_worth = start_sheet["net_worth_minor"]
        for acct in out_of_check:
            line = start_lines.get(acct)
            if line is not None and line.get("in_total"):
                opening_worth -= line["value_minor"]
        expected = opening_worth + income - spending + currency_change + outside
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
        # Each account in another currency's part of the currency change:
        # the revaluation of its opening balance and the spread on its
        # conversions in the month.
        "currency_change_lines": spreads,
        "outside_minor": outside,
        "outside": _shown(outside),
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
