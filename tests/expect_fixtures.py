"""Shared seeding for the expect-back build (personal-ledger 23, ticket 25).

The check book's specimens, with the mockup's names: the Household's cards
and bank, the rupee card, the people, a loan and a company. Every lane
imports this file; a lane that needs more adds a helper in its own test file.

Rows are written as fin stores them (trend_fixtures' shape: one calendar-month
statement per account and month). The records (expectations, names,
set-offs, let-gos, wordings, dismissals, refund links) are written directly,
not through expect_back's guarded writes, each inside an open change-history
entry, so a test can undo any of them from Changes as the operator would.
`entry()` wraps several into one change.

Amounts are whole minor units of the account's currency, always positive in
the records; a row is positive for money out, negative for money in. Every
name and figure is invented. Nothing here opens a database: tests get `conn`
from tests/conftest.py, whose guard fails any test that touches the real file.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass, field

import book_type
import history
from trend_fixtures import month_end, type_id

PEOPLE = ("Jay", "Kim", "Lena", "Ravi", "Parents", "Sam", "Dev")


@dataclass
class Book:
    """The ids of the check book's accounts, by the specimens' names."""
    rewards_card: int
    everyday_card: int
    savings: int
    rupee_card: int
    home_loan: int
    company: int
    people: dict[str, int]
    statements: dict = field(default_factory=dict)


def _account(conn, name: str, kind: str, currency: str = "SGD") -> int:
    return conn.execute(
        "INSERT INTO accounts (name, short_name, type, currency, owner) VALUES (?, ?, ?, ?, ?)",
        (name, name.replace(" ", "-"), kind, currency, book_type.DEFAULT_BOOK),
    ).lastrowid


def check_book(conn: sqlite3.Connection) -> Book:
    """The accounts every case uses. Commits."""
    book = Book(
        rewards_card=_account(conn, "Rewards card", "card"),
        everyday_card=_account(conn, "Everyday card", "card"),
        savings=_account(conn, "Everyday savings", "bank"),
        rupee_card=_account(conn, "Rupee card", "card", "INR"),
        home_loan=_account(conn, "Home loan", "loan"),
        company=_account(conn, "Moom", "company"),
        people={name: _account(conn, name, "person") for name in PEOPLE},
    )
    conn.commit()
    return book


def row(conn: sqlite3.Connection, book: Book, account_id: int, day: str, description: str,
        amount_minor: int, *, flow: str = "expense", type_name: str | None = None,
        book_name: str | None = None, other_side: int | None = None) -> int:
    """A row on `account_id`, filed on that account's calendar-month record.
    Commits."""
    month = day[:7]
    statement_id = book.statements.get((account_id, month))
    if statement_id is None:
        statement_id = conn.execute(
            "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, ?)",
            (account_id, month_end(month), f"sample-{month}.csv"),
        ).lastrowid
        book.statements[(account_id, month)] = statement_id
    row_id = conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id, "
        "flow_type, other_side_id, cat_source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'auto')",
        (statement_id, day, description, amount_minor, book_name,
         type_id(conn, type_name) if type_name else None, flow, other_side),
    ).lastrowid
    conn.commit()
    return row_id


# --- changes -----------------------------------------------------------------

_open: list[int] = []


@contextlib.contextmanager
def entry(conn: sqlite3.Connection, summary: str):
    """One change-history entry around the writes inside it, as an app route
    makes one: opened, recorded by the triggers, committed and closed. Nested
    use joins the entry already open. Yields the entry's id."""
    if _open:
        yield _open[-1]
        return
    with history.WRITE_LOCK:
        entry_id = history.open_entry(conn, history.VIA_APP, history.APP_ACTOR, summary)
        _open.append(entry_id)
        try:
            yield entry_id
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            _open.pop()
            history.close_entry(conn, entry_id, summary)


def _currency_of(conn, row_id: int) -> str:
    return conn.execute(
        "SELECT COALESCE(a.currency, 'SGD') FROM transactions t JOIN statements s ON s.id = t.statement_id "
        "JOIN accounts a ON a.id = s.account_id WHERE t.id = ?", (row_id,)).fetchone()[0]


def mark(conn: sqlite3.Connection, row_id: int, amount_minor: int, *, names=(), direction: str | None = None,
         note: str | None = None, source: str = "hand") -> int:
    """An expectation on a row, named for `names` (person account ids). The
    direction is the row's: back on money out, pay on money in."""
    if direction is None:
        amount = conn.execute("SELECT amount_minor FROM transactions WHERE id = ?", (row_id,)).fetchone()[0]
        direction = "pay" if amount < 0 else "back"
    with entry(conn, "Expect back") as _:
        expectation_id = conn.execute(
            "INSERT INTO expectation (row_id, direction, amount_minor, currency, note, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (row_id, direction, amount_minor, _currency_of(conn, row_id), note, source),
        ).lastrowid
        for person_id in names:
            conn.execute("INSERT INTO expectation_name (expectation_id, person_id) VALUES (?, ?)",
                         (expectation_id, person_id))
    return expectation_id


