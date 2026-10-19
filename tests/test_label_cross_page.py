"""Changing history across pages and features (fin-surfaces 54; spec 24, the
case list run together, and its Testing Decisions).

Personal-ledger 48-50 gave each row's book and type a source of its own;
fin-surfaces 49-53 put a preview in front of every change that would relabel
past rows, retired Re-run every rule for the drift count, and showed the
marks and the rule. Each ticket tested its own cases. These tests run them
together, on one book at a time, and check what only holds where they meet:

- the decision's worked example, end to end: Confirm all marks the rows, the
  merchant page and the row's sheet show the marks, a later type fix is
  previewed and applied, and Undo takes it all back in order;
- a chosen field is never reached, by any change that can reach past rows;
- after either answer to any preview, no labelled row differs from what its
  rule says now (the drift count);
- the money the preview says moves is what the books' figures move, month by
  month (the merchant's trend and types over time);
- the merchant sheet, the Queue and chat give one preview;
- a preview goes stale when another feature writes its rows; Undo is refused
  while a later change of another feature holds the same rows, and once that
  is undone the book returns whole;
- the row's sheet names the change that set a mark, whichever way it came in;
- Only new rows means the next statement's rows take the change;
- teaching a wording and adding it as a rule take the same rows;
- a book converted from today's shape waits and previews as the old mark did.

Each step is a request through fin's public routes (the Flask test client, or
mcp_tools.call for chat); what is asserted is what the rows, merchants and
rules say afterwards, values and sources. The database is the temporary one
(the conversion's book is under tmp_path); every merchant, wording and figure
is invented, and no wording is one of fin's seeded rules. fin has no Flights
type: Transport stands in for it.
"""

from __future__ import annotations

import datetime
import sqlite3

import pytest

import app as fin_app
import convert_all
import db
import label_source
import mcp_tools
import trend_api
import trend_types_api
import trend_fixtures as tf
from test_balance_sheet import bring_in
from test_label_source_conversion import blank, todays  # noqa: F401 (fixtures)

CHAT_ACTOR = "chat:sample-subject"
TODAY = datetime.date(2026, 10, 19)
TWELVE = ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
          "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(trend_api, "_today", lambda: TODAY)
    monkeypatch.setattr(trend_types_api, "_today", lambda: TODAY)


# --- the book --------------------------------------------------------------------------


class Book:
    """A household card and a rupee card, each with a statement every month of
    the twelve; merchants with their rules; rows as fin's writers leave them,
    each field with its source (an empty field has none)."""

    def __init__(self, conn):
        self.conn = conn
        self.card = tf.account(conn, "Sample Card 0001")
        self.rupee_card = tf.account(conn, "Sample Rupee Card 0002", currency="INR")
        self.statements = {(a, m): tf.statement(conn, a, m)
                           for a in (self.card, self.rupee_card) for m in TWELVE}

    def merchant(self, name, book, type_name, wording=None, *, priority=0):
        svc = tf.merchant(self.conn, name, book, type_name)
        if wording:
            self.rule(wording, svc, priority=priority)
        return svc

    def rule(self, wording, svc, *, priority=0) -> int:
        rid = self.conn.execute(
            "INSERT INTO merchant_rules (pattern, service_id, match_type, confidence, priority)"
            " VALUES (?, ?, 'contains', 'confirmed', ?)", (wording, svc, priority)).lastrowid
        self.conn.commit()
        db.invalidate_rules_cache()
        return rid

    def row(self, wording, amount_minor, day, *, svc=None, book="Household", type_name=None,
            sources=("merchant", "merchant"), rupee=False) -> int:
        """A row; with a merchant and no type named, the merchant's own label.
        A row with no merchant and no type has no label at all."""
        if svc is not None and type_name is None:
            held = self.conn.execute("SELECT book, type_id FROM services WHERE id = ?", (svc,)).fetchone()
            book, type_id = held[0], held[1]
        else:
            type_id = tf.type_id(self.conn, type_name) if type_name else None
            if svc is None and type_id is None:
                book = None
        book_source = sources[0] if book else None
        type_source = sources[1] if type_id else None
        stmt = self.statements[(self.rupee_card if rupee else self.card, day[:7])]
        rid = self.conn.execute(
            "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id,"
            " service_id, cat_source, flow_type, book_source, type_source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'expense', ?, ?)",
            (stmt, day, wording, amount_minor, book, type_id, svc,
             label_source.mark(book_source, type_source, "auto"), book_source, type_source),
        ).lastrowid
        self.conn.commit()
        return rid


# --- reading the book and the app -----------------------------------------------------------


def state(conn) -> tuple:
    """Every row, merchant and rule, sources included."""
    rows = {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT id, service_id, book, type_id, book_source, type_source, cat_source, flow_type FROM transactions")}
    merchants = {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT id, name, book, type_id, review_each_time FROM services")}
    rules = {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT id, pattern, match_type, service_id, priority, min_amount_minor, max_amount_minor,"
        " book_override, type_override_id FROM merchant_rules")}
    return rows, merchants, rules


def fields(conn, ids) -> dict:
    """Each row's (service id, book, type id, book source, type source)."""
    marks = ",".join("?" * len(ids))
    return {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT id, service_id, book, type_id, book_source, type_source FROM transactions"
        f" WHERE id IN ({marks})", list(ids))}


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def ok(resp) -> dict:
    assert resp.status_code == 200, (resp.status_code, resp.get_json())
    return resp.get_json()


