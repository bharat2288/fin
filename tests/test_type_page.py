"""Ticket 45: the type page in static/type-page.js (spec 15, Client C3).

Run under node: app.js's real money / esc / day / pageLink / registerRoute are
lifted out of static/app.js, never rewritten here; trend.js is the real card;
the server is a stand-in `get` that answers from the shared contract fixture
tests/fixtures/trend/trend_type.json (ticket 24) and records each URL it is
asked. Figures in through the payload, figures and words out through the page's
HTML, the card's drawn view and the URLs it reads. Every figure is invented.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
FIXTURES = ROOT / "tests" / "fixtures" / "trend"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
TREND_JS = (STATIC / "trend.js").read_text(encoding="utf-8")
TYPE_JS = (STATIC / "type-page.js").read_text(encoding="utf-8")
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


FIX = json.loads((FIXTURES / "trend_type.json").read_text(encoding="utf-8"))

# Rows as /api/transactions answers them (two invented Dining rows).
ROWS = {"transactions": [
    {"id": 9001, "date": "2026-09-14", "description": "GRAB FOOD 4012", "amount_sgd": 24.5, "currency": "SGD"},
    {"id": 9002, "date": "2026-09-02", "description": "DIN TAI FUNG", "amount_sgd": 61.0, "currency": "SGD"},
], "total": 2, "page": 1, "per_page": 50, "pages": 1}

# A stand-in DOM: elements by id, each holding innerHTML; the trend host takes
# the card's listener and gives its plot.
DOM = r"""
const els = {};
function makeEl(id) {
  const plot = { innerHTML: '', id: 'plot' };
  return { id, innerHTML: '', handlers: [], plot,
    addEventListener(type, fn) { this.handlers.push(fn); },
    querySelector(sel) { return sel === '.trend-plot' ? plot : null; },
    querySelectorAll() { return []; } };
}
globalThis.document = { getElementById: id => (els[id] = els[id] || makeEl(id)) };
globalThis.location = { hash: '#/books/type/7' };
const drawn = [];
"""


def run(body, *, answers=None):
    """Run `body` with the page loaded. `answers` maps a URL prefix to the JSON
    the stand-in server gives, or to {"status": 404} for a refusal."""
    lifted = [_const("SIGNS"), _const("DIGITS"), _const("formatters"), _function("digitsOf"),
              _function("groupFor"), _function("money"), _function("toMinor"), _function("esc"),
              _const("store"), _const("MONTHS"), _const("LONG_MONTHS"), _function("day"),
              _function("monthName"), _function("plural"), _function("monthEnd"),
              _const("pageLinkKinds"), _function("pageLink"), _function("pager")]
    default = {"/api/trend": FIX, "/api/transactions": ROWS}
    code = "\n".join([
        "globalThis.window = globalThis;",
        "const memory = new Map();",
        "globalThis.localStorage = { getItem: k => memory.has(k) ? memory.get(k) : null, setItem: (k, v) => memory.set(k, String(v)) };",
        *lifted,
        "const ROUTES = [];", _function("registerRoute"),
        "const ACT = {};",
        "const icon = n => `<i data-icon=\"${n}\"></i>`;",
        "let after = null; function afterRender(fn) { after = fn; }",
        "function spendRowTr(row) { return `<tr data-tx=\"${row.id}\"><td>${esc(row.description)}</td></tr>`; }",
        "async function act() { return null; }",
        "const chartTokens = () => ({ books: [] });",
        "const out = v => console.log(JSON.stringify(v));",
        "const asked = [];",
        "const ANSWERS = " + json.dumps({**default, **(answers or {})}) + ";",
        "async function get(url) {",
        "  asked.push(url);",
        "  const hit = Object.keys(ANSWERS).filter(k => url.startsWith(k)).sort((a, b) => b.length - a.length)[0];",
        "  const a = hit ? ANSWERS[hit] : { status: 404 };",
        "  if (a && a.status) { const e = new Error('no'); e.status = a.status; throw e; }",
        "  return JSON.parse(JSON.stringify(a));",
        "}",
        DOM,
        TREND_JS,
        "window.Trend.registerMode('bars', (plot, view, o) => { drawn.push({ view, o }); plot.innerHTML = 'BARS'; });",
        TYPE_JS,
        "const page = async (id = 7) => { const html = await ROUTES.find(([re]) => re.test('/books/type/' + id))[2](('/books/type/' + id).match(ROUTES[0][0]));",
        "  if (after) { const f = after; after = null; await f(); } return html; };",
        "const below = () => document.getElementById('tp-below').innerHTML;",
        "const settle = () => new Promise(r => setTimeout(r, 0));",
        "(async () => {", body, "})().catch(e => { console.error(e.stack); process.exit(1); });",
    ])
    res = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# --- the route and the links (cases 58, 45) ----------------------------------

def test_loading_the_page_registers_its_route_and_switches_type_links_on():
    got = run("out({ routes: ROUTES.map(([re, place]) => [re.test('/books/type/12'), place]),"
              " link: pageLink('type', 12, 'Transport'), merchant: pageLink('merchant', 202, 'Din Tai Fung') });")
    assert got["routes"] == [[True, "books"]]
    assert got["link"] == '<a class="link" href="#/books/type/12">Transport</a>'
    assert got["merchant"] == '<a class="link" href="#/books/merchant/202">Din Tai Fung</a>'


# --- case 36: Dining -----------------------------------------------------------

def test_case_36_dining_reads_chart_then_merchants_then_rows():
    got = run("const html = await page(7); await settle(); out({ html, below: below(), asked, drawn: drawn.length });")
    html, low = got["html"], got["below"]
    assert "Dining" in text(html)
    assert got["drawn"] == 1, "the chart is drawn once, by the card"
    # chart first, then the merchants, then the rows
    assert html.index('id="tp-trend"') < html.index('id="tp-below"')
    t = text(low)
    assert "Merchants in Dining" in t and "Household · 12M · largest first" in t
    order = ["Grab Food", "Din Tai Fung", "PS.Cafe", "Toast Box", "Swiggy", "14 others"]
    at = [t.index(n) for n in order]
    assert at == sorted(at), "largest first, the others last"
    for name, fig, pct in [("Grab Food", "S$ 2,480.00", "32%"), ("Din Tai Fung", "S$ 1,690.00", "22%"),
                           ("PS.Cafe", "S$ 1,080.00", "14%"), ("Toast Box", "S$ 840.00", "11%"),
                           ("Swiggy", "S$ 520.00", "7%"), ("14 others", "S$ 1,110.00", "14%")]:
        assert re.search(rf"{re.escape(name)}.*?{re.escape(fig)}.*?{re.escape(pct)}", t), name
    assert re.search(r"Swiggy\s*paid in ₹", t)
    assert t.count("paid in") == 1
    assert '<a class="link" href="#/books/merchant/202">Din Tai Fung</a>' in low
    assert "#/books/merchant/" not in low.split("14 others")[1].split("</tr>")[0], "the others are not one merchant"
    assert low.index("Merchants in Dining") < low.index('data-tx="9001"'), "the rows come after the table"
    rows_url = [u for u in got["asked"] if u.startswith("/api/transactions")][0]
    q = dict(p.split("=", 1) for p in rows_url.split("?", 1)[1].split("&"))
    assert q["types"] == "Dining" and q["expense_only"] == "true" and q["book"] == "Household"
    assert q["start"] == "2025-11-01" and q["end"] == "2026-10-31"


def test_one_book_on_offers_no_book_picker():
    got = run("await page(7); await settle(); out(below());")
    assert 'data-change="tp-book"' not in got


def test_several_books_on_offer_a_picker_and_the_table_follows_the_first_on():
    two = json.loads(json.dumps(FIX))
    two["books"].append(dict(two["books"][0], book="Larkspur", net=48000))
    larkspur = json.loads(json.dumps(two))
    larkspur["merchants"] = {"book": "Larkspur", "net": 48000,
                             "rows": [{"id": 201, "name": "Grab Food", "net": 48000, "share_pct": 100, "paid_in": None}],
                             "others": None}
    got = run("""
      await page(7); await settle();
      const first = below();
      const tp = window.TypePage;
      out({ first, picked: tp.tableBook(['Household', 'Larkspur'], ['Larkspur'], 'Household'),
            keep: tp.tableBook(['Household', 'Larkspur'], ['Household', 'Larkspur'], 'Larkspur'),
            none: tp.tableBook(['Household', 'Larkspur'], ['Household', 'Larkspur'], null) });
      ACT['tp-book']({ value: 'Larkspur' }); await settle(); await settle();
      out({ first, after: below(), asked });""", answers={"/api/trend": two, "/api/trend?subject=type&id=7&span=12M&stack=book&book=Larkspur": larkspur})
    assert 'data-change="tp-book"' in got["first"]
    assert "Household · 12M · largest first" in text(got["first"])
    assert "Larkspur · 12M · largest first" in text(got["after"])
    assert any("book=Larkspur" in u and u.startswith("/api/trend") for u in got["asked"])
    assert any("book=Larkspur" in u and u.startswith("/api/transactions") for u in got["asked"])


def test_the_table_follows_the_first_book_switched_on():
    got = run("const tp = window.TypePage; out([tp.tableBook(['Household', 'Larkspur'], ['Larkspur'], 'Household'),"
              " tp.tableBook(['Household', 'Larkspur'], ['Household', 'Larkspur'], 'Larkspur'),"
              " tp.tableBook(['Household', 'Larkspur'], ['Household', 'Larkspur'], null)]);")
    assert got == ["Larkspur", "Larkspur", "Household"]


# --- case 17: Stack by Book | Merchant -----------------------------------------

def test_case_17_stack_by_merchant_reads_the_merchant_stacking():
    got = run("""
      await page(7); await settle();
      const host = document.getElementById('tp-trend');
      const stack = window.Trend.stackControl(drawn[0].view);
      host.handlers[0]({ target: { closest: () => ({ dataset: { trend: 'stack', value: 'merchant' }, disabled: false }) } });
      await settle(); await settle();
      out({ stack, asked, drawn: drawn.length });""")
    assert [o["value"] for o in got["stack"]["options"]] == ["book", "merchant"]
    assert any(u.startswith("/api/trend?") and "stack=merchant" in u for u in got["asked"])
    assert got["drawn"] == 2, "the card draws the merchant stacking"


# --- case 25: a bar narrows the table and the rows ----------------------------

def test_case_25_dinings_sep_bar_narrows_the_table_and_the_rows():
    got = run("""
      await page(7); await settle();
      drawn[0].o.onMonth(['2026-09'], 'September 2026');
      await settle(); await settle();
      out({ below: below(), asked });""")
    t = text(got["below"])
    assert "Showing September 2026 · Show all" in t
    assert "Household · September 2026 · largest first" in t
    table = [u for u in got["asked"] if u.startswith("/api/trend?") and "months=" in u]
    assert table and "months=2026-09" in table[-1]
    rows = [u for u in got["asked"] if u.startswith("/api/transactions")][-1]
    assert "start=2026-09-01" in rows and "end=2026-09-30" in rows


def test_case_25_a_quarter_bar_narrows_to_its_months_and_show_all_clears():
    got = run("""
      await page(7); await settle();
      drawn[0].o.onMonth(['2026-07', '2026-08', '2026-09'], 'July to September 2026');
      await settle(); await settle();
      const narrowed = below(); const n = asked.length;
      ACT['tp-all'](); await settle(); await settle();
      out({ narrowed, after: below(), asked, n });""")
    assert "Showing July to September 2026" in text(got["narrowed"])
    narrowed = got["asked"][:got["n"]]
    assert any("months=2026-07%2C2026-08%2C2026-09" in u or "months=2026-07,2026-08,2026-09" in u for u in narrowed)
    rows = [u for u in narrowed if u.startswith("/api/transactions")][-1]
    assert "start=2026-07-01" in rows and "end=2026-09-30" in rows
    assert "Showing" not in text(got["after"])
    rows = [u for u in got["asked"][got["n"]:] if u.startswith("/api/transactions")][-1]
    assert "start=2025-11-01" in rows and "end=2026-10-31" in rows


def test_case_25_a_hatched_segment_goes_to_import():
    got = run("await page(7); await settle(); drawn[0].o.onImport(); out(location.hash);")
    assert got == "#/books/import"


# --- case 49: a type deleted elsewhere -----------------------------------------

def test_case_49_a_deleted_type_says_so_and_links_to_lists():
    got = run("const html = await page(99999); out({ html, asked });", answers={"/api/trend": {"status": 404}})
    assert "This type is no longer in fin" in text(got["html"])
    assert 'href="#/books/lists/types"' in got["html"]
    assert not any(u.startswith("/api/transactions") for u in got["asked"])


# --- case 60: a type with no rows in the span ------------------------------------

def test_case_60_gifts_on_6M_says_no_rows_and_drops_the_table_and_rows():
    gifts = json.loads(json.dumps(FIX))
    gifts.update(subject={"kind": "type", "id": 9, "name": "Gifts"}, span="6M", buckets=[], books=[],
                 stack_keys=[], stack_options=[], left_out=[], merchants=None, empty={"last_date": "2026-02-14"})
    got = run("const html = await page(9); await settle();"
              " out({ html, below: below(), plot: document.getElementById('tp-trend').plot.innerHTML, asked });",
              answers={"/api/trend": gifts})
    assert "No Gifts rows in the last 6 months; last row 14 Feb 2026." in text(got["plot"])
    assert "Merchants in" not in got["below"] and "data-tx" not in got["below"]
    assert not any(u.startswith("/api/transactions") for u in got["asked"])


def test_the_span_chips_stay_live_on_an_empty_span():
    gifts = json.loads(json.dumps(FIX))
    gifts.update(span="6M", buckets=[], books=[], stack_options=[], left_out=[], merchants=None,
                 empty={"last_date": "2026-02-14"})
    got = run("""
      await page(7); await settle();
      const host = document.getElementById('tp-trend');
      host.handlers[0]({ target: { closest: () => ({ dataset: { trend: 'span', value: '12M' }, disabled: false }) } });
      await settle(); await settle();
      out(asked);""", answers={"/api/trend": gifts})
    assert any(u.startswith("/api/trend?") and "span=12M" in u for u in got)


# --- the page's own stylesheet block ---------------------------------------------

def test_the_type_block_holds_only_type_page_rules():
    css = (STATIC / "trend.css").read_text(encoding="utf-8")
    block = css.split("/* ==== reserved: type ==== */")[1].split("/* ==== end: type ==== */")[0]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    selectors = re.findall(r"([^{}]+)\{", block)
    assert selectors, "the type block has the page's rules"
    for sel in selectors:
        if sel.strip().startswith("@media"):
            continue
        for part in sel.split(","):
            assert part.strip().startswith(".tp-"), part
