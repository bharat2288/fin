"""stat-cards' missing card asks the trend's missing-month rule (S3) whether an
account should have sent a statement (fin-surfaces 43, slice N2).

stat-cards keeps its own account set (accounts that carried the book's rows
in the year before) and its own "no row in the month" condition; only the
"should have sent and not imported" comparison is S3's. The case S3 adds is
the current month: it is "so far", never missing. Home asks for the month
before, so Home reads as it did (tests/test_household_spending_cards.py holds
that case unchanged).

Driven through the HTTP interface against the temporary database. Every
figure, merchant and name here is invented.
"""

from datetime import date

import book_type


def _month(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _months_around_today() -> tuple[str, str]:
    today = date.today()
    current = _month(today.year, today.month)
    if today.month == 1:
        before = _month(today.year - 1, 12)
    else:
        before = _month(today.year, today.month - 1)
    return before, current


def _card_with_a_moom_row_in(client, conn, month: str) -> int:
    resp = client.post("/api/accounts", json={"name": "Sample Card 0043", "type": "card", "last_four": "0043"})
    assert resp.status_code == 200, resp.get_json()
    card_id = resp.get_json()["id"]
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, 'sample.csv')",
        (card_id, f"{month}-28"),
    )
    statement_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id, flow_type)"
        " VALUES (?, ?, 'SMPL ADS', 30010, 'Moom', ?, 'expense')",
        (statement_id, f"{month}-10", book_type.spending_type_ids(conn)["Advertising"]),
    )
    conn.commit()
    return card_id


def _cards(client, month: str) -> dict:
    resp = client.get(f"/api/dashboard/stat-cards?ref_month={month}")
    assert resp.status_code == 200
    return resp.get_json()


def test_the_current_month_is_so_far_and_never_missing(client, conn):
    before, current = _months_around_today()
    _card_with_a_moom_row_in(client, conn, before)

    # No Moom row this month and no statement for it yet: still not missing,
    # because the statement is not owed until the month is over.
    assert _cards(client, current)["moom_missing"] is False


def test_the_month_before_is_still_missing_when_its_statement_has_not_come(client, conn):
    before, current = _months_around_today()
    year, month = int(before[:4]), int(before[5:])
    earlier = _month(year - 1, 12) if month == 1 else _month(year, month - 1)
    _card_with_a_moom_row_in(client, conn, earlier)

    shown = _cards(client, before)
    assert shown["moom_missing"] is True
    assert shown["kalesh_missing"] is False
