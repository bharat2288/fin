"""Let it go… (personal-ledger 37; spec 23 E3 and D4): let go and undo the let-go,
both directions, through the routes the drawer calls, and the drawer's own logic
under node.

Server (seam 1, the Flask test client on a temporary database): a back item lets go
as mine (its row's type and book, asking for a type when the row has none) or as a
gift (Gifts & Donations · Household, to whom); a pay item only as a gift to me, with
an income type. The let-go takes the whole open rest and counts on the day it is let
go. Each write is one change in the history and undoable from Changes; every refusal
writes nothing, the change history included.

Client (seam 5's node half): the confirm offers the choices by direction, stays
disabled until a choice (and, when one is asked, a type) is picked, and sends the
request in the route's shape (tests/fixtures/expect/letgo.json).

Every name, figure and date is invented (the spec's case list).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import expect_back
import letgo_api
from expect_fixtures import (
    harbour_set_against, last_entry, mark, october, opening, row, set_off,
)
from trend_fixtures import type_id

SEVEN = ("expectation", "expectation_name", "set_off", "let_go", "refund_link", "sender_wording", "dismissal")
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
FIXTURES = Path(__file__).parent / "fixtures" / "expect"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def snapshot(conn) -> dict:
    """Every record, every row and the history: what a refusal must leave as it was."""
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")]
            for t in SEVEN + ("transactions", "change_entries", "change_rows")}


def income_type_id(conn, name: str) -> int:
    return conn.execute("SELECT id FROM types WHERE kind = 'income' AND name = ?", (name,)).fetchone()[0]


def on_day(monkeypatch, iso: str) -> None:
    """The day the operator lets go: the route's today."""
    monkeypatch.setattr(letgo_api, "today", lambda: iso)


def let_go(client, expectation_id, became=None, *, to_id=None, type_id=None):
    body = {"became": became, "to_id": to_id, "type_id": type_id}
    return client.post(f"/api/expectations/{expectation_id}/let-go", json=body)


def undo_let_go(client, expectation_id):
    return client.delete(f"/api/expectations/{expectation_id}/let-go")


def undo(client, entry_id):
    return client.post(f"/api/history/{entry_id}/undo")


def entries(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0]


def refused(resp, status: int, words: str) -> None:
    assert resp.status_code == status, resp.get_json()
    assert resp.get_json() == {"error": words}
    assert "X-Fin-Change" not in resp.headers


@pytest.fixture
def story(conn):
    return october(conn)


@pytest.fixture
def harbour(conn, story):
    """Cases 1, 10 and 11: HARBOUR GRILL S$ 200.00 expected back from Jay and Kim,
    S$ 180.00 back, S$ 20.00 open; KLOOK S$ 170.00 open."""
    return harbour_set_against(conn, story)


# --- case 24: a gift ----------------------------------------------------------------


def test_case_24_harbour_let_go_as_a_gift_to_kim_takes_the_whole_open_rest(conn, client, story, harbour,
                                                                           monkeypatch):
    on_day(monkeypatch, "2026-10-19")
    kim = story.people["Kim"]
    before = entries(conn)
    resp = let_go(client, harbour["harbour"], "gift", to_id=kim)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["open_before_minor"] == 2000
    assert body["let_go"] == {
        "id": body["let_go"]["id"], "expectation_id": harbour["harbour"], "amount_minor": 2000, "currency": "SGD",
        "became": "gift", "book": "Household", "type_id": type_id(conn, "Gifts & Donations"),
        "display_type": "Gifts & Donations", "to": {"account_id": kim, "name": "Kim"},
        "counts_on": "2026-10-19", "made_at": body["let_go"]["made_at"],
    }
    assert body["expectation"] == {"id": harbour["harbour"], "amount_minor": 20000, "currency": "SGD",
                                   "open_minor": 0, "state": "let_go"}
    # One change, named, carrying the one record.
    assert entries(conn) == before + 1
    made = int(resp.headers["X-Fin-Change"])
    assert conn.execute("SELECT summary FROM change_entries WHERE id = ?", (made,)).fetchone()[0] \
        == letgo_api.SUMMARIES["api_let_go"]
    assert [tuple(r) for r in conn.execute("SELECT tbl, op FROM change_rows WHERE entry_id = ?", (made,))] \
        == [("let_go", "insert")]
    # Expected back as at 19 Oct loses the S$ 20.00 (Books' open-as-at read).
    held = {x["id"]: x["open_minor"] for x in expect_back.open_as_at(conn, "2026-10-19")["expectations"]}
    assert held[harbour["harbour"]] == 0
    assert held[harbour["klook"]] == 17000


