"""The "Jev is sure" card's save (fin-surfaces 05).

The card adds no write route: a ✓ is today's resolve with scope
`service_default`, the merchant name and pattern the screen derives, and the
book from the row's picker. These hold the figures the card relies on, driven
through `/api/transactions/resolve` and `/api/history` against the temporary
database. Every merchant, figure and name is invented.
"""

import pytest

from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    label, merchant_shown, row, shown, statement, type_id,
)


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def sure_save(conn, txs: list[int], merchant: str, pattern: str, type_name: str, book: str | None) -> dict:
    """The body a ✓ sends for a group: its first row, scope service_default,
    the pattern, and the picker's book (none when the picker is empty)."""
    body = {
        "tx_id": txs[0], "service_name": merchant, "type_id": type_id(conn, type_name),
        "apply_scope": "service_default", "pattern": pattern, "match_type": "contains",
        "suggestion_visible": True,
    }
    if book:
        body["book"] = book
    return body


def test_case_3_software_and_ai_tools_with_no_book_writes_nothing_and_with_moom_sets_the_merchants_book(
    client, conn, statement
):
    txs = [row(conn, statement, "SAMPLE *AICHAT", 20.0, day=f"2026-04-1{i}") for i in range(4)]
    before = entries(client)
    body = sure_save(conn, txs, "Sample *Aichat", "SAMPLE *AICHAT", "Software & AI tools", None)

    refused = client.post("/api/transactions/resolve", json=body)

    assert refused.status_code == 400
    assert "book" in refused.get_json()["error"]
    assert refused.headers.get("X-Fin-Change") is None
    assert entries(client) == before
    assert all(shown(client, tx)["type_id"] is None for tx in txs)
    assert not [s for s in client.get("/api/services").get_json() if s["name"] == "Sample *Aichat"]
    assert not [r for r in client.get("/api/rules").get_json() if r["pattern"] == "SAMPLE *AICHAT"]

    saved = client.post("/api/transactions/resolve", json={**body, "book": "Moom"})

    assert saved.status_code == 200, saved.get_json()
    made = merchant_shown(client, saved.get_json()["service_id"])
    assert (made["book"], made["display_type"]) == ("Moom", "Software & AI tools")
    assert all(label(client, tx) == ("Moom", "Software & AI tools", "service_default") for tx in txs)


@pytest.mark.parametrize("type_name, book", [
    ("Travel", "Household"),      # case 1: the type's own book, as prefilled
    ("Transport", "Kalesh"),      # case 4: a prefilled book the operator changed
])
def test_cases_1_and_4_one_save_labels_the_group_with_the_picked_book_and_one_undo_reverses_it(
    client, conn, statement, type_name, book
):
    txs = [row(conn, statement, "SAMPLE AIRWAYS", 61.5, day=f"2026-04-1{i}") for i in range(3)]

    saved = client.post("/api/transactions/resolve",
                        json=sure_save(conn, txs, "Sample Airways", "SAMPLE AIRWAYS", type_name, book))

    assert saved.status_code == 200, saved.get_json()
    assert set(saved.get_json()["backfilled_ids"]) >= set(txs[1:])
    assert all(label(client, tx) == (book, type_name, "service_default") for tx in txs)
    made = merchant_shown(client, saved.get_json()["service_id"])
    assert (made["book"], made["display_type"]) == (book, type_name)

    change = saved.headers["X-Fin-Change"]
    assert client.post(f"/api/history/{change}/undo").status_code == 200
    assert all(shown(client, tx)["type_id"] is None for tx in txs)
    assert not [s for s in client.get("/api/services").get_json() if s["name"] == "Sample Airways"]
