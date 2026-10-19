"""Personal-ledger 30: readers B. The lists, the Queue and review predicates,
the row payload's parts and the pill (spec 23, E4; shared rule "accounted
for").

/api/transactions carries every row's amount, expected, placed and own part
(and counted_minor, the figure a client sum adds). Since the switch (ticket
45) the review list, the Queue's counts, the untyped and check-each-row
lists, the review guess tally, the merchants' rows to check and the
book-move count read the own part: a row whose own part is 0 is accounted
for and leaves them, its flow kept.

Figures in and out through the routes; the client's row renderers are lifted
out of static/app.js and run under node, the script on stdin. Every name and
figure is invented; the database is the temporary one conftest gives.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import db
from expect_fixtures import (harbour_set_against, mark, october, row, set_off, unlink)
from trend_fixtures import type_id

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

PARTS = ("amount_minor", "expected_minor", "placed_minor", "own_minor", "counted_minor")


@pytest.fixture
def story(conn):
    return october(conn)


def listed(client, query: str) -> dict[int, dict]:
    got = client.get(f"/api/transactions?per_page=500&{query}")
    assert got.status_code == 200, got.get_json()
    return {t["id"]: t for t in got.get_json()["transactions"]}


def parts_of(tx: dict) -> tuple:
    return tuple(tx[k] for k in PARTS)


def review_list(client) -> dict[int, dict]:
    """The Queue's transfers: the review list, as loadQueue reads it."""
    return listed(client, "flow=review&sort=amount&sort_dir=desc")


def waiting(client) -> dict:
    return client.get("/api/review").get_json()


def merchant(conn, name: str, type_name: str = "Dining", book: str = "Household", each: int = 0) -> int:
    svc = conn.execute("INSERT INTO services (name, book, type_id, review_each_time) VALUES (?, ?, ?, ?)",
                       (name, book, type_id(conn, type_name), each)).lastrowid
    conn.commit()
    return svc


def file_under(conn, row_id: int, service_id: int) -> None:
    conn.execute("UPDATE transactions SET service_id = ? WHERE id = ?", (service_id, row_id))
    conn.commit()


# --- 47 (route half): every row carries its parts -----------------------------------


def test_47_every_row_carries_its_amount_expected_placed_and_own_part(client, conn, story):
    harbour_set_against(conn, story)
    rows = listed(client, "")
    # HARBOUR GRILL S$ 300.00, S$ 200.00 expected back: own S$ 100.00.
    assert parts_of(rows[story.harbour]) == (30000, 20000, 0, 10000, 10000)
    # KLOOK all of it expected back: own S$ 0.
    assert parts_of(rows[story.klook]) == (18000, 18000, 0, 0, 0)
    # Jay's S$ 120.00 wholly placed: own S$ 0, with the row's sign.
    assert parts_of(rows[story.jay_0412]) == (-12000, 0, -12000, 0, 0)
    # A row with no record: its own part is its amount.
    assert parts_of(rows[story.golden]) == (9600, 0, 0, 9600, 9600)
    # The old key stays, in the account's own currency.
    assert rows[story.harbour]["amount_sgd"] == 300.0


def test_a_row_with_nothing_on_it_draws_no_pill(client, conn, story):
    harbour_set_against(conn, story)
    rows = listed(client, "")
    assert rows[story.golden]["expect_pill"] is None
    assert rows[story.golden]["counted_minor"] == rows[story.golden]["amount_minor"]


# --- case 1 and 2: the row reads its own part, with the pill ---------------------


def test_case_1_harbour_reads_its_own_part_with_the_pill_on_spending_account_rows_and_merchant_page(
        client, conn, story):
    jay, kim = story.people["Jay"], story.people["Kim"]
    file_under(conn, story.harbour, merchant(conn, "Harbour Grill"))
    mark(conn, story.harbour, 20000, names=(jay, kim))
    svc = conn.execute("SELECT service_id FROM transactions WHERE id = ?", (story.harbour,)).fetchone()[0]
    for query in ("book=Household&expense_only=true&month=2026-10",          # Spending
                  f"account_id={story.book.rewards_card}",                    # the account's rows
                  f"service_id={svc}&expense_only=true&sort=date&sort_dir=desc"):  # the merchant page
        tx = listed(client, query)[story.harbour]
        assert tx["counted_minor"] == 10000, query
        assert tx["expect_pill"] == "S$ 200 expected back", query


