"""Ingest helpers for the fin database.

Provides account/statement creation and the PayNow type fallback,
imported by app.py for the import-confirm workflow.
"""

import re
import sqlite3

import account_kind


# Bank statement PayNow payee → type mapping
# These are identified by the "To:" field in bank statement descriptions
PAYNOW_RULES = [
    # (pattern_in_description, type name, notes)
    ("CENTRAL PROVIDENT FUND BOARD", "Tax", "CPF payment"),
    ("CPF VOLUNTARY CONTRIBUTIONS", "Tax", "CPF voluntary contribution"),
    ("SINGAPORE LIFE", "Insurance", "Life insurance premium"),
    ("INLAND REVENUE", "Tax", "Tax payment (IRAS)"),
    ("SINGAPORE ISLAND", "Fitness", "SICC country club"),
    ("SICC", "Fitness", "SICC country club"),
    ("SITOH SIEW KIM", "Kids", "Nanny"),
    ("OSTEOPATHIC", "Medical", "Osteopath"),
    ("TERRA MEDICAL", "Medical", "Medical"),
    ("LIMITLESS WELLNESS", "Fitness", "Wellness"),
    ("VIN GOLF", "Fitness", "Golf"),
    ("RK", "Other", "Miscellaneous"),
    ("AMAC", "Home", "Appliance repair"),
    ("BJT", "Other", "Unknown"),
    ("FATELICIOUS", "Dining", "Snacks"),
    ("DREAMCORE", "Other", "PC parts"),
    ("JEREMY L", "Other", "Unknown"),
    ("LEC", "Other", "Unknown"),
]


def paynow_type(description: str) -> str | None:
    """Match a bank statement PayNow/transfer description to a type.

    Returns the type's name, or None if no payee wording matches. The caller
    resolves the name to its id.
    """
    desc_upper = description.upper()
    for pattern, type_name, _ in PAYNOW_RULES:
        if pattern.upper() in desc_upper:
            return type_name
    return None


def find_account(conn: sqlite3.Connection, card_info: str) -> int | None:
    """The account a card info string names, or None when there is none yet.

    Matching priority:
    1. Exact name match
    2. Last-four digit match (extracts trailing 4-digit group from card_info)
    3. Account number substring match (long digit sequences in card_info)
    """
    if not card_info:
        card_info = "Unknown Account"

    # 1. Exact name match
    existing = conn.execute(
        "SELECT id FROM accounts WHERE name = ?", (card_info,)
    ).fetchone()
    if existing:
        return existing[0]

    # 2. Match by last_four digits (strip all non-digits, take last 4)
    all_digits = re.sub(r"\D", "", card_info)
    last_four = all_digits[-4:] if len(all_digits) >= 4 else None
    if last_four:
        match = conn.execute(
            "SELECT id FROM accounts WHERE last_four = ? AND status = 'active'",
            (last_four,),
        ).fetchone()
        if match:
            return match[0]

    # 3. Match by long account number substring (6+ digits)
    long_digits = re.findall(r"\d{6,}", card_info)
    for num in long_digits:
        match = conn.execute(
            "SELECT id FROM accounts WHERE name LIKE ? AND status = 'active'",
            (f"%{num}%",),
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

    found = find_account(conn, card_info)
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


def ensure_statement(
    conn: sqlite3.Connection,
    account_id: int,
    statement_date: str,
    filename: str,
) -> tuple[int, bool]:
    """Get or create a statement record.

    Returns (statement_id, is_new). If a record already exists for this
    account + date, returns the existing ID with is_new=False. Does not
    commit: the caller owns the transaction.
    """
    existing = conn.execute(
        "SELECT id FROM statements WHERE account_id = ? AND statement_date = ?",
        (account_id, statement_date),
    ).fetchone()
    if existing:
        return (existing["id"], False)

    cur = conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, ?)",
        (account_id, statement_date, filename),
    )
    return (cur.lastrowid, True)