def test_case_24_neither_choice_picked_is_refused_and_writes_nothing(conn, client, harbour, monkeypatch):
    on_day(monkeypatch, "2026-10-19")
    held = snapshot(conn)
    for became in (None, "", "both"):
        refused(let_go(client, harbour["harbour"], became), 400, "pick mine or a gift")
    assert snapshot(conn) == held


def test_a_gift_takes_gifts_and_donations_and_to_whom_must_be_a_person(conn, client, story, harbour,
                                                                       monkeypatch):
    on_day(monkeypatch, "2026-10-19")
    held = snapshot(conn)
    refused(let_go(client, harbour["harbour"], "gift", type_id=type_id(conn, "Dining")), 400,
            "a gift counts as Gifts & Donations")
    refused(let_go(client, harbour["harbour"], "gift", to_id=story.book.savings), 400,
            "a name on an expectation is a person's account")
    assert snapshot(conn) == held
    # No one named: a gift to no one in particular.
    resp = let_go(client, harbour["harbour"], "gift")
    assert resp.status_code == 200 and resp.get_json()["let_go"]["to"] is None


# --- case 25: mine, in a later month; a row with no type asks for one ------------------------


def test_case_25_golden_village_mine_counts_on_3_nov_as_its_rows_type_and_book(conn, client, story,
                                                                               monkeypatch):
    golden = mark(conn, story.golden, 6400, names=(story.people["Jay"], story.people["Lena"]))
    on_day(monkeypatch, "2026-11-03")
    resp = let_go(client, golden, "mine")
    assert resp.status_code == 200, resp.get_json()
    lg = resp.get_json()["let_go"]
    assert (lg["amount_minor"], lg["became"], lg["counts_on"], lg["book"], lg["display_type"], lg["to"]) \
        == (6400, "mine", "2026-11-03", "Household", "Entertainment", None)
    assert lg["type_id"] == type_id(conn, "Entertainment")
    # Counted on its own day: open as at the end of September is untouched, as at 3 Nov nothing is open.
    sep = {x["id"]: x["open_minor"] for x in expect_back.open_as_at(conn, "2026-09-30")["expectations"]}
    nov = {x["id"]: x["open_minor"] for x in expect_back.open_as_at(conn, "2026-11-03")["expectations"]}
    assert sep[golden] == 6400 and nov[golden] == 0


def loan_to_ravi(conn, story) -> int:
    """Case 40's figures: S$ 2,000.00 out to Ravi on 3 Jun with no type, S$ 500.00 back on 15 Aug."""
    ravi = story.people["Ravi"]
    lent = row(conn, story.book, story.book.savings, "2026-06-03", "TRANSFER TO RAVI", 200000,
               flow="movement", other_side=ravi)
    back = row(conn, story.book, story.book.savings, "2026-08-15", "FROM RAVI", -50000,
               flow="movement", other_side=ravi)
    loan = mark(conn, lent, 200000, names=(ravi,), source="converted")
    set_off(conn, back, loan, 50000, source="converted")
    return loan


def test_case_25_a_loan_row_with_no_type_let_go_as_mine_asks_for_a_type(conn, client, story, monkeypatch):
    loan = loan_to_ravi(conn, story)
    on_day(monkeypatch, "2026-10-19")
    held = snapshot(conn)
    refused(let_go(client, loan, "mine"), 400, "TRANSFER TO RAVI has no type: pick the type it was")
    refused(let_go(client, loan, "mine", type_id=income_type_id(conn, "Salary")), 400,
            "mine is spending: pick a spending type")
    assert snapshot(conn) == held
    resp = let_go(client, loan, "mine", type_id=type_id(conn, "Other"))
    assert resp.status_code == 200, resp.get_json()
    lg = resp.get_json()["let_go"]
    assert (lg["amount_minor"], lg["book"], lg["display_type"]) == (150000, "Household", "Other")


