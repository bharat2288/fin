"""The conversion runner, proved on a temporary database in the old shape.

Every figure and name here is invented. Nothing in this file opens the
repository's own database: each test builds its own from schema.sql.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

import conversion
import db

DAY = date(2026, 3, 14)


@pytest.fixture
def old_db(tmp_path: Path) -> Path:
    """A database in the old shape (today's schema.sql) with two accounts."""
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(db.SCHEMA_PATH.read_text())
    conn.executemany(
        "INSERT INTO accounts (id, name, short_name, type) VALUES (?, ?, ?, ?)",
        [(1, "Sample Card 0001", "Sample-0001", "credit_card"),
         (2, "Sample Bank 0002", "Sample-0002", "bank")],
    )
    conn.executemany(
        "INSERT INTO statements (id, account_id, statement_date) VALUES (?, ?, ?)",
        [(1, 1, "2026-01-31"), (2, 2, "2026-01-31")],
    )
    conn.executemany(
        "INSERT INTO transactions (statement_id, date, description, amount_sgd, notes)"
        " VALUES (?, ?, ?, ?, ?)",
        [(1, "2026-01-05", "CORNER CAFE", 12.30, None),
         (1, "2026-01-09", "BOOK SHOP", 40.05, None),
         (2, "2026-01-12", "TRANSFER TO A FRIEND", 250.00, None),
         (2, "2026-01-20", "PAY FROM EMPLOYER", -3100.10, None)],
    )
    conn.commit()
    conn.close()
    return path


def _dump(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def _files(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir())


def _notes(path: Path) -> list:
    conn = sqlite3.connect(str(path))
    try:
        return [r[0] for r in conn.execute("SELECT notes FROM transactions ORDER BY id")]
    finally:
        conn.close()


def _mark_rows(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE transactions SET notes = 'converted'")


def _rows_marked(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE notes IS NOT 'converted'"
    ).fetchone()[0] == 0


MARK = conversion.Step(name="mark-rows", apply=_mark_rows, is_applied=_rows_marked)


# --- the step runs behind a backup ---------------------------------------


def test_step_runs_behind_a_dated_backup_beside_the_database(old_db):
    before = _dump(old_db)

    report = conversion.run_step(old_db, MARK, today=DAY)

    backup = Path(report["backup"])
    assert backup.parent == old_db.parent
    assert backup.name == "ledger.db.pre-mark-rows-20260314.bak"
    assert backup.stat().st_size == old_db.stat().st_size
    # The copy holds the database as it was before the step; the source moved on.
    assert _dump(backup) == before
    assert _notes(old_db) == ["converted"] * 4
    assert report["status"] == "applied"


def test_backup_holds_rows_still_sitting_in_the_write_ahead_log(old_db):
    # The app opens the database in WAL mode, where committed rows can live in
    # the -wal file. A plain copy of the main file would miss them.
    other = sqlite3.connect(str(old_db))
    other.execute("PRAGMA journal_mode=WAL")
    other.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_sgd)"
        " VALUES (1, '2026-01-25', 'LATE ROW', 7.00)"
    )
    other.commit()
    try:
        report = conversion.run_step(old_db, MARK, today=DAY)
    finally:
        other.close()

    assert len(_notes(Path(report["backup"]))) == 5


def test_an_earlier_backup_of_the_same_day_is_never_overwritten(old_db):
    earlier = old_db.parent / "ledger.db.pre-mark-rows-20260314.bak"
    earlier.write_bytes(b"an earlier copy")

    report = conversion.run_step(old_db, MARK, today=DAY)

    assert earlier.read_bytes() == b"an earlier copy"
    assert Path(report["backup"]).name == "ledger.db.pre-mark-rows-20260314-2.bak"


# --- P8 refusal: a conversion started without its backup refuses ---------


def test_conversion_refuses_when_the_backup_copy_is_missing(old_db):
    before = _dump(old_db)
    ran = []

    step = conversion.Step(
        name="mark-rows",
        apply=lambda conn: (ran.append(1), _mark_rows(conn)),
        is_applied=_rows_marked,
    )

    def no_copy(source: Path, target: Path) -> None:
        pass

    with pytest.raises(conversion.ConversionRefused, match="backup"):
        conversion.run_step(old_db, step, today=DAY, backup=no_copy)

    assert ran == []
    assert _dump(old_db) == before


def test_conversion_refuses_when_the_backup_copy_differs_in_size(old_db):
    before = _dump(old_db)

    def short_copy(source: Path, target: Path) -> None:
        target.write_bytes(source.read_bytes()[:-512])

    with pytest.raises(conversion.ConversionRefused, match="size"):
        conversion.run_step(old_db, MARK, today=DAY, backup=short_copy)

    assert _dump(old_db) == before
    # The bad copy is not left under the name a good backup would have.
    assert [name for name in _files(old_db) if name.endswith(".bak")] == []


def test_a_refused_bad_copy_does_not_take_the_first_backup_name_of_the_day(old_db):
    def short_copy(source: Path, target: Path) -> None:
        target.write_bytes(source.read_bytes()[:-512])

    with pytest.raises(conversion.ConversionRefused, match="size"):
        conversion.run_step(old_db, MARK, today=DAY, backup=short_copy)

    report = conversion.run_step(old_db, MARK, today=DAY)

    assert Path(report["backup"]).name == "ledger.db.pre-mark-rows-20260314.bak"


def test_conversion_refuses_a_database_that_is_not_there(tmp_path):
    with pytest.raises(conversion.ConversionRefused):
        conversion.run_step(tmp_path / "absent.db", MARK, today=DAY)

    assert list(tmp_path.iterdir()) == []


# --- P12 replay: each conversion step run twice changes nothing ----------


def test_step_run_twice_changes_nothing(old_db):
    conversion.run_step(old_db, MARK, today=DAY)
    after_first = _dump(old_db)
    files_after_first = _files(old_db)

    second = conversion.run_step(old_db, MARK, today=DAY)

    assert second["status"] == "already-applied"
    assert second["backup"] is None
    assert _dump(old_db) == after_first
    assert _files(old_db) == files_after_first


# --- counts and per-account totals, before and after ---------------------


def test_report_states_row_counts_and_account_totals_before_and_after(old_db):
    report = conversion.run_step(old_db, MARK, today=DAY)

    assert report["before"]["rows"]["transactions"] == 4
    assert report["before"]["rows"]["accounts"] == 2
    assert report["before"]["account_totals_cents"] == {1: 5235, 2: -285010}
    assert report["after"] == report["before"]


def test_step_that_loses_a_row_fails_and_leaves_the_database_as_it_was(old_db):
    before = _dump(old_db)

    def lose_a_row(conn):
        _mark_rows(conn)
        conn.execute("DELETE FROM transactions WHERE description = 'BOOK SHOP'")

    step = conversion.Step(name="lossy", apply=lose_a_row, is_applied=_rows_marked)

    with pytest.raises(conversion.ConversionFailed, match="transactions"):
        conversion.run_step(old_db, step, today=DAY)

    assert _dump(old_db) == before


def test_step_that_moves_an_account_total_by_a_cent_fails(old_db):
    before = _dump(old_db)

    def shave_a_cent(conn):
        _mark_rows(conn)
        conn.execute(
            "UPDATE transactions SET amount_sgd = 12.29 WHERE description = 'CORNER CAFE'"
        )

    step = conversion.Step(name="shave", apply=shave_a_cent, is_applied=_rows_marked)

    with pytest.raises(conversion.ConversionFailed, match="account 1"):
        conversion.run_step(old_db, step, today=DAY)

    assert _dump(old_db) == before


def _add_row_on_no_statement(path: Path) -> None:
    # Foreign keys are not enforced on a plain connection, so a row can point
    # at a statement that is not there. It belongs to no account.
    conn = sqlite3.connect(str(path))
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_sgd)"
        " VALUES (999, '2026-01-15', 'STRAY ROW', 5.00)"
    )
    conn.commit()
    conn.close()


