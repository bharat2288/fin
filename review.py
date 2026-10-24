"""The review list's choices: what the operator can say a waiting transfer was.

The list is declared here, once, with a plain description for each choice.
Each choice states the flow it writes and what it asks for; the label action
validates against this declaration and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

import account_kind

# What a choice asks the operator for.
ASKS_TYPE = "type"                # a spending type, and a book where the type proposes none
ASKS_ACCOUNT = "account"          # one of the accounts of the kinds the choice names
ASKS_INCOME_KIND = "income_kind"  # one of the income kinds


@dataclass(frozen=True)
class Choice:
    name: str
    label: str            # as the list offers it, after "This was..."
    flow: str             # the flow the label writes
    asks: str | None
    kinds: tuple[str, ...]  # the account kinds its other side may be
    description: str


CHOICES = (
    Choice("spending", "spending", "expense", ASKS_TYPE, (),
           "money spent; it takes a type, and counts in its book's spending"),
    Choice("gift", "a gift", "expense", None, (),
           "a gift: spending when given, income when received, each under the type fin files gifts under"),
    Choice("own_account", "a move to my own account", "transfer", ASKS_ACCOUNT,
           (*account_kind.STATEMENT_KINDS, "untracked"),
           "money moved to or from another of the household's bank accounts or cards, "
           "or one of its own that fin holds no statements for"),
    Choice("company", "money into a company", "movement", ASKS_ACCOUNT, ("company",),
           "capital put into a company, or the company paying it back; it moves the company's balance"),
    Choice("loan_repayment", "a loan repayment", "movement", ASKS_ACCOUNT, ("loan",),
           "an instalment or repayment on one of the household's loans"),
    Choice("income", "income", "income", ASKS_INCOME_KIND, (),
           "money the household earned or was given; it takes an income kind"),
)

BY_NAME = {choice.name: choice for choice in CHOICES}

# A person is no movement's other side since the contract (personal-ledger 23,
# release 3): what is lent to or owed to a person is an expectation on the
# row, named for them. "a loan to a person" left the choices; a label or a
# flow edit that would name a person as a movement's other side is refused in
# these words, and writes nothing. Rows converted before keep their flow and
# other side as history.
RETIRED = ("loan_to_person",)
PERSON_MOVEMENT_REFUSED = (
    "A person is no longer a movement's other side: mark the row Expect some back… "
    "(money out) or Expect to pay back (money in)."
)


class LabelRefused(ValueError):
    """The label does not say what its choice asks for. Nothing was changed."""