def test_case_2_klook_all_of_it_stays_on_spending_at_nothing(client, conn, story):
    mark(conn, story.klook, 18000, names=(story.people["Kim"],), note="concert tickets")
    tx = listed(client, "book=Household&expense_only=true&month=2026-09")[story.klook]
    assert (tx["amount_minor"], tx["counted_minor"]) == (18000, 0)
    assert tx["expect_pill"] == "S$ 180 expected back"
    # Accounted for, its flow kept.
    assert tx["flow_type"] == "expense"


def test_a_rupee_row_and_a_to_pay_back_row_word_their_pill(client, conn, story):
    mark(conn, story.swiggy, 180000, names=(story.people["Jay"],))
    parents = row(conn, story.book, story.book.savings, "2026-03-01", "FROM PARENTS", -500000, flow="income")
    mark(conn, parents, 500000, names=(story.people["Parents"],))
    rows = listed(client, "")
    assert rows[story.swiggy]["expect_pill"] == "₹ 1,800 expected back"
    assert rows[story.swiggy]["counted_minor"] == 60000
    assert rows[parents]["expect_pill"] == "S$ 5,000 to pay back"
    assert rows[parents]["counted_minor"] == 0


# --- the Queue: the review list and its counts ----------------------------------


def test_case_10_a_wholly_placed_paynow_leaves_the_queue(client, conn, story):
    harbour = mark(conn, story.harbour, 20000, names=(story.people["Jay"], story.people["Kim"]))
    before = waiting(client)
    assert story.jay_0412 in review_list(client)
    set_off(conn, story.jay_0412, harbour, 12000)
    assert story.jay_0412 not in review_list(client)
    after = waiting(client)
    assert (before["waiting"] - after["waiting"], before["waiting_in_count"] - after["waiting_in_count"]) == (1, 1)
    assert round(before["waiting_in_total"] - after["waiting_in_total"], 2) == 120.0
    # Its row on the account still shows, struck to nothing, its flow kept.
    tx = listed(client, f"account_id={story.book.savings}")[story.jay_0412]
    assert (tx["flow_type"], tx["counted_minor"], tx["expect_pill"]) == ("review", 0, "S$ 120 set off")


def test_case_13_lena_stays_in_the_queue_with_s_20(client, conn, story):
    airbnb = mark(conn, story.airbnb, 4000, names=(story.people["Lena"],))
    set_off(conn, story.lena_2210, airbnb, 4000)
    tx = review_list(client)[story.lena_2210]
    assert (tx["own_minor"], tx["counted_minor"]) == (-2000, -2000)
    assert tx["expect_pill"] == "S$ 40 set off"
    # The waiting figures count her S$ 20.00, not S$ 60.00: with Jay and Kim
    # untouched, money in waiting is 120 + 70 + 20.
    w = waiting(client)
    assert (w["waiting_in_count"], w["waiting_in_total"]) == (3, 210.0)


def test_case_23_kim_returns_to_money_in_with_s_60_after_unlink(client, conn, story):
    held = harbour_set_against(conn, story)
    assert story.kim_7781 not in review_list(client)
    unlink(conn, held["kim_on_harbour"])
    tx = review_list(client)[story.kim_7781]
    assert tx["counted_minor"] == -6000
    assert tx["expect_pill"] == "S$ 10 set off"


def test_case_17d_unwaiting_returns_lena_with_s_100(client, conn, story):
    lena = story.people["Lena"]
    bbq = row(conn, story.book, story.book.savings, "2026-10-17", "PAYNOW FROM LENA 2210", -10000, flow="review")
    waiting_id = set_off(conn, bbq, None, 10000, person_id=lena, note="for the BBQ, when it is charged")
    assert bbq not in review_list(client)
    tx = listed(client, f"account_id={story.book.savings}")[bbq]
    assert tx["expect_pill"] == "S$ 100 waiting for its row"
    unlink(conn, waiting_id)
    tx = review_list(client)[bbq]
    assert tx["counted_minor"] == -10000
    assert tx["expect_pill"] is None


