"""The HDFC savings statement parser, on synthetic page text.

No PDF is on disk and none is opened: pdfplumber is replaced, and the text is
shaped like the statement's own (a header line, rows dated dd/mm/yy that each
carry a closing balance, a closing summary). Every name, number and figure is
invented, and so is the password. Amounts are whole paise; a row's amount is
positive for money out.
"""

import pytest
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException

import parse_hdfc
import tie

HEADER = "Date Narration Chq./Ref.No. ValueDt WithdrawalAmt. DepositAmt. ClosingBalance"

PAGE_ONE = "\n".join([
    "HDFC BANK LIMITED",
    "SAMPLE HOLDER",
    "Account No : 00000000001234",
    "Statement of account",
    "From : 01/04/2026 To : 30/06/2026",
    HEADER,
    "02/04/26 UPI-SAMPLE GROCER-PAYMENT 0000000000000001 02/04/26 1,500.00 98,500.00",
    "15/04/26 NEFT CR-SAMPLE EMPLOYER 0000000000000002 15/04/26 25,000.50 1,23,500.50",
    "FROM SAMPLE EMPLOYER PVT",
    "Page No .: 1",
])

PAGE_TWO = "\n".join([
    "HDFC BANK LIMITED",
    HEADER,
    "01/06/26 INTEREST PAID TILL 31-MAY-2026 0000000000000003 31/05/26 310.25 1,23,810.75",
    "STATEMENT SUMMARY :-",
    "Opening Balance Dr Count Cr Count Debits Credits Closing Bal",
    "1,00,000.00 1 2 1,500.00 25,310.75 1,23,810.75",
    "Generated On: 01-Jul-2026",
])

SECRET = "invented-pass-phrase"


class FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self, **_kwargs):
        return self._text


class FakePdf:
    def __init__(self, pages):
        self.pages = [FakePage(text) for text in pages]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def close(self):
        pass


def fake_open(pages, *, locked_with: str | None = None, calls: list | None = None):
    """A stand-in for pdfplumber.open. With `locked_with` the file opens only
    when that password is given, and fails the way an encrypted PDF does."""

    def opener(_path, password=None, **_kwargs):
        if calls is not None:
            calls.append(password)
        if locked_with is not None and password != locked_with:
            raise PdfminerException(PDFPasswordIncorrect())
        return FakePdf(pages)

    return opener


def read(monkeypatch, pages=(PAGE_ONE, PAGE_TWO), **kwargs):
    monkeypatch.setattr(parse_hdfc.pdfplumber, "open", fake_open(list(pages), **kwargs))
    return parse_hdfc.parse_hdfc_pdf("synthetic.pdf")


@pytest.fixture(autouse=True)
def no_password_set(monkeypatch):
    monkeypatch.delenv(parse_hdfc.PASSWORD_VARIABLE, raising=False)


# --- reading the statement ------------------------------------------------------


def test_rows_are_read_in_paise_with_money_out_positive(monkeypatch):
    stmt = read(monkeypatch)

    assert [(tx.date, tx.description, tx.amount_minor) for tx in stmt.transactions] == [
        ("2026-04-02", "UPI-SAMPLE GROCER-PAYMENT", 150000),
        ("2026-04-15", "NEFT CR-SAMPLE EMPLOYER FROM SAMPLE EMPLOYER PVT", -2500050),
        ("2026-06-01", "INTEREST PAID TILL 31-MAY-2026", -31025),
    ]


# HDFC prints the column header on the first page only: later pages carry the
# rows on from their top, and a narration cut by the page break runs on there.
MULTI_PAGE = [
    "\n".join([
        "HDFC BANK LIMITED",
        "SAMPLE HOLDER",
        "Account No : 00000000001234",
        "From : 01/04/2026 To : 30/06/2026",
        HEADER,
        "02/04/26 UPI-SAMPLE GROCER-PAYMENT 0000000000000001 02/04/26 1,500.00 98,500.00",
        "15/04/26 NEFT CR-SAMPLE EMPLOYER 0000000000000002 15/04/26 25,000.50 1,23,500.50",
        "Page No .: 1",
    ]),
    "\n".join([
        "Page No .: 2",
        "FROM SAMPLE EMPLOYER PVT",
        "03/05/26 UPI-SAMPLE PHARMACY 0000000000000004 03/05/26 200.00 1,23,300.50",
        "HDFC BANK LIMITED",
        "*Closing balance includes funds earmarked for hold and uncleared funds",
    ]),
    "\n".join([
        "01/06/26 INTEREST PAID TILL 31-MAY-2026 0000000000000003 31/05/26 310.25 1,23,610.75",
        "STATEMENT SUMMARY :-",
        "Opening Balance Dr Count Cr Count Debits Credits Closing Bal",
        "1,00,000.00 2 2 1,700.00 25,310.75 1,23,610.75",
    ]),
]


