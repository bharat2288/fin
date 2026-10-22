"""Walk-fixes 05: every records table sorts by its columns (spec cases 9-14).

The tables are drawn in static/app.js (and type-page.js, expect-books.js), so the whole real
app.js is loaded under node over a stand-in document and a stand-in fetch that answers each
/api/ URL from invented payloads. The views are then called as the router calls them, and a
heading click goes through the real ACT handler. What is pinned is what the operator gets:
which headings sort, the order the rows come out in, the URL the server is asked, and the
phone's Sort select. Every name and figure is invented.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the tables are drawn only in JavaScript")

# The stand-in browser: enough of document, window and fetch for app.js to load and draw.
# fetch answers from ANSWERS: the full URL first, else its path; anything else is a 404.
PRELUDE = r"""
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.scrollTo = () => {};
const writes = [];
const el = () => ({ innerHTML: '', dataset: {}, classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {}, removeAttribute() {},
    addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], appendChild() {}, style: {} });
const viewEl = el();
globalThis.document = { addEventListener() {}, querySelector: s => s === '#view' ? viewEl : null, querySelectorAll: () => [],
    getElementById: () => null, documentElement: el(), createElement: el, body: el() };
globalThis.localStorage = { getItem: () => null, setItem: (k, v) => writes.push(k) };
globalThis.sessionStorage = globalThis.localStorage;
globalThis.location = { hash: '#/' };
globalThis.history = { replaceState() {} };
let ANSWERS = {};
const asked = [];
globalThis.fetch = async url => {
    asked.push(url);
    const path = url.split('?')[0];
    const hit = url in ANSWERS ? ANSWERS[url] : ANSWERS[path];
    const ok = hit !== undefined;
    return { ok, status: ok ? 200 : 404, json: async () => JSON.parse(JSON.stringify(ok ? hit : { error: 'not here' })), headers: { get: () => null } };
};
"""

TYPES = [
    {"id": 1, "name": "Dining", "display_name": "Dining", "kind": "spending", "covers": "restaurants and cafes", "not_for": "groceries", "proposed_book": "Household"},
    {"id": 2, "name": "Travel", "display_name": "Travel", "kind": "spending", "covers": "flights", "not_for": "", "proposed_book": "Household"},
    {"id": 3, "name": "Advertising", "display_name": "Advertising", "kind": "spending", "covers": "paid adverts", "not_for": "software", "proposed_book": "Moom"},
]
INCOME = [
    {"id": 11, "name": "Salary", "display_name": "Salary", "kind": "income", "covers": "pay", "not_for": ""},
    {"id": 12, "name": "Interest", "display_name": "Interest", "kind": "income", "covers": "bank interest", "not_for": "dividends"},
]
BOOKS = [{"name": "Household"}, {"name": "Moom"}]
ACCOUNTS = [
    {"id": 1, "name": "Wise", "type": "bank", "owner": "Joint", "currency": "SGD", "takes_a_figure": False,
     "anchor": {"date": "2026-09-30", "amount_minor": 500000, "source": "statement"}},
    {"id": 2, "name": "citi card", "type": "card", "owner": "Bharat", "currency": "SGD", "takes_a_figure": False,
     "anchor": {"date": "2026-08-31", "amount_minor": -12000, "source": "statement"}},
    {"id": 3, "name": "HDFC", "type": "bank", "owner": "Bharat", "currency": "INR", "takes_a_figure": False, "anchor": None},
]
REFS = {"/api/types": TYPES, "/api/types?kind=income": INCOME, "/api/books": BOOKS, "/api/accounts": ACCOUNTS,
        "/api/review": [], "/api/account-kinds": []}

# Lists › Merchants, in the list's own order: grab, Amazon, Cold Storage.
SERVICES = [
    {"id": 1, "name": "grab", "book": "Household", "type_id": 1, "type_name": "Dining", "display_type": "Dining", "txn_count": 42, "rule_count": 3,
     "review_each_time": 0, "exclude_from_expense_views": 0, "is_one_off": 0},
    {"id": 2, "name": "Amazon", "book": "Moom", "type_id": 3, "type_name": "Advertising", "display_type": "Advertising", "txn_count": 7, "rule_count": 1,
     "review_each_time": 1, "exclude_from_expense_views": 0, "is_one_off": 0},
    {"id": 3, "name": "Cold Storage", "book": "", "type_id": 2, "type_name": "Travel", "display_type": "Travel", "txn_count": 120, "rule_count": 0,
     "review_each_time": 1, "exclude_from_expense_views": 0, "is_one_off": 0},
]


def node(body, *, answers=None, extra_js=()):
    """Load app.js (and any extra static files after it) and run `body`; its last line printed is the result."""
    js = [(STATIC / "app.js").read_text(encoding="utf-8")] + [(STATIC / f).read_text(encoding="utf-8") for f in extra_js]
    code = "\n".join([PRELUDE, *js, f"ANSWERS = {json.dumps({**REFS, **(answers or {})})};",
                      "const out = v => console.log(JSON.stringify(v));",
                      "const click = (table, key) => ACT.sort({ dataset: { table, key } });",
                      "const pick = (table, value) => ACT['sort-pick']({ dataset: { table }, value });",
                      "(async () => {", body, "})().catch(e => { console.error(e && e.stack || e); process.exit(1); });"])
    res = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def heads(html, table):
    """The column keys whose heading sorts `table`, in heading order."""
    return re.findall(rf'data-act="sort" data-table="{re.escape(table)}" data-key="([\w-]+)"', html)


def merchant_names(html):
    return re.findall(r'<td class="sl-name"><b>([^<]+)</b>', html)


# --- case 9 -----------------------------------------------------------------------

def test_case_9_merchants_rows_cycles_up_down_own_order_and_search_and_chips_keep_it():
    got = node("""
        const draw = () => viewLists('merchants');
        const seen = [await draw()];
        click('merchants', 'rows'); seen.push(await draw());
        click('merchants', 'rows'); seen.push(await draw());
        click('merchants', 'rows'); seen.push(await draw());
        click('merchants', 'rows'); click('merchants', 'rows');          // ↓ again
        listState.filter = 'each'; seen.push(await draw());              // a chip keeps the sort
        listState.filter = 'all'; listState.search = 'o'; seen.push(await draw());   // so does a search
        out(seen);
    """, answers={"/api/services": SERVICES})
    own, up, down, back, chip, search = got
    assert merchant_names(own) == ["grab", "Amazon", "Cold Storage"]
    assert merchant_names(up) == ["Amazon", "grab", "Cold Storage"]       # 7, 42, 120
    assert 'data-key="rows" data-dir="asc"' in up
    assert merchant_names(down) == ["Cold Storage", "grab", "Amazon"]
    assert 'data-key="rows" data-dir="desc"' in down
    assert merchant_names(back) == ["grab", "Amazon", "Cold Storage"]     # the list's own order, ↕
    assert 'data-dir' not in re.search(r'data-table="merchants" data-key="rows"[^>]*>', back).group(0)
    assert merchant_names(chip) == ["Cold Storage", "Amazon"]
    assert merchant_names(search) == ["Cold Storage", "Amazon"]


# --- case 10 ----------------------------------------------------------------------

RULES = [
    {"id": 1, "pattern": "GRAB", "match_type": "contains", "service_name": "grab", "book": "Household", "type_name": "Dining", "display_type": "Dining",
     "min_amount": None, "max_amount": None, "rows_labelled": 5},
    {"id": 2, "pattern": "amzn", "match_type": "startswith", "service_name": "Amazon", "book": "Moom", "type_name": "Advertising", "display_type": "Advertising",
     "min_amount": None, "max_amount": None, "rows_labelled": 30},
    {"id": 3, "pattern": "COLD STORAGE", "match_type": "exact", "service_name": "Cold Storage", "book": "Household", "type_name": "Travel", "display_type": "Travel",
     "min_amount": None, "max_amount": None, "rows_labelled": None},
]
RATES = [
    {"date": "2026-09-30", "pair": "INR/SGD", "rate": "0.0155", "source": "ECB"},
    {"date": "2026-10-15", "pair": "INR/SGD", "rate": "0.0161", "source": "you"},
    {"date": "2026-08-31", "pair": "USD/SGD", "rate": "1.29", "source": "ECB"},
]
SHEET = {"as_at": "2026-10-31", "net_worth_minor": 488000, "left_out": [], "note": None, "currency_change": None, "sections": [
    {"heading": "Bank", "owed": False, "total_minor": 500000, "left_out": [], "lines": [
        {"account_id": 1, "name": "Wise", "currency": "SGD", "balance_minor": 500000, "value_minor": 500000, "in_total": True,
         "rests_on": {"date": "2026-09-30", "age_days": 31, "source": "statement"}, "check": {"status": "ties"}},
        {"account_id": 3, "name": "HDFC", "currency": "INR", "balance_minor": None, "value_minor": None, "in_total": False, "balance": "no figure",
         "rests_on": None, "check": None}]},
    {"heading": "Cards", "owed": True, "total_minor": -12000, "left_out": [], "lines": [
        {"account_id": 2, "name": "citi card", "currency": "SGD", "balance_minor": -12000, "value_minor": -12000, "in_total": True,
         "rests_on": {"date": "2026-08-31", "age_days": 61, "source": "statement"}, "check": {"status": "off"}}]}]}
SUBS = [
    {"id": 1, "service_name": "Netflix", "amount": 22.98, "currency": "SGD", "frequency": "monthly", "periods": 1, "monthly_sgd": 22.98, "status": "active",
     "book": "Household", "account_name": "citi card", "tx_last_paid": "2026-10-03", "computed_renewal": "2099-11-03", "display_type": "Dining"},
    {"id": 2, "service_name": "Adobe", "amount": 600, "currency": "SGD", "frequency": "yearly", "periods": 1, "monthly_sgd": 50, "status": "active",
     "book": "Household", "account_name": "Wise", "tx_last_paid": "2026-01-10", "computed_renewal": "2099-01-10", "display_type": "Advertising"},
    {"id": 3, "service_name": "iCloud", "amount": 3.98, "currency": "SGD", "frequency": "monthly", "periods": 1, "monthly_sgd": 3.98, "status": "active",
     "book": "Household", "account_name": "", "tx_last_paid": None, "computed_renewal": "2099-10-20", "display_type": None},
]


def _row(i, date, desc, minor, type_name, service=None):
    return {"id": i, "date": date, "description": desc, "amount_sgd": minor / 100, "amount_minor": minor, "counted_minor": minor, "currency": "SGD",
            "display_type": type_name, "type": type_name, "type_id": 1, "service_id": None, "service_name": service, "account_id": 1, "account_name": "Wise",
            "book": "Household", "flow_type": "expense"}


SPEND_ROWS = [_row(1, "2026-10-05", "GRAB 1", 1250, "Dining", "grab"), _row(2, "2026-10-20", "amzn mktp", 9900, "Advertising", "Amazon"),
              _row(3, "2026-10-11", "Cold Storage", 4300, "Travel", "Cold Storage"), _row(4, "2026-10-12", "GRAB 2", 800, "Dining", "grab")]


def _spending(view):
    month = "2026-10"
    return f"""
        S.month = '{month}';
        Object.assign(S.spending, {{ book: 'Household', view: '{view}', span: 'month', search: '', types: [], account: '', oneOffs: true, fold: false }});
        const realFetch = globalThis.fetch;
        globalThis.fetch = async url => url.startsWith('/api/transactions?')
            ? {{ ok: true, status: 200, json: async () => ({{ transactions: JSON.parse(JSON.stringify({json.dumps(SPEND_ROWS)})), total: 4, pages: 1, page: 1 }}), headers: {{ get: () => null }} }}
            : realFetch(url);
    """


TIES = {"ties": [
    {"date": "2026-09-30", "status": "ties", "opening_minor": 400000, "in_minor": 150000, "out_minor": 50000, "closing_minor": 500000, "rows": 3, "row_ids": []},
    {"date": "2026-08-31", "status": "off", "opening_minor": 300000, "in_minor": 20000, "out_minor": 90000, "closing_minor": 400000, "difference_minor": 170000,
     "rows": 2, "row_ids": []},
    {"date": "2026-07-31", "status": "ties", "opening_minor": None, "closing_minor": 300000, "rows": 0, "row_ids": []}]}
PAST = [
    {"created_at": "2026-10-01 09:00:00", "accounts": ["Wise"], "total_lines": 40, "status": "committed", "statements": []},
    {"created_at": "2026-10-09 09:00:00", "accounts": ["citi card"], "total_lines": 12, "status": "failed", "statements": []},
    {"created_at": "2026-09-15 09:00:00", "accounts": ["Amex"], "total_lines": 300, "status": "committed", "statements": []},
]
ENTRY = {"id": 50, "changes": [
    {"table": "transactions", "op": "update", "row_id": 1, "currency": "SGD", "before": {"description": "GRAB 1", "date": "2026-10-05", "amount_minor": 1250, "notes": "a", "account_id": 1},
     "after": {"description": "GRAB 1", "date": "2026-10-05", "amount_minor": 1250, "notes": "b", "account_id": 1}},
    {"table": "transactions", "op": "update", "row_id": 2, "currency": "SGD", "before": {"description": "amzn mktp", "date": "2026-10-20", "amount_minor": 9900, "notes": "a", "account_id": 2},
     "after": {"description": "amzn mktp", "date": "2026-10-20", "amount_minor": 9900, "notes": "b", "account_id": 2}},
    {"table": "transactions", "op": "update", "row_id": 3, "currency": "SGD", "before": {"description": "Cold Storage", "date": "2026-10-11", "amount_minor": 4300, "notes": "a", "account_id": 1},
     "after": {"description": "Cold Storage", "date": "2026-10-11", "amount_minor": 4300, "notes": "b", "account_id": 1}}]}
TP_PAYLOAD = {"subject": {"name": "Dining"}, "currency": "SGD"}
TP_TABLE = {"book": "Household", "rows": [{"id": 1, "name": "grab", "net": 4000, "share_pct": 40}, {"id": 2, "name": "Amazon", "net": 6000, "share_pct": 60}],
            "others": {"count": 3, "net": 500, "share_pct": 5}}

# Each records table: how it is drawn, the keys whose headings sort it (control columns have none),
# one heading clicked, and the order the rows then come out in.
TABLES = {
    "merchants": ("await viewLists('merchants')", {"/api/services": SERVICES},
                  ["name", "book", "type", "rows", "rules"], "name", r'<td class="sl-name"><b>([^<]+)</b>', ["Amazon", "Cold Storage", "grab"]),
    "rules": ("await viewLists('rules')", {"/api/rules": RULES, "/api/rules/drift": {"count": 0}},
              ["pattern", "match", "merchant", "sets", "rows"], "rows", r'<b class="mono-ish">([^<]+)</b>', ["GRAB", "amzn", "COLD STORAGE"]),
    "accounts": ("S.month = '2026-10'; ANSWERS['/api/balance-sheet'] = " + json.dumps(SHEET) + "; await viewLists('accounts')", {},
                 ["name", "kind", "whose", "currency", "rests", "balance"], "balance", r'<a href="#/books/account/\d+"><b>([^<]+)</b>', ["citi card", "Wise", "HDFC"]),
    "rates": ("await viewLists('rates')", {"/api/rates": RATES},
              ["day", "pair", "rate", "source"], "day", r'<td class="num sl-name">([^<]+)</td>', ["31 Aug 2026", "30 Sep 2026", "15 Oct 2026"]),
    "types": ("await viewLists('types')", {},
              ["name", "covers", "notfor", "book"], "covers", r'<td class="sl-name"><b>([^<]+)</b>', ["Travel", "Advertising", "Dining"]),
    "bills": ("await viewBills()", {"/api/subscriptions": SUBS},
              ["name", "amount", "often", "month", "from", "last", "next"], "month", r'<td class="sl-name"><b>([^<]+)</b>', ["iCloud", "Netflix", "Adobe"]),
    "spend-rows": (_spending("flat") + "await viewSpending()", {},
                   ["date", "description", "type", "amount"], "amount", r'data-act="row" data-tx="(\d+)"', ["4", "1", "3", "2"]),
    "spend-groups": (_spending("merchant") + "await viewSpending()", {},
                     ["name", "rows", "net", "share"], "name", r'data-act="spend-drill" data-key="([^"]+)"', ["Amazon", "Cold Storage", "grab"]),
    "acct-ties": ("S.month = '2026-10'; await viewAccount(1)", {"/api/balance-sheet": SHEET, "/api/accounts/1/ties": TIES, "/api/anchors": [],
                                                                 "/api/statements/refused": {"refused": []}, "/api/transactions": {"transactions": [], "total": 0, "pages": 1, "page": 1}},
                  ["date", "opening", "in", "out", "made", "says", "check"], "in", r'<td data-label="Statement"><b>([^<]+)</b>', ["31 Aug 2026", "30 Sep 2026", "31 Jul 2026"]),
    "imports": ("pastImportsHTML(" + json.dumps(PAST) + ")", {},
                ["when", "accounts", "rows", "state", "result"], "rows", r'<td class="r num" data-label="Rows">(\d+)</td>', ["12", "40", "300"]),
    "change-rows": ("await changeRowsHTML(" + json.dumps(ENTRY) + ")", {"/api/services": []},
                    ["row", "account", "amount"], "account", r'data-act="row" data-tx="(\d+)"', ["2", "1", "3"]),
}


@pytest.mark.parametrize("table", list(TABLES))
def test_case_10_each_records_table_sorts_by_its_data_columns(table):
    draw, answers, keys, key, row_re, sorted_order = TABLES[table]
    setup, _, expr = draw.rpartition(";")
    got = node(f"""
        const draw = async () => {{ {setup + ";" if setup else ""} return {expr.strip()}; }};
        const before = await draw();
        click('{table}', '{key}');
        out([before, await draw()]);
    """, answers=answers)
    before, after = got
    assert heads(before, table) == keys
    assert re.findall(row_re, after.split("Income kinds")[0]) == sorted_order
    assert f'data-table="{table}" data-key="{key}" data-dir="asc"' in after


def test_case_10_income_kinds_sort_on_their_own():
    before, after = node("""
        const before = await viewLists('types');
        click('income-kinds', 'name');
        out([before, await viewLists('types')]);
    """)
    kinds = lambda html: re.findall(r'<td class="sl-name"><b>([^<]+)</b>', html.split('Income kinds')[1])
    spending = lambda html: re.findall(r'<td class="sl-name"><b>([^<]+)</b>', html.split('Income kinds')[0])
    assert kinds(before) == ["Salary", "Interest"]
    assert kinds(after) == ["Interest", "Salary"]
    assert spending(after) == spending(before)       # one table's sort leaves the other's order alone


def test_case_10_control_columns_carry_no_sort():
    got = node("""
        out([await viewLists('merchants'), await viewLists('accounts'), await viewLists('rates')]);
    """, answers={"/api/services": SERVICES, "/api/rates": RATES, "/api/balance-sheet": SHEET})
    merchants, accounts, rates = got
    # The Edit column and the marks column of Merchants, the Edit of Accounts, the Change of Rates: plain headings.
    assert '<th><span class="sr-only">Edit</span></th>' in merchants
    assert merchants.count('data-act="sort" data-table="merchants"') == 5
    assert accounts.count('data-act="sort" data-table="accounts"') == 6
    assert rates.count('data-act="sort" data-table="rates"') == 4


def test_case_10_the_type_page_merchants_sort_and_others_stay_last():
    got = node(f"""
        const ctx = {{ book: 'Household', on: ['Household'], label: null }};
        const before = TypePage.merchantsHtml({json.dumps(TP_PAYLOAD)}, {json.dumps(TP_TABLE)}, ctx);
        click('tp-merchants', 'name');
        out([before, TypePage.merchantsHtml({json.dumps(TP_PAYLOAD)}, {json.dumps(TP_TABLE)}, ctx)]);
    """, extra_js=["trend.js", "type-page.js"])
    before, after = got
    assert heads(before, "tp-merchants") == ["name", "net", "share"]
    names = lambda html: re.findall(r'<tr(?: class="tp-others")?><td>(?:<a[^>]*>)?([^<]+)', html)
    assert names(before) == ["grab", "Amazon", "3 others"]
    assert names(after) == ["Amazon", "grab", "3 others"]


def test_case_10_the_type_page_rows_sort_on_the_server():
    got = node("""
        const s = { id: 1, months: null, page: 4 };
        const payload = { subject: { name: 'Dining' }, buckets: [{ months: ['2026-09', '2026-10'] }] };
        const before = TypePage.rowsUrl(s, payload, 'Household');
        click('tp-rows', 'amount');
        out([before, TypePage.rowsUrl(s, payload, 'Household'), TypePage.state.page]);
    """, extra_js=["trend.js", "type-page.js"])
    before, after, _ = got
    assert "sort=date&sort_dir=desc" in before
    assert "sort=amount&sort_dir=asc" in after


def test_case_10_expect_back_sections_sort_each_on_its_own():
    sheet = {"expect": json.loads((FIXTURES / "expect" / "books_sections.json").read_text(encoding="utf-8"))}
    got = node(f"""
        const sheet = {json.dumps(sheet)};
        const before = window.expectBooksSections(sheet);
        click('xb-expected_back', 'open');
        out([before, window.expectBooksSections(sheet)]);
    """, extra_js=["expect-mark.js", "expect-books.js"])
    before, after = got
    assert heads(before, "xb-expected_back") == ["date", "who", "open", "expected", "age"]
    section = lambda html, key: html.split(f'data-section="{key}"')[1].split("</section>")[0]
    opens = lambda html: [int(k.split(":")[1]) for k in re.findall(r'<tr data-key="([^"]+)"', section(html, "expected_back"))]
    lines = sheet["expect"]["sections"][0]["lines"]
    by_sgd = [int(x["key"].split(":")[1]) for x in sorted(lines, key=lambda x: (x["sgd_minor"] is None, x["sgd_minor"] or 0))]
    assert opens(after) == by_sgd
    assert section(after, "to_pay_back") == section(before, "to_pay_back")


# --- case 11 ----------------------------------------------------------------------

def test_case_11_the_comparator_orders_text_money_dates_and_puts_empties_last():
    got = node("""
        const by = (kind, values, dir) => [...values].sort((a, b) => sortCompare(kind, a, b, dir));
        out({
            text: by('text', ['cherry', 'Banana', null, 'apple', ''], 'asc'),
            textDown: by('text', ['cherry', 'Banana', null, 'apple', ''], 'desc'),
            money: by('money', [1250, -300, null, 99900, 0], 'asc'),
            moneyDown: by('money', [1250, -300, null, 99900, 0], 'desc'),
            date: by('date', ['2026-10-05', null, '2025-12-31', '2026-01-02'], 'asc'),
            dateDown: by('date', ['2026-10-05', null, '2025-12-31', '2026-01-02'], 'desc'),
        });
    """)
    assert got["text"] == ["apple", "Banana", "cherry", None, ""]
    assert got["textDown"] == ["cherry", "Banana", "apple", None, ""]
    assert got["money"] == [-300, 0, 1250, 99900, None]
    assert got["moneyDown"] == [99900, 1250, 0, -300, None]
    assert got["date"] == ["2025-12-31", "2026-01-02", "2026-10-05", None]
    assert got["dateDown"] == ["2026-10-05", "2026-01-02", "2025-12-31", None]


def test_case_11_a_row_in_another_currency_sorts_by_its_s_dollar_part():
    # Expect back: a rupee line sorts by its S$ beside (sgd_minor); one with no rate has none, and goes last.
    got = node("""
        const line = (key, open, sgd, cur) => ({ key, title: key, date: '2026-01-01', names: [], open_minor: open, amount_minor: open, currency: cur,
            sgd_minor: sgd, no_rate: sgd === null, age_days: 1 });
        const sheet = { expect: { sections: [{ key: 'expected_back', heading: 'Expected back', owed: false, in_total: true, total_minor: 0, left_out: [],
            lines: [line('a', 500000, 7750, 'INR'), line('b', 5000, 5000, 'SGD'), line('c', 900000, null, 'INR'), line('d', 9000, 9000, 'SGD')] }] } };
        click('xb-expected_back', 'open');
        const up = window.expectBooksSections(sheet);
        click('xb-expected_back', 'open');
        out([up, window.expectBooksSections(sheet)]);
    """, extra_js=["expect-mark.js", "expect-books.js"])
    keys = lambda html: re.findall(r'<tr data-key="([^"]+)"', html)
    assert keys(got[0]) == ["b", "a", "d", "c"]
    assert keys(got[1]) == ["d", "a", "b", "c"]


# --- case 12 ----------------------------------------------------------------------

MERCHANT = {"id": 104, "name": "Indigoair", "rows": 120, "first_date": "2026-01-01", "last_date": "2026-09-02", "accounts": 1, "rule_count": 1,
            "type_id": 2, "type_name": "Travel", "display_type": "Travel", "book": "Household"}


def test_case_12_the_merchant_page_asks_the_server_and_returns_to_page_1():
    got = node("""
        const rowsAsked = () => asked.filter(u => u.startsWith('/api/transactions?service_id=104')).pop();
        merchantPage.id = 104; merchantPage.page = 3;
        const own = await viewMerchant(104);
        const first = rowsAsked();
        click('mp', 'amount');
        const pageAfter = merchantPage.page;
        const sortedHtml = await viewMerchant(104);
        const sorted = rowsAsked();
        ACT['mp-page']({ dataset: { page: '2' } });
        await viewMerchant(104);
        const nextPage = rowsAsked();
        click('mp', 'amount');
        await viewMerchant(104);
        out({ own, first, pageAfter, sorted, nextPage, down: rowsAsked(), sortedHtml });
    """, answers={"/api/services/104": MERCHANT, "/api/services/104/book-varies": {"suggest": False},
                  "/api/transactions": {"transactions": [_row(1, "2026-09-01", "INDIGO", 30000, "Travel")], "total": 120, "pages": 3, "page": 3}})
    assert heads(got["own"], "mp") == ["date", "account", "description", "amount", "type"]   # not the tick, Book or state columns
    assert "sort=date&sort_dir=desc" in got["first"] and "page=3" in got["first"]
    assert got["pageAfter"] == 1
    # The pages are newest first only in the rows' own order: sorted, they are Previous and Next.
    assert "Newer" in got["own"] and "Older" in got["own"]
    assert "Previous" in got["sortedHtml"] and "Next" in got["sortedHtml"] and "Older" not in got["sortedHtml"]
    assert "Newest first." in got["own"] and "Newest first." not in got["sortedHtml"].split("<select")[0]
    assert "sort=amount&sort_dir=asc" in got["sorted"] and "&page=1" in got["sorted"]
    assert "sort=amount&sort_dir=asc" in got["nextPage"] and "&page=2" in got["nextPage"]   # the order holds across pages
    assert "sort=amount&sort_dir=desc" in got["down"] and "&page=1" in got["down"]


def test_case_12_an_account_page_rows_sort_on_the_server():
    got = node("""
        S.month = '2026-10';
        await viewAccount(1);
        click('acct-rows', 'out');
        await viewAccount(1);
        const out1 = asked.filter(u => u.startsWith('/api/transactions?account_id=1')).pop();
        click('acct-rows', 'in'); click('acct-rows', 'in');
        const html = await viewAccount(1);
        out([out1, asked.filter(u => u.startsWith('/api/transactions?account_id=1')).pop(), html]);
    """, answers={"/api/balance-sheet": SHEET, "/api/accounts/1/ties": TIES, "/api/anchors": [], "/api/statements/refused": {"refused": []},
                  "/api/transactions": {"transactions": [_row(1, "2026-09-01", "INDIGO", 30000, "Travel")], "total": 1, "pages": 1, "page": 1}})
    by_out, by_in, html = got
    assert "sort=amount&sort_dir=asc" in by_out
    assert "sort=amount&sort_dir=asc" in by_in            # money in, largest first: the most negative amount first
    assert heads(html, "acct-rows") == ["date", "description", "type", "in", "out"]


# --- case 13 ----------------------------------------------------------------------

def test_case_13_a_sort_is_kept_while_the_page_is_open_and_gone_on_reload():
    first = node("""
        click('merchants', 'rows');
        await viewLists('rules');                 // another page drawn between
        const kept = await viewLists('merchants');
        out([kept, writes]);
    """, answers={"/api/services": SERVICES, "/api/rules": RULES, "/api/rules/drift": {"count": 0}})
    kept, writes = first
    assert merchant_names(kept) == ["Amazon", "grab", "Cold Storage"]
    assert writes == []                           # nothing kept in the browser: a reload forgets it
    reloaded = node("out(await viewLists('merchants'));", answers={"/api/services": SERVICES})
    assert merchant_names(reloaded) == ["grab", "Amazon", "Cold Storage"]


# --- case 14 ----------------------------------------------------------------------

def test_case_14_stacked_tables_sort_from_a_phone_select_and_the_sheet_keeps_its_own():
    got = node("""
        const lists = await viewLists('merchants');
        pick('merchants', 'rows:desc');
        const picked = await viewLists('merchants');
        S.month = '2026-10';
        const sheet = await viewSheet();
        out([lists, picked, sheet]);
    """, answers={"/api/services": SERVICES, "/api/balance-sheet": SHEET, "/api/subscriptions": [], "/api/statements/refused": {"refused": []},
                  "/api/transactions": {"transactions": [], "total": 0, "pages": 1, "page": 1}})
    lists, picked, sheet = got
    select = re.search(r'<label class="field phone-only sort-pick"><span>Sort</span><select data-change="sort-pick" data-table="merchants"[^>]*>(.*?)</select>', lists, re.S)
    assert select, "no Sort select above Lists › Merchants"
    options = re.findall(r'<option value="([^"]*)"[^>]*>([^<]+)</option>', select.group(1))
    assert options[0] == ("", "As listed")
    assert ("rows:asc", "Rows: fewest first") in options and ("rows:desc", "Rows: most first") in options
    assert ("name:asc", "Merchant: A to Z") in options
    assert merchant_names(picked) == ["Cold Storage", "grab", "Amazon"]
    assert re.search(r'<option value="rows:desc" selected>', picked)
    # The balance sheet's phone select reads exactly as it did.
    assert re.search(r'<label class="field phone-only sheet-sort"><select data-change="sort-pick" data-table="sheet" aria-label="Sort">\s*'
                     r'<option value="">Sheet order</option><option value="rests">Rests on: oldest first</option>\s*'
                     r'<option value="check">Check: worst first</option></select></label>', sheet), sheet


def test_case_14_the_balance_sheet_headings_and_order_are_unchanged():
    got = node("""
        S.month = '2026-10';
        const own = await viewSheet();
        click('sheet', 'value');
        const up = await viewSheet();
        click('sheet', 'value');
        const down = await viewSheet();
        pick('sheet', 'rests');
        out([own, up, down, await viewSheet()]);
    """, answers={"/api/balance-sheet": SHEET, "/api/subscriptions": [], "/api/statements/refused": {"refused": []},
                  "/api/transactions": {"transactions": [], "total": 0, "pages": 1, "page": 1}})
    own, up, down, rests = got
    names = lambda html: re.findall(r'<td class="acct-name"><a href="#/books/account/\d+">([^<]+)</a>', html)
    assert names(own) == ["Wise", "HDFC", "citi card"]
    assert re.findall(r'<th[^>]*><button[^>]*>([^<]+)</button></th>', own) == ["In S$", "Rests on", "Check"]
    assert names(up) == ["Wise", "HDFC", "citi card"]       # largest first; a line with no figure after
    assert names(down) == ["HDFC", "Wise", "citi card"]     # as the sheet has always done
    assert names(rests) == ["HDFC", "Wise", "citi card"]    # no balance to rest on first, then oldest
