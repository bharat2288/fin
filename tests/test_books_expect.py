"""Books' three expect-back sections (personal-ledger 23, ticket 31; spec E5, D5).

Expected back, Waiting for its row and To pay back, as at a day, read through
expect_back.open_as_at: oldest first with age, 'old' past 90 days, a rupee
item in rupees with its S$ value beside at the latest saved rate, and no rate
said rather than guessed. balance_sheet.sheet() carries them under "expect",
in the total since the switch (tests/test_expect_switch.py). expect-books.js
draws them in 26's slot.

Seam 2 of the spec (the sheet as a function on synthetic rows, then
GET /api/balance-sheet once), with figures in and out. Every name and figure
is invented (the case list's specimens).
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

import balance_sheet
import books_expect
import expect_back
from expect_fixtures import Book, check_book, entry, mark, opening, row, set_off
from trend_fixtures import anchor, seed_rate

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "expect"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

OCT_19 = "2026-10-19"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@dataclass
class Case48:
    book: Book
    ids: dict


def case_48(conn, b: Book, *, rates: bool = True, tag: str = "") -> Case48:
    """Case 48's set, on the check book `b`: the AIRBNB fixture (S$ 580.00 back
    before October), cases 1, 2, 9, 9a, 10, 11, 17, 19, 40, 41 and 42. `tag`
    tells copies apart (case 55)."""
    p = b.people
    ids = {}

    def r(account, day, text, amount, **kw):
        return row(conn, b, account, day, text + tag, amount, **kw)

    # The AIRBNB fixture: 28 Jun, S$ 620.00 back from Lena, S$ 580.00 set off on 26 Sep.
    airbnb = r(b.rewards_card, "2026-06-28", "AIRBNB", 62000, type_name="Travel")
    ids["airbnb"] = mark(conn, airbnb, 62000, names=(p["Lena"],))
    set_off(conn, r(b.savings, "2026-09-26", "PAYNOW FROM LENA 2210", -58000, flow="review"), ids["airbnb"], 58000)
    # Case 1: HARBOUR GRILL, S$ 200.00 of S$ 300.00 back from Jay and Kim.
    harbour = r(b.rewards_card, "2026-10-02", "HARBOUR GRILL", 30000, type_name="Dining")
    ids["harbour"] = mark(conn, harbour, 20000, names=(p["Kim"], p["Jay"]))
    # Case 2: KLOOK TRAVEL, all of S$ 180.00 back from Kim.
    klook = r(b.everyday_card, "2026-09-28", "KLOOK TRAVEL", 18000, type_name="Entertainment")
    ids["klook"] = mark(conn, klook, 18000, names=(p["Kim"],), note="concert tickets")
    # Case 9: GOLDEN VILLAGE, S$ 64.00 of S$ 96.00 back from Jay and Lena.
    golden = r(b.rewards_card, "2026-09-21", "GOLDEN VILLAGE", 9600, type_name="Entertainment")
    ids["golden"] = mark(conn, golden, 6400, names=(p["Jay"], p["Lena"]))
    # Case 9a: SWIGGY, ₹ 1,800.00 of ₹ 2,400.00 back from Jay.
    swiggy = r(b.rupee_card, "2026-10-06", "SWIGGY · Mumbai", 240000, type_name="Dining")
    ids["swiggy"] = mark(conn, swiggy, 180000, names=(p["Jay"],))
    # Case 10: Jay's S$ 120.00 on HARBOUR.
    set_off(conn, r(b.savings, "2026-10-10", "PAYNOW FROM JAY TAN 0412", -12000, flow="review"), ids["harbour"], 12000)
    # Case 11: Kim's S$ 70.00, S$ 60.00 on HARBOUR and S$ 10.00 on KLOOK, one change.
    kim = r(b.savings, "2026-10-15", "PAYNOW FROM KIM L 7781", -7000, flow="review")
    with entry(conn, "Set off"):
        set_off(conn, kim, ids["harbour"], 6000)
        set_off(conn, kim, ids["klook"], 1000)
    # Case 17: Lena's S$ 100.00, waiting for its row.
    lena = r(b.savings, "2026-10-17", "PAYNOW FROM LENA 2210", -10000, flow="review")
    ids["waiting"] = set_off(conn, lena, None, 10000, person_id=p["Lena"], note="for the BBQ, when it is charged")
    # Case 19: PURPLE LOTUS, ₹ 6,000.00 back from Jay; his S$ 90.00 set off at 0.0155 as ₹ 5,806.45.
    lotus = r(b.rupee_card, "2026-09-04", "PURPLE LOTUS MUMBAI", 600000, type_name="Dining")
    ids["lotus"] = mark(conn, lotus, 600000, names=(p["Jay"],))
    set_off(conn, r(b.savings, "2026-09-22", "PAYNOW FROM JAY TAN", -9000, flow="review"), ids["lotus"], 9000,
            item_amount_minor=580645, rate="0.0155", rate_date="2026-09-30")
    # Case 40: Ravi, S$ 2,000.00 lent on 3 Jun, S$ 500.00 back on 15 Aug (converted).
    lent = r(b.savings, "2026-06-03", "PAYNOW TO RAVI", 200000, flow="movement", other_side=p["Ravi"])
    ids["ravi"] = mark(conn, lent, 200000, names=(p["Ravi"],), source="converted")
    back = r(b.savings, "2026-08-15", "PAYNOW FROM RAVI", -50000, flow="movement", other_side=p["Ravi"])
    set_off(conn, back, ids["ravi"], 50000, source="converted")
    # Case 41: Sam's opening figure, S$ 800.00 on 1 Jan.
    ids["sam"] = opening(conn, p["Sam"], "2026-01-01", 80000)
    # Case 42: Parents, S$ 5,000.00 in on 1 Mar to pay back, S$ 1,000.00 paid back on 1 Aug.
    loan = r(b.savings, "2026-03-01", "FROM PARENTS", -500000, flow="movement", other_side=p["Parents"])
    ids["parents"] = mark(conn, loan, 500000, names=(p["Parents"],), direction="pay", source="converted")
    repaid = r(b.savings, "2026-08-01", "TO PARENTS", 100000, flow="movement", other_side=p["Parents"])
    set_off(conn, repaid, ids["parents"], 100000, source="converted")
    if rates:
        seed_rate(conn, "INR/SGD", "2026-08-31", "0.0160")
        seed_rate(conn, "INR/SGD", "2026-09-30", "0.0155")
    return Case48(b, ids)


@pytest.fixture
def case48(conn):
    return case_48(conn, check_book(conn))


def by_key(shown: dict) -> dict:
    return {s["key"]: s for s in shown["sections"]}


def line(shown: dict, section: str, key: str) -> dict:
    [found] = [x for x in by_key(shown)[section]["lines"] if x["key"] == key]
    return found


# --- case 48: the three sections ----------------------------------------------


def test_case_48_expected_back_oldest_first_with_age_and_old(conn, case48):
    ids = case48.ids
    shown = books_expect.sections(conn, OCT_19)
    assert shown["as_at"] == OCT_19
    assert shown["rate"] == {"text": "0.0155", "date": "2026-09-30"}
    back = by_key(shown)["expected_back"]
    assert back["heading"] == "Expected back" and back["owed"] is False
    assert back["total_minor"] == 262490  # S$ 2,624.90
    assert back["left_out"] == []
    got = [(x["key"], x["title"], x["date"], x["open_minor"], x["amount_minor"], x["currency"], x["sgd_minor"],
            x["age_days"], x["old"]) for x in back["lines"]]
    assert got == [
        (f"expectation:{ids['sam']}", "Opening figure · Sam", "2026-01-01", 80000, 80000, "SGD", 80000, 291, True),
        (f"expectation:{ids['ravi']}", "Loan to Ravi", "2026-06-03", 150000, 200000, "SGD", 150000, 138, True),
        (f"expectation:{ids['airbnb']}", "AIRBNB", "2026-06-28", 4000, 62000, "SGD", 4000, 113, True),
        (f"expectation:{ids['lotus']}", "PURPLE LOTUS MUMBAI", "2026-09-04", 19355, 600000, "INR", 300, 45, False),
        (f"expectation:{ids['golden']}", "GOLDEN VILLAGE", "2026-09-21", 6400, 6400, "SGD", 6400, 28, False),
        (f"expectation:{ids['klook']}", "KLOOK TRAVEL", "2026-09-28", 17000, 18000, "SGD", 17000, 21, False),
        (f"expectation:{ids['harbour']}", "HARBOUR GRILL", "2026-10-02", 2000, 20000, "SGD", 2000, 17, False),
        (f"expectation:{ids['swiggy']}", "SWIGGY · Mumbai", "2026-10-06", 180000, 180000, "INR", 2790, 13, False),
    ]
    assert line(shown, "expected_back", f"expectation:{ids['harbour']}")["names"] == ["Jay", "Kim"]
    assert line(shown, "expected_back", f"expectation:{ids['golden']}")["names"] == ["Jay", "Lena"]
    assert line(shown, "expected_back", f"expectation:{ids['airbnb']}")["last_set_off"] == "2026-09-26"
    assert line(shown, "expected_back", f"expectation:{ids['harbour']}")["last_set_off"] == "2026-10-15"
    assert line(shown, "expected_back", f"expectation:{ids['golden']}")["last_set_off"] is None


def test_case_48_waiting_and_to_pay_back(conn, case48):
    ids = case48.ids
    shown = books_expect.sections(conn, OCT_19)
    assert [s["key"] for s in shown["sections"]] == ["expected_back", "waiting", "to_pay_back"]
    waiting = by_key(shown)["waiting"]
    assert waiting["heading"] == "Waiting for its row" and waiting["owed"] is True
    assert waiting["total_minor"] == 10000
    [w] = waiting["lines"]
    assert w == {
        "key": f"waiting:{ids['waiting']}", "set_off_id": ids["waiting"], "title": "PAYNOW FROM LENA 2210",
        "date": "2026-10-17", "names": ["Lena"], "note": "for the BBQ, when it is charged",
        "open_minor": 10000, "amount_minor": 10000, "currency": "SGD", "sgd_minor": 10000, "rate": None,
        "no_rate": False, "age_days": 2, "old": False,
    }
    pay = by_key(shown)["to_pay_back"]
    assert pay["heading"] == "To pay back" and pay["owed"] is True
    # Case 18 (Books half): S$ 4,000.00, 232 d.
    assert pay["total_minor"] == 400000
    [p] = pay["lines"]
    assert (p["key"], p["title"], p["open_minor"], p["amount_minor"], p["age_days"], p["old"], p["last_set_off"]) == (
        f"expectation:{ids['parents']}", "From Parents", 400000, 500000, 232, True, "2026-08-01")


def test_case_9a_and_19_rupee_items_show_rupees_with_sgd_beside(conn, case48):
    ids = case48.ids
    shown = books_expect.sections(conn, OCT_19)
    swiggy = line(shown, "expected_back", f"expectation:{ids['swiggy']}")
    assert (swiggy["open_minor"], swiggy["currency"], swiggy["sgd_minor"]) == (180000, "INR", 2790)
    assert swiggy["rate"] == {"text": "0.0155", "date": "2026-09-30"} and swiggy["no_rate"] is False
    lotus = line(shown, "expected_back", f"expectation:{ids['lotus']}")
    assert (lotus["open_minor"], lotus["sgd_minor"]) == (19355, 300)  # ₹ 193.55, S$ 3.00 beside


def test_the_payload_has_the_shape_the_client_draws_from(conn, case48):
    shown = books_expect.sections(conn, OCT_19)
    pinned = fixture("books_sections")
    assert set(shown) == set(pinned)
    for got, want in zip(shown["sections"], pinned["sections"]):
        assert set(got) == set(want)
        for g, w in zip(got["lines"], want["lines"]):
            assert set(g) == set(w)


def test_once_through_the_route(client, conn, case48):
    body = client.get("/api/balance-sheet?month=2026-10").get_json()
    shown = body["expect"]
    totals = {s["key"]: (s["total_minor"], s["in_total"]) for s in shown["sections"]}
    assert totals == {"expected_back": (262490, True), "waiting": (10000, True), "to_pay_back": (400000, True)}
    assert body["month_check"]["month"] == "2026-10"


# --- case 49: no rate -----------------------------------------------------------


def test_case_49_a_rupee_item_with_no_rate_is_left_out_of_the_sgd_total_and_said(conn):
    b = check_book(conn)
    case = case_48(conn, b, rates=False)
    shown = books_expect.sections(conn, OCT_19)
    back = by_key(shown)["expected_back"]
    swiggy = line(shown, "expected_back", f"expectation:{case.ids['swiggy']}")
    assert (swiggy["open_minor"], swiggy["currency"], swiggy["sgd_minor"], swiggy["rate"], swiggy["no_rate"]) == (
        180000, "INR", None, None, True)
    assert shown["rate"] is None
    # Sam 800 + Ravi 1,500 + AIRBNB 40 + GOLDEN 64 + KLOOK 170 + HARBOUR 20; both rupee items left out.
    assert back["total_minor"] == 259400
    assert [(x["key"], x["title"], x["currency"], x["open_minor"], x["reason"]) for x in back["left_out"]] == [
        (f"expectation:{case.ids['lotus']}", "PURPLE LOTUS MUMBAI", "INR", 19355, "no rate"),
        (f"expectation:{case.ids['swiggy']}", "SWIGGY · Mumbai", "INR", 180000, "no rate"),
    ]


def test_a_rate_saved_after_the_day_is_no_rate_for_it(conn):
    b = check_book(conn)
    case = case_48(conn, b, rates=False)
    seed_rate(conn, "INR/SGD", "2026-10-31", "0.0150")
    swiggy = line(books_expect.sections(conn, OCT_19), "expected_back", f"expectation:{case.ids['swiggy']}")
    assert swiggy["no_rate"] is True and swiggy["sgd_minor"] is None


# --- case 17e: waiting across a month end --------------------------------------


def test_case_17e_waiting_across_a_month_end(conn):
    b = check_book(conn)
    lena = row(conn, b, b.savings, "2026-09-30", "PAYNOW FROM LENA 2210", -10000, flow="review")
    waiting = set_off(conn, lena, None, 10000, person_id=b.people["Lena"])
    september_before = books_expect.sections(conn, "2026-09-30")
    cold = row(conn, b, b.rewards_card, "2026-10-02", "COLD STORAGE", 26000, type_name="Groceries")
    with entry(conn, "Expect back and set Lena's S$ 100 against it"):
        expectation = mark(conn, cold, 10000, names=(b.people["Lena"],))
        conn.execute("UPDATE set_off SET expectation_id = ? WHERE id = ?", (expectation, waiting))
    september = books_expect.sections(conn, "2026-09-30")
    assert september == september_before
    assert [(s["key"], s["total_minor"]) for s in september["sections"]] == [("waiting", 10000)]
    assert by_key(september)["waiting"]["lines"][0]["key"] == f"waiting:{waiting}"
    october = books_expect.sections(conn, "2026-10-31")
    assert october["sections"] == []  # Waiting S$ 0; the expectation closed, so not listed
    assert expect_back.open_as_at(conn, "2026-10-31")["expectations"][0]["open_minor"] == 0


def test_nothing_held_draws_no_section(conn):
    check_book(conn)
    assert books_expect.sections(conn, OCT_19) == {"as_at": OCT_19, "rate": None, "sections": []}


def test_the_day_must_be_a_day(conn):
    with pytest.raises(ValueError):
        books_expect.sections(conn, "2026-10")


# --- case 55: one read each at ten times ----------------------------------------


def _statements(conn, on: str) -> int:
    seen = []
    conn.set_trace_callback(seen.append)
    try:
        books_expect.sections(conn, on)
    finally:
        conn.set_trace_callback(None)
    return len(seen)


def test_case_55_each_section_is_one_read_at_ten_times(conn):
    b = check_book(conn)
    case_48(conn, b)
    once = _statements(conn, OCT_19)
    for n in range(1, 10):
        case_48(conn, b, rates=False, tag=f" #{n}")
    tenfold = books_expect.sections(conn, OCT_19)
    assert by_key(tenfold)["expected_back"]["total_minor"] == 10 * 262490
    assert len(by_key(tenfold)["expected_back"]["lines"]) == 80
    assert _statements(conn, OCT_19) == once
    assert once <= 8


# --- the client: expect-books.js in 26's slot ----------------------------------


PRELUDE = r"""
globalThis.window = globalThis;
const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
const money = (minor, cur = 'SGD') => minor === null || minor === undefined ? '—' : `${cur} ${(minor / 100).toFixed(2)}`;
const day = iso => 'D' + String(iso);
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;
"""


def _draw(sheet: dict) -> str:
    js = "\n".join((ROOT / "static" / f"{s}.js").read_text(encoding="utf-8") for s in ("expect-mark", "expect-books"))
    code = PRELUDE + js + f"\nconsole.log(JSON.stringify(window.expectBooksSections({json.dumps(sheet)})));"
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_the_client_draws_the_three_sections_each_row_opening_its_page():
    html = _draw({"expect": fixture("books_sections")})
    for heading in ("Expected back", "Waiting for its row", "To pay back"):
        assert heading in html
    assert html.index("Expected back") < html.index("Waiting for its row") < html.index("To pay back")
    for expectation_id in (9, 5, 4, 6, 3, 2, 1, 7, 8):
        assert f'href="#/books/expectation/{expectation_id}"' in html
    assert "SGD 2624.90" in html and "SGD 100.00" in html and "SGD 4000.00" in html
    # A rupee item in rupees, its S$ beside.
    assert "INR 1800.00" in html and "SGD 27.90" in html and "INR 193.55" in html and "SGD 3.00" in html
    assert html.count("not in the total yet") == 3
    assert html.count("xb-age xb-old") == 4  # Sam, Ravi, AIRBNB, Parents
    assert "for the BBQ, when it is charged" in html
    assert "last paid back D2026-08-01" in html and "last back D2026-10-15" in html


@needs_node
def test_the_client_says_no_rate_and_what_the_total_leaves_out():
    html = _draw({"expect": fixture("books_sections_no_rate")})
    assert "no rate" in html
    assert "SGD 800.00" in html
    assert "1 item left out of the S$ total" in html


@needs_node
def test_the_client_draws_nothing_without_sections():
    assert _draw({}) == ""
    assert _draw({"expect": {"as_at": OCT_19, "rate": None, "sections": []}}) == ""