def change_id(resp) -> int:
    ok(resp)
    return int(resp.headers["X-Fin-Change"])


def preview(resp) -> dict:
    assert resp.status_code == 409, (resp.status_code, resp.get_json())
    body = resp.get_json()
    assert "nothing was saved" in body["error"].lower(), body["error"]
    return body["preview"]


def drift(client) -> dict:
    return ok(client.get("/api/rules/drift"))


def sheet(client, tx_id) -> dict:
    """A row as its own sheet reads it (its sources and who set them)."""
    found = ok(client.get(f"/api/transactions?tx_id={tx_id}"))["transactions"]
    assert len(found) == 1
    return found[0]


def merchant_page(client, svc) -> dict:
    """The merchant page's rows: each row's sources, which its tags draw."""
    found = ok(client.get(f"/api/transactions?service_id={svc}&per_page=500"))["transactions"]
    return {t["id"]: t["sources"] for t in found}


def waiting(client, svc) -> list[int]:
    """The rows of a merchant waiting in the Queue's Check each row step."""
    found = ok(client.get(f"/api/transactions?look=each&service_id={svc}&per_page=500"))["transactions"]
    return sorted(t["id"] for t in found)


def chat(tool, **args) -> dict:
    return mcp_tools.call(tool, args, actor=CHAT_ACTOR)


def answered(client, method, path, body, answer="apply", key="past"):
    """A change sent, then, when it previews, sent again with the answer and
    the preview's token. Returns (response, preview or None)."""
    first = client.open(path, method=method, json=body)
    if first.status_code != 409:
        return first, None
    shown = preview(first)
    return client.open(path, method=method, json={**body, key: answer, "token": shown["token"]}), shown


# --- 1. the decision's worked example, end to end (personal-ledger 24; cases 1, 9, 10, 12, 19) ---


def test_the_worked_example_confirm_all_the_marks_shown_a_type_fix_previewed_then_undone(client, conn):
    """IndigoAir, Check each row, 8 waiting; 2 moved to Moom on their lines;
    Confirm all. The 6 are checked for both fields, the 2 chosen for book and
    checked for type. A later type fix reaches all 8 (previewed); the 2 stay
    Moom. Undo takes it back in order."""
    b = Book(conn)
    indigo = b.merchant("IndigoAir", "Household", "Travel", "INDIGOAIR")
    rows = [b.row(f"INDIGOAIR 6E {n}", 40000 + n, f"2026-{9 + n // 5:02d}-{2 + n:02d}", svc=indigo)
            for n in range(8)]
    moved, kept = rows[:2], rows[2:]
    travel, transport = tf.type_id(conn, "Travel"), tf.type_id(conn, "Transport")
    start = state(conn)

    assert ok(client.post(f"/api/services/{indigo}/check-each-row", json={"on": True, "past": "queue"}))["waiting"] == 8
    assert waiting(client, indigo) == sorted(rows)
    after_on = state(conn)

    # Confirm all, the 2 Oct line's book changed: each line sends what it shows.
    confirm = change_id(client.post("/api/transactions/bulk-label", json={"rows": [
        {"tx_id": t, "book": "Moom" if t in moved else "Household", "type_id": travel} for t in rows]}))
    f = fields(conn, rows)
    assert {f[t] for t in kept} == {(indigo, "Household", travel, "checked", "checked")}
    assert {f[t] for t in moved} == {(indigo, "Moom", travel, "chosen", "checked")}
    assert waiting(client, indigo) == []
    after_confirm = state(conn)

    # The merchant page's tags and the row's sheet read the same marks.
    page = merchant_page(client, indigo)
    assert {t: page[t] for t in moved} == {t: {"book": "chosen", "type": "checked"} for t in moved}
    assert {t: page[t] for t in kept} == {t: {"book": "checked", "type": "checked"} for t in kept}
    set_by = sheet(client, moved[0])["set_by"]
    assert (set_by["book"]["entry_id"], set_by["book"]["via"]) == (confirm, "app")
    assert set_by["type"]["entry_id"] == confirm
    # "Book varies" counts the 2 chosen Moom rows, never the 6 checked ones.
    assert ok(client.get(f"/api/services/{indigo}/book-varies"))["moved"] == 2

    # A later type fix reaches all 8, previewed; the 2 stay Moom.
    shown = preview(client.put(f"/api/services/{indigo}", json={"type_id": transport}))
    assert (shown["count"], shown["checked"], shown["chosen"], shown["money"]) == (8, 8, 0, None)
    fix = change_id(client.put(f"/api/services/{indigo}",
                               json={"type_id": transport, "past": "apply", "token": shown["token"]}))
    f = fields(conn, rows)
    assert {f[t] for t in kept} == {(indigo, "Household", transport, "checked", "checked")}
    assert {f[t] for t in moved} == {(indigo, "Moom", transport, "chosen", "checked")}
    assert waiting(client, indigo) == []                       # checked rows do not wait again
    # Apply set a value, not the mark: the sheet still names Confirm all.
    assert sheet(client, moved[0])["set_by"]["type"]["entry_id"] == confirm
    assert drift(client)["count"] == 0

    # Undo: refused while the fix holds the rows, named; then in order.
    after_fix = state(conn)
    refused = client.post(f"/api/history/{confirm}/undo")
    assert refused.status_code == 409 and f"change {fix}" in refused.get_json()["error"]
    assert state(conn) == after_fix
    ok(client.post(f"/api/history/{fix}/undo"))
    assert state(conn) == after_confirm
    ok(client.post(f"/api/history/{confirm}/undo"))
    assert state(conn) == after_on
    assert waiting(client, indigo) == sorted(rows)
    assert {sheet(client, t)["sources"]["book"] for t in rows} == {"merchant"}
    assert state(conn)[0] == start[0]


