"""Parse Citi Singapore PDF statements (credit card + Citi Plus checking).

Citi Credit Card bill format (Citi Prestige, Citi Rewards):
  - Page 1: "YOUR BILL SUMMARY", "Statement Date June 05, 2026"
  - Page 2+ header: "CITI PRESTIGE CARD #### #### #### #### Payment Due Date: ..."
    (the principal card number — identifies the account)
  - Summary row: PREVIOUS BALANCE - PAYMENTS & CREDITS + PURCHASES & ADVANCES
    + INTEREST CHARGES + FEES & CHARGES = CURRENT BALANCE (six amounts)
  - Transactions between "BALANCE PREVIOUS STATEMENT" and "GRAND TOTAL":
    "DD MMM DESCRIPTION AMOUNT", credits in parentheses "(500.00)"
  - Per-cardholder sections: "CITI PRESTIGE CARD #### ... #### - HOLDER NAME"
    (supplementary cards appear as their own section)
  - Foreign spend: next line "FOREIGN AMOUNT <CURRENCY NAME> <AMOUNT>"

Citi Plus account statement format:
  - Page 1: "SUMMARY OF YOUR CITI PLUS ACCOUNT", "... as of Jun 30 2026 ..."
  - "Your Checking Details" then "<Product> <10-digit acct> SGD"
  - Rows: "Mon DD YYYY Mon DD YYYY DESCRIPTION AMOUNT BALANCE" — one amount
    column, so direction comes from the running balance
  - "OPENING BALANCE" / "CLOSING BALANCE" rows, then "TOTAL <debits> <credits>"
  - Savings & Investments section follows — out of scope, not parsed

Text is extracted with x_tolerance=1.5: pdfplumber's default (3) glues Citi's
tightly-kerned words together ("BALANCEPREVIOUSSTATEMENT"), which would make
descriptions differ from the Citi CSV export and break merchant rules.

Both parsers hand over the statement's opening and closing balance, signed as
the household sees them (a card's balance owed is negative). Whether the rows
carry the one to the other is checked by the import path (tie.py), which
refuses a statement that does not reconcile — a short import is worse than
none. The checking parser still raises ValueError itself when a row disagrees
with its running balance or the rows disagree with the statement's TOTAL line.
"""

import re
import sys
from pathlib import Path

import pdfplumber

import money
from parse_dbs import ParsedTransaction, ParsedStatement, MONTH_MAP, _direction_from_balance

TEXT_X_TOLERANCE = 1.5

FULL_MONTHS = {
    "JANUARY": "01", "FEBRUARY": "02", "MARCH": "03", "APRIL": "04",
    "MAY": "05", "JUNE": "06", "JULY": "07", "AUGUST": "08",
    "SEPTEMBER": "09", "OCTOBER": "10", "NOVEMBER": "11", "DECEMBER": "12",
}

# Currency names as Citi prints them on "FOREIGN AMOUNT" lines → ISO code.
# Only names seen on real statements; anything else keeps Citi's own name
# (amount is still captured) until a statement shows us the spelling.
FOREIGN_CURRENCY_NAMES = {
    "U.S. DOLLAR": "USD",
    "RUPIAH": "IDR",
    "BAHT": "THB",
}

AMOUNT = r"\(?-?[\d,]+\.\d{2}\)?"


def _amount(text: str) -> int:
    """Parse '1,234.56', '(500.00)' or '-12.50' into whole minor units.
    Parentheses mean credit."""
    text = text.strip()
    negative = text.startswith("(") or text.startswith("-")
    value = money.parse_minor(text.strip("()-"))
    return -value if negative else value


def _read_pages(filepath: str) -> list[str]:
    """Every page's text. Any failure becomes one fixed-message ValueError.

    The upload route echoes str(e), and pdfminer messages can quote raw tokens
    from the file. The raise sits outside the except block so the original
    exception is not kept on __context__ either.
    """
    unreadable = False
    try:
        with pdfplumber.open(filepath) as pdf:
            pages = [(p.extract_text(x_tolerance=TEXT_X_TOLERANCE) or "") for p in pdf.pages]
    except Exception:
        unreadable = True
    if unreadable:
        raise ValueError("Citi PDF: could not read page text")
    return pages


