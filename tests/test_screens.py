"""What fin's screens keep (fin-surfaces 02, screens.py): the "Claude may
write" switch, the "you last looked" mark Home counts from, statements
refused at upload with "Known, leave it", an account's tie lines, and the
history list's "asked first" and blocker marks.

Driven through the HTTP interface and mcp_tools.call against a temporary
database. Every name and figure is invented.
"""

import io

import pytest

import access_gate
import mcp_tools
import parsers
from parse_dbs import ParsedStatement, ParsedTransaction
from test_balance_sheet import bring_in

BANK = "Sample Bank 0002"
ACTOR = "chat:sample-subject"


def chat(tool_name, **args):
    return mcp_tools.call(tool_name, args, actor=ACTOR)


def chat_environ():
    return {access_gate.CHAT_CALL_ENVIRON_KEY: access_gate.chat_call_identity(ACTOR)}


@pytest.fixture
def book(client):
    bring_in(client, BANK, [
        ("2026-08-03", "SAMPLE GROCER", 14000),
        ("2026-08-12", "SAMPLE CAFE", 15950),
    ], opening=100000, closing=100000 - 14000 - 15950)
    return client


def first_row(client):
    return client.get("/api/transactions?per_page=50").get_json()["transactions"][0]


def upload_refused(client, *, closing_date="2026-09-30", difference=1234):
    """Upload a statement whose rows do not tie: refused, nothing written."""
    def parse(_path):
        return [ParsedStatement(
            statement_type="bank", statement_date=closing_date, accounts=[BANK],
            filename="sample.csv",
            transactions=[ParsedTransaction(date=closing_date[:8] + "05", description="SAMPLE SHOP",
                                            amount_minor=5000, card_info=BANK)],
            opening_minor=70050, closing_minor=70050 - 5000 - difference,
            opening_date=closing_date[:8] + "01", closing_date=closing_date,
        )]
    parsers._PARSERS.insert(0, {"name": "Stand-in", "ext": ".csv", "detect_fn": lambda _p: True,
                                "parse_fn": parse})
    try:
        resp = client.post("/api/import/upload", data={"files": (io.BytesIO(b"x"), "sample.csv")},
                           content_type="multipart/form-data")
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    assert resp.status_code == 200
    return resp.get_json()


# --- the "Claude may write" switch ------------------------------------------


def test_the_switch_is_on_until_the_operator_turns_it_off(book):
    assert book.get("/api/settings").get_json()["claude_may_write"] is True
    resp = book.put("/api/settings/claude-write", json={"on": False})
    assert resp.status_code == 200 and resp.get_json() == {"claude_may_write": False}
    assert book.get("/api/settings").get_json()["claude_may_write"] is False


def test_with_the_switch_off_chat_writes_are_refused_and_reads_work(book):
    tx = first_row(book)
    book.put("/api/settings/claude-write", json={"on": False})
    before = book.get("/api/history").get_json()["entries"]

    answer = chat("set_note", tx_id=tx["id"], notes="sample note")
    assert answer["ok"] is False and "switched off" in answer["error"]
    assert chat("transactions", per_page=5)["ok"] is True
    assert book.get("/api/history").get_json()["entries"] == before
    assert first_row(book)["notes"] is None

    book.put("/api/settings/claude-write", json={"on": True})
    assert chat("set_note", tx_id=tx["id"], notes="sample note")["ok"] is True


def test_the_switch_is_never_set_from_the_chat(book):
    resp = book.put("/api/settings/claude-write", json={"on": True}, environ_base=chat_environ())
    assert resp.status_code == 403
    assert not any("setting" in t.name or "switch" in t.name for t in mcp_tools.TOOLS)


def test_the_switch_takes_only_true_or_false(book):
    assert book.put("/api/settings/claude-write", json={"on": "no"}).status_code == 400


def test_flipping_the_switch_is_not_a_change_to_the_book(book):
    before = book.get("/api/history").get_json()["entries"]
    book.put("/api/settings/claude-write", json={"on": False})
    assert book.get("/api/history").get_json()["entries"] == before


# --- since you last looked ----------------------------------------------------


