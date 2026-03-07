"""Parse DBS CSV exports (credit card and bank) into structured transactions."""

import csv
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from parse_dbs import ParsedTransaction, ParsedStatement, MONTH_MAP


def _parse_date(date_str: str) -> str:
    """Parse 'DD MMM YYYY' or 'DD/MM/YYYY' to 'YYYY-MM-DD'."""
    date_str = date_str.strip()
    # "07 Mar 2026"
    m = re.match(r"(\d{1,2})\s+(\w{3})\s+(\d{4})", date_str)
    if m:
        day = m.group(1).zfill(2)
        mon = MONTH_MAP.get(m.group(2).upper()[:3], "01")
        year = m.group(3)
        return f"{year}-{mon}-{day}"
    # "07/03/2026"
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", date_str)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return date_str


def _extract_foreign_currency(description: str) -> tuple[str, float | None, str | None]:
    """Extract foreign currency info from description like 'MERCHANT ... INR 729.00'.

    Returns (cleaned_description, foreign_amount, foreign_currency).
    """
    # Pattern: ends with CURRENCY_CODE AMOUNT
    m = re.search(r"\s+(USD|EUR|GBP|AUD|INR|MYR|IDR|THB|JPY|HKD|NZD|TWD|KRW|CNY|PHP|VND)\s+([\d,]+\.\d{2})$", description)
    if m:
        currency = m.group(1)
        amount = float(m.group(2).replace(",", ""))
        cleaned = description[:m.start()].strip()
        return cleaned, amount, currency
    return description, None, None


def _detect_csv_type(first_line: str) -> str:
    """Detect CSV type from first line."""
    if "Account Details For" in first_line:
        return "bank"
    if "Card Transaction Details For" in first_line:
        return "credit_card"
    return "unknown"


def _extract_account_name(first_line: str, csv_type: str) -> str:
    """Extract account/card name from header line."""
    # Parse as CSV to handle quotes
    reader = csv.reader([first_line])
    row = next(reader)
    if len(row) >= 2:
        return row[1].strip()
    return "Unknown"


def _parse_cc_section(lines: list[str], account_name: str, filename: str) -> list[ParsedTransaction]:
    """Parse a single credit card section (one card's transactions).

    Takes the lines starting from the header row through to the end of that card's data.
    Returns a list of ParsedTransaction objects.
    """
    reader = csv.DictReader(lines)
    transactions = []

    for row in reader:
        tx_date_str = row.get("Transaction Date", "").strip()
        if not tx_date_str:
            continue

        # Skip if this row is actually another header (multi-card CSV)
        if tx_date_str == "Transaction Date":
            break

        tx_date = _parse_date(tx_date_str)

        description_raw = row.get("Transaction Description", "").strip()
        tx_type = row.get("Transaction Type", "").strip()
        debit = row.get("Debit Amount", "").strip()
        credit = row.get("Credit Amount", "").strip()

        # Parse amount — debit is expense (positive), credit is refund (negative)
        if debit:
            try:
                amount_sgd = float(debit.replace(",", ""))
            except ValueError:
                continue
        elif credit:
            try:
                amount_sgd = -float(credit.replace(",", ""))
            except ValueError:
                continue
        else:
            continue

        # Extract foreign currency from description
        description, amount_foreign, currency_foreign = _extract_foreign_currency(description_raw)

        is_payment = tx_type in ("PAYMENT", "PAYMENT & CREDITS") or "PAYMENT" in description.upper()

        tx = ParsedTransaction(
            date=tx_date,
            description=description,
            amount_sgd=amount_sgd,
            amount_foreign=amount_foreign,
            currency_foreign=currency_foreign,
            is_payment=is_payment,
            card_info=account_name,
        )
        transactions.append(tx)

    return transactions


def parse_cc_csv(filepath: str) -> list[ParsedStatement]:
    """Parse a DBS credit card CSV export.

    Handles multi-card CSVs (primary + supplementary card in one file).
    Returns a list of ParsedStatement objects — one per card found.

    Columns: Transaction Date, Transaction Posting Date, Transaction Description,
    Transaction Type, Payment Type, Transaction Status, Debit Amount, Credit Amount
    """
    path = Path(filepath)
    with open(path, encoding="utf-8-sig") as f:
        raw_lines = f.readlines()

    # Split the file into card sections.
    # Each section starts with metadata rows, then a header row, then data.
    # Supplementary cards are introduced by a "Supplementary Card:" line.
    sections = []  # list of (account_name, data_lines_starting_from_header)

    # Primary card — always first
    primary_name = _extract_account_name(raw_lines[0], "credit_card")

    # Find all header row positions
    header_positions = []
    for i, line in enumerate(raw_lines):
        if line.startswith('"Transaction Date","Transaction Posting Date"'):
            header_positions.append(i)

    if not header_positions:
        raise ValueError(f"Could not find data header in {filepath}")

    # For each header, determine which card it belongs to
    for idx, header_pos in enumerate(header_positions):
        if idx == 0:
            # Primary card
            card_name = primary_name
        else:
            # Look backwards from header for "Supplementary Card:" or card name
            card_name = primary_name + " (supplementary)"
            for j in range(header_pos - 1, max(header_pos - 5, 0), -1):
                line = raw_lines[j].strip().strip('"').strip(',').strip('"')
                if "Vantage" in line or "Card" in line:
                    # Parse as CSV to extract clean card name
                    reader = csv.reader([raw_lines[j]])
                    row = next(reader)
                    candidate = row[0].strip()
                    if candidate and candidate != "Supplementary Card:":
                        card_name = candidate
                        break

        # Data lines: from header to next header (or end of file)
        end_pos = header_positions[idx + 1] if idx + 1 < len(header_positions) else len(raw_lines)
        # But trim empty/metadata lines between sections
        data_lines = raw_lines[header_pos:end_pos]
        sections.append((card_name, data_lines))

    # Parse each section into a statement
    statements = []
    for card_name, data_lines in sections:
        txns = _parse_cc_section(data_lines, card_name, path.name)
        dates = [tx.date for tx in txns]

        stmt = ParsedStatement(
            statement_type="credit_card",
            statement_date=max(dates) if dates else "",
            accounts=[card_name],
            filename=path.name,
            transactions=txns,
        )
        statements.append(stmt)

    return statements


