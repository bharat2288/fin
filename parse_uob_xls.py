"""Parse UOB credit card XLS exports into structured transactions.

UOB CC XLS format (real BIFF .xls):
  Row 3: Account Number: XXXXXXXXXXXXXXXX, SGD
  Row 4: Account Type: LADY'S SOLITAIRE CARD
  Row 5: Statement Date: DD MMM YYYY
  Row 8: Header row (Transaction Date, Posting Date, Description, ...)
  Row 9: Previous Balance
  Row 10+: Transactions
"""

import re
import sys
from pathlib import Path

import pandas as pd

from parse_dbs import ParsedTransaction, ParsedStatement, MONTH_MAP


def _parse_date(date_val) -> str:
    """Parse date from XLS cell — could be string 'DD MMM YYYY' or datetime."""
    if hasattr(date_val, "strftime"):
        return date_val.strftime("%Y-%m-%d")
    date_str = str(date_val).strip()
    m = re.match(r"(\d{1,2})\s+(\w{3})\s+(\d{4})", date_str)
    if m:
        day = m.group(1).zfill(2)
        mon = MONTH_MAP.get(m.group(2).upper()[:3], "01")
        return f"{m.group(3)}-{mon}-{day}"
    return date_str


def _extract_foreign_currency(description: str) -> tuple[str, float | None, str | None]:
    """Extract foreign currency from description if present."""
    m = re.search(
        r"\s+(USD|EUR|GBP|AUD|INR|MYR|IDR|THB|JPY|HKD)\s+([\d,]+\.?\d*)\s*$",
        description,
    )
    if m:
        currency = m.group(1)
        amount = float(m.group(2).replace(",", ""))
        cleaned = description[: m.start()].strip()
        return cleaned, amount, currency
    return description, None, None


def detect_uob_xls(filepath: str) -> bool:
    """Check if an XLS file is a UOB credit card export."""
    path = Path(filepath)
    if path.suffix.lower() != ".xls":
        return False
    try:
        df = pd.read_excel(filepath)
        first_col = str(df.columns[0]) if len(df.columns) > 0 else ""
        return "United Overseas Bank" in first_col
    except Exception:
        return False


def parse_uob_xls(filepath: str) -> ParsedStatement:
    """Parse a UOB credit card XLS export."""
    path = Path(filepath)
    df = pd.read_excel(filepath)

    # Extract metadata from early rows
    account_number = ""
    account_type = ""
    statement_date_str = ""

    for _, row in df.iterrows():
        label = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
        value = str(row.iloc[1]).strip() if len(row) > 1 and pd.notna(row.iloc[1]) else ""

        if label == "Account Number:":
            account_number = value
        elif label == "Account Type:":
            account_type = value
        elif label == "Statement Date:":
            statement_date_str = value
        elif label == "Transaction Date":
            # Reached header row — data follows
            break

    # Build account name
    last_four = account_number[-4:] if account_number else ""
    account_name = f"UOB {account_type} {last_four}".strip()

    # Parse statement date
    stmt_date = _parse_date(statement_date_str) if statement_date_str else ""

    # Find data rows — after the header row
    header_idx = None
    for i, row in df.iterrows():
        if str(row.iloc[0]).strip() == "Transaction Date":
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(f"Could not find transaction header in {filepath}")

    transactions = []

    for i in range(header_idx + 1, len(df)):
        row = df.iloc[i]
        tx_date_val = row.iloc[0]

        if pd.isna(tx_date_val):
            continue

        tx_date_str = str(tx_date_val).strip()
        # Skip non-date rows (Previous Balance, Printed On, etc.)
        if not re.match(r"\d", tx_date_str):
            continue

        tx_date = _parse_date(tx_date_val)

        # Description (col 2) — may contain \n with Ref No
        description_raw = str(row.iloc[2]) if pd.notna(row.iloc[2]) else ""
        # Strip ref numbers
        description = re.sub(r"\n?Ref No:.*$", "", description_raw).strip()
        # Collapse whitespace
        description = re.sub(r"\s{2,}", " ", description)

        # Foreign currency (cols 3-4) and local amount (cols 5-6)
        foreign_currency = str(row.iloc[3]).strip() if pd.notna(row.iloc[3]) else ""
        foreign_amount_val = row.iloc[4] if len(row) > 4 and pd.notna(row.iloc[4]) else None
        local_amount_val = row.iloc[6] if len(row) > 6 and pd.notna(row.iloc[6]) else None

        if local_amount_val is None:
            continue

        amount_sgd = float(local_amount_val)

        # Negative = payment/credit in UOB convention
        is_payment = amount_sgd < 0 and "PAYMT" in description.upper()

        # Foreign currency handling
        amount_foreign = None
        currency_foreign = None
        if foreign_currency and foreign_amount_val is not None:
            currency_foreign = foreign_currency
            amount_foreign = float(foreign_amount_val)

        tx = ParsedTransaction(
            date=tx_date,
            description=description,
            amount_sgd=abs(amount_sgd) if amount_sgd >= 0 else amount_sgd,
            amount_foreign=amount_foreign,
            currency_foreign=currency_foreign,
            is_payment=is_payment,
            card_info=account_name,
        )
        transactions.append(tx)

    return ParsedStatement(
        statement_type="credit_card",
        statement_date=stmt_date,
        accounts=[account_name],
        filename=path.name,
        transactions=transactions,
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py parse_uob_xls.py <path_to_xls>")
        sys.exit(1)

    from parse_dbs import print_summary
    stmt = parse_uob_xls(sys.argv[1])
    print_summary(stmt)
