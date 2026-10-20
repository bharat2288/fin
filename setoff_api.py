"""E2, the set-off routes (tickets 36, 39): Back for..., Unlink.

One lane's routes on the expect-back blueprint (expect_routes.py), which
registers this module with the others. No module here imports app.py.

- GET  /api/transactions/<id>/back-for   what one payment may be set off
  against: the open items of the other direction, nearest the payment's day
  first, each with what is still open (the open list's shape, tests/fixtures/
  expect/open_list.json). A read: it writes nothing and fetches nothing.
- POST /api/transactions/<id>/set-offs   one Back for... request: every
  placement of one payment, all written or none, as one change. What is not
  placed stays on the payment as its own part, to label, or waits for its row
  when the request carries a waiting part (waiting_api, ticket 40). A wording
  of the sender is learnt when exactly one name on the ticked items matches it.
- DELETE /api/set-offs/<id>              Unlink one set-off, as one change;
  on a waiting set-off this is Un-wait.

Every guard (the payment's remainder, each item's open amount and state) is
read inside the transaction that writes, under the change history's write
lock, which app.py holds for every write request. The writes themselves are
expect_back's guarded ones, so the machine's refusals and words are its.

Amounts are whole minor units of the payment's currency; a request carries
them as decimal text, parsed by money.parse_minor, never as a float. A
placement on an item kept in another currency converts at the payment's
month's rate (placement_rate: an ended month's own month-end rate, or the
month still running at the latest rate saved by the day of placing), through
money's one inverse, half-even; the record keeps both amounts, the rate text
and its date, and is never restated. An ended month with no saved rate is
refused naming the month, with what the existing rates fetch would save
(`fetch`). Nothing here fetches.

A request may ask for a preview (`preview: true`): the same write, answered
and then rolled back, so the drawer's rate line shows exactly what Set off
would store, from this module's one conversion, and writes nothing.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from flask import jsonify, request

import book_type
import db
import expect_back
import money
import placement_rate
import rates
import waiting_api
from expect_back import Refused

# endpoint (without the blueprint's "expect." prefix) -> what the change did
SUMMARIES: dict[str, str] = {
    "set_off": "Set off a payment",
    "unlink_set_off": "Unlinked a payment",
}

NO_PLACEMENTS = "tick at least one item to set this payment off against"
BAD_PLACEMENT = "each placement names an expectation and an amount"
BAD_PREVIEW = "preview is true or left out"

# Words a bank puts before the sender's name, and nothing else: a learnt
# wording is the sender's name as the statement prints it, without them and
# without the reference numbers after it.
_LEAD_WORDS = frozenset({
    "PAYNOW", "FROM", "TO", "INWARD", "INCOMING", "FAST", "TRANSFER", "TRF", "FUND", "FUNDS",
    "PAYMENT", "GIRO", "IBG", "OTHR", "FT", "IB", "REMITTANCE", "BY",
})


def today() -> str:
    """The day of placing, YYYY-MM-DD, the server's own. Tests replace this;
    nothing else here reads the clock."""
    return date.today().isoformat()


class NotFound(LookupError):
    """The row or the record asked for is not in the book."""


# --- words --------------------------------------------------------------------


def _short(minor: int, currency: str) -> str:
    return expect_back.short(minor, currency)


def _full(minor: int, currency: str) -> str:
    return expect_back.full(minor, currency)


def _sign(currency: str) -> str:
    return expect_back.SIGNS.get(currency, currency)


def _mask(text: str | None) -> str | None:
    """A card number in an account's name shows its last four only (as the
    rest of fin shows account names)."""
    if text is None:
        return None
    text = re.sub(r"\b(\d{4})[-\s](\d{4})[-\s](\d{4})[-\s](\d{4})\b", r"****-****-****-\4", text)
    return re.sub(r"\b\d{12}(\d{4})\b", r"************\1", text)


def sender_wording(description: str | None) -> str | None:
    """The sender as a payment's text prints them: 'PAYNOW FROM JAY TAN 0412'
    -> 'JAY TAN'. The bank's lead words and trailing references go; None when
    nothing is left ('INWARD PAYNOW 9921')."""
    words = re.findall(r"[A-Z0-9&'.-]+", (description or "").upper())
    while words and words[0] in _LEAD_WORDS:
        words.pop(0)
    while words and any(ch.isdigit() for ch in words[-1]):
        words.pop()
    return " ".join(words) or None


# --- reads --------------------------------------------------------------------


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
    """The payment as a write's answer carries it."""
    return {k: p[k] for k in ("id", "amount_minor", "currency", "expected_minor", "placed_minor",
                              "own_minor", "left_minor")}


