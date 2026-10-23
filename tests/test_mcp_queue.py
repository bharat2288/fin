"""The `queue` tool (chat-queue-read 02): chat reads the Queue as its cards hold it.

One read over `GET /api/queue/cards`, run in-process as every chat tool is:
the merchant groups of rows with no type (rows, Jev's guess, the suggested
name and wording, the prefilled book, the held merchant of that name), the
rows under Check each row, and the transfers waiting. It writes nothing and
answers with "Claude may write" off. Driven through mcp_tools.call against a
temporary book; Jev's answers are stored, nothing reaches the network. Every
wording, merchant and figure here is invented.
"""

import pytest

import chat_writes
import db
import mcp_tools
from gate_tokens import dump
from test_book_and_type_switch import merchant, type_id
from test_mcp_tools import ACTOR, keys_anywhere
from test_queue_cards import book, card, key, never_asked  # noqa: F401 (fixtures)


def queue() -> dict:
    answer = mcp_tools.call("queue", {}, actor=ACTOR)
    assert answer["ok"], answer
    return answer["result"]


def group_of(result: dict, tx: int) -> dict:
    return next(g for g in result["untyped"]["groups"] if tx in [r["id"] for r in g["rows"]])


def test_queue_is_a_read_that_takes_nothing():
    tool = mcp_tools.BY_NAME["queue"]

    assert tool.write is False
    assert tool.input_schema["properties"] == {}
    assert mcp_tools.call("queue", {"limit": 5}, actor=ACTOR) == {"ok": False, "error": "queue does not take limit"}


# --- case 1: chat sees the cards the operator sees --------------------------------


def test_case_1_chat_reads_each_group_with_its_guess_name_wording_book_and_held_merchant(client, conn, book, key):
    held = merchant(conn, "RHODE skin", "Moom", "Health & Beauty")

    result = queue()

    indigo = group_of(result, book["indigo_old"])
    assert [(r["id"], r["date"], r["amount_minor"], r["account"]) for r in indigo["rows"]] == [
        (book["indigo_old"], "2026-09-02", 41250, "Sample card 0001"),
        (book["indigo_new"], "2026-09-20", 38800, "Sample card 0001"),
    ]
    assert (indigo["merchant"], indigo["route"]) == ("INDIGOAIR MUMBAI", "prefill")
    assert [(t["name"], t["probability"]) for t in indigo["types"]] == [("Travel", pytest.approx(1.0))]
    assert (indigo["suggested_name"], indigo["suggested_wording"]) == ("Indigoair", "INDIGOAIR")
    assert indigo["prefill"] == {"book": "Household", "reason": "type", "hint": ""}
    assert indigo["held"] is None

    rhode = group_of(result, book["rhode"])
    assert [(t["name"], t["probability"]) for t in rhode["types"]] == [("Health & Beauty", pytest.approx(0.99))]
    assert (rhode["suggested_name"], rhode["suggested_wording"]) == ("Rhode Skin", "RHODE SKIN")
    assert rhode["held"] == {
        "id": held, "name": "RHODE skin", "type_id": type_id(conn, "Health & Beauty"),
        "type": "Health & Beauty", "book": "Moom",
    }

    paynow = group_of(result, book["paynow"])
    assert (paynow["suggested_name"], paynow["suggested_wording"], paynow["route"]) == ("", "", "blank")

    assert result["cut"] == []
    assert not keys_anywhere(result) & mcp_tools.HIDDEN_KEYS


# --- case 6: a read under the switch ------------------------------------------------


def test_case_6_queue_answers_with_writes_off_and_writes_nothing(client, conn, book, key):
    chat_writes.write(False, "fin")
    assert chat_writes.enabled() is False
    before = dump(db.DB_PATH)

    result = queue()

    assert sorted(r["id"] for g in result["untyped"]["groups"] for r in g["rows"]) == sorted(book.values())
    assert dump(db.DB_PATH) == before
