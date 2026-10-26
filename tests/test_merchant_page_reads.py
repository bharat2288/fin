"""Walk-fixes 01: the merchant page asks for everything at once.

The reads live in static/app.js, so viewMerchant, the router's render, the quiet mark and
Edit the merchant are lifted out of the real file and run under node (as test_client_seams
does) over a stubbed `get` that records each URL asked for and answers only when released.
What is pinned is what the operator waits on: which reads the page makes, that none waits
on another, and that the change marks no longer hold up the first draw.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the page's reads run only in JavaScript")


def _function(name, prefix="function"):
    m = re.search(rf"^{prefix} {name}\(.*\}}$", APP_JS, re.M) or re.search(rf"^{prefix} {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _act(name):
    m = re.search(rf"^ACT\.{name} = async el => \{{.*?^\}};$", APP_JS, re.M | re.S)
    assert m, f"ACT.{name} not found in static/app.js"
    return m.group(0)


def _const(name):
    m = re.search(rf"^(?:const|let) {name} = .*?;$", APP_JS, re.M)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _node(code):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# A get that records each URL and answers only when the test releases it: a read that
# waits on another is not asked for until that one is released.
STUB_GET = r"""
globalThis.window = globalThis;
const asked = [], held = [];
let answers = {};
function get(url, opts) {
    asked.push(url);
    return new Promise((ok, bad) => held.push({ url, ok, bad }));
}
function answer(url) {
    if (url in answers) return answers[url];
    for (const [k, v] of Object.entries(answers)) if (url.startsWith(k + '?')) return v;
    return {};
}
async function release() {
    while (held.length) {
        const h = held.shift(), v = answer(h.url);
        if (v && v.__status) { const e = new Error(v.error || 'gone'); e.status = v.__status; h.bad(e); } else h.ok(v);
        await new Promise(r => setTimeout(r, 0));
    }
}
const flush = () => new Promise(r => setTimeout(r, 0));
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;');
const icon = n => `<i ${n}></i>`;
"""

# The page's drawing helpers, stubbed: what is pinned here is its reads, not its markup.
VIEW_STUBS = r"""
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const day = d => d; const tag = (c, t) => `<span>${t}</span>`; const toMinor = a => a; const money = a => String(a);
const sourceTagsHTML = () => ''; const rowAmount = () => ''; const struckAmount = () => ''; const dirTag = () => '';
const typeOptions = () => ''; const bookOptions = () => ''; const eachSegHTML = () => ''; const pager = () => '';
const mpTickedWords = () => ''; const mpCanApply = () => false; const bookVariesWords = () => ''; const bookVariesButtons = () => '';
const mpAccountCell = () => ''; const rowMark = id => `<mark data-row="${id}"></mark>`; const afterRender = () => {};
const partyBalancesHTML = () => '';
const sortOf = () => ({ key: null }); const sortServer = () => ({ sort: 'date', sort_dir: 'desc' }); const sortHead = (t, k) => k; const sortPicker = () => ''; const MP_SORT = {};
const S = { marks: { rows: {}, accounts: {} } };
const ACT = {};
"""


def _view_merchant_code(body):
    return "\n".join([STUB_GET, VIEW_STUBS, _const("MP_PAGE"), _const("merchantPage"), _function("monthQuery"),
        _function("mpCrumbs"), _function("servicesList", "async function"), _function("refs", "async function"),
        _function("viewMerchant", "async function"),
        "(async () => {", body, "})().catch(e => { console.log(JSON.stringify({ threw: String(e && e.message) })); });"])


MERCHANT = {"id": 104, "name": "Indigoair", "rows": 2, "first_date": "2026-09-01", "last_date": "2026-09-02",
            "accounts": 1, "rule_count": 1, "type_id": 3, "type_name": "Travel", "book": "Household"}


def test_case_4_every_read_is_asked_for_before_any_answers():
    out = _node(_view_merchant_code(f"""
        answers = {{ '/api/services/104/book-varies': {{ suggest: false }}, '/api/services/104': {json.dumps(MERCHANT)}, '/api/services': [],
            '/api/transactions': {{ transactions: [], total: 0, pages: 1 }}, '/api/types': [], '/api/books': [], '/api/accounts': [] }};
        const page = viewMerchant(104);
        await flush(); await flush();
        const before = asked.slice();
        await release();
        const html = await page;
        console.log(JSON.stringify({{ before, after: asked, h1: /<h1>Indigoair<\\/h1>/.test(html) }}));
    """))
    before = out["before"]
    assert any(u == "/api/services/104" for u in before), before
    assert any(u.startswith("/api/transactions?service_id=104&") for u in before), before
    assert "/api/services/104/book-varies" in before
    assert "/api/types" in before and "/api/books" in before
    # A party's balances (fin-redesign 21) are read in the same round.
    assert "/api/services/104/balances" in before
    # Nothing is asked for once the first answers arrive: one round, nothing waits on another.
    assert out["after"] == before
    assert out["h1"]


def test_case_6_opening_a_merchant_reads_no_merchants_list():
    out = _node(_view_merchant_code(f"""
        answers = {{ '/api/services/104/book-varies': {{ suggest: false }}, '/api/services/104': {json.dumps(MERCHANT)}, '/api/services': [],
            '/api/transactions': {{ transactions: [], total: 0, pages: 1 }}, '/api/types': [], '/api/books': [], '/api/accounts': [] }};
        const page = viewMerchant(104);
        await flush(); await release(); await page;
        console.log(JSON.stringify(asked));
    """))
    assert "/api/services" not in out
    assert not [u for u in out if u.startswith("/api/services?")]


def test_case_8_a_merchant_no_longer_in_fin_says_so():
    out = _node(_view_merchant_code("""
        answers = { '/api/services/104/book-varies': { __status: 404, error: 'This merchant is no longer in fin' },
            '/api/services/104': { __status: 404, error: 'gone' },
            '/api/transactions': { transactions: [], total: 0, pages: 1 }, '/api/types': [], '/api/books': [], '/api/accounts': [] };
        const page = viewMerchant(104);
        await flush(); await release();
        console.log(JSON.stringify(await page));
    """))
    assert isinstance(out, str), out
    assert "This merchant is no longer in fin." in out and 'href="#/books/lists/merchants"' in out


def test_case_8_a_failure_other_than_gone_is_still_an_error():
    out = _node(_view_merchant_code("""
        answers = { '/api/services/104/book-varies': { suggest: false }, '/api/services/104': { __status: 500, error: 'broken' },
            '/api/transactions': { transactions: [], total: 0, pages: 1 }, '/api/types': [], '/api/books': [], '/api/accounts': [] };
        const page = viewMerchant(104).catch(e => ({ threw: e.message, status: e.status }));
        await flush(); await release();
        console.log(JSON.stringify(await page));
    """))
    assert out == {"threw": "broken", "status": 500}


def _render_code(body):
    # The router with a page that draws the quiet mark on row 5 and account 9.
    return "\n".join([STUB_GET, r"""
