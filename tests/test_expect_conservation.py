"""Release 2's conservation check (personal-ledger 23, ticket 46; spec case 50
and Further Notes: "a conservation test that runs the month check after each
event of the October story").

October is played as the operator would live it: each row arrives (seeded as
a statement's row, through tests/expect_fixtures.py), then the operator acts
on it through the app's own routes: Expect some back…, the refund linker,
Back for…, This was…, Nothing yet, Let it go…. After every event the month
check is read through GET /api/balance-sheet, and the difference it cannot
explain stays what it was before October began (S$ 0.00). Net worth moves only
by what income, spending and currency change say.

Each act is also one change: it answers with one X-Fin-Change, undoing that
change from Changes puts the sheet, the check and every expect-back record
back exactly as they were before the act, and undoing the undo puts the act
back. So conservation holds in both directions, not only forwards.

The one moment the check does not tie is named and asserted: case 13's S$ 20
left on Lena's payment waits for a label, unexplained beside the review list,
until This was… labels it a gift.

Seam 1 (the routes through the Flask test client) for the acts, Seam 2 (the
sheet and the month check) for the figures. Every name and figure is invented;
nothing opens the real database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

import expect_back
import letgo_api
from expect_fixtures import Book, check_book, mark, opening, row, set_off
from trend_fixtures import anchor, merchant, seed_rate
from trend_fixtures import row as tf_row

MONTH = "2026-10"
TODAY = "2026-10-19"
RECORDS = ("expectation", "expectation_name", "set_off", "let_go", "refund_link", "sender_wording", "dismissal")
CHECK_FIGURES = ("opening_minor", "income_minor", "spending_minor", "currency_change_minor", "outside_minor",
                 "expected_minor", "actual_minor", "unexplained_minor")


# --- the book before October -------------------------------------------------------


@dataclass
class Story:
    """The check book and the October story's rows and records, by name."""
    book: Book
    ids: dict = field(default_factory=dict)

    @property
    def people(self) -> dict[str, int]:
        return self.book.people


def before_october(conn) -> Story:
    """The check book as it stands on 30 Sep: the Household's two cards and its
    bank figured on 30 Sep (so all three are in the October check), the rupee
    card with no figure (left out of it), the rates saved for August and
    September, and the items open before October: AIRBNB (S$ 620.00 back from
    Lena, S$ 580.00 set off on 26 Sep), KLOOK (all of S$ 180.00 back from Kim)
    and Parents' S$ 5,000.00 to pay back, S$ 1,000.00 of it paid on 1 Aug
    (case 42, as the conversion leaves it)."""
    b = check_book(conn)
    p = b.people
    s = Story(b)
    anchor(conn, b.rewards_card, "2026-09-30", -100000, source="supplied")
    anchor(conn, b.everyday_card, "2026-09-30", -50000, source="supplied")
    anchor(conn, b.savings, "2026-09-30", 900000, source="supplied")
    seed_rate(conn, "INR/SGD", "2026-08-31", "0.0160")
    seed_rate(conn, "INR/SGD", "2026-09-30", "0.0155")

    s.ids["airbnb"] = mark(conn, row(conn, b, b.rewards_card, "2026-06-28", "AIRBNB", 62000, type_name="Travel"),
                           62000, names=(p["Lena"],))
    set_off(conn, row(conn, b, b.savings, "2026-09-26", "PAYNOW FROM LENA 1500", -58000, flow="review"),
            s.ids["airbnb"], 58000)
    s.ids["klook_row"] = row(conn, b, b.everyday_card, "2026-09-28", "KLOOK TRAVEL", 18000, type_name="Entertainment")
    s.ids["klook"] = mark(conn, s.ids["klook_row"], 18000, names=(p["Kim"],), note="concert tickets")
    loan = row(conn, b, b.savings, "2026-03-01", "FROM PARENTS", -500000, flow="movement", other_side=p["Parents"])
    s.ids["parents"] = mark(conn, loan, 500000, names=(p["Parents"],), direction="pay", source="converted")
    repaid = row(conn, b, b.savings, "2026-08-01", "TO PARENTS", 100000, flow="movement", other_side=p["Parents"])
    set_off(conn, repaid, s.ids["parents"], 100000, source="converted")
    s.ids["sam"] = opening(conn, p["Sam"], "2026-01-01", 80000)
    return s


