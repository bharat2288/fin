"""D3, the expectation's page, and the open list (ticket 35; spec 23, E1 reads).

Two reads on the expect-back blueprint (expect_routes.py), both read-only:

- GET /api/expectations/<id>: one expectation as its page draws it: the
  expectation, its row's own part, its age and how much is back, and its
  history lines: Marked, each set-off (refund set-offs as refund lines), a
  let-go, and the open rest, named per Who is still out. Each line carries
  the actions the page draws on it: Undo the mark and Unlink are always
  drawn, so their refusal names what holds them (cases 21, 26b); Let it go...
  only on an open rest, which a closed or let-go item has none of (case 26a);
  Undo on a let-go.
- GET /api/expectations: the open list Back for... and Books read: every
  expectation with something open and nothing let go, filtered by direction,
  person and currency, nearest a date first (else oldest first). Given a
  payment row it lists what that payment can set off (money in: what is
  expected back; money out: what is to pay back), nearest the payment's day.

Shapes are the contract's (tests/fixtures/expect/page.json, open_list.json).
Every figure is the records', read in a fixed number of statements however
many expectations and set-offs there are (case 55): no read here runs one
query per item. Nothing here writes, and no route here opens a change.
No module here imports app.py.
"""

from __future__ import annotations

import datetime
import json
import re
import sqlite3

from flask import jsonify, request

import book_type
import db
import expect_back
import suggest

# endpoint (without the blueprint's "expect." prefix) -> what the change did.
# These two routes only read, so they name no change.
SUMMARIES: dict[str, str] = {}

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_WHOLE = re.compile(r"^\d+$")
# Words a bank prints around a sender's name, never part of it.
_SENDER_FILLER = frozenset(
    "PAYNOW FROM INWARD OUTWARD TRANSFER FAST GIRO IBG FUNDS FUND TRF TFR CREDIT RECEIVED "
    "INCOMING PAYMENT OTHR OTHER REF REFERENCE TO BY VIA MOBILE".split())


def _today() -> datetime.date:
    """Today. Tests replace this; nothing else here reads the clock."""
    return datetime.date.today()


class _Refused(Exception):
    def __init__(self, words: str, status: int = 400):
        super().__init__(words)
        self.status = status


def register(bp) -> None:
    """Add the page read and the open list to the expect-back blueprint."""
    bp.add_url_rule("/api/expectations/<int:expectation_id>", "expectation_page", _page_view,
                    methods=["GET"])
    bp.add_url_rule("/api/expectations", "open_list", _open_list_view, methods=["GET"])


def _page_view(expectation_id: int):
    try:
        with db.get_db() as conn:
            body = page(conn, expectation_id, today=_today())
    except _Refused as refused:
        return jsonify({"error": str(refused)}), refused.status
    return jsonify(body)


def _open_list_view():
    args = request.args
    try:
        with db.get_db() as conn:
            body = open_list(conn, direction=args.get("direction"), person=args.get("person"),
                             currency=args.get("currency"), near=args.get("near"),
                             payment=args.get("payment"))
    except _Refused as refused:
        return jsonify({"error": str(refused)}), refused.status
    return jsonify(body)


# ---------------------------------------------------------------------------
# The expectations, in one read
# ---------------------------------------------------------------------------

# Every column an expectation's payload needs, its set-off sum grouped once
# rather than per item. Filters are fixed fragments below; only values reach
# SQL, as parameters.
_ITEMS = f"""
    SELECT e.id, e.direction, e.amount_minor, e.currency, e.note, e.source, e.made_at,
           e.row_id, e.person_id, e.opening_date,
           COALESCE(t.date, e.opening_date) AS day,
           COALESCE(placed.total, 0) AS set_off_minor,
           COALESCE(l.amount_minor, 0) AS let_go_minor,
           l.id AS let_go_id, l.became AS let_go_became, l.book AS let_go_book,
           l.counts_on AS let_go_on, lw.name AS let_go_to,
           lty.name AS let_go_type, ltp.name AS let_go_parent,
           t.date AS row_date, t.description, s.account_id, a.name AS account_name,
           t.amount_minor AS row_amount, COALESCE(a.currency, 'SGD') AS row_currency,
           COALESCE(t.book, '{book_type.DEFAULT_BOOK}') AS row_book, t.type_id,
           ty.name AS type_name, tp.name AS parent_type, p.name AS person_name
    FROM expectation e
    LEFT JOIN (SELECT expectation_id, SUM(item_amount_minor) AS total FROM set_off
               WHERE expectation_id IS NOT NULL GROUP BY expectation_id) placed
           ON placed.expectation_id = e.id
    LEFT JOIN let_go l ON l.expectation_id = e.id
    LEFT JOIN accounts lw ON lw.id = l.to_whom_id
    LEFT JOIN types lty ON lty.id = l.type_id
    LEFT JOIN types ltp ON ltp.id = lty.parent_id
    LEFT JOIN transactions t ON t.id = e.row_id
    LEFT JOIN statements s ON s.id = t.statement_id
    LEFT JOIN accounts a ON a.id = s.account_id
    LEFT JOIN types ty ON ty.id = t.type_id
    LEFT JOIN types tp ON tp.id = ty.parent_id
    LEFT JOIN accounts p ON p.id = e.person_id
"""


