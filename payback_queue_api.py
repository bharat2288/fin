"""E6, the Queue's paybacks (tickets 41, 42).

One lane's routes on the expect-back blueprint (expect_routes.py), which
registers this module with the others; each writing route's change is named
in SUMMARIES (endpoint name within the blueprint -> the words Changes shows).
No module here imports app.py. The rule is payback_queue.py's:

  GET  /api/queue/paybacks                   the lane read: each money-in row
       waiting for its label, with its best matches and their reasons
       (tests/fixtures/expect/queue_paybacks.json). Writes nothing.
  POST /api/transactions/<id>/not-a-payback  a dismissal of kind `payback`:
       the row is never suggested again and stays a row to label. One change,
       undone from Changes.

The Queue's ✓ Set off posts setoff_api's route, and a refund ask's ✓ posts
refund_link_api's hand link: this lane adds no second way to write either.
A refusal is a 4xx with {"error": words} and writes nothing.
"""

from __future__ import annotations

from flask import jsonify

import db
import expect_back
import payback_queue

# endpoint (without the blueprint's "expect." prefix) -> what the change did
SUMMARIES: dict[str, str] = {
    "not_a_payback": "Not a payback",
}

ONLY_MONEY_IN = "only money in is offered as a payback"


class NotFound(LookupError):
    """The row asked for is not in the book."""


def not_a_payback(conn, tx_id: int) -> dict:
    """Set a money-in row aside from the payback suggestions. Every guard is
    read inside the transaction that writes, under the write lock app.py
    holds for every write request. Nothing here commits."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    found = conn.execute("SELECT amount_minor, description FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if found is None:
        raise NotFound("no such row")
    if found["amount_minor"] >= 0:
        raise expect_back.Refused(ONLY_MONEY_IN)
    why = expect_back.refusal_to_mark(conn, tx_id, expect_back.PAY)
    if why:
        raise expect_back.Refused(why)
    if conn.execute("SELECT 1 FROM dismissal WHERE row_id = ? AND kind = ?",
                    (tx_id, payback_queue.NOT_A_PAYBACK)).fetchone():
        raise expect_back.Refused(f"{found['description']} is already set aside as not a payback")
    dismissal_id = conn.execute("INSERT INTO dismissal (row_id, kind) VALUES (?, ?)",
                                (tx_id, payback_queue.NOT_A_PAYBACK)).lastrowid
    made = conn.execute("SELECT id, row_id, kind, purchase_row_id, made_at FROM dismissal WHERE id = ?",
                        (dismissal_id,)).fetchone()
    return {"dismissal": dict(made)}


def api_paybacks():
    """GET: the Queue's payback suggestions, one read for the lane."""
    with db.get_db() as conn:
        return jsonify(payback_queue.paybacks(conn))


def api_not_a_payback(tx_id: int):
    """POST {}: Not a payback, as one change."""
    with db.get_db() as conn:
        try:
            out = not_a_payback(conn, tx_id)
        except NotFound as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 404
        except expect_back.Refused as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 400
        conn.commit()
    return jsonify(out)


def register(bp) -> None:
    """Add this lane's routes to the expect-back blueprint."""
    bp.add_url_rule("/api/queue/paybacks", endpoint="queue_paybacks", view_func=api_paybacks, methods=["GET"])
    bp.add_url_rule("/api/transactions/<int:tx_id>/not-a-payback", endpoint="not_a_payback",
                    view_func=api_not_a_payback, methods=["POST"])
