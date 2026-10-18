"""Teaching a merchant as Check each row (fin-surfaces 13; spec 08, case 5).

Resolve takes an optional `check_each_row: true`, which sets the flag on the
merchant it finds or creates, in the same transaction (one change entry).
The screen then checks the rows it showed with their picked books through
bulk label (a second entry; the toast's Undo takes both). Rows the rule
reached that were not on screen stay waiting. A merchant fin already held
keeps its past rows waiting unless the screen's offer answers `past: leave`
(case 12's "Not now"), written in the same change. Driven through the Flask test
client against the temporary database. Every merchant, figure and name is
invented.
"""

import pytest

from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    label, merchant, merchant_shown, row, rule_shown, shown, statement, type_id,
)


def waiting(client) -> list[int]:
    """The rows the Queue's Check each row step lists."""
    payload = client.get("/api/transactions?look=mixed&per_page=500").get_json()
    return sorted(t["id"] for t in payload["transactions"])


def flag(conn, service_id: int) -> int:
    return conn.execute("SELECT review_each_time FROM services WHERE id = ?", (service_id,)).fetchone()[0]


def entries(client) -> list[dict]:
    return [e for e in client.get("/api/history").get_json()["entries"] if not e.get("undoes")]


def undo(client, *resps) -> None:
    """The toast's Undo over several changes: newest first."""
    for change in sorted((int(r.headers["X-Fin-Change"]) for r in resps), reverse=True):
        assert client.post(f"/api/history/{change}/undo").status_code == 200


@pytest.fixture
def milis_card(conn) -> int:
    """A second Household card, as Mili's: its own statement."""
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four)"
        " VALUES ('Sample Card 7436 (MK)', 'Sample-7436', 'credit_card', '7436')"
    )
    account_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, '2026-09-30', 'mk.csv')",
        (account_id,),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def teach(conn, tx: int, name: str, pattern: str, type_name: str, book: str, **more) -> dict:
    """The body a ✓ sends for a line set to Check each row: its first row,
    scope service_default, the pattern and the usual book."""
    return {
        "tx_id": tx, "service_name": name, "type_id": type_id(conn, type_name), "book": book,
        "apply_scope": "service_default", "pattern": pattern, "match_type": "contains",
        "suggestion_visible": True, "check_each_row": True, **more,
    }


# --- resolve with check_each_row ---------------------------------------------


def test_case_5_taught_as_check_each_row_on_the_sure_card(client, conn, statement, milis_card):
    """Indigoair, usual book Household; the Mili's card row flipped to Moom.
    A third row the rule reaches but that was not on screen waits."""
    yours = row(conn, statement, "SAMPLEAIR 312-4567890 MUMBAI", 2400.0, day="2026-09-02")
    milis = row(conn, milis_card, "SAMPLEAIR 312-9988776 DELHI", 3239.40, day="2026-09-09")
    unseen = row(conn, milis_card, "SAMPLEAIR 312-5550001 GOA", 410.0, day="2026-09-12")
    before = len(entries(client))

    taught = client.post("/api/transactions/resolve", json=teach(conn, yours, "Sampleair", "SAMPLEAIR", "Travel", "Household"))

    assert taught.status_code == 200, taught.get_json()
    svc = taught.get_json()["service_id"]
    made = merchant_shown(client, svc)
    assert (made["name"], made["book"], made["display_type"]) == ("Sampleair", "Household", "Travel")
    assert flag(conn, svc) == 1
    assert rule_shown(client, taught.get_json()["rule_id"])["service_id"] == svc
    assert len(entries(client)) == before + 1

    checked = client.post("/api/transactions/bulk-label", json={"rows": [
        {"tx_id": yours, "book": "Household", "type_id": type_id(conn, "Travel")},
        {"tx_id": milis, "book": "Moom", "type_id": type_id(conn, "Travel")},
    ]})

    assert checked.status_code == 200, checked.get_json()
    assert label(client, yours) == ("Household", "Travel", "manual")
    assert label(client, milis) == ("Moom", "Travel", "manual")
    assert label(client, unseen) == ("Household", "Travel", "service_default")
    assert waiting(client) == [unseen]
    assert shown(client, unseen)["not_checked"]
    assert len(entries(client)) == before + 2

    undo(client, taught, checked)
    assert [s for s in client.get("/api/services").get_json() if s["name"] == "Sampleair"] == []
    assert [r for r in client.get("/api/rules").get_json() if r["pattern"] == "SAMPLEAIR"] == []
    for tx in (yours, milis, unseen):
        assert shown(client, tx)["type_id"] is None
        assert shown(client, tx)["service_id"] is None
    assert waiting(client) == []


