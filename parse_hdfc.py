"""Parse HDFC Bank savings statements (PDF, rupees).

The statement:
  - a header line "Date Narration Chq./Ref.No. ValueDt WithdrawalAmt.
    DepositAmt. ClosingBalance", on the first page (some files repeat it on
    each page); a later page without it carries the rows on from its top;
  - rows dated dd/mm/yy: "DATE NARRATION REF VALUEDATE AMOUNT CLOSINGBALANCE".
    The text carries one amount per row and not which column it stood in, so
    the direction comes from the closing balance the row itself states;
  - a narration too long for its column runs on to the following lines;
  - a closing "STATEMENT SUMMARY" whose figures line reads: opening balance,
    debit count, credit count, debits, credits, closing balance.

Amounts are whole paise; a row's amount is positive for money out. The parser
hands over the opening and closing balance the summary states. Whether the
rows carry the one to the other is checked by the import path (tie.py). The
parser itself refuses a file where a row disagrees with its own closing
balance, or where the rows disagree with the summary's counts and totals.

The password. Some of these files are encrypted and some are not, and a file
that needs no password fails to open when it is given one. So the file is
opened with no password first, and only when that fails for want of one is it
opened with the password in the environment variable FIN_HDFC_PDF_PASSWORD.
The password is read from nowhere else, is never written or logged, and no
decrypted copy of the file is made: the pages are read in memory. A file that
needs the password when the variable is not set is refused, naming the
variable.
"""

from __future__ import annotations

import os
import re

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

import money
from parse_dbs import ParsedStatement, ParsedTransaction

CURRENCY = "INR"
PASSWORD_VARIABLE = "FIN_HDFC_PDF_PASSWORD"
ACCOUNT_LABEL = "HDFC Bank Savings"

_AMOUNT = r"-?[\d,]+\.\d{2}"
_DAY = r"\d{2}/\d{2}/\d{2}"
_ROW = re.compile(rf"^({_DAY})\s+(.+?)\s+({_DAY})\s+({_AMOUNT})\s+({_AMOUNT})$")
_SUMMARY = re.compile(rf"^({_AMOUNT})\s+(\d+)\s+(\d+)\s+({_AMOUNT})\s+({_AMOUNT})\s+({_AMOUNT})$")
_PERIOD = re.compile(r"From\s*:\s*(\d{2})/(\d{2})/(\d{4})\s*To\s*:\s*(\d{2})/(\d{2})/(\d{4})", re.I)
_ACCOUNT_NUMBER = re.compile(r"Account\s*No\.?\s*:?\s*(\d{6,})", re.I)
# The reference a row ends its narration with: letters and digits, with a digit.
_REFERENCE = re.compile(r"(?=\w*\d)\w{6,}")

# Compared with spaces removed and in upper case: the spacing of a heading
# varies with how the page's text is read.
_HEADER = "DATENARRATIONCHQ./REF.NO.VALUEDT"
_SUMMARY_HEADING = "STATEMENTSUMMARY"
# Lines that end the rows of a page: what follows is not a narration.
_END_OF_ROWS = ("PAGENO", "HDFCBANKLIMITED", "*CLOSINGBALANCE", "CONTENTSOFTHISSTATEMENT", "GENERATEDON")
# The labels of the page-top block (branch, address, the holder's details)
# each page opens with, matched on the compacted line with the colon that
# follows them: a line starting with one is header text, never a narration
# running on ("STATE BANK ..." is not "State :").
_PAGE_TOP_LABEL = re.compile(
    r"^(ACCOUNTBRANCH|ADDRESS|CITY|STATE|PHONENO|EMAIL|CUSTID|ACCOUNTNO|A/COPENDATE|ACCOUNTSTATUS"
    r"|RTGS/NEFTIFSC|MICR|BRANCHCODE|PRODUCTCODE|JOINTHOLDERS|NOMINATION|ODLIMIT|CURRENCY|FROM)\.?:"
    r"|^STATEMENTOFACCOUNT"
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).upper()


# --- opening the file -----------------------------------------------------------

