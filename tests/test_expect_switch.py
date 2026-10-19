"""The switch (personal-ledger 23, ticket 45; spec, The conversion step 2).

Expect back's gate goes: every reader, the payload and the month check read a
row's own part and the let-go terms; Expected back (an asset), Waiting for
its row and To pay back (owed) are in net worth; and persons leave the
balance sheet's counterparty lines, so a person's money is the expectations
named for them, never a balance. Net worth on every month end is unchanged by
the switch on a converted book, except by the money left on converted rows to
label (case 43), asserted exactly.

Seam 2 (the sheet and the month check as functions) on the converted book of
seam 3 (the conversion step on a database under tmp_path), and the account
page's person branch under node. Every name and figure is invented.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

import balance_sheet
import convert_expect_back
import month_check
from expect_fixtures import check_book, entry
from test_books_expect import case_48
from test_convert_expect_back import book, open_book, run, seeded  # noqa: F401  (book is a fixture)
from trend_fixtures import anchor

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

TODAY = date(2026, 10, 19)
MONTHS = [f"2026-{m:02d}" for m in range(1, 11)]


def sheet(conn, month: str) -> dict:
    return balance_sheet.sheet(conn, month, today=TODAY)


def lines_of(shown: dict) -> list[dict]:
    return [line for section in shown["sections"] for line in section["lines"]]


def old_person_lines(conn, month: str) -> int:
    """What the persons' lines added to net worth before the switch: each
    person's old line (the conversion's own read since the contract). Every
    person here is kept in S$, so each line was in the total at its face."""
    as_at = balance_sheet.month_end(month).isoformat()
    persons = conn.execute(
        "SELECT id, name, type, currency, status FROM accounts WHERE type = 'person' AND owner = 'Household'"
    ).fetchall()
    assert {p["currency"] or "SGD" for p in persons} <= {"SGD"}
    return sum(convert_expect_back.old_line(conn, p, as_at) for p in persons)


# --- case 40 and 43: net worth across the switch ------------------------------------


def test_case_40_and_43_net_worth_moves_only_by_the_money_left_to_label(book, capsys):
    ids = seeded(book)
    conn = open_book(book)
    try:
        anchor(conn, ids["savings"], "2026-01-01", 1000000, source="supplied")
        # Before the conversion, net worth as the sheet gave it with the persons' lines in it.
        before = {m: sheet(conn, m)["net_worth_minor"] + old_person_lines(conn, m) for m in MONTHS}
    finally:
        conn.close()

    code, out = run(book, capsys)
    assert code == 0, out

    conn = open_book(book)
    try:
        after = {m: sheet(conn, m) for m in MONTHS}
        unplaced = -sum(r[0] for r in conn.execute(
            "SELECT rp.own_minor FROM row_parts rp JOIN transactions t ON t.id = rp.row_id "
            "JOIN accounts p ON p.id = t.other_side_id WHERE p.type = 'person' AND t.flow_type = 'movement' "
            "AND t.id IN (SELECT payment_row_id FROM set_off UNION SELECT row_id FROM expectation)"))
    finally:
        conn.close()

    # Case 43: Dev's S$ 100.00 left on his 1 Jun row is the one declared change.
    assert unplaced == 10000
    moved = {m: after[m]["net_worth_minor"] - before[m] for m in MONTHS}
    assert moved == {m: (unplaced if m >= "2026-06" else 0) for m in MONTHS}

    # Case 40: Ravi's person line goes; Expected back holds his S$ 1,500.00 and is in the total.
    october = after["2026-10"]
    assert not [x for x in lines_of(october) if x.get("kind") == "person"]
    assert "Ravi" not in {x["name"] for x in lines_of(october)}
    back = {s["key"]: s for s in october["expect"]["sections"]}["expected_back"]
    assert back["in_total"] is True
    assert [x["open_minor"] for x in back["lines"] if x["title"] == "Loan to Ravi"] == [150000]


def test_persons_leave_the_counterparty_lines_and_companies_stay(conn):
    b = check_book(conn)
    anchor(conn, b.people["Ravi"], "2026-01-01", 80000, source="supplied")
    anchor(conn, b.company, "2026-01-01", 1500000, source="supplied")
    shown = sheet(conn, "2026-10")
    [companies] = [s for s in shown["sections"] if s["name"] == "companies"]
    assert [(x["name"], x["kind"]) for x in companies["lines"]] == [("Moom", "company")]
    assert companies["heading"] == "Companies (our money in them)"
    # Ravi's S$ 800.00 figure no longer counts: Moom's S$ 15,000.00 is all there is.
    assert shown["net_worth_minor"] == 1500000


# --- case 48 (total) --------------------------------------------------------------------


def test_case_48_the_three_sections_count_in_net_worth(conn):
    b = check_book(conn)
    anchor(conn, b.savings, "2026-01-01", 1000000, source="supplied")
    case_48(conn, b)
    after = sheet(conn, "2026-10")
    with entry(conn, "Every record taken away, to compare"):
        for table in ("set_off", "let_go", "expectation_name", "expectation"):
            conn.execute(f"DELETE FROM {table}")
    before = sheet(conn, "2026-10")

    totals = {s["key"]: (s["total_minor"], s["in_total"]) for s in after["expect"]["sections"]}
    assert totals == {"expected_back": (262490, True), "waiting": (10000, True), "to_pay_back": (400000, True)}
    # S$ 2,624.90 is ours; S$ 100.00 and S$ 4,000.00 are owed.
    assert after["net_worth_minor"] - before["net_worth_minor"] == 262490 - 10000 - 400000
    assert after["left_out"] == before["left_out"]


# --- case 17e: a checked month never changes --------------------------------------------


def test_case_17e_septembers_checked_net_worth_stays_when_the_row_arrives(conn):
    from expect_fixtures import mark, row, set_off
    b = check_book(conn)
    anchor(conn, b.rewards_card, "2026-08-31", -100000, source="supplied")
    anchor(conn, b.savings, "2026-08-31", 500000, source="supplied")
    lena = row(conn, b, b.savings, "2026-09-30", "PAYNOW FROM LENA 2210", -10000, flow="review")
    waiting = set_off(conn, lena, None, 10000, person_id=b.people["Lena"])
    september = month_check.check(conn, "2026-09", sheet(conn, "2026-09"))
    # Waiting for its row is owed: the S$ 100.00 in is not more net worth.
    assert september["actual_minor"] == 500000 - 100000
    cold = row(conn, b, b.rewards_card, "2026-10-02", "COLD STORAGE", 26000, type_name="Groceries")
    with entry(conn, "Expect back and set Lena's S$ 100 against it"):
        expectation = mark(conn, cold, 10000, names=(b.people["Lena"],))
        conn.execute("UPDATE set_off SET expectation_id = ? WHERE id = ?", (expectation, waiting))
    again = month_check.check(conn, "2026-09", sheet(conn, "2026-09"))
    assert again["actual_minor"] == september["actual_minor"]
    assert again["unexplained_minor"] == september["unexplained_minor"] == 0


# --- the account page: a person is their expectations ---------------------------------


def app_function(name: str) -> str:
    import re
    m = re.search(rf"^(?:async )?function {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found"
    return m.group(0)


def run_node(code: str):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


PRELUDE = r"""
globalThis.window = globalThis;
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;');
const icon = n => `<i ${n}></i>`;
const money = (minor, cur = 'SGD') => `${cur} ${(minor / 100).toFixed(2)}`;
const day = iso => String(iso);
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const booksNav = () => '<nav></nav>';
const accountMark = () => '';
const monthPicker = () => '';
const KIND_WORDS = { person: 'Person' };
let mounted = 0;
window.mountAccountTrend = () => { mounted += 1; };
const afterRender = fn => fn();
const asked = [];
"""

RAVI = {"id": 41, "name": "Ravi", "type": "person", "owner": "Household", "currency": None, "status": "active",
        "takes_a_figure": True}
OPEN = {"direction": None, "near": None, "payment": None, "items": [
    {"id": 7, "direction": "back", "amount_minor": 200000, "currency": "SGD", "day": "2026-06-03",
     "names": [{"account_id": 41, "name": "Ravi"}], "set_off_minor": 50000, "let_go_minor": 0,
     "open_minor": 150000, "state": "partly_back",
     "row": {"id": 301, "date": "2026-06-03", "description": "TRANSFER TO RAVI"}, "opening": None},
    {"id": 8, "direction": "pay", "amount_minor": 30000, "currency": "SGD", "day": "2026-09-01",
     "names": [{"account_id": 41, "name": "Ravi"}], "set_off_minor": 0, "let_go_minor": 0,
     "open_minor": 30000, "state": "open", "row": None,
     "opening": {"account_id": 41, "name": "Ravi", "date": "2026-09-01"}},
]}


def person_page(open_list: dict) -> str:
    code = "\n".join([PRELUDE, app_function("personAccountPage"), f"""
    const get = async url => {{ asked.push(url); return {json.dumps(open_list)}; }};
    personAccountPage({json.dumps(RAVI)}, '<li>figure</li>', 1)
        .then(html => console.log(JSON.stringify({{ html, asked, mounted }})));"""])
    return run_node(code)


@needs_node
def test_a_person_accounts_page_draws_their_expectations_not_a_balance():
    got = person_page(OPEN)
    html = got["html"]
    # The open list for this person, read once; no balance trend is mounted.
    assert got["asked"] == ["/api/expectations?person=41"]
    assert got["mounted"] == 0
    assert "Balance at" not in html
    assert 'href="#/books/expectation/7"' in html and "TRANSFER TO RAVI" in html
    assert "SGD 1500.00 of SGD 2000.00" in html and "expected back" in html
    assert 'href="#/books/expectation/8"' in html and "Opening figure" in html and "to pay back" in html
    # The figures stay listed, so one can still be corrected.
    assert "<li>figure</li>" in html


@needs_node
def test_a_person_with_nothing_open_says_so():
    html = person_page({**OPEN, "items": []})["html"]
    assert "Nothing is open with Ravi" in html
