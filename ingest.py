"""Ingest helpers for the fin database.

Provides account/statement creation and PayNow categorization,
imported by app.py for the import-confirm workflow.
"""

import re
import sqlite3


# Bank statement PayNow payee → category mapping
# These are identified by the "To:" field in bank statement descriptions
PAYNOW_RULES = [
    # (pattern_in_description, category_name, notes)
    ("SINGAPORE LIFE", "Insurance", "Life insurance premium"),
    ("INLAND REVENUE", "Other", "Tax payment (IRAS)"),
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


def categorize_bank_paynow(description: str) -> tuple[int | None, str | None]:
    """Match bank statement PayNow/transfer descriptions to categories.

    Returns (category_id, category_name) or (None, None) if no match.
    Requires a DB lookup, so we return the category name for later resolution.
    """
    desc_upper = description.upper()
    for pattern, cat_name, _ in PAYNOW_RULES:
        if pattern.upper() in desc_upper:
            return None, cat_name  # category_name, resolve to ID later
    return None, None


def ensure_account(conn: sqlite3.Connection, card_info: str, stmt_type: str) -> int:
    """Find or create an account from card info string."""
    if not card_info:
        card_info = "Unknown Account"

    existing = conn.execute(
        "SELECT id FROM accounts WHERE name = ?", (card_info,)
    ).fetchone()
    if existing:
        return existing[0]

    digits = re.findall(r"\d{4}", card_info)
    last_four = digits[-1] if digits else None

    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four) VALUES (?, ?, ?, ?)",
        (card_info, card_info, stmt_type, last_four),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def ensure_statement(
    conn: sqlite3.Connection,
    account_id: int,
    statement_date: str,
    filename: str,
) -> tuple[int, bool]:
    """Get or create a statement record.

    Returns (statement_id, is_new). If a record already exists for this
    account + date, returns the existing ID with is_new=False.
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
    conn.commit()
    return (cur.lastrowid, True)
