"""Parse UOB PDF statements (bank + credit card) into structured transactions.

UOB Bank Statement format:
  - Period on page 1: "Period: DD MMM YYYY to DD MMM YYYY"
  - Account on page 2: "One Account 380-344-339-2"
  - Transactions: Date | Description | Withdrawals | Deposits | Balance
  - Multi-line descriptions (continuation lines have no date)

UOB Credit Card Statement format:
  - Statement Date on page 1: "Statement Date DD MMM YYYY"
  - Card: "LADY'S SOLITAIRE CARD 5522-5320-3064-7655"
  - Transactions: Post Date | Trans Date | Description | Amount SGD
  - "CR" suffix on amounts = credit/payment
  - "Ref No." lines follow each transaction (skip)
"""

import re
import sys
from pathlib import Path

import pdfplumber

from parse_dbs import ParsedTransaction, ParsedStatement, MONTH_MAP


def _parse_date_uob(date_str: str, year: int) -> str:
    """Parse UOB date like '01 Oct' or '15 NOV' with known year to YYYY-MM-DD."""
    date_str = date_str.strip()
    m = re.match(r"(\d{1,2})\s+(\w{3})", date_str)
    if m:
        day = m.group(1).zfill(2)
        mon = MONTH_MAP.get(m.group(2).upper()[:3], "01")
        return f"{year}-{mon}-{day}"
    return date_str


def _extract_statement_period(text: str) -> tuple[int, int, str]:
    """Extract year(s) and statement date from UOB bank statement page 1.

    Returns (start_year, end_year, statement_date_str).
    """
    # "Period: 01 Oct 2025 to 31 Oct 2025"
    m = re.search(r"Period:\s*(\d{1,2}\s+\w{3}\s+(\d{4}))\s+to\s+(\d{1,2}\s+\w{3}\s+(\d{4}))", text)
    if m:
        start_year = int(m.group(2))
        end_year = int(m.group(4))
        end_date = m.group(3) + " " + m.group(4)
        return start_year, end_year, end_date
    return 2025, 2025, ""


def _extract_cc_statement_date(text: str) -> tuple[int, int, str]:
    """Extract statement date from UOB CC statement page 1.

    Returns (month, year, date_str).
    """
    # "Statement Date 12 NOV 2025"
    m = re.search(r"Statement Date\s+(\d{1,2})\s+(\w{3})\s+(\d{4})", text)
    if m:
        day = m.group(1)
        mon_str = m.group(2)
        year = int(m.group(3))
        mon = int(MONTH_MAP.get(mon_str.upper()[:3], "01"))
        return mon, year, f"{day} {mon_str} {year}"
    return 1, 2025, ""


def _extract_account_number(text: str) -> str:
    """Extract UOB account number like '380-344-339-2' from transaction page."""
    m = re.search(r"One Account\s+([\d-]+)", text)
    if m:
        return m.group(1)
    return ""


def _extract_cc_card_info(text: str) -> tuple[str, str]:
    """Extract card name and number from UOB CC statement.

    Returns (card_name, card_number).
    """
    # "LADY'S SOLITAIRE 5522-5320-3064-7655 MILI KALE"
    m = re.search(r"(LADY'S SOLITAIRE|UOB ONE|UOB PREFERRED|UOB VISA)\s+(?:CARD\s+)?([\d-]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # Fallback: look for card number pattern
    m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{4})", text)
    if m:
        return "UOB Card", m.group(1)
    return "UOB Card", ""


def _parse_amount(amount_str: str) -> float:
    """Parse amount string, handling commas."""
    return float(amount_str.replace(",", ""))


