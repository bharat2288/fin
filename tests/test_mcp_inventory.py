"""The MCP surface at /mcp (fin-online D4): its tool list is pinned here.

GOLDEN is the tool list fin-surfaces 01 rules. This branch builds the hosting
shell only, so it is empty; the build that adds mcp_tools.py updates GOLDEN in
the same change. The seam (mcp_tools.TOOLS and mcp_tools.call(name, args,
actor)) is proved with a stand-in registry.
"""

from __future__ import annotations

import asyncio
import json

import httpx

import mcp_server
from gate_tokens import (
    APP_AUD,
    CHAT_AUD,
    book,  # noqa: F401 (fixture)
    composed,
    header,
    mint,
    running,
)

GOLDEN: list[str] = []

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

    assert [name for name, *_rest in tools] == GOLDEN


def test_mcp_lists_exactly_the_golden_tools_through_the_gate(book):
    [response] = _rpc(composed(), ("tools/list", {}))

    assert response.status_code == 200, response.text
    assert [tool["name"] for tool in response.json()["result"]["tools"]] == GOLDEN


SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}


def test_the_seam_lists_mcp_tools_and_calls_them_with_the_chat_actor(book):
    calls = []

    def call(name, args, actor):
        calls.append((name, args, actor))
        return {"doubled": args["n"] * 2}

    tools = [("double", "Doubles n.", SCHEMA, None)]
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
        composed(mcp_app=_endpoint([("boom", "Fails.", {"type": "object"}, None)], call)),
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
