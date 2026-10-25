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
_loan_figure; the confirm works it out again under the terms held then, and
refuses when that is not what the preview showed). A loan without terms
imports its figure as printed.

Rule of 78 (RULE_OF_78), the method hire purchase uses: with N instalments
and flat interest I, instalment k carries I x 2(N + 1 - k) / (N(N + 1)) of
interest, and with r instalments left the interest not yet earned is
I x r(r + 1) / (N(N + 1)), in whole minor units, half up. Two figures one
instalment apart then differ by that instalment's principal, and the interest
worked out between them is that instalment's interest.

How many instalments are left is read from the printed figure itself: the
number of level instalments ((amount financed + I) / N) the total of the
remaining instalments comes nearest to. A lender's rounding of the
instalment, a last instalment made to come out even, a small fee or a
benefit move the total by less than half an instalment. The schedule's own
count on the statement's day (from the first instalment's date) is shown
beside it, not used: a statement may already have taken off an instalment
due later that month.

The terms are also the loan's first figure: the amount financed is owed from
their start day (`owed_from`, by default the last day before the first
instalment's month), and loan_interest.py reads it as a figure, so interest is
worked out from the start without a figure typed for it.

What the operator entered is held as entered: the flat rate as typed, or the
interest over the term; the interest is worked out from a rate once, when the
terms are saved. The terms are stored in one row per loan (the loan_terms
table, history tracked, made by a start: history.ADDED_ON_START), set through
the account's own route, and read here. A book not started since the table
came reads as holding no terms.

The lock: while a loan holds figures from its statements, its terms do not
change (they were worked out under them), neither through the account route
nor by undoing the change that set them (history.undo asks refusal_to_undo).
"""

from __future__ import annotations

import json
import sqlite3
from calendar import monthrange
from datetime import date, timedelta
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

_COLUMNS = ("financed_minor", "interest_minor", "flat_rate", "months", "first_instalment",
            "owed_from", "method")
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


def _day(value, what: str) -> str:
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise TermsRefused(f"{what} is a day, YYYY-MM-DD; nothing was saved") from None
    return value


def default_owed_from(first_instalment: str) -> str:
    """The last day before the first instalment's month."""
    return (date.fromisoformat(first_instalment).replace(day=1) - timedelta(days=1)).isoformat()


def checked(data, currency: str) -> dict:
    """Terms as sent (`financed`, `months`, `first_instalment`, `method`, one of
    `flat_rate`, percent a year, or `interest`, the flat interest over the term,
    and optionally `owed_from`; amounts as text in whole units of the loan's
    currency) as they are stored. Refused (TermsRefused) when any is missing or
    out of range."""
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
    first = _day(data.get("first_instalment"), "the first instalment's date")
    owed_from = data.get("owed_from") or default_owed_from(first)
    _day(owed_from, "the day the amount financed is owed from")
    if owed_from >= first:
        raise TermsRefused("the day the amount financed is owed from comes before the first "
                           "instalment; nothing was saved")

    has_rate, has_interest = data.get("flat_rate") not in (None, ""), data.get("interest") not in (None, "")
    if has_rate == has_interest:
        raise TermsRefused("give the flat rate a year or the interest over the term, one of them; "
                           "nothing was saved")
    rate_text = None
    if has_interest:
        interest = _amount(data["interest"], "the interest", currency)
    else:
        rate_text = str(data["flat_rate"]).strip().rstrip("%").strip()
        try:
            rate = Fraction(Decimal(rate_text))
        except (InvalidOperation, ValueError):
            raise TermsRefused("the flat rate is a percent a year, e.g. 1.68; nothing was saved") from None
        if rate <= 0 or rate >= 100:
            raise TermsRefused("the flat rate is a percent a year above 0 and below 100; nothing was saved")
        interest = _half_up(financed * rate / 100 * months / 12)
    return {"financed_minor": financed, "interest_minor": interest, "flat_rate": rate_text,
            "months": months, "first_instalment": first, "owed_from": owed_from, "method": method}


def _has_table(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'loan_terms'"
    ).fetchone() is not None


def held(conn: sqlite3.Connection, account_id: int) -> dict | None:
    """The terms a loan holds, or None."""
    if not _has_table(conn):
        return None
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM loan_terms WHERE account_id = ?", (account_id,)
    ).fetchone()
    return None if row is None else dict(zip(_COLUMNS, tuple(row)))


def all_held(conn: sqlite3.Connection) -> dict[int, dict]:
    """Every loan's terms, by account id."""
    if not _has_table(conn):
        return {}
    return {r[0]: dict(zip(_COLUMNS, tuple(r)[1:])) for r in conn.execute(
        f"SELECT account_id, {', '.join(_COLUMNS)} FROM loan_terms")}


def statement_figures(conn: sqlite3.Connection, account_id: int) -> int:
    """How many of the loan's figures came from its statements."""
    return conn.execute(
        "SELECT COUNT(*) FROM anchors WHERE account_id = ? AND source = 'statement'", (account_id,)
    ).fetchone()[0]


def lock_refusal(conn: sqlite3.Connection, account_id: int) -> str | None:
    """Why the loan's terms may not change now, or None: it holds figures from
    its statements, worked out under the terms it has (or imported as printed,
    without terms)."""
    count = statement_figures(conn, account_id)
    if not count:
        return None
    figures = f"{count} {'figure' if count == 1 else 'figures'}"
    if held(conn, account_id) is not None:
        return (f"this loan holds {figures} from its statements, worked out under the terms it has; "
                "undo those imports before changing its terms. Nothing was saved.")
    return (f"this loan holds {figures} from its statements, imported as printed without terms; "
            "undo those imports before giving it terms. Nothing was saved.")