def test_claudes_changes_are_counted_until_the_operator_looks(book):
    tx = first_row(book)
    assert book.get("/api/settings").get_json()["claude_count"] == 0
    chat("set_note", tx_id=tx["id"], notes="one")
    chat("set_note", tx_id=tx["id"], notes="two")
    book.put(f"/api/transactions/{tx['id']}", json={"notes": "mine"})  # the operator's own
    assert book.get("/api/settings").get_json()["claude_count"] == 2

    looked = book.post("/api/changes/looked", json={}).get_json()
    assert looked["claude_count"] == 0
    assert looked["last_looked"] == looked["newest"]

    chat("set_note", tx_id=tx["id"], notes="three")
    shown = book.get("/api/history").get_json()
    assert shown["claude_count"] == 1
    assert shown["last_looked"] == looked["last_looked"]


def test_the_mark_never_moves_back(book):
    tx = first_row(book)
    chat("set_note", tx_id=tx["id"], notes="one")
    newest = book.post("/api/changes/looked", json={}).get_json()["last_looked"]
    assert book.post("/api/changes/looked", json={"upto": 1}).get_json()["last_looked"] == newest
    assert book.post("/api/changes/looked", json={"upto": "x"}).status_code == 400


def test_looking_is_not_a_change_and_not_the_chats(book):
    before = book.get("/api/history").get_json()["entries"]
    book.post("/api/changes/looked", json={})
    assert book.get("/api/history").get_json()["entries"] == before
    assert book.post("/api/changes/looked", json={}, environ_base=chat_environ()).status_code == 403


# --- the history list's marks --------------------------------------------------


def test_a_blocked_undo_is_named_in_the_list(book):
    tx = first_row(book)
    book.put(f"/api/transactions/{tx['id']}", json={"notes": "first"})
    book.put(f"/api/transactions/{tx['id']}", json={"notes": "second"})
    entries = book.get("/api/history?blockers=1").get_json()["entries"]
    first, second = entries[1], entries[0]
    assert first["blocked_by"]["id"] == second["id"]
    assert second["blocked_by"] is None
    assert all(e["asked_first"] is False for e in entries)


def test_an_undo_can_itself_be_undone(book):
    tx = first_row(book)
    book.put(f"/api/transactions/{tx['id']}", json={"notes": "first"})
    entry = book.get("/api/history").get_json()["entries"][0]["id"]
    undo = book.post(f"/api/history/{entry}/undo").get_json()
    assert first_row(book)["notes"] is None
    assert book.post(f"/api/history/{undo['by']}/undo").status_code == 200
    assert first_row(book)["notes"] == "first"


def test_a_chat_change_over_the_count_is_marked_asked_first(book, conn):
    conn.execute("INSERT INTO change_entries (via, actor, summary, open) VALUES ('chat', ?, 'many', 0)",
                 (ACTOR,))
    entry = conn.execute("SELECT MAX(id) FROM change_entries").fetchone()[0]
    for n in range(mcp_tools.MANY_ROWS + 1):
        conn.execute("INSERT INTO change_rows (entry_id, tbl, row_id, op) VALUES (?, 'transactions', ?, 'update')",
                     (entry, 1000 + n))
    conn.commit()
    shown = book.get("/api/history").get_json()
    assert shown["entries"][0]["asked_first"] is True
    assert shown["many_rows"] == mcp_tools.MANY_ROWS
    assert book.get(f"/api/history/{entry}").get_json()["asked_first"] is True


# --- refused statements --------------------------------------------------------


def test_a_refused_statement_is_kept_without_its_rows_or_file_name(book, conn):
    rows_before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    preview = upload_refused(book)
    assert preview["errors"] and preview["errors"][0]["tie"]["currency"] == "SGD"
    refused = book.get("/api/statements/refused").get_json()["refused"]
    assert len(refused) == 1
    r = refused[0]
    assert r["account_name"] == BANK and r["statement_date"] == "2026-09-30"
    assert r["difference_minor"] == 1234 and r["set_aside"] is False
    assert r["opening_minor"] + r["rows_minor"] - r["closing_minor"] == r["difference_minor"]
    assert "file" not in r and "filename" not in r
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == rows_before
    account = conn.execute("SELECT id FROM accounts WHERE name = ?", (BANK,)).fetchone()[0]
    assert book.get(f"/api/statements/refused?account_id={account}").get_json()["refused"] == refused


