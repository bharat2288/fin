"""DBS statements in the Wealth Management layout, and where a row's wording ends.

From September 2026 DBS sends a savings account and the car loan as a "Wealth
Management Consolidated Statement": a cover page with the address and "As of
30-SEP-2026", then "Your Banking Products At A Glance" (the summary), then
the same transaction details as before, each page framed by the relationship
manager's line above and the bank's address below. Both are read as before.

A row's wording is its own lines: it ends at the page's or the account's
carried-forward line, so a page footer or the bank's messages after an
account's last row are not part of that row.

No PDF is opened: pdfplumber is replaced and the page text is shaped like the
statement's own. Every name, number and figure is invented.
"""

import pytest

import parse_dbs
import parse_dbs_loan
import parsers
import tie
from test_parse_hdfc import fake_open

COVER = "\n".join([
    "SAMPLE HOLDER",
    "1 SAMPLE ROAD",
    "SINGAPORE 000001",
    "As of 30-SEP-2026",
])
SUMMARY = "\n".join([
    "Page: 01",
    "Relationship Manager: Sample Manager Deposits",
    "Your Banking Products At A Glance Statement as of: 30-SEP-2026",
    "Deposits",
    "Current and Savings Account",
    "Account Account No. Balance Balance",
    "Sample Savings Account 000-00000-1 SGD 1,410.25 1,410.25",
    "Total Balance in SGD Equivalent 1,410.25",
    "DBS Bank Ltd S/N: S-0000000000000000000000",
    "1 Sample Boulevard, Sample Tower, Singapore 000000 Co. Reg. No. 000000000E",
])
DETAILS_1 = "\n".join([
    "Page: 05",
    "Relationship Manager: Sample Manager",
    "Your Transaction Details Statement as of: 30-SEP-2026 Transaction Details",
    "Deposits",
    "Sample Savings Account Account No. 000-00000-1",
    "Date Description Withdrawal (-) Deposit (+) Balance (SGD)",
    "Balance Brought Forward 1,000.00",
    "01/09/2026 Salary 1,000.00 2,000.00",
    "SAMPLE EMPLOYER PTE LTD",
    "03/09/2026 Funds Transfer 600.00 1,400.00",
    "TOP-UP TO PAYLAH! :",
    "Balance Carried Forward 1,400.00",
    "DBS Bank Ltd S/N: S-0000000000000000000000",
    "1 Sample Boulevard, Sample Tower, Singapore 000000 Co. Reg. No. 000000000E",
])
DETAILS_2 = "\n".join([
    "Page: 06",
    "Relationship Manager: Sample Manager",
    "Your Transaction Details Statement as of: 30-SEP-2026 Transaction Details",
    "Sample Savings Account Account No. 000-00000-1",
    "Date Description Withdrawal (-) Deposit (+) Balance (SGD)",
    "Balance Brought Forward 1,400.00",
    "30/09/2026 Interest Earned 10.25 1,410.25",
    "4 4 4 4 4",
    "Total Balance Carried Forward: 600.00 1,010.25 1,410.25",
    "DBS Bank Ltd S/N: S-0000000000000000000000",
    "1 Sample Boulevard, Sample Tower, Singapore 000000 Co. Reg. No. 000000000E",
])
MESSAGES = "\n".join([
    "Page: 07",
    "Relationship Manager: Sample Manager Messages For You",
    "Messages For You",
    "l Sample notice about cheques.",
])


def read(monkeypatch, pages):
    monkeypatch.setattr(parse_dbs.pdfplumber, "open", fake_open(list(pages)))
    return parse_dbs.parse_statement(__file__)


def test_a_savings_account_in_the_wealth_layout_is_read_and_ties(monkeypatch):
    stmt = read(monkeypatch, (COVER, SUMMARY, DETAILS_1, DETAILS_2, MESSAGES))

    assert (stmt.statement_type, stmt.statement_date, stmt.accounts) == ("bank", "2026-09-30", ["000-00000-1"])
    assert [(t.date, t.description, t.amount_minor) for t in stmt.transactions] == [
        ("2026-09-01", "Salary SAMPLE EMPLOYER PTE LTD", -100000),
        ("2026-09-03", "Funds Transfer TOP-UP TO PAYLAH! :", 60000),
        ("2026-09-30", "Interest Earned", -1025),
    ]
    assert (stmt.opening_minor, stmt.closing_minor, stmt.closing_date) == (100000, 141025, "2026-09-30")
    assert tie.check(stmt)["difference_minor"] == 0


# --- the old layout: a row's wording ends at the carried-forward line -------------------

