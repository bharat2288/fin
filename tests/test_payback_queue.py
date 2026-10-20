"""Personal-ledger 41: the Queue's paybacks (E6 + D6).

The lane read GET /api/queue/paybacks offers, for each money-in row waiting for its
label, one best match with its reasons (or two that tie); ✓ Set off posts Back for…'s
own route; Not a payback writes a dismissal; the refund asks of ticket 38's read are
drawn beside them with a ✓ that writes the hand link.

Seam 1 of the 23 spec for the routes (the Flask test client on a temporary database,
figures in and out in whole minor units, each write one change undone from Changes),
and the D6 client under node (static/expect-queue.js run whole after the shells it
builds on, app.js's money and day helpers lifted out of the real file, the script on
stdin). The client's own requests are posted to the real routes, so what the page
sends is what the server is tested with.

Every name and figure is invented; the book is the case list's October story.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import expect_back
import payback_queue
from expect_fixtures import last_entry, mark, october, row, set_off, wording
from test_expect_contract import load

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


# ---------------------------------------------------------------------------
# The book: the case list's open items, as they stand on 10 Oct
# ---------------------------------------------------------------------------

@pytest.fixture
def book(conn):
    """HARBOUR GRILL S$ 200 back (Jay, Kim); KLOOK all of it (Kim); GOLDEN VILLAGE
    S$ 64 (Jay, Lena); AIRBNB S$ 620 (Lena) with S$ 580 back in July; the loan to
    Ravi, S$ 2,000 with S$ 500 back (converted). Nothing set off in October."""
    o = october(conn)
    b, p = o.book, o.people
    lena_july = row(conn, b, b.savings, "2026-07-20", "PAYNOW FROM LENA 1500", -58000, flow="review")
    ravi_loan = row(conn, b, b.savings, "2026-06-03", "PAYNOW TO RAVI", 200000, flow="movement",
                    other_side=p["Ravi"])
    ravi_back = row(conn, b, b.savings, "2026-08-15", "PAYNOW FROM RAVI", -50000, flow="movement",
                    other_side=p["Ravi"])
    o.ids = {
        "harbour": mark(conn, o.harbour, 20000, names=(p["Jay"], p["Kim"])),
        "klook": mark(conn, o.klook, 18000, names=(p["Kim"],), note="concert tickets"),
        "golden": mark(conn, o.golden, 6400, names=(p["Jay"], p["Lena"])),
        "airbnb": mark(conn, o.airbnb, 62000, names=(p["Lena"],)),
        "ravi": mark(conn, ravi_loan, 200000, names=(p["Ravi"],), source="converted"),
    }
    set_off(conn, lena_july, o.ids["airbnb"], 58000)
    set_off(conn, ravi_back, o.ids["ravi"], 50000, source="converted")
    return o


def paybacks(client) -> dict:
    resp = client.get("/api/queue/paybacks")
    assert resp.status_code == 200, resp.get_json()
    assert "X-Fin-Change" not in resp.headers
    f = resp.get_json()
    check_shape(f)
    return f


def check_shape(f: dict) -> None:
    """The fixture's shape (tests/fixtures/expect/queue_paybacks.json)."""
    fixture = load("queue_paybacks")
    assert set(f) == set(fixture)
    for p in f["paybacks"]:
        assert set(p) == set(fixture["paybacks"][0])
        assert set(p["payment"]) == set(fixture["paybacks"][0]["payment"])
        assert p["payment"]["amount_minor"] < 0 < p["payment"]["left_minor"]
        for s in p["suggestions"]:
            assert set(s) == set(fixture["paybacks"][0]["suggestions"][0])
            assert s["open_minor"] >= p["payment"]["left_minor"]


def for_row(f: dict, row_id: int) -> dict | None:
    found = [p for p in f["paybacks"] if p["payment"]["id"] == row_id]
    assert len(found) <= 1
    return found[0] if found else None


def offered(f: dict, row_id: int) -> list[tuple[str, list[str]]]:
    return [(s["title"], s["reasons"]) for s in for_row(f, row_id)["suggestions"]]


def entries(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0]


def undo(client, entry_id):
    return client.post(f"/api/history/{entry_id}/undo")


# ---------------------------------------------------------------------------
# The client, under node
# ---------------------------------------------------------------------------

def app_part(pattern: str) -> str:
    m = re.search(pattern, APP_JS, re.M | re.S)
    assert m, pattern
    return m.group(0)


def app_function(name: str) -> str:
    return app_part(rf"^function {name}\(.*?^\}}$")