def _learnt_person(conn: sqlite3.Connection, description: str) -> dict | None:
    """The person a learnt wording in this text names, when exactly one does."""
    text = (description or "").upper()
    found = {}
    for person_id, words, name in conn.execute(
        "SELECT w.person_id, w.wording, a.name FROM sender_wording w JOIN accounts a ON a.id = w.person_id"
    ):
        if expect_back._word(words).search(text):
            found[person_id] = name
    if len(found) != 1:
        return None
    [(person_id, name)] = found.items()
    return {"account_id": person_id, "name": name}


def _direction_paid(payment: dict) -> str:
    """The direction of the items a payment sets off: money in pays back what
    is expected back; money out pays what is to pay back."""
    return expect_back.BACK if payment["amount_minor"] < 0 else expect_back.PAY


def _own_direction(payment: dict) -> str:
    """The direction an expectation on the payment row itself would take: the
    eligibility rule for a payment is that one (spec 23: a set-off's payment
    row obeys the same exclusions)."""
    return expect_back.PAY if payment["amount_minor"] < 0 else expect_back.BACK


def _state(amount: int, placed: int, let_go: int) -> str:
    if let_go:
        return expect_back.LET_GO
    if placed == 0:
        return expect_back.OPEN
    return expect_back.CLOSED if placed >= amount else expect_back.PARTLY_BACK


# Every open item of one direction, in one read: nothing let go, something
# still open. The set-off and let-go sums are joined, not read per item.
_ITEMS = """
    SELECT e.id, e.row_id, e.person_id, e.opening_date, e.direction, e.amount_minor, e.currency,
           e.note, e.source, e.made_at, COALESCE(t.date, e.opening_date) AS day,
           COALESCE(so.total, 0) AS set_off_minor, COALESCE(l.amount_minor, 0) AS let_go_minor,
           t.date AS row_date, t.description, t.amount_minor AS row_amount, t.type_id,
           COALESCE(t.book, ?) AS book, s.account_id, a.name AS account_name,
           COALESCE(a.currency, 'SGD') AS row_currency, ty.name AS type_name, pt.name AS parent_name,
           op.name AS opening_name
    FROM expectation e
    LEFT JOIN (SELECT expectation_id, SUM(item_amount_minor) AS total FROM set_off
               WHERE expectation_id IS NOT NULL GROUP BY expectation_id) so ON so.expectation_id = e.id
    LEFT JOIN let_go l ON l.expectation_id = e.id
    LEFT JOIN transactions t ON t.id = e.row_id
    LEFT JOIN statements s ON s.id = t.statement_id
    LEFT JOIN accounts a ON a.id = s.account_id
    LEFT JOIN types ty ON ty.id = t.type_id
    LEFT JOIN types pt ON pt.id = ty.parent_id
    LEFT JOIN accounts op ON op.id = e.person_id
    WHERE e.direction = ? AND l.id IS NULL
      AND e.amount_minor - COALESCE(so.total, 0) > 0
"""


