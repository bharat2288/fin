"""Books' three expect-back sections (personal-ledger 23, ticket 31; spec E5).

As at a day, from expect_back.open_as_at (the one open-as-at function):

    Expected back        the open part of every back expectation: yours, held
                         by others; an asset
    Waiting for its row  every set-off still waiting for its row then: money
                         in held for a row not yet in; owed
    To pay back          the open part of every pay expectation; owed

Each lists its items oldest first with their age in days, "old" past 90 days
(age only informs: nothing happens by itself). An item kept in another
currency (a rupee item) is shown in its own currency with its S$ value beside
it, at the latest rate saved on or before the day (balance_sheet.valued). With
no such rate it is shown in its own currency with "no rate", left out of the
S$ total, and the section says so; no rate is fetched or guessed.

Each line carries a key, "expectation:<id>" or "waiting:<set-off id>", which
the month check reads in place of an account id, which these lines lack.

Since the switch (ticket 45) every section is in net worth (in_total), less
any item with no rate. Amounts are whole minor
units: open_minor and amount_minor in the item's own currency, sgd_minor in
the currency of the totals. Worked out on read; nothing is stored, and each
section is one read whatever the number of items (case 55).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import balance_sheet
import expect_back
import money

OLD_AFTER_DAYS = 90
NO_RATE = "no rate"

# (key, heading, owed), in the order Books shows them.
SECTIONS = (
    ("expected_back", "Expected back", False),
    ("waiting", "Waiting for its row", True),
    ("to_pay_back", "To pay back", True),
)


def valuer(conn: sqlite3.Connection, on: str):
    """A function valuing an amount (whole minor units of a currency) in the
    currency of the totals as at `on`, at the latest rate saved on or before
    it (balance_sheet.valued, read once per currency). It returns
    (value_minor, rate) with rate {"text", "date"} for another currency, or
    (None, None) when there is no such rate. Half-even to the cent."""
    found: dict[str, dict] = {}

    def value(amount_minor: int, currency: str):
        if currency == balance_sheet.TOTAL_CURRENCY:
            return amount_minor, None
        if currency not in found:
            found[currency] = balance_sheet.valued(conn, 0, currency, on)
        rate = found[currency]["rate"]
        if rate is None:
            return None, None
        shown = {"text": rate["rate"], "date": rate["date"]}
        return money.convert_minor(amount_minor, Decimal(rate["rate"]), currency,
                                   balance_sheet.TOTAL_CURRENCY), shown

    return value


def _details(conn: sqlite3.Connection) -> dict:
    """How each expectation is named on Books, by id: its row's text, a loan
    to or from a person when its row is a movement naming one, or an opening
    figure's person. One read."""
    titles = {}
    for r in conn.execute(
        "SELECT e.id, e.direction, t.description, t.flow_type, o.type AS other_kind, o.name AS other_name, "
        "p.name AS person_name FROM expectation e "
        "LEFT JOIN transactions t ON t.id = e.row_id "
        "LEFT JOIN accounts o ON o.id = t.other_side_id "
        "LEFT JOIN accounts p ON p.id = e.person_id"
    ):
        if r["description"] is None:
            titles[r["id"]] = f"Opening figure · {r['person_name']}"
        elif r["flow_type"] == "movement" and r["other_kind"] == "person":
            titles[r["id"]] = (f"Loan to {r['other_name']}" if r["direction"] == expect_back.BACK
                               else f"From {r['other_name']}")
        else:
            titles[r["id"]] = r["description"]
    return titles


def _names(conn: sqlite3.Connection) -> dict[int, list[str]]:
    """The names on each expectation, by id, in name order. One read."""
    names: dict[int, list[str]] = {}
    for expectation_id, name in conn.execute(
        "SELECT n.expectation_id, a.name FROM expectation_name n JOIN accounts a ON a.id = n.person_id "
        "ORDER BY a.name, a.id"
    ):
        names.setdefault(expectation_id, []).append(name)
    return names


def _last_set_off(conn: sqlite3.Connection, on: str) -> dict[int, str]:
    """The day of the latest set-off counted on or before `on`, by
    expectation: its payment row's day (ROW_DAY). One read."""
    return dict(conn.execute(
        f"SELECT so.expectation_id, MAX({balance_sheet.ROW_DAY}) FROM set_off so "
        "JOIN transactions t ON t.id = so.payment_row_id JOIN statements s ON s.id = t.statement_id "
        f"WHERE so.expectation_id IS NOT NULL AND {balance_sheet.ROW_DAY} <= ? GROUP BY so.expectation_id",
        (on,),
    ).fetchall())