def lifted() -> str:
    return "\n".join([
        "globalThis.window = globalThis;",
        "const esc = s => String(s ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));",
        app_part(r"^const SIGNS = .*?;$"),
        app_part(r"^const DIGITS = .*?;$"),
        app_part(r"^const formatters = .*?;$"),
        app_part(r"^function digitsOf\(.*?\}$"),
        app_function("groupFor"),
        app_function("money"),
        app_part(r"^const MONTHS = .*?;$"),
        app_function("day"),
        (STATIC / "expect-mark.js").read_text(encoding="utf-8"),
        (STATIC / "expect-setoff.js").read_text(encoding="utf-8"),
        (STATIC / "expect-queue.js").read_text(encoding="utf-8"),
        "const Q = window.Expect.queue;",
    ])


def run(expr: str):
    code = lifted() + f"\nconsole.log(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def text(html: str) -> str:
    """What the operator reads: the markup's words, spaces collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace(" ?", "?").strip()


def step(f: dict, asks: list | None = None) -> str:
    return run(f"Q.stepHTML({json.dumps(f)}, {json.dumps(asks or [])})")


# ---------------------------------------------------------------------------
# Case 27: one best match, with its reasons; ✓ Set off writes case 10
# ---------------------------------------------------------------------------

def test_case_27_one_best_match_with_its_reasons(client, book):
    f = paybacks(client)
    p = for_row(f, book.jay_0412)
    assert p["payment"] == {"id": book.jay_0412, "description": "PAYNOW FROM JAY TAN 0412", "date": "2026-10-10",
                            "account_name": "Everyday savings", "amount_minor": -12000, "currency": "SGD",
                            "left_minor": 12000}
    # GOLDEN VILLAGE fits on date and names Jay, but has S$ 64 open, less than S$ 120.
    assert p["suggestions"] == [{
        "expectation_id": book.ids["harbour"], "title": "HARBOUR GRILL", "date": "2026-10-02",
        "open_minor": 20000, "currency": "SGD",
        "reasons": ["Jay is named on it", "8 days after", "S$ 120 ≤ S$ 200 open"]}]


def test_case_27_the_window_reads_the_row_date_not_the_day_it_was_marked(client, conn, book):
    """GOLDEN VILLAGE marked on 19 Oct, after the payment, still qualifies on its row's
    21 Sep: with S$ 64 placed it is offered to a S$ 64 Jay payment."""
    conn.execute("UPDATE expectation SET made_at = '2026-10-19 09:00:00' WHERE id = ?", (book.ids["golden"],))
    conn.commit()
    b = book.book
    jay = row(conn, b, b.savings, "2026-10-11", "PAYNOW FROM JAY TAN 0999", -6400, flow="review")
    assert [t for t, _ in offered(paybacks(client), jay)] == ["GOLDEN VILLAGE"]


@needs_node
def test_case_27_the_step_draws_the_best_match_and_its_actions(client, book):
    f = paybacks(client)
    jay = for_row(f, book.jay_0412)
    words = text(step({"paybacks": [jay], "waiting_offers": []}))
    assert "Back for… 1" in words
    assert "PAYNOW FROM JAY TAN 0412" in words
    assert "Back for HARBOUR GRILL, 2 Oct? S$ 200 open" in words
    assert "Jay is named on it 8 days after S$ 120 ≤ S$ 200 open" in words
    assert "✓ Set off Another… Not a payback" in words


@needs_node
def test_case_27_check_set_off_posts_case_10_through_the_set_off_route(client, conn, book):
    f = paybacks(client)
    jay = for_row(f, book.jay_0412)
    body = run(f"Q.setOffRequest({json.dumps(jay)}, {json.dumps(jay['suggestions'][0])})")
    assert body == {"placements": [{"expectation_id": book.ids["harbour"], "amount": "120.00", "open_minor": 20000}],
                    "waiting": None}
    before = entries(conn)
    resp = client.post(f"/api/transactions/{book.jay_0412}/set-offs", json=body)
    assert resp.status_code == 200, resp.get_json()
    assert entries(conn) == before + 1
    assert expect_back.open_minor(conn, book.ids["harbour"]) == 8000
    assert resp.get_json()["learnt"]["wording"] == "JAY TAN"
    # The payment is wholly placed: it leaves the lane.
    assert for_row(paybacks(client), book.jay_0412) is None
    # One change, undone from Changes: HARBOUR is S$ 200 open again and Jay is back in the lane.
    assert undo(client, int(resp.headers["X-Fin-Change"])).status_code == 200
    assert expect_back.open_minor(conn, book.ids["harbour"]) == 20000
    assert [t for t, _ in offered(paybacks(client), book.jay_0412)] == ["HARBOUR GRILL"]


# ---------------------------------------------------------------------------
# Cases 28-30: ties, an exact amount, nothing fits
# ---------------------------------------------------------------------------

def test_case_28_two_fit_equally_both_offered_oldest_first(client, conn, book):
    b = book.book
    kim = row(conn, b, b.savings, "2026-10-09", "PAYNOW FROM KIM L 7781", -7000, flow="review")
    assert offered(paybacks(client), kim) == [
        ("KLOOK TRAVEL", ["Kim is named on it", "11 days after", "S$ 70 ≤ S$ 180 open"]),
        ("HARBOUR GRILL", ["Kim is named on it", "7 days after", "S$ 70 ≤ S$ 200 open"]),
    ]


@needs_node
def test_case_28_a_tie_draws_a_check_for_each_with_another_and_not_a_payback():
    f = load("queue_paybacks")
    kim = f["paybacks"][1]
    words = text(step({"paybacks": [kim], "waiting_offers": []}))
    assert "Back for one of these? Two fit equally:" in words
    assert "✓ KLOOK TRAVEL, 28 Sep · S$ 180 open" in words
    assert "✓ HARBOUR GRILL, 2 Oct · S$ 200 open" in words
    assert "Another… Not a payback" in words
    two = [run(f"Q.setOffRequest({json.dumps(kim)}, {json.dumps(s)})") for s in kim["suggestions"]]
    assert [b["placements"] for b in two] == [
        [{"expectation_id": 2, "amount": "70.00", "open_minor": 18000}],
        [{"expectation_id": 1, "amount": "70.00", "open_minor": 20000}]]


def test_case_29_an_exact_amount_wins_alone(client, conn, book):
    b = book.book
    lena = row(conn, b, b.savings, "2026-09-24", "PAYNOW FROM LENA 2210", -4000, flow="review")
    assert offered(paybacks(client), lena) == [
        ("AIRBNB", ["Lena is named on it", "88 days after", "exactly the S$ 40 open"])]


def test_case_30_nothing_fits_the_row_is_listed_with_no_suggestion(client, conn, book):
    b = book.book
    inward = row(conn, b, b.savings, "2026-10-07", "INWARD PAYNOW 9921", -4500, flow="review")
    p = for_row(paybacks(client), inward)
    assert p["suggestions"] == [] and p["payment"]["left_minor"] == 4500


@needs_node
def test_case_30_a_plain_row_gets_back_for_beside_this_was():
    f = load("queue_paybacks")
    # The step draws only what has a suggestion; the plain row stays in its own card.
    words = text(step(f))
    assert "INWARD PAYNOW 9921" not in words
    assert run(f"Q.plainIds({json.dumps(f)})") == [207]
    beside = run("Q.backForButton(207)")
    assert 'data-act="back-for"' in beside and 'data-tx="207"' in beside and text(beside) == "Back for…"


# ---------------------------------------------------------------------------
# Case 31: Not a payback
# ---------------------------------------------------------------------------

def test_case_31_not_a_payback_is_one_change_and_never_offered_again(client, conn, book):
    before = entries(conn)
    resp = client.post(f"/api/transactions/{book.jay_0412}/not-a-payback", json={})
    assert resp.status_code == 200, resp.get_json()
    d = resp.get_json()["dismissal"]
    assert set(d) == set(load("not_a_payback")["response"]["dismissal"])
    assert (d["row_id"], d["kind"], d["purchase_row_id"]) == (book.jay_0412, "payback", None)
    assert entries(conn) == before + 1
    summary = conn.execute("SELECT summary FROM change_entries WHERE id = ?",
                           (int(resp.headers["X-Fin-Change"]),)).fetchone()[0]
    assert summary == "Not a payback"
    assert for_row(paybacks(client), book.jay_0412) is None
    # It stays a money-in row to label: its parts are untouched, Back for… by hand still opens.
    assert expect_back.parts(conn, [book.jay_0412])[book.jay_0412]["own_minor"] == -12000
    assert client.get(f"/api/transactions/{book.jay_0412}/back-for").status_code == 200
    # Undone from Changes: offered again.
    assert undo(client, int(resp.headers["X-Fin-Change"])).status_code == 200
    assert [t for t, _ in offered(paybacks(client), book.jay_0412)] == ["HARBOUR GRILL"]


def test_case_31_refusals_write_nothing(client, conn, book):
    client.post(f"/api/transactions/{book.jay_0412}/not-a-payback", json={})
    before = (entries(conn), conn.execute("SELECT COUNT(*) FROM dismissal").fetchone()[0])
    for tx, status, words in [
        (book.jay_0412, 400, "PAYNOW FROM JAY TAN 0412 is already set aside as not a payback"),
        (book.harbour, 400, "only money in is offered as a payback"),
        (book.card_payoff, 400, "only money in is offered as a payback"),
        (999999, 404, "no such row"),
    ]:
        resp = client.post(f"/api/transactions/{tx}/not-a-payback", json={})
        assert resp.status_code == status and resp.get_json() == {"error": words}
        assert "X-Fin-Change" not in resp.headers
    assert (entries(conn), conn.execute("SELECT COUNT(*) FROM dismissal").fetchone()[0]) == before


def test_case_31_a_company_row_cannot_be_set_aside(client, conn, book):
    b = book.book
    moom_in = row(conn, b, b.savings, "2026-10-09", "SAMPLE CLIENT", -5000, flow="review", book_name="Moom")
    resp = client.post(f"/api/transactions/{moom_in}/not-a-payback", json={})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "only Household money can be expected to pay back: this row is the company's"}
    assert for_row(paybacks(client), moom_in) is None


# ---------------------------------------------------------------------------
# Case 32: the wording is learnt; case 33: outside 90 days
# ---------------------------------------------------------------------------

def place(client, payment_id, *placements):
    resp = client.post(f"/api/transactions/{payment_id}/set-offs", json={
        "placements": [{"expectation_id": e, "amount": a} for e, a in placements], "waiting": None})
    assert resp.status_code == 200, resp.get_json()
    return resp


def test_case_32_after_cases_10_and_11_golden_village_alone(client, conn, book):
    b, ids = book.book, book.ids
    jay_0533 = row(conn, b, b.savings, "2026-10-25", "PAYNOW FROM JAY TAN 0533", -4000, flow="review")
    place(client, book.jay_0412, (ids["harbour"], "120.00"))  # case 10
    # After case 10 alone HARBOUR has S$ 80 open and ties with GOLDEN VILLAGE.
    assert [t for t, _ in offered(paybacks(client), jay_0533)] == ["GOLDEN VILLAGE", "HARBOUR GRILL"]
    place(client, book.kim_7781, (ids["harbour"], "60.00"), (ids["klook"], "10.00"))  # case 11
    assert offered(paybacks(client), jay_0533) == [
        ("GOLDEN VILLAGE", ["Jay is named on it", "34 days after", "S$ 40 ≤ S$ 64 open"])]


def test_case_32_a_learnt_wording_names_the_sender(client, conn, book):
    b, p = book.book, book.people
    wording(conn, "MR J TAN", p["Jay"])
    mr_tan = row(conn, b, b.savings, "2026-10-11", "PAYNOW FROM MR J TAN 0001", -12000, flow="review")
    assert offered(paybacks(client), mr_tan) == [
        ("HARBOUR GRILL", ["Jay is named on it", "9 days after", "S$ 120 ≤ S$ 200 open"])]


def test_case_33_outside_90_days_not_offered_but_back_for_lists_it(client, conn, book):
    b = book.book
    late = row(conn, b, b.savings, "2027-01-30", "PAYNOW FROM JAY TAN 0601", -4000, flow="review")
    assert for_row(paybacks(client), late)["suggestions"] == []
    listed = client.get(f"/api/transactions/{late}/back-for").get_json()["items"]
    assert book.ids["golden"] in [e["id"] for e in listed]


def test_the_window_edge_90_days_offered_91_not(client, conn, book):
    b = book.book
    on_90 = row(conn, b, b.savings, "2026-12-20", "PAYNOW FROM JAY TAN 0700", -6400, flow="review")   # 90 d after 21 Sep
    on_91 = row(conn, b, b.savings, "2026-12-21", "PAYNOW FROM JAY TAN 0701", -6400, flow="review")
    before = row(conn, b, b.savings, "2026-09-20", "PAYNOW FROM JAY TAN 0702", -6400, flow="review")  # the day before
    f = paybacks(client)
    # S$ 64 is exactly GOLDEN VILLAGE's open amount, so on day 90 it is offered alone;
    # a day later it is out of the window and HARBOUR GRILL (2 Oct, 80 days) is all that fits.
    assert [t for t, _ in offered(f, on_90)] == ["GOLDEN VILLAGE"]
    assert [t for t, _ in offered(f, on_91)] == ["HARBOUR GRILL"]
    # A payment before every item it names: nothing.
    assert offered(f, before) == []


def test_another_currency_is_not_suggested(client, conn, book):
    b, p = book.book, book.people
    mark(conn, book.swiggy, 180000, names=(p["Jay"],))
    jay = row(conn, b, b.savings, "2026-10-08", "PAYNOW FROM JAY TAN 0800", -1000, flow="review")
    # SWIGGY names Jay and fits on date and (in rupees) on amount: another currency, never suggested.
    assert [t for t, _ in offered(paybacks(client), jay)] == ["GOLDEN VILLAGE", "HARBOUR GRILL"]


# ---------------------------------------------------------------------------
# Case 33a: an item with no names
# ---------------------------------------------------------------------------

@pytest.fixture
def unnamed(conn):
    o = october(conn)
    p = o.people
    o.ids = {
        "harbour": mark(conn, o.harbour, 20000),
        "klook": mark(conn, o.klook, 18000, names=(p["Kim"],)),
        "golden": mark(conn, o.golden, 6400, names=(p["Jay"], p["Lena"])),
    }
    return o


def test_case_33a_an_unnamed_item_offered_on_amount_and_date_alone(client, conn, unnamed):
    b = unnamed.book
    inward = row(conn, b, b.savings, "2026-10-10", "INWARD PAYNOW 9921", -12000, flow="review")
    assert offered(paybacks(client), inward) == [
        ("HARBOUR GRILL", ["8 days after", "S$ 120 ≤ S$ 200 open", "no names on it"])]
    small = row(conn, b, b.savings, "2026-10-10", "INWARD PAYNOW 9922", -5000, flow="review")
    # GOLDEN VILLAGE (Jay, Lena) would fit on amount and date: its names do not match, so never.
    assert [t for t, _ in offered(paybacks(client), small)] == ["HARBOUR GRILL"]


def test_case_33a_a_named_fit_outranks_every_unnamed_item(client, conn, unnamed):
    b = unnamed.book
    kim = row(conn, b, b.savings, "2026-10-09", "PAYNOW FROM KIM L 7781", -7000, flow="review")
    assert [t for t, _ in offered(paybacks(client), kim)] == ["KLOOK TRAVEL"]


@needs_node
def test_case_33a_the_step_draws_the_reasons_as_given(client, conn, unnamed):
    b = unnamed.book
    inward = row(conn, b, b.savings, "2026-10-10", "INWARD PAYNOW 9921", -12000, flow="review")
    one = for_row(paybacks(client), inward)
    words = text(step({"paybacks": [one], "waiting_offers": []}))
    assert "Back for HARBOUR GRILL, 2 Oct? S$ 200 open" in words
    assert "8 days after S$ 120 ≤ S$ 200 open no names on it" in words


# ---------------------------------------------------------------------------
# What the lane leaves out
# ---------------------------------------------------------------------------

def test_rows_already_labelled_or_wholly_placed_are_not_in_the_lane(client, conn, book):
    b = book.book
    salary = row(conn, b, b.savings, "2026-10-09", "SALARY", -500000, flow="income")
    f = paybacks(client)
    ids = [p["payment"]["id"] for p in f["paybacks"]]
    assert salary not in ids                       # labelled: not waiting in the Queue
    assert book.harbour not in ids                 # money out
    assert book.jay_0412 in ids and book.kim_7781 in ids and book.lena_2210 in ids
    # Newest payment first.
    dates = [p["payment"]["date"] for p in f["paybacks"]]
    assert dates == sorted(dates, reverse=True)


def test_a_partly_placed_payment_is_matched_on_what_it_has_left(client, conn, book):
    """Case 13's PayNow, S$ 40 of its S$ 60 on AIRBNB: S$ 20 left, matched on that."""
    place(client, book.lena_2210, (book.ids["airbnb"], "40.00"))
    p = for_row(paybacks(client), book.lena_2210)
    assert p["payment"]["left_minor"] == 2000
    assert [(t, r[-1]) for t, r in offered(paybacks(client), book.lena_2210)] == [
        ("GOLDEN VILLAGE", "S$ 20 ≤ S$ 64 open")]


