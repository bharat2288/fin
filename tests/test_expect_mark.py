"""Expect some back… / Expect to pay back (personal-ledger 34): mark a row,
edit the mark, undo the mark; and the drawer's pure helpers (D1).

Seam 1 of spec 23: the HTTP routes through the Flask test client on a
temporary database. Figures in and out: a request, a payload, a refusal's
words and an unchanged database; each write is one change, and undo from
Changes takes it back. The drawer's helpers run under node (seam 5), the
script on stdin. Every name and figure is invented (the spec's case list).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import expect_back
from expect_fixtures import (
    entry, harbour_set_against, last_entry, let_go, mark, october, row, set_off,
)

SEVEN = ("expectation", "expectation_name", "set_off", "let_go", "refund_link", "sender_wording", "dismissal")
STATIC = Path(__file__).resolve().parent.parent / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


@pytest.fixture
def story(conn):
    return october(conn)


def snapshot(conn) -> dict:
    """Every record, every row, every account and the history."""
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")]
            for t in SEVEN + ("transactions", "accounts", "change_entries", "change_rows")}


def mark_route(client, tx_id, **body):
    full = {"direction": "back", "all": False, "amount": None, "names": [], "new_names": [], "note": None}
    full.update(body)
    return client.post(f"/api/transactions/{tx_id}/expectation", json=full)


def undo_mark_route(client, expectation_id):
    return client.delete(f"/api/expectations/{expectation_id}")


def undo(client, entry_id):
    return client.post(f"/api/history/{entry_id}/undo")


def refused(client, conn, words, call):
    """The call is refused with these words and writes nothing at all."""
    before = snapshot(conn)
    resp = call()
    assert 400 <= resp.status_code < 500, resp.get_json()
    assert resp.get_json() == {"error": words}
    assert "X-Fin-Change" not in resp.headers
    assert snapshot(conn) == before


def expectation_of(conn, row_id):
    return conn.execute("SELECT * FROM expectation WHERE row_id = ?", (row_id,)).fetchone()


def names_of(conn, expectation_id):
    return sorted(r[0] for r in conn.execute(
        "SELECT a.name FROM expectation_name n JOIN accounts a ON a.id = n.person_id "
        "WHERE n.expectation_id = ?", (expectation_id,)))


def summary_of(conn, entry_id):
    return conn.execute("SELECT summary FROM change_entries WHERE id = ?", (entry_id,)).fetchone()[0]


# --- 1. marking -----------------------------------------------------------------


def test_case_1_part_of_a_dinner_preview_then_one_expectation_one_change(client, conn, story):
    jay, kim = story.people["Jay"], story.people["Kim"]
    # The drawer's live line, as the amount is typed: read, never written.
    before = snapshot(conn)
    seen = client.get(f"/api/transactions/{story.harbour}/expectation?direction=back&amount=200.00").get_json()
    assert snapshot(conn) == before
    assert seen["would"] == {"expected_minor": 20000, "own_minor": 10000,
                             "yours": "Yours: S$ 100, Dining · Household", "error": None}
    assert seen["expectation"] is None and seen["refusal"] is None and seen["direction"] == "back"
    assert {p["name"] for p in seen["people"]} >= {"Jay", "Kim", "Lena"}

    resp = mark_route(client, story.harbour, amount="200.00", names=[jay, kim])
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert set(body) == {"expectation", "row", "spending_change"}
    assert body["row"] == {"id": story.harbour, "amount_minor": 30000, "currency": "SGD",
                           "expected_minor": 20000, "placed_minor": 0, "own_minor": 10000}
    x = body["expectation"]
    assert (x["direction"], x["amount_minor"], x["currency"], x["open_minor"], x["state"], x["source"]) == \
        ("back", 20000, "SGD", 20000, "open", "hand")
    assert [n["name"] for n in x["names"]] == ["Jay", "Kim"]
    assert x["day"] == "2026-10-02" and x["row"]["description"] == "HARBOUR GRILL"
    assert x["row"]["display_type"] == "Dining" and x["opening"] is None
    assert body["spending_change"] == {"month": "2026-10", "minor": -20000, "currency": "SGD"}

    change = int(resp.headers["X-Fin-Change"])
    assert summary_of(conn, change) == "Expect back S$ 200 on HARBOUR GRILL"
    assert conn.execute("SELECT COUNT(*) FROM change_entries WHERE id >= ?", (change,)).fetchone()[0] == 1
    assert names_of(conn, x["id"]) == ["Jay", "Kim"]
    # Undo from Changes takes the mark and its names back.
    assert undo(client, change).status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM expectation").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM expectation_name").fetchone()[0] == 0


def test_case_2_all_of_it_is_none_of_yours(client, conn, story):
    kim = story.people["Kim"]
    seen = client.get(f"/api/transactions/{story.klook}/expectation?direction=back&all=1").get_json()
    assert seen["would"]["yours"] == "None of it is yours: it is not spending."
    assert seen["would"]["own_minor"] == 0
    resp = mark_route(client, story.klook, all=True, names=[kim], note="concert tickets")
    body = resp.get_json()
    assert body["row"]["own_minor"] == 0 and body["expectation"]["amount_minor"] == 18000
    assert body["expectation"]["note"] == "concert tickets"
    assert body["spending_change"] == {"month": "2026-09", "minor": -18000, "currency": "SGD"}


def test_case_3_more_than_the_row_is_refused(client, conn, story):
    refused(client, conn, "more than the row's S$ 300.00",
            lambda: mark_route(client, story.harbour, amount="350.00"))
    seen = client.get(f"/api/transactions/{story.harbour}/expectation?direction=back&amount=350.00").get_json()
    assert seen["would"]["error"] == "more than the row's S$ 300.00"


@pytest.mark.parametrize("amount, words", [
    ("200.005", "not a whole number of minor units"),
    ("0", "the amount must be more than nothing"),
    ("-5", "the amount must be more than nothing"),
    ("lots", "not an amount"),
    (None, "an amount is needed, or All of it"),
])
def test_case_4_not_whole_cents_and_nothing_are_refused(client, conn, story, amount, words):
    refused(client, conn, words, lambda: mark_route(client, story.harbour, amount=amount))


def test_case_5_the_wrong_way_round(client, conn, story):
    refused(client, conn, "money in can only be expected to pay back",
            lambda: mark_route(client, story.jay_0412, amount="10.00"))
    refused(client, conn, "money out can only be expected back",
            lambda: mark_route(client, story.harbour, direction="pay", amount="10.00"))
    refused(client, conn, "a move between your own accounts",
            lambda: mark_route(client, story.card_payoff, amount="10.00"))
    b = story.book
    transfer = row(conn, b, b.savings, "2026-10-09", "TO EVERYDAY CARD", 5000, flow="transfer",
                   other_side=b.everyday_card)
    refused(client, conn, "a move between your own accounts", lambda: mark_route(client, transfer, amount="10.00"))
    # The drawer is told before anything is typed.
    seen = client.get(f"/api/transactions/{story.card_payoff}/expectation").get_json()
    assert seen["refusal"] == "a move between your own accounts"


def test_case_6_a_second_open_edits_the_one_expectation(client, conn, story):
    jay, kim = story.people["Jay"], story.people["Kim"]
    first = mark_route(client, story.harbour, amount="200.00", names=[jay, kim]).get_json()["expectation"]
    seen = client.get(f"/api/transactions/{story.harbour}/expectation").get_json()
    assert seen["expectation"]["id"] == first["id"] and seen["expectation"]["amount_minor"] == 20000
    resp = mark_route(client, story.harbour, amount="210.00", names=[kim], note="Kim's birthday")
    assert resp.status_code == 200, resp.get_json()
    again = resp.get_json()["expectation"]
    assert again["id"] == first["id"] and again["amount_minor"] == 21000 and again["note"] == "Kim's birthday"
    assert [n["name"] for n in again["names"]] == ["Kim"]
    assert conn.execute("SELECT COUNT(*) FROM expectation WHERE row_id = ?", (story.harbour,)).fetchone()[0] == 1
    assert resp.get_json()["spending_change"] == {"month": "2026-10", "minor": -1000, "currency": "SGD"}
    change = int(resp.headers["X-Fin-Change"])
    assert summary_of(conn, change) == "Changed what is expected back on HARBOUR GRILL to S$ 210"
    # One change, undone whole: the amount, the note and the names as they were.
    assert undo(client, change).status_code == 200
    assert expectation_of(conn, story.harbour)["amount_minor"] == 20000
    assert expectation_of(conn, story.harbour)["note"] is None
    assert names_of(conn, first["id"]) == ["Jay", "Kim"]


def test_an_edit_that_changes_nothing_writes_no_change(client, conn, story):
    jay = story.people["Jay"]
    mark_route(client, story.harbour, amount="200.00", names=[jay])
    before = snapshot(conn)
    resp = mark_route(client, story.harbour, amount="200.00", names=[jay])
    assert resp.status_code == 200 and "X-Fin-Change" not in resp.headers
    assert snapshot(conn) == before


def test_case_7_edit_down_not_below_what_is_back(client, conn, story):
    made = harbour_set_against(conn, story)          # HARBOUR S$ 180.00 back of S$ 200.00
    resp = mark_route(client, story.harbour, amount="190.00",
                      names=[story.people["Jay"], story.people["Kim"]])
    assert resp.status_code == 200, resp.get_json()
    x = resp.get_json()["expectation"]
    assert (x["open_minor"], x["state"]) == (1000, "partly_back")
    assert resp.get_json()["row"]["own_minor"] == 11000
    refused(client, conn, "S$ 180.00 has already come back",
            lambda: mark_route(client, story.harbour, amount="150.00"))
    assert made["harbour"] == x["id"]


def test_case_8_someone_new_is_made_once_and_the_review_route_reuses_them(client, conn, story):
    jay = story.people["Jay"]
    resp = mark_route(client, story.harbour, amount="200.00", names=[jay], new_names=["Mei"])
    assert resp.status_code == 200, resp.get_json()
    mei = conn.execute("SELECT id, type, owner FROM accounts WHERE name = 'Mei'").fetchone()
    assert (mei["type"], mei["owner"]) == ("person", "Household")
    assert [n["name"] for n in resp.get_json()["expectation"]["names"]] == ["Jay", "Mei"]
    # An existing "MEI" is reused, not made twice; so is a name already on the book.
    mark_route(client, story.klook, all=True, new_names=["MEI", " jay "])
    assert conn.execute("SELECT COUNT(*) FROM accounts WHERE UPPER(name) = 'MEI'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM accounts WHERE UPPER(name) = 'JAY'").fetchone()[0] == 1
    assert names_of(conn, expectation_of(conn, story.klook)["id"]) == ["Jay", "Mei"]
    # The review route's "a loan to a person" finds the same Mei, as before.
    labelled = client.post(f"/api/review/{story.lena_2210}/label", json={"choice": "loan_to_person", "person": "mei"})
    assert labelled.status_code == 200, labelled.get_json()
    other = conn.execute("SELECT other_side_id FROM transactions WHERE id = ?", (story.lena_2210,)).fetchone()[0]
    assert other == mei["id"]
    # Undoing the first mark takes Mei back only if nothing rests on her: here the
    # second mark and the label do, so the undo is refused by name.
    first = int(resp.headers["X-Fin-Change"])
    assert undo(client, first).status_code == 409


def test_someone_new_with_no_name_and_a_name_that_is_not_a_person_are_refused(client, conn, story):
    refused(client, conn, "a person needs a name",
            lambda: mark_route(client, story.harbour, amount="200.00", new_names=["  "]))
    refused(client, conn, "a name on an expectation is a person's account",
            lambda: mark_route(client, story.harbour, amount="200.00", names=[story.book.savings]))


def test_case_9_mark_an_old_row_names_the_month(client, conn, story):
    resp = mark_route(client, story.golden, amount="64.00", names=[story.people["Jay"], story.people["Lena"]])
    assert resp.get_json()["spending_change"] == {"month": "2026-09", "minor": -6400, "currency": "SGD"}
    assert resp.get_json()["row"]["own_minor"] == 3200


def test_case_9a_part_of_a_rupee_row_in_rupees(client, conn, story):
    seen = client.get(f"/api/transactions/{story.swiggy}/expectation?direction=back&amount=1,800.00").get_json()
    assert seen["would"]["yours"] == "Yours: ₹ 600, Dining · Household"
    resp = mark_route(client, story.swiggy, amount="1,800.00", names=[story.people["Jay"]])
    body = resp.get_json()
    assert body["expectation"]["currency"] == "INR" and body["expectation"]["amount_minor"] == 180000
    assert body["row"] == {"id": story.swiggy, "amount_minor": 240000, "currency": "INR",
                           "expected_minor": 180000, "placed_minor": 0, "own_minor": 60000}
    assert body["spending_change"] == {"month": "2026-10", "minor": -180000, "currency": "INR"}
    assert summary_of(conn, int(resp.headers["X-Fin-Change"])) == "Expect back ₹ 1,800 on SWIGGY · Mumbai"


def test_case_9b_not_a_household_row(client, conn, story):
    b = story.book
    refused(client, conn, "only Household spending can be expected back: this row is the company's cost",
            lambda: mark_route(client, story.company_row, amount="10.00"))
    refused(client, conn, "this move already counts on that account's line",
            lambda: mark_route(client, story.loan_row, amount="10.00"))
    holding = conn.execute("INSERT INTO accounts (name, short_name, type, owner) VALUES "
                           "('Index fund', 'Index-fund', 'holding', 'Household')").lastrowid
    conn.commit()
    to_fund = row(conn, b, b.savings, "2026-10-11", "TO INDEX FUND", 50000, flow="movement", other_side=holding)
    to_company = row(conn, b, b.savings, "2026-10-12", "TO MOOM", 20000, flow="movement", other_side=b.company)
    for moved in (to_fund, to_company):
        refused(client, conn, "this move already counts on that account's line",
                lambda: mark_route(client, moved, amount="10.00"))


def test_a_movement_naming_a_person_may_be_expected_back(client, conn, story):
    b = story.book
    lent = row(conn, b, b.savings, "2026-06-03", "PAYNOW TO RAVI", 200000, flow="movement",
               other_side=story.people["Ravi"])
    resp = mark_route(client, lent, all=True, names=[story.people["Ravi"]])
    assert resp.status_code == 200, resp.get_json()
    # Not spending: no month's spending moves.
    assert resp.get_json()["spending_change"] is None


# --- Expect to pay back --------------------------------------------------------


def test_case_18_from_parents_is_to_pay_back_and_march_income_is_unchanged(client, conn, story):
    b = story.book
    parents = row(conn, b, b.savings, "2026-03-01", "FROM PARENTS", -500000, flow="review")
    cards = "/api/dashboard/stat-cards?start=2026-03-01&end=2026-03-31"
    march = client.get(cards).get_json()
    seen = client.get(f"/api/transactions/{parents}/expectation").get_json()
    assert seen["direction"] == "pay"
    resp = mark_route(client, parents, direction="pay", all=True, names=[story.people["Parents"]])
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert (body["expectation"]["direction"], body["expectation"]["amount_minor"]) == ("pay", 500000)
    assert body["row"] == {"id": parents, "amount_minor": -500000, "currency": "SGD",
                           "expected_minor": -500000, "placed_minor": 0, "own_minor": 0}
    assert body["spending_change"] is None
    assert summary_of(conn, int(resp.headers["X-Fin-Change"])) == "Expect to pay back S$ 5,000 on FROM PARENTS"
    assert conn.execute("SELECT flow_type FROM transactions WHERE id = ?", (parents,)).fetchone()[0] == "review"
    after = client.get(cards).get_json()
    queue = ("waiting", "waiting_total", "waiting_in_total", "waiting_in_count")
    assert {k: v for k, v in after.items() if k not in queue} == {k: v for k, v in march.items() if k not in queue}
    # Accounted for, the payment leaves the Queue (case 18a).
    assert (march["waiting"] - after["waiting"], march["waiting_in_count"] - after["waiting_in_count"]) == (1, 1)
    assert march["waiting_in_total"] - after["waiting_in_total"] == 5000.0


def test_pay_on_a_payment_already_partly_placed_offers_its_own_part_only(client, conn, story):
    h = mark(conn, story.harbour, 20000, names=(story.people["Jay"],))
    set_off(conn, story.jay_0412, h, 8000)                 # S$ 40.00 of Jay's S$ 120.00 left
    seen = client.get(f"/api/transactions/{story.jay_0412}/expectation?direction=pay&all=1").get_json()
    assert seen["would"]["expected_minor"] == 4000 and seen["would"]["own_minor"] == 0
    refused(client, conn, "more than the S$ 40.00 left on this row",
            lambda: mark_route(client, story.jay_0412, direction="pay", amount="50.00"))
    resp = mark_route(client, story.jay_0412, direction="pay", all=True)
    assert resp.status_code == 200 and resp.get_json()["expectation"]["amount_minor"] == 4000


def test_a_payment_wholly_placed_has_nothing_left_to_expect(client, conn, story):
    h = mark(conn, story.harbour, 20000)
    set_off(conn, story.jay_0412, h, 12000)
    refused(client, conn, "nothing is left on this row: it is all placed",
            lambda: mark_route(client, story.jay_0412, direction="pay", all=True))


# --- undo the mark ---------------------------------------------------------------


def test_case_21_undo_the_mark_refused_while_money_is_set_against_it(client, conn, story):
    made = harbour_set_against(conn, story)
    refused(client, conn,
            "Can't undo the mark yet: Jay's S$ 120 and Kim's S$ 60 are set against it. Unlink those first.",
            lambda: undo_mark_route(client, made["harbour"]))
    # From Changes, the same words.
    resp = undo(client, made["mark_entry"])
    assert resp.status_code == 409
    assert resp.get_json()["error"] == \
        "Can't undo the mark yet: Jay's S$ 120 and Kim's S$ 60 are set against it. Unlink those first."


def test_case_22_undo_the_mark_before_any_set_off(client, conn, story):
    klook = mark_route(client, story.klook, all=True, names=[story.people["Kim"]]).get_json()["expectation"]
    resp = undo_mark_route(client, klook["id"])
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json() == {
        "removed": {"expectation_id": klook["id"], "row_id": story.klook},
        "row": {"id": story.klook, "amount_minor": 18000, "currency": "SGD",
                "expected_minor": 0, "placed_minor": 0, "own_minor": 18000},
        "spending_change": {"month": "2026-09", "minor": 18000, "currency": "SGD"},
    }
    assert expectation_of(conn, story.klook) is None
    change = int(resp.headers["X-Fin-Change"])
    assert summary_of(conn, change) == "Undid the mark on KLOOK TRAVEL"
    assert conn.execute("SELECT COUNT(*) FROM change_entries WHERE id >= ?", (change,)).fetchone()[0] == 1
    # The undo is itself undoable: the mark, its id and its name come back.
    assert undo(client, change).status_code == 200
    back = expectation_of(conn, story.klook)
    assert back["id"] == klook["id"] and names_of(conn, back["id"]) == ["Kim"]


def test_undo_the_mark_on_nothing_is_not_found(client, conn, story):
    before = snapshot(conn)
    assert undo_mark_route(client, 999).status_code == 404
    assert snapshot(conn) == before


def _closed_harbour(conn, story):
    """Cases 10, 11 and a further Kim payment of S$ 20.00: HARBOUR closed, S$ 200.00 back."""
    made = harbour_set_against(conn, story)
    b = story.book
    kim_more = row(conn, b, b.savings, "2026-10-18", "PAYNOW FROM KIM L 7790", -2000, flow="review")
    set_off(conn, kim_more, made["harbour"], 2000)
    return made


def test_case_26c_a_closed_item_edited_up_reopens_and_down_closes_again(client, conn, story):
    made = _closed_harbour(conn, story)
    assert expect_back.state(conn, made["harbour"]) == "closed"
    up = mark_route(client, story.harbour, amount="250.00")
    assert up.status_code == 200, up.get_json()
    assert (up.get_json()["expectation"]["open_minor"], up.get_json()["expectation"]["state"]) == (5000, "partly_back")
    assert up.get_json()["row"]["own_minor"] == 5000
    down = mark_route(client, story.harbour, amount="200.00")
    assert down.get_json()["expectation"]["state"] == "closed"
    # Let go (from partly back), and the edit is refused.
    mark_route(client, story.harbour, amount="250.00")
    let_go(conn, made["harbour"], "gift", "2026-10-19", to_whom_id=story.people["Kim"])
    refused(client, conn, "let go since; undo the let-go first",
            lambda: mark_route(client, story.harbour, amount="260.00"))


def test_case_26b_undo_the_mark_on_a_let_go_item_is_refused(client, conn, story):
    h = mark(conn, story.harbour, 20000, names=(story.people["Kim"],))
    let_go(conn, h, "gift", "2026-10-19", to_whom_id=story.people["Kim"])
    refused(client, conn, "let go since; undo the let-go first", lambda: undo_mark_route(client, h))


# --- the machine, path by path ---------------------------------------------------


def test_machine_open_edit_stays_open_and_open_undo_mark_is_gone(client, conn, story):
    x = mark_route(client, story.harbour, amount="200.00").get_json()["expectation"]
    assert x["state"] == "open"
    edited = mark_route(client, story.harbour, amount="150.00").get_json()["expectation"]
    assert (edited["state"], edited["open_minor"]) == ("open", 15000)
    assert undo_mark_route(client, x["id"]).status_code == 200
    assert expectation_of(conn, story.harbour) is None


def test_machine_partly_back_edit_to_what_is_back_closes(client, conn, story):
    harbour_set_against(conn, story)
    x = mark_route(client, story.harbour, amount="180.00").get_json()["expectation"]
    assert (x["state"], x["open_minor"]) == ("closed", 0)


@pytest.mark.parametrize("state", ["partly_back", "closed"])
def test_machine_undo_mark_refused_from_partly_back_and_closed(client, conn, story, state):
    made = harbour_set_against(conn, story) if state == "partly_back" else _closed_harbour(conn, story)
    assert expect_back.state(conn, made["harbour"]) == state
    resp = undo_mark_route(client, made["harbour"])
    assert resp.status_code == 409 and resp.get_json()["error"].startswith("Can't undo the mark yet: ")


def test_machine_a_pay_item_edits_and_undoes_the_same_way(client, conn, story):
    b = story.book
    parents = row(conn, b, b.savings, "2026-03-01", "FROM PARENTS", -500000, flow="review")
    x = mark_route(client, parents, direction="pay", all=True).get_json()["expectation"]
    edited = mark_route(client, parents, direction="pay", amount="4,000.00")
    assert edited.status_code == 200 and edited.get_json()["expectation"]["amount_minor"] == 400000
    assert summary_of(conn, int(edited.headers["X-Fin-Change"])) == \
        "Changed what is to pay back on FROM PARENTS to S$ 4,000"
    # The direction of a mark is its row's: a back request on it is refused.
    refused(client, conn, "money in can only be expected to pay back",
            lambda: mark_route(client, parents, direction="back", amount="10.00"))
    assert undo_mark_route(client, x["id"]).status_code == 200


def test_unknown_row_and_bad_requests_write_nothing(client, conn, story):
    before = snapshot(conn)
    assert mark_route(client, 99999, amount="1.00").status_code == 404
    assert client.post(f"/api/transactions/{story.harbour}/expectation", data="x").status_code == 400
    assert mark_route(client, story.harbour, amount="1.00", direction="sideways").status_code == 400
    assert mark_route(client, story.harbour, amount="1.00", names="Jay").status_code == 400
    assert mark_route(client, story.harbour, amount="1.00", note=5).status_code == 400
    assert snapshot(conn) == before


def test_guards_are_read_at_the_write_not_from_the_drawer(client, conn, story):
    """The drawer read HARBOUR open; a payment was placed since (another tab).
    The edit reads the set-offs in its own transaction and is refused."""
    h = mark(conn, story.harbour, 20000, names=(story.people["Jay"],))
    seen = client.get(f"/api/transactions/{story.harbour}/expectation").get_json()
    assert seen["expectation"]["state"] == "open"
    set_off(conn, story.jay_0412, h, 12000)
    refused(client, conn, "S$ 120.00 has already come back",
            lambda: mark_route(client, story.harbour, amount="100.00"))


def test_the_routes_name_their_changes_for_the_history():
    import expectation_api
    assert set(expectation_api.SUMMARIES) == {"mark", "undo_mark"}


# --- the drawer's helpers, under node --------------------------------------------

APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")


def app_function(name: str) -> str:
    m = (re.search(rf"^function {name}\(.*\}}$", APP_JS, re.M)
         or re.search(rf"^function {name}\(.*?^\}}$", APP_JS, re.M | re.S))
    assert m, name
    return m.group(0)


def run_node(code: str):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def harness(body: str) -> str:
    consts = "\n".join(re.search(rf"^const {n} = .*$", APP_JS, re.M).group(0)
                       for n in ("SIGNS", "DIGITS", "formatters"))
    return ("globalThis.window = globalThis;\n"
            "const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;');\n"
            f"{consts}\n{app_function('digitsOf')}\n{app_function('groupFor')}\n{app_function('money')}\n"
            + (STATIC / "expect-mark.js").read_text(encoding="utf-8") + "\n" + body)


@needs_node
def test_case_9_toast_names_the_month_and_how_much():
    out = run_node(harness("""
    const w = Expect.mark.spendingWords;
    console.log(JSON.stringify([
      w({ month: '2026-09', minor: -6400, currency: 'SGD' }, '2026-10-19'),
      w({ month: '2026-09', minor: 18000, currency: 'SGD' }, '2026-10-19'),
      w({ month: '2025-12', minor: -1000, currency: 'SGD' }, '2026-10-19'),
      w({ month: '2026-10', minor: -180000, currency: 'INR' }, '2026-10-19'),
      w({ month: '2026-10', minor: 0, currency: 'SGD' }, '2026-10-19'),
      w(null, '2026-10-19'),
    ]));"""))
    assert out == [
        "September's spending is now S$ 64.00 less.",
        "September's spending is now S$ 180.00 more.",
        "December 2025's spending is now S$ 10.00 less.",
        "October's spending is now ₹ 1,800.00 less.",
        "", "",
    ]


@needs_node
def test_case_9a_the_pill_reads_the_rows_own_parts_from_the_payload():
    out = run_node(harness("""
    console.log(JSON.stringify([
      window.expectRowPill({ id: 1, currency: 'INR', amount_sgd: 2400, expected_minor: 180000 }),
      window.expectRowPill({ id: 2, currency: 'SGD', amount_sgd: 300, expected_minor: 20000 }),
      window.expectRowPill({ id: 3, currency: 'SGD', amount_sgd: -5000, expected_minor: -500000 }),
      window.expectRowPill({ id: 4, currency: 'SGD', amount_sgd: 300, expected_minor: 0 }),
      window.expectRowPill({ id: 5, currency: 'SGD', amount_sgd: 300 }),
      window.expectRowPill({ id: 6, currency: 'SGD', amount_sgd: 20.5, expected_minor: 1050 }),
    ]));"""))
    texts = [re.sub(r"<[^>]+>", "", s).strip() for s in out]
    assert texts == ["₹ 1,800 expected back", "S$ 200 expected back", "S$ 5,000 to pay back", "", "",
                     "S$ 10.50 expected back"]
    assert all('class="xb-pill"' in s for s in out[:3])


@needs_node
def test_the_row_actions_offer_the_mark_its_mirror_or_its_edit():
    out = run_node(harness("""
    const a = window.expectRowActions;
    const label = h => h.replace(/<[^>]+>/g, '');
    console.log(JSON.stringify([
      label(a({ id: 1, amount_sgd: 300, flow_type: 'expense', currency: 'SGD' })),
      label(a({ id: 2, amount_sgd: -120, flow_type: 'review', currency: 'SGD' })),
      label(a({ id: 3, amount_sgd: 300, flow_type: 'expense', currency: 'SGD', expected_minor: 20000 })),
      label(a({ id: 4, amount_sgd: -50, flow_type: 'income', currency: 'SGD', expected_minor: -5000 })),
      a({ id: 5, amount_sgd: 400, flow_type: 'payment', currency: 'SGD' }),
      a({ id: 6, amount_sgd: 50, flow_type: 'transfer', currency: 'SGD' }),
      /data-act="expect-mark" data-tx="1"/.test(a({ id: 1, amount_sgd: 300, flow_type: 'expense' })),
      label(window.expectMerchantRowAction({ id: 7, amount_sgd: 30, flow_type: 'expense' })),
      window.expectMerchantRowAction({ id: 8, amount_sgd: -30, flow_type: 'refund' }),
    ]));"""))
    assert out == ["Expect some back…", "Expect to pay back", "Change what is expected back…",
                   "Change what is to pay back…", "", "", True, "Expect some back…", ""]


@needs_node
def test_the_drawer_sends_the_contract_request():
    out = run_node(harness("""
    const r = Expect.mark.request;
    console.log(JSON.stringify([
      r({ direction: 'back', part: 'part', amount: ' 200.00 ', names: [31, 32], newNames: [], note: '' }),
      r({ direction: 'back', part: 'all', amount: '12', names: [32], newNames: ['Mei'], note: ' concert tickets ' }),
    ]));"""))
    assert out == [
        {"direction": "back", "all": False, "amount": "200.00", "names": [31, 32], "new_names": [], "note": None},
        {"direction": "back", "all": True, "amount": None, "names": [32], "new_names": ["Mei"],
         "note": "concert tickets"},
    ]
    contract = json.loads((Path(__file__).parent / "fixtures" / "expect" / "mark.json").read_text(encoding="utf-8"))
    assert set(out[0]) == set(contract["request"])