# --- 2. a chosen field is never reached --------------------------------------------------


@pytest.fixture
def kept(client, conn):
    """Sample Air (Household · Travel, SAMPLE AIR) with three rows from the
    merchant and three whose fields were chosen through the routes: a type in
    the row's sheet, a book by Set book (the same book: still chosen), a book
    by chat's set_label. Two rows with no merchant read SAMPLE LOUNGE: one
    with its type chosen in its sheet, one waiting in No type. Sample Other
    Air (Moom · Transport, SAMPLE OTHER) is the merge's target."""
    b = Book(conn)
    air = b.merchant("Sample Air", "Household", "Travel", "SAMPLE AIR")
    other = b.merchant("Sample Other Air", "Moom", "Transport", "SAMPLE OTHER")
    plain = [b.row(f"SAMPLE AIR {n}", 30000 + n, f"2026-0{5 + n}-11", svc=air) for n in range(3)]
    in_sheet = b.row("SAMPLE AIR SHEET 1", 21000, "2026-08-03", svc=air)
    set_book = b.row("SAMPLE AIR SETBOOK 2", 22000, "2026-08-04", svc=air)
    in_chat = b.row("SAMPLE AIR CHAT 3", 23000, "2026-08-05", svc=air)
    loose = b.row("SAMPLE LOUNGE 7", 1500, "2026-08-06")
    queue = b.row("SAMPLE LOUNGE 8", 1600, "2026-08-07")
    dining = tf.type_id(conn, "Dining")

    ok(client.put(f"/api/transactions/{in_sheet}", json={"type_id": dining}))
    ok(client.post("/api/transactions/bulk-label",
                   json={"rows": [{"tx_id": set_book, "book": "Household"}], "chosen": True}))
    assert chat("set_label", tx_id=in_chat, book="Kalesh")["ok"]
    ok(client.put(f"/api/transactions/{loose}", json={"type_id": dining}))

    chosen = {in_sheet: "type_id", set_book: "book", in_chat: "book", loose: "type_id"}
    f = fields(conn, list(chosen))
    for tx, field_name in chosen.items():
        assert f[tx][4 if field_name == "type_id" else 3] == "chosen", (tx, f[tx])
    rule = conn.execute("SELECT id FROM merchant_rules WHERE pattern = 'SAMPLE AIR'").fetchone()[0]
    return {"air": air, "other": other, "rule": rule, "plain": plain, "chosen": chosen,
            "queue": queue, "travel": tf.type_id(conn, "Travel")}


def _chosen_fields(conn, chosen: dict) -> dict:
    f = fields(conn, list(chosen))
    return {tx: (f[tx][2], f[tx][4]) if name == "type_id" else (f[tx][1], f[tx][3])
            for tx, name in chosen.items()}


def _drifted(client, conn, k):
    """The rows from the merchant left reading an older default (an import
    before the merchant's type changed, say), then the drift's Apply."""
    conn.execute("UPDATE services SET type_id = ? WHERE id = ?", (tf.type_id(conn, "Transport"), k["air"]))
    conn.commit()
    db.invalidate_rules_cache()
    shown = drift(client)
    assert shown["count"] >= 3
    return client.post("/api/rules/drift", json={"past": "apply", "token": shown["token"]}), shown


CHANGES = {
    "a merchant's book": lambda c, conn, k: answered(c, "PUT", f"/api/services/{k['air']}", {"book": "Kalesh"}),
    "a merchant's type": lambda c, conn, k: answered(
        c, "PUT", f"/api/services/{k['air']}", {"type_id": tf.type_id(conn, "Transport")}),
    "a merge": lambda c, conn, k: answered(c, "POST", f"/api/services/{k['air']}/merge", {"target_id": k["other"]}),
    "a rule added": lambda c, conn, k: answered(
        c, "POST", "/api/rules", {"pattern": "SAMPLE", "service_id": k["other"], "priority": 5}),
    "a rule's wording edited": lambda c, conn, k: answered(
        c, "PUT", f"/api/rules/{k['rule']}", {"pattern": "SAMPLE", "priority": 5}),
    "a rule deleted": lambda c, conn, k: answered(c, "DELETE", f"/api/rules/{k['rule']}", {}),
    "the drift applied": _drifted,
    "a merchant taught from the Queue": lambda c, conn, k: answered(
        c, "POST", "/api/transactions/resolve",
        {"tx_id": k["queue"], "service_name": "Sample Lounge", "type_id": k["travel"], "book": "Household",
         "pattern": "SAMPLE LOUNGE"}, key="past_rows"),
    "Check each row turned on, not now": lambda c, conn, k: (
        c.post(f"/api/services/{k['air']}/check-each-row", json={"on": True, "past": "leave"}), None),
}


