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
from test_balance_sheet import bring_in, make_account
from test_mcp_tools import many  # noqa: F401 (a fixture)

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


# --- the "Claude may write" switch (chat_writes.py, the one switch) ----------
# The switch's own contract is tests/test_chat_writes_switch.py; these hold
# what the screens add to it.


def turn(client, on, **kwargs):
    return client.put("/api/chat-writes", json={"enabled": on}, **kwargs)


def test_the_switch_is_on_until_the_operator_turns_it_off(book):
    assert book.get("/api/chat-writes").get_json()["enabled"] is True
    resp = turn(book, False)
    assert resp.status_code == 200 and resp.get_json()["enabled"] is False
    assert book.get("/api/chat-writes").get_json()["enabled"] is False


def test_with_the_switch_off_chat_writes_are_refused_and_reads_work(book):
    tx = first_row(book)
    turn(book, False)
    before = book.get("/api/history").get_json()["entries"]

    answer = chat("set_note", tx_id=tx["id"], notes="sample note")
    assert answer["ok"] is False and "turned off" in answer["error"]
    assert chat("transactions", per_page=5)["ok"] is True
    assert book.get("/api/history").get_json()["entries"] == before
    assert first_row(book)["notes"] is None

    turn(book, True)
    done = chat("set_note", tx_id=tx["id"], notes="sample note")
    assert done["ok"] is True

    # Undo is a write too: refused while the switch is off.
    turn(book, False)
    before = book.get("/api/history").get_json()["entries"]
    refused = chat("undo", entry_id=done["change"]["entry_id"])
    assert refused["ok"] is False and "turned off" in refused["error"]
    assert first_row(book)["notes"] == "sample note"
    assert book.get("/api/history").get_json()["entries"] == before
    turn(book, True)
    assert chat("undo", entry_id=done["change"]["entry_id"])["ok"] is True
    assert first_row(book)["notes"] is None


def test_the_switch_is_never_turned_on_from_the_chat(book):
    resp = turn(book, True, environ_base=chat_environ())
    assert resp.status_code == 403
    turn(book, False)
    for tool in mcp_tools.TOOLS:
        args = {k: 1 if v.get("type") == "integer" else "x"
                for k, v in tool.input_schema["properties"].items() if v.get("type") in ("integer", "string")}
        method, path, body = tool.request(args)
        assert not (path == "/api/chat-writes" and (body or {}).get("enabled") is not False), tool.name
    assert book.get("/api/chat-writes").get_json()["enabled"] is False


def test_the_chat_can_turn_writes_off_even_while_they_are_off(book):
    before = book.get("/api/history").get_json()["entries"]
    answer = chat("turn_off_writes")
    assert answer["ok"] is True
    assert book.get("/api/chat-writes").get_json()["enabled"] is False
    # Again, with writes already off: still allowed, still off.
    again = chat("turn_off_writes")
    assert again["ok"] is True
    assert "change" not in again
    assert book.get("/api/chat-writes").get_json()["enabled"] is False
    assert book.get("/api/history").get_json()["entries"] == before
    tx = first_row(book)
    assert chat("set_note", tx_id=tx["id"], notes="x")["ok"] is False


def test_with_the_switch_off_an_import_from_the_chats_side_is_refused(client):
    """The upload command arrives stamped as the chat (Claude Code
    (upload)); with writes off it is refused, and the operator's own import
    still works."""
    upload = access_gate.AccessIdentity(email="", gate=access_gate.APP_GATE, client=access_gate.UPLOAD_ACTOR)
    environ = {"asgi.scope": {access_gate.ACCESS_IDENTITY_SCOPE_KEY: upload}}
    turn(client, False)
    up = client.post("/api/import/upload", data={"files": (io.BytesIO(b"x"), "sample.csv")},
                     content_type="multipart/form-data", environ_base=environ)
    assert up.status_code == 403 and "switched off" in up.get_json()["error"]
    confirm = client.post("/api/import/confirm", json={"import_id": 1, "groups": []}, environ_base=environ)
    assert confirm.status_code == 403
    # The operator's import is not the chat's: it goes through.
    bring_in(client, BANK, [("2026-08-03", "SAMPLE GROCER", 14000)], opening=100000, closing=86000)
    assert client.get("/api/transactions").get_json()["total"] == 1


def test_the_switch_takes_only_true_or_false(book):
    assert turn(book, "no").status_code == 400


def test_flipping_the_switch_is_not_a_change_to_the_book(book):
    before = book.get("/api/history").get_json()["entries"]
    turn(book, False)
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


