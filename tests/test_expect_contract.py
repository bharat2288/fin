"""Personal-ledger 26: the client seams expect back plugs into, and its JSON contract, pinned.

Two halves:

- The fixtures in tests/fixtures/expect/ are the route shapes of the 23 spec (E1-E7, D1-D6):
  mark, the open list, the expectation's page, set-off request and response, waiting, let-go,
  Books sections, Queue paybacks and both refund asks. Every client lane draws from them and
  every route lane must answer in them, so a key renamed here is a failing test before it is a
  broken drawer. Figures are the case list's, checked here for conservation: a row's own part,
  expected and placed sum to its amount; an expectation's open rest is what is left after its
  set-offs and let-go; Books' totals are their lines'.
- The seams in static/app.js are lifted out of the real file and run under node, with the
  script on stdin: every slot draws nothing while its shell is empty, so each screen renders
  exactly as before, and draws what a lane registers once it is filled.

Every name, figure and date is invented.
"""

import json
import re
import shutil
import subprocess
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest

import money

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
FIXTURES = Path(__file__).parent / "fixtures" / "expect"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

# index.html order; expect-mark.js holds the shared namespace, so it loads first.
SHELLS = ["expect-mark", "expect-setoff", "expect-waiting", "expect-letgo",
          "expect-page", "expect-books", "expect-queue", "expect-refund"]
SPECIMEN_NAMES = {"Jay", "Kim", "Lena", "Ravi", "Parents", "Sam", "Dev", "Mei"}
CURRENCIES = {"SGD", "INR"}
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The node harness: pure helpers lifted out of the real files
# ---------------------------------------------------------------------------

def app_function(name: str) -> str:
    m = (re.search(rf"^(?:async )?function {name}\(.*\}}$", APP_JS, re.M)
         or re.search(rf"^(?:async )?function {name}\(.*?^\}}$", APP_JS, re.M | re.S))
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def run_node(code: str):
    """Runs code under node, the script on stdin (a long -e passes Windows' command-line limit);
    the script prints one JSON value."""
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def shells_js() -> str:
    return "\n".join((STATIC / f"{s}.js").read_text(encoding="utf-8") for s in SHELLS)


# What app.js's row renderers read, stubbed: enough to draw a row the way the page does.
PRELUDE = r"""
globalThis.window = globalThis;
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
const icon = n => `<i ${n}></i>`;
const S = { marks: { rows: {}, accounts: {} } };
const DIGITS = { SGD: 2, INR: 2 };
const money = (minor, cur = 'SGD') => `${cur} ${(minor / 100).toFixed(2)}`;
const toMinor = (a, cur) => a === null || a === undefined ? null : Math.round(Number(a) * 100);
const day = iso => String(iso);
const tag = (k, t) => `<span class="tag ${k}">${t}</span>`;
const claudeMark = id => id ? `<b>${id}</b>` : '';
const rowMark = id => claudeMark(S.marks.rows[id]);
const dirTag = l => `<i ${l}>`;
const rowAmount = (m, c) => m === null ? '—' : (m < 0 ? 'in ' : 'out ') + money(Math.abs(m), c);
const notCheckedTag = r => r.not_checked ? '<nc>' : '';
const pageLink = (k, id, t) => `<a ${k}=${id}>${esc(t)}</a>`;
const acctRowLabel = r => r.flow_type || '';
const FLOW_WORDS = {};
"""
ROWS = [
    {"id": 101, "date": "2026-10-02", "description": "HARBOUR GRILL", "amount_sgd": 300.0, "currency": "SGD",
     "flow_type": "expense", "display_type": "Dining", "type_id": 7, "book": "Household", "account_id": 11,
     "account_name": "Rewards card", "service_id": 5, "notes": None, "is_one_off": 0},
    {"id": 201, "date": "2026-10-10", "description": "PAYNOW FROM JAY TAN 0412", "amount_sgd": -120.0, "currency": "SGD",
     "flow_type": "review", "display_type": None, "type_id": None, "book": "Household", "account_id": 13,
     "account_name": "Everyday savings", "service_id": None, "notes": "a note", "is_one_off": 1},
]
# A slot one-liner, as app.js writes it: the hook's words or nothing.
PILL_SLOT = "${window.expectRowPill?.(row) || ''}"


def _without_pill(fn_text: str) -> str:
    """The renderer as it was before the slot: the pill call taken out."""
    assert PILL_SLOT in fn_text, "the renderer has no row-pill slot"
    return fn_text.replace(PILL_SLOT, "")