@pytest.mark.parametrize("change", list(CHANGES))
def test_a_chosen_field_is_never_reached_by_a_later_change(client, conn, kept, change):
    """Spec 24: a later fix reaches every field except a chosen one, whatever
    the route (a merchant's book or type, a merge, a rule added, edited or
    deleted, the drift's Apply, teaching from the Queue, Check each row)."""
    held = _chosen_fields(conn, kept["chosen"])
    before = state(conn)[0]

    resp, _ = CHANGES[change](client, conn, kept)

    ok(resp)
    after = state(conn)[0]
    assert after != before, "the change reached no row at all: the case proves nothing"
    assert _chosen_fields(conn, kept["chosen"]) == held


# --- 3. after either answer, no labelled row differs from its rule ---------------------------


@pytest.fixture
def agreeing(conn):
    """A book whose every labelled row says what its rule gives now (the drift
    count is 0): IndigoAir (INDIGOAIR, priority 1) with 4 rows from the
    merchant, 1 checked, 1 with its book chosen Moom; Air India (AIR INDIA)
    with 3 rows reading AIR INDIA INDIGO LOUNGE; Sample Food (SAMPLE FOOD)
    with 3; Sample Rides (SAMPLE RIDE) with 2; and one row with no merchant
    and no label, SAMPLE INDIGO 6E BLR."""
    b = Book(conn)
    ids = {}
    ids["indigo"] = b.merchant("IndigoAir", "Household", "Travel")
    ids["indigo_rule"] = b.rule("INDIGOAIR", ids["indigo"], priority=1)
    ids["air_india"] = b.merchant("Air India", "Household", "Travel", "AIR INDIA")
    ids["food"] = b.merchant("Sample Food", "Household", "Dining", "SAMPLE FOOD")
    ids["rides"] = b.merchant("Sample Rides", "Household", "Transport")
    ids["rides_rule"] = b.rule("SAMPLE RIDE", ids["rides"])
    for n in range(4):
        b.row(f"INDIGOAIR 6E {n}", 41000 + n, f"2026-0{3 + n}-08", svc=ids["indigo"])
    b.row("INDIGOAIR 6E CHECKED", 42000, "2026-07-08", svc=ids["indigo"], sources=("checked", "checked"))
    b.row("INDIGOAIR 6E CHOSEN", 43000, "2026-08-08", svc=ids["indigo"], book="Moom", type_name="Travel",
          sources=("chosen", "merchant"))
    for n in range(3):
        b.row(f"AIR INDIA INDIGO LOUNGE {n}", 4200 + n, f"2026-0{5 + n}-14", svc=ids["air_india"])
        b.row(f"SAMPLE FOOD {n}", 1800 + n, f"2026-0{5 + n}-15", svc=ids["food"])
    for n in range(2):
        b.row(f"SAMPLE RIDE {n}", 1100 + n, f"2026-0{5 + n}-16", svc=ids["rides"])
    ids["loose"] = b.row("SAMPLE INDIGO 6E BLR", 18990, "2026-05-09")
    return ids


PREVIEWED = {
    "a merchant's book": lambda ids, conn: ("PUT", f"/api/services/{ids['indigo']}", {"book": "Moom"}),
    "a merchant's type": lambda ids, conn: (
        "PUT", f"/api/services/{ids['indigo']}", {"type_id": tf.type_id(conn, "Transport")}),
    "a merge": lambda ids, conn: ("POST", f"/api/services/{ids['food']}/merge", {"target_id": ids["rides"]}),
    "a rule added": lambda ids, conn: (
        "POST", "/api/rules", {"pattern": "SAMPLE INDIGO", "service_id": ids["indigo"]}),
    "a rule's wording edited": lambda ids, conn: ("PUT", f"/api/rules/{ids['indigo_rule']}", {"pattern": "INDIGO"}),
    "a rule deleted": lambda ids, conn: ("DELETE", f"/api/rules/{ids['rides_rule']}", {}),
}


