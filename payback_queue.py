"""E6, the Queue's paybacks (personal-ledger 23, ticket 41): one read for the
Money in lane.

For each money-in row still waiting for its label (flow `review`) that has
something left to place and was not set aside as Not a payback, the open back
expectations it could be paying back. A candidate is

  - in the payment's currency (suggesting across currencies is out of scope;
    Back for... still lists those items);
  - dated (its row's date, or an opening figure's date) on or before the
    payment's day and within WINDOW_DAYS of it: the window reads the row's
    date, not the day it was marked (case 27);
  - open by at least what the payment has left to place;
  - named for someone the payment's text matches (a learnt sender wording, or
    the person's name as a word in the text), or named for no one. An item
    whose names do not match the sender is never a candidate (case 33a).

Ranking, as ruled (spec 23, Rulings 9): among the named fits, those whose open
amount equals the payment are offered alone; otherwise every named fit ties
and all are offered, oldest first. Items with no names are offered only when
no named item fits, by the same rule. Each suggestion carries its reasons as
words ('Jay is named on it', '8 days after', 'S$ 120 ≤ S$ 200 open'); the
page only draws them.

Waiting set-offs (ticket 42): money in left waiting for its row is offered the
rows that have come in since, as waiting_offers below says.

One read for the lane whatever its size (case 55): the payments, the open
items, their names, the learnt wordings and the waiting set-offs with their
rows are five queries, matched here.
Nothing here writes. Amounts are whole minor units of the account's currency.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import book_type
import expect_back
import setoff_api

# How many days after an item's date a payment may still be suggested for it.
WINDOW_DAYS = 90
NOT_A_PAYBACK = "payback"

# The money-in rows the lane looks at: waiting for their label, with what each
# has left to place (its own part), not set aside as Not a payback. The
# eligibility columns are read so expect_back's one rule decides each row,
# the book as it reads it (expect_back.BOOK_SQL: the merchant's where the row
# has none).
_PAYMENTS = f"""
    SELECT t.id, t.date, t.description, t.amount_minor, t.flow_type, t.other_side_id,
           {expect_back.BOOK_SQL} AS book, s.account_id, a.name AS account_name, a.owner,
           COALESCE(a.currency, 'SGD') AS currency, o.type AS other_kind, rp.own_minor
    FROM transactions t
    JOIN statements s ON s.id = t.statement_id
    JOIN accounts a ON a.id = s.account_id
    LEFT JOIN services sv ON sv.id = t.service_id
    LEFT JOIN accounts o ON o.id = t.other_side_id
    JOIN row_parts rp ON rp.row_id = t.id
    WHERE t.amount_minor < 0 AND t.flow_type = 'review' AND rp.own_minor != 0
      AND NOT EXISTS (SELECT 1 FROM dismissal d WHERE d.row_id = t.id AND d.kind = 'payback')
    ORDER BY t.date DESC, t.id DESC
"""


def _day(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def _title(item: dict) -> str:
    """How an item is called (as Back for... calls it): an opening figure by its
    person, a converted loan by whom it was to, otherwise its row's text."""
    if item["opening"] is not None:
        return f"Opening figure · {item['opening']['person']['name']}"
    if item["source"] == expect_back.CONVERTED and len(item["names"]) == 1:
        return f"Loan to {item['names'][0]['name']}"
    return item["row"]["description"]


def _days_words(days: int) -> str:
    if days == 0:
        return "the same day"
    return "1 day after" if days == 1 else f"{days} days after"


def _amount_words(left: int, open_: int, currency: str) -> str:
    short = expect_back.short
    if left == open_:
        return f"exactly the {short(open_, currency)} open"
    return f"{short(left, currency)} ≤ {short(open_, currency)} open"


def _named_words(names: list[str]) -> str:
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"{listed} {'is' if len(names) == 1 else 'are'} named on it"


def _sender(text: str, wordings: list[tuple[int, str]]) -> set[int]:
    """The people a learnt wording in the payment's text names."""
    return {person_id for person_id, words in wordings if expect_back._word(words).search(text)}


def _ranked(fits: list[tuple[dict, list[str]]], left: int) -> list[tuple[dict, list[str]]]:
    """Equal open amounts alone, else every fit, oldest first."""
    equal = [f for f in fits if f[0]["open_minor"] == left]
    chosen = equal or fits
    return sorted(chosen, key=lambda f: (f[0]["day"], f[0]["id"]))


