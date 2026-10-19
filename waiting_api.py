"""E2, waiting for its row (ticket 40): Nothing yet, attach by hand, Un-wait.

Money in may arrive before the money out it is for: Lena pays S$ 100 for the
BBQ before the BBQ is charged. Nothing yet leaves the payment's open part
waiting for its row: a set-off with no expectation, naming who it is from and
a note instead. It counts under Books' Waiting for its row, not as income, and
nothing happens to it by itself (case 17c).

Attaching it to a row by hand makes an expectation of that size on the row,
closed at once by the waiting money (case 17). When the row is smaller, the
one waiting set-off becomes a placed part of the row's size and a smaller
waiting part (case 17a). Un-wait is Unlink on the waiting set-off
(setoff_api's DELETE /api/set-offs/<id>): the payment's own part comes back.

The waiting set-off's own small machine (spec 23, Lifecycle,
`set_off.expectation`): waiting -> placed when its row comes, or split into a
placed part and a smaller waiting part; placed -> waiting is forbidden (Unlink
removes a placed set-off instead).

How an attach is written. The waiting set-off is removed and its parts are
inserted afresh in the same change: the placed part (its expectation set)
and, when the row is smaller, the waiting rest, which keeps the time it was
first set waiting. An update in place would leave a record the change did not
insert pointing at the expectation it did, which the undo guard
(expect_back.refusal_to_undo) rightly refuses, so the attach could not be
undone from Changes. Removing and inserting conserves every amount and keeps
the attach one change, undone whole.

Routes (on the expect-back blueprint, expect_routes.py; no module here
imports app.py):
- GET  /api/transactions/<id>/waiting   the Nothing yet drawer: the payment,
  the people to name, the sender fin knows, what already waits on it.
- POST /api/transactions/<id>/waiting   Nothing yet: {person_id?, note?};
  the payment's whole open part waits for its row, as one change.
- GET  /api/set-offs/<id>/attach        the rows a waiting set-off may be
  attached to, nearest the payment first, each with the split it would make.
- POST /api/set-offs/<id>/attach        attach it: {row_id}, as one change.

Every guard is read inside the transaction that writes, under the change
history's write lock (app.py holds it for every write request); the writes
are expect_back's guarded ones, so its refusals and words are the machine's.
Amounts are whole minor units of the payment's currency; a waiting part
attaches only to a row in that currency.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from flask import g, jsonify, request

import account_kind
import book_type
import db
import expect_back as xb
import expectation_api
import money
import person

# endpoint (without the blueprint's "expect." prefix) -> what the change did.
# Each write names its own change more exactly (_name_change); these stand when it cannot.
SUMMARIES: dict[str, str] = {
    "nothing_yet": "Left a payment waiting for its row",
    "attach_waiting": "Attached a waiting payment to its row",
}

# How far after a payment a hand attach looks for its row (as the Queue's
# waiting offers do, E6).
WINDOW_DAYS = 90
ROW_LIMIT = 50

NOTHING_LEFT_TO_WAIT = "nothing is left on this payment to wait for its row"
BAD_WAITING = "the waiting part names who it is from and a note"
NAME_THE_ROW = "name the row to attach"


class NotFound(LookupError):
    """The row or the set-off asked for is not in the book."""


# --- reads ----------------------------------------------------------------------


def _payment(conn: sqlite3.Connection, tx_id: int) -> dict:
    """A payment row with its parts and what it has left to place."""
    found = conn.execute(
        "SELECT t.id, t.date, t.description, COALESCE(a.currency, 'SGD') AS currency, "
        "rp.amount_minor, rp.expected_minor, rp.placed_minor, rp.own_minor "
        "FROM transactions t JOIN statements s ON s.id = t.statement_id "
        "JOIN accounts a ON a.id = s.account_id JOIN row_parts rp ON rp.row_id = t.id WHERE t.id = ?",
        (tx_id,),
    ).fetchone()
    if found is None:
        raise NotFound("no such row")
    p = dict(found)
    p["left_minor"] = abs(p["own_minor"])
    return p


def _parts(p: dict) -> dict:
    return {k: p[k] for k in ("id", "amount_minor", "currency", "expected_minor", "placed_minor",
                              "own_minor", "left_minor")}


def _own_direction(payment: dict) -> str:
    """The direction an expectation on the payment itself would take: a
    payment obeys that direction's exclusions (spec 23, shared rules)."""
    return xb.PAY if payment["amount_minor"] < 0 else xb.BACK


def _item_direction(payment: dict) -> str:
    """The direction of the expectation a waiting payment's row will carry:
    money in waits for money out expected back, and the mirror."""
    return xb.BACK if payment["amount_minor"] < 0 else xb.PAY


