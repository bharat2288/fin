"""Conversion step: a source for a row's book and for its type (expand).

Personal-ledger 24 (ADR-008): a row's book and its type each say where they
came from, one of book_type.SOURCES, or nothing when the field is empty. This
step gives an existing database the two columns, in the shape schema.sql
declares, puts the transactions history triggers in place with both, and
fills them from the one mark a row carries today (`cat_source`):

- `auto` and `service_default` -> from the merchant;
- `rule_override` -> from the rule for the field the rule sets, from the
  merchant for the other;
- `fallback` -> from the wording;
- `derived` -> worked out;
- `manual` -> each field is compared with what its merchant, or its
  merchant's winning rule, gives now: the same -> checked; different, or no
  merchant -> chosen.

It changes no book, no type and no `cat_source`, which stays and is still
written (expand; a contract step retires it). It runs after the expect-back
step (conversion.CHAIN), and only through the conversion runner, so it sits
behind a backup, in one transaction, and a second run changes nothing. A mark
it does not know refuses the step, naming it, before anything is written.

    python convert_label_source.py <path to database>
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import book_type
import conversion
import db
import history

NAME = "label-source"

MERCHANT, RULE, WORDING, CHECKED, CHOSEN, DERIVED = book_type.SOURCE_NAMES
SOURCE_COLUMNS = ("book_source", "type_source")
# Read by the change-history step: a book reaching it has neither column yet,
# so its transactions triggers leave them out until this step adds them.
ADDS_COLUMNS = {"transactions": SOURCE_COLUMNS}

# The old marks that give both fields one source, whatever the row holds.
_DIRECT = {"auto": MERCHANT, "service_default": MERCHANT, "fallback": WORDING, "derived": DERIVED}
_RULE_OVERRIDE = "rule_override"
_MANUAL = "manual"
KNOWN_MARKS = (*_DIRECT, _RULE_OVERRIDE, _MANUAL)
# A row with no mark carries the column's default.
_NO_MARK = "auto"

_TRIGGER_NAME = re.compile(r"CREATE TRIGGER IF NOT EXISTS (history_transactions_(?:insert|update|delete)) ")


def column_sql(column: str) -> str:
    """A source column as schema.sql declares it: the CHECK holds the declared
    sources, so any writer handing an unknown one is refused."""
    names = ", ".join(f"'{name}'" for name in book_type.SOURCE_NAMES)
    return f"{column} TEXT CHECK ({column} IN ({names}))"


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}


def has_columns(conn: sqlite3.Connection) -> bool:
    return set(SOURCE_COLUMNS) <= _columns(conn)


def _triggers_record_sources(conn: sqlite3.Connection) -> bool:
    for op in ("insert", "update", "delete"):
        found = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (f"history_transactions_{op}",),
        ).fetchone()
        if found is None or not all(f"'{c}', " in found[0] for c in SOURCE_COLUMNS):
            return False
    return True


def unknown_marks(conn: sqlite3.Connection) -> dict[str, int]:
    """Marks no rule of this step reads, with how many rows carry each."""
    marks = ", ".join("?" for _ in KNOWN_MARKS)
    return dict(conn.execute(
        f"SELECT COALESCE(cat_source, ?) AS mark, COUNT(*) FROM transactions"
        f" WHERE COALESCE(cat_source, ?) NOT IN ({marks}) GROUP BY mark ORDER BY mark",
        (_NO_MARK, _NO_MARK, *KNOWN_MARKS),
    ).fetchall())


def _same_book(value: str, offered: str | None) -> bool:
    """A book equals the one offered; a merchant with no book offers the book
    an empty book reads as."""
    return value == (offered or book_type.DEFAULT_BOOK)


def _own_rule(conn: sqlite3.Connection, row) -> dict | None:
    """The rule that labels the row's wording now, when it is a rule of the
    row's own merchant; a rule of another merchant offers this row nothing."""
    rule = db.first_rule(row["description"], conn, row["amount_minor"])
    if rule is None or row["service_id"] is None or rule["service_id"] != row["service_id"]:
        return None
    return rule


def _overrides(rule: dict | None) -> bool:
    return rule is not None and (rule["book_override"] is not None or rule["type_override_id"] is not None)


def _sources(conn: sqlite3.Connection, row, merchants: dict) -> tuple[str, str]:
    """The (book source, type source) the row's mark gives, before empty
    fields are taken out."""
    mark = row["cat_source"] or _NO_MARK
    if mark in _DIRECT:
        return _DIRECT[mark], _DIRECT[mark]
    merchant = merchants.get(row["service_id"])
    rule = _own_rule(conn, row)
    if mark == _RULE_OVERRIDE:
        if _overrides(rule):
            return (RULE if rule["book_override"] is not None else MERCHANT,
                    RULE if rule["type_override_id"] is not None else MERCHANT)
        # The rule that set it is not there to ask: a field equal to the
        # merchant's is the merchant's, a different one came from a rule.
        offered_book, offered_type = merchant or (None, None)
        return (MERCHANT if _same_book(row["book"], offered_book) else RULE,
                MERCHANT if row["type_id"] == offered_type else RULE)
    # manual
    if merchant is None:
        return CHOSEN, CHOSEN
    offered_book, offered_type = db.rule_label(rule)[:2] if _overrides(rule) else merchant
    return (CHECKED if _same_book(row["book"], offered_book) else CHOSEN,
            CHECKED if row["type_id"] == offered_type else CHOSEN)