def dev_converted(conn, story) -> tuple[int, int]:
    """Case 43 as the conversion leaves it: Dev's 1 May S$ 500.00 out a back
    expectation, closed by S$ 500.00 of the 1 Jun S$ 600.00 in."""
    dev = story.people["Dev"]
    may = row(conn, story.book, story.book.savings, "2026-05-01", "TRANSFER TO DEV", 50000,
              flow="movement", other_side=dev)
    jun = row(conn, story.book, story.book.savings, "2026-06-01", "TRANSFER FROM DEV", -60000,
              flow="movement", other_side=dev)
    item = mark(conn, may, 50000, names=(dev,), source="converted")
    set_off(conn, jun, item, 50000, source="converted")
    return may, jun


def test_case_43_devs_1_jun_row_goes_to_money_in_with_s_100(client, conn, story):
    may, jun = dev_converted(conn, story)
    rows = review_list(client)
    assert may not in rows
    assert rows[jun]["counted_minor"] == -10000
    assert rows[jun]["flow_type"] == "movement"   # its flow kept
    w = waiting(client)
    assert (w["waiting_in_count"], w["waiting_in_total"]) == (4, 350.0)


def test_a_person_movement_with_no_record_stays_out_of_the_queue(client, conn, story):
    """A row the conversion left (another currency, before 2026) is not
    asked about: only a converted row with money left on it waits."""
    old = row(conn, story.book, story.book.savings, "2025-12-10", "TRANSFER TO LENA", 30000,
              flow="movement", other_side=story.people["Lena"])
    assert old not in review_list(client)


# --- the untyped and check-each-row predicates ----------------------------------


def untyped(conn, story) -> int:
    return row(conn, story.book, story.book.rewards_card, "2026-10-09", "MYSTERY SHOP", 5000)


def guessed_ids(client) -> set[int]:
    return {i for g in client.get("/api/queue/guesses").get_json()["groups"] for i in g["tx_ids"]}


def test_an_untyped_row_wholly_expected_back_leaves_the_no_type_list_and_the_guesses(client, conn, story):
    shop = untyped(conn, story)
    part = row(conn, story.book, story.book.rewards_card, "2026-10-11", "OTHER SHOP", 4000)
    mark(conn, shop, 5000, names=(story.people["Jay"],))
    mark(conn, part, 1000, names=(story.people["Jay"],))
    listed_ids = set(listed(client, "types=__untyped__"))
    assert part in listed_ids and part in guessed_ids(client)
    assert shop not in listed_ids
    assert shop not in guessed_ids(client)


def test_a_row_to_check_wholly_expected_back_leaves_the_check_each_row_list(client, conn, story):
    svc = merchant(conn, "Harbour Grill", each=1)
    file_under(conn, story.harbour, svc)
    file_under(conn, story.golden, svc)
    assert client.get(f"/api/services/{svc}").get_json()["rows_to_check"] == 2
    mark(conn, story.harbour, 30000, names=(story.people["Jay"],))
    each = listed(client, "look=each")
    assert story.golden in each
    assert story.harbour not in each
    assert listed(client, "")[story.harbour]["not_checked"] is False
    assert client.get(f"/api/services/{svc}").get_json()["rows_to_check"] == 1


# --- the review guess tally and the book-move count ------------------------------


def ravi_repaid(conn, story) -> int:
    """Two of Ravi's repayments, hand-labelled as a loan to him and wholly set
    off against the loan; a third of the same text waits."""
    ravi = story.people["Ravi"]
    loan = row(conn, story.book, story.book.savings, "2026-06-03", "TRANSFER TO RAVI", 200000,
               flow="movement", other_side=ravi)
    item = mark(conn, loan, 200000, names=(ravi,), source="converted")
    for day in ("2026-08-15", "2026-09-15"):
        paid = row(conn, story.book, story.book.savings, day, "PAYNOW FROM RAVI K", -5000,
                   flow="movement", other_side=ravi)
        conn.execute("UPDATE transactions SET flow_type_manual = 1 WHERE id = ?", (paid,))
        conn.commit()
        set_off(conn, paid, item, 5000, source="converted")
    return row(conn, story.book, story.book.savings, "2026-10-15", "PAYNOW FROM RAVI K", -5000, flow="review")


def test_the_review_guess_does_not_count_rows_accounted_for(client, conn, story):
    new = ravi_repaid(conn, story)
    assert client.get(f"/api/review/{new}/suggestion").get_json()["guess"] is None