@pytest.mark.parametrize("answer", ["apply", "only_new"])
@pytest.mark.parametrize("change", list(PREVIEWED))
def test_after_either_answer_no_labelled_row_differs_from_its_rule(client, conn, agreeing, change, answer):
    """The preview (fin-surfaces 49, 50) and the drift count (51) read one
    rule of what the rules give: Apply leaves every row saying it, and Only
    new rows marks what it keeps checked, which the drift never counts. The
    one row the drift may still count is the unlabelled one a rule now
    matches, waiting in No type (case 15: Only new rows moves none; the
    mockup's drift draws "no merchant → …")."""
    assert drift(client)["count"] == 0
    method, path, body = PREVIEWED[change](agreeing, conn)

    shown = preview(client.open(path, method=method, json=body))
    assert shown["count"] > 0
    ok(client.open(path, method=method, json={**body, "past": answer, "token": shown["token"]}))

    after = drift(client)
    unlabelled = {r[0] for r in conn.execute("SELECT id FROM transactions WHERE book IS NULL AND type_id IS NULL")}
    assert {x["id"] for x in after["examples"]} <= unlabelled
    if answer == "apply" or change not in ("a rule added", "a rule's wording edited"):
        assert after["count"] == 0
    else:
        assert after["count"] == 1 and after["examples"][0]["id"] == agreeing["loose"]


# --- 4. the money the preview says moves is what the figures move ---------------------------


@pytest.fixture
def indigo_book(conn):
    """IndigoAir, Household · Travel: 25 past rows, as case 11. Two a month
    Nov 2025 - Sep 2026 on the card at S$ 440.00 (one S$ 445.00), 6 of them
    checked; one rupee row on 25 Sep, Rs 10,000.00 (S$ 155.00 at the saved
    month-end rate); 2 rows whose book is chosen (Household) stay."""
    b = Book(conn)
    svc = b.merchant("IndigoAir", "Household", "Travel", "INDIGOAIR")
    tf.seed_rate(conn, "INR/SGD", "2026-09-30", "0.0155")
    rows, n = [], 0
    for month in TWELVE[:-1]:
        for day in ("05", "20"):
            amount = 44500 if (month, day) == ("2026-09", "20") else 44000
            sources = ("checked", "checked") if n % 4 == 1 and n < 24 else ("merchant", "merchant")
            rows.append(b.row(f"INDIGOAIR 6E {n:03d}", amount, f"{month}-{day}", svc=svc, sources=sources))
            n += 1
    rows.append(b.row("INDIGOAIR 6E DEL", 1_000_000, "2026-09-25", svc=svc, rupee=True))
    chosen = [b.row("INDIGOAIR 6E KEPT", 30000, day, svc=svc, sources=("chosen", "merchant"))
              for day in ("2026-09-28", "2026-08-28")]
    return {"svc": svc, "rows": rows, "chosen": chosen, "book": b}


def _by_book_month(client, svc) -> dict:
    """The merchant trend's net per (book, month), in S$ minor units."""
    trend = ok(client.get(f"/api/trend?subject=merchant&id={svc}&span=12M"))
    return {(book, b["key"]): v["net"] for b in trend["buckets"] for book, v in b["by"].items()}


def _types_over_time(client, book) -> dict:
    """Types over time for a book: each month's net, every type together."""
    body = ok(client.get(f"/api/trend/types?book={book}&span=12M"))
    out: dict = {}
    for t in body["types"]:
        for p in t["points"]:
            out[p["month"]] = out.get(p["month"], 0) + (p["net"] or 0)
    return out


def test_the_money_the_preview_moves_is_what_the_books_figures_move_month_by_month(client, conn, indigo_book):
    svc = indigo_book["svc"]
    shown = preview(client.put(f"/api/services/{svc}", json={"book": "Moom"}))
    assert (shown["count"], shown["checked"], shown["chosen"]) == (23, 6, 2)
    assert shown["money"] == [{"from": "Household", "to": "Moom", "amount_minor": 984000}]
    assert shown["months"] == {"count": 11, "first": "2025-11", "last": "2026-09"}
    trend_before = _by_book_month(client, svc)
    household_before, moom_before = _types_over_time(client, "Household"), _types_over_time(client, "Moom")

    ok(client.put(f"/api/services/{svc}", json={"book": "Moom", "past": "apply", "token": shown["token"]}))

    trend_after = _by_book_month(client, svc)
    household_after, moom_after = _types_over_time(client, "Household"), _types_over_time(client, "Moom")
    moved_out = {m: trend_before.get(("Household", m), 0) - trend_after.get(("Household", m), 0) for m in TWELVE}
    moved_in = {m: trend_after.get(("Moom", m), 0) - trend_before.get(("Moom", m), 0) for m in TWELVE}
    # The merchant's trend: what leaves Household each month arrives in Moom.
    assert moved_out == moved_in
    assert sum(moved_out.values()) == 984000
    months = sorted(m for m, v in moved_out.items() if v)
    assert (len(months), months[0], months[-1]) == (11, "2025-11", "2026-09")
    # Types over time reads the same move, book by book.
    assert {m: household_before.get(m, 0) - household_after.get(m, 0) for m in TWELVE} == moved_out
    assert {m: moom_after.get(m, 0) - moom_before.get(m, 0) for m in TWELVE} == moved_in
    # The 2 chosen rows stay Household: September's and August's S$ 300.00 each.
    assert trend_after[("Household", "2026-09")] == trend_after[("Household", "2026-08")] == 30000


# --- 5. the merchant sheet, the Queue and chat give one preview (cases 13, 24) ---------------


