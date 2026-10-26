"""Loan interest, worked out from the figures the operator supplies.

When a figure for a loan follows an earlier one, the interest for the period
between them is the instalments paid in it less the fall in what is owed. It
is recorded as household spending, spread evenly across the calendar months
of the period, in whole minor units whose parts add up exactly to the whole.

Where the derived rows live: on the loan account itself, under a statement
record named DERIVED_STATEMENT, dated the period's closing figure and starting
the day after the figure before (its period_start). They are
on no bank account or card, so they are in no statement's tie and no bank
balance. They are not rows of the loan's own statement either: every row under
a DERIVED_STATEMENT record has its book and its type worked out
(label_source.DERIVED), and nothing adds them up as the loan's rows. A loan's balance is its figure and the instalments naming
it since, less the interest dated since (`interest_between`): between two
figures what is owed falls by the principal repaid, as the principal card says.

Instalments are movement rows on the household's bank accounts and cards that
name the loan as their other side; positive is money out.

A loan's figures are its anchors and, when it holds hire-purchase terms
(loan_terms.py), the terms' own first figure: the amount financed, owed from
their start day, unless a figure is held for that very day. So a hire-purchase
loan has interest worked out from its start without a figure typed for it.
With terms, every figure must also fit their schedule (`misfit`), checked by
an import before it writes.

`derive` is safe to run again: rows that already say what the figures give are
left as they are, and rows that do not are replaced. It does not commit.
"""

from __future__ import annotations

import sqlite3
from calendar import monthrange
from datetime import date, timedelta

import account_kind
import anchors
import book_type
import flow
import label_source
import loan_terms

LOAN = "loan"
# The name of the statement record derived rows sit under.
DERIVED_STATEMENT = "derived: loan interest"
BOOK = book_type.DEFAULT_BOOK
FLOW = "expense"

_INSTALMENTS_FROM = (
    "FROM transactions t JOIN statements s ON s.id = t.statement_id "
    "JOIN accounts a ON a.id = s.account_id "
    "JOIN accounts l ON l.id = t.other_side_id "
    "WHERE t.flow_type = ? AND a.owner = ? AND a.type IN (?, ?) "
    "AND l.type = ? AND l.owner = ? "
)
_INSTALMENT_PARAMS = (
    flow.MOVEMENT, account_kind.HOUSEHOLD, *account_kind.STATEMENT_KINDS,
    LOAN, account_kind.HOUSEHOLD,
)

_DERIVED_FROM = (
    "FROM transactions t JOIN statements s ON s.id = t.statement_id "
    "JOIN accounts l ON l.id = s.account_id "
    "WHERE s.filename = ? AND l.type = ? "
)
_DERIVED_PARAMS = (DERIVED_STATEMENT, LOAN)


def spread(total: int, parts: int) -> list[int]:
    """`total` in `parts` whole amounts as even as whole numbers allow; the
    odd units go to the first parts, and the parts add up to `total` exactly."""
    base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def _months(after: str, to: str) -> list[tuple[str, str]]:
    """The calendar months holding the days after `after` up to `to`, each as
    (YYYY-MM, the day its share is dated: the month's last day in the period)."""
    day = date.fromisoformat(after) + timedelta(days=1)
    last = date.fromisoformat(to)
    months = []
    y, m = day.year, day.month
    while (y, m) <= (last.year, last.month):
        month_end = date(y, m, monthrange(y, m)[1])
        months.append((f"{y:04d}-{m:02d}", min(month_end, last).isoformat()))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


TERMS_SOURCE = "terms"


def figures(conn: sqlite3.Connection, loan_id: int) -> list[tuple[str, int, str]]:
    """The loan's figures, oldest first: (day, amount as stored, owed so
    negative; source). Its anchors, and its terms' first figure (source
    TERMS_SOURCE) on a day no anchor holds."""
    held = [tuple(r) for r in conn.execute(
        "SELECT date, amount, source FROM anchors WHERE account_id = ? ORDER BY date", (loan_id,)
    )]
    terms = loan_terms.held(conn, loan_id)
    if terms is not None and terms["owed_from"] not in {day for day, _, _ in held}:
        held.append((terms["owed_from"], -terms["financed_minor"], TERMS_SOURCE))
    return sorted(held)


