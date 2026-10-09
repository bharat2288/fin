"""The "Claude may write" switch (fin-surfaces 01, Off switches).

With it off, every chat write tool refuses with a fixed message and reads
still work. The chat can turn it off (a lost phone, odd behaviour), never on:
only the operator, in fin's own app, turns it back on. Copied from folio's
chat_writes_enabled posture.

fin has no settings table, so the switch is a small JSON file beside the book
(as backup.py keeps backup-status.json): {"enabled", "changed_at",
"changed_by"}. No file means on, the ruled default. A file that cannot be
read or is not a switch means off: it fails closed. Writes are atomic (a
temporary file beside it, then a rename). The file is not part of the book:
it is not in the change history and not backed up.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import db

STATE_FILE = "chat-writes.json"

WRITES_OFF = "Claude's writes are turned off in fin. Turn them back on in fin's app."


def state_path(db_path=None) -> Path:
    return Path(db_path if db_path is not None else db.current_db_path()).parent / STATE_FILE


def read(db_path=None) -> dict:
    """The switch: {"enabled": bool, "changed_at": str|None, "changed_by": str|None},
    with "unreadable": True when the file is there but is not a switch (read as off)."""
    try:
        text = state_path(db_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"enabled": True, "changed_at": None, "changed_by": None}
    except OSError:
        return {"enabled": False, "changed_at": None, "changed_by": None, "unreadable": True}
    try:
        recorded = json.loads(text)
    except ValueError:
        recorded = None
    if not isinstance(recorded, dict) or not isinstance(recorded.get("enabled"), bool):
        return {"enabled": False, "changed_at": None, "changed_by": None, "unreadable": True}
    changed_at, changed_by = recorded.get("changed_at"), recorded.get("changed_by")
    return {
        "enabled": recorded["enabled"],
        "changed_at": changed_at if isinstance(changed_at, str) else None,
        "changed_by": changed_by if isinstance(changed_by, str) else None,
    }


def enabled(db_path=None) -> bool:
    return read(db_path)["enabled"]


def write(on: bool, changed_by: str, db_path=None) -> dict:
    """Set the switch, atomically. Returns what was written."""
    if not isinstance(on, bool):
        raise TypeError("the switch is a boolean")
    state = {
        "enabled": on,
        "changed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "changed_by": str(changed_by)[:80],
    }
    target = state_path(db_path)
    fd, tmp_name = tempfile.mkstemp(prefix=".fin-chat-writes-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return state
