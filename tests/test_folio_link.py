"""A folio reference on a row, an account or a party (fin-redesign 25; spec
part 2, case 11).

folio keeps an LP's units and what they mean; fin keeps the money legs. A
row, an account or a party (a merchant) may carry one read-only link to its
folio record: an LP's page or one row of folio's ledger. The operator pastes
the folio address; fin keeps only which record it is and draws the link from
folio's own address. fin never reads from or writes to folio.

Driven through the HTTP interface (the folio-link routes, Changes, Undo), a
start (db.init_db), and static/folio-link.js under node. Every name, figure
and folio number here is invented.
"""

import json
import socket
import subprocess
from pathlib import Path

import pytest

import db
import folio_link
import history
from test_client_seams import NODE, needs_node

STATIC = Path(__file__).resolve().parent.parent / "static"
FOLIO = "https://folio.bhasuri.ai"


# --- the book ---------------------------------------------------------------


def make_account(conn, name, kind="bank", currency="SGD"):
    acct = conn.execute(
        "INSERT INTO accounts (name, short_name, type, currency) VALUES (?, ?, ?, ?)",
        (name, name.replace(" ", "-"), kind, currency),
    ).lastrowid
    conn.commit()
    return acct


def make_row(conn, account, day, description, amount_minor):
    stmt = conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, ?)",
        (account, day, f"{day}-{amount_minor}.csv"),
    ).lastrowid
    row = conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type)"
        " VALUES (?, ?, ?, ?, 'movement')",
        (stmt, day, description, amount_minor),
    ).lastrowid
    conn.commit()
    return row


def make_party(conn, name, role="person"):
    party = conn.execute("INSERT INTO services (name, role) VALUES (?, ?)", (name, role)).lastrowid
    conn.commit()
    return party


@pytest.fixture
def book(conn):
    """Sample LP #18's two money legs on a US$ bank, Sample Partner's party,
    and a person account joined to it."""
    usd = make_account(conn, "Sample USD Bank", currency="USD")
    leg_in = make_row(conn, usd, "2025-07-17", "SAMPLE LP SUBSCRIPTION", -4_954_065)
    leg_out = make_row(conn, usd, "2026-09-25", "SAMPLE LP REDEMPTION", 5_000_000)
    party = make_party(conn, "Sample Partner")
    person = make_account(conn, "Sample Partner", kind="person")
    return {"usd": usd, "in": leg_in, "out": leg_out, "party": party, "person": person}


def link(client, subject, subject_id, address):
    return client.put(f"/api/folio-links/{subject}/{subject_id}", json={"link": address})


def held(client, subject, subject_id):
    resp = client.get(f"/api/folio-links/{subject}/{subject_id}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["link"]


def entries(client):
    return client.get("/api/history").get_json()["entries"]


def counts(conn):
    return (conn.execute("SELECT COUNT(*) FROM folio_link").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0])


# --- case 11: the LP's money legs and the party carry their folio links ------


def test_the_lp_legs_and_the_party_link_to_their_folio_records(client, book):
    assert link(client, "row", book["in"], f"{FOLIO}/#/transactions?tx=901").status_code == 200
    assert link(client, "row", book["out"], f"{FOLIO}/#/transactions?lp=18&tx=1460").status_code == 200
    assert link(client, "party", book["party"], f"{FOLIO}/#/lps/18").status_code == 200

    assert held(client, "row", book["in"]) == {
        "target": "tx", "folio_id": 901, "url": f"{FOLIO}/#/transactions?tx=901", "words": "folio transaction 901"}
    assert held(client, "row", book["out"])["url"] == f"{FOLIO}/#/transactions?tx=1460"
    assert held(client, "party", book["party"]) == {
        "target": "lp", "folio_id": 18, "url": f"{FOLIO}/#/lps/18", "words": "folio LP 18"}


def test_an_account_carries_a_link_too(client, book):
    assert link(client, "account", book["person"], f"{FOLIO}/#/lps/18").status_code == 200
    assert held(client, "account", book["person"])["url"] == f"{FOLIO}/#/lps/18"


def test_a_subject_with_no_link_reads_none(client, book):
    assert held(client, "row", book["in"]) is None
    assert held(client, "account", book["usd"]) is None
    assert held(client, "party", book["party"]) is None


# --- what the operator may paste ---------------------------------------------


