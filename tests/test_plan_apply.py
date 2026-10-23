"""Chat-plan-apply 01: preview a batch of teaches, and apply it as one change
(spec cases 1-3, 5-9).

`POST /api/plans/preview` takes {items: [...]}, each item the resolve route's
body for a Queue card (its rows as tx_ids, a name or a held merchant, the
wording and other wordings, a type, a book, Check each row, the answers), and
writes nothing: it gives, per merchant, the rows that would be labelled, the
rows of another merchant a wording would reach, any refusal, and one token.
`POST /api/plans/apply` takes the same items and the token and writes the
whole batch as one change entry, which one Undo takes back. A refused item, a
stale token or a replay writes nothing and leaves no entry.

Each case is one request through fin's public routes (the Flask test client),
then what the book holds afterwards, read back table by table. The database is
the temporary one; every merchant, wording and figure is invented.
"""

import pytest

import access_gate
import history
from test_book_and_type_switch import merchant, rule, statement, type_id  # noqa: F401 (statement is a fixture)
from test_preview_merchant_change import add_row, entries

CHAT_ACTOR = "chat:sample-subject"
STALE = "the book changed since the preview: preview again"


def book_state(conn) -> dict:
    """Every tracked table's rows, and how many change entries there are."""
    state = {t: sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}")) for t in history.TRACKED}
    state["change_entries"] = conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0]
    return state


def untyped(conn, statement_id, description, amount_minor, day="2026-09-10"):
    return add_row(conn, statement_id, description, amount_minor, service_id=None, book=None, type_name=None,
                   day=day)


@pytest.fixture
def queue(conn, statement):  # noqa: F811
    """Three cards' rows waiting for a type: two Indigoair rows; one Rhode row
    whose wording holds both RHODE SKIN and RHODESKIN, Rhode held already
    (Health & Beauty, Household, its own note); two Vaura Pilates rows."""
    rhode = merchant(conn, "Rhode", "Household", "Health & Beauty")
    conn.execute("UPDATE services SET notes = 'sample note', exclude_from_expense_views = 0 WHERE id = ?", (rhode,))
    conn.commit()
    return {
        "indigo": [untyped(conn, statement, "INDIGOAIR 6E 101", 21000, "2026-09-02"),
                   untyped(conn, statement, "INDIGOAIR 6E 202", 23000, "2026-09-12")],
        "rhode": [untyped(conn, statement, "SP RHODE SKIN RHODESKIN.COM CA", 5300, "2026-09-05")],
        "vaura": [untyped(conn, statement, "VAURA PILATES SG", 4000, "2026-09-03"),
                  untyped(conn, statement, "VAURA PILATES SG", 4000, "2026-09-17")],
        "rhode_id": rhode,
        "statement": statement,
    }


def items_of(conn, queue) -> list[dict]:
    return [
        {"tx_ids": queue["indigo"], "service_name": "Indigoair", "pattern": "INDIGOAIR",
         "type_id": type_id(conn, "Travel"), "book": "Household"},
        {"tx_ids": queue["rhode"], "service_id": queue["rhode_id"], "pattern": "RHODE SKIN",
         "wordings": ["RHODESKIN"]},
        {"tx_ids": queue["vaura"], "service_name": "Vaura Pilates", "pattern": "VAURA PILATES",
         "type_id": type_id(conn, "Fitness"), "book": "Household", "check_each_row": True},
    ]


def preview(client, items) -> dict:
    resp = client.post("/api/plans/preview", json={"items": items})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def apply(client, items, token, **kw):
    return client.post("/api/plans/apply", json={"items": items, "token": token}, **kw)


def as_chat() -> dict:
    return {"environ_base": {access_gate.CHAT_CALL_ENVIRON_KEY: access_gate.chat_call_identity(CHAT_ACTOR)}}


# --- case 1: preview writes nothing; apply makes one entry ------------------------------------


def test_case_1_the_preview_gives_each_merchants_rows_and_a_token_and_writes_nothing(client, conn, queue):
    before = book_state(conn)

    shown = preview(client, items_of(conn, queue))

    assert [i["merchant"] for i in shown["items"]] == ["Indigoair", "Rhode", "Vaura Pilates"]
    assert [i["count"] for i in shown["items"]] == [2, 1, 2]
    assert [sorted(r["id"] for r in i["rows"]) for i in shown["items"]] == [
        sorted(queue["indigo"]), queue["rhode"], sorted(queue["vaura"])]
    assert [i["overlaps"] for i in shown["items"]] == [[], [], []]
    assert [i["new_merchant"] for i in shown["items"]] == [True, False, True]
    assert shown["refusals"] == []
    assert (shown["merchants"], shown["rows"]) == (3, 5)
    assert isinstance(shown["token"], str) and shown["token"]
    assert book_state(conn) == before


