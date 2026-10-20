"""Personal-ledger 39: Back for… across currencies (E2's rate branch, cases 19, 19a, 19b, 20).

A S$ payment placed against an item kept in rupees converts at the rate of the
**payment's month**, through money's one inverse, half-even: an ended month at its
own month-end rate, or refused naming the month (with what a Fetch would save); the
month still running at the latest rate saved on or before the day of placing, saying
so, and never restated when the month's own rate arrives. The record keeps both
amounts, the rate text and its date. Nothing here, nor opening the drawer, fetches.

Seam 1 of the 23 spec: the HTTP routes through the Flask test client on a temporary
database, figures in and out in whole minor units, refusals that write nothing (no
change entry, the records unchanged), each write one change that undo from Changes
takes back. The drawer's rate line is the client's pure helper, run under node with
app.js's money and day helpers lifted from the real file (script on stdin).

Every name and figure is invented: the case list's check book.
"""

from __future__ import annotations

import json
import subprocess

import pytest

import expect_back
import rates
import setoff_api
from expect_fixtures import last_entry, mark, october, row
from test_expect_contract import EXP_BRIEF, SET_OFF, check_parts, load
from test_fill_oldest_first import LIFTED, NODE, app_part
from trend_fixtures import seed_rate

PAIR = "INR/SGD"


@pytest.fixture(autouse=True)
def no_fetch(monkeypatch):
    """Any reach outside for a rate fails the test, unless a test stands in for the
    source itself (the Fetch the refusal offers)."""
    def refuse(url):
        raise AssertionError(f"a rate was fetched: {url}")
    monkeypatch.setattr(rates, "_http_get", refuse)


@pytest.fixture(autouse=True)
def placed_on_19_oct(monkeypatch):
    """Today, the day of placing, is 19 Oct 2026, as the case list has it."""
    monkeypatch.setattr(setoff_api, "today", lambda: "2026-10-19")


@pytest.fixture
def book(conn):
    """The October story with the case list's saved rates (0.0160 on 31 Aug, 0.0155
    on 30 Sep; none in June, none yet for October) and its rupee items, each expected
    back in full: PURPLE LOTUS MUMBAI ₹ 6,000.00 (Jay), TAJ BENGALURU ₹ 8,000.00
    (Jay), CHAAT HOUSE DELHI ₹ 3,000.00 (Kim); and the S$ payments set against them."""
    o = october(conn)
    b, p = o.book, o.people
    seed_rate(conn, PAIR, "2026-08-31", "0.0160")
    seed_rate(conn, PAIR, "2026-09-30", "0.0155")
    lotus = row(conn, b, b.rupee_card, "2026-09-04", "PURPLE LOTUS MUMBAI", 600000, type_name="Dining")
    taj = row(conn, b, b.rupee_card, "2026-09-26", "TAJ BENGALURU", 800000, type_name="Dining")
    chaat = row(conn, b, b.rupee_card, "2026-08-20", "CHAAT HOUSE DELHI", 300000, type_name="Dining")
    o.fx = {
        "lotus": mark(conn, lotus, 600000, names=(p["Jay"],)),
        "taj": mark(conn, taj, 800000, names=(p["Jay"],)),
        "chaat": mark(conn, chaat, 300000, names=(p["Kim"],)),
    }
    o.jay_22_sep = row(conn, b, b.savings, "2026-09-22", "PAYNOW FROM JAY TAN", -9000, flow="review")
    o.jay_0471 = row(conn, b, b.savings, "2026-10-10", "PAYNOW FROM JAY TAN 0471", -9000, flow="review")
    o.kim_5_sep = row(conn, b, b.savings, "2026-09-05", "PAYNOW FROM KIM L 7781", -3100, flow="review")
    o.june_50 = row(conn, b, b.savings, "2026-06-08", "PAYNOW FROM JAY TAN 0302", -5000, flow="review")
    return o


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def place(client, payment_id, *placements, preview=None):
    body = {"placements": [{"expectation_id": e, "amount": a} for e, a in placements], "waiting": None}
    if preview is not None:
        body["preview"] = preview
    return client.post(f"/api/transactions/{payment_id}/set-offs", json=body)