def test_an_opening_figure_let_go_as_mine_asks_for_a_type_and_counts_household(conn, client, story,
                                                                                monkeypatch):
    sam = opening(conn, story.people["Sam"], "2026-01-01", 80000)
    on_day(monkeypatch, "2026-10-19")
    refused(let_go(client, sam, "mine"), 400, "Opening figure · Sam has no type: pick the type it was")
    resp = let_go(client, sam, "mine", type_id=type_id(conn, "Other"))
    assert resp.status_code == 200
    assert (resp.get_json()["let_go"]["amount_minor"], resp.get_json()["let_go"]["book"]) == (80000, "Household")


def test_mine_is_to_no_one(conn, client, story, harbour, monkeypatch):
    on_day(monkeypatch, "2026-10-19")
    held = snapshot(conn)
    refused(let_go(client, harbour["harbour"], "mine", to_id=story.people["Kim"]), 400, "only a gift is to someone")
    assert snapshot(conn) == held


# --- case 26: undo the let-go -----------------------------------------------------------


def test_case_26_undo_the_let_go_opens_the_rest_again_one_change(conn, client, story, harbour, monkeypatch):
    on_day(monkeypatch, "2026-10-19")
    assert let_go(client, harbour["harbour"], "gift", to_id=story.people["Kim"]).status_code == 200
    # While let go, the item takes no set-off: undo the let-go first.
    with pytest.raises(expect_back.Refused, match="let go since; undo the let-go first"):
        expect_back.insert_set_off(conn, story.lena_2210, harbour["harbour"], 1000)
    conn.rollback()
    before = entries(conn)
    resp = undo_let_go(client, harbour["harbour"])
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json() == {"expectation": {"id": harbour["harbour"], "amount_minor": 20000, "currency": "SGD",
                                               "open_minor": 2000, "state": "partly_back"}}
    assert entries(conn) == before + 1
    made = int(resp.headers["X-Fin-Change"])
    assert conn.execute("SELECT summary FROM change_entries WHERE id = ?", (made,)).fetchone()[0] \
        == letgo_api.SUMMARIES["api_undo_let_go"]
    assert conn.execute("SELECT COUNT(*) FROM let_go").fetchone()[0] == 0
    # Undoing the undo from Changes lets it go again, as it was.
    assert undo(client, made).status_code == 200
    assert expect_back.state(conn, harbour["harbour"]) == "let_go"
    assert conn.execute("SELECT amount_minor, counts_on FROM let_go").fetchone()[:] == (2000, "2026-10-19")


def test_the_let_go_undone_from_changes_reopens_the_rest(conn, client, story, harbour, monkeypatch):
    on_day(monkeypatch, "2026-10-19")
    resp = let_go(client, harbour["harbour"], "gift", to_id=story.people["Kim"])
    assert undo(client, int(resp.headers["X-Fin-Change"])).status_code == 200
    assert expect_back.open_minor(conn, harbour["harbour"]) == 2000


def test_undo_the_let_go_with_nothing_let_go_is_refused(conn, client, harbour):
    held = snapshot(conn)
    refused(undo_let_go(client, harbour["harbour"]), 400, "nothing let go on HARBOUR GRILL")
    assert snapshot(conn) == held


def test_letting_go_twice_is_refused(conn, client, harbour, monkeypatch):
    on_day(monkeypatch, "2026-10-19")
    assert let_go(client, harbour["harbour"], "gift").status_code == 200
    held = snapshot(conn)
    refused(let_go(client, harbour["harbour"], "mine"), 400, "let go since; undo the let-go first")
    assert snapshot(conn) == held


def test_no_such_expectation(conn, client, monkeypatch):
    on_day(monkeypatch, "2026-10-19")
    held = snapshot(conn)
    refused(let_go(client, 999, "gift"), 404, "no such expectation")
    refused(undo_let_go(client, 999), 404, "no such expectation")
    assert snapshot(conn) == held


