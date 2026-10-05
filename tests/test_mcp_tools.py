"""The chat tools (fin-surfaces 01): the tool list is the permission list.

Each tool runs fin's own route in-process; writes land at once, stamped as
the chat client's in the change history, and undoable. Driven through
mcp_tools.call against a temporary database. Every name and figure is
invented.
"""

import json
import sqlite3

import pytest

import mcp_tools
from test_balance_sheet import bring_in, make_account

BANK = "Sample Bank 0002"
# The chat gate's label for the client: folio's chat:<sub>.
ACTOR = "chat:sample-subject"

# The whole surface. A tool added or removed is a change to what a chat client
# may do, so it changes this list in the same commit (ruling Q3: the tool list
# is the permission list, changed only through the pipeline).
READS = [
    "balance_sheet", "spending_cards", "spending_by_type", "spending_by_month", "transactions",
    "review_summary", "accounts", "figures", "merchants", "rules", "types", "books", "flows",
    "coverage", "rates", "history", "change",
]
WRITES = [
    "set_label", "set_note", "label_review", "enter_figure", "correct_figure", "delete_figure",
    "add_rule", "change_rule", "delete_rule", "rerun_rules", "change_merchant", "merge_merchants",
    "add_company_or_person", "pair_transfers", "fetch_rate", "set_rate", "stop_claude_writing", "undo",
]


def call(tool_name, **args):
    return mcp_tools.call(tool_name, args, actor=ACTOR)


