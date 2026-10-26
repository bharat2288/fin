"""A month held from one kind of export, imported again from the other; and a
DBS account kept in two currencies.

A bank's CSV export and its PDF statement print the same rows in different
words: the CSV's "TOP-UP TO PAYLAH! : 90000001" is the statement's "Funds
Transfer TOP-UP TO PAYLAH! : 90000001", its "BILL DBSC-…" the statement's
"Advice Bill Payment DBSC-…". Each prints every row of the account's day, so
a row one holds on that day for that amount is the other's row: importing
the other adds nothing twice.

A DBS account may print a second section in another currency ("CURRENCY:
UNITED STATES DOLLAR") after its SGD rows. That section is an account of its
own, kept in that currency, "<number> USD"; an older read put its rows on the
SGD account with no balance to tell in from out by, so one may be held with
its sign turned. Importing the statement again moves them to the USD account.

Every name, number and figure is invented.
"""

import pdfplumber
import pytest

import parse_dbs
import parsers
import tie
from test_import_that_ties import confirm, stand_in, upload  # noqa: F401
from test_parse_hdfc import fake_open

BANK = "Sample Bank 100-000001-1"

CSV_ROWS = [
    ("2026-01-01", "TOP-UP TO PAYLAH! : 90000001 TF000000000000000001", 54000),
    ("2026-01-02", "BILL DBSC-0000000000000009 : I-BANK REF: 0000000001", 2000000),
]
PDF_ROWS = [
    ("2026-01-01", "Funds Transfer TOP-UP TO PAYLAH! : 90000001 TF000000000000000001", 54000),
    ("2026-01-02", "Advice Bill Payment DBSC-0000000000000009 : I-BANK REF: 0000000001", 2000000),
    ("2026-01-31", "Interest Earned", -10),
]


def rows(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "SELECT t.date, t.description, t.amount_minor, s.printed FROM transactions t "
        "JOIN statements s ON s.id = t.statement_id ORDER BY t.date, t.id"
    )]


def test_a_statement_over_a_month_held_from_csv_adds_only_what_the_csv_did_not_hold(client, conn, stand_in):
    stand_in(CSV_ROWS, account=BANK)
    confirm(client, upload(client))
    # 1,000.00 brought forward; 540.00 and 20,000.00 out, 0.10 in.
    stand_in(PDF_ROWS, 100000000, 100000000 - 54000 - 2000000 + 10, account=BANK,
             closing_date="2026-01-31", opening_date="2026-01-01")

    body = confirm(client, upload(client)).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"], body["rows_refiled"]) == (1, 2, 2)
    assert rows(conn) == [(*CSV_ROWS[0], 1), (*CSV_ROWS[1], 1), (*PDF_ROWS[2], 1)]


def test_a_csv_over_a_month_held_from_a_statement_adds_nothing(client, conn, stand_in):
    stand_in(PDF_ROWS, 100000000, 100000000 - 54000 - 2000000 + 10, account=BANK,
             closing_date="2026-01-31", opening_date="2026-01-01")
    confirm(client, upload(client))
    stand_in(CSV_ROWS, account=BANK)

    body = confirm(client, upload(client)).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"]) == (0, 2)
    assert [r[1] for r in rows(conn)] == [d for _, d, _ in PDF_ROWS]


def test_of_two_rows_alike_in_day_and_amount_the_one_the_csv_did_not_hold_is_added(client, conn, stand_in):
    stand_in([("2026-01-07", "GIRO TO :SAMPLE ONE REF:SI", 100000)], account=BANK)
    confirm(client, upload(client))
    stand_in([("2026-01-07", "GIRO Standing Instruction TO :SAMPLE ONE REF:SI", 100000),
              ("2026-01-07", "GIRO Standing Instruction TO :SAMPLE TWO REF:SI", 100000)],
             100000000, 100000000 - 200000, account=BANK, closing_date="2026-01-31", opening_date="2026-01-01")

    body = confirm(client, upload(client)).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"]) == (1, 1)
    assert len(rows(conn)) == 2


def test_a_csv_over_a_month_held_from_csv_still_goes_by_wording(client, conn, stand_in):
    stand_in([("2026-01-07", "SAMPLE SHOP", 500)], account=BANK)
    confirm(client, upload(client))
    stand_in([("2026-01-07", "OTHER SHOP", 500)], account=BANK)

    body = confirm(client, upload(client)).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"]) == (1, 0)


# --- an account kept in two currencies ---------------------------------------------------