def test_the_book_move_count_does_not_count_rows_accounted_for(client, conn, story):
    svc = merchant(conn, "Harbour Grill", book="Moom")
    for r in (story.harbour, story.golden):
        file_under(conn, r, svc)
        conn.execute("UPDATE transactions SET book = 'Household', cat_source = 'manual' WHERE id = ?", (r,))
    conn.commit()
    assert client.get(f"/api/services/{svc}/book-varies").get_json()["moved"] == 2
    mark(conn, story.harbour, 30000, names=(story.people["Jay"],))
    mark(conn, story.golden, 9600, names=(story.people["Jay"],))
    varies = client.get(f"/api/services/{svc}/book-varies").get_json()
    assert (varies["moved"], varies["suggest"]) == (0, False)


# --- 55 (lane half): the list is one read at ten times ---------------------------


def test_case_55_the_transaction_list_is_one_read_at_ten_times(client, conn, story, monkeypatch):
    seen: list[str] = []
    opened = db.get_connection

    def tracing():
        c = opened()
        c.set_trace_callback(seen.append)
        return c

    monkeypatch.setattr(db, "get_connection", tracing)

    def statements_for(query: str) -> int:
        seen.clear()
        got = client.get(f"/api/transactions?{query}")
        assert got.status_code == 200
        # The page carries every row's parts.
        assert all(t["counted_minor"] == t["own_minor"] for t in got.get_json()["transactions"])
        return sum(1 for s in seen if s.lstrip().upper().startswith("SELECT"))

    harbour_set_against(conn, story)
    small = statements_for("per_page=500")
    b = story.book
    for i in range(10 * 130):
        r = row(conn, b, b.rewards_card, f"2026-{(i % 9) + 1:02d}-1{i % 9}", f"DINNER {i}", 9000 + i, type_name="Dining")
        if i % 2:
            mark(conn, r, 3000, names=(b.people["Jay"],))
    large = statements_for("per_page=2000")
    # A count and the page: the parts come with the page, never a read per row.
    assert small == large == 2


# --- the client: the amount cell and the pill draw the payload --------------------


def app_function(name: str, source: str = APP_JS) -> str:
    m = (re.search(rf"^(?:async )?function {name}\(.*\}}$", source, re.M)
         or re.search(rf"^(?:async )?function {name}\(.*?^\}}$", source, re.M | re.S))
    assert m, f"{name} not found"
    return m.group(0)


def run_node(code: str):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


PRELUDE = r"""
globalThis.window = globalThis;
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
const icon = n => `<i ${n}></i>`;
const S = { marks: { rows: {}, accounts: {} } };
const money = (minor, cur = 'SGD') => `${cur} ${(minor / 100).toFixed(2)}`;
const toMinor = (a, cur) => a === null || a === undefined ? null : Math.round(Number(a) * 100);
const day = iso => String(iso);
const tag = (k, t) => `<span class="tag ${k}">${t}</span>`;
const rowMark = id => '';
const dirTag = l => `<dir ${l}>`;
const rowAmount = (m, c) => m === null ? '—' : (m < 0 ? '<dir in> ' : '<dir out> ') + money(Math.abs(m), c);
const notCheckedTag = r => '';
const pageLink = (k, id, t) => `<a ${k}=${id}>${esc(t)}</a>`;
const acctRowLabel = r => r.flow_type || '';
"""

# HARBOUR GRILL as the payload carries it. counted_minor is set apart
# from amount less expected on purpose: the client draws the payload's
# figure, it never works one out.
HARBOUR = {"id": 101, "date": "2026-10-02", "description": "HARBOUR GRILL", "amount_sgd": 300.0, "currency": "SGD",
           "amount_minor": 30000, "expected_minor": 20000, "placed_minor": 0, "own_minor": 10000,
           "counted_minor": 12345, "expect_pill": "S$ 200 expected back",
           "flow_type": "expense", "display_type": "Dining", "type_id": 7, "book": "Household", "account_id": 11,
           "account_name": "Rewards card", "service_id": 5, "notes": None, "is_one_off": 0}
