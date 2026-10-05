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
)

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


def schema_statements() -> list[str]:
    """The history block of schema.sql (tables, indexes, triggers), one
    statement each, for a caller that must run them inside its own
    transaction (executescript would commit)."""
    block = _BLOCK.search(SCHEMA_PATH.read_text()).group(1)
    statements, current = [], ""
    for line in block.splitlines(keepends=True):
        if not current and line.lstrip().startswith("--"):
            continue
        current += line
        if sqlite3.complete_statement(current):
            statements.append(current.strip())
            current = ""
    return statements


def has_shape(conn: sqlite3.Connection) -> bool:
    """Whether the database keeps the history: its two tables and every
    tracked table's three triggers."""
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')")}
    wanted = {"change_entries", "change_rows"} | {
        f"history_{t}_{op}" for t in TRACKED for op in ("insert", "update", "delete")
    }
    return wanted <= names


def open_entry(conn: sqlite3.Connection, via: str, actor: str, summary: str = "") -> int:
    """Open an entry and commit it, so the triggers record into it whichever
    connection makes the change. The caller holds WRITE_LOCK."""
    if via not in VIAS:
        raise ValueError(f"unknown via: {via!r}")
    # An entry left open by a crash would take rows that are not its own.
    conn.execute("UPDATE change_entries SET open = 0 WHERE open = 1")
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


def _shown(tbl: str, data: str | None) -> dict | None:
    if data is None:
        return None
    row = json.loads(data)
    for field in HIDDEN_FIELDS.get(tbl, ()):
        row.pop(field, None)
    return row


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
    shown["changes"] = [
        {
            "table": c["tbl"],
            "row_id": c["row_id"],
            "op": c["op"],
            "before": _shown(c["tbl"], c["before"]),
            "after": _shown(c["tbl"], c["after"]),
        }
        for c in conn.execute(
            "SELECT tbl, row_id, op, before, after FROM change_rows WHERE entry_id = ? ORDER BY id",
            (entry_id,),
        )
    ]
    return shown


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
    undone, or a later entry changed one of its rows."""
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
    try:
        _write_back(conn, entry_id)
    except sqlite3.IntegrityError:
        conn.rollback()
        raise UndoRefused("undoing this change would break a link between rows; nothing was changed") from None
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
    holds WRITE_LOCK and nothing has been written since."""
    conn.execute("UPDATE change_entries SET open = 0 WHERE id = ?", (entry_id,))
    conn.commit()
    _write_back(conn, entry_id)
    conn.execute("DELETE FROM change_rows WHERE entry_id = ?", (entry_id,))
    conn.execute("DELETE FROM change_entries WHERE id = ?", (entry_id,))
    conn.commit()


def without_history(schema: str) -> str:
    """schema.sql's text without its history block: the shape a database had
    before the change-history step, for building older databases in tests."""
    return _BLOCK.sub("", schema)