def test_home_is_told_when_the_operator_last_looked(book):
    assert book.get("/api/settings").get_json()["last_looked_at"] is None
    looked = book.post("/api/changes/looked", json={}).get_json()
    at = looked["last_looked_at"]
    assert at and len(at) == 19 and at[4] == "-" and at[10] == " "   # UTC, as the history's times
    assert book.get("/api/settings").get_json()["last_looked_at"] == at


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


def test_asked_first_is_recorded_when_the_chat_carries_the_agreed_count(many):
    stopped = chat("rerun_rules")
    assert stopped["stopped"] is True
    # Stopped: nothing stands, nothing is recorded as asked.
    assert not any(e["asked_first"] for e in many.get("/api/history").get_json()["entries"])

    count = stopped["would_change_rows"]
    done = chat("rerun_rules", expected_count=count)
    entry = done["change"]["entry_id"]
    shown = many.get("/api/history").get_json()
    assert shown["many_rows"] == mcp_tools.MANY_ROWS
    top = shown["entries"][0]
    assert (top["id"], top["asked_first"], top["asked_count"]) == (entry, True, count)
    one = many.get(f"/api/history/{entry}").get_json()
    assert one["asked_first"] is True and one["asked_count"] == count


def test_a_large_change_not_asked_in_chat_is_not_marked(many, conn):
    # The operator's own change over the count, and a chat change under it.
    many.post("/api/rules/recategorize", json={})
    tx = first_row(many)
    chat("set_note", tx_id=tx["id"], notes="small")
    entries = many.get("/api/history").get_json()["entries"]
    assert entries[1]["rows"] > mcp_tools.MANY_ROWS and entries[1]["via"] == "app"
    assert not any(e["asked_first"] for e in entries)
    # A chat entry over the count with no recorded yes is not marked either.
    conn.execute("INSERT INTO change_entries (via, actor, summary, open) VALUES ('chat', ?, 'many', 0)", (ACTOR,))
    entry = conn.execute("SELECT MAX(id) FROM change_entries").fetchone()[0]
    for n in range(mcp_tools.MANY_ROWS + 1):
        conn.execute("INSERT INTO change_rows (entry_id, tbl, row_id, op) VALUES (?, 'transactions', ?, 'update')",
                     (entry, 1000 + n))
    conn.commit()
    assert many.get(f"/api/history/{entry}").get_json()["asked_first"] is False


# --- each changed row's currency --------------------------------------------------


def test_a_change_names_each_rows_currency(client, conn):
    bring_in(client, BANK, [("2026-08-03", "SAMPLE GROCER", 14000)], opening=100000, closing=86000)
    conn.execute("UPDATE accounts SET currency = 'INR' WHERE name = ?", (BANK,))
    conn.commit()
    tx = first_row(client)
    client.put(f"/api/transactions/{tx['id']}", json={"notes": "rupees"})
    entry = client.get("/api/history").get_json()["entries"][0]["id"]
    changes = client.get(f"/api/history/{entry}").get_json()["changes"]
    assert [c["currency"] for c in changes] == ["INR"]
    # The import's own entry: its rows, statement and figure, all the account's.
    imported = client.get("/api/history").get_json()["entries"][-1]["id"]
    held = client.get(f"/api/history/{imported}").get_json()["changes"]
    assert {c["currency"] for c in held if c["table"] in ("transactions", "statements", "anchors")} == {"INR"}


# --- the quiet mark: what Claude changed since you last looked ---------------------


def test_marks_list_the_rows_and_balances_claude_changed_since_you_looked(book, conn):
    tx = first_row(book)
    other = book.get("/api/transactions?per_page=50").get_json()["transactions"][1]
    assert book.get("/api/changes/marks").get_json() == {"last_looked": None, "rows": {}, "accounts": {}}

    note = chat("set_note", tx_id=tx["id"], notes="from chat")["change"]["entry_id"]
    book.put(f"/api/transactions/{other['id']}", json={"notes": "mine"})  # the operator's own: no mark
    loan = make_account(book, "Sample Loan", "loan")
    figure = chat("enter_figure", account_id=loan, amount="1000.00", date="2026-08-31")["change"]["entry_id"]

    marks = book.get("/api/changes/marks").get_json()
    assert marks["rows"] == {str(tx["id"]): note}
    assert marks["accounts"] == {str(loan): figure}

    # An undone change leaves no mark; looking clears them all.
    book.post(f"/api/history/{figure}/undo")
    assert book.get("/api/changes/marks").get_json()["accounts"] == {}
    book.post("/api/changes/looked", json={})
    looked = book.get("/api/changes/marks").get_json()
    assert looked["rows"] == {} and looked["accounts"] == {}


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