def _set_off(conn: sqlite3.Connection, set_off_id: int):
    found = conn.execute("SELECT * FROM set_off WHERE id = ?", (set_off_id,)).fetchone()
    if found is None:
        raise NotFound("no such set-off")
    return found


def _shown(conn: sqlite3.Connection, set_off_ids) -> list[dict]:
    """Set-offs as an answer carries them (setoff_api's one shape)."""
    import setoff_api  # imported here: setoff_api imports this module for its waiting part

    return setoff_api._set_offs(conn, set_off_ids, {})  # same currency: no rate, so no month_ended


def _people(conn: sqlite3.Connection) -> list[dict]:
    return [{"account_id": p[0], "name": p[1]} for p in conn.execute(
        "SELECT id, name FROM accounts WHERE type = ? AND owner = ? ORDER BY name COLLATE NOCASE, id",
        (person.PERSON, account_kind.HOUSEHOLD))]


def _named_sender(people: list[dict], description: str | None) -> dict | None:
    """The one person whose name is a word in the payment's text, or None."""
    text = (description or "").upper()
    found = [p for p in people if xb._word(p["name"]).search(text)]
    return found[0] if len(found) == 1 else None


def drawer(conn: sqlite3.Connection, tx_id: int) -> dict:
    """What the Nothing yet drawer reads: the payment and what it has left,
    the people to name, the sender a learnt wording (or a name in the text)
    points to, and the set-offs already waiting on it. Writes nothing."""
    import setoff_api  # imported here: setoff_api imports this module

    payment = _payment(conn, tx_id)
    people = _people(conn)
    shown = {**_parts(payment), "description": payment["description"], "date": payment["date"]}
    waiting = [r[0] for r in conn.execute(
        "SELECT id FROM set_off WHERE payment_row_id = ? AND expectation_id IS NULL ORDER BY id", (tx_id,))]
    sender = setoff_api._learnt_person(conn, payment["description"]) or _named_sender(people, payment["description"])
    return {"payment": shown, "people": people, "sender": sender, "waiting": _shown(conn, waiting)}


def _would(room: int, waiting_minor: int) -> dict:
    """The split an attach makes: an expectation of the smaller of the row's
    room and the waiting amount, placed at once; the rest still waits."""
    take = min(room, waiting_minor)
    return {"expected_minor": take, "placed_minor": take, "waiting_minor": waiting_minor - take}


def attach_rows(conn: sqlite3.Connection, set_off_id: int) -> dict:
    """The rows a waiting set-off may be attached to: the other way to the
    payment, in its currency, with no expectation and room left, where an
    expectation of that direction may sit, dated on or after the payment and
    within WINDOW_DAYS of it; nearest first. One read. Writes nothing. (The
    attach route itself takes any row the machine allows.)"""
    so = _set_off(conn, set_off_id)
    shown = _shown(conn, [set_off_id])[0]
    if so["expectation_id"] is not None:
        return {"set_off": shown, "rows": []}
    payment = _payment(conn, so["payment_row_id"])
    direction = _item_direction(payment)
    end = (date.fromisoformat(payment["date"]) + timedelta(days=WINDOW_DAYS)).isoformat()
    sign = "t.amount_minor > 0" if direction == xb.BACK else "t.amount_minor < 0"
    found = conn.execute(
        "SELECT t.id, t.date, t.description, t.amount_minor, t.flow_type, t.other_side_id, t.type_id, "
        "COALESCE(t.book, ?) AS book, s.account_id, a.name AS account_name, "
        "COALESCE(a.currency, 'SGD') AS currency, a.owner, o.type AS other_kind, "
        "ty.name AS type_name, pt.name AS parent_type, rp.placed_minor "
        "FROM transactions t JOIN statements s ON s.id = t.statement_id "
        "JOIN accounts a ON a.id = s.account_id JOIN row_parts rp ON rp.row_id = t.id "
        "LEFT JOIN accounts o ON o.id = t.other_side_id "
        "LEFT JOIN types ty ON ty.id = t.type_id LEFT JOIN types pt ON pt.id = ty.parent_id "
        f"WHERE {sign} AND COALESCE(a.currency, 'SGD') = ? AND t.id != ? "
        "AND t.date >= ? AND t.date <= ? "
        "AND NOT EXISTS (SELECT 1 FROM expectation e WHERE e.row_id = t.id) "
        "ORDER BY t.date, t.id",
        (book_type.DEFAULT_BOOK, payment["currency"], payment["id"], payment["date"], end),
    ).fetchall()
    rows = []
    for r in found:
        if xb._row_refusal(r, direction) is not None:
            continue
        room = abs(r["amount_minor"]) - abs(r["placed_minor"])
        if room <= 0:
            continue
        rows.append({**expectation_api._row_brief(r), "would": _would(room, so["amount_minor"])})
        if len(rows) == ROW_LIMIT:
            break
    return {"set_off": shown, "rows": rows}


