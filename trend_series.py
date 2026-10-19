"""S1, the net series (fin-surfaces 25; spec 15, Implementation Decisions S1).

What a subject's spending came to, per month, per book, per stack key and per
currency: {spent, back, net} in whole minor units. Net is the signed sum of
the month's expense and refund rows (money out positive, a refund negative);
spent is its positive part and back its negative part negated. A refund
counts in the month it arrives.

The subject is a merchant, a type, or every type of one book. Household
counts the accounts the household owns (ruling 6); a company book counts
every account. Merchants hidden from expense views count: hiding changes what
a list shows, never a figure. Rows keep their account's currency, never the
printed foreign amount. Books are never added together: each book is its own
branch of the answer.

One grouped query whatever the span. Built on scope.py, never app.py.

Expect back (personal-ledger 23, ticket 29): a row counts its own part (its
amount less what is expected back on it or placed from it,
expect_back.own_sql) and each let-go counts as a line of its own on the day it
counts, in its own book and type (expect_back.let_go_sql). counted_lines() is
that source; the dashboard's stat-cards, monthly and types read it too, so
every spending figure sums the same lines. While expect_back's gate is off a
line is a row at its bare amount and there are no let-go lines, so every
figure is what it was.
"""

from __future__ import annotations

import sqlite3

import account_kind
import book_type
import expect_back
import trend_span
from scope import _TYPE_JOINS, book_expr

NO_TYPE = "No type"          # as app.NO_TYPE_LABEL
NO_MERCHANT = "No merchant"

# What a bar segment stands for -> the SQL naming it. A frozen allowlist:
# stack_by never reaches the query as text of its own.
_STACK = {
    "book": book_expr("t"),
    "type": ("COALESCE(CASE WHEN tp.name IS NOT NULL THEN tp.name || ' > ' || ty.name"
             " ELSE ty.name END, :no_type)"),
    "merchant": "COALESCE(svc.name, :no_merchant)",
}

# Subject kind -> (the filter it puts on a row, how its name is found).
_SUBJECT = {
    "merchant": ("t.service_id = :subject", "SELECT name FROM services WHERE id = ?"),
    "type": ("t.type_id = :subject",
             "SELECT CASE WHEN p.name IS NOT NULL THEN p.name || ' > ' || ty.name"
             " ELSE ty.name END FROM types ty LEFT JOIN types p ON p.id = ty.parent_id"
             " WHERE ty.id = ?"),
    "book": (f"{book_expr('t')} = :subject", None),
}

# Which accounts a book counts: Household only the household's own; a
# company book every account.
_ACCOUNTS_COUNTED = (
    f"(({book_expr('t')} = :household AND a.owner = :household_owner)"
    f" OR {book_expr('t')} <> :household)"
)


def counted_lines() -> str:
    """The lines a spending or income figure sums, as a parenthesised SELECT a
    reader puts in its FROM in place of `transactions`. Built at each call, so
    it reads the gate as it is now.

    One line per row, at its own part, and one per let-go on the day it counts
    (a back let-go as spending: mine in the row's book, type and merchant, a
    gift with no merchant; a pay let-go as income). A row whose own part is
    nothing though its amount is not (wholly expected back, or wholly placed)
    is accounted for: it is no line, so it adds no zero group and no row to a
    count. Columns: id (the row's; NULL for a let-go), date, book, type_id,
    service_id, is_one_off, flow_type, account_id (the row's account; a
    let-go's is its expectation's) and amount_minor (what the line counts,
    signed as a row).
    """
    own = expect_back.own_sql("tx")
    return f"""(
        SELECT tx.id AS id, tx.date AS date, tx.book AS book, tx.type_id AS type_id,
               tx.service_id AS service_id, tx.is_one_off AS is_one_off, tx.flow_type AS flow_type,
               st.account_id AS account_id, {own} AS amount_minor
        FROM transactions tx JOIN statements st ON st.id = tx.statement_id
        WHERE NOT (COALESCE({own}, 0) = 0 AND COALESCE(tx.amount_minor, 0) <> 0)
        UNION ALL
        SELECT NULL, lg.day, lg.book, lg.type_id,
               CASE WHEN lgl.became = '{expect_back.MINE}' THEN lgt.service_id END,
               CASE WHEN lgl.became = '{expect_back.MINE}' THEN COALESCE(lgt.is_one_off, 0) ELSE 0 END,
               lg.flow_type, lg.account_id, lg.amount_minor
        FROM ({expect_back.let_go_sql()}) lg
        JOIN let_go lgl ON lgl.id = lg.let_go_id
        JOIN expectation lge ON lge.id = lgl.expectation_id
        LEFT JOIN transactions lgt ON lgt.id = lge.row_id
    )"""


