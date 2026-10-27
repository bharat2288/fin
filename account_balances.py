"""An account's balance at each month end, and its money in and out per month
(fin-surfaces 30; spec 15, Server S4): the series the account page draws.

    balance    balance_sheet.balance_on, unchanged, at each calendar month end
               (calendar.monthrange, not balance_sheet.month_end, which refuses
               a month before the sheet starts). None before the account's
               first anchor: a gap, never 0. None for a month that ends before
               the balance sheet starts (1 Jan 2026), even when an earlier
               anchor exists, with a note saying so (case 56).
    in, out    every row the balance moves by, transfers included, as positive
               amounts: in is money in (negative rows), out is money out. A row
               counts in the month of the day the balance counts it on
               (balance_sheet.ROW_DAY), so the month before's balance plus in
               less out is the month's balance whenever no anchor falls between
               them. A month with no rows is 0 and 0.
    split card A cardholder's account that is part of a card held as one bill,
               and the card's balance account itself, chart the whole card
               (card_balance.part_of_ids and members): its one balance, and the
               rows of every cardholder's account. The answer says so.
    shared     (shared.py) the household's part: the balance rests on a figure
               supplied, and in and out leave out the rows that are not ours.
    no rows    an account whose balance moves by rows on other accounts (a
    of its own company, a loan, a holding, a bank holding no statement): in
               and out are the rows its balance moves by
               (balance_sheet.balance_rows), each from the account's side, so
               a row on another account that is money out of it is money in
               here. A company needs no figure: its balance is its line's at
               each month end (balance_sheet.company_balance_on).

The month-end value in S$ (S2), missing months and estimates (S3), and the
month under way are not decided here: the /balances route composes them.
Everything is worked out on read; nothing is stored.
"""

from __future__ import annotations

import calendar
import sqlite3

import account_kind
import anchors
import balance_sheet
import card_balance
import depreciation
import shared

START_NOTE = "Balances start on 1 Jan 2026, as on the balance sheet"
NO_BALANCE = "No balance yet: import a statement or add an anchor"
SHARED_NO_BALANCE = ("Shared: no balance yet. Add a figure for the household's part; "
                     "its statements are the whole account's")
UNTRACKED_NOTE = "Untracked: fin holds no statement or figure for it; money moved here leaves fin's sight"


def _month_end(month: str) -> str:
    year, number = int(month[:4]), int(month[5:7])
    return f"{month}-{calendar.monthrange(year, number)[1]:02d}"


def _day_before(month: str) -> str:
    """The last day of the month before 'YYYY-MM'."""
    year, number = int(month[:4]), int(month[5:7])
    year, number = (year - 1, 12) if number == 1 else (year, number - 1)
    return _month_end(f"{year:04d}-{number:02d}")


def _in_and_out(conn: sqlite3.Connection, members: list[int], months: list[str],
                ours_only: bool = False) -> dict:
    """{month: (in, out)} over the rows of these accounts, by the month each
    row counts in, in one grouped read; only the household's rows where
    `ours_only` (a shared account's part)."""
    if not months:
        return {}
    held = ", ".join("?" for _ in members)
    rows = conn.execute(
        "SELECT substr(day, 1, 7), "
        "COALESCE(SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END), 0), "
        "COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 0) "
        f"FROM (SELECT {balance_sheet.ROW_DAY} AS day, t.amount_minor AS amount "
        "FROM transactions t JOIN statements s ON t.statement_id = s.id "
        f"WHERE s.account_id IN ({held})"
        + (f" AND {balance_sheet.OURS_ONLY}" if ours_only else "") + ") "
        "WHERE day > ? AND day <= ? GROUP BY 1",
        (*members, _day_before(min(months)), _month_end(max(months))),
    ).fetchall()
    return {month: (money_in, money_out) for month, money_in, money_out in rows}


