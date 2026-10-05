"""Parse DBS credit card and bank statement PDFs into structured transactions."""

import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

import money


@dataclass
class ParsedTransaction:
    """A single parsed transaction from a statement."""
    date: str              # YYYY-MM-DD
    description: str       # raw merchant/description text
    amount_minor: int      # whole minor units (cents); positive = expense, negative = credit
    amount_foreign: float | None = None
    currency_foreign: str | None = None
    is_payment: bool = False        # DEPRECATED — use flow_type (ADR v2)
    is_transfer: bool = False       # DEPRECATED — use flow_type (ADR v2)
    flow_type: str | None = None    # expense|income|transfer|payment|refund (set post-parse)
    card_info: str = ""    # which card this belongs to


@dataclass
class ParsedStatement:
    """Result of parsing a statement PDF."""
    statement_type: str    # 'credit_card' or 'bank'
    statement_date: str    # YYYY-MM-DD
    accounts: list[str] = field(default_factory=list)
    transactions: list[ParsedTransaction] = field(default_factory=list)
    filename: str = ""
    # The balances the source states, in whole minor units, signed as the
    # household sees them: cash positive, anything owed negative (a card's
    # amount owed is handed over negated). None where the source states none;
    # the import path checks the rows against them (tie.py), never the parser.
    opening_minor: int | None = None
    closing_minor: int | None = None
    opening_date: str | None = None   # YYYY-MM-DD, where the source gives it
    closing_date: str | None = None   # YYYY-MM-DD; always given with the balances
    # The currency the account is kept in, as a three-letter code. The rows
    # and the balances are whole minor units of it.
    currency: str = "SGD"
    # A card statement's sections, one per card, as parse_cc_statement read
    # them: what by_card splits the statement by. Not part of what the import
    # reads.
    card_sections: list = field(default_factory=list, repr=False)


# Month abbreviation → number
MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

# How far past the statement date a row may legitimately fall (late
# postings). A row whose month wrapped the year lands ~11 months ahead, so
# any slack well short of that separates the two cases.
LATE_POSTING_GRACE = timedelta(days=31)


DBS_SUPPLEMENTARY_CARDHOLDER_MAP = {
    ("7436", "BHARAT SURI"): "DBS Vantage Visa Infinite Card 3696",
    ("7436", "MILI KALE"): "DBS Vantage Visa Infinite Card 7436 (MK)",
}


def _normalize_card_header(card_header: str) -> str:
    """Normalize a DBS card header to a stable account label."""
    digits = re.findall(r"\d{4}", card_header)
    if digits:
        return f"{card_header.split('CARD NO')[0].strip()} {digits[-1]}"
    return card_header.strip()


def _subsection_account_label(base_card: str, holder_name: str) -> str:
    """Map known consolidated-card subsections to the correct live account."""
    digits = re.findall(r"\d{4}", base_card)
    last_four = digits[-1] if digits else ""
    return DBS_SUPPLEMENTARY_CARDHOLDER_MAP.get(
        (last_four, holder_name.upper().strip()),
        base_card,
    )


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


