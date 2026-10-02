"""Citi PDF parser — card bills and Citi Plus checking statements.

All fixtures are synthetic page texts shaped like pdfplumber's output for the
real layouts (fake merchants, fake card/account numbers, fake holder name).
Real-statement coverage lives in test_parse_citi_pdf_real.py, skipped unless
an env var points at a statements folder.
"""

import pytest

import parsers
from ingest import ensure_account
from parse_citi_pdf import (
    classify_citi_text,
    parse_citi_card_pages,
    parse_citi_checking_pages,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

CARD_PAGE1 = """0000000000000000
YOUR BILL SUMMARY
Statement Date June 05, 2026
Credit Limit $10,000.00
Current Balance $1,020.00
Total Minimum Payment $50.00
Payment Due Date June 30, 2026
If you have more than one Citibank credit card and receive separate statements
YOUR CITIBANK CARDS CURRENT AMOUNT MINIMUM REWARD TOTAL POINTS
CITIPRESTIGE CARD 1,020.00 0.00 50.00 POINTS 1,000"""

CARD_PAGE2 = """0000000000000000 CITI PRESTIGE CARD 4111 1111 1111 9876 Payment Due Date: June 30, 2026
PREVIOUS PAYMENTS PURCHASES INTEREST FEES CURRENT
BALANCE - CREDITS + ADVANCES + CHARGES + CHARGES = BALANCE
500.00 500.00 1,000.00 0.00 20.00 1,020.00
DATE DESCRIPTION AMOUNT(SGD)
TRANSACTIONS FOR CITI PRESTIGE CARD
ALL TRANSACTIONS BILLED IN SINGAPORE DOLLARS
BALANCE PREVIOUS STATEMENT 500.00
12 MAY PAYMENT-ATM/INTERNET (500.00)
SUB-TOTAL: (500.00)
CITI PRESTIGE CARD 4111 1111 1111 9876 - JANE TESTER
20 MAY FAKE COFFEE ROASTERS SINGAPORE SG 600.00
28 MAY TEST HOTEL JAKARTA ID 100.00
FOREIGN AMOUNT RUPIAH 1,150,000.00
SUB-TOTAL: 700.00"""

CARD_PAGE3 = """0000000000000000 CITI PRESTIGE CARD 4111 1111 1111 9876 Payment Due Date: June 30, 2026
Page 3 of 6
DATE DESCRIPTION AMOUNT(SGD)
CITI PRESTIGE CARD 4111 1111 1111 5555 - JOHN TESTER
01 JUN DEMO BOOKSHOP NEW YORK US 300.00
FOREIGN AMOUNT U.S. DOLLAR 225.00
02 JUN ANNUAL FEE 20.00
SUB-TOTAL: 320.00
GRAND TOTAL 1,020.00"""

CARD_PAGES = [CARD_PAGE1, CARD_PAGE2, CARD_PAGE3]


CHK_PAGE1 = """MR JANE TESTER
1 FAKE STREET
SINGAPORE 000000
SUMMARY OF YOUR CITI PLUS ACCOUNT
All amounts are in Singapore Dollars as of Jun 30 2026 unless otherwise stated
Checking 10,507.34
Savings&Investments 20,000.00
DETAILS OF YOUR CITI PLUS ACCOUNT
Your Checking Details
Citi Interest Booster Account 1234567890 SGD
Transactions Done
Jun 01 2026 Jun 01 2026 OPENING BALANCE 10,000.00
Jun 03 2026 Jun 03 2026 FAST INCOMING TEST CO REF0001 2,000.00 12,000.00
Jun 10 2026 Jun 10 2026 BILL PAYMENT CITI CARD 1,500.00 10,500.00
Jun 30 2026 Jun 30 2026 BONUS INTEREST 12.34 10,512.34
BONUS INTEREST RATE @ 1.50 % P.A.
ABC1DEF2/X123456/Y123456/Z123456789/1"""

CHK_PAGE2 = """Page 2 of 5
MR JANE TESTER Statement Period Jun 01 2026 - Jun 30 2026
Citi Interest Booster Account 1234567890 SGD (continued)
Transactions Done
Jun 30 2026 Jun 30 2026 SERVICE FEE (GST) 5.00 10,507.34
Jun 30 2026 CLOSING BALANCE 10,507.34
TOTAL 1,505.00 2,012.34
You have earned interest of SGD 12.34 since Jan 2026.
Your Savings & Investments Details
Time Deposits 9999999999
SGD 000001 20,000.00 Jun 01 2026 Jul 01 2026 1.00000 16.44"""

CHK_PAGES = [CHK_PAGE1, CHK_PAGE2]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_classifies_card_bill():
    assert classify_citi_text(CARD_PAGE1) == "credit_card"


def test_classifies_citi_plus_statement():
    assert classify_citi_text(CHK_PAGE1) == "bank"


@pytest.mark.parametrize("text", [
    "United Overseas Bank Limited\nStatement of Account\nOne Account 000-000-000-0",
    "DBS Bank Ltd\nCredit Cards\nStatement of Account\nSTATEMENT DATE 05 Jun 2026",
    "DBS Bank Ltd\nConsolidated Statement\nTransaction Details\nas at 30 Jun 2026",
    # A non-Citi bill that happens to use a similar heading
    "OTHER BANK\nYOUR BILL SUMMARY\nStatement Date June 05, 2026",
])
def test_does_not_claim_other_banks(text):
    assert classify_citi_text(text) is None


def test_registered_ahead_of_dbs_pdf_fallback():
    pdf_names = [p["name"] for p in parsers._PARSERS if p["ext"] == ".pdf"]
    assert "Citi PDF" in pdf_names
    citi = next(p for p in parsers._PARSERS if p["name"] == "Citi PDF")
    assert citi["detect_fn"] is not None
    assert pdf_names.index("Citi PDF") < pdf_names.index("DBS PDF")


# ---------------------------------------------------------------------------
# Credit-card bills
# ---------------------------------------------------------------------------

def test_card_statement_header_fields():
    stmt = parse_citi_card_pages(CARD_PAGES, "2026-06.pdf")
    assert stmt.statement_type == "credit_card"
    assert stmt.statement_date == "2026-06-05"
    # Same name form as parse_citi_csv, so ensure_account hits the
    # existing account by exact name or last four.
    assert stmt.accounts == ["Citi Card 9876"]
    assert stmt.filename == "2026-06.pdf"


def test_card_rows_signs_and_dates():
    stmt = parse_citi_card_pages(CARD_PAGES)
    rows = [(t.date, t.description, t.amount_sgd) for t in stmt.transactions]
    assert rows == [
        ("2026-05-12", "PAYMENT-ATM/INTERNET", -500.00),
        ("2026-05-20", "FAKE COFFEE ROASTERS SINGAPORE SG", 600.00),
        ("2026-05-28", "TEST HOTEL JAKARTA ID", 100.00),
        ("2026-06-01", "DEMO BOOKSHOP NEW YORK US", 300.00),
        ("2026-06-02", "ANNUAL FEE", 20.00),
    ]


def test_card_payment_row_is_emitted_and_flagged():
    stmt = parse_citi_card_pages(CARD_PAGES)
    payments = [t for t in stmt.transactions if t.is_payment]
    assert len(payments) == 1 and payments[0].amount_sgd < 0


def test_card_foreign_amount_attached_to_preceding_row():
    stmt = parse_citi_card_pages(CARD_PAGES)
    by_desc = {t.description: t for t in stmt.transactions}
    hotel = by_desc["TEST HOTEL JAKARTA ID"]
    assert (hotel.amount_foreign, hotel.currency_foreign) == (1150000.00, "IDR")
    book = by_desc["DEMO BOOKSHOP NEW YORK US"]
    assert (book.amount_foreign, book.currency_foreign) == (225.00, "USD")
    assert by_desc["ANNUAL FEE"].amount_foreign is None


def test_card_supplementary_rows_stay_on_principal_account():
    stmt = parse_citi_card_pages(CARD_PAGES)
    assert {t.card_info for t in stmt.transactions} == {"Citi Card 9876"}


def test_card_holder_name_never_reaches_descriptions():
    stmt = parse_citi_card_pages(CARD_PAGES)
    assert not any("TESTER" in t.description for t in stmt.transactions)


def test_card_year_rolls_back_for_december_rows_on_january_bill():
    pages = [
        CARD_PAGE1.replace("June 05, 2026", "January 05, 2027"),
        CARD_PAGE2.replace("12 MAY", "12 DEC").replace("20 MAY", "20 DEC")
                  .replace("28 MAY", "28 DEC"),
        CARD_PAGE3.replace("01 JUN", "01 JAN").replace("02 JUN", "02 JAN"),
    ]
    stmt = parse_citi_card_pages(pages)
    assert stmt.statement_date == "2027-01-05"
    assert [t.date[:7] for t in stmt.transactions] == [
        "2026-12", "2026-12", "2026-12", "2027-01", "2027-01",
    ]


def test_card_credit_balance_and_no_activity_month():
    # Credit balance carried forward, nothing billed: zero rows, still valid.
    page2 = """0000000000000000 CITI REWARDS WORLD MASTERCARD 4111 1111 1111 4321 Payment Due Date: ---
PREVIOUS PAYMENTS PURCHASES INTEREST FEES CURRENT
BALANCE - CREDITS + ADVANCES + CHARGES + CHARGES = BALANCE
-12.50 0.00 0.00 0.00 0.00 -12.50
DATE DESCRIPTION AMOUNT(SGD)
TRANSACTIONS FOR CITI REWARDS WORLD MASTERCARD
ALL TRANSACTIONS BILLED IN SINGAPORE DOLLARS
BALANCE PREVIOUS STATEMENT -12.50
SUB-TOTAL: 0.00
GRAND TOTAL -12.50"""
    stmt = parse_citi_card_pages([CARD_PAGE1, page2])
    assert stmt.transactions == []
    assert stmt.accounts == ["Citi Card 4321"]


def test_card_that_does_not_reconcile_raises():
    # Dropping a billed row must fail loudly, never import a short statement.
    broken = [CARD_PAGE1, CARD_PAGE2.replace("28 MAY TEST HOTEL JAKARTA ID 100.00\n", ""), CARD_PAGE3]
    with pytest.raises(ValueError, match="reconcile"):
        parse_citi_card_pages(broken)


def test_card_account_resolves_to_existing_account(conn):
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four) VALUES (?, ?, ?, ?)",
        ("Citi Prestige (existing)", "Citi-Prestige", "credit_card", "9876"),
    )
    conn.commit()
    existing_id = conn.execute("SELECT id FROM accounts WHERE last_four = '9876'").fetchone()[0]
    before = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]

    stmt = parse_citi_card_pages(CARD_PAGES)
    assert ensure_account(conn, stmt.transactions[0].card_info, "credit_card") == existing_id
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == before