@pytest.mark.parametrize("address, target, folio_id", [
    (f"{FOLIO}/#/lps/18", "lp", 18),
    (f"{FOLIO}/#/lps/18?scope=personal", "lp", 18),
    (f"  {FOLIO}#/lps/18/  ", "lp", 18),
    ("http://folio.bhasuri.ai/#/lps/18", "lp", 18),
    ("#/lps/18", "lp", 18),
    ("LP 18", "lp", 18),
    ("lp #18", "lp", 18),
    (f"{FOLIO}/#/transactions?tx=901", "tx", 901),
    (f"{FOLIO}/#/transactions?from=2025-07-01&tx=901&lp=18", "tx", 901),
    ("#/transactions?tx=901", "tx", 901),
])
def test_a_folio_address_or_an_lp_number_reads_as_its_record(address, target, folio_id):
    assert folio_link.parsed(address) == (target, folio_id)


@pytest.mark.parametrize("address, words", [
    ("https://example.com/#/lps/18", "not a folio address"),
    ("https://folio.bhasuri.ai.example.com/#/lps/18", "not a folio address"),
    (f"{FOLIO}/#/fund", "an LP's page or a row of folio's ledger"),
    (f"{FOLIO}/#/transactions?lp=18", "an LP's page or a row of folio's ledger"),
    (f"{FOLIO}/#/lps/0", "an LP's page or a row of folio's ledger"),
    (f"{FOLIO}/#/lps/abc", "an LP's page or a row of folio's ledger"),
    ("", "paste the folio address"),
    ("   ", "paste the folio address"),
])
def test_anything_else_is_refused_and_writes_nothing(client, conn, book, address, words):
    before = counts(conn)
    resp = link(client, "row", book["in"], address)
    assert resp.status_code == 400
    error = resp.get_json()["error"]
    assert words in error and error.endswith("Nothing was changed.")
    assert counts(conn) == before


@pytest.mark.parametrize("body", [None, {}, {"link": 18}, {"link": None}, ["#/lps/18"]])
def test_a_body_without_an_address_is_refused(client, conn, book, body):
    before = counts(conn)
    resp = client.put(f"/api/folio-links/row/{book['in']}", json=body)
    assert resp.status_code == 400
    assert "Nothing was changed" in resp.get_json()["error"]
    assert counts(conn) == before


@pytest.mark.parametrize("subject", ["row", "account", "party"])
def test_a_subject_no_longer_in_fin_is_refused(client, conn, book, subject):
    before = counts(conn)
    resp = link(client, subject, 99_999, "LP 18")
    assert resp.status_code == 404
    assert "Nothing was changed" in resp.get_json()["error"]
    assert counts(conn) == before
    assert client.get(f"/api/folio-links/{subject}/99999").status_code == 404


def test_an_unknown_kind_of_subject_has_no_route(client, book):
    assert link(client, "statement", book["in"], "LP 18").status_code == 404


# --- one link each, changed and taken off, every change undone ---------------


def test_a_second_address_replaces_the_first(client, conn, book):
    link(client, "party", book["party"], "LP 18")
    assert link(client, "party", book["party"], "LP 19").status_code == 200
    assert held(client, "party", book["party"])["folio_id"] == 19
    assert counts(conn)[0] == 1


def test_the_same_record_again_is_no_change(client, conn, book):
    link(client, "party", book["party"], "LP 18")
    before = len(entries(client))
    assert link(client, "party", book["party"], f"{FOLIO}/#/lps/18").status_code == 200
    assert len(entries(client)) == before


def test_one_record_may_be_linked_from_many_subjects(client, conn, book):
    for subject, subject_id in (("party", book["party"]), ("account", book["person"])):
        assert link(client, subject, subject_id, "LP 18").status_code == 200
    assert counts(conn)[0] == 2


def test_linking_is_one_change_named_in_changes_and_undone_whole(client, book):
    link(client, "party", book["party"], "LP 18")
    entry = entries(client)[0]
    assert entry["summary"] == "Linked the party Sample Partner to folio LP 18"
    assert client.post(f"/api/history/{entry['id']}/undo").status_code == 200
    assert held(client, "party", book["party"]) is None


def test_a_row_link_names_the_row_in_changes(client, book):
    link(client, "row", book["in"], "#/transactions?tx=901")
    assert entries(client)[0]["summary"] == "Linked the row SAMPLE LP SUBSCRIPTION to folio transaction 901"


def test_taking_a_link_off_is_one_change_and_undo_puts_it_back(client, book):
    link(client, "account", book["person"], "LP 18")
    resp = client.delete(f"/api/folio-links/account/{book['person']}")
    assert resp.status_code == 200 and resp.get_json() == {"link": None}
    assert held(client, "account", book["person"]) is None
    entry = entries(client)[0]
    assert entry["summary"] == "Took the folio link off the account Sample Partner"
    assert client.post(f"/api/history/{entry['id']}/undo").status_code == 200
    assert held(client, "account", book["person"])["folio_id"] == 18