# ---------------------------------------------------------------------------
# index.html: every new tag after app.js; each shell its own stylesheet
# ---------------------------------------------------------------------------

def test_every_shell_tag_follows_app_js_with_expect_mark_first():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    app = html.index('src="/static/app.js"')
    at = [html.find(f'src="/static/{s}.js"') for s in SHELLS]
    assert all(i > app for i in at), dict(zip(SHELLS, at))
    assert at == sorted(at), "the shells load in the order SHELLS names (expect-mark first)"
    for s in SHELLS:
        assert html.count(f'href="/static/{s}.css"') == 1, s


@pytest.mark.parametrize("name", SHELLS)
def test_each_shell_has_its_script_and_stylesheet(name):
    assert (STATIC / f"{name}.js").is_file()
    assert (STATIC / f"{name}.css").is_file()


@pytest.mark.parametrize("name", SHELLS)
def test_every_class_in_a_shell_stylesheet_is_prefixed_xb(name):
    """So no later lane edits styles.css, and no expect rule reaches a page it was not written for."""
    css = re.sub(r"/\*.*?\*/", "", (STATIC / f"{name}.css").read_text(encoding="utf-8"), flags=re.S)
    css = re.sub(r"\{[^{}]*\}", "{}", css)  # declarations out: only selectors are left
    classes = re.findall(r"\.(-?[A-Za-z_][\w-]*)", css)
    assert all(c.startswith("xb-") for c in classes), [c for c in classes if not c.startswith("xb-")]


# ---------------------------------------------------------------------------
# The shells: one namespace, slots that draw nothing until a lane fills them
# ---------------------------------------------------------------------------

@needs_node
def test_empty_shells_draw_nothing_in_any_slot():
    code = "globalThis.window = globalThis;" + shells_js() + r"""
    (async () => {
      const row = { id: 1 };
      console.log(JSON.stringify([
        window.expectRowActions(row), window.expectRowPill(row), window.expectMerchantRowAction(row),
        window.expectBooksSections({}), await window.expectQueueStep([]), window.expectBackForFoot({}),
        window.expectChangeWords({ table: 'expectation', op: 'insert' }, {}, {}),
        window.expectChangeWords({ table: 'transactions', op: 'insert' }, {}, {}),
        typeof window.viewExpectation, typeof window.openExpectMark, typeof window.openBackFor, typeof window.openLetGo,
        Object.keys(window.Expect.slots).sort()]));
    })();"""
    out = run_node(code)
    # A row the lists say nothing about (no amount, no parts) draws nothing in any slot,
    # whichever lanes have filled them since (personal-ledger 34 fills the mark's).
    assert out[:8] == [""] * 8
    # An entry point is absent until its lane fills its shell, then a function
    # (34 fills openExpectMark, 35 viewExpectation, 36 openBackFor, 37 openLetGo).
    assert out[8:12] == ["function"] * 4
    assert set(out[12]) <= {"rowActions", "rowPill", "merchantRowAction", "booksSections", "queueStep", "backForFoot"}
    assert "rowActions" in out[12]  # personal-ledger 36: the row sheet's Back for…


@needs_node
def test_a_filled_slot_draws_what_each_lane_registers_in_load_order():
    code = "globalThis.window = globalThis;" + shells_js() + r"""
    (async () => {
      Expect.fill('rowActions', r => `<mark ${r.id}>`);
      Expect.fill('rowActions', r => `<back ${r.id}>`);
      Expect.fill('rowActions', () => null);
      Expect.fill('queueStep', async list => `<q ${list.length}>`);
      Expect.fill('backForFoot', p => `<wait ${p.id}>`);
      Expect.words.set_off = (c, ctx, o) => `set off ${c.after.amount_minor}`;
      console.log(JSON.stringify([
        window.expectRowActions({ id: 7 }), await window.expectQueueStep([1, 2]), window.expectBackForFoot({ id: 9 }),
        window.expectChangeWords({ table: 'set_off', op: 'insert', after: { amount_minor: 12000 } }, {}, { rows: 1 }),
        window.expectChangeWords({ table: 'let_go', op: 'insert', after: {} }, {}, {})]));
    })();"""
    assert run_node(code) == ["<mark 7><back 7>", "<q 2>", "<wait 9>", "set off 12000", ""]