OLD_LAST_PAGE = "\n".join([
    "Transaction Details",
    "as at 30 Sep 2026",
    "Sample Savings Account Account No. 000-00000-2",
    "Date Description Withdrawal (-) Deposit (+) Balance",
    "Balance Brought Forward SGD 1,000.00",
    "30/09/2026 Interest Earned 0.25 1,000.25",
    "Total Balance Carried Forward in SGD: 0.00 0.25 1,000.25",
    "Transaction Details as of 30 Sep 2026 Page 2 of 3",
    ".oN geR ziB BSOP",
    "Messages For You",
    "l Sample notice about cheques.",
])


def test_the_page_footer_and_messages_after_an_accounts_last_row_are_not_its_wording(monkeypatch):
    stmt = read(monkeypatch, (OLD_LAST_PAGE,))

    assert [(t.description, t.amount_minor) for t in stmt.transactions] == [("Interest Earned", -25)]
    assert tie.check(stmt)["difference_minor"] == 0


# --- the car loan ---------------------------------------------------------------------------

LOAN_SUMMARY = "\n".join([
    "Page: 01",
    "Relationship Manager: Sample Manager Loans",
    "Your Banking Products At A Glance Statement as of: 30-SEP-2026",
    "Loans",
    "Auto Loan",
    "Vehicle No. Account No. Outstanding (SGD)",
    "SAB1234Z HPR000001X 50,344.00",
    "Total Outstanding Amount 50,344.00",
    "Total Loan Outstanding SGD 50,344.00",
    "DBS Bank Ltd S/N: S-0000000000000000000000",
])


@pytest.fixture
def read_loan(monkeypatch, tmp_path):
    """Read page text through the parser registry, as the upload does."""
    def reader(pages):
        path = tmp_path / "2026-09.pdf"
        path.write_bytes(b"stand-in")
        monkeypatch.setattr(parse_dbs_loan.pdfplumber, "open", fake_open(list(pages)))
        monkeypatch.setattr(parse_dbs.pdfplumber, "open", fake_open(list(pages)))
        return parsers.auto_detect_and_parse(str(path))
    return reader


def test_the_car_loan_in_the_wealth_layout_is_read_as_what_is_owed(read_loan):
    [loan] = read_loan((COVER, LOAN_SUMMARY, MESSAGES))

    assert isinstance(loan, parse_dbs_loan.LoanStatement)
    assert (loan.account, loan.statement_date, loan.owed_minor) == ("DBS auto loan", "2026-09-30", 5034400)


def test_a_wealth_layout_loan_total_that_disagrees_with_its_line_is_refused(read_loan):
    page = LOAN_SUMMARY.replace("SAB1234Z HPR000001X 50,344.00", "SAB1234Z HPR000001X 50,344.01")

    with pytest.raises(ValueError, match="does not add up"):
        read_loan((COVER, page, MESSAGES))


def test_a_row_that_runs_over_the_page_keeps_its_wording_from_the_next_page(monkeypatch):
    page_1 = DETAILS_1.replace("TOP-UP TO PAYLAH! :\n", "")
    page_2 = DETAILS_2.replace("Balance Brought Forward 1,400.00",
                               "Balance Brought Forward 1,400.00\nTOP-UP TO PAYLAH! :")

    stmt = read(monkeypatch, (COVER, SUMMARY, page_1, page_2, MESSAGES))

    assert [t.description for t in stmt.transactions] == [
        "Salary SAMPLE EMPLOYER PTE LTD", "Funds Transfer TOP-UP TO PAYLAH! :", "Interest Earned"]


def test_the_statement_date_is_the_statements_not_a_rate_dated_in_its_messages(monkeypatch):
    messages = MESSAGES + "\nExchange rates quoted are as at 1 Oct 2026."

    stmt = read(monkeypatch, (COVER, SUMMARY, DETAILS_1, DETAILS_2, messages))

    assert (stmt.statement_date, stmt.closing_date) == ("2026-09-30", "2026-09-30")


def test_a_car_loan_total_is_never_taken_from_another_loans_section(read_loan):
    page = LOAN_SUMMARY.replace("Total Outstanding Amount 50,344.00\n", "").replace(
        "Total Loan Outstanding SGD 50,344.00",
        "Home Loan\nProperty Account No. Outstanding (SGD)\nSAMPLE HLN000001X 400,000.00\n"
        "Total Outstanding Amount 400,000.00\nTotal Loan Outstanding SGD 450,344.00")

    with pytest.raises(ValueError, match="not a DBS account statement"):
        read_loan((COVER, page, MESSAGES))
