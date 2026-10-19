"""What a row field's source decides, and which source each writer sets.

Personal-ledger 24 (ADR-008): a row's book and its type each say where they
came from, one of book_type.SOURCES. This module is the one helper every
writer and every query that once read the row's one mark reads:

- **reachable by a later change**: from the merchant, a rule or the wording,
  and checked. Never chosen, never worked out;
- **waits in Check each row**: neither field checked nor chosen (the row
  half; the merchant's flag and the row's flow are the caller's);
- **counted by "book varies"**: the book chosen (the caller compares it with
  the merchant's);

and the sources the writers set, each field on its own:

- bulk label: checked where the value sent is the row's, chosen where it
  differs, or chosen whatever it is when the request says so (Set book);
- accepting the past ("Not now, only new rows"): a field from fin becomes
  checked;
- a later change reaching a field: a checked field stays checked, one from
  fin takes the source that gave its new value.

The one mark a row carried before the two sources (RETIRED_MARK) is retired
(personal-ledger 51): no writer sets it and no query reads it. A field with a
value and no source reads as from the merchant, as a row with no mark read
then; the contract step (retire_single_mark.py) wrote every other reading an
empty source had into the field before it dropped the mark.
"""

from __future__ import annotations

import json
import re

import book_type

MERCHANT, RULE, WORDING, CHECKED, CHOSEN, DERIVED = book_type.SOURCE_NAMES

# A field of a row, and the column that holds its source.
FIELDS = {"book": "book_source", "type_id": "type_source"}
# Sources fin gave: the rules, the merchant, the wording.
FROM_FIN = (MERCHANT, RULE, WORDING)
# What a later change may reach.
REACHABLE = (*FROM_FIN, CHECKED)
# The operator has looked at the field: a row with either does not wait.
LOOKED_AT = (CHECKED, CHOSEN)
# What a later change leaves alone.
KEPT = (CHOSEN, DERIVED)

# The column that held the one mark, retired (personal-ledger 51). Its one
# name: the contract step drops it, fin will not start on a book that still
# holds it (db), and the change history recorded before the label-source step
# carries it in a row's before and after (_set_it reads it there).
RETIRED_MARK = "cat_source"
# What a field with no source reads as: a row with no mark read so.
NO_SOURCE = MERCHANT

_ALIAS = re.compile(r"[a-z]\w*")


def _quoted(names) -> str:
    return ", ".join(f"'{name}'" for name in names)


def _column(alias: str, field: str) -> str:
    """A source column, qualified by a table alias (a fixed name from the
    caller's own query, never input)."""
    if field not in FIELDS:
        raise ValueError(f"not a labelled field: {field!r}")
    if alias and not _ALIAS.fullmatch(alias):
        raise ValueError(f"not a table alias: {alias!r}")
    return f"{alias}.{FIELDS[field]}" if alias else FIELDS[field]


def source_of(source: str | None) -> str:
    """A field's source as the helpers read it: its own, or from the merchant
    for a field with none."""
    return NO_SOURCE if source is None else source


def _source_sql(alias: str, field: str) -> str:
    return f"COALESCE({_column(alias, field)}, '{NO_SOURCE}')"


def reachable(source: str | None) -> bool:
    """Whether a later change may reach a field with this source (read by
    source_of)."""
    return source_of(source) in REACHABLE


def reachable_sql(field: str, alias: str = "t") -> str:
    """SQL: the row's `field` may be reached by a later change."""
    return f"{_source_sql(alias, field)} IN ({_quoted(REACHABLE)})"


def any_reachable_sql(alias: str = "t") -> str:
    """SQL: a later change may reach at least one of the row's fields."""
    return f"({reachable_sql('book', alias)} OR {reachable_sql('type_id', alias)})"


def from_fin_sql(alias: str = "t") -> str:
    """SQL: at least one of the row's fields is as fin gave it, not looked at."""
    either = " OR ".join(f"{_source_sql(alias, field)} IN ({_quoted(FROM_FIN)})" for field in FIELDS)
    return f"({either})"


def waits_sql(alias: str = "t") -> str:
    """SQL, the row half of Check each row: neither field checked nor chosen."""
    neither = " AND ".join(f"{_source_sql(alias, field)} NOT IN ({_quoted(LOOKED_AT)})" for field in FIELDS)
    return f"({neither})"


def looked_at_sql(alias: str = "t") -> str:
    """SQL: the operator has looked at a field of the row (checked or chosen
    either one), what a hand-set row was before the two sources."""
    return f"NOT {waits_sql(alias)}"