def snapshot(conn) -> dict:
    return {
        "set_off": conn.execute("SELECT * FROM set_off ORDER BY id").fetchall(),
        "sender_wording": conn.execute("SELECT * FROM sender_wording ORDER BY id").fetchall(),
        "entries": conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0],
    }


def refused(conn, resp, body: dict, before: dict):
    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json() == body
    assert "X-Fin-Change" not in resp.headers
    assert snapshot(conn) == before


def record(conn, set_off_id) -> tuple:
    return tuple(conn.execute(
        "SELECT amount_minor, item_amount_minor, rate, rate_date FROM set_off WHERE id = ?",
        (set_off_id,)).fetchone())


def own_of(conn, row_id) -> int:
    return expect_back.parts(conn, [row_id])[row_id]["own_minor"]


def open_of(conn, expectation_id) -> int:
    return expect_back.open_minor(conn, expectation_id)


def rate_line(set_off: dict) -> str:
    """The drawer's rate line for one set-off answer, by the client's own helper."""
    code = "\n".join([
        LIFTED,
        app_part(r"^const LONG_MONTHS = .*?;$"),
        f"console.log(JSON.stringify(X.rateLine({json.dumps(set_off)})));",
    ])
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


# ---------------------------------------------------------------------------
# Case 19: an ended month, at its own month-end rate
# ---------------------------------------------------------------------------

def test_case_19_a_payment_in_an_ended_month_sets_off_at_its_month_end_rate(client, conn, book):
    resp = place(client, book.jay_22_sep, (book.fx["lotus"], "90.00"))
    assert resp.status_code == 200, resp.get_json()
    out = resp.get_json()
    [s] = out["set_offs"]
    assert set(s) == SET_OFF
    # 9,000 cents / 0.0155 = 580,645.16 paise -> ₹ 5,806.45 (half-even); ₹ 193.55 open
    assert (s["amount_minor"], s["currency"], s["exp_amount_minor"], s["exp_currency"]) == (9000, "SGD", 580645, "INR")
    assert (s["rate"], s["rate_date"], s["rate_month_ended"]) == ("0.0155", "2026-09-30", True)
    [e] = out["expectations"]
    assert set(e) == EXP_BRIEF
    assert (e["id"], e["open_minor"], e["currency"], e["state"]) == (book.fx["lotus"], 19355, "INR", "partly_back")
    check_parts(out["payment"])
    assert (out["payment"]["own_minor"], out["payment"]["left_minor"]) == (0, 0)
    # the record holds both amounts, the rate text and its date
    assert record(conn, s["id"]) == (9000, 580645, "0.0155", "2026-09-30")
    assert open_of(conn, book.fx["lotus"]) == 19355 and own_of(conn, book.jay_22_sep) == 0


def test_case_19_the_answer_is_the_shape_ticket_26_pinned(client, conn, book):
    pinned = load("setoff")["cross_currency"]["response"]
    out = place(client, book.jay_22_sep, (book.fx["lotus"], "90.00")).get_json()
    ignore = {"id", "payment_row_id", "expectation_id", "person", "note"}
    assert [{k: v for k, v in s.items() if k not in ignore} for s in out["set_offs"]] == \
           [{k: v for k, v in s.items() if k not in ignore} for s in pinned["set_offs"]]
    assert [{k: v for k, v in e.items() if k != "id"} for e in out["expectations"]] == \
           [{k: v for k, v in e.items() if k != "id"} for e in pinned["expectations"]]


def test_case_19_is_one_change_and_undo_restores_both_amounts(client, conn, book):
    resp = place(client, book.jay_22_sep, (book.fx["lotus"], "90.00"))
    change = int(resp.headers["X-Fin-Change"])
    assert change == last_entry(conn)
    assert client.post(f"/api/history/{change}/undo").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM set_off").fetchone()[0] == 0
    assert open_of(conn, book.fx["lotus"]) == 600000
    assert own_of(conn, book.jay_22_sep) == -9000


@node
def test_case_19_the_drawer_line(client, conn, book):
    [s] = place(client, book.jay_22_sep, (book.fx["lotus"], "90.00")).get_json()["set_offs"]
    assert rate_line(s) == "S$ 90 = ₹ 5,806.45 at 0.0155 (30 Sep)"


