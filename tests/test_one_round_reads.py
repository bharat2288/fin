"""Speed pass 02: Home, the Account page and the Queue read in one round (spec cases 6-9).

The whole real app.js is loaded under node (as test_table_sort does) over a stand-in fetch
that records each request and answers only when the test releases it: a read that waits on
another is not asked for until that one is released. What is pinned is what the operator
waits on, the rounds a screen's reads take, and that each screen draws exactly what it drew
before (the snapshots in fixtures/one_round, drawn by the two-round code on main 35dfdd9).
Every name and figure is invented.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
SNAPSHOTS = Path(__file__).resolve().parent / "fixtures" / "one_round"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the screens' reads run only in JavaScript")

# The stand-in browser, on a fixed day. fetch answers from ANSWERS, the first [needle, value]
# whose needle the URL holds (an unknown URL is a 404), and only once released: release()
# answers every request held at that moment, one round, then lets the code ask its next.
PRELUDE = r"""
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.scrollTo = () => {};
globalThis.MutationObserver = class { observe() {} disconnect() {} };
const RealDate = Date;
globalThis.Date = class extends RealDate {
    constructor(...a) { if (a.length) super(...a); else super('2026-10-23T12:00:00'); }
    static now() { return new RealDate('2026-10-23T12:00:00').getTime(); }
};
const el = () => ({ innerHTML: '', dataset: {}, classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {}, removeAttribute() {},
    addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], appendChild() {}, style: {} });
