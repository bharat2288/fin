"""E1, the expectation routes (ticket 34): mark a row, edit the mark, undo the mark.

Expect some back… on money out, Expect to pay back on money in (spec 23, E1
and D1). One route both marks a row and edits its mark, since a row never
carries two expectations: a second open edits the one there is. Its read
serves the drawer: the row's parts, the mark if there is one, the people to
name, why the row cannot be marked, and, as the amount is typed, what would be
the operator's own ("Yours: S$ 100, Dining · Household"), so no copy of the
own-part rule lives in the client.

Every write holds the database's write lock from its first read (app.py's
change entry holds the process's), reads its guards in the transaction it
writes in, and is one change; a refusal writes nothing and carries the
machine's words (expect_back.MACHINE). The open list and the expectation's
page are ticket 35's (expectation_page_api.py). No module here imports app.py.
"""

from __future__ import annotations

import sqlite3

from flask import g, jsonify, request

import account_kind
import book_type
import expect_back as xb
import money
import person
from db import get_db

# endpoint (without the blueprint's "expect." prefix) -> what the change did.
# Each write names its own change more exactly (_name_change); these stand when it cannot.
SUMMARIES: dict[str, str] = {
    "mark": "Marked what is expected back",
    "undo_mark": "Undid the mark",
}

SPENDING_FLOWS = ("expense", "refund")
# How the "Yours:" line names a row with no type.
FLOW_WORDS = {"expense": "no type", "income": "income", "refund": "a refund",
              "review": "waiting for a label", "movement": "a move"}
# parse_minor's own words, passed on as they are.
PARSER_WORDS = ("not an amount", "not a whole number of minor units")


class NotFound(LookupError):
    """No such row or expectation."""


# --- reads -----------------------------------------------------------------------


def _row(conn: sqlite3.Connection, row_id: int) -> sqlite3.Row:
    found = conn.execute(
        "SELECT t.id, t.date, t.description, t.amount_minor, t.flow_type, t.type_id, "
        "COALESCE(t.book, ?) AS book, s.account_id, a.name AS account_name, "
        "COALESCE(a.currency, 'SGD') AS currency, ty.name AS type_name, pt.name AS parent_type "
        "FROM transactions t JOIN statements s ON s.id = t.statement_id "
        "JOIN accounts a ON a.id = s.account_id "
        "LEFT JOIN types ty ON ty.id = t.type_id LEFT JOIN types pt ON pt.id = ty.parent_id "
        "WHERE t.id = ?",
        (book_type.DEFAULT_BOOK, row_id),
    ).fetchone()
    if found is None:
        raise NotFound("no such row")
    return found


def _display_type(r) -> str | None:
    return book_type.display_name(r["type_name"], r["parent_type"]) if r["type_name"] else None


def _row_brief(r) -> dict:
    return {"id": r["id"], "date": r["date"], "description": r["description"], "account_id": r["account_id"],
            "account_name": r["account_name"], "amount_minor": r["amount_minor"], "currency": r["currency"],
            "book": r["book"], "type_id": r["type_id"], "display_type": _display_type(r)}


def _parts(conn: sqlite3.Connection, r) -> dict:
    """The row's parts, from row_parts (the own part's one home)."""
    p = xb.parts(conn, [r["id"]])[r["id"]]
    return {"id": r["id"], "amount_minor": p["amount_minor"], "currency": r["currency"],
            "expected_minor": p["expected_minor"], "placed_minor": p["placed_minor"], "own_minor": p["own_minor"]}


