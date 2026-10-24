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
import suggest
from gate_tokens import dump
from test_book_and_type_switch import merchant, type_id
from test_mcp_tools import ACTOR, keys_anywhere
from test_preview_merchant_change import add_row
from test_queue_cards import book, card, key, many_untyped, never_asked  # noqa: F401 (fixtures)
from test_queue_guesses import answer


def queue() -> dict:
    answer = mcp_tools.call("queue", {}, actor=ACTOR)
    assert answer["ok"], answer
    return answer["result"]


def group_of(result: dict, tx: int) -> dict:
    return next(g for g in result["untyped"]["groups"] if tx in [r["id"] for r in g["rows"]])


def test_queue_is_a_read_whose_paging_arguments_are_all_optional():
    tool = mcp_tools.BY_NAME["queue"]

    assert tool.write is False
    assert set(tool.input_schema["properties"]) == {"section", "after", "limit"}
    assert tool.input_schema["required"] == []
    assert mcp_tools.call("queue", {"page": 2}, actor=ACTOR) == {"ok": False, "error": "queue does not take page"}


def test_the_queue_tool_names_the_piles_fins_route_pages():
    import app as fin_app

    assert mcp_tools.QUEUE_SECTIONS == fin_app.QUEUE_SECTIONS
    assert mcp_tools.BY_NAME["queue"].input_schema["properties"]["section"]["enum"] == list(fin_app.QUEUE_SECTIONS)


def test_the_queue_tool_says_plans_save_cards_and_no_text_says_saving_is_only_the_operators():
    # plan_preview and plan_apply save the Queue's cards in batches (chat-plan-apply 02).
    said = mcp_tools.BY_NAME["queue"].description
    assert "plan_preview" in said and "plan_apply" in said
    assert "is the operator's, in fin's Queue" not in said
    assert "no tool here labels a group" not in mcp_tools.__doc__
    assert "plan_preview" in mcp_tools.__doc__.split("- The Queue")[1].split("- Plans")[0]


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


# --- paging (fin-redesign item 27): every card reached, a pile at a time -----------------
#
# One 100-row budget spans the bare call's cards, the biggest row's card first,
# so two big cards fill it and the small ones (Jev's among them) never show.
# A section pages one pile by card, from a cursor; queue_summary counts them all.


def call_ok(name: str, **args) -> dict:
    answer = mcp_tools.call(name, args, actor=ACTOR)
    assert answer["ok"], answer
    return answer["result"]


@pytest.fixture
def piles(client, conn, card, key) -> dict:
    """Over 100 rows with no type, most on small cards behind two big ones:
    two Sample Hotel cards of 60 rows each (no guess), 30 stalls Jev is sure
    of (two small rows each), 6 shops Jev offers three types for (one row
    each) and 20 small PayNows (no guess, one each); then 4 rows under Check
    each row from one merchant, and 3 transfers waiting."""
    rows = {"hotel": [], "sure": [], "pick": [], "paynow": [], "each": [], "transfers": []}
    for hotel in "AB":
        for n in range(60):
            rows["hotel"].append(add_row(conn, card, f"SAMPLE HOTEL {hotel} SINGAPORE", 50_000 + n,
                                         service_id=None, book=None, type_name=None, day="2026-09-03"))
    for n in range(30):
        stall = f"SAMPLE STALL {chr(65 + n % 26)}{chr(65 + n // 26)} SINGAPORE"
        for day in ("2026-09-04", "2026-09-05"):
            rows["sure"].append(add_row(conn, card, stall, 500 + n, service_id=None, book=None,
                                        type_name=None, day=day))
        answer(conn, suggest.row_merchant(conn, rows["sure"][-1]), "Travel", 1.0)
    for n in range(6):
        rows["pick"].append(add_row(conn, card, f"SAMPLE SHOP {chr(65 + n)} SINGAPORE", 700 + n,
                                    service_id=None, book=None, type_name=None, day="2026-09-06"))
        answer(conn, suggest.row_merchant(conn, rows["pick"][-1]), "Travel", 0.6)
    for n in range(20):
        rows["paynow"].append(add_row(conn, card, f"PAYNOW TO SAMPLE PAYEE {n:02}", 300 + n,
                                      service_id=None, book=None, type_name=None, day="2026-09-07"))
    svc = merchant(conn, "Sample Travelsite", "Moom", "Travel")
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (svc,))
    conn.commit()
    for n in range(4):
        rows["each"].append(add_row(conn, card, f"SAMPLE TRAVELSITE BKG {n:04}", 1000 + n, service_id=svc,
                                    book="Moom", type_name="Travel", day="2026-09-14"))
    for n in range(3):
        rows["transfers"].append(add_row(conn, card, f"TRANSFER TO 00998877{n:04}", 2000 + n, service_id=None,
                                         book=None, type_name=None, flow="review", day="2026-09-05"))
    return rows


def pages(section: str, limit: int | None = None) -> list[dict]:
    """Every page of one section, following the cursor to the end."""
    got, after = [], None
    while True:
        args = {"section": section, **({"limit": limit} if limit else {}), **({"after": after} if after else {})}
        page = call_ok("queue", **args)
        got.append(page)
        after = page["next"]
        if after is None:
            return got
        assert len(got) < 100, "the cursor never ends"


