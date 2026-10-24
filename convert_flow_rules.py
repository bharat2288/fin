"""Conversion step: the hard-coded flow lists become rules (fin-redesign 08).

ADR-009, RUL-010. The classifier held two lists the operator could not see or
change: the household's own-account names, where a row naming one was a
transfer, and the loan merchants, where a row of the merchant named exactly
"UOB Home Loan" or "Car Loan" repaid its loan. A new book gets them as rules
at its first start; this step gives them to a book from before, through the
same seeding (db.seed_flow_rules) and its marker (db.FLOW_RULES_SEED):

- each own-account name becomes a rule stating its rows are a transfer
  (db.OWN_ACCOUNT_RULES), unless a rule with its pattern is already there;
  Maya, Shalini and Rahul Kale and the "RK" short forms are not the
  household (RUL-010) and are not among them;
- each rule of a loan merchant, where the book holds the merchant and its
  loan, states that its rows repay the loan (db.LOAN_MERCHANTS);
- the default rules of six bills the household only pays state that their
  rows are spending (db.SPENDING_PAYEES), where each states no flow yet.

No row changes: a rule says what a new row is, and is applied to past rows
only when the operator applies it. It runs after rule-flow
(conversion.CHAIN), only through the conversion runner, so it sits behind a
backup, in one transaction, and a second run changes nothing: the marker is
there. A rule the operator deletes afterwards stays deleted.

    python convert_flow_rules.py <path to database>
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import conversion
import db

NAME = "flow-rules"

# Grown by this step: a rule and the merchant it is on, for each own-account
# name.
_GROWN = ("merchant_rules", "services")
# The marker's table (schema.sql declares it; its one home): a book from
# before the default rules were seeded once has none yet.
_SEEDED_ONCE = re.compile(r"CREATE TABLE IF NOT EXISTS seeded_once \([^;]*\);")


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute(_SEEDED_ONCE.search(db.SCHEMA_PATH.read_text(encoding="utf-8")).group(0))
    db.seed_flow_rules(conn)


def _measure(conn: sqlite3.Connection) -> dict:
    return {
        **conversion.measure(conn),
        "transactions": [tuple(r) for r in conn.execute("SELECT * FROM transactions ORDER BY id")],
        "rules": {r[0]: tuple(r[1:]) for r in conn.execute(
            "SELECT id, pattern, service_id, match_type, priority, book_override, type_override_id,"
            " min_amount_minor, max_amount_minor, flow, other_side_id FROM merchant_rules")},
        "services": [tuple(r) for r in conn.execute("SELECT * FROM services ORDER BY id")],
    }


def _invariant(before: dict, after: dict) -> list[str]:
    problems = []
    for table, count in before["rows"].items():
        if after["rows"].get(table) != count and table not in _GROWN:
            problems.append(f"{table} went from {count} rows to {after['rows'].get(table)}")
    if before["account_totals_cents"] != after["account_totals_cents"]:
        problems.append("an account's total changed")
    if before["transactions"] != after["transactions"]:
        problems.append("a row changed")
    if not set(before["services"]) <= set(after["services"]):
        problems.append("a merchant changed")
    for rule_id, held in before["rules"].items():
        now = after["rules"].get(rule_id)
        # Only a rule that stated no flow may gain one; nothing else about it moves.
        if now is None or now[:-2] != held[:-2] or (held[-2:] != now[-2:] and held[-2:] != (None, None)):
            problems.append(f"rule {rule_id} changed")
    added = set(after["rules"]) - set(before["rules"])
    if any(after["rules"][rule_id][-2] is None for rule_id in added):
        problems.append("a rule was added that states no flow")
    return problems


STEP = conversion.Step(name=NAME, apply=_apply, is_applied=db.flow_rules_seeded,
                       invariant=_invariant, measure=_measure)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_flow_rules.py <path to database>")
        return 2
    path = Path(argv[0]).resolve()
    if not path.is_file():
        print(f"no database at {path}")
        return 1

    try:
        report = conversion.run_step(path, STEP)
    except (conversion.ConversionRefused, conversion.ConversionFailed) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print(f"step {report['step']}: {report['status']}")
    if report["backup"]:
        # Counts only: a name, a wording or an amount is never printed.
        before, after = report["before"], report["after"]
        stating = sum(1 for r in after["rules"].values() if r[-2]) - sum(1 for r in before["rules"].values() if r[-2])
        print(f"backup: {report['backup']}")
        print(f"rules before: {before['rows']['merchant_rules']}, after: {after['rows']['merchant_rules']}")
        print(f"rules that state a flow: {stating} more")
        print("no row was changed: the rules say what new rows are")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