def test_rows_on_pages_without_the_header_are_read_and_a_narration_runs_across_the_break(monkeypatch):
    stmt = read(monkeypatch, MULTI_PAGE)

    assert [(tx.date, tx.description, tx.amount_minor) for tx in stmt.transactions] == [
        ("2026-04-02", "UPI-SAMPLE GROCER-PAYMENT", 150000),
        ("2026-04-15", "NEFT CR-SAMPLE EMPLOYER FROM SAMPLE EMPLOYER PVT", -2500050),
        ("2026-05-03", "UPI-SAMPLE PHARMACY", 20000),
        ("2026-06-01", "INTEREST PAID TILL 31-MAY-2026", -31025),
    ]
    assert tie.check(stmt)["difference_minor"] == 0


def test_a_page_before_the_header_is_still_not_read_as_rows(monkeypatch):
    cover = "SAMPLE COVER LETTER\n01/01/26 NOT A ROW 0000000000000009 01/01/26 1.00 2.00"
    stmt = read(monkeypatch, [cover, *MULTI_PAGE])

    assert len(stmt.transactions) == 4


def test_the_statement_is_a_rupee_bank_account_named_by_its_last_four(monkeypatch):
    stmt = read(monkeypatch)

    assert stmt.statement_type == "bank"
    assert stmt.currency == "INR"
    assert stmt.accounts == ["HDFC Bank Savings 1234"]
    assert {tx.card_info for tx in stmt.transactions} == {"HDFC Bank Savings 1234"}
    assert stmt.statement_date == "2026-06-30"


def test_it_hands_over_opening_and_closing_balances_and_the_shared_check_ties(monkeypatch):
    stmt = read(monkeypatch)

    assert (stmt.opening_minor, stmt.closing_minor) == (10000000, 12381075)
    assert (stmt.opening_date, stmt.closing_date) == ("2026-04-01", "2026-06-30")
    figures = tie.check(stmt)
    assert figures["difference_minor"] == 0
    # Opening plus what the rows make of it is the closing balance, to the paisa.
    assert 10000000 - (150000 - 2500050 - 31025) == 12381075
    assert figures["rows_minor"] == 2381075 and figures["rows"] == 3


def test_a_statement_with_no_rows_still_states_its_balances(monkeypatch):
    page = "\n".join([
        "HDFC BANK LIMITED",
        "Account No : 00000000001234",
        "From : 01/07/2026 To : 30/09/2026",
        HEADER,
        "STATEMENT SUMMARY :-",
        "Opening Balance Dr Count Cr Count Debits Credits Closing Bal",
        "1,23,810.75 0 0 0.00 0.00 1,23,810.75",
    ])
    stmt = read(monkeypatch, [page])

    assert stmt.transactions == []
    assert (stmt.opening_minor, stmt.closing_minor) == (12381075, 12381075)
    assert tie.check(stmt)["difference_minor"] == 0


def test_a_row_that_disagrees_with_its_closing_balance_is_refused(monkeypatch):
    wrong = PAGE_ONE.replace("1,500.00 98,500.00", "1,500.00 98,400.00")

    with pytest.raises(ValueError, match="closing balance"):
        read(monkeypatch, [wrong, PAGE_TWO])


def test_rows_that_disagree_with_the_summary_totals_are_refused(monkeypatch):
    wrong = PAGE_TWO.replace("1 2 1,500.00 25,310.75", "1 2 1,500.00 25,310.76")

    with pytest.raises(ValueError, match="summary"):
        read(monkeypatch, [PAGE_ONE, wrong])