def test_an_empty_book_has_an_empty_lane(client):
    assert paybacks(client) == {"paybacks": [], "waiting_offers": []}


# ---------------------------------------------------------------------------
# Case 35 (Queue half) and 37a (draw half): the refund asks
# ---------------------------------------------------------------------------

def test_case_35_check_writes_the_hand_link_as_one_change(client, conn):
    o = october(conn)
    b = o.book
    first = row(conn, b, b.rewards_card, "2026-09-20", "AMAZON.SG", 14000, type_name="Home")
    second = row(conn, b, b.rewards_card, "2026-09-24", "AMAZON.SG", 9000, type_name="Home")
    refund = row(conn, b, b.rewards_card, "2026-10-08", "AMAZON.SG REFUND", -9000, flow="refund")
    asks = client.get("/api/refund-links/asks").get_json()["asks"]
    assert [(a["refund"]["id"], [p["id"] for p in a["purchases"]]) for a in asks] == [(refund, [first, second])]
    if NODE:
        words = text(step({"paybacks": [], "waiting_offers": []}, asks))
        assert "Refund of which Amazon order?" in words
        assert "✓ 20 Sep · S$ 140" in words and "✓ 24 Sep · S$ 90" in words
        body = run(f"Q.handLinkRequest({json.dumps(asks[0])}, {json.dumps(asks[0]['purchases'][1])})")
    else:
        body = {"refund_row_id": refund, "purchase_row_id": second}
    assert body == {"refund_row_id": refund, "purchase_row_id": second}
    before = entries(conn)
    resp = client.post("/api/refund-links", json=body)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["refund_link"]["source"] == "hand"
    assert entries(conn) == before + 1
    assert client.get("/api/refund-links/asks").get_json()["asks"] == []
    assert undo(client, int(resp.headers["X-Fin-Change"])).status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM refund_link").fetchone()[0] == 0