def _suggestion(item: dict, matched: list[str], days: int, left: int) -> dict:
    cur = item["currency"]
    reasons = [_named_words(matched)] if matched else []
    reasons += [_days_words(days), _amount_words(left, item["open_minor"], cur)]
    if not matched:
        reasons.append("no names on it")
    return {"expectation_id": item["id"], "title": _title(item), "date": item["day"],
            "open_minor": item["open_minor"], "currency": cur, "reasons": reasons}


def suggestions_for(payment: dict, items: list[dict], wordings: list[tuple[int, str]]) -> list[dict]:
    """The suggestions for one payment from the open back items (setoff_api's
    open-list shape), ranked as the module says."""
    paid_on = _day(payment["date"])
    left = abs(payment["own_minor"])
    if paid_on is None or left == 0:
        return []
    text = (payment["description"] or "").upper()
    learnt = _sender(text, wordings)
    named, unnamed = [], []
    for item in items:
        if item["currency"] != payment["currency"] or item["open_minor"] < left:
            continue
        on = _day(item["day"])
        if on is None:
            continue
        days = (paid_on - on).days
        if not 0 <= days <= WINDOW_DAYS:
            continue
        if not item["names"]:
            unnamed.append((item, [], days))
            continue
        matched = [n["name"] for n in item["names"]
                   if n["account_id"] in learnt or expect_back._word(n["name"]).search(text)]
        if matched:
            named.append((item, matched, days))
    pool = named or unnamed
    by_id = {f[0]["id"]: f for f in pool}
    chosen = _ranked([(f[0], f[1]) for f in pool], left)
    return [_suggestion(item, matched, by_id[item["id"]][2], left) for item, matched in chosen]


def _shown(p) -> dict:
    return {"id": p["id"], "description": p["description"], "date": p["date"],
            "account_name": setoff_api._mask(p["account_name"]), "amount_minor": p["amount_minor"],
            "currency": p["currency"], "left_minor": abs(p["own_minor"])}


# The waiting set-offs (money in held for a row not yet in), each with every row
# that fits E6's waiting signals, in one read: a LEFT JOIN, so a set-off with no
# fit still comes back once, with no row. The signals in SQL: money out in the
# payment's currency, no expectation on it, dated on or after the payment and
# within WINDOW_DAYS of it, room (amount less what is already placed on it) for
# the whole waiting amount, and brought in after the payment was set waiting.
# The row's own import time is read (its created_at), its statement's as the
# fallback: a calendar-month record keeps the time of its first import, so
# rows a later import adds to it would otherwise never count as new. Whether a
# row is Household spending is expect_back's one rule, applied here after.
_WAITING = f"""
    SELECT w.id AS set_off_id, w.amount_minor AS waiting_minor, w.made_at,
           w.person_id, pn.name AS person_name,
           p.id AS pay_id, p.date AS pay_date, p.description AS pay_description,
           p.amount_minor AS pay_amount_minor, pa.name AS pay_account_name,
           COALESCE(pa.currency, 'SGD') AS currency, prp.own_minor AS pay_own_minor,
           c.id, c.date, c.description, c.amount_minor, c.flow_type, c.other_side_id,
           c.book, c.account_name, c.owner, c.other_kind
    FROM set_off w
    JOIN transactions p ON p.id = w.payment_row_id
    JOIN statements ps ON ps.id = p.statement_id
    JOIN accounts pa ON pa.id = ps.account_id
    JOIN row_parts prp ON prp.row_id = p.id
    LEFT JOIN accounts pn ON pn.id = w.person_id
    LEFT JOIN (
        SELECT t.id, t.date, t.description, t.amount_minor, t.flow_type, t.other_side_id,
               {expect_back.BOOK_SQL} AS book, a.name AS account_name, a.owner, o.type AS other_kind,
               COALESCE(a.currency, 'SGD') AS currency,
               t.amount_minor - ABS(rp.placed_minor) AS room_minor,
               COALESCE(t.created_at, s.imported_at) AS came_in
        FROM transactions t
        JOIN statements s ON s.id = t.statement_id
        JOIN accounts a ON a.id = s.account_id
        LEFT JOIN services sv ON sv.id = t.service_id
        JOIN row_parts rp ON rp.row_id = t.id
        LEFT JOIN accounts o ON o.id = t.other_side_id
        WHERE t.amount_minor > 0
          AND NOT EXISTS (SELECT 1 FROM expectation e WHERE e.row_id = t.id)
    ) c ON c.currency = COALESCE(pa.currency, 'SGD')
       AND c.date >= p.date AND c.date <= date(p.date, '+{WINDOW_DAYS} days')
       AND c.room_minor >= w.amount_minor
       AND c.came_in > w.made_at
    WHERE w.expectation_id IS NULL AND p.amount_minor < 0
    ORDER BY w.made_at, w.id, c.date, c.id
"""


