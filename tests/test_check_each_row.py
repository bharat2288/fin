"""Check each row: the merchant setting, its Queue step, and bulk label
(fin-surfaces 12; spec 08, cases 6-8, 12-15, 20).

"Check each row" is the existing `services.review_each_time` flag. A row
waits when its merchant has the flag and its label is not hand-set; checking
it makes the label hand-set. Two routes write: the setting
(`POST /api/services/<id>/check-each-row`) and bulk label
(`POST /api/transactions/bulk-label`), each one change entry, undone whole.
Driven through the Flask test client against the temporary database. Every
merchant, figure and name is invented.
"""

import pytest

import db
from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    label, merchant, row, rule, shown, statement, type_id,
)


def waiting(client) -> list[int]:
    """The rows the Queue's Check each row step lists."""
    payload = client.get("/api/transactions?look=mixed&per_page=500").get_json()
    return sorted(t["id"] for t in payload["transactions"])


def flag(conn, service_id: int) -> int:
    return conn.execute("SELECT review_each_time FROM services WHERE id = ?", (service_id,)).fetchone()[0]


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def undo(client, resp) -> None:
    change = resp.headers["X-Fin-Change"]
    assert client.post(f"/api/history/{change}/undo").status_code == 200


@pytest.fixture
def expedia(conn, statement) -> tuple[int, list[int]]:
    """An Every row the same merchant with 4 past rows: 3 labelled from it,
    1 the operator set by hand to Moom."""
    svc = merchant(conn, "Sample Travelsite", "Household", "Travel")
    txs = [row(conn, statement, "SAMPLE TRAVELSITE BKG", 100.0 + i, book="Household", type_name="Travel",
               service_id=svc, cat_source="service_default", day=f"2026-03-1{i}") for i in range(3)]
    txs.append(row(conn, statement, "SAMPLE TRAVELSITE BKG", 300.0, book="Moom", type_name="Travel",
                   service_id=svc, cat_source="manual", day="2026-03-20"))
    return svc, txs


# --- the setting ------------------------------------------------------------


def test_case_12_on_leaving_the_past_marks_its_rows_checked_in_one_change_undone_whole(client, conn, expedia):
    svc, txs = expedia
    before = [label(client, tx)[:2] for tx in txs]

    resp = client.post(f"/api/services/{svc}/check-each-row", json={"on": True, "past": "leave"})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["checked"] == 3
    assert flag(conn, svc) == 1
    assert waiting(client) == []
    assert [label(client, tx) for tx in txs] == [(*b, "manual") for b in before]
    assert len([e for e in entries(client) if not e.get("undoes")]) == 1

    undo(client, resp)
    assert flag(conn, svc) == 0
    assert [label(client, tx) for tx in txs] == [
        ("Household", "Travel", "service_default")] * 3 + [("Moom", "Travel", "manual")]


def test_case_13_on_bringing_the_past_leaves_its_rows_waiting(client, conn, expedia):
    svc, txs = expedia

    resp = client.post(f"/api/services/{svc}/check-each-row", json={"on": True, "past": "queue"})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["waiting"] == 3
    assert flag(conn, svc) == 1
    assert waiting(client) == sorted(txs[:3])
    assert all(shown(client, tx)["not_checked"] for tx in txs[:3])
    assert not shown(client, txs[3])["not_checked"]

    undo(client, resp)
    assert flag(conn, svc) == 0
    assert waiting(client) == []


def test_case_14_off_empties_its_waiting_rows_and_keeps_their_labels(client, conn, expedia):
    svc, txs = expedia
    client.post(f"/api/services/{svc}/check-each-row", json={"on": True, "past": "queue"})
    before = [label(client, tx) for tx in txs]

    resp = client.post(f"/api/services/{svc}/check-each-row", json={"on": False})

    assert resp.status_code == 200, resp.get_json()
    assert flag(conn, svc) == 0
    assert waiting(client) == []
    assert [label(client, tx) for tx in txs] == before
    assert not any(shown(client, tx)["not_checked"] for tx in txs)

    undo(client, resp)
    assert flag(conn, svc) == 1
    assert waiting(client) == sorted(txs[:3])


def test_on_with_no_past_given_leaves_the_past_alone(client, conn, expedia):
    svc, txs = expedia

    resp = client.post(f"/api/services/{svc}/check-each-row", json={"on": True})

    assert resp.status_code == 200
    assert waiting(client) == []
    assert label(client, txs[0])[2] == "manual"