# --- case 26a: a closed item ---------------------------------------------------------------


def test_case_26a_let_go_of_closed_klook_is_refused_and_writes_nothing(conn, client, story, harbour, monkeypatch):
    more = row(conn, story.book, story.book.savings, "2026-10-16", "PAYNOW FROM KIM L 7781", -17000, flow="review")
    set_off(conn, more, harbour["klook"], 17000)
    assert expect_back.state(conn, harbour["klook"]) == "closed"
    on_day(monkeypatch, "2026-10-19")
    held = snapshot(conn)
    for became in ("gift", "mine"):
        refused(let_go(client, harbour["klook"], became), 400, "nothing open on KLOOK TRAVEL")
    assert snapshot(conn) == held


# --- case 26d: a To pay back item ------------------------------------------------------------


def parents_to_pay_back(conn, story) -> int:
    """Cases 18 and 42: S$ 5,000.00 in from Parents on 1 Mar, to pay back; S$ 1,000.00 paid back on 1 Aug."""
    parents = story.people["Parents"]
    came = row(conn, story.book, story.book.savings, "2026-03-01", "FROM PARENTS", -500000, flow="review")
    paid = row(conn, story.book, story.book.savings, "2026-08-01", "TO PARENTS", 100000,
               flow="movement", other_side=parents)
    owed = mark(conn, came, 500000, names=(parents,))
    set_off(conn, paid, owed, 100000)
    return owed


def test_case_26d_parents_only_a_gift_to_me_with_an_income_type(conn, client, story, monkeypatch):
    owed = parents_to_pay_back(conn, story)
    on_day(monkeypatch, "2026-10-19")
    held = snapshot(conn)
    refused(let_go(client, owed, "mine"), 400, "money in was never spent: let it go only as a gift to me")
    refused(let_go(client, owed, "gift"), 400, "a gift to me is income: pick an income type")
    refused(let_go(client, owed, "gift", type_id=type_id(conn, "Gifts & Donations")), 400,
            "a gift to me is income: pick an income type")
    refused(let_go(client, owed, "gift", to_id=story.people["Parents"], type_id=income_type_id(conn, "Gift received")),
            400, "a gift to me is to no one")
    assert snapshot(conn) == held

    gift = income_type_id(conn, "Gift received")
    resp = let_go(client, owed, "gift", type_id=gift)
    assert resp.status_code == 200, resp.get_json()
    lg = resp.get_json()["let_go"]
    assert (lg["amount_minor"], lg["became"], lg["book"], lg["type_id"], lg["display_type"], lg["counts_on"]) \
        == (400000, "gift", "Household", gift, "Gift received", "2026-10-19")
    # To pay back S$ 0.
    pay = [x for x in expect_back.open_as_at(conn, "2026-10-19")["expectations"] if x["direction"] == "pay"]
    assert sum(x["open_minor"] for x in pay) == 0


# --- case 54 end to end, and the undo guard --------------------------------------------------


def test_case_54_undoing_case_10s_set_off_after_the_let_go_is_refused(conn, client, story, harbour, monkeypatch):
    on_day(monkeypatch, "2026-10-19")
    assert let_go(client, harbour["harbour"], "gift", to_id=story.people["Kim"]).status_code == 200
    held = snapshot(conn)
    resp = undo(client, harbour["jay_entry"])
    assert resp.status_code == 409
    assert resp.get_json() == {"error": "let go since; undo the let-go first"}
    assert snapshot(conn) == held
    # Unlinking Jay's line directly is refused with the same words (case 26b).
    with pytest.raises(expect_back.Refused, match="let go since; undo the let-go first"):
        expect_back.delete_set_off(conn, harbour["jay_on_harbour"])
    conn.rollback()
    # Once the let-go is undone, the set-off's undo goes through.
    assert undo_let_go(client, harbour["harbour"]).status_code == 200
    assert undo(client, harbour["jay_entry"]).status_code == 200
    assert expect_back.open_minor(conn, harbour["harbour"]) == 14000


