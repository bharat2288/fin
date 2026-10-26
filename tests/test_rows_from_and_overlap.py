"""Rows from a day, and two printed statements of one account over the same days.

An import may be told to leave out every row before a day ("rows from"): a
range statement that starts before the books do (HDFC's April-to-March) comes
in from that day only. The statement still ties: its opening becomes the
balance its own rows carry it to on the day before, and its period starts on
that day. A statement wholly before the day comes in not at all.

Two printed statements of one account may cover the same days in different
layouts (a month's statement and a range statement): one words a row
"NETBANK, 573573 MUM", the other "NETBANK,MUM". A row of the second is the
first's when the day, the amount and the letters are the same.

Every name, number and figure is invented.
"""

import io

import pytest

from test_import_that_ties import confirm, stand_in, upload  # noqa: F401

BANK = "Sample Savings 0001"

RANGE = [
    ("2025-12-20", "UPI-SAMPLE GROCER-OKAXIS", 150000),
    ("2025-12-28", "NEFT CR-SAMPLE EMPLOYER", -5000000),
    ("2026-01-05", "NETBANK, 573573 MUM", 2500000),
    ("2026-01-09", "ATW-000001XXXXXX0001-S1AN0001-MUMBAI", 1000000),
]
OPENING = 10000000  # ₹1,00,000.00 on 1 Dec 2025
CLOSING = OPENING - sum(amount for *_, amount in RANGE)


def upload_from(client, day: str | None, filename: str = "sample.csv"):
    data = {"files": (io.BytesIO(b"stand-in"), filename)}
    if day is not None:
        data["rows_from"] = day
    return client.post("/api/import/upload", data=data, content_type="multipart/form-data")


def held_rows(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "SELECT t.date, t.description, t.amount_minor FROM transactions t ORDER BY t.date, t.id"
    )]


def test_a_statement_over_the_day_comes_in_from_it_and_still_ties(client, conn, stand_in):
    stand_in(RANGE, OPENING, CLOSING, account=BANK, opening_date="2025-12-01", closing_date="2026-01-31")

    resp = upload_from(client, "2026-01-01")

    assert resp.status_code == 200, resp.get_json()
    preview = resp.get_json()
    (group,) = preview["groups"]
    assert [t["date"] for t in group["transactions"]] == ["2026-01-05", "2026-01-09"]
    (line,) = group["statements"]
    # ₹1,00,000 less ₹1,500 out plus ₹50,000 in: ₹1,48,500 on 31 Dec.
    assert (line["opening_minor"], line["opening_date"], line["closing_minor"]) == (14850000, "2026-01-01", CLOSING)
    assert group["tie"] == "ties"
    assert preview["rows_from"] == {"day": "2026-01-01", "rows": 2, "statements": 0}

    body = confirm(client, preview).get_json()

    assert body["transactions_saved"] == 2
    assert held_rows(conn) == RANGE[2:]
    assert conn.execute("SELECT period_start FROM statements").fetchone()[0] == "2026-01-01"


def test_a_statement_wholly_before_the_day_comes_in_not_at_all(client, conn, stand_in):
    stand_in(RANGE[:2], OPENING, OPENING - 150000 + 5000000, account=BANK,
             opening_date="2025-12-01", closing_date="2025-12-31")

    preview = upload_from(client, "2026-01-01").get_json()

    assert preview["groups"] == []
    assert preview["rows_from"] == {"day": "2026-01-01", "rows": 2, "statements": 1}
    confirm(client, preview)
    assert held_rows(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 0


def test_rows_with_no_balance_before_the_day_are_left_out(client, conn, stand_in):
    stand_in(RANGE, account=BANK)

    preview = upload_from(client, "2026-01-01").get_json()
    confirm(client, preview)

    assert held_rows(conn) == RANGE[2:]
    assert preview["rows_from"]["rows"] == 2


def test_with_no_day_every_row_comes_in(client, conn, stand_in):
    stand_in(RANGE, OPENING, CLOSING, account=BANK, opening_date="2025-12-01", closing_date="2026-01-31")

    preview = upload_from(client, None).get_json()
    confirm(client, preview)

    assert held_rows(conn) == RANGE
    assert preview["rows_from"] is None


@pytest.mark.parametrize("day", ["1 Jan 2026", "2026-13-01", "2026-1-1"])
def test_a_day_that_is_not_a_date_is_refused(client, conn, stand_in, day):
    stand_in(RANGE, account=BANK)

    resp = upload_from(client, day)

    assert resp.status_code == 400
    assert "rows from" in resp.get_json()["error"].lower()


MONTH = [
    ("2026-04-02", "NETBANK, 573573 MUM", 2500000),
    ("2026-04-02", "IMPS-612300000001-SAMPLE PERSON-HDFC-XXXXXXX0001-RENT", 4000000),
    ("2026-04-10", "UPI-SAMPLE CAFE-PAYTMQR1234@PAYTM-PYTM0123456-610000000001-UPI", 45000),
]
# The range statement's layout words the same rows apart from their digits
# and spacing, and carries one row the month statement did not hold.
RANGE_LAYOUT = [
    ("2026-04-02", "NETBANK,MUM", 2500000),
    ("2026-04-02", "IMPS-612300000002-SAMPLE PERSON-HDFC-XXXXXXX0001- RENT", 4000000),
    ("2026-04-10", "UPI-SAMPLE CAFE-PAYTMQR5678@PAYTM-PYTM0987654-610000000002-UPI", 45000),
    ("2026-04-10", "UPI-SAMPLE BAKERY-PAYTMQR1234@PAYTM-PYTM0123456-610000000003-UPI", 45000),
]


def test_a_second_printed_statement_over_the_same_days_adds_only_its_new_row(client, conn, stand_in):
    stand_in(MONTH, OPENING, OPENING - sum(a for *_, a in MONTH), account=BANK,
             opening_date="2026-04-01", closing_date="2026-04-30")
    confirm(client, upload(client))
    stand_in(RANGE_LAYOUT, OPENING, OPENING - sum(a for *_, a in RANGE_LAYOUT), account=BANK,
             opening_date="2026-04-01", closing_date="2026-09-30")

    body = confirm(client, upload(client)).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"]) == (1, 3)
    assert [d for _, d, _ in held_rows(conn)] == [
        MONTH[0][1], MONTH[1][1], MONTH[2][1], RANGE_LAYOUT[3][1],
    ]


def test_two_rows_alike_but_for_digits_on_one_day_each_claim_their_own_held_row(client, conn, stand_in):
    withdrawals = [
        ("2026-04-05", "ATW-000001XXXXXX0001-S1AN0001-MUMBAI", 1000000),
        ("2026-04-05", "ATW-000001XXXXXX0001-S1AN0002-MUMBAI", 1000000),
    ]
    stand_in(withdrawals, OPENING, OPENING - 2000000, account=BANK,
             opening_date="2026-04-01", closing_date="2026-04-30")
    confirm(client, upload(client))
    other_layout = [(day, description.replace("-S1AN", "- S1AN").replace("0001-", "1-"), amount)
                    for day, description, amount in withdrawals]
    stand_in(other_layout, OPENING, OPENING - 2000000, account=BANK,
             opening_date="2026-04-01", closing_date="2026-05-31")

    body = confirm(client, upload(client)).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"]) == (0, 2)
    assert len(held_rows(conn)) == 2