def test_known_leave_it_sets_it_aside_and_it_stays_refused(book):
    upload_refused(book)
    r = book.get("/api/statements/refused").get_json()["refused"][0]
    assert book.post(f"/api/statements/refused/{r['id']}/set-aside", json={}).status_code == 200
    again = book.get("/api/statements/refused").get_json()["refused"]
    assert len(again) == 1 and again[0]["set_aside"] is True
    # The same file again keeps it set aside.
    upload_refused(book)
    assert book.get("/api/statements/refused").get_json()["refused"][0]["set_aside"] is True
    book.post(f"/api/statements/refused/{r['id']}/set-aside", json={"aside": False})
    assert book.get("/api/statements/refused").get_json()["refused"][0]["set_aside"] is False
    assert book.post("/api/statements/refused/999/set-aside", json={}).status_code == 404


def test_a_file_that_ties_for_the_same_day_ends_the_refusal(book):
    upload_refused(book)
    bring_in(book, BANK, [("2026-09-05", "SAMPLE SHOP", 5000)],
             opening=70050, closing=65050, closing_date="2026-09-30")
    assert book.get("/api/statements/refused").get_json()["refused"] == []


# --- an account's tie lines ------------------------------------------------------


def test_an_accounts_tie_lines_draw_each_statement_as_a_sum(book, conn):
    bring_in(book, BANK, [("2026-09-05", "SAMPLE SHOP", 5000)],
             opening=70050, closing=65050, closing_date="2026-09-30")
    account = conn.execute("SELECT id FROM accounts WHERE name = ?", (BANK,)).fetchone()[0]
    shown = book.get(f"/api/accounts/{account}/ties").get_json()
    newest, first = shown["ties"][0], shown["ties"][-1]
    assert newest["status"] == "ties" and newest["date"] == "2026-09-30"
    assert newest["opening_minor"] + newest["rows_minor"] == newest["closing_minor"]
    assert newest["rows"] == 1
    assert first["status"] == "not_checked"
    assert book.get("/api/accounts/9999/ties").status_code == 404


# --- the queue's mixed-merchant rows ---------------------------------------------


def test_look_mixed_lists_rows_of_a_mixed_merchant_not_yet_set_by_hand(book, conn):
    tx = first_row(book)
    conn.execute("INSERT INTO services (name, review_each_time) VALUES ('Sample Market', 1)")
    svc = conn.execute("SELECT id FROM services WHERE name = 'Sample Market'").fetchone()[0]
    conn.execute("UPDATE transactions SET service_id = ? WHERE id = ?", (svc, tx["id"]))
    conn.commit()
    listed = book.get("/api/transactions?look=mixed").get_json()["transactions"]
    assert [r["id"] for r in listed] == [tx["id"]]
    assert listed[0]["account_id"]
    book.put(f"/api/transactions/{tx['id']}", json={"type_id": None})
    assert book.get("/api/transactions?look=mixed").get_json()["transactions"] == []


def test_tx_id_reads_one_row_hidden_merchant_or_not(book, conn):
    tx = first_row(book)
    shown = book.get(f"/api/transactions?tx_id={tx['id']}").get_json()
    assert [r["id"] for r in shown["transactions"]] == [tx["id"]]
    assert book.get("/api/transactions?tx_id=999999").get_json()["transactions"] == []
    conn.execute("INSERT INTO services (name, exclude_from_expense_views) VALUES ('Sample Hidden', 1)")
    svc = conn.execute("SELECT id FROM services WHERE name = 'Sample Hidden'").fetchone()[0]
    conn.execute("UPDATE transactions SET service_id = ? WHERE id = ?", (svc, tx["id"]))
    conn.commit()
    assert [r["id"] for r in book.get(f"/api/transactions?tx_id={tx['id']}").get_json()["transactions"]] == [tx["id"]]