# --- reading the sheet and the check through the route ------------------------------


def read(client, conn) -> dict:
    """What the act must conserve: the month check's figures, net worth and each
    Books section's total, as GET /api/balance-sheet answers; and every
    expect-back record, as the database holds it."""
    resp = client.get(f"/api/balance-sheet?month={MONTH}")
    assert resp.status_code == 200, resp.get_json()
    shown = resp.get_json()
    mc = shown["month_check"]
    assert mc["available"], mc["why"]
    return {
        "check": {k: mc[k] for k in CHECK_FIGURES},
        "net_worth_minor": shown["net_worth_minor"],
        "books": {sec["key"]: sec["total_minor"] for sec in shown["expect"]["sections"]},
        "records": {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")] for t in RECORDS},
    }


def act(client, conn, method: str, url: str, body: dict | None = None) -> dict:
    """One act through its route: it answers 200 with exactly one change; undoing
    that change restores everything read before it; undoing the undo puts the
    act back. Returns the route's answer."""
    before = read(client, conn)
    entries = conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0]
    resp = client.open(url, method=method, json=body if body is not None else {})
    assert resp.status_code == 200, (url, resp.get_json())
    change = resp.headers.get("X-Fin-Change")
    assert change, f"{url} made no change in the history"
    assert conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0] == entries + 1, url
    after = read(client, conn)

    undone = client.post(f"/api/history/{change}/undo")
    assert undone.status_code == 200, (url, undone.get_json())
    assert read(client, conn) == before, f"undoing {url} did not put everything back"
    redone = client.post(f"/api/history/{undone.headers['X-Fin-Change']}/undo")
    assert redone.status_code == 200, (url, redone.get_json())
    assert read(client, conn) == after, f"undoing the undo of {url} did not put the act back"
    return resp.get_json()


def gift_received(conn) -> int:
    return conn.execute("SELECT id FROM types WHERE kind = 'income' AND name = 'Gift received'").fetchone()[0]


# --- the October story, event by event ------------------------------------------------


