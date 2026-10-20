"""Personal-ledger 36: Back for… (E2 core, D2's routes): set off one payment, in one
currency, against open expectations, and Unlink.

Seam 1 of the 23 spec: the HTTP routes through the Flask test client on a temporary
database, figures in and out in whole minor units, refusals that write nothing (no
change entry, the records unchanged), each write one change that undo from Changes
takes back. The answers are checked against the route shapes ticket 26 pinned
(tests/fixtures/expect/), through the contract test's own checkers.

Every name and figure is invented; the book is the case list's October story.
"""

from __future__ import annotations

import threading

import pytest

import expect_back
from expect_fixtures import last_entry, let_go, mark, october, row, set_off, wording
from test_expect_contract import EXP_BRIEF, SET_OFF, check_expectation, check_parts

HARBOUR_OPEN_WORDS = "HARBOUR GRILL has S$ 0.00 open now"


# ---------------------------------------------------------------------------
# The book: the case list's open items, as they stand on 10 Oct
# ---------------------------------------------------------------------------

@pytest.fixture
def book(conn):
    """HARBOUR GRILL S$ 200 back (Jay, Kim); KLOOK all of it (Kim); GOLDEN VILLAGE
    S$ 64 (Jay, Lena); AIRBNB S$ 620 (Lena) with S$ 580 back before October; the
    loan to Ravi, S$ 2,000 with S$ 500 back (converted). Nothing yet set off in
    October."""
    o = october(conn)
    b, p = o.book, o.people
    lena_july = row(conn, b, b.savings, "2026-07-20", "PAYNOW FROM LENA 1500", -58000, flow="review")
    ravi_loan = row(conn, b, b.savings, "2026-06-03", "PAYNOW TO RAVI", 200000, flow="movement",
                    other_side=p["Ravi"])
    ravi_back = row(conn, b, b.savings, "2026-08-15", "PAYNOW FROM RAVI", -50000, flow="movement",
                    other_side=p["Ravi"])
    ids = {
        "harbour": mark(conn, o.harbour, 20000, names=(p["Jay"], p["Kim"])),
        "klook": mark(conn, o.klook, 18000, names=(p["Kim"],), note="concert tickets"),
        "golden": mark(conn, o.golden, 6400, names=(p["Jay"], p["Lena"])),
        "airbnb": mark(conn, o.airbnb, 62000, names=(p["Lena"],)),
        "ravi": mark(conn, ravi_loan, 200000, names=(p["Ravi"],), source="converted"),
    }
    set_off(conn, lena_july, ids["airbnb"], 58000)
    set_off(conn, ravi_back, ids["ravi"], 50000, source="converted")
    o.ids = ids
    return o


def cases_10_and_11(conn, o) -> dict:
    """Cases 10 and 11 written as they stand after the drawer: Jay's S$ 120 on
    HARBOUR; Kim's S$ 70 as S$ 60 on HARBOUR and S$ 10 on KLOOK. HARBOUR has
    S$ 20 open, KLOOK S$ 170."""
    harbour, klook = o.ids["harbour"], o.ids["klook"]
    jay_on_harbour = set_off(conn, o.jay_0412, harbour, 12000)
    kim_on_harbour = set_off(conn, o.kim_7781, harbour, 6000)
    kim_on_klook = set_off(conn, o.kim_7781, klook, 1000)
    return {"harbour": harbour, "klook": klook, "jay_on_harbour": jay_on_harbour,
            "kim_on_harbour": kim_on_harbour, "kim_on_klook": kim_on_klook}


def snapshot(conn) -> dict:
    """What a refused request must leave as it was."""
    return {
        "set_off": conn.execute("SELECT id, payment_row_id, expectation_id, amount_minor FROM set_off ORDER BY id").fetchall(),
        "sender_wording": conn.execute("SELECT id, wording, person_id FROM sender_wording ORDER BY id").fetchall(),
        "entries": conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0],
    }


def back_for(client, payment_id):
    return client.get(f"/api/transactions/{payment_id}/back-for")


def place(client, payment_id, *placements, waiting=None):
    """POST one Back for… request: each placement (expectation id, amount text[, extra keys])."""
    body = []
    for e, amount, *extra in placements:
        body.append({"expectation_id": e, "amount": amount, **(extra[0] if extra else {})})
    return client.post(f"/api/transactions/{payment_id}/set-offs", json={"placements": body, "waiting": waiting})