def test_rows_on_no_statement_are_totalled_under_no_account(old_db):
    _add_row_on_no_statement(old_db)

    report = conversion.run_step(old_db, MARK, today=DAY)

    assert report["before"]["account_totals_cents"] == {1: 5235, 2: -285010, None: 500}
    assert report["after"] == report["before"]


def test_step_that_moves_the_amount_of_a_row_on_no_statement_fails(old_db):
    _add_row_on_no_statement(old_db)
    before = _dump(old_db)

    def inflate(conn):
        _mark_rows(conn)
        conn.execute(
            "UPDATE transactions SET amount_sgd = 500.00 WHERE description = 'STRAY ROW'"
        )

    step = conversion.Step(name="inflate", apply=inflate, is_applied=_rows_marked)

    with pytest.raises(conversion.ConversionFailed, match="no account"):
        conversion.run_step(old_db, step, today=DAY)

    assert _dump(old_db) == before


def test_step_is_held_to_the_invariant_it_declares(old_db):
    # A step may declare its own invariant in place of "nothing moved".
    def add_a_row(conn):
        _mark_rows(conn)
        conn.execute(
            "INSERT INTO transactions (statement_id, date, description, amount_sgd, notes)"
            " VALUES (1, '2026-01-28', 'DERIVED ROW', 1.00, 'converted')"
        )

    def one_more_row(before, after):
        gained = after["rows"]["transactions"] - before["rows"]["transactions"]
        return [] if gained == 1 else [f"expected one new row, got {gained}"]

    step = conversion.Step(
        name="add-row", apply=add_a_row, is_applied=_rows_marked, invariant=one_more_row
    )
    report = conversion.run_step(old_db, step, today=DAY)
    assert report["after"]["rows"]["transactions"] == 5

    def two_more_rows(before, after):
        return ["expected two new rows"]

    conn = sqlite3.connect(str(old_db))
    conn.execute("UPDATE transactions SET notes = NULL")
    conn.commit()
    conn.close()
    before = _dump(old_db)
    failing = conversion.Step(
        name="add-row", apply=add_a_row, is_applied=_rows_marked, invariant=two_more_rows
    )
    with pytest.raises(conversion.ConversionFailed, match="expected two new rows"):
        conversion.run_step(old_db, failing, today=DAY)
    assert _dump(old_db) == before