@needs_node
def test_change_words_only_answer_for_the_seven_tables():
    code = "globalThis.window = globalThis;" + shells_js() + r"""
    for (const t of Expect.TABLES) Expect.words[t] = () => t;
    Expect.words.transactions = () => 'not mine';
    console.log(JSON.stringify([Expect.TABLES, window.expectChangeWords({ table: 'transactions' }, {}, {})]));"""
    tables, other = run_node(code)
    assert tables == ["expectation", "expectation_name", "set_off", "let_go", "refund_link", "sender_wording", "dismissal"]
    assert other == ""


# ---------------------------------------------------------------------------
# app.js: the route, the slots, the Changes words, the reserved entry points
# ---------------------------------------------------------------------------

@needs_node
def test_the_expectation_route_opens_the_page_the_shell_fills():
    m = re.search(r"^registerRoute\(/\^\\/books\\/expectation.*$", APP_JS, re.M)
    assert m, "no expectation route in static/app.js"
    code = "\n".join(["globalThis.window = globalThis;", r"const ROUTES = [[/^\/?$/, 'home', () => 'h']];",
                      app_function("registerRoute"), m.group(0),
                      "const find = p => { const r = ROUTES.find(([re]) => re.test(p)) || ROUTES[0]; return [r[1], r[2](p.match(r[0]))]; };",
                      "const empty = find('/books/expectation/12');",
                      "window.viewExpectation = id => 'page ' + id + ' ' + typeof id;",
                      "console.log(JSON.stringify([empty, find('/books/expectation/12'), find('/books/expectation/x')[0]]));"])
    empty, filled, other = run_node(code)
    assert empty == ["books", ""]
    assert filled == ["books", "page 12 number"]
    assert other == "home"


@needs_node
@pytest.mark.parametrize("name,args", [("rowTr", "row"), ("spendRowTr", "row"), ("acctRowTr", "row")])
def test_a_row_renderer_with_an_empty_pill_slot_draws_exactly_as_before(name, args):
    fn = app_function(name)
    old = _without_pill(fn).replace(f"function {name}(", f"function old_{name}(", 1)
    code = "\n".join([PRELUDE, fn, old, f"const rows = {json.dumps(ROWS)};",
                      f"const before = rows.map(r => old_{name}(r)), empty = rows.map(r => {name}(r));",
                      "window.expectRowPill = r => `<pill ${r.id}>`;",
                      f"const filled = rows.map(r => {name}(r));",
                      "console.log(JSON.stringify([before, empty, filled]));"])
    before, empty, filled = run_node(code)
    assert empty == before
    assert all(f"<pill {r['id']}>" in html for r, html in zip(ROWS, filled))


def test_the_merchant_rows_carry_the_pill_and_an_action_slot():
    fn = app_function("viewMerchant")
    assert "${rowMark(x.id)}${window.expectRowPill?.(x) || ''}" in fn
    assert "${mark}${window.expectMerchantRowAction?.(x) || ''}" in fn


def test_the_row_sheet_actions_line_has_its_slot():
    fn = app_function("openRowSheet")
    assert "${thisWas}${window.expectRowActions?.(found) || ''}</div>" in fn


def test_the_balance_sheet_has_its_section_slot_before_the_month_check():
    fn = app_function("viewSheet")
    # on the card's closing line, so an empty slot leaves the page byte for byte as it was
    assert "</section>${window.expectBooksSections?.(sheet) || ''}\n    ${monthCheckHTML(sheet)}" in fn


def test_the_queue_money_in_lane_has_its_step():
    fn = app_function("laneHTML")
    assert "lane === 'in' ? (await window.expectQueueStep?.(list)) || '' : ''" in fn
    assert re.search(r'<div class="lane-body">\$\{xb\}\$\{list\.length', fn)


@needs_node
def test_slot_expressions_are_empty_strings_when_no_shell_has_filled_them():
    code = r"""globalThis.window = globalThis;
    (async () => {
      const found = {}, x = {}, sheet = {}, list = [], lane = 'in';
      console.log(JSON.stringify([
        `${window.expectRowActions?.(found) || ''}`, `${window.expectMerchantRowAction?.(x) || ''}`,
        `${window.expectBooksSections?.(sheet) || ''}`, lane === 'in' ? (await window.expectQueueStep?.(list)) || '' : '']));
    })();"""
    assert run_node(code) == ["", "", "", ""]