def _names(conn: sqlite3.Connection, expectation_ids) -> dict[int, list[dict]]:
    """The names on each expectation, in the order they were given; one read."""
    ids = [int(i) for i in expectation_ids]
    out: dict[int, list[dict]] = {i: [] for i in ids}
    if not ids:
        return out
    marks = ",".join("?" for _ in ids)
    for expectation_id, account_id, name in conn.execute(
        "SELECT n.expectation_id, a.id, a.name FROM expectation_name n JOIN accounts a ON a.id = n.person_id "
        f"WHERE n.expectation_id IN ({marks}) ORDER BY n.id",
        ids,
    ):
        out[expectation_id].append({"account_id": account_id, "name": name})
    return out


def _item(r, names: list[dict]) -> dict:
    """One open item in the open list's shape."""
    row = opening = None
    if r["row_id"] is not None:
        row = {
            "id": r["row_id"], "date": r["row_date"], "description": r["description"],
            "account_id": r["account_id"], "account_name": _mask(r["account_name"]),
            "amount_minor": r["row_amount"], "currency": r["row_currency"], "book": r["book"],
            "type_id": r["type_id"],
            "display_type": book_type.display_name(r["type_name"], r["parent_name"]) if r["type_name"] else None,
        }
    else:
        opening = {"person": {"account_id": r["person_id"], "name": r["opening_name"]}, "date": r["opening_date"]}
    return {
        "id": r["id"], "direction": r["direction"], "amount_minor": r["amount_minor"],
        "currency": r["currency"], "note": r["note"], "source": r["source"], "made_at": r["made_at"],
        "day": r["day"], "names": names, "set_off_minor": r["set_off_minor"],
        "let_go_minor": r["let_go_minor"],
        "open_minor": r["amount_minor"] - r["set_off_minor"] - r["let_go_minor"],
        "state": _state(r["amount_minor"], r["set_off_minor"], r["let_go_minor"]),
        "row": row, "opening": opening,
    }


def back_for(conn: sqlite3.Connection, tx_id: int) -> dict:
    """What Back for... lists for one payment: the payment, its sender, and
    every open item of the other direction, nearest the payment's day first.
    Items in another currency are listed too; setting off across currencies
    is the rate step's. Raises NotFound, and Refused when the row can pay
    nothing (a move between own accounts, a company's row)."""
    payment = _payment(conn, tx_id)
    why = expect_back.refusal_to_mark(conn, tx_id, _own_direction(payment))
    if why:
        raise Refused(why)
    rows = conn.execute(_ITEMS, (book_type.DEFAULT_BOOK, _direction_paid(payment))).fetchall()
    names = _names(conn, [r["id"] for r in rows])
    near = date.fromisoformat(payment["date"])
    items = sorted((_item(r, names[r["id"]]) for r in rows),
                   key=lambda e: (abs((date.fromisoformat(e["day"]) - near).days), e["day"], e["id"]))
    shown = {k: payment[k] for k in ("id", "amount_minor", "currency", "expected_minor", "placed_minor",
                                     "own_minor", "description", "date", "left_minor")}
    shown["sender"] = {"wording": sender_wording(payment["description"]),
                       "person": _learnt_person(conn, payment["description"])}
    return {"direction": _direction_paid(payment), "near": payment["date"], "payment": shown, "items": items}


def _brief(conn: sqlite3.Connection, expectation_id: int) -> dict:
    x = conn.execute("SELECT id, amount_minor, currency FROM expectation WHERE id = ?",
                     (expectation_id,)).fetchone()
    return {"id": x["id"], "amount_minor": x["amount_minor"], "currency": x["currency"],
            "open_minor": expect_back.open_minor(conn, expectation_id),
            "state": expect_back.state(conn, expectation_id)}


