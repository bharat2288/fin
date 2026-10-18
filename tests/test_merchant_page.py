"""The merchant page (fin-surfaces 11; spec 08, cases 17-19, 22, 23).

A Books view of one merchant: its facts (`GET /api/services/<id>`) and its
rows (the rows read filtered by `service_id`, newest first, paged), each with
a type and a book picker. Set book for ticked rows and one row's picker both
write through 12's bulk label: one change, undone whole. Driven through the
Flask test client against the temporary database. Every merchant, figure and
name is invented.
"""

import pytest

from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    label, merchant, row, rule, shown, statement, type_id,
)


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def rows_of(client, svc: int, **args) -> dict:
    query = "&".join(f"{k}={v}" for k, v in {"service_id": svc, "expense_only": "true", "sort": "date",
                                             "sort_dir": "desc", **args}.items())
    return client.get(f"/api/transactions?{query}").get_json()


@pytest.fixture
def indigoair(conn, statement) -> tuple[int, list[int]]:
    """Travel, usual book Household, Check each row: 4 rows, newest last
    inserted; one hand-set to Moom; one more row on another merchant."""
    svc = merchant(conn, "Sample Airways", "Household", "Travel")
    rule(conn, "SAMPLE AIRWAYS", svc)
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (svc,))
    conn.commit()
    txs = [row(conn, statement, f"SAMPLE AIRWAYS 312-{i}", 400.0 + i, book="Household", type_name="Travel",
               service_id=svc, cat_source="manual" if i < 2 else "service_default", day=f"2026-0{i + 6}-02")
           for i in range(4)]
    conn.execute("UPDATE transactions SET book = 'Moom' WHERE id = ?", (txs[1],))
    other = merchant(conn, "Sample Bazaar", "Household", "Shopping")
    row(conn, statement, "SAMPLE BAZAAR", 12.0, book="Household", type_name="Shopping", service_id=other)
    conn.commit()
    return svc, txs


def test_case_17_the_page_reads_the_merchant_its_setting_and_its_span(client, indigoair):
    svc, _ = indigoair

    got = client.get(f"/api/services/{svc}")

    assert got.status_code == 200
    m = got.get_json()
    assert (m["name"], m["display_type"], m["book"], m["review_each_time"]) == ("Sample Airways", "Travel", "Household", 1)
    assert (m["rows"], m["first_date"], m["last_date"], m["accounts"], m["rule_count"]) == (4, "2026-06-02", "2026-09-02", 1, 1)
    assert m["rows_to_check"] == 2
    assert [r["pattern"] for r in m["rules"]] == ["SAMPLE AIRWAYS"]


def test_case_17_its_rows_newest_first_each_with_its_marks(client, indigoair):
    svc, txs = indigoair

    payload = rows_of(client, svc)

    assert [t["id"] for t in payload["transactions"]] == list(reversed(txs))
    marks = [(t["book"], t["cat_source"], t["not_checked"]) for t in payload["transactions"]]
    assert marks == [("Household", "service_default", True), ("Household", "service_default", True),
                     ("Moom", "manual", False), ("Household", "manual", False)]


def test_its_rows_page_and_a_hidden_merchant_still_lists_them(client, conn, indigoair):
    svc, txs = indigoair
    conn.execute("UPDATE services SET exclude_from_expense_views = 1 WHERE id = ?", (svc,))
    conn.commit()

    first, second = rows_of(client, svc, per_page=3, page=1), rows_of(client, svc, per_page=3, page=2)

    assert (first["total"], first["pages"]) == (4, 2)
    assert [t["id"] for t in first["transactions"] + second["transactions"]] == list(reversed(txs))


def test_case_23_a_merchant_no_longer_in_fin_says_so(client, conn, indigoair):
    svc, _ = indigoair
    conn.execute("DELETE FROM transactions WHERE service_id = ?", (svc,))
    conn.execute("DELETE FROM merchant_rules WHERE service_id = ?", (svc,))
    conn.commit()
    assert client.delete(f"/api/services/{svc}").status_code == 200

    got = client.get(f"/api/services/{svc}")

    assert got.status_code == 404
    assert got.get_json()["error"] == "This merchant is no longer in fin"


def test_case_18_set_book_on_three_ticked_rows_is_one_change_undone_whole(client, indigoair):
    svc, txs = indigoair
    ticked = [txs[0], txs[2], txs[3]]
    before = len(entries(client))

    resp = client.post("/api/transactions/bulk-label", json={"rows": [{"tx_id": tx, "book": "Moom"} for tx in ticked]})

    assert resp.status_code == 200, resp.get_json()
    assert len(entries(client)) == before + 1
    assert [label(client, tx) for tx in ticked] == [("Moom", "Travel", "manual")] * 3
    assert client.get(f"/api/services/{svc}").get_json()["rows_to_check"] == 0

    assert client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo").status_code == 200
    assert [label(client, tx) for tx in ticked] == [
        ("Household", "Travel", "manual"), ("Household", "Travel", "service_default"), ("Household", "Travel", "service_default")]


def test_case_19_one_rows_picker_saves_that_row_hand_set_with_undo(client, conn, indigoair):
    _, txs = indigoair

    resp = client.post("/api/transactions/bulk-label",
                       json={"rows": [{"tx_id": txs[3], "type_id": type_id(conn, "Transport")}]})

    assert resp.status_code == 200, resp.get_json()
    assert label(client, txs[3]) == ("Household", "Transport", "manual")
    assert label(client, txs[2]) == ("Household", "Travel", "service_default")
    assert client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo").status_code == 200
    assert label(client, txs[3]) == ("Household", "Travel", "service_default")