const viewEl = el();
globalThis.document = { addEventListener() {}, querySelector: s => s === '#view' ? viewEl : null, querySelectorAll: () => [],
    getElementById: () => null, documentElement: el(), createElement: el, body: el(), activeElement: null };
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.sessionStorage = globalThis.localStorage;
globalThis.location = { hash: '#/' };
globalThis.history = { replaceState() {} };
let ANSWERS = [];
const asked = [], held = [];
globalThis.fetch = (url, opts) => {
    asked.push((opts && opts.method === 'POST' ? 'POST ' : '') + url);
    return new Promise(ok => held.push({ url, ok, sent: opts && opts.body ? JSON.parse(opts.body) : null }));
};
function answerOf(url, sent) {
    const hit = ANSWERS.find(([needle]) => url.includes(needle));
    let body = hit ? hit[1] : { error: 'not here' };
    // The prefill read answers only the rows it was asked for, as the server does.
    if (hit && sent && sent.tx_ids && body.prefill) {
        body = { prefill: Object.fromEntries(Object.entries(body.prefill).filter(([id]) => sent.tx_ids.includes(Number(id)))) };
    }
    return { ok: !!hit, status: hit ? 200 : 404, json: async () => JSON.parse(JSON.stringify(body)), headers: { get: () => null } };
}
const flush = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setTimeout(r, 0)); };
async function release(only = () => true) {
    const now = held.filter(h => only(h.url));
    now.forEach(h => held.splice(held.indexOf(h), 1));
    now.forEach(h => h.ok(answerOf(h.url, h.sent)));
    await flush();
}
/** Runs a view to its draw: {first: what it asked before any answer, rounds, html}. */
async function rounds(view) {
    let html = null, failed = null;
    view().then(h => { html = h; }, e => { failed = String(e && e.stack || e); });
    await flush();
    const first = asked.slice();
    let n = 0;
    while (html === null && failed === null && n < 10) { n++; await release(); }
    if (failed) throw new Error(failed);
    return { first, rounds: n, html };
}
"""


def node(body, answers):
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    code = "\n".join([PRELUDE, js, f"ANSWERS = {json.dumps(answers)};",
                      "const out = v => console.log(JSON.stringify(v));",
                      "(async () => {", body, "})().catch(e => { console.error(e && e.stack || e); process.exit(1); });"])
    res = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


TYPES = [
    {"id": 1, "name": "Dining", "display_name": "Dining", "kind": "spending", "proposed_book": "Household"},
    {"id": 2, "name": "Travel", "display_name": "Travel", "kind": "spending", "proposed_book": "Household"},
    {"id": 3, "name": "Advertising", "display_name": "Advertising", "kind": "spending", "proposed_book": "Moom"},
]
BOOKS = [{"name": "Household"}, {"name": "Moom"}]
ACCOUNTS = [
    {"id": 1, "name": "Wise", "type": "bank", "owner": "Joint", "currency": "SGD", "takes_a_figure": False},
    {"id": 2, "name": "citi card", "type": "card", "owner": "Moom", "currency": "SGD", "takes_a_figure": False},
]
REFS = [["/api/types", TYPES], ["/api/books", BOOKS], ["/api/accounts", ACCOUNTS],
        ["/api/review", []], ["/api/account-kinds", []]]


def _row(i, desc, amount, account="citi card", **more):
    return {"id": i, "date": f"2026-10-{i + 1:02d}", "description": desc, "amount_sgd": amount, "currency": "SGD",
            "account_name": account, "account_id": 2 if account == "citi card" else 1, "service_name": None, "type_id": None,
            "book": None, "display_type": None, **more}


def _list(rows):
    return {"transactions": rows, "total": len(rows), "pages": 1, "page": 1}


# The Queue: one transfer, four rows with no type (a sure guess, three guesses, none, and a
# refund), and two rows of a merchant checked each time.
REVIEW = _list([_row(1, "PAYNOW TO J TAN", 500, account="Wise")])
UNTYPED = _list([_row(2, "GRAB A-R12345", 12.5), _row(3, "INDIGOAIR 3124567", 300), _row(4, "KOPI CORNER 88", 4.2),
                 _row(6, "GRAB A-R99887", 18), _row(7, "INDIGOAIR REFUND", -20, account="Wise")])
MIXED = _list([_row(5, "AMAZON MKTP", 80, service_name="Amazon", type_id=3, book="Moom", display_type="Advertising"),
               _row(8, "AMAZON MKTP 2", 15, service_name="Amazon", type_id=3, book="Moom", display_type="Advertising")])
GUESSES = {"groups": [
    {"merchant": "Grab", "route": "prefill", "types": [{"type_id": 1, "name": "Dining", "probability": 0.95}], "tx_ids": [2, 6]},
    {"merchant": "Indigoair", "route": "top3", "types": [{"type_id": 2, "name": "Travel", "probability": 0.6},
        {"type_id": 1, "name": "Dining", "probability": 0.2}, {"type_id": 3, "name": "Advertising", "probability": 0.1}], "tx_ids": [3, 7]},
    {"merchant": "Kopi Corner", "route": "blank", "types": [], "tx_ids": [4]}]}
PREFILL = {"prefill": {
    "2": {"book": "Moom", "reason": "account", "account": "citi card", "account_kind": "card"},
    "6": {"book": "Moom", "reason": "account", "account": "citi card", "account_kind": "card"},
    "3": {"book": "Household", "reason": "last", "account": "citi card", "account_kind": "card"},
    "5": {"book": "Household", "reason": "last", "account": "citi card", "account_kind": "card"},
    "7": {"book": "Household", "reason": "type", "account": "Wise", "account_kind": "bank"}}}
SHEET = {"as_at": "2026-10-31", "net_worth_minor": 488000, "left_out": [], "note": None, "currency_change": None, "sections": [
    {"heading": "Bank", "owed": False, "total_minor": 500000, "left_out": [], "lines": [
        {"account_id": 1, "name": "Wise", "currency": "SGD", "balance_minor": 500000, "value_minor": 500000, "in_total": True,
         "rests_on": {"date": "2026-09-30", "age_days": 23, "source": "statement"}, "check": {"status": "ties"}, "since": "3 rows"}]},
    {"heading": "Cards", "owed": True, "total_minor": -12000, "left_out": [], "lines": [
        {"account_id": 2, "name": "citi card", "currency": "SGD", "balance_minor": -12000, "value_minor": -12000, "in_total": True,
         "rests_on": {"date": "2026-08-31", "age_days": 53, "source": "statement"}, "check": {"status": "off", "difference_minor": 4500}}]}]}
SUBS = [{"id": 1, "service_name": "Netflix", "amount": 22.98, "currency": "SGD", "frequency": "monthly", "periods": 1, "status": "active",
         "book": "Household", "account_name": "citi card", "tx_last_paid": "2026-07-03", "computed_renewal": "2026-11-03",
         # The server's verdict (bills.py, fin-redesign 10): the screens read it, never work it out.
         "state": "missed", "due": "2026-10-03", "every": "monthly"}]
CARDS = {"ref_month": "2026-09", "household": 1234.5, "moom": 300, "avg_household": 1100, "avg_moom": 280, "avg_months": 3,
         "household_rows": 40, "moom_out_rows": 4, "held_out_out_total": 500, "held_out_in_total": 0,
         "loan_principal": 0, "loan_instalments": 0, "loan_interest": 0,
         "history": [{"month": f"2026-{m:02d}", "household": 1000 + m, "moom": 200 + m} for m in range(1, 10)]}
QUEUE_READS = [["flow=review", REVIEW], ["types=__untyped__", UNTYPED], ["look=each", MIXED], ["/api/balance-sheet", SHEET],
               ["/api/statements/refused", {"refused": []}], ["/api/subscriptions", SUBS]]
QUEUE = [*QUEUE_READS, *REFS, ["/api/queue/guesses", GUESSES], ["/api/book-prefill", PREFILL],
         ["/api/suggestions/status", {"enabled": False}], ["/api/services", [{"id": 9, "name": "Amazon", "review_each_time": 1}]]]
HOME = [*QUEUE, ["/api/settings", {}], ["/api/dashboard/stat-cards", CARDS]]
TIES = {"ties": [{"date": "2026-09-30", "status": "ties", "opening_minor": 400000, "in_minor": 150000, "out_minor": 50000,
                  "closing_minor": 500000, "rows": 3, "row_ids": []}]}
ACCOUNT = [["/api/accounts/1/ties", TIES], *REFS, ["/api/balance-sheet", SHEET],
           ["/api/anchors", [{"date": "2026-09-30", "amount_minor": 500000}]], ["/api/statements/refused", {"refused": []}],
           ["/api/transactions?account_id=1&per_page=50&page=1", _list([_row(9, "SALARY OCT", -4000, account="Wise", type_id=1, display_type="Dining", book="Household")])]]

SCREENS = {"home": ("viewHome()", HOME), "account": ("S.month = '2026-10'; viewAccount(1)", ACCOUNT), "queue": ("viewQueue()", QUEUE)}


def _draw(screen):
    setup, _, call = SCREENS[screen][0].rpartition(";")
    return node(f"{setup}; out(await rounds(() => {call.strip()}));", SCREENS[screen][1])


def test_case_6_home_asks_for_the_guesses_before_any_answer():
    got = _draw("home")
    assert "/api/queue/guesses" in got["first"], got["first"]
    assert got["rounds"] == 1


def test_case_7_the_account_page_asks_for_its_rows_before_any_answer():
    got = _draw("account")
    assert any(u.startswith("/api/transactions?account_id=1&") for u in got["first"]), got["first"]
    assert got["rounds"] == 1


def test_case_8_the_queue_asks_for_its_prefills_once_its_own_rows_are_in():
    got = node("""
        const page = viewQueue();
        await flush();
        // Only the Queue's own reads answer: the guesses, merchants and switch stay held.
        const waits = u => u.includes('/api/queue/guesses') || u.startsWith('/api/services') || u.includes('/api/suggestions/status');
        await release(u => !waits(u));
        const early = asked.filter(u => u.startsWith('POST /api/book-prefill'));
        while (held.length) await release();
        await page;
        out({ early, held: held.map(h => h.url) });
    """, QUEUE)
    assert got["early"] == ["POST /api/book-prefill"], got
    assert got["held"] == []


@pytest.mark.parametrize("screen", list(SCREENS))
def test_case_9_each_screen_draws_what_it_drew_before(screen):
    html = _draw(screen)["html"]
    assert html == (SNAPSHOTS / f"{screen}.html").read_text(encoding="utf-8")