def expectation_payload(conn: sqlite3.Connection, expectation_id: int) -> dict:
    """One expectation as the drawers read it (tests/fixtures/expect/mark.json)."""
    x = conn.execute("SELECT * FROM expectation WHERE id = ?", (expectation_id,)).fetchone()
    if x is None:
        raise NotFound("no such expectation")
    names = [{"account_id": n[0], "name": n[1]} for n in conn.execute(
        "SELECT a.id, a.name FROM expectation_name n JOIN accounts a ON a.id = n.person_id "
        "WHERE n.expectation_id = ? ORDER BY n.id", (expectation_id,))]
    back = xb.set_off_minor(conn, expectation_id)
    let_go = conn.execute("SELECT amount_minor FROM let_go WHERE expectation_id = ?", (expectation_id,)).fetchone()
    let_go_minor = let_go[0] if let_go else 0
    if x["row_id"] is not None:
        r = _row(conn, x["row_id"])
        row, opening, day = _row_brief(r), None, r["date"]
    else:
        who = conn.execute("SELECT name FROM accounts WHERE id = ?", (x["person_id"],)).fetchone()
        row, day = None, x["opening_date"]
        opening = {"person_id": x["person_id"], "name": who[0] if who else None, "date": x["opening_date"]}
    return {"id": x["id"], "direction": x["direction"], "amount_minor": x["amount_minor"], "currency": x["currency"],
            "note": x["note"], "source": x["source"], "made_at": x["made_at"], "day": day, "names": names,
            "set_off_minor": back, "let_go_minor": let_go_minor,
            "open_minor": x["amount_minor"] - back - let_go_minor,
            "state": xb.state(conn, expectation_id), "row": row, "opening": opening}


def _people(conn: sqlite3.Connection) -> list[dict]:
    return [{"account_id": p[0], "name": p[1]} for p in conn.execute(
        "SELECT id, name FROM accounts WHERE type = ? AND owner = ? ORDER BY name COLLATE NOCASE, id",
        (person.PERSON, account_kind.HOUSEHOLD))]


def _mark_on(conn: sqlite3.Connection, row_id: int):
    return conn.execute("SELECT * FROM expectation WHERE row_id = ?", (row_id,)).fetchone()


# --- the rule for one request ------------------------------------------------------


def _sign(r) -> int:
    return -1 if r["amount_minor"] < 0 else 1


def _left_to_mark(conn: sqlite3.Connection, r) -> int:
    """How much of the row may be expected: the row less what it places as a payment."""
    return abs(r["amount_minor"]) - abs(_parts(conn, r)["placed_minor"])


def _amount(conn: sqlite3.Connection, r, all_of_it: bool, text) -> int:
    """The amount a request asks to expect, in the row's minor units. Refused
    with the parser's words, or the machine's births."""
    left = _left_to_mark(conn, r)
    if all_of_it:
        if left <= 0:
            raise xb.Refused("nothing is left on this row: it is all placed")
        return left
    if text is None or (isinstance(text, str) and not text.strip()):
        raise xb.Refused("an amount is needed, or All of it")
    if not isinstance(text, str):
        raise xb.Refused("an amount is text, as typed")
    try:
        amount = money.parse_minor(text, r["currency"])
    except ValueError as e:
        raise xb.Refused(str(e) if str(e) in PARSER_WORDS else PARSER_WORDS[0]) from None
    if amount <= 0:
        raise xb.Refused("the amount must be more than nothing")
    if amount > abs(r["amount_minor"]):
        raise xb.Refused(f"more than the row's {xb.full(abs(r['amount_minor']), r['currency'])}")
    if amount > left:
        raise xb.Refused(f"more than the {xb.full(left, r['currency'])} left on this row")
    return amount


def _direction(r, asked) -> str:
    if asked is None:
        return xb.PAY if r["amount_minor"] < 0 else xb.BACK
    if asked not in xb.DIRECTIONS:
        raise xb.Refused("direction must be back or pay")
    return asked


def _yours(r, direction: str, own_minor: int) -> str:
    if own_minor == 0:
        return f"None of it is yours: it is not {'spending' if direction == xb.BACK else 'income'}."
    what = _display_type(r) or FLOW_WORDS.get(r["flow_type"], r["flow_type"])
    return f"Yours: {xb.short(abs(own_minor), r['currency'])}, {what} · {r['book']}"


def _spending_change(r, delta_expected: int) -> dict | None:
    """What a change of `delta_expected` on this row does to its month's
    spending: on a spending row its own part moves by the change, the other way."""
    if r["flow_type"] not in SPENDING_FLOWS or delta_expected == 0:
        return None
    return {"month": r["date"][:7], "minor": -_sign(r) * delta_expected, "currency": r["currency"]}


