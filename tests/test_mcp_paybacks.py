"""Paybacks over chat (fin-redesign 12, case 25; RUL-016: no per-person shares).

A group order paid for many people, and the people paying back over two days,
is expect-back and set-off as the app keeps it. The chat gets three things
that made it slow: a read of the Queue's payback suggestions (paybacks), the
mark made in the same change as the row's label (mark_expect_back with book
or type_id), and several payments set off against one expectation in one call
(set_off_payments), one change entry and one Undo, behind expected_count when
it changes more than MANY_ROWS rows.

Driven through mcp_tools.call on a temporary database seeded with the check
book (tests/expect_fixtures.py). Every name and figure is invented.
"""

from __future__ import annotations

import pytest

import chat_writes
import mcp_tools
import setoff_api
from expect_fixtures import mark, october, row
from trend_fixtures import type_id

ACTOR = "chat:sample-subject"
# The tables a payback write touches, and the row they rest on.
TRACKED = ("transactions", "accounts", "expectation", "expectation_name", "set_off", "sender_wording",
           "dismissal", "let_go")

# Fourteen friends, each paying back S$ 50.00 of a S$ 750.00 group order.
FRIENDS = ("Asha", "Bram", "Cleo", "Dara", "Eli", "Fay", "Gus", "Hana", "Ivo", "Juno", "Kai", "Lia",
           "Milo", "Nell")


def chat(name, **args):
    return mcp_tools.call(name, args, actor=ACTOR)


def tracked(conn) -> dict:
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")] for t in TRACKED}


def entries(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute("SELECT id, via, actor, summary, undone_by FROM change_entries "
                                           "ORDER BY id")]


def person(conn, name: str) -> int:
    pid = conn.execute("INSERT INTO accounts (name, short_name, type, currency, owner) "
                       "VALUES (?, ?, 'person', 'SGD', 'Household')", (name, name)).lastrowid
    conn.commit()
    return pid


@pytest.fixture
def on_29_apr(monkeypatch):
    monkeypatch.setattr(setoff_api, "today", lambda: "2026-04-29")


@pytest.fixture
def feast(conn):
    """The group order on 26 Apr (S$ 750.00, Rewards card, no type yet), and
    each friend's PayNow on 27 or 28 Apr (S$ 50.00 in, waiting for its label)."""
    story = october(conn)
    b = story.book
    story.feast = row(conn, b, b.rewards_card, "2026-04-26", "SAMPLE FEAST GROUP ORDER", 75000)
    story.friends = {name: person(conn, name) for name in FRIENDS}
    story.paybacks = {
        name: row(conn, b, b.savings, f"2026-04-{27 + i % 2}", f"PAYNOW FROM {name.upper()} {1000 + i}", -5000,
                  flow="review")
        for i, name in enumerate(FRIENDS)
    }
    return story


@pytest.fixture
def marked(conn, feast):
    """S$ 700.00 of the order expected back, named for the fourteen."""
    feast.expectation = mark(conn, feast.feast, 70000, names=tuple(feast.friends.values()))
    return feast


def all_fourteen(story) -> list[dict]:
    return [{"tx_id": story.paybacks[name], "amount": "50.00"} for name in FRIENDS]


# --- the read ------------------------------------------------------------------


def test_case_25_the_chat_reads_the_queues_payback_suggestions(client, conn, marked):
    before = entries(conn)

    answer = chat("paybacks")

    assert answer["ok"], answer
    assert answer["result"] == client.get("/api/queue/paybacks").get_json()
    offered = {p["payment"]["id"]: [s["expectation_id"] for s in p["suggestions"]]
               for p in answer["result"]["paybacks"]}
    assert {pid: offered[pid] for pid in marked.paybacks.values()} == \
        {pid: [marked.expectation] for pid in marked.paybacks.values()}
    asha = next(p for p in answer["result"]["paybacks"] if p["payment"]["id"] == marked.paybacks["Asha"])
    assert asha["suggestions"][0]["reasons"][0] == "Asha is named on it"
    assert entries(conn) == before


