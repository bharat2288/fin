"""The change history: every change to the book, from the app or from a chat
client, as one entry that can be undone (fin-surfaces 01, Q3 and Q4).

An entry is one app action or one chat tool call. While it is open, the
triggers schema.sql declares on the tracked tables record every row it
inserts, updates or deletes, before and after. Seeding and the conversion
steps run with no entry open and are never recorded.

Undo writes the rows an entry changed back to how they were, as a new entry.
It is refused, naming the blocker, when a later entry changed any of the same
rows: undo that one first.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# The tables whose rows are recorded, each with its three triggers in
# schema.sql. Undo writes to these tables and to no other.
TRACKED = (
    "transactions",
    "anchors",
    "services",
    "merchant_rules",
    "accounts",
    "rates",
    "statements",
    "subscriptions",
    # Expect back (personal-ledger 23; expect_back.py).
    "expectation",
    "expectation_name",
    "set_off",
    "let_go",
    "refund_link",
    "sender_wording",
    "dismissal",
    # Types in the app (fin RUL-002): an added type and a fixed wording.
    "types",
    # Each declared type's stable key (type-relationships 01).
    "type_keys",
    # A type's usual book and Check each row by default (type-relationships 02).
    "type_settings",
    # fin's own filings: the type each slot files under (type-relationships 03).
    "type_slots",
)

# Tracked tables whose triggers a start puts in place itself: the table was
# there, with every column its triggers record, before it was tracked, so
# schema.sql's CREATE TRIGGER IF NOT EXISTS gives a book them on its next
# start and no conversion step is owed; so are a new table's, made by the
# same start. has_shape does not ask for them.
ADDED_ON_START = ("types", "type_keys", "type_settings", "type_slots")

# Where a change came from.
VIA_APP = "app"    # the operator, in fin's own screens
VIA_CHAT = "chat"  # a chat client through fin's MCP tools
VIAS = (VIA_APP, VIA_CHAT)
APP_ACTOR = "fin"

# Fields the history never shows: a statement's file name.
HIDDEN_FIELDS = {"statements": ("filename",)}

# One write at a time, so the rows the triggers record belong to the entry
# that is open. Re-entrant: a chat tool call holds it around the app route it
# runs, which takes it again.
WRITE_LOCK = threading.RLock()

_BLOCK = re.compile(r"-- history:begin\n(.*?)-- history:end", re.S)


class UndoRefused(Exception):
    """The entry was not undone. Nothing was changed."""


def schema_statements(leave_out: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    """The history block of schema.sql (tables, indexes, triggers), one
    statement each, for a caller that must run them inside its own
    transaction (executescript would commit).

    `leave_out` names, per tracked table, columns a database does not have
    yet because a later conversion step adds them: that table's triggers then
    record every other column, and the later step puts the full triggers in
    place when it adds the columns."""
    block = _BLOCK.search(SCHEMA_PATH.read_text()).group(1)
    statements, current = [], ""
    for line in block.splitlines(keepends=True):
        if not current and line.lstrip().startswith("--"):
            continue
        current += line
        if sqlite3.complete_statement(current):
            statements.append(_leaving_out(current.strip(), leave_out or {}))
            current = ""
    return statements


def table_triggers(table: str) -> list[str]:
    """The three history triggers schema.sql declares on one tracked table."""
    if table not in TRACKED:
        raise ValueError(f"not a tracked table: {table!r}")
    return [s for s in schema_statements() if s.startswith(f"CREATE TRIGGER IF NOT EXISTS history_{table}_")]


def _leaving_out(statement: str, leave_out: dict[str, tuple[str, ...]]) -> str:
    for table, columns in leave_out.items():
        if not statement.startswith(f"CREATE TRIGGER IF NOT EXISTS history_{table}_"):
            continue
        for column in columns:
            if not re.fullmatch(r"\w+", column):
                raise ValueError(f"not a column name: {column!r}")
            statement = re.sub(rf", '{column}', (?:NEW|OLD)\.{column}\b", "", statement)
    return statement


def has_shape(conn: sqlite3.Connection) -> bool:
    """Whether the database keeps the history: its two tables and every
    tracked table's three triggers, but those a start adds (ADDED_ON_START)."""
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')")}
    wanted = {"change_entries", "change_rows"} | {
        f"history_{t}_{op}" for t in TRACKED if t not in ADDED_ON_START
        for op in ("insert", "update", "delete")
    }
    return wanted <= names


def close_left_open(conn: sqlite3.Connection) -> None:
    """Close any entry a crash left open: while open it would take rows that
    are not its own. Not committed here."""
    conn.execute("UPDATE change_entries SET open = 0 WHERE open = 1")