@needs_node
def test_change_words_hand_the_seven_tables_to_the_hook_and_nothing_else_changes():
    fn = app_function("changeWords")
    assert "window.expectChangeWords?.(c, ctx, { rows, subject })" in fn
    old = re.sub(r"\n\s*const xw = window\.expectChangeWords.*\n", "\n", fn).replace("function changeWords(", "function oldWords(", 1)
    assert old.count("\n") == fn.count("\n") - 1
    stubs = r"""
    const rowName = c => (c.after || c.before || {}).description || '';
    const accountOf = () => 'Everyday savings';
    const isEmpty = v => v === null || v === undefined;
    const valueText = (t, k, v) => v === null || v === undefined ? '' : String(v);
    const changedFields = c => Object.keys(c.after || {}).filter(k => (c.before || {})[k] !== c.after[k]);
    const FIELD_WORDS = {};
    """
    changes = [
        {"table": "transactions", "op": "update", "before": {"description": "HARBOUR GRILL", "notes": None},
         "after": {"description": "HARBOUR GRILL", "notes": "dinner"}},
        {"table": "statements", "op": "insert", "after": {"statement_date": "2026-10-31"}},
        {"table": "services", "op": "insert", "after": {"name": "KLOOK"}},
        {"table": "expectation", "op": "insert", "after": {"amount_minor": 20000}},
        {"table": "set_off", "op": "insert", "after": {"amount_minor": 12000}},
        {"table": "dismissal", "op": "delete", "before": {"kind": "payback"}},
    ]
    code = "\n".join([PRELUDE, stubs, app_function("rowClauses"), app_function("joinClauses"), fn, old, shells_js(),
                      f"const cs = {json.dumps(changes)};",
                      "const before = cs.map(c => oldWords(c, {}, {})), empty = cs.map(c => changeWords(c, {}, {}));",
                      "for (const t of Expect.TABLES) Expect.words[t] = c => 'expect ' + c.table;",
                      "Expect.words.transactions = () => 'never';",
                      "const filled = cs.map(c => changeWords(c, {}, { rows: 2 })), old2 = cs.map(c => oldWords(c, {}, { rows: 2 }));",
                      "console.log(JSON.stringify([before, empty, filled, old2]));"])
    before, empty, filled, old2 = run_node(code)
    assert empty == before
    assert before[3:] == ["", "", ""]
    assert filled[3:] == ["expect expectation", "expect set_off", "expect dismissal"]
    assert filled[:3] == old2[:3]  # a many-row entry on the other tables reads as it did


@needs_node
def test_the_reserved_entry_points_hand_their_ids_to_the_shells():
    lines = re.findall(r"^ACT\['(?:expect-mark|back-for|let-go)'\] = .*$", APP_JS, re.M)
    assert len(lines) == 3, lines
    code = "\n".join(["globalThis.window = globalThis; const ACT = {};", *lines,
                      "const before = ['expect-mark', 'back-for', 'let-go'].map(k => ACT[k]({ dataset: { tx: '5', id: '6' } }));",
                      "const got = [];",
                      "window.openExpectMark = (id, el) => got.push(['mark', id]);",
                      "window.openBackFor = (id, el) => got.push(['back', id]);",
                      "window.openLetGo = (id, el) => got.push(['letgo', id]);",
                      "['expect-mark', 'back-for', 'let-go'].forEach(k => ACT[k]({ dataset: { tx: '5', id: '6' } }));",
                      "console.log(JSON.stringify([before, got]));"])
    before, got = run_node(code)
    assert before == [None, None, None]
    assert got == [["mark", 5], ["back", 5], ["letgo", 6]]


# ---------------------------------------------------------------------------
# The fixtures: shapes pinned, money whole minor units, figures that conserve
# ---------------------------------------------------------------------------

ROW = {"id", "date", "description", "account_id", "account_name", "amount_minor", "currency", "book", "type_id", "display_type"}
PARTS = {"id", "amount_minor", "currency", "expected_minor", "placed_minor", "own_minor"}
EXPECTATION = {"id", "direction", "amount_minor", "currency", "note", "source", "made_at", "day", "names",
               "set_off_minor", "let_go_minor", "open_minor", "state", "row", "opening"}
EXP_BRIEF = {"id", "amount_minor", "currency", "open_minor", "state"}
SET_OFF = {"id", "payment_row_id", "expectation_id", "date", "amount_minor", "currency", "exp_amount_minor",
           "exp_currency", "rate", "rate_date", "rate_month_ended", "source", "person", "note", "state"}
LET_GO = {"id", "expectation_id", "amount_minor", "currency", "became", "book", "type_id", "display_type", "to",
          "counts_on", "made_at"}
BOOKS_LINE = {"key", "expectation_id", "title", "date", "names", "open_minor", "amount_minor", "currency",
              "sgd_minor", "rate", "no_rate", "age_days", "old", "last_set_off"}
