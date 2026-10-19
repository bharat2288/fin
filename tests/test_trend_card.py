"""Ticket 36: the trend card core in static/trend.js (spec 15, Client C1).

Figures in and figures out through window.Trend's public functions, run under
node against the shared fixtures in tests/fixtures/trend/ (ticket 24). The
app.js globals the card declares (money, esc, store, act) are lifted out of the
real static/app.js, never rewritten here, so the ₹ grouping is fin's own.
Every figure is invented.
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
    return (FIXTURES / f"{name}.json").read_text(encoding="utf-8")


# A Map-backed device storage, or one that throws (a private window).
STORAGE = """
const memory = new Map();
globalThis.localStorage = PRIVATE ? {
  getItem() { throw new Error('SecurityError'); }, setItem() { throw new Error('SecurityError'); } }
  : { getItem: k => memory.has(k) ? memory.get(k) : null, setItem: (k, v) => memory.set(k, String(v)) };
"""


def run(body, *, private=False, preload=None):
    """Run `body` after app.js's real money / esc / store and trend.js; it
    prints one JSON value with out(...)."""
    lifted = [_const("SIGNS"), _const("DIGITS"), _const("formatters"), _function("digitsOf"),
              _function("groupFor"), _function("money"), _function("toMinor"), _function("esc"),
              _const("store"), _const("MONTHS"), _const("LONG_MONTHS"), _function("day"),
              _function("monthName"), _function("plural"), _function("monthEnd")]
    seed = "".join(f"memory.set({json.dumps(k)}, {json.dumps(v)});" for k, v in (preload or {}).items())
    code = "\n".join([
        "globalThis.window = globalThis;",
        f"const PRIVATE = {str(private).lower()};",
        STORAGE, seed, *lifted,
        "const out = v => console.log(JSON.stringify(v));",
        "const calls = [];",
        "let actAnswer = null;",
        "async function act(method, url, body, done) { calls.push({method, url, body}); return actAnswer; }",
        TREND_JS,
        "const load = n => JSON.parse(FIX[n]);",
        "const FIX = " + json.dumps({n: fixture(n) for n in
                                     ("trend_merchant", "trend_type", "balances_inr", "balances_sgd")}) + ";",
        "(async () => {", body, "})().catch(e => { console.error(e.stack); process.exit(1); });",
    ])
    # The script goes in on stdin: with the fixtures it is past Windows' command-line limit.
    res = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30,
                         encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


# A stand-in element: the card writes its markup here and listens for clicks;
# a click is a target whose closest() finds the pressed control.
FAKE_EL = """
function fakeEl() {
  const plot = { innerHTML: '' };
  return { innerHTML: '', handlers: {}, plot,
    addEventListener(type, fn) { this.handlers[type] = fn; },
    querySelector(sel) { return sel === '.trend-plot' ? plot : null; } };
}
function press(el, data, disabled = false) {
  const control = { dataset: data, disabled };
  return el.handlers.click({ target: { closest: () => control }, preventDefault() {} });
}
const drawn = [];
window.Trend.registerMode('bars', (plot, view) => drawn.push(view));
"""


# --- the globals it declares -------------------------------------------------

def test_it_declares_the_app_js_globals_it_uses_and_copies_none():
    declared = re.search(r"/\* global ([^*]+)\*/", TREND_JS)
    assert declared, "trend.js names the app.js globals it reads"
    names = {n.strip() for n in declared.group(1).split(",")}
    assert {"money", "esc", "store", "act"} <= names
    body = TREND_JS.replace(declared.group(0), "")
    for name in names:
        assert re.search(rf"\b{name}\(|\b{name}\.|typeof {name}\b", body), f"{name} is declared but not used"
        assert not re.search(rf"\bfunction {name}\(|\b(?:const|let|var) {name}\b", body), f"{name} is copied"


# --- span chips, remembered (cases 19, 24) -----------------------------------

def test_the_span_opens_at_12M_with_nothing_remembered():
    assert run("out(window.Trend.span());") == "12M"


def test_in_a_private_window_the_span_opens_at_12M_and_picking_one_does_not_throw():
    assert run("window.Trend.rememberSpan('2Y'); out(window.Trend.span());", private=True) == "12M"


def test_a_picked_span_is_remembered_on_the_trend_pages_key_not_spendings():
    got = run("""
      window.Trend.rememberSpan('2Y');
      out({ span: window.Trend.span(), trend: memory.get('fin.trend.span'),
            spending: memory.get('fin.spending') ?? null });""",
              preload={"fin.spending": json.dumps({"span": "month"})})
    assert got == {"span": "2Y", "trend": '"2Y"', "spending": json.dumps({"span": "month"})}


def test_a_remembered_value_that_is_not_a_chip_reads_as_12M():
    assert run("out(window.Trend.span());", preload={"fin.trend.span": '"9M"'}) == "12M"


def test_case_24_picking_2Y_on_one_card_opens_the_next_card_at_2Y():
    got = run(FAKE_EL + """
      const asked = [];
      const grab = fakeEl();
      window.Trend.card(grab, load('trend_merchant'), { onSpan: s => asked.push(s) });
      press(grab, { trend: 'span', value: '2Y' });
      out({ asked, next: window.Trend.span(), stored: memory.get('fin.trend.span') });""")
    assert got == {"asked": ["2Y"], "next": "2Y", "stored": '"2Y"'}


def test_case_19_the_chips_and_the_12_bars_reach_the_drawer():
    got = run(FAKE_EL + """
      const el = fakeEl();
      window.Trend.card(el, load('trend_merchant'), {});
      out({ chips: window.Trend.spanChips('12M'), bars: drawn[0].buckets.map(b => b.key),
            soFar: drawn[0].buckets.filter(b => b.so_far).map(b => b.key) });""")
    assert got["chips"] == [{"span": s, "pressed": s == "12M"} for s in ("6M", "12M", "YTD", "2Y", "All")]
    assert got["bars"][0] == "2025-11" and got["bars"][-1] == "2026-10" and len(got["bars"]) == 12
    assert got["soFar"] == ["2026-10"]


# --- legend chips (cases 13, 14) ----------------------------------------------

def test_case_13_taking_larkspur_off_leaves_household_and_draws_its_average_line():
    got = run("""
      const p = load('trend_merchant');
      const both = window.Trend.view(p, { hidden: [] });
      const hidden = window.Trend.toggleBook(p, [], 'Larkspur');
      const one = window.Trend.view(p, { hidden });
      const back = window.Trend.view(p, { hidden: window.Trend.toggleBook(p, hidden, 'Larkspur') });
      out({ hidden,
            books: [...new Set(one.buckets.flatMap(b => Object.values(b.by).map(x => x.book)))],
            top: one.top, bothTop: both.top, line: one.avg_line, bothLine: both.avg_line,
            backLine: back.avg_line });""")
    assert got["hidden"] == ["Larkspur"]
    assert got["books"] == ["Household"]
    # The top of the scale is Household's own largest month in the fixture
    # (Oct 2026, S$ 390.00); the spec's S$ 320.00 is its December on the check book.
    assert got["top"] == 39000 and got["bothTop"] == 39000 + 5240
    assert got["line"] == 24000 and got["bothLine"] is None and got["backLine"] is None


def test_case_14_the_last_book_showing_cannot_be_taken_off():
    got = run("""
      const p = load('trend_merchant');
      const hidden = ['Larkspur'];
      out({ chips: window.Trend.legend(p, hidden),
            again: window.Trend.toggleBook(p, hidden, 'Household') });""")
    assert got["chips"] == [{"book": "Household", "on": True, "disabled": True},
                            {"book": "Larkspur", "on": False, "disabled": False}]
    assert got["again"] == ["Larkspur"]


def test_the_legend_chip_takes_the_book_off_on_the_card_and_the_last_one_is_disabled():
    got = run(FAKE_EL + """
      const el = fakeEl();
      window.Trend.card(el, load('trend_merchant'), {});
      press(el, { trend: 'book', value: 'Larkspur' });
      const one = el.innerHTML;
      press(el, { trend: 'book', value: 'Household' }, true);
      out({ draws: drawn.length, lastLine: drawn[drawn.length - 1].avg_line,
            householdDisabled: /data-value="Household"[^>]*disabled/.test(one),
            still: el.innerHTML === one });""")
    assert got == {"draws": 2, "lastLine": 24000, "householdDisabled": True, "still": True}


# --- the summary (cases 15, 22) ------------------------------------------------

def test_case_15_each_book_its_own_net_and_average_and_nothing_adds_them():
    got = run("out(window.Trend.summary(load('trend_merchant'), { hidden: [] }));")
    assert [line["text"] for line in got["lines"]] == [
        "Household S$ 2,640.00 · avg S$ 240.00/mo",
        "Larkspur S$ 488.40 · avg S$ 40.70/mo"]
    assert "3,128.40" not in json.dumps(got)


def test_case_22_one_book_net_average_change_spent_refunded_and_the_merchant_tail():
    got = run("""out(window.Trend.summary(load('trend_merchant'), { hidden: ['Larkspur'] },
                                        { kind: 'merchant' }));""")
    assert got["lines"] == [{
        "book": "Household",
        "text": "Net S$ 2,640.00 · avg S$ 240.00/mo · vs previous 12M +10% · spent S$ 2,742.00 · refunded S$ 102.00",
        "change": {"text": "vs previous 12M +10%", "tone": "rise"}}]
    assert got["tail"] == "last row 12 Oct 2026 · Household 31 rows · Larkspur 9 rows"


def test_a_fall_takes_the_success_tone_and_no_previous_span_leaves_the_change_out():
    got = run("""
      const p = load('trend_merchant');
      p.books[0].vs_prev_pct = -5;
      const fall = window.Trend.summary(p, { hidden: ['Larkspur'] }).lines[0];
      p.books[0].vs_prev_pct = null;
      const none = window.Trend.summary(p, { hidden: ['Larkspur'] }).lines[0];
      out({ fall: fall.change, none });""")
    assert got["fall"] == {"text": "vs previous 12M −5%", "tone": "fall"}
    assert got["none"]["change"] is None and "vs previous" not in got["none"]["text"]
    assert "tail" not in run("out(window.Trend.summary(load('trend_merchant'), { hidden: [] }));")


# --- Stack by (case 18) --------------------------------------------------------

def test_case_18_the_control_follows_stack_options():
    got = run(FAKE_EL + """
      const asked = [];
      const el = fakeEl();
      window.Trend.card(el, load('trend_type'), { onStack: s => asked.push(s) });
      press(el, { trend: 'stack', value: 'merchant' });
      out({ grab: window.Trend.stackControl(load('trend_merchant')),
            dining: window.Trend.stackControl(load('trend_type')), asked,
            shown: el.innerHTML.includes('Stack by') });""")
    assert got["grab"] is None
    assert got["dining"] == {"current": "book", "options": [
        {"value": "book", "label": "Book"}, {"value": "merchant", "label": "Merchant"}]}
    assert got["asked"] == ["merchant"] and got["shown"] is True


# --- no saved rate, and Fetch (case 28) ---------------------------------------

def test_case_28_the_no_rate_month_is_named_and_left_out_and_opening_never_fetches():
    got = run(FAKE_EL + """
      const el = fakeEl();
      const p = load('trend_type');
      window.Trend.card(el, p, {});
      out({ calls, notes: window.Trend.noRate(p), summary: window.Trend.summary(p, { hidden: [] }),
            onCard: el.innerHTML.includes('₹ 2,100 not counted: no saved rate for June 2026.') });""")
    assert got["calls"] == []
    assert got["notes"] == [{"month": "2026-06", "currency": "INR",
                             "text": "₹ 2,100 not counted: no saved rate for June 2026."}]
    assert got["summary"]["left_out"] == "leaves out ₹ 2,100 (June 2026, no rate)"
    assert got["onCard"] is True


def test_case_28_fetch_goes_through_the_rates_fetch_and_a_failure_keeps_the_mark():
    got = run(FAKE_EL + """
      const refreshed = [];
      const el = fakeEl();
      window.Trend.card(el, load('trend_type'), { onRefresh: () => refreshed.push(1) });
      actAnswer = null;                      // the fetch failed: act toasted the reason
      await press(el, { trend: 'fetch', value: '2026-06', cur: 'INR' });
      const failed = { refreshed: refreshed.length, mark: el.innerHTML.includes('no saved rate for June 2026') };
      actAnswer = { ok: true, data: { created: true } };
      await press(el, { trend: 'fetch', value: '2026-06', cur: 'INR' });
      out({ calls, failed, refreshed: refreshed.length });""")
    assert got["calls"][0] == {"method": "POST", "url": "/api/rates/fetch",
                               "body": {"currency": "INR", "date": "2026-06-30"}}
    assert got["failed"] == {"refreshed": 0, "mark": True}
    assert got["refreshed"] == 1


def test_a_currency_fin_keeps_no_rate_for_is_named_in_the_summary():
    got = run("""
      const p = load('trend_merchant');
      p.left_out = [{ kind: 'no_currency', currency: 'USD', amount_minor: 4000, month: '2026-09',
                      reason: 'in USD, a currency fin keeps no rate for' }];
      out(window.Trend.summary(p, { hidden: [] }).left_out);""")
    assert got == "leaves out US$ 40.00 (in USD, a currency fin keeps no rate for)"


# --- ₹ / S$ (cases 29, 30) ----------------------------------------------------

def test_case_29_a_rupee_account_opens_in_rupees_with_a_switch_and_an_sgd_one_has_none():
    got = run("""
      const inr = load('balances_inr');
      out({ sw: window.Trend.currencySwitch(inr, 'INR'),
            sgdSw: window.Trend.currencySwitch(load('balances_sgd'), null),
            inr: window.Trend.accountSummary(inr, 'INR'),
            sgd: window.Trend.accountSummary(inr, 'SGD') });""")
    assert got["sw"] == {"current": "INR", "options": [{"value": "INR", "label": "₹"},
                                                       {"value": "SGD", "label": "S$"}]}
    assert got["sgdSw"] is None
    assert got["inr"] == {"text": "Balance ₹ 5,20,000 on 30 Sep 2026 · in ₹ 62,400 · out ₹ 48,750",
                          "note": None}
    assert got["sgd"] == {"text": "Balance S$ 8,060.00 on 30 Sep 2026 at 0.0155 · in S$ 967.20 · out S$ 755.62",
                          "note": "S$ at each month's end-of-month rate"}


def test_the_switch_on_the_card_redraws_the_months_in_sgd():
    got = run(FAKE_EL + """
      window.Trend.registerMode('line', (plot, view) => drawn.push(view));
      const el = fakeEl();
      window.Trend.card(el, load('balances_inr'), { mode: 'line' });
      press(el, { trend: 'cur', value: 'SGD' });
      const sep = v => v.months.find(m => m.month === '2026-09');
      out({ first: [drawn[0].cur, sep(drawn[0]).balance], then: [drawn[1].cur, sep(drawn[1]).balance,
            drawn[1].months.find(m => m.month === '2026-10').in] });""")
    assert got["first"] == ["INR", 52_000_000]
    # October has no saved rate yet, so it has no S$ figure: left out, never guessed.
    assert got["then"] == ["SGD", 806_000, None]


def test_case_30_large_rupee_figures_group_in_lakhs_and_convert_half_even():
    got = run("""out({ s: window.Trend.toSgd(12450000, '0.0155'), r: window.Trend.fmt(12450000, 'INR'),
                       half: window.Trend.toSgd(345000, '0.0155'), aug: window.Trend.toSgd(200000, '0.0154'),
                       paise: window.Trend.fmt(12450050, 'INR'), sgd: window.Trend.fmt(192975, 'SGD') });""")
    assert got == {"s": 192975, "r": "₹ 1,24,500", "half": 5348, "aug": 3080,
                   "paise": "₹ 1,24,500.50", "sgd": "S$ 1,929.75"}