def opening(conn: sqlite3.Connection, person_id: int, day: str, amount_minor: int, *,
            direction: str = "back", source: str = "converted") -> int:
    """An opening figure: an expectation with no row, named for its person."""
    with entry(conn, "Opening figure"):
        expectation_id = conn.execute(
            "INSERT INTO expectation (person_id, opening_date, direction, amount_minor, currency, source) "
            "VALUES (?, ?, ?, ?, 'SGD', ?)",
            (person_id, day, direction, amount_minor, source),
        ).lastrowid
        conn.execute("INSERT INTO expectation_name (expectation_id, person_id) VALUES (?, ?)",
                     (expectation_id, person_id))
    return expectation_id


def set_off(conn: sqlite3.Connection, payment_row_id: int, expectation_id: int | None, amount_minor: int, *,
            item_amount_minor: int | None = None, rate: str | None = None, rate_date: str | None = None,
            person_id: int | None = None, note: str | None = None, source: str = "hand") -> int:
    """A payment row placed against an expectation, or waiting for its row
    (expectation_id None, with a person)."""
    with entry(conn, "Set off"):
        return conn.execute(
            "INSERT INTO set_off (payment_row_id, expectation_id, person_id, note, amount_minor, "
            "item_amount_minor, rate, rate_date, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (payment_row_id, expectation_id, person_id, note, amount_minor,
             item_amount_minor if item_amount_minor is not None else amount_minor, rate, rate_date, source),
        ).lastrowid


def unlink(conn: sqlite3.Connection, set_off_id: int) -> None:
    """Unlink one set-off."""
    with entry(conn, "Unlinked a payment"):
        conn.execute("DELETE FROM set_off WHERE id = ?", (set_off_id,))


def edit(conn: sqlite3.Connection, expectation_id: int, amount_minor: int) -> None:
    """Change an expectation's amount."""
    with entry(conn, "Changed what is expected back"):
        conn.execute("UPDATE expectation SET amount_minor = ? WHERE id = ?", (amount_minor, expectation_id))


def let_go(conn: sqlite3.Connection, expectation_id: int, became: str, counts_on: str, *,
           type_name: str = "Gifts & Donations", book_name: str = book_type.DEFAULT_BOOK,
           to_whom_id: int | None = None) -> int:
    """Let an expectation's whole open rest go, counted on `counts_on`."""
    expected = conn.execute("SELECT amount_minor FROM expectation WHERE id = ?", (expectation_id,)).fetchone()[0]
    placed = conn.execute("SELECT COALESCE(SUM(item_amount_minor), 0) FROM set_off WHERE expectation_id = ?",
                          (expectation_id,)).fetchone()[0]
    with entry(conn, "Let it go"):
        return conn.execute(
            "INSERT INTO let_go (expectation_id, amount_minor, became, book, type_id, to_whom_id, counts_on) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (expectation_id, expected - placed, became, book_name, type_id(conn, type_name),
             to_whom_id, counts_on),
        ).lastrowid


def wording(conn: sqlite3.Connection, words: str, person_id: int) -> int:
    """A sender wording learnt for a person."""
    with entry(conn, "Learnt a sender's wording"):
        return conn.execute("INSERT INTO sender_wording (wording, person_id) VALUES (?, ?)",
                            (words, person_id)).lastrowid


def dismiss(conn: sqlite3.Connection, row_id: int, kind: str = "payback", purchase_row_id: int | None = None) -> int:
    """Not a payback, or an unlinked refund link the linker must not remake."""
    with entry(conn, "Not a payback"):
        return conn.execute("INSERT INTO dismissal (row_id, kind, purchase_row_id) VALUES (?, ?, ?)",
                            (row_id, kind, purchase_row_id)).lastrowid


def refund_link(conn: sqlite3.Connection, refund_row_id: int, purchase_row_id: int, source: str = "auto") -> int:
    """A refund row linked to its purchase."""
    with entry(conn, "Linked a refund"):
        return conn.execute("INSERT INTO refund_link (refund_row_id, purchase_row_id, source) VALUES (?, ?, ?)",
                            (refund_row_id, purchase_row_id, source)).lastrowid


def last_entry(conn: sqlite3.Connection) -> int:
    """The newest closed change's id."""
    return conn.execute("SELECT MAX(id) FROM change_entries WHERE open = 0").fetchone()[0]