const S = { marks: { rows: {}, accounts: {} } };
const slots = [];
const view = { innerHTML: '', __after: null };
const $ = sel => sel === '#view' ? view : null;
function $$(sel) {
    // The page's mark slots, read back out of what was drawn.
    if (sel === '[data-place]') return [];
    const key = sel.includes('row') ? 'row' : 'account';
    return [...view.innerHTML.matchAll(new RegExp(`<span class="cmark-at" data-mark-${key}="(\\d+)">`, 'g'))].map(m => {
        const el = { dataset: key === 'row' ? { markRow: m[1] } : { markAccount: m[1] }, innerHTML: '' };
        slots.push(el); return el;
    });
}
globalThis.location = { hash: '#/books/merchant/1' };
let afterNextRender = null, renderToken = 0;
let frames = 0; function refreshFrame() { frames++; }
let drawn = 0;
const ROUTES = [[/^\/books\/merchant\/(\d+)$/, 'books', () => { drawn++; return `<h1>M</h1>${rowMark(5)}${accountMark(9)}`; }]];
""", _function("claudeMark"), _function("rowMark"), _function("accountMark"), _function("applyMarks"),
        _function("refs", "async function"), _function("render", "async function"),
        "(async () => {", body, "})().catch(e => { console.log(JSON.stringify({ threw: String(e && e.message) })); });"])


def test_case_7_the_marks_read_does_not_hold_up_the_first_draw():
    out = _node(_render_code("""
        answers = { '/api/changes/marks': { rows: { 5: 77 }, accounts: { 9: 78 } }, '/api/types': [], '/api/books': [], '/api/accounts': [] };
        // Every read but the marks answers; the marks are held.
        const marksHeld = () => held.filter(h => h.url === '/api/changes/marks');
        const p = render();
        await flush();
        while (held.some(h => h.url !== '/api/changes/marks')) {
            const i = held.findIndex(h => h.url !== '/api/changes/marks');
            const [h] = held.splice(i, 1); h.ok(answer(h.url)); await flush();
        }
        await flush(); await flush();
        const first = { drawn, h1: view.innerHTML.includes('<h1>M</h1>'), held: marksHeld().length };
        await release(); await p;
        console.log(JSON.stringify({ first, slots: slots.map(s => s.innerHTML), marks: S.marks }));
    """))
    assert out["first"] == {"drawn": 1, "h1": True, "held": 1}


def test_case_7_a_row_claude_changed_still_shows_its_dot():
    out = _node(_render_code("""
        answers = { '/api/changes/marks': { rows: { 5: 77 }, accounts: { 9: 78 } }, '/api/types': [], '/api/books': [], '/api/accounts': [] };
        const p = render();
        await flush(); await release(); await p;
        console.log(JSON.stringify({ slots: slots.map(s => [s.dataset, s.innerHTML]), marks: S.marks }));
    """))
    by = {json.dumps(d, sort_keys=True): html for d, html in out["slots"]}
    assert 'data-entry="77"' in by[json.dumps({"markRow": "5"})]
    assert 'data-entry="78"' in by[json.dumps({"markAccount": "9"})]
    assert out["marks"] == {"rows": {"5": 77}, "accounts": {"9": 78}}


def test_a_mark_drawn_from_the_last_read_goes_once_the_new_read_says_so():
    out = _node(_render_code("""
        S.marks = { rows: { 5: 70 }, accounts: {} };
        answers = { '/api/changes/marks': { rows: {}, accounts: {} }, '/api/types': [], '/api/books': [], '/api/accounts': [] };
        const p = render();
        await flush(); await release(); await p;
        console.log(JSON.stringify(slots.filter(s => s.dataset.markRow === '5').map(s => s.innerHTML)));
    """))
    assert out and all(h == "" for h in out)


def test_case_6_edit_the_merchant_reads_the_list_itself_and_offers_every_other():
    code = "\n".join([STUB_GET, r"""
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const merchantFields = () => '';
let sheet = null; function openSheet(title, body) { sheet = { title, body }; }
function toast(m) { sheet = { toast: m }; }
const ACT = {};
""", _function("servicesList", "async function"), _function("refs", "async function"), _act("merchant"), r"""
(async () => {
    answers = { '/api/services': [{ id: 1, name: 'Grab', txn_count: 4, rule_count: 1 }, { id: 2, name: 'Gojek', txn_count: 1, rule_count: 0 },
        { id: 3, name: 'Comfort', txn_count: 0, rule_count: 0 }], '/api/types': [], '/api/books': [], '/api/accounts': [] };
    const p = ACT.merchant({ dataset: { id: '1' } });
    await flush(); await release(); await p;
    const merge = (sheet.body || '').match(/<select id="mc-target">.*?<\/select>/s);
    console.log(JSON.stringify({ title: sheet.title, merge: merge ? merge[0] : null, asked }));
})().catch(e => { console.log(JSON.stringify({ threw: String(e && e.message) })); });
"""])
    out = _node(code)
    assert out.get("title") == "Grab", out
    assert "/api/services" in out["asked"]
    assert '<option value="2">Gojek</option>' in out["merge"] and '<option value="3">Comfort</option>' in out["merge"]
    assert 'value="1"' not in out["merge"]
