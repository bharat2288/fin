"""Read the DBS car-loan summary: what is owed on the car loan on a day.

DBS sends the car loan as a "Consolidated Statement" whose Account Summary
lists only Loans:
  - "as at DD Mon YYYY", the day the summary is for;
  - "Auto Loan Total Outstanding: SGD 52,340.00";
  - under "Vehicle No. Account No. Outstanding (SGD)", one line per car loan:
    the vehicle, the loan number and what is outstanding on it.

It prints no rows, no instalment, no instalments left and no split of an
instalment into principal and interest: the one fact is what is outstanding.
That is the loan's figure (a statement's figure, dated the summary's day), and
loan_interest.py works the interest out between two figures from the
instalments the bank rows paid. What is outstanding is the total of the
remaining instalments: it falls by a whole instalment each month, principal
and interest not yet earned together. A loan holding its hire-purchase terms
(loan_terms.py) has it turned into principal owed when it is imported; a loan
without them takes it as printed.

A summary is read only when it lists no account with rows: a consolidated
statement that also carries a deposit account is the DBS bank reader's, as
before. A summary whose total is not its one loan line is refused, and so is
one listing more than one car loan: fin keeps one car loan account.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import pdfplumber

import money
from parse_dbs import _parse_statement_date

# The loan account fin keeps for the car loan: the one the car-loan
# instalments name (db.LOAN_MERCHANTS).
ACCOUNT = "DBS auto loan"
CURRENCY = "SGD"

_TOTAL = re.compile(r"Auto Loan Total Outstanding:\s*SGD\s*([\d,]+\.\d{2})")
_TABLE_HEADER = "Vehicle No. Account No. Outstanding (SGD)"
_LOAN_LINE = re.compile(r"^(\S+)\s+(\S+)\s+([\d,]+\.\d{2})$")
# What a statement with rows prints: the DBS bank reader's and card reader's.
_ROWS_MARKERS = ("Balance Brought Forward", "Balance Carried Forward", "Transaction Details", "CARD NO")


@dataclass
class LoanStatement:
    """What a loan statement states: what is owed on one day. It has no rows,
    and its figure is what is owed, positive, in whole minor units."""
    account: str
    statement_date: str    # YYYY-MM-DD
    owed_minor: int
    currency: str = CURRENCY
    filename: str = ""


def looks_like_auto_loan(text: str) -> bool:
    """Whether the text is a DBS car-loan summary and nothing else."""
    return bool(_TOTAL.search(text)) and not any(marker in text for marker in _ROWS_MARKERS)


def _pages(filepath: str) -> list[str]:
    with pdfplumber.open(filepath) as pdf:
        return [(page.extract_text() or "") for page in pdf.pages]


def detect_dbs_auto_loan_pdf(filepath: str) -> bool:
    try:
        return looks_like_auto_loan("\n".join(_pages(filepath)))
    except Exception:
        return False


def parse_auto_loan_text(text: str, filename: str = "") -> LoanStatement:
    """Read one car-loan summary's text."""
    on = _parse_statement_date(text)
    if on == "unknown":
        raise ValueError("DBS car loan: the summary's date was not found; nothing was read.")
    total = money.parse_minor(_TOTAL.search(text).group(1), CURRENCY)

    lines = [line.strip() for line in text.splitlines()]
    loans = []
    if _TABLE_HEADER in lines:
        for line in lines[lines.index(_TABLE_HEADER) + 1:]:
            match = _LOAN_LINE.match(line)
            if not match:
                break
            loans.append(money.parse_minor(match.group(3), CURRENCY))
    if len(loans) > 1:
        raise ValueError(
            "DBS car loan: the summary lists more than one car loan, and fin keeps one; "
            "nothing was read."
        )
    if loans != [total]:
        raise ValueError(
            "DBS car loan: the total outstanding does not add up to the loan's own line; "
            "nothing was read."
        )
    return LoanStatement(account=ACCOUNT, statement_date=on, owed_minor=total, filename=filename)


def parse_dbs_auto_loan_pdf(filepath: str) -> LoanStatement:
    """Read a DBS car-loan summary PDF."""
    return parse_auto_loan_text("\n".join(_pages(filepath)), os.path.basename(filepath))
