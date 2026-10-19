"""fin suggests Check each row (fin-surfaces 14; spec 08, case 16).

Once the operator has moved two rows of an Every row the same merchant to
another book (hand-set to a book other than its own), fin asks whether to
check each row from now on. `GET /api/services/<id>/book-varies` answers
{suggest, moved, to_book}; a "No" is kept in `app_settings` (key
`check_each_row_declined`, a JSON list of merchant ids) and fin does not ask
again for that merchant. Driven through the Flask test client against the
temporary database. Every merchant, figure and name is invented.
"""

import json

import pytest

from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    merchant, row, statement, type_id,
)


def varies(client, svc: int) -> dict:
    resp = client.get(f"/api/services/{svc}/book-varies")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def move(client, tx: int, book: str):
    """One row's book changed on the merchant page (bulk label, hand-set)."""
    resp = client.post("/api/transactions/bulk-label", json={"rows": [{"tx_id": tx, "book": book}]})
    assert resp.status_code == 200, resp.get_json()
    return resp


@pytest.fixture
def rhode(conn, statement) -> tuple[int, list[int]]:
    """Every row the same, Health & Beauty · Household: 4 rows labelled from it."""
    svc = merchant(conn, "Sample Skin", "Household", "Health & Beauty")
    txs = [row(conn, statement, f"SAMPLE SKIN {i}", 40.0 + i, book="Household", type_name="Health & Beauty",
               service_id=svc, mark="service_default", day=f"2026-05-0{i + 1}") for i in range(4)]
    return svc, txs


def test_case_16_one_moved_row_is_not_enough_and_the_second_asks(client, rhode):
    svc, txs = rhode
    assert varies(client, svc) == {"suggest": False, "moved": 0, "to_book": None}

    move(client, txs[0], "Moom")
    assert varies(client, svc) == {"suggest": False, "moved": 1, "to_book": "Moom"}

    move(client, txs[1], "Moom")
    assert varies(client, svc) == {"suggest": True, "moved": 2, "to_book": "Moom"}


def test_case_16_this_was_only_this_row_counts_as_a_move(client, conn, statement, rhode):
    """"This was…" → spending, Only this row, on a row of the merchant with no type yet."""
    svc, txs = rhode
    move(client, txs[0], "Moom")
    untyped = row(conn, statement, "SAMPLE SKIN 9", 55.0, service_id=svc, day="2026-05-09")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": untyped, "service_id": svc, "service_name": "Sample Skin", "book": "Moom",
        "type_id": type_id(conn, "Health & Beauty"), "pattern": "SAMPLE SKIN", "apply_scope": "transaction",
    })

    assert resp.status_code == 200, resp.get_json()
    assert varies(client, svc) == {"suggest": True, "moved": 2, "to_book": "Moom"}


def test_only_hand_set_rows_in_another_book_count(client, conn, statement, rhode):
    """A row hand-set to the merchant's own book is not a move; nor is a row
    in another book that a rule labelled."""
    svc, txs = rhode
    move(client, txs[0], "Household")
    row(conn, statement, "SAMPLE SKIN STUDIO", 70.0, book="Moom", type_name="Health & Beauty",
        service_id=svc, mark="rule_override")
    move(client, txs[1], "Moom")

    assert varies(client, svc) == {"suggest": False, "moved": 1, "to_book": "Moom"}


def test_rows_moved_to_two_books_ask_without_naming_one(client, rhode):
    svc, txs = rhode
    move(client, txs[0], "Moom")
    move(client, txs[1], "Kalesh")

    assert varies(client, svc) == {"suggest": True, "moved": 2, "to_book": None}


def test_never_for_a_check_each_row_merchant(client, conn, rhode):
    svc, txs = rhode
    move(client, txs[0], "Moom")
    move(client, txs[1], "Moom")

    resp = client.post(f"/api/services/{svc}/check-each-row", json={"on": True, "past": "queue"})

    assert resp.status_code == 200, resp.get_json()
    assert varies(client, svc) == {"suggest": False, "moved": 2, "to_book": "Moom"}


def test_an_undone_move_takes_the_question_back(client, rhode):
    svc, txs = rhode
    move(client, txs[0], "Moom")
    second = move(client, txs[1], "Moom")
    assert varies(client, svc)["suggest"] is True

    assert client.post(f"/api/history/{second.headers['X-Fin-Change']}/undo").status_code == 200

    assert varies(client, svc) == {"suggest": False, "moved": 1, "to_book": "Moom"}


def test_case_16_no_is_kept_in_app_settings_and_not_asked_again(client, conn, rhode):
    svc, txs = rhode
    other = merchant(conn, "Sample Spa", "Household", "Health & Beauty")
    move(client, txs[0], "Moom")
    move(client, txs[1], "Moom")
    before = len(entries(client))

    resp = client.post(f"/api/services/{svc}/book-varies/decline")

    assert resp.status_code == 200, resp.get_json()
    assert varies(client, svc) == {"suggest": False, "moved": 2, "to_book": "Moom"}
    stored = conn.execute("SELECT value FROM app_settings WHERE key = 'check_each_row_declined'").fetchone()
    assert json.loads(stored[0]) == [svc]
    # Not a change to the book: nothing in the history to undo.
    assert len(entries(client)) == before

    # A third move does not ask again; a second No keeps one entry.
    move(client, txs[2], "Moom")
    assert varies(client, svc)["suggest"] is False
    assert client.post(f"/api/services/{svc}/book-varies/decline").status_code == 200
    assert client.post(f"/api/services/{other}/book-varies/decline").status_code == 200
    stored = conn.execute("SELECT value FROM app_settings WHERE key = 'check_each_row_declined'").fetchone()
    assert json.loads(stored[0]) == [svc, other]


def test_no_for_one_merchant_leaves_another_asked(client, conn, statement, rhode):
    svc, _ = rhode
    spa = merchant(conn, "Sample Spa", "Household", "Health & Beauty")
    spa_rows = [row(conn, statement, f"SAMPLE SPA {i}", 30.0, book="Household", type_name="Health & Beauty",
                    service_id=spa, mark="service_default") for i in range(2)]
    client.post(f"/api/services/{svc}/book-varies/decline")

    for tx in spa_rows:
        move(client, tx, "Moom")

    assert varies(client, spa)["suggest"] is True


def test_a_merchant_no_longer_in_fin(client):
    assert client.get("/api/services/9999/book-varies").status_code == 404
    assert client.post("/api/services/9999/book-varies/decline").status_code == 404
