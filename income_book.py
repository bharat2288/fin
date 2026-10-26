"""The book an income kind belongs to (fin-redesign 26, part 2; fin RUL-032,
RUL-034): Rental belongs to Orange Grove, so the book's Home card nets the
rent against its costs.

A row still carries a book only when it is spending or a refund
(flow.LABELLED): an income row has its kind, and the kind names the book.
An income kind with no row here, or with Household chosen, is the
household's, and no card nets it.

Which kinds go to which book is a table of its own, one row per kind, so no
row is converted: made empty on start (history.ADDED_ON_START).
"""

from __future__ import annotations

import sqlite3

import book_type

CHOSE = "Chose the book an income kind belongs to"


def held(conn: sqlite3.Connection) -> dict[int, str]:
    """Each income kind's book, by the kind's id; none on a book older than
    the table (a conversion reading it before the start that makes it)."""
    try:
        return dict(conn.execute("SELECT type_id, book FROM income_books"))
    except sqlite3.OperationalError:
        return {}


def book_of(conn: sqlite3.Connection, type_id: int) -> str:
    """The book the income kind belongs to: its chosen book, or Household."""
    return held(conn).get(type_id, book_type.DEFAULT_BOOK)


def kinds_by_book(conn: sqlite3.Connection) -> dict[str, list[int]]:
    """The income kinds each book other than Household holds, by book."""
    out: dict[str, list[int]] = {}
    for type_id, book in sorted(held(conn).items()):
        out.setdefault(book, []).append(type_id)
    return out


def choose(conn: sqlite3.Connection, type_id: int, book: str) -> bool:
    """The income kind belongs to `book` from now on; Household takes its row
    away. Returns whether that changed anything. Not committed."""
    if book == book_of(conn, type_id):
        return False
    if book == book_type.DEFAULT_BOOK:
        conn.execute("DELETE FROM income_books WHERE type_id = ?", (type_id,))
    elif type_id in held(conn):
        conn.execute("UPDATE income_books SET book = ? WHERE type_id = ?", (book, type_id))
    else:
        conn.execute("INSERT INTO income_books (type_id, book) VALUES (?, ?)", (type_id, book))
    return True
