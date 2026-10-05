"""The one runner every conversion of existing rows goes through.

A conversion step changes rows that already exist (relabelling them, changing
how an amount is stored). The runner makes each step cheap to get wrong:

1. it writes a dated backup copy of the database beside it, and refuses to
   start if the copy is missing or differs in size from the source;
2. it runs the step in one transaction, schema changes included;
3. it records row counts and per-account totals before and after (or the
   step's own measure, when it declares one), and fails, rolling everything
   back, if the step's declared invariant does not hold;
4. it is safe to run twice: a step that is already applied is not run again
   and no second backup is written.

The runner takes the database path from its caller. It never opens a database
by a relative name and has no default path.
"""

from __future__ import annotations

import importlib
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import money

# Tables whose row counts are recorded. A frozen list: table names in the
# count query come from here and nowhere else. A table absent from the
# database (not created yet, or retired by an earlier step) is left out.
COUNTED_TABLES = (
    "accounts",
    "statements",
    "transactions",
    "services",
    "merchant_rules",
    "subscriptions",
    "categories",
    "batch_imports",
)

# The conversion steps in the order they run: (step name, module). Each
# module declares STEP and a main(argv). convert_all.py runs them in this
# order, and run_step refuses a step from this list while a step before it
# has not been applied.
CHAIN = (
    ("book-and-type", "convert_book_type"),
    ("retire-categories", "retire_categories"),
    ("minor-units", "convert_minor_units"),
    ("retire-float-amounts", "retire_float_amounts"),
    ("account-kinds", "convert_account_kinds"),
    ("movements", "convert_movements"),
    ("printed-statements", "convert_printed_statements"),
    ("flows", "convert_null_flows"),
)

_STEP_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


class ConversionRefused(Exception):
    """The runner would not start the step. Nothing was changed."""


class ConversionFailed(Exception):
    """The step was started and did not pass. Its changes were rolled back and
    the database is as it was, unless the message says the changes could not be
    rolled back and names the backup to restore from."""


def chain_step(module_name: str) -> Step:
    """The STEP a module of the chain declares."""
    return importlib.import_module(module_name).STEP


def not_applied(conn: sqlite3.Connection, names=None) -> list[str]:
    """The steps of the chain that do not read as applied, in chain order.
    `names` limits the reading to those steps."""
    waiting = []
    for name, module_name in CHAIN:
        if names is not None and name not in names:
            continue
        try:
            applied = chain_step(module_name).is_applied(conn)
        except sqlite3.Error:
            # A shape the step cannot read (a column it needs is gone) is not
            # the shape it leaves behind.
            applied = False
        if not applied:
            waiting.append(name)
    return waiting


def _earlier(step_name: str) -> list[str]:
    names = [name for name, _ in CHAIN]
    return names[: names.index(step_name)] if step_name in names else []


def unchanged(before: dict, after: dict) -> list[str]:
    """The default invariant: no row count and no account total moved."""
    problems = []
    for table in sorted(set(before["rows"]) | set(after["rows"])):
        was, now = before["rows"].get(table), after["rows"].get(table)
        if was != now:
            problems.append(f"row count of {table} changed: {was} -> {now}")
    was_totals, now_totals = before["account_totals_cents"], after["account_totals_cents"]
    # The key None holds rows that belong to no account; it sorts last.
    for account_id in sorted(set(was_totals) | set(now_totals), key=lambda a: (a is None, a or 0)):
        was, now = was_totals.get(account_id), now_totals.get(account_id)
        if was != now:
            whose = "rows on no account" if account_id is None else f"account {account_id}"
            problems.append(f"total of {whose} changed: {was} -> {now} cents")
    return problems