def periods(conn: sqlite3.Connection, loan_id: int) -> list[dict]:
    """What each figure after the loan's first works out to: one period per
    figure that follows an earlier one, oldest first."""
    held = figures(conn, loan_id)
    worked = []
    for (start, owed_before, source), (end, owed_after, _) in zip(held, held[1:]):
        count, paid = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(t.amount_minor), 0) " + _INSTALMENTS_FROM
            + "AND t.other_side_id = ? AND t.date > ? AND t.date <= ?",
            (*_INSTALMENT_PARAMS, loan_id, start, end),
        ).fetchone()
        # Figures are stored negative: what is owed. The fall in what is owed
        # is how far the figure rose toward zero.
        fell = owed_after - owed_before
        interest = paid - fell
        months = _months(start, end)
        worked.append({
            "from": start,
            "from_terms": source == TERMS_SOURCE,
            "to": end,
            "instalments": count,
            "paid_minor": paid,
            "fell_minor": fell,
            "interest_minor": interest,
            "negative": interest < 0,
            "months": [
                {"month": month, "date": day, "interest_minor": share}
                for (month, day), share in zip(months, spread(interest, len(months)))
            ],
        })
    return worked


def _wanted(conn: sqlite3.Connection, loan_id: int) -> list[tuple]:
    """The derived rows the loan's figures give: (period end, date, amount,
    description). A month whose share is nothing gets no row."""
    rows = []
    for period in periods(conn, loan_id):
        description = (
            f"Loan interest, worked out from your figures of "
            f"{day_shown(period['from'])} and {day_shown(period['to'])}"
        )
        for part in period["months"]:
            if part["interest_minor"]:
                rows.append((period["to"], part["date"], part["interest_minor"], description))
    return sorted(rows)


