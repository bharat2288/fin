"""fin-redesign 10: bills on the server (spec cases 17, 18, 19).

The missed-bill rule has one home, bills.py, read by the app's Bills screen,
the Queue and Home (GET /api/subscriptions) and by the chat (the bills tool,
GET /api/bills). A bill waiting on its paying account's statement reads
"awaiting statement"; a bill's cadence survives the app's bill sheet; a bill
whose currency is not its paying account's is flagged and fixed by a
previewed change. Driven through the Flask test client, mcp_tools.call, and
the real static/app.js under node for the sheet. Every name and figure is
invented.
"""

import datetime
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

import app as fin_app
import bills
import chat_writes
import mcp_tools

TODAY = datetime.date(2026, 10, 25)
STATIC = Path(__file__).resolve().parent.parent / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the bill sheet is drawn only in JavaScript")
ACTOR = "chat:sample-subject"


@pytest.fixture(autouse=True)
def fixed_day(monkeypatch):
    monkeypatch.setattr(bills, "today", lambda: TODAY)
    monkeypatch.setattr(fin_app, "_get_usd_sgd_rate", lambda: 1.35)


def account(conn, name, kind="bank", currency="SGD") -> int:
    conn.execute("INSERT INTO accounts (name, short_name, type, currency) VALUES (?, ?, ?, ?)",
                 (name, name, kind, currency))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def statement(conn, account_id, closing, start) -> int:
    """A printed statement covering start..closing."""
    conn.execute("INSERT INTO statements (account_id, statement_date, period_start, printed) VALUES (?, ?, ?, 1)",
                 (account_id, closing, start))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def paid(conn, statement_id, on, description, cents) -> None:
    conn.execute("INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type) "
                 "VALUES (?, ?, ?, ?, 'expense')", (statement_id, on, description, cents))
    conn.commit()


def merchant(client, name) -> int:
    resp = client.post("/api/services", json={"name": name, "book": "Household"})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def bill(client, name, **fields) -> int:
    body = {"service_id": merchant(client, name), "match_pattern": name.upper(), **fields}
    resp = client.post("/api/subscriptions", json=body)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def shown(client, bill_id) -> dict:
    return next(s for s in client.get("/api/subscriptions").get_json() if s["id"] == bill_id)


def held(conn, bill_id) -> dict:
    conn.row_factory = sqlite3.Row
    return dict(conn.execute("SELECT * FROM subscriptions WHERE id = ?", (bill_id,)).fetchone())


def entries(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0]


# --- case 18: a missed bill waits for the paying account's statement ------------------


@pytest.fixture
def covered_to_31_aug(client, conn):
    """A bank whose statements cover 1 Jul to 31 Aug, and two bills paid from it."""
    bank = account(conn, "Sample Bank 0001")
    statement(conn, bank, "2026-07-31", "2026-07-01")
    aug = statement(conn, bank, "2026-08-31", "2026-08-01")
    paid(conn, aug, "2026-08-05", "SAMPLE LEDGER SG", 9025)
    # Sample Ledger: monthly, due on the 5th; its last payment seen is 5 Aug.
    ledger = bill(client, "Sample Ledger", amount=90.25, frequency="monthly", account_id=bank,
                  renewal_date="2026-08-05")
    # Sample Rent: S$19,500 every 6 months, due 15 Aug; the last payment seen is in February.
    rent = bill(client, "Sample Rent", amount=19500, frequency="half-yearly", account_id=bank,
                renewal_date="2026-02-15", last_paid="2026-02-16")
    return {"bank": bank, "ledger": ledger, "rent": rent}


def test_case_18_a_bill_due_after_the_statements_awaits_one_and_one_unpaid_inside_them_is_missed(client, covered_to_31_aug):
    ledger = shown(client, covered_to_31_aug["ledger"])
    rent = shown(client, covered_to_31_aug["rent"])

    assert (ledger["state"], ledger["due"], ledger["covered_to"]) == ("awaiting_statement", "2026-10-05", "2026-08-31")
    assert (rent["state"], rent["due"]) == ("missed", "2026-08-15")