def refused(client, conn, resp, words: str, before: dict):
    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json() == {"error": words}
    assert "X-Fin-Change" not in resp.headers
    assert snapshot(conn) == before


def open_of(conn, expectation_id) -> int:
    return expect_back.open_minor(conn, expectation_id)


def undo(client, entry_id):
    return client.post(f"/api/history/{entry_id}/undo")


# ---------------------------------------------------------------------------
# The read: what Back for… lists
# ---------------------------------------------------------------------------

def test_case_10_back_for_lists_what_is_open_nearest_the_payment_first(client, conn, book):
    resp = back_for(client, book.jay_0412)
    assert resp.status_code == 200
    f = resp.get_json()
    assert set(f) == {"direction", "near", "payment", "items"}
    assert f["direction"] == "back" and f["near"] == "2026-10-10"
    p = f["payment"]
    check_parts(p)
    assert (p["amount_minor"], p["own_minor"], p["left_minor"]) == (-12000, -12000, 12000)
    assert p["description"] == "PAYNOW FROM JAY TAN 0412" and p["date"] == "2026-10-10"
    assert p["sender"] == {"wording": "JAY TAN", "person": None}
    for e in f["items"]:
        check_expectation(e)
    assert [e["id"] for e in f["items"]] == [book.ids[k] for k in ("harbour", "klook", "golden", "airbnb", "ravi")]
    assert [(e["open_minor"], e["amount_minor"]) for e in f["items"]] == [
        (20000, 20000), (18000, 18000), (6400, 6400), (4000, 62000), (150000, 200000)]
    assert [n["name"] for n in f["items"][0]["names"]] == ["Jay", "Kim"]
    assert f["items"][0]["row"]["display_type"] == "Dining" and f["items"][0]["row"]["book"] == "Household"
    assert f["items"][4]["source"] == "converted"


def test_a_read_writes_nothing(client, conn, book):
    before = snapshot(conn)
    assert back_for(client, book.jay_0412).status_code == 200
    assert snapshot(conn) == before


def test_money_out_lists_what_is_to_pay_back(client, conn, book):
    b, parents = book.book, book.people["Parents"]
    gift_in = row(conn, b, b.savings, "2026-03-01", "FROM PARENTS", -500000, flow="review")
    owed = mark(conn, gift_in, 500000, names=(parents,))
    paid = row(conn, b, b.savings, "2026-08-01", "TRANSFER TO PARENTS", 100000, flow="movement", other_side=parents)
    f = back_for(client, paid).get_json()
    assert f["direction"] == "pay"
    assert [e["id"] for e in f["items"]] == [owed]
    check_expectation(f["items"][0])


def test_the_read_refuses_a_row_that_cannot_pay_anything(client, conn, book):
    resp = back_for(client, book.card_payoff)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "a move between your own accounts"}
    assert back_for(client, 999999).status_code == 404


# ---------------------------------------------------------------------------
# Set off: one request, every placement or none, one change
# ---------------------------------------------------------------------------