def count_rows(conn: sqlite3.Connection) -> dict[str, int]:
    """The row count of each counted table the database holds."""
    present = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    rows = {}
    for table in COUNTED_TABLES:
        if table in present:
            rows[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return rows


def measure(conn: sqlite3.Connection) -> dict:
    """The default measure: row counts per table and the total of each
    account's rows, in cents. The total is read from the whole minor units a
    row carries; a database that still holds the float amount is totalled
    from that, as it was before the amounts were converted.

    Every transaction row is in exactly one total. A row whose statement is
    missing, or whose statement names no account, is totalled under the key
    None, so a change to its amount is seen like any other.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(transactions)")}
    rows = conn.execute(
        f"""
        SELECT s.account_id, t.{"amount_sgd" if "amount_sgd" in columns else "amount_minor"}
        FROM transactions t
        LEFT JOIN statements s ON s.id = t.statement_id
        """
    ).fetchall()
    totals: dict = {}
    for account_id, amount in rows:
        if "amount_sgd" in columns and amount is not None:
            # Each float is rounded to its cent by the one rule money has
            # (money.to_minor: its decimal reading, half-even), the rule the
            # minor-units step fills the integers by, so the money steps and
            # this measure never disagree about a half cent. SQLite's ROUND
            # rounds the binary value half away from zero, and would.
            amount = money.to_minor(amount)
        if amount is None:
            totals.setdefault(account_id, None)
            continue
        totals[account_id] = (totals.get(account_id) or 0) + amount
    return {"rows": count_rows(conn), "account_totals_cents": totals}


@dataclass(frozen=True)
class Step:
    """One conversion step.

    name       — lowercase words joined by hyphens; it goes into the backup's
                 file name.
    apply      — does the work on the connection it is given. The runner
                 owns the transaction: while apply runs, BEGIN, COMMIT and
                 ROLLBACK are refused, and so is conn.executescript (Python
                 commits before it runs a script). Use conn.execute, one
                 statement at a time.
    is_applied — reads the database and says whether the step's work is
                 already there. This is what makes a second run a no-op, and
                 it reads the data itself, so it stays true for a database
                 restored from a backup or created fresh in the new shape.
    invariant  — given the measurements before and after, returns a list of
                 problems (empty when it holds). Defaults to `unchanged`.
    measure    — reads the database and returns the measurements the
                 invariant is given. Defaults to `measure`, which totals the
                 float amount; a step that changes how an amount is stored
                 declares its own, and its invariant reads that.
    """

    name: str
    apply: Callable[[sqlite3.Connection], None]
    is_applied: Callable[[sqlite3.Connection], bool]
    invariant: Callable[[dict, dict], list[str]] = unchanged
    measure: Callable[[sqlite3.Connection], dict] = measure


def copy_file(source: Path, target: Path) -> None:
    """The default backup: a byte copy of the database file."""
    shutil.copy2(source, target)


def _backup_path(db_path: Path, step_name: str, day: date) -> Path:
    """`<database>.pre-<step>-<YYYYMMDD>.bak`, numbered if that name is taken."""
    stem = f"{db_path.name}.pre-{step_name}-{day:%Y%m%d}"
    candidate = db_path.with_name(f"{stem}.bak")
    n = 2
    while candidate.exists():
        candidate = db_path.with_name(f"{stem}-{n}.bak")
        n += 1
    return candidate


def run_step(
    db_path: Path | str,
    step: Step,
    *,
    today: date | None = None,
    backup: Callable[[Path, Path], None] = copy_file,
) -> dict:
    """Run one conversion step behind a backup, in one transaction.

    Returns a report: the step's name, `status` ("applied" or
    "already-applied"), the backup's path (None when nothing was run), and the
    measurements before and after.

    Raises ConversionRefused when the step was not started (a step of the
    chain is not started while a step before it is not applied), ConversionFailed
    when it was started and rolled back.
    """
    if not _STEP_NAME.fullmatch(step.name):
        raise ConversionRefused(f"step name {step.name!r} must be lowercase words joined by hyphens")
    db_path = Path(db_path).resolve()
    if not db_path.is_file():
        raise ConversionRefused(f"no database at {db_path}")

    # isolation_level=None: the sqlite3 module opens no transaction of its
    # own, so the BEGIN below covers schema changes as well as row changes.
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        if step.is_applied(conn):
            return {
                "step": step.name,
                "status": "already-applied",
                "backup": None,
                "before": None,
                "after": None,
            }

        # A step of the chain runs only on a database every earlier step has
        # been applied to: it is written for that shape.
        waiting = not_applied(conn, _earlier(step.name))
        if waiting:
            raise ConversionRefused(
                f"step {step.name} runs after {', '.join(waiting)}, which "
                f"{'has' if len(waiting) == 1 else 'have'} not been applied; nothing was "
                "changed. Run every step in order with: python convert_all.py <path to database>"
            )

        # In WAL mode committed rows can sit in the -wal file, where a copy of
        # the main file would miss them. Fold them in, then take the write
        # lock so nothing can change between the copy and the step.
        busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
        if busy:
            raise ConversionRefused(
                "the database is in use by another connection; close it and run again"
            )
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            raise ConversionRefused(
                "the database is being written by another connection; close it and run again"
            ) from None
        try:
            wal = db_path.with_name(db_path.name + "-wal")
            if wal.exists() and wal.stat().st_size:
                raise ConversionRefused(
                    "the database changed while the backup was being prepared; run again"
                )

            target = _backup_path(db_path, step.name, today or date.today())
            backup(db_path, target)
            if not target.is_file():
                raise ConversionRefused(f"backup copy is missing: {target.name} was not written")
            source_size, copy_size = db_path.stat().st_size, target.stat().st_size
            if copy_size != source_size:
                # The runner wrote this file a moment ago under a name that was
                # free, so it is the runner's to remove. Left in place it would
                # read as the day's first backup.
                target.unlink()
                raise ConversionRefused(
                    f"backup copy {target.name} differs in size from the database "
                    f"({copy_size} bytes against {source_size}); the bad copy was removed"
                )

            before = step.measure(conn)

            # While the step runs, SQLite refuses any statement that begins or
            # ends a transaction, so the step cannot commit part of its work
            # out from under the checks below. The refusal happens when the
            # statement is prepared: the runner's transaction stays open.
            tried_transaction_control = []

            def refuse_transaction_control(action, *details):
                if action == sqlite3.SQLITE_TRANSACTION:
                    tried_transaction_control.append(details[0])
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            conn.set_authorizer(refuse_transaction_control)
            try:
                step.apply(conn)
            except Exception as exc:
                if not tried_transaction_control:
                    # The type only: an exception's text can carry row contents.
                    raise ConversionFailed(
                        f"step {step.name} raised {type(exc).__name__} and was rolled back"
                    ) from exc
            finally:
                conn.set_authorizer(None)
            if not conn.in_transaction:
                # Not reachable while the refusal above holds; kept so that a
                # commit the runner could not prevent is never reported as a
                # rollback.
                raise ConversionFailed(
                    f"step {step.name} ended the runner's transaction; its changes "
                    f"could not be checked or rolled back. Restore from {target.name} "
                    "if they are wrong."
                )
            if tried_transaction_control:
                raise ConversionFailed(
                    f"step {step.name} tried to control the transaction "
                    f"({', '.join(tried_transaction_control)}), which the runner owns; "
                    "it was refused and the step was rolled back"
                )
            after = step.measure(conn)
            problems = step.invariant(before, after)
            if problems:
                raise ConversionFailed(
                    f"step {step.name} broke its invariant and was rolled back: "
                    + "; ".join(problems)
                )
            if not step.is_applied(conn):
                raise ConversionFailed(
                    f"step {step.name} ran but does not read as applied, so a second "
                    "run would repeat it; rolled back"
                )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    return {
        "step": step.name,
        "status": "applied",
        "backup": str(target),
        "before": before,
        "after": after,
    }