def _waiting_details(conn: sqlite3.Connection, ids: list[int]) -> dict:
    """Each waiting set-off's payment text, note and who it is from. One read."""
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    return {
        r["id"]: r for r in conn.execute(
            "SELECT so.id, t.description, so.note, p.name AS person_name, so.expectation_id "
            "FROM set_off so JOIN transactions t ON t.id = so.payment_row_id "
            f"LEFT JOIN accounts p ON p.id = so.person_id WHERE so.id IN ({marks})",
            ids,
        )
    }


def _aged(line: dict, age_on: date) -> dict:
    line["age_days"] = (age_on - date.fromisoformat(line["date"])).days
    line["old"] = line["age_days"] > OLD_AFTER_DAYS
    return line


def _priced(line: dict, value) -> dict:
    sgd, rate = value(line["open_minor"], line["currency"])
    line.update(sgd_minor=sgd, rate=rate, no_rate=sgd is None)
    return line


def sections(conn: sqlite3.Connection, on: str, *, today: date | None = None) -> dict:
    """The three sections as at day `on` (YYYY-MM-DD).

    Open amounts are as at `on`; ages are counted to `on`, or to `today` when
    that is earlier (the month still running: its end is not yet here, and an
    item is as old as it is today). `as_at` is that day.

    {"as_at", "rate": the rate a rupee item is valued at ({"text", "date"}, or
     None), "sections": [{"key", "heading", "owed", "in_total", "total_minor",
     "left_out": [{"key", "title", "currency", "open_minor", "reason"}],
     "lines": [...]}]}; a section with nothing in it is not listed.
    """
    as_at = date.fromisoformat(on)
    age_on = min(as_at, today) if today is not None else as_at
    held = expect_back.open_as_at(conn, on)
    value = valuer(conn, on)
    titles = _details(conn) if held["expectations"] else {}
    names = _names(conn) if held["expectations"] or held["waiting"] else {}
    last = _last_set_off(conn, on) if held["expectations"] else {}

    lines: dict[str, list] = {key: [] for key, _, _ in SECTIONS}
    for x in held["expectations"]:
        if x["open_minor"] <= 0:
            continue
        line = {
            "key": f"expectation:{x['id']}",
            "expectation_id": x["id"],
            "title": titles[x["id"]],
            "date": x["day"],
            "names": names.get(x["id"], []),
            "open_minor": x["open_minor"],
            "amount_minor": x["amount_minor"],
            "currency": x["currency"],
            "last_set_off": last.get(x["id"]),
        }
        section = "expected_back" if x["direction"] == expect_back.BACK else "to_pay_back"
        lines[section].append(_aged(_priced(line, value), age_on))

    waiting = _waiting_details(conn, [w["id"] for w in held["waiting"]])
    for w in held["waiting"]:
        found = waiting[w["id"]]
        if found["person_name"] is not None:
            who = [found["person_name"]]
        else:
            who = names.get(found["expectation_id"], [])
        line = {
            "key": f"waiting:{w['id']}",
            "set_off_id": w["id"],
            "title": found["description"],
            "date": w["day"],
            "names": who,
            "note": found["note"],
            "open_minor": w["amount_minor"],
            "amount_minor": w["amount_minor"],
            "currency": w["currency"],
        }
        lines["waiting"].append(_aged(_priced(line, value), age_on))

    shown = []
    rate = None
    for key, heading, owed in SECTIONS:
        listed = lines[key]
        if not listed:
            continue
        rate = rate or next((x["rate"] for x in listed if x["rate"] is not None), None)
        shown.append({
            "key": key,
            "heading": heading,
            "owed": owed,
            "in_total": True,
            "total_minor": sum(x["sgd_minor"] for x in listed if not x["no_rate"]),
            "left_out": [
                {"key": x["key"], "title": x["title"], "currency": x["currency"],
                 "open_minor": x["open_minor"], "reason": NO_RATE}
                for x in listed if x["no_rate"]
            ],
            "lines": listed,
        })
    return {"as_at": age_on.isoformat(), "rate": rate, "sections": shown}


def net_minor(shown: dict) -> int:
    """What the sections add to net worth once they are in the total:
    Expected back less what is owed (Waiting for its row, To pay back)."""
    return sum(-s["total_minor"] if s["owed"] else s["total_minor"]
               for s in shown["sections"] if s["in_total"])


def left_out(shown: dict) -> list[dict]:
    """The items the sections leave out of a total they are in, as the sheet
    lists what it leaves out: {"name", "why"}."""
    return [{"name": x["title"], "why": x["reason"]}
            for s in shown["sections"] if s["in_total"] for x in s["left_out"]]
