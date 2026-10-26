"""The chat tools for expect back (personal-ledger 44; spec 23 E8, Seam 4).

Seven write tools (mark_expect_back, undo_mark, set_off, unlink_set_off,
let_go, undo_let_go, not_a_payback) and one read (expected_back) over the
expect-back routes, driven through mcp_tools.call on a temporary database
seeded from the case list's check book (tests/expect_fixtures.py). A tool is
a thin wrapper: Claude does exactly what the app does, so each figure here is
the one the app route gives, each write is one change stamped as the chat
client's and undoable, and each refusal is fin's own words with nothing
written. Every name and figure is invented.
"""

import pytest

import chat_writes
import letgo_api
import mcp_tools
import setoff_api
from expect_fixtures import harbour_set_against, last_entry, mark, october, row, set_off
from gate_tokens import dump

ACTOR = "chat:sample-subject"
SEVEN = ("expectation", "expectation_name", "set_off", "let_go", "refund_link", "sender_wording", "dismissal")

NEW_WRITES = ["mark_expect_back", "undo_mark", "set_off", "unlink_set_off", "let_go", "undo_let_go",
              "not_a_payback"]


def chat(name, **args):
    return mcp_tools.call(name, args, actor=ACTOR)


def stamp(conn, answer):
    """(via, actor, summary, rows) of the change a chat answer names."""
    entry_id = answer["change"]["entry_id"]
    via, actor, summary = conn.execute("SELECT via, actor, summary FROM change_entries WHERE id = ?",
                                       (entry_id,)).fetchone()
    return via, actor, summary, answer["change"]["rows"]


def snapshot(conn) -> dict:
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")]
            for t in SEVEN + ("transactions", "change_entries", "change_rows")}


def refused(conn, answer, words=None, *, before):
    """A refusal: ok false, fin's words, no change named, and nothing written."""
    assert answer["ok"] is False, answer
    assert "no tool named" not in answer["error"], "the tool does not exist"
    assert "change" not in answer
    if words is not None:
        assert answer["error"] == words
    assert snapshot(conn) == before


def expectation_of(conn, row_id):
    return conn.execute("SELECT * FROM expectation WHERE row_id = ?", (row_id,)).fetchone()


def entries(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0]


def open_of(item):
    return item["open_minor"]


@pytest.fixture
def story(conn):
    return october(conn)


@pytest.fixture
def on_19_oct(monkeypatch):
    monkeypatch.setattr(setoff_api, "today", lambda: "2026-10-19")
    monkeypatch.setattr(letgo_api, "today", lambda: "2026-10-19")


# --- the surface ---------------------------------------------------------------


def test_the_seven_writes_and_the_read_are_on_the_list():
    by_name = {t.name: t for t in mcp_tools.TOOLS}

    for name in NEW_WRITES:
        assert by_name[name].write is True, name
        assert name in mcp_tools._STOPPED_WHEN_OFF, name
        assert "expected_count" in by_name[name].input_schema["properties"], name
    assert by_name["expected_back"].write is False
    assert "expected_back" not in mcp_tools._STOPPED_WHEN_OFF


def test_a_let_go_must_name_mine_or_a_gift_in_its_schema():
    schema = mcp_tools.BY_NAME["let_go"].input_schema

    assert "became" in schema["required"]
    assert schema["properties"]["became"]["enum"] == ["mine", "gift"]
    assert "became" in mcp_tools.BY_NAME["let_go"].input_schema["properties"]
    assert "enum" in mcp_tools.BY_NAME["mark_expect_back"].input_schema["properties"]["direction"]
    assert mcp_tools.BY_NAME["mark_expect_back"].input_schema["properties"]["direction"]["enum"] == ["back", "pay"]


@pytest.mark.parametrize("name", ["spending_cards", "spending_by_type", "spending_by_month"])
def test_the_spending_reads_say_net_of_refunds_and_of_what_is_expected_back(name):
    assert "net of refunds and of what is expected back" in mcp_tools.BY_NAME[name].description