def held_merchant(conn, statement) -> tuple[int, int, int, int]:
    """Sample Air, Every row the same, with one past row fin labelled; and
    two new rows the rule will reach: the one taught and one more."""
    svc = merchant(conn, "Sample Air", "Household", "Travel")
    past = row(conn, statement, "SAMPLE AIR OLD BOOKING", 90.0, book="Household", type_name="Travel",
               service_id=svc, cat_source="service_default", day="2026-03-01")
    tx = row(conn, statement, "SAMPLE AIR 0042", 120.0, day="2026-09-02")
    reached = row(conn, statement, "SAMPLE AIR 0077", 130.0, day="2026-09-05")
    return svc, past, tx, reached


def test_an_existing_merchant_taught_as_check_each_row_takes_the_flag_only(client, conn, statement):
    """No answer to the offer: the flag only, in one change. Its past rows
    keep their labels and wait, with the row taught and the rows the new rule
    reached; nothing is marked checked unasked."""
    svc, past, tx, reached = held_merchant(conn, statement)
    before = len(entries(client))

    resp = client.post("/api/transactions/resolve", json=teach(conn, tx, "Sample Air", "SAMPLE AIR", "Travel", "Household",
                                                               service_id=svc))

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["service_id"] == svc
    assert flag(conn, svc) == 1
    assert label(client, past) == ("Household", "Travel", "service_default")
    assert waiting(client) == sorted([past, tx, reached])
    assert len(entries(client)) == before + 1

    undo(client, resp)
    assert flag(conn, svc) == 0
    assert waiting(client) == []
    assert shown(client, tx)["type_id"] is None


def test_case_12s_not_now_when_taught_marks_the_past_rows_checked_in_the_same_change(client, conn, statement):
    """past: leave, the operator's "Not now, only new rows": its past rows are
    marked checked, labels unchanged; the rows this save labels still wait.
    One change, and its Undo returns all of it."""
    svc, past, tx, reached = held_merchant(conn, statement)
    before = len(entries(client))

    resp = client.post("/api/transactions/resolve", json=teach(conn, tx, "Sample Air", "SAMPLE AIR", "Travel", "Household",
                                                               service_id=svc, past="leave"))

    assert resp.status_code == 200, resp.get_json()
    assert flag(conn, svc) == 1
    assert label(client, past) == ("Household", "Travel", "manual")
    assert waiting(client) == sorted([tx, reached])
    assert len(entries(client)) == before + 1

    undo(client, resp)
    assert flag(conn, svc) == 0
    assert label(client, past) == ("Household", "Travel", "service_default")
    assert shown(client, tx)["type_id"] is None
    assert shown(client, reached)["type_id"] is None


def test_case_13s_bring_them_too_when_taught_leaves_the_past_rows_waiting(client, conn, statement):
    svc, past, tx, reached = held_merchant(conn, statement)

    resp = client.post("/api/transactions/resolve", json=teach(conn, tx, "Sample Air", "SAMPLE AIR", "Travel", "Household",
                                                               service_id=svc, past="queue"))

    assert resp.status_code == 200, resp.get_json()
    assert label(client, past) == ("Household", "Travel", "service_default")
    assert waiting(client) == sorted([past, tx, reached])


@pytest.mark.parametrize("more", [
    {"past": "leave", "check_each_row": False},         # an answer with no Check each row
    {"past": "leave", "check_each_row": None},
    {"past": "forget"},                                  # not an answer
])
def test_a_past_answer_out_of_place_is_refused_and_nothing_is_written(client, conn, statement, more):
    svc, past, tx, _ = held_merchant(conn, statement)
    before = len(entries(client))
    body = teach(conn, tx, "Sample Air", "SAMPLE AIR", "Travel", "Household", service_id=svc, **more)
    if body["check_each_row"] is None:
        del body["check_each_row"]

    resp = client.post("/api/transactions/resolve", json=body)

    assert resp.status_code == 400
    assert len(entries(client)) == before
    assert flag(conn, svc) == 0
    assert label(client, past) == ("Household", "Travel", "service_default")
    assert shown(client, tx)["type_id"] is None


