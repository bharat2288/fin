"""Conversion step: give every row its amount in whole minor units beside the
float it still carries.

This is the expand half of the money change. It adds an integer amount to
each row (`amount_minor`: cents, for an SGD account) and to each rule's amount
thresholds, fills them from the floats, and turns the foreign-currency names
on card rows into standard three-letter codes. It removes nothing and changes
no float: nothing reads the new amounts yet, and the float stays the amount
the app shows until the step that drops it. It runs only through the
conversion runner (conversion.run_step).

The step fails, and is rolled back, unless the row count is unchanged and the
new integer amounts of every account add up to its old amounts each rounded to
its cent.

Nothing writes the integer amount on a new row yet, so rows imported after the
step have none. Run the step again: it reads as not applied and fills them.

    python convert_minor_units.py <path to database>
"""

from __future__ import annotations

import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import conversion
import money

# The old amounts are SGD: their column says so.
OLD_CURRENCY = "SGD"

# The columns the step adds. Frozen: table and column names in the statements
# below come from here and nowhere else.
#   table, float column, integer column
AMOUNT = ("transactions", "amount_sgd", "amount_minor")
THRESHOLDS = (
    ("merchant_rules", "min_amount", "min_amount_minor"),
    ("merchant_rules", "max_amount", "max_amount_minor"),
)


class AccountNotInSgd(Exception):
    """An account with rows names a currency other than SGD. Its float amounts
    are SGD by their column, so its minor units cannot be told; the runner
    rolls the step back."""


# --- reading the database ---------------------------------------------------


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _has_shape(conn: sqlite3.Connection) -> bool:
    return all(
        integer in _columns(conn, table) for table, _, integer in (AMOUNT, *THRESHOLDS)
    )


def _float_amount_retired(conn: sqlite3.Connection) -> bool:
    """Whether the float amount is gone (the step that drops it has run, or
    the database was created after it): there is nothing left to convert."""
    return AMOUNT[1] not in _columns(conn, AMOUNT[0])


def _add_shape(conn: sqlite3.Connection) -> None:
    for table, _, integer in (AMOUNT, *THRESHOLDS):
        if integer not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {integer} INTEGER")


