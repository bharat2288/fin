"""The `queue` tool (chat-queue-read 02): chat reads the Queue as its cards hold it.

One read over `GET /api/queue/cards`, run in-process as every chat tool is:
the merchant groups of rows with no type (rows, Jev's guess, the suggested
name and wording, the prefilled book, the held merchant of that name), the
rows under Check each row, and the transfers waiting. It writes nothing and
answers with "Claude may write" off. Driven through mcp_tools.call against a
temporary book; Jev's answers are stored, nothing reaches the network. Every
wording, merchant and figure here is invented.
"""

import json

import pytest

import chat_writes
import db
import mcp_tools
from gate_tokens import dump
from test_book_and_type_switch import merchant, type_id
from test_mcp_tools import ACTOR, keys_anywhere
from test_preview_merchant_change import add_row
from test_queue_cards import book, card, key, many_untyped, never_asked  # noqa: F401 (fixtures)


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
    assert [(r["id"], r["date"], r["amount_minor"]) for r in indigo["rows"]] == [
        (book["indigo_old"], "2026-09-02", 41250),
        (book["indigo_new"], "2026-09-20", 38800),
    ]
    assert indigo["account"] == "Sample card 0001"
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
        "type": "Health & Beauty", "book": "Moom", "review_each_time": False,
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


# --- case 8: a large Queue, read in one answer -----------------------------------------


def test_case_8_a_large_queue_comes_back_small_enough_to_read_in_one_answer(client, conn, card, key):
    many_untyped(conn, card)
    svc = merchant(conn, "Sample Travelsite", "Moom", "Travel")
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (svc,))
    conn.commit()
    for n in range(300):
        add_row(conn, card, f"SAMPLE TRAVELSITE BKG {n:04}", 1000 + n, service_id=svc, book="Moom",
                type_name="Travel", day="2026-09-14")
        add_row(conn, card, f"TRANSFER TO 00998877{n:04}", 2000 + n, service_id=None, book=None,
                type_name=None, flow="review", day="2026-09-05")

    result = queue()

    assert [len(result["check_each"]["rows"]), len(result["transfers"]["rows"])] == [100, 100]
    assert sum(len(g["rows"]) for g in result["untyped"]["groups"]) == 100
    assert len(result["cut"]) == 3
    # The rows with no type about 48 KB and all three lists full about 105 KB,
    # where 500 rows a list was about 230 KB and 426 KB.
    assert len(json.dumps(result["untyped"])) < 52_000
    assert len(json.dumps(result)) < 112_000