WAITING_LINE = (BOOKS_LINE - {"expectation_id", "last_set_off"}) | {"set_off_id", "note"}
SECTION = {"key", "heading", "owed", "in_total", "total_minor", "left_out", "lines"}
PAGE_LINES = {
    "marked": {"kind", "id", "date", "amount_minor", "currency", "actions"},
    "set_off": {"kind", "id", "date", "person", "amount_minor", "currency", "payment", "elsewhere", "actions"},
    "refund": {"kind", "id", "date", "purchase_date", "amount_minor", "currency", "refund", "actions"},
    "let_go": {"kind", "id", "date", "amount_minor", "currency", "became", "to", "book", "display_type", "actions"},
    "open": {"kind", "amount_minor", "currency", "name", "actions"},
}
STATES = {"open", "partly_back", "closed", "let_go"}
ALL_FIXTURES = sorted(p.stem for p in FIXTURES.glob("*.json"))


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}.{k}", k, v
            yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")


def check_expectation(e):
    assert set(e) == EXPECTATION
    assert e["direction"] in {"back", "pay"} and e["source"] in {"hand", "converted"}
    assert e["currency"] in CURRENCIES and e["state"] in STATES
    assert 0 < e["amount_minor"]
    assert e["open_minor"] == e["amount_minor"] - e["set_off_minor"] - e["let_go_minor"] >= 0
    state = ("let_go" if e["let_go_minor"] else "closed" if not e["open_minor"]
             else "partly_back" if e["set_off_minor"] else "open")
    assert e["state"] == state
    assert all(set(n) == {"account_id", "name"} and n["name"] in SPECIMEN_NAMES for n in e["names"])
    assert (e["row"] is None) != (e["opening"] is None), "a row, or an opening figure, never both"
    if e["row"]:
        r = e["row"]
        assert set(r) == ROW and r["currency"] == e["currency"]
        assert e["amount_minor"] <= abs(r["amount_minor"])
        # back on money out (positive), pay on money in (negative)
        assert (r["amount_minor"] > 0) == (e["direction"] == "back")
        assert e["day"] == r["date"]


def check_parts(p):
    assert set(p) >= PARTS
    assert p["own_minor"] == p["amount_minor"] - p["expected_minor"] - p["placed_minor"]
    for k in ("expected_minor", "placed_minor", "own_minor"):  # each part carries the row's sign
        assert p[k] == 0 or (p[k] > 0) == (p["amount_minor"] > 0), k


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_money_is_whole_minor_units_in_a_stated_currency(name):
    for path, key, value in walk(load(name)):
        if key.endswith("_minor"):
            assert value is None or type(value) is int, path
        if key in ("currency", "exp_currency") and value is not None:
            assert value in CURRENCIES, path
        if key in ("date", "day", "counts_on", "as_at", "near", "rate_date", "purchase_date", "last_set_off") and value is not None:
            assert DAY.match(value), path


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_names_are_the_case_lists_specimens(name):
    seen = []
    for path, key, value in walk(load(name)):
        if key == "names":
            seen += [(path, n if isinstance(n, str) else n["name"]) for n in value if not isinstance(n, int)]
        elif key in ("person", "to") and isinstance(value, str):
            seen.append((path, value))
        elif key in ("person", "to") and isinstance(value, dict):
            seen.append((path, value["name"]))
    assert all(n in SPECIMEN_NAMES for _, n in seen), [x for x in seen if x[1] not in SPECIMEN_NAMES]


def test_every_route_names_a_fixture_that_exists():
    routes = load("routes")["routes"]
    assert {"mark", "undo_mark", "open_list", "page", "set_off", "unlink", "waiting", "attach", "let_go",
            "undo_let_go", "not_a_payback", "books_sections", "queue_paybacks", "refund_asks",
            "refund_hand_link", "refund_answer", "refund_unlink"} == set(routes)
    for key, r in routes.items():
        assert r["method"] in {"GET", "POST", "PUT", "DELETE"}, key
        assert r["path"].startswith("/api/"), key
        assert (FIXTURES / f"{r['fixture']}.json").is_file(), key