# --- one transaction ------------------------------------------------------


def test_step_that_raises_part_way_is_rolled_back_schema_change_included(old_db):
    before = _dump(old_db)

    def half_done(conn):
        conn.execute("ALTER TABLE transactions ADD COLUMN book TEXT")
        conn.execute("UPDATE transactions SET book = 'Household'")
        raise RuntimeError("stopped half way")

    step = conversion.Step(name="half", apply=half_done, is_applied=lambda conn: False)

    with pytest.raises(conversion.ConversionFailed, match="half"):
        conversion.run_step(old_db, step, today=DAY)

    assert _dump(old_db) == before


def test_step_that_commits_on_its_own_fails_and_nothing_is_kept(old_db):
    before = _dump(old_db)

    def commits(conn):
        _mark_rows(conn)
        conn.execute("COMMIT")

    step = conversion.Step(name="commits", apply=commits, is_applied=_rows_marked)

    with pytest.raises(conversion.ConversionFailed, match="transaction"):
        conversion.run_step(old_db, step, today=DAY)

    assert _dump(old_db) == before


def test_step_that_commits_and_begins_again_cannot_keep_an_unchecked_change(old_db):
    # Without the refusal the runner would see an open transaction, check only
    # what came after the step's own BEGIN, and report a rollback that could
    # not undo the committed part.
    before = _dump(old_db)

    def commits_then_begins(conn):
        conn.execute(
            "UPDATE transactions SET amount_sgd = 99.00 WHERE description = 'CORNER CAFE'"
        )
        conn.execute("COMMIT")
        conn.execute("BEGIN")
        _mark_rows(conn)

    step = conversion.Step(
        name="commit-begin", apply=commits_then_begins, is_applied=_rows_marked
    )

    with pytest.raises(conversion.ConversionFailed, match="transaction"):
        conversion.run_step(old_db, step, today=DAY)

    assert _dump(old_db) == before


def test_step_that_uses_executescript_fails_and_nothing_is_kept(old_db):
    # executescript commits the open transaction before it runs its script.
    before = _dump(old_db)

    def scripted(conn):
        conn.executescript("UPDATE transactions SET notes = 'converted';")

    step = conversion.Step(name="scripted", apply=scripted, is_applied=_rows_marked)

    with pytest.raises(conversion.ConversionFailed, match="transaction"):
        conversion.run_step(old_db, step, today=DAY)

    assert _dump(old_db) == before


def test_step_that_swallows_the_refusal_still_fails(old_db):
    before = _dump(old_db)

    def tries_quietly(conn):
        _mark_rows(conn)
        try:
            conn.execute("COMMIT")
        except sqlite3.DatabaseError:
            pass

    step = conversion.Step(name="quiet", apply=tries_quietly, is_applied=_rows_marked)

    with pytest.raises(conversion.ConversionFailed, match="transaction"):
        conversion.run_step(old_db, step, today=DAY)

    assert _dump(old_db) == before


def test_the_runner_can_still_commit_after_a_step(old_db):
    # The refusal covers the step only: the runner's own COMMIT must go through,
    # and so must a later run on a fresh connection.
    conversion.run_step(old_db, MARK, today=DAY)

    assert _notes(old_db) == ["converted"] * 4


def test_step_that_does_not_reach_its_applied_state_fails(old_db):
    # Otherwise a second run would do the work again.
    before = _dump(old_db)
    step = conversion.Step(name="noop", apply=lambda conn: None, is_applied=_rows_marked)

    with pytest.raises(conversion.ConversionFailed, match="applied"):
        conversion.run_step(old_db, step, today=DAY)

    assert _dump(old_db) == before