def test_case_18_a_bill_paid_inside_the_statements_is_on_track(client, conn, covered_to_31_aug):
    stmt = conn.execute("SELECT id FROM statements WHERE statement_date = '2026-08-31'").fetchone()[0]
    paid(conn, stmt, "2026-08-14", "SAMPLE RENT AUG", 1950000)

    assert shown(client, covered_to_31_aug["rent"])["state"] == "on_track"


def test_case_18_a_due_day_in_a_month_with_no_statement_awaits_it(client, conn):
    bank = account(conn, "Sample Bank 0003")
    statement(conn, bank, "2026-06-30", "2026-06-01")
    statement(conn, bank, "2026-08-31", "2026-08-01")   # July's statement is not imported
    gym = bill(client, "Sample Gym", amount=360, frequency="half-yearly", account_id=bank,
               renewal_date="2026-01-12")   # due 12 Jul, next 12 Jan

    got = shown(client, gym)
    assert got["state"] == "awaiting_statement"
    assert got["due"] == "2026-07-12"


def test_case_18_a_paused_bill_and_one_not_yet_late_are_never_missed(client, conn):
    bank = account(conn, "Sample Bank 0004")
    statement(conn, bank, "2026-10-31", "2026-10-01")   # a statement dated ahead, as a test can
    soon = bill(client, "Sample Paper", amount=12, frequency="monthly", account_id=bank, renewal_date="2026-09-21")
    paused = bill(client, "Sample Stream", amount=9, frequency="monthly", account_id=bank, renewal_date="2026-05-01",
                  status="paused")

    assert shown(client, soon)["state"] == "on_track"        # due 21 Oct, 4 days ago: inside the grace
    assert shown(client, paused)["state"] == "paused"


def test_case_18_the_queue_and_the_chat_read_the_same_rule(client, covered_to_31_aug):
    app_read = {s["id"]: s["state"] for s in client.get("/api/subscriptions").get_json()}
    answer = mcp_tools.call("bills", {}, actor=ACTOR)
    assert answer["ok"], answer
    chat_read = {b["id"]: b["state"] for b in answer["result"]["bills"]}

    assert chat_read == app_read
    assert answer["result"]["counts"]["missed"] == 1
    assert answer["result"]["counts"]["awaiting_statement"] == 1
    only = mcp_tools.call("bills", {"state": "missed"}, actor=ACTOR)["result"]["bills"]
    assert [b["id"] for b in only] == [covered_to_31_aug["rent"]]


def test_the_bills_tool_is_a_read_that_works_with_writes_off(client, covered_to_31_aug, conn):
    assert not mcp_tools.BY_NAME["bills"].write
    chat_writes.write(False, "test")
    before = entries(conn)

    assert mcp_tools.call("bills", {}, actor=ACTOR)["ok"]
    assert entries(conn) == before


# --- case 17: a bill holds cadence ------------------------------------------------------


def test_case_17_a_half_yearly_bill_reads_its_monthly_share(client, conn):
    bank = account(conn, "Sample Bank 0005")
    half = bill(client, "Sample Rent", amount=19500, frequency="half-yearly", account_id=bank, renewal_date="2027-02-15")
    six = bill(client, "Sample Club", amount=19500, frequency="monthly", periods=6, account_id=bank, renewal_date="2027-02-15")

    for got in (shown(client, half), shown(client, six)):
        assert (got["monthly_sgd"], got["every"], got["cadence"]) == (3250.0, "every 6 months", {"unit": "month", "count": 6})