# --- 46. Claude marks ----------------------------------------------------------


def test_case_46_chat_marks_part_of_a_dinner_as_the_app_does(client, conn, story):
    jay, kim = story.people["Jay"], story.people["Kim"]
    start = entries(conn)

    done = chat("mark_expect_back", tx_id=story.harbour, direction="back", amount="200.00", names=[jay, kim])

    assert done["ok"], done
    x = expectation_of(conn, story.harbour)
    assert (x["direction"], x["amount_minor"], x["currency"], x["source"]) == ("back", 20000, "SGD", "hand")
    assert sorted(r[0] for r in conn.execute(
        "SELECT person_id FROM expectation_name WHERE expectation_id = ?", (x["id"],))) == sorted([jay, kim])
    via, actor, summary, rows = stamp(conn, done)
    assert (via, actor, summary) == ("chat", ACTOR, "Expect back S$ 200 on HARBOUR GRILL")
    assert rows >= 2
    assert entries(conn) == start + 1



def test_chat_marks_a_waiting_row_as_spending_and_expected_back_in_one_change(client, conn, story):
    """A transfer-shaped row waiting for a label (chat's brief, E): flow
    expense, a type and the mark go in together, one change, undone whole."""
    waiting = row(conn, story.book, story.book.savings, "2026-10-08", "PAYNOW TO SAMPLE FRIEND REF 1201", 45000,
                  flow="review")
    before = snapshot(conn)
    kind = conn.execute("SELECT type_id FROM transactions WHERE id = ?", (story.harbour,)).fetchone()[0]

    done = chat("mark_expect_back", tx_id=waiting, direction="back", amount="300.00",
                names=[story.people["Jay"]], flow_type="expense", type_id=kind)

    assert done["ok"], done
    assert conn.execute("SELECT flow_type, type_id FROM transactions WHERE id = ?", (waiting,)).fetchone()[:] == (
        "expense", kind)
    assert expectation_of(conn, waiting)["amount_minor"] == 30000
    assert chat("undo", entry_id=done["change"]["entry_id"])["ok"]
    assert snapshot(conn)["expectation"] == before["expectation"]
    assert conn.execute("SELECT flow_type FROM transactions WHERE id = ?", (waiting,)).fetchone()[0] == "review"


def test_a_waiting_row_without_a_flow_is_still_refused_in_fins_words(client, conn, story):
    waiting = row(conn, story.book, story.book.savings, "2026-10-08", "PAYNOW TO SAMPLE FRIEND REF 1201", 45000,
                  flow="review")
    before = snapshot(conn)

    answer = chat("mark_expect_back", tx_id=waiting, direction="back", all=True)

    refused(conn, answer, before=before)
    assert "review" in answer["error"]

def test_case_46_the_chat_mark_gives_the_apps_own_figures(client, conn, story):
    chat("mark_expect_back", tx_id=story.harbour, direction="back", amount="200.00",
         names=[story.people["Jay"]])

    harbour = next(r for r in chat("transactions", per_page=100)["result"]["transactions"]
                   if r["id"] == story.harbour)

    assert (harbour["amount_minor"], harbour["expected_minor"], harbour["placed_minor"],
            harbour["own_minor"]) == (30000, 20000, 0, 10000)


def test_case_46_a_chat_mark_is_undone_by_the_chat(client, conn, story):
    before = snapshot(conn)
    done = chat("mark_expect_back", tx_id=story.harbour, direction="back", all=True)
    assert done["ok"], done
    assert expectation_of(conn, story.harbour)["amount_minor"] == 30000

    undone = chat("undo", entry_id=done["change"]["entry_id"])

    assert undone["ok"], undone
    assert expectation_of(conn, story.harbour) is None
    assert [tuple(r) for r in conn.execute("SELECT * FROM expectation")] == before["expectation"]