PAGE = "\n".join([
    "Transaction Details",
    "as at 31 Jan 2025",
    "My Account Account No. 100-000001-1",
    "Date Description Withdrawal (-) Deposit (+) Balance",
    "CURRENCY: SINGAPORE DOLLAR",
    "Balance Brought Forward SGD 1,000.00",
    "31/01/2025 Interest Earned 0.95 1,000.95",
    "Total Balance Carried Forward in SGD: 0.00 0.95 1,000.95",
    "Date Description Withdrawal (-) Deposit (+) Balance",
    "CURRENCY: UNITED STATES DOLLAR",
    "Balance Brought Forward USD 0.70",
    "07/01/2025 Advice Remittance Transfer of Funds 5,000.00 5,000.70",
    "SAMPLE EXCHANGE",
    "08/01/2025 Advice Funds Transfer 5,000.00 0.70",
    "200-000002-2 : I-BANK",
    "SGD@ 0.7400000",
    "31/01/2025 Interest 0.25 0.95",
    "Total Balance Carried Forward in USD: 5,000.00 5,000.25 0.95",
    "Indicative in SGD @ 1.350000000 1.28",
    "Transaction Details as of 31 Jan 2025 Page 3 of 4",
])


def read(monkeypatch, pages):
    monkeypatch.setattr(pdfplumber, "open", fake_open(list(pages)))
    return parse_dbs.parse_statement(__file__)


def test_a_section_in_another_currency_is_its_own_account_kept_in_it(monkeypatch):
    sgd, usd = read(monkeypatch, (PAGE,))

    assert (sgd.accounts, sgd.currency, [(t.description, t.amount_minor) for t in sgd.transactions]) == (
        ["100-000001-1"], "SGD", [("Interest Earned", -95)])
    assert (usd.accounts, usd.currency, [(t.description, t.amount_minor) for t in usd.transactions]) == (
        ["100-000001-1 USD"], "USD", [
            ("Advice Remittance Transfer of Funds SAMPLE EXCHANGE", -500000),
            ("Advice Funds Transfer 200-000002-2 : I-BANK SGD@ 0.7400000", 500000),
            ("Interest", -25),
        ])
    assert (usd.opening_minor, usd.closing_minor, usd.closing_date) == (70, 95, "2025-01-31")
    assert all(tie.check(s)["difference_minor"] == 0 for s in (sgd, usd))


@pytest.fixture
def older_read(client, conn, stand_in):
    """The book as an older read left it: the USD section's rows on the SGD
    account, on a month record, as if Singapore dollars; the money in held
    as money out, the interest's wording run on into the total line, and the
    second transfer left out."""
    stand_in([
        ("2025-01-07", "Advice Remittance Transfer of Funds SAMPLE EXCHANGE", 500000),
        ("2025-01-31", "Interest Earned", -95),
        ("2025-01-31", "Interest Total Balance Carried Forward in USD: 5,000.00 5,000.25 0.95", -25),
    ], account=BANK)
    confirm(client, upload(client))
    parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]


def by_account(conn) -> dict:
    found = {}
    for r in conn.execute(
        "SELECT a.name, a.currency, t.date, t.description, t.amount_minor FROM transactions t "
        "JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id ORDER BY t.date, t.id"
    ):
        found.setdefault((r["name"], r["currency"]), []).append((r["date"], r["description"], r["amount_minor"]))
    return found


def test_importing_it_again_moves_the_usd_rows_to_a_usd_account_with_their_own_signs(
    client, conn, older_read, monkeypatch
):
    monkeypatch.setattr(pdfplumber, "open", fake_open([PAGE]))

    preview = upload(client, "2025-01.pdf")
    body = confirm(client, preview).get_json()

    assert [line["status"] for g in preview["groups"] for line in g["statements"]] == ["ties", "ties"]
    assert (body["transactions_saved"], body["duplicates_skipped"], body["rows_refiled"]) == (1, 3, 3)
    assert by_account(conn) == {
        (BANK, "SGD"): [("2025-01-31", "Interest Earned", -95)],
        ("100-000001-1 USD", "USD"): [
            ("2025-01-07", "Advice Remittance Transfer of Funds SAMPLE EXCHANGE", -500000),
            ("2025-01-08", "Advice Funds Transfer 200-000002-2 : I-BANK SGD@ 0.7400000", 500000),
            ("2025-01-31", "Interest", -25),
        ],
    }
    assert conn.execute("SELECT type FROM accounts WHERE name = '100-000001-1 USD'").fetchone()["type"] == "bank"


def test_importing_it_a_third_time_changes_nothing(client, conn, older_read, monkeypatch):
    monkeypatch.setattr(pdfplumber, "open", fake_open([PAGE]))
    confirm(client, upload(client, "2025-01.pdf"))
    before = by_account(conn)

    body = confirm(client, upload(client, "2025-01.pdf")).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"], body["rows_refiled"]) == (0, 4, 0)
    assert by_account(conn) == before