def test_case_17_saving_a_bill_by_its_cadence_keeps_how_it_is_held(client, conn):
    bank = account(conn, "Sample Bank 0006")
    half = bill(client, "Sample Rent", amount=19500, frequency="half-yearly", account_id=bank)
    six = bill(client, "Sample Club", amount=19500, frequency="monthly", periods=6, account_id=bank)
    monthly = bill(client, "Sample Paper", amount=12, frequency="monthly", account_id=bank)
    six_months = {"every": {"unit": "month", "count": 6}}

    for b in (half, six, monthly):
        assert client.put(f"/api/subscriptions/{b}", json=six_months).status_code == 200

    assert (held(conn, half)["frequency"], held(conn, half)["periods"]) == ("half-yearly", 1)
    assert (held(conn, six)["frequency"], held(conn, six)["periods"]) == ("monthly", 6)
    assert (held(conn, monthly)["frequency"], held(conn, monthly)["periods"]) == ("half-yearly", 1)
    assert shown(client, monthly)["monthly_sgd"] == 2.0


def test_case_17_a_cadence_fin_does_not_know_is_refused_and_writes_nothing(client, conn):
    b = bill(client, "Sample Paper", amount=12, frequency="monthly")
    two = merchant(client, "Sample Two")
    before = (held(conn, b), entries(conn))

    for body in ({"frequency": "fortnightly"}, {"every": {"unit": "month", "count": 0}},
                 {"every": {"unit": "decade", "count": 1}}, {"periods": -2}):
        assert client.put(f"/api/subscriptions/{b}", json=body).status_code == 400, body
    assert client.post("/api/subscriptions", json={"service_id": two, "frequency": "annual"}).status_code == 400

    assert (held(conn, b), entries(conn)) == before


def _node(code):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# The stand-in browser: the sheet's HTML is kept, and a field is read back from
# it as the browser would (a select's selected option, else its first; an
# input's value).
SHEET_PRELUDE = r"""
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.scrollTo = () => {};
globalThis.setTimeout = () => 0;
const el = () => ({ innerHTML: '', dataset: {}, classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {}, removeAttribute() {},
    addEventListener() {}, querySelector: () => el(), querySelectorAll: () => [], appendChild() {}, remove() {}, focus() {}, style: {} });
const sheets = el();
const unq = s => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
function field(id) {
    const html = sheets.innerHTML;
    const sel = html.match(new RegExp(`<select id="${id}"[^>]*>([\\s\\S]*?)</select>`));
    if (sel) {
        const opts = [...sel[1].matchAll(/<option value="([^"]*)"( selected)?/g)];
        const on = opts.find(o => o[2]) || opts[0];
        return { value: unq(on ? on[1] : '') };
    }
    const inp = html.match(new RegExp(`<input[^>]*id="${id}"[^>]*>`));
    if (inp) { const v = inp[0].match(/value="([^"]*)"/); return { value: unq(v ? v[1] : '') }; }
    return null;
}
globalThis.document = { addEventListener() {}, removeEventListener() {},
    querySelector: s => s === '#sheets' ? sheets : s.startsWith('#bl-') ? field(s.slice(1)) : el(),
    querySelectorAll: () => [], getElementById: () => null, documentElement: el(), createElement: el, body: el() };
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.sessionStorage = globalThis.localStorage;
globalThis.location = { hash: '#/books/bills' };
globalThis.history = { replaceState() {} };
let ANSWERS = {};
const sent = [];
globalThis.fetch = async (url, opts = {}) => {
    if (opts.method && opts.method !== 'GET') { sent.push([opts.method, url, JSON.parse(opts.body || 'null')]); return { ok: true, status: 200, json: async () => ({ success: true }), headers: { get: () => null } }; }
    const path = url.split('?')[0];
    const hit = url in ANSWERS ? ANSWERS[url] : ANSWERS[path];
    return { ok: hit !== undefined, status: hit !== undefined ? 200 : 404, json: async () => JSON.parse(JSON.stringify(hit ?? { error: 'not here' })), headers: { get: () => null } };
};
"""


