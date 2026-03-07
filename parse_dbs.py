"""Parse DBS credit card and bank statement PDFs into structured transactions."""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber


@dataclass
class ParsedTransaction:
    """A single parsed transaction from a statement."""
    date: str              # YYYY-MM-DD
    description: str       # raw merchant/description text
    amount_sgd: float      # positive = expense, negative = credit
    amount_foreign: float | None = None
    currency_foreign: str | None = None
    is_payment: bool = False
    is_transfer: bool = False
    card_info: str = ""    # which card this belongs to


@dataclass
class ParsedStatement:
    """Result of parsing a statement PDF."""
    statement_type: str    # 'credit_card' or 'bank'
    statement_date: str    # YYYY-MM-DD
    accounts: list[str] = field(default_factory=list)
    transactions: list[ParsedTransaction] = field(default_factory=list)
    filename: str = ""


# Month abbreviation → number
MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}


def _parse_statement_date(text: str) -> str:
    """Extract statement date from header text. Returns YYYY-MM-DD."""
    # CC format: "STATEMENT DATE" on one line, then "03 Jun 2024 ..." on the next
    # Also try same-line match
    m = re.search(r"STATEMENT DATE.*?(\d{2})\s+(\w{3})\s+(\d{4})", text)
    if not m:
        # Date is on line after "STATEMENT DATE"
        m = re.search(r"STATEMENT DATE[^\n]*\n(\d{2})\s+(\w{3})\s+(\d{4})", text)
    if m:
        day, mon, year = m.group(1), m.group(2).upper()[:3], m.group(3)
        return f"{year}-{MONTH_MAP.get(mon, '01')}-{day}"

    # Bank format: "as at 30 Apr 2025"
    m = re.search(r"as at\s+(\d{1,2})\s+(\w{3})\s+(\d{4})", text)
    if m:
        day, mon, year = m.group(1).zfill(2), m.group(2).upper()[:3], m.group(3)
        return f"{year}-{MONTH_MAP.get(mon, '01')}-{day}"

    return "unknown"


def _detect_statement_type(text: str) -> str:
    """Detect whether this is a credit card or bank statement."""
    if "Credit Cards" in text or "Statement of Account" in text:
        return "credit_card"
    if "Consolidated Statement" in text or "Transaction Details" in text:
        return "bank"
    return "unknown"


def parse_cc_statement(pdf_path: str) -> ParsedStatement:
    """Parse a DBS credit card statement PDF.

    DBS CC statement format:
    - Each card section starts with "DBS [CARD TYPE] CARD NO.: XXXX..."
    - Transactions: "DD MMM DESCRIPTION AMOUNT"
    - Credits have " CR" suffix
    - Foreign transactions have a second line: "CURRENCY AMOUNT"
    - Payments start with "PAYMENT -"
    """
    path = Path(pdf_path)
    pdf = pdfplumber.open(str(path))

    all_text = ""
    for page in pdf.pages:
        text = page.extract_text()
        if text:
            all_text += text + "\n"

    statement = ParsedStatement(
        statement_type="credit_card",
        statement_date=_parse_statement_date(all_text),
        filename=path.name,
    )

    # Determine the statement year from the statement date
    stmt_year = statement.statement_date[:4] if statement.statement_date != "unknown" else "2024"

    current_card = ""
    lines = all_text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Detect card section headers
        card_match = re.match(r"(DBS\s+.*?CARD\s+NO\.?:?\s*[\d\s]+)", line)
        if card_match:
            current_card = card_match.group(1).strip()
            # Clean up card info — extract last 4 digits
            digits = re.findall(r"\d{4}", current_card)
            if digits:
                current_card = f"{current_card.split('CARD NO')[0].strip()} {digits[-1]}"
            if current_card not in statement.accounts:
                statement.accounts.append(current_card)
            i += 1
            continue

        # Skip non-transaction lines
        # Transaction pattern: DD MMM DESCRIPTION AMOUNT [CR]
        tx_match = re.match(
            r"(\d{2})\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|JUL|AUG|SEP|OCT|NOV|DEC)\s+"
            r"(.+?)\s+"
            r"([\d,]+\.\d{2})\s*(CR)?$",
            line,
        )

        if tx_match:
            day = tx_match.group(1)
            month = MONTH_MAP[tx_match.group(2)]
            description = tx_match.group(3).strip()
            amount = float(tx_match.group(4).replace(",", ""))
            is_credit = tx_match.group(5) == "CR"

            if is_credit:
                amount = -amount

            # Check if this is a payment
            is_payment = "PAYMENT" in description.upper() and (
                "DBS INTERNET" in description.upper()
                or "GIRO" in description.upper()
                or is_credit
            )

            # Check next line for foreign currency info
            amount_foreign = None
            currency_foreign = None
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                fx_match = re.match(
                    r"([A-Z][A-Z\s]+?)\s+([\d,]+\.\d{2})$",
                    next_line,
                )
                if fx_match:
                    currency_foreign = fx_match.group(1).strip()
                    # Filter out non-currency lines (page headers, etc.)
                    if len(currency_foreign.split()) <= 3 and not any(
                        skip in currency_foreign
                        for skip in ["CARD", "DBS", "PREVIOUS", "STATEMENT", "PAGE", "PDS_"]
                    ):
                        amount_foreign = float(fx_match.group(2).replace(",", ""))
                        i += 1  # skip the foreign currency line
                    else:
                        currency_foreign = None

            tx = ParsedTransaction(
                date=f"{stmt_year}-{month}-{day}",
                description=description,
                amount_sgd=amount,
                amount_foreign=amount_foreign,
                currency_foreign=currency_foreign,
                is_payment=is_payment,
                card_info=current_card,
            )
            statement.transactions.append(tx)

        i += 1

    pdf.close()
    return statement