def _compact(text: str) -> str:
    """Upper-case and strip whitespace — Citi headings vary in spacing."""
    return re.sub(r"\s+", "", text).upper()


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def classify_citi_text(page1_text: str) -> str | None:
    """Classify page-1 text: 'credit_card', 'bank' (Citi Plus), or None."""
    compact = _compact(page1_text)
    if "CITI" not in compact:
        return None
    if re.search(r"SUMMARYOFYOURCITI\w*ACCOUNT", compact):
        return "bank"
    if "YOURBILLSUMMARY" in compact and "CITIBANK" in compact:
        return "credit_card"
    return None


def detect_citi_pdf(filepath: str) -> str | None:
    """Detect a Citi PDF statement. Returns 'credit_card', 'bank', or None."""
    try:
        with pdfplumber.open(filepath) as pdf:
            text = pdf.pages[0].extract_text(x_tolerance=TEXT_X_TOLERANCE) or ""
        return classify_citi_text(text)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Credit-card bills
# ---------------------------------------------------------------------------

CARD_HEADER_RE = re.compile(r"CITI\s+[A-Z ]+?\s+((?:\d{4}\s?){3}\d{4})\s+Payment Due Date")
CARD_SECTION_RE = re.compile(r"^CITI\s+[A-Z ]+?\s+(?:\d{4}\s?){3}\d{4}\s+-\s")
CARD_ROW_RE = re.compile(rf"^(\d{{1,2}})\s?([A-Z]{{3}})\s+(.+?)\s+({AMOUNT})$")
CARD_FX_RE = re.compile(r"^FOREIGN AMOUNT\s+(.+?)\s+([\d,]+\.\d{2})$")
CARD_SUMMARY_RE = re.compile(rf"^({AMOUNT})" + rf"\s+({AMOUNT})" * 5 + r"$")


def _card_statement_date(text: str) -> tuple[int, int, str]:
    """'Statement Date June 05, 2026' → (month, year, 'YYYY-MM-DD')."""
    m = re.search(r"Statement Date:?\s*([A-Za-z]+)\s*(\d{1,2}),\s*(\d{4})", text)
    if not m:
        raise ValueError("Citi card statement: statement date not found")
    mon = FULL_MONTHS.get(m.group(1).upper())
    if not mon:
        raise ValueError("Citi card statement: unrecognised statement month")
    return int(mon), int(m.group(3)), f"{m.group(3)}-{mon}-{m.group(2).zfill(2)}"


