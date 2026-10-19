"""Conversion step: retire the single mark (contract).

Personal-ledger 24 and 51 (ADR-008): a row's book and its type each say where
they came from (book_source, type_source; label_source). The one mark a row
carried before them (label_source.RETIRED_MARK) was kept and written in step
while the readers moved to the two sources (expand, convert_label_source.py).
Nothing reads it now, so this step drops it from an existing book, with the
transactions history triggers that named it.

While the mark was there, a field with no source read the mark: a row named
by hand on the review list, with no book and no type, read as chosen, and the
rules never took it. Once the mark is gone a field with no source reads as
from the merchant (label_source.NO_SOURCE). So before the drop the step
writes into each field with no source the reading the mark gave it, where
that is not already from the merchant, and into each field with a value and
no source the reading whatever it is. Every field then reads the same source
after the drop as before; a field that is empty and read from the merchant
stays without a source.

It changes no book, no type and no amount. It runs after the label-source
step (conversion.CHAIN), only through the conversion runner, so it sits
behind a backup, in one transaction. It records that it ran
(conversion.record_applied), in that transaction, and reads the record; a
book made new in schema.sql's shape, which never held the mark, reads as
retired by its shape, which nothing the operator does can change.

    python retire_single_mark.py <path to database>
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import conversion
import convert_label_source
import history
import label_source

NAME = "retire-single-mark"
MARK = label_source.RETIRED_MARK
# The column as the books before this step declare it. A step before this one
# that rebuilds the table from schema.sql keeps it (conversion.retired_after),
# for this step to drop.
RETIRES_COLUMNS = {"transactions": ((MARK, "TEXT DEFAULT 'auto'"),)}

# What a field with no source read as while the mark was there (label_source
# in the expand step); any other mark, and none, read from the merchant.
EXPAND_READING = {
    "manual": label_source.CHOSEN,
    "derived": label_source.DERIVED,
    "rule_override": label_source.RULE,
    "fallback": label_source.WORDING,
}
_NO_MARK = "auto"

_TRIGGER_NAME = re.compile(r"CREATE TRIGGER IF NOT EXISTS (history_transactions_(?:insert|update|delete)) ")


class LabelSourceNotApplied(Exception):
    """The book has no source columns: dropping the mark now would lose every
    label's provenance."""


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}


def has_mark(conn: sqlite3.Connection) -> bool:
    """Whether the book still holds the mark."""
    return MARK in _columns(conn)


def _reading(source: str | None, mark: str | None) -> str:
    """A field's source as fin read it while the book held the mark."""
    if source is not None:
        return source
    return EXPAND_READING.get(mark or _NO_MARK, label_source.NO_SOURCE)


def planned(conn: sqlite3.Connection) -> list[tuple[str | None, str | None, int]]:
    """(book source, type source, row id) for every row with a field whose
    source the mark alone gives: a field with a value and no source, or an
    empty one that read other than from the merchant."""
    out = []
    for row in conn.execute(
        f"SELECT id, book, type_id, book_source, type_source, {MARK} AS mark FROM transactions"
        " WHERE book_source IS NULL OR type_source IS NULL ORDER BY id"
    ).fetchall():
        sources, wanted = [], False
        for field, column in label_source.FIELDS.items():
            held, reading = row[column], _reading(None, row["mark"])
            if held is None and (row[field] is not None or reading != label_source.NO_SOURCE):
                sources.append(reading)
                wanted = True
            else:
                sources.append(held)
        if wanted:
            out.append((*sources, row["id"]))
    return out


def _apply(conn: sqlite3.Connection) -> None:
    if not convert_label_source.has_columns(conn):
        raise LabelSourceNotApplied()
    if has_mark(conn):
        conn.executemany("UPDATE transactions SET book_source = ?, type_source = ? WHERE id = ?", planned(conn))
        # SQLite will not drop a column a trigger names: the history triggers
        # go first, and schema.sql's, which do not name it, take their place.
        triggers = history.table_triggers("transactions")
        for statement in triggers:
            conn.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME.match(statement).group(1)}")
        conn.execute(f"ALTER TABLE transactions DROP COLUMN {MARK}")
        for statement in triggers:
            conn.execute(statement)
    # In the runner's transaction, with the drop: committed or rolled back
    # with it.
    conversion.record_applied(conn, NAME)


def _is_applied(conn: sqlite3.Connection) -> bool:
    """Applied once the book records the step ran; before that, only a book
    whose shape never held the mark (made new in schema.sql's shape, with
    the source columns), never from what its rows hold."""
    if conversion.recorded(conn, NAME):
        return True
    return not has_mark(conn) and convert_label_source.has_columns(conn)


def _measure(conn: sqlite3.Connection) -> dict:
    """The runner's measure, and each row's book, type and the source each
    field reads as (_reading while the book holds the mark, label_source
    after it)."""
    marked = has_mark(conn)
    mark = MARK if marked else "NULL"
    readings = {}
    for r in conn.execute(
        f"SELECT id, book, type_id, book_source, type_source, {mark} AS mark FROM transactions"
    ):
        if marked:
            book_reads, type_reads = _reading(r["book_source"], r["mark"]), _reading(r["type_source"], r["mark"])
        else:
            book_reads, type_reads = label_source.source_of(r["book_source"]), label_source.source_of(r["type_source"])
        readings[r["id"]] = (r["book"], r["type_id"], book_reads, type_reads)
    return {
        **conversion.measure(conn),
        "readings": readings,
        "has_mark": marked,
        "unsourced": convert_label_source.unsourced(conn),
        "sources": {
            column: dict(Counter(r[0] for r in conn.execute(
                f"SELECT {column} FROM transactions WHERE {column} IS NOT NULL")))
            for column in convert_label_source.SOURCE_COLUMNS
        },
    }


def _invariant(before: dict, after: dict) -> list[str]:
    problems = conversion.unchanged(before, after)
    moved = sum(1 for i, read in before["readings"].items() if after["readings"].get(i) != read)
    if moved:
        problems.append(f"{moved} rows changed their book, their type or the source a field reads as")
    if after["unsourced"]:
        problems.append(f"{after['unsourced']} rows with a book or a type have no source for it")
    if after["has_mark"]:
        problems.append("the mark is still there")
    return problems


STEP = conversion.Step(name=NAME, apply=_apply, is_applied=_is_applied, invariant=_invariant, measure=_measure)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python retire_single_mark.py <path to database>")
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
        # Counts only: a name, a label or an amount is never printed.
        print(f"backup: {report['backup']}")
        print(f"rows before: {report['before']['rows']}")
        print(f"rows after:  {report['after']['rows']}")
        for column, field in zip(convert_label_source.SOURCE_COLUMNS, ("book", "type")):
            was, now = report["before"]["sources"][column], report["after"]["sources"][column]
            given = {name: now.get(name, 0) - was.get(name, 0) for name in now}
            shown = ", ".join(f"{name} {n}" for name, n in sorted(given.items()) if n)
            print(f"{field} sources given from the mark: {shown or 'none'}")
        print("no book, type or amount was changed; the single mark is dropped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