def _sheet_save(client, bill_id):
    """Open the bill's sheet in the real app.js over the server's own answers,
    press Save untouched, and return what the sheet sends."""
    reads = ["/api/subscriptions", "/api/types", "/api/books", "/api/accounts", "/api/review", "/api/account-kinds", "/api/services"]
    answers = {u: client.get(u).get_json() for u in reads}
    code = "\n".join([SHEET_PRELUDE, (STATIC / "app.js").read_text(encoding="utf-8"),
                      f"ANSWERS = {json.dumps(answers)};",
                      "render = async () => {}; refreshFrame = () => {};",
                      "(async () => {",
                      "await viewBills();",
                      f"await ACT.bill({{ dataset: {{ id: '{bill_id}' }} }});",
                      f"await ACT['bill-save']({{ dataset: {{ id: '{bill_id}' }} }});",
                      "console.log(JSON.stringify(sent));",
                      "})().catch(e => { console.error(e && e.stack || e); process.exit(1); });"])
    return _node(code)


@needs_node
@pytest.mark.parametrize("held_as", [{"frequency": "half-yearly"}, {"frequency": "monthly", "periods": 6}])
def test_case_17_saving_the_bill_sheet_untouched_keeps_every_6_months(client, conn, held_as):
    bank = account(conn, "Sample Bank 0007")
    rent = bill(client, "Sample Rent", amount=19500, account_id=bank, renewal_date="2027-02-15", **held_as)

    sent = _sheet_save(client, rent)
    assert [(m, u) for m, u, _ in sent] == [("PUT", f"/api/subscriptions/{rent}")]
    resp = client.put(sent[0][1], json=sent[0][2])
    assert resp.status_code == 200, resp.get_json()

    assert (held(conn, rent)["frequency"], held(conn, rent)["periods"]) == (held_as["frequency"], held_as.get("periods", 1))
    got = shown(client, rent)
    assert (got["every"], got["monthly_sgd"]) == ("every 6 months", 3250.0)


# --- case 19: a bill's currency is its paying account's ---------------------------------


@pytest.fixture
def power(client, conn):
    """Sample Power billed in US$257.22, paid from an S$ bank; its last payment S$560.00."""
    bank = account(conn, "Sample Bank 0008")
    stmt = statement(conn, bank, "2026-08-31", "2026-08-01")
    paid(conn, stmt, "2026-08-18", "SAMPLE POWER", 56000)
    conn.execute("INSERT INTO services (name, book) VALUES ('Sample Power', 'Household')")
    service = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Held from before the rule: written as the old screen let it be.
    conn.execute("INSERT INTO subscriptions (service_id, amount, currency, frequency, periods, account_id, renewal_date, "
                 "match_pattern, status) VALUES (?, 257.22, 'USD', 'monthly', 1, ?, '2026-08-18', 'SAMPLE POWER', 'active')",
                 (service, bank))
    conn.commit()
    return {"bank": bank, "bill": conn.execute("SELECT last_insert_rowid()").fetchone()[0]}


def test_case_19_a_bill_in_another_currency_than_its_account_is_flagged(client, power):
    got = shown(client, power["bill"])
    assert (got["currency"], got["account_currency"], got["currency_mismatch"]) == ("USD", "SGD", True)
    chat = next(b for b in mcp_tools.call("bills", {}, actor=ACTOR)["result"]["bills"] if b["id"] == power["bill"])
    assert chat["currency_mismatch"] is True


def test_case_19_the_fix_is_previewed_first_and_the_preview_writes_nothing(client, conn, power):
    before = (held(conn, power["bill"]), entries(conn))

    resp = client.post(f"/api/bills/{power['bill']}/currency", json={})

    assert resp.status_code == 409
    preview = resp.get_json()["preview"]
    assert preview["before"] == {"amount": 257.22, "currency": "USD"}
    assert preview["after"] == {"amount": 560.0, "currency": "SGD"}
    assert preview["basis"] == {"kind": "last_payment", "date": "2026-08-18"}
    assert preview["token"]
    assert (held(conn, power["bill"]), entries(conn)) == before


