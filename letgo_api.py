"""E3, the let-go routes (ticket 37): let go, undo the let-go.

One lane's routes on the expect-back blueprint (expect_routes.py), which
registers this module with the others. No module here imports app.py.

Let it go… is the operator's act alone (spec 23, Solution 5; E3). It takes an
expectation's whole open rest and counts it on the day it is let go, so a
month already read does not change:

- a back item (money out, expected back) becomes "mine after all", spending in
  its row's type and book (a type is asked for when the row, or an opening
  figure, has none), or "a gift", Household, under the type fin files gifts
  given under (book_type's gift_given slot), to whom if named;
- a pay item (money in, to pay back) becomes only "a gift to me", income in
  the Household with the income type the operator picks (case 26d).

Undo the let-go opens the rest again. Each is one change in the history,
undoable from Changes; every refusal writes nothing. The guarded writes, the
machine and its words are expect_back.py's; every guard is read inside the
write's own transaction.
"""

from __future__ import annotations

from datetime import date

from flask import jsonify, request

import account_kind
import book_type
import db
import expect_back

# endpoint (without the blueprint's "expect." prefix) -> what the change did
SUMMARIES: dict[str, str] = {
    "api_let_go": "Let what was still open go",
    "api_undo_let_go": "Undid a let-go",
}

PICK = "pick mine or a gift"


def today() -> str:
    """The day a let-go counts on: the day it is let go."""
    return date.today().isoformat()


def _type(conn, type_id) -> dict | None:
    """A type's id, kind and display name, or None."""
    if isinstance(type_id, bool) or not isinstance(type_id, int):
        return None
    found = conn.execute(
        "SELECT t.id, t.kind, t.name, p.name FROM types t LEFT JOIN types p ON p.id = t.parent_id WHERE t.id = ?",
        (type_id,),
    ).fetchone()
    if found is None:
        return None
    return {"id": found[0], "kind": found[1], "display": book_type.display_name(found[2], found[3])}


def _what_it_became(conn, x, became: str, type_id, to_id) -> tuple[str, int]:
    """(book, type id) the let-go counts in, or Refused in the operator's words."""
    if x["direction"] == expect_back.PAY:
        if became != expect_back.GIFT:
            raise expect_back.Refused("money in was never spent: let it go only as a gift to me")
        if to_id is not None:
            raise expect_back.Refused("a gift to me is to no one")
        picked = _type(conn, type_id)
        if picked is None or picked["kind"] != book_type.INCOME:
            raise expect_back.Refused("a gift to me is income: pick an income type")
        return account_kind.HOUSEHOLD, picked["id"]
    if became == expect_back.GIFT:
        gifts = book_type.slot_type(conn, book_type.GIFT_GIVEN_SLOT)
        if type_id is not None and type_id != gifts:
            raise expect_back.Refused(f"a gift counts as {book_type.spending_type_names(conn)[gifts]}")
        return account_kind.HOUSEHOLD, gifts
    # Mine after all: the row's book and type; a type is asked for when there is none.
    row = (conn.execute("SELECT book, type_id FROM transactions WHERE id = ?", (x["row_id"],)).fetchone()
           if x["row_id"] is not None else None)
    book = (row["book"] if row is not None else None) or book_type.DEFAULT_BOOK
    if type_id is None:
        type_id = row["type_id"] if row is not None else None
        if type_id is None:
            raise expect_back.Refused(f"{expect_back._item_name(x)} has no type: pick the type it was")
    picked = _type(conn, type_id)
    if picked is None or picked["kind"] != book_type.SPENDING:
        raise expect_back.Refused("mine is spending: pick a spending type")
    return book, picked["id"]


def _brief(conn, expectation_id: int) -> dict:
    """The expectation as the drawer redraws it: amount, open rest and state now."""
    x = expect_back._expectation(conn, expectation_id)
    return {"id": x["id"], "amount_minor": x["amount_minor"], "currency": x["currency"],
            "open_minor": expect_back.open_minor(conn, expectation_id),
            "state": expect_back.state(conn, expectation_id)}


def _let_go_out(conn, let_go_id: int) -> dict:
    lg = conn.execute(
        "SELECT l.*, e.currency, a.name AS to_name FROM let_go l JOIN expectation e ON e.id = l.expectation_id "
        "LEFT JOIN accounts a ON a.id = l.to_whom_id WHERE l.id = ?",
        (let_go_id,),
    ).fetchone()
    return {
        "id": lg["id"], "expectation_id": lg["expectation_id"], "amount_minor": lg["amount_minor"],
        "currency": lg["currency"], "became": lg["became"], "book": lg["book"], "type_id": lg["type_id"],
        "display_type": _type(conn, lg["type_id"])["display"],
        "to": {"account_id": lg["to_whom_id"], "name": lg["to_name"]} if lg["to_whom_id"] is not None else None,
        "counts_on": lg["counts_on"], "made_at": lg["made_at"],
    }


def _refused(conn, words: str, status: int = 400):
    """Nothing written: the transaction goes back, the words go out."""
    conn.rollback()
    return jsonify({"error": words}), status


def api_let_go(expectation_id: int):
    """Let it go…: the whole open rest, counted today.

    Body {became: "mine" | "gift"; to_id: a gift's person account, or null;
    type_id: null, or the type asked for (mine on a row with no type; a gift
    to me's income type)}. No default: neither picked is refused. Returns
    {let_go, expectation: {id, amount_minor, currency, open_minor, state},
    open_before_minor}."""
    body = request.get_json(silent=True) or {}
    became, to_id, type_id = body.get("became"), body.get("to_id"), body.get("type_id")
    with db.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        x = expect_back._expectation(conn, expectation_id)
        if x is None:
            return _refused(conn, "no such expectation", 404)
        try:
            if became not in expect_back.BECAME:
                raise expect_back.Refused(PICK)
            expect_back._refused_event(conn, x, "let_go")
            book, type_id = _what_it_became(conn, x, became, type_id, to_id)
            open_before = expect_back.open_minor(conn, expectation_id)
            let_go_id = expect_back.insert_let_go(conn, expectation_id, became, book=book, type_id=type_id,
                                                  counts_on=today(), to_whom_id=to_id)
        except expect_back.Refused as e:
            return _refused(conn, str(e))
        out = {"let_go": _let_go_out(conn, let_go_id), "expectation": _brief(conn, expectation_id),
               "open_before_minor": open_before}
        conn.commit()
    return jsonify(out)


def api_undo_let_go(expectation_id: int):
    """Undo the let-go: the open rest is open again. Returns {expectation}."""
    with db.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if expect_back._expectation(conn, expectation_id) is None:
            return _refused(conn, "no such expectation", 404)
        try:
            expect_back.delete_let_go(conn, expectation_id)
        except expect_back.Refused as e:
            return _refused(conn, str(e))
        out = {"expectation": _brief(conn, expectation_id)}
        conn.commit()
    return jsonify(out)


def register(bp) -> None:
    """Add this lane's routes to the expect-back blueprint."""
    bp.add_url_rule("/api/expectations/<int:expectation_id>/let-go", "api_let_go", api_let_go,
                    methods=["POST"])
    bp.add_url_rule("/api/expectations/<int:expectation_id>/let-go", "api_undo_let_go", api_undo_let_go,
                    methods=["DELETE"])
