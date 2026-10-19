"""The month check's expect-back terms (personal-ledger 23, ticket 31; spec E5).

Since the switch (ticket 45), the check reads spending and income through
the own part with the let-go terms, counts the three Books sections in net
worth (persons are no longer lines of it), and adds a currency-change line
for each open foreign-currency item and the spread on each cross-currency
set-off. The month ties after every act of the October story (case 50), a To
pay back let-go (50a) and a rupee item across two month ends (50b).

Seam 2 of the spec: the sheet and the month check as functions on synthetic
rows. Every name and figure is invented (the case list's specimens).
"""

from datetime import date

import pytest

import balance_sheet
import expect_back
import month_check
from expect_fixtures import check_book, entry, let_go, mark, row, set_off
from trend_fixtures import anchor, seed_rate

LATER = date(2027, 1, 31)  # ages do not enter the check; any day after the month will do


def sheet(conn, month: str) -> dict:
    return balance_sheet.sheet(conn, month, today=LATER)


def check(conn, month: str = "2026-10") -> dict:
    return month_check.check(conn, month, sheet(conn, month))


def september_book(conn):
    """The check book with the Rewards card and Everyday savings figured on
    30 Sep (both in the check), and the items open before October: AIRBNB
    (S$ 620.00 back from Lena, S$ 580.00 set off on 26 Sep) and KLOOK (all of
    S$ 180.00 back from Kim, on the Everyday card, which has no figure)."""
    b = check_book(conn)
    anchor(conn, b.rewards_card, "2026-09-30", -100000, source="supplied")
    anchor(conn, b.savings, "2026-09-30", 500000, source="supplied")
    p = b.people
    airbnb = mark(conn, row(conn, b, b.rewards_card, "2026-06-28", "AIRBNB", 62000, type_name="Travel"),
                  62000, names=(p["Lena"],))
    set_off(conn, row(conn, b, b.savings, "2026-09-26", "PAYNOW FROM LENA 2210", -58000, flow="review"),
            airbnb, 58000)
    klook = mark(conn, row(conn, b, b.everyday_card, "2026-09-28", "KLOOK TRAVEL", 18000,
                           type_name="Entertainment"), 18000, names=(p["Kim"],))
    return b, {"airbnb": airbnb, "klook": klook}


def ties(shown: dict) -> None:
    assert shown["available"], shown["why"]
    assert shown["unexplained_minor"] == 0, (shown["unexplained_minor"], shown)


# --- case 50: the October story ties after every act ----------------------------


def test_case_50_the_month_check_ties_after_every_act(conn):
    b, items = september_book(conn)
    p = b.people
    ties(check(conn))
    start = check(conn)["opening_minor"]

    # Dinner: card owed +S$ 300, spending S$ 100, Expected back +S$ 200.
    harbour = mark(conn, row(conn, b, b.rewards_card, "2026-10-02", "HARBOUR GRILL", 30000, type_name="Dining"),
                   20000, names=(p["Jay"], p["Kim"]))
    shown = check(conn)
    ties(shown)
    assert shown["spending_minor"] == 10000
    assert shown["actual_minor"] - start == -10000

    # Jay: cash +S$ 120, Expected back -S$ 120; never income.
    set_off(conn, row(conn, b, b.savings, "2026-10-10", "PAYNOW FROM JAY TAN 0412", -12000, flow="review"),
            harbour, 12000)
    shown = check(conn)
    ties(shown)
    assert shown["income_minor"] == 0

    # Kim: S$ 70 as S$ 60 on HARBOUR and S$ 10 on KLOOK, one change.
    kim = row(conn, b, b.savings, "2026-10-15", "PAYNOW FROM KIM L 7781", -7000, flow="review")
    with entry(conn, "Set off"):
        set_off(conn, kim, harbour, 6000)
        set_off(conn, kim, items["klook"], 1000)
    ties(check(conn))

    # Case 13: Lena's S$ 60 closes AIRBNB's S$ 40; the S$ 20 left, labelled, is income.
    lena = row(conn, b, b.savings, "2026-10-12", "PAYNOW FROM LENA 2210", -6000, flow="review")
    set_off(conn, lena, items["airbnb"], 4000)
    # Until the S$ 20 left on the payment is labelled it waits for review, unexplained beside the list.
    assert check(conn)["unexplained_minor"] == 2000
    with entry(conn, "This was a gift"):
        conn.execute("UPDATE transactions SET flow_type = 'income' WHERE id = ?", (lena,))
    shown = check(conn)
    ties(shown)
    assert shown["income_minor"] == 2000

    # Case 17: Lena's S$ 100 waiting for its row: cash +S$ 100, Waiting +S$ 100 owed.
    waiting = row(conn, b, b.savings, "2026-10-17", "PAYNOW FROM LENA 2210", -10000, flow="review")
    set_off(conn, waiting, None, 10000, person_id=p["Lena"])
    shown = check(conn)
    ties(shown)
    assert shown["actual_minor"] - start == -10000 + 2000

    # Case 24: HARBOUR's S$ 20 let go as a gift: Expected back -S$ 20, spending +S$ 20.
    let_go(conn, harbour, "gift", "2026-10-19", to_whom_id=p["Kim"])
    shown = check(conn)
    ties(shown)
    assert shown["spending_minor"] == 12000
    assert shown["actual_minor"] - start == -12000 + 2000


