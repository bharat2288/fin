"""Hire-purchase terms on a loan, and what they make of its statement's figure.

A hire-purchase loan (a car loan) charges flat interest: the amount financed
times the flat rate a year times the years, fixed when the loan is made, added
to the amount financed and repaid in equal monthly instalments. Its statement
prints the total of the remaining instalments, which falls by a whole
instalment each month: principal and interest not yet earned, together. fin's
figure for a loan is principal owed, so that loan_interest.py's interest
between two figures (the instalments naming the loan less the fall in what is
owed) is the interest those instalments carried. Taken as printed, the fall is
the whole instalment, the interest comes out 0 and the balance sheet overstates
the debt by the interest not yet earned.

The terms turn one into the other when the statement is imported (app.py's
_loan_figure): principal owed = the total of the remaining instalments less
the interest not yet earned. A loan without terms imports its figure as
printed.

Rule of 78 (RULE_OF_78), the method hire purchase uses: with N instalments
and flat interest I, instalment k carries I x 2(N + 1 - k) / (N(N + 1)) of
interest, and with r instalments left the interest not yet earned is
I x r(r + 1) / (N(N + 1)), in whole minor units, half up. Two figures one
instalment apart then differ by that instalment's principal, and the interest
worked out between them is that instalment's interest.

How many instalments are left is read from the printed figure itself: the
instalments a total of remaining instalments holds, rounded up, at the terms'
instalment ((amount financed + I) / N). A last instalment the bank makes
smaller to come out even is still one instalment. The schedule's own count on
the statement's day (from the first instalment's date) is shown beside it,
not used: a statement may already have taken off an instalment due later that
month.

The terms are stored in one row per loan (the loan_terms table, history
tracked), set through the account's own route, and read here.
"""

from __future__ import annotations

import sqlite3
from calendar import monthrange
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction

import money

LOAN = "loan"

# (name, description). How the flat interest is spread over the instalments.
RULE_OF_78 = "rule-of-78"
METHODS = (
    (RULE_OF_78, "Rule of 78: each instalment carries interest in proportion to the "
                 "instalments left when it is paid, most in the first"),
)
METHOD_NAMES = tuple(name for name, _ in METHODS)

_COLUMNS = ("financed_minor", "interest_minor", "months", "first_instalment", "method")
# The longest term taken: thirty years of monthly instalments.
MAX_MONTHS = 360


class TermsRefused(ValueError):
    """Terms that cannot be held, in the operator's words. Nothing was written."""


def _half_up(value: Fraction) -> int:
    """A fraction of a minor unit to the nearest whole one, half up."""
    return int((Decimal(value.numerator) / Decimal(value.denominator)).quantize(
        Decimal(1), rounding=ROUND_HALF_UP))


def _amount(value, field: str, currency: str) -> int:
    try:
        minor = money.parse_minor(str(value), currency)
    except (ValueError, money.UnknownCurrency):
        raise TermsRefused(f"{field} is not an amount; nothing was saved") from None
    if minor <= 0:
        raise TermsRefused(f"{field} must be more than nothing; nothing was saved")
    return minor


def checked(data, currency: str) -> dict:
    """Terms as sent (`financed`, `months`, `first_instalment`, `method`, and
    one of `flat_rate`, percent a year, or `interest`, the flat interest over
    the term; amounts as text in whole units of the loan's currency) as they
    are stored. Refused (TermsRefused) when any is missing or out of range."""
    if not isinstance(data, dict):
        raise TermsRefused("terms are the amount financed, the months, the flat rate or the "
                           "interest, and the first instalment's date; nothing was saved")
    financed = _amount(data.get("financed"), "the amount financed", currency)
    months = data.get("months")
    if isinstance(months, str) and months.strip().isdigit():
        months = int(months)
    if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= MAX_MONTHS:
        raise TermsRefused(f"the months must be a whole number from 1 to {MAX_MONTHS}; nothing was saved")
    method = data.get("method", RULE_OF_78)
    if method not in METHOD_NAMES:
        raise TermsRefused(f"unknown method {method!r}; fin knows {', '.join(METHOD_NAMES)}; nothing was saved")
    first = data.get("first_instalment")
    try:
        if not isinstance(first, str) or date.fromisoformat(first).isoformat() != first:
            raise ValueError
    except ValueError:
        raise TermsRefused("the first instalment's date is a day, YYYY-MM-DD; nothing was saved") from None

    has_rate, has_interest = data.get("flat_rate") not in (None, ""), data.get("interest") not in (None, "")
    if has_rate == has_interest:
        raise TermsRefused("give the flat rate a year or the interest over the term, one of them; "
                           "nothing was saved")
    if has_interest:
        interest = _amount(data["interest"], "the interest", currency)
    else:
        try:
            rate = Fraction(Decimal(str(data["flat_rate"]).strip().rstrip("%")))
        except (InvalidOperation, ValueError):
            raise TermsRefused("the flat rate is a percent a year, e.g. 1.68; nothing was saved") from None
        if rate <= 0 or rate >= 100:
            raise TermsRefused("the flat rate is a percent a year above 0 and below 100; nothing was saved")
        interest = _half_up(financed * rate / 100 * months / 12)
    return {"financed_minor": financed, "interest_minor": interest, "months": months,
            "first_instalment": first, "method": method}