@needs_node
def test_case_35_the_ask_is_drawn_from_the_fixture_shape():
    f = load("refund_ask_which")
    words = text(step({"paybacks": [], "waiting_offers": []}, f["asks"]))
    assert "AMAZON.SG REFUND" in words and "Refund of which Amazon order?" in words
    assert run(f"Q.handLinkRequest({json.dumps(f['asks'][0])}, {json.dumps(f['asks'][0]['purchases'][1])})") \
        == f["hand_link_request"]


@needs_node
def test_case_37a_keep_it_or_to_pay_back_is_drawn_from_the_fixture_shape():
    f = load("refund_ask_paid_back")
    html = step({"paybacks": [], "waiting_offers": []}, f["asks"])
    words = text(html)
    assert "KLOOK TRAVEL REFUND" in words
    assert "Refund of a purchase already paid back: keep it, or To pay back?" in words
    assert "Keep it" in words and "To pay back" in words
    assert 'data-link="42"' in html
    assert run(f"Q.answerRequest('pay_back')") == f["answer_request"]


@needs_node
def test_the_whole_fixture_draws_four_cards_in_the_step():
    f = load("queue_paybacks")
    asks = load("refund_ask_which")["asks"]
    words = text(step(f, asks))
    # Jay's, Kim's, Lena's waiting payment (personal-ledger 42) and the Amazon refund; the plain
    # row is not counted.
    assert "Back for… 4" in words