def _needs_password(error: BaseException) -> bool:
    """Whether opening failed because the file wants a password. pdfplumber
    wraps the reader's own exception, so the wrapped one is looked at too."""
    seen = []
    pending = [error]
    while pending:
        exc = pending.pop()
        if exc is None or any(exc is other for other in seen):
            continue
        seen.append(exc)
        if isinstance(exc, PDFPasswordIncorrect):
            return True
        pending += [a for a in exc.args if isinstance(a, BaseException)]
        pending += [exc.__cause__, exc.__context__]
    return False


def _open(filepath: str):
    """The opened PDF: with no password, and only if that fails for want of
    one, with the password from the environment.

    Every refusal is raised outside the `except` block that saw the failure,
    with a fixed message: the upload route echoes the message, and neither it
    nor the exceptions chained to it may carry the password or a token of the
    file.
    """
    locked = False
    try:
        return pdfplumber.open(filepath)
    except Exception as error:
        locked = _needs_password(error)
    if not locked:
        raise ValueError("HDFC statement: the file could not be opened")

    password = os.environ.get(PASSWORD_VARIABLE)
    if not password:
        raise ValueError(
            "HDFC statement: this file is password-protected. Set the environment "
            f"variable {PASSWORD_VARIABLE} to its password and start fin again; "
            "nothing was read."
        )
    try:
        return pdfplumber.open(filepath, password=password)
    except Exception:
        pass
    raise ValueError(
        "HDFC statement: the file did not open with the password in the environment "
        f"variable {PASSWORD_VARIABLE}; nothing was read."
    )


def _read_pages(filepath: str) -> list[str]:
    """Every page's text, read in memory."""
    pdf = _open(filepath)
    unreadable = False
    try:
        pages = [(page.extract_text() or "") for page in pdf.pages]
    except Exception:
        unreadable = True
    finally:
        pdf.close()
    if unreadable:
        raise ValueError("HDFC statement: could not read page text")
    return pages


# --- detection ------------------------------------------------------------------

def looks_like_hdfc(page_text: str) -> bool:
    compact = _compact(page_text)
    return "HDFCBANK" in compact and _HEADER in compact


def detect_hdfc_pdf(filepath: str) -> bool:
    """Whether the file is an HDFC savings statement. A file that will not
    open without a password is claimed too: no other statement fin reads is
    encrypted, and the refusal that names the variable is this parser's."""
    try:
        pdf = pdfplumber.open(filepath)
    except Exception as error:
        return _needs_password(error)
    try:
        return looks_like_hdfc(pdf.pages[0].extract_text() or "")
    except Exception:
        return False
    finally:
        pdf.close()


# --- parsing --------------------------------------------------------------------

def _iso(day: str) -> str:
    """dd/mm/yy as YYYY-MM-DD."""
    dd, mm, yy = day.split("/")
    return f"20{yy}-{mm}-{dd}"


def _description(narration: str) -> str:
    """A row's narration without the reference number it ends with."""
    words = narration.split()
    if len(words) > 1 and _REFERENCE.fullmatch(words[-1]):
        words = words[:-1]
    return " ".join(words)


def _table_lines(pages: list[str]):
    """The table's lines, up to the summary: (kind, value) where kind is
    'row' (a match) or 'more' (a narration running on).

    The table starts under the column header. A page that prints the header
    is read from under it; a later page that does not (HDFC prints it on the
    first page only) carries the table on from its top, so a narration cut
    by the page break runs on there. On such a page a footer line seen
    before its first row (a page number at the top) is passed over; after
    it, one ends the page's rows.

    Such a page opens with the page-top block (branch, address, the holder's
    name) before its first row. A line there is read as a narration running
    on only when it cannot be header text: it starts with none of the block's
    labels and is none of the lines above the column header on the first
    page (the block's unlabelled lines, the address and the holder's name,
    print there too)."""
    page_top = _page_top_lines(pages)
    started = False
    for text in pages:
        lines = [raw.strip() for raw in text.splitlines()]
        headed = any(_HEADER in _compact(line) for line in lines)
        in_rows = started and not headed
        rows_on_page = 0
        for line in lines:
            compact = _compact(line)
            if not compact:
                continue
            if _SUMMARY_HEADING in compact:
                return
            if _HEADER in compact:
                in_rows = started = True
                continue
            if not in_rows:
                continue
            if compact.startswith(_END_OF_ROWS):
                if rows_on_page or headed:
                    in_rows = False
                continue
            if not headed and not rows_on_page and (compact in page_top or _PAGE_TOP_LABEL.match(compact)):
                continue
            match = _ROW.match(line)
            if match:
                rows_on_page += 1
            yield ("row", match) if match else ("more", line)