def derive(conn: sqlite3.Connection, loan_id: int | None = None) -> int:
    """Bring the derived interest rows in line with the figures and the
    instalments, for one loan or for every loan the household owes. Returns
    how many loans had their rows replaced. Does not commit."""
    if loan_id is None:
        loans = [r[0] for r in conn.execute(
            "SELECT id FROM accounts WHERE type = ? AND owner = ? ORDER BY id",
            (LOAN, account_kind.HOUSEHOLD),
        )]
    else:
        loans = [r[0] for r in conn.execute(
            "SELECT id FROM accounts WHERE id = ? AND type = ? AND owner = ?",
            (loan_id, LOAN, account_kind.HOUSEHOLD),
        )]
    # The type the slot files loan interest under now (type-relationships 03).
    type_id = book_type.slot_type(conn, book_type.LOAN_INTEREST_SLOT)

    changed = 0
    for loan in loans:
        wanted = _wanted(conn, loan)
        held = sorted(
            tuple(r) for r in conn.execute(
                "SELECT s.statement_date, t.date, t.amount_minor, t.description "
                + _DERIVED_FROM
                + "AND l.id = ? AND t.book_source = ? AND t.type_source = ? AND t.flow_type = ? "
                "AND t.book = ? AND t.type_id IS NOT NULL",
                (*_DERIVED_PARAMS, loan, label_source.DERIVED, label_source.DERIVED, FLOW, BOOK),
            )
        )
        total_held = conn.execute(
            "SELECT COUNT(*) " + _DERIVED_FROM + "AND l.id = ?", (*_DERIVED_PARAMS, loan)
        ).fetchone()[0]
        if held == wanted and total_held == len(wanted):
            continue

        # A month already filed keeps the type it was filed under: the slot
        # moved for new rows only leaves it (type-relationships 03).
        filed = {
            (period_end, day): filed_type
            for period_end, day, filed_type in conn.execute(
                "SELECT s.statement_date, t.date, t.type_id " + _DERIVED_FROM
                + "AND l.id = ? AND t.type_source = ? AND t.type_id IS NOT NULL",
                (*_DERIVED_PARAMS, loan, label_source.DERIVED),
            )
        }
        # Replace. Only rows under a derived statement record of this loan are
        # removed: nothing a statement or the operator put there.
        conn.execute(
            "DELETE FROM transactions WHERE statement_id IN ("
            "SELECT s.id FROM statements s JOIN accounts l ON l.id = s.account_id "
            "WHERE s.filename = ? AND l.type = ? AND l.id = ?)",
            (*_DERIVED_PARAMS, loan),
        )
        conn.execute(
            "DELETE FROM statements WHERE filename = ? AND account_id = ? "
            "AND account_id IN (SELECT id FROM accounts WHERE type = ?)",
            (DERIVED_STATEMENT, loan, LOAN),
        )
        # Each record covers its period: from the day after the figure before.
        starts = {p["to"]: (date.fromisoformat(p["from"]) + timedelta(days=1)).isoformat()
                  for p in periods(conn, loan)}
        statement_ids: dict[str, int] = {}
        for period_end, day, amount, description in wanted:
            if period_end not in statement_ids:
                statement_ids[period_end] = conn.execute(
                    "INSERT INTO statements (account_id, statement_date, filename, period_start)"
                    " VALUES (?, ?, ?, ?)",
                    (loan, period_end, DERIVED_STATEMENT, starts[period_end]),
                ).lastrowid
            # Worked out, in both fields (label_source): nothing later reaches it.
            conn.execute(
                "INSERT INTO transactions (statement_id, date, description, amount_minor, "
                "flow_type, book, type_id, book_source, type_source)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (statement_ids[period_end], day, description, amount, FLOW, BOOK,
                 filed.get((period_end, day), type_id), label_source.DERIVED, label_source.DERIVED),
            )
        changed += 1
    return changed


def filed_ids(conn: sqlite3.Connection) -> list[int]:
    """The rows fin filed as loan interest: every worked-out row on a loan,
    oldest first (type-relationships 03, moving the slot)."""
    return [r[0] for r in conn.execute(
        "SELECT t.id " + _DERIVED_FROM + "AND t.type_source = ? ORDER BY t.date, t.id",
        (*_DERIVED_PARAMS, label_source.DERIVED),
    )]


def interest_between(conn: sqlite3.Connection, loan_id: int, after: str, upto: str) -> int:
    """The interest worked out for one loan dated after one day up to
    another, in whole minor units. Between two figures it is what has been
    added to what is owed since the earlier one; after the latest figure
    there is none yet."""
    return conn.execute(
        "SELECT COALESCE(SUM(t.amount_minor), 0) " + _DERIVED_FROM
        + "AND l.id = ? AND t.date > ? AND t.date <= ?",
        (*_DERIVED_PARAMS, loan_id, after, upto),
    ).fetchone()[0]


def month_figures(conn: sqlite3.Connection, month: str) -> dict:
    """A calendar month (YYYY-MM) across the household's loans, in whole minor
    units: the instalments paid, the derived interest dated in it, and the
    principal repaid, which is the instalments less the interest. With no
    derived interest the instalments are repayment in full."""
    instalments = conn.execute(
        "SELECT COALESCE(SUM(t.amount_minor), 0) " + _INSTALMENTS_FROM
        + "AND strftime('%Y-%m', t.date) = ?",
        (*_INSTALMENT_PARAMS, month),
    ).fetchone()[0]
    interest = conn.execute(
        "SELECT COALESCE(SUM(t.amount_minor), 0) " + _DERIVED_FROM
        + "AND l.owner = ? AND strftime('%Y-%m', t.date) = ?",
        (*_DERIVED_PARAMS, account_kind.HOUSEHOLD, month),
    ).fetchone()[0]
    return {
        "instalments_minor": instalments,
        "interest_minor": interest,
        "principal_minor": instalments - interest,
    }