def test_case_19_the_fix_on_its_token_is_one_change_and_undo_puts_it_back(client, conn, power):
    before = held(conn, power["bill"])
    token = client.post(f"/api/bills/{power['bill']}/currency", json={}).get_json()["preview"]["token"]

    resp = client.post(f"/api/bills/{power['bill']}/currency", json={"token": token, "amount": 545.5})

    assert resp.status_code == 200, resp.get_json()
    after = held(conn, power["bill"])
    assert (after["currency"], after["amount"]) == ("SGD", 545.5)
    assert shown(client, power["bill"])["currency_mismatch"] is False
    change = int(resp.headers["X-Fin-Change"])
    assert resp.headers["X-Fin-Change-Rows"] == "1"
    assert client.post(f"/api/history/{change}/undo").status_code == 200
    assert held(conn, power["bill"]) == before


def test_case_19_a_refused_fix_writes_nothing(client, conn, power):
    token = client.post(f"/api/bills/{power['bill']}/currency", json={}).get_json()["preview"]["token"]
    client.put(f"/api/subscriptions/{power['bill']}", json={"amount": 260})   # the bill changed since the preview
    before = (held(conn, power["bill"]), entries(conn))

    stale = client.post(f"/api/bills/{power['bill']}/currency", json={"token": token})
    finer = client.post(f"/api/bills/{power['bill']}/currency", json={"token": "x", "amount": 1.234})

    assert stale.status_code == 409 and "changed" in stale.get_json()["error"]
    assert finer.status_code == 400
    assert (held(conn, power["bill"]), entries(conn)) == before


def test_case_19_a_currency_is_not_set_against_the_paying_account(client, conn, power):
    sgd_bill = bill(client, "Sample Water", amount=40, account_id=power["bank"])
    gas = merchant(client, "Sample Gas")
    before = (held(conn, sgd_bill), held(conn, power["bill"]), entries(conn))

    assert client.put(f"/api/subscriptions/{sgd_bill}", json={"currency": "USD"}).status_code == 400
    assert client.post("/api/subscriptions", json={"service_id": gas, "frequency": "monthly",
                                                    "account_id": power["bank"], "currency": "USD"}).status_code == 400
    # Saving a flagged bill without touching its currency is not refused: the flag stays.
    assert client.put(f"/api/subscriptions/{power['bill']}", json={"currency": "USD", "notes": "n"}).status_code == 200
    assert held(conn, sgd_bill) == before[0]
    assert conn.execute("SELECT COUNT(*) FROM subscriptions WHERE service_id = ?", (gas,)).fetchone()[0] == 0


def test_case_19_a_new_bill_takes_its_paying_accounts_currency(client, conn):
    rupee = account(conn, "Sample Rupee 0009", currency="INR")
    b = bill(client, "Sample Tutor", amount=4000, frequency="monthly", account_id=rupee)
    assert held(conn, b)["currency"] == "INR"


@needs_node
def test_case_18_the_bills_screen_says_awaiting_statement_and_missed_from_the_servers_read(client, covered_to_31_aug):
    reads = ["/api/subscriptions", "/api/types", "/api/books", "/api/accounts", "/api/review", "/api/account-kinds"]
    answers = {u: client.get(u).get_json() for u in reads}
    html = _node("\n".join([SHEET_PRELUDE, (STATIC / "app.js").read_text(encoding="utf-8"),
                            f"ANSWERS = {json.dumps(answers)};",
                            "(async () => { console.log(JSON.stringify(await viewBills())); })()"
                            ".catch(e => { console.error(e && e.stack || e); process.exit(1); });"]))
    rows = {name: row for name, row in __import__("re").findall(r'<td class="sl-name"><b>([^<]+)</b>(.*?)</tr>', html, __import__("re").S)}

    assert "awaiting statement" in rows["Sample Ledger"] and "no payment seen" not in rows["Sample Ledger"]
    assert "no payment seen for 15 Aug" in rows["Sample Rent"]
    assert "every 6 months" in rows["Sample Rent"]
    assert "1 of 2 not seen" in html