@pytest.mark.parametrize("body", [{}, {"on": "yes"}, {"on": True, "past": "everything"}])
def test_the_setting_refuses_a_body_it_does_not_understand_and_writes_nothing(client, conn, expedia, body):
    svc, _ = expedia
    before = entries(client)

    resp = client.post(f"/api/services/{svc}/check-each-row", json=body)

    assert resp.status_code == 400
    assert resp.headers.get("X-Fin-Change") is None
    assert entries(client) == before
    assert flag(conn, svc) == 0


def test_the_setting_of_a_merchant_no_longer_in_fin_says_so(client):
    resp = client.post("/api/services/9999/check-each-row", json={"on": True, "past": "leave"})

    assert resp.status_code == 404
    assert "no longer in fin" in resp.get_json()["error"]


def test_off_leaves_untyped_rows_and_rows_of_other_merchants_alone(client, conn, statement, expedia):
    svc, txs = expedia
    other = merchant(conn, "Sample Bazaar", "Household", "Shopping")
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (other,))
    conn.commit()
    theirs = row(conn, statement, "SAMPLE BAZAAR", 12.0, book="Household", type_name="Shopping",
                 service_id=other, cat_source="service_default")
    client.post(f"/api/services/{svc}/check-each-row", json={"on": True, "past": "leave"})

    assert waiting(client) == [theirs]
    assert label(client, theirs)[2] == "service_default"


# --- rows that wait -----------------------------------------------------------


def test_case_6_a_new_row_of_a_check_each_row_merchant_counts_in_its_usual_book_and_waits(client, conn, statement):
    svc = merchant(conn, "Sample Airways", "Household", "Travel")
    rule(conn, "SAMPLE AIRWAYS", svc)
    assert client.post(f"/api/services/{svc}/check-each-row", json={"on": True, "past": "leave"}).status_code == 200

    found = db.match_merchant("SAMPLE AIRWAYS 312-1112223 MUMBAI", conn, 118000)
    assert (found["book"], found["service_id"], found["review_each_time"]) == ("Household", svc, True)
    tx = row(conn, statement, "SAMPLE AIRWAYS 312-1112223 MUMBAI", 1180.0, book=found["book"],
             type_name="Travel", service_id=svc, cat_source=found["cat_source"])

    assert label(client, tx) == ("Household", "Travel", "service_default")
    assert shown(client, tx)["not_checked"]
    assert waiting(client) == [tx]


def test_case_15_a_merchant_flagged_before_this_change_lists_its_rows_unchanged(client, conn, statement):
    svc = merchant(conn, "Sample Bazaar", "Household", "Shopping")
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (svc,))
    conn.commit()
    txs = [row(conn, statement, "SAMPLE BAZAAR MKTPLACE", amount, book="Household", type_name="Shopping",
               service_id=svc, cat_source="service_default", day=f"2026-04-1{i}")
           for i, amount in enumerate((45.90, 219.00, 49.90))]

    assert waiting(client) == sorted(txs)
    assert client.get("/api/transactions?look=each&per_page=500").get_json()["total"] == 3
    assert all(shown(client, tx)["not_checked"] for tx in txs)
    assert [label(client, tx) for tx in txs] == [("Household", "Shopping", "service_default")] * 3


# --- bulk label ---------------------------------------------------------------


@pytest.fixture
def amazon(conn, statement) -> tuple[int, list[int]]:
    """A Check each row merchant with 3 rows waiting (case 7)."""
    svc = merchant(conn, "Sample Bazaar", "Household", "Shopping")
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (svc,))
    conn.commit()
    txs = [row(conn, statement, "SAMPLE BAZAAR MKTPLACE", amount, book="Household", type_name="Shopping",
               service_id=svc, cat_source="service_default", day=f"2026-04-1{i}")
           for i, amount in enumerate((45.90, 219.00, 49.90))]
    return svc, txs


