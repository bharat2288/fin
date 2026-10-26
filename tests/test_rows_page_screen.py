"""Ticket 19: the Rows page in static/rows-page.js, as the approved mockup draws it.

Run under node: app.js's real money / esc / day / tag / pager / booksNav /
registerRoute and the sort helpers are lifted out of static/app.js, never
rewritten here; the server is a stand-in `get` that answers the two reads the
page makes and records each URL it is asked. Figures in through the payloads,
words out through the page's HTML and the URLs it reads. Every figure is
invented.
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
ROWS_JS = (STATIC / "rows-page.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def _function(name):
    m = re.search(rf"^(?:async )?function {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _const(name):
    m = re.search(rf"^const {name} = (?:\{{\n.*?^\}};|\[\n.*?^\];|.*?;)$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


SUMMARY = {
    "flows": {"expense": 3, "refund": 1, "payment": 2, "movement": 2, "income": 1, "review": 1},
    "rows": 10, "in_sgd_minor": 367000, "out_sgd_minor": 160200, "unvalued": 1, "waiting": 1,
    "months": ["2026-09", "2026-08"], "latest_month": "2026-09",
    "parties": [{"id": 31, "name": "Sample Grocer", "rows": 2}, {"id": 32, "name": "Sample Lender", "rows": 2}],
}

ROWS = {"transactions": [
    {"id": 1, "date": "2026-08-10", "description": "INWARD REMITTANCE", "amount_minor": -1000000, "amount_sgd": -10000.0,
     "currency": "INR", "sgd_minor": -15000, "flow_type": "review", "account_name": "Sample Rupee Bank 0009",
     "account_id": 3, "hidden": False, "service_id": None, "other_side_name": None, "book": "Household"},
    {"id": 2, "date": "2026-08-15", "description": "SAMPLE LOAN INSTALMENT", "amount_minor": 100000, "amount_sgd": 1000.0,
     "currency": "SGD", "sgd_minor": 100000, "flow_type": "movement", "account_name": "Sample Bank 0002",
     "account_id": 2, "hidden": False, "service_id": None, "other_side_id": 4, "other_side_name": "Sample Car Loan",
     "book": "Household"},
    {"id": 3, "date": "2026-08-06", "description": "SAMPLE STREAM", "amount_minor": 1200, "amount_sgd": 12.0,
     "currency": "SGD", "sgd_minor": 1200, "flow_type": "expense", "account_name": "Sample Card 0001", "account_id": 1,
     "hidden": True, "service_id": 30, "service_name": "Sample Stream", "display_type": "Subscriptions", "book": "Household"},
    {"id": 4, "date": "2026-09-03", "description": "SAMPLE BOOKS MUMBAI", "amount_minor": 200000, "amount_sgd": 2000.0,
     "currency": "INR", "sgd_minor": None, "flow_type": "expense", "account_name": "Sample Rupee Bank 0009",
     "account_id": 3, "hidden": False, "service_id": None, "display_type": "Books", "book": "Household"},
], "total": 4, "page": 1, "per_page": 50, "pages": 1}

REFS = {"accounts": [{"id": 1, "name": "Sample Card 0001", "type": "card"}, {"id": 2, "name": "Sample Bank 0002", "type": "bank"},
                     {"id": 3, "name": "Sample Rupee Bank 0009", "type": "bank"}, {"id": 4, "name": "Sample Car Loan", "type": "loan"}],
        "books": [{"name": "Household"}, {"name": "Moom"}, {"name": "Kalesh"}]}


def run(body, *, hash="#/books/rows", answers=None):
    lifted = [_const("SIGNS"), _const("DIGITS"), _const("formatters"), _function("digitsOf"),
              _function("groupFor"), _function("money"), _function("toMinor"), _function("esc"),
              _const("store"), _const("MONTHS"), _const("LONG_MONTHS"), _function("day"),
              _function("monthName"), _function("plural"), _function("tag"), _function("pager"), _function("dirTag"), _function("rowAmount"),
              _const("pageLinkKinds"), _function("pageLink"), _const("booksNavExtra"), _function("booksNav"),
              _const("SORTS"), _const("SORT_REDRAW"), _function("sortOf"), _function("nextSort"),
              _function("sortServer"), _function("sortHead"), _const("SORT_WORDS"), _function("sortPicker")]
    default = {"/api/rows/summary": SUMMARY, "/api/transactions": ROWS}
    code = "\n".join([
        "globalThis.window = globalThis;",
        "const memory = new Map();",
        "globalThis.localStorage = { getItem: k => memory.has(k) ? memory.get(k) : null, setItem: (k, v) => memory.set(k, String(v)) };",
        f"globalThis.location = {{ hash: {json.dumps(hash)} }};",
        "globalThis.scrollTo = () => {};",
        "const listeners = {}; globalThis.addEventListener = (t, fn) => { listeners[t] = fn; };",
        *lifted,
        "window.booksNavExtra = booksNavExtra;",
        "const ROUTES = [];", _function("registerRoute"),
        "const ACT = {};",
        "const icon = n => `<i data-icon=\"${n}\"></i>`;",
        "const rowMark = id => '';",
        "let renders = 0; function rerender() { renders += 1; }",
        "const REFS = " + json.dumps(REFS) + ";",
        "async function refs() { return { ...REFS, accountById: new Map(REFS.accounts.map(a => [a.id, a])) }; }",
        "const out = v => console.log(JSON.stringify(v));",
        "const asked = [];",
        "const ANSWERS = " + json.dumps({**default, **(answers or {})}) + ";",
        "async function get(url) {",
        "  asked.push(url);",
        "  const hit = Object.keys(ANSWERS).filter(k => url.startsWith(k)).sort((a, b) => b.length - a.length)[0];",
        "  return JSON.parse(JSON.stringify(hit ? ANSWERS[hit] : {}));",
        "}",
        ROWS_JS,
        "const page = async () => ROUTES.find(([re]) => re.test('/books/rows'))[2]([]);",
        "const param = (url, k) => new URL(url, 'http://x').searchParams.get(k);",
        "const rowsAsked = () => asked.filter(u => u.startsWith('/api/transactions'));",
        "(async () => {", body, "})().catch(e => { console.error(e.stack); process.exit(1); });",
    ])
    res = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def test_the_page_has_its_route_and_its_tab_beside_spending():
    got = run("out({ route: ROUTES.some(([re]) => re.test('/books/rows')), nav: booksNav('rows') });")
    assert got["route"] is True
    hrefs = re.findall(r'href="([^"]+)"', got["nav"])
    assert hrefs[:3] == ["#/books", "#/books/spending", "#/books/rows"]
    assert 'href="#/books/rows" aria-current="page"' in got["nav"]


def test_it_opens_on_the_latest_month_with_every_row_whatever_its_flow():
    got = run("const html = await page(); out({ html, asked });")
    rows = [u for u in got["asked"] if u.startswith("/api/transactions")]
    assert rows, got["asked"]
    q = dict(re.findall(r"[?&]([^=&]+)=([^&]*)", rows[-1]))
    assert q["every"] == "true" and q["month"] == "2026-09"
    assert "flow" not in q and "include_hidden" not in q


def test_the_flow_chips_carry_their_counts_and_the_figures_are_money_in_and_out():
    t = text(run("out({ html: await page() });")["html"])
    for words in ("Every flow 10", "Spending 3", "Refund 1", "Card payment 2", "Movement 2", "Income 1",
                  "Waiting for a label 1", "Transfer 0"):
        assert words in t, words
    assert "Money in S$ 3,670.00" in t and "Money out S$ 1,602.00" in t
    assert "1 with no rate" in t


def test_each_row_shows_its_own_amount_its_sgd_value_its_flow_and_where_it_goes():
    html = run("out({ html: await page() });")["html"]
    t = text(html)
    # A rupee row: its rupees, and its S$ value at the rate of its day.
    assert "₹ 10,000.00" in t or "₹10,000.00" in t
    assert "S$ 150.00" in t
    # A rupee row with no rate says so, never a guess.
    assert "no rate" in t
    # A movement names its other side; a hidden merchant's row says it is hidden.
    assert "into Sample Car Loan" in t
    assert "hidden from spending" in t
    assert 'data-act="row" data-tx="3"' in html


def test_a_link_in_sets_the_filters_it_names():
    got = run("await page(); out({ asked: rowsAsked() });", hash="#/books/rows?account=3&flow=movement&month=2026-08")
    url = got["asked"][-1]
    assert "account_id=3" in url and "flow=movement" in url and "month=2026-08" in url


def test_a_flow_chip_a_book_and_a_party_narrow_the_next_read():
    got = run("""await page();
        ACT['rows-set']({ dataset: { k: 'flow', v: 'movement' } });
        ACT['rows-set']({ dataset: { k: 'book', v: 'Moom' } });
        ACT['rows-pick']({ dataset: { k: 'party' }, value: '32' });
        await page();
        out({ asked: rowsAsked(), renders });""")
    url = got["asked"][-1]
    assert "flow=movement" in url and "book=Moom" in url and "party_id=32" in url
    assert got["renders"] == 3


def test_every_month_reads_without_a_month():
    got = run("""await page(); ACT['rows-pick']({ dataset: { k: 'month' }, value: 'all' }); await page();
        out({ asked: rowsAsked() });""")
    assert "month=" not in got["asked"][-1]


def test_the_same_link_followed_again_sets_its_filters_again():
    got = run("""await page();
        ACT['rows-pick']({ dataset: { k: 'account' }, value: '1' });
        location.hash = '#/books/merchant/30'; listeners.hashchange();
        location.hash = '#/books/rows?account=3&month=all'; listeners.hashchange();
        await page();
        out({ asked: rowsAsked() });""", hash="#/books/rows?account=3&month=all")
    assert "account_id=3" in got["asked"][-1]
