"""Ticket 32: Books -> Spending is net of refunds (trend spec, Net everywhere N1).

The seam is the page itself. The real `static/app.js` runs under node, in a
vm with a stub document, against the real Flask app on a throwaway port and a
temporary database seeded through tests/trend_fixtures.py. Rows go in through
the server, and what comes out is what `viewSpending` draws: the tile, the
section aside, the tables and the Chart.js bar config (Chart is a stub that
keeps its config). Nothing opens the real database.

Every name and figure is invented. Money is whole minor units in the seeds.
"""

import html as htmllib
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

import app as fin_app
import money
import trend_fixtures as tf

APP_JS_PATH = Path(__file__).resolve().parent.parent / "static" / "app.js"
APP_JS = APP_JS_PATH.read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

# app.js in a vm with a stub document. Today is 19 Oct 2026. `fetch` goes to
# the throwaway server. Chart keeps its config so a test can read the bars.
HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const [, appJs, base, expr] = process.argv;
const loud = name => new Proxy(function () {}, {
  get: (t, k) => k === Symbol.toPrimitive ? () => '' : (k === 'length' ? 0 : loud(name + '.' + String(k))),
  apply: () => loud(name + '()'), construct: () => loud('new ' + name),
});
const FIXED = new Date(2026, 9, 19, 12).getTime();
class FixedDate extends Date {
  constructor(...a) { a.length ? super(...a) : super(FIXED); }
  static now() { return FIXED; }
}
const viewEl = {};
const CHARTS = [];
const store = {};
const doc = {
  addEventListener() {}, querySelectorAll: () => [], documentElement: {},
  querySelector: sel => (sel === '#view' ? viewEl : null),
  getElementById: id => (id.startsWith('chart-') ? {} : null),
  createElement: () => loud('el'),
};
const ctx = {
  console, document: doc, Date: FixedDate, Intl, URLSearchParams, FormData: class {}, setTimeout, innerWidth: 1366,
  localStorage: { getItem: k => store[k] ?? null, setItem(k, v) { store[k] = v; } },
  location: { hash: '' }, matchMedia: () => ({ matches: false, addEventListener() {} }),
  getComputedStyle: () => ({ getPropertyValue: () => '' }), addEventListener() {}, scrollTo() {},
  fetch: (u, o) => fetch(new URL(u, base), o),
  Chart: class { constructor(canvas, cfg) { CHARTS.push(cfg); } destroy() {} },
  __viewEl: viewEl, __charts: CHARTS,
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(appJs, 'utf8'), ctx, { filename: 'app.js' });
(async () => {
  const out = await vm.runInContext('(async () => (' + expr + '))()', ctx);
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _run(expr, base="http://127.0.0.1:1"):
    done = subprocess.run([NODE, "-e", HARNESS, str(APP_JS_PATH), base, expr],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# What the page does with a book, a view and a period, as one expression.
PAGE = """(async () => {
  S.month = '2026-09';
  Object.assign(S.spending, {book: %(book)s, view: %(view)s, span: %(span)s, types: [], search: '', account: '', oneOffs: true, fold: false});
  const html = await viewSpending();
  __viewEl.__after && __viewEl.__after();
  const flat = x => [].concat(x).join(' | ');
  const charts = __charts.map(cfg => {
    const data = cfg.data.datasets[0].data;
    const hover = data.map((raw, i) => flat(cfg.options.plugins.tooltip.callbacks.label({datasetIndex: 0, dataIndex: i, raw})));
    return {labels: cfg.data.labels.map(l => [].concat(l)[0]), net: data, hover, tick: cfg.options.scales.y.ticks.callback(-1500)};
  });
  return {html, charts};
})()"""


def page(base, book="Household", view="type", span="month"):
    return _run(PAGE % {"book": json.dumps(book), "view": json.dumps(view), "span": json.dumps(span)}, base)


@pytest.fixture
def server(temp_db):
    """The real app on a throwaway local port, over the temporary database."""
    srv = make_server("127.0.0.1", 0, fin_app.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()


# --- reading what the page drew ----------------------------------------------


def words(fragment):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def first(html, css_class, closing="div"):
    found = re.search(rf'class="{css_class}[^"]*"[^>]*>(.*?)</{closing}>', html, re.S)
    assert found, f"no .{css_class} on the page"
    return words(found.group(1))


def tile(html, nth=0):
    """The nth book tile: its figure, its sub line, its chip and its notes."""
    block = re.split(r'<div class="stat-tile(?: hatch)?">', html)[1 + nth].split("</section>")[0]
    chip = re.search(r'<span class="delta-chip[^>]*>(.*?)</span>', block, re.S)
    other = re.search(r'<p class="small muted sl-other">(.*?)</p>', block, re.S)
    return {
        "figure": first(block, "stat-tile__figure"),
        "sub": first(block, "stat-tile__sub"),
        "chip": words(chip.group(1)) if chip else None,
        "other": words(other.group(1)) if other else None,
    }


def aside(html):
    return first(html, "section-header__aside", "span")


def foot(html):
    return first(html.split('<section class="card">')[1], "card-foot", "p")


def table(html):
    """The By type / By merchant table: one list of cell texts per row, in order."""
    rows = re.findall(r'<tr class="clickable" data-act="spend-drill".*?</tr>', html, re.S)
    return [[words(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)] for r in rows]


# --- seeding -------------------------------------------------------------------


def seed_household(conn, other_refund=6250):
    """Household, Sep 2026, on S$ accounts: spent S$ 4,212.50 and refunded
    S$ 312.50 (with the default `other_refund`) -> net S$ 3,900.00.

    Inside it: a merchant hidden from expense views (Tidy Storage, S$ 45.00), a
    Shopping purchase of S$ 600.00 refunded S$ 250.00, and a refund in a type
    with no spend (Entertainment). Outside it: a rupee row (Swiggy, Rs 3,450).
    August has more refunded than spent, so its bar goes below the axis.
    `other_refund` is the Entertainment refund; the Groceries row makes up the
    rest so the net stays S$ 3,900.00 whatever it is."""
    card = tf.account(conn, "Everyday card", type="card")
    rupee = tf.account(conn, "Rupee card", type="card", currency="INR")
    stmts = {m: tf.statement(conn, card, m) for m in ("2026-07", "2026-08", "2026-09")}
    rupee_stmt = tf.statement(conn, rupee, "2026-09")
    fresh = tf.merchant(conn, "Fresh Mart", "Household", "Groceries")
    gadget = tf.merchant(conn, "Gadget House", "Household", "Shopping")
    tidy = tf.merchant(conn, "Tidy Storage", "Household", "Repairs & maintenance", hidden=True)
    cinema = tf.merchant(conn, "Cinema Hub", "Household", "Entertainment")
    swiggy = tf.merchant(conn, "Swiggy", "Household", "Dining")
    groceries = 350_500 + other_refund
    sep = stmts["2026-09"]
    tf.row(conn, sep, "FRESH MART", groceries, service_id=fresh, type_name="Groceries", day="2026-09-03")
    tf.row(conn, sep, "GADGET HOUSE", 60_000, service_id=gadget, type_name="Shopping", day="2026-09-05")
    tf.row(conn, sep, "GADGET HOUSE RETURN", -25_000, service_id=gadget, type_name="Shopping",
           flow="refund", day="2026-09-20")
    tf.row(conn, sep, "TIDY STORAGE", 4_500, service_id=tidy, type_name="Repairs & maintenance", day="2026-09-07")
    tf.row(conn, sep, "CINEMA HUB REFUND", -other_refund, service_id=cinema, type_name="Entertainment",
           flow="refund", day="2026-09-22")
    tf.row(conn, rupee_stmt, "SWIGGY", 345_000, service_id=swiggy, type_name="Dining", day="2026-09-12")
    # August: S$ 100.00 spent, S$ 400.00 back -> net S$ -300.00.
    tf.row(conn, stmts["2026-08"], "FRESH MART", 10_000, service_id=fresh, type_name="Groceries", day="2026-08-09")
    tf.row(conn, stmts["2026-08"], "GADGET HOUSE RETURN", -40_000, service_id=gadget, type_name="Shopping",
           flow="refund", day="2026-08-18")
    tf.row(conn, stmts["2026-07"], "FRESH MART", 100_000, service_id=fresh, type_name="Groceries", day="2026-07-09")
    return {"card": card}


# --- the signed sum and the groups (figures in, figures out) ----------------------


@needs_node
def test_totalsByCurrency_is_a_signed_sum_so_a_refund_is_taken_off():
    rows = ("[{amount_sgd: '100.00', currency: 'SGD'}, {amount_sgd: '-30.00', currency: 'SGD'},"
            " {amount_sgd: '3450.00', currency: 'INR'}, {amount_sgd: '-10.00', currency: 'INR'}]")
    assert _run(f"totalsByCurrency({rows})") == {"SGD": 7000, "INR": 344000}
    assert _run("totalsByCurrency([{amount_sgd: '-80.00', currency: 'SGD'}])") == {"SGD": -8000}


@needs_node
def test_groupRows_nets_a_refund_inside_its_group_and_sorts_a_negative_group_last():
    rows = [("Shopping", "600.00"), ("Shopping", "-250.00"), ("Entertainment", "-80.00"), ("Groceries", "3585.00")]
    js = "[" + ",".join(f"{{k:'{k}', amount_sgd:'{a}', currency:'SGD'}}" for k, a in rows) + "]"
    groups = _run(f"groupRows({js}, r => r.k).map(g => ({{name: g.name, rows: g.list.length, by: g.by, sgd: g.sgd}}))")
    assert groups == [
        {"name": "Groceries", "rows": 1, "by": {"SGD": 358500}, "sgd": 358500},
        {"name": "Shopping", "rows": 2, "by": {"SGD": 35000}, "sgd": 35000},
        {"name": "Entertainment", "rows": 1, "by": {"SGD": -8000}, "sgd": -8000},
    ]


# --- case 7: the tile, the chip, the foot and the aside ---------------------------


@needs_node
def test_case_7_the_tile_reads_net_after_refunds(conn, server):
    seed_household(conn)
    html = page(server)["html"]
    t = tile(html)
    assert t["figure"] == "S$ 3,900.00"
    assert t["chip"].endswith("after S$ 312.50 refunds")
    assert aside(html) == "net S$ 3,900.00 · spent S$ 4,212.50 · refunded S$ 312.50"
    assert "never taken off" not in html and "not taken off" not in html
    assert "net of refunds" in foot(html).lower()


# --- case 8: the chart ---------------------------------------------------------------


@needs_node
def test_case_8_the_bars_are_net_below_the_axis_and_the_hover_gives_spent_refunded_net(conn, server):
    seed_household(conn)
    chart = page(server)["charts"][0]
    labels, net, hover = chart["labels"], chart["net"], chart["hover"]
    assert labels[-1] == "Sep" and labels[-2] == "Aug"
    assert net[-1] == 3900.0
    assert net[-2] == -300.0 and net[-3] == 1000.0
    assert "Spent S$ 4,212.50" in hover[-1] and "Refunded S$ 312.50" in hover[-1] and "Net S$ 3,900.00" in hover[-1]
    assert "Spent S$ 100.00" in hover[-2] and "Refunded S$ 400.00" in hover[-2] and "Net S$ −300.00" in hover[-2]
    assert chart["tick"] == "−S$ 1.5k"


# --- case 9: the tables ----------------------------------------------------------------


@needs_node
def test_case_9_by_type_shares_are_of_net_a_refund_sits_in_its_group_and_a_negative_group_is_last(conn, server):
    seed_household(conn, other_refund=8000)          # refunds S$ 330.00 over the book, net still S$ 3,900.00
    html = page(server, view="type")["html"]
    rows = table(html)
    by_name = {r[0]: r for r in rows}
    # Shopping: spent 600.00, refunded 250.00 -> net 350.00, 9% of 3,900.00; two rows (the refund is one).
    assert by_name["Shopping"][1:] == ["2", "S$ 350.00", "9%"]
    # Entertainment: refunded 80.00, spent nothing -> below zero, last, no share.
    assert by_name["Entertainment"][1:] == ["1", "S$ −80.00", "—"]
    assert rows[-1][0] == "Entertainment"
    assert by_name["Groceries"][3] == "92%"
    assert "Back in" not in html and "not in the share" not in html


@needs_node
def test_case_9_by_merchant_puts_the_refund_in_its_own_merchant(conn, server):
    seed_household(conn, other_refund=8000)
    rows = table(page(server, view="merchant")["html"])
    by_name = {r[0]: r for r in rows}
    assert by_name["Gadget House"][1:] == ["2", "S$ 350.00", "9%"]
    assert by_name["Cinema Hub"][1:] == ["1", "S$ −80.00", "—"]
    assert rows[-1][0] == "Cinema Hub"


# --- case 11: one figure ------------------------------------------------------------------


@needs_node
def test_case_11_spending_and_home_state_one_figure_with_the_hidden_merchant_in_it(conn, client, server):
    seed_household(conn)
    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-09").get_json()
    html = page(server, view="merchant")["html"]
    assert money.to_minor(cards["household"]) == 390_000     # Home's number, hidden merchant counted, rupees out
    assert tile(html)["figure"] == "S$ 3,900.00"             # the same on Books -> Spending
    # The lists still hide the merchant; the figure counts it.
    assert "Tidy Storage" not in html
    # Rupees stay outside the S$ figure (S$ accounts only) and are named, not dropped.
    assert tile(html)["other"] and "₹ 3,450.00" in tile(html)["other"]


# --- case 52: a company book counts every account --------------------------------------------


@needs_node
def test_case_52_a_company_book_reads_every_account(conn, client, server):
    company_card = tf.account(conn, "Company card", type="card", owner="Moom")
    household_card = tf.account(conn, "Everyday card", type="card")
    own = tf.statement(conn, company_card, "2026-09")
    paid = tf.statement(conn, household_card, "2026-09")
    ads = tf.merchant(conn, "Ad Network", "Moom", "Advertising")
    tf.row(conn, own, "AD NETWORK", 116_000, service_id=ads, book="Moom", type_name="Advertising", day="2026-09-04")
    tf.row(conn, paid, "AD NETWORK", 32_000, service_id=ads, book="Moom", type_name="Advertising", day="2026-09-11")
    html = page(server, book="Moom")["html"]
    assert tile(html)["figure"] == "S$ 1,480.00"
    # Home's tile for the same book counts only what the household paid, as it does today.
    assert money.to_minor(client.get("/api/dashboard/stat-cards?ref_month=2026-09").get_json()["moom"]) == 32_000


# --- case 12: the old words leave the Spending strings -----------------------------------------------


def test_case_12_the_old_refund_words_are_gone_from_the_spending_code():
    start = APP_JS.index("// Spending: the old dashboard")
    end = APP_JS.index("// Bills: the subscriptions")
    spending = APP_JS[start:end].lower()
    for phrase in ("not taken off", "never taken off", "taken off it", "not in the share", "back in: refunds"):
        assert phrase not in spending, phrase


# --- the other views of the same page keep to the same rule ------------------------------------------


@needs_node
def test_twelve_months_is_the_net_of_the_twelve_and_every_book_keeps_its_own_tile(conn, server):
    seed_household(conn)
    company_card = tf.account(conn, "Company card", type="card", owner="Moom")
    own = tf.statement(conn, company_card, "2026-09")
    tf.row(conn, own, "AD NETWORK", 116_000, book="Moom", type_name="Advertising", day="2026-09-04")
    html = page(server, book="all", view="type", span="year")["html"]
    # Household: Jul 1,000.00 + Aug -300.00 + Sep 3,900.00; Moom has its own tile, nothing adds them.
    assert tile(html, 0)["figure"] == "S$ 4,600.00"
    assert tile(html, 1)["figure"] == "S$ 1,160.00"
    assert "never added together" in foot(html)


@needs_node
def test_the_flat_list_still_shows_every_row_with_a_refund_marked(conn, server):
    seed_household(conn)
    html = page(server, view="flat")["html"]
    assert "refund" in html and "GADGET HOUSE RETURN" in html
    assert "TIDY STORAGE" not in html          # the lists keep hiding the merchant