# ---------------------------------------------------------------------------
# Case 19a: the payment's month still running
# ---------------------------------------------------------------------------

def test_case_19a_the_running_month_uses_the_latest_rate_and_is_never_restated(client, conn, book):
    resp = place(client, book.jay_0471, (book.fx["taj"], "90.00"))
    assert resp.status_code == 200, resp.get_json()
    [s] = resp.get_json()["set_offs"]
    assert (s["exp_amount_minor"], s["rate"], s["rate_date"], s["rate_month_ended"]) == (580645, "0.0155", "2026-09-30", False)
    assert open_of(conn, book.fx["taj"]) == 219355          # ₹ 2,193.55 open
    held = record(conn, s["id"])
    assert held == (9000, 580645, "0.0155", "2026-09-30")
    # 31 Oct's rate arrives: the placement is not restated
    seed_rate(conn, PAIR, "2026-10-31", "0.0150")
    assert record(conn, s["id"]) == held
    assert open_of(conn, book.fx["taj"]) == 219355
    [taj] = [e for e in client.get(f"/api/transactions/{book.kim_7781}/back-for").get_json()["items"]
             if e["id"] == book.fx["taj"]]
    assert taj["open_minor"] == 219355


@node
def test_case_19a_the_drawer_line_says_the_month_has_no_rate_yet(client, conn, book):
    [s] = place(client, book.jay_0471, (book.fx["taj"], "90.00")).get_json()["set_offs"]
    assert rate_line(s) == "S$ 90 = ₹ 5,806.45 at 0.0155 (30 Sep; October has no rate yet)"


@node
def test_a_running_month_with_its_own_rate_so_far_says_it_has_not_ended(client, conn, book):
    seed_rate(conn, PAIR, "2026-10-16", "0.0150")
    [s] = place(client, book.jay_0471, (book.fx["taj"], "90.00")).get_json()["set_offs"]
    # 9,000 / 0.0150 = 600,000 paise
    assert (s["exp_amount_minor"], s["rate_date"], s["rate_month_ended"]) == (600000, "2026-10-16", False)
    assert rate_line(s) == "S$ 90 = ₹ 6,000 at 0.0150 (16 Oct; October has not ended)"


# ---------------------------------------------------------------------------
# Case 19b: the payment's month, not the expectation's
# ---------------------------------------------------------------------------

def test_case_19b_the_payment_month_rate_not_the_expectations(client, conn, book):
    resp = place(client, book.kim_5_sep, (book.fx["chaat"], "31.00"))
    assert resp.status_code == 200, resp.get_json()
    [s] = resp.get_json()["set_offs"]
    # 3,100 cents / 0.0155 = 200,000 paise; at August's 0.0160 it would be 193,750
    assert (s["exp_amount_minor"], s["rate"], s["rate_date"]) == (200000, "0.0155", "2026-09-30")
    assert open_of(conn, book.fx["chaat"]) == 100000         # ₹ 1,000.00 open


# ---------------------------------------------------------------------------
# Case 20: an ended month with no saved rate
# ---------------------------------------------------------------------------

def test_case_20_no_saved_rate_for_an_ended_month_is_refused_and_writes_nothing(client, conn, book):
    before = snapshot(conn)
    refused(conn, place(client, book.june_50, (book.fx["lotus"], "50.00")),
            {"error": "no saved INR/SGD rate for June 2026. Fetch it.",
             "fetch": {"currency": "INR", "date": "2026-06-30"}}, before)
    assert open_of(conn, book.fx["lotus"]) == 600000


def test_case_20_the_fetch_offered_is_the_existing_rates_fetch_and_then_it_places(client, conn, book, monkeypatch):
    fetch = place(client, book.june_50, (book.fx["lotus"], "50.00")).get_json()["fetch"]
    asked = []

    def source(url):
        asked.append(url)
        return '{"date": "2026-06-30", "rates": {"SGD": 0.0160}}'
    monkeypatch.setattr(rates, "_http_get", source)
    saved = client.post("/api/rates/fetch", json=fetch)
    assert saved.status_code == 200, saved.get_json()
    assert len(asked) == 1
    resp = place(client, book.june_50, (book.fx["lotus"], "50.00"))
    assert resp.status_code == 200, resp.get_json()
    [s] = resp.get_json()["set_offs"]
    # 5,000 / 0.0160 = 312,500 paise
    assert (s["exp_amount_minor"], s["rate"], s["rate_date"], s["rate_month_ended"]) == (312500, "0.0160", "2026-06-30", True)