def test_case_10_one_payment_one_item_learns_the_sender(client, conn, book):
    resp = place(client, book.jay_0412, (book.ids["harbour"], "120.00"))
    assert resp.status_code == 200, resp.get_json()
    change = int(resp.headers["X-Fin-Change"])
    out = resp.get_json()
    assert set(out) == {"payment", "set_offs", "expectations", "learnt"}
    check_parts(out["payment"])
    assert (out["payment"]["placed_minor"], out["payment"]["own_minor"], out["payment"]["left_minor"]) == (-12000, 0, 0)
    s, = out["set_offs"]
    assert set(s) == SET_OFF
    assert (s["payment_row_id"], s["expectation_id"], s["amount_minor"], s["exp_amount_minor"]) == (
        book.jay_0412, book.ids["harbour"], 12000, 12000)
    assert (s["currency"], s["exp_currency"], s["rate"], s["rate_date"], s["rate_month_ended"]) == ("SGD", "SGD", None, None, None)
    assert (s["date"], s["source"], s["state"], s["person"]) == ("2026-10-10", "hand", "placed", None)
    e, = out["expectations"]
    assert set(e) == EXP_BRIEF
    assert (e["id"], e["open_minor"], e["state"]) == (book.ids["harbour"], 8000, "partly_back")
    assert out["learnt"] == {"wording": "JAY TAN", "person": {"account_id": book.people["Jay"], "name": "Jay"}}
    assert open_of(conn, book.ids["harbour"]) == 8000
    # one change, holding the set-off and the wording, named in Changes
    tables = sorted(c["table"] for c in client.get(f"/api/history/{change}").get_json()["changes"])
    assert tables == ["sender_wording", "set_off"]
    assert client.get(f"/api/history/{change}").get_json()["summary"] == "Set off a payment"
    # the next read knows the sender, and the payment has nothing left
    again = back_for(client, book.jay_0412).get_json()
    assert again["payment"]["left_minor"] == 0
    assert again["payment"]["sender"]["person"] == {"account_id": book.people["Jay"], "name": "Jay"}
    # undo from Changes takes both back
    assert undo(client, change).status_code == 200
    assert open_of(conn, book.ids["harbour"]) == 20000
    assert conn.execute("SELECT COUNT(*) FROM sender_wording").fetchone()[0] == 0


def test_case_11_one_payment_two_items_two_set_offs_one_change(client, conn, book):
    place(client, book.jay_0412, (book.ids["harbour"], "120.00"))
    entries = conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0]
    resp = place(client, book.kim_7781, (book.ids["harbour"], "60.00"), (book.ids["klook"], "10.00"))
    assert resp.status_code == 200, resp.get_json()
    out = resp.get_json()
    assert [s["amount_minor"] for s in out["set_offs"]] == [6000, 1000]
    assert {e["id"]: e["open_minor"] for e in out["expectations"]} == {book.ids["harbour"]: 2000, book.ids["klook"]: 17000}
    assert out["learnt"] == {"wording": "KIM L", "person": {"account_id": book.people["Kim"], "name": "Kim"}}
    assert conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0] == entries + 1
    change = int(resp.headers["X-Fin-Change"])
    assert undo(client, change).status_code == 200
    assert (open_of(conn, book.ids["harbour"]), open_of(conn, book.ids["klook"])) == (8000, 18000)


def test_case_13_over_payment_the_rest_stays_on_the_payment(client, conn, book):
    resp = place(client, book.lena_2210, (book.ids["airbnb"], "40.00"))
    assert resp.status_code == 200, resp.get_json()
    out = resp.get_json()
    assert (out["payment"]["own_minor"], out["payment"]["left_minor"]) == (-2000, 2000)
    assert out["expectations"] == [{"id": book.ids["airbnb"], "amount_minor": 62000, "currency": "SGD",
                                    "open_minor": 0, "state": "closed"}]
    # AIRBNB, closed, is no longer offered; the payment still has S$ 20 to place or label
    f = back_for(client, book.lena_2210).get_json()
    assert book.ids["airbnb"] not in [e["id"] for e in f["items"]]
    assert f["payment"]["left_minor"] == 2000


def test_case_14_more_than_the_payment_refuses_the_whole_request(client, conn, book):
    before = snapshot(conn)
    resp = place(client, book.jay_0412, (book.ids["harbour"], "120.00"), (book.ids["golden"], "64.00"))
    refused(client, conn, resp, "S$ 184 of S$ 120 placed · S$ 64 more than the payment", before)


def test_case_15_more_than_is_open_is_refused(client, conn, book):
    before = snapshot(conn)
    resp = place(client, book.lena_2210, (book.ids["airbnb"], "60.00"))
    refused(client, conn, resp, "AIRBNB has S$ 40.00 open", before)


def test_case_16_a_closed_item_is_not_listed_and_is_refused(client, conn, book):
    b = book.book
    kim_sep = row(conn, b, b.savings, "2026-09-30", "PAYNOW FROM KIM L 7700", -18000, flow="review")
    set_off(conn, kim_sep, book.ids["klook"], 18000)
    assert book.ids["klook"] not in [e["id"] for e in back_for(client, book.jay_0412).get_json()["items"]]
    before = snapshot(conn)
    refused(client, conn, place(client, book.jay_0412, (book.ids["klook"], "10.00")),
            "nothing open on KLOOK TRAVEL", before)