def _amounts_to_write(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    """(whole cents, row id) for each row whose integer amount is missing or
    is not its float's cents."""
    out = []
    for row_id, amount, minor, has_account, currency in conn.execute(
        "SELECT t.id, t.amount_sgd, t.amount_minor, a.id IS NOT NULL, a.currency"
        " FROM transactions t"
        " LEFT JOIN statements s ON s.id = t.statement_id"
        " LEFT JOIN accounts a ON a.id = s.account_id"
    ).fetchall():
        # An account with no currency recorded reads as SGD, the column's default.
        if has_account and (currency or OLD_CURRENCY).strip().upper() != OLD_CURRENCY:
            raise AccountNotInSgd()
        want = money.to_minor(amount, OLD_CURRENCY)
        if minor != want:
            out.append((want, row_id))
    return out


def _thresholds_to_write(conn: sqlite3.Connection, column: tuple) -> list[tuple[int | None, int]]:
    table, real, integer = column
    out = []
    for row_id, value, minor in conn.execute(
        f"SELECT id, {real}, {integer} FROM {table}"
    ).fetchall():
        want = None if value is None else money.to_minor(value, OLD_CURRENCY)
        if minor != want:
            out.append((want, row_id))
    return out


def _currency_names_to_write(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(code, name as stored) for each stored foreign-currency name that is a
    known currency and is not already its code."""
    out = []
    for (name,) in conn.execute(
        "SELECT DISTINCT currency_foreign FROM transactions WHERE currency_foreign IS NOT NULL"
    ).fetchall():
        code = money.currency_code(name)
        if code and code != name:
            out.append((code, name))
    return out


# --- the step ---------------------------------------------------------------


def _is_applied(conn: sqlite3.Connection) -> bool:
    if _float_amount_retired(conn):
        return True
    if not _has_shape(conn):
        return False
    try:
        pending = _amounts_to_write(conn)
    except AccountNotInSgd:
        return False  # the step is run, and stops on it by name
    return not (
        pending
        or any(_thresholds_to_write(conn, column) for column in THRESHOLDS)
        or _currency_names_to_write(conn)
    )


def _apply(conn: sqlite3.Connection) -> None:
    _add_shape(conn)
    # The float is the amount of record until it is dropped, so an integer
    # that disagrees with it is rewritten, not kept.
    for want, row_id in _amounts_to_write(conn):
        conn.execute("UPDATE transactions SET amount_minor = ? WHERE id = ?", (want, row_id))
    for column in THRESHOLDS:
        table, _, integer = column
        for want, row_id in _thresholds_to_write(conn, column):
            conn.execute(f"UPDATE {table} SET {integer} = ? WHERE id = ?", (want, row_id))
    for code, name in _currency_names_to_write(conn):
        conn.execute(
            "UPDATE transactions SET currency_foreign = ? WHERE currency_foreign = ?", (code, name)
        )


def _measure(conn: sqlite3.Connection) -> dict:
    """Row counts, and for each account both totals: its float amounts each
    rounded to its cent, and its integer amounts. Every row is in exactly one
    account's totals; a row on no account is under the key None.

    Also what the step must leave alone or carry over: the foreign amounts,
    and the rule thresholds in both forms.
    """
    has_shape = _has_shape(conn)
    cents: dict = {}
    minor: dict = {}
    without_minor = 0
    foreign_rows, foreign_total = 0, Decimal(0)
    for account_id, amount, integer, foreign in conn.execute(
        "SELECT s.account_id, t.amount_sgd, "
        + ("t.amount_minor" if has_shape else "NULL")
        + ", t.amount_foreign FROM transactions t"
        " LEFT JOIN statements s ON s.id = t.statement_id"
    ).fetchall():
        cents[account_id] = cents.get(account_id, 0) + money.to_minor(amount, OLD_CURRENCY)
        if integer is None:
            without_minor += 1
        else:
            minor[account_id] = minor.get(account_id, 0) + integer
        if foreign is not None:
            foreign_rows += 1
            foreign_total += Decimal(repr(float(foreign)))

    thresholds = {}
    for table, real, integer in THRESHOLDS:
        floats = conn.execute(f"SELECT id, {real} FROM {table} WHERE {real} IS NOT NULL").fetchall()
        thresholds[real] = {
            "set": len(floats),
            "cents": sum(money.to_minor(value, OLD_CURRENCY) for _, value in floats),
        }
        if has_shape:
            thresholds[integer] = dict(zip(("set", "cents"), conn.execute(
                f"SELECT COUNT({integer}), COALESCE(SUM({integer}), 0) FROM {table}"
            ).fetchone()))

    return {
        "rows": conversion.count_rows(conn),
        "account_totals_cents": cents,
        "account_totals_minor": minor,
        "rows_without_minor": without_minor,
        "foreign_amounts": {"rows": foreign_rows, "total": str(foreign_total)},
        "rule_thresholds": thresholds,
    }


def _whose(account_id) -> str:
    return "rows on no account" if account_id is None else f"account {account_id}"


def _invariant(before: dict, after: dict) -> list[str]:
    """The row count is unchanged, no float amount moved, and every account's
    integer amounts add up to its old amounts each rounded to its cent."""
    problems = []
    for table in sorted(set(before["rows"]) | set(after["rows"])):
        was, now = before["rows"].get(table), after["rows"].get(table)
        if was != now:
            problems.append(f"row count of {table} changed: {was} -> {now}")

    old = before["account_totals_cents"]
    kept, new = after["account_totals_cents"], after["account_totals_minor"]
    # The key None holds rows that belong to no account; it sorts last.
    for account_id in sorted(set(old) | set(kept) | set(new), key=lambda a: (a is None, a or 0)):
        want = old.get(account_id)
        if kept.get(account_id) != want:
            problems.append(
                f"float total of {_whose(account_id)} changed: "
                f"{want} -> {kept.get(account_id)} cents"
            )
        if new.get(account_id) != want:
            problems.append(
                f"integer total of {_whose(account_id)} is {new.get(account_id)} cents; "
                f"its old amounts add up to {want}"
            )
    if after["rows_without_minor"]:
        n = after["rows_without_minor"]
        problems.append(f"{n} row{'' if n == 1 else 's'} left without an integer amount")

    if after["foreign_amounts"] != before["foreign_amounts"]:
        problems.append(
            f"foreign amounts changed: {before['foreign_amounts']} -> {after['foreign_amounts']}"
        )
    for _, real, integer in THRESHOLDS:
        want = before["rule_thresholds"][real]
        if after["rule_thresholds"][real] != want:
            problems.append(f"rule threshold {real} changed")
        if after["rule_thresholds"].get(integer) != want:
            problems.append(
                f"rule threshold {integer} is {after['rule_thresholds'].get(integer)}; "
                f"{real} gives {want}"
            )
    return problems


STEP = conversion.Step(
    name="minor-units",
    apply=_apply,
    is_applied=_is_applied,
    invariant=_invariant,
    measure=_measure,
)


# --- what the conversion did --------------------------------------------------


def summary(conn: sqlite3.Connection) -> dict:
    """What the operator should look at after the step: how many amounts were
    not a whole number of cents (and so were rounded), and the foreign-currency
    names left as they were because the step does not know them."""
    rounded = 0
    if not _float_amount_retired(conn):
        rounded = sum(
            not money.is_whole_minor(amount, OLD_CURRENCY)
            for (amount,) in conn.execute("SELECT amount_sgd FROM transactions")
        )
    unknown = sorted(
        name
        for (name,) in conn.execute(
            "SELECT DISTINCT currency_foreign FROM transactions WHERE currency_foreign IS NOT NULL"
        )
        if money.currency_code(name) is None
    )
    return {"rows_rounded": rounded, "unknown_currency_names": unknown}


def _print_totals(heading: str, totals: dict) -> None:
    print(heading)
    for account_id in sorted(totals, key=lambda a: (a is None, a or 0)):
        print(f"  {_whose(account_id)}: {totals[account_id]}")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_minor_units.py <path to database>")
        return 2
    try:
        report = conversion.run_step(argv[0], STEP)
    except (conversion.ConversionRefused, conversion.ConversionFailed) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print(f"step {report['step']}: {report['status']}")
    if report["backup"]:
        print(f"backup: {report['backup']}")
        print(f"rows before: {report['before']['rows']}")
        print(f"rows after:  {report['after']['rows']}")
        _print_totals(
            "old amounts, each rounded to its cent, per account (cents):",
            report["before"]["account_totals_cents"],
        )
        _print_totals(
            "integer amounts per account (cents):", report["after"]["account_totals_minor"]
        )

    conn = sqlite3.connect(f"{Path(argv[0]).resolve().as_uri()}?mode=ro", uri=True)
    try:
        found = summary(conn)
    finally:
        conn.close()
    n = found["rows_rounded"]
    print(
        f"{n} row{' was' if n == 1 else 's were'} not a whole number of cents "
        "and rounded half-even"
    )
    if found["unknown_currency_names"]:
        print("foreign-currency names left as they are (not known to money.py):")
        for name in found["unknown_currency_names"]:
            print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