def test_opening_the_drawer_never_fetches(client, conn, book):
    before = snapshot(conn)
    resp = client.get(f"/api/transactions/{book.june_50}/back-for")
    assert resp.status_code == 200
    ids = [e["id"] for e in resp.get_json()["items"]]
    assert book.fx["lotus"] in ids                               # listed, in its own currency
    assert snapshot(conn) == before


def test_the_running_month_with_no_rate_at_all_is_refused_naming_the_day(client, conn, book):
    conn.execute("DELETE FROM rates")
    conn.commit()
    before = snapshot(conn)
    refused(conn, place(client, book.jay_0471, (book.fx["taj"], "90.00")),
            {"error": "no saved INR/SGD rate on or before 19 Oct 2026. Fetch it.",
             "fetch": {"currency": "INR", "date": "2026-10-19"}}, before)


# ---------------------------------------------------------------------------
# One request, all or none, across currencies
# ---------------------------------------------------------------------------

def test_one_payment_across_both_currencies_is_one_change(client, conn, book):
    harbour = mark(conn, book.harbour, 20000, names=(book.people["Jay"],))
    resp = place(client, book.jay_0412, (harbour, "30.00"), (book.fx["lotus"], "90.00"))
    assert resp.status_code == 200, resp.get_json()
    a, b = resp.get_json()["set_offs"]
    assert (a["exp_amount_minor"], a["rate"], a["rate_month_ended"]) == (3000, None, None)
    # the payment is 10 Oct: October runs, so the latest rate on or before 19 Oct
    assert (b["exp_amount_minor"], b["rate_date"], b["rate_month_ended"]) == (580645, "2026-09-30", False)
    assert int(resp.headers["X-Fin-Change"]) == last_entry(conn)


def test_a_placement_with_no_rate_writes_none_of_the_request(client, conn, book):
    june_dinner = row(conn, book.book, book.book.rewards_card, "2026-06-02", "SAMPLE BISTRO", 8000, type_name="Dining")
    june_owed = mark(conn, june_dinner, 8000)
    before = snapshot(conn)
    resp = place(client, book.june_50, (june_owed, "10.00"), (book.fx["lotus"], "40.00"))
    refused(conn, resp, resp.get_json(), before)
    assert resp.get_json()["error"] == "no saved INR/SGD rate for June 2026. Fetch it."
    assert (open_of(conn, june_owed), open_of(conn, book.fx["lotus"])) == (8000, 600000)


def test_more_than_is_open_is_refused_in_the_items_currency(client, conn, book):
    # S$ 100 at 0.0155 = ₹ 6,451.61, more than the ₹ 6,000.00 open
    big = row(conn, book.book, book.book.savings, "2026-09-23", "PAYNOW FROM JAY TAN 0390", -10000, flow="review")
    before = snapshot(conn)
    refused(conn, place(client, big, (book.fx["lotus"], "100.00")),
            {"error": "PURPLE LOTUS MUMBAI has ₹ 6,000.00 open"}, before)


def test_what_the_drawer_showed_open_is_read_in_the_items_currency(client, conn, book):
    # the drawer showed ₹ 6,000.00 open; another tab has since placed ₹ 5,806.45
    assert place(client, book.jay_22_sep, (book.fx["lotus"], "90.00")).status_code == 200
    second = row(conn, book.book, book.book.savings, "2026-09-24", "PAYNOW FROM JAY TAN 0391", -500, flow="review")
    before = snapshot(conn)
    body = {"placements": [{"expectation_id": book.fx["lotus"], "amount": "5.00", "open_minor": 600000}], "waiting": None}
    resp = client.post(f"/api/transactions/{second}/set-offs", json=body)
    refused(conn, resp, {"error": "PURPLE LOTUS MUMBAI has ₹ 193.55 open now"}, before)
    # S$ 3.00 still fits the ₹ 193.55 open: ₹ 193.548… -> ₹ 193.55
    body["placements"][0]["amount"] = "3.00"
    ok = client.post(f"/api/transactions/{second}/set-offs", json=body)
    assert ok.status_code == 200, ok.get_json()
    assert open_of(conn, book.fx["lotus"]) == 0


