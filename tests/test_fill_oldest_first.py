"""Personal-ledger 36: the Back for… drawer's pure helpers (D2), under node.

The drawer's arithmetic is the operator's to see, not to trust blindly: ticks fill
oldest first, each capped by what is open and by what the payment has left; the
running line says what is placed and what stays; Set off is refused while over;
"Expect to pay this back instead…" waits until the ticks are cleared. Every figure
comes from the route's payload; these helpers only add up what the operator typed.

static/expect-setoff.js is run whole (after expect-mark.js, which holds the shared
namespace) with app.js's money and day helpers lifted out of the real file, the
script passed to node on stdin.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def app_part(pattern: str) -> str:
    m = re.search(pattern, APP_JS, re.M | re.S)
    assert m, pattern
    return m.group(0)


def app_function(name: str) -> str:
    return app_part(rf"^function {name}\(.*?^\}}$")


LIFTED = "\n".join([
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
    "const X = window.Expect.setoff;",
])


def run(expr: str):
    code = LIFTED + f"\nconsole.log(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


HARBOUR = {"id": 1, "day": "2026-10-02", "open_minor": 8000, "amount_minor": 20000, "currency": "SGD"}
KLOOK = {"id": 2, "day": "2026-09-28", "open_minor": 18000, "amount_minor": 18000, "currency": "SGD"}
AIRBNB = {"id": 4, "day": "2026-06-28", "open_minor": 4000, "amount_minor": 62000, "currency": "SGD"}
SWIGGY = {"id": 6, "day": "2026-10-06", "open_minor": 180000, "amount_minor": 180000, "currency": "INR"}


def test_case_11_ticks_fill_oldest_first_capped_by_what_the_payment_has_left():
    # Kim's S$ 70 with HARBOUR (S$ 80 open, 2 Oct) and KLOOK (S$ 180 open, 28 Sep) ticked
    assert run(f"X.fillOldestFirst({json.dumps([HARBOUR, KLOOK])}, 7000, 'SGD')") == {"1": 0, "2": 7000}


def test_case_10_one_tick_takes_the_payment_up_to_what_is_open():
    harbour = {**HARBOUR, "open_minor": 20000}
    assert run(f"X.fillOldestFirst({json.dumps([harbour])}, 12000, 'SGD')") == {"1": 12000}


def test_case_13_a_tick_takes_no_more_than_is_open():
    assert run(f"X.fillOldestFirst({json.dumps([AIRBNB])}, 6000, 'SGD')") == {"4": 4000}


def test_an_item_in_another_currency_is_not_filled():
    # the rate step (ticket 39) fills across currencies; until then it stays at nothing
    assert run(f"X.fillOldestFirst({json.dumps([SWIGGY, HARBOUR])}, 12000, 'SGD')") == {"6": 0, "1": 8000}


def test_a_payment_with_nothing_left_fills_nothing():
    assert run(f"X.fillOldestFirst({json.dumps([HARBOUR, KLOOK])}, 0, 'SGD')") == {"1": 0, "2": 0}


@pytest.mark.parametrize("placed, left, words, can", [
    (12000, 12000, "S$ 120 of S$ 120 placed", True),                                          # case 10
    (4000, 6000, "S$ 40 of S$ 60 placed · S$ 20 stays on this payment, to label", True),     # case 13
    (18400, 12000, "S$ 184 of S$ 120 placed · S$ 64 more than the payment", False),          # case 14
    (0, 0, "S$ 0 left on this payment", False),                                               # case 16
    (0, 12000, "S$ 0 of S$ 120 placed · S$ 120 stays on this payment, to label", False),
    (100000, 100000, "S$ 1,000 of S$ 1,000 placed", True),
])
def test_the_running_placed_line(placed, left, words, can):
    assert run(f"X.placedLine({placed}, {left}, 'SGD')") == {"text": words, "over": placed > left, "can": can}


def test_case_18a_expect_to_pay_back_instead_waits_for_the_ticks_to_clear():
    assert run("[X.insteadLink(0, -500000), X.insteadLink(1, -500000), X.insteadLink(0, 100000)]") == [
        {"disabled": False, "hint": ""},
        {"disabled": True, "hint": "Clear the ticks first"},
        None,  # money out has no "expect to pay back" of its own
    ]


def test_item_titles_and_what_is_open():
    loan = {"row": {"description": "PAYNOW TO RAVI"}, "opening": None, "source": "converted", "direction": "back",
            "names": [{"account_id": 5, "name": "Ravi"}], "open_minor": 150000, "amount_minor": 200000, "currency": "SGD"}
    owed = {**loan, "direction": "pay", "names": [{"account_id": 6, "name": "Parents"}]}
    opening = {**loan, "row": None, "opening": {"person": {"account_id": 7, "name": "Sam"}, "date": "2026-01-01"}}
    harbour = {**loan, "row": {"description": "HARBOUR GRILL"}, "source": "hand", "open_minor": 20000, "amount_minor": 20000}
    assert run(f"[{json.dumps(loan)}, {json.dumps(owed)}, {json.dumps(opening)}, {json.dumps(harbour)}].map(X.itemTitle)") == [
        "Loan to Ravi", "Loan from Parents", "Opening figure · Sam", "HARBOUR GRILL"]
    assert run(f"[{json.dumps(loan)}, {json.dumps(harbour)}].map(X.openWords)") == [
        "S$ 1,500 of S$ 2,000 open", "S$ 200 open"]


def test_the_done_toast_names_what_was_learnt_or_what_was_paid_back():
    learnt = {"set_offs": [{"amount_minor": 12000, "currency": "SGD", "date": "2026-10-10"}],
              "learnt": {"wording": "JAY TAN", "person": {"account_id": 1, "name": "Jay"}}}
    plain = {**learnt, "learnt": None}
    paid_back = {"set_offs": [{"amount_minor": 100000, "currency": "SGD", "date": "2026-08-01"}], "learnt": None}
    out = run(f"[X.doneToast({json.dumps(learnt)}, ['HARBOUR GRILL'], 'back'),"
              f" X.doneToast({json.dumps(plain)}, ['HARBOUR GRILL', 'KLOOK TRAVEL'], 'back'),"
              f" X.doneToast({json.dumps(paid_back)}, ['FROM PARENTS'], 'pay')]")
    assert out == [
        "Set off S$ 120 against HARBOUR GRILL. Next time 'JAY TAN' suggests Jay first.",
        "Set off S$ 120 against HARBOUR GRILL and KLOOK TRAVEL.",
        "S$ 1,000 paid back on 1 Aug",
    ]


@pytest.mark.parametrize("text, minor", [
    ("120", 12000), ("120.5", 12050), ("1,000.00", 100000), (" 60.00 ", 6000), ("0", 0),
    ("120.005", None), ("-5", None), ("lots", None), ("", None),
])
def test_an_amount_typed_is_read_as_whole_cents_or_not_at_all(text, minor):
    assert run(f"X.parseAmount({json.dumps(text)}, 'SGD')") == minor


def test_an_amount_is_sent_as_decimal_text():
    assert run("[X.amountText(12000, 'SGD'), X.amountText(5, 'SGD'), X.amountText(100000, 'SGD')]") == [
        "120.00", "0.05", "1000.00"]


def test_back_for_is_offered_on_money_in_and_on_a_movement_out_only():
    rows = [
        {"id": 201, "amount_sgd": -120.0, "flow_type": "review"},
        {"id": 202, "amount_sgd": 1000.0, "flow_type": "movement"},
        {"id": 101, "amount_sgd": 300.0, "flow_type": "expense"},
        {"id": 301, "amount_sgd": -400.0, "flow_type": "payment"},
        {"id": 302, "amount_sgd": -400.0, "flow_type": "transfer"},
        {"id": 1},
    ]
    out = run(f"{json.dumps(rows)}.map(r => window.expectRowActions(r))")
    assert 'data-act="back-for"' in out[0] and 'data-tx="201"' in out[0] and "Back for…" in out[0]
    assert 'data-act="back-for"' in out[1] and 'data-tx="202"' in out[1]
    # Other lanes fill the same slot (34 adds Expect some back… on money out), so only
    # Back for… is looked for here.
    assert all('data-act="back-for"' not in o for o in out[2:])


def test_the_drawer_and_its_entry_point_are_defined():
    typ, slots = run("[typeof window.openBackFor, Object.keys(window.Expect.slots)]")
    assert typ == "function" and "rowActions" in slots  # 34 fills other slots too