def test_case_17e_the_waiting_payment_finds_its_row_in_the_next_month(conn):
    b, _ = september_book(conn)
    lena = row(conn, b, b.savings, "2026-09-30", "PAYNOW FROM LENA 2210", -10000, flow="review")
    waiting = set_off(conn, lena, None, 10000, person_id=b.people["Lena"])
    september = check(conn, "2026-09")
    cold = row(conn, b, b.rewards_card, "2026-10-02", "COLD STORAGE", 26000, type_name="Groceries")
    with entry(conn, "Expect back and set Lena's S$ 100 against it"):
        expectation = mark(conn, cold, 10000, names=(b.people["Lena"],))
        conn.execute("UPDATE set_off SET expectation_id = ? WHERE id = ?", (expectation, waiting))
    assert check(conn, "2026-09")["actual_minor"] == september["actual_minor"]  # a checked month never changes
    shown = check(conn)
    ties(shown)
    assert shown["spending_minor"] == 16000


# --- case 50a: a To pay back let-go ties ----------------------------------------


def test_case_50a_a_to_pay_back_let_go_is_income_and_the_check_still_ties(conn):
    b, _ = september_book(conn)
    parents = b.people["Parents"]
    loan = row(conn, b, b.savings, "2026-03-01", "FROM PARENTS", -500000, flow="movement", other_side=parents)
    owed = mark(conn, loan, 500000, names=(parents,), direction="pay", source="converted")
    repaid = row(conn, b, b.savings, "2026-08-01", "TO PARENTS", 100000, flow="movement", other_side=parents)
    set_off(conn, repaid, owed, 100000, source="converted")
    before = check(conn)
    ties(before)
    gift_received = conn.execute("SELECT id FROM types WHERE kind = 'income' AND name = 'Gift received'").fetchone()[0]
    with entry(conn, "Let it go"):
        conn.execute("INSERT INTO let_go (expectation_id, amount_minor, became, book, type_id, counts_on) "
                     "VALUES (?, 400000, 'gift', 'Household', ?, '2026-10-19')", (owed, gift_received))
    after = check(conn)
    ties(after)
    assert after["income_minor"] - before["income_minor"] == 400000
    assert after["actual_minor"] - before["actual_minor"] == 400000  # To pay back -S$ 4,000.00


# --- case 50b: a rupee item across two month ends -------------------------------


def rupee_story(conn):
    b, _ = september_book(conn)
    anchor(conn, b.savings, "2026-10-31", 500000, source="supplied")
    for day, rate in (("2026-08-31", "0.0160"), ("2026-09-30", "0.0155"),
                      ("2026-10-31", "0.0150"), ("2026-11-30", "0.0160")):
        seed_rate(conn, "INR/SGD", day, rate)
    swiggy = mark(conn, row(conn, b, b.rupee_card, "2026-10-06", "SWIGGY · Mumbai", 240000, type_name="Dining"),
                  180000, names=(b.people["Jay"],))
    return b, swiggy


def test_case_50b_currency_change_and_spread_on_a_rupee_item(conn):
    b, swiggy = rupee_story(conn)
    # October: the Rupee card has no figure, so SWIGGY's S$ 27.00 comes into the check from outside it.
    october = check(conn)
    ties(october)
    assert october["outside_minor"] == 2700
    jay = row(conn, b, b.savings, "2026-11-12", "PAYNOW FROM JAY TAN", -900, flow="review")
    set_off(conn, jay, swiggy, 900, item_amount_minor=60000, rate="0.0150", rate_date="2026-10-31")
    november = check(conn, "2026-11")
    ties(november)
    assert november["income_minor"] == 0 and november["spending_minor"] == 0
    assert november["actual_minor"] - november["opening_minor"] == 120  # cash +9.00, SWIGGY 27.00 -> 19.20
    assert november["currency_change_minor"] == 120
    [term] = [x for x in november["currency_change_lines"] if x.get("key") == f"expectation:{swiggy}"]
    assert term["name"] == "SWIGGY · Mumbai"
    assert term["revaluation_minor"] == 180  # ₹ 1,800 x (0.0160 - 0.0150)
    assert term["spread_minor"] == -60       # S$ 9.00 - ₹ 600 x 0.0160
    books = {s["key"]: s for s in sheet(conn, "2026-11")["expect"]["sections"]}
    [line] = [x for x in books["expected_back"]["lines"] if x["key"] == f"expectation:{swiggy}"]
    assert (line["open_minor"], line["sgd_minor"]) == (120000, 1920)