def test_case_46_a_new_person_is_named_as_the_app_names_one(client, conn, story):
    done = chat("mark_expect_back", tx_id=story.klook, direction="back", all=True, new_names=["Mei"],
                note="concert tickets")

    assert done["ok"], done
    x = expectation_of(conn, story.klook)
    assert (x["amount_minor"], x["note"]) == (18000, "concert tickets")
    [(name,)] = conn.execute("SELECT a.name FROM expectation_name n JOIN accounts a ON a.id = n.person_id "
                             "WHERE n.expectation_id = ?", (x["id"],)).fetchall()
    assert name == "Mei"


def test_case_3_a_mark_over_the_row_is_refused_in_fins_words(client, conn, story):
    before = snapshot(conn)

    answer = chat("mark_expect_back", tx_id=story.harbour, direction="back", amount="350.00")

    refused(conn, answer, "more than the row's S$ 300.00", before=before)


def test_case_5_the_wrong_direction_is_refused_and_writes_nothing(client, conn, story):
    before = snapshot(conn)

    answer = chat("mark_expect_back", tx_id=story.jay_0412, direction="back", all=True)

    refused(conn, answer, before=before)


def test_case_46a_the_mirror_expect_to_pay_back(client, conn, story):
    parents = story.people["Parents"]
    march = row(conn, story.book, story.book.savings, "2026-03-01", "FROM PARENTS", -500000, flow="review")

    done = chat("mark_expect_back", tx_id=march, direction="pay", all=True, names=[parents])

    assert done["ok"], done
    x = expectation_of(conn, march)
    assert (x["direction"], x["amount_minor"]) == ("pay", 500000)
    assert stamp(conn, done)[:3] == ("chat", ACTOR, "Expect to pay back S$ 5,000 on FROM PARENTS")
    owed = chat("expected_back", direction="pay")["result"]["items"]
    assert [(i["id"], i["amount_minor"], open_of(i)) for i in owed] == [(x["id"], 500000, 500000)]


# --- undo the mark (21, 22) ----------------------------------------------------------


def test_case_22_undo_the_mark_before_any_set_off(client, conn, story):
    marked = chat("mark_expect_back", tx_id=story.klook, direction="back", all=True, names=[story.people["Kim"]])
    x = expectation_of(conn, story.klook)

    done = chat("undo_mark", expectation_id=x["id"])

    assert done["ok"], done
    assert expectation_of(conn, story.klook) is None
    assert stamp(conn, done)[:2] == ("chat", ACTOR)
    # One change; it is undoable too, and the mark returns.
    assert chat("undo", entry_id=done["change"]["entry_id"])["ok"]
    assert expectation_of(conn, story.klook)["amount_minor"] == 18000
    assert marked["ok"]


def test_case_21_undo_the_mark_is_refused_while_money_is_set_against_it(client, conn, story):
    ids = harbour_set_against(conn, story)
    before = snapshot(conn)

    answer = chat("undo_mark", expectation_id=ids["harbour"])

    refused(conn, answer, "Can't undo the mark yet: Jay's S$ 120 and Kim's S$ 60 are set against it. "
                          "Unlink those first.", before=before)


# --- 10, 11, 13, 14, 17: set off --------------------------------------------------------


def test_case_10_chat_sets_one_payment_off_against_one_item(client, conn, story, on_19_oct):
    harbour = mark(conn, story.harbour, 20000, names=(story.people["Jay"], story.people["Kim"]))
    start = entries(conn)

    done = chat("set_off", tx_id=story.jay_0412, placements=[{"expectation_id": harbour, "amount": "120.00"}])

    assert done["ok"], done
    [so] = conn.execute("SELECT * FROM set_off").fetchall()
    assert (so["payment_row_id"], so["expectation_id"], so["amount_minor"], so["item_amount_minor"],
            so["source"]) == (story.jay_0412, harbour, 12000, 12000, "hand")
    assert stamp(conn, done)[:3] == ("chat", ACTOR, "Set off a payment")
    assert done["result"]["learnt"]["wording"] == "JAY TAN"
    page = chat("expected_back", expectation_id=harbour)["result"]
    assert page["expectation"]["open_minor"] == 8000
    assert entries(conn) == start + 1


