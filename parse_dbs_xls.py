"""Parse DBS Business Banking XLS exports into structured transactions.

DBS Business XLS format (real BIFF .xls):
  Row 0 header: "Account Details for :", "COMPANY NAME ACCOUNT_NO CURRENCY"
  Row 0: Statement as at : DD-MMM-YYYY, To : DD-MMM-YYYY
  Row 1-3: Opening/Ledger/Available Balance
  Row 4: Header (Date, Value Date, Transaction Description 1, Description 2, Debit, Credit, Running Balance)
  Row 5+: Transactions
  Last rows: Printed By/On metadata
"""

import re
import sys
from pathlib import Path

import pandas as pd

from parse_dbs import ParsedTransaction, ParsedStatement, MONTH_MAP


def _parse_date(date_val) -> str:
    """Parse date from XLS cell — could be 'DD-MMM-YYYY' string or datetime."""
    if hasattr(date_val, "strftime"):
        return date_val.strftime("%Y-%m-%d")
    date_str = str(date_val).strip()
    # "04-Mar-2026"
    m = re.match(r"(\d{2})-(\w{3})-(\d{4})", date_str)
    if m:
        day = m.group(1)
        mon = MONTH_MAP.get(m.group(2).upper()[:3], "01")
        return f"{m.group(3)}-{mon}-{day}"
    return date_str


def detect_dbs_xls(filepath: str) -> bool:
    """Check if an XLS file is a DBS Business Banking export."""
    path = Path(filepath)
    if path.suffix.lower() != ".xls":
        return False
    try:
        df = pd.read_excel(filepath)
        first_col = str(df.columns[0]) if len(df.columns) > 0 else ""
        return "Account Details for" in first_col
    except Exception:
        return False


def parse_dbs_xls(filepath: str) -> ParsedStatement:
    """Parse a DBS Business Banking XLS export."""
    path = Path(filepath)
    df = pd.read_excel(filepath)

    # Extract account name from column header
    # Column 1 value is like "KALESH INC PTE. LTD. 0725605300 SGD"
    account_info = str(df.columns[1]) if len(df.columns) > 1 else ""
    # Extract account number from the string
    acct_match = re.search(r"(\d{10,})", account_info)
    account_number = acct_match.group(1) if acct_match else ""
    account_name = account_info.strip()

    # Find the header row (contains "Date", "Value Date", etc.)
    header_idx = None
    for i, row in df.iterrows():
        if str(row.iloc[0]).strip() == "Date":
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(f"Could not find transaction header in {filepath}")

    # Extract statement period from earlier rows
    stmt_date = ""
    for i in range(header_idx):
        row = df.iloc[i]
        label = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
        if label == "Statement as at :":
            # "To :" is in col 2, end date in col 3
            end_date = row.iloc[3] if len(row) > 3 and pd.notna(row.iloc[3]) else ""
            if end_date:
                stmt_date = _parse_date(end_date)
            break

    transactions = []

    for i in range(header_idx + 1, len(df)):
        row = df.iloc[i]
        date_val = row.iloc[0]

        if pd.isna(date_val):
            continue

        date_str = str(date_val).strip()
        # Skip metadata rows (Printed By, Printed On)
        if date_str in ("Printed By", "Printed On") or not re.match(r"\d", date_str):
            continue

        tx_date = _parse_date(date_val)

        # Description from cols 2 + 3
        desc1 = str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else ""
        desc2 = str(row.iloc[3]).strip() if len(row) > 3 and pd.notna(row.iloc[3]) else ""
        description = f"{desc1} | {desc2}".strip(" |") if desc2 else desc1

        # Debit (col 4) and Credit (col 5)
        debit = row.iloc[4] if len(row) > 4 and pd.notna(row.iloc[4]) else None
        credit = row.iloc[5] if len(row) > 5 and pd.notna(row.iloc[5]) else None

        if debit is not None:
            amount_sgd = float(debit)
        elif credit is not None:
            amount_sgd = -float(credit)
        else:
            continue

        desc_upper = description.upper()
        is_payment = "BILL PAYMENT" in desc_upper
        is_transfer = (
            "SALARY" in desc_upper
            or "FAST PAYMENT" in desc_upper
            or "FUNDS TRANSFER" in desc_upper
            or amount_sgd < 0  # credits/deposits
        )

        tx = ParsedTransaction(
            date=tx_date,
            description=description,
            amount_sgd=amount_sgd,
            is_payment=is_payment,
            is_transfer=is_transfer,
            card_info=account_name,
        )
        transactions.append(tx)

    return ParsedStatement(
        statement_type="bank",
        statement_date=stmt_date,
        accounts=[account_name],
        filename=path.name,
        transactions=transactions,
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py parse_dbs_xls.py <path_to_xls>")
        sys.exit(1)

    from parse_dbs import print_summary
    stmt = parse_dbs_xls(sys.argv[1])
    print_summary(stmt)
