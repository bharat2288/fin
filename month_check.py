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
                  a movement naming a loan, a holding or a company, moves
                  money between two lines of net worth and is neither. A
                  movement naming a person is neither too: what is expected or
                  placed on it moves Expect back's lines, and money left on it
                  to label shows as unexplained until it is labelled.
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

Expect back (personal-ledger 23): a row counts its own part as income or
spending, a let-go counts on
its day (a back item's rest as Household spending, a pay item's as income),
and Books' three sections are lines of net worth read by their keys. Each
foreign-currency item in the check has its own currency change (its open
amount at the start revalued) and the spread on the month's set-offs across
currencies (what the payment moved less what the item fell, at the month-end
rate); an item made or paid against from an account left out of the check
is money across its edge (`outside`); an item with no rate, or an opening
figure dated in the month, is left out of both sides (_expect_terms).

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
import books_expect
import card_balance
import expect_back
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
    {account id: (income, spending)}. A cardholder's account that is part of
    its card's one balance (card_balance.py) counts as that balance's account."""
    income = ", ".join("?" for _ in INCOME_FLOWS)
    spending = ", ".join("?" for _ in SPENDING_FLOWS)
    # A row counts its own part (expect_back.own_sql): its amount less what is
    # expected or placed on it.
    own = expect_back.own_sql("t")
    rows = conn.execute(
        "SELECT a.id, "
        f"COALESCE(SUM(CASE WHEN t.flow_type IN ({income}) THEN -{own} ELSE 0 END), 0), "
        f"COALESCE(SUM(CASE WHEN t.flow_type IN ({spending}) AND COALESCE(t.book, ?) = ? "
        f"THEN {own} ELSE 0 END), 0) "
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
    parts = card_balance.part_of_ids(conn)
    figures: dict = {}
    for acct, income_minor, spending_minor in rows:
        acct = parts.get(acct, acct)
        held_income, held_spending = figures.get(acct, (0, 0))
        figures[acct] = (held_income + income_minor, held_spending + spending_minor)
    return figures


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
    naming it (a loan, a holding, a company, a bank account with no
    statement), counted as that line counts it.
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
    # An account that is part of another's balance has no line of its own.
    for acct in sorted(a for a in in_check if a in lines):
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
        elif kind == "company":
            # Only a company that is a book has rows paid for it, as its line counts them.
            name = names[acct]
            is_book = name in book_type.company_books(conn)
            book = name if is_book else None
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


def _keyed_line(section: dict, line: dict) -> dict:
    """An expect-back line (books_expect.py) as the check reads a line: by its
    key, its value signed as net worth counts it (what is owed negative), in
    the total only when it has an S$ value."""
    value = line["sgd_minor"]
    return {
        "account_id": line["key"],
        "name": line["title"],
        "kind": section["key"],
        "currency": line["currency"],
        "balance_minor": line["open_minor"],
        "value_minor": None if value is None else (-value if section["owed"] else value),
        "in_total": value is not None,
        "left_out": books_expect.NO_RATE if value is None else None,
        "check": None,
        "rests_on": None,
    }


def _line_values(shown: dict, parts: dict | None = None) -> dict:
    """Each line of a sheet by its account id, less the lines of accounts
    that are part of another's balance; and each expect-back line in a
    section that is in the total, by its key ("expectation:<id>",
    "waiting:<set-off id>"), which those lines carry in place of an account."""
    lines = {
        line["account_id"]: line for section in shown["sections"] for line in section["lines"]
        if line["account_id"] not in (parts or {})
    }
    for section in shown.get("expect", {}).get("sections", []):
        if section["in_total"]:
            lines.update((line["key"], _keyed_line(section, line)) for line in section["lines"])
    return lines


def _split(lines: dict) -> tuple[dict, dict]:
    """A sheet's lines as _line_values gives them, apart: the accounts' (by
    account id) and expect back's (by key)."""
    keyed = {k: line for k, line in lines.items() if isinstance(k, str)}
    return {k: line for k, line in lines.items() if not isinstance(k, str)}, keyed