def test_the_bare_call_still_fills_its_one_budget_with_the_biggest_cards_and_shows_no_small_one(piles):
    result = queue()

    shown = [r["id"] for g in result["untyped"]["groups"] for r in g["rows"]]
    assert len(shown) == 100 and set(shown) <= set(piles["hotel"])
    assert result["untyped"]["shown"] == "showing 100 of 206"
    assert set(result) == {"untyped", "check_each", "transfers", "cut"}


def test_paging_the_rows_with_no_type_reaches_every_card_exactly_once(piles):
    got = pages("untyped")

    cards = [g for page in got for g in page["groups"]]
    ids = [r["id"] for g in cards for r in g["rows"]]
    assert sorted(ids) == sorted(piles["hotel"] + piles["sure"] + piles["pick"] + piles["paynow"])
    assert len(ids) == len(set(ids))
    assert len(cards) == 58 == got[0]["cards"]
    assert all(page["total"] == 206 for page in got)
    # A page holds at most 100 rows and ends at a card's end: no card is split.
    assert all(sum(len(g["rows"]) for g in page["groups"]) <= 100 for page in got)
    assert all(g["row_count"] == len(g["rows"]) for g in cards)
    # The order is the bare call's: the biggest row's card first.
    first = queue()["untyped"]["groups"][0]
    assert got[0]["groups"][0] == first
    assert len(json.dumps(got[0])) < 112_000


def test_a_smaller_limit_pages_by_fewer_rows_and_still_reaches_every_card_once(piles):
    got = pages("sure", limit=7)

    cards = [g for page in got for g in page["groups"]]
    assert sorted(r["id"] for g in cards for r in g["rows"]) == sorted(piles["sure"])
    assert len(cards) == 30 and all(g["route"] == "prefill" for g in cards)
    assert all(sum(len(g["rows"]) for g in page["groups"]) <= 7 for page in got)


@pytest.mark.parametrize("section, pile, route", [("sure", "sure", "prefill"), ("pick", "pick", "top3")])
def test_a_section_holds_one_pile_of_jevs_guesses(piles, section, pile, route):
    [page] = pages(section)

    assert sorted(r["id"] for g in page["groups"] for r in g["rows"]) == sorted(piles[pile])
    assert {g["route"] for g in page["groups"]} == {route}
    assert (page["section"], page["next"]) == (section, None)


def test_no_guess_holds_the_cards_jev_has_no_guess_for(piles):
    got = pages("no_guess")

    ids = [r["id"] for page in got for g in page["groups"] for r in g["rows"]]
    assert sorted(ids) == sorted(piles["hotel"] + piles["paynow"])
    assert {g["route"] for page in got for g in page["groups"]} == {"blank"}


@pytest.mark.parametrize("section, pile", [("check_each", "each"), ("transfers", "transfers")])
def test_the_row_lists_page_by_row_and_reach_every_row_once(piles, section, pile):
    got = pages(section, limit=2)

    ids = [r["id"] for page in got for r in page["rows"]]
    assert sorted(ids) == sorted(piles[pile]) and len(ids) == len(set(ids))
    assert [len(page["rows"]) for page in got] == [2] * (len(ids) // 2) + [len(ids) % 2] * (len(ids) % 2)
    assert all(page["total"] == len(piles[pile]) for page in got)
    # Each row as the bare call lists it.
    bare = queue()[section]["rows"]
    assert [r for page in got for r in page["rows"]] == bare


def test_queue_summary_counts_every_piles_cards_and_rows(piles):
    summary = call_ok("queue_summary")

    assert summary == {
        "jev": True,
        "untyped": {"cards": 58, "rows": 206},
        "sure": {"cards": 30, "rows": 60},
        "pick": {"cards": 6, "rows": 6},
        "no_guess": {"cards": 22, "rows": 140},
        "check_each": {"cards": 1, "rows": 4},
        "transfers": {"cards": 3, "rows": 3},
    }
    # The counts are the pages' own.
    assert len([g for page in pages("untyped") for g in page["groups"]]) == summary["untyped"]["cards"]
    assert mcp_tools.BY_NAME["queue_summary"].write is False


def test_queue_summary_with_jev_off_counts_every_card_as_no_guess(piles, monkeypatch):
    monkeypatch.delenv(suggest.KEY_ENV)

    summary = call_ok("queue_summary")

    assert summary["jev"] is False
    assert summary["sure"] == summary["pick"] == {"cards": 0, "rows": 0}
    assert summary["no_guess"]["rows"] == summary["untyped"]["rows"] == 206


@pytest.mark.parametrize("args, why", [
    ({"limit": 5}, "limit and after go with a section"),
    ({"after": "x"}, "limit and after go with a section"),
    ({"section": "untyped", "limit": 0}, "limit is a number of rows from 1 to 100"),
    ({"section": "untyped", "limit": 101}, "limit is a number of rows from 1 to 100"),
    ({"section": "untyped", "after": "not a cursor"}, "after is not a cursor the queue gave"),
    ({"section": "elsewhere"}, "section must be one of untyped, sure, pick, no_guess, check_each, transfers"),
])
def test_a_page_asked_for_wrongly_is_refused_and_says_why(piles, args, why):
    assert mcp_tools.call("queue", args, actor=ACTOR) == {"ok": False, "error": why}


def test_paging_writes_nothing_and_answers_with_writes_off(piles):
    chat_writes.write(False, "fin")
    before = dump(db.DB_PATH)

    pages("untyped")
    call_ok("queue_summary")

    assert dump(db.DB_PATH) == before