def counted_from() -> str:
    """`FROM` text for a reader of counted lines: the lines as `t`, and `s`
    as the line's account, so the shared filters' `s.account_id` (scope.py,
    app._build_filters) read as they do on rows."""
    return f"{counted_lines()} t JOIN (SELECT id AS account_id FROM accounts) s ON s.account_id = t.account_id"


def _months(start: str, end: str) -> list[str]:
    start, end = trend_span.parse_month(start), trend_span.parse_month(end)
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    months, (y, m) = [], (int(start[:4]), int(start[5:7]))
    while f"{y:04d}-{m:02d}" <= end:
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def _subject(conn: sqlite3.Connection, subject) -> tuple[str, dict]:
    """(the row filter, the subject as the answer names it). LookupError when
    the merchant or type is not in fin."""
    kind, ref = subject
    if kind not in _SUBJECT:
        raise ValueError(f"subject must be one of {', '.join(_SUBJECT)}")
    where, name_sql = _SUBJECT[kind]
    if name_sql is None:
        return where, {"kind": kind, "id": ref, "name": ref}
    found = conn.execute(name_sql, (ref,)).fetchone()
    if found is None:
        raise LookupError(f"{kind} {ref} is not in fin")
    return where, {"kind": kind, "id": ref, "name": found[0]}


def series(conn: sqlite3.Connection, subject, start: str, end: str,
           stack_by: str = "book") -> dict:
    """The net series of `subject` from month `start` to `end` ('YYYY-MM',
    inclusive), stacked by `stack_by` ('book', 'type' or 'merchant').

    `subject` is ('merchant', service id), ('type', type id) or ('book', name).
    Answers {subject, start, end, stack_by, series}, where series is
    month -> book -> stack key -> currency -> {spent, back, net}. Every month
    of the span is present; a month with no rows is {}. A type's key is its
    display name ('Transport > Rides'), a merchant's its name.
    """
    if stack_by not in _STACK:
        raise ValueError(f"stack_by must be one of {', '.join(_STACK)}")
    months = _months(start, end)
    where, named = _subject(conn, subject)
    rows = conn.execute(
        f"""
        SELECT strftime('%Y-%m', t.date) AS row_month,
               {book_expr('t')} AS row_book,
               {_STACK[stack_by]} AS row_key,
               COALESCE(a.currency, 'SGD') AS row_currency,
               SUM(CASE WHEN t.amount_minor > 0 THEN t.amount_minor ELSE 0 END) AS spent,
               SUM(CASE WHEN t.amount_minor < 0 THEN -t.amount_minor ELSE 0 END) AS back
        FROM {counted_from()}
        JOIN accounts a ON a.id = s.account_id
        LEFT JOIN services svc ON svc.id = t.service_id
        {_TYPE_JOINS.format(owner="t")}
        WHERE t.flow_type IN ('expense', 'refund')
          AND strftime('%Y-%m', t.date) BETWEEN :start AND :end
          AND {_ACCOUNTS_COUNTED}
          AND {where}
        GROUP BY row_month, row_book, row_key, row_currency
        ORDER BY row_month, row_book, row_key, row_currency
        """,
        {
            "start": months[0], "end": months[-1], "subject": subject[1],
            "household": book_type.DEFAULT_BOOK, "household_owner": account_kind.HOUSEHOLD,
            "no_type": NO_TYPE, "no_merchant": NO_MERCHANT,
        },
    ).fetchall()

    out: dict = {m: {} for m in months}
    for r in rows:
        spent, back = r["spent"] or 0, r["back"] or 0
        out[r["row_month"]].setdefault(r["row_book"], {}).setdefault(r["row_key"], {})[r["row_currency"]] = {
            "spent": spent, "back": back, "net": spent - back,
        }
    return {"subject": named, "start": months[0], "end": months[-1],
            "stack_by": stack_by, "series": out}