def _page_top_lines(pages: list[str]) -> frozenset[str]:
    """The first headed page's lines above its column header, compacted: the
    page-top block as the statement prints it."""
    for text in pages:
        above = []
        for line in text.splitlines():
            compact = _compact(line)
            if _HEADER in compact:
                return frozenset(c for c in above if c)
            above.append(compact)
    return frozenset()


def _summary(pages: list[str]) -> tuple[int, int, int, int, int, int]:
    """Opening balance, debit count, credit count, debits, credits and closing
    balance, from the statement summary."""
    after_heading = False
    for text in pages:
        for line in (raw.strip() for raw in text.splitlines()):
            if _SUMMARY_HEADING in _compact(line):
                after_heading = True
                continue
            match = _SUMMARY.match(line) if after_heading else None
            if match:
                opening, debit_count, credit_count, debits, credits, closing = match.groups()
                return (
                    money.parse_minor(opening, CURRENCY), int(debit_count), int(credit_count),
                    money.parse_minor(debits, CURRENCY), money.parse_minor(credits, CURRENCY),
                    money.parse_minor(closing, CURRENCY),
                )
    raise ValueError("HDFC statement: the statement summary was not found")


def parse_hdfc_text(pages: list[str], filename: str = "") -> ParsedStatement:
    """Parse the page texts of one HDFC savings statement."""
    everything = "\n".join(pages)
    opening, debit_count, credit_count, debits, credits, closing = _summary(pages)

    number = _ACCOUNT_NUMBER.search(everything)
    account = f"{ACCOUNT_LABEL} {number.group(1)[-4:]}" if number else ACCOUNT_LABEL

    read = []  # [date, narration, amount as printed, closing balance]
    for kind, value in _table_lines(pages):
        if kind == "row":
            day, narration, _value_day, amount, balance = value.groups()
            read.append([
                _iso(day), _description(narration),
                money.parse_minor(amount, CURRENCY), money.parse_minor(balance, CURRENCY),
            ])
        elif read:
            read[-1][1] = f"{read[-1][1]} {value}"

    transactions = []
    balance = opening
    for day, description, printed, closing_balance in read:
        # Money out lowers the balance: the change is the row's signed amount.
        amount = balance - closing_balance
        if abs(amount) != abs(printed):
            raise ValueError("HDFC statement: a row disagrees with its own closing balance")
        transactions.append(ParsedTransaction(
            date=day, description=description, amount_minor=amount, card_info=account,
        ))
        balance = closing_balance

    out = [tx.amount_minor for tx in transactions if tx.amount_minor > 0]
    back = [-tx.amount_minor for tx in transactions if tx.amount_minor < 0]
    if (len(out), sum(out), len(back), sum(back)) != (debit_count, debits, credit_count, credits):
        raise ValueError("HDFC statement: the rows disagree with the statement summary")

    period = _PERIOD.search(everything)
    if period:
        d1, m1, y1, d2, m2, y2 = period.groups()
        opening_date, closing_date = f"{y1}-{m1}-{d1}", f"{y2}-{m2}-{d2}"
    elif transactions:
        opening_date, closing_date = None, max(tx.date for tx in transactions)
    else:
        raise ValueError("HDFC statement: the statement period was not found")

    return ParsedStatement(
        statement_type="bank",
        statement_date=closing_date,
        accounts=[account],
        transactions=transactions,
        filename=filename,
        opening_minor=opening,
        closing_minor=closing,
        opening_date=opening_date,
        closing_date=closing_date,
        currency=CURRENCY,
    )


def parse_hdfc_pdf(filepath: str) -> ParsedStatement:
    """Parse an HDFC savings statement PDF."""
    pages = _read_pages(filepath)
    if not any(looks_like_hdfc(text) for text in pages):
        raise ValueError("HDFC statement: the file is not an HDFC savings statement")
    return parse_hdfc_text(pages, os.path.basename(filepath))
