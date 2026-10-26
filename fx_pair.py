"""Cross-currency pair offers (fin-redesign 24; spec part 2, cases 10 and 13;
fin RUL-022).

Pairing (pairing.py) needs exactly opposite amounts, so a move from S$ into
rupees never pairs. Here the Queue is offered such a move instead: an S$ row
out and a row in another currency in, both on household bank accounts, a day
apart at most, whose rate is within BAND of that day's ECB rate (the saved
rate valuation.py would value the row in at). The offer's rate is the real
one: an RFX rate in the in row's wording ("SGD500.0@74.52", rupees a dollar)
when it carries one, else the two amounts'. With no saved rate for the day
nothing is offered, never guessed.

Confirming writes one fx_pair row holding the real rate for both rows; from
then on every S$ reader values the in row at it (valuation.py). A leg still
waiting (review) becomes a transfer naming the other's account, as pairing
names it, unless the other leg is someone else's money on a shared account
(not ours): then it keeps waiting for its label. A leg already labelled keeps
its label. Not a pair writes a
declined fx_pair row, and that pair is not offered again. Each is one change
in Changes, and its Undo takes the pair (and so its rate) away.

Of several offers sharing a row, the one nearest its ECB rate is offered,
then the nearest in date: a row is in one offer at most.

app.py registers the blueprint with one line, merges SUMMARIES into the change
history's words with another, and reads `shown` for /api/transactions.

  GET  /api/fx-pairs/offers    {offers: [...]}: what the Queue offers now
  POST /api/fx-pairs           {out_id, in_id}: confirm an offer, one change
  POST /api/fx-pairs/decline   {out_id, in_id}: Not a pair, one change

A refusal is a 4xx with {"error": words} and writes nothing.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from flask import Blueprint, g, jsonify, request

import account_kind
import expect_back
import flow
import money
import rates
import valuation
from db import get_db

# How far an offer's rate may be from the day's ECB rate, as a share of it.
BAND = Decimal("0.03")
# How many days apart the two legs may be dated.
WINDOW_DAYS = 1
# The flows a leg may have: money moving, never spending or income. A row
# that is someone else's on a shared account (#2124's rupee leg on 6324, hers)
# is a leg too, and keeps its flow.
PAIRABLE_FLOWS = (flow.REVIEW, "transfer", flow.MOVEMENT, flow.NOT_OURS)
# What a waiting leg becomes once its pair is confirmed.
PAIRED_FLOW = "transfer"

CONFIRMED = "confirmed"
DECLINED = "declined"
FROM_AMOUNTS = "amounts"
FROM_WORDING = "wording"

# An RFX rate in a wording: "SGD500.0@74.52", units of the row's currency for
# one S$.
_RFX = re.compile(r"\bSGD\s?[0-9][0-9,]*(?:\.[0-9]+)?\s?@\s?([0-9]+(?:\.[0-9]+)?)\b")

# The digits a stored rate keeps: enough that an amount of a billion minor
# units is valued to the cent the two amounts say.
_RATE_PLACES = Decimal(1).scaleb(-20)
_SHOWN_PLACES = Decimal("0.01")

NOTHING = "Nothing was changed."
NOT_OFFERED = f"these two rows are not offered as a pair now (one may be paired already, or said not to be). {NOTHING}"


def wording_rate(description: str | None) -> Decimal | None:
    """The RFX rate a wording carries, as units of its currency for one S$,
    or None."""
    found = _RFX.search((description or "").upper())
    if found is None:
        return None
    rate = Decimal(found.group(1))
    return rate if rate > 0 else None


def _per_unit(per_sgd: Decimal) -> Decimal:
    """S$ for one unit, from units for one S$, at the places a rate is kept."""
    with localcontext() as exact:
        exact.prec = 60
        return (Decimal(1) / per_sgd).quantize(_RATE_PLACES, rounding=ROUND_HALF_EVEN)


def _per_sgd(per_unit: Decimal) -> Decimal:
    with localcontext() as exact:
        exact.prec = 60
        return Decimal(1) / per_unit


def _amounts_rate(out_minor: int, out_currency: str, in_minor: int, in_currency: str) -> Decimal:
    """S$ for one unit of the in row's currency, as the two amounts say."""
    sgd = Decimal(out_minor).scaleb(-money.MINOR_UNIT_DIGITS[out_currency])
    units = Decimal(abs(in_minor)).scaleb(-money.MINOR_UNIT_DIGITS[in_currency])
    with localcontext() as exact:
        exact.prec = 60
        return (sgd / units).quantize(_RATE_PLACES, rounding=ROUND_HALF_EVEN)


def shown_rate(per_unit: Decimal) -> str:
    """A rate as the screens say it: units for one S$, to two places."""
    return str(_per_sgd(per_unit).quantize(_SHOWN_PLACES, rounding=ROUND_HALF_EVEN))