def _set_offs(conn: sqlite3.Connection, set_off_ids, month_ended: dict[int, bool]) -> list[dict]:
    """Set-offs as a write's answer carries them, in the order asked.
    `month_ended` says, for each set-off placed at a rate, whether the
    payment's month had ended when it was placed (its own month-end rate) or
    was still running (the latest rate then): the write's own rate answer,
    never the stored made_at, which is UTC and not the day of placing."""
    ids = [int(i) for i in set_off_ids]
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    found = {
        r["id"]: r for r in conn.execute(
            "SELECT so.*, t.date, COALESCE(a.currency, 'SGD') AS currency, e.currency AS exp_currency, "
            "p.name AS person_name "
            "FROM set_off so JOIN transactions t ON t.id = so.payment_row_id "
            "JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id "
            "LEFT JOIN expectation e ON e.id = so.expectation_id LEFT JOIN accounts p ON p.id = so.person_id "
            f"WHERE so.id IN ({marks})", ids)
    }
    out = []
    for i in ids:
        r = found[i]
        waiting = r["expectation_id"] is None
        out.append({
            "id": r["id"], "payment_row_id": r["payment_row_id"], "expectation_id": r["expectation_id"],
            "date": r["date"], "amount_minor": r["amount_minor"], "currency": r["currency"],
            # A waiting set-off has no expectation yet, so no amount in one.
            "exp_amount_minor": None if waiting else r["item_amount_minor"],
            "exp_currency": None if waiting else (r["exp_currency"] or r["currency"]),
            "rate": r["rate"], "rate_date": r["rate_date"],
            "rate_month_ended": None if r["rate"] is None else month_ended[i],
            "source": r["source"],
            "person": {"account_id": r["person_id"], "name": r["person_name"]} if r["person_id"] else None,
            "note": r["note"], "state": "waiting" if waiting else "placed",
        })
    return out


# --- the write ----------------------------------------------------------------


def _begin(conn: sqlite3.Connection) -> None:
    """Hold the database's write lock from the first guard read."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def _checked_placements(conn: sqlite3.Connection, placements, currency: str) -> list[dict]:
    """Each placement read and parsed: its expectation, its amount in the
    payment's currency, and what the drawer showed open, when it says."""
    if not isinstance(placements, list) or not placements:
        raise Refused(NO_PLACEMENTS)
    out, seen = [], set()
    for p in placements:
        if not isinstance(p, dict):
            raise Refused(BAD_PLACEMENT)
        expectation_id, text = p.get("expectation_id"), p.get("amount")
        if isinstance(expectation_id, bool) or not isinstance(expectation_id, int) or not isinstance(text, str):
            raise Refused(BAD_PLACEMENT)
        x = conn.execute(
            "SELECT e.id, e.row_id, e.currency, t.description, a.name AS person_name FROM expectation e "
            "LEFT JOIN transactions t ON t.id = e.row_id LEFT JOIN accounts a ON a.id = e.person_id "
            "WHERE e.id = ?", (expectation_id,)).fetchone()
        if x is None:
            raise Refused("no such expectation")
        name = x["description"] if x["row_id"] is not None else f"Opening figure · {x['person_name']}"
        if expectation_id in seen:
            raise Refused(f"{name} is ticked twice")
        seen.add(expectation_id)
        try:
            amount = money.parse_minor(text, currency)
        except ValueError:
            raise Refused(f"{text} is not an amount in whole cents") from None
        if amount <= 0:
            raise Refused("the amount must be more than nothing")
        shown = p.get("open_minor")
        if isinstance(shown, bool) or not isinstance(shown, int):
            shown = None
        out.append({"expectation_id": expectation_id, "amount_minor": amount, "name": name,
                    "currency": x["currency"], "shown_open_minor": shown})
    return out