def test_mark_case_1_part_of_a_dinner():
    f = load("mark")
    assert set(f["request"]) == {"direction", "all", "amount", "names", "new_names", "note"}
    assert set(f["request_all"]) == set(f["request_new_name"]) == set(f["request"])
    assert money.parse_minor(f["request"]["amount"]) == 20000
    resp = f["response"]
    assert set(resp) == {"expectation", "row", "spending_change"}
    check_expectation(resp["expectation"])
    check_parts(resp["row"])
    assert resp["row"]["own_minor"] == 10000 and resp["row"]["expected_minor"] == resp["expectation"]["amount_minor"]
    assert resp["spending_change"] == {"month": "2026-10", "minor": -20000, "currency": "SGD"}
    assert set(f["refusal"]) == {"error"}


def test_undo_mark_returns_the_month_its_spending_comes_back_to():
    f = load("undo_mark")
    check_parts(f["response"]["row"])
    assert f["response"]["row"]["own_minor"] == f["response"]["row"]["amount_minor"]
    assert f["response"]["spending_change"]["minor"] == 18000
    assert f["refusal"]["error"].startswith("Can't undo the mark yet")


def test_open_list_case_10_nearest_the_payment_first():
    f = load("open_list")
    assert set(f) == {"direction", "near", "payment", "items"}
    p = f["payment"]
    check_parts(p)
    assert p["left_minor"] == -p["amount_minor"] - -p["placed_minor"] == 12000
    for e in f["items"]:
        check_expectation(e)
        assert e["direction"] == f["direction"] and e["open_minor"] > 0
    near = date.fromisoformat(f["near"])
    gaps = [abs((date.fromisoformat(e["day"]) - near).days) for e in f["items"]]
    assert gaps == sorted(gaps)
    assert [e["row"]["description"] for e in f["items"]] == [
        "HARBOUR GRILL", "KLOOK TRAVEL", "GOLDEN VILLAGE", "AIRBNB", "PAYNOW TO RAVI"]
    assert [(e["open_minor"], e["amount_minor"]) for e in f["items"]] == [
        (20000, 20000), (18000, 18000), (6400, 6400), (4000, 62000), (150000, 200000)]


@pytest.mark.parametrize("name", ["page", "page_let_go", "page_refund"])
def test_the_expectation_page_lines_add_up_to_its_meter(name):
    f = load(name)
    assert set(f) == {"as_at", "expectation", "own_minor", "age_days", "back_pct", "lines"}
    e = f["expectation"]
    check_expectation(e)
    for line in f["lines"]:
        assert set(line) == PAGE_LINES[line["kind"]], line["kind"]
    by = lambda k: sum(x["amount_minor"] for x in f["lines"] if x["kind"] in k)  # noqa: E731
    assert by({"marked"}) == e["amount_minor"]
    assert by({"set_off", "refund"}) == e["set_off_minor"]
    assert by({"let_go"}) == e["let_go_minor"]
    assert by({"open"}) == e["open_minor"]
    assert f["own_minor"] == e["row"]["amount_minor"] - e["amount_minor"]
    assert f["back_pct"] == round(100 * e["set_off_minor"] / e["amount_minor"])
    assert f["age_days"] == (date.fromisoformat(f["as_at"]) - date.fromisoformat(e["day"])).days
    for line in f["lines"]:
        if line["kind"] == "set_off":  # what one payment placed here and elsewhere never passes the payment
            placed = line["amount_minor"] + sum(x["amount_minor"] for x in line["elsewhere"])
            assert placed <= -line["payment"]["amount_minor"]


def test_page_case_12_harbour_open_rest_names_nobody():
    f = load("page")
    assert f["back_pct"] == 90 and f["age_days"] == 17
    assert [x["kind"] for x in f["lines"]] == ["marked", "set_off", "set_off", "open"]
    assert f["lines"][-1] == {"kind": "open", "amount_minor": 2000, "currency": "SGD", "name": None, "actions": ["let_go"]}
    assert load("page_let_go")["expectation"]["state"] == "let_go"
    assert not any(x["kind"] == "open" for x in load("page_let_go")["lines"])


def test_set_off_case_11_one_request_every_placement():
    f = load("setoff")
    assert set(f["request"]) == {"placements", "waiting"}
    req_minor = [money.parse_minor(p["amount"]) for p in f["request"]["placements"]]
    resp = f["response"]
    assert set(resp) == {"payment", "set_offs", "expectations", "learnt"}
    check_parts(resp["payment"])
    for s in resp["set_offs"]:
        assert set(s) == SET_OFF and s["state"] == "placed"
        assert s["payment_row_id"] == resp["payment"]["id"]
    for e in resp["expectations"]:
        assert set(e) == EXP_BRIEF
    assert [s["amount_minor"] for s in resp["set_offs"]] == req_minor == [6000, 1000]
    assert -resp["payment"]["placed_minor"] == sum(req_minor) <= -resp["payment"]["amount_minor"]
    assert resp["payment"]["left_minor"] == -resp["payment"]["own_minor"] == 0
    assert set(f["learnt_example"]) == {"wording", "person"}
    assert set(f["refusal"]) == {"error"}