def _type_words(name, parent):
    return book_type.display_name(name, parent) if name else None


def _state(amount: int, set_off: int, let_go_id) -> str:
    """The derived state, as expect_back.state reads it, from figures already read."""
    if let_go_id is not None:
        return expect_back.LET_GO
    if set_off == 0:
        return expect_back.OPEN
    return expect_back.CLOSED if set_off >= amount else expect_back.PARTLY_BACK


def _names(conn: sqlite3.Connection, ids) -> dict[int, list[dict]]:
    """The names on each expectation, in the order they were put on it. One read."""
    out: dict[int, list[dict]] = {i: [] for i in ids}
    for r in conn.execute(
        "SELECT n.expectation_id, a.id, a.name FROM expectation_name n JOIN accounts a ON a.id = n.person_id "
        "WHERE n.expectation_id IN (SELECT value FROM json_each(?)) ORDER BY n.expectation_id, n.id",
        (json.dumps(list(ids)),),
    ):
        out.setdefault(r[0], []).append({"account_id": r[1], "name": r[2]})
    return out


def _item(r, names: list[dict]) -> dict:
    """One expectation in the contract's shape."""
    row = None
    opening = None
    if r["row_id"] is not None:
        row = {"id": r["row_id"], "date": r["row_date"], "description": r["description"],
               "account_id": r["account_id"], "account_name": r["account_name"],
               "amount_minor": r["row_amount"], "currency": r["row_currency"], "book": r["row_book"],
               "type_id": r["type_id"], "display_type": _type_words(r["type_name"], r["parent_type"])}
    else:
        opening = {"account_id": r["person_id"], "name": r["person_name"], "date": r["opening_date"]}
    open_minor = r["amount_minor"] - r["set_off_minor"] - r["let_go_minor"]
    return {
        "id": r["id"], "direction": r["direction"], "amount_minor": r["amount_minor"],
        "currency": r["currency"], "note": r["note"], "source": r["source"], "made_at": r["made_at"],
        "day": r["day"], "names": names, "set_off_minor": r["set_off_minor"],
        "let_go_minor": r["let_go_minor"], "open_minor": open_minor,
        "state": _state(r["amount_minor"], r["set_off_minor"], r["let_go_id"]),
        "row": row, "opening": opening,
    }


# ---------------------------------------------------------------------------
# Who paid: expect_back.payer_name's rule, for many payments in one read
# ---------------------------------------------------------------------------

def _wordings(conn: sqlite3.Connection) -> list[tuple[int, str, str]]:
    return [tuple(r) for r in conn.execute(
        "SELECT w.person_id, w.wording, a.name FROM sender_wording w JOIN accounts a ON a.id = w.person_id "
        "ORDER BY w.id")]


def _payers(conn: sqlite3.Connection, payments: dict[int, str], wordings) -> dict[int, str | None]:
    """The one person each payment is from, by its text: the rule of
    expect_back.payer_name (a learnt wording it carries, or the name of a
    person named on what it is set against or waiting for), read for every
    payment at once. None when no one, or more than one, matches."""
    candidates: dict[int, dict[int, str]] = {p: {} for p in payments}
    for payment_row_id, person_id, name in conn.execute(
        "SELECT so.payment_row_id, a.id, a.name FROM set_off so "
        "JOIN expectation_name n ON n.expectation_id = so.expectation_id JOIN accounts a ON a.id = n.person_id "
        "WHERE so.payment_row_id IN (SELECT value FROM json_each(?)) "
        "UNION SELECT so.payment_row_id, a.id, a.name FROM set_off so JOIN accounts a ON a.id = so.person_id "
        "WHERE so.payment_row_id IN (SELECT value FROM json_each(?))",
        (json.dumps(list(payments)), json.dumps(list(payments))),
    ):
        candidates[payment_row_id][person_id] = name
    out = {}
    for payment_row_id, description in payments.items():
        text = (description or "").upper()
        found = {pid: name for pid, name in candidates[payment_row_id].items()
                 if expect_back._word(name).search(text)}
        for person_id, words, name in wordings:
            if expect_back._word(words).search(text):
                found[person_id] = name
        out[payment_row_id] = next(iter(found.values())) if len(found) == 1 else None
    return out


