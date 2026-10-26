"""Ingest helpers for the fin database.

Provides account/statement creation, imported by app.py for the
import-confirm workflow. (The PayNow payee wordings are merchant rules now:
db.PAYNOW_WORDINGS, type-relationships 05.)
"""

import re
import sqlite3

import account_kind
import money
import statement_period


def find_account(conn: sqlite3.Connection, card_info: str, currency: str | None = None) -> int | None:
    """The account a card info string names, or None when there is none yet.

    Matching priority:
    1. Exact name match
    2. Last-four digit match (extracts trailing 4-digit group from card_info)
    3. Account number substring match (long digit sequences in card_info)

    A name ending in a currency fin keeps ("100-018277-1 USD", an account's
    section in another currency, parse_dbs) matches by number only an account
    kept in that currency. Otherwise 2 and 3 take an account kept in the
    currency given (SGD, with none) first, then any: a statement in the wrong
    currency for its account is still found, and refused.
    """
    if not card_info:
        card_info = "Unknown Account"
    suffix = re.search(r"\s([A-Z]{3})$", card_info)
    only = suffix.group(1) if suffix and suffix.group(1) in money.MINOR_UNIT_DIGITS else None
    # Unnamed, the household's own currency first (two sections of one DBS
    # account share its last four).
    kept_in = [only] if only else [currency, None] if currency else ["SGD", None]

    # 1. Exact name match
    existing = conn.execute(
        "SELECT id FROM accounts WHERE name = ?", (card_info,)
    ).fetchone()
    if existing:
        return existing[0]

    # An untracked account holds no statement, so no statement lands on it.
    untracked = ", ".join("?" for _ in account_kind.UNTRACKED_KINDS)

    # 2. Match by last_four digits (strip all non-digits, take last 4)
    all_digits = re.sub(r"\D", "", card_info)
    last_four = all_digits[-4:] if len(all_digits) >= 4 else None
    # An account's section in another currency ("… USD") is never taken for
    # a name that does not say that currency.
    foreign = [c for c in money.MINOR_UNIT_DIGITS if c != "SGD"]
    long_digits = re.findall(r"\d{6,}", card_info)
    for code in kept_in:
        in_currency = (
            " AND COALESCE(currency, 'SGD') = ?" if code is not None
            else "".join(f" AND name NOT LIKE '% {c}'" for c in foreign)
        )
        if last_four:
            match = conn.execute(
                f"SELECT id FROM accounts WHERE last_four = ? AND status = 'active' AND type NOT IN ({untracked})"
                f"{in_currency} ORDER BY id",
                (last_four, *account_kind.UNTRACKED_KINDS, *([code] if code else [])),
            ).fetchone()
            if match:
                return match[0]

        # 3. Match by long account number substring (6+ digits)
        for num in long_digits:
            match = conn.execute(
                f"SELECT id FROM accounts WHERE name LIKE ? AND status = 'active' AND type NOT IN ({untracked})"
                f"{in_currency} ORDER BY id",
                (f"%{num}%", *account_kind.UNTRACKED_KINDS, *([code] if code else [])),
            ).fetchone()
            if match:
                return match[0]

    return None