def test_a_statement_with_no_summary_is_refused(monkeypatch):
    with pytest.raises(ValueError, match="summary"):
        read(monkeypatch, [PAGE_ONE])


def test_the_detector_knows_the_statement_and_leaves_others_alone(monkeypatch):
    monkeypatch.setattr(parse_hdfc.pdfplumber, "open", fake_open([PAGE_ONE, PAGE_TWO]))
    assert parse_hdfc.detect_hdfc_pdf("synthetic.pdf") is True

    monkeypatch.setattr(parse_hdfc.pdfplumber, "open", fake_open(["SAMPLE BANK\nStatement of Account"]))
    assert parse_hdfc.detect_hdfc_pdf("synthetic.pdf") is False


# --- the password ---------------------------------------------------------------


def test_a_file_that_opens_with_no_password_is_never_given_one(monkeypatch):
    """Handing the password to a file that needs none makes it fail to open."""
    monkeypatch.setenv(parse_hdfc.PASSWORD_VARIABLE, SECRET)
    calls = []

    stmt = read(monkeypatch, calls=calls)

    assert calls == [None]
    assert len(stmt.transactions) == 3


def test_a_locked_file_is_opened_with_the_password_from_the_environment(monkeypatch):
    monkeypatch.setenv(parse_hdfc.PASSWORD_VARIABLE, SECRET)
    calls = []

    stmt = read(monkeypatch, locked_with=SECRET, calls=calls)

    assert calls == [None, SECRET]
    assert stmt.closing_minor == 12381075


def test_a_locked_file_with_no_password_set_fails_closed_naming_the_variable(monkeypatch):
    with pytest.raises(ValueError) as refused:
        read(monkeypatch, locked_with=SECRET)

    assert "FIN_HDFC_PDF_PASSWORD" in str(refused.value)
    assert parse_hdfc.PASSWORD_VARIABLE == "FIN_HDFC_PDF_PASSWORD"


def test_an_empty_password_counts_as_none_set(monkeypatch):
    monkeypatch.setenv(parse_hdfc.PASSWORD_VARIABLE, "")
    calls = []

    with pytest.raises(ValueError, match="FIN_HDFC_PDF_PASSWORD"):
        read(monkeypatch, locked_with=SECRET, calls=calls)
    assert calls == [None]


def test_an_error_while_opening_never_carries_the_password(monkeypatch):
    """Neither in its message nor in the exceptions chained to it."""
    monkeypatch.setenv(parse_hdfc.PASSWORD_VARIABLE, SECRET)

    def opener(_path, password=None, **_kwargs):
        if password is None:
            raise PdfminerException(PDFPasswordIncorrect())
        raise RuntimeError(f"could not decrypt with {password}")

    monkeypatch.setattr(parse_hdfc.pdfplumber, "open", opener)
    with pytest.raises(ValueError) as refused:
        parse_hdfc.parse_hdfc_pdf("synthetic.pdf")

    error = refused.value
    assert SECRET not in str(error) and SECRET not in repr(error)
    assert error.__cause__ is None and error.__context__ is None
    assert "FIN_HDFC_PDF_PASSWORD" in str(error)


def test_a_locked_file_is_claimed_by_the_detector_so_the_refusal_is_the_parsers(monkeypatch):
    monkeypatch.setattr(parse_hdfc.pdfplumber, "open", fake_open([PAGE_ONE], locked_with=SECRET))

    assert parse_hdfc.detect_hdfc_pdf("synthetic.pdf") is True


def test_no_copy_of_the_file_is_written(monkeypatch, tmp_path):
    monkeypatch.setenv(parse_hdfc.PASSWORD_VARIABLE, SECRET)
    statement = tmp_path / "synthetic.pdf"
    statement.write_bytes(b"stand-in")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        parse_hdfc.pdfplumber, "open", fake_open([PAGE_ONE, PAGE_TWO], locked_with=SECRET)
    )

    parse_hdfc.parse_hdfc_pdf(str(statement))

    assert [p.name for p in tmp_path.iterdir()] == ["synthetic.pdf"]
    assert statement.read_bytes() == b"stand-in"
