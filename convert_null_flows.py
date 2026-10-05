"""Conversion step: give a flow to every row that has none.

A row with no flow (from before flows were classified, or saved without one)
was given one by the app on start, with no backup and without the facts of
the account it is on (S6). This step does that work instead, behind the
runner's backup and in one transaction: each such row is classified by the
same classifier the import uses (flow.classify_row), with its account's kind
and owner and the merchant a rule gave it, and takes the flow and the other
side that gives. A row's flow is not marked as set by hand. Nothing else about a row changes, and no row that has a
flow is touched. It runs last in the chain (conversion.CHAIN), after the
accounts have their kinds and owners.

    python convert_null_flows.py <path to database>
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

import conversion
import flow


def waiting(conn: sqlite3.Connection) -> int:
    """How many rows have no flow."""
    return conn.execute("SELECT COUNT(*) FROM transactions WHERE flow_type IS NULL").fetchone()[0]


def planned(conn: sqlite3.Connection) -> list[tuple[int, str, int | None]]:
    """(row id, flow, other side) for each row with no flow."""
    conn.row_factory = sqlite3.Row
    ctx = flow.build_context(conn)
    rows = conn.execute(
        "SELECT t.id, t.description, t.amount_minor, t.service_id, t.type_id, "
        "a.type AS account_kind, a.owner AS account_owner "
        "FROM transactions t LEFT JOIN statements s ON s.id = t.statement_id "
        "LEFT JOIN accounts a ON a.id = s.account_id "
        "WHERE t.flow_type IS NULL ORDER BY t.id"
    ).fetchall()
    out = []
    for r in rows:
        name, other_side = flow.classify_row(
            {
                "description": r["description"],
                "amount_minor": r["amount_minor"],
                "account_kind": r["account_kind"],
                "account_owner": r["account_owner"],
                "service_id": r["service_id"],
                "labelled": r["service_id"] is not None or r["type_id"] is not None,
            },
            ctx,
        )
        out.append((r["id"], flow.checked_flow(name), other_side))
    return out


def _is_applied(conn: sqlite3.Connection) -> bool:
    return waiting(conn) == 0


def _apply(conn: sqlite3.Connection) -> None:
    # A row on no account (its statement is gone) is classified from its
    # wording alone, as an import with no account facts would be.
    for row_id, name, other_side in planned(conn):
        conn.execute(
            "UPDATE transactions SET flow_type = ?, other_side_id = ? WHERE id = ? AND flow_type IS NULL",
            (name, other_side, row_id),
        )


STEP = conversion.Step(name="flows", apply=_apply, is_applied=_is_applied)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_null_flows.py <path to database>")
        return 2
    path = Path(argv[0]).resolve()
    if not path.is_file():
        print(f"no database at {path}")
        return 1

    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        tally = Counter(name for _, name, _ in planned(conn)) if _has_shape(conn) else Counter()
    finally:
        conn.close()

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
        for name, count in sorted(tally.items()):
            print(f"no flow -> {name}: {count}")
    return 0


def _has_shape(conn: sqlite3.Connection) -> bool:
    """Whether the accounts have owners, so a row can be classified."""
    return "owner" in {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
