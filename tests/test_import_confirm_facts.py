"""The import confirm takes choices, never facts.

The upload keeps its preview's facts on the server (batch_imports.result_json):
each group's account, currency and statement tie lines, and each row's date,
description, amounts, account and statement. The confirm writes those, with
only the request's choices per row (book, type, merchant, flow, other side,
skip, one-off, label source). A confirm that does not answer an open preview
is refused and writes nothing; so is one whose shape is not the preview's.
The upload credential (a service token) reaches the confirm, so it cannot
write a made-up row or anchor either.

Every name and figure is invented; every book is a temporary file.
"""

from __future__ import annotations

import asyncio
import io
import json

import httpx

import app as fin_app
from gate_tokens import book, composed, header, upload_token  # noqa: F401 (fixture)
from test_import_that_ties import (
    BANK,
    CLOSING,
    OPENING,
    ROWS,
    anchors_of,
    confirm,
    held,
    stand_in,  # noqa: F401 (fixture)
    stored_rows,
    upload,
)

EXPECTED_ROWS = sorted((on, description, amount) for on, description, amount in ROWS)


def _body(preview: dict, **overrides) -> dict:
    body = {
        "import_id": preview["import_id"],
        "groups": [
            {"account": g["account"], "currency": g["currency"], "transactions": g["transactions"],
             "statements": g["statements"]}
            for g in preview["groups"]
        ],
    }
    body.update(overrides)
    return body


def _forge(preview: dict) -> dict:
    """The preview with every fact a request could carry changed."""
    forged = json.loads(json.dumps(preview))
    [group] = forged["groups"]
    group["currency"] = "INR"
    group["statements"] = [{**line, "closing_minor": 1, "opening_minor": 1 + 14000}
                           for line in group["statements"]]
    for tx in group["transactions"]:
        tx["date"] = "2026-01-01"
        tx["description"] = "MADE UP ROW"
        tx["amount_sgd"] = 99999.99
        tx["amount_foreign"] = 1.0
        tx["currency_foreign"] = "USD"
        tx["account"] = "Sample Other Bank 0009"
        tx["statement"] = None
    return forged


# --- the facts are the stored preview's -----------------------------------------------


def test_a_confirm_writes_the_stored_facts_not_the_ones_it_sends(client, conn, stand_in):
    stand_in(ROWS, OPENING, CLOSING)
    preview = upload(client)

    resp = client.post("/api/import/confirm", json=_body(_forge(preview)))

    assert resp.status_code == 200, resp.get_json()
    assert sorted(stored_rows(conn)) == EXPECTED_ROWS
    assert [a["name"] for a in client.get("/api/accounts").get_json()] == [BANK]
    [account] = client.get("/api/accounts").get_json()
    assert account["currency"] == "SGD"
    assert [(a["date"], a["amount_minor"]) for a in anchors_of(client)] == [("2026-08-31", CLOSING)]
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE amount_foreign IS NOT NULL").fetchone()[0] == 0


def test_a_confirm_takes_each_rows_choices(client, conn, stand_in):
    stand_in(ROWS, OPENING, CLOSING)
    preview = upload(client)
    preview["groups"][0]["transactions"][0]["book"] = "Moom"
    preview["groups"][0]["transactions"][0]["is_one_off"] = True

    resp = confirm(client, preview)

    assert resp.status_code == 200, resp.get_json()
    chosen = conn.execute(
        "SELECT book, is_one_off FROM transactions WHERE description = ?", (ROWS[0][1],)
    ).fetchone()
    assert (chosen["book"], chosen["is_one_off"]) == ("Moom", 1)


