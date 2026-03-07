"""Parse Citi credit card CSV exports into structured transactions.

Citi CSV format (no header row):
  "DD/MM/YYYY","Description","Amount","","'CardNumber'"

Amount convention: negative = expense, positive = payment/credit.
Foreign currency info embedded in description (e.g., "USD 49.00 USD 49.00").
"""

import csv
import re
import sys
from pathlib import Path

from parse_dbs import ParsedTransaction, ParsedStatement


def _parse_date(date_str: str) -> str:
    """Parse 'DD/MM/YYYY' to 'YYYY-MM-DD'."""
    date_str = date_str.strip()
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", date_str)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return date_str


def _extract_foreign_currency(description: str) -> tuple[str, float | None, str | None]:
    """Extract foreign currency from Citi descriptions.

    Citi format embeds FX like: "MERCHANT LOCATION USD 49.00 USD 49.00"
    The currency and amount appear twice (original + billed). We take the first.
    Also handles: "XXXX-XXXX-XXXX-9923 USD 49.00 USD 49.00"
    """
    # Pattern: CURRENCY AMOUNT CURRENCY AMOUNT at end of description
    m = re.search(
        r"\s+(USD|EUR|GBP|AUD|INR|MYR|IDR|THB|JPY|HKD|NZD|TWD|KRW|CNY|PHP|VND|CAD|CHF|SEK|NOK|DKK)\s+"
        r"([\d,]+\.\d{2})\s+"
        r"(?:USD|EUR|GBP|AUD|INR|MYR|IDR|THB|JPY|HKD|NZD|TWD|KRW|CNY|PHP|VND|CAD|CHF|SEK|NOK|DKK)\s+"
        r"[\d,]+\.\d{2}\s*$",
        description,
    )
    if m:
        currency = m.group(1)
        amount = float(m.group(2).replace(",", ""))
        cleaned = description[: m.start()].strip()
        return cleaned, amount, currency

    # Single currency mention (e.g., "CCY CONVERSION FEE SGD 40.20")
    m = re.search(
        r"\s+(USD|EUR|GBP|AUD|SGD)\s+([\d,]+\.\d{2})\s*$",
        description,
    )
    if m:
        currency = m.group(1)
        amount = float(m.group(2).replace(",", ""))
        cleaned = description[: m.start()].strip()
        # Don't treat SGD amounts as foreign currency
        if currency == "SGD":
            return description, None, None
        return cleaned, amount, currency

    return description, None, None


def _clean_description(description: str) -> str:
    """Clean up Citi description formatting.

    Citi descriptions have lots of extra whitespace between fields.
    Also strip masked card numbers like XXXX-XXXX-XXXX-9923.
    """
    # Remove masked card numbers
    description = re.sub(r"\s*XXXX-XXXX-XXXX-\d{4}\s*", " ", description)
    # Collapse multiple spaces
    description = re.sub(r"\s{2,}", " ", description).strip()
    return description


def _extract_card_last_four(card_field: str) -> str:
    """Extract last 4 digits from Citi card field like \"'5425504000682531'\"."""
    digits = re.findall(r"\d+", card_field)
    if digits:
        full_num = digits[0]
        return full_num[-4:]
    return ""


def detect_citi_csv(filepath: str) -> bool:
    """Check if a CSV file is in Citi format (headerless, 5 columns)."""
    path = Path(filepath)
    with open(path, encoding="utf-8-sig") as f:
        first_line = f.readline().strip()

    # Citi CSVs start with a date in DD/MM/YYYY format, no header row
    if re.match(r'^"\d{2}/\d{2}/\d{4}"', first_line):
        return True
    return False


def parse_citi_csv(filepath: str) -> ParsedStatement:
    """Parse a Citi credit card CSV export.

    No header row. Columns:
    0: Date (DD/MM/YYYY)
    1: Description
    2: Amount (negative = expense, positive = payment)
    3: (empty)
    4: Card number (quoted with single quotes inside double quotes)
    """
    path = Path(filepath)
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        raise ValueError(f"Empty CSV: {filepath}")

    # Extract card number from first row to build account name
    card_field = rows[0][4] if len(rows[0]) > 4 else ""
    last_four = _extract_card_last_four(card_field)

    # Determine card name from filename or last four
    # Citi files are named like ACCT_531_07_12_2025.csv
    stem = path.stem  # e.g., "ACCT_531_07_12_2025"
    account_name = f"Citi Card {last_four}" if last_four else f"Citi Card ({stem})"

    statement = ParsedStatement(
        statement_type="credit_card",
        statement_date="",
        accounts=[account_name],
        filename=path.name,
    )

    dates = []

    for row in rows:
        if len(row) < 3:
            continue

        date_str = row[0].strip()
        if not re.match(r"\d{2}/\d{2}/\d{4}", date_str):
            continue

        tx_date = _parse_date(date_str)
        dates.append(tx_date)

        description_raw = row[1].strip()
        amount_str = row[2].strip()

        if not amount_str:
            continue

        # Citi: negative = expense, positive = payment/credit
        # Our convention: positive = expense, negative = credit
        amount_sgd = -float(amount_str.replace(",", ""))

        # Clean description and extract FX info
        description = _clean_description(description_raw)
        description, amount_foreign, currency_foreign = _extract_foreign_currency(description)

        # Detect payments
        is_payment = "PAYMENT" in description.upper()

        tx = ParsedTransaction(
            date=tx_date,
            description=description,
            amount_sgd=amount_sgd,
            amount_foreign=amount_foreign,
            currency_foreign=currency_foreign,
            is_payment=is_payment,
            card_info=account_name,
        )
        statement.transactions.append(tx)

    if dates:
        statement.statement_date = max(dates)

    return statement


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py parse_citi_csv.py <path_to_csv>")
        sys.exit(1)

    from parse_dbs import print_summary
    stmt = parse_citi_csv(sys.argv[1])
    print_summary(stmt)