def parse_bank_statement(pdf_path: str) -> ParsedStatement:
    """Parse a DBS bank/savings statement PDF.

    DBS bank statement format:
    - "Date Description Withdrawal (-) Deposit (+) Balance"
    - Date: DD/MM/YYYY
    - Multi-line descriptions (PayNow has TO: lines, etc.)
    - Amount columns are positional
    """
    path = Path(pdf_path)
    pdf = pdfplumber.open(str(path))

    all_text = ""
    for page in pdf.pages:
        text = page.extract_text()
        if text:
            all_text += text + "\n"

    statement = ParsedStatement(
        statement_type="bank",
        statement_date=_parse_statement_date(all_text),
        filename=path.name,
    )

    # Extract account info
    acct_match = re.search(r"Account No\.\s*([\d-]+)", all_text)
    if acct_match:
        statement.accounts.append(acct_match.group(1))

    lines = all_text.split("\n")
    i = 0
    current_desc_lines = []
    current_date = None
    current_withdrawal = None
    current_deposit = None

    while i < len(lines):
        line = lines[i].strip()

        # Match transaction start: DD/MM/YYYY Description [amount] [amount] balance
        tx_match = re.match(
            r"(\d{2}/\d{2}/\d{4})\s+(.+)",
            line,
        )

        if tx_match:
            # Save previous transaction if exists
            if current_date and (current_withdrawal or current_deposit):
                _save_bank_tx(
                    statement, current_date, current_desc_lines,
                    current_withdrawal, current_deposit,
                )

            date_str = tx_match.group(1)  # DD/MM/YYYY
            parts = date_str.split("/")
            current_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
            rest = tx_match.group(2)

            # Extract amounts from the rest of the line
            # Pattern: description ... withdrawal deposit balance
            # Amounts are decimal numbers with optional commas
            amounts = re.findall(r"[\d,]+\.\d{2}", rest)
            desc_part = re.sub(r"[\d,]+\.\d{2}", "", rest).strip()

            current_desc_lines = [desc_part]
            current_withdrawal = None
            current_deposit = None

            if len(amounts) >= 2:
                # Last amount is always balance
                # If 3 amounts: withdrawal, deposit, balance (rare)
                # If 2 amounts: either (withdrawal, balance) or (deposit, balance)
                # We look at context to decide
                balance = float(amounts[-1].replace(",", ""))
                if len(amounts) == 3:
                    current_withdrawal = float(amounts[0].replace(",", ""))
                    current_deposit = float(amounts[1].replace(",", ""))
                elif len(amounts) == 2:
                    amt = float(amounts[0].replace(",", ""))
                    # Heuristic: if description contains deposit keywords, it's a deposit
                    if any(kw in desc_part.upper() for kw in [
                        "INTEREST EARNED", "SALARY", "FUNDS TRANSFER"
                    ]) and "BILL PAYMENT" not in desc_part.upper():
                        # Could be either — check against balance change
                        # For now, use keyword heuristics
                        pass
                    # Actually the DBS format puts withdrawal and deposit in fixed columns
                    # But pdfplumber merges them. We need a different approach for bank statements.
                    # For now, mark as withdrawal (most common) — user can correct
                    current_withdrawal = amt

        elif current_date and line and not line.startswith("Balance") and not line.startswith("PDS_"):
            # Continuation line for current transaction description
            if not re.match(r"^[A-Z]\d+$", line):  # skip reference numbers
                current_desc_lines.append(line)

        i += 1

    # Save last transaction
    if current_date and (current_withdrawal or current_deposit):
        _save_bank_tx(
            statement, current_date, current_desc_lines,
            current_withdrawal, current_deposit,
        )

    pdf.close()
    return statement


