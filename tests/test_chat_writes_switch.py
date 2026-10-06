"""The "Claude may write" switch (fin-surfaces 01, Off switches).

With it off, every chat write tool refuses with a fixed message before
anything runs, and reads still work. The chat can turn it off
(turn_off_writes), never on: only the operator, in fin's app. The switch is a
small JSON file beside the book; no file is on, a file that is not a switch is
off. Every name and figure is invented; every book is a temporary file.
"""

from __future__ import annotations

import json

import pytest

import access_gate
import app as fin_app
import chat_writes
import db
import mcp_tools
from gate_tokens import dump
from test_mcp_tools import ACTOR, book, call, first_row  # noqa: F401 (fixture)


def switch_file():
    return chat_writes.state_path()


def as_chat():
    """The environ an in-process chat call carries (mcp_tools.call): via chat."""
    return {access_gate.CHAT_CALL_ENVIRON_KEY: access_gate.chat_call_identity(ACTOR)}


def turn(client, on, **kwargs):
    return client.put("/api/chat-writes", json={"enabled": on}, **kwargs)


def sample_args(tool) -> dict:
    """Arguments of the right types, so a refusal is the switch's, not the schema's."""
    args = {}
    for name, spec in tool.input_schema["properties"].items():
        kind = spec.get("type")
        if name == "expected_count":
            continue
        if spec.get("enum"):
            args[name] = spec["enum"][0]
        elif kind == "integer":
            args[name] = 1
        elif kind == "string":
            args[name] = "2026-08-03" if name == "date" else "SAMPLE"
        elif kind == "boolean":
            args[name] = True
    return args


WRITES = [t for t in mcp_tools.TOOLS if t.write and t.name != "turn_off_writes"]


# --- the default and the file -------------------------------------------------------


def test_no_file_means_claude_may_write(client):
    assert not switch_file().exists()

    assert client.get("/api/chat-writes").get_json() == {
        "enabled": True, "changed_at": None, "changed_by": None,
    }


@pytest.mark.parametrize("content", [
    "not json", "", "[]", "null", '{"enabled": "yes"}', '{"enabled": 1}', '{"changed_by": "fin"}',
])
def test_a_file_that_is_not_a_switch_reads_off(client, content):
    switch_file().write_text(content, encoding="utf-8")

    shown = client.get("/api/chat-writes").get_json()

    assert shown["enabled"] is False and shown["unreadable"] is True
    assert chat_writes.enabled() is False


def test_a_switch_file_that_cannot_be_read_reads_off(client):
    switch_file().mkdir()  # there, but not a file

    assert chat_writes.enabled() is False


def test_the_switch_is_written_atomically_beside_the_book(client):
    assert turn(client, False).status_code == 200

    assert switch_file().parent == db.DB_PATH.parent
    saved = json.loads(switch_file().read_text(encoding="utf-8"))
    assert saved["enabled"] is False and saved["changed_by"] == "fin" and saved["changed_at"]
    assert [p.name for p in switch_file().parent.iterdir() if p.name.startswith(".fin-chat-writes-")] == []


# --- off: writes refuse, reads work ------------------------------------------------------


def test_with_writes_off_every_write_tool_refuses_and_writes_nothing(book):
    row = first_row()
    assert turn(book, False).status_code == 200
    before = dump(db.DB_PATH)

    for tool in WRITES:
        args = {**sample_args(tool), **({"tx_id": row["id"]} if "tx_id" in tool.input_schema["properties"] else {})}
        assert call(tool.name, **args) == {"ok": False, "error": chat_writes.WRITES_OFF}, tool.name

    assert dump(db.DB_PATH) == before
    assert call("transactions", per_page=50)["ok"]
    assert call("balance_sheet", month="2026-08")["ok"]
    assert call("history")["ok"]


def test_with_writes_off_from_an_unreadable_file_writes_refuse(book):
    row = first_row()
    switch_file().write_text("{", encoding="utf-8")

    assert call("set_label", tx_id=row["id"], book="Moom") == {"ok": False, "error": chat_writes.WRITES_OFF}
    assert first_row()["book"] == row["book"]


def test_turned_back_on_in_the_app_writes_land_again(book):
    row = first_row()
    turn(book, False)
    assert not call("set_label", tx_id=row["id"], book="Moom")["ok"]

    assert turn(book, True).get_json()["enabled"] is True

    assert call("set_label", tx_id=row["id"], book="Moom")["ok"]
    assert first_row()["book"] == "Moom"


# --- the chat turns it off, never on ----------------------------------------------------


def test_the_chat_turns_writes_off_and_again_when_already_off(book):
    before = call("history")["result"]["entries"]

    first = call("turn_off_writes")
    again = call("turn_off_writes")

    assert first["ok"] and again["ok"], (first, again)
    assert first["result"]["enabled"] is False and again["result"]["enabled"] is False
    assert chat_writes.read()["changed_by"] == ACTOR
    assert not call("set_note", tx_id=first_row()["id"], notes="x")["ok"]
    # The switch is not the book: no change entry.
    assert call("history")["result"]["entries"] == before


def test_no_chat_tool_turns_writes_on():
    for tool in mcp_tools.TOOLS:
        method, path, payload = tool.request(sample_args(tool))
        if path == "/api/chat-writes":
            assert (tool.name, method, payload) == ("turn_off_writes", "PUT", {"enabled": False})


def test_the_route_refuses_a_chat_call_turning_writes_on(client):
    turn(client, False)

    refused = turn(client, True, environ_base=as_chat())
    turned_off = turn(client, False, environ_base=as_chat())

    assert refused.status_code == 403
    assert refused.get_json() == {"error": fin_app.CHAT_WRITES_ON_ONLY_IN_APP}
    assert turned_off.status_code == 200
    assert chat_writes.enabled() is False


def test_a_chat_call_cannot_turn_writes_on_even_when_they_are_on(client):
    assert turn(client, True, environ_base=as_chat()).status_code == 403
    assert not switch_file().exists()


@pytest.mark.parametrize("body", [
    {"enabled": "true"}, {"enabled": 1}, {"enabled": None}, {}, {"on": True}, [True], "true",
])
def test_the_switch_takes_a_json_boolean_only(client, body):
    resp = client.put("/api/chat-writes", json=body)

    assert resp.status_code == 400
    assert resp.get_json() == {"error": fin_app.CHAT_WRITES_NOT_BOOLEAN}
    assert not switch_file().exists()


def test_the_switch_refuses_a_body_that_is_not_json(client):
    resp = client.put("/api/chat-writes", data="enabled=true", content_type="text/plain")

    assert resp.status_code == 400
    assert not switch_file().exists()