def test_an_amount_too_small_to_be_a_paisa_is_refused(client, conn, book):
    # a rupee payment against a S$ item: ₹ 0.10 x 0.0155 = S$ 0.00155 -> no whole cent
    b = book.book
    sgd_item = mark(conn, book.klook, 18000)
    rupee_in = row(conn, b, b.rupee_card, "2026-09-12", "REFUND SAMPLE STALL", -10, flow="review")
    before = snapshot(conn)
    refused(conn, place(client, rupee_in, (sgd_item, "0.10")),
            {"error": "₹ 0.10 is less than S$ 0.01 at 0.0155"}, before)


def test_a_rupee_payment_against_a_dollar_item_multiplies(client, conn, book):
    b = book.book
    sgd_item = mark(conn, book.klook, 18000)
    rupee_in = row(conn, b, b.rupee_card, "2026-09-12", "REFUND SAMPLE STALL", -100000, flow="review")
    resp = place(client, rupee_in, (sgd_item, "1000.00"))
    assert resp.status_code == 200, resp.get_json()
    [s] = resp.get_json()["set_offs"]
    # ₹ 1,000.00 x 0.0155 = S$ 15.50
    assert (s["amount_minor"], s["currency"], s["exp_amount_minor"], s["exp_currency"]) == (100000, "INR", 1550, "SGD")
    assert open_of(conn, sgd_item) == 16450


# ---------------------------------------------------------------------------
# The drawer's preview: the same figures, nothing written
# ---------------------------------------------------------------------------

def test_a_preview_answers_the_write_and_writes_nothing(client, conn, book):
    before = snapshot(conn)
    resp = place(client, book.jay_22_sep, (book.fx["lotus"], "90.00"), preview=True)
    assert resp.status_code == 200, resp.get_json()
    assert "X-Fin-Change" not in resp.headers
    assert snapshot(conn) == before
    [s] = resp.get_json()["set_offs"]
    assert (s["exp_amount_minor"], s["rate"], s["rate_date"], s["rate_month_ended"]) == (580645, "0.0155", "2026-09-30", True)
    written = place(client, book.jay_22_sep, (book.fx["lotus"], "90.00")).get_json()["set_offs"][0]
    assert {k: v for k, v in s.items() if k != "id"} == {k: v for k, v in written.items() if k != "id"}


def test_a_preview_refuses_as_the_write_does(client, conn, book):
    before = snapshot(conn)
    refused(conn, place(client, book.june_50, (book.fx["lotus"], "50.00"), preview=True),
            {"error": "no saved INR/SGD rate for June 2026. Fetch it.",
             "fetch": {"currency": "INR", "date": "2026-06-30"}}, before)


def test_preview_must_be_true_or_absent(client, conn, book):
    before = snapshot(conn)
    refused(conn, place(client, book.jay_22_sep, (book.fx["lotus"], "90.00"), preview="yes"),
            {"error": "preview is true or left out"}, before)


# ---------------------------------------------------------------------------
# The drawer's pure helpers
# ---------------------------------------------------------------------------

@node
def test_one_currency_has_no_rate_line():
    s = {"amount_minor": 6000, "currency": "SGD", "exp_amount_minor": 6000, "exp_currency": "SGD",
         "rate": None, "rate_date": None, "rate_month_ended": None, "date": "2026-10-15"}
    assert rate_line(s) is None


@node
def test_only_foreign_ticked_amounts_are_previewed():
    code = "\n".join([
        LIFTED,
        "const items = [{id: 1, currency: 'SGD'}, {id: 6, currency: 'INR'}, {id: 7, currency: 'INR'}, {id: 8, currency: 'INR'}];",
        "const amounts = new Map([[1, 3000], [6, 9000], [7, null], [8, 0]]);",
        "console.log(JSON.stringify(X.foreignPlacements(items, amounts, 'SGD')));",
    ])
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [{"expectation_id": 6, "amount": "90.00"}]