def test_set_off_case_19_across_currencies_through_the_one_inverse_half_even():
    resp = load("setoff")["cross_currency"]["response"]
    s, = resp["set_offs"]
    rate = Decimal(s["rate"])
    assert s["currency"] == "SGD" and s["exp_currency"] == "INR"
    assert s["amount_minor"] == 9000 and s["rate_date"] == "2026-09-30" and s["rate_month_ended"] is True
    exact = Decimal(s["amount_minor"]) / rate
    assert s["exp_amount_minor"] == int(exact.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)) == 580645
    assert money.convert_minor(s["exp_amount_minor"], rate, "INR", "SGD") == 9000
    e, = resp["expectations"]
    assert e["open_minor"] == e["amount_minor"] - s["exp_amount_minor"] == 19355


def test_unlink_case_23_returns_the_payment_part_to_place_or_label():
    resp = load("unlink")["response"]
    assert set(resp) == {"removed", "payment", "expectation"}
    check_parts(resp["payment"])
    assert resp["payment"]["left_minor"] == -resp["payment"]["own_minor"] == 6000
    assert resp["expectation"]["open_minor"] == 8000


def test_waiting_case_17_and_attach_split_case_17a():
    f = load("waiting")
    w = f["response"]["set_off"]
    assert set(w) == SET_OFF and w["state"] == "waiting" and w["expectation_id"] is None
    assert w["person"]["name"] == "Lena" and w["amount_minor"] == 10000
    check_parts(f["response"]["payment"])
    a = f["attach_response"]
    assert set(a) == {"expectation", "placed", "waiting", "row"}
    check_expectation(a["expectation"])
    assert a["expectation"]["state"] == "closed" and a["expectation"]["amount_minor"] == 8000
    assert set(a["placed"]) == set(a["waiting"]) == SET_OFF
    assert a["placed"]["state"] == "placed" and a["waiting"]["state"] == "waiting"
    assert a["placed"]["amount_minor"] + a["waiting"]["amount_minor"] == w["amount_minor"]
    assert a["placed"]["date"] == w["date"] < a["expectation"]["day"]  # case 17e: the set-off keeps the payment's day
    check_parts(a["row"])
    assert a["row"]["own_minor"] == 0


def test_let_go_case_24_takes_the_whole_open_rest():
    f = load("letgo")
    for k in ("request", "request_mine", "request_pay"):
        assert set(f[k]) == {"became", "to_id", "type_id"} and f[k]["became"] in {"mine", "gift"}
    resp = f["response"]
    assert set(resp["let_go"]) == LET_GO
    assert resp["let_go"]["amount_minor"] == resp["open_before_minor"] == 2000
    assert set(resp["expectation"]) == EXP_BRIEF and resp["expectation"]["state"] == "let_go"
    assert resp["expectation"]["open_minor"] == 0
    assert f["undo_response"]["expectation"]["open_minor"] == resp["open_before_minor"]


def test_not_a_payback_writes_a_payback_dismissal():
    d = load("not_a_payback")["response"]["dismissal"]
    assert set(d) == {"id", "row_id", "kind", "purchase_row_id", "made_at"}
    assert d["kind"] == "payback" and d["purchase_row_id"] is None


def test_books_case_48_three_sections_oldest_first_totals_their_lines():
    f = load("books_sections")
    as_at = date.fromisoformat(f["as_at"])
    secs = {s["key"]: s for s in f["sections"]}
    assert list(secs) == ["expected_back", "waiting", "to_pay_back"]
    for s in f["sections"]:
        assert set(s) == SECTION and s["in_total"] is False  # out of the total until the switch
        for line in s["lines"]:
            assert set(line) == (WAITING_LINE if s["key"] == "waiting" else BOOKS_LINE)
            assert line["age_days"] == (as_at - date.fromisoformat(line["date"])).days
            assert line["old"] == (line["age_days"] > 90)
            assert 0 < line["open_minor"] <= line["amount_minor"]
            if line["currency"] == "SGD":
                assert line["sgd_minor"] == line["open_minor"] and line["rate"] is None
            else:
                rate = Decimal(line["rate"]["text"])
                assert line["sgd_minor"] == money.convert_minor(line["open_minor"], rate, line["currency"], "SGD")
        assert [x["date"] for x in s["lines"]] == sorted(x["date"] for x in s["lines"])
        assert s["total_minor"] == sum(x["sgd_minor"] for x in s["lines"] if not x["no_rate"])
    assert secs["expected_back"]["total_minor"] == 262490
    assert not secs["expected_back"]["owed"] and secs["waiting"]["owed"] and secs["to_pay_back"]["owed"]
    assert secs["waiting"]["total_minor"] == 10000 and secs["to_pay_back"]["total_minor"] == 400000
    beside = {x["title"]: x["sgd_minor"] for x in secs["expected_back"]["lines"] if x["currency"] == "INR"}
    assert beside == {"PURPLE LOTUS MUMBAI": 300, "SWIGGY · Mumbai": 2790}
    assert [x["key"] for x in secs["waiting"]["lines"]] == ["waiting:16"]