def _still_out(names: list[dict], paid_texts: list[str], wordings) -> str | None:
    """Who is still out: the open rest names a person only when exactly one
    named person has no set-off here whose payment's text matches their name
    or a wording learnt for them."""
    texts = [(t or "").upper() for t in paid_texts]
    unpaid = []
    for n in names:
        patterns = [expect_back._word(n["name"])] + [
            expect_back._word(w) for pid, w, _ in wordings if pid == n["account_id"]]
        if not any(p.search(t) for p in patterns for t in texts):
            unpaid.append(n["name"])
    return unpaid[0] if len(unpaid) == 1 else None


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def page(conn: sqlite3.Connection, expectation_id: int, *, today: datetime.date) -> dict:
    """One expectation's page (contract: page.json). Refused 404 when there is none."""
    found = conn.execute(_ITEMS + " WHERE e.id = ?", (expectation_id,)).fetchone()
    if found is None:
        raise _Refused("no such expectation", 404)
    names = _names(conn, [expectation_id])[expectation_id]
    item = _item(found, names)

    set_offs = conn.execute(
        "SELECT so.id, so.payment_row_id, so.amount_minor, so.item_amount_minor, so.source, "
        "t.date, t.description, t.amount_minor AS payment_amount, COALESCE(a.currency, 'SGD') AS payment_currency "
        "FROM set_off so JOIN transactions t ON t.id = so.payment_row_id "
        "JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id "
        "WHERE so.expectation_id = ? ORDER BY t.date, so.id",
        (expectation_id,),
    ).fetchall()
    payments = {so["payment_row_id"]: so["description"] for so in set_offs}
    # What each payment placed elsewhere, in the payment's own currency, so the
    # page can say 'S$ 70, S$ 10 of it went to Klook'.
    elsewhere: dict[int, list[dict]] = {p: [] for p in payments}
    for r in conn.execute(
        "SELECT so.payment_row_id, so.expectation_id, so.amount_minor, COALESCE(a.currency, 'SGD'), "
        "COALESCE(it.description, CASE WHEN so.expectation_id IS NULL THEN 'its row, when it comes' "
        "ELSE 'Opening figure · ' || op.name END) "
        "FROM set_off so JOIN transactions t ON t.id = so.payment_row_id "
        "JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id "
        "LEFT JOIN expectation e ON e.id = so.expectation_id "
        "LEFT JOIN transactions it ON it.id = e.row_id LEFT JOIN accounts op ON op.id = e.person_id "
        "WHERE so.payment_row_id IN (SELECT value FROM json_each(?)) "
        "AND (so.expectation_id IS NULL OR so.expectation_id != ?) ORDER BY so.id",
        (json.dumps(list(payments)), expectation_id),
    ):
        elsewhere[r[0]].append({"expectation_id": r[1], "title": r[4], "amount_minor": r[2], "currency": r[3]})
    wordings = _wordings(conn)
    payers = _payers(conn, payments, wordings)

    own = None
    if found["row_id"] is not None:
        own = expect_back.parts(conn, [found["row_id"]])[found["row_id"]]["own_minor"]

    currency = item["currency"]
    lines: list[dict] = [{"kind": "marked", "id": item["id"], "date": item["day"],
                          "amount_minor": item["amount_minor"], "currency": currency,
                          "actions": ["undo_mark"]}]
    for so in set_offs:
        payment = {"id": so["payment_row_id"], "description": so["description"],
                   "amount_minor": so["payment_amount"], "currency": so["payment_currency"]}
        if so["source"] == "refund":
            lines.append({"kind": "refund", "id": so["id"], "date": so["date"],
                          "purchase_date": item["day"], "amount_minor": so["item_amount_minor"],
                          "currency": currency, "refund": payment, "actions": ["unlink"]})
        else:
            lines.append({"kind": "set_off", "id": so["id"], "date": so["date"],
                          "person": payers[so["payment_row_id"]], "amount_minor": so["item_amount_minor"],
                          "currency": currency, "payment": payment,
                          "elsewhere": elsewhere[so["payment_row_id"]], "actions": ["unlink"]})
    if found["let_go_id"] is not None:
        lines.append({"kind": "let_go", "id": found["let_go_id"], "date": found["let_go_on"],
                      "amount_minor": found["let_go_minor"], "currency": currency,
                      "became": found["let_go_became"], "to": found["let_go_to"],
                      "book": found["let_go_book"],
                      "display_type": _type_words(found["let_go_type"], found["let_go_parent"]),
                      "actions": ["undo_let_go"]})
    elif item["open_minor"] > 0:
        lines.append({"kind": "open", "amount_minor": item["open_minor"], "currency": currency,
                      "name": _still_out(names, [so["description"] for so in set_offs], wordings),
                      "actions": ["let_go"]})

    age = (today - datetime.date.fromisoformat(item["day"])).days
    return {
        "as_at": today.isoformat(),
        "expectation": item,
        "own_minor": own,
        "age_days": age,
        "back_pct": round(100 * item["set_off_minor"] / item["amount_minor"]),
        "lines": lines,
    }