def test_case_16_a_payment_wholly_placed_has_nothing_left(client, conn, book):
    assert place(client, book.jay_0412, (book.ids["harbour"], "120.00")).status_code == 200
    assert back_for(client, book.jay_0412).get_json()["payment"]["left_minor"] == 0
    before = snapshot(conn)
    refused(client, conn, place(client, book.jay_0412, (book.ids["golden"], "10.00")),
            "S$ 0 left on this payment", before)


def test_case_18_money_out_against_to_pay_back(client, conn, book):
    b, parents = book.book, book.people["Parents"]
    gift_in = row(conn, b, b.savings, "2026-03-01", "FROM PARENTS", -500000, flow="review")
    owed = mark(conn, gift_in, 500000, names=(parents,))
    paid = row(conn, b, b.savings, "2026-08-01", "TRANSFER TO PARENTS", 100000, flow="movement", other_side=parents)
    resp = place(client, paid, (owed, "1,000.00"))
    assert resp.status_code == 200, resp.get_json()
    out = resp.get_json()
    check_parts(out["payment"])
    assert (out["payment"]["placed_minor"], out["payment"]["own_minor"]) == (100000, 0)
    assert out["set_offs"][0]["date"] == "2026-08-01" and out["set_offs"][0]["amount_minor"] == 100000
    assert out["expectations"][0]["open_minor"] == 400000
    assert open_of(conn, owed) == 400000


def test_case_23_unlink_returns_the_payment_part_one_change_undo_relinks(client, conn, book):
    held = cases_10_and_11(conn, book)
    resp = client.delete(f"/api/set-offs/{held['kim_on_harbour']}")
    assert resp.status_code == 200, resp.get_json()
    out = resp.get_json()
    assert set(out) == {"removed", "payment", "expectation"}
    assert out["removed"] == {"set_off_id": held["kim_on_harbour"], "expectation_id": held["harbour"],
                              "payment_row_id": book.kim_7781}
    check_parts(out["payment"])
    assert (out["payment"]["placed_minor"], out["payment"]["own_minor"], out["payment"]["left_minor"]) == (-1000, -6000, 6000)
    assert set(out["expectation"]) == EXP_BRIEF
    assert (out["expectation"]["open_minor"], out["expectation"]["state"]) == (8000, "partly_back")
    change = int(resp.headers["X-Fin-Change"])
    assert [c["table"] for c in client.get(f"/api/history/{change}").get_json()["changes"]] == ["set_off"]
    assert client.get(f"/api/history/{change}").get_json()["summary"] == "Unlinked a payment"
    assert undo(client, change).status_code == 200
    assert open_of(conn, held["harbour"]) == 2000


def test_unlink_of_nothing_is_not_found(client, conn, book):
    before = snapshot(conn)
    resp = client.delete("/api/set-offs/999999")
    assert resp.status_code == 404 and resp.get_json() == {"error": "no such set-off"}
    assert snapshot(conn) == before


def test_case_26_a_let_go_item_is_not_offered_and_a_set_off_on_it_is_refused(client, conn, book):
    held = cases_10_and_11(conn, book)
    let_go(conn, held["harbour"], "gift", "2026-10-19", to_whom_id=book.people["Kim"])
    b = book.book
    jay_late = row(conn, b, b.savings, "2026-10-25", "PAYNOW FROM JAY TAN 0533", -4000, flow="review")
    assert held["harbour"] not in [e["id"] for e in back_for(client, jay_late).get_json()["items"]]
    before = snapshot(conn)
    refused(client, conn, place(client, jay_late, (held["harbour"], "20.00")), expect_back.LET_GO_SINCE, before)


def test_case_26b_unlink_on_a_let_go_item_is_refused(client, conn, book):
    held = cases_10_and_11(conn, book)
    let_go(conn, held["harbour"], "gift", "2026-10-19", to_whom_id=book.people["Kim"])
    before = snapshot(conn)
    refused(client, conn, client.delete(f"/api/set-offs/{held['jay_on_harbour']}"), expect_back.LET_GO_SINCE, before)


# ---------------------------------------------------------------------------
# Case 32: a wording is learnt only when exactly one ticked name matches
# ---------------------------------------------------------------------------