def test_the_paybacks_read_answers_with_writes_off(client, conn, marked):
    assert client.put("/api/chat-writes", json={"enabled": False}).status_code == 200

    assert chat("paybacks")["ok"]


# --- several payments against one expectation, one change ---------------------


def test_case_25_the_chat_sets_off_fourteen_paybacks_in_one_call(client, conn, marked, on_29_apr):
    start = len(entries(conn))

    done = chat("set_off_payments", expectation_id=marked.expectation, payments=all_fourteen(marked),
                expected_count=28)

    assert done["ok"], done
    placed = conn.execute("SELECT payment_row_id, expectation_id, amount_minor, item_amount_minor, source "
                          "FROM set_off ORDER BY id").fetchall()
    assert [tuple(r) for r in placed] == [(marked.paybacks[n], marked.expectation, 5000, 5000, "hand")
                                          for n in FRIENDS]
    assert done["result"]["expectation"]["open_minor"] == 0
    assert done["result"]["expectation"]["state"] == "closed"
    assert [p["own_minor"] for p in done["result"]["payments"]] == [0] * 14
    # Each sender's wording is learnt, as one set_off call learns it.
    assert sorted(r[0] for r in conn.execute("SELECT wording FROM sender_wording")) == \
        sorted(n.upper() for n in FRIENDS)
    # One change entry, stamped as the chat's, asked first (28 rows over 20).
    rows = entries(conn)
    assert len(rows) == start + 1
    _, via, actor, summary, _ = rows[-1]
    assert (via, actor) == ("chat", ACTOR)
    assert summary == "Set off 14 payments against SAMPLE FEAST GROUP ORDER"
    assert done["change"] == {"entry_id": rows[-1][0], "rows": 28}
    assert conn.execute("SELECT expected_count FROM change_asked WHERE entry_id = ?",
                        (rows[-1][0],)).fetchone()[0] == 28
    # The paid-back payments leave the Queue's money-in lane.
    lane = chat("paybacks")["result"]["paybacks"]
    assert not {p["payment"]["id"] for p in lane} & set(marked.paybacks.values())


def test_case_25_one_undo_puts_the_fourteen_back_exactly(client, conn, marked, on_29_apr):
    before = tracked(conn)
    done = chat("set_off_payments", expectation_id=marked.expectation, payments=all_fourteen(marked),
                expected_count=28)
    assert done["ok"], done

    undone = chat("undo", entry_id=done["change"]["entry_id"], expected_count=28)

    assert undone["ok"], undone
    assert tracked(conn) == before
    page = chat("expected_back", expectation_id=marked.expectation)["result"]
    assert page["expectation"]["open_minor"] == 70000


def test_case_25_a_large_batch_without_its_count_is_put_back(client, conn, marked, on_29_apr):
    before, history_before = tracked(conn), entries(conn)

    answer = chat("set_off_payments", expectation_id=marked.expectation, payments=all_fourteen(marked))

    assert answer["ok"] is False
    assert answer["stopped"] is True
    assert answer["would_change_rows"] == 28
    assert "expected_count=28" in answer["error"]
    assert tracked(conn) == before
    assert entries(conn) == history_before


def test_a_few_paybacks_need_no_count(client, conn, marked, on_29_apr):
    three = all_fourteen(marked)[:3]

    done = chat("set_off_payments", expectation_id=marked.expectation, payments=three)

    assert done["ok"], done
    assert done["change"]["rows"] == 6
    assert done["result"]["expectation"]["open_minor"] == 70000 - 3 * 5000


def test_a_part_of_a_payment_leaves_the_rest_its_own(client, conn, marked, on_29_apr):
    done = chat("set_off_payments", expectation_id=marked.expectation,
                payments=[{"tx_id": marked.paybacks["Asha"], "amount": "30.00"}])

    assert done["ok"], done
    [payment] = done["result"]["payments"]
    assert (payment["id"], payment["placed_minor"], payment["own_minor"]) == (marked.paybacks["Asha"], -3000, -2000)


