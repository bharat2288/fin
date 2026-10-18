"""Book prefill (fin-surfaces 10; spec 08, cases 6, 9-11 and 24).

A row's book picker is prefilled from, first match wins: the operator's last
hand-set choice for this merchant on this account; else a company-owned
account's company; else the type's book. `POST /api/book-prefill {tx_ids}`
reads it for many rows in one call and writes nothing. These hold that,
against the temporary database. Every account, merchant and figure is invented.
"""

import time

import pytest

from test_book_and_type_switch import merchant, row, type_id  # noqa: F401


def account(conn, name: str, owner: str = "Household", kind: str = "card") -> int:
    """A statement on a new account of that owner; returns the statement's id."""
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, owner) VALUES (?, ?, ?, ?)",
        (name, name, kind, owner),
    )
    account_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, '2026-09-30', ?)",
        (account_id, f"{name}.csv"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


@pytest.fixture
def cards(conn) -> dict:
    """Your card and Mili's card (Household), and the Moom card (Moom's own)."""
    return {
        "you": account(conn, "Sample Vantage 3696"),
        "mili": account(conn, "Sample Vantage 7436 (MK)"),
        "moom": account(conn, "Moom card", owner="Moom"),
    }


def prefill(client, *tx_ids) -> dict:
    resp = client.post("/api/book-prefill", json={"tx_ids": list(tx_ids)})
    assert resp.status_code == 200, resp.get_json()
    assert resp.headers.get("X-Fin-Change") is None
    return {int(k): v for k, v in resp.get_json()["prefill"].items()}


def changes(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0]


def test_case_9_a_company_card_prefills_its_company(client, conn, cards):
    air = merchant(conn, "Sample Air", "Household", "Travel")
    tx = row(conn, cards["moom"], "SAMPLE AIR 312-1", 1180.0, book="Household", type_name="Travel",
             service_id=air, cat_source="service_default")

    assert prefill(client, tx)[tx] == {
        "book": "Moom", "reason": "account", "account": "Moom card", "account_kind": "card"}


def test_case_10_no_history_on_a_household_card_gives_the_types_book(client, conn, cards):
    skin = merchant(conn, "Sample Skin", "Household", "Health & Beauty")
    tx = row(conn, cards["you"], "SAMPLE SKIN 1", 60.0, book="Household", type_name="Health & Beauty",
             service_id=skin, cat_source="service_default")
    ads = row(conn, cards["you"], "SAMPLE ADS 1", 90.0, type_name="Advertising")
    tool = row(conn, cards["you"], "SAMPLE TOOL 1", 20.0, type_name="Software & AI tools")

    got = prefill(client, tx, ads, tool)

    assert got[tx]["book"] == "Household" and got[tx]["reason"] == "type"
    assert got[ads]["book"] == "Moom" and got[ads]["reason"] == "type"
    assert got[tool] == {"book": None, "reason": "type", "account": "Sample Vantage 3696", "account_kind": "card"}


def test_a_row_with_no_type_takes_its_merchants_type_and_one_with_neither_has_no_book(client, conn, cards):
    air = merchant(conn, "Sample Air", "Household", "Travel")
    on_merchant = row(conn, cards["you"], "SAMPLE AIR 9", service_id=air)
    bare = row(conn, cards["you"], "SOMETHING NEW")

    got = prefill(client, on_merchant, bare)

    assert (got[on_merchant]["book"], got[on_merchant]["reason"]) == ("Household", "type")
    assert (got[bare]["book"], got[bare]["reason"]) == (None, "type")


def test_case_11_history_on_the_account_beats_the_account(client, conn, cards):
    skin = merchant(conn, "Sample Skin", "Household", "Health & Beauty")
    row(conn, cards["moom"], "SAMPLE SKIN 1", book="Household", type_name="Health & Beauty",
        service_id=skin, cat_source="manual", day="2026-08-02")
    nxt = row(conn, cards["moom"], "SAMPLE SKIN 2", book="Household", type_name="Health & Beauty",
              service_id=skin, cat_source="service_default", day="2026-09-02")

    assert prefill(client, nxt)[nxt] == {
        "book": "Household", "reason": "last", "account": "Moom card", "account_kind": "card"}


def test_case_6_the_last_choice_on_that_card_prefills_a_new_row(client, conn, cards):
    air = merchant(conn, "Sample Air", "Household", "Travel")
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (air,))
    conn.commit()
    # Mili's card went Moom last time; your card went Household; an older
    # Mili's card row went Kalesh, and a later one on her card is not hand-set.
    row(conn, cards["mili"], "SAMPLE AIR 1", book="Kalesh", type_name="Travel", service_id=air,
        cat_source="manual", day="2026-07-01")
    row(conn, cards["mili"], "SAMPLE AIR 2", book="Moom", type_name="Travel", service_id=air,
        cat_source="manual", day="2026-09-09")
    row(conn, cards["mili"], "SAMPLE AIR 3", book="Household", type_name="Travel", service_id=air,
        cat_source="service_default", day="2026-09-20")
    row(conn, cards["you"], "SAMPLE AIR 4", book="Household", type_name="Travel", service_id=air,
        cat_source="manual", day="2026-09-25")
    new = row(conn, cards["mili"], "SAMPLE AIR 312-1112223", 1180.0, book="Household", type_name="Travel",
              service_id=air, cat_source="service_default", day="2026-10-03")

    assert prefill(client, new)[new] == {
        "book": "Moom", "reason": "last", "account": "Sample Vantage 7436 (MK)", "account_kind": "card"}