def named_by(conn: sqlite3.Connection, loan_id: int, on: str) -> int:
    """How many instalments name the loan, dated up to a day."""
    return conn.execute(
        "SELECT COUNT(*) " + _INSTALMENTS_FROM + "AND t.other_side_id = ? AND t.date <= ?",
        (*_INSTALMENT_PARAMS, loan_id, on),
    ).fetchone()[0]


def misfit(conn: sqlite3.Connection, loan_id: int, name: str, currency: str | None) -> str | None:
    """With hire-purchase terms, the first of the loan's figures that does not
    fit their schedule (loan_terms.fit), named in plain words, or None. A loan
    without terms has no schedule: None."""
    terms = loan_terms.held(conn, loan_id)
    if terms is None:
        return None
    for day, amount, _source in figures(conn, loan_id):
        named = named_by(conn, loan_id, day)
        out = loan_terms.fit(terms, -amount, named, day)
        if out is None:
            continue
        text = (
            f"{name}'s figure of {anchors.format_amount(-amount, currency)} for {day_shown(day)} does not "
            f"fit its hire-purchase terms: with {named} {'instalment' if named == 1 else 'instalments'} "
            f"naming it by then, they leave about {anchors.format_amount(out['expected_minor'], currency)} "
            "of principal owed."
        )
        if out["looks_printed"]:
            text += (" It looks like the total of the remaining instalments, not principal owed: "
                     "enter the principal owed instead.")
        return text + (" Check the figure, and label the instalments as repayments of the loan where "
                       "they are not yet.")
    return None


def day_shown(iso: str) -> str:
    """A day as the operator reads it: "30 Jun 2026"."""
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b} {d.year}"


def _month_names(period: dict) -> str:
    names = [f"{date.fromisoformat(m['date']):%B}" for m in period["months"]]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def sentence(period: dict, currency: str | None) -> str:
    """What was worked out for one period, in the words the dialog shows."""
    def shown(minor: int) -> str:
        return anchors.format_amount(minor, currency)

    count = period["instalments"]
    paid = f"{count} {'instalment' if count == 1 else 'instalments'}, {shown(period['paid_minor'])} paid."
    fell = period["fell_minor"]
    moved = (
        f"The loan fell by {shown(fell)}." if fell >= 0
        else f"The loan rose by {shown(-fell)}."
    )
    months = period["months"]
    spending = (
        f"Interest {shown(period['interest_minor'])} is counted as spending "
        f"{'in' if len(months) == 1 else 'across'} {_month_names(period)}."
    )
    since = (f"Since the loan began ({day_shown(period['from'])}, the amount financed under its terms)"
             if period.get("from_terms") else f"Since your last figure ({day_shown(period['from'])})")
    text = f"{since}: {paid} {moved} {spending}"
    if period["negative"]:
        text += (
            " The loan fell by more than was paid, so the interest comes out negative;"
            " it is recorded as it comes out. Check the figures, and the review list"
            " for an instalment not yet labelled."
        )
    return text


def worked_out(conn: sqlite3.Connection, loan_id: int, on: str, currency: str | None) -> tuple:
    """What entering the figure dated `on` worked out: (the period that ends
    at the figure, or None when it is the loan's first; the message)."""
    if not conn.execute(
        "SELECT 1 FROM accounts WHERE id = ? AND type = ? AND owner = ?",
        (loan_id, LOAN, account_kind.HOUSEHOLD),
    ).fetchone():
        return None, None
    worked = periods(conn, loan_id)
    ending = next((p for p in worked if p["to"] == on), None)
    following = next((p for p in worked if p["from"] == on), None)
    parts = []
    if ending is not None:
        parts.append(sentence(ending, currency))
    if following is not None:
        parts.append(
            f"The period from this figure to your next ({day_shown(following['to'])}) was worked "
            f"out again: interest {anchors.format_amount(following['interest_minor'], currency)}."
        )
    return ending, (" ".join(parts) or None)
