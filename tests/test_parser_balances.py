"""The balances a statement states, as its parser hands them to the import.

A parser whose source states an opening and a closing balance returns them
with the rows; the import path checks one against the other (tie.py). These
read synthetic text shaped like pdfplumber's output (invented account numbers,
payees and figures); no PDF is on disk.
"""

import pytest

import parse_dbs
import parse_dbs_business
import parse_uob
import tie


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self, **_kwargs):
        return self._text


class _FakePdf:
    def __init__(self, pages):
        self.pages = [_FakePage(text) for text in pages]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def close(self):
        pass


def _read(monkeypatch, module, parse, pages):
    monkeypatch.setattr(module.pdfplumber, "open", lambda _path: _FakePdf(pages))
    return parse("synthetic.pdf")


# --- DBS business account: brought forward and carried forward ----------------------

BUSINESS = "\n".join([
    "Details Of Your DBS Business/Corporate Multi-Currency Account",
    "Account No: 000-000000-0",
    "01-Jan-2026 to 31-Jan-2026",
    "Currency: SGD",
    "Balance Brought Forward 1,000.10",
    "02-Jan-26 02-Jan-26 SAMPLE INWARD CREDIT 0.20 1,000.30",
    "03-Jan-26 03-Jan-26 SAMPLE CARD SPEND 250.29 750.01",
    "09-Jan-26 09-Jan-26 SAMPLE SUPPLIER 49.56 700.45",
    "Balance Carried Forward 700.45",
])


def test_dbs_business_hands_over_brought_and_carried_forward_and_ties(monkeypatch):
    stmt = _read(monkeypatch, parse_dbs_business, parse_dbs_business.parse_dbs_business_pdf, [BUSINESS])

    assert (stmt.opening_minor, stmt.closing_minor) == (100010, 70045)
    assert (stmt.opening_date, stmt.closing_date) == ("2026-01-01", "2026-01-31")
    figures = tie.check(stmt)
    assert (figures["rows_minor"], figures["difference_minor"]) == (-29965, 0)
    assert 100010 - sum(t.amount_minor for t in stmt.transactions) == 70045


def test_dbs_business_with_a_row_not_read_does_not_tie(monkeypatch):
    short = BUSINESS.replace("03-Jan-26 03-Jan-26 SAMPLE CARD SPEND 250.29 750.01\n", "")

    stmt = _read(monkeypatch, parse_dbs_business, parse_dbs_business.parse_dbs_business_pdf, [short])

    with pytest.raises(tie.DoesNotTie) as excinfo:
        tie.check(stmt)
    assert excinfo.value.figures["difference_minor"] != 0


def test_dbs_business_one_cent_off_its_carried_forward_does_not_tie(monkeypatch):
    off = BUSINESS.replace("Balance Carried Forward 700.45", "Balance Carried Forward 700.46")

    stmt = _read(monkeypatch, parse_dbs_business, parse_dbs_business.parse_dbs_business_pdf, [off])

    with pytest.raises(tie.DoesNotTie) as excinfo:
        tie.check(stmt)
    assert excinfo.value.figures["difference_minor"] == -1


def test_dbs_business_with_no_carried_forward_line_states_no_balance(monkeypatch):
    cut = BUSINESS.replace("\nBalance Carried Forward 700.45", "")

    stmt = _read(monkeypatch, parse_dbs_business, parse_dbs_business.parse_dbs_business_pdf, [cut])

    assert (stmt.opening_minor, stmt.closing_minor) == (None, None)
    assert tie.check(stmt) is None


# --- DBS bank account: brought forward and carried forward --------------------------

DBS_BANK = "\n".join([
    "DBS Bank Ltd",
    "Consolidated Statement",
    "as at 31 Aug 2026",
    "Transaction Details",
    "Account No. 000-00000-0",
    "Date Description Withdrawal (-) Deposit (+) Balance",
    "Balance Brought Forward SGD 1,000.00",
    "03/08/2026 SAMPLE SHOP 100.00 900.00",
    "05/08/2026 SAMPLE EMPLOYER 500.25 1,400.25",
    "Balance Carried Forward SGD 1,400.25",
])


def test_dbs_bank_hands_over_brought_and_carried_forward_and_ties(monkeypatch):
    stmt = _read(monkeypatch, parse_dbs, parse_dbs.parse_bank_statement, [DBS_BANK])

    assert [t.amount_minor for t in stmt.transactions] == [10000, -50025]
    assert (stmt.opening_minor, stmt.closing_minor) == (100000, 140025)
    assert (stmt.opening_date, stmt.closing_date) == (None, "2026-08-31")
    assert tie.check(stmt)["difference_minor"] == 0
    assert 100000 - sum(t.amount_minor for t in stmt.transactions) == 140025