def test_the_merchant_sheet_the_queue_and_chat_give_one_preview(client, conn, indigo_book):
    svc = indigo_book["svc"]
    # The Queue's row: IndigoAir's, with no type yet (No type › Pick a type).
    untaught = indigo_book["book"].row("INDIGOAIR 6E NEW", 41000, "2026-10-03", svc=svc)
    conn.execute("UPDATE transactions SET type_id = NULL, type_source = NULL WHERE id = ?", (untaught,))
    conn.commit()
    home = tf.type_id(conn, "Home")
    before, history_before = state(conn), entries(client)

    sheet_said = preview(client.put(f"/api/services/{svc}", json={"type_id": home}))
    chat_said = chat("change_merchant", service_id=svc, type_id=home)
    queue_said = preview(client.post("/api/transactions/resolve", json={
        "tx_id": untaught, "service_id": svc, "type_id": home, "pattern": "INDIGOAIR"}))

    assert chat_said["ok"] is False and chat_said["preview"] == sheet_said   # the same plan, the same token
    numbers = ("count", "checked", "chosen", "money", "months", "left_out", "examples", "others", "change")
    assert {k: queue_said[k] for k in numbers} == {k: sheet_said[k] for k in numbers}
    # A type change: the 2 rows whose book is chosen have their type from the
    # merchant, so they are reached too; the new row with no type is not counted.
    assert (sheet_said["count"], sheet_said["checked"], sheet_said["chosen"]) == (25, 6, 0)
    assert state(conn) == before and entries(client) == history_before    # three asks, nothing written

    # Answered in chat with the sheet's token: saved as Only new rows, one entry via chat.
    said = chat("change_merchant", service_id=svc, type_id=home, past="only_new", token=sheet_said["token"])
    assert said["ok"], said
    newest = entries(client)[0]
    assert (newest["id"], newest["via"], newest["actor"]) == (said["change"]["entry_id"], "chat", CHAT_ACTOR)
    assert {fields(conn, [t])[t][4] for t in indigo_book["rows"]} == {"checked"}


# --- 6. a preview goes stale when another feature writes its rows (case 20) -----------------


def _before_the_preview(name, conn, ids):
    """What stands before tab A previews: for the drift, a row from the
    merchant left reading an older type (an import before a rule, say)."""
    if name == "the drift applied":
        conn.execute("UPDATE transactions SET type_id = ? WHERE id = ?",
                     (tf.type_id(conn, "Dining"), ids["rows"][4]))
        conn.commit()


def _other_tab(name):
    def write(client, conn, ids):
        rows = ids["rows"]
        if name == "the same change":
            ok(client.put(f"/api/services/{ids['svc']}", json={"book": "Moom", "past": "apply"}))
        elif name == "a row edited in its sheet":
            ok(client.put(f"/api/transactions/{rows[0]}", json={"type_id": tf.type_id(conn, "Dining")}))
        elif name == "chat's set_label":
            assert chat("set_label", tx_id=rows[1], book="Kalesh")["ok"]
        elif name == "a line's ✓":
            ok(client.post("/api/transactions/bulk-label",
                           json={"rows": [{"tx_id": rows[2], "book": "Household"}]}))
        elif name == "Set book":
            ok(client.post("/api/transactions/bulk-label",
                           json={"rows": [{"tx_id": rows[3], "book": "Household"}], "chosen": True}))
        elif name == "its rule edited":
            rule = conn.execute("SELECT id FROM merchant_rules WHERE service_id = ?", (ids["svc"],)).fetchone()[0]
            ok(client.put(f"/api/rules/{rule}", json={"priority": 2}))
        elif name == "Check each row turned on":
            ok(client.post(f"/api/services/{ids['svc']}/check-each-row", json={"on": True, "past": "leave"}))
        elif name == "the drift applied":
            shown = drift(client)
            assert shown["count"] == 1
            ok(client.post("/api/rules/drift", json={"past": "apply", "token": shown["token"]}))
        else:
            raise AssertionError(name)
    return write


OTHER_TABS = ["the same change", "a row edited in its sheet", "chat's set_label", "a line's ✓", "Set book",
              "its rule edited", "Check each row turned on", "the drift applied"]


@pytest.mark.parametrize("other_tab", OTHER_TABS)
def test_a_preview_goes_stale_when_another_feature_writes_its_rows(client, conn, indigo_book, other_tab):
    svc = indigo_book["svc"]
    _before_the_preview(other_tab, conn, indigo_book)
    token = preview(client.put(f"/api/services/{svc}", json={"book": "Moom"}))["token"]
    _other_tab(other_tab)(client, conn, indigo_book)
    before, history_before = state(conn), entries(client)

    resp = client.put(f"/api/services/{svc}", json={"book": "Moom", "past": "apply", "token": token})

    assert resp.status_code == 409 and resp.get_json().get("stale") is True, resp.get_json()
    assert resp.get_json()["error"] == "IndigoAir changed since this preview; look again"
    assert state(conn) == before and entries(client) == history_before


# --- 7. Undo across features (case 19) --------------------------------------------------


