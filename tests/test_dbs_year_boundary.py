"""Year inference for DBS card statements.

Card statements print transaction dates as DD MON with no year. Stamping
every row with the statement's year pushed December rows of a January
statement a year into the future. Fixtures are synthetic.
"""

import parse_dbs
from parse_dbs import _infer_tx_date


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakePdf:
    def __init__(self, text):
        self.pages = [_FakePage(text)]

    def close(self):
        pass


def _parse_cc_text(monkeypatch, text):
    """Run parse_cc_statement over synthetic statement text (no PDF on disk)."""
    monkeypatch.setattr(parse_dbs.pdfplumber, "open", lambda _path: _FakePdf(text))
    return parse_dbs.parse_cc_statement("synthetic.pdf")


def test_december_row_in_january_statement_gets_previous_year():
    assert _infer_tx_date("23", "12", "2026-01-15") == "2025-12-23"


def test_january_row_in_january_statement_keeps_statement_year():
    assert _infer_tx_date("05", "01", "2026-01-15") == "2026-01-05"


def test_row_a_few_days_after_statement_date_keeps_statement_year():
    """A late-posted row just past the cut-off is not a wrapped year."""
    assert _infer_tx_date("18", "01", "2026-01-15") == "2026-01-18"


def test_statements_inside_one_year_are_unchanged():
    assert _infer_tx_date("28", "05", "2025-06-03") == "2025-05-28"
    assert _infer_tx_date("20", "11", "2025-12-03") == "2025-11-20"
    assert _infer_tx_date("02", "12", "2025-12-03") == "2025-12-02"


def test_unknown_statement_date_keeps_legacy_default_year():
    assert _infer_tx_date("23", "12", "unknown") == "2024-12-23"


def test_consolidated_january_statement_dates_every_card_section(monkeypatch):
    """Both card sections of a consolidated statement wrap the same way."""
    text = "\n".join([
        "DBS Credit Cards",
        "STATEMENT DATE",
        "15 Jan 2026",
        "DBS SAMPLE CARD CARD NO.: 0000 0000 0000 1111",
        "23 DEC SAMPLE MERCHANT ONE 12.30",
        "05 JAN SAMPLE MERCHANT TWO 4.50",
        "DBS OTHER CARD CARD NO.: 0000 0000 0000 2222",
        "30 DEC SAMPLE MERCHANT THREE 7.00",
        "02 JAN BILL PAYMENT - DBS INTERNET/WIRELESS 100.00 CR",
    ])
    statement = _parse_cc_text(monkeypatch, text)
    assert statement.statement_date == "2026-01-15"
    assert [tx.date for tx in statement.transactions] == [
        "2025-12-23", "2026-01-05", "2025-12-30", "2026-01-02",
    ]


def test_mid_year_statement_rows_keep_statement_year(monkeypatch):
    text = "\n".join([
        "DBS Credit Cards",
        "STATEMENT DATE",
        "03 Jun 2025",
        "DBS SAMPLE CARD CARD NO.: 0000 0000 0000 1111",
        "28 MAY SAMPLE MERCHANT ONE 12.30",
        "02 JUN SAMPLE MERCHANT TWO 4.50",
    ])
    statement = _parse_cc_text(monkeypatch, text)
    assert [tx.date for tx in statement.transactions] == ["2025-05-28", "2025-06-02"]