def test_case_10_the_set_off_is_undone_by_the_chat(client, conn, story, on_19_oct):
    harbour = mark(conn, story.harbour, 20000, names=(story.people["Jay"],))
    done = chat("set_off", tx_id=story.jay_0412, placements=[{"expectation_id": harbour, "amount": "120.00"}])
    assert done["ok"], done

    assert chat("undo", entry_id=done["change"]["entry_id"])["ok"]

    assert conn.execute("SELECT COUNT(*) FROM set_off").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sender_wording").fetchone()[0] == 0


def test_case_11_two_items_in_one_request_are_one_change(client, conn, story, on_19_oct):
    kim = story.people["Kim"]
    harbour = mark(conn, story.harbour, 8000, names=(kim,))
    klook = mark(conn, story.klook, 18000, names=(kim,))
    start = entries(conn)

    done = chat("set_off", tx_id=story.kim_7781, placements=[
        {"expectation_id": harbour, "amount": "60.00"}, {"expectation_id": klook, "amount": "10.00"}])

    assert done["ok"], done
    assert sorted((r["expectation_id"], r["amount_minor"]) for r in conn.execute("SELECT * FROM set_off")) == \
        sorted([(harbour, 6000), (klook, 1000)])
    assert entries(conn) == start + 1
    assert stamp(conn, done)[:2] == ("chat", ACTOR)


def test_case_14_placing_more_than_the_payment_is_refused_whole(client, conn, story, on_19_oct):
    harbour = mark(conn, story.harbour, 20000, names=(story.people["Jay"],))
    golden = mark(conn, story.golden, 6400, names=(story.people["Jay"],))
    before = snapshot(conn)

    answer = chat("set_off", tx_id=story.jay_0412, placements=[
        {"expectation_id": harbour, "amount": "120.00"}, {"expectation_id": golden, "amount": "64.00"}])

    refused(conn, answer, "S$ 184 of S$ 120 placed · S$ 64 more than the payment", before=before)


def test_a_set_off_with_nothing_ticked_is_refused(client, conn, story, on_19_oct):
    before = snapshot(conn)

    answer = chat("set_off", tx_id=story.jay_0412)

    refused(conn, answer, setoff_api.NO_PLACEMENTS, before=before)


def test_case_17_chat_leaves_a_payment_waiting_for_its_row(client, conn, story, on_19_oct):
    lena = story.people["Lena"]

    done = chat("set_off", tx_id=story.lena_2210, placements=[], waiting={"person_id": lena, "note": "for the BBQ"})

    assert done["ok"], done
    [so] = conn.execute("SELECT * FROM set_off").fetchall()
    assert (so["payment_row_id"], so["expectation_id"], so["amount_minor"], so["person_id"], so["note"]) == \
        (story.lena_2210, None, 6000, lena, "for the BBQ")
    assert stamp(conn, done)[:2] == ("chat", ACTOR)


def test_case_13_a_placement_and_a_waiting_part_are_one_change(client, conn, story, on_19_oct):
    lena = story.people["Lena"]
    airbnb = mark(conn, story.airbnb, 4000, names=(lena,))
    start = entries(conn)

    done = chat("set_off", tx_id=story.lena_2210, placements=[{"expectation_id": airbnb, "amount": "40.00"}],
                waiting={"person_id": lena, "amount": "20.00"})

    assert done["ok"], done
    assert sorted(((r["expectation_id"], r["amount_minor"]) for r in conn.execute("SELECT * FROM set_off")),
                  key=repr) == sorted([(airbnb, 4000), (None, 2000)], key=repr)
    assert entries(conn) == start + 1


# --- 23: unlink ------------------------------------------------------------------------


