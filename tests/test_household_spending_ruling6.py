"""Household spending counts household-owned accounts only, hidden merchants
and archived accounts included (ruling 6): hiding a merchant or archiving an
account changes what is shown, never the figure, and the dashboard and the
month check state the same figure.

Through the HTTP interface against a temporary database; rows and statement
balances are seeded with SQL as whole cents. Every name and figure is
invented."""

import pytest

from test_household_spending_cards import cents, make_account, row, statement

MONTH = "2026-08"
VISIBLE, HIDDEN, ON_KALESH = 10_000, 5_000, 7_000


@pytest.fixture
def seeded(client, conn):
    card = make_account(client, "Sample Card 0001", "card")
    kalesh_card = make_account(client, "Sample Kalesh Card 0009", "card", owner="Kalesh")
    stmt = statement(conn, card)
    row(conn, stmt, "SAMPLE GROCER", VISIBLE, type_name="Groceries")
    hidden = row(conn, stmt, "SAMPLE HIDDEN SHOP", HIDDEN, type_name="Groceries")
    conn.execute("INSERT INTO services (name, exclude_from_expense_views) VALUES ('Hidden Shop', 1)")
    conn.execute("UPDATE transactions SET service_id = last_insert_rowid() WHERE id = ?", (hidden,))
    # A row nobody gave a book, on a card Kalesh owns: not the household's money.
    row(conn, statement(conn, kalesh_card), "SAMPLE KALESH LUNCH", ON_KALESH, type_name="Groceries")
    # The household card's balance at both month-ends, so it is in the check.
    conn.executemany(
        "INSERT INTO anchors (account_id, date, amount, source) VALUES (?, ?, ?, 'statement')",
        [(card, "2026-07-31", -50_000), (card, "2026-08-31", -50_000 - VISIBLE - HIDDEN)],
    )
    conn.commit()
    return {"card": card}


def household_card(client) -> int:
    return cents(client.get(f"/api/dashboard/stat-cards?ref_month={MONTH}").get_json()["household"])


def household_charts(client) -> tuple[int, int]:
    query = f"start={MONTH}-01&end={MONTH}-31&book=Household"
    monthly = client.get(f"/api/dashboard/monthly?{query}").get_json()
    types = client.get(f"/api/dashboard/types?{query}").get_json()
    return (
        sum(cents(v) for v in monthly.get(MONTH, {}).values()),
        sum(cents(t["total"]) for t in types),
    )


def month_check_spending(client) -> int:
    return client.get(f"/api/balance-sheet?month={MONTH}").get_json()["month_check"]["spending_minor"]


def test_household_spending_is_household_owned_accounts_hidden_merchants_included(client, seeded):
    assert household_card(client) == VISIBLE + HIDDEN == 15_000
    assert household_charts(client) == (15_000, 15_000)


def test_the_dashboard_and_the_month_check_state_the_same_figure(client, seeded):
    assert month_check_spending(client) == household_card(client) == 15_000
    assert client.get(f"/api/balance-sheet?month={MONTH}").get_json()["month_check"]["unexplained_minor"] == 0


def test_archiving_the_account_changes_no_figure(client, seeded):
    client.put(f"/api/accounts/{seeded['card']}", json={"status": "archived"})

    assert household_card(client) == 15_000
    assert household_charts(client) == (15_000, 15_000)
    assert month_check_spending(client) == 15_000
    shown = client.get(f"/api/balance-sheet?month={MONTH}").get_json()
    assert shown["net_worth_minor"] == -65_000
    assert shown["month_check"]["unexplained_minor"] == 0


def test_a_hidden_merchant_is_still_left_off_the_list(client, seeded):
    listed = client.get(f"/api/transactions?per_page=500&start={MONTH}-01").get_json()["transactions"]
    assert "SAMPLE HIDDEN SHOP" not in [t["description"] for t in listed]


def test_the_review_list_hides_no_merchant_so_it_matches_its_count(client, conn, seeded):
    stmt = conn.execute("SELECT id FROM statements WHERE account_id = ?", (seeded["card"],)).fetchone()[0]
    waiting = row(conn, stmt, "SAMPLE HIDDEN SHOP TRANSFER", 2_500, flow="review")
    conn.execute("UPDATE transactions SET service_id = (SELECT id FROM services WHERE name = 'Hidden Shop')"
                 " WHERE id = ?", (waiting,))
    conn.commit()

    listed = client.get("/api/transactions?per_page=500&flow=review").get_json()["transactions"]

    assert [t["id"] for t in listed] == [waiting]
    assert client.get("/api/review").get_json()["waiting"] == 1
