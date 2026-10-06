"""What fin's screens keep that is not part of the book (fin-surfaces 02).

- The refusal a chat-side import (the upload command) answers while the
  "Claude may write" switch is off. The switch itself is chat_writes.py's
  (chat-writes.json beside the book), the one switch; it is not kept here.
- Where the operator last looked in the change history: Home's quiet line
  counts the chat's changes after it. It moves when Recent changes is opened
  or "Looks right" is tapped on Home (02, ruling 7).
- Statements refused at upload because they do not tie. Their rows are never
  written; the record keeps the tie line so the queue and the account page say
  so until a file that ties is imported for the same account and day. "Known,
  leave it" sets one aside: it leaves Home and the top of the queue and stays
  marked refused on its account (02, ruling 8).

None of this is in a tracked table: looking, or setting a refusal aside, is
not a change to the book and is not in the history.
"""

from __future__ import annotations

import json
import sqlite3

LAST_LOOKED = "last_looked_entry"
LAST_LOOKED_AT = "last_looked_at"   # when the operator last looked (UTC), for Home's line
KEYS = (LAST_LOOKED, LAST_LOOKED_AT)

# What a chat-side import answers while the switch (chat_writes.py) is off.
IMPORTS_OFF = (
    "Claude's writes are switched off in fin, so an import from Claude's side is "
    "refused; nothing was imported. The operator can import in fin, or switch writes back on there."
)


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else row[0]


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    if key not in KEYS:
        raise ValueError(f"unknown setting: {key!r}")
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value),
    )
    conn.commit()


def last_looked(conn: sqlite3.Connection) -> int | None:
    value = _get(conn, LAST_LOOKED)
    return int(value) if value is not None else None


def mark_looked(conn: sqlite3.Connection, upto: int | None = None) -> int:
    """Move the mark to `upto`, or to the newest closed entry. It never moves
    back: an older id leaves it where it is. Returns where it is."""
    newest = conn.execute("SELECT COALESCE(MAX(id), 0) FROM change_entries WHERE open = 0").fetchone()[0]
    target = newest if upto is None else max(0, min(int(upto), newest))
    held = last_looked(conn)
    # Looking is looking, whether or not the mark moves: keep when it happened.
    _set(conn, LAST_LOOKED_AT, conn.execute("SELECT datetime('now')").fetchone()[0])
    if held is None or target > held:
        _set(conn, LAST_LOOKED, str(target))
        return target
    return held


def since_looked(conn: sqlite3.Connection) -> dict:
    """The chat's changes after the mark: how many, and the newest entry id.
    With no mark yet, every chat change counts."""
    mark = last_looked(conn)
    row = conn.execute(
        "SELECT COUNT(*), MAX(at) FROM change_entries WHERE open = 0 AND via = 'chat' AND id > ?",
        (mark or 0,),
    ).fetchone()
    newest = conn.execute("SELECT COALESCE(MAX(id), 0) FROM change_entries WHERE open = 0").fetchone()[0]
    # When the operator last looked; a book from before that key falls back to when the mark last moved.
    looked_at = _get(conn, LAST_LOOKED_AT)
    if looked_at is None:
        row_at = conn.execute("SELECT updated_at FROM app_settings WHERE key = ?", (LAST_LOOKED,)).fetchone()
        looked_at = row_at[0] if row_at else None
    return {"last_looked": mark, "last_looked_at": looked_at,
            "claude_count": row[0], "latest_claude_at": row[1], "newest": newest}