def events(client, conn, s: Story):
    """The October story: each event is a row arriving and what the operator
    does with it. Yields (event name, what the check must say after it)."""
    b, p = s.book, s.people
    oc = b.statements

    # Case 1. HARBOUR GRILL arrives; Expect some back… S$ 200.00 from Jay and Kim.
    s.ids["harbour_row"] = row(conn, b, b.rewards_card, "2026-10-02", "HARBOUR GRILL", 30000, type_name="Dining")
    got = act(client, conn, "POST", f"/api/transactions/{s.ids['harbour_row']}/expectation",
              {"direction": "back", "all": False, "amount": "200.00", "names": [p["Jay"], p["Kim"]],
               "new_names": [], "note": None})
    s.ids["harbour"] = got["expectation"]["id"]
    yield "case 1: the dinner", {"spending_minor": 10000}

    # Case 9a. SWIGGY arrives on the rupee card (left out of the check); ₹ 1,800.00 back from Jay.
    s.ids["swiggy_row"] = row(conn, b, b.rupee_card, "2026-10-06", "SWIGGY · Mumbai", 240000, type_name="Dining")
    got = act(client, conn, "POST", f"/api/transactions/{s.ids['swiggy_row']}/expectation",
              {"direction": "back", "all": False, "amount": "1800.00", "names": [p["Jay"]],
               "new_names": [], "note": None})
    s.ids["swiggy"] = got["expectation"]["id"]
    yield "case 9a: a rupee row", {}

    # Case 36. KLOOK TRAVEL REFUND arrives; the linker links it and closes S$ 30.00 of KLOOK.
    s.ids["klook_refund"] = row(conn, b, b.everyday_card, "2026-10-04", "KLOOK TRAVEL REFUND", -3000,
                                flow="refund", type_name="Entertainment")
    act(client, conn, "POST", "/api/refund-links/run")
    assert [tuple(r) for r in conn.execute("SELECT amount_minor, source FROM set_off WHERE payment_row_id = ?",
                                           (s.ids["klook_refund"],))] == [(3000, "refund")]
    yield "case 36: a refund closes part of KLOOK", {}

    # Case 10. Jay's S$ 120.00 arrives; Back for… HARBOUR GRILL.
    s.ids["jay"] = row(conn, b, b.savings, "2026-10-10", "PAYNOW FROM JAY TAN 0412", -12000, flow="review")
    act(client, conn, "POST", f"/api/transactions/{s.ids['jay']}/set-offs",
        {"placements": [{"expectation_id": s.ids["harbour"], "amount": "120.00"}], "waiting": None})
    yield "case 10: Jay pays back", {"income_minor": 0}

    # Case 11. Kim's S$ 70.00 arrives; S$ 60.00 on HARBOUR and S$ 10.00 on KLOOK, one change.
    s.ids["kim"] = row(conn, b, b.savings, "2026-10-15", "PAYNOW FROM KIM L 7781", -7000, flow="review")
    act(client, conn, "POST", f"/api/transactions/{s.ids['kim']}/set-offs",
        {"placements": [{"expectation_id": s.ids["harbour"], "amount": "60.00"},
                        {"expectation_id": s.ids["klook"], "amount": "10.00"}], "waiting": None})
    yield "case 11: Kim's payment on two items", {}

    # Case 13. Lena's S$ 60.00 arrives; S$ 40.00 closes AIRBNB, S$ 20.00 stays on it to label.
    s.ids["lena"] = row(conn, b, b.savings, "2026-10-12", "PAYNOW FROM LENA 2210", -6000, flow="review")
    act(client, conn, "POST", f"/api/transactions/{s.ids['lena']}/set-offs",
        {"placements": [{"expectation_id": s.ids["airbnb"], "amount": "40.00"}], "waiting": None})
    yield "case 13: the rest waits for a label", {"unexplained_minor": 2000}
    # This was… a gift: the S$ 20.00 left is income, and the check ties again.
    act(client, conn, "POST", f"/api/review/{s.ids['lena']}/label", {"choice": "gift"})
    yield "case 13: labelled a gift", {"income_minor": 2000}

    # Case 17. Lena's S$ 100.00 arrives before its row; Nothing yet.
    s.ids["lena_bbq"] = row(conn, b, b.savings, "2026-10-17", "PAYNOW FROM LENA 2210", -10000, flow="review")
    act(client, conn, "POST", f"/api/transactions/{s.ids['lena_bbq']}/waiting",
        {"person_id": p["Lena"], "note": "for the BBQ, when it is charged"})
    yield "case 17: waiting for its row", {}

    # Rows needing no act: spending, a refund netting in its month (15), a merchant
    # hidden from expense views, a company's cost on a household card, a card payoff
    # and a loan instalment (to an account left out of the check).
    tidy = merchant(conn, "Tidy Storage", type_name="Repairs & maintenance", hidden=True)
    row(conn, b, b.everyday_card, "2026-10-09", "FRESH MART", 4550, type_name="Groceries")
    row(conn, b, b.everyday_card, "2026-10-20", "FRESH MART REFUND", -500, flow="refund", type_name="Groceries")
    tf_row(conn, oc[(b.everyday_card, MONTH)], "TIDY STORAGE", 4500, service_id=tidy,
           type_name="Repairs & maintenance", day="2026-10-07")
    row(conn, b, b.rewards_card, "2026-10-03", "SAMPLE SUPPLIER", 5000, type_name="Dining", book_name="Moom")
    row(conn, b, b.savings, "2026-10-08", "PAYMENT TO REWARDS CARD", 40000, flow="payment", other_side=b.rewards_card)
    row(conn, b, b.rewards_card, "2026-10-08", "PAYMENT RECEIVED", -40000, flow="payment", other_side=b.savings)
    row(conn, b, b.savings, "2026-10-05", "LOAN INSTALMENT", 90000, flow="movement", other_side=b.home_loan)
    conn.commit()
    yield "rows that need nothing", {}

    # Case 24. HARBOUR's S$ 20.00 let go as a gift to Kim, on 19 Oct.
    act(client, conn, "POST", f"/api/expectations/{s.ids['harbour']}/let-go",
        {"became": "gift", "to_id": p["Kim"], "type_id": None})
    yield "case 24: let go as a gift", {}

    # Case 26d / 50a. Parents' S$ 4,000.00 to pay back let go: a gift to me, income on 19 Oct.
    act(client, conn, "POST", f"/api/expectations/{s.ids['parents']}/let-go",
        {"became": "gift", "to_id": None, "type_id": gift_received(conn)})
    yield "case 26d: a To pay back item let go", {}


