"""The MCP surface at /mcp (fin-online D4): its tool list is pinned here.

GOLDEN is the tool list fin-surfaces 01 rules, written out: a tool added to or
removed from mcp_tools.TOOLS changes it in the same commit. The seam
(mcp_tools.TOOLS of mcp_tools.Tool, and mcp_tools.call(name, args, actor)) is
proved with a stand-in registry, and end to end through the gate with a real
write stamped as the chat client.
"""

from __future__ import annotations

import asyncio
import json

import httpx

import mcp_server
import mcp_tools
from gate_tokens import (
    APP_AUD,
    CHAT_AUD,
    book,  # noqa: F401 (fixture)
    composed,
    header,
    mint,
    running,
)

GOLDEN: list[str] = [
    # reads
    "balance_sheet", "spending_cards", "spending_by_type", "spending_by_month", "transactions",
    "review_summary", "accounts", "figures", "merchants", "rules", "types", "books", "flows",
    "coverage", "rates", "history", "change",
    # writes
    "set_label", "set_note", "label_review", "enter_figure", "correct_figure", "delete_figure",
    "add_rule", "change_rule", "delete_rule", "rerun_rules", "change_merchant", "merge_merchants",
    "add_company_or_person", "pair_transfers", "fetch_rate", "set_rate", "undo",
    # 01's Off switches: chat can turn Claude's writes off, never on.
    "turn_off_writes",
]

CHAT = header(mint(aud=CHAT_AUD))
ACCEPT = {"Accept": "application/json, text/event-stream"}


def _rpc(asgi, *calls: tuple[str, dict], headers=None) -> list[httpx.Response]:
    async def run():
        async with running(asgi):
            transport = httpx.ASGITransport(app=asgi)
            async with httpx.AsyncClient(transport=transport, base_url="http://fin.test") as client:
                out = []
                for n, (method, params) in enumerate(calls, 1):
                    body = {"jsonrpc": "2.0", "id": n, "method": method, "params": params}
                    out.append(await client.post("/mcp", json=body, headers={**ACCEPT, **(headers or CHAT)}))
                return out

    return asyncio.run(run())


def _endpoint(tools, call):
    return mcp_server.ChatEndpoint(local_dev=False, registry=(tools, call))


def test_the_registry_holds_exactly_the_golden_tools():
    tools, _call = mcp_server.load_registry()

    assert [tool.name for tool in tools] == GOLDEN


def test_mcp_lists_exactly_the_golden_tools_through_the_gate(book):
    [response] = _rpc(composed(), ("tools/list", {}))

    assert response.status_code == 200, response.text
    listed = response.json()["result"]["tools"]
    assert [tool["name"] for tool in listed] == GOLDEN
    # Each tool is registered from its own fields; only a write is not read-only.
    for tool, registered in zip(listed, mcp_tools.TOOLS):
        assert (tool["description"], tool["inputSchema"]) == (registered.description, registered.input_schema)
        assert tool["annotations"]["readOnlyHint"] is (not registered.write)


SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}


def test_the_seam_lists_mcp_tools_and_calls_them_with_the_chat_actor(book):
    calls = []

    def call(name, args, actor):
        calls.append((name, args, actor))
        return {"doubled": args["n"] * 2}

    tools = [mcp_tools.Tool("double", "Doubles n.", SCHEMA, False, lambda a: ("GET", "/", None))]
    listed, called = _rpc(
        composed(mcp_app=_endpoint(tools, call)),
        ("tools/list", {}),
        ("tools/call", {"name": "double", "arguments": {"n": 21}}),
    )

    [tool] = listed.json()["result"]["tools"]
    assert (tool["name"], tool["description"], tool["inputSchema"]) == ("double", "Doubles n.", SCHEMA)
    result = called.json()["result"]
    assert not result.get("isError")
    assert json.loads(result["content"][0]["text"]) == {"doubled": 42}
    assert calls == [("double", {"n": 21}, "chat:fake-subject-1")]


def test_a_failing_tool_answers_a_fixed_message_never_its_text(book, caplog):
    def call(name, args, actor):
        raise RuntimeError("SECRET-DETAIL /volume/fin.db")

    unknown, failed = _rpc(
        composed(mcp_app=_endpoint([mcp_tools.Tool("boom", "Fails.", {"type": "object"}, False, lambda a: ("GET", "/", None))], call)),
        ("tools/call", {"name": "nope", "arguments": {}}),
        ("tools/call", {"name": "boom", "arguments": {}}),
    )

    assert unknown.json()["result"]["content"][0]["text"] == mcp_server.UNKNOWN_TOOL
    assert failed.json()["result"]["isError"] is True
    assert failed.json()["result"]["content"][0]["text"] == mcp_server.TOOL_FAILED
    assert "SECRET-DETAIL" not in failed.text and "SECRET-DETAIL" not in caplog.text


def test_mcp_refuses_an_app_gate_token(book):
    [response] = _rpc(composed(), ("tools/list", {}), headers=header(mint(aud=APP_AUD)))

    assert response.status_code == 403
    assert response.json() == {"error": "access denied"}


def test_the_endpoint_refuses_a_request_the_gate_did_not_pass():
    endpoint = _endpoint([], None)

    async def run():
        async with endpoint.lifespan():
            transport = httpx.ASGITransport(app=endpoint)
            async with httpx.AsyncClient(transport=transport, base_url="http://fin.test") as client:
                body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
                return await client.post("/mcp", json=body, headers={**ACCEPT, **CHAT})

    response = asyncio.run(run())

    assert response.status_code == 401
    assert response.json() == {"error": "authentication required"}


def test_a_refused_call_reaches_the_chat_as_an_error_carrying_the_answer(book):
    def call(name, args, actor):
        return {"ok": False, "error": "x was given arguments it cannot use"}

    tools = [mcp_tools.Tool("x", "Refuses.", {"type": "object"}, True, lambda a: ("POST", "/", None))]
    [called] = _rpc(composed(mcp_app=_endpoint(tools, call)), ("tools/call", {"name": "x", "arguments": {}}))

    result = called.json()["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"] == "x was given arguments it cannot use"


def test_a_chat_write_through_the_gate_lands_stamped_as_the_chat_client(book):
    # The hosted path whole: the chat gate verifies the token, /mcp calls
    # mcp_tools, which runs fin's own route in-process past Flask's second
    # layer (LOCAL_DEV off in this book), and the change history names the
    # client the gate verified.
    [called] = _rpc(composed(), ("tools/call", {"name": "add_company_or_person",
                                                "arguments": {"name": "Sample Company", "kind": "company"}}))

    result = called.json()["result"]
    assert not result.get("isError"), result
    [entry] = [e for e in _history(book) if e[0] == result["structuredContent"]["change"]["entry_id"]]
    assert entry[1:] == ("chat", "chat:fake-subject-1")


def _history(db_path):
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT id, via, actor FROM change_entries").fetchall()
    finally:
        conn.close()
