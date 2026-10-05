"""Conversion step: statement records that are statements as printed.

Gives an existing database what card balances by statement need (ruling 1):
the statement records gain `printed`, in the shape schema.sql declares. A
printed record is one statement as the bank printed it: dated by its closing
day, holding exactly the rows it printed, its closing balance the anchor on
that day. Every record an existing database holds is a calendar-month record
(the import filed rows by their own month, dated the 1st), so every one is
marked not printed; no row is moved and no amount is touched.

Which statement an existing row printed on was never stored, so this step
cannot re-file rows: importing a statement again files its rows under it
(the confirm moves a row it already holds in a month record onto the printed
record), and until then a row on a month record counts by its own date, as
before. It runs only through the conversion runner (conversion.run_step), so
it sits behind a backup, in one transaction, and a second run changes nothing.

    python convert_printed_statements.py <path to database>
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import conversion

# As schema.sql declares the column.
_PRINTED_COLUMN = "printed INTEGER NOT NULL DEFAULT 0"


def has_shape(conn: sqlite3.Connection) -> bool:
    """Whether a statement record can say it is a statement as printed."""
    return "printed" in {r[1] for r in conn.execute("PRAGMA table_info(statements)")}


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute(f"ALTER TABLE statements ADD COLUMN {_PRINTED_COLUMN}")


STEP = conversion.Step(name="printed-statements", apply=_apply, is_applied=has_shape)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_printed_statements.py <path to database>")
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
        print(f"backup: {report['backup']}")
        print(f"rows before: {report['before']['rows']}")
        print(f"rows after:  {report['after']['rows']}")
        print("every statement record is marked as a month record; import a statement "
              "again to file its rows under it")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