# Everything the October story does to October's figures, once it has all happened.
SPENDING_AT_END = 10000 + 2000 + 4550 - 500 + 4500   # HARBOUR's own part, the gift, Fresh Mart net, Tidy
INCOME_AT_END = 2000 + 400000                         # Lena's S$ 20 gift, Parents' let-go


@pytest.fixture(autouse=True)
def let_go_on_19_october(monkeypatch):
    monkeypatch.setattr(letgo_api, "today", lambda: TODAY)


def play(client, conn) -> Story:
    """The whole October story, every check passing; for other tests to read."""
    s = before_october(conn)
    for _ in events(client, conn, s):
        pass
    return s


# --- the test ------------------------------------------------------------------------


def test_case_50_the_month_check_ties_after_every_event_of_the_october_story(client, conn):
    s = before_october(conn)
    start = read(client, conn)
    assert start["check"]["unexplained_minor"] == 0, start["check"]   # the planted S$ 0.00
    opening_worth = start["check"]["opening_minor"]

    seen = []
    for name, says in events(client, conn, s):
        now = read(client, conn)
        check = now["check"]
        want = {"unexplained_minor": 0, **says}
        assert {k: check[k] for k in want} == want, (name, check)
        # Net worth moves by exactly what the check explains, after every event.
        assert check["opening_minor"] == opening_worth, name
        assert (check["actual_minor"] - check["opening_minor"] - check["outside_minor"]
                == check["income_minor"] - check["spending_minor"] + check["currency_change_minor"]
                + check["unexplained_minor"]), name
        seen.append(name)
    assert len(seen) == 11

    end = read(client, conn)["check"]
    assert (end["spending_minor"], end["income_minor"]) == (SPENDING_AT_END, INCOME_AT_END)
    books = read(client, conn)["books"]
    # Expected back: Sam S$ 800, KLOOK S$ 140, SWIGGY ₹ 1,800 (S$ 27.90); AIRBNB and HARBOUR closed or let go.
    # To pay back is empty, so Books shows no such section.
    assert books == {"expected_back": 80000 + 14000 + 2790, "waiting": 10000}


# --- the machine's transitions no lane tested on their own --------------------------------
#
# Checked against the list under the machine (spec 23, Implementation Decisions):
# every transition and forbidden entry has its route test in tickets 34-43
# (test_expect_mark, _setoff, _letgo, _waiting, _foundation, test_refund_close)
# except these three ends of allowed transitions, played here through the routes,
# each conserving the month check and undone and redone like every act above:
# partly_back -unlink-> open, closed -unlink-> partly_back and closed -unlink-> open,
# open -let_go-> let_go -undo_let_go-> open.


def state(conn, expectation_id: int) -> str:
    return expect_back.state(conn, expectation_id)