def test_undo_is_refused_while_another_features_later_change_holds_the_rows_then_the_book_returns_whole(
        client, conn, indigo_book):
    svc, rows = indigo_book["svc"], indigo_book["rows"]
    start = state(conn)

    applied, _ = answered(client, "PUT", f"/api/services/{svc}", {"type_id": tf.type_id(conn, "Transport")})
    first = change_id(applied)
    said = chat("set_label", tx_id=rows[0], book="Kalesh")
    assert said["ok"], said
    later = said["change"]["entry_id"]

    # The Changes list names the later change on the first, and Undo refuses.
    listed = next(e for e in ok(client.get("/api/history?blockers=1"))["entries"] if e["id"] == first)
    assert listed["blocked_by"]["id"] == later and listed["blocked_by"]["via"] == "chat"
    held = state(conn)
    refused = client.post(f"/api/history/{first}/undo")
    assert refused.status_code == 409 and f"change {later}" in refused.get_json()["error"]
    assert state(conn) == held

    # Chat undoes its own; then the Apply undoes, and every row, the merchant
    # and its rule are as they were, sources included.
    assert chat("undo", entry_id=later)["ok"]
    ok(client.post(f"/api/history/{first}/undo"))
    assert state(conn) == start


def test_a_rule_edit_then_a_merge_over_the_same_rows_undo_in_order_and_the_book_returns_whole(
        client, conn, agreeing):
    """The edit takes Air India's rows to IndigoAir; merging IndigoAir into
    Sample Rides then moves those same rows again."""
    start = state(conn)
    edit, _ = answered(client, "PUT", f"/api/rules/{agreeing['indigo_rule']}", {"pattern": "INDIGO"})
    edit_id = change_id(edit)
    merged, _ = answered(client, "POST", f"/api/services/{agreeing['indigo']}/merge",
                         {"target_id": agreeing["rides"]})
    merge_id = change_id(merged)

    refused = client.post(f"/api/history/{edit_id}/undo")
    assert refused.status_code == 409 and f"change {merge_id}" in refused.get_json()["error"]
    ok(client.post(f"/api/history/{merge_id}/undo"))
    ok(client.post(f"/api/history/{edit_id}/undo"))
    assert state(conn) == start
    assert drift(client)["count"] == 0


# --- 8. the row's sheet names the change that set a mark ------------------------------------


def test_the_rows_sheet_names_the_change_that_set_its_mark_whichever_way_it_came_in(client, conn, indigo_book):
    svc, rows = indigo_book["svc"], indigo_book["rows"]
    reachable = [t for t in rows if fields(conn, [t])[t][3] == "merchant"]
    shown = preview(client.put(f"/api/services/{svc}", json={"book": "Moom"}))

    said = chat("change_merchant", service_id=svc, book="Moom", past="only_new", token=shown["token"])
    assert said["ok"], said
    by_chat = said["change"]["entry_id"]

    page = merchant_page(client, svc)
    assert {page[t]["book"] for t in rows} == {"checked"}             # the tags: book checked on all 23
    assert {page[t]["book"] for t in indigo_book["chosen"]} == {"chosen"}
    set_by = sheet(client, reachable[0])["set_by"]["book"]
    assert (set_by["entry_id"], set_by["via"], set_by["actor"]) == (by_chat, "chat", CHAT_ACTOR)
    assert sheet(client, reachable[0])["set_by"]["type"] is None      # still from the merchant

    # A later type fix in the app reaches them; the book's mark keeps its setter.
    fixed, _ = answered(client, "PUT", f"/api/services/{svc}", {"type_id": tf.type_id(conn, "Transport")})
    ok(fixed)
    assert sheet(client, reachable[0])["set_by"]["book"]["entry_id"] == by_chat
    assert sheet(client, reachable[0])["sources"] == {"book": "checked", "type": "merchant"}


# --- 9. Only new rows means the next statement's rows take the change ----------------------


def test_only_new_rows_means_the_next_statements_rows_take_the_change(client, conn, indigo_book):
    svc, rows = indigo_book["svc"], indigo_book["rows"]
    kept, _ = answered(client, "PUT", f"/api/services/{svc}", {"book": "Moom"}, answer="only_new")
    ok(kept)

    bring_in(client, "Sample Card 0003", [("2026-10-12", "INDIGOAIR 6E 900", 41500)], kind="card",
             closing_date="2026-10-15")

    new = conn.execute("SELECT id FROM transactions WHERE description = 'INDIGOAIR 6E 900'").fetchone()[0]
    assert fields(conn, [new])[new] == (svc, "Moom", tf.type_id(conn, "Travel"), "merchant", "merchant")
    assert {fields(conn, [t])[t][1:4:2] for t in rows} == {("Household", "checked")}
    assert drift(client)["count"] == 0


# --- 10. teaching a wording and adding it as a rule take the same rows (cases 16, 29) -------