JAY = {**HARBOUR, "id": 201, "description": "PAYNOW FROM JAY TAN 0412", "amount_sgd": -120.0, "amount_minor": -12000,
       "expected_minor": 0, "placed_minor": -12000, "own_minor": 0, "counted_minor": 0, "expect_pill": "S$ 120 set off",
       "flow_type": "review", "display_type": None, "type_id": None}
PLAIN = {**HARBOUR, "id": 102, "counted_minor": 30000, "expect_pill": None}


def renderers() -> str:
    return "\n".join([PRELUDE, app_function("struckAmount"), app_function("rowTr"),
                      app_function("spendRowTr"), app_function("acctRowTr")])


@needs_node
@pytest.mark.parametrize("name", ["rowTr", "spendRowTr", "acctRowTr"])
def test_a_row_renderer_strikes_the_amount_and_draws_the_payloads_figure_and_pill(name):
    code = renderers() + f"""
    const rows = {json.dumps([HARBOUR, JAY, PLAIN])};
    console.log(JSON.stringify(rows.map(r => {name}(r))));"""
    harbour, jay, off = run_node(code)
    assert "SGD 300.00</s>" in harbour and "SGD 123.45" in harbour
    assert "S$ 200 expected back" in harbour
    assert "SGD 120.00</s>" in jay and "SGD 0.00" in jay and "S$ 120 set off" in jay
    # A row with nothing on it (counted is the amount, no pill): drawn as before.
    assert "</s>" not in off and "expected back" not in off and "SGD 300.00" in off


@needs_node
def test_the_struck_cell_keeps_the_rows_direction():
    code = renderers() + f"""
    console.log(JSON.stringify([rowTr({json.dumps(JAY)}), acctRowTr({json.dumps(JAY)})]));"""
    row_html, acct_html = run_node(code)
    assert "<dir in>" in row_html
    # The account's rows: money in stays in the money-in column.
    cells = re.findall(r'<td class="r num row-(in|out)">(.*?)</td>', acct_html)
    assert [k for k, v in cells if v] == ["in"]


@needs_node
def test_the_merchant_page_row_strikes_its_amount_and_draws_the_pill():
    fn = app_function("viewMerchant")
    # The state column's source tags (fin-surfaces 52) are app.js's own.
    code = "\n".join([PRELUDE, app_function("struckAmount"), app_function("sourceTagsHTML"), r"""
    const merchantPage = { id: null, page: 1, ticks: new Set(), book: '', busy: false, month: null };
    const MP_PAGE = 50, ACT = {};
    const blank = () => '';
    const [mpCrumbs, typeOptions, bookOptions, eachSegHTML, bookVariesWords, bookVariesButtons, mpTickedWords,
           mpCanApply, afterRender, monthQuery, pager] = Array(11).fill(blank);
    const plural = (n, w) => `${n} ${w}`;
    const mpAccountCell = x => x.account_name;
    const refs = async () => ({ types: [], books: [] });
    const servicesList = async () => [];
    const ROWS = """ + json.dumps([HARBOUR, PLAIN]) + r""";
    const get = async url => url.startsWith('/api/services/5/book-varies') ? { suggest: false }
        : url.startsWith('/api/services/5') ? { id: 5, name: 'Harbour Grill', rows: 2, rule_count: 0, review_each_time: 0 }
        : { transactions: ROWS, total: 2, page: 1, pages: 1 };
    """ + fn + r"""
    viewMerchant(5).then(html => console.log(JSON.stringify(html)));"""])
    html = run_node(code)
    harbour, off = [m for m in re.findall(r'<tr[^>]*data-tx="(\d+)">(.*?)</tr>', html, re.S)]
    assert harbour[0] == "101" and "SGD 300.00</s>" in harbour[1] and "SGD 123.45" in harbour[1]
    assert "S$ 200 expected back" in harbour[1]
    assert "</s>" not in off[1] and "expected back" not in off[1]


@needs_node
def test_a_queue_item_counts_what_the_row_has_left():
    code = "\n".join([PRELUDE, app_function("rowItem"),
                      f"console.log(JSON.stringify([rowItem({json.dumps({**JAY, 'counted_minor': -2000})}, 'transfer'),"
                      f" rowItem({json.dumps(PLAIN)}, 'untyped')].map(i => [i.lane, i.amount, i.signed])));"])
    assert run_node(code) == [["in", 2000, -2000], ["out", 30000, 30000]]