def test_undoing_the_mark_while_let_go_is_refused(conn, client, story, harbour, monkeypatch):
    on_day(monkeypatch, "2026-10-19")
    assert let_go(client, harbour["harbour"], "gift").status_code == 200
    resp = undo(client, harbour["mark_entry"])
    assert resp.status_code == 409 and resp.get_json() == {"error": "let go since; undo the let-go first"}


# --- the route's day ---------------------------------------------------------------------------


def test_today_is_the_iso_day():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", letgo_api.today())


# ---------------------------------------------------------------------------
# The drawer (D4) under node: choices by direction, disabled until picked
# ---------------------------------------------------------------------------

PRELUDE = r"""
globalThis.window = globalThis;
const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
const icon = n => `<i ${n}></i>`;
const SIGNS = { SGD: 'S$', INR: '₹' };
const money = (minor, cur = 'SGD') => `${SIGNS[cur] || cur} ${(Math.abs(minor) / 100).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}`;
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const LONG_MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
const day = (iso, { year = true } = {}) => { const [y, m, d] = iso.split('-').map(Number); return `${d} ${MONTHS[m - 1]}${year ? ' ' + y : ''}`; };
const ACT = {};
const calls = [];
const box = { body: '', foot: '' };
const openSheet = (title, body, opts) => { calls.push(['open', title, opts.center, opts.eyebrow]); box.body = body; box.foot = opts.foot || ''; return {}; };
const closeSheet = () => calls.push(['close']);
const sheetBody = () => ({ set innerHTML(v) { box.body = v; }, get innerHTML() { return box.body; } });
const $ = sel => sel === '#sheets .sheet-foot' ? { set innerHTML(v) { box.foot = v; }, get innerHTML() { return box.foot; } } : null;
const toast = (m, o) => calls.push(['toast', m, (o && o.undo) || null, !!(o && o.bad)]);
const refreshFrame = () => calls.push(['refresh']);
const rerender = () => calls.push(['rerender']);
let GETS = {};
const get = async url => { calls.push(['get', url]); if (!(url in GETS)) throw new Error('no ' + url); return GETS[url]; };
let SEND = { ok: true, status: 200, data: {}, change: 41 };
const send = async (method, url, body) => { calls.push(['send', method, url, body]); return SEND; };
"""


def shells(*names: str) -> str:
    return "\n".join((STATIC / f"{n}.js").read_text(encoding="utf-8") for n in names)


def run_node(code: str):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def open_page() -> dict:
    """HARBOUR GRILL's page with S$ 20.00 open (the page fixture)."""
    return fixture("page")


def pay_page() -> dict:
    """Parents' To pay back item: S$ 4,000.00 open of S$ 5,000.00 on FROM PARENTS."""
    page = fixture("page")
    e = page["expectation"]
    e.update({"id": 7, "direction": "pay", "amount_minor": 500000, "set_off_minor": 100000, "let_go_minor": 0,
              "open_minor": 400000, "state": "partly_back", "names": [{"account_id": 35, "name": "Parents"}]})
    e["row"].update({"id": 301, "description": "FROM PARENTS", "amount_minor": -500000, "type_id": None,
                     "display_type": None})
    page["lines"] = [{"kind": "open", "amount_minor": 400000, "currency": "SGD", "name": None, "actions": ["let_go"]}]
    return page


def untyped_page() -> dict:
    """Loan to Ravi: S$ 1,500.00 open on a movement with no type."""
    page = fixture("page")
    e = page["expectation"]
    e.update({"id": 5, "open_minor": 150000, "names": [{"account_id": 34, "name": "Ravi"}]})
    e["row"].update({"description": "TRANSFER TO RAVI", "type_id": None, "display_type": None})
    page["lines"] = [{"kind": "open", "amount_minor": 150000, "currency": "SGD", "name": "Ravi",
                      "actions": ["let_go"]}]
    return page


