"""A DBS statement that prints two accounts: each account's rows on its own
account, and an older read's mistakes put right by importing it again.

A BS statement prints the Multiplier account, then Home Reno, then SRS, one
after the other. An older read filed every row on the first account, as one
statement with no balance. Now each account with rows is its own statement,
with its own balances; an account with no rows (SRS) is left out.

Importing such a statement again over the older read's rows:
- a row the older read filed on the other account is moved to its own;
- a row whose wording an older read ran on into the page's footer or the
  next account's heading takes the wording printed.

The real DBS reader over synthetic page text, through the upload; every
name, number and figure is invented.
"""

import pdfplumber
import pytest

import app as fin_app
import parse_dbs
import parsers
import tie
from test_import_that_ties import confirm, stand_in, upload  # noqa: F401
from test_parse_hdfc import fake_open

PERSONAL = "Sample Personal 100-000001-1"
HOME_RENO = "Sample Home Reno"

PAGE = "\n".join([
    "Transaction Details",
    "as at 31 Aug 2026",
    "Sample Multiplier Account Account No. 100-000001-1",
    "Date Description Withdrawal (-) Deposit (+) Balance",
    "CURRENCY: SINGAPORE DOLLAR",
    "Balance Brought Forward SGD 1,000.00",
    "31/08/2026 Interest Earned 0.95 1,000.95",
    "Total Balance Carried Forward in SGD: 0.00 0.95 1,000.95",
    "My Account (Home Reno) Account No. 200-000002-2",
    "Date Description Withdrawal (-) Deposit (+) Balance",
    "CURRENCY: SINGAPORE DOLLAR",
    "Balance Brought Forward SGD 5,000.00",
    "07/08/2026 GIRO Standing Instruction 1,000.00 6,000.00",
    "BY :SAMPLE OWNER",
    "31/08/2026 Interest Earned 1.20 6,001.20",
    "Total Balance Carried Forward in SGD: 0.00 1,001.20 6,001.20",
    "Supplementary Retirement Scheme Account Account No. 0120-000000-0-000",
    "Date Description Withdrawal (-) Deposit (+) Balance (SGD)",
    "Balance Brought Forward 0.20",
    "Total Balance Carried Forward: 0.00 0.00 0.20",
    "Transaction Details as of 31 Aug 2026 Page 3 of 5",
])


def read(monkeypatch, pages):
    monkeypatch.setattr(pdfplumber, "open", fake_open(list(pages)))
    return parse_dbs.parse_statement(__file__)


def test_each_account_with_rows_is_its_own_statement_with_its_own_balances(monkeypatch):
    multiplier, home_reno = read(monkeypatch, (PAGE,))

    assert (multiplier.accounts, [(t.description, t.amount_minor) for t in multiplier.transactions]) == (
        ["100-000001-1"], [("Interest Earned", -95)])
    assert (multiplier.opening_minor, multiplier.closing_minor) == (100000, 100095)
    assert (home_reno.accounts, [(t.description, t.amount_minor) for t in home_reno.transactions]) == (
        ["200-000002-2"], [("GIRO Standing Instruction BY :SAMPLE OWNER", -100000), ("Interest Earned", -120)])
    assert (home_reno.opening_minor, home_reno.closing_minor, home_reno.closing_date) == (
        500000, 600120, "2026-08-31")
    assert all(tie.check(s)["difference_minor"] == 0 for s in (multiplier, home_reno))
    assert (multiplier.siblings, home_reno.siblings) == (["200-000002-2"], ["100-000001-1"])


def test_one_account_with_rows_beside_one_without_states_its_balances(monkeypatch):
    page = PAGE.replace("07/08/2026 GIRO Standing Instruction 1,000.00 6,000.00\nBY :SAMPLE OWNER\n", "").replace(
        "31/08/2026 Interest Earned 1.20 6,001.20\n", "")

    [stmt] = read(monkeypatch, (page,))

    assert stmt.accounts == ["100-000001-1"]
    assert (stmt.opening_minor, stmt.closing_minor) == (100000, 100095)
    assert tie.check(stmt)["difference_minor"] == 0


def test_an_account_number_an_import_creates_an_account_for_is_a_bank_account():
    assert fin_app._kind_from_account_name("200-000002-2") == "bank"


# --- importing it again over an older read ---------------------------------------------------


@pytest.fixture
def older_read(client, conn, stand_in):
    """The book as an older read left it: both accounts' rows on the
    Multiplier's account, on a month record, each account's last row run on
    into the page's footer or the next account's heading; Home Reno an
    account with no rows, its number's last four set."""
    stand_in([
        ("2026-08-31", "Interest Earned My Account (Home Reno) Account No. 200-000002-2 Date Description", -95),
        ("2026-08-31", "Interest Earned Supplementary Retirement Scheme Account Account No. 0120", -120),
    ], account=PERSONAL)
    confirm(client, upload(client))
    parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    conn.execute("INSERT INTO accounts (name, short_name, type, last_four) VALUES (?, ?, 'bank', '0022')",
                 (HOME_RENO, HOME_RENO))
    conn.commit()


def rows_by_account(conn) -> dict:
    found = {}
    for r in conn.execute(
        "SELECT a.name, t.date, t.description, t.amount_minor FROM transactions t "
        "JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id ORDER BY t.date, t.id"
    ):
        found.setdefault(r["name"], []).append((r["date"], r["description"], r["amount_minor"]))
    return found


def test_importing_it_again_moves_the_other_accounts_row_and_tidies_both_wordings(
    client, conn, older_read, monkeypatch
):
    monkeypatch.setattr(pdfplumber, "open", fake_open([PAGE]))

    preview = upload(client, "2026-08.pdf")
    resp = confirm(client, preview)

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert [line["status"] for g in preview["groups"] for line in g["statements"]] == ["ties", "ties"]
    assert (body["transactions_saved"], body["duplicates_skipped"]) == (1, 2)
    assert (body["rows_refiled"], body["wordings_tidied"]) == (2, 2)
    assert rows_by_account(conn) == {
        PERSONAL: [("2026-08-31", "Interest Earned", -95)],
        HOME_RENO: [("2026-08-07", "GIRO Standing Instruction BY :SAMPLE OWNER", -100000),
                    ("2026-08-31", "Interest Earned", -120)],
    }


def test_importing_it_a_third_time_changes_nothing(client, conn, older_read, monkeypatch):
    monkeypatch.setattr(pdfplumber, "open", fake_open([PAGE]))
    confirm(client, upload(client, "2026-08.pdf"))
    before = rows_by_account(conn)

    body = confirm(client, upload(client, "2026-08.pdf")).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"], body["wordings_tidied"]) == (0, 3, 0)
    assert rows_by_account(conn) == before


def test_a_csv_wording_longer_than_the_statements_keeps_its_own(client, conn, stand_in):
    card = "SAMPLE VISA CARD 0009"
    stand_in([("2026-08-03", "SAMPLE GROCER SINGAPORE SG", 14000)], account=card, kind="credit_card")
    confirm(client, upload(client))
    stand_in([("2026-08-03", "SAMPLE GROCER", 14000)], account=card, kind="credit_card")

    body = confirm(client, upload(client)).get_json()

    assert (body["duplicates_skipped"], body["wordings_tidied"]) == (1, 0)
    assert [r["description"] for r in conn.execute("SELECT description FROM transactions")] == [
        "SAMPLE GROCER SINGAPORE SG"]