# --- the writes -------------------------------------------------------------------


def _begin(conn: sqlite3.Connection) -> None:
    """Hold the database's write lock from the first guard read."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def _name_change(conn: sqlite3.Connection, words: str) -> None:
    """Name this request's change in Changes, unless its caller (chat) already named it."""
    entry_id = g.get("change_entry")
    if entry_id is not None:
        conn.execute("UPDATE change_entries SET summary = ? WHERE id = ? AND summary = ''", (words, entry_id))


def checked_waiting(conn: sqlite3.Connection, waiting, currency: str) -> dict:
    """A waiting part as a request carries it, read: {person_id?, note?,
    amount?}; amount is decimal text in the payment's currency, None for all
    that is left. Refused with the operator's words."""
    if not isinstance(waiting, dict):
        raise xb.Refused(BAD_WAITING)
    person_id = waiting.get("person_id")
    if person_id is not None:
        xb._checked_people(conn, [person_id])
    note = waiting.get("note")
    if note is not None and not isinstance(note, str):
        raise xb.Refused("a note is text")
    text = waiting.get("amount")
    amount = None
    if text is not None:
        try:
            if not isinstance(text, str):
                raise ValueError(text)
            amount = money.parse_minor(text, currency)
        except ValueError:
            raise xb.Refused(f"{text} is not an amount in whole cents") from None
        if amount <= 0:
            raise xb.Refused("the amount must be more than nothing")
    return {"person_id": person_id, "note": (note.strip() or None) if note is not None else None,
            "amount_minor": amount}


def wait(conn: sqlite3.Connection, tx_id: int, part: dict) -> int:
    """Leave `part` of a payment (checked_waiting's shape; all that is left
    when it names no amount) waiting for its row, in the caller's change.
    Returns the set-off's id. Refused when nothing is left, or when the
    amount is more than is left (expect_back's words)."""
    _begin(conn)
    payment = _payment(conn, tx_id)
    left = payment["left_minor"]
    if left == 0:
        raise xb.Refused(NOTHING_LEFT_TO_WAIT)
    amount = part["amount_minor"] if part["amount_minor"] is not None else left
    return xb.insert_set_off(conn, tx_id, None, amount, person_id=part["person_id"], note=part["note"])


def nothing_yet(conn: sqlite3.Connection, tx_id: int, body: dict) -> dict:
    """Nothing yet: the payment's whole open part waits for its row."""
    _begin(conn)
    payment = _payment(conn, tx_id)
    why = xb.refusal_to_mark(conn, tx_id, _own_direction(payment))
    if why:
        raise xb.Refused(why)
    if payment["left_minor"] == 0:
        raise xb.Refused(f"{xb.short(0, payment['currency'])} left on this payment")
    part = checked_waiting(conn, {"person_id": body.get("person_id"), "note": body.get("note")},
                           payment["currency"])
    set_off_id = wait(conn, tx_id, part)
    _name_change(conn, f"Nothing yet: {xb.short(payment['left_minor'], payment['currency'])} of "
                       f"{payment['description']} waits for its row")
    return {"set_off": _shown(conn, [set_off_id])[0], "payment": _parts(_payment(conn, tx_id))}


def _placed_words(conn: sqlite3.Connection, so) -> str:
    """Why a placed set-off cannot go back to waiting: the machine's words."""
    x = xb._expectation(conn, so["expectation_id"])
    cur = _payment(conn, so["payment_row_id"])["currency"]
    return (f"{xb.short(so['amount_minor'], cur)} of this payment is placed against {xb._item_name(x)}: "
            "it is not waiting for its row. Unlink it to place it again.")


