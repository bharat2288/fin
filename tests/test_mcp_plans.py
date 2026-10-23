"""Chat-plan-apply 02: plan_preview and plan_apply, the chat's batch of teaches
(spec cases 1, 4, 8).

plan_preview runs `POST /api/plans/preview` in-process and writes nothing;
plan_apply runs `POST /api/plans/apply` with the preview's token and writes the
batch as one change entry, stamped as the chat's. Both stop with "Claude may
write" off, before anything runs. The token stands in for expected_count: a
batch over MANY_ROWS rows applies on it and is recorded as asked first.

Each case is one call through mcp_tools.call against a temporary book, then
what the book holds, read back table by table. Every merchant, wording and
figure is invented.
"""

import app as fin_app
import chat_writes
import mcp_tools
import relabel_preview
from test_book_and_type_switch import statement, type_id  # noqa: F401 (statement is a fixture)
from test_mcp_tools import ACTOR
from test_plan_apply import STALE, book_state, items_of, queue, untyped  # noqa: F401 (queue is a fixture)


def call(tool_name, **args):
    return mcp_tools.call(tool_name, args, actor=ACTOR)


def change(entry_id) -> dict:
    shown = call("change", entry_id=entry_id)
    assert shown["ok"], shown
    return shown["result"]


# --- the surface ------------------------------------------------------------------------------


def test_the_preview_writes_nothing_and_the_apply_writes_and_both_stop_when_off():
    preview, apply = mcp_tools.BY_NAME["plan_preview"], mcp_tools.BY_NAME["plan_apply"]

    assert (preview.write, apply.write) == (False, True)
    assert {"plan_preview", "plan_apply"} <= mcp_tools._STOPPED_WHEN_OFF
    assert preview.input_schema["required"] == ["items"]
    assert sorted(apply.input_schema["required"]) == ["items", "token"]
    # The token is the operator's yes to the preview's count: there is no other count to give.
    assert "expected_count" not in apply.input_schema["properties"]
    assert preview.request({"items": [{"tx_ids": [1]}]}) == ("POST", "/api/plans/preview", {"items": [{"tx_ids": [1]}]})
    assert preview.input_schema["properties"]["items"]["maxItems"] == fin_app.MAX_PLAN_ITEMS
    item = preview.input_schema["properties"]["items"]["items"]["properties"]
    assert set(item) == fin_app._PLAN_ITEM_KEYS
    assert tuple(item["match_type"]["enum"]) == relabel_preview.MATCH_TYPES
    assert tuple(item["past"]["enum"]) == fin_app.CHECK_EACH_ROW_PAST
    assert item["past_rows"]["enum"] == [relabel_preview.APPLY, relabel_preview.ONLY_NEW]
    assert apply.input_schema["properties"]["items"] == preview.input_schema["properties"]["items"]


# --- case 1: preview, then apply as one chat change --------------------------------------------


def test_case_1_chat_previews_a_batch_and_applies_it_as_one_change_stamped_as_chats(conn, queue):
    items = items_of(conn, queue)
    before = book_state(conn)

    shown = call("plan_preview", items=items)

    assert shown["ok"], shown
    result = shown["result"]
    assert [(i["merchant"], i["count"], i["overlaps"]) for i in result["items"]] == [
        ("Indigoair", 2, []), ("Rhode", 1, []), ("Vaura Pilates", 2, [])]
    assert (result["refusals"], result["merchants"], result["rows"]) == ([], 3, 5)
    assert book_state(conn) == before

    applied = call("plan_apply", items=items, token=result["token"])

    assert applied["ok"], applied
    assert (applied["result"]["merchants"], applied["result"]["rows"]) == (3, 5)
    entry = change(applied["change"]["entry_id"])
    assert (entry["via"], entry["actor"]) == ("chat", ACTOR)
    assert entry["summary"] == "Chat applied a plan: 3 merchants, 5 rows"
    assert entry["asked_first"] is False
    after = book_state(conn)
    assert after["change_entries"] == before["change_entries"] + 1
    assert {r[0] for r in conn.execute("SELECT id FROM transactions WHERE type_id IS NULL")}.isdisjoint(
        queue["indigo"] + queue["rhode"] + queue["vaura"])


def test_a_second_apply_of_the_same_token_is_refused_through_chat_and_writes_nothing(conn, queue):
    items = items_of(conn, queue)
    del items[1]["wordings"]
    token = call("plan_preview", items=items)["result"]["token"]
    assert call("plan_apply", items=items, token=token)["ok"]
    after_first = book_state(conn)

    again = call("plan_apply", items=items, token=token)

    assert again == {"ok": False, "error": STALE, "stale": True}
    assert book_state(conn) == after_first


def test_a_refused_item_is_named_to_chat_and_the_apply_writes_nothing(conn, queue):
    items = items_of(conn, queue)
    token = call("plan_preview", items=items)["result"]["token"]
    items[2]["book"] = "Nowhere"
    before = book_state(conn)

    refused = call("plan_apply", items=items, token=token)

    assert refused["ok"] is False
    assert [r["merchant"] for r in refused["refusals"]] == ["Vaura Pilates"]
    assert book_state(conn) == before


# --- case 4: the switch off stops both, before anything runs ------------------------------------


def test_case_4_with_writes_off_both_plan_tools_refuse_with_the_switchs_words(client, conn, queue):
    items = items_of(conn, queue)
    token = call("plan_preview", items=items)["result"]["token"]
    assert client.put("/api/chat-writes", json={"enabled": False}).status_code == 200
    before = book_state(conn)

    assert call("plan_preview", items=items) == {"ok": False, "error": chat_writes.WRITES_OFF}
    assert call("plan_apply", items=items, token=token) == {"ok": False, "error": chat_writes.WRITES_OFF}

    assert book_state(conn) == before


# --- case 8: size ------------------------------------------------------------------------------


def test_case_8_a_batch_over_many_rows_applies_on_the_token_and_is_recorded_as_asked_first(
        conn, queue):
    bulk = [untyped(conn, queue["statement"], f"SAMPLEBULK {n:02d}", 1000 + n, f"2026-09-{n + 1:02d}")
            for n in range(22)]
    items = [{"tx_ids": bulk, "service_name": "Samplebulk", "pattern": "SAMPLEBULK",
              "type_id": type_id(conn, "Shopping"), "book": "Household"}]
    shown = call("plan_preview", items=items)["result"]
    assert shown["rows"] == 22 > mcp_tools.MANY_ROWS

    applied = call("plan_apply", items=items, token=shown["token"])

    assert applied["ok"], applied
    rows = applied["change"]["rows"]
    assert rows > mcp_tools.MANY_ROWS
    entry = change(applied["change"]["entry_id"])
    assert (entry["asked_first"], entry["asked_count"]) == (True, rows)
    assert entry["summary"] == "Chat applied a plan: 1 merchant, 22 rows"
    assert conn.execute(
        "SELECT COUNT(*) FROM transactions t JOIN services s ON s.id = t.service_id WHERE s.name = 'Samplebulk'"
    ).fetchone()[0] == 22


def test_case_8_a_batch_of_21_merchants_is_refused_through_chat(conn, queue):
    item = items_of(conn, queue)[0]
    items = [{**item, "tx_ids": [10_000 + n]} for n in range(21)]
    before = book_state(conn)

    for answer in (call("plan_preview", items=items), call("plan_apply", items=items, token="0" * 32)):
        assert answer["ok"] is False and "20" in answer["error"]
    assert book_state(conn) == before