# ---------------------------------------------------------------------------
# Citi Plus checking
# ---------------------------------------------------------------------------

def test_checking_header_fields():
    stmt = parse_citi_checking_pages(CHK_PAGES, "2026-06.pdf")
    assert stmt.statement_type == "bank"
    assert stmt.statement_date == "2026-06-30"
    assert stmt.accounts == ["Citi Checking 1234567890"]


def test_checking_rows_signed_by_balance_movement():
    stmt = parse_citi_checking_pages(CHK_PAGES)
    rows = [(t.date, t.amount_sgd) for t in stmt.transactions]
    # Credits (balance up) negative, debits (balance down) positive.
    assert rows == [
        ("2026-06-03", -2000.00),
        ("2026-06-10", 1500.00),
        ("2026-06-30", -12.34),
        ("2026-06-30", 5.00),
    ]


def test_checking_opening_closing_and_savings_rows_are_not_transactions():
    stmt = parse_citi_checking_pages(CHK_PAGES)
    descs = " | ".join(t.description for t in stmt.transactions)
    assert "OPENING" not in descs and "CLOSING" not in descs
    assert len(stmt.transactions) == 4


def test_checking_continuation_line_appended_but_page_furniture_is_not():
    stmt = parse_citi_checking_pages(CHK_PAGES)
    descs = [t.description for t in stmt.transactions]
    assert descs[2] == "BONUS INTEREST BONUS INTEREST RATE @ 1.50 % P.A."
    assert descs[0] == "FAST INCOMING TEST CO REF0001"
    joined = " ".join(descs)
    assert "TESTER" not in joined          # page header carries the holder name
    assert "/X123456/" not in joined       # page footer code
    assert "Page" not in joined


def test_checking_that_does_not_reconcile_raises():
    broken = [CHK_PAGE1, CHK_PAGE2.replace("TOTAL 1,505.00 2,012.34", "TOTAL 1,505.00 2,999.99")]
    with pytest.raises(ValueError, match="reconcile"):
        parse_citi_checking_pages(broken)


def test_checking_bill_payment_to_card_is_emitted():
    stmt = parse_citi_checking_pages(CHK_PAGES)
    assert any(t.is_payment and t.amount_sgd > 0 for t in stmt.transactions)