# --- a refusal writes nothing --------------------------------------------------


def refused(conn, answer, words, *, before, history_before):
    assert answer["ok"] is False, answer
    assert "no tool named" not in answer["error"], "the tool does not exist"
    assert "change" not in answer and "stopped" not in answer
    assert answer["error"] == words
    assert tracked(conn) == before
    assert entries(conn) == history_before


def test_one_payment_that_cannot_pay_refuses_the_whole_batch(client, conn, marked, on_29_apr):
    b = marked.book
    own_move = row(conn, b, b.savings, "2026-04-28", "FROM EVERYDAY CARD 7001", -5000, flow="transfer")
    before, history_before = tracked(conn), entries(conn)

    answer = chat("set_off_payments", expectation_id=marked.expectation,
                  payments=all_fourteen(marked)[:2] + [{"tx_id": own_move, "amount": "50.00"}])

    refused(conn, answer, "FROM EVERYDAY CARD 7001: a move between your own accounts",
            before=before, history_before=history_before)


def test_more_than_is_open_is_refused_whole(client, conn, feast, on_29_apr):
    small = mark(conn, feast.feast, 10000, names=tuple(feast.friends.values()))
    before, history_before = tracked(conn), entries(conn)

    answer = chat("set_off_payments", expectation_id=small, payments=all_fourteen(feast)[:3])

    refused(conn, answer, "PAYNOW FROM CLEO 1002: nothing open on SAMPLE FEAST GROUP ORDER",
            before=before, history_before=history_before)


def test_a_payment_listed_twice_is_refused(client, conn, marked, on_29_apr):
    asha = {"tx_id": marked.paybacks["Asha"], "amount": "20.00"}
    before, history_before = tracked(conn), entries(conn)

    answer = chat("set_off_payments", expectation_id=marked.expectation, payments=[asha, asha])

    refused(conn, answer, "PAYNOW FROM ASHA 1000 is listed twice", before=before, history_before=history_before)


def test_no_payments_or_no_such_expectation_is_refused(client, conn, marked, on_29_apr):
    before, history_before = tracked(conn), entries(conn)

    empty = chat("set_off_payments", expectation_id=marked.expectation, payments=[])
    nowhere = chat("set_off_payments", expectation_id=9999, payments=all_fourteen(marked)[:1])
    unnamed = chat("set_off_payments", expectation_id=marked.expectation, payments=[{"amount": "5.00"}])

    refused(conn, empty, setoff_api.NO_PAYMENTS, before=before, history_before=history_before)
    refused(conn, nowhere, "no such expectation", before=before, history_before=history_before)
    refused(conn, unnamed, setoff_api.BAD_PAYMENT, before=before, history_before=history_before)


def test_with_writes_off_the_batch_is_refused(client, conn, marked, on_29_apr):
    assert client.put("/api/chat-writes", json={"enabled": False}).status_code == 200
    before = tracked(conn)

    answer = chat("set_off_payments", expectation_id=marked.expectation, payments=all_fourteen(marked)[:2])

    assert answer == {"ok": False, "error": chat_writes.WRITES_OFF}
    assert tracked(conn) == before


# --- the mark in the same change as the label ------------------------------------


def test_case_25_the_chat_labels_the_order_and_marks_it_in_one_change(client, conn, feast):
    dining = type_id(conn, "Dining")
    before = tracked(conn)
    start = len(entries(conn))

    done = chat("mark_expect_back", tx_id=feast.feast, direction="back", amount="700.00",
                names=list(feast.friends.values()), type_id=dining, book="Household")

    assert done["ok"], done
    held = conn.execute("SELECT book, type_id, book_source, type_source FROM transactions WHERE id = ?",
                        (feast.feast,)).fetchone()
    assert tuple(held) == ("Household", dining, "chosen", "chosen")
    x = conn.execute("SELECT * FROM expectation WHERE row_id = ?", (feast.feast,)).fetchone()
    assert (x["direction"], x["amount_minor"]) == ("back", 70000)
    assert conn.execute("SELECT COUNT(*) FROM expectation_name WHERE expectation_id = ?",
                        (x["id"],)).fetchone()[0] == 14
    assert done["result"]["row"]["own_minor"] == 5000
    rows = entries(conn)
    assert len(rows) == start + 1
    assert rows[-1][1:4] == ("chat", ACTOR, "Expect back S$ 700 on SAMPLE FEAST GROUP ORDER and labelled it")
    assert done["change"]["rows"] == 16

    undone = chat("undo", entry_id=done["change"]["entry_id"])

    assert undone["ok"], undone
    assert tracked(conn) == before


