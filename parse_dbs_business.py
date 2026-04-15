"""Parse DBS Business Multi-Currency Account statements (Kalesh Inc).

Format: "Details Of Your DBS Business/Corporate Multi-Currency Account"
SGD-only account. Single account per statement. Withdrawal vs Deposit
disambiguated via running balance delta (more robust than column positioning).
"""

import re
from pathlib import Path

import pdfplumber

from parse_dbs import MONTH_MAP, ParsedStatement, ParsedTransaction


TX_LINE_RE = re.compile(
    r"^(\d{2})-(\w{3})-(\d{2})\s+(\d{2})-(\w{3})-(\d{2})\s+(.*)$"
)
AMOUNT_RE = re.compile(r"^-?[\d,]+\.\d{2}$")


def detect_dbs_business_pdf(filepath: str) -> bool:
    """Detect the Kalesh Business Multi-Currency format."""
    try:
        with pdfplumber.open(filepath) as pdf:
            text = pdf.pages[0].extract_text() or ""
    except Exception:
        return False
    return "Multi-Currency Account" in text and "Details Of Your DBS" in text


def _parse_date(day: str, mon: str, yy: str) -> str:
    mm = MONTH_MAP.get(mon.upper()[:3], "01")
    return f"20{yy}-{mm}-{day}"


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def _split_amounts(tail: str) -> tuple[str, list[float]]:
    """Strip trailing amount tokens from a line; return (description, amounts)."""
    tokens = tail.rsplit(None, 3)
    amounts: list[float] = []
    desc_parts: list[str] = list(tokens)
    while desc_parts and AMOUNT_RE.match(desc_parts[-1]):
        amounts.insert(0, _to_float(desc_parts.pop()))
    return " ".join(desc_parts).strip(), amounts


def _classify(description: str, is_deposit: bool) -> tuple[bool, bool]:
    """Return (is_transfer, is_payment) flags for a transaction."""
    upper = description.upper()
    is_transfer = False
    is_payment = False
    if "TRANSFER OF FUND" in upper and "SURI BHARAT" in upper:
        is_transfer = True
    elif "INWARD PAYNOW" in upper and "SURI BHARAT" in upper:
        is_transfer = True
    elif is_deposit and "CASH REBATE" in upper:
        is_payment = True
    return is_transfer, is_payment


def parse_dbs_business_pdf(filepath: str) -> ParsedStatement:
    path = Path(filepath)
    with pdfplumber.open(filepath) as pdf:
        full_text = "\n".join((page.extract_text() or "") for page in pdf.pages)

    period = re.search(
        r"(\d{2})-(\w{3})-(\d{4})\s+to\s+(\d{2})-(\w{3})-(\d{4})",
        full_text,
    )
    if not period:
        raise ValueError(f"Could not find statement period in {path.name}")
    stmt_date = f"{period.group(6)}-{MONTH_MAP[period.group(5).upper()[:3]]}-01"

    acct_match = re.search(r"Account No:\s*([\d-]+)", full_text)
    account_no = acct_match.group(1) if acct_match else ""
    compact = account_no.replace("-", "")
    account_name = f"DBS Business {compact}" if compact else "DBS Business"

    lines = full_text.split("\n")
    txns: list[ParsedTransaction] = []
    running_balance: float | None = None
    current: dict | None = None

    def finalize(entry: dict) -> None:
        if running_balance is None or entry.get("new_balance") is None:
            return
        amount = entry["amount"]
        new_bal = entry["new_balance"]
        prev_bal = entry["prev_balance"]
        delta = round(new_bal - prev_bal, 2)
        is_deposit = delta > 0 or (delta == 0 and entry.get("explicit_deposit"))
        description = " ".join(entry["desc_parts"]).strip()
        is_transfer, is_payment = _classify(description, is_deposit)
        signed = -amount if is_deposit else amount
        txns.append(
            ParsedTransaction(
                date=entry["date"],
                description=description,
                amount_sgd=signed,
                is_payment=is_payment,
                is_transfer=is_transfer,
                card_info=account_name,
            )
        )

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        if line.startswith("Balance Brought Forward"):
            m = re.search(r"([\d,]+\.\d{2})", line)
            if m:
                running_balance = _to_float(m.group(1))
            current = None
            continue

        if line.startswith(("Balance Carried Forward", "Total ", "Currency:")):
            if current:
                finalize(current)
                current = None
            continue

        m = TX_LINE_RE.match(line)
        if m:
            if current:
                finalize(current)
            date = _parse_date(m.group(1), m.group(2), m.group(3))
            desc_tail = m.group(7)
            desc, amounts = _split_amounts(desc_tail)
            if len(amounts) >= 2:
                amount, new_balance = amounts[0], amounts[1]
            elif len(amounts) == 1:
                amount, new_balance = amounts[0], None
            else:
                amount, new_balance = 0.0, None
            prev_balance = running_balance if running_balance is not None else 0.0
            current = {
                "date": date,
                "desc_parts": [desc] if desc else [],
                "amount": amount,
                "new_balance": new_balance,
                "prev_balance": prev_balance,
            }
            if new_balance is not None:
                running_balance = new_balance
            continue

        if current is not None:
            current["desc_parts"].append(line)

    if current:
        finalize(current)

    return ParsedStatement(
        statement_type="bank",
        statement_date=stmt_date,
        accounts=[account_name],
        filename=path.name,
        transactions=txns,
    )