def test_machine_unlink_from_closed_and_to_open_and_undo_let_go_to_open_conserve(client, conn):
    s = before_october(conn)
    b, p = s.book, s.people
    ties = lambda: read(client, conn)["check"]["unexplained_minor"] == 0  # noqa: E731
    assert ties()

    harbour_row = row(conn, b, b.rewards_card, "2026-10-02", "HARBOUR GRILL", 30000, type_name="Dining")
    harbour = act(client, conn, "POST", f"/api/transactions/{harbour_row}/expectation",
                  {"direction": "back", "all": False, "amount": "200.00", "names": [p["Jay"], p["Kim"]],
                   "new_names": [], "note": None})["expectation"]["id"]
    jay = row(conn, b, b.savings, "2026-10-10", "PAYNOW FROM JAY TAN 0412", -12000, flow="review")
    act(client, conn, "POST", f"/api/transactions/{jay}/set-offs",
        {"placements": [{"expectation_id": harbour, "amount": "120.00"}], "waiting": None})
    assert state(conn, harbour) == "partly_back" and ties()

    # partly_back -unlink-> open: Jay's S$ 120 back in the Queue, HARBOUR all open again.
    [jay_on_harbour] = [r[0] for r in conn.execute("SELECT id FROM set_off WHERE payment_row_id = ?", (jay,))]
    act(client, conn, "DELETE", f"/api/set-offs/{jay_on_harbour}")
    assert state(conn, harbour) == "open"
    assert expect_back.parts(conn, [jay])[jay]["own_minor"] == -12000
    act(client, conn, "POST", f"/api/review/{jay}/label", {"choice": "gift"})   # labelled, so the check ties
    assert ties()

    # KLOOK closed by Kim's two payments, S$ 100 and S$ 80.
    kim_100 = row(conn, b, b.savings, "2026-10-15", "PAYNOW FROM KIM L 7781", -10000, flow="review")
    kim_80 = row(conn, b, b.savings, "2026-10-16", "PAYNOW FROM KIM L 7781", -8000, flow="review")
    for payment, amount in ((kim_100, "100.00"), (kim_80, "80.00")):
        act(client, conn, "POST", f"/api/transactions/{payment}/set-offs",
            {"placements": [{"expectation_id": s.ids["klook"], "amount": amount}], "waiting": None})
    assert state(conn, s.ids["klook"]) == "closed" and ties()

    # closed -unlink-> partly_back, then partly_back -unlink-> open.
    [on_80] = [r[0] for r in conn.execute("SELECT id FROM set_off WHERE payment_row_id = ?", (kim_80,))]
    act(client, conn, "DELETE", f"/api/set-offs/{on_80}")
    assert state(conn, s.ids["klook"]) == "partly_back"
    [on_100] = [r[0] for r in conn.execute("SELECT id FROM set_off WHERE payment_row_id = ?", (kim_100,))]
    act(client, conn, "DELETE", f"/api/set-offs/{on_100}")
    assert state(conn, s.ids["klook"]) == "open"
    # Both payments back on Money in, unexplained until labelled.
    assert read(client, conn)["check"]["unexplained_minor"] == 18000
    for payment in (kim_100, kim_80):
        act(client, conn, "POST", f"/api/review/{payment}/label", {"choice": "gift"})
    assert ties()

    # closed -unlink-> open: one payment of all S$ 180, unlinked.
    kim_180 = row(conn, b, b.savings, "2026-10-17", "PAYNOW FROM KIM L 7781", -18000, flow="review")
    act(client, conn, "POST", f"/api/transactions/{kim_180}/set-offs",
        {"placements": [{"expectation_id": s.ids["klook"], "amount": "180.00"}], "waiting": None})
    assert state(conn, s.ids["klook"]) == "closed" and ties()
    [on_180] = [r[0] for r in conn.execute("SELECT id FROM set_off WHERE payment_row_id = ?", (kim_180,))]
    act(client, conn, "DELETE", f"/api/set-offs/{on_180}")
    assert state(conn, s.ids["klook"]) == "open"
    act(client, conn, "POST", f"/api/review/{kim_180}/label", {"choice": "gift"})
    assert ties()

    # open -let_go-> let_go -undo_let_go-> open: HARBOUR's whole S$ 200 let go as mine, then undone.
    act(client, conn, "POST", f"/api/expectations/{harbour}/let-go", {"became": "mine", "to_id": None, "type_id": None})
    assert state(conn, harbour) == "let_go" and ties()
    assert read(client, conn)["check"]["spending_minor"] == 30000
    act(client, conn, "DELETE", f"/api/expectations/{harbour}/let-go")
    assert state(conn, harbour) == "open" and ties()
    assert read(client, conn)["check"]["spending_minor"] == 10000
