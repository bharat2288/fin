"""The book a loan's worked-out interest goes to (fin-redesign 26; fin
RUL-032): Orange Grove is its own book, and the mortgage's interest is its
cost, not Household spending.

A loan the operator chose a book for has one row here; a loan with none, or
with Household chosen, has its interest in Household (book_type.DEFAULT_BOOK).
loan_interest.derive files the loan's rows under `book_of`, so a new choice
moves rows already worked out in the same change. The rows are then that
book's costs everywhere a book's spending is read: a company book with an
account of its own (book_type.company_book_of) counts them as paid for it,
as it counts any of the household's spending filed under it.

Which loans go to which book is a table of its own, one row per loan, so no
account row is converted: made empty on start (history.ADDED_ON_START).
"""

from __future__ import annotations

import json
import sqlite3

import book_type

CHOSE = "Chose the book a loan's interest goes to"


def held(conn: sqlite3.Connection) -> dict[int, str]:
    """Each loan's chosen book, by the loan's id; none on a book older than
    the table (a conversion reading it before the start that makes it)."""
    try:
        return dict(conn.execute("SELECT account_id, book FROM interest_books"))
    except sqlite3.OperationalError:
        return {}


def book_of(conn: sqlite3.Connection, loan_id: int) -> str:
    """The book the loan's interest goes to: its chosen book, or Household."""
    return held(conn).get(loan_id, book_type.DEFAULT_BOOK)


def choose(conn: sqlite3.Connection, loan_id: int, book: str) -> bool:
    """File the loan's interest under `book` from now on. Household takes the
    loan's row away. Returns whether that changed anything. Not committed;
    the rows are the caller's to work out again (loan_interest.derive)."""
    if book == book_of(conn, loan_id):
        return False
    if book == book_type.DEFAULT_BOOK:
        conn.execute("DELETE FROM interest_books WHERE account_id = ?", (loan_id,))
    elif loan_id in held(conn):
        conn.execute("UPDATE interest_books SET book = ? WHERE account_id = ?", (book, loan_id))
    else:
        conn.execute("INSERT INTO interest_books (account_id, book) VALUES (?, ?)", (loan_id, book))
    return True


def loans_in_entry(conn: sqlite3.Connection, entry_id: int) -> set[int]:
    """The loans whose choice of book a change entry made, changed or took
    away: an undo of it works their interest out again (history.undo)."""
    loans = set()
    for before, after in conn.execute(
        "SELECT before, after FROM change_rows WHERE entry_id = ? AND tbl = 'interest_books'", (entry_id,)
    ):
        for side in (before, after):
            if side:
                loans.add(json.loads(side)["account_id"])
    return loans