def claude_marks(conn: sqlite3.Connection) -> dict:
    """What the chat changed after the mark, for the quiet mark on rows and
    balances (01, "For 02" item 4): each row the chat changed, and each
    account whose balance-sheet line it touched (a figure, the account
    itself, or a row naming it as the other side, or a row added to or
    taken from it), mapped to the newest such change still in force. With
    no mark yet, every chat change counts."""
    mark = last_looked(conn) or 0
    rows: dict[int, int] = {}
    accounts: dict[int, int] = {}

    def touch(target: dict, key, entry_id: int) -> None:
        if key is None:
            return
        key = int(key)
        if entry_id > target.get(key, 0):
            target[key] = entry_id

    changed = conn.execute(
        "SELECT c.entry_id, c.tbl, c.row_id, c.op, c.before, c.after FROM change_rows c "
        "JOIN change_entries e ON e.id = c.entry_id "
        "WHERE e.open = 0 AND e.via = 'chat' AND e.id > ? AND e.undone_by IS NULL",
        (mark,),
    ).fetchall()
    for entry_id, tbl, row_id, op, before, after in changed:
        old = json.loads(before) if before else {}
        new = json.loads(after) if after else {}
        if tbl == "transactions":
            touch(rows, row_id, entry_id)
            if old.get("other_side_id") != new.get("other_side_id"):
                touch(accounts, old.get("other_side_id"), entry_id)
                touch(accounts, new.get("other_side_id"), entry_id)
            if op != "update" or old.get("amount_minor") != new.get("amount_minor"):
                sid = new.get("statement_id") or old.get("statement_id")
                found = conn.execute("SELECT account_id FROM statements WHERE id = ?", (sid,)).fetchone()
                touch(accounts, found[0] if found else None, entry_id)
        elif tbl == "anchors":
            touch(accounts, new.get("account_id") or old.get("account_id"), entry_id)
        elif tbl == "accounts":
            touch(accounts, row_id, entry_id)
    return {"last_looked": mark or None, "rows": rows, "accounts": accounts}


# ---------------------------------------------------------------------------
# Refused statements
# ---------------------------------------------------------------------------

def record_refused(conn: sqlite3.Connection, *, account_name: str, account_id: int | None,
                   statement_date: str, currency: str, figures: dict) -> None:
    """Keep a refusal. The same account and day again replaces its figures
    and leaves a "Known, leave it" in place. Not committed here."""
    conn.execute(
        "INSERT INTO refused_statements (account_name, account_id, statement_date, currency, "
        "opening_minor, rows_minor, closing_minor, difference_minor, row_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(account_name, statement_date) DO UPDATE SET "
        "account_id = excluded.account_id, currency = excluded.currency, "
        "opening_minor = excluded.opening_minor, rows_minor = excluded.rows_minor, "
        "closing_minor = excluded.closing_minor, difference_minor = excluded.difference_minor, "
        "row_count = excluded.row_count, refused_at = datetime('now')",
        (account_name, account_id, statement_date, currency,
         figures["opening_minor"], figures["rows_minor"], figures["closing_minor"],
         figures["difference_minor"], figures.get("rows", 0)),
    )


# A refusal stands until a statement for the same account and closing day is
# held: the file that ties was imported.
_STANDING = (
    "NOT EXISTS (SELECT 1 FROM statements s JOIN accounts a ON a.id = s.account_id "
    "WHERE (a.id = r.account_id OR a.name = r.account_name) AND s.statement_date = r.statement_date)"
)


def refused(conn: sqlite3.Connection, account_id: int | None = None) -> list[dict]:
    """Every refusal that still stands, newest first; with an account, its own."""
    sql = f"SELECT r.* FROM refused_statements r WHERE {_STANDING}"
    args: list = []
    if account_id is not None:
        sql += " AND (r.account_id = ? OR r.account_name = (SELECT name FROM accounts WHERE id = ?))"
        args += [account_id, account_id]
    sql += " ORDER BY r.statement_date DESC, r.id DESC"
    return [
        {
            "id": r["id"],
            "account_name": r["account_name"],
            "account_id": r["account_id"],
            "statement_date": r["statement_date"],
            "currency": r["currency"],
            "opening_minor": r["opening_minor"],
            "rows_minor": r["rows_minor"],
            "closing_minor": r["closing_minor"],
            "difference_minor": r["difference_minor"],
            "rows": r["row_count"],
            "refused_at": r["refused_at"],
            "set_aside": r["set_aside_at"] is not None,
            "set_aside_at": r["set_aside_at"],
        }
        for r in conn.execute(sql, args)
    ]


def set_aside(conn: sqlite3.Connection, refused_id: int, aside: bool) -> bool:
    """Set a refusal aside, or bring it back. False when there is no such one."""
    cur = conn.execute(
        "UPDATE refused_statements SET set_aside_at = "
        + ("datetime('now')" if aside else "NULL")
        + " WHERE id = ?",
        (refused_id,),
    )
    conn.commit()
    return cur.rowcount > 0