def _plan(conn: sqlite3.Connection, r, body: dict) -> dict:
    """Check a request against the row, writing nothing: the direction, the
    amount, the names (ids and new ones) and the note."""
    direction = _direction(r, body.get("direction"))
    why = xb.refusal_to_mark(conn, r["id"], direction)
    if why:
        raise xb.Refused(why)
    names = body.get("names")
    new_names = body.get("new_names") or []
    if names is not None and (not isinstance(names, list)
                              or any(isinstance(n, bool) or not isinstance(n, int) for n in names)):
        raise xb.Refused("names are person account ids")
    if not isinstance(new_names, list):
        raise xb.Refused("new names are a list of names")
    checked_new = []
    for name in new_names:
        try:
            checked_new.append(person.checked_name(name))
        except ValueError as e:
            raise xb.Refused(str(e)) from None
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise xb.Refused("a note is text")
    return {"direction": direction, "amount": _amount(conn, r, body.get("all") is True, body.get("amount")),
            "names": list(dict.fromkeys(names or [])), "new_names": checked_new,
            "note": (note.strip() or None) if note is not None else None, "has_note": "note" in body,
            "has_names": names is not None or bool(checked_new)}


# --- the writes ---------------------------------------------------------------------


def _begin(conn: sqlite3.Connection) -> None:
    """Hold the database's write lock from the first read."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def _name_change(conn: sqlite3.Connection, words: str) -> None:
    """Name this request's change in Changes, unless its caller (chat) already named it."""
    entry_id = g.get("change_entry")
    if entry_id is not None:
        conn.execute("UPDATE change_entries SET summary = ? WHERE id = ? AND summary = ''", (words, entry_id))


def _people_for(conn: sqlite3.Connection, plan: dict) -> list[int]:
    """The names asked for, with each new one found or made (person.py's one find-or-create)."""
    ids = list(plan["names"])
    for name in plan["new_names"]:
        person_id, _made = person.find_or_create_person(conn, name)
        if person_id not in ids:
            ids.append(person_id)
    return ids


def _what(direction: str) -> str:
    return "expected back" if direction == xb.BACK else "to pay back"


def write_mark(conn: sqlite3.Connection, row_id: int, body: dict) -> dict:
    """Mark the row, or edit its mark. Commits on success; on a refusal rolls
    back and raises xb.Refused (NotFound for no such row)."""
    _begin(conn)
    try:
        r = _row(conn, row_id)
        plan = _plan(conn, r, body)
        held = _mark_on(conn, row_id)
        amount = plan["amount"]
        if held is None:
            expectation_id = xb.insert_expectation(conn, row_id, plan["direction"], amount,
                                                   names=_people_for(conn, plan), note=plan["note"])
            delta = amount
            verb = "Expect back" if plan["direction"] == xb.BACK else "Expect to pay back"
            _name_change(conn, f"{verb} {xb.short(amount, r['currency'])} on {r['description']}")
        else:
            expectation_id = held["id"]
            delta = amount - held["amount_minor"]
            if delta:
                xb.edit_amount(conn, expectation_id, amount)
            if plan["has_names"]:
                wanted = xb._checked_people(conn, _people_for(conn, plan))
                have = [n[0] for n in conn.execute(
                    "SELECT person_id FROM expectation_name WHERE expectation_id = ?", (expectation_id,))]
                for gone in [p for p in have if p not in wanted]:
                    conn.execute("DELETE FROM expectation_name WHERE expectation_id = ? AND person_id = ?",
                                 (expectation_id, gone))
                for added in [p for p in wanted if p not in have]:
                    conn.execute("INSERT INTO expectation_name (expectation_id, person_id) VALUES (?, ?)",
                                 (expectation_id, added))
            if plan["has_note"] and plan["note"] != held["note"]:
                conn.execute("UPDATE expectation SET note = ? WHERE id = ?", (plan["note"], expectation_id))
            _name_change(conn, f"Changed what is {_what(held['direction'])} on {r['description']} "
                               f"to {xb.short(amount, r['currency'])}")
        result = {"expectation": expectation_payload(conn, expectation_id), "row": _parts(conn, r),
                  "spending_change": _spending_change(r, delta)}
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return result


