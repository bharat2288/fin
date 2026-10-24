"""Conversion step: the hard-coded flow lists become rules (fin-redesign 08).

ADR-009, RUL-010. The classifier held two lists the operator could not see or
change: the household's own-account names, where a row naming one was a
transfer, and the loan merchants, where a row of the merchant named exactly
"UOB Home Loan" or "Car Loan" repaid its loan. A new book gets them as rules
at its first start; this step gives them to a book from before, through the
same seeding (db.seed_flow_rules) and its marker (db.FLOW_RULES_SEED):

- each own-account name becomes a rule stating its rows are a transfer
  (db.OWN_ACCOUNT_RULES); where the operator has a rule of the name, in any
  case, none is added: the transfer is stated on it where it states no flow
  and is for the same money, and otherwise it is left and named;
  Maya, Shalini and Rahul Kale and the "RK" short forms are not the
  household (RUL-010) and are not among them;
- each rule of a loan merchant (the name, or the name going on past a space
  or a bracket: db.loan_merchants), where the book holds the merchant and
  its loan, states that its rows repay the loan (db.LOAN_MERCHANTS);
- the default rules of six bills the household only pays state that their
  rows are spending, for money out (db.SPENDING_PAYEES), where each states
  no flow yet.

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


def _gained_a_flow(held: tuple, now: tuple) -> bool:
    """Whether a rule (pattern, merchant, match, priority, book, type, least,
    most, flow, other side) changed only by gaining a flow where it stated
    none, and, for a spending payee's, money out only where it had no least
    amount. Nothing else about a rule moves."""
    if held[-2:] != (None, None) or now[:-4] != held[:-4] or now[-3] != held[-3]:
        return False
    return now[-4] == held[-4] or (held[-4] is None and now[-4] == db.MONEY_OUT and now[-2] == "expense")


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
        if now is None:
            problems.append(f"rule {rule_id} changed")
        elif now != held and not _gained_a_flow(held, now):
            problems.append(f"rule {rule_id} changed")
    added = set(after["rules"]) - set(before["rules"])
    if any(after["rules"][rule_id][-2] is None for rule_id in added):
        problems.append("a rule was added that states no flow")
    return problems


STEP = conversion.Step(name=NAME, apply=_apply, is_applied=db.flow_rules_seeded,
                       invariant=_invariant, measure=_measure)


def _left(rules) -> list[str]:
    """The own-account names (db.OWN_ACCOUNT_RULES, the seed's own words)
    whose rule the book holds but which does not state the transfer: the
    operator's rule of the name, left as it is (db._seed_rule)."""
    held = [(r[0].upper(), r[-2]) for r in rules]
    return [pattern for pattern in db.OWN_ACCOUNT_RULES_PATTERNS
            if any(p == pattern for p, _ in held) and not any(p == pattern and f == "transfer" for p, f in held)]


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
        loans = [r for i, r in after["rules"].items() if r[-2] == "movement" and before["rules"].get(i, r)[-2] is None]
        print(f"loan merchants found: {len({r[1] for r in loans})}, their rules stating the loan: {len(loans)}")
        for pattern in _left(after["rules"].values()):
            print(f"left as the operator has it: {pattern} (its own rule of the name states another flow, "
                  "or none for other money)")
        print("no row was changed: the rules say what new rows are")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