@needs_node
def test_the_drawer_offers_mine_or_a_gift_on_a_back_item_and_only_a_gift_to_me_on_a_pay_item():
    code = PRELUDE + shells("expect-mark", "expect-letgo") + f"""
    const L = Expect.letGo;
    const back = L.choices({json.dumps(open_page())}), pay = L.choices({json.dumps(pay_page())});
    const loan = L.choices({json.dumps(untyped_page())});
    console.log(JSON.stringify([back, pay, loan]));"""
    back, pay, loan = run_node(code)
    assert [(c["k"], c["title"], c["sub"], c["asks_type"]) for c in back] == [
        ("mine", "It was mine after all", "Spending: Dining · Household", None),
        ("gift", "A gift", "Spending: Gifts & Donations · Household", None),
    ]
    assert [(c["k"], c["title"], c["asks_type"]) for c in pay] == [("gift", "A gift to me", "income")]
    assert [(c["k"], c["asks_type"]) for c in loan] == [("mine", "spending"), ("gift", None)]


@needs_node
def test_the_confirm_is_disabled_until_a_choice_and_any_type_asked_is_picked_and_sends_the_fixture_requests():
    f = fixture("letgo")
    code = PRELUDE + shells("expect-mark", "expect-letgo") + f"""
    const L = Expect.letGo;
    const back = {json.dumps(open_page())}, pay = {json.dumps(pay_page())}, loan = {json.dumps(untyped_page())};
    console.log(JSON.stringify([
      L.request(back, {{}}), L.request(back, {{ pick: 'gift', to: 32 }}), L.request(back, {{ pick: 'mine' }}),
      L.request(pay, {{}}), L.request(pay, {{ pick: 'gift' }}), L.request(pay, {{ pick: 'gift', type: 51 }}),
      L.request(loan, {{ pick: 'mine' }}), L.request(loan, {{ pick: 'mine', type: '19' }}),
      L.request(back, {{ pick: 'mine', to: 32 }}),
    ]));"""
    out = run_node(code)
    assert out[0] is None  # neither picked: Let it go stays disabled
    assert out[1] == f["request"]
    assert out[2] == f["request_mine"]
    assert out[3] is None and out[4] is None  # a gift to me waits for its income type
    assert out[5] == f["request_pay"]
    assert out[6] is None  # no type on the row: asks for one first
    assert out[7] == {"became": "mine", "to_id": None, "type_id": 19}
    assert out[8] == f["request_mine"]  # mine is to no one, whatever was ticked for a gift


@needs_node
def test_the_let_go_reads_in_words_from_the_response():
    f = fixture("letgo")
    mine = dict(f["response"]["let_go"], became="mine", to=None, display_type="Entertainment", amount_minor=6400)
    pay = dict(f["response"]["let_go"], to=None, display_type="Gift received", amount_minor=400000)
    code = PRELUDE + shells("expect-mark", "expect-letgo") + f"""
    const L = Expect.letGo;
    console.log(JSON.stringify([L.words({json.dumps(f["response"]["let_go"])}, 'back'),
      L.words({json.dumps(mine)}, 'back'), L.words({json.dumps(pay)}, 'pay')]));"""
    assert run_node(code) == ["S$ 20 let go as a gift to Kim", "S$ 64 let go as mine, Entertainment · Household",
                              "S$ 4,000 let go as a gift to me, Gift received"]


@needs_node
def test_open_let_go_draws_a_centred_confirm_disabled_until_picked_then_posts_and_toasts_with_undo():
    f = fixture("letgo")
    code = PRELUDE + shells("expect-mark", "expect-letgo") + f"""
    GETS['/api/expectations/1'] = {json.dumps(open_page())};
    SEND = {{ ok: true, status: 200, data: {json.dumps(f["response"])}, change: 41 }};
    Expect.letGo.today = () => '2026-10-19';
    (async () => {{
      await window.openLetGo(1);
      const first = box.foot;
      const words = box.body;
      ACT['xb-lg-pick']({{ dataset: {{ k: 'gift' }} }});
      const picked = box.foot, pickedBody = box.body;
      ACT['xb-lg-to']({{ dataset: {{ to: '32' }} }});
      await ACT['xb-lg-go']({{}});
      console.log(JSON.stringify({{ first, words, picked, pickedBody, calls }}));
    }})();"""
    out = run_node(code)
    opened = next(c for c in out["calls"] if c[0] == "open")
    assert opened[1] == "S$ 20.00 from HARBOUR GRILL" and opened[2] is True and opened[3] == "Let it go"
    assert re.search(r'data-act="xb-lg-go"[^>]*disabled', out["first"])
    assert "Counted on <b>19 Oct</b>, today" in out["words"]
    assert not re.search(r'data-act="xb-lg-go"[^>]*disabled', out["picked"])
    assert 'aria-pressed="true"' in out["pickedBody"]
    sent = next(c for c in out["calls"] if c[0] == "send")
    assert sent[1:] == ["POST", "/api/expectations/1/let-go", f["request"]]
    toast = next(c for c in out["calls"] if c[0] == "toast")
    assert toast[1:] == ["S$ 20 let go as a gift to Kim, counted on 19 Oct.", 41, False]
    assert ["close"] in out["calls"] and ["refresh"] in out["calls"]