def _in_and_out_of(conn: sqlite3.Connection, account, months: list[str]) -> dict:
    """{month: (in, out)} over the rows the account's balance moves by
    (balance_sheet.balance_rows), each signed from its side: a row of its own
    as it stands, a row on another account the other way round."""
    if not months:
        return {}
    where, params = balance_sheet.balance_rows(conn, account)
    rows = conn.execute(
        "SELECT substr(day, 1, 7), "
        "COALESCE(SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END), 0), "
        "COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 0) "
        f"FROM (SELECT {balance_sheet.ROW_DAY} AS day, "
        "CASE WHEN s.account_id = ? THEN t.amount_minor ELSE -t.amount_minor END AS amount "
        f"FROM transactions t JOIN statements s ON t.statement_id = s.id WHERE {where}) "
        "WHERE day > ? AND day <= ? GROUP BY 1",
        (account["id"], *params, _day_before(min(months)), _month_end(max(months))),
    ).fetchall()
    return {month: (money_in, money_out) for month, money_in, money_out in rows}


def own_rows(conn: sqlite3.Connection, account) -> bool:
    """Whether the account's balance moves by its own statements' rows (and
    on a split card its parts'), as balance_sheet._moved reads it: a card,
    or a bank holding a statement."""
    return account["type"] in balance_sheet.OWN_ROW_KINDS and (
        account["type"] != "bank" or conn.execute(
            "SELECT 1 FROM statements WHERE account_id = ? LIMIT 1", (account["id"],)
        ).fetchone() is not None)


def _card(conn: sqlite3.Connection, members: list[int]) -> dict | None:
    """What a split card's chart says it is: its parts, named by their last
    four (or by name when an account holds none)."""
    if len(members) < 2:
        return None
    held = ", ".join("?" for _ in members)
    found = {
        r[0]: {"id": r[0], "name": r[1], "last_four": r[2]}
        for r in conn.execute(
            f"SELECT id, name, last_four FROM accounts WHERE id IN ({held})", members
        )
    }
    parts = [found[m] for m in members]
    labels = [p["last_four"] or p["name"] for p in parts]
    return {
        "whole_id": members[0],
        "parts": parts,
        "text": "the whole card: parts " + ", ".join(labels[:-1]) + " and " + labels[-1],
    }


def balances(conn: sqlite3.Connection, account, months: list[str]) -> dict:
    """The account's month-end balance and money in and out for each month
    ('YYYY-MM', in the order given). `account` is its row (id, type,
    currency), as balance_on takes it.

    Returns {charted_id, card, no_balance, months}: the account whose balance
    is charted (the whole card's for a split card), the card's parts and its
    words (None for an account on no split card), the words that replace the
    line when the charted account has no anchor at all (else None), and per
    month {month, end, balance, in, out, note}, in whole minor units of the
    account's currency.
    """
    whole = card_balance.part_of_ids(conn).get(account["id"], account["id"])
    charted = conn.execute(
        "SELECT id, name, type, currency FROM accounts WHERE id = ?", (whole,)
    ).fetchone()
    members = card_balance.members(conn, whole)
    is_shared = shared.is_shared(conn, whole)
    # A shared account's balance rests only on a figure for the household's part.
    anchored = conn.execute(
        "SELECT 1 FROM anchors WHERE account_id = ?" + (" AND source = ?" if is_shared else "") + " LIMIT 1",
        (whole, *((anchors.SUPPLIED,) if is_shared else ())),
    ).fetchone() is not None
    # A holding with depreciation terms rests on them, figure or none.
    anchored = anchored or (charted["type"] == depreciation.HOLDING
                            and depreciation.held(conn, whole) is not None)
    # A company needs no figure: counted from the start without one.
    company = charted["type"] in balance_sheet.COUNTERPARTIES[2]
    anchored = anchored or company
    flows = (_in_and_out(conn, members, months, ours_only=is_shared) if own_rows(conn, charted)
             else _in_and_out_of(conn, charted, months))
    start = balance_sheet.START.isoformat()

    series = []
    for month in months:
        end = _month_end(month)
        before_start = end < start
        money_in, money_out = flows.get(month, (0, 0))
        series.append({
            "month": month,
            "end": end,
            "balance": None if before_start
            else balance_sheet.company_balance_on(conn, charted, end) if company
            else balance_sheet.balance_on(conn, charted, end),
            "in": money_in,
            "out": money_out,
            "note": START_NOTE if before_start else None,
        })
    return {
        "charted_id": whole,
        "card": _card(conn, members),
        "shared": is_shared,
        "no_balance": None if anchored
        else SHARED_NO_BALANCE if is_shared
        else UNTRACKED_NOTE if charted["type"] in account_kind.UNTRACKED_KINDS else NO_BALANCE,
        "months": series,
    }