def open_entry(conn: sqlite3.Connection, via: str, actor: str, summary: str = "") -> int:
    """Open an entry and commit it, so the triggers record into it whichever
    connection makes the change. The caller holds WRITE_LOCK."""
    if via not in VIAS:
        raise ValueError(f"unknown via: {via!r}")
    close_left_open(conn)
    cur = conn.execute(
        "INSERT INTO change_entries (via, actor, summary) VALUES (?, ?, ?)",
        (via, actor or APP_ACTOR, summary),
    )
    conn.commit()
    return cur.lastrowid


def close_entry(conn: sqlite3.Connection, entry_id: int, summary: str | None = None) -> int:
    """Close an entry and return how many rows it changed. An entry that
    changed nothing is removed."""
    count = row_count(conn, entry_id)
    if count == 0:
        conn.execute("DELETE FROM change_entries WHERE id = ? AND undone_by IS NULL", (entry_id,))
    elif summary is not None:
        conn.execute(
            "UPDATE change_entries SET open = 0, summary = CASE WHEN summary = '' THEN ? ELSE summary END "
            "WHERE id = ?",
            (summary, entry_id),
        )
    conn.execute("UPDATE change_entries SET open = 0 WHERE id = ?", (entry_id,))
    conn.commit()
    return count


def row_count(conn: sqlite3.Connection, entry_id: int) -> int:
    """How many distinct rows an entry changed."""
    return conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT tbl, row_id FROM change_rows WHERE entry_id = ?)",
        (entry_id,),
    ).fetchone()[0]


def _shown(tbl: str, data: str | None, held: set[str]) -> dict | None:
    """A recorded row as the history shows it: the fields its table `held`
    now, never a hidden one. A row recorded before a contract step dropped a
    column still carries it; it is not shown, as undo never writes it back."""
    if data is None:
        return None
    hidden = HIDDEN_FIELDS.get(tbl, ())
    return {k: v for k, v in json.loads(data).items() if k in held and k not in hidden}


class _Held(dict):
    """Each tracked table's columns, read once per call."""

    def __init__(self, conn: sqlite3.Connection):
        super().__init__()
        self.conn = conn

    def __missing__(self, tbl: str) -> set[str]:
        self[tbl] = _columns(self.conn, tbl) if tbl in TRACKED else set()
        return self[tbl]


def _entry(conn: sqlite3.Connection, r) -> dict:
    counts = dict(conn.execute(
        "SELECT tbl, COUNT(DISTINCT row_id) FROM change_rows WHERE entry_id = ? GROUP BY tbl",
        (r["id"],),
    ).fetchall())
    return {
        "id": r["id"],
        "at": r["at"],
        "via": r["via"],
        "actor": r["actor"],
        "summary": r["summary"],
        "rows": sum(counts.values()),
        "rows_by_table": counts,
        "undoes": r["undoes"],
        "undone_by": r["undone_by"],
    }


def record_asked(conn: sqlite3.Connection, entry_id: int, expected_count: int) -> None:
    """Record that a chat change stands on the count the operator agreed to
    in chat ("asked first, yes in chat"), in change_asked, and commit."""
    conn.execute(
        "INSERT OR REPLACE INTO change_asked (entry_id, expected_count) VALUES (?, ?)",
        (entry_id, expected_count),
    )
    conn.commit()


def asked_counts(conn: sqlite3.Connection, entry_ids) -> dict[int, int]:
    """For each of these entries that was asked first in chat, the count the
    operator agreed to."""
    ids = [int(i) for i in entry_ids]
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    return dict(conn.execute(
        f"SELECT entry_id, expected_count FROM change_asked WHERE entry_id IN ({marks})", ids,
    ).fetchall())


def entries(conn: sqlite3.Connection, limit: int = 50, before: int | None = None) -> list[dict]:
    """Closed entries, newest first."""
    sql = "SELECT * FROM change_entries WHERE open = 0"
    args: list = []
    if before is not None:
        sql += " AND id < ?"
        args.append(before)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    return [_entry(conn, r) for r in conn.execute(sql, args).fetchall()]


def entry(conn: sqlite3.Connection, entry_id: int) -> dict | None:
    """One entry with each row it changed, before and after."""
    r = conn.execute("SELECT * FROM change_entries WHERE id = ? AND open = 0", (entry_id,)).fetchone()
    if r is None:
        return None
    shown = _entry(conn, r)
    held = _Held(conn)
    shown["changes"] = [
        {
            "table": c["tbl"],
            "row_id": c["row_id"],
            "op": c["op"],
            "before": _shown(c["tbl"], c["before"], held[c["tbl"]]),
            "after": _shown(c["tbl"], c["after"], held[c["tbl"]]),
        }
        for c in conn.execute(
            "SELECT tbl, row_id, op, before, after FROM change_rows WHERE entry_id = ? ORDER BY id",
            (entry_id,),
        )
    ]
    return shown


