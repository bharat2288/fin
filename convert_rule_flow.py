"""Conversion step: a merchant rule may state a flow and an other side (expand).

fin-redesign 07 (ADR-009): a rule may say what a row is as well as label it.
This step gives an existing database the two columns merchant_rules gains, in
the shape schema.sql declares (`flow` and `other_side_id`, both empty), and
puts the merchant_rules history triggers in place with them, so a rule's flow
is recorded and an Undo puts it back. Every rule it finds states no flow, so
it labels only, as before: no row, no rule and no figure changes.

It runs after retire-single-mark (conversion.CHAIN), and only through the
conversion runner, so it sits behind a backup, in one transaction, and a
second run changes nothing: the columns are there.

    python convert_rule_flow.py <path to database>
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import conversion
import history

NAME = "rule-flow"

# As schema.sql declares them.
_COLUMNS = (
    ("flow", "flow TEXT"),
    ("other_side_id", "other_side_id INTEGER REFERENCES accounts(id)"),
)
# Read by the steps before this one: a book reaching them has neither column
# yet, so a rebuild or a trigger made from schema.sql leaves them out until
# this step adds them.
ADDS_COLUMNS = {"merchant_rules": tuple(name for name, _ in _COLUMNS)}

_TRIGGER_NAME = re.compile(r"CREATE TRIGGER IF NOT EXISTS (history_merchant_rules_(?:insert|update|delete)) ")


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(merchant_rules)")}


def has_shape(conn: sqlite3.Connection) -> bool:
    """Whether a rule can state a flow and an other side."""
    return {name for name, _ in _COLUMNS} <= _columns(conn)


def _apply(conn: sqlite3.Connection) -> None:
    held = _columns(conn)
    for name, declaration in _COLUMNS:
        if name not in held:
            conn.execute(f"ALTER TABLE merchant_rules ADD COLUMN {declaration}")
    # The history records every column: the triggers made without the two
    # are replaced by schema.sql's.
    for statement in history.table_triggers("merchant_rules"):
        conn.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME.match(statement).group(1)}")
        conn.execute(statement)


def _measure(conn: sqlite3.Connection) -> dict:
    return {
        **conversion.measure(conn),
        "rules": [tuple(r) for r in conn.execute(
            "SELECT id, pattern, service_id, match_type, priority, book_override, type_override_id,"
            " min_amount_minor, max_amount_minor FROM merchant_rules ORDER BY id")],
        "flows": [tuple(r) for r in conn.execute(
            "SELECT id, flow_type, other_side_id FROM transactions ORDER BY id")],
        "stating": conn.execute("SELECT COUNT(*) FROM merchant_rules WHERE flow IS NOT NULL").fetchone()[0]
        if has_shape(conn) else 0,
    }


def _invariant(before: dict, after: dict) -> list[str]:
    problems = conversion.unchanged(before, after)
    if before["rules"] != after["rules"]:
        problems.append("a merchant rule changed")
    if before["flows"] != after["flows"]:
        problems.append("a row's flow or other side changed")
    if after["stating"]:
        problems.append(f"{after['stating']} rules state a flow")
    return problems


STEP = conversion.Step(name=NAME, apply=_apply, is_applied=has_shape, invariant=_invariant, measure=_measure)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_rule_flow.py <path to database>")
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
        print(f"backup: {report['backup']}")
        print(f"rows before: {report['before']['rows']}")
        print(f"rows after:  {report['after']['rows']}")
        print("every rule states no flow and labels only, as before; no row was changed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