def planned(conn: sqlite3.Connection) -> list[tuple[str | None, str | None, int]]:
    """(book source, type source, row id) for every row with a book or a type
    that has no source for it yet. A source already held is kept."""
    conn.row_factory = sqlite3.Row
    held = "book_source, type_source" if has_columns(conn) else "NULL AS book_source, NULL AS type_source"
    rows = conn.execute(
        f"SELECT id, description, amount_minor, book, type_id, service_id, cat_source, {held}"
        " FROM transactions WHERE book IS NOT NULL OR type_id IS NOT NULL ORDER BY id"
    ).fetchall()
    merchants = {r["id"]: (r["book"], r["type_id"])
                 for r in conn.execute("SELECT id, book, type_id FROM services")}
    # The rules are read from this database: the cache may hold another's.
    db.invalidate_rules_cache()
    try:
        out = []
        for row in rows:
            book_wanted = row["book"] is not None and row["book_source"] is None
            type_wanted = row["type_id"] is not None and row["type_source"] is None
            if not (book_wanted or type_wanted):
                continue
            book_source, type_source = _sources(conn, row, merchants)
            out.append((
                book_type.checked_source(book_source if book_wanted else row["book_source"]),
                book_type.checked_source(type_source if type_wanted else row["type_source"]),
                row["id"],
            ))
        return out
    finally:
        db.invalidate_rules_cache()


def _refusal(conn: sqlite3.Connection) -> str | None:
    unknown = unknown_marks(conn)
    if not unknown:
        return None
    marks = ", ".join(f"'{mark}' ({n} {'row' if n == 1 else 'rows'})" for mark, n in unknown.items())
    return f"rows carry a mark this step does not know: {marks}; set each to a known mark first"


def _apply(conn: sqlite3.Connection) -> None:
    if not has_columns(conn):
        for column in SOURCE_COLUMNS:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {column_sql(column)}")
    # The history records every column: the triggers the change-history step
    # made without the two are replaced by schema.sql's.
    for statement in history.table_triggers("transactions"):
        conn.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME.match(statement).group(1)}")
        conn.execute(statement)
    conn.executemany(
        "UPDATE transactions SET book_source = ?, type_source = ? WHERE id = ?", planned(conn)
    )


def unsourced(conn: sqlite3.Connection) -> int:
    """How many rows hold a book or a type with no source for it."""
    return conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE (book IS NOT NULL AND book_source IS NULL)"
        " OR (type_id IS NOT NULL AND type_source IS NULL)"
    ).fetchone()[0]


def _is_applied(conn: sqlite3.Connection) -> bool:
    """The columns are there, the history records them, and every book and
    type has its source. Read from the data, not the columns alone: a book
    made new in schema.sql's shape has the columns from its start, and a row
    written there before every writer sets its sources (personal-ledger 49)
    reads as this step's to fill."""
    return has_columns(conn) and _triggers_record_sources(conn) and not unsourced(conn)


def _measure(conn: sqlite3.Connection) -> dict:
    measured = {
        **conversion.measure(conn),
        "labels": {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT id, book, type_id, cat_source FROM transactions")},
        "unsourced": None,
        "sources": None,
    }
    if has_columns(conn):
        measured["unsourced"] = unsourced(conn)
        measured["sources"] = {
            column: dict(Counter(r[0] for r in conn.execute(
                f"SELECT {column} FROM transactions WHERE {column} IS NOT NULL")))
            for column in SOURCE_COLUMNS
        }
    return measured


def _invariant(before: dict, after: dict) -> list[str]:
    problems = conversion.unchanged(before, after)
    moved = sum(1 for i, label in before["labels"].items() if after["labels"].get(i) != label)
    if moved:
        problems.append(f"{moved} rows changed their book, type or mark")
    if after["unsourced"]:
        problems.append(f"{after['unsourced']} rows with a book or a type have no source for it")
    return problems


STEP = conversion.Step(
    name=NAME, apply=_apply, is_applied=_is_applied, invariant=_invariant, measure=_measure,
    refusal=_refusal,
)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python convert_label_source.py <path to database>")
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
        for column, field in zip(SOURCE_COLUMNS, ("book", "type")):
            counts = report["after"]["sources"][column]
            shown = ", ".join(f"{name} {counts[name]}" for name in book_type.SOURCE_NAMES if counts.get(name))
            print(f"{field} sources: {shown or 'none'}")
        print("no book, type or mark was changed; cat_source is kept")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