def _card_summary(text: str) -> dict:
    """Six-amount summary row under the PREVIOUS ... CURRENT heading."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("PREVIOUS") and "CURRENT" in line:
            for candidate in lines[i + 1:i + 4]:
                m = CARD_SUMMARY_RE.match(candidate.strip())
                if m:
                    prev, pay, purch, interest, fees, current = (_amount(g) for g in m.groups())
                    return {
                        "previous": prev, "payments": pay, "purchases": purch,
                        "interest": interest, "fees": fees, "current": current,
                    }
    raise ValueError("Citi card statement: balance summary not found")


def parse_citi_card_pages(pages: list[str], filename: str = "") -> ParsedStatement:
    """Parse a Citi credit-card bill from its page texts."""
    text = "\n".join(pages)
    stmt_month, stmt_year, stmt_date = _card_statement_date(text)

    m = CARD_HEADER_RE.search(text)
    if not m:
        raise ValueError("Citi card statement: card number not found")
    last_four = re.sub(r"\D", "", m.group(1))[-4:]
    # Same name form as parse_citi_csv so ensure_account finds the account
    # the CSV path already created (exact name, else last four).
    account_name = f"Citi Card {last_four}"

    summary = _card_summary(text)

    transactions = []
    in_transactions = False
    for raw in text.split("\n"):
        line = raw.strip()
        if line.startswith("BALANCE PREVIOUS STATEMENT"):
            in_transactions = True
            continue
        if line.startswith("GRAND TOTAL"):
            in_transactions = False
            continue
        if not in_transactions:
            continue

        # Cardholder section headers — supplementary-card rows stay on the
        # principal account (one bill, one account); see module docstring.
        if CARD_SECTION_RE.match(line):
            continue

        fx = CARD_FX_RE.match(line)
        if fx and transactions:
            name = fx.group(1).strip()
            transactions[-1].amount_foreign = float(fx.group(2).replace(",", ""))
            transactions[-1].currency_foreign = FOREIGN_CURRENCY_NAMES.get(name, name)
            continue

        row = CARD_ROW_RE.match(line)
        if not row:
            continue  # SUB-TOTAL, page headers/footers, column headings

        day, mon_abbr, description, amount_str = row.groups()
        mon = MONTH_MAP.get(mon_abbr)
        if not mon:
            continue
        # Rows from a month after the statement month belong to last year
        # (December rows on a January bill).
        year = stmt_year - 1 if int(mon) > stmt_month else stmt_year

        # Citi prints charges positive and credits in parentheses, which is
        # already our convention: positive = expense, negative = credit.
        amount_minor = _amount(amount_str)
        transactions.append(ParsedTransaction(
            date=f"{year}-{mon}-{day.zfill(2)}",
            description=description.strip(),
            amount_minor=amount_minor,
            is_payment="PAYMENT" in description.upper(),
            card_info=account_name,
        ))

    # Previous balance + every billed row must land on the current balance,
    # to the cent: the import path checks it (tie.py). Citi prints what is
    # owed as a positive figure; a balance owed is stored negative.
    return ParsedStatement(
        statement_type="credit_card",
        statement_date=stmt_date,
        accounts=[account_name],
        filename=filename,
        transactions=transactions,
        opening_minor=-summary["previous"],
        closing_minor=-summary["current"],
        closing_date=stmt_date,
    )


# ---------------------------------------------------------------------------
# Citi Plus checking
# ---------------------------------------------------------------------------

CHK_DATE = r"([A-Z][a-z]{2})\s(\d{1,2})\s(\d{4})"
CHK_ACCOUNT_RE = re.compile(r"^(.+?)\s+(\d{6,})\s+SGD(?:\s+\(continued\))?$")
CHK_ROW_RE = re.compile(rf"^{CHK_DATE}\s+{CHK_DATE}\s+(.+?)\s+({AMOUNT})\s+({AMOUNT})$")
CHK_OPENING_RE = re.compile(rf"^{CHK_DATE}\s+{CHK_DATE}\s+OPENING BALANCE\s+({AMOUNT})$")
CHK_CLOSING_RE = re.compile(rf"^{CHK_DATE}\s+CLOSING BALANCE\s+({AMOUNT})$")
CHK_TOTAL_RE = re.compile(rf"^TOTAL\s+({AMOUNT})\s+({AMOUNT})$")

# Lines that are page furniture, never a description continuation. The page
# header carries the holder's name, so it must never be appended to a row.
CHK_FURNITURE_RES = (
    re.compile(r"^Page \d+ of \d+$"),
    re.compile(rf"{CHK_DATE}\s+-\s+{CHK_DATE}$"),      # page header with period
    re.compile(r"^\S*\d\S*/\S+/\S+$"),                  # footer document code
    re.compile(r"^Transactions Done$"),
    CHK_ACCOUNT_RE,
)


def _chk_date(mon: str, day: str, year: str) -> str:
    # No fallback month: reconciliation never checks dates, so a guessed
    # month would be saved silently.
    month = MONTH_MAP.get(mon.upper())
    if month is None:
        raise ValueError("Citi PDF: unrecognised month in transaction date")
    return f"{year}-{month}-{day.zfill(2)}"


def parse_citi_checking_pages(pages: list[str], filename: str = "") -> ParsedStatement:
    """Parse the Checking section of a Citi Plus account statement."""
    text = "\n".join(pages)

    m = re.search(rf"as of {CHK_DATE}", text)
    if not m:
        raise ValueError("Citi Plus statement: statement date not found")
    stmt_date = _chk_date(*m.groups())

    if "Your Checking Details" not in text:
        raise ValueError("Citi Plus statement: no Checking section")
    section = text.split("Your Checking Details", 1)[1]
    section = section.split("Your Savings & Investments Details", 1)[0]

    account_number = ""
    opening = closing = None
    opening_date = closing_date = None
    totals = None
    balance = None
    transactions = []
    continuing = False  # True while lines directly after a row may extend it

    for raw in section.split("\n"):
        line = raw.strip()

        acct = CHK_ACCOUNT_RE.match(line)
        if acct and not account_number:
            account_number = acct.group(2)

        if (m := CHK_OPENING_RE.match(line)):
            opening = balance = _amount(m.group(7))
            opening_date = _chk_date(m.group(1), m.group(2), m.group(3))
            continuing = False
            continue
        if (m := CHK_CLOSING_RE.match(line)):
            closing = _amount(m.group(4))
            closing_date = _chk_date(m.group(1), m.group(2), m.group(3))
            continuing = False
            continue
        if (m := CHK_TOTAL_RE.match(line)):
            totals = (_amount(m.group(1)), _amount(m.group(2)))
            break  # everything after TOTAL is commentary

        row = CHK_ROW_RE.match(line)
        if row:
            description = row.group(7).strip()
            amount = _amount(row.group(8))
            new_balance = _amount(row.group(9))
            withdrawal, deposit = _direction_from_balance(balance, new_balance, [amount])
            if withdrawal is None and deposit is None:
                raise ValueError(
                    "Citi Plus statement does not reconcile: a row's amount does not "
                    "match its running-balance movement"
                )
            balance = new_balance
            # Debit (balance down) positive = expense; credit negative.
            amount_minor = withdrawal if withdrawal is not None else -deposit
            desc_upper = description.upper()
            transactions.append(ParsedTransaction(
                date=_chk_date(row.group(1), row.group(2), row.group(3)),
                description=description,
                amount_minor=amount_minor,
                is_payment="BILL PAYMENT" in desc_upper,
                card_info="",  # filled once the account number is known
            ))
            continuing = True
            continue

        if any(r.search(line) for r in CHK_FURNITURE_RES) or not line:
            continuing = False
            continue
        if continuing and transactions:
            transactions[-1].description += " " + line

    if not account_number:
        raise ValueError("Citi Plus statement: checking account number not found")
    if opening is None or closing is None or totals is None:
        raise ValueError("Citi Plus statement: opening/closing balance or totals not found")

    debits = sum(t.amount_minor for t in transactions if t.amount_minor > 0)
    credits = -sum(t.amount_minor for t in transactions if t.amount_minor < 0)
    # Opening less the rows against the closing balance is the import path's
    # check (tie.py); the TOTAL line is this statement's own second witness.
    if debits != totals[0] or credits != totals[1]:
        raise ValueError(
            f"Citi Plus statement does not reconcile: {len(transactions)} parsed rows "
            "do not match the statement's totals"
        )

    account_name = f"Citi Checking {account_number}"
    for tx in transactions:
        tx.card_info = account_name

    return ParsedStatement(
        statement_type="bank",
        statement_date=stmt_date,
        accounts=[account_name],
        filename=filename,
        transactions=transactions,
        opening_minor=opening,
        closing_minor=closing,
        opening_date=opening_date,
        closing_date=closing_date,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_citi_pdf(filepath: str) -> ParsedStatement:
    """Auto-detect Citi PDF type and parse."""
    pages = _read_pages(filepath)
    kind = classify_citi_text(pages[0] if pages else "")
    name = Path(filepath).name
    if kind == "credit_card":
        return parse_citi_card_pages(pages, name)
    if kind == "bank":
        return parse_citi_checking_pages(pages, name)
    raise ValueError(f"Not a recognized Citi PDF: {name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py parse_citi_pdf.py <path_to_pdf>")
        sys.exit(1)

    from parse_dbs import print_summary
    stmt = parse_citi_pdf(sys.argv[1])
    print_summary(stmt)