def brief(conn: sqlite3.Connection, entry_id: int, scan: int = 400) -> dict | None:
    """What an entry did, in short, so its line in the list can say it in
    words: the first row it changed (before and after), whether every row
    took the same new values (same_change), and whether every row had the
    same name (same_label). An entry that changed one row twice counts it
    once, from its first before to its last after. None for no rows."""
    rows: dict[tuple[str, int], dict] = {}
    held = _Held(conn)
    for tbl, row_id, op, before, after in conn.execute(
        "SELECT tbl, row_id, op, before, after FROM change_rows WHERE entry_id = ? ORDER BY id LIMIT ?",
        (entry_id, scan),
    ):
        key = (tbl, row_id)
        if key in rows:
            rows[key]["after"] = _shown(tbl, after, held[tbl])
            rows[key]["op"] = "delete" if after is None else rows[key]["op"]
        else:
            rows[key] = {"table": tbl, "row_id": row_id, "op": op,
                         "before": _shown(tbl, before, held[tbl]), "after": _shown(tbl, after, held[tbl])}
    if not rows:
        return None
    shown = list(rows.values())

    def changed(c: dict) -> tuple:
        if c["op"] != "update":
            return (c["table"], c["op"])
        b, a = c["before"] or {}, c["after"] or {}
        return (c["table"], "update", tuple(sorted(
            (k, json.dumps(a.get(k))) for k in a if k != "id" and b.get(k) != a.get(k))))

    def named(c: dict):
        row = c["after"] or c["before"] or {}
        return row.get("description") or row.get("name") or row.get("pattern")

    first = shown[0]
    return {**first,
            "same_change": all(changed(c) == changed(first) for c in shown),
            "same_label": all(named(c) == named(first) for c in shown)}


def row_entries(conn: sqlite3.Connection, tbl: str, row_id: int) -> list[dict]:
    """The entries that changed one row, newest first."""
    if tbl not in TRACKED:
        return []
    return [
        _entry(conn, r)
        for r in conn.execute(
            "SELECT DISTINCT e.* FROM change_entries e JOIN change_rows c ON c.entry_id = e.id "
            "WHERE c.tbl = ? AND c.row_id = ? AND e.open = 0 ORDER BY e.id DESC",
            (tbl, row_id),
        )
    ]


def _in_force(conn: sqlite3.Connection, entry_id: int) -> bool:
    """Whether an entry's change still stands: not undone, or undone by an
    undo that was itself undone."""
    by = conn.execute("SELECT undone_by FROM change_entries WHERE id = ?", (entry_id,)).fetchone()[0]
    return by is None or not _in_force(conn, by)


def blocker(conn: sqlite3.Connection, entry_id: int) -> dict | None:
    """The earliest later entry, still in force, that changed a row this one
    changed. A later change and the undo that cancelled it block nothing."""
    later = conn.execute(
        "SELECT e.* FROM change_entries e WHERE e.id > ? AND e.open = 0 AND EXISTS ("
        "  SELECT 1 FROM change_rows l JOIN change_rows mine "
        "    ON mine.tbl = l.tbl AND mine.row_id = l.row_id "
        "  WHERE l.entry_id = e.id AND mine.entry_id = ?) "
        "ORDER BY e.id",
        (entry_id, entry_id),
    ).fetchall()
    for r in later:
        if not _in_force(conn, r["id"]):
            continue
        if r["undoes"] is not None and r["undoes"] > entry_id:
            continue  # it cancels a later change, which is not in force
        return _entry(conn, r)
    return None