def write(conn: sqlite3.Connection, account_id: int, terms: dict | None) -> bool:
    """Hold these terms for the loan, or none (None). Returns whether anything
    changed: terms the loan holds already change nothing, locked or not.
    Refused (TermsRefused) under the lock (lock_refusal). Does not commit."""
    if held(conn, account_id) == terms:
        return False
    why = lock_refusal(conn, account_id)
    if why:
        raise TermsRefused(why)
    if terms is None:
        conn.execute("DELETE FROM loan_terms WHERE account_id = ?", (account_id,))
    elif held(conn, account_id) is None:
        conn.execute(
            f"INSERT INTO loan_terms (account_id, {', '.join(_COLUMNS)}) VALUES (?, {', '.join('?' for _ in _COLUMNS)})",
            (account_id, *(terms[c] for c in _COLUMNS)),
        )
    else:
        conn.execute(
            f"UPDATE loan_terms SET {', '.join(f'{c} = ?' for c in _COLUMNS)} WHERE account_id = ?",
            (*(terms[c] for c in _COLUMNS), account_id),
        )
    return True


def loans_in_entry(conn: sqlite3.Connection, entry_id: int) -> set[int]:
    """The loans whose terms a change entry set, changed or took off."""
    loans = set()
    for before, after in conn.execute(
        "SELECT before, after FROM change_rows WHERE entry_id = ? AND tbl = 'loan_terms'", (entry_id,)
    ):
        for side in (before, after):
            if side:
                loans.add(json.loads(side)["account_id"])
    return loans


def refusal_to_undo(conn: sqlite3.Connection, entry_id: int) -> str | None:
    """Why undoing a change entry would change a locked loan's terms, in the
    lock's own words, or None."""
    for loan in sorted(loans_in_entry(conn, entry_id)):
        why = lock_refusal(conn, loan)
        if why:
            return why
    return None


def instalment_minor(terms: dict) -> int:
    """The level instalment the terms give, half up."""
    return _half_up(Fraction(terms["financed_minor"] + terms["interest_minor"], terms["months"]))


def shown(terms: dict | None) -> dict | None:
    """The terms as the account read gives them: as held, with the level
    instalment they come to."""
    if terms is None:
        return None
    return {**terms, "instalment_minor": instalment_minor(terms)}


def _total(terms: dict) -> int:
    return terms["financed_minor"] + terms["interest_minor"]


def _unearned(terms: dict, left: int) -> Fraction:
    n = terms["months"]
    return Fraction(terms["interest_minor"] * left * (left + 1), n * (n + 1))


def unearned_minor(terms: dict, left: int) -> int:
    """The interest not yet earned with `left` instalments still to pay."""
    return _half_up(_unearned(terms, left))


def instalment_interest_minor(terms: dict, k: int) -> int:
    """The interest instalment k (from 1) carries: the fall in the interest not
    yet earned as it is paid."""
    n = terms["months"]
    return unearned_minor(terms, n + 1 - k) - unearned_minor(terms, n - k)


def left_in(terms: dict, printed_minor: int) -> int:
    """How many instalments a total of remaining instalments holds: the
    nearest whole number of level instalments, half up."""
    total, n = _total(terms), terms["months"]
    left = (2 * printed_minor * n + total) // (2 * total)
    return max(0, min(n, left))


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
    below nothing, or less than the interest it would be less."""
    if printed_minor < 0:
        raise ValueError(
            "the statement's total of remaining instalments is below nothing; "
            "nothing was read from this file."
        )
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


# --- does a figure fit the schedule ---------------------------------------------------
#
# The check behind loan_interest.misfit. A figure (principal owed) fits when it
# is within half a level instalment of the principal the schedule leaves owed
# after the instalments naming the loan by its day. Half an instalment because
# an instalment missing or one too many moves principal by a whole
# instalment's principal (over half an instalment for any term fin takes),
# and a total of remaining instalments typed as principal is over by the
# interest not yet earned; while the lender's rounding of the instalment
# (under one unit per instalment), a fee or a benefit on an instalment (paid
# money, not principal: it comes out as interest) stay well inside it.


def tolerance_minor(terms: dict) -> int:
    """Half a level instalment."""
    return _half_up(Fraction(_total(terms), 2 * terms["months"]))


def schedule_owed_minor(terms: dict, left: int) -> int:
    """The principal the schedule leaves owed with `left` level instalments to pay."""
    return _half_up(Fraction(_total(terms) * left, terms["months"]) - _unearned(terms, left))


def fit(terms: dict, owed_minor: int, named: int, on: str) -> dict | None:
    """Whether a figure of principal owed, dated `on`, fits the schedule after
    `named` instalments naming the loan: None when it fits, else what the
    schedule leaves owed then (`expected_minor`) and whether the figure looks
    like a total of remaining instalments rather than principal
    (`looks_printed`)."""
    left = max(0, terms["months"] - named)
    expected = schedule_owed_minor(terms, left)
    tolerance = tolerance_minor(terms)
    if abs(owed_minor - expected) <= tolerance:
        return None
    by_date = left_by_schedule(terms, on)
    near = range(max(0, min(left, by_date) - 1), min(terms["months"], max(left, by_date) + 1) + 1)
    looks_printed = any(
        abs(owed_minor - _half_up(Fraction(_total(terms) * r, terms["months"]))) <= tolerance
        and abs(owed_minor - schedule_owed_minor(terms, r)) > tolerance
        for r in near
    )
    return {"expected_minor": expected, "looks_printed": looks_printed}