def test_dbs_bank_balance_carried_over_a_page_break_still_ties(monkeypatch):
    """Each page carries its balance forward and brings it forward again: the
    opening balance is the first brought forward, the closing the last carried."""
    page1 = "\n".join(DBS_BANK.split("\n")[:8] + ["Balance Carried Forward SGD 900.00"])
    page2 = "\n".join([
        "Balance Brought Forward SGD 900.00",
        "05/08/2026 SAMPLE EMPLOYER 500.25 1,400.25",
        "Balance Carried Forward SGD 1,400.25",
    ])

    stmt = _read(monkeypatch, parse_dbs, parse_dbs.parse_bank_statement, [page1, page2])

    assert (stmt.opening_minor, stmt.closing_minor) == (100000, 140025)
    assert tie.check(stmt)["difference_minor"] == 0


def test_dbs_bank_with_a_row_not_read_does_not_tie(monkeypatch):
    short = DBS_BANK.replace("03/08/2026 SAMPLE SHOP 100.00 900.00\n", "")

    stmt = _read(monkeypatch, parse_dbs, parse_dbs.parse_bank_statement, [short])

    with pytest.raises(tie.DoesNotTie):
        tie.check(stmt)


def test_dbs_bank_one_cent_off_its_carried_forward_does_not_tie(monkeypatch):
    off = DBS_BANK.replace("Carried Forward SGD 1,400.25", "Carried Forward SGD 1,400.24")

    stmt = _read(monkeypatch, parse_dbs, parse_dbs.parse_bank_statement, [off])

    with pytest.raises(tie.DoesNotTie) as excinfo:
        tie.check(stmt)
    assert excinfo.value.figures["difference_minor"] == 1


@pytest.mark.parametrize("change", [
    # no carried-forward line
    lambda text: text.replace("\nBalance Carried Forward SGD 1,400.25", ""),
    # no statement date to anchor the closing balance on
    lambda text: text.replace("as at 31 Aug 2026\n", ""),
    # a second account in the same file: one pair of balances cannot be read for two
    lambda text: text + "\nAccount No. 000-00000-1\nBalance Brought Forward SGD 5.00"
                        "\nBalance Carried Forward SGD 5.00",
])
def test_dbs_bank_states_no_balance_when_one_cannot_be_read_for_one_account(monkeypatch, change):
    stmt = _read(monkeypatch, parse_dbs, parse_dbs.parse_bank_statement, [change(DBS_BANK)])

    assert (stmt.opening_minor, stmt.closing_minor, stmt.closing_date) == (None, None, None)
    assert tie.check(stmt) is None


# --- UOB bank account: balance brought forward and the last running balance ---------

UOB_PAGE1 = "\n".join([
    "United Overseas Bank Limited",
    "Statement of Account",
    "Period: 01 Aug 2026 to 31 Aug 2026",
])

UOB_PAGE2 = "\n".join([
    "Account Transaction Details",
    "One Account 000-000-000-0",
    "Date Description Withdrawals Deposits Balance",
    "SGD SGD SGD",
    "01 Aug BALANCE B/F 1,000.00",
    "03 Aug SAMPLE SHOP 100.00 900.00",
    "05 Aug SAMPLE EMPLOYER 500.25 1,400.25",
    "End of Transaction Details",
])


def test_uob_bank_hands_over_brought_forward_and_its_last_balance_and_ties(monkeypatch):
    stmt = _read(monkeypatch, parse_uob, parse_uob.parse_uob_bank_pdf, [UOB_PAGE1, UOB_PAGE2])

    assert [t.amount_minor for t in stmt.transactions] == [10000, -50025]
    assert (stmt.opening_minor, stmt.closing_minor) == (100000, 140025)
    assert (stmt.opening_date, stmt.closing_date) == (None, "2026-08-31")
    assert tie.check(stmt)["difference_minor"] == 0
    assert 100000 - sum(t.amount_minor for t in stmt.transactions) == 140025


def test_uob_bank_with_a_row_read_the_wrong_way_round_does_not_tie(monkeypatch):
    """A second page starts with no balance to compare with, so its first row
    is taken as money out. Here it was money in: the rows no longer carry the
    brought-forward balance to the last balance printed."""
    page3 = "\n".join([
        "Account Transaction Details",
        "One Account 000-000-000-0",
        "07 Aug SAMPLE SENDER 20.00 1,420.25",
        "End of Transaction Details",
    ])

    stmt = _read(monkeypatch, parse_uob, parse_uob.parse_uob_bank_pdf, [UOB_PAGE1, UOB_PAGE2, page3])

    assert stmt.closing_minor == 142025
    with pytest.raises(tie.DoesNotTie) as excinfo:
        tie.check(stmt)
    assert excinfo.value.figures["difference_minor"] == -4000


@pytest.mark.parametrize("page1, page2", [
    # no balance brought forward
    (UOB_PAGE1, UOB_PAGE2.replace("01 Aug BALANCE B/F 1,000.00\n", "")),
    # no period, so no day to anchor the closing balance on
    (UOB_PAGE1.replace("\nPeriod: 01 Aug 2026 to 31 Aug 2026", ""), UOB_PAGE2),
])
def test_uob_bank_states_no_balance_when_one_cannot_be_read(monkeypatch, page1, page2):
    stmt = _read(monkeypatch, parse_uob, parse_uob.parse_uob_bank_pdf, [page1, page2])

    assert (stmt.opening_minor, stmt.closing_minor, stmt.closing_date) == (None, None, None)
    assert tie.check(stmt) is None