def held(conn: sqlite3.Connection, account_id: int) -> dict | None:
    """The terms a loan holds, or None."""
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM loan_terms WHERE account_id = ?", (account_id,)
    ).fetchone()
    return None if row is None else dict(zip(_COLUMNS, tuple(row)))


def all_held(conn: sqlite3.Connection) -> dict[int, dict]:
    """Every loan's terms, by account id."""
    return {r[0]: dict(zip(_COLUMNS, tuple(r)[1:])) for r in conn.execute(
        f"SELECT account_id, {', '.join(_COLUMNS)} FROM loan_terms")}


def statement_figures(conn: sqlite3.Connection, account_id: int) -> int:
    """How many of the loan's figures came from its statements."""
    return conn.execute(
        "SELECT COUNT(*) FROM anchors WHERE account_id = ? AND source = 'statement'", (account_id,)
    ).fetchone()[0]


def write(conn: sqlite3.Connection, account_id: int, terms: dict | None) -> bool:
    """Hold these terms for the loan, or none (None). Returns whether anything
    changed. Refused (TermsRefused) when the loan holds figures from its
    statements and the terms would change: those were worked out under the
    terms it has, and would be left as they were. Does not commit."""
    if held(conn, account_id) == terms:
        return False
    count = statement_figures(conn, account_id)
    if count:
        raise TermsRefused(
            f"this loan holds {count} {'figure' if count == 1 else 'figures'} from its statements, "
            "worked out under the terms it has; undo those imports before changing its terms. "
            "Nothing was saved."
        )
    if terms is None:
        conn.execute("DELETE FROM loan_terms WHERE account_id = ?", (account_id,))
    elif held(conn, account_id) is None:
        conn.execute(
            f"INSERT INTO loan_terms (account_id, {', '.join(_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, *(terms[c] for c in _COLUMNS)),
        )
    else:
        conn.execute(
            f"UPDATE loan_terms SET {', '.join(f'{c} = ?' for c in _COLUMNS)} WHERE account_id = ?",
            (*(terms[c] for c in _COLUMNS), account_id),
        )
    return True


def instalment_minor(terms: dict) -> int:
    """The level instalment the terms give, half up."""
    return _half_up(Fraction(terms["financed_minor"] + terms["interest_minor"], terms["months"]))


def flat_rate(terms: dict) -> str:
    """The flat rate a year the terms come to, in percent, as text: "1.68"."""
    rate = Fraction(terms["interest_minor"] * 1200, terms["financed_minor"] * terms["months"])
    shown = (Decimal(rate.numerator) / Decimal(rate.denominator)).quantize(Decimal("0.0001"),
                                                                            rounding=ROUND_HALF_UP)
    return format(shown.normalize(), "f")


def shown(terms: dict | None) -> dict | None:
    """The terms as the account read gives them: as held, with the instalment
    and the flat rate they come to."""
    if terms is None:
        return None
    return {**terms, "instalment_minor": instalment_minor(terms), "flat_rate": flat_rate(terms)}


def unearned_minor(terms: dict, left: int) -> int:
    """The interest not yet earned with `left` instalments still to pay."""
    n = terms["months"]
    return _half_up(Fraction(terms["interest_minor"] * left * (left + 1), n * (n + 1)))


def instalment_interest_minor(terms: dict, k: int) -> int:
    """The interest instalment k (from 1) carries: the fall in the interest not
    yet earned as it is paid."""
    n = terms["months"]
    return unearned_minor(terms, n + 1 - k) - unearned_minor(terms, n - k)


def left_in(terms: dict, printed_minor: int) -> int:
    """How many instalments a total of remaining instalments holds."""
    total = terms["financed_minor"] + terms["interest_minor"]
    left = -((-printed_minor * terms["months"]) // total)   # rounded up
    return max(0, min(terms["months"], left))


def _add_months(day: date, count: int) -> date:
    y, m = divmod(day.month - 1 + count, 12)
    year, month = day.year + y, m + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def left_by_schedule(terms: dict, on: str) -> int:
    """How many instalments the schedule still has to come after the day `on`."""
    first, day = date.fromisoformat(terms["first_instalment"]), date.fromisoformat(on)
    due = sum(1 for k in range(terms["months"]) if _add_months(first, k) <= day)
    return terms["months"] - due


def principal_owed(terms: dict, printed_minor: int, on: str) -> dict:
    """What a statement's total of remaining instalments, dated `on`, comes to
    under the terms: the instalments it holds, the interest not yet earned,
    and the principal owed. Refused (ValueError) when the printed figure is
    less than the interest it would be less."""
    left = left_in(terms, printed_minor)
    unearned = unearned_minor(terms, left)
    owed = printed_minor - unearned
    if owed < 0:
        raise ValueError(
            "the statement's remaining instalments are less than the interest not yet earned "
            "under the loan's terms; check the terms. Nothing was read from this file."
        )
    return {
        "printed_minor": printed_minor,
        "instalments_left": left,
        "months": terms["months"],
        "left_by_schedule": left_by_schedule(terms, on),
        "unearned_minor": unearned,
        "owed_minor": owed,
        "method": terms["method"],
    }