def test_changing_a_link_and_undoing_puts_the_old_one_back(client, book):
    link(client, "party", book["party"], "LP 18")
    link(client, "party", book["party"], "LP 19")
    assert client.post(f"/api/history/{entries(client)[0]['id']}/undo").status_code == 200
    assert held(client, "party", book["party"])["folio_id"] == 18


def test_taking_off_a_link_that_is_not_there_is_refused(client, conn, book):
    before = counts(conn)
    resp = client.delete(f"/api/folio-links/row/{book['in']}")
    assert resp.status_code == 404
    assert "Nothing was changed" in resp.get_json()["error"]
    assert counts(conn) == before


# --- nothing in folio changes, no number moves -------------------------------


def test_fin_never_reaches_folio(client, book, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("fin opened a connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert link(client, "party", book["party"], f"{FOLIO}/#/lps/18").status_code == 200
    assert held(client, "party", book["party"])["folio_id"] == 18
    assert client.delete(f"/api/folio-links/party/{book['party']}").status_code == 200


def test_a_link_moves_no_figure(client, book):
    before = client.get("/api/balance-sheet").get_json()
    rows_before = client.get(f"/api/transactions?account_id={book['usd']}").get_json()
    link(client, "row", book["in"], "#/transactions?tx=901")
    link(client, "account", book["usd"], "LP 18")
    assert client.get("/api/balance-sheet").get_json() == before
    assert client.get(f"/api/transactions?account_id={book['usd']}").get_json() == rows_before


def test_folio_address_follows_the_configured_folio(client, book, monkeypatch):
    monkeypatch.setenv("FIN_FOLIO_URL", "https://folio.sample.test/")
    assert link(client, "party", book["party"], "https://folio.sample.test/#/lps/18").status_code == 200
    assert held(client, "party", book["party"])["url"] == "https://folio.sample.test/#/lps/18"
    assert link(client, "party", book["party"], f"{FOLIO}/#/lps/18").status_code == 400


# --- the table, from a start --------------------------------------------------


def test_a_start_makes_the_table_and_its_history_and_records_nothing(temp_db):
    db.init_db()
    conn = db.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM folio_link").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM change_rows").fetchone()[0] == 0
        assert history.has_shape(conn)
    finally:
        conn.close()
    assert "folio_link" in history.TRACKED and "folio_link" in history.ADDED_ON_START


# --- the screens ----------------------------------------------------------------

FOLIO_JS = (STATIC / "folio-link.js").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")


def _node(code):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, check=True, encoding="utf-8")
    return json.loads(out.stdout)


PRELUDE = """
globalThis.window = globalThis;
globalThis.document = { addEventListener() {}, querySelectorAll() { return []; } };
globalThis.MutationObserver = class { observe() {} };
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const icon = n => `<i ${n}></i>`;
globalThis.esc = esc; globalThis.icon = icon; globalThis.ACT = {};
"""


@needs_node
def test_a_held_link_draws_as_a_link_out_to_folio_in_a_new_tab():
    out = _node(PRELUDE + FOLIO_JS + """
        const html = folioLinkHTML('party', 7, { target: 'lp', folio_id: 18, url: 'https://folio.bhasuri.ai/#/lps/18', words: 'folio LP 18' });
        console.log(JSON.stringify(html));""")
    assert 'href="https://folio.bhasuri.ai/#/lps/18"' in out
    assert 'target="_blank"' in out and 'rel="noopener noreferrer"' in out
    assert "folio LP 18" in out
    assert 'data-act="folio-edit"' in out and 'data-act="folio-off"' in out


@needs_node
def test_no_link_draws_the_offer_to_add_one():
    out = _node(PRELUDE + FOLIO_JS + "console.log(JSON.stringify(folioLinkHTML('row', 5, null)));")
    assert "Link to folio" in out and 'data-act="folio-edit"' in out and "href" not in out


@needs_node
def test_the_slot_names_its_subject():
    out = _node(PRELUDE + FOLIO_JS + "console.log(JSON.stringify(folioSlot('account', 12)));")
    assert 'data-folio-subject="account"' in out and 'data-folio-id="12"' in out


@needs_node
def test_only_a_folio_address_from_the_server_is_drawn_as_a_link():
    out = _node(PRELUDE + FOLIO_JS + """
        console.log(JSON.stringify(folioLinkHTML('row', 5, { target: 'lp', folio_id: 1, url: 'javascript:alert(1)', words: 'x' })));""")
    assert "javascript:" not in out


def test_the_row_sheet_the_account_pages_and_the_merchant_page_hold_a_slot():
    assert APP_JS.count("window.folioSlot?.(") == 4
    for subject in ("'row', found.id", "'account', id", "'account', acct.id", "'party', s.id"):
        assert f"window.folioSlot?.({subject})" in APP_JS, subject


def test_the_page_loads_the_script():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<script src="/static/folio-link.js"></script>' in index