def test_a_past_answer_for_a_merchant_already_checking_each_row_changes_nothing_past(client, conn, statement):
    """The offer is for a merchant newly set: one already set keeps its waiting rows."""
    svc, past, tx, _ = held_merchant(conn, statement)
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (svc,))
    conn.commit()

    resp = client.post("/api/transactions/resolve", json=teach(conn, tx, "Sample Air", "SAMPLE AIR", "Travel", "Household",
                                                               service_id=svc, past="leave"))

    assert resp.status_code == 200, resp.get_json()
    assert past in waiting(client)


def test_a_merchant_already_set_to_check_each_row_keeps_its_waiting_rows(client, conn, statement):
    svc = merchant(conn, "Sample Mart", "Household", "Shopping")
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (svc,))
    conn.commit()
    old = row(conn, statement, "SAMPLE MART 1", 45.90, book="Household", type_name="Shopping",
              service_id=svc, cat_source="service_default", day="2026-09-01")
    tx = row(conn, statement, "SAMPLE MART 2", 19.0, day="2026-09-03")

    resp = client.post("/api/transactions/resolve", json=teach(conn, tx, "Sample Mart", "SAMPLE MART", "Shopping", "Household",
                                                               service_id=svc))

    assert resp.status_code == 200, resp.get_json()
    assert waiting(client) == sorted([old, tx])


def test_without_check_each_row_resolve_leaves_the_flag_alone(client, conn, statement):
    tx = row(conn, statement, "SAMPLE GYM 0042")
    body = teach(conn, tx, "Sample Gym", "SAMPLE GYM", "Fitness", "Household")
    del body["check_each_row"]

    resp = client.post("/api/transactions/resolve", json=body)

    assert resp.status_code == 200
    assert flag(conn, resp.get_json()["service_id"]) == 0


@pytest.mark.parametrize("more", [
    {"check_each_row": "yes"},                           # not a true or false
    {"apply_scope": "transaction"},                      # only this row: no merchant is taught
    {"apply_scope": "rule"},                             # a rule override: the same
])
def test_check_each_row_is_refused_where_it_does_not_belong_and_nothing_is_written(client, conn, statement, more):
    tx = row(conn, statement, "SAMPLE SHOP 0042")
    before = len(entries(client))

    resp = client.post("/api/transactions/resolve", json=teach(conn, tx, "Sample Shop", "SAMPLE SHOP", "Shopping", "Household",
                                                               **more))

    assert resp.status_code == 400
    assert resp.headers.get("X-Fin-Change") is None
    assert len(entries(client)) == before
    assert shown(client, tx)["type_id"] is None
    assert not [s for s in client.get("/api/services").get_json() if s["name"] == "Sample Shop"]


def test_check_each_row_false_sets_a_check_each_row_merchant_back_to_every_row_the_same(client, conn, statement):
    svc = merchant(conn, "Sample Mart", "Household", "Shopping")
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (svc,))
    conn.commit()
    old = row(conn, statement, "SAMPLE MART 1", 45.90, book="Household", type_name="Shopping",
              service_id=svc, cat_source="service_default", day="2026-09-01")
    tx = row(conn, statement, "SAMPLE MART 2", 19.0, day="2026-09-03")

    resp = client.post("/api/transactions/resolve", json=teach(conn, tx, "Sample Mart", "SAMPLE MART", "Shopping", "Household",
                                                               service_id=svc, check_each_row=False))

    assert resp.status_code == 200, resp.get_json()
    assert flag(conn, svc) == 0
    assert waiting(client) == []
    assert label(client, old) == ("Household", "Shopping", "service_default")

    undo(client, resp)
    assert flag(conn, svc) == 1
    assert waiting(client) == [old]


def test_check_each_row_false_is_every_row_the_same(client, conn, statement):
    tx = row(conn, statement, "SAMPLE BAKERY 0042")

    resp = client.post("/api/transactions/resolve", json=teach(conn, tx, "Sample Bakery", "SAMPLE BAKERY", "Dining", "Household",
                                                               check_each_row=False))

    assert resp.status_code == 200
    assert flag(conn, resp.get_json()["service_id"]) == 0
