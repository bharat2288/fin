"""Conversion step: drop the float amounts from a database whose rows already
carry their amounts in whole minor units.

This is the contract half of the money change. Nothing reads a float amount
any more, so the float amount goes from every row and the two float
thresholds go from every rule. It changes no amount: the whole minor units
are what the minor-units step (convert_minor_units.py) left, and that step
must have run first, with nothing waiting for it. It runs only through the
conversion runner (conversion.run_step), so it sits behind a backup, in one
transaction.

The step fails, and is rolled back, unless the row count is unchanged and the
whole minor units of every account add up, after the floats are gone, to what
those floats added up to each rounded to its cent.

    python retire_float_amounts.py <path to database>
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import conversion
import convert_minor_units

# The float columns the step drops, each with the integer column that holds
# its value in whole minor units. Frozen, and the same declaration the
# minor-units step filled the integers from: table and column names in the
# statements below come from here and nowhere else.
#   table, float column, integer column
AMOUNT = convert_minor_units.AMOUNT
THRESHOLDS = convert_minor_units.THRESHOLDS
RETIRED = (AMOUNT, *THRESHOLDS)


class MinorUnitsNotApplied(Exception):
    """The minor-units step has not run, or rows have arrived since that it
    has not filled, or a row's whole minor units are not its float's.
    Dropping the floats now would lose those amounts."""


class PartlyRetired(Exception):
    """Some of the float columns are gone and others are not. No step leaves
    a database in that shape, so this one does not guess what happened."""


# --- reading the database ---------------------------------------------------


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _floats_left(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return [column for column in RETIRED if column[1] in _columns(conn, column[0])]


def _has_integers(conn: sqlite3.Connection) -> bool:
    return all(integer in _columns(conn, table) for table, _, integer in RETIRED)


# --- the step ---------------------------------------------------------------


def _is_applied(conn: sqlite3.Connection) -> bool:
    return not _floats_left(conn) and _has_integers(conn)


def _apply(conn: sqlite3.Connection) -> None:
    left = _floats_left(conn)
    if left and len(left) != len(RETIRED):
        raise PartlyRetired()
    # Every float must already be held as whole minor units. The minor-units
    # step reads as applied only then.
    if not _has_integers(conn) or not convert_minor_units.STEP.is_applied(conn):
        raise MinorUnitsNotApplied()
    for table, real, _ in left:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {real}")


def _measure(conn: sqlite3.Connection) -> dict:
    """Row counts, and for each account the total of its whole minor units.
    While the float amount is still there, also each account's floats each
    rounded to its cent. Every row is in exactly one account's totals; a row
    on no account is under the key None. And the rule thresholds, in whole
    minor units."""
    has_integers = _has_integers(conn)
    minor: dict = {}
    without_minor = 0
    if has_integers:
        for account_id, integer in conn.execute(
            "SELECT s.account_id, t.amount_minor FROM transactions t"
            " LEFT JOIN statements s ON s.id = t.statement_id"
        ).fetchall():
            if integer is None:
                without_minor += 1
            else:
                minor[account_id] = minor.get(account_id, 0) + integer
    else:
        without_minor = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    measured = {
        "rows": conversion.count_rows(conn),
        "account_totals_minor": minor,
        "rows_without_minor": without_minor,
        "rule_thresholds": {},
    }
    if AMOUNT[1] in _columns(conn, AMOUNT[0]):
        measured["account_totals_cents"] = conversion.measure(conn)["account_totals_cents"]
    if has_integers:
        for table, _, integer in THRESHOLDS:
            measured["rule_thresholds"][integer] = dict(zip(("set", "cents"), conn.execute(
                f"SELECT COUNT({integer}), COALESCE(SUM({integer}), 0) FROM {table}"
            ).fetchone()))
    return measured


def _whose(account_id) -> str:
    return "rows on no account" if account_id is None else f"account {account_id}"


def _invariant(before: dict, after: dict) -> list[str]:
    """The row count is unchanged, every row has its amount, and every
    account's whole minor units add up, after the floats are gone, to what
    they did before and to what the floats did each rounded to its cent."""
    problems = []
    for table in sorted(set(before["rows"]) | set(after["rows"])):
        was, now = before["rows"].get(table), after["rows"].get(table)
        if was != now:
            problems.append(f"row count of {table} changed: {was} -> {now}")

    floats = before.get("account_totals_cents")
    was_minor, now_minor = before["account_totals_minor"], after["account_totals_minor"]
    accounts = set(was_minor) | set(now_minor) | set(floats or {})
    # The key None holds rows that belong to no account; it sorts last.
    for account_id in sorted(accounts, key=lambda a: (a is None, a or 0)):
        now = now_minor.get(account_id)
        if now != was_minor.get(account_id):
            problems.append(
                f"integer total of {_whose(account_id)} changed: "
                f"{was_minor.get(account_id)} -> {now} cents"
            )
        if floats is not None and now != floats.get(account_id):
            problems.append(
                f"integer total of {_whose(account_id)} is {now} cents; "
                f"its float amounts added up to {floats.get(account_id)}"
            )
    if after["rows_without_minor"]:
        n = after["rows_without_minor"]
        problems.append(f"{n} row{'' if n == 1 else 's'} left without an integer amount")

    for _, _, integer in THRESHOLDS:
        was, now = before["rule_thresholds"].get(integer), after["rule_thresholds"].get(integer)
        if was != now:
            problems.append(f"rule threshold {integer} changed: {was} -> {now}")
    return problems


STEP = conversion.Step(
    name="retire-float-amounts",
    apply=_apply,
    is_applied=_is_applied,
    invariant=_invariant,
    measure=_measure,
)


# --- the command ---------------------------------------------------------------


def _print_totals(heading: str, totals: dict) -> None:
    print(heading)
    for account_id in sorted(totals, key=lambda a: (a is None, a or 0)):
        print(f"  {_whose(account_id)}: {totals[account_id]}")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python retire_float_amounts.py <path to database>")
        return 2
    path = Path(argv[0]).resolve()
    if not path.is_file():
        print(f"no database at {path}")
        return 1

    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        ready = _is_applied(conn) or convert_minor_units.STEP.is_applied(conn)
    finally:
        conn.close()
    if not ready:
        print(
            "the minor-units step has not run on this database, or rows have arrived "
            "since that it has not filled. Run it first:\n"
            f"  python convert_minor_units.py {argv[0]}"
        )
        return 1

    try:
        report = conversion.run_step(path, STEP)
    except (conversion.ConversionRefused, conversion.ConversionFailed) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print(f"step {report['step']}: {report['status']}")
    if report["backup"]:
        print(f"backup: {report['backup']}")
        print(f"rows before: {report['before']['rows']}")
        print(f"rows after:  {report['after']['rows']}")
        _print_totals(
            "float amounts, each rounded to its cent, per account (cents), before:",
            report["before"].get("account_totals_cents", {}),
        )
        _print_totals(
            "integer amounts per account (cents), after:", report["after"]["account_totals_minor"]
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