def keys_anywhere(value) -> set:
    if isinstance(value, dict):
        return set(value) | set().union(*(keys_anywhere(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(keys_anywhere(v) for v in value)) if value else set()
    return set()


@pytest.fixture
def book(client):
    bring_in(client, BANK, [
        ("2026-08-03", "SAMPLE GROCER", 14000),
        ("2026-08-12", "SAMPLE CAFE", 15950),
    ], opening=100000, closing=100000 - 14000 - 15950)
    return client


def first_row():
    return call("transactions", per_page=50)["result"]["transactions"][0]


# --- the surface ------------------------------------------------------------


def test_the_tool_list_is_exactly_the_ruled_surface():
    assert [t.name for t in mcp_tools.TOOLS if not t.write] == READS
    assert [t.name for t in mcp_tools.TOOLS if t.write] == WRITES


def test_no_tool_reaches_what_the_ruling_keeps_out():
    paths = set()
    for tool in mcp_tools.TOOLS:
        args = {k: 1 if v.get("type") == "integer" else "x" for k, v in tool.input_schema["properties"].items()
                if v.get("type") in ("integer", "string")}
        if "kind" in args:
            args["kind"] = "company"
        paths.add(tool.request(args)[1])
    for forbidden in ("/api/import", "/api/subscriptions", "/api/suggestions", "/api/accounts/1",
                      "/api/services/bulk-rename", "/api/loan-interest"):
        assert not any(p.startswith(forbidden) for p in paths), forbidden
    assert not any(p.startswith("/api/transactions/") and p != "/api/transactions/1" for p in paths)


def test_every_tool_has_a_closed_schema_and_a_description():
    for tool in mcp_tools.TOOLS:
        assert tool.description
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema["additionalProperties"] is False
        assert set(tool.input_schema["required"]) <= set(tool.input_schema["properties"])
        json.dumps(tool.input_schema)


def test_an_unknown_tool_or_argument_is_refused(client):
    assert call("drop_everything") == {"ok": False, "error": "no tool named 'drop_everything'"}
    assert call("transactions", sql="SELECT 1")["error"] == "transactions does not take sql"
    assert call("set_label")["error"] == "set_label needs tx_id"
    assert call("set_label", tx_id="7")["error"] == "tx_id must be an integer"


# --- reads ------------------------------------------------------------------


def test_reads_return_rows_and_the_balance_sheet(book):
    rows = call("transactions", per_page=50)["result"]["transactions"]
    assert {r["description"] for r in rows} == {"SAMPLE GROCER", "SAMPLE CAFE"}
    sheet = call("balance_sheet", month="2026-08")
    assert sheet["ok"] and "month_check" in sheet["result"]


def test_no_answer_carries_a_file_name_or_a_path(book):
    for tool in mcp_tools.TOOLS:
        if tool.write or tool.input_schema["required"]:
            continue
        answer = call(tool.name)
        assert not (keys_anywhere(answer) & mcp_tools.HIDDEN_KEYS), tool.name
    entry = call("history")["result"]["entries"][-1]
    shown = call("change", entry_id=entry["id"])
    assert not (keys_anywhere(shown) & mcp_tools.HIDDEN_KEYS)
    assert "sample.csv" not in json.dumps(shown)


def test_a_read_records_nothing(book):
    before = call("history")["result"]["entries"]
    call("balance_sheet", month="2026-08")
    call("transactions")
    assert call("history")["result"]["entries"] == before


# --- writes -----------------------------------------------------------------


def test_a_write_lands_at_once_stamped_as_the_client_and_undoes(book):
    row = first_row()

    done = call("set_label", tx_id=row["id"], book="Moom")

    assert done["ok"], done
    assert done["change"]["rows"] == 1
    latest = call("history")["result"]["entries"][0]
    assert (latest["id"], latest["via"], latest["actor"]) == (done["change"]["entry_id"], "chat", ACTOR)
    assert first_row()["book"] == "Moom"

    assert call("undo", entry_id=done["change"]["entry_id"])["ok"]
    assert first_row()["book"] == row["book"]


def test_a_refusal_comes_back_as_fins_own_message_and_changes_nothing(book):
    before = call("history")["result"]["entries"]
    answer = call("set_label", tx_id=first_row()["id"], book="Nobody")
    assert answer["ok"] is False and answer["error"]
    assert call("history")["result"]["entries"] == before


def test_a_failure_inside_fin_gives_a_fixed_message(book, monkeypatch):
    import app as fin_app

    def broken(*_a, **_k):
        raise RuntimeError("row SAMPLE GROCER 140.00 could not be read")

    monkeypatch.setitem(fin_app.app.view_functions, "api_update_transaction", broken)
    answer = call("set_label", tx_id=first_row()["id"], book="Moom")
    assert answer == {"ok": False, "error": mcp_tools.FIXED_ERROR}


def test_a_figure_entered_from_chat_shows_on_the_sheet(client):
    reno = make_account(client, "Sample Reno 0003", "bank")
    assert call("enter_figure", account_id=reno, amount="1000.00", date="2026-06-30")["ok"]
    [figure] = call("figures", account_id=reno)["result"]
    assert figure["source"] == "supplied"


def test_a_company_or_person_can_be_added_and_nothing_else(client):
    assert call("add_company_or_person", kind="person", name="Sample Friend")["ok"]
    assert call("add_company_or_person", kind="bank", name="Sample Bank 9")["error"] == (
        "kind must be one of company, person"
    )
    kinds = {a["name"]: a["type"] for a in call("accounts")["result"]}
    assert kinds["Sample Friend"] == "person"


# --- many rows --------------------------------------------------------------


@pytest.fixture
def many(client):
    bring_in(client, BANK, [(f"2026-08-{d:02d}", "SAMPLE GROCER", 1000) for d in range(1, 26)])
    svc = client.post("/api/services", json={"name": "Sample Grocer", "book": "Household"}).get_json()
    types = client.get("/api/types").get_json()
    listed = types if isinstance(types, list) else types["types"]
    type_id = listed[0]["id"]
    client.put(f"/api/services/{svc['id']}", json={"type_id": type_id})
    rule = client.post("/api/rules", json={"pattern": "SAMPLE GROCER", "service_id": svc["id"]})
    assert rule.status_code == 200, rule.get_json()
    return client


def labels(client) -> list:
    return sorted(
        (t["id"], t["service_id"], t["type_id"])
        for t in client.get("/api/transactions?per_page=100").get_json()["transactions"]
    )


def test_a_change_over_twenty_rows_stops_and_gives_the_count(many):
    before, history_before = labels(many), call("history")["result"]["entries"]

    answer = call("rerun_rules")

    assert answer["stopped"] is True
    count = answer["would_change_rows"]
    assert count > mcp_tools.MANY_ROWS
    assert labels(many) == before
    assert call("history")["result"]["entries"] == history_before

    assert call("rerun_rules", expected_count=count + 1)["stopped"] is True
    done = call("rerun_rules", expected_count=count)
    assert done["ok"] and done["change"]["rows"] == count
    assert labels(many) != before


def test_undoing_a_change_over_twenty_rows_stops_before_writing_and_gives_the_count(many):
    done = call("rerun_rules", expected_count=call("rerun_rules")["would_change_rows"])
    entry, count = done["change"]["entry_id"], done["change"]["rows"]
    after, history_after = labels(many), call("history")["result"]["entries"]

    answer = call("undo", entry_id=entry)

    assert (answer["ok"], answer.get("stopped"), answer.get("would_change_rows")) == (False, True, count)
    assert labels(many) == after
    assert call("history")["result"]["entries"] == history_after
    assert call("undo", entry_id=entry, expected_count=count + 1)["stopped"] is True

    undone = call("undo", entry_id=entry, expected_count=count)
    assert undone["ok"], undone
    assert undone["change"]["rows"] == count
    assert call("change", entry_id=entry)["result"]["undone_by"] == undone["change"]["entry_id"]


def test_a_change_that_could_not_be_put_back_is_reported_as_standing(many, monkeypatch):
    def broken(conn, entry_id):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(mcp_tools.history, "discard", broken)
    answer = call("rerun_rules")

    assert answer["ok"] is False and answer.get("stopped") is not True
    assert answer["change"]["rows"] > mcp_tools.MANY_ROWS
    assert "could not be put back" in answer["error"]
    assert str(answer["change"]["entry_id"]) in answer["error"]
    assert "FOREIGN KEY" not in answer["error"]


def test_the_chat_may_undo_only_a_chat_change(book):
    row = first_row()
    by_app = book.put(f"/api/transactions/{row['id']}", json={"book": "Moom"})
    entry = int(by_app.headers["X-Fin-Change"])
    history_before = call("history")["result"]["entries"]

    answer = call("undo", entry_id=entry)

    assert answer["ok"] is False and "only a change made from the chat" in answer["error"]
    assert first_row()["book"] == "Moom"
    assert call("history")["result"]["entries"] == history_before
    # The operator's own undo, in fin, is not limited.
    assert book.post(f"/api/history/{entry}/undo").status_code == 200
    assert first_row()["book"] == row["book"]