def attach(conn: sqlite3.Connection, set_off_id: int, row_id, *, source: str = xb.HAND) -> dict:
    """Attach a waiting set-off to its row: an expectation on the row of the
    smaller of the row's room and the waiting amount, closed at once by that
    much of the payment; what is left still waits (case 17a). The
    expectation takes the row's day, the set-off keeps the payment's (case
    17e). Nothing here commits; the route does."""
    _begin(conn)
    so = _set_off(conn, set_off_id)
    if so["expectation_id"] is not None:
        raise xb.Refused(_placed_words(conn, so))
    if isinstance(row_id, bool) or not isinstance(row_id, int):
        raise xb.Refused(NAME_THE_ROW)
    try:
        r = expectation_api._row(conn, row_id)
    except expectation_api.NotFound:
        raise NotFound("no such row") from None
    payment = _payment(conn, so["payment_row_id"])
    direction = _item_direction(payment)
    why = xb.refusal_to_mark(conn, row_id, direction)
    if why:
        raise xb.Refused(why)
    if r["currency"] != payment["currency"]:
        raise xb.Refused(f"{r['description']} is kept in {xb.SIGNS.get(r['currency'], r['currency'])} and this "
                         f"payment in {xb.SIGNS.get(payment['currency'], payment['currency'])}: "
                         "attach a row in the payment's currency")
    room = expectation_api._left_to_mark(conn, r)
    if room <= 0:
        raise xb.Refused("nothing is left on this row: it is all placed")
    split = _would(room, so["amount_minor"])
    people = [so["person_id"]] if so["person_id"] is not None else []

    xb.delete_set_off(conn, set_off_id)
    expectation_id = xb.insert_expectation(conn, row_id, direction, split["expected_minor"], names=people)
    placed = xb.insert_set_off(conn, so["payment_row_id"], expectation_id, split["placed_minor"],
                               person_id=so["person_id"], note=so["note"], source=source)
    rest = None
    if split["waiting_minor"]:
        rest = xb.insert_set_off(conn, so["payment_row_id"], None, split["waiting_minor"],
                                 person_id=so["person_id"], note=so["note"], source=so["source"])
        # The rest is the same money, waiting since it was first set waiting.
        conn.execute("UPDATE set_off SET made_at = ? WHERE id = ?", (so["made_at"], rest))

    cur = payment["currency"]
    words = f"Attached {xb.short(split['placed_minor'], cur)} of {payment['description']} to {r['description']}"
    if rest is not None:
        words += f"; {xb.short(split['waiting_minor'], cur)} still waits"
    _name_change(conn, words)
    return {
        "expectation": expectation_api.expectation_payload(conn, expectation_id),
        "placed": _shown(conn, [placed])[0],
        "waiting": _shown(conn, [rest])[0] if rest is not None else None,
        "row": expectation_api._parts(conn, r),
    }


def name_unwait(conn: sqlite3.Connection, set_off_id: int) -> None:
    """Name Un-wait's change, before a waiting set-off is unlinked; a placed
    set-off's Unlink keeps its own words."""
    found = conn.execute("SELECT * FROM set_off WHERE id = ?", (set_off_id,)).fetchone()
    if found is None or found["expectation_id"] is not None:
        return
    payment = _payment(conn, found["payment_row_id"])
    _name_change(conn, f"Un-waited {xb.short(found['amount_minor'], payment['currency'])} of "
                       f"{payment['description']}")


# --- routes ---------------------------------------------------------------------


def _answer(work):
    """Run one route's work on its own connection: a refusal or a miss writes
    nothing (rolled back); otherwise commit, then answer."""
    with db.get_db() as conn:
        try:
            out = work(conn)
        except NotFound as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 404
        except xb.Refused as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 400
        conn.commit()
    return jsonify(out)


def api_drawer(tx_id: int):
    """GET: what the Nothing yet drawer reads."""
    return _answer(lambda conn: drawer(conn, tx_id))


def api_nothing_yet(tx_id: int):
    """POST {person_id?, note?}: Nothing yet, as one change."""
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    return _answer(lambda conn: nothing_yet(conn, tx_id, body))


def api_attach_rows(set_off_id: int):
    """GET: the rows a waiting set-off may be attached to."""
    return _answer(lambda conn: attach_rows(conn, set_off_id))


def api_attach(set_off_id: int):
    """POST {row_id}: attach a waiting set-off to its row, as one change."""
    body = request.get_json(silent=True)
    row_id = body.get("row_id") if isinstance(body, dict) else None
    return _answer(lambda conn: attach(conn, set_off_id, row_id))


def register(bp) -> None:
    """Add this lane's routes to the expect-back blueprint."""
    bp.add_url_rule("/api/transactions/<int:tx_id>/waiting", endpoint="waiting_drawer",
                    view_func=api_drawer, methods=["GET"])
    bp.add_url_rule("/api/transactions/<int:tx_id>/waiting", endpoint="nothing_yet",
                    view_func=api_nothing_yet, methods=["POST"])
    bp.add_url_rule("/api/set-offs/<int:set_off_id>/attach", endpoint="attach_rows",
                    view_func=api_attach_rows, methods=["GET"])
    bp.add_url_rule("/api/set-offs/<int:set_off_id>/attach", endpoint="attach_waiting",
                    view_func=api_attach, methods=["POST"])