@pytest.fixture
def taught(conn):
    """IndigoAir (INDIGOAIR) with its own rows; Air India holding four rows
    that read INDIGO TRANSFER DESK, which no rule of its own matches; one
    row with no merchant waiting in No type, INDIGO 6E 2024."""
    b = Book(conn)
    indigo = b.merchant("IndigoAir", "Household", "Travel", "INDIGOAIR")
    air_india = b.merchant("Air India", "Household", "Transport", "AIR INDIA")
    own = [b.row(f"INDIGOAIR 6E {n}", 41000, f"2026-0{3 + n}-08", svc=indigo) for n in range(3)]
    theirs = [b.row(f"INDIGO TRANSFER DESK {n}", 5200 + n, f"2026-0{4 + n}-12", svc=air_india) for n in range(4)]
    queue = b.row("INDIGO 6E 2024", 16000, "2026-09-02")
    return {"indigo": indigo, "air_india": air_india, "own": own, "theirs": theirs, "queue": queue}


def test_teaching_a_wording_and_adding_it_as_a_rule_name_and_take_the_same_rows(client, conn, taught):
    """The rule shown when teaching (fin-surfaces 53) and the rule preview
    (50) each work out the rows a wording would take; they must agree."""
    indigo, air_india = taught["indigo"], taught["air_india"]
    named = [{"service_id": air_india, "name": "Air India", "count": 4}]
    start = state(conn)

    live = ok(client.get(f"/api/rules/reach?pattern=INDIGO&service_id={indigo}&tx_id={taught['queue']}"))
    teach = {"tx_id": taught["queue"], "service_id": indigo, "pattern": "INDIGO"}
    by_teaching = preview(client.post("/api/transactions/resolve", json=teach))
    add = {"pattern": "INDIGO", "service_id": indigo}
    by_rule = preview(client.post("/api/rules", json=add))

    assert live["others"] == by_teaching["others"] == by_rule["others"] == named
    assert by_teaching["count"] == 4 and by_rule["count"] == 5      # the rule's also labels the waiting row

    # Applied either way, the four rows read alike, sources included.
    ok(client.post("/api/transactions/resolve", json={**teach, "past_rows": "apply", "token": by_teaching["token"]}))
    after_teaching = fields(conn, taught["theirs"] + [taught["queue"]])
    ok(client.post(f"/api/history/{entries(client)[0]['id']}/undo"))
    assert state(conn)[0] == start[0]
    ok(client.post("/api/rules", json={**add, "past": "apply", "token": by_rule["token"]}))
    after_rule = fields(conn, taught["theirs"] + [taught["queue"]])
    assert after_rule == after_teaching
    assert {after_rule[t] for t in taught["theirs"]} == {
        (indigo, "Household", tf.type_id(conn, "Travel"), "merchant", "merchant")}


def test_lists_rules_count_of_rows_a_rule_labels_is_the_delete_previews(client, conn, agreeing):
    """Case 17: "9 rows are labelled by this rule" is what Lists › Rules says
    the rule labels."""
    listed = {r["id"]: r["rows_labelled"] for r in ok(client.get("/api/rules"))}
    for rule in (agreeing["indigo_rule"], agreeing["rides_rule"]):
        shown = preview(client.delete(f"/api/rules/{rule}"))
        assert shown["labelled"] == listed[rule] > 0


# --- 11. a converted book waits and previews as the old mark did (cases 32, 1, 10, 11) --------


def test_a_book_converted_from_todays_shape_waits_varies_and_previews_as_its_old_mark_said(todays, monkeypatch):
    """Case 32's book, Grab set to check each row, converted through the one
    command. What waited (typed spending rows not hand-set) waits still;
    "book varies" counts the 6 hand-set Moom rows still; and the preview
    reads the 34 rows hand-set as Grab's as checked, the 6 Moom ones as
    chosen."""
    old = sqlite3.connect(str(todays))
    try:
        grab = old.execute("SELECT id FROM services WHERE name = 'Grab'").fetchone()[0]
        old.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (grab,))
        old.commit()
        waited = sorted(r[0] for r in old.execute(
            "SELECT id FROM transactions WHERE service_id = ? AND COALESCE(cat_source, 'auto') != 'manual'"
            " AND type_id IS NOT NULL AND COALESCE(flow_type, 'expense') IN ('expense', 'refund')", (grab,)))
        varied = old.execute(
            "SELECT COUNT(*) FROM transactions WHERE service_id = ? AND cat_source = 'manual'"
            " AND COALESCE(book, 'Household') != 'Household'", (grab,)).fetchone()[0]
    finally:
        old.close()
    assert waited and varied == 6

    assert convert_all.main([str(todays)]) == 0
    monkeypatch.setattr(db, "DB_PATH", todays)
    db.invalidate_rules_cache()
    db.init_db()
    db.invalidate_rules_cache()
    fin_app.app.config["TESTING"] = True
    with fin_app.app.test_client() as client:
        assert waiting(client, grab) == waited
        assert ok(client.get(f"/api/services/{grab}/book-varies"))["moved"] == varied
        book_change = preview(client.put(f"/api/services/{grab}", json={"book": "Kalesh"}))
        assert (book_change["count"], book_change["checked"], book_change["chosen"]) == (35, 34, 6)
        conn = db.get_connection()
        try:
            dining = tf.type_id(conn, "Dining")
        finally:
            conn.close()
        type_change = preview(client.put(f"/api/services/{grab}", json={"type_id": dining}))
        assert (type_change["count"], type_change["checked"], type_change["chosen"]) == (41, 40, 0)
    db.invalidate_rules_cache()