def write_undo_mark(conn: sqlite3.Connection, expectation_id: int) -> dict:
    """Undo the mark: only while nothing is set off or let go (the machine)."""
    _begin(conn)
    try:
        x = conn.execute("SELECT * FROM expectation WHERE id = ?", (expectation_id,)).fetchone()
        if x is None:
            raise NotFound("no such expectation")
        xb.delete_expectation(conn, expectation_id)
        if x["row_id"] is not None:
            r = _row(conn, x["row_id"])
            row, change, what = _parts(conn, r), _spending_change(r, -x["amount_minor"]), r["description"]
        else:
            who = conn.execute("SELECT name FROM accounts WHERE id = ?", (x["person_id"],)).fetchone()
            row, change, what = None, None, f"Opening figure · {who[0] if who else '?'}"
        _name_change(conn, f"Undid the mark on {what}")
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return {"removed": {"expectation_id": expectation_id, "row_id": x["row_id"]}, "row": row,
            "spending_change": change}


def drawer(conn: sqlite3.Connection, row_id: int, args) -> dict:
    """What the drawer reads: the row, its mark, the people, why it cannot be
    marked; and with ?amount= (or ?all=1) what would be the operator's own,
    or why it would be refused. Writes nothing."""
    r = _row(conn, row_id)
    held = _mark_on(conn, row_id)
    direction = held["direction"] if held is not None else _direction(r, None)
    out = {"row": {**_row_brief(r), **_parts(conn, r)}, "direction": direction,
           "expectation": expectation_payload(conn, held["id"]) if held is not None else None,
           "people": _people(conn), "refusal": xb.refusal_to_mark(conn, row_id, direction), "would": None}
    all_of_it = args.get("all") in ("1", "true")
    if "amount" not in args and not all_of_it:
        return out
    try:
        asked = _direction(r, args.get("direction") or direction)
        why = xb.refusal_to_mark(conn, row_id, asked)
        if why:
            raise xb.Refused(why)
        amount = _amount(conn, r, all_of_it, args.get("amount"))
        if held is not None:
            back = xb.set_off_minor(conn, held["id"])
            if amount < back:
                raise xb.Refused(f"{xb.full(back, r['currency'])} has already come back")
        delta = amount - (held["amount_minor"] if held is not None else 0)
        own = out["row"]["own_minor"] - _sign(r) * delta
        out["would"] = {"expected_minor": amount, "own_minor": own, "yours": _yours(r, asked, own), "error": None}
    except xb.Refused as e:
        out["would"] = {"expected_minor": None, "own_minor": None, "yours": None, "error": str(e)}
    return out


# --- routes --------------------------------------------------------------------------


def _refused(words, status: int = 400):
    return jsonify({"error": str(words)}), status


def register(bp) -> None:
    """Add this lane's routes to the expect-back blueprint."""

    @bp.route("/api/transactions/<int:tx_id>/expectation", methods=["GET"])
    def mark_drawer(tx_id: int):
        with get_db() as conn:
            try:
                return jsonify(drawer(conn, tx_id, request.args))
            except NotFound as e:
                return _refused(e, 404)

    @bp.route("/api/transactions/<int:tx_id>/expectation", methods=["POST"])
    def mark(tx_id: int):
        """Expect some back… (or Expect to pay back): {direction, all, amount,
        names, new_names, note}. Marks the row, or edits the mark it has."""
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _refused("send the mark as JSON")
        with get_db() as conn:
            try:
                return jsonify(write_mark(conn, tx_id, body))
            except NotFound as e:
                return _refused(e, 404)
            except xb.Refused as e:
                return _refused(e)

    @bp.route("/api/expectations/<int:expectation_id>", methods=["DELETE"])
    def undo_mark(expectation_id: int):
        """Undo the mark: refused, naming what is set against it, until unlinked."""
        with get_db() as conn:
            try:
                return jsonify(write_undo_mark(conn, expectation_id))
            except NotFound as e:
                return _refused(e, 404)
            except xb.Refused as e:
                return _refused(e, 409)