def _learn(conn: sqlite3.Connection, payment: dict, expectation_ids) -> dict | None:
    """Teach fin the sender's wording when exactly one person named on the
    ticked items matches the payment's text (by their name, or a wording
    already learnt for them). A wording already learnt is kept as it is."""
    words = sender_wording(payment["description"])
    if words is None:
        return None
    text = (payment["description"] or "").upper()
    named = {}
    for people in _names(conn, expectation_ids).values():
        for n in people:
            named[n["account_id"]] = n["name"]
    learnt: dict[int, list[str]] = {}
    for person_id, wording in conn.execute("SELECT person_id, wording FROM sender_wording"):
        learnt.setdefault(person_id, []).append(wording)
    matching = [
        (person_id, name) for person_id, name in named.items()
        if expect_back._word(name).search(text)
        or any(expect_back._word(w).search(text) for w in learnt.get(person_id, ()))
    ]
    if len(matching) != 1:
        return None
    if conn.execute("SELECT 1 FROM sender_wording WHERE wording = ?", (words,)).fetchone():
        return None
    [(person_id, name)] = matching
    conn.execute("INSERT INTO sender_wording (wording, person_id) VALUES (?, ?)", (words, person_id))
    return {"wording": words, "person": {"account_id": person_id, "name": name}}


def _convert(conn: sqlite3.Connection, p: dict, payment: dict, placed_on: str) -> None:
    """Fill in a placement's amount in its item's currency and the rate it
    was placed at (None in one currency): the payment's month's rate, by
    placement_rate, which raises NoRate (nothing written) when the rule
    allows no saved rate."""
    cur = payment["currency"]
    if p["currency"] == cur:
        p["item_amount_minor"], p["rate"] = p["amount_minor"], None
        return
    try:
        placed = placement_rate.place(conn, p["amount_minor"], cur, p["currency"], payment["date"], placed_on)
    except (rates.InvalidRate, money.UnknownCurrency):
        raise Refused(f"{p['name']} is kept in {_sign(p['currency'])} and this payment in {_sign(cur)}: "
                      "fin keeps no rate between them") from None
    if placed["amount_minor"] <= 0:
        raise Refused(f"{_full(p['amount_minor'], cur)} is less than {_full(1, p['currency'])} "
                      f"at {placed['rate']['rate']}")
    p["item_amount_minor"], p["rate"] = placed["amount_minor"], placed["rate"]


def set_off_payment(conn: sqlite3.Connection, tx_id: int, placements, waiting=None) -> dict:
    """Write one Back for... request: every placement, and the waiting part
    when it carries one, or none of it. A waiting part ({person_id?, note?,
    amount?}) leaves the rest of the payment (or the amount it names) waiting
    for its row, in the same change; with one, the request may place nothing.
    Nothing here commits; the route does, once everything is written, and
    rolls back on any refusal."""
    _begin(conn)
    payment = _payment(conn, tx_id)
    why = expect_back.refusal_to_mark(conn, tx_id, _own_direction(payment))
    if why:
        raise Refused(why)
    part = waiting_api.checked_waiting(conn, waiting, payment["currency"]) if waiting is not None else None
    if part is not None and placements == []:
        wanted = []
    else:
        wanted = _checked_placements(conn, placements, payment["currency"])
    cur, left = payment["currency"], payment["left_minor"]
    if left == 0:
        raise Refused(f"{_short(0, cur)} left on this payment")
    total = sum(p["amount_minor"] for p in wanted)
    if total > left:
        raise Refused(f"{_short(total, cur)} of {_short(left, cur)} placed · "
                      f"{_short(total - left, cur)} more than the payment")
    placed_on = today()
    for p in wanted:
        _convert(conn, p, payment, placed_on)
        # What the drawer showed open has changed since (another tab placed
        # money on it): say so, rather than the bare figure.
        if p["shown_open_minor"] is not None and expect_back.state(conn, p["expectation_id"]) != expect_back.LET_GO:
            now = expect_back.open_minor(conn, p["expectation_id"])
            if p["item_amount_minor"] > now and now != p["shown_open_minor"]:
                raise Refused(f"{p['name']} has {_full(now, p['currency'])} open now")
    made, month_ended = [], {}
    for p in wanted:
        rate = p["rate"]
        if rate is None:
            made.append(expect_back.insert_set_off(conn, tx_id, p["expectation_id"], p["amount_minor"]))
            continue
        set_off_id = expect_back.insert_set_off(
            conn, tx_id, p["expectation_id"], p["amount_minor"], item_amount_minor=p["item_amount_minor"],
            rate=rate["rate"], rate_date=rate["date"])
        made.append(set_off_id)
        month_ended[set_off_id] = not rate["running"]
    waiting_id = waiting_api.wait(conn, tx_id, part) if part is not None else None
    learnt = _learn(conn, payment, [p["expectation_id"] for p in wanted])
    out = {
        "payment": _parts(_payment(conn, tx_id)),
        "set_offs": _set_offs(conn, made, month_ended),
        "expectations": [_brief(conn, p["expectation_id"]) for p in wanted],
        "learnt": learnt,
    }
    if part is not None:
        out["waiting"] = _set_offs(conn, [waiting_id], {})[0]
    return out


