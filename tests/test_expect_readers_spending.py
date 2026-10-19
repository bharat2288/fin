"""Readers A (personal-ledger 23, ticket 29): spending figures read each row's
own part plus the let-go terms (since the switch, ticket 45).

The readers: stat-cards (book sums, paid sums, the out/back sides and their
3-month averages), monthly, types, 15's trend series and its types-over-time
route, and the client's totalsByCurrency / groupRows, which sum each row's
counted_minor. The company book's paid-for sum on the balance sheet stays on the
whole amount, pinned here.

Figures in, figures out: records are seeded through tests/expect_fixtures.py
(inside open change-history entries, so an undo goes through
/api/history/<id>/undo as Changes sends it), and read back through the routes
and the public functions. Every name and figure is invented (the spec's case
list). Nothing opens the real database.
"""

from __future__ import annotations

import calendar
import datetime
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import expect_back
import money
import trend_series
import trend_types_api
from expect_fixtures import (
    entry, harbour_set_against, last_entry, let_go, mark, october, row, set_off,
)
from trend_fixtures import type_id

APP_JS = Path(__file__).resolve().parent.parent / "static" / "app.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

HOUSEHOLD = "Household"


@pytest.fixture
def story(conn):
    return october(conn)


@pytest.fixture(autouse=True)
def today_is_19_october(monkeypatch):
    monkeypatch.setattr(trend_types_api, "_today", lambda: datetime.date(2026, 10, 19))


# --- reading every reader --------------------------------------------------------