def shown(held: str | None) -> dict | None:
    """A row's real rate as /api/transactions lists it, from the JSON its
    query builds (app.py), or None: {per_sgd, currency, source, pair_row_id}."""
    if held is None:
        return None
    pair = json.loads(held)
    return {"per_sgd": shown_rate(Decimal(pair["rate"])), "currency": pair["currency"],
            "source": pair["source"], "pair_row_id": pair["pair_row_id"]}


# --- what is offered ---------------------------------------------------------

_NO_RECORDS = (
    "NOT EXISTS (SELECT 1 FROM expectation e WHERE e.row_id = {row}.id) "
    "AND NOT EXISTS (SELECT 1 FROM set_off so WHERE so.payment_row_id = {row}.id)"
)


def _legs(conn: sqlite3.Connection) -> list[dict]:
    """Every row that may be a leg: on a household bank account, moving
    money, with an amount, in no confirmed pair. A row pairing.py matched
    already (an other side it named, not set by hand) has its S$ counterpart,
    and a row an expectation or a set-off rests on has its money placed
    (pairing.py's rule): neither is a leg."""
    rows = conn.execute(
        "SELECT t.id, t.date, t.description, t.amount_minor, t.flow_type, "
        "       COALESCE(t.flow_type_manual, 0) AS manual, "
        "       a.id AS account_id, a.name AS account_name, COALESCE(a.currency, 'SGD') AS currency "
        "FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        "WHERE a.owner = ? AND a.type = 'bank' "
        f"  AND t.flow_type IN ({','.join('?' * len(PAIRABLE_FLOWS))}) "
        "  AND t.amount_minor IS NOT NULL AND t.amount_minor != 0 "
        "  AND NOT (t.other_side_id IS NOT NULL AND COALESCE(t.flow_type_manual, 0) = 0) "
        f"  AND {_NO_RECORDS.format(row='t')} "
        "  AND NOT EXISTS (SELECT 1 FROM fx_pair p WHERE p.state = 'confirmed' "
        "                  AND (p.out_row_id = t.id OR p.in_row_id = t.id)) "
        "ORDER BY t.date, t.id",
        (account_kind.HOUSEHOLD, *PAIRABLE_FLOWS),
    ).fetchall()
    found = []
    for r in rows:
        try:
            day = date.fromisoformat(r["date"][:10])
        except (TypeError, ValueError):
            continue  # a row with no readable date is nobody's leg
        found.append({**dict(r), "day": day})
    return found


def _declined(conn: sqlite3.Connection) -> set[tuple[int, int]]:
    return {(r[0], r[1]) for r in conn.execute(
        "SELECT out_row_id, in_row_id FROM fx_pair WHERE state = 'declined'")}


def _leg(row: dict) -> dict:
    return {"id": row["id"], "date": row["date"], "description": row["description"],
            "amount_minor": row["amount_minor"], "currency": row["currency"],
            "account_id": row["account_id"], "account": row["account_name"], "flow_type": row["flow_type"]}


def offers(conn: sqlite3.Connection) -> list[dict]:
    """What the Queue offers now, the biggest S$ amount first. Each offer
    {out, in, currency, rate (S$ for one unit, decimal text), per_sgd,
    source, ecb_rate, ecb_per_sgd, off_percent}."""
    legs = _legs(conn)
    outs = [r for r in legs if r["currency"] == rates.QUOTE and r["amount_minor"] > 0]
    ins = [r for r in legs if r["currency"] != rates.QUOTE and r["currency"] in money.MINOR_UNIT_DIGITS
           and r["amount_minor"] < 0]
    if not outs or not ins:
        return []
    declined = _declined(conn)
    held = valuation._snapshot(conn)

    candidates = []
    for back in ins:
        ecb = valuation.day_rate(held, back["currency"], back["date"])
        if ecb is None:
            continue  # no saved rate for its day: nothing is guessed
        per_sgd = wording_rate(back["description"])
        for out in outs:
            gap = abs((back["day"] - out["day"]).days)
            if gap > WINDOW_DAYS or (out["id"], back["id"]) in declined:
                continue
            if per_sgd is not None:
                rate, source = _per_unit(per_sgd), FROM_WORDING
            else:
                rate, source = _amounts_rate(out["amount_minor"], out["currency"],
                                             back["amount_minor"], back["currency"]), FROM_AMOUNTS
            off = abs(rate - ecb) / ecb
            if per_sgd is not None:
                # A wording's rate is its own: the amounts must still say the same move.
                said = _amounts_rate(out["amount_minor"], out["currency"], back["amount_minor"], back["currency"])
                off = max(off, abs(said - ecb) / ecb)
            if off > BAND:
                continue
            candidates.append((off, gap, out["id"], back["id"], out, back, rate, source, ecb))

    candidates.sort(key=lambda c: c[:4])
    used, made = set(), []
    for off, _gap, _o, _b, out, back, rate, source, ecb in candidates:
        if out["id"] in used or back["id"] in used:
            continue
        used.update((out["id"], back["id"]))
        made.append({
            "out": _leg(out), "in": _leg(back), "currency": back["currency"],
            "rate": str(rate), "per_sgd": shown_rate(rate), "source": source,
            "ecb_rate": str(ecb), "ecb_per_sgd": shown_rate(ecb),
            "off_percent": str((off * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN)),
        })
    made.sort(key=lambda o: (-o["out"]["amount_minor"], o["out"]["date"], o["out"]["id"]))
    return made