def _expect_terms(conn: sqlite3.Connection, after: str, upto: str, in_check: set, parts: dict,
                  end_keyed: dict, start_keyed: dict) -> dict:
    """The check's expect-back terms for the month after `after` up to `upto`
    (spec E5), the three Books sections being in net worth.

    Which items are in the check: every item is, unless it has no S$ value
    at an end where it is listed (no rate), it is in another currency with no
    rate at the month-end, or it is an opening figure dated in the month (as
    an account's first figure is): those are left out of both sides, and
    `out` names each, {key: {"name", "why"}}.

    Every event of the month moves an item's line and, but for a let-go, the
    account of the row it is on. Valued at the month-end rate:
        made        the row's expectation, on the row's day: a back item
                    rises with the row's money out, a pay item with its money in
        set off     on its payment row's day: the payment's money moves its
                    account, the item falls (or, before its expectation's day,
                    the payment waits: Waiting for its row rises)
        found its   on its expectation's day, a waiting set-off leaves
        row         Waiting for its row and is set against the item
        let go      on its own day: the item falls, and a back item's rest is
                    spending (Household), a pay item's income
    Where every side of an event is in the check, the rows and lines it
    moves cancel, or leave the spread of a set-off across currencies (what
    the payment moved less what the item fell, both at the month-end rate):
    currency change. Where a side is left out, what the sides in the check
    moved is money across the check's edge: `outside`.

    Each foreign-currency item in the check has its line of currency change:
    its open amount at the start valued at the end rate less at the start
    rate, plus its spread; and, as the sheet values each line on its own,
    the cent of rounding between its events and its two values joins that
    spread, so the item ties to the cent.
    """
    total = balance_sheet.TOTAL_CURRENCY
    value_at_end = books_expect.valuer(conn, upto)

    def v(amount: int, currency: str) -> int | None:
        return value_at_end(amount, currency)[0]

    day = balance_sheet.ROW_DAY
    items = {
        r["id"]: r for r in conn.execute(
            f"SELECT e.id, e.direction, e.currency, e.amount_minor, e.row_id, s.account_id, p.name AS person_name, "
            f"COALESCE({day}, e.opening_date) AS day FROM expectation e "
            "LEFT JOIN transactions t ON t.id = e.row_id LEFT JOIN statements s ON s.id = t.statement_id "
            "LEFT JOIN accounts p ON p.id = e.person_id")
    }
    keys = set(end_keyed) | set(start_keyed)
    out: dict = {}
    for key in keys:
        end, start = end_keyed.get(key), start_keyed.get(key)
        line = end or start
        if any(x is not None and not x["in_total"] for x in (end, start)):
            out[key] = {"name": line["name"], "why": books_expect.NO_RATE}
        elif line["currency"] != total and v(0, line["currency"]) is None:
            out[key] = {"name": line["name"], "why": f"{books_expect.NO_RATE} on {upto}"}
    for x in items.values():
        if x["row_id"] is None and after < x["day"] <= upto:
            out[f"expectation:{x['id']}"] = {"name": f"Opening figure · {x['person_name']}",
                                             "why": f"opening figure on {x['day']}"}

    def held(key: str) -> bool:
        return key not in out

    def row_in(account_id) -> bool:
        return parts.get(account_id, account_id) in in_check

    outside = 0
    spread: dict[str, int] = {}
    moved: dict[str, int] = {}   # what the month's events moved each item's line, valued

    def event(sides: list, key: str) -> None:
        """One event's sides: (in the check, what it moved net worth by)."""
        nonlocal outside
        counted = sum(amount for inside, amount in sides if inside and amount is not None)
        if all(inside for inside, _ in sides):
            spread[key] = spread.get(key, 0) + counted
        else:
            outside += counted

    def line_moved(key: str, amount: int | None) -> tuple:
        if amount is not None:
            moved[key] = moved.get(key, 0) + amount
        return held(key), amount

    def sign(x) -> int:
        return 1 if x["direction"] == expect_back.BACK else -1

    for x in items.values():
        if x["row_id"] is not None and after < x["day"] <= upto:
            key = f"expectation:{x['id']}"
            worth = v(x["amount_minor"], x["currency"])
            event([(row_in(x["account_id"]), None if worth is None else -sign(x) * worth),
                   line_moved(key, None if worth is None else sign(x) * worth)], key)

    for so in conn.execute(
        f"SELECT so.id, so.expectation_id, so.amount_minor, so.item_amount_minor, t.amount_minor AS row_amount, "
        f"COALESCE(a.currency, ?) AS currency, s.account_id, {day} AS day FROM set_off so "
        "JOIN transactions t ON t.id = so.payment_row_id JOIN statements s ON s.id = t.statement_id "
        "JOIN accounts a ON a.id = s.account_id",
        (total,),
    ):
        x = items.get(so["expectation_id"])
        waits = x is None or x["day"] > so["day"]
        paid = v(so["amount_minor"], so["currency"])
        into = 1 if so["row_amount"] < 0 else -1  # money in raises its account
        waiting = f"waiting:{so['id']}"
        if after < so["day"] <= upto:
            payment = (row_in(so["account_id"]), None if paid is None else into * paid)
            if waits:
                event([payment, line_moved(waiting, None if paid is None else -paid)], waiting)
            else:
                key = f"expectation:{x['id']}"
                fell = v(so["item_amount_minor"], x["currency"])
                event([payment, line_moved(key, None if fell is None else -sign(x) * fell)], key)
        if x is not None and waits and after < x["day"] <= upto:
            key = f"expectation:{x['id']}"
            fell = v(so["item_amount_minor"], x["currency"])
            event([line_moved(waiting, None if paid is None else paid),
                   line_moved(key, None if fell is None else -sign(x) * fell)], key)

    income = spending = 0
    for g in conn.execute(
        f"SELECT g.*, l.expectation_id FROM ({expect_back.let_go_sql()}) g "
        "JOIN let_go l ON l.id = g.let_go_id WHERE g.day > ? AND g.day <= ?",
        (after, upto),
    ):
        key = f"expectation:{g['expectation_id']}"
        if not held(key):
            continue
        worth = v(abs(g["amount_minor"]), g["currency"])
        if worth is None:
            continue
        if g["flow_type"] == "expense":
            if (g["book"] or book_type.DEFAULT_BOOK) == book_type.DEFAULT_BOOK:
                spending += worth
            line_moved(key, -worth)
        else:
            income += worth
            line_moved(key, worth)

    lines = []
    for key in sorted(keys | set(spread)):
        if not held(key):
            continue
        end, start = end_keyed.get(key), start_keyed.get(key)
        line = end or start
        revaluation = 0
        if line is not None and line["currency"] != total:
            if start is not None:
                revaluation = _signed(start, v(start["balance_minor"], line["currency"])) - start["value_minor"]
            # The cent of rounding between the line's two values and its events.
            change = (end["value_minor"] if end else 0) - (start["value_minor"] if start else 0)
            spread[key] = spread.get(key, 0) + change - revaluation - moved.get(key, 0)
        elif not spread.get(key):
            continue
        lines.append({
            "account_id": None,
            "key": key,
            "name": line["name"] if line is not None else key,
            "revaluation_minor": revaluation,
            "spread_minor": spread.get(key, 0),
            "spread": _shown(spread.get(key, 0)),
        })
    currency_change = sum(x["revaluation_minor"] + x["spread_minor"] for x in lines)
    return {"income": income, "spending": spending, "currency_change": currency_change,
            "outside": outside, "lines": lines, "out": out}