def test_a_rupee_item_with_no_rate_is_left_out_of_both_sides_and_said(conn):
    b, _ = september_book(conn)
    swiggy = mark(conn, row(conn, b, b.rupee_card, "2026-10-06", "SWIGGY · Mumbai", 240000, type_name="Dining"),
                  180000, names=(b.people["Jay"],))
    jay = row(conn, b, b.savings, "2026-10-12", "PAYNOW FROM JAY TAN", -900, flow="review")
    set_off(conn, jay, swiggy, 900, item_amount_minor=58065, rate="0.0155", rate_date="2026-09-30")
    shown = check(conn)
    ties(shown)
    assert shown["outside_minor"] == 900  # the S$ 9.00 came in against an item the check leaves out
    assert {"account_id": f"expectation:{swiggy}", "name": "SWIGGY · Mumbai", "why": "no rate"} in shown["left_out"]
    assert sheet(conn, "2026-10")["note"] and "SWIGGY · Mumbai" in sheet(conn, "2026-10")["note"]


# --- what crosses the check's edge ---------------------------------------------


def test_an_item_marked_on_a_card_left_out_of_the_check_comes_in_from_outside(conn):
    b, _ = september_book(conn)
    harbour = mark(conn, row(conn, b, b.everyday_card, "2026-10-02", "HARBOUR GRILL", 30000, type_name="Dining"),
                   20000, names=(b.people["Jay"],))
    shown = check(conn)
    ties(shown)
    assert shown["outside_minor"] == 20000 and shown["spending_minor"] == 0
    set_off(conn, row(conn, b, b.savings, "2026-10-10", "PAYNOW FROM JAY TAN 0412", -12000, flow="review"),
            harbour, 12000)
    ties(check(conn))
    let_go(conn, harbour, "mine", "2026-10-19", type_name="Dining")
    shown = check(conn)
    ties(shown)
    assert shown["spending_minor"] == 8000


def test_an_opening_figure_dated_in_the_month_is_left_out_as_a_first_figure_is(conn):
    from expect_fixtures import opening
    b, _ = september_book(conn)
    sam = opening(conn, b.people["Sam"], "2026-10-05", 80000)
    set_off(conn, row(conn, b, b.savings, "2026-10-20", "PAYNOW FROM SAM", -30000, flow="review"), sam, 30000)
    # Dev's figure is made and paid in full within the month: listed at neither end.
    dev = opening(conn, b.people["Dev"], "2026-10-06", 10000)
    set_off(conn, row(conn, b, b.savings, "2026-10-21", "PAYNOW FROM DEV", -10000, flow="review"), dev, 10000)
    shown = check(conn)
    ties(shown)
    assert shown["outside_minor"] == 40000  # S$ 300 and S$ 100 came in against figures the check leaves out
    assert {"account_id": f"expectation:{sam}", "name": "Opening figure · Sam",
            "why": "opening figure on 2026-10-05"} in shown["left_out"]
    # A month later the figure is held at both ends and in the check like any other item.
    anchor(conn, b.savings, "2026-10-31", 530000, source="supplied")
    set_off(conn, row(conn, b, b.savings, "2026-11-03", "PAYNOW FROM SAM", -20000, flow="review"), sam, 20000)
    november = check(conn, "2026-11")
    ties(november)
    assert november["outside_minor"] == 0


def test_the_company_crossing_stays_on_the_whole_amount(conn):
    b, _ = september_book(conn)
    supplier = row(conn, b, b.everyday_card, "2026-10-03", "SAMPLE SUPPLIER", 5000, type_name="Dining",
                   book_name="Moom")
    # No expectation can sit on a company-book row (case 9b), so the company's
    # line and the check's crossing read the row whole.
    assert expect_back.refusal_to_mark(conn, supplier, "back") == (
        "only Household spending can be expected back: this row is the company's cost")
    shown = check(conn)
    ties(shown)
    assert shown["outside_minor"] == 5000
    lines = {x["name"]: x for s in sheet(conn, "2026-10")["sections"] for x in s["lines"]}
    assert lines["Moom"]["made_of"]["paid_for_minor"] == 5000

