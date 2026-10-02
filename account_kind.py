"""Account kind and owner: what an account is, and whose sheet it is on.

Both vocabularies are declared here, once, with a plain description for each
member. Every writer of an account validates against this declaration through
`checked_kind` and `checked_owner`. The kind is stored in the accounts
table's `type` column.
"""

from __future__ import annotations

import book_type

# (name, description). A new kind is a new value here.
KINDS = (
    ("bank", "a bank or deposit account; its balance rests on a statement, or on a "
             "supplied figure while it has no statement"),
    ("card", "a credit or debit card; its balance rests on a statement and what is owed is negative"),
    ("loan", "money the household owes a lender; its figure is supplied and is negative"),
    ("holding", "something owned that no statement fin imports reports: a home, a car, "
                "crypto held elsewhere; its value is a supplied figure"),
    ("company", "the household's money in a company: costs paid for it plus capital put in, "
                "less what it has paid back"),
    ("person", "money lent to, or owed by, a person outside the household"),
)

KIND_NAMES = tuple(name for name, _ in KINDS)
# The kind an account is given when its writer names none.
DEFAULT_KIND = "card"
# Kinds whose rows come from imported statements.
STATEMENT_KINDS = ("bank", "card")
# Kinds whose balance rests on a figure the operator supplies.
SUPPLIED_FIGURE_KINDS = ("loan", "holding", "company", "person")
# Statement kinds that take a supplied figure while the account has no statement.
SUPPLIED_UNTIL_STATEMENT_KINDS = ("bank",)
# Kinds whose supplied figure is what is owed: typed positive, stored negative.
OWED_KINDS = ("loan",)

# (name, description). The owner says whose sheet the account is on: the
# household's, or a company's. The companies are the company books, so a new
# book is a new owner.
HOUSEHOLD = book_type.DEFAULT_BOOK
OWNERS = ((HOUSEHOLD, "the household's own; on the household's balance sheet"),) + tuple(
    (name, f"owned by the company {name}; not on the household's balance sheet")
    for name in book_type.BOOK_NAMES
    if name != HOUSEHOLD
)

OWNER_NAMES = tuple(name for name, _ in OWNERS)
DEFAULT_OWNER = HOUSEHOLD


def takes_a_figure(kind: str, statement_count: int) -> bool:
    """Whether a figure can be supplied for an account of this kind that has
    this many statements."""
    if kind in SUPPLIED_FIGURE_KINDS:
        return True
    return kind in SUPPLIED_UNTIL_STATEMENT_KINDS and statement_count == 0


class UnknownAccountValue(ValueError):
    """A writer was handed a kind or an owner that is not in its vocabulary."""


def checked_kind(value) -> str:
    """A kind as a writer may store it: one of the declared kinds."""
    if not isinstance(value, str) or value not in KIND_NAMES:
        raise UnknownAccountValue(f"unknown account kind: {value!r}")
    return value


def checked_owner(value) -> str:
    """An owner as a writer may store it: one of the declared owners."""
    if not isinstance(value, str) or value not in OWNER_NAMES:
        raise UnknownAccountValue(f"unknown account owner: {value!r}")
    return value