def test_last_is_by_date_then_id_and_never_the_row_itself(client, conn, cards):
    air = merchant(conn, "Sample Air", "Household", "Travel")
    first = row(conn, cards["mili"], "SAMPLE AIR 1", book="Kalesh", type_name="Travel", service_id=air,
                cat_source="manual", day="2026-09-09")
    second = row(conn, cards["mili"], "SAMPLE AIR 2", book="Moom", type_name="Travel", service_id=air,
                 cat_source="manual", day="2026-09-09")

    got = prefill(client, first, second)

    # The same day: the later id is the last; each row reads the other, not itself.
    assert (got[first]["book"], got[first]["reason"]) == ("Moom", "last")
    assert (got[second]["book"], got[second]["reason"]) == ("Kalesh", "last")


def test_another_merchants_history_is_not_this_ones(client, conn, cards):
    air = merchant(conn, "Sample Air", "Household", "Travel")
    other = merchant(conn, "Other Air", "Household", "Travel")
    row(conn, cards["mili"], "OTHER AIR 1", book="Moom", type_name="Travel", service_id=other, cat_source="manual")
    tx = row(conn, cards["mili"], "SAMPLE AIR 1", book="Household", type_name="Travel", service_id=air)

    assert (prefill(client, tx)[tx]["reason"]) == "type"


def test_one_read_for_many_rows_writes_nothing(client, conn, cards):
    tool = row(conn, cards["moom"], "SAMPLE TOOL 1", type_name="Software & AI tools")
    before = changes(conn)

    got = prefill(client, tool, 999999)

    assert got == {tool: {"book": "Moom", "reason": "account", "account": "Moom card", "account_kind": "card"}}
    assert changes(conn) == before


@pytest.mark.parametrize("body", [None, {}, {"tx_ids": "1"}, {"tx_ids": [1, "x"]}, {"tx_ids": [True]}])
def test_a_body_without_a_list_of_row_ids_is_refused(client, body):
    resp = client.post("/api/book-prefill", json=body)
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_case_24_thirteen_hundred_rows_in_one_read(client, conn, cards):
    airs = [merchant(conn, f"Sample Air {n}", "Household", "Travel") for n in range(40)]
    ids = []
    for i in range(1300):
        svc = airs[i % len(airs)]
        statement = (cards["you"], cards["mili"], cards["moom"])[i % 3]
        ids.append(row(conn, statement, f"SAMPLE AIR {i}", type_name="Travel", service_id=svc,
                       book=("Moom" if i % 7 == 0 else "Household"),
                       cat_source=("manual" if i % 5 == 0 else "service_default"),
                       day=f"2026-{1 + i % 9:02d}-{1 + i % 28:02d}"))

    fresh = merchant(conn, "Fresh Air", "Household", "Travel")
    ids += [row(conn, cards[k], "FRESH AIR 1", type_name="Travel", service_id=fresh) for k in ("you", "moom")]

    started = time.perf_counter()
    got = prefill(client, *ids)
    took = time.perf_counter() - started

    assert set(got) == set(ids)
    assert {v["reason"] for v in got.values()} == {"last", "account", "type"}
    assert took < 2.0
    # The one read agrees with each row read on its own.
    for tx in ids[:60]:
        assert prefill(client, tx)[tx] == got[tx]