# --- the October story -------------------------------------------------------


@dataclass
class October:
    """The specimen rows of the spec's case list, with the check book."""
    book: Book
    harbour: int        # HARBOUR GRILL, 2 Oct, Rewards card, S$ 300.00, Dining
    klook: int          # KLOOK TRAVEL, 28 Sep, Everyday card, S$ 180.00, Entertainment
    golden: int         # GOLDEN VILLAGE, 21 Sep, Rewards card, S$ 96.00, Entertainment
    airbnb: int         # AIRBNB, 28 Jun, Rewards card, S$ 620.00, Travel
    jay_0412: int       # PAYNOW FROM JAY TAN 0412, 10 Oct, Everyday savings, S$ 120.00 in
    kim_7781: int       # PAYNOW FROM KIM L 7781, 15 Oct, S$ 70.00 in
    lena_2210: int      # PAYNOW FROM LENA 2210, 12 Oct, S$ 60.00 in
    swiggy: int         # SWIGGY · Mumbai, 6 Oct, Rupee card, ₹ 2,400.00, Dining
    company_row: int    # SAMPLE SUPPLIER, 3 Oct, Rewards card, S$ 50.00, Moom's cost
    loan_row: int       # LOAN INSTALMENT, 5 Oct, Everyday savings, S$ 900.00 movement to the home loan
    card_payoff: int    # PAYMENT TO REWARDS CARD, 8 Oct, Everyday savings, S$ 400.00 payment

    @property
    def people(self) -> dict[str, int]:
        return self.book.people


def october(conn: sqlite3.Connection) -> October:
    """The check book with the case list's rows, before any record."""
    b = check_book(conn)
    return October(
        book=b,
        harbour=row(conn, b, b.rewards_card, "2026-10-02", "HARBOUR GRILL", 30000, type_name="Dining"),
        klook=row(conn, b, b.everyday_card, "2026-09-28", "KLOOK TRAVEL", 18000, type_name="Entertainment"),
        golden=row(conn, b, b.rewards_card, "2026-09-21", "GOLDEN VILLAGE", 9600, type_name="Entertainment"),
        airbnb=row(conn, b, b.rewards_card, "2026-06-28", "AIRBNB", 62000, type_name="Travel"),
        jay_0412=row(conn, b, b.savings, "2026-10-10", "PAYNOW FROM JAY TAN 0412", -12000, flow="review"),
        kim_7781=row(conn, b, b.savings, "2026-10-15", "PAYNOW FROM KIM L 7781", -7000, flow="review"),
        lena_2210=row(conn, b, b.savings, "2026-10-12", "PAYNOW FROM LENA 2210", -6000, flow="review"),
        swiggy=row(conn, b, b.rupee_card, "2026-10-06", "SWIGGY · Mumbai", 240000, type_name="Dining"),
        company_row=row(conn, b, b.rewards_card, "2026-10-03", "SAMPLE SUPPLIER", 5000,
                        type_name="Dining", book_name="Moom"),
        loan_row=row(conn, b, b.savings, "2026-10-05", "LOAN INSTALMENT", 90000, flow="movement",
                     other_side=b.home_loan),
        card_payoff=row(conn, b, b.savings, "2026-10-08", "PAYMENT TO REWARDS CARD", 40000, flow="payment",
                        other_side=b.rewards_card),
    )


def harbour_set_against(conn: sqlite3.Connection, o: October) -> dict:
    """Cases 1, 10 and 11 on the seeded book: HARBOUR GRILL S$ 200.00 expected
    back from Jay and Kim; KLOOK all of it from Kim; Jay's S$ 120.00 on
    HARBOUR; Kim's S$ 70.00 as S$ 60.00 on HARBOUR and S$ 10.00 on KLOOK. Each
    act its own change. HARBOUR has S$ 20.00 open."""
    jay, kim = o.people["Jay"], o.people["Kim"]
    harbour = mark(conn, o.harbour, 20000, names=(jay, kim))
    mark_entry = last_entry(conn)
    klook = mark(conn, o.klook, 18000, names=(kim,), note="concert tickets")
    jay_on_harbour = set_off(conn, o.jay_0412, harbour, 12000)
    jay_entry = last_entry(conn)
    with entry(conn, "Set off") as kim_entry:
        kim_on_harbour = set_off(conn, o.kim_7781, harbour, 6000)
        kim_on_klook = set_off(conn, o.kim_7781, klook, 1000)
    return {"harbour": harbour, "klook": klook, "mark_entry": mark_entry, "jay_entry": jay_entry,
            "kim_entry": kim_entry, "jay_on_harbour": jay_on_harbour, "kim_on_harbour": kim_on_harbour,
            "kim_on_klook": kim_on_klook}
