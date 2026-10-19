"""E7, refund links on demand and by hand (tickets 38, 43).

One lane's routes on the expect-back blueprint (expect_routes.py), which
registers this module with the others; each writing route's change is named
in SUMMARIES (endpoint name within the blueprint -> the words Changes shows).
No module here imports app.py. The rule is refund_links.py's:

  POST /api/refund-links/run     link every refund one purchase fits: one
                                 change ({linked, asks}); none when nothing links
  GET  /api/refund-links/asks    the refunds two or more purchases fit, with them
  POST /api/refund-links         {refund_row_id, purchase_row_id}: a hand link,
                                 one change ({refund_link})
  GET  /api/refund-links?row=<id>  a refund row's link, its purchase and the
                                 row sheet's words ("refund of 20 Jul")

A refusal is a 4xx with {"error": words} and writes nothing.
"""

from __future__ import annotations

from flask import jsonify, request

import refund_links
from db import get_db

# endpoint (without the blueprint's "expect." prefix) -> what the change did
SUMMARIES: dict[str, str] = {
    "refund_links_run": "Linked refunds to their purchases",
    "refund_link_by_hand": "Linked a refund by hand",
}


def run():
    with get_db() as conn:
        made = refund_links.link_refunds(conn)
        conn.commit()
        asked = len(refund_links.asks(conn))
    return jsonify({"linked": made, "asks": asked})


def asks():
    with get_db() as conn:
        return jsonify({"asks": refund_links.asks(conn)})


def by_hand():
    body = request.get_json(silent=True) or {}
    ids = [body.get("refund_row_id"), body.get("purchase_row_id")]
    if any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
        return jsonify({"error": "refund_row_id and purchase_row_id are row ids"}), 400
    with get_db() as conn:
        try:
            made = refund_links.hand_link(conn, *ids)
        except refund_links.Refused as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 409
        conn.commit()
    return jsonify({"refund_link": made})


def of_row():
    row_id = request.args.get("row", type=int)
    if row_id is None:
        return jsonify({"error": "row is a row id"}), 400
    with get_db() as conn:
        return jsonify(refund_links.link_of(conn, row_id))


def register(bp) -> None:
    """Add this lane's routes to the expect-back blueprint."""
    bp.add_url_rule("/api/refund-links/run", "refund_links_run", run, methods=["POST"])
    bp.add_url_rule("/api/refund-links/asks", "refund_link_asks", asks, methods=["GET"])
    bp.add_url_rule("/api/refund-links", "refund_link_by_hand", by_hand, methods=["POST"])
    bp.add_url_rule("/api/refund-links", "refund_link_of_row", of_row, methods=["GET"])