# ---------------------------------------------------------------------------
# The open list
# ---------------------------------------------------------------------------

def sender_wording(description: str | None) -> str:
    """The sender's wording in a payment's text: its cleaned text without the
    words a bank prints around a name ('PAYNOW FROM JAY TAN 0412' -> 'JAY TAN')."""
    return " ".join(w for w in suggest.clean(description).split() if w not in _SENDER_FILLER)


def _payment(conn: sqlite3.Connection, payment_row_id: int, wordings) -> dict:
    r = conn.execute(
        "SELECT t.id, t.date, t.description, COALESCE(a.currency, 'SGD') AS currency "
        "FROM transactions t JOIN statements s ON s.id = t.statement_id "
        "JOIN accounts a ON a.id = s.account_id WHERE t.id = ?",
        (payment_row_id,),
    ).fetchone()
    if r is None:
        raise _Refused("no such row", 404)
    p = expect_back.parts(conn, [payment_row_id])[payment_row_id]
    text = (r["description"] or "").upper()
    learnt = {pid: name for pid, words, name in wordings if expect_back._word(words).search(text)}
    person = ({"account_id": next(iter(learnt)), "name": next(iter(learnt.values()))}
              if len(learnt) == 1 else None)
    return {"id": r["id"], "amount_minor": p["amount_minor"], "currency": r["currency"],
            "expected_minor": p["expected_minor"], "placed_minor": p["placed_minor"],
            "own_minor": p["own_minor"], "description": r["description"], "date": r["date"],
            "left_minor": abs(p["own_minor"]),
            "sender": {"wording": sender_wording(r["description"]), "person": person}}


def open_list(conn: sqlite3.Connection, *, direction=None, person=None, currency=None, near=None,
              payment=None) -> dict:
    """Every expectation with something open and nothing let go (contract:
    open_list.json), nearest `near` first, else oldest first."""
    if direction is not None and direction not in expect_back.DIRECTIONS:
        raise _Refused(f"direction must be one of {', '.join(expect_back.DIRECTIONS)}")
    if near is not None:
        try:
            if not _DAY.match(near):
                raise ValueError(near)
            datetime.date.fromisoformat(near)
        except ValueError:
            raise _Refused("near is a day, YYYY-MM-DD") from None
    if person is not None and not _WHOLE.match(person):
        raise _Refused("person is an account id")
    if currency is not None and not _CURRENCY.match(currency):
        raise _Refused("currency is a three-letter code")
    if payment is not None and not _WHOLE.match(payment):
        raise _Refused("payment is a row id")

    paid_by = None
    if payment is not None:
        paid_by = _payment(conn, int(payment), _wordings(conn))
        wanted = expect_back.BACK if paid_by["amount_minor"] < 0 else expect_back.PAY
        if direction is not None and direction != wanted:
            raise _Refused("money in sets off what is expected back" if wanted == expect_back.BACK
                           else "money out sets off what is to pay back")
        direction = wanted
        if near is None:
            near = paid_by["date"]

    where = ["l.id IS NULL", "e.amount_minor - COALESCE(placed.total, 0) > 0"]
    args: list = []
    if direction is not None:
        where.append("e.direction = ?")
        args.append(direction)
    if currency is not None:
        where.append("e.currency = ?")
        args.append(currency)
    if person is not None:
        where.append("EXISTS (SELECT 1 FROM expectation_name n WHERE n.expectation_id = e.id AND n.person_id = ?)")
        args.append(int(person))
    order = "day, e.id"
    if near is not None:
        order = "ABS(julianday(day) - julianday(?)), day, e.id"
        args.append(near)
    found = conn.execute(_ITEMS + " WHERE " + " AND ".join(where) + " ORDER BY " + order, args).fetchall()
    names = _names(conn, [r["id"] for r in found])
    return {
        "direction": direction,
        "near": near,
        "payment": paid_by,
        "items": [_item(r, names[r["id"]]) for r in found],
    }