def test_case_23_chat_unlinks_a_set_off_and_the_unlink_is_undone(client, conn, story):
    ids = harbour_set_against(conn, story)

    done = chat("unlink_set_off", set_off_id=ids["kim_on_harbour"])

    assert done["ok"], done
    assert stamp(conn, done)[:3] == ("chat", ACTOR, "Unlinked a payment")
    assert conn.execute("SELECT 1 FROM set_off WHERE id = ?", (ids["kim_on_harbour"],)).fetchone() is None
    assert chat("expected_back", expectation_id=ids["harbour"])["result"]["expectation"]["open_minor"] == 8000

    assert chat("undo", entry_id=done["change"]["entry_id"])["ok"]
    assert conn.execute("SELECT 1 FROM set_off WHERE id = ?", (ids["kim_on_harbour"],)).fetchone() is not None


def test_case_17d_chat_un_waits_a_waiting_set_off(client, conn, story, on_19_oct):
    lena = story.people["Lena"]
    waiting = set_off(conn, story.lena_2210, None, 6000, person_id=lena, note="for the BBQ")

    done = chat("unlink_set_off", set_off_id=waiting)

    assert done["ok"], done
    assert conn.execute("SELECT COUNT(*) FROM set_off").fetchone()[0] == 0


def test_an_unlink_of_nothing_is_refused(client, conn, story):
    before = snapshot(conn)

    refused(conn, chat("unlink_set_off", set_off_id=999), "no such set-off", before=before)


# --- 24, 25, 26: let go ----------------------------------------------------------------


def test_case_24_chat_lets_the_open_rest_go_as_a_gift_and_undoes_it(client, conn, story, on_19_oct):
    ids = harbour_set_against(conn, story)
    kim = story.people["Kim"]

    done = chat("let_go", expectation_id=ids["harbour"], became="gift", to_id=kim)

    assert done["ok"], done
    [lg] = conn.execute("SELECT * FROM let_go").fetchall()
    assert (lg["expectation_id"], lg["amount_minor"], lg["became"], lg["to_whom_id"], lg["counts_on"]) == \
        (ids["harbour"], 2000, "gift", kim, "2026-10-19")
    assert stamp(conn, done)[:2] == ("chat", ACTOR)

    undone = chat("undo_let_go", expectation_id=ids["harbour"])

    assert undone["ok"], undone
    assert conn.execute("SELECT COUNT(*) FROM let_go").fetchone()[0] == 0
    assert stamp(conn, undone)[:3] == ("chat", ACTOR, "Undid a let-go")
    assert chat("expected_back", expectation_id=ids["harbour"])["result"]["expectation"]["open_minor"] == 2000


def test_case_24_chat_lets_it_go_as_mine_with_the_rows_own_type(client, conn, story, on_19_oct):
    ids = harbour_set_against(conn, story)

    done = chat("let_go", expectation_id=ids["harbour"], became="mine")

    assert done["ok"], done
    [lg] = conn.execute("SELECT became, amount_minor FROM let_go").fetchall()
    assert tuple(lg) == ("mine", 2000)


def test_case_46_a_let_go_must_name_mine_or_a_gift_no_default(client, conn, story, on_19_oct):
    ids = harbour_set_against(conn, story)
    before = snapshot(conn)

    unnamed = chat("let_go", expectation_id=ids["harbour"])
    unknown = chat("let_go", expectation_id=ids["harbour"], became="forgiven")

    refused(conn, unnamed, "let_go needs became", before=before)
    refused(conn, unknown, "became must be one of mine, gift", before=before)


def test_case_26a_a_let_go_of_a_closed_item_is_refused(client, conn, story, on_19_oct):
    klook = mark(conn, story.klook, 18000, names=(story.people["Kim"],))
    set_off(conn, story.kim_7781, klook, 7000)
    set_off(conn, story.jay_0412, klook, 11000)
    before = snapshot(conn)

    answer = chat("let_go", expectation_id=klook, became="gift")

    refused(conn, answer, "nothing open on KLOOK TRAVEL", before=before)


