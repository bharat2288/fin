"""The Queue's "Book each row" save (fin-surfaces 06, case 15).

A mixed merchant's rows sit on one card, one line per row with its own book
picker. The card adds no write route: Save sends today's per-row write
(`PUT /api/transactions/<id>` with the type shown and the book picked) for
each row in turn, and the toast's one Undo undoes every change, newest first.
These hold that, against the temporary database. Every merchant, figure and
name is invented.
"""

from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    label, merchant, row, statement, type_id,
)


def mixed_ids(client) -> list[int]:
    """The rows the Queue lists under Book each row."""
    payload = client.get("/api/transactions?look=mixed&per_page=500").get_json()
    return sorted(t["id"] for t in payload["transactions"])


def test_case_15_each_row_takes_its_own_book_one_change_each_and_one_undo_over_all(
    client, conn, statement
):
    shop = merchant(conn, "Sample Bazaar", "Household", "Shopping")
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (shop,))
    conn.commit()
    txs = [row(conn, statement, "SAMPLE BAZAAR MKTPLACE", amount, book="Household", type_name="Shopping",
               service_id=shop, cat_source="service_default", day=f"2026-04-1{i}")
           for i, amount in enumerate((45.90, 219.00, 49.90))]
    assert mixed_ids(client) == sorted(txs)

    books = ["Household", "Kalesh", "Moom"]
    changes = []
    for tx, book in zip(txs, books):
        resp = client.put(f"/api/transactions/{tx}", json={"type_id": type_id(conn, "Shopping"), "book": book})
        assert resp.status_code == 200, resp.get_json()
        changes.append(int(resp.headers["X-Fin-Change"]))

    assert len(set(changes)) == 3
    assert [label(client, tx) for tx in txs] == [(b, "Shopping", "manual") for b in books]
    assert mixed_ids(client) == []

    for change in sorted(changes, reverse=True):
        assert client.post(f"/api/history/{change}/undo").status_code == 200
    assert [label(client, tx) for tx in txs] == [("Household", "Shopping", "service_default")] * 3
    assert mixed_ids(client) == sorted(txs)


def test_a_refused_row_writes_nothing_and_leaves_the_rows_before_it_saved(client, conn, statement):
    shop = merchant(conn, "Sample Bazaar", "Household", "Shopping")
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (shop,))
    conn.commit()
    first, second = (row(conn, statement, "SAMPLE BAZAAR MKTPLACE", 20.0, book="Household", type_name="Shopping",
                         service_id=shop, cat_source="service_default", day=f"2026-04-1{i}") for i in range(2))
    shopping = type_id(conn, "Shopping")

    assert client.put(f"/api/transactions/{first}", json={"type_id": shopping, "book": "Kalesh"}).status_code == 200
    refused = client.put(f"/api/transactions/{second}", json={"type_id": shopping, "book": "No Such Book"})

    assert refused.status_code == 400
    assert refused.headers.get("X-Fin-Change") is None
    assert label(client, first) == ("Kalesh", "Shopping", "manual")
    assert label(client, second) == ("Household", "Shopping", "service_default")
    assert mixed_ids(client) == [second]