def test_case_32_two_matching_names_teach_nothing(client, conn, book):
    b = book.book
    both = row(conn, b, b.savings, "2026-10-11", "PAYNOW FROM JAY KIM 3030", -5000, flow="review")
    out = place(client, both, (book.ids["harbour"], "50.00")).get_json()
    assert out["learnt"] is None
    assert conn.execute("SELECT COUNT(*) FROM sender_wording").fetchone()[0] == 0


def test_case_32_no_matching_name_teaches_nothing(client, conn, book):
    b = book.book
    inward = row(conn, b, b.savings, "2026-10-10", "INWARD PAYNOW 9921", -4500, flow="review")
    assert place(client, inward, (book.ids["harbour"], "45.00")).get_json()["learnt"] is None
    lena = row(conn, b, b.savings, "2026-10-13", "PAYNOW FROM LENA 2211", -1000, flow="review")
    # Lena is not named on HARBOUR: her name in the text teaches nothing
    assert place(client, lena, (book.ids["harbour"], "10.00")).get_json()["learnt"] is None
    assert conn.execute("SELECT COUNT(*) FROM sender_wording").fetchone()[0] == 0


def test_case_32_a_wording_already_learnt_is_not_learnt_again(client, conn, book):
    wording(conn, "JAY TAN", book.people["Jay"])
    b = book.book
    again = row(conn, b, b.savings, "2026-10-25", "PAYNOW FROM JAY TAN 0533", -4000, flow="review")
    out = place(client, again, (book.ids["golden"], "40.00")).get_json()
    assert out["learnt"] is None
    assert [tuple(r) for r in conn.execute("SELECT wording, person_id FROM sender_wording")] == [("JAY TAN", book.people["Jay"])]


def test_case_32_one_name_among_several_ticked_items_teaches(client, conn, book):
    # HARBOUR (Jay, Kim) and GOLDEN (Jay, Lena): the text says JAY, so Jay alone matches
    out = place(client, book.jay_0412, (book.ids["harbour"], "100.00"), (book.ids["golden"], "20.00")).get_json()
    assert out["learnt"] == {"wording": "JAY TAN", "person": {"account_id": book.people["Jay"], "name": "Jay"}}


# ---------------------------------------------------------------------------
# Failure shapes: two at once, a bad second placement, a bad request
# ---------------------------------------------------------------------------

def test_case_52_two_requests_at_once_one_writes_the_other_is_refused(temp_db, conn, book):
    import app as fin_app

    held = cases_10_and_11(conn, book)
    b = book.book
    kim_new = row(conn, b, b.savings, "2026-10-20", "PAYNOW FROM KIM L 7790", -2000, flow="review")
    jay_new = row(conn, b, b.savings, "2026-10-20", "PAYNOW FROM JAY TAN 0600", -2000, flow="review")
    before_entries = conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0]
    gate = threading.Barrier(2)
    answers = {}

    def tab(name, payment):
        with fin_app.app.test_client() as c:
            gate.wait()
            # each tab's drawer read S$ 20.00 open on HARBOUR
            answers[name] = place(c, payment, (held["harbour"], "20.00", {"open_minor": 2000}))

    tabs = [threading.Thread(target=tab, args=("a", kim_new)), threading.Thread(target=tab, args=("b", jay_new))]
    for t in tabs:
        t.start()
    for t in tabs:
        t.join(30)
    codes = sorted(r.status_code for r in answers.values())
    assert codes == [200, 400]
    loser = next(r for r in answers.values() if r.status_code == 400)
    assert loser.get_json() == {"error": HARBOUR_OPEN_WORDS}
    assert open_of(conn, held["harbour"]) == 0
    assert conn.execute("SELECT COUNT(*) FROM set_off WHERE expectation_id = ?", (held["harbour"],)).fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0] == before_entries + 1


def test_without_what_the_drawer_saw_the_machine_words_stand(client, conn, book):
    held = cases_10_and_11(conn, book)
    b = book.book
    jay_new = row(conn, b, b.savings, "2026-10-20", "PAYNOW FROM JAY TAN 0600", -3000, flow="review")
    before = snapshot(conn)
    refused(client, conn, place(client, jay_new, (held["harbour"], "30.00", {"open_minor": 3000})),
            "HARBOUR GRILL has S$ 20.00 open now", before)
    refused(client, conn, place(client, jay_new, (held["harbour"], "30.00")), "HARBOUR GRILL has S$ 20.00 open", before)