def parse_uob_bank_pdf(filepath: str) -> ParsedStatement:
    """Parse a UOB bank account PDF statement."""
    pdf = pdfplumber.open(filepath)
    path = Path(filepath)

    # Page 1: extract period/year
    page1_text = pdf.pages[0].extract_text() or ""
    start_year, end_year, end_date_str = _extract_statement_period(page1_text)

    account_number = ""
    transactions = []

    for page in pdf.pages:
        text = page.extract_text() or ""
        if "Account Transaction Details" not in text:
            continue

        # Extract account number
        if not account_number:
            account_number = _extract_account_number(text)

        lines = text.split("\n")
        in_transactions = False

        for line in lines:
            line = line.strip()

            if "Account Transaction Details" in line:
                in_transactions = True
                continue
            if "End of Transaction" in line or line.startswith("Total "):
                break
            if not in_transactions:
                continue

            # Skip header lines
            if line.startswith("Date ") or line.startswith("SGD"):
                continue
            if line.startswith("One Account"):
                continue

            # Transaction line: starts with "DD MMM"
            m = re.match(r"^(\d{1,2}\s+\w{3})\s+(.+)", line)
            if not m:
                # Continuation line (description overflow) — append to last transaction
                if transactions and line and not re.match(r"^\d", line):
                    last = transactions[-1]
                    transactions[-1] = ParsedTransaction(
                        date=last.date,
                        description=last.description + " " + line,
                        amount_sgd=last.amount_sgd,
                        amount_foreign=last.amount_foreign,
                        currency_foreign=last.currency_foreign,
                        is_payment=last.is_payment,
                        is_transfer=last.is_transfer,
                        card_info=last.card_info,
                    )
                continue

            date_str = m.group(1)
            rest = m.group(2)

            # Determine year from month (handle Dec→Jan boundary)
            mon_match = re.match(r"\d{1,2}\s+(\w{3})", date_str)
            mon_num = int(MONTH_MAP.get(mon_match.group(1).upper()[:3], "01")) if mon_match else 1
            # If statement spans year boundary (e.g., Dec 2025 to Jan 2026)
            if start_year != end_year and mon_num >= 10:
                year = start_year
            else:
                year = end_year

            tx_date = _parse_date_uob(date_str, year)

            # Parse rest: Description followed by amounts
            # Amounts are at the end: "8,429.37 77,136.99" (withdrawal + balance)
            # or "10,200.00 88,927.67" (deposit + balance)
            # The challenge: description can contain numbers, amounts are right-aligned

            # Strategy: find amount patterns at end of line
            # Pattern: optional withdrawal, optional deposit, balance (always present)
            amounts = re.findall(r"[\d,]+\.\d{2}", rest)

            if len(amounts) >= 2:
                # Last amount is always balance — ignore it
                # If BALANCE B/F, skip entirely
                if "BALANCE B/F" in rest:
                    continue

                # Extract description (everything before the first amount)
                first_amt_pos = rest.find(amounts[0])
                description = rest[:first_amt_pos].strip()

                # Determine if withdrawal or deposit
                # If 3 amounts: withdrawal, deposit (empty), balance — but text extraction
                # may merge them. Use context from description.
                if "Interest Credit" in description or "Inward Credit" in description:
                    # Deposit (credit) — negative in our convention
                    amount_sgd = -_parse_amount(amounts[0])
                elif "Bill Payment" in description or "Misc Debit" in description or "PAYNOW" in description.upper():
                    # Withdrawal (debit) — positive in our convention
                    amount_sgd = _parse_amount(amounts[0])
                else:
                    # Default: positive = expense
                    amount_sgd = _parse_amount(amounts[0])

                desc_upper = description.upper()
                is_payment = "BILL PAYMENT" in desc_upper
                is_transfer = (
                    "BALANCE B/F" in desc_upper
                    or "INTEREST CREDIT" in desc_upper
                    or "INWARD CREDIT" in desc_upper
                    or "TRF." in desc_upper
                    or "MISC DEBIT" in desc_upper  # loan repayment
                )

                tx = ParsedTransaction(
                    date=tx_date,
                    description=description,
                    amount_sgd=amount_sgd,
                    is_payment=is_payment,
                    is_transfer=is_transfer,
                    card_info=f"UOB One Account {account_number}",
                )
                transactions.append(tx)
            elif len(amounts) == 1 and "BALANCE B/F" in rest:
                continue

    pdf.close()

    account_name = f"UOB One Account {account_number}" if account_number else "UOB One Account"

    # Statement date from period end
    stmt_date = ""
    if end_date_str:
        m = re.match(r"(\d{1,2})\s+(\w{3})\s+(\d{4})", end_date_str)
        if m:
            day = m.group(1).zfill(2)
            mon = MONTH_MAP.get(m.group(2).upper()[:3], "01")
            stmt_date = f"{m.group(3)}-{mon}-{day}"

    return ParsedStatement(
        statement_type="bank",
        statement_date=stmt_date,
        accounts=[account_name],
        filename=path.name,
        transactions=transactions,
    )


