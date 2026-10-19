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

One read for the lane whatever its size (case 55): the payments, the open
items, their names and the learnt wordings are four queries, matched here.
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
# eligibility columns are read so expect_back's one rule decides each row.
_PAYMENTS = """
    SELECT t.id, t.date, t.description, t.amount_minor, t.flow_type, t.other_side_id,
           COALESCE(t.book, ?) AS book, s.account_id, a.name AS account_name, a.owner,
           COALESCE(a.currency, 'SGD') AS currency, o.type AS other_kind, rp.own_minor
    FROM transactions t
    JOIN statements s ON s.id = t.statement_id
    JOIN accounts a ON a.id = s.account_id
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


def paybacks(conn: sqlite3.Connection) -> dict:
    """The lane read: {paybacks: [{payment, suggestions}], waiting_offers: []},
    newest payment first. Waiting offers are ticket 42's; the key is here so
    the payload keeps its one shape (tests/fixtures/expect/queue_paybacks.json)."""
    payments = [
        p for p in conn.execute(_PAYMENTS, (book_type.DEFAULT_BOOK,)).fetchall()
        # A payment may pay what its own row could carry to pay back (spec 23:
        # a set-off's payment row obeys the same exclusions): Household money in.
        if expect_back._row_refusal(p, expect_back.PAY) is None
    ]
    if not payments:
        return {"paybacks": [], "waiting_offers": []}
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
    return {"paybacks": out, "waiting_offers": []}
