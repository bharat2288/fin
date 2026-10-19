"""Personal-ledger 40: waiting for its row (E2's waiting half, the set_off.expectation machine).

Nothing yet in the Back for… drawer leaves a payment's open part waiting for its row (a set-off
with no expectation, a person and a note instead); attaching it by hand to a row makes an
expectation of that size, closed at once, or splits the set-off into a placed part and a smaller
waiting one; Unlink on a waiting set-off (Un-wait) gives the payment its own part back.

Seam 1 of the 23 spec (the HTTP routes through the Flask test client, figures in whole minor
units, refusals that write nothing, each write one change that undo from Changes takes back),
and Seam 2's open_as_at for the as-at days of case 17e. The client's pure helpers run under node
with the script on stdin. Answers are checked against ticket 26's pinned shapes
(tests/fixtures/expect/waiting.json) through the contract test's own checkers.

Every name and figure is invented; the book is the case list's October story.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import expect_back
from expect_fixtures import last_entry, mark, october, row, set_off
from test_expect_contract import SET_OFF, check_expectation, check_parts

STATIC = Path(__file__).resolve().parent.parent / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")
BBQ = "for the BBQ, when it is charged"


# ---------------------------------------------------------------------------
# The book
# ---------------------------------------------------------------------------

@pytest.fixture
def book(conn):
    """The October story with Lena's S$ 100.00 for the BBQ (17 Oct) and the rows that may
    arrive for it: COLD STORAGE S$ 260.00 (21 Oct) and a smaller COLD STORAGE S$ 80.00."""
    o = october(conn)
    b = o.book
    o.lena_bbq = row(conn, b, b.savings, "2026-10-17", "PAYNOW FROM LENA 2210", -10000, flow="review")
    o.cold_260 = row(conn, b, b.everyday_card, "2026-10-21", "COLD STORAGE", 26000, type_name="Groceries")
    o.cold_80 = row(conn, b, b.rewards_card, "2026-10-21", "COLD STORAGE", 8000, type_name="Groceries")
    o.airbnb_x = mark(conn, o.airbnb, 4000, names=(o.people["Lena"],))
    o.harbour_x = mark(conn, o.harbour, 20000, names=(o.people["Jay"], o.people["Kim"]))
    return o


def snapshot(conn) -> dict:
    return {
        "set_off": conn.execute("SELECT * FROM set_off ORDER BY id").fetchall(),
        "expectation": conn.execute("SELECT * FROM expectation ORDER BY id").fetchall(),
        "expectation_name": conn.execute("SELECT * FROM expectation_name ORDER BY id").fetchall(),
        "entries": conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0],
    }


def refused(conn, resp, words: str, before: dict, status: int = 400):
    assert resp.status_code == status, resp.get_json()
    assert resp.get_json() == {"error": words}
    assert "X-Fin-Change" not in resp.headers
    assert snapshot(conn) == before


def nothing_yet(client, tx_id, **body):
    return client.post(f"/api/transactions/{tx_id}/waiting", json=body)


def attach(client, set_off_id, row_id):
    return client.post(f"/api/set-offs/{set_off_id}/attach", json={"row_id": row_id})


def unwait(client, set_off_id):
    return client.delete(f"/api/set-offs/{set_off_id}")


def undo(client, entry_id):
    return client.post(f"/api/history/{entry_id}/undo")


def own(conn, row_id) -> int:
    return expect_back.parts(conn, [row_id])[row_id]["own_minor"]


def waiting_as_at(conn, day: str) -> list[tuple[int, int, str]]:
    return [(w["id"], w["amount_minor"], w["day"]) for w in expect_back.open_as_at(conn, day)["waiting"]]


def lena_waits(client, conn, o) -> int:
    resp = nothing_yet(client, o.lena_bbq, person_id=o.people["Lena"], note=BBQ)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["set_off"]["id"]


# ---------------------------------------------------------------------------
# Case 17: Nothing yet
# ---------------------------------------------------------------------------

def test_case_17_nothing_yet_leaves_the_payment_waiting_not_income(client, conn, book):
    resp = nothing_yet(client, book.lena_bbq, person_id=book.people["Lena"], note=BBQ)
    assert resp.status_code == 200, resp.get_json()
    change = int(resp.headers["X-Fin-Change"])
    assert change == last_entry(conn)
    out = resp.get_json()
    assert set(out) == {"set_off", "payment"}
    s = out["set_off"]
    assert set(s) == SET_OFF
    assert (s["payment_row_id"], s["expectation_id"], s["amount_minor"], s["currency"], s["state"]) == (
        book.lena_bbq, None, 10000, "SGD", "waiting")
    assert (s["exp_amount_minor"], s["exp_currency"], s["rate"], s["date"], s["source"]) == (
        None, None, None, "2026-10-17", "hand")
    assert s["person"] == {"account_id": book.people["Lena"], "name": "Lena"} and s["note"] == BBQ
    p = out["payment"]
    check_parts(p)
    assert (p["amount_minor"], p["placed_minor"], p["own_minor"], p["left_minor"]) == (-10000, -10000, 0, 0)
    # Books' Waiting for its row (E5's one function) holds it; no expectation moved.
    assert waiting_as_at(conn, "2026-10-19") == [(s["id"], 10000, "2026-10-17")]
    assert [x["open_minor"] for x in expect_back.open_as_at(conn, "2026-10-19")["expectations"]] == [4000, 20000]
    summary = conn.execute("SELECT summary FROM change_entries WHERE id = ?", (change,)).fetchone()[0]
    assert summary == "Nothing yet: S$ 100 of PAYNOW FROM LENA 2210 waits for its row"


def test_case_17_a_waiting_payment_labelled_income_is_not_income(client, conn, book, monkeypatch):
    monkeypatch.setattr(expect_back, "SWITCHED", True)
    conn.execute("UPDATE transactions SET flow_type = 'income' WHERE id = ?", (book.lena_bbq,))
    conn.commit()
    assert counted(client, book.lena_bbq) == -10000
    lena_waits(client, conn, book)
    assert counted(client, book.lena_bbq) == 0


def counted(client, row_id: int) -> int:
    """What a sum of rows adds for this row, as /api/transactions carries it (the own part once
    switched): the figure every income and spending reader counts."""
    got = client.get(f"/api/transactions?tx_id={row_id}").get_json()
    rows = got["transactions"] if isinstance(got, dict) else got
    [r] = [r for r in rows if r["id"] == row_id]
    return r["counted_minor"]


def test_nothing_yet_takes_what_is_left_of_a_partly_placed_payment(client, conn, book):
    set_off(conn, book.lena_bbq, book.airbnb_x, 4000)
    resp = nothing_yet(client, book.lena_bbq, person_id=book.people["Lena"])
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["set_off"]["amount_minor"] == 6000
    assert resp.get_json()["set_off"]["note"] is None
    assert own(conn, book.lena_bbq) == 0


def test_nothing_yet_with_no_person(client, conn, book):
    resp = nothing_yet(client, book.lena_bbq)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["set_off"]["person"] is None


@pytest.mark.parametrize("what, words", [
    ("nothing_left", "S$ 0 left on this payment"),
    ("card_payoff", "a move between your own accounts"),
    ("company", "only Household money can be expected to pay back: this row is the company's"),
    ("not_a_person", "a name on an expectation is a person's account"),
    ("note_not_text", "a note is text"),
])
def test_nothing_yet_refusals_write_nothing(client, conn, book, what, words):
    tx, body = book.lena_bbq, {"person_id": book.people["Lena"]}
    if what == "nothing_left":
        set_off(conn, book.lena_bbq, book.airbnb_x, 4000)
        set_off(conn, book.lena_bbq, book.harbour_x, 6000)
    elif what == "card_payoff":
        tx = book.card_payoff
    elif what == "company":
        b = book.book
        tx = row(conn, b, b.rewards_card, "2026-10-16", "SUPPLIER CREDIT", -3000, flow="review", book_name="Moom")
    elif what == "not_a_person":
        body = {"person_id": book.book.savings}
    elif what == "note_not_text":
        body = {"note": 5}
    before = snapshot(conn)
    refused(conn, nothing_yet(client, tx, **body), words, before)


def test_nothing_yet_on_no_such_row(client, conn, book):
    resp = nothing_yet(client, 999999)
    assert resp.status_code == 404 and resp.get_json() == {"error": "no such row"}


def test_nothing_yet_is_one_change_undone_from_changes(client, conn, book):
    resp = nothing_yet(client, book.lena_bbq, person_id=book.people["Lena"], note=BBQ)
    assert undo(client, int(resp.headers["X-Fin-Change"])).status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM set_off").fetchone()[0] == 0
    assert own(conn, book.lena_bbq) == -10000


# ---------------------------------------------------------------------------
# Cases 17 and 17a: attach by hand, whole or split
# ---------------------------------------------------------------------------

def test_case_17_attach_a_larger_row_makes_an_expectation_of_the_waiting_size_closed(client, conn, book):
    w = lena_waits(client, conn, book)
    resp = attach(client, w, book.cold_260)
    assert resp.status_code == 200, resp.get_json()
    out = resp.get_json()
    assert set(out) == {"expectation", "placed", "waiting", "row"}
    e = out["expectation"]
    check_expectation(e)
    assert (e["amount_minor"], e["open_minor"], e["state"], e["direction"], e["day"]) == (
        10000, 0, "closed", "back", "2026-10-21")
    assert e["names"] == [{"account_id": book.people["Lena"], "name": "Lena"}]
    assert out["placed"]["state"] == "placed" and out["placed"]["amount_minor"] == 10000
    assert out["placed"]["expectation_id"] == e["id"] and out["placed"]["note"] == BBQ
    assert out["waiting"] is None
    check_parts(out["row"])
    assert (out["row"]["expected_minor"], out["row"]["own_minor"]) == (10000, 16000)
    assert waiting_as_at(conn, "2026-10-31") == []
    assert own(conn, book.lena_bbq) == 0


def test_case_17a_a_smaller_row_splits_the_waiting_set_off_in_one_change(client, conn, book):
    w = lena_waits(client, conn, book)
    entries = conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0]
    resp = attach(client, w, book.cold_80)
    assert resp.status_code == 200, resp.get_json()
    change = int(resp.headers["X-Fin-Change"])
    assert conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0] == entries + 1
    out = resp.get_json()
    e, placed, waiting = out["expectation"], out["placed"], out["waiting"]
    check_expectation(e)
    assert (e["amount_minor"], e["open_minor"], e["state"]) == (8000, 0, "closed")
    assert set(placed) == set(waiting) == SET_OFF
    assert (placed["state"], placed["amount_minor"], placed["exp_amount_minor"]) == ("placed", 8000, 8000)
    assert (waiting["state"], waiting["amount_minor"], waiting["expectation_id"]) == ("waiting", 2000, None)
    assert waiting["person"]["name"] == "Lena" and waiting["note"] == BBQ
    assert placed["amount_minor"] + waiting["amount_minor"] == 10000
    assert (out["row"]["expected_minor"], out["row"]["own_minor"]) == (8000, 0)
    assert own(conn, book.lena_bbq) == 0
    assert waiting_as_at(conn, "2026-10-31") == [(waiting["id"], 2000, "2026-10-17")]
    summary = conn.execute("SELECT summary FROM change_entries WHERE id = ?", (change,)).fetchone()[0]
    assert summary == "Attached S$ 80 of PAYNOW FROM LENA 2210 to COLD STORAGE; S$ 20 still waits"


def test_case_17a_undoing_the_attach_puts_the_whole_waiting_set_off_back(client, conn, book):
    w = lena_waits(client, conn, book)
    before = conn.execute("SELECT * FROM set_off ORDER BY id").fetchall()
    resp = attach(client, w, book.cold_80)
    assert undo(client, int(resp.headers["X-Fin-Change"])).status_code == 200
    assert conn.execute("SELECT * FROM set_off ORDER BY id").fetchall() == before
    assert conn.execute("SELECT COUNT(*) FROM expectation WHERE row_id = ?", (book.cold_80,)).fetchone()[0] == 0
    assert own(conn, book.cold_80) == 8000


def test_the_waiting_rest_keeps_the_day_it_was_set_waiting(client, conn, book):
    w = lena_waits(client, conn, book)
    conn.execute("UPDATE set_off SET made_at = '2026-10-17 09:00:00' WHERE id = ?", (w,))
    conn.commit()
    rest = attach(client, w, book.cold_80).get_json()["waiting"]
    made = conn.execute("SELECT made_at FROM set_off WHERE id = ?", (rest["id"],)).fetchone()[0]
    assert made == "2026-10-17 09:00:00"


# ---------------------------------------------------------------------------
# Case 17e: waiting across a month end
# ---------------------------------------------------------------------------

def test_case_17e_the_expectation_takes_the_rows_day_the_set_off_the_payments(client, conn, book):
    b = book.book
    sept = row(conn, b, b.savings, "2026-09-30", "PAYNOW FROM LENA 2210", -10000, flow="review")
    oct2 = row(conn, b, b.everyday_card, "2026-10-02", "COLD STORAGE", 26000, type_name="Groceries")
    w = nothing_yet(client, sept, person_id=book.people["Lena"]).get_json()["set_off"]["id"]
    out = attach(client, w, oct2).get_json()
    assert out["expectation"]["day"] == "2026-10-02"
    assert out["placed"]["date"] == "2026-09-30"
    e_id = out["expectation"]["id"]
    at_sept = expect_back.open_as_at(conn, "2026-09-30")
    assert [(x["payment_row_id"], x["amount_minor"]) for x in at_sept["waiting"]] == [(sept, 10000)]
    assert e_id not in [x["id"] for x in at_sept["expectations"]]
    assert all(x["open_minor"] >= 0 for x in at_sept["expectations"])
    at_oct = expect_back.open_as_at(conn, "2026-10-31")
    assert [x for x in at_oct["waiting"] if x["payment_row_id"] == sept] == []
    assert [x["open_minor"] for x in at_oct["expectations"] if x["id"] == e_id] == [0]


# ---------------------------------------------------------------------------
# Case 17c: the row never comes
# ---------------------------------------------------------------------------

def test_case_17c_waiting_keeps_its_age_and_nothing_happens_by_itself(client, conn, book):
    w = lena_waits(client, conn, book)
    b = book.book
    # A row that would fit arrives, and other writes happen: nothing attaches on its own.
    row(conn, b, b.everyday_card, "2026-10-25", "FAIRPRICE", 14000, type_name="Groceries")
    assert client.post("/api/refund-links/run").status_code in (200, 404)
    assert waiting_as_at(conn, "2026-12-31") == [(w, 10000, "2026-10-17")]
    assert conn.execute("SELECT expectation_id FROM set_off WHERE id = ?", (w,)).fetchone()[0] is None


# ---------------------------------------------------------------------------
# Case 17d: Un-wait
# ---------------------------------------------------------------------------

def test_case_17d_unlink_gives_the_payment_its_own_part_back(client, conn, book):
    w = lena_waits(client, conn, book)
    resp = unwait(client, w)
    assert resp.status_code == 200, resp.get_json()
    change = int(resp.headers["X-Fin-Change"])
    out = resp.get_json()
    assert out["expectation"] is None
    assert (out["payment"]["own_minor"], out["payment"]["left_minor"]) == (-10000, 10000)
    assert waiting_as_at(conn, "2026-10-31") == []
    summary = conn.execute("SELECT summary FROM change_entries WHERE id = ?", (change,)).fetchone()[0]
    assert summary == "Un-waited S$ 100 of PAYNOW FROM LENA 2210"
    assert undo(client, change).status_code == 200
    assert waiting_as_at(conn, "2026-10-31") == [(w, 10000, "2026-10-17")]


def test_case_17d_unwaited_and_labelled_income_counts_as_income(client, conn, book, monkeypatch):
    monkeypatch.setattr(expect_back, "SWITCHED", True)
    conn.execute("UPDATE transactions SET flow_type = 'income' WHERE id = ?", (book.lena_bbq,))
    conn.commit()
    w = lena_waits(client, conn, book)
    assert counted(client, book.lena_bbq) == 0
    unwait(client, w)
    assert counted(client, book.lena_bbq) == -10000


# ---------------------------------------------------------------------------
# The machine: set_off.expectation
# ---------------------------------------------------------------------------

def test_machine_placed_to_waiting_is_refused(client, conn, book):
    placed = set_off(conn, book.lena_bbq, book.airbnb_x, 4000)
    before = snapshot(conn)
    refused(conn, attach(client, placed, book.cold_80),
            "S$ 40 of this payment is placed against AIRBNB: it is not waiting for its row. "
            "Unlink it to place it again.", before)


def test_machine_a_placed_set_off_stays_placed_when_the_rest_waits(client, conn, book):
    placed = set_off(conn, book.lena_bbq, book.airbnb_x, 4000)
    w = lena_waits(client, conn, book)
    assert tuple(conn.execute("SELECT expectation_id, amount_minor FROM set_off WHERE id = ?",
                              (placed,)).fetchone()) == (book.airbnb_x, 4000)
    assert conn.execute("SELECT amount_minor FROM set_off WHERE id = ?", (w,)).fetchone()[0] == 6000


@pytest.mark.parametrize("what, words", [
    ("marked", "this row already has an expectation: edit that one"),
    ("money_in", "money in can only be expected to pay back"),
    ("rupee", "SWIGGY · Mumbai is kept in ₹ and this payment in S$: attach a row in the payment's currency"),
    ("company", "only Household spending can be expected back: this row is the company's cost"),
    ("transfer", "a move between your own accounts"),
])
def test_attach_refusals_write_nothing(client, conn, book, what, words):
    w = lena_waits(client, conn, book)
    target = {"marked": book.harbour, "money_in": book.jay_0412, "rupee": book.swiggy,
              "company": book.company_row, "transfer": book.card_payoff}[what]
    before = snapshot(conn)
    refused(conn, attach(client, w, target), words, before)


def test_attach_misses(client, conn, book):
    w = lena_waits(client, conn, book)
    resp = attach(client, 999999, book.cold_80)
    assert resp.status_code == 404 and resp.get_json() == {"error": "no such set-off"}
    resp = attach(client, w, 999999)
    assert resp.status_code == 404 and resp.get_json() == {"error": "no such row"}
    before = snapshot(conn)
    refused(conn, client.post(f"/api/set-offs/{w}/attach", json={"row_id": "301"}), "name the row to attach", before)


def test_undoing_nothing_yet_after_the_attach_is_refused(client, conn, book):
    resp = nothing_yet(client, book.lena_bbq, person_id=book.people["Lena"])
    first = int(resp.headers["X-Fin-Change"])
    attach(client, resp.get_json()["set_off"]["id"], book.cold_80)
    resp = undo(client, first)
    assert resp.status_code in (400, 409), resp.get_json()
    assert conn.execute("SELECT COUNT(*) FROM set_off").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# A set-off request carries the rest as a waiting part
# ---------------------------------------------------------------------------

def place(client, payment_id, placements, waiting):
    return client.post(f"/api/transactions/{payment_id}/set-offs",
                       json={"placements": [{"expectation_id": e, "amount": a} for e, a in placements],
                             "waiting": waiting})


def test_a_set_off_request_leaves_the_rest_waiting_in_the_same_change(client, conn, book):
    resp = place(client, book.lena_2210, [(book.airbnb_x, "40.00")], {"person_id": book.people["Lena"], "note": BBQ})
    assert resp.status_code == 200, resp.get_json()
    out = resp.get_json()
    assert set(out) == {"payment", "set_offs", "expectations", "learnt", "waiting"}
    assert [s["amount_minor"] for s in out["set_offs"]] == [4000]
    assert (out["waiting"]["state"], out["waiting"]["amount_minor"], out["waiting"]["note"]) == ("waiting", 2000, BBQ)
    assert (out["payment"]["own_minor"], out["payment"]["left_minor"]) == (0, 0)
    change = int(resp.headers["X-Fin-Change"])
    assert {r[0] for r in conn.execute("SELECT DISTINCT tbl FROM change_rows WHERE entry_id = ?", (change,))} >= {"set_off"}
    assert conn.execute("SELECT COUNT(*) FROM change_rows WHERE entry_id = ? AND tbl = 'set_off'",
                        (change,)).fetchone()[0] == 2
    assert undo(client, change).status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM set_off WHERE payment_row_id = ?", (book.lena_2210,)).fetchone()[0] == 0


def test_a_waiting_part_may_name_its_amount_the_rest_stays_to_label(client, conn, book):
    resp = place(client, book.lena_2210, [(book.airbnb_x, "40.00")], {"amount": "10.00", "person_id": book.people["Lena"]})
    assert resp.status_code == 200, resp.get_json()
    out = resp.get_json()
    assert out["waiting"]["amount_minor"] == 1000
    assert (out["payment"]["own_minor"], out["payment"]["left_minor"]) == (-1000, 1000)


def test_a_waiting_part_alone(client, conn, book):
    resp = place(client, book.lena_2210, [], {"person_id": book.people["Lena"]})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["set_offs"] == [] and resp.get_json()["waiting"]["amount_minor"] == 6000


def test_without_a_waiting_part_the_answer_is_as_before(client, conn, book):
    resp = place(client, book.lena_2210, [(book.airbnb_x, "40.00")], None)
    assert set(resp.get_json()) == {"payment", "set_offs", "expectations", "learnt"}


@pytest.mark.parametrize("placements, waiting, words", [
    ([("harbour", "60.00")], {}, "nothing is left on this payment to wait for its row"),
    ([("airbnb", "40.00")], {"amount": "30.00"}, "S$ 30 is more than the S$ 20 left on this payment"),
    ([("airbnb", "40.00")], {"amount": "2.005"}, "2.005 is not an amount in whole cents"),
    ([("airbnb", "40.00")], {"person_id": "bad"}, "a name on an expectation is a person's account"),
    ([], None, "tick at least one item to set this payment off against"),
    ([("airbnb", "40.00")], "soon", "the waiting part names who it is from and a note"),
])
def test_a_request_with_a_bad_waiting_part_writes_none_of_it(client, conn, book, placements, waiting, words):
    ids = {"harbour": book.harbour_x, "airbnb": book.airbnb_x}
    before = snapshot(conn)
    refused(conn, place(client, book.lena_2210, [(ids[k], a) for k, a in placements], waiting), words, before)


# ---------------------------------------------------------------------------
# The reads the drawers draw from
# ---------------------------------------------------------------------------

def test_the_waiting_drawer_read(client, conn, book):
    set_off(conn, book.lena_bbq, book.airbnb_x, 4000)
    w = lena_waits(client, conn, book)
    resp = client.get(f"/api/transactions/{book.lena_bbq}/waiting")
    assert resp.status_code == 200
    f = resp.get_json()
    assert set(f) == {"payment", "people", "sender", "waiting"}
    check_parts(f["payment"])
    assert f["payment"]["left_minor"] == 0 and f["payment"]["description"] == "PAYNOW FROM LENA 2210"
    assert {"account_id": book.people["Lena"], "name": "Lena"} in f["people"]
    assert f["sender"] == {"account_id": book.people["Lena"], "name": "Lena"}
    assert [(s["id"], s["amount_minor"], s["state"]) for s in f["waiting"]] == [(w, 6000, "waiting")]
    assert client.get("/api/transactions/999999/waiting").status_code == 404


def test_the_attach_drawer_read_lists_rows_that_may_take_it_nearest_first(client, conn, book):
    w = lena_waits(client, conn, book)
    resp = client.get(f"/api/set-offs/{w}/attach")
    assert resp.status_code == 200
    f = resp.get_json()
    assert set(f) == {"set_off", "rows"}
    assert f["set_off"]["id"] == w and f["set_off"]["state"] == "waiting"
    ids = [r["id"] for r in f["rows"]]
    # Money out in S$, Household, no expectation, on or after the payment and within 90 days.
    assert book.cold_260 in ids and book.cold_80 in ids
    for gone in (book.harbour, book.swiggy, book.company_row, book.card_payoff, book.jay_0412, book.klook):
        assert gone not in ids
    by = {r["id"]: r for r in f["rows"]}
    assert by[book.cold_80]["would"] == {"expected_minor": 8000, "placed_minor": 8000, "waiting_minor": 2000}
    assert by[book.cold_260]["would"] == {"expected_minor": 10000, "placed_minor": 10000, "waiting_minor": 0}
    days = [r["date"] for r in f["rows"]]
    assert days == sorted(days)
    assert client.get("/api/set-offs/999999/attach").status_code == 404


# ---------------------------------------------------------------------------
# The client's pure helpers, under node
# ---------------------------------------------------------------------------

def run_node(code: str):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def waiting_js(expr: str):
    prelude = (
        "globalThis.window = globalThis;\n"
        "globalThis.money = (minor, cur = 'SGD') => `${cur === 'INR' ? '₹' : 'S$'} ${(minor / 100).toFixed(2)}`;\n"
    )
    shells = "\n".join((STATIC / f"{s}.js").read_text(encoding="utf-8") for s in ("expect-mark", "expect-waiting"))
    return run_node(prelude + shells + f"\nprocess.stdout.write(JSON.stringify({expr}));")


@needs_node
def test_client_the_footer_offers_nothing_yet_while_something_is_left():
    got = waiting_js("[Expect.waiting.footButton({id: 7, left_minor: 10000, placed_minor: 0}),"
                     " Expect.waiting.footButton({id: 7, left_minor: 0, placed_minor: -10000}),"
                     " Expect.waiting.footButton({id: 7, left_minor: 0, placed_minor: 0})]")
    assert got[0] == {"label": "Nothing yet", "tx": 7}
    assert got[1] == {"label": "Waiting…", "tx": 7}
    assert got[2] is None
    drawn = waiting_js("Expect.draw('backForFoot', {id: 7, left_minor: 10000, placed_minor: 0})")
    assert "Nothing yet" in drawn and 'data-xb-nothing-yet="7"' in drawn


@needs_node
def test_client_attach_words_read_the_servers_split():
    got = waiting_js("[Expect.waiting.wouldWords({expected_minor: 8000, placed_minor: 8000, waiting_minor: 2000}, 'SGD'),"
                     " Expect.waiting.wouldWords({expected_minor: 10000, placed_minor: 10000, waiting_minor: 0}, 'SGD')]")
    assert got == ["Expect back S$ 80, closed at once · S$ 20 still waits",
                   "Expect back S$ 100, closed at once"]