def test_case_53_a_second_placement_invalid_writes_neither(client, conn, book):
    before = snapshot(conn)
    resp = place(client, book.lena_2210, (book.ids["golden"], "10.00"), (book.ids["airbnb"], "50.00"))
    refused(client, conn, resp, "AIRBNB has S$ 40.00 open", before)
    assert open_of(conn, book.ids["golden"]) == 6400


# Across currencies: tests/test_expect_setoff_fx.py (ticket 39).


@pytest.mark.parametrize("body, words", [
    ({}, "tick at least one item to set this payment off against"),
    ({"placements": []}, "tick at least one item to set this payment off against"),
    ({"placements": [{"expectation_id": "x", "amount": "1.00"}]}, "each placement names an expectation and an amount"),
    ({"placements": [{"expectation_id": 1}]}, "each placement names an expectation and an amount"),
])
def test_a_malformed_request_is_refused_and_writes_nothing(client, conn, book, body, words):
    before = snapshot(conn)
    refused(client, conn, client.post(f"/api/transactions/{book.jay_0412}/set-offs", json=body), words, before)


@pytest.mark.parametrize("amount, words", [
    ("120.005", "120.005 is not an amount in whole cents"),
    ("lots", "lots is not an amount in whole cents"),
    ("0", "the amount must be more than nothing"),
    ("-5.00", "the amount must be more than nothing"),
])
def test_an_amount_that_is_not_whole_cents_or_more_than_nothing_is_refused(client, conn, book, amount, words):
    before = snapshot(conn)
    refused(client, conn, place(client, book.jay_0412, (book.ids["harbour"], amount)), words, before)


def test_one_item_twice_in_a_request_is_refused(client, conn, book):
    before = snapshot(conn)
    refused(client, conn, place(client, book.jay_0412, (book.ids["harbour"], "10.00"), (book.ids["harbour"], "10.00")),
            "HARBOUR GRILL is ticked twice", before)


def test_a_waiting_part_rides_the_same_request(client, conn, book):
    """Ticket 40 opened the waiting part (tests/test_expect_waiting.py holds its cases)."""
    resp = place(client, book.lena_2210, (book.ids["airbnb"], "40.00"),
                 waiting={"amount": "20.00", "person_id": book.people["Lena"]})
    assert resp.status_code == 200, resp.get_json()
    out = resp.get_json()
    assert (out["waiting"]["state"], out["waiting"]["amount_minor"]) == ("waiting", 2000)
    assert out["payment"]["left_minor"] == 0


def test_a_payment_the_wrong_way_round_is_refused(client, conn, book):
    # money in placed against a To pay back item (money in): the payment must be money out
    b = book.book
    gift_in = row(conn, b, b.savings, "2026-03-01", "FROM PARENTS", -500000, flow="review")
    owed = mark(conn, gift_in, 500000, names=(book.people["Parents"],))
    before = snapshot(conn)
    refused(client, conn, place(client, book.jay_0412, (owed, "10.00")), "a payment for FROM PARENTS is money out", before)


def test_an_unknown_payment_or_item_is_refused(client, conn, book):
    before = snapshot(conn)
    resp = place(client, 999999, (book.ids["harbour"], "10.00"))
    assert resp.status_code == 404 and resp.get_json() == {"error": "no such row"}
    refused(client, conn, place(client, book.jay_0412, (999999, "10.00")), "no such expectation", before)


def test_a_set_off_written_through_the_route_is_undone_with_its_entry_only(client, conn, book):
    """The fixture's entry() and the route's entry are separate changes: undoing the
    route's leaves the fixture's marks standing."""
    resp = place(client, book.jay_0412, (book.ids["golden"], "64.00"), (book.ids["harbour"], "56.00"))
    assert resp.status_code == 200
    change = int(resp.headers["X-Fin-Change"])
    assert change == last_entry(conn)
    assert undo(client, change).status_code == 200
    assert (open_of(conn, book.ids["golden"]), open_of(conn, book.ids["harbour"])) == (6400, 20000)
    assert conn.execute("SELECT COUNT(*) FROM expectation").fetchone()[0] == 5
