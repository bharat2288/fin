"""A shared account (fin-redesign 20; RUL-020): a bank account or a card the
household keeps money on beside someone else's, such as HDFC 6324.

A row on a shared account is "not ours" (flow.NOT_OURS) unless a rule states
what it is or it moves money from one of the household's own accounts
(flow.classify_row): it counts in no spending, income or balance. The
statement tie still checks every row on it. Its balance on the sheet is the
household's part: the latest figure the operator supplied, plus the
household's rows after it (balance_sheet); its statement balances are the
whole account's and are only tied.

Which accounts are shared is a table of its own, one row per account, so no
account row is converted: made empty on start (history.ADDED_ON_START).
"""

from __future__ import annotations

import sqlite3

import account_kind

# The kinds of account that can be shared: those that hold statements.
KINDS = account_kind.STATEMENT_KINDS

MARKED = "Marked an account shared"
UNMARKED = "Marked an account not shared"


def ids(conn: sqlite3.Connection) -> frozenset[int]:
    """The ids of the accounts marked shared; none on a book older than the
    table (a conversion reading it before the start that makes it)."""
    try:
        return frozenset(r[0] for r in conn.execute("SELECT account_id FROM shared_accounts"))
    except sqlite3.OperationalError:
        return frozenset()


def is_shared(conn: sqlite3.Connection, account_id) -> bool:
    return account_id is not None and account_id in ids(conn)


def kind_refusal(kind: str) -> str | None:
    """Why an account of this kind cannot be shared, or None."""
    if kind in KINDS:
        return None
    return f"only a bank account or a card can be shared; this is a {kind} account"


def not_ours_refusal(conn: sqlite3.Connection, account_id, flow_name) -> str | None:
    """Why a row on this account may not be said to be not ours, or None."""
    import flow  # flow reads this module's ids
    if flow_name != flow.NOT_OURS or is_shared(conn, account_id):
        return None
    return ("a row is not ours only on a shared account; mark the account shared first. "
            "Nothing was changed")


def mark(conn: sqlite3.Connection, account_id: int, shared: bool) -> bool:
    """Mark the account shared, or not. Returns whether that changed
    anything. Not committed; the rows' flows are the caller's to move."""
    if shared == is_shared(conn, account_id):
        return False
    if shared:
        conn.execute("INSERT INTO shared_accounts (account_id) VALUES (?)", (account_id,))
    else:
        conn.execute("DELETE FROM shared_accounts WHERE account_id = ?", (account_id,))
    return True