def test_case_7_confirm_all_with_one_line_changed_checks_every_row_in_one_change(client, conn, amazon):
    _, txs = amazon
    shopping, home = type_id(conn, "Shopping"), type_id(conn, "Home")
    lines = [{"tx_id": txs[0], "type_id": shopping, "book": "Household"},
             {"tx_id": txs[1], "type_id": home, "book": "Household"},
             {"tx_id": txs[2], "type_id": shopping, "book": "Household"}]
    before = len(entries(client))

    resp = client.post("/api/transactions/bulk-label", json={"rows": lines})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["labelled"] == 3
    assert len(entries(client)) == before + 1
    assert [label(client, tx) for tx in txs] == [
        ("Household", "Shopping", "manual"), ("Household", "Home", "manual"), ("Household", "Shopping", "manual")]
    assert waiting(client) == []

    undo(client, resp)
    assert [label(client, tx) for tx in txs] == [("Household", "Shopping", "service_default")] * 3
    assert waiting(client) == sorted(txs)


def test_case_8_one_line_checks_one_row_and_the_others_stay(client, conn, amazon):
    _, txs = amazon

    resp = client.post("/api/transactions/bulk-label",
                       json={"rows": [{"tx_id": txs[1], "type_id": type_id(conn, "Shopping"), "book": "Moom"}]})

    assert resp.status_code == 200, resp.get_json()
    assert label(client, txs[1]) == ("Moom", "Shopping", "manual")
    assert waiting(client) == sorted([txs[0], txs[2]])


def test_case_18_a_book_alone_keeps_each_rows_type(client, conn, statement, amazon):
    _, txs = amazon
    client.post("/api/transactions/bulk-label",
                json={"rows": [{"tx_id": txs[0], "type_id": type_id(conn, "Home")}]})

    resp = client.post("/api/transactions/bulk-label", json={"rows": [{"tx_id": tx, "book": "Moom"} for tx in txs]})

    assert resp.status_code == 200, resp.get_json()
    assert [label(client, tx) for tx in txs] == [
        ("Moom", "Home", "manual"), ("Moom", "Shopping", "manual"), ("Moom", "Shopping", "manual")]


@pytest.mark.parametrize("bad", [
    {"book": "No Such Book"},
    {"type_id": 99999},
    {"type_id": "Shopping"},
])
def test_case_20_bulk_label_refuses_whole_on_an_unknown_book_or_type_and_writes_nothing(client, conn, amazon, bad):
    _, txs = amazon
    lines = [{"tx_id": txs[0], "book": "Moom"}, {"tx_id": txs[1], **bad}, {"tx_id": txs[2], "book": "Moom"}]
    before = entries(client)

    resp = client.post("/api/transactions/bulk-label", json={"rows": lines})

    assert resp.status_code == 400
    assert resp.get_json()["error"]
    assert resp.headers.get("X-Fin-Change") is None
    assert entries(client) == before
    assert [label(client, tx) for tx in txs] == [("Household", "Shopping", "service_default")] * 3
    assert waiting(client) == sorted(txs)


def test_case_20_a_row_deleted_elsewhere_refuses_the_whole_confirm_and_says_why(client, conn, amazon):
    _, txs = amazon
    conn.execute("DELETE FROM transactions WHERE id = ?", (txs[1],))
    conn.commit()
    before = entries(client)

    resp = client.post("/api/transactions/bulk-label",
                       json={"rows": [{"tx_id": tx, "book": "Household"} for tx in txs]})

    assert resp.status_code == 400
    assert "no longer in fin" in resp.get_json()["error"]
    assert entries(client) == before
    assert waiting(client) == sorted([txs[0], txs[2]])


@pytest.mark.parametrize("body", [
    {}, {"rows": []}, {"rows": "all"}, {"rows": [{"book": "Moom"}]}, {"rows": [{"tx_id": "1"}]},
])
def test_bulk_label_refuses_a_body_it_does_not_understand(client, amazon, body):
    resp = client.post("/api/transactions/bulk-label", json=body)

    assert resp.status_code == 400
    assert resp.headers.get("X-Fin-Change") is None


def test_bulk_label_refuses_a_row_named_twice(client, amazon):
    _, txs = amazon

    resp = client.post("/api/transactions/bulk-label",
                       json={"rows": [{"tx_id": txs[0], "book": "Moom"}, {"tx_id": txs[0], "book": "Kalesh"}]})

    assert resp.status_code == 400
    assert label(client, txs[0])[2] == "service_default"


def test_lists_count_the_rows_the_offer_would_bring(client, conn, expedia):
    svc, _ = expedia

    listed = next(s for s in client.get("/api/services").get_json() if s["id"] == svc)

    assert listed["rows_to_check"] == 3