def unlink(conn: sqlite3.Connection, set_off_id: int) -> dict:
    """Unlink one set-off; never while its item is let go."""
    _begin(conn)
    found = conn.execute("SELECT payment_row_id, expectation_id FROM set_off WHERE id = ?",
                         (set_off_id,)).fetchone()
    if found is None:
        raise NotFound("no such set-off")
    payment_row_id, expectation_id = found
    waiting_api.name_unwait(conn, set_off_id)
    expect_back.delete_set_off(conn, set_off_id)
    return {
        "removed": {"set_off_id": set_off_id, "expectation_id": expectation_id, "payment_row_id": payment_row_id},
        "payment": _parts(_payment(conn, payment_row_id)),
        "expectation": _brief(conn, expectation_id) if expectation_id is not None else None,
    }


# --- routes -------------------------------------------------------------------


def _answer(work, *, preview: bool = False):
    """Run one route's work on its own connection: a refusal or a miss writes
    nothing (rolled back); a preview is answered and rolled back; otherwise
    commit, then answer. No saved rate is refused with what the existing rates
    fetch would save: {"error", "fetch": {"currency", "date"}}."""
    with db.get_db() as conn:
        try:
            out = work(conn)
        except NotFound as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 404
        except Refused as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 400
        except placement_rate.NoRate as e:
            conn.rollback()
            fetch = {"currency": e.pair.split("/")[0], "date": e.fetch_on}
            return jsonify({"error": str(e), "fetch": fetch}), 400
        if preview:
            conn.rollback()
        else:
            conn.commit()
    return jsonify(out)


def api_back_for(tx_id: int):
    """GET: what one payment may be set off against (Back for...)."""
    return _answer(lambda conn: back_for(conn, tx_id))


def api_set_off(tx_id: int):
    """POST {placements: [{expectation_id, amount, open_minor?}], waiting: null
    | {person_id?, note?, amount?}, preview?: true}: set one payment off against
    every ticked item, and leave the rest waiting for its row when asked, as one
    change. amount is decimal text in the payment's currency; open_minor, when
    the drawer sends it, is what it showed open, so a change made since is
    named as such. preview: answer as the write would, and write nothing."""
    data = request.get_json(silent=True) or {}
    preview = data.get("preview", False)
    if not isinstance(preview, bool):
        return jsonify({"error": BAD_PREVIEW}), 400
    return _answer(lambda conn: set_off_payment(conn, tx_id, data.get("placements"), data.get("waiting")),
                   preview=preview)


def api_unlink(set_off_id: int):
    """DELETE: Unlink one set-off, as one change."""
    return _answer(lambda conn: unlink(conn, set_off_id))


def register(bp) -> None:
    """Add this lane's routes to the expect-back blueprint."""
    bp.add_url_rule("/api/transactions/<int:tx_id>/back-for", endpoint="back_for",
                    view_func=api_back_for, methods=["GET"])
    bp.add_url_rule("/api/transactions/<int:tx_id>/set-offs", endpoint="set_off",
                    view_func=api_set_off, methods=["POST"])
    bp.add_url_rule("/api/set-offs/<int:set_off_id>", endpoint="unlink_set_off",
                    view_func=api_unlink, methods=["DELETE"])