def ensure_account(
    conn: sqlite3.Connection, card_info: str, stmt_type: str, currency: str = "SGD",
) -> int:
    """Find or create an account from card info string: the account
    `find_account` gives, or a new one of the kind given, kept in the
    currency given. Does not commit: the caller owns the transaction."""
    if not card_info:
        card_info = "Unknown Account"

    found = find_account(conn, card_info, currency)
    if found is not None:
        return found

    all_digits = re.sub(r"\D", "", card_info)
    last_four = all_digits[-4:] if len(all_digits) >= 4 else None

    account_kind.checked_kind(stmt_type)
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four, currency) VALUES (?, ?, ?, ?, ?)",
        (card_info, card_info, stmt_type, last_four, currency),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def ensure_named_account(
    conn: sqlite3.Connection, name: str, stmt_type: str, currency: str = "SGD",
) -> int:
    """The account of exactly this name, or a new one: for an account a rule
    names (a cardholder's account on a card split by cardholder), which a
    match by last four could take for another. Does not commit."""
    existing = conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
    if existing:
        return existing[0]
    account_kind.checked_kind(stmt_type)
    all_digits = re.sub(r"\D", "", name)
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four, currency) VALUES (?, ?, ?, ?, ?)",
        (name, name, stmt_type, all_digits[-4:] if len(all_digits) >= 4 else None, currency),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _free_day_in_month(conn: sqlite3.Connection, account_id: int, month: str) -> str:
    """The first day of a month (YYYY-MM) the account holds no record for."""
    taken = {r[0] for r in conn.execute(
        "SELECT statement_date FROM statements WHERE account_id = ? AND statement_date LIKE ?",
        (account_id, f"{month}-%"),
    )}
    for day in range(1, 32):
        candidate = f"{month}-{day:02d}"
        if candidate not in taken:
            return candidate
    raise ValueError(f"no free day left in {month} for a statement record")


def ensure_statement(
    conn: sqlite3.Connection,
    account_id: int,
    statement_date: str,
    filename: str,
    printed: bool = False,
    period_start: str | None = None,
) -> tuple[int, bool]:
    """Get or create a statement record.

    Returns (statement_id, is_new). Does not commit: the caller owns the
    transaction.

    `printed` is a statement as printed: dated by its closing day, holding
    the rows it printed; the record held for that day is returned. A month
    record (printed False) is the account's record for the month of
    `statement_date`, whatever day it is dated: normally the 1st, or the
    first free day when a printed statement closed on the 1st. A month
    record that sits on the day a printed statement needs is moved to a free
    day of its month, keeping its rows, so the two never share a record.

    A new record stores the first day it covers where that is known on its
    own (statement_period.py): a printed statement's `period_start`, the
    opening day its parser read; a month record's 1st. A printed statement
    whose parser read none stores none: its start is read from the closing
    before it whenever it is asked for (statement_period.start), so a
    statement imported, undone or deleted beside it later never leaves it
    stale. A printed record already held that has none is given the opening
    day when this import reads one.
    """
    if not printed:
        month = statement_date[:7]
        existing = conn.execute(
            "SELECT id FROM statements WHERE account_id = ? AND printed = 0 "
            "AND statement_date LIKE ? ORDER BY statement_date LIMIT 1",
            (account_id, f"{month}-%"),
        ).fetchone()
        if existing:
            return (existing[0], False)
        held = conn.execute(
            "SELECT 1 FROM statements WHERE account_id = ? AND statement_date = ?",
            (account_id, statement_date),
        ).fetchone()
        on = _free_day_in_month(conn, account_id, month) if held else statement_date
        cur = conn.execute(
            "INSERT INTO statements (account_id, statement_date, filename, printed, period_start)"
            " VALUES (?, ?, ?, 0, ?)",
            (account_id, on, filename, statement_period.first_of_month(statement_date)),
        )
        return (cur.lastrowid, True)

    existing = conn.execute(
        "SELECT id, printed FROM statements WHERE account_id = ? AND statement_date = ?",
        (account_id, statement_date),
    ).fetchone()
    if existing and existing[1]:
        if period_start:
            conn.execute(
                "UPDATE statements SET period_start = ? WHERE id = ? AND period_start IS NULL",
                (period_start, existing[0]),
            )
        return (existing[0], False)
    if existing:
        conn.execute(
            "UPDATE statements SET statement_date = ? WHERE id = ?",
            (_free_day_in_month(conn, account_id, statement_date[:7]), existing[0]),
        )
    cur = conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename, printed, period_start)"
        " VALUES (?, ?, ?, 1, ?)",
        (account_id, statement_date, filename, period_start or None),
    )
    return (cur.lastrowid, True)