def test_a_tie_line_keeps_money_in_and_out_apart_and_names_its_rows(book, conn):
    bring_in(book, BANK, [("2026-09-05", "SAMPLE SHOP", 5000), ("2026-09-09", "SAMPLE REFUND", -1500)],
             opening=70050, closing=66550, closing_date="2026-09-30")
    account = conn.execute("SELECT id FROM accounts WHERE name = ?", (BANK,)).fetchone()[0]
    newest = book.get(f"/api/accounts/{account}/ties").get_json()["ties"][0]
    assert (newest["in_minor"], newest["out_minor"]) == (1500, 5000)
    assert newest["opening_minor"] + newest["in_minor"] - newest["out_minor"] == newest["closing_minor"]
    held = {r[0] for r in conn.execute(
        "SELECT t.id FROM transactions t JOIN statements s ON t.statement_id = s.id "
        "WHERE s.account_id = ? AND t.date LIKE '2026-09-%'", (account,))}
    assert set(newest["row_ids"]) == held and len(held) == 2


# --- the queue's mixed-merchant rows ---------------------------------------------


def test_look_mixed_lists_rows_of_a_mixed_merchant_not_yet_set_by_hand(book, conn):
    tx = first_row(book)
    conn.execute("INSERT INTO services (name, review_each_time) VALUES ('Sample Market', 1)")
    svc = conn.execute("SELECT id FROM services WHERE name = 'Sample Market'").fetchone()[0]
    type_id = conn.execute("SELECT id FROM types WHERE kind = 'spending' ORDER BY id LIMIT 1").fetchone()[0]
    conn.execute("UPDATE transactions SET service_id = ?, type_id = ?, flow_type = 'expense' WHERE id = ?",
                 (svc, type_id, tx["id"]))
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


def test_look_mixed_never_lists_a_row_the_queue_lists_elsewhere(book, conn):
    """An untyped row is the queue's "no type" item and a transfer waiting
    for review its "This was…" item: neither is also a mixed-merchant item."""
    rows = book.get("/api/transactions?per_page=50").get_json()["transactions"]
    conn.execute("INSERT INTO services (name, review_each_time) VALUES ('Sample Market', 1)")
    svc = conn.execute("SELECT id FROM services WHERE name = 'Sample Market'").fetchone()[0]
    conn.execute("UPDATE transactions SET service_id = ?, type_id = NULL, flow_type = 'expense' WHERE id = ?",
                 (svc, rows[0]["id"]))
    conn.execute("UPDATE transactions SET service_id = ?, type_id = NULL, flow_type = 'review' WHERE id = ?",
                 (svc, rows[1]["id"]))
    conn.commit()
    assert book.get("/api/transactions?look=mixed").get_json()["transactions"] == []
    untyped = {r["id"] for r in book.get("/api/transactions?types=__untyped__").get_json()["transactions"]}
    review = {r["id"] for r in book.get("/api/transactions?flow=review").get_json()["transactions"]}
    assert rows[0]["id"] in untyped and rows[1]["id"] in review


def test_between_two_of_your_figures_the_tie_line_shows_what_the_rows_moved(client, conn):
    make_account(client, "Sample Cash Account", "bank")
    account = conn.execute("SELECT id FROM accounts WHERE name = 'Sample Cash Account'").fetchone()[0]
    assert client.post("/api/anchors", json={"account_id": account, "amount": "1000.00", "date": "2026-08-01"}).status_code == 200
    resp = client.post("/api/anchors", json={"account_id": account, "amount": "900.00", "date": "2026-08-31"})
    assert resp.status_code == 200, resp.get_json()
    bring_in(client, "Sample Cash Account", [("2026-08-05", "SAMPLE SHOP", 20000), ("2026-08-09", "SAMPLE REFUND", -5000)])
    later, first = client.get(f"/api/accounts/{account}/ties").get_json()["ties"]
    assert first["status"] == later["status"] == "your figure"
    assert (later["opening_minor"], later["in_minor"], later["out_minor"]) == (100000, 5000, 20000)
    # 1,000.00 + 50.00 - 200.00 = 850.00 from the rows; the figure says 900.00.
    assert later["moved_minor"] == 5000
    assert len(later["row_ids"]) == 2 and first["row_ids"] == []


def test_the_switch_says_when_it_was_last_flipped(book):
    assert book.get("/api/chat-writes").get_json()["changed_at"] is None
    turn(book, False)
    assert book.get("/api/chat-writes").get_json()["changed_at"]


def test_the_history_says_when_the_operator_last_looked(book):
    assert book.get("/api/history").get_json()["last_looked_at"] is None
    book.post("/api/changes/looked", json={})
    assert book.get("/api/history").get_json()["last_looked_at"]