def book_varies_sql(alias: str = "t") -> str:
    """SQL, the row half of "book varies": the book is chosen."""
    return f"{_source_sql(alias, 'book')} = '{CHOSEN}'"


def given(value, source: str) -> str | None:
    """The source a field takes with a value: none when the field is empty."""
    return None if value is None else book_type.checked_source(source)


def same(field: str, sent, current) -> bool:
    """A value sent is the row's own; an empty book reads as the default book."""
    if field == "book":
        return sent == (current or book_type.DEFAULT_BOOK)
    return sent == current


def bulk(field: str, sent, current, held: str | None, chosen: bool = False) -> str:
    """Bulk label's source for a field sent: chosen when the request says
    so (Set book) or the value differs from the row's; else checked. A field
    already chosen or worked out, sent back as it is, keeps its source."""
    if chosen or not same(field, sent, current):
        return CHOSEN
    return held if held in KEPT else CHECKED


def changed_or_kept(field: str, sent, current, held: str | None, source: str) -> str | None:
    """A field written by hand where the operator may leave it as it was:
    `source` when the value changes, else its own source (an empty field
    given a value takes `source`)."""
    if sent is None:
        return None
    if held is not None and current is not None and same(field, sent, current):
        return held
    return book_type.checked_source(source)


def accepted(source: str | None) -> str | None:
    """A field accepted as it stands (Not now, only new rows): checked when
    fin gave it, else its own."""
    return CHECKED if source in FROM_FIN else source


def reached(held: str | None, value, source: str) -> str | None:
    """The source of a field a later change reached: a checked field stays
    checked, one fin gave takes the source that gave its new value."""
    if value is None:
        return None
    return CHECKED if held == CHECKED else book_type.checked_source(source)


def rule_sources(rule) -> tuple[str, str]:
    """The (book source, type source) a rule's label carries: from the rule
    for the field it overrides, from the merchant for the other."""
    return (
        RULE if rule["book_override"] is not None else MERCHANT,
        RULE if rule["type_override_id"] is not None else MERCHANT,
    )


# --- The marks shown (fin-surfaces 52) ---------------------------------------

# The field names a screen reads a row's two sources by.
SHOWN = {"book": "book", "type_id": "type"}


def shown_sources(tx) -> dict:
    """A row's two sources as the screens are given them, read by source_of;
    none for an empty field with no source. `tx` holds the row's raw book,
    type_id and the two source columns."""
    out = {}
    for field, name in SHOWN.items():
        held = tx[FIELDS[field]]
        has_value = tx[field] is not None or held is not None
        out[name] = source_of(held) if has_value else None
    return out


def _set_it(field: str, source: str, before: dict | None, after: dict) -> bool:
    """Whether one recorded change set a field to the source it holds now.

    A change recorded with the source columns set it when it gave the field
    that source, or (chosen) a new value while chosen. One recorded before
    the label-source step holds no source: the row's hand-set mark there is
    what set a field now checked or chosen."""
    column = FIELDS[field]
    before = before or {}
    if column in after:
        if after[column] != source:
            return False
        if before.get(column) != source:
            return True
        return source == CHOSEN and before.get(field) != after.get(field)
    if source not in LOOKED_AT or after.get(RETIRED_MARK) != "manual":
        return False
    return before.get(RETIRED_MARK) != "manual" or before.get(field) != after.get(field)


def set_by(conn, tx_id: int, sources: dict) -> dict:
    """For each field checked or chosen, the latest change entry that set it
    so: its id, where it came from (`via`, `actor`) and when (`at`, UTC).
    None for a field from fin, and for one set before fin kept a history."""
    wanted = {field: sources[name] for field, name in SHOWN.items() if sources.get(name) in LOOKED_AT}
    out = {name: None for name in SHOWN.values()}
    if not wanted:
        return out
    found: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT e.id, e.via, e.actor, e.at, c.before, c.after FROM change_rows c"
        " JOIN change_entries e ON e.id = c.entry_id"
        " WHERE c.tbl = 'transactions' AND c.row_id = ? AND e.open = 0 AND c.after IS NOT NULL"
        " ORDER BY c.id DESC",
        (tx_id,),
    ):
        before = json.loads(r["before"]) if r["before"] else None
        after = json.loads(r["after"])
        for field, source in wanted.items():
            if field not in found and _set_it(field, source, before, after):
                found[field] = {"entry_id": r["id"], "via": r["via"], "actor": r["actor"], "at": r["at"]}
        if len(found) == len(wanted):
            break
    for field, entry in found.items():
        out[SHOWN[field]] = entry
    return out