def test_case_26d_a_pay_item_is_let_go_only_as_a_gift_to_me(client, conn, story, on_19_oct):
    march = row(conn, story.book, story.book.savings, "2026-03-01", "FROM PARENTS", -500000, flow="review")
    parents = mark(conn, march, 500000, names=(story.people["Parents"],))
    before = snapshot(conn)

    answer = chat("let_go", expectation_id=parents, became="mine")

    refused(conn, answer, before=before)


# --- 31: not a payback --------------------------------------------------------------------


def test_case_31_chat_sets_a_payment_aside_as_not_a_payback(client, conn, story):
    done = chat("not_a_payback", tx_id=story.jay_0412)

    assert done["ok"], done
    [d] = conn.execute("SELECT row_id, kind FROM dismissal").fetchall()
    assert tuple(d) == (story.jay_0412, "payback")
    assert stamp(conn, done)[:3] == ("chat", ACTOR, "Not a payback")

    assert chat("undo", entry_id=done["change"]["entry_id"])["ok"]
    assert conn.execute("SELECT COUNT(*) FROM dismissal").fetchone()[0] == 0


def test_not_a_payback_on_money_out_is_refused(client, conn, story):
    before = snapshot(conn)

    answer = chat("not_a_payback", tx_id=story.harbour)

    refused(conn, answer, before=before)


# --- the history: every chat write is one change, undoable --------------------------------


def test_every_chat_write_is_one_change_stamped_chat(client, conn, story, on_19_oct):
    jay = story.people["Jay"]
    steps = [
        chat("mark_expect_back", tx_id=story.harbour, direction="back", amount="200.00", names=[jay]),
    ]
    harbour = expectation_of(conn, story.harbour)["id"]
    steps.append(chat("set_off", tx_id=story.jay_0412,
                      placements=[{"expectation_id": harbour, "amount": "120.00"}]))
    set_off_id = conn.execute("SELECT id FROM set_off").fetchone()[0]
    steps.append(chat("unlink_set_off", set_off_id=set_off_id))
    steps.append(chat("let_go", expectation_id=harbour, became="gift", to_id=jay))
    steps.append(chat("undo_let_go", expectation_id=harbour))
    steps.append(chat("not_a_payback", tx_id=story.kim_7781))
    steps.append(chat("undo_mark", expectation_id=harbour))

    assert all(s["ok"] for s in steps), steps
    entries = conn.execute("SELECT id, via, actor FROM change_entries ORDER BY id").fetchall()
    assert len(entries) == len(steps)
    assert [(e[1], e[2]) for e in entries] == [("chat", ACTOR)] * len(steps)
    assert [e[0] for e in entries] == [s["change"]["entry_id"] for s in steps]


# --- 47. chat writes off -----------------------------------------------------------------


def test_case_47_with_chat_writes_off_every_new_write_is_refused_and_reads_answer(client, conn, story, temp_db):
    ids = harbour_set_against(conn, story)
    assert client.put("/api/chat-writes", json={"enabled": False}).status_code == 200
    before = dump(temp_db)
    calls = {
        "mark_expect_back": dict(tx_id=story.golden, direction="back", all=True),
        "undo_mark": dict(expectation_id=ids["klook"]),
        "set_off": dict(tx_id=story.lena_2210, placements=[], waiting={"person_id": story.people["Lena"]}),
        "unlink_set_off": dict(set_off_id=ids["kim_on_harbour"]),
        "let_go": dict(expectation_id=ids["harbour"], became="gift"),
        "undo_let_go": dict(expectation_id=ids["harbour"]),
        "not_a_payback": dict(tx_id=story.jay_0412),
    }
    assert sorted(calls) == sorted(NEW_WRITES)

    for name, args in calls.items():
        assert chat(name, **args) == {"ok": False, "error": chat_writes.WRITES_OFF}, name

    assert dump(temp_db) == before
    harbour = next(r for r in chat("transactions", per_page=100)["result"]["transactions"]
                   if r["id"] == story.harbour)
    assert (harbour["amount_minor"], harbour["expected_minor"], harbour["placed_minor"],
            harbour["own_minor"]) == (30000, 20000, 0, 10000)
    assert chat("expected_back")["ok"]
    assert chat("expected_back", expectation_id=ids["harbour"])["ok"]