def _infer_tx_date(day: str, month: str, statement_date: str) -> str:
    """Date a DD MON row from a card statement that prints no year.

    The row takes the statement's year unless that would put it beyond the
    statement date plus late-posting grace; then it belongs to the year
    before (December rows of a January statement). Unknown statement dates
    keep the legacy 2024 default.
    """
    try:
        stmt = date.fromisoformat(statement_date)
    except ValueError:
        # "unknown" or a malformed header date: legacy stamping, no inference
        year = statement_date[:4] if statement_date[:4].isdigit() else "2024"
        return f"{year}-{month}-{day}"
    try:
        candidate = date(stmt.year, int(month), int(day))
    except ValueError:
        # 29 FEB against a non-leap statement year: no safe inference
        return f"{stmt.year}-{month}-{day}"
    if candidate > stmt + LATE_POSTING_GRACE:
        return f"{stmt.year - 1}-{month}-{day}"
    return f"{stmt.year}-{month}-{day}"


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

    current_card = ""
    current_tx_account = ""
    lines = all_text.split("\n")
    i = 0
    # Each card's section: its previous balance and its sub-total, as printed
    # (whole cents, owed positive, a credit balance negative), and the
    # accounts its rows went to. A card whose header repeats on a later page
    # is the same section.
    sections: dict[str, dict] = {}

    while i < len(lines):
        line = lines[i].strip()

        # Detect card section headers
        card_match = re.match(r"(DBS\s+.*?CARD\s+NO\.?:?\s*[\d\s]+)", line)
        if card_match:
            current_card = _normalize_card_header(card_match.group(1).strip())
            # Clean up card info — extract last 4 digits
            current_tx_account = current_card
            if current_card not in statement.accounts:
                statement.accounts.append(current_card)
            sections.setdefault(
                current_card,
                {"card": current_card, "previous": None, "sub_total": None, "accounts": []},
            )
            i += 1
            continue

        # A card section's balances: the previous statement's balance, and
        # the section's sub-total (the previous balance plus what is new).
        balance_match = re.match(
            r"(PREVIOUS BALANCE|SUB[\s-]*TOTAL)\s*:?\s+([\d,]+\.\d{2})\s*(CR)?$",
            line,
            re.IGNORECASE,
        )
        if balance_match and current_card:
            printed = money.parse_minor(balance_match.group(2))
            if balance_match.group(3):
                printed = -printed
            which = "previous" if balance_match.group(1).upper().startswith("PREV") else "sub_total"
            section = sections[current_card]
            if which == "previous" and section["previous"] is None:
                section["previous"] = printed
            elif which == "sub_total":
                section["sub_total"] = printed
            i += 1
            continue

        subsection_match = re.match(r"NEW TRANSACTIONS\s+(.+?)$", line, re.IGNORECASE)
        if subsection_match and current_card:
            current_tx_account = _subsection_account_label(
                current_card,
                subsection_match.group(1),
            )
            if current_tx_account not in statement.accounts:
                statement.accounts.append(current_tx_account)
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
            amount = money.parse_minor(tx_match.group(4))
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
                        for skip in ["CARD", "DBS", "PREVIOUS", "STATEMENT", "PAGE", "PDS_",
                                     "TOTAL", "BALANCE"]
                    ):
                        amount_foreign = float(fx_match.group(2).replace(",", ""))
                        i += 1  # skip the foreign currency line
                    else:
                        currency_foreign = None

            tx = ParsedTransaction(
                date=_infer_tx_date(day, month, statement.statement_date),
                description=description,
                amount_minor=amount,
                amount_foreign=amount_foreign,
                currency_foreign=currency_foreign,
                is_payment=is_payment,
                card_info=current_tx_account or current_card,
            )
            statement.transactions.append(tx)
            if current_card and tx.card_info not in sections[current_card]["accounts"]:
                sections[current_card]["accounts"].append(tx.card_info)

        i += 1

    pdf.close()
    statement.card_sections = list(sections.values())
    return statement


def _split_by_cardholder(section: dict) -> bool:
    """Whether a card section's rows went to more than its own card's account:
    the DBS Vantage card, whose two cardholders' subsections are filed to two
    accounts (DBS_SUPPLEMENTARY_CARDHOLDER_MAP) under one statement balance.

    Ruling 3 (how that bill is held) is pending, so such a section hands over
    no balance and its rows are filed exactly as before: by cardholder, not
    checked. This is the one place that decides it:
      - "one bill, one account": file the supplementary cardholder's rows to
        the card's own account (the map, and handle_vantage_split for the CSV
        export) and drop this exclusion; the section then ties and anchors
        like any other.
      - "split per cardholder": the one balance belongs to two accounts; the
        section would be tied as a whole and its anchor needs a home both
        accounts count toward (a parent account, or the supplementary
        account read as part of the main card's balance), which the balance
        sheet does not have yet.
    """
    return any(account != section["card"] for account in section["accounts"])