def test_books_case_49_no_rate_is_left_out_and_said():
    s, = load("books_sections_no_rate")["sections"]
    nr = [x for x in s["lines"] if x["no_rate"]]
    assert len(nr) == 1 and nr[0]["sgd_minor"] is None and nr[0]["rate"] is None
    assert s["total_minor"] == sum(x["sgd_minor"] for x in s["lines"] if not x["no_rate"]) == 80000
    assert [x["key"] for x in s["left_out"]] == [nr[0]["key"]]


def test_queue_paybacks_cases_27_28_30_and_the_waiting_offer_17():
    f = load("queue_paybacks")
    assert set(f) == {"paybacks", "waiting_offers"}
    for p in f["paybacks"]:
        assert set(p) == {"payment", "suggestions"}
        assert set(p["payment"]) == {"id", "description", "date", "account_name", "amount_minor", "currency", "left_minor"}
        assert p["payment"]["amount_minor"] < 0
        for s in p["suggestions"]:
            assert set(s) == {"expectation_id", "title", "date", "open_minor", "currency", "reasons"}
            assert s["open_minor"] >= p["payment"]["left_minor"] and s["currency"] == p["payment"]["currency"]
            assert 0 <= (date.fromisoformat(p["payment"]["date"]) - date.fromisoformat(s["date"])).days <= 90
            assert all(isinstance(r, str) and r for r in s["reasons"])
    assert [len(p["suggestions"]) for p in f["paybacks"]] == [1, 2, 0]
    assert f["paybacks"][0]["suggestions"][0]["reasons"] == ["Jay is named on it", "8 days after", "S$ 120 ≤ S$ 200 open"]
    w, = f["waiting_offers"]
    assert set(w) == {"set_off_id", "person", "amount_minor", "currency", "payment", "rows"}
    for r in w["rows"]:
        assert r["amount_minor"] >= w["amount_minor"] and r["date"] >= w["payment"]["date"]


def test_refund_ask_which_case_35_two_purchases_fit():
    f = load("refund_ask_which")
    ask, = f["asks"]
    assert set(ask) == {"kind", "refund", "purchases"} and ask["kind"] == "which"
    assert ask["refund"]["amount_minor"] < 0
    for p in ask["purchases"]:
        assert p["left_minor"] >= -ask["refund"]["amount_minor"]
        assert 0 <= (date.fromisoformat(ask["refund"]["date"]) - date.fromisoformat(p["date"])).days <= 90
    assert set(f["hand_link_request"]) == {"refund_row_id", "purchase_row_id"}
    link = f["hand_link_response"]["refund_link"]
    assert set(link) == {"id", "refund_row_id", "purchase_row_id", "source", "made_at"} and link["source"] == "hand"
    assert link["purchase_row_id"] in {p["id"] for p in ask["purchases"]}


def test_refund_ask_paid_back_case_37a_keep_it_or_to_pay_back():
    f = load("refund_ask_paid_back")
    ask, = f["asks"]
    assert ask["kind"] == "paid_back" and ask["answers"] == ["keep", "pay_back"]
    assert ask["rest_minor"] == -ask["refund"]["amount_minor"] > ask["own_minor"] + ask["open_minor"]
    assert f["answer_request"]["answer"] in ask["answers"]
    e = f["answer_response"]["expectation"]
    check_expectation(e)
    assert e["direction"] == "pay" and e["amount_minor"] == ask["rest_minor"]
    assert [n["name"] for n in e["names"]] == [n["name"] for n in ask["names"]]
    check_parts(f["answer_response"]["row"])
    assert f["answer_response"]["row"]["own_minor"] == 0