def test_case_47_the_transactions_read_returns_the_four_part_fields(client, conn, story):
    ids = harbour_set_against(conn, story)

    rows = {r["id"]: r for r in chat("transactions", per_page=100)["result"]["transactions"]}

    harbour, jay = rows[story.harbour], rows[story.jay_0412]
    assert (harbour["amount_minor"], harbour["expected_minor"], harbour["placed_minor"], harbour["own_minor"]) == \
        (30000, 20000, 0, 10000)
    assert (jay["amount_minor"], jay["expected_minor"], jay["placed_minor"], jay["own_minor"]) == \
        (-12000, 0, -12000, 0)
    assert ids["harbour"]


# --- the read ------------------------------------------------------------------------------


def test_the_expected_back_read_lists_what_is_open_oldest_first(client, conn, story):
    ids = harbour_set_against(conn, story)
    gone = mark(conn, story.golden, 6400, names=(story.people["Jay"], story.people["Lena"]))

    answer = chat("expected_back")

    assert answer["ok"], answer
    items = answer["result"]["items"]
    assert [i["id"] for i in items] == [gone, ids["klook"], ids["harbour"]]
    assert [open_of(i) for i in items] == [6400, 17000, 2000]


def test_the_expected_back_read_filters_by_direction_person_and_currency(client, conn, story):
    harbour_set_against(conn, story)
    march = row(conn, story.book, story.book.savings, "2026-03-01", "FROM PARENTS", -500000, flow="review")
    owed = mark(conn, march, 500000, names=(story.people["Parents"],))

    pay = chat("expected_back", direction="pay")["result"]
    kim = chat("expected_back", person=story.people["Kim"])["result"]
    rupees = chat("expected_back", currency="INR")["result"]

    assert [i["id"] for i in pay["items"]] == [owed]
    assert len(kim["items"]) == 2
    assert rupees["items"] == []


def test_the_expected_back_read_for_a_payment_lists_what_it_can_set_off(client, conn, story):
    ids = harbour_set_against(conn, story)

    answer = chat("expected_back", payment=story.lena_2210)

    assert answer["ok"], answer
    assert answer["result"]["direction"] == "back"
    assert answer["result"]["payment"]["id"] == story.lena_2210
    assert {i["id"] for i in answer["result"]["items"]} == {ids["harbour"], ids["klook"]}


def test_the_expected_back_page_names_the_set_offs_for_unlinking(client, conn, story):
    ids = harbour_set_against(conn, story)

    page = chat("expected_back", expectation_id=ids["harbour"])["result"]

    set_off_lines = [line for line in page["lines"] if line["kind"] == "set_off"]
    assert sorted(line["id"] for line in set_off_lines) == sorted([ids["jay_on_harbour"], ids["kim_on_harbour"]])


def test_the_expected_back_page_wins_over_filters(client, conn, story):
    ids = harbour_set_against(conn, story)

    answer = chat("expected_back", expectation_id=ids["harbour"], direction="pay")

    assert answer["ok"], answer
    assert answer["result"]["expectation"]["id"] == ids["harbour"]


def test_a_read_writes_nothing(client, conn, story):
    harbour_set_against(conn, story)
    before = snapshot(conn)

    chat("expected_back")
    chat("transactions", per_page=100)

    assert snapshot(conn) == before
    assert last_entry(conn) == before["change_entries"][-1][0]



def test_money_in_waiting_is_not_said_spending_to_be_marked(client, conn, story):
    before = snapshot(conn)

    answer = chat("mark_expect_back", tx_id=story.jay_0412, direction="pay", all=True, flow_type="expense")

    refused(conn, answer, before=before)
    assert "money out" in answer["error"]