@needs_node
def test_a_refusal_is_said_and_the_drawer_stays_open():
    code = PRELUDE + shells("expect-mark", "expect-letgo") + f"""
    GETS['/api/expectations/1'] = {json.dumps(open_page())};
    SEND = {{ ok: false, status: 400, data: {{ error: 'nothing open on KLOOK TRAVEL' }}, change: null }};
    (async () => {{
      await window.openLetGo(1);
      ACT['xb-lg-pick']({{ dataset: {{ k: 'mine' }} }});
      await ACT['xb-lg-go']({{}});
      console.log(JSON.stringify(calls));
    }})();"""
    calls = run_node(code)
    assert ["toast", "nothing open on KLOOK TRAVEL", None, True] in calls
    assert ["close"] not in calls[calls.index(next(c for c in calls if c[0] == "send")):]


@needs_node
def test_a_pay_item_asks_for_an_income_type_and_a_typeless_row_for_a_spending_type():
    code = PRELUDE + shells("expect-mark", "expect-letgo") + f"""
    GETS['/api/expectations/7'] = {json.dumps(pay_page())};
    GETS['/api/types?kind=income'] = [{{ id: 51, kind: 'income', name: 'Gift received', display_name: 'Gift received' }}];
    GETS['/api/expectations/5'] = {json.dumps(untyped_page())};
    GETS['/api/types'] = [{{ id: 19, kind: 'spending', name: 'Other', display_name: 'Other' }}];
    (async () => {{
      await window.openLetGo(7);
      const payFirst = box.foot;
      ACT['xb-lg-pick']({{ dataset: {{ k: 'gift' }} }});
      const payBody = box.body, payFoot = box.foot;
      ACT['xb-lg-type']({{ value: '51' }});
      const payTyped = box.foot;
      await window.openLetGo(5);
      ACT['xb-lg-pick']({{ dataset: {{ k: 'mine' }} }});
      const loanBody = box.body, loanFoot = box.foot;
      console.log(JSON.stringify({{ payFirst, payBody, payFoot, payTyped, loanBody, loanFoot }}));
    }})();"""
    out = run_node(code)
    assert re.search(r'data-act="xb-lg-go"[^>]*disabled', out["payFirst"])  # one choice, still picked by hand
    assert "A gift to me" in out["payBody"] and "It was mine after all" not in out["payBody"]
    assert "Gift received" in out["payBody"]
    assert re.search(r'data-act="xb-lg-go"[^>]*disabled', out["payFoot"])
    assert not re.search(r'data-act="xb-lg-go"[^>]*disabled', out["payTyped"])
    assert "Other" in out["loanBody"]
    assert re.search(r'data-act="xb-lg-go"[^>]*disabled', out["loanFoot"])


@needs_node
def test_undo_the_let_go_deletes_and_toasts():
    code = PRELUDE + shells("expect-mark", "expect-letgo") + r"""
    SEND = { ok: true, status: 200, data: { expectation: { id: 1, amount_minor: 20000, currency: 'SGD', open_minor: 2000, state: 'partly_back' } }, change: 42 };
    (async () => {
      await ACT['undo-let-go']({ dataset: { id: '1' } });
      console.log(JSON.stringify(calls));
    })();"""
    calls = run_node(code)
    assert ["send", "DELETE", "/api/expectations/1/let-go", None] in calls
    assert ["toast", "S$ 20 open again.", 42, False] in calls