# --- confirming and declining ---------------------------------------------------

def _pair_words(offer: dict) -> tuple[str, str]:
    out, back = offer["out"], offer["in"]
    return (f"{expect_back.full(out['amount_minor'], out['currency'])} out of {out['account']}",
            f"{expect_back.full(abs(back['amount_minor']), back['currency'])} into {back['account']}")


def _pair_waiting_leg(conn: sqlite3.Connection, leg_id: int, other_account: int) -> None:
    """A leg still waiting becomes a transfer naming the other's account, set
    by hand, its book and type emptied with the move (as pairing.py empties
    them); a leg of any other flow keeps its flow, other side and label."""
    conn.execute(
        "UPDATE transactions SET flow_type = ?, other_side_id = ?, flow_type_manual = 1, "
        "book = NULL, type_id = NULL, book_source = NULL, type_source = NULL "
        "WHERE id = ? AND flow_type = ?",
        (flow.checked_flow(PAIRED_FLOW), other_account, leg_id, flow.REVIEW),
    )


def _name_change(conn: sqlite3.Connection, words: str) -> None:
    """Name this request's change in Changes (app.py's _name_change)."""
    entry_id = g.get("change_entry")
    if entry_id is not None:
        conn.execute("UPDATE change_entries SET summary = ? WHERE id = ? AND summary = ''", (words, entry_id))


def _asked() -> tuple[int, int] | None:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    out_id, in_id = data.get("out_id"), data.get("in_id")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (out_id, in_id)):
        return None
    return out_id, in_id


_BODY = f"send {{\"out_id\": the S$ row's id, \"in_id\": the other row's id}}. {NOTHING}"


def _offered(conn: sqlite3.Connection, out_id: int, in_id: int) -> dict | None:
    return next((o for o in offers(conn) if o["out"]["id"] == out_id and o["in"]["id"] == in_id), None)


bp = Blueprint("fx_pair", __name__)

# endpoint (without the blueprint's "fx_pair." prefix) -> what the change did;
# each route names its pair in its own summary.
SUMMARIES: dict[str, str] = {
    "fx_pair_confirm": "Paired across currencies",
    "fx_pair_decline": "Not a pair",
}


@bp.route("/api/fx-pairs/offers")
def fx_pair_offers():
    with get_db() as conn:
        return jsonify({"offers": offers(conn)})


@bp.route("/api/fx-pairs", methods=["POST"])
def fx_pair_confirm():
    """Confirm an offer: the pair and its real rate, one change."""
    asked = _asked()
    if asked is None:
        return jsonify({"error": _BODY}), 400
    with get_db() as conn:
        offer = _offered(conn, *asked)
        if offer is None:
            return jsonify({"error": NOT_OFFERED}), 409
        out, back = offer["out"], offer["in"]
        conn.execute(
            "INSERT INTO fx_pair (out_row_id, in_row_id, state, currency, rate, source) VALUES (?, ?, ?, ?, ?, ?)",
            (out["id"], back["id"], CONFIRMED, offer["currency"], offer["rate"], offer["source"]))
        # A move between our own accounts only when neither leg is someone
        # else's money: a waiting leg paired with a not-ours one keeps waiting
        # for its label (a payment to them, not a transfer).
        if flow.NOT_OURS not in (out["flow_type"], back["flow_type"]):
            _pair_waiting_leg(conn, out["id"], back["account_id"])
            _pair_waiting_leg(conn, back["id"], out["account_id"])
        sent, came = _pair_words(offer)
        sign = expect_back.SIGNS.get(offer["currency"], offer["currency"])
        _name_change(conn, f"Paired {sent} with {came} at {sign} {offer['per_sgd']} a dollar")
        conn.commit()
    return jsonify({"paired": offer})


@bp.route("/api/fx-pairs/decline", methods=["POST"])
def fx_pair_decline():
    """Not a pair: the offer is not made again, one change."""
    asked = _asked()
    if asked is None:
        return jsonify({"error": _BODY}), 400
    with get_db() as conn:
        offer = _offered(conn, *asked)
        if offer is None:
            return jsonify({"error": NOT_OFFERED}), 409
        conn.execute("INSERT INTO fx_pair (out_row_id, in_row_id, state) VALUES (?, ?, ?)",
                     (offer["out"]["id"], offer["in"]["id"], DECLINED))
        sent, came = _pair_words(offer)
        _name_change(conn, f"Not a pair: {sent} and {came}")
        conn.commit()
    return jsonify({"declined": offer})