def parse_uob_cc_pdf(filepath: str) -> ParsedStatement:
    """Parse a UOB credit card PDF statement."""
    pdf = pdfplumber.open(filepath)
    path = Path(filepath)

    # Page 1: statement date + card info + transactions
    page1_text = pdf.pages[0].extract_text() or ""
    stmt_month, stmt_year, stmt_date_str = _extract_cc_statement_date(page1_text)
    card_name, card_number = _extract_cc_card_info(page1_text)

    account_name = f"UOB {card_name} {card_number}" if card_number else f"UOB {card_name}"

    transactions = []

    for page in pdf.pages:
        text = page.extract_text() or ""

        # Only process pages with transaction data
        if "Description of Transaction" not in text and "Post" not in text:
            continue

        lines = text.split("\n")
        in_transactions = False

        for line in lines:
            line = line.strip()

            # Start after the transaction header
            if "Description of Transaction" in line:
                in_transactions = True
                continue
            if "End of Transaction" in line or "SUB TOTAL" in line or "TOTAL BALANCE" in line:
                break
            if not in_transactions:
                continue

            # Skip non-transaction lines
            if line.startswith("Ref No."):
                continue
            if "PREVIOUS BALANCE" in line:
                continue
            if "ADD UNI$" in line or "MEMBERSHIP FEE" in line:
                continue

            # Transaction line: "DD MMM DD MMM Description Amount"
            # Post date, Trans date, Description, Amount
            m = re.match(
                r"^(\d{1,2}\s+\w{3})\s+(\d{1,2}\s+\w{3})\s+(.+?)\s+([\d,]+\.\d{2})(CR)?$",
                line,
            )
            if not m:
                # Try payment line: "DD MMM DD MMM PAYMT ... AmountCR"
                m = re.match(
                    r"^(\d{1,2}\s+\w{3})\s+(\d{1,2}\s+\w{3})\s+(.+?)\s+([\d,]+\.\d{2})(CR)$",
                    line,
                )
                if not m:
                    continue

            post_date_str = m.group(1)
            trans_date_str = m.group(2)
            description = m.group(3).strip()
            amount = _parse_amount(m.group(4))
            is_credit = m.group(5) == "CR" if m.group(5) else False

            # Determine year from month
            # Transaction month vs statement month: if trans month > stmt month,
            # it's from the previous year
            trans_mon_match = re.match(r"\d{1,2}\s+(\w{3})", trans_date_str)
            if trans_mon_match:
                trans_mon = int(MONTH_MAP.get(trans_mon_match.group(1).upper()[:3], "01"))
                if trans_mon > stmt_month:
                    year = stmt_year - 1
                else:
                    year = stmt_year
            else:
                year = stmt_year

            tx_date = _parse_date_uob(trans_date_str, year)

            # Credits are negative (payments/refunds), debits are positive (expenses)
            if is_credit:
                amount_sgd = -amount
            else:
                amount_sgd = amount

            is_payment = "PAYMT" in description.upper() or "PAYMENT" in description.upper()

            tx = ParsedTransaction(
                date=tx_date,
                description=description,
                amount_sgd=amount_sgd,
                is_payment=is_payment,
                card_info=account_name,
            )
            transactions.append(tx)

    pdf.close()

    # Statement date
    stmt_date = ""
    if stmt_date_str:
        m = re.match(r"(\d{1,2})\s+(\w{3})\s+(\d{4})", stmt_date_str)
        if m:
            day = m.group(1).zfill(2)
            mon = MONTH_MAP.get(m.group(2).upper()[:3], "01")
            stmt_date = f"{m.group(3)}-{mon}-{day}"

    return ParsedStatement(
        statement_type="credit_card",
        statement_date=stmt_date,
        accounts=[account_name],
        filename=path.name,
        transactions=transactions,
    )


def detect_uob_pdf(filepath: str) -> str | None:
    """Detect if a PDF is a UOB statement and what type.

    Returns 'bank', 'credit_card', or None.
    """
    try:
        pdf = pdfplumber.open(filepath)
        text = pdf.pages[0].extract_text() or ""
        pdf.close()

        if "United Overseas Bank" in text or "UOB" in text:
            if "Statement of Account" in text or "One Account" in text:
                return "bank"
            if "Credit Card" in text or "Statement Summary" in text:
                return "credit_card"
            # Fallback: check for CC-specific patterns
            if "Amount to Pay" in text or "SOLITAIRE" in text:
                return "credit_card"
        return None
    except Exception:
        return None


def parse_uob_pdf(filepath: str) -> ParsedStatement:
    """Auto-detect UOB PDF type and parse."""
    uob_type = detect_uob_pdf(filepath)
    if uob_type == "bank":
        return parse_uob_bank_pdf(filepath)
    elif uob_type == "credit_card":
        return parse_uob_cc_pdf(filepath)
    else:
        raise ValueError(f"Not a recognized UOB PDF: {filepath}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py parse_uob.py <path_to_pdf>")
        sys.exit(1)

    from parse_dbs import print_summary
    stmt = parse_uob_pdf(sys.argv[1])
    print_summary(stmt)