def by_card(statement: ParsedStatement) -> list[ParsedStatement]:
    """A card statement as the import takes it: one statement per card
    section that prints both its previous balance and its sub-total, with
    that card's rows and its balances (signed as the household sees them:
    owed negative), closing on the statement date; and, when anything is
    left, the rest as one statement with no balance, as before. A section
    split by cardholder (_split_by_cardholder) is always in the rest. A
    statement with no section that states its balances is returned as it is.
    """
    try:
        date.fromisoformat(statement.statement_date)
    except (TypeError, ValueError):
        return [statement]
    stated = [
        section for section in statement.card_sections
        if section["previous"] is not None and section["sub_total"] is not None
        and not _split_by_cardholder(section)
    ]
    if not stated:
        return [statement]

    split_off = {section["card"] for section in stated}
    cards = [
        ParsedStatement(
            statement_type=statement.statement_type,
            statement_date=statement.statement_date,
            accounts=[section["card"]],
            transactions=[tx for tx in statement.transactions if tx.card_info == section["card"]],
            filename=statement.filename,
            opening_minor=-section["previous"],
            closing_minor=-section["sub_total"],
            closing_date=statement.statement_date,
            currency=statement.currency,
        )
        for section in stated
    ]
    rest_rows = [tx for tx in statement.transactions if tx.card_info not in split_off]
    rest_accounts = [name for name in statement.accounts if name not in split_off]
    if rest_rows or rest_accounts:
        cards.append(ParsedStatement(
            statement_type=statement.statement_type,
            statement_date=statement.statement_date,
            accounts=rest_accounts,
            transactions=rest_rows,
            filename=statement.filename,
            currency=statement.currency,
            card_sections=[s for s in statement.card_sections if s["card"] not in split_off],
        ))
    return cards


# How far, in whole minor units, a balance movement may sit from the amount
# printed on its line and still be read as that line's movement.
BALANCE_MATCH_WITHIN = 1


def _direction_from_balance(
    prev_balance: int | None,
    new_balance: int,
    candidate_amounts: list[int],
) -> tuple[int | None, int | None]:
    """Resolve (withdrawal, deposit) from a running-balance delta.

    Every figure is in whole minor units. Returns (withdrawal, deposit) where
    exactly one is set, or (None, None) when the delta doesn't reconcile
    against any amount printed on the line (caller falls back to its keyword
    heuristic).
    """
    if prev_balance is None:
        return None, None
    delta = new_balance - prev_balance
    if not any(abs(abs(delta) - amt) <= BALANCE_MATCH_WITHIN for amt in candidate_amounts):
        return None, None
    if delta < 0:
        return -delta, None
    return None, delta


