"""Read the Independent Reserve account activity export (CSV).

The export is everything the exchange account did, newest first:
  - a first line "sep=,", then the header "Settled,Date,TransactionGuid,
    TradeGuid,OrderGuid,Type,Status,Currency,Credit,Debit,Balance,Comment,
    BlockchainTransaction";
  - one line per entry: its time (the exchange's own, Sydney, with its
    offset), its Type (Deposit, Withdrawal, Trade, Brokerage, GST, Withdrawal
    Fee, Referral Bonus), Status, Currency, the amount as Credit or Debit, and
    that currency's running balance. Amounts carry up to eight decimals.

The household's money moves in and out of the exchange as Singapore dollars:
an SGD Deposit (from a bank account) and an SGD Withdrawal (to one). Those
are the moves this reads, dated in Singapore, in whole cents; a row's amount
is positive for money out of the exchange account. Nothing else is a move:
  - a Trade sells one currency for another inside the account (every trade
    in the export is crypto sold for SGD, its two sides sharing a trade id),
    and its Brokerage and GST are charged inside it, in fractions of a cent;
  - a Withdrawal Fee and its GST are the exchange's charges, inside it;
  - a crypto Deposit or Withdrawal moves coins, not money;
  - a line not Confirmed did not happen.
They are counted in `left_out`, by what they are.

fin keeps Independent Reserve as a holding (RUL-012), and a holding takes no
statement rows: its balance moves by the bank rows that name it, and its value
is a figure. The SGD moves here are those bank rows seen from the exchange's
side, so the upload says what the file holds and imports nothing from it.
"""

from __future__ import annotations

import csv
import io
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import money
from parse_dbs import ParsedTransaction

ACCOUNT = "Independent Reserve"
CURRENCY = "SGD"
HEADER = (
    "Settled,Date,TransactionGuid,TradeGuid,OrderGuid,Type,Status,Currency,"
    "Credit,Debit,Balance,Comment,BlockchainTransaction"
)
# The household's day: Singapore keeps one offset all year.
SINGAPORE = timezone(timedelta(hours=8))
_TIME_FORMAT = "%d %b %Y %H:%M:%S %z"
# (Type, the move's wording). A deposit is money into the exchange account.
MOVES = {"Deposit": "deposit", "Withdrawal": "withdrawal"}
CONFIRMED = "Confirmed"


@dataclass
class Activity:
    """What the export holds: the SGD moves, oldest first, and a count of
    every other line by what it is."""
    moves: list[ParsedTransaction] = field(default_factory=list)
    left_out: Counter = field(default_factory=Counter)
    first_date: str | None = None   # YYYY-MM-DD, in Singapore
    last_date: str | None = None
    filename: str = ""


def _lines(filepath: str) -> list[str]:
    with open(filepath, encoding="utf-8-sig", newline="") as handle:
        return handle.read().splitlines()


def detect_independent_reserve_csv(filepath: str) -> bool:
    try:
        lines = _lines(filepath)[:2]
    except (OSError, UnicodeDecodeError):
        return False
    return len(lines) == 2 and lines[0].strip() == "sep=," and lines[1].strip() == HEADER


def _day(when: str) -> str:
    """An export time as the day it was in Singapore."""
    return datetime.strptime(when.strip(), _TIME_FORMAT).astimezone(SINGAPORE).date().isoformat()


def read_activity(filepath: str) -> Activity:
    """Read the export. Refuses (ValueError) a file that is not one, and an
    SGD move that is not a whole number of cents."""
    lines = _lines(filepath)
    if len(lines) < 2 or lines[1].strip() != HEADER:
        raise ValueError("Independent Reserve export: the header line was not found; nothing was read.")
    activity = Activity(filename=os.path.basename(filepath))
    days = []
    for entry in csv.DictReader(io.StringIO("\n".join(lines[1:]))):
        on = _day(entry["Date"])
        days.append(on)
        kind = entry["Type"]
        if entry["Status"] != CONFIRMED:
            activity.left_out["not confirmed"] += 1
        elif entry["Currency"] != CURRENCY:
            activity.left_out["in another currency"] += 1
        elif kind not in MOVES:
            activity.left_out[f"{CURRENCY} {kind}"] += 1
        else:
            printed = entry["Credit"] or entry["Debit"]
            try:
                amount = money.parse_minor(printed, CURRENCY)
            except ValueError:
                raise ValueError(
                    f"Independent Reserve export: an SGD {MOVES[kind]} of {on} is not a whole "
                    "number of cents; nothing was read."
                ) from None
            description = " ".join(
                part for part in (f"{ACCOUNT} {CURRENCY} {MOVES[kind]}", entry["Comment"].strip()) if part
            )
            activity.moves.append(ParsedTransaction(
                date=on,
                description=description,
                amount_minor=-amount if entry["Credit"] else amount,
                card_info=ACCOUNT,
            ))
    # The export is newest first; the moves are handed over oldest first.
    activity.moves.reverse()
    if days:
        activity.first_date, activity.last_date = min(days), max(days)
    return activity


def _shown(day: str) -> str:
    d = datetime.strptime(day, "%Y-%m-%d")
    return f"{d.day} {d:%b %Y}"


def parse_independent_reserve_csv(filepath: str):
    """The upload's reading of the export: what it holds, and that nothing
    is imported from it (a holding takes no statement rows)."""
    activity = read_activity(filepath)
    deposits = sum(1 for tx in activity.moves if tx.amount_minor < 0)
    withdrawals = len(activity.moves) - deposits

    def counted(n: int, what: str) -> str:
        return f"{n} {CURRENCY} {what}{'' if n == 1 else 's'}"

    span = (
        f", {_shown(activity.first_date)} to {_shown(activity.last_date)}" if activity.first_date else ""
    )
    raise ValueError(
        f"{ACCOUNT} account activity{span}: {counted(deposits, 'deposit')} and "
        f"{counted(withdrawals, 'withdrawal')} read. {ACCOUNT} is a holding, and a holding "
        "takes no statement rows: its deposits and withdrawals are the bank rows that name it. "
        "Nothing was imported."
    )