def parse_bank_csv(filepath: str) -> ParsedStatement:
    """Parse a DBS bank account CSV export.

    Columns: Transaction Date, Value Date, Statement Code, Description,
    Supplementary Code, Supplementary Code Description, Client Reference,
    Additional Reference, Status, Currency, Debit Amount, Credit Amount
    """
    path = Path(filepath)
    with open(path, encoding="utf-8-sig") as f:
        raw_lines = f.readlines()

    account_name = _extract_account_name(raw_lines[0], "bank")

    # Find data header
    data_start = None
    for i, line in enumerate(raw_lines):
        if line.startswith('"Transaction Date","Value Date"'):
            data_start = i
            break

    if data_start is None:
        raise ValueError(f"Could not find data header in {filepath}")

    statement = ParsedStatement(
        statement_type="bank",
        statement_date="",
        accounts=[account_name],
        filename=path.name,
    )

    reader = csv.DictReader(raw_lines[data_start:])
    dates = []

    for row in reader:
        tx_date_str = row.get("Transaction Date", "").strip()
        if not tx_date_str:
            continue

        tx_date = _parse_date(tx_date_str)
        dates.append(tx_date)

        # Build description from multiple fields
        desc_parts = [
            row.get("Description", "").strip(),
        ]
        client_ref = row.get("Client Reference", "").strip()
        addl_ref = row.get("Additional Reference", "").strip()
        if client_ref:
            desc_parts.append(client_ref)
        if addl_ref:
            desc_parts.append(addl_ref)
        description = " | ".join(p for p in desc_parts if p)

        debit = row.get("Debit Amount", "").strip()
        credit = row.get("Credit Amount", "").strip()

        if debit:
            amount_sgd = float(debit.replace(",", ""))
        elif credit:
            amount_sgd = -float(credit.replace(",", ""))
        else:
            continue

        # Detect transfers and payments
        desc_upper = description.upper()
        stmt_code = row.get("Statement Code", "").strip().upper()

        # Bill payments to own credit cards
        is_payment = "BILL DBSC" in desc_upper or "BILL PAYMENT" in desc_upper

        # Internal transfers between own accounts
        is_transfer = (
            "TRF FT" in desc_upper
            or "FUNDS TRANSFER" in desc_upper
            or stmt_code == "ATINT"  # interest earned
            or "SI TO :" in desc_upper  # standing instruction to self
            or "MEP " in desc_upper  # fixed deposit / money market placement
            or "ICT CSL:" in desc_upper  # bank-to-bank (Citi)
            or "ICT UOB:" in desc_upper  # bank-to-bank (UOB)
            or (amount_sgd < 0 and "ICT" not in desc_upper and "GIRO" not in desc_upper)  # credits/deposits
        )

        tx = ParsedTransaction(
            date=tx_date,
            description=description,
            amount_sgd=amount_sgd,
            is_payment=is_payment,
            is_transfer=is_transfer,
            card_info=account_name,
        )
        statement.transactions.append(tx)

    if dates:
        statement.statement_date = max(dates)

    return statement


def parse_csv(filepath: str) -> list[ParsedStatement]:
    """Auto-detect and parse a DBS CSV export.

    Returns a list of ParsedStatement objects (multiple if multi-card CSV).
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {filepath}")

    with open(path, encoding="utf-8-sig") as f:
        first_line = f.readline()

    csv_type = _detect_csv_type(first_line)

    if csv_type == "credit_card":
        return parse_cc_csv(filepath)  # already returns list
    elif csv_type == "bank":
        return [parse_bank_csv(filepath)]  # wrap in list for consistency
    else:
        raise ValueError(f"Could not detect CSV type from: {path.name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py parse_dbs_csv.py <path_to_csv>")
        sys.exit(1)

    from parse_dbs import print_summary
    stmt = parse_csv(sys.argv[1])
    print_summary(stmt)
