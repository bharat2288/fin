"""Ticket 44: the merchant page's trend (static/merchant-trend.js; spec 15, Client C2).

The page's script runs under node with the real trend.js and trend-bars.js and the
app.js globals it reads lifted out of the real static/app.js (money, esc, store, day,
monthName, plural, monthEnd and the rows' month query). Figures go in as R1 payloads
(the shared fixture, or one built here in its shape) and come out through
window.MerchantTrend's functions, the card's markup text and what a bar's click asks
for. The SVG's structure is never read. Every figure is invented.
"""

import copy
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
PAGE_JS = (STATIC / "merchant-trend.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def _function(name):
    m = re.search(rf"^function {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _const(name):
    m = re.search(rf"^const {name} = (?:\{{\n.*?^\}};|.*?;)$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


STORAGE = """
const memory = new Map();
globalThis.localStorage = PRIVATE ? {
  getItem() { throw new Error('SecurityError'); }, setItem() { throw new Error('SecurityError'); } }
  : { getItem: k => memory.has(k) ? memory.get(k) : null, setItem: (k, v) => memory.set(k, String(v)) };
"""

# A stand-in element: the card writes its markup here and listens for clicks; the bars
# mode is wrapped so each draw is recorded with the opts the page passed, and the real
# model is worked out from the view the card handed it.
FAKES = """
function fakeEl() {
  const plot = { innerHTML: '' };
  return { innerHTML: '', handlers: {}, plot,
    addEventListener(type, fn) { this.handlers[type] = fn; },
    querySelector(sel) { return sel === '.trend-plot' ? plot : null; } };
}
function press(el, data) {
  const control = { dataset: data, disabled: false };
  return el.handlers.click({ target: { closest: () => control }, preventDefault() {} });
}
const drawn = [];
const realBars = window.Trend.modes.bars;
window.Trend.registerMode('bars', (plot, view, opts) => {
  drawn.push({ view, opts, model: window.Trend.bars.model(view, {}) });
  plot.innerHTML = '<svg></svg>';
});
const asked = [];
let answer = null;
async function get(url) { asked.push(url); if (answer instanceof Error) throw answer; return answer; }
let rerenders = 0;
function rerender() { rerenders += 1; }
globalThis.location = { hash: '#/books/merchant/101' };
const notFound = () => Object.assign(new Error('not found'), { status: 404 });
"""


def run(body, *, private=False, preload=None):
    lifted = [_const("SIGNS"), _const("DIGITS"), _const("formatters"), _function("digitsOf"),
              _function("groupFor"), _function("money"), _function("toMinor"), _function("esc"),
              _const("store"), _const("MONTHS"), _const("LONG_MONTHS"), _function("day"),
              _function("monthName"), _function("plural"), _function("monthEnd"),
              _function("monthQuery"), _const("merchantPage")]
    seed = "".join(f"memory.set({json.dumps(k)}, {json.dumps(v)});" for k, v in (preload or {}).items())
    code = "\n".join([
        "globalThis.window = globalThis;",
        f"const PRIVATE = {str(private).lower()};",
        STORAGE, seed, *lifted,
        "const out = v => console.log(JSON.stringify(v));",
        "async function act() { return null; }",
        "function icon(n) { return ''; }",
        (STATIC / "trend.js").read_text(encoding="utf-8"),
        (STATIC / "trend-bars.js").read_text(encoding="utf-8"),
        FAKES,
        PAGE_JS,
        "const MT = window.MerchantTrend;",
        "const FIX = " + json.dumps({"grab": fixture("trend_merchant")}) + ";",
        "const load = n => JSON.parse(JSON.stringify(FIX[n]));",
        "(async () => {", body, "})().catch(e => { console.error(e.stack); process.exit(1); });",
    ])
    # The script goes in on stdin: with the fixture it is past Windows' command-line limit.
    res = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30,
                         encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def few_payload(rows, first, last, net, name="Kopi Lane"):
    p = copy.deepcopy(fixture("trend_merchant"))
    p["subject"] = {"kind": "merchant", "id": 7, "name": name}
    p["few"] = {"rows": rows, "first_month": first, "last_month": last, "net": net}
    return p


def show(payload, extra=""):
    return run(f"""
      const host = fakeEl();
      MT.show(host, {json.dumps(payload)}, 7);
      {extra}
      out({{ html: host.innerHTML, draws: drawn.length }});""")


FEW_TAIL = "A chart appears once this merchant has 3 rows across 2 months."


# --- the globals it reads ------------------------------------------------------

def test_it_declares_the_app_js_globals_it_uses_and_copies_none():
    declared = re.search(r"/\* global ([^*]+)\*/", PAGE_JS)
    assert declared, "merchant-trend.js names the app.js globals it reads"
    names = {n.strip() for n in declared.group(1).split(",")}
    body = PAGE_JS.replace(declared.group(0), "")
    for name in names:
        assert re.search(rf"\b{name}\(|\b{name}\.|\b{name}\b", body), f"{name} is declared but not used"
        assert not re.search(rf"\bfunction {name}\(|\b(?:const|let|var) {name}\b", body), f"{name} is copied"


def test_it_fills_the_mount_slot():
    assert run("out(typeof window.mountMerchantTrend);") == "function"


# --- the few-rows line (cases 2, 3, 4) -----------------------------------------

def test_case_2_two_rows_one_month_is_a_line_not_a_chart():
    got = show(few_payload(2, "2026-03", "2026-03", 8400))
    assert "2 rows, both in March 2026: S$ 84 net. " + FEW_TAIL in got["html"]
    assert got["draws"] == 0
    assert 'data-trend="span"' not in got["html"]


def test_case_3_three_rows_one_month_is_still_the_line():
    p = few_payload(3, "2026-05", "2026-05", 13000, "Hana Florist")
    assert run(f"out(MT.plan({json.dumps(p)}));")["text"].startswith("3 rows, all in May 2026: S$ 130 net.")


def test_case_4_two_rows_two_months_counts_all_time_not_the_span():
    p = few_payload(2, "2026-02", "2026-04", 24000, "Sato Dental")
    got = run(f"out(MT.plan({json.dumps(p)}));")
    assert got["kind"] == "few"
    assert got["text"].startswith("2 rows, February to April 2026: S$ 240 net.")


def test_the_few_line_keeps_cents_when_there_are_any_and_names_both_years():
    p = few_payload(2, "2025-12", "2026-01", 8450)
    assert run(f"out(MT.plan({json.dumps(p)}).text);").startswith("2 rows, December 2025 to January 2026: S$ 84.50 net.")


# --- no rows in the span (case 59) ---------------------------------------------

def test_case_59_no_rows_in_the_span_replaces_the_chart_and_keeps_the_chips():
    p = copy.deepcopy(fixture("trend_merchant"))
    p.update(subject={"kind": "merchant", "id": 9, "name": "Pale Ale Co"}, span="6M",
             empty={"last_date": "2026-03-22"}, buckets=[], books=[])
    got = run(f"""
      const host = fakeEl();
      MT.show(host, {json.dumps(p)}, 9);
      out({{ html: host.innerHTML, plot: host.plot.innerHTML, draws: drawn.length }});""")
    assert "No rows in the last 6 months; last row 22 Mar 2026." in got["plot"]
    assert 'data-trend="span"' in got["html"] and got["draws"] == 0


def test_case_59_on_12M_the_chart_draws():
    got = run("const host = fakeEl(); MT.show(host, load('grab'), 101); out({ plan: MT.plan(load('grab')).kind, draws: drawn.length });")
    assert got == {"plan": "chart", "draws": 1}


# --- the chart: net, stacked by book, no grand total (cases 1, 5) ---------------

def test_case_1_and_5_sep_has_two_segments_a_cap_and_the_hover_and_no_total_anywhere():
    got = run("""
      const host = fakeEl();
      MT.show(host, load('grab'), 101);
      const sep = drawn[0].model.buckets.find(b => b.key === '2026-09');
      out({ segs: sep.segments.map(s => [s.key, s.net, s.cap]), lines: sep.hover.lines, html: host.innerHTML });""")
    assert got["segs"] == [["Household", 19440, [19440, 21240]], ["Larkspur", 6100, None]]
    assert got["lines"][:2] == ["Household · spent S$ 212.40 · refunded S$ 18.00 · net S$ 194.40",
                                "Larkspur · net S$ 61.00"]
    assert "255.40" not in json.dumps(got)


# --- books on and off, the summary (cases 13, 22) --------------------------------

def test_case_13_and_22_larkspur_off_draws_household_with_its_line_and_the_merchant_summary():
    got = run("""
      const host = fakeEl();
      MT.show(host, load('grab'), 101);
      press(host, { trend: 'book', value: 'Larkspur' });
      const one = { books: drawn[1].view.books_on, line: drawn[1].view.avg_line, html: host.innerHTML };
      press(host, { trend: 'book', value: 'Larkspur' });
      out({ one, back: drawn[2].view.books_on, backLine: drawn[2].view.avg_line });""")
    assert got["one"]["books"] == ["Household"] and got["one"]["line"] == 24000
    assert got["back"] == ["Household", "Larkspur"] and got["backLine"] is None
    html = got["one"]["html"]
    assert "Net S$ 2,640.00 · avg S$ 240.00/mo · " in html and "vs previous 12M +10%" in html
    assert "spent S$ 2,742.00 · refunded S$ 102.00" in html
    assert "last row 12 Oct 2026 · Household 31 rows · Larkspur 9 rows" in html
    assert "trend-rise" in html


# --- stack by (case 16) and the span (case 24) -----------------------------------

def test_case_16_amazon_offers_book_or_type_and_type_asks_for_the_type_stack():
    p = copy.deepcopy(fixture("trend_merchant"))
    p.update(subject={"kind": "merchant", "id": 55, "name": "Amazon"}, stack_options=["book", "type"])
    got = run(f"""
      const host = fakeEl();
      MT.state.id = 55;
      MT.show(host, {json.dumps(p)}, 55);
      const control = /Stack by/.test(host.innerHTML);
      answer = {json.dumps(p)};
      press(host, {{ trend: 'stack', value: 'type' }});
      await new Promise(r => setTimeout(r, 0));
      out({{ control, asked }});""")
    assert got["control"] is True
    assert got["asked"] == ["/api/trend?subject=merchant&id=55&span=12M&stack=type"]


def test_case_24_the_page_asks_for_the_span_remembered_on_the_trend_pages():
    body = """
      const host = fakeEl();
      answer = load('grab');
      await MT.load(101, host);
      out(asked);"""
    assert run(body, preload={"fin.trend.span": '"2Y"'}) == ["/api/trend?subject=merchant&id=101&span=2Y"]
    assert run(body, private=True) == ["/api/trend?subject=merchant&id=101&span=12M"]


def test_case_24_a_span_chip_asks_again_at_that_span_and_remembers_it():
    got = run("""
      const host = fakeEl();
      answer = load('grab');
      await MT.load(101, host);
      press(host, { trend: 'span', value: '2Y' });
      await new Promise(r => setTimeout(r, 0));
      out({ asked, kept: memory.get('fin.trend.span') });""")
    assert got["asked"][-1] == "/api/trend?subject=merchant&id=101&span=2Y" and got["kept"] == '"2Y"'


def test_case_24_in_a_private_window_a_span_chip_still_switches_only_the_remembering_is_lost():
    # Walk finding 19-private: with device storage refused, Trend.span() answers 12M,
    # so the chip's own value has to reach the read, and the page's redraws keep it.
    got = run("""
      const host = fakeEl();
      answer = load('grab');
      await MT.load(101, host);
      press(host, { trend: 'span', value: '2Y' });
      await new Promise(r => setTimeout(r, 0));
      await MT.load(101, host);
      out(asked);""", private=True)
    assert got == ["/api/trend?subject=merchant&id=101&span=12M",
                   "/api/trend?subject=merchant&id=101&span=2Y",
                   "/api/trend?subject=merchant&id=101&span=2Y"]


# --- a bar narrows the rows (case 25) --------------------------------------------

def test_case_25_the_sep_bar_narrows_the_rows_to_september():
    got = run("""
      const host = fakeEl();
      merchantPage.id = 101; merchantPage.page = 3;
      MT.show(host, load('grab'), 101);
      drawn[0].opts.onMonth(['2026-09'], 'September 2026');
      out({ month: merchantPage.month, page: merchantPage.page, rerenders,
            words: MT.showing(merchantPage.month), query: monthQuery(merchantPage.month) });""")
    assert got == {"month": "2026-09", "page": 1, "rerenders": 1, "words": "Showing September 2026",
                   "query": "&month=2026-09"}


def test_case_25_a_quarter_bar_narrows_to_its_months():
    got = run("""
      MT.show(fakeEl(), load('grab'), 101);
      drawn[0].opts.onMonth(['2026-07', '2026-08', '2026-09'], 'July to September 2026');
      out({ month: merchantPage.month, words: MT.showing(merchantPage.month), query: monthQuery(merchantPage.month) });""")
    assert got == {"month": ["2026-07", "2026-08", "2026-09"], "words": "Showing July to September 2026",
                   "query": "&start=2026-07-01&end=2026-09-30"}


def test_case_25_months_with_a_gap_narrow_to_the_whole_stretch_they_span():
    got = run("""
      MT.narrow(['2026-07', '2026-09']);
      out({ month: merchantPage.month, words: MT.showing(merchantPage.month) });""")
    assert got == {"month": ["2026-07", "2026-08", "2026-09"], "words": "Showing July to September 2026"}


def test_case_25_a_hatched_segment_goes_to_import_and_the_other_book_narrows():
    got = run("""
      MT.show(fakeEl(), load('grab'), 101);
      const jul = drawn[0].model.buckets.find(b => b.key === '2026-07');
      const larkspur = jul.segments.find(s => s.book === 'Larkspur' && !s.placeholder).click;
      const hatch = jul.segments.find(s => s.placeholder).click;
      drawn[0].opts.onImport();
      out({ larkspur, hatch, hash: location.hash });""")
    assert got["hatch"] == {"kind": "import"}
    assert got["larkspur"]["months"] == ["2026-07"]
    assert got["hash"] == "#/books/import"


def test_show_all_clears_the_narrowing_and_no_month_adds_nothing_to_the_query():
    got = run("""
      MT.narrow(['2026-09']);
      MT.showAll();
      out({ month: merchantPage.month, words: MT.showing(merchantPage.month), query: monthQuery(merchantPage.month), rerenders });""")
    assert got == {"month": None, "words": None, "query": "", "rerenders": 2}


def test_a_month_that_is_not_a_month_is_never_put_on_the_query():
    got = run("""
      MT.narrow(['2026-09&x=1']);
      out({ month: merchantPage.month, rerenders });""")
    assert got == {"month": None, "rerenders": 0}


# --- the mount: where the card goes, and the Rows card's note --------------------

FAKE_DOC = """
function node(name) {
  const n = { name, innerHTML: '', textContent: '', attrs: {}, handlers: {}, kids: [], className: '',
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(t, f) { this.handlers[t] = f; },
    appendChild(c) { this.kids.push(c); return c; },
    insertBefore(c, ref) { const i = this.kids.indexOf(c); if (i >= 0) this.kids.splice(i, 1);
      this.kids.splice(this.kids.indexOf(ref), 0, c); return c; },
    querySelector(sel) { return (this.find || {})[sel] || null; } };
  return n;
}
const facts = node('facts'), rows = node('rows'), head = node('head'), empty = node('empty');
const page = node('page');
page.kids = [facts, rows];
rows.parentNode = page;
rows.find = { '.card-head': head, '.card-empty': empty };
page.find = { 'section[aria-labelledby="mp-rows"]': rows };
globalThis.document = {
  querySelector: sel => sel === '#view .mp' ? page : null,
  createElement: tag => { const n = node(tag); const button = node('button'), plot = node('plot');
    Object.defineProperty(n, 'find', { get() { return { button, '.trend-plot': plot }; } }); n.button = button; return n; },
};
"""


def test_the_card_goes_between_the_facts_and_rows_and_the_rows_say_what_they_show():
    got = run(FAKE_DOC + """
      answer = load('grab');
      merchantPage.id = 101; merchantPage.month = ['2026-07', '2026-08', '2026-09'];
      window.mountMerchantTrend();
      await new Promise(r => setTimeout(r, 0));
      const order = page.kids.map(k => k.name);
      const note = head.kids[0];
      const first = { order, note: note && note.innerHTML, empty: empty.textContent, draws: drawn.length };
      note.button.handlers.click();
      out({ first, month: merchantPage.month, rerenders, asked });""")
    assert got["first"]["order"] == ["facts", "section", "rows"]
    assert "Showing July to September 2026" in got["first"]["note"] and "Show all" in got["first"]["note"]
    assert got["first"]["empty"] == "No rows from this merchant in July to September 2026."
    assert got["first"]["draws"] == 1
    assert got["month"] is None and got["rerenders"] == 1
    assert got["asked"] == ["/api/trend?subject=merchant&id=101&span=12M"]


def test_a_redraw_keeps_the_same_card_so_a_book_taken_off_stays_off():
    got = run(FAKE_DOC + """
      answer = load('grab');
      merchantPage.id = 101;
      window.mountMerchantTrend();
      await new Promise(r => setTimeout(r, 0));
      const card = page.kids[1];
      press(card, { trend: 'book', value: 'Larkspur' });
      window.mountMerchantTrend();
      await new Promise(r => setTimeout(r, 0));
      out({ same: page.kids[1] === card, books: drawn[drawn.length - 1].view.books_on, count: page.kids.length });""")
    assert got == {"same": True, "books": ["Household"], "count": 3}


# --- a merchant deleted elsewhere (case 49) --------------------------------------

def test_case_49_a_merchant_gone_from_fin_says_so_and_links_to_lists():
    got = run("""
      const host = fakeEl();
      answer = notFound();
      await MT.load(101, host);
      out(host.innerHTML);""")
    assert "This merchant is no longer in fin" in got
    assert 'href="#/books/lists/merchants"' in got