def test_case_1_apply_writes_the_batch_as_one_change_and_the_queue_lets_the_rows_go(client, conn, queue):
    items = items_of(conn, queue)
    token = preview(client, items)["token"]
    entries_before = len(entries(client))

    resp = apply(client, items, token)

    assert resp.status_code == 200, resp.get_json()
    assert (resp.get_json()["merchants"], resp.get_json()["rows"]) == (3, 5)
    shown = entries(client)
    assert len(shown) == entries_before + 1
    assert shown[0]["summary"] == "Applied a plan: 3 merchants, 5 rows"
    assert int(resp.headers["X-Fin-Change"]) == shown[0]["id"]
    labelled = {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT t.id, s.name, t.book, t.type_id FROM transactions t JOIN services s ON s.id = t.service_id")}
    travel, beauty, fitness = (type_id(conn, n) for n in ("Travel", "Health & Beauty", "Fitness"))
    assert {i: labelled[i] for i in queue["indigo"]} == {i: ("Indigoair", "Household", travel) for i in queue["indigo"]}
    assert labelled[queue["rhode"][0]] == ("Rhode", "Household", beauty)
    assert {i: labelled[i] for i in queue["vaura"]} == {i: ("Vaura Pilates", "Household", fitness) for i in queue["vaura"]}
    assert sorted(r[0] for r in conn.execute(
        "SELECT r.pattern FROM merchant_rules r JOIN services s ON s.id = r.service_id"
        " WHERE s.name IN ('Indigoair', 'Rhode', 'Vaura Pilates')")) == [
        "INDIGOAIR", "RHODE SKIN", "RHODESKIN", "VAURA PILATES"]
    assert conn.execute("SELECT review_each_time FROM services WHERE name = 'Vaura Pilates'").fetchone()[0] == 1
    cards = client.get("/api/queue/cards").get_json()
    waiting = {r["id"] for g in cards["untyped"]["groups"] for r in g["rows"]}
    waiting |= {r["id"] for r in cards["check_each"]["rows"]}
    assert waiting.isdisjoint(queue["indigo"] + queue["rhode"] + queue["vaura"])


# --- case 2: one Undo takes the whole batch back ----------------------------------------------


def test_case_2_undo_of_the_entry_puts_every_row_rule_and_merchant_back(client, conn, queue):
    items = items_of(conn, queue)
    before = book_state(conn)
    rhode_before = tuple(conn.execute("SELECT * FROM services WHERE id = ?", (queue["rhode_id"],)).fetchone())
    resp = apply(client, items, preview(client, items)["token"])
    assert resp.status_code == 200, resp.get_json()
    assert tuple(conn.execute("SELECT * FROM services WHERE id = ?", (queue["rhode_id"],)).fetchone()) == rhode_before

    undone = client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")

    assert undone.status_code == 200, undone.get_json()
    after = book_state(conn)
    assert {t: after[t] for t in history.TRACKED} == {t: before[t] for t in history.TRACKED}


# --- case 3: a refused item refuses the batch -------------------------------------------------


@pytest.mark.parametrize("spoil, words", [
    (lambda item: item.update(wordings=["NO SUCH WORDING"]), "The wording NO SUCH WORDING matches no row"),
    (lambda item: item.update(type_id=987654), "987654"),
    (lambda item: item.update(book="Nowhere"), "Nowhere"),
])
def test_case_3_a_refused_item_is_named_and_the_batch_writes_nothing(client, conn, queue, spoil, words):
    items = items_of(conn, queue)
    spoil(items[2])
    before = book_state(conn)

    shown = preview(client, items)

    assert [r["merchant"] for r in shown["refusals"]] == ["Vaura Pilates"]
    assert words in shown["refusals"][0]["error"]
    assert shown["token"] is None
    assert [i["merchant"] for i in shown["items"]] == ["Indigoair", "Rhode"]
    good = preview(client, items_of(conn, queue))["token"]

    resp = apply(client, items, good)

    assert resp.status_code == 400
    assert [r["merchant"] for r in resp.get_json()["refusals"]] == ["Vaura Pilates"]
    assert book_state(conn) == before


# --- case 5: a row changed after the preview makes its token stale ----------------------------


def test_case_5_a_row_labelled_in_the_app_after_the_preview_refuses_the_apply(client, conn, queue):
    items = items_of(conn, queue)
    token = preview(client, items)["token"]
    put = client.put(f"/api/transactions/{queue['vaura'][1]}", json={"type_id": type_id(conn, "Dining")})
    assert put.status_code == 200, put.get_json()
    before = book_state(conn)

    resp = apply(client, items, token)

    assert resp.status_code == 409
    assert resp.get_json()["error"] == STALE
    assert book_state(conn) == before


def test_an_apply_with_other_items_than_the_preview_is_refused(client, conn, queue):
    items = items_of(conn, queue)
    token = preview(client, items)["token"]
    items[0]["book"] = "Moom"
    before = book_state(conn)

    resp = apply(client, items, token)

    assert resp.status_code == 409
    assert book_state(conn) == before


def test_an_apply_without_a_token_is_refused(client, conn, queue):
    before = book_state(conn)

    resp = client.post("/api/plans/apply", json={"items": items_of(conn, queue)})

    assert resp.status_code == 400
    assert book_state(conn) == before


# --- case 6: a replay is refused --------------------------------------------------------------


def test_case_6_the_same_token_sent_twice_writes_once(client, conn, queue):
    items = items_of(conn, queue)
    del items[1]["wordings"]
    token = preview(client, items)["token"]
    first = apply(client, items, token)
    assert first.status_code == 200, first.get_json()
    after_first = book_state(conn)

    again = apply(client, items, token)

    assert again.status_code == 409, again.get_json()
    assert again.get_json()["error"] == STALE
    assert book_state(conn) == after_first
    assert sum(1 for e in entries(client) if e["summary"].startswith("Applied a plan")) == 1


def test_case_6_a_replay_whose_wording_no_longer_reaches_a_row_is_refused_by_name(client, conn, queue):
    """Once applied, RHODE SKIN (the longer wording) wins the Rhode row, so
    RHODESKIN reaches nothing: the second apply is refused for that."""
    items = items_of(conn, queue)
    token = preview(client, items)["token"]
    assert apply(client, items, token).status_code == 200
    after_first = book_state(conn)

    again = apply(client, items, token)

    assert again.status_code == 400, again.get_json()
    assert [r["merchant"] for r in again.get_json()["refusals"]] == ["Rhode"]
    assert book_state(conn) == after_first


# --- case 7: a wording that reaches another merchant's rows -----------------------------------


@pytest.fixture
def gifts(conn, queue):
    """Sample Gifts holds a row whose wording holds RHODESKIN."""
    svc = merchant(conn, "Sample Gifts", "Moom", "Shopping")
    rule(conn, "SAMPLEGIFTS", svc)
    held = add_row(conn, queue["statement"], "RHODESKIN GIFT CARD", 2500, service_id=svc, book="Moom",
                   type_name="Shopping", day="2026-08-01")
    return {"svc": svc, "held": held}


def held_row(conn, tx):
    return tuple(conn.execute(
        "SELECT service_id, book, type_id, book_source, type_source FROM transactions WHERE id = ?", (tx,)).fetchone())


def test_case_7_the_preview_names_the_other_merchants_rows_and_without_an_answer_only_new_rows_take_it(
        client, conn, queue, gifts):
    items = items_of(conn, queue)

    shown = preview(client, items)

    assert shown["items"][1]["overlaps"] == [{"service_id": gifts["svc"], "name": "Sample Gifts", "count": 1}]
    resp = apply(client, items, shown["token"])
    assert resp.status_code == 200, resp.get_json()
    assert held_row(conn, gifts["held"]) == (gifts["svc"], "Moom", type_id(conn, "Shopping"), "checked", "checked")


def test_case_7_with_past_rows_apply_the_other_merchants_rows_move_too(client, conn, queue, gifts):
    items = items_of(conn, queue)
    items[1]["past_rows"] = "apply"

    resp = apply(client, items, preview(client, items)["token"])

    assert resp.status_code == 200, resp.get_json()
    assert held_row(conn, gifts["held"])[:3] == (queue["rhode_id"], "Household", type_id(conn, "Health & Beauty"))


# --- case 8: at most 20 merchants -------------------------------------------------------------


def test_case_8_a_batch_of_21_merchants_is_refused(client, conn, queue):
    item = items_of(conn, queue)[0]
    items = [{**item, "tx_ids": [queue["indigo"][0]] if n == 0 else [10_000 + n]} for n in range(21)]
    before = book_state(conn)

    for route in ("/api/plans/preview", "/api/plans/apply"):
        resp = client.post(route, json={"items": items, "token": "0" * 32})
        assert resp.status_code == 400
        assert "20" in resp.get_json()["error"]
    assert book_state(conn) == before


def test_a_row_in_two_items_is_refused(client, conn, queue):
    items = items_of(conn, queue)
    items[2]["tx_ids"] = [*queue["vaura"], queue["indigo"][0]]

    resp = client.post("/api/plans/preview", json={"items": items})

    assert resp.status_code == 400


# --- case 9: the entry lists every row, and reads as chat's -----------------------------------


def test_case_9_the_entry_lists_every_row_before_and_after_and_reads_as_chats(client, conn, queue):
    items = items_of(conn, queue)
    token = preview(client, items)["token"]

    resp = apply(client, items, token, **as_chat())

    assert resp.status_code == 200, resp.get_json()
    shown = client.get(f"/api/history/{resp.headers['X-Fin-Change']}").get_json()
    assert (shown["via"], shown["actor"]) == ("chat", CHAT_ACTOR)
    assert shown["summary"] == "Chat applied a plan: 3 merchants, 5 rows"
    rows = {}   # each row's first before and last after: a Check each row row is checked after its teach
    for c in shown["changes"]:
        if c["table"] == "transactions":
            rows[c["row_id"]] = (rows[c["row_id"]][0] if c["row_id"] in rows else c["before"], c["after"])
    assert set(rows) == set(queue["indigo"] + queue["rhode"] + queue["vaura"])
    assert all(before["type_id"] is None and after["type_id"] is not None for before, after in rows.values())
    assert {c["table"] for c in shown["changes"]} == {"transactions", "services", "merchant_rules"}
