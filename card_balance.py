"""A card held as one bill whose rows are split by cardholder (ruling 3,
amended 2026-10-05: the DBS Vantage card).

The card prints one PREVIOUS BALANCE and one TOTAL, with a SUB-TOTAL for each
cardholder's block. Its rows stay on one account per cardholder; its one
balance sits on the balance account (the main cardholder's), which every
cardholder's rows move:

    the statement   ties as a whole: PREVIOUS BALANCE plus every row of the
                    card, on every cardholder's account, is the TOTAL
                    (parse_dbs.by_card hands it over as one statement);
    the import      files each row on its cardholder's account and anchors
                    the TOTAL on the balance account;
    the sheet       the balance account's balance is its anchor less the rows
                    of every account the card's rows are on; a cardholder's
                    account that is part of it shows no balance of its own
                    and is never left out: it is counted in that one owed
                    figure, once (balance_sheet.py, month_check.py).

Rows printed before the first cardholder's block (bill payments) and after
the last SUB-TOTAL (fees) are the card's: they go on the balance account.

Declared here, once: the parsers (parse_dbs, parsers.handle_vantage_split)
and the ledger read it.
"""

from __future__ import annotations

import sqlite3

# (the card's last four, the cardholder as the statement prints them) ->
# the account that cardholder's rows go on.
CARDHOLDER_ACCOUNTS = {
    ("7436", "BHARAT SURI"): "DBS Vantage Visa Infinite Card 3696",
    ("7436", "MILI KALE"): "DBS Vantage Visa Infinite Card 7436 (MK)",
}
# The card's last four -> the account its one balance sits on.
BALANCE_ACCOUNTS = {
    "7436": "DBS Vantage Visa Infinite Card 3696",
}
# The card's last four -> the account name the card's header gave before the
# card was one balance (parse_dbs._normalize_card_header). Rows an import
# filed there are moved to the balance account by convert_vantage_label.py.
HEADER_LABELS = {
    "7436": "DBS VANTAGE VISA INFINITE 7436",
}


def is_split(last_four: str | None) -> bool:
    """Whether a card's rows are split by cardholder."""
    return any(four == last_four for four, _ in CARDHOLDER_ACCOUNTS)


def balance_account(last_four: str | None) -> str | None:
    """The account a split card's one balance sits on, or None."""
    return BALANCE_ACCOUNTS.get(last_four) if last_four else None


def cardholder_account(last_four: str | None, holder: str) -> str | None:
    """The account a cardholder's rows go on, or None for a holder not named."""
    return CARDHOLDER_ACCOUNTS.get((last_four or "", holder.upper().strip()))


def part_of_names() -> dict[str, str]:
    """{a cardholder's account: the balance account it is part of}, for every
    cardholder account that is not itself its card's balance account."""
    return {
        account: BALANCE_ACCOUNTS[four]
        for (four, _), account in CARDHOLDER_ACCOUNTS.items()
        if four in BALANCE_ACCOUNTS and account != BALANCE_ACCOUNTS[four]
    }


def part_of_ids(conn: sqlite3.Connection) -> dict[int, int]:
    """{id of an account that is part of a card's balance: id of the balance
    account}, for the accounts the book holds."""
    ids = {name: id_ for id_, name in conn.execute("SELECT id, name FROM accounts")}
    return {
        ids[part]: ids[whole]
        for part, whole in part_of_names().items()
        if part in ids and whole in ids
    }


def members(conn: sqlite3.Connection, account_id: int) -> list[int]:
    """The accounts whose rows move this account's balance: itself, and every
    cardholder account that is part of it."""
    return [account_id] + sorted(p for p, whole in part_of_ids(conn).items() if whole == account_id)