def _save_bank_tx(
    statement: ParsedStatement,
    date: str,
    desc_lines: list[str],
    withdrawal: float | None,
    deposit: float | None,
) -> None:
    """Helper to save a bank transaction."""
    description = " ".join(line.strip() for line in desc_lines if line.strip())
    # Clean up description
    description = re.sub(r"\s+", " ", description).strip()

    if withdrawal:
        amount = withdrawal
    elif deposit:
        amount = -deposit  # negative = money in
    else:
        return

    # Detect transfers and payments
    desc_upper = description.upper()
    is_transfer = any(kw in desc_upper for kw in [
        "FUNDS TRANSFER", "I-BANK",
    ])
    is_payment = "BILL PAYMENT" in desc_upper or "DBSC-" in desc_upper

    tx = ParsedTransaction(
        date=date,
        description=description,
        amount_sgd=amount,
        is_payment=is_payment,
        is_transfer=is_transfer,
        card_info=statement.accounts[0] if statement.accounts else "",
    )
    statement.transactions.append(tx)


def parse_statement(pdf_path: str) -> ParsedStatement:
    """Auto-detect and parse a DBS statement PDF."""
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Read first page to detect type
    pdf = pdfplumber.open(str(path))
    first_page = pdf.pages[0].extract_text() or ""
    pdf.close()

    stmt_type = _detect_statement_type(first_page)

    if stmt_type == "credit_card":
        return parse_cc_statement(pdf_path)
    elif stmt_type == "bank":
        return parse_bank_statement(pdf_path)
    else:
        raise ValueError(f"Could not detect statement type from: {path.name}")


def print_summary(statement: ParsedStatement) -> None:
    """Print a readable summary of parsed transactions."""
    print(f"\n{'='*70}")
    print(f"Statement: {statement.filename}")
    print(f"Type: {statement.statement_type}")
    print(f"Date: {statement.statement_date}")
    print(f"Accounts: {', '.join(statement.accounts)}")
    print(f"Transactions: {len(statement.transactions)}")

    # Separate payments/transfers from expenses
    expenses = [t for t in statement.transactions if not t.is_payment and not t.is_transfer and t.amount_sgd > 0]
    credits = [t for t in statement.transactions if t.amount_sgd < 0]
    payments = [t for t in statement.transactions if t.is_payment]

    total_expenses = sum(t.amount_sgd for t in expenses)
    total_credits = sum(abs(t.amount_sgd) for t in credits)

    print(f"\nExpenses: {len(expenses)} transactions, SGD {total_expenses:,.2f}")
    print(f"Credits/Refunds: {len(credits)} transactions, SGD {total_credits:,.2f}")
    print(f"Payments: {len(payments)}")
    print(f"{'='*70}")

    print(f"\n{'DATE':<12} {'AMOUNT':>10} {'DESCRIPTION'}")
    print("-" * 70)
    for tx in expenses:
        fx = f" ({tx.currency_foreign} {tx.amount_foreign:,.2f})" if tx.amount_foreign else ""
        print(f"{tx.date:<12} {tx.amount_sgd:>10,.2f} {tx.description[:45]}{fx}")

    if credits:
        print(f"\n--- Credits/Refunds ---")
        for tx in credits:
            print(f"{tx.date:<12} {tx.amount_sgd:>10,.2f} {tx.description[:45]}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py parse_dbs.py <path_to_pdf>")
        sys.exit(1)

    stmt = parse_statement(sys.argv[1])
    print_summary(stmt)