def test_a_mark_the_label_makes_impossible_writes_neither(client, conn, feast):
    before, history_before = tracked(conn), entries(conn)

    answer = chat("mark_expect_back", tx_id=feast.feast, direction="back", all=True, book="Moom")

    refused(conn, answer, "only Household spending can be expected back: this row is the company's cost",
            before=before, history_before=history_before)


def test_a_bad_label_writes_no_mark(client, conn, feast):
    before, history_before = tracked(conn), entries(conn)

    answer = chat("mark_expect_back", tx_id=feast.feast, direction="back", all=True, type_id=99999)

    assert answer["ok"] is False
    assert answer["error"] == "unknown type: 99999"
    assert tracked(conn) == before
    assert entries(conn) == history_before


# --- the surface: existing tools keep their names and parameters ---------------------


SET_OFF_BEFORE = {"tx_id", "placements", "waiting", "expected_count"}
MARK_BEFORE = {"tx_id", "direction", "all", "amount", "names", "new_names", "note", "expected_count"}


def test_existing_tools_keep_every_parameter_and_requirement():
    set_off = mcp_tools.BY_NAME["set_off"].input_schema
    mark_tool = mcp_tools.BY_NAME["mark_expect_back"].input_schema

    assert set(set_off["properties"]) == SET_OFF_BEFORE
    assert set(mark_tool["properties"]) == MARK_BEFORE | {"book", "type_id"}
    assert mark_tool["required"] == ["tx_id", "direction"]


def test_a_mark_without_a_label_goes_where_it_always_went():
    tool = mcp_tools.BY_NAME["mark_expect_back"]

    assert tool.request({"tx_id": 7, "direction": "back", "all": True}) == \
        ("POST", "/api/transactions/7/expectation", {"direction": "back", "all": True})


def test_the_new_tools_are_titled_and_stopped_by_the_switch():
    assert mcp_tools.BY_NAME["paybacks"].write is False
    assert mcp_tools.BY_NAME["set_off_payments"].write is True
    assert "set_off_payments" in mcp_tools._STOPPED_WHEN_OFF
    assert "paybacks" not in mcp_tools._STOPPED_WHEN_OFF
    assert mcp_tools.BY_NAME["set_off_payments"].input_schema["required"] == ["expectation_id", "payments"]


def test_a_payment_with_no_saved_rate_is_named_in_the_refusal(client, conn, monkeypatch):
    # A rupee expectation and a dollar payment with no saved rate: the
    # refusal says which payment of the batch it stopped on.
    monkeypatch.setattr(setoff_api, "today", lambda: "2026-10-20")
    o = october(conn)
    item = mark(conn, o.swiggy, 240000)
    before, history_before = tracked(conn), entries(conn)

    answer = chat("set_off_payments", expectation_id=item, payments=[{"tx_id": o.jay_0412, "amount": "10.00"}])

    assert answer["ok"] is False
    assert str(answer["error"]).startswith(conn.execute(
        "SELECT description FROM transactions WHERE id = ?", (o.jay_0412,)).fetchone()[0] + ": ")
    assert tracked(conn) == before and entries(conn) == history_before


def test_a_body_that_is_not_an_object_is_refused_as_before(client, conn):
    o = october(conn)

    resp = client.put(f"/api/transactions/{o.harbour}", json=[1])

    assert resp.status_code == 400