def _columns(conn: sqlite3.Connection, tbl: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")}


def _write_back(conn: sqlite3.Connection, entry_id: int) -> None:
    """Write every row the entry changed back to its state before it, last
    change first. Table and column names come from TRACKED and the table's
    own columns, never from the stored JSON alone."""
    changes = conn.execute(
        "SELECT tbl, row_id, op, before FROM change_rows WHERE entry_id = ? ORDER BY id DESC",
        (entry_id,),
    ).fetchall()
    # Jev's stored answers name types by display name and are not tracked:
    # a type's name written back takes them with it, as the rename did
    # (type-relationships 04). Imported here: suggest imports db, which
    # imports this module.
    import book_type
    import suggest

    names = book_type.spending_type_names(conn) if any(c[0] == "types" for c in changes) else None
    _write_rows(conn, changes)
    if names is not None:
        suggest.follow_renames(conn, names)


def _write_rows(conn: sqlite3.Connection, changes: list) -> None:
    for tbl, row_id, op, before in changes:
        if tbl not in TRACKED:
            raise UndoRefused("this change touched a table undo does not write")
        if op == "insert":
            conn.execute(f"DELETE FROM {tbl} WHERE id = ?", (row_id,))
            continue
        cols = _columns(conn, tbl)
        row = {k: v for k, v in json.loads(before).items() if k in cols}
        if op == "update":
            sets = [k for k in row if k != "id"]
            conn.execute(
                f"UPDATE {tbl} SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                [row[k] for k in sets] + [row_id],
            )
        else:  # delete
            names = list(row)
            conn.execute(
                f"INSERT INTO {tbl} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                [row[k] for k in names],
            )


def undo(conn: sqlite3.Connection, entry_id: int, undo_entry_id: int) -> dict:
    """Undo an entry inside the open entry `undo_entry_id`, and commit.
    Refused, changing nothing, when the entry is unknown, still open, already
    undone, or a later entry changed one of its rows; and, naming it, when it
    would leave an expect-back record pointing at nothing or broken
    (expect_back.refusal_to_undo, expect_back.invariant_check), or take out
    a type something still uses (book_type.refusal_to_undo)."""
    r = conn.execute("SELECT * FROM change_entries WHERE id = ?", (entry_id,)).fetchone()
    if r is None or r["open"]:
        raise UndoRefused("no such change")
    if r["undone_by"] is not None:
        raise UndoRefused(f"this change was already undone (change {r['undone_by']})")
    later = blocker(conn, entry_id)
    if later is not None:
        raise UndoRefused(
            f"a later change touched the same rows: change {later['id']} "
            f"({later['summary'] or 'no summary'}, {later['at']}). Undo that one first."
        )
    # The expect-back records link rows to rows and to each other, so a change
    # can be undone without touching a later change's rows and still break
    # them (spec 23, E0): refused by name before writing, and checked again
    # after writing back, before the commit. Imported here: expect_back reads
    # the balance sheet, which this module must not load at import.
    import expect_back

    dangling = expect_back.refusal_to_undo(conn, entry_id)
    if dangling:
        raise UndoRefused(dangling)
    # A type added in the app is taken out again only while nothing uses it.
    import book_type

    in_use = book_type.refusal_to_undo(conn, entry_id)
    if in_use:
        raise UndoRefused(in_use)
    try:
        _write_back(conn, entry_id)
    except sqlite3.IntegrityError:
        conn.rollback()
        raise UndoRefused("undoing this change would break a link between rows; nothing was changed") from None
    broken = expect_back.invariant_check(conn, entry_id)
    if broken:
        conn.rollback()
        raise UndoRefused(broken)
    conn.execute(
        "UPDATE change_entries SET undoes = ?, summary = ? WHERE id = ?",
        (entry_id, f"Undid: {r['summary'] or 'change ' + str(entry_id)}", undo_entry_id),
    )
    conn.execute("UPDATE change_entries SET undone_by = ? WHERE id = ?", (undo_entry_id, entry_id))
    conn.commit()
    return {"undone": entry_id, "by": undo_entry_id}


def discard(conn: sqlite3.Connection, entry_id: int) -> None:
    """Write an entry's rows back and remove it, leaving no trace: for a chat
    write that stopped to ask the operator about its row count. The caller
    holds WRITE_LOCK and nothing has been written since.

    One transaction: the entry is closed (so the triggers do not record the
    write-back into it), an undo it made is taken back (the entry it undid
    is in force again, so no undone_by points at the entry being removed),
    its rows are written back and it is deleted. Anything failing rolls all
    of it back and raises: the entry then stands as it was."""
    try:
        conn.execute("UPDATE change_entries SET open = 0 WHERE id = ?", (entry_id,))
        conn.execute("UPDATE change_entries SET undone_by = NULL WHERE undone_by = ?", (entry_id,))
        conn.execute("UPDATE change_entries SET undoes = NULL WHERE undoes = ?", (entry_id,))
        _write_back(conn, entry_id)
        conn.execute("DELETE FROM change_rows WHERE entry_id = ?", (entry_id,))
        conn.execute("DELETE FROM change_asked WHERE entry_id = ?", (entry_id,))
        conn.execute("DELETE FROM change_entries WHERE id = ?", (entry_id,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def without_history(schema: str) -> str:
    """schema.sql's text without its history block: the shape a database had
    before the change-history step, for building older databases in tests."""
    return _BLOCK.sub("", schema)