def test_a_row_of_the_sgd_account_is_never_taken_with_its_sign_turned(client, conn, stand_in, monkeypatch):
    # An SGD row held as money out on the day the USD section takes the
    # same figure in: it is the SGD account's, not a USD row to turn.
    stand_in([("2025-01-07", "Advice FAST Payment / Receipt SAMPLE PAYEE", 500000)], account=BANK)
    confirm(client, upload(client))
    parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    page = PAGE.replace(
        "Balance Brought Forward SGD 1,000.00\n31/01/2025 Interest Earned 0.95 1,000.95\n"
        "Total Balance Carried Forward in SGD: 0.00 0.95 1,000.95",
        "Balance Brought Forward SGD 10,000.00\n07/01/2025 Advice FAST Payment / Receipt 5,000.00 5,000.00\n"
        "SAMPLE PAYEE\n31/01/2025 Interest Earned 0.95 5,000.95\n"
        "Total Balance Carried Forward in SGD: 5,000.00 0.95 5,000.95",
    )
    assert page != PAGE
    monkeypatch.setattr(pdfplumber, "open", fake_open([page]))

    confirm(client, upload(client, "2025-01.pdf"))

    assert ("2025-01-07", "Advice FAST Payment / Receipt SAMPLE PAYEE", 500000) in by_account(conn)[(BANK, "SGD")]


def test_two_rows_alike_in_day_and_amount_each_pair_with_their_own_wording(client, conn, stand_in):
    stand_in([("2026-01-07", "GIRO TO :SAMPLE ONE REF:SI", 100000)], account=BANK)
    confirm(client, upload(client))
    stand_in([("2026-01-07", "GIRO Standing Instruction TO :SAMPLE TWO REF:ZZ", 100000),
              ("2026-01-07", "GIRO Standing Instruction TO :SAMPLE ONE REF:SI", 100000)],
             100000000, 100000000 - 200000, account=BANK, closing_date="2026-01-31", opening_date="2026-01-01")

    confirm(client, upload(client))

    assert sorted(r[1] for r in rows(conn)) == [
        "GIRO Standing Instruction TO :SAMPLE TWO REF:ZZ", "GIRO TO :SAMPLE ONE REF:SI"]


def test_a_row_held_with_its_sign_turned_is_read_again_and_loses_its_labels(client, conn, older_read, monkeypatch):
    conn.execute("UPDATE transactions SET flow_type = 'expense', flow_type_manual = 1, book = 'Household' "
                 "WHERE description LIKE 'Advice Remittance%'")
    conn.commit()
    monkeypatch.setattr(pdfplumber, "open", fake_open([PAGE]))

    confirm(client, upload(client, "2025-01.pdf"))

    row = conn.execute("SELECT amount_minor, flow_type, flow_type_manual, book FROM transactions "
                       "WHERE description LIKE 'Advice Remittance%'").fetchone()
    assert (row["amount_minor"], row["flow_type_manual"], row["book"]) == (-500000, 0, None)
    assert row["flow_type"] not in ("expense", "refund")


def test_a_new_sgd_row_on_the_day_of_a_misfiled_usd_row_is_saved_and_the_usd_row_moves(
    client, conn, older_read, monkeypatch
):
    page = PAGE.replace(
        "Balance Brought Forward SGD 1,000.00\n31/01/2025 Interest Earned 0.95 1,000.95\n"
        "Total Balance Carried Forward in SGD: 0.00 0.95 1,000.95",
        "Balance Brought Forward SGD 10,000.00\n07/01/2025 Advice FAST Payment / Receipt 5,000.00 5,000.00\n"
        "SAMPLE PAYEE\n31/01/2025 Interest Earned 0.95 5,000.95\n"
        "Total Balance Carried Forward in SGD: 5,000.00 0.95 5,000.95",
    )
    assert page != PAGE
    monkeypatch.setattr(pdfplumber, "open", fake_open([page]))

    confirm(client, upload(client, "2025-01.pdf"))

    found = by_account(conn)
    assert ("2025-01-07", "Advice FAST Payment / Receipt SAMPLE PAYEE", 500000) in found[(BANK, "SGD")]
    assert [r for r in found[("100-000001-1 USD", "USD")] if "Remittance" in r[1]] == [
        ("2025-01-07", "Advice Remittance Transfer of Funds SAMPLE EXCHANGE", -500000)]


def test_a_statement_of_two_sections_over_a_month_held_from_csv_adds_nothing_twice(
    client, conn, stand_in, monkeypatch
):
    stand_in([("2025-01-31", "INT EARNED", -95)], account=BANK)
    confirm(client, upload(client))
    parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    monkeypatch.setattr(pdfplumber, "open", fake_open([PAGE]))

    body = confirm(client, upload(client, "2025-01.pdf")).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"]) == (3, 1)
    assert by_account(conn)[(BANK, "SGD")] == [("2025-01-31", "INT EARNED", -95)]


def test_an_account_number_without_a_currency_is_never_its_usd_section(conn):
    import ingest
    usd = ingest.ensure_account(conn, "100-000001-1 USD", "bank", "USD")

    assert ingest.find_account(conn, "100-000001-1") is None
    assert ingest.find_account(conn, "100-000001-1", "SGD") is None
    sgd = ingest.ensure_account(conn, "100-000001-1", "bank", "SGD")
    assert sgd != usd
    assert (ingest.find_account(conn, "Sample 100-000001-1"), ingest.find_account(conn, "Sample 100-000001-1 USD")) == (
        sgd, usd)