def _last_day(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def stat_cards(client, month: str) -> dict:
    resp = client.get(f"/api/dashboard/stat-cards?ref_month={month}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def monthly(client, month: str, book: str = HOUSEHOLD) -> dict:
    """The month's type -> net, in minor units, from /api/dashboard/monthly."""
    got = client.get(f"/api/dashboard/monthly?start={month}-01&end={_last_day(month)}&book={book}").get_json()
    return {name: money.to_minor(total) for name, total in got.get(month, {}).items()}


def types(client, month: str, book: str = HOUSEHOLD) -> dict:
    """The month's type -> net, in minor units, from /api/dashboard/types."""
    got = client.get(f"/api/dashboard/types?start={month}-01&end={_last_day(month)}&book={book}").get_json()
    return {r["type"]: money.to_minor(r["total"]) for r in got}


def series(conn, month: str, currency: str = "SGD", book: str = HOUSEHOLD) -> dict:
    """15's net series for the book's types in one month: type -> net."""
    got = trend_series.series(conn, ("book", book), month, month, "type")["series"][month]
    return {key: cells[currency]["net"] for key, cells in got.get(book, {}).items() if currency in cells}


def trend_types(client, month: str, book: str = HOUSEHOLD) -> dict:
    """/api/trend/types: each type's S$ point in `month` (6M ending there)."""
    got = client.get(f"/api/trend/types?book={book}&span=6M&to={month}").get_json()
    i = got["months"].index(month)
    return {t["name"]: t["points"][i]["net"] for t in got["types"] if t["points"][i]["net"]}


def every_reader(client, conn, month: str) -> dict:
    return {"monthly": monthly(client, month), "types": types(client, month),
            "series": series(conn, month), "trend": trend_types(client, month)}


# --- the client sum under node ---------------------------------------------------

HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const loud = name => new Proxy(function () {}, {
  get: (t, k) => k === Symbol.toPrimitive ? () => '' : (k === 'length' ? 0 : loud(name + '.' + String(k))),
  apply: () => loud(name + '()'), construct: () => loud('new ' + name),
});
const doc = {
  addEventListener() {}, querySelectorAll: () => [], documentElement: {},
  querySelector: () => null, getElementById: () => null, createElement: () => loud('el'),
};
const ctx = {
  console, document: doc, Date, Intl, URLSearchParams, FormData: class {}, setTimeout, innerWidth: 1366,
  localStorage: { getItem: () => null, setItem() {} }, location: { hash: '' },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  getComputedStyle: () => ({ getPropertyValue: () => '' }), addEventListener() {}, scrollTo() {},
  fetch: () => Promise.reject(new Error('no server in this test')),
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(APP_JS, 'utf8'), ctx, { filename: 'app.js' });
process.stdout.write(JSON.stringify(vm.runInContext(EXPR, ctx)));
"""


def client_js(expr: str):
    code = f"const APP_JS = {json.dumps(str(APP_JS))};\nconst EXPR = {json.dumps(expr)};\n" + HARNESS
    done = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def payload_rows(conn, row_ids) -> list[dict]:
    """Rows as /api/transactions carries them: the amount, and
    counted_minor, which the server sets to the own part (row_parts, the
    formula's one home)."""
    parts = expect_back.parts(conn, row_ids)
    out = []
    for row_id in row_ids:
        currency, description = conn.execute(
            "SELECT COALESCE(a.currency, 'SGD'), t.description FROM transactions t "
            "JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id WHERE t.id = ?",
            (row_id,)).fetchone()
        p = parts[row_id]
        out.append({"id": row_id, "description": description, "currency": currency,
                    "amount_sgd": money.from_minor(p["amount_minor"], currency),
                    "counted_minor": p["own_minor"]})
    return out


@needs_node
def test_the_client_sums_counted_minor_and_falls_back_to_the_amount():
    rows = ("[{amount_sgd: 300.0, currency: 'SGD', counted_minor: 10000},"
            " {amount_sgd: -30.0, currency: 'SGD', counted_minor: 0},"
            " {amount_sgd: 2400.0, currency: 'INR', counted_minor: 60000}]")
    assert client_js(f"totalsByCurrency({rows})") == {"SGD": 10000, "INR": 60000}
    # A payload without counted_minor (before the list carries it) counts the amount, as today.
    assert client_js("totalsByCurrency([{amount_sgd: '300.00', currency: 'SGD'}, {amount_sgd: '-30.00'}])") == {"SGD": 27000}
    groups = client_js(
        "groupRows([{k: 'Dining', amount_sgd: 300.0, currency: 'SGD', counted_minor: 10000},"
        " {k: 'Entertainment', amount_sgd: 180.0, currency: 'SGD', counted_minor: 0},"
        " {k: 'Entertainment', amount_sgd: 96.0, currency: 'SGD', counted_minor: 3200}], r => r.k)"
        ".map(g => ({name: g.name, by: g.by, sgd: g.sgd}))")
    assert groups == [{"name": "Dining", "by": {"SGD": 10000}, "sgd": 10000},
                      {"name": "Entertainment", "by": {"SGD": 3200}, "sgd": 3200}]


# --- the own part ----------------------------------------------------------------


def test_case_1_part_of_a_dinner_counts_its_own_part_on_every_reader(conn, client, story):
    mark(conn, story.harbour, 20000, names=(story.people["Jay"], story.people["Kim"]))
    readers = every_reader(client, conn, "2026-10")
    assert readers == {"monthly": {"Dining": 10000}, "types": {"Dining": 10000},
                       "series": {"Dining": 10000}, "trend": {"Dining": 10000}}
    cards = stat_cards(client, "2026-10")
    assert money.to_minor(cards["household"]) == 10000
    assert money.to_minor(cards["household_out"]) == 10000
    assert money.to_minor(cards["household_back"]) == 0
    assert cards["household_out_rows"] == 1
    # The client's sum over the same row, counted_minor as the server sets it.
    if NODE:
        rows = payload_rows(conn, [story.harbour])
        assert client_js(f"totalsByCurrency({json.dumps(rows)})") == {"SGD": 10000}


def test_case_2_all_of_it_takes_the_row_out_of_september(conn, client, story):
    before = monthly(client, "2026-09")["Entertainment"]
    mark(conn, story.klook, 18000, names=(story.people["Kim"],), note="concert tickets")
    after = every_reader(client, conn, "2026-09")
    assert before - after["monthly"]["Entertainment"] == 18000
    assert after["types"]["Entertainment"] == after["series"]["Entertainment"] == 9600
    assert after["trend"]["Entertainment"] == 9600
    cards = stat_cards(client, "2026-09")
    # KLOOK is wholly expected back: its own part S$ 0 is neither out nor back.
    assert money.to_minor(cards["household"]) == 9600
    assert cards["household_out_rows"] == 1


def test_case_9_marking_an_old_row_changes_its_own_month(conn, client, story):
    mark(conn, story.golden, 6400, names=(story.people["Jay"], story.people["Lena"]))
    # September Entertainment: KLOOK S$ 180.00 + GOLDEN VILLAGE's own S$ 32.00.
    readers = every_reader(client, conn, "2026-09")
    assert {r: v["Entertainment"] for r, v in readers.items()} == dict.fromkeys(readers, 21200)
    assert "Entertainment" not in monthly(client, "2026-10")


def test_case_9a_a_rupee_row_counts_its_rupee_own_part(conn, client, story):
    mark(conn, story.swiggy, 180000, names=(story.people["Jay"],))
    assert series(conn, "2026-10", "INR") == {"Dining": 60000}
    # S$ figures leave rupee rows out, as before: Dining in S$ is HARBOUR alone.
    assert monthly(client, "2026-10") == {"Dining": 30000}
    # Types over time values rupees at the month-end rate; October has none, so
    # the own part is named as left out, never the whole ₹ 2,400.00.
    got = client.get("/api/trend/types?book=Household&span=6M&to=2026-10").get_json()
    assert [(x["currency"], x["month"], x["amount_minor"]) for x in got["left_out"]] == [("INR", "2026-10", 60000)]


def test_case_22_undo_the_mark_puts_september_back(conn, client, story):
    mark(conn, story.klook, 18000, names=(story.people["Kim"],))
    assert monthly(client, "2026-09")["Entertainment"] == 9600
    assert client.post(f"/api/history/{last_entry(conn)}/undo").status_code == 200
    readers = every_reader(client, conn, "2026-09")
    assert {r: v["Entertainment"] for r, v in readers.items()} == dict.fromkeys(readers, 27600)


# --- the let-go terms -------------------------------------------------------------


def test_case_24_a_gift_counts_in_gifts_on_its_own_day_and_dining_stays(conn, client, story):
    o = harbour_set_against(conn, story)
    let_go(conn, o["harbour"], "gift", "2026-10-19", to_whom_id=story.people["Kim"])
    readers = every_reader(client, conn, "2026-10")
    for name, got in readers.items():
        assert got == {"Dining": 10000, "Gifts & Donations": 2000}, name
    cards = stat_cards(client, "2026-10")
    assert money.to_minor(cards["household"]) == 12000
    assert money.to_minor(cards["household_out"]) == 12000


def test_case_25_mine_after_all_counts_in_a_later_month_and_september_stays(conn, client, story,
                                                                            monkeypatch):
    golden = mark(conn, story.golden, 6400, names=(story.people["Jay"], story.people["Lena"]))
    let_go(conn, golden, "mine", "2026-11-03", type_name="Entertainment")
    monkeypatch.setattr(trend_types_api, "_today", lambda: datetime.date(2026, 11, 19))
    assert monthly(client, "2026-09")["Entertainment"] == 18000 + 3200
    november = every_reader(client, conn, "2026-11")
    assert {r: v.get("Entertainment") for r, v in november.items()} == dict.fromkeys(november, 6400)
    assert money.to_minor(stat_cards(client, "2026-11")["household"]) == 6400


def test_case_26_undo_the_let_go_takes_gifts_back_off(conn, client, story):
    o = harbour_set_against(conn, story)
    let_go(conn, o["harbour"], "gift", "2026-10-19", to_whom_id=story.people["Kim"])
    assert types(client, "2026-10")["Gifts & Donations"] == 2000
    assert client.post(f"/api/history/{last_entry(conn)}/undo").status_code == 200
    readers = every_reader(client, conn, "2026-10")
    for name, got in readers.items():
        assert got == {"Dining": 10000}, name


def test_case_26d_a_to_pay_back_let_go_is_never_spending(conn, client, story):
    b = story.book
    parents = row(conn, b, b.savings, "2026-03-01", "FROM PARENTS", -500000, flow="income")
    owed = mark(conn, parents, 500000, names=(story.people["Parents"],))
    gift_in = conn.execute("SELECT id FROM types WHERE kind = 'income' AND name = 'Gift received'").fetchone()[0]
    with entry(conn, "Let it go"):
        conn.execute(
            "INSERT INTO let_go (expectation_id, amount_minor, became, book, type_id, counts_on) "
            "VALUES (?, 500000, 'gift', 'Household', ?, '2026-10-19')", (owed, gift_in))
    readers = every_reader(client, conn, "2026-10")
    for name, got in readers.items():
        assert got == {"Dining": 30000}, name
    assert money.to_minor(stat_cards(client, "2026-10")["household"]) == 30000


# --- a refund that closes an expectation ---------------------------------------------


def test_case_36_and_36a_a_closing_refund_nets_nowhere_and_unlinked_nets_its_month(conn, client, story):
    b = story.book
    klook = mark(conn, story.klook, 18000, names=(story.people["Kim"],))
    set_off(conn, story.kim_7781, klook, 1000)
    refund = row(conn, b, b.everyday_card, "2026-10-04", "KLOOK TRAVEL REFUND", -3000,
                 flow="refund", type_name="Entertainment")
    with entry(conn, "Linked a refund"):
        conn.execute("INSERT INTO refund_link (refund_row_id, purchase_row_id, source) VALUES (?, ?, 'auto')",
                     (refund, story.klook))
        conn.execute("INSERT INTO set_off (payment_row_id, expectation_id, amount_minor, item_amount_minor, source)"
                     " VALUES (?, ?, 3000, 3000, 'refund')", (refund, klook))
    readers = every_reader(client, conn, "2026-10")
    for name, got in readers.items():
        assert got == {"Dining": 30000}, name          # no Entertainment netting in October
    assert money.to_minor(stat_cards(client, "2026-10")["household_back"]) == 0

    # Unlink: the link and its set-off go in one change; the refund nets −S$ 30.00.
    with entry(conn, "Unlinked a refund"):
        conn.execute("DELETE FROM set_off WHERE payment_row_id = ?", (refund,))
        conn.execute("DELETE FROM refund_link WHERE id = (SELECT id FROM refund_link WHERE refund_row_id = ?)",
                     (refund,))
    readers = every_reader(client, conn, "2026-10")
    for name, got in readers.items():
        assert got == {"Dining": 30000, "Entertainment": -3000}, name
    cards = stat_cards(client, "2026-10")
    assert money.to_minor(cards["household_back"]) == 3000
    assert cards["household_back_rows"] == 1


# --- case 51: one figure in every reader ------------------------------------------------------


def _own_parts_in(conn, month: str) -> tuple[int, list[int]]:
    """Σ own parts of Household S$ spending rows dated in `month`, and their ids."""
    ids = [r[0] for r in conn.execute(
        "SELECT t.id FROM transactions t JOIN statements s ON s.id = t.statement_id "
        "JOIN accounts a ON a.id = s.account_id "
        "WHERE t.flow_type IN ('expense', 'refund') AND COALESCE(t.book, 'Household') = 'Household' "
        "AND a.owner = 'Household' AND COALESCE(a.currency, 'SGD') = 'SGD' AND strftime('%Y-%m', t.date) = ?",
        (month,))]
    return sum(p["own_minor"] for p in expect_back.parts(conn, ids).values()), ids


def test_case_51_october_household_spending_is_one_figure(conn, client, story):
    b = story.book
    o = harbour_set_against(conn, story)                                   # cases 1, 10, 11
    mark(conn, story.swiggy, 180000, names=(story.people["Jay"],))         # 9a: rupees, outside S$
    let_go(conn, o["harbour"], "gift", "2026-10-19", to_whom_id=story.people["Kim"])   # 24
    refund = row(conn, b, b.everyday_card, "2026-10-04", "KLOOK TRAVEL REFUND", -3000,
                 flow="refund", type_name="Entertainment")
    set_off(conn, refund, o["klook"], 3000, source="refund")              # 36
    row(conn, b, b.everyday_card, "2026-10-09", "FRESH MART", 4550, type_name="Groceries")

    own, ids = _own_parts_in(conn, "2026-10")
    let_gos = conn.execute("SELECT SUM(amount_minor) FROM let_go WHERE counts_on LIKE '2026-10-%'").fetchone()[0]
    expected = own + let_gos
    assert expected == 10000 + 0 + 4550 + 2000

    cards = stat_cards(client, "2026-10")
    readers = every_reader(client, conn, "2026-10")
    figures = {
        "stat-cards": money.to_minor(cards["household"]),
        "stat-cards out-back": money.to_minor(cards["household_out"]) - money.to_minor(cards["household_back"]),
        **{name: sum(got.values()) for name, got in readers.items()},
    }
    assert figures == dict.fromkeys(figures, expected)
    if NODE:
        # The client sums the rows' counted_minor: the own-part half (let-gos are no row in the list).
        rows = payload_rows(conn, ids)
        assert client_js(f"totalsByCurrency({json.dumps(rows)})") == {"SGD": own}


def test_the_three_month_averages_read_own_parts(conn, client, story):
    mark(conn, story.klook, 18000, names=(story.people["Kim"],))
    cards = stat_cards(client, "2026-12")      # averages over Sep, Oct, Nov
    # Sep: GOLDEN 96.00 (KLOOK wholly back); Oct: HARBOUR 300.00; Nov: nothing.
    months_with_rows = 2
    assert money.to_minor(cards["avg_household"]) == money.mean_minor(9600 + 30000, months_with_rows)
    assert money.to_minor(cards["avg_household_out"]) == money.mean_minor(9600 + 30000, months_with_rows)


# --- the pin: the company book's paid-for sum stays whole -------------------------------------------


def _moom_line(client) -> dict:
    shown = client.get("/api/balance-sheet?month=2026-10").get_json()
    return next(line for s in shown["sections"] for line in s["lines"] if line["name"] == "Moom")


def test_pin_the_company_paid_for_sum_stays_on_the_whole_amount(conn, client, story, monkeypatch):
    before = _moom_line(client)
    # No writer can put an expectation on a company-book row (case 9b); one
    # written straight in shows the sheet's paid-for sum never reads own parts.
    assert expect_back.refusal_to_mark(conn, story.company_row, "back")
    with entry(conn, "Forced"):
        conn.execute("INSERT INTO expectation (row_id, direction, amount_minor, currency) VALUES (?, 'back', 2000, 'SGD')",
                     (story.company_row,))
    after = _moom_line(client)
    assert after == before
    assert after["made_of"]["paid_for_minor"] == 5000