def test_a_confirm_whose_shape_is_not_the_previews_is_refused_writing_nothing(client, conn, stand_in):
    before = held(conn)
    stand_in(ROWS, OPENING, CLOSING)
    preview = upload(client)
    body = _body(preview)
    group = body["groups"][0]

    doctored = [
        {**body, "groups": []},
        {**body, "groups": body["groups"] * 2},
        {**body, "groups": [{**group, "account": "Sample Other Bank 0009"}]},
        {**body, "groups": [{**group, "transactions": group["transactions"][:-1]}]},
        {**body, "groups": [{**group, "transactions": group["transactions"] + group["transactions"][:1]}]},
        {**body, "groups": [{**group, "transactions": ["a row"] * len(group["transactions"])}]},
        {**body, "groups": "all of them"},
    ]
    for sent in doctored:
        resp = client.post("/api/import/confirm", json=sent)
        assert resp.status_code == 400, sent
        assert resp.get_json() == {"error": fin_app.PREVIEW_MISMATCH}
    assert held(conn) == before
    # The preview is still open: the right shape still imports it.
    assert confirm(client, preview).status_code == 200


def test_an_unknown_import_is_refused_writing_nothing(client, conn, stand_in):
    before = held(conn)
    stand_in(ROWS, OPENING, CLOSING)
    preview = upload(client)

    for import_id in (preview["import_id"] + 1, None, "1", True, 0):
        resp = client.post("/api/import/confirm", json=_body(preview, import_id=import_id))
        assert resp.status_code == 409, import_id
        assert resp.get_json() == {"error": fin_app.PREVIEW_NOT_OPEN}
    assert held(conn) == before


def test_a_confirmed_preview_cannot_be_confirmed_again(client, conn, stand_in):
    stand_in(ROWS, OPENING, CLOSING)
    preview = upload(client)
    assert confirm(client, preview).status_code == 200
    after = held(conn)
    rows = stored_rows(conn)

    again = confirm(client, preview)

    assert again.status_code == 409
    assert again.get_json() == {"error": fin_app.PREVIEW_NOT_OPEN}
    assert held(conn) == after and stored_rows(conn) == rows


def test_a_failed_preview_cannot_be_confirmed(client, conn, stand_in):
    stand_in(ROWS, OPENING, CLOSING)
    preview = upload(client)
    conn.execute("UPDATE batch_imports SET status = 'failed' WHERE id = ?", (preview["import_id"],))
    conn.commit()
    before = held(conn)

    assert confirm(client, preview).status_code == 409
    assert held(conn) == before


def test_the_import_history_does_not_show_an_open_previews_facts(client, stand_in):
    stand_in(ROWS, OPENING, CLOSING)
    upload(client)

    [shown] = client.get("/api/import/history").get_json()

    assert shown["status"] == "preview"
    assert shown["result"] is None


# --- the upload credential ---------------------------------------------------------------


def _as_upload_client(asgi, preview_body_of):
    """Upload as the service token, then confirm what preview_body_of makes of
    the preview. Returns (upload response, confirm response)."""

    async def run():
        transport = httpx.ASGITransport(app=asgi)
        async with httpx.AsyncClient(transport=transport, base_url="http://fin.test") as client:
            token = header(upload_token())
            uploaded = await client.post(
                "/api/import/upload", headers=token,
                files={"files": ("sample.csv", io.BytesIO(b"stand-in"), "text/csv")},
            )
            confirmed = await client.post(
                "/api/import/confirm", headers=token, json=preview_body_of(uploaded.json()),
            )
            return uploaded, confirmed

    return asyncio.run(run())


def _ledger(db_path) -> list[tuple]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        return sorted(conn.execute("SELECT date, description, amount_minor FROM transactions").fetchall())
    finally:
        conn.close()


def test_the_upload_credential_cannot_write_a_forged_row(book, stand_in):
    stand_in(ROWS, OPENING, CLOSING)

    uploaded, confirmed = _as_upload_client(composed(), lambda p: _body(_forge(p)))

    assert uploaded.status_code == 200, uploaded.text
    assert confirmed.status_code == 200, confirmed.text
    assert _ledger(book) == EXPECTED_ROWS


def test_the_upload_credential_cannot_confirm_an_import_it_did_not_upload(book, stand_in):
    stand_in(ROWS, OPENING, CLOSING)

    _uploaded, confirmed = _as_upload_client(
        composed(), lambda p: _body(_forge(p), import_id=p["import_id"] + 7),
    )

    assert confirmed.status_code == 409
    assert confirmed.json() == {"error": fin_app.PREVIEW_NOT_OPEN}
    assert _ledger(book) == []