def _named_row(description: str | None, name: str | None) -> bool:
    """Whether a row's text holds the waiting set-off's person's name, as a word."""
    return bool(name) and bool(expect_back._word(name).search((description or "").upper()))


def _waiting_row(r) -> dict:
    return {"id": r["id"], "description": r["description"], "date": r["date"],
            "account_name": setoff_api._mask(r["account_name"]), "amount_minor": r["amount_minor"],
            "currency": r["currency"]}


def waiting_offers(conn: sqlite3.Connection) -> list[dict]:
    """For each set-off waiting for its row (money in, held), the rows the Queue
    offers it: Household spending (flow expense) that fits the signals of
    _WAITING; a row whose text holds the set-off's person's name first of all,
    then nearest the payment, every fit offered (spec 23, E6; cases 17, 17a,
    17b). Oldest waiting first. One read. Nothing here writes; ✓ posts ticket
    40's attach route (waiting_api)."""
    offers: dict[int, dict] = {}
    for r in conn.execute(_WAITING, (book_type.DEFAULT_BOOK,)).fetchall():
        w = offers.get(r["set_off_id"])
        if w is None:
            person = ({"account_id": r["person_id"], "name": r["person_name"]}
                      if r["person_id"] is not None else None)
            payment = {"id": r["pay_id"], "description": r["pay_description"], "date": r["pay_date"],
                       "account_name": setoff_api._mask(r["pay_account_name"]),
                       "amount_minor": r["pay_amount_minor"], "currency": r["currency"],
                       "left_minor": abs(r["pay_own_minor"])}
            w = offers[r["set_off_id"]] = {
                "set_off_id": r["set_off_id"], "person": person, "amount_minor": r["waiting_minor"],
                "currency": r["currency"], "payment": payment, "rows": [], "_fits": []}
        if r["id"] is None or r["flow_type"] != "expense" or expect_back._row_refusal(r, expect_back.BACK):
            continue
        days = (_day(r["date"]) - _day(r["pay_date"])).days
        named = _named_row(r["description"], r["person_name"])
        w["_fits"].append(((0 if named else 1, days, r["date"], r["id"]), _waiting_row(r)))
    out = []
    for w in offers.values():
        w["rows"] = [shown for _, shown in sorted(w.pop("_fits"), key=lambda f: f[0])]
        out.append(w)
    return out


def paybacks(conn: sqlite3.Connection) -> dict:
    """The lane read: {paybacks: [{payment, suggestions}], waiting_offers},
    newest payment first, oldest waiting set-off first
    (tests/fixtures/expect/queue_paybacks.json)."""
    payments = [
        p for p in conn.execute(_PAYMENTS, (book_type.DEFAULT_BOOK,)).fetchall()
        # A payment may pay what its own row could carry to pay back (spec 23:
        # a set-off's payment row obeys the same exclusions): Household money in.
        if expect_back._row_refusal(p, expect_back.PAY) is None
    ]
    waiting = waiting_offers(conn)
    if not payments:
        return {"paybacks": [], "waiting_offers": waiting}
    rows = conn.execute(setoff_api._ITEMS, (book_type.DEFAULT_BOOK, expect_back.BACK)).fetchall()
    names = setoff_api._names(conn, [r["id"] for r in rows])
    items = [setoff_api._item(r, names[r["id"]]) for r in rows]
    for item in items:
        item["day"] = (item["day"] or "")[:10]
    wordings = [(r[0], r[1]) for r in conn.execute("SELECT person_id, wording FROM sender_wording")]
    out = []
    for p in payments:
        found = suggestions_for(dict(p), items, wordings)
        out.append({"payment": _shown(p), "suggestions": found})
    return {"paybacks": out, "waiting_offers": waiting}
