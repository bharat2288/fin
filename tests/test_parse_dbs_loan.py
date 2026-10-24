"""The DBS car-loan summary, and the DBS reader refusing a file it finds
nothing in.

DBS sends the car loan as a one-page "Consolidated Statement" whose Account
Summary lists only Loans: the total outstanding on the auto loan, and one line
per loan (vehicle, loan number, outstanding). It has no rows. fin reads what
is owed on the summary's date and nothing else.

No PDF is opened: pdfplumber is replaced and the page text is shaped like the
statement's own. Every name, number and figure is invented.
"""

import pytest

import parse_dbs
import parse_dbs_loan
import parsers
from test_parse_hdfc import fake_open

LOAN_PAGE = "\n".join([
    "S/N: EN00000000000001 Consolidated Statement",
    "SAMPLE HOLDER",
    "1 SAMPLE ROAD",
    "SINGAPORE 000001",
    "Account Summary",
    "as at 30 Apr 2026",
    "Loans",
    "Auto Loan Total Outstanding: SGD 52,340.00",
    "Vehicle No. Account No. Outstanding (SGD)",
    "SAB1234Z HPR000001X 52,340.00",
    "Account Summary as of 30 Apr 2026 Page 1 of 2",
    "PDS_SAMPLE_0001",
])
MESSAGES_PAGE = "\n".join([
    "Messages For You",
    "Please examine this statement.",
    "Page 2 of 2",
])

# A consolidated statement of a savings account: what the DBS bank reader takes.
BANK_PAGE = "\n".join([
    "Consolidated Statement",
    "Account Summary",
    "as at 31 May 2026",
    "Transaction Details",
    "Sample Savings Account Account No. 000-0-000001",
    "Date Description Withdrawal (-) Deposit (+) Balance",
    "01/05/2026 Balance Brought Forward SGD 1,000.00",
    "05/05/2026 SAMPLE GROCER 40.00 960.00",
    "31/05/2026 Balance Carried Forward SGD 960.00",
])


@pytest.fixture
def read(monkeypatch, tmp_path):
    """Read page text through the parser registry, as the upload does."""
    def reader(pages=(LOAN_PAGE, MESSAGES_PAGE)):
        path = tmp_path / "2026-04.pdf"
        path.write_bytes(b"stand-in")
        monkeypatch.setattr(parse_dbs_loan.pdfplumber, "open", fake_open(list(pages)))
        return parsers.auto_detect_and_parse(str(path))
    return reader


def test_a_car_loan_summary_is_read_as_what_is_owed_on_its_day_and_no_rows(read):
    [loan] = read()

    assert isinstance(loan, parse_dbs_loan.LoanStatement)
    assert (loan.account, loan.statement_date, loan.owed_minor, loan.currency) == (
        "DBS auto loan", "2026-04-30", 5234000, "SGD",
    )
    assert loan.filename == "2026-04.pdf"


def test_the_summary_names_the_loan_account_fin_keeps_for_the_car_loan():
    import flow

    assert parse_dbs_loan.ACCOUNT == dict(flow.LOAN_MERCHANTS)["Car Loan"]


def test_a_total_that_disagrees_with_its_loan_line_is_refused(read):
    page = LOAN_PAGE.replace("SAB1234Z HPR000001X 52,340.00", "SAB1234Z HPR000001X 52,340.01")

    with pytest.raises(ValueError, match="does not add up"):
        read((page, MESSAGES_PAGE))


def test_a_summary_with_two_car_loans_is_refused_as_fin_keeps_one(read):
    page = LOAN_PAGE.replace(
        "SAB1234Z HPR000001X 52,340.00",
        "SAB1234Z HPR000001X 50,000.00\nSAB5678Z HPR000002X 2,340.00",
    )

    with pytest.raises(ValueError, match="more than one car loan"):
        read((page, MESSAGES_PAGE))


def test_a_summary_with_no_date_is_refused(read):
    page = LOAN_PAGE.replace("as at 30 Apr 2026", "")

    with pytest.raises(ValueError, match="date"):
        read((page, MESSAGES_PAGE))


def test_a_consolidated_statement_with_account_rows_is_still_the_bank_readers(read):
    with_loan = BANK_PAGE + "\nLoans\nAuto Loan Total Outstanding: SGD 52,340.00"

    [stmt] = read((with_loan,))

    assert not parse_dbs_loan.detect_dbs_auto_loan_pdf("x.pdf")
    assert stmt.statement_type == "bank"
    assert [(tx.date, tx.amount_minor) for tx in stmt.transactions] == [("2026-05-05", 4000)]


# --- the DBS reader, the fallback for every other PDF -----------------------------


def test_a_statement_of_account_with_no_card_in_it_is_refused_not_read_as_an_empty_card(monkeypatch):
    page = "\n".join([
        "Statement of Account",
        "SAMPLE HOLDER",
        "STATEMENT DATE 30 Apr 2026",
        "Hire purchase SAMPLE",
        "Instalment 1,000.00",
    ])
    monkeypatch.setattr(parse_dbs.pdfplumber, "open", fake_open([page]))

    with pytest.raises(ValueError, match="no card"):
        parse_dbs.parse_statement(__file__)


def test_a_consolidated_statement_with_no_account_and_no_rows_is_refused(monkeypatch):
    page = "\n".join([
        "Consolidated Statement",
        "Account Summary",
        "as at 30 Apr 2026",
        "Investments",
        "Sample Fund 1,000.00",
    ])
    monkeypatch.setattr(parse_dbs.pdfplumber, "open", fake_open([page]))

    with pytest.raises(ValueError, match="no account"):
        parse_dbs.parse_statement(__file__)


def test_a_dbs_bank_statement_with_rows_reads_as_before(monkeypatch):
    monkeypatch.setattr(parse_dbs.pdfplumber, "open", fake_open([BANK_PAGE]))

    stmt = parse_dbs.parse_statement(__file__)

    assert stmt.accounts == ["000-0-000001"]
    assert (stmt.opening_minor, stmt.closing_minor) == (100000, 96000)
