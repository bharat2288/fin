"""Parties: a link, not a new home (fin-redesign 09; design evaluation §2).

The merchant row (services) is the party and its rules are its aliases. A
merchant may say what part it plays, its role, from the vocabulary declared
here, once, with a plain description for each member; a merchant that says
none is read as it always was. A person, company, loan, holding or untracked
account may name the party it belongs to (accounts.party_id), so two
person accounts can be one party without being merged (RUL-011), and a
company account can be joined to the merchant its money arrives under.
Companies and people stay accounts with balances (ADR-002, ADR-003).

Nothing reads a role or a party to work out a figure yet: the links move no
number (spec case 12).
"""

from __future__ import annotations

# (name, description). A new role is a new value here.
ROLES = (
    ("merchant", "a shop or service the household buys from"),
    ("employer", "pays the household a salary or other income"),
    ("lender", "a bank or finance house the household repays a loan to; its loan account is joined to it"),
    ("person", "a person outside the household, or people who count as one, whose person accounts "
               "are joined to it"),
    ("company", "a company of the household's, whose company account is joined to it"),
    ("holding", "an exchange, fund or platform where the household holds something it owns; its "
                "holding account is joined to it"),
    ("own", "a place of the household's own that fin holds no statements for; its untracked "
            "account is joined to it"),
)
ROLE_NAMES = tuple(name for name, _ in ROLES)

# The account kinds (account_kind.KINDS) that may name a party. A bank
# account or a card is the household's own place, never a party's.
ACCOUNT_KINDS = ("loan", "holding", "company", "person", "untracked")


class Refused(ValueError):
    """A role or a party link fin cannot hold. Nothing was written."""


def checked_role(value) -> str | None:
    """A role as a writer may store it: one of the declared roles, or None
    (from None or "") for a merchant that says none."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in ROLE_NAMES:
        raise Refused(f"unknown role: {value!r}; a merchant's role is one of {', '.join(ROLE_NAMES)}")
    return value


def kind_refusal(kind: str) -> str | None:
    """Why an account of this kind cannot belong to a party, or None."""
    if kind in ACCOUNT_KINDS:
        return None
    article = "an" if kind[:1] in "aeiou" else "a"
    return (f"{article} {kind} account has no party: only a {', '.join(ACCOUNT_KINDS[:-1])} or "
            f"{ACCOUNT_KINDS[-1]} account joins one")
