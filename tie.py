"""The statement tie: the one check that a statement's rows account for its
own balances.

A statement that states its opening and closing balance ties when the opening
balance less the sum of its rows equals the closing balance, exactly, in whole
minor units. A row's amount is positive for money out. A balance is signed as
the household sees it (cash positive, anything owed negative), so the rule is
the same for a bank account and for a card: the parser of a card statement
hands over the amount owed negated.

Every parser that returns balances is checked here, by the import path, and
nowhere else. A statement that states no balance is not checked.
"""

from __future__ import annotations

from datetime import date

REFUSAL = (
    "Statement does not reconcile: its rows do not add up to its own closing balance. "
    "A row was probably missed when the file was read."
)


class DoesNotTie(ValueError):
    """The rows do not carry the opening balance to the closing balance.

    `figures` is what `figures()` returned: opening, what the rows add up to,
    closing, and the difference.
    """

    def __init__(self, figures: dict):
        super().__init__(REFUSAL)
        self.figures = figures


def _whole(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what} must be a whole number of minor units")
    return value


def figures(opening_minor: int, closing_minor: int, amounts: list[int]) -> dict:
    """Opening, what the rows add up to, closing and the difference, in whole
    minor units.

    `rows_minor` is the change the rows make to the balance (money out
    lowers it), so opening plus rows is the closing balance the rows give;
    `difference_minor` is that less the closing balance stated, and is zero
    when the statement ties.
    """
    opening = _whole(opening_minor, "opening balance")
    closing = _whole(closing_minor, "closing balance")
    rows = -sum(_whole(amount, "a row's amount") for amount in amounts)
    return {
        "opening_minor": opening,
        "rows_minor": rows,
        "closing_minor": closing,
        "difference_minor": opening + rows - closing,
        "rows": len(amounts),
    }


def checked(opening_minor: int, closing_minor: int, amounts: list[int]) -> dict:
    """The figures of a statement that ties. Raises DoesNotTie otherwise."""
    result = figures(opening_minor, closing_minor, amounts)
    if result["difference_minor"] != 0:
        raise DoesNotTie(result)
    return result


def states_balances(statement) -> bool:
    """Whether a parsed statement carries the balances its source states."""
    return statement.opening_minor is not None or statement.closing_minor is not None


def check(statement) -> dict | None:
    """Check a parsed statement against its own balances.

    Returns its figures when it ties and None when its source states no
    balance (not checked). Raises DoesNotTie when the rows do not add up, and
    ValueError when the balances are stated in part or with no real closing
    day: a statement is never taken as tying on half the facts.
    """
    if not states_balances(statement):
        return None
    if statement.opening_minor is None or statement.closing_minor is None:
        raise ValueError("Statement states one balance but not the other")
    try:
        if date.fromisoformat(statement.closing_date).isoformat() != statement.closing_date:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Statement states a closing balance but not its date") from None
    return checked(
        statement.opening_minor,
        statement.closing_minor,
        [tx.amount_minor for tx in statement.transactions],
    )