def _signed(line: dict, value: int) -> int:
    """A value as an expect-back line counts in net worth: owed negative."""
    return -value if line["kind"] != "expected_back" else value


def check(conn: sqlite3.Connection, month: str, end_sheet: dict, name_of=lambda name: name,
          waiting: dict | None = None) -> dict:
    """The month check for a month written YYYY-MM, given that month's
    balance sheet. `waiting` is the month's transfers waiting for review
    (count, and money out and money in apart, as positive figures), which
    stand beside the check; it is not part of the arithmetic."""
    as_at = balance_sheet.month_end(month)
    start = as_at.replace(day=1) - timedelta(days=1)
    after, upto = start.isoformat(), as_at.isoformat()

    # A cardholder's account that is part of its card's one balance is not a
    # line of its own here: its rows are the balance account's
    # (card_balance.py), in the check or out of it with that account.
    parts = card_balance.part_of_ids(conn)
    # Expect back's lines (keyed, books_expect.py) are read apart from the
    # accounts': they carry a key, not an account.
    end_lines, end_keyed = _split(_line_values(end_sheet, parts))
    start_keyed: dict = {}
    if start < balance_sheet.START:
        start_sheet = start_lines = None
    else:
        start_sheet = balance_sheet.sheet(conn, f"{start:%Y-%m}", name_of)
        start_lines, start_keyed = _split(_line_values(start_sheet, parts))

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
    # An untracked account has no line on the sheet: money moved to it leaves
    # the check the same way.
    with_parts = in_check | {part for part, whole in parts.items() if whole in in_check}
    untracked = {r[0] for r in conn.execute(
        f"SELECT id FROM accounts WHERE type IN ({', '.join('?' for _ in account_kind.UNTRACKED_KINDS)})",
        account_kind.UNTRACKED_KINDS)}
    outside, touched = _crossing(conn, after, upto, with_parts, out_of_check | untracked, end_lines)
    touched = {parts.get(acct, acct) for acct in touched}

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

    # Expect back (spec E5), its sections in net worth: spending and income
    # with the let-gos, money moved across the check's edge by its items, and
    # each foreign-currency item's currency change and spread.
    keyed_out: dict = {}
    if start_sheet is not None:
        terms = _expect_terms(conn, after, upto, with_parts, parts, end_keyed, start_keyed)
        income += terms["income"]
        spending += terms["spending"]
        currency_change += terms["currency_change"]
        outside += terms["outside"]
        spreads += terms["lines"]
        keyed_out = terms["out"]

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
    # Expect back's items left out of both sides, and why.
    for key in sorted(keyed_out, key=lambda k: (keyed_out[k]["name"], k)):
        left_out.append({"account_id": key, **keyed_out[key]})

    actual = end_sheet["net_worth_minor"]
    for acct in out_of_check:
        line = end_lines.get(acct)
        if line is not None and line.get("in_total"):
            actual -= line["value_minor"]
    for key in keyed_out:
        line = end_keyed.get(key)
        if line is not None and line["in_total"]:
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
        for key in keyed_out:
            line = start_keyed.get(key)
            if line is not None and line["in_total"]:
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
        "left_out": [{"account_id": e["account_id"], "name": e["name"], "why": e["why"]} for e in left_out],
        # How many accounts are in the check (in net worth at both ends): the
        # page says "adds up for the N accounts in the check" when some are
        # left out, and a plain tick only when none is.
        "in_check_count": len(in_check),
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