@needs_node
def test_an_empty_lane_draws_nothing():
    assert step({"paybacks": [], "waiting_offers": []}) == ""
    assert step(load("queue_paybacks") | {"paybacks": [load("queue_paybacks")["paybacks"][2]],
                                          "waiting_offers": []}) == ""


# ---------------------------------------------------------------------------
# Case 55 (lane half): one read for the lane, whatever its size
# ---------------------------------------------------------------------------

def reads(conn) -> int:
    seen = []
    conn.set_trace_callback(lambda sql: seen.append(sql) if sql.lstrip().upper().startswith(("SELECT", "WITH")) else None)
    try:
        payback_queue.paybacks(conn)
    finally:
        conn.set_trace_callback(None)
    return len(seen)


def test_case_55_the_lane_is_one_read_whatever_its_size(conn, book):
    small = reads(conn)
    b, p = book.book, book.people
    for i in range(30):
        spent = row(conn, b, b.everyday_card, f"2026-09-{1 + i % 28:02d}", f"DINNER {i}", 10000 + i, type_name="Dining")
        mark(conn, spent, 5000, names=(p["Jay"], p["Kim"]) if i % 2 else ())
        row(conn, b, b.savings, f"2026-10-{1 + i % 28:02d}", f"PAYNOW FROM JAY TAN {i:04d}", -1000 - i, flow="review")
    assert reads(conn) == small <= 5   # personal-ledger 42 adds the waiting offers' one read
