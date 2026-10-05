"""DBS business account statements: rows signed by the running balance, in
whole cents.

The parser reads the amount printed on each line and the balance after it.
Whether a row is money in or money out comes from how the balance moved. The
fixture is synthetic text shaped like pdfplumber's output (invented account
number, payees and figures); no PDF is on disk.
"""

import parse_dbs_business
from parse_dbs_business import parse_dbs_business_pdf


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakePdf:
    def __init__(self, text):
        self.pages = [_FakePage(text)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _parse(monkeypatch, text):
    monkeypatch.setattr(parse_dbs_business.pdfplumber, "open", lambda _path: _FakePdf(text))
    return parse_dbs_business_pdf("synthetic.pdf")


STATEMENT = "\n".join([
    "Details Of Your DBS Business/Corporate Multi-Currency Account",
    "Account No: 000-000000-0",
    "01-Jan-2026 to 31-Jan-2026",
    "Currency: SGD",
    "Balance Brought Forward 0.10",
    # 0.10 + 0.20 is 0.30000000000000004 as floats.
    "02-Jan-26 02-Jan-26 SAMPLE INWARD CREDIT 0.20 0.30",
    "03-Jan-26 03-Jan-26 SAMPLE CARD SPEND 0.29 0.01",
    "05-Jan-26 05-Jan-26 SAMPLE CUSTOMER PAYMENT 4,350.00 4,350.01",
    "REF 0001 SAMPLE",
    "09-Jan-26 09-Jan-26 SAMPLE SUPPLIER 1,234.56 3,115.45",
    "Balance Carried Forward 3,115.45",
])


def test_rows_are_signed_by_the_balance_movement_in_whole_cents(monkeypatch):
    stmt = _parse(monkeypatch, STATEMENT)

    assert stmt.accounts == ["DBS Business 0000000000"]
    rows = [(t.date, t.description, t.amount_minor) for t in stmt.transactions]
    # Money in (balance up) negative, money out (balance down) positive.
    assert rows == [
        ("2026-01-02", "SAMPLE INWARD CREDIT", -20),
        ("2026-01-03", "SAMPLE CARD SPEND", 29),
        ("2026-01-05", "SAMPLE CUSTOMER PAYMENT REF 0001 SAMPLE", -435000),
        ("2026-01-09", "SAMPLE SUPPLIER", 123456),
    ]
    assert all(type(t.amount_minor) is int for t in stmt.transactions)


def test_rows_carry_the_opening_balance_to_the_closing_balance_to_the_cent(monkeypatch):
    stmt = _parse(monkeypatch, STATEMENT)

    # Brought forward 0.10, less the rows (money out positive), is 3,115.45.
    assert 10 - sum(t.amount_minor for t in stmt.transactions) == 311545


def test_a_balance_below_zero_is_read(monkeypatch):
    overdrawn = STATEMENT.replace(
        "09-Jan-26 09-Jan-26 SAMPLE SUPPLIER 1,234.56 3,115.45",
        "09-Jan-26 09-Jan-26 SAMPLE SUPPLIER 4,350.02 -0.01\n"
        "10-Jan-26 10-Jan-26 SAMPLE TOP UP 0.01 0.00",
    )

    stmt = _parse(monkeypatch, overdrawn)

    assert [t.amount_minor for t in stmt.transactions][-2:] == [435002, -1]