def parse_bank_statement(pdf_path: str) -> ParsedStatement:
    """Parse a DBS bank/savings statement PDF.

    DBS bank statement format:
    - "Date Description Withdrawal (-) Deposit (+) Balance"
    - Date: DD/MM/YYYY
    - Multi-line descriptions (PayNow has TO: lines, etc.)
    - Amount columns are positional

    A statement of one account hands over its first "Balance Brought Forward"
    and last "Balance Carried Forward" as its opening and closing balance, the
    closing dated by the statement date; the import path checks the rows
    against them (tie.py). With more than one account number in the file, a
    balance line missing or no statement date, it states no balance.
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
    prev_balance = None
    opening = None   # the first balance brought forward
    closing = None   # the last balance carried forward

    while i < len(lines):
        line = lines[i].strip()

        # Running balance anchor — restarts at each page/section header
        bf_match = re.search(
            r"Balance Brought Forward(?:\s+SGD)?\s+([\d,]+\.\d{2})", line
        )
        if bf_match:
            prev_balance = money.parse_minor(bf_match.group(1))
            if opening is None:
                opening = prev_balance
            i += 1
            continue

        cf_match = re.search(
            r"Balance Carried Forward(?:\s+SGD)?\s+([\d,]+\.\d{2})", line
        )
        if cf_match:
            closing = money.parse_minor(cf_match.group(1))

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
                # Last amount is always the running balance. Direction comes
                # from the balance delta — the printed columns merge under
                # pdfplumber, so keyword guessing mis-signs deposits.
                balance = money.parse_minor(amounts[-1])
                candidates = [money.parse_minor(a) for a in amounts[:-1]]
                current_withdrawal, current_deposit = _direction_from_balance(
                    prev_balance, balance, candidates
                )
                if current_withdrawal is None and current_deposit is None:
                    # No balance anchor or delta doesn't reconcile —
                    # legacy fallback: treat first amount as withdrawal
                    current_withdrawal = candidates[0]
                prev_balance = balance

        elif current_date and line and not line.startswith("Balance") and not line.startswith("Total Balance") and not line.startswith("PDS_"):
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

    try:
        closing_date = date.fromisoformat(statement.statement_date).isoformat()
    except ValueError:
        closing_date = None
    one_account = len(set(re.findall(r"Account No\.\s*([\d-]+)", all_text))) == 1
    if one_account and opening is not None and closing is not None and closing_date:
        statement.opening_minor = opening
        statement.closing_minor = closing
        statement.closing_date = closing_date
    return statement


def _save_bank_tx(
    statement: ParsedStatement,
    date: str,
    desc_lines: list[str],
    withdrawal: int | None,
    deposit: int | None,
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
        amount_minor=amount,
        is_payment=is_payment,
        is_transfer=is_transfer,
        card_info=statement.accounts[0] if statement.accounts else "",
    )
    statement.transactions.append(tx)


def parse_statement(pdf_path: str) -> ParsedStatement | list[ParsedStatement]:
    """Auto-detect and parse a DBS statement PDF. A card statement comes back
    as a list, one statement per card that states its balances (by_card)."""
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Read first page to detect type
    pdf = pdfplumber.open(str(path))
    first_page = pdf.pages[0].extract_text() or ""
    pdf.close()

    stmt_type = _detect_statement_type(first_page)

    if stmt_type == "credit_card":
        return by_card(parse_cc_statement(pdf_path))
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
    expenses = [t for t in statement.transactions if not t.is_payment and not t.is_transfer and t.amount_minor > 0]
    credits = [t for t in statement.transactions if t.amount_minor < 0]
    payments = [t for t in statement.transactions if t.is_payment]

    total_expenses = money.from_minor(sum(t.amount_minor for t in expenses))
    total_credits = money.from_minor(sum(abs(t.amount_minor) for t in credits))

    print(f"\nExpenses: {len(expenses)} transactions, SGD {total_expenses:,.2f}")
    print(f"Credits/Refunds: {len(credits)} transactions, SGD {total_credits:,.2f}")
    print(f"Payments: {len(payments)}")
    print(f"{'='*70}")

    print(f"\n{'DATE':<12} {'AMOUNT':>10} {'DESCRIPTION'}")
    print("-" * 70)
    for tx in expenses:
        fx = f" ({tx.currency_foreign} {tx.amount_foreign:,.2f})" if tx.amount_foreign else ""
        print(f"{tx.date:<12} {money.from_minor(tx.amount_minor):>10,.2f} {tx.description[:45]}{fx}")

    if credits:
        print(f"\n--- Credits/Refunds ---")
        for tx in credits:
            print(f"{tx.date:<12} {money.from_minor(tx.amount_minor):>10,.2f} {tx.description[:45]}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py parse_dbs.py <path_to_pdf>")
        sys.exit(1)

    parsed = parse_statement(sys.argv[1])
    for stmt in parsed if isinstance(parsed, list) else [parsed]:
        print_summary(stmt)
