"""The sure card's rows and Something else… (fin-surfaces 09).

The card adds no route: a line's rows come from the Queue's read of rows with
no type, its name is the one resolve creates, and Something else… is today's
"This was…" over the line's rows. These hold the figures the card relies on,
driven through the HTTP interface against the temporary database. Every
merchant, figure and name is invented.
"""

import pytest

import suggest
from test_queue_guesses import answer, by_merchant, guesses, statement_on, type_id

DESC = ("SAMPLEAIR 312-0000001 TOWN", "SAMPLEAIR 312-0000002 TOWN")


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suggestions on, so the stored answers are read; the model is never asked."""
    monkeypatch.setenv(suggest.KEY_ENV, "not-a-real-key")

    def refuse(body, key):
        raise AssertionError("the sure card asked the model")

    monkeypatch.setattr(suggest, "post", refuse)


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def untyped(client) -> dict:
    """The rows with no type as the Queue reads them, by id."""
    payload = client.get("/api/transactions?types=__untyped__&per_page=500").get_json()
    return {t["id"]: t for t in payload["transactions"]}


def one(client, tx_id: int) -> dict:
    return client.get(f"/api/transactions?tx_id={tx_id}").get_json()["transactions"][0]


def add_row(conn, statement_id: int, description: str, amount_minor: int, day: str) -> int:
    tx = conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type)"
        " VALUES (?, ?, ?, ?, 'expense')",
        (statement_id, day, description, amount_minor),
    ).lastrowid
    conn.commit()
    return tx


@pytest.fixture
def line(conn) -> dict:
    """One sure line: two rows on two cards that Jev holds as one merchant,
    sure of Travel."""
    yours, milis = statement_on(conn, "card", "3696"), statement_on(conn, "card", "7436")
    txs = [add_row(conn, yours, DESC[0], 240000, "2026-09-02"),
           add_row(conn, milis, DESC[1], 323940, "2026-09-09")]
    merchant = suggest.merchant_string(DESC[0], "expense", "card")
    assert merchant == suggest.merchant_string(DESC[1], "expense", "card")
    answer(conn, merchant, "Travel", 0.96)
    return {"txs": txs, "merchant": merchant}


def sure_group(client, merchant: str) -> dict:
    return by_merchant(guesses(client)).get((merchant, "prefill"))


def test_case_1_a_lines_rows_carry_date_account_amount_and_text_and_reading_them_writes_nothing(
    client, conn, line
):
    before = entries(client)

    rows = untyped(client)

    got = [(rows[t]["date"], rows[t]["account_name"], rows[t]["amount_sgd"], rows[t]["description"]) for t in line["txs"]]
    assert got == [("2026-09-02", "Sample card 3696", 2400.0, DESC[0]),
                   ("2026-09-09", "Sample card 7436", 3239.4, DESC[1])]
    assert sorted(sure_group(client, line["merchant"])["tx_ids"]) == sorted(line["txs"])
    assert entries(client) == before


def test_case_2_the_save_creates_the_merchant_by_the_name_the_line_shows(client, conn, line):
    """The line shows resolve's name from the first row's text; ✓ sends it
    as the merchant, and that is the merchant made, whatever Jev calls it."""
    shown = "Sampleair 312-0000001 Town"   # titleCase(suggestPattern(first row's text))
    saved = client.post("/api/transactions/resolve", json={
        "tx_id": line["txs"][0], "service_name": shown, "type_id": type_id(conn, "Travel"),
        "apply_scope": "service_default", "pattern": DESC[0], "match_type": "contains",
        "suggestion_visible": True, "book": "Household"})

    assert saved.status_code == 200, saved.get_json()
    made = [s for s in client.get("/api/services").get_json() if s["id"] == saved.get_json()["service_id"]]
    assert [s["name"] for s in made] == [shown]
    assert line["merchant"] != shown


def test_case_3_something_else_a_move_to_my_own_account_labels_the_lines_rows_and_teaches_nothing(
    client, conn, line
):
    bank = conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four) VALUES ('Sample bank 0099', 'Bank-0099', 'bank', '0099')"
    ).lastrowid
    conn.commit()
    services_before = client.get("/api/services").get_json()
    rules_before = client.get("/api/rules").get_json()

    # "This was…" over the line's rows labels each row with the choice.
    changes = []
    for tx in line["txs"]:
        r = client.post(f"/api/review/{tx}/label", json={"choice": "own_account", "account_id": bank})
        assert r.status_code == 200, r.get_json()
        changes.append(r.headers["X-Fin-Change"])

    for tx in line["txs"]:
        assert (one(client, tx)["flow_type"], one(client, tx)["type_id"]) == ("transfer", None)
    assert not set(line["txs"]) & set(untyped(client))
    assert sure_group(client, line["merchant"]) is None          # the line leaves the card
    assert client.get("/api/services").get_json() == services_before   # not taught as Travel
    assert client.get("/api/rules").get_json() == rules_before

    # The toast's one Undo is both changes, newest first.
    for change in reversed(changes):
        assert client.post(f"/api/history/{change}/undo").status_code == 200
    assert set(line["txs"]) <= set(untyped(client))
    assert sorted(sure_group(client, line["merchant"])["tx_ids"]) == sorted(line["txs"])


def test_case_4_something_else_only_this_row_labels_one_row_and_the_other_stays_on_the_card(
    client, conn, line
):
    first, other = line["txs"]
    saved = client.post("/api/transactions/resolve", json={
        "tx_id": first, "service_name": "Sampleair 312-0000001 Town", "type_id": type_id(conn, "Kids"),
        "apply_scope": "transaction", "pattern": DESC[0], "match_type": "contains",
        "suggestion_visible": True})

    assert saved.status_code == 200, saved.get_json()
    assert one(client, first)["display_type"] == "Kids"
    assert other in untyped(client)
    group = sure_group(client, line["merchant"])
    assert group["tx_ids"] == [other]
    assert group["types"][0]["name"] == "Travel"
