"""Ticket 46: the account page's trend (static/account-trend.js; spec 15, Client C4).

The page script fills ticket 23's slot in viewAccount: it reads R3
(/api/accounts/<id>/balances), draws it through the trend card (trend.js) in its
line mode (trend-line.js), and narrows the account's rows when an in / out bar is
clicked. These tests run the real card and the real line mode under node, with
app.js's own money / esc / day / monthName / store lifted out of the file, a
stand-in get() answering the shared R3 fixtures (ticket 24), and a small stand-in
DOM. Figures and words in and out: the SVG's structure is never asserted (the
stand-in DOM only finds the clickable months the line mode listens on). Every
figure is invented.
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
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def _function(name):
    m = re.search(rf"^function {name}\(.*?^\}}$", APP_JS, re.M | re.S) or re.search(rf"^function {name}\(.*\}}$", APP_JS, re.M)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _const(name):
    m = re.search(rf"^const {name} = (?:\{{\n.*?^\}};|.*?;)$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# A stand-in DOM, just enough for the card, the line mode and the page script:
# an element keeps the markup written into it, finds a child by a class the
# markup names, and holds its listeners. The line mode's clickable months are
# found from the markup it writes (its hit rectangles carry data-k / data-bar).
FAKE_DOM = r"""
class El {
  constructor(sel, html) { this.sel = sel; this._html = html || ''; this.kids = {}; this.handlers = {}; this.dataset = {}; this.isConnected = true; this.style = {}; }
  set innerHTML(h) { this._html = h; this.kids = {}; }
  get innerHTML() { return this._html; }
  get clientWidth() { return 1366; }
  addEventListener(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); }
  fire(type, ev) { return (this.handlers[type] || []).map(fn => fn(ev || {})); }
  getBoundingClientRect() { return { left: 0, width: 100 }; }
  get offsetWidth() { return 10; }
  closest(sel) { return sel === '#view' ? VIEW : null; }
  querySelector(sel) {
    const cls = sel.replace(/^\./, '');
    if (!this._html.includes(cls)) return null;
    return this.kids[sel] = this.kids[sel] || new El(sel, '');
  }
  querySelectorAll(sel) {
    if (sel === '.trend-line__chart') {
      const key = sel + '*';
      if (!this.kids[key]) this.kids[key] = this._html.split('<div class="trend-line__chart">').slice(1).map(part => new El(sel, part));
      return this.kids[key];
    }
    if (sel === '.trend-line__hit') {
      const key = sel + '*';
      if (!this.kids[key]) {
        this.kids[key] = [...this._html.matchAll(/<rect class="trend-line__hit" data-k="(\d+)"( data-bar="1")?/g)].map(m => {
          const r = new El(sel, ''); r.dataset.k = m[1]; if (m[2]) r.dataset.bar = '1'; return r; });
      }
      return this.kids[key];
    }
    return [];
  }
}
const VIEW = new El('#view', '');
const text = el => {
  // Every piece of markup written under el, tags taken out, entities read back.
  const all = [];
  const walk = e => { all.push(e._html); Object.values(e.kids).forEach(k => Array.isArray(k) ? k.forEach(walk) : walk(k)); };
  walk(el);
  return all.join(' ').replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/\s+/g, ' ');
};
// A click on the account's in / out bar for a month (the second chart's hit areas).
function clickBar(section, month, payload) {
  const card = section.querySelector('.acct-trend__card');
  const plot = card.querySelector('.trend-plot');
  const k = String(payload.months.findIndex(m => m.month === month));
  const bars = plot.querySelectorAll('.trend-line__chart').flatMap(c => c.querySelectorAll('.trend-line__hit')).filter(r => r.dataset.bar && r.dataset.k === k);
  if (bars.length !== 1) throw new Error('no single bar for ' + month);
  bars[0].fire('click');
}
// A press on a card control (span chip, currency switch): the card listens on its element.
function pressCard(section, kind, value) {
  const card = section.querySelector('.acct-trend__card');
  const control = { dataset: { trend: kind, value }, disabled: false };
  return card.fire('click', { target: { closest: () => control } });
}
// A press on the page script's own control (Show all).
function pressPage(section, kind) {
  const control = { dataset: { acctTrend: kind } };
  return section.fire('click', { target: { closest: s => s === '[data-acct-trend]' ? control : null } });
}
"""


def run(body, *, answers=None, missing=None):
    """Run `body` with the page script loaded. `answers` maps a URL prefix to
    the JSON get() answers; `missing` is a list of URL prefixes that answer 404."""
    lifted = [_const("SIGNS"), _const("DIGITS"), _const("formatters"), _function("digitsOf"),
              _function("groupFor"), _function("money"), _function("toMinor"), _function("esc"),
              _const("store"), _const("MONTHS"), _const("LONG_MONTHS"), _function("day"),
              _function("monthName"), _function("plural"), _function("monthEnd"), _function("sentence"),
              _const("accountPage")]
    code = "\n".join([
        "globalThis.window = globalThis;",
        "const memory = new Map(); globalThis.localStorage = { getItem: k => memory.has(k) ? memory.get(k) : null, setItem: (k, v) => memory.set(k, String(v)) };",
        *lifted,
        "const out = v => console.log(JSON.stringify(v));",
        "globalThis.location = { hash: '#/books/account/31' };",
        "const urls = []; let rerenders = 0;",
        "function rerender() { rerenders++; }",
        "function icon(n) { return ''; }",
        "async function act() { return null; }",
        "const ANSWERS = " + json.dumps(answers or {}) + ";",
        "const MISSING = " + json.dumps(missing or []) + ";",
        """async function get(url) {
             urls.push(url);
             if (MISSING.some(p => url.startsWith(p))) { const e = new Error('This account is no longer in fin'); e.status = 404; throw e; }
             const k = Object.keys(ANSWERS).find(p => url.startsWith(p));
             if (!k) { const e = new Error('fin could not read ' + url); e.status = 500; throw e; }
             return JSON.parse(JSON.stringify(ANSWERS[k]));
           }""",
        FAKE_DOM,
        (STATIC / "trend.js").read_text(encoding="utf-8"),
        (STATIC / "trend-bars.js").read_text(encoding="utf-8"),
        (STATIC / "trend-line.js").read_text(encoding="utf-8"),
        (STATIC / "account-trend.js").read_text(encoding="utf-8"),
        "(async () => {", body, "})().catch(e => { console.error(e.stack); process.exit(1); });",
    ])
    res = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


SGD = "/api/accounts/31/balances"
INR = "/api/accounts/32/balances"


def show(account_id=31):
    return f"accountPage.id = {account_id}; const section = new El('section', ''); await window.AccountTrend.show(section, {account_id});"


# --- case 25: a bar narrows the rows; a hatched month goes to Import ----------

def test_case_25_the_sep_in_out_bar_narrows_the_rows_to_september_2026():
    p = load("balances_sgd")
    got = run(show() + f"""
      const before = text(section);
      clickBar(section, '2026-09', {json.dumps(p)});
      out({{ before, month: accountPage.month, page: accountPage.page, rerenders, hash: location.hash }});
    """, answers={SGD: p})
    assert got["month"] == "2026-09"
    assert got["page"] == 1
    assert got["rerenders"] == 1
    assert got["hash"] == "#/books/account/31"
    assert "Showing" not in got["before"]


def test_case_25_once_narrowed_the_page_says_so_and_show_all_undoes_it():
    p = load("balances_sgd")
    got = run("accountPage.month = '2026-09'; accountPage.page = 3;" + show() + """
      const shown = text(section);
      pressPage(section, 'all');
      out({ shown, month: accountPage.month, page: accountPage.page, rerenders });
    """, answers={SGD: p})
    assert "Showing September 2026 · Show all" in got["shown"]
    assert got["month"] is None and got["page"] == 1 and got["rerenders"] == 1


def test_case_25_a_hatched_month_goes_to_import_and_narrows_nothing():
    p = load("balances_sgd")
    assert next(m for m in p["months"] if m["month"] == "2026-07")["missing"] is True
    got = run(show() + f"""
      clickBar(section, '2026-07', {json.dumps(p)});
      out({{ month: accountPage.month, rerenders, hash: location.hash }});
    """, answers={SGD: p})
    assert got == {"month": None, "rerenders": 0, "hash": "#/books/import"}


def test_case_25_a_month_beside_the_hatch_still_narrows():
    p = load("balances_sgd")
    got = run(show() + f"""
      clickBar(section, '2026-08', {json.dumps(p)});
      out({{ month: accountPage.month, hash: location.hash }});
    """, answers={SGD: p})
    assert got == {"month": "2026-08", "hash": "#/books/account/31"}


# --- cases 37 and 54: the head card, the month under way ----------------------

def test_case_54_the_head_card_names_the_last_month_end_never_a_future_statement():
    got = run(show() + "out(text(section));", answers={SGD: load("balances_sgd")})
    assert "Balance S$ 55,000.00, 30 Sep 2026" in got
    assert "31 Oct" not in got


def test_case_37_the_card_reads_septembers_balance_and_in_and_out():
    got = run(show() + "out(text(section));", answers={SGD: load("balances_sgd")})
    assert "Balance S$ 55,000.00 on 30 Sep 2026 · in S$ 11,550.00 · out S$ 10,650.00" in got
    assert "Money in and money out" in got and "Balance at each month end" in got


def test_the_page_reads_the_remembered_span_and_a_chip_reads_the_new_one():
    p = load("balances_sgd")
    two = copy.deepcopy(p)
    two["span"] = "2Y"
    got = run(show() + """
      pressCard(section, 'span', '2Y');
      await new Promise(r => setTimeout(r, 0));
      out({ urls, span: window.Trend.span(), shown: text(section) });
    """, answers={SGD + "?span=2Y": two, SGD + "?span=12M": p})
    assert got["urls"] == [SGD + "?span=12M", SGD + "?span=2Y"]
    assert got["span"] == "2Y"


# --- case 29: a rupee account opens in ₹ with the switch; S$ has none ---------

def test_case_29_a_rupee_account_opens_in_rupees_and_its_switch_shows_sgd_at_the_rate():
    got = run(show(32) + """
      const inr = text(section);
      pressCard(section, 'cur', 'SGD');
      out({ inr, sgd: text(section) });
    """, answers={INR: load("balances_inr")})
    assert "Balance ₹ 5,20,000, 30 Sep 2026" in got["inr"]
    assert "in ₹ 62,400 · out ₹ 48,750" in got["inr"]
    assert "S$ 8,060.00" in got["sgd"] and "at 0.0155" in got["sgd"]
    assert "S$ at each month's end-of-month rate" in got["sgd"]


def test_case_29_an_sgd_account_has_no_switch():
    got = run(show() + """
      const card = section.querySelector('.acct-trend__card');
      out({ hasSwitch: card.innerHTML.includes('data-trend="cur"'), shown: text(section) });
    """, answers={SGD: load("balances_sgd")})
    assert got["hasSwitch"] is False
    assert "₹" not in got["shown"]


# --- case 38: a split card's part charts the whole card and says so -----------

@pytest.mark.parametrize("part_of", [
    {"whole_id": 33, "parts": [{"id": 33, "name": "Rewards card", "last_four": "3696"},
                               {"id": 34, "name": "Rewards card (7436)", "last_four": "7436"}],
     "text": "the whole card: parts 3696 and 7436"},
    "the whole card: parts 3696 and 7436",
])
def test_case_38_a_split_cards_part_says_it_charts_the_whole_card(part_of):
    p = load("balances_sgd")
    p["account"]["part_of"] = part_of
    got = run(show() + "out(text(section));", answers={SGD: p})
    assert "the whole card: parts 3696 and 7436" in got.lower()


def test_case_38_an_account_on_no_split_card_says_nothing_of_parts():
    got = run(show() + "out(text(section));", answers={SGD: load("balances_sgd")})
    assert "whole card" not in got.lower()


# --- case 49: an account deleted elsewhere -------------------------------------

def test_case_49_an_account_deleted_elsewhere_gets_its_page_and_a_link_to_lists():
    got = run(show() + "out({ view: VIEW.innerHTML, words: text(VIEW) });", missing=[SGD])
    assert "This account is no longer in fin" in got["words"]
    assert "Go to Lists" in got["words"]
    assert 'href="#/books/lists/accounts"' in got["view"]


def test_another_failure_is_said_in_the_card_and_the_page_stays():
    got = run(show() + "out({ view: VIEW.innerHTML, words: text(section) });", answers={})
    assert got["view"] == ""
    assert "fin could not read" in got["words"]


# --- cases 56 and 61: before the sheet starts; no anchor at all ----------------

def test_case_61_no_anchor_at_all_replaces_the_line_has_no_head_and_its_bars_still_narrow():
    p = load("balances_sgd")
    for m in p["months"]:
        m.update(balance=None, estimate=None, note=None)
    p["head"] = {"balance": None, "date": None}
    got = run(show() + f"""
      const shown = text(section);
      clickBar(section, '2026-09', {json.dumps(p)});
      out({{ shown, month: accountPage.month }});
    """, answers={SGD: p})
    assert "No balance yet: import a statement or add an anchor" in got["shown"]
    assert "Balance S$" not in got["shown"]
    assert "S$ 0.00" not in got["shown"]
    assert got["month"] == "2026-09"


def test_case_56_the_months_before_the_sheet_carry_its_note():
    got = run(show() + "out(text(section));", answers={SGD: load("balances_sgd")})
    assert "Balances start on 1 Jan 2026, as on the balance sheet" in got


# --- the slot -----------------------------------------------------------------

def test_it_fills_23s_slot_and_nothing_else():
    src = (STATIC / "account-trend.js").read_text(encoding="utf-8")
    assert "window.mountAccountTrend =" in src
    assert "window.mountAccountTrend?.()" in APP_JS
