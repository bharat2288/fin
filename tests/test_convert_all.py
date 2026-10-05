"""The one command that converts a database: every step, in order, stopping
at the first that does not pass; and a step that will not run while a step
before it has not been applied. Every figure, merchant and name is invented.
"""

import dataclasses
import sqlite3
from pathlib import Path

import pytest

import app as fin_app
import conversion
import convert_account_kinds
import convert_all
import db
import retire_float_amounts

OLD_SCHEMA = Path(__file__).parent / "schema_before_book_and_type.sql"
STEP_NAMES = [name for name, _ in conversion.CHAIN]


def query(path: Path, sql: str, params=()) -> list[tuple]:
    conn = sqlite3.connect(str(path))
    try:
        return [tuple(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def dump(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def backups(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir() if p.name.endswith(".bak"))


def columns(path: Path, table: str) -> set[str]:
    return {r[1] for r in query(path, f"PRAGMA table_info({table})")}


@pytest.fixture
def old(tmp_path: Path) -> Path:
    """A database in the oldest shape, with half-cent amounts among its rows
    and a row with no flow."""
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA.read_text())
    conn.executescript("""
        INSERT INTO categories (id, name, parent_id) VALUES (1, 'Dining', NULL), (2, 'Other', NULL);
        INSERT INTO accounts (id, name, short_name, type) VALUES
            (1, 'Sample Card 0001', 'Sample-0001', 'credit_card'),
            (2, 'Sample Bank 0002', 'Sample-0002', 'bank');
        INSERT INTO statements (id, account_id, statement_date) VALUES
            (1, 1, '2026-01-01'), (2, 2, '2026-01-01');
        INSERT INTO services (id, name, category_id) VALUES (1, 'Sample Cafe', 1);
        INSERT INTO transactions
            (id, statement_id, date, description, amount_sgd, category_id, service_id,
             cat_source, flow_type)
        VALUES
            (1, 1, '2026-01-10', 'SAMPLE CAFE', 12.30, 1, 1, 'service_default', 'expense'),
            (2, 1, '2026-01-11', 'SAMPLE CAFE', 12.345, 1, 1, 'service_default', 'expense'),
            (3, 1, '2026-01-12', 'CORNER STALL', 0.005, 2, NULL, 'auto', 'expense'),
            (4, 2, '2026-01-13', 'SAMPLE STALL', 2.675, 2, NULL, 'auto', 'expense'),
            (5, 2, '2026-01-14', 'SAMPLE REFUND', -0.015, 2, NULL, 'auto', 'expense'),
            (6, 2, '2026-01-15', 'SAMPLE SHOP', 7.50, 2, NULL, 'auto', NULL);
    """)
    conn.commit()
    conn.close()
    return path


def test_the_command_runs_every_step_in_order(old, capsys):
    assert convert_all.main([str(old)]) == 0

    out = capsys.readouterr().out
    shown = [line for line in out.splitlines() if line.startswith("--- step ")]
    assert [line.split(": ")[1].split(" ")[0] for line in shown] == STEP_NAMES
    assert f"all {len(STEP_NAMES)} steps applied" in out
    # Each step that did work wrote its backup first; one with nothing to do
    # (this database has no row the movements step reclassifies) wrote none.
    assert len(backups(old)) == out.count(": applied") >= len(STEP_NAMES) - 1
    conn = sqlite3.connect(str(old))
    try:
        assert conversion.not_applied(conn) == []
    finally:
        conn.close()
    # The half cents went one way, by one rule: half-even on the decimal reading.
    assert query(old, "SELECT id, amount_minor FROM transactions ORDER BY id") == [
        (1, 1230), (2, 1234), (3, 0), (4, 268), (5, -2), (6, 750),
    ]
    # And the row with no flow has one.
    assert query(old, "SELECT COUNT(*) FROM transactions WHERE flow_type IS NULL") == [(0,)]


def test_run_again_it_changes_nothing(old, capsys):
    convert_all.main([str(old)])
    before, written = dump(old), backups(old)
    capsys.readouterr()

    assert convert_all.main([str(old)]) == 0

    out = capsys.readouterr().out
    assert out.count("already-applied") == len(STEP_NAMES)
    assert dump(old) == before
    assert backups(old) == written


def test_a_failed_step_stops_the_steps_after_it(old, capsys, monkeypatch):
    monkeypatch.setattr(
        retire_float_amounts,
        "STEP",
        dataclasses.replace(retire_float_amounts.STEP, invariant=lambda before, after: ["forced"]),
    )

    assert convert_all.main([str(old)]) == 1

    out = capsys.readouterr().out
    assert f"stopped at step 4 of {len(STEP_NAMES)}: retire-float-amounts" in out
    assert "not run: account-kinds, movements, printed-statements, flows" in out
    assert "--- step 5 of" not in out
    # Steps 5 to 7 left nothing behind: no owner, no printed mark, no backup.
    assert "owner" not in columns(old, "accounts")
    assert "printed" not in columns(old, "statements")
    assert not [name for name in backups(old) if "account-kinds" in name or "printed" in name]
    # The float amounts are still there: step 4 was rolled back.
    assert "amount_sgd" in columns(old, "transactions")

    # Fixed, the same command picks up where it stopped.
    monkeypatch.undo()
    capsys.readouterr()
    assert convert_all.main([str(old)]) == 0
    assert "amount_sgd" not in columns(old, "transactions")
    assert "owner" in columns(old, "accounts")


def test_a_step_will_not_run_while_a_step_before_it_has_not_been_applied(old):
    before = dump(old)

    with pytest.raises(conversion.ConversionRefused) as refused:
        conversion.run_step(old, convert_account_kinds.STEP)

    message = str(refused.value)
    for earlier in ("book-and-type", "retire-categories", "minor-units", "retire-float-amounts"):
        assert earlier in message
    assert "python convert_all.py" in message
    assert dump(old) == before
    assert backups(old) == []


def test_the_steps_own_command_says_so_too(old, capsys):
    before = dump(old)

    assert convert_account_kinds.main([str(old)]) == 1

    assert "runs after book-and-type" in capsys.readouterr().out
    assert dump(old) == before


def test_the_command_needs_a_database_path(tmp_path, capsys):
    assert convert_all.main([]) == 2
    assert convert_all.main([str(tmp_path / "missing.db")]) == 1


def test_a_start_on_an_unconverted_database_says_every_step_and_the_one_command(
    old, monkeypatch, capsys
):
    monkeypatch.setattr(db, "DB_PATH", old)
    before = dump(old)

    assert fin_app.main([]) == 1

    err = capsys.readouterr().err
    assert "Traceback" not in err
    listed = [line.split(". ", 1)[1].split(" ")[0] for line in err.splitlines()
              if line.startswith("  ") and ". " in line]
    assert listed == STEP_NAMES
    assert f"python convert_all.py {old}" in err
    assert dump(old) == before
    assert backups(old) == []


def test_part_way_through_it_names_only_the_steps_left(old, monkeypatch, capsys):
    monkeypatch.setattr(
        retire_float_amounts,
        "STEP",
        dataclasses.replace(retire_float_amounts.STEP, invariant=lambda before, after: ["forced"]),
    )
    convert_all.main([str(old)])
    monkeypatch.undo()
    monkeypatch.setattr(db, "DB_PATH", old)
    capsys.readouterr()

    assert fin_app.main([]) == 1

    err = capsys.readouterr().err
    listed = [line.split(". ", 1)[1].split(" ")[0] for line in err.splitlines()
              if line.startswith("  ") and ". " in line]
    assert listed == STEP_NAMES[STEP_NAMES.index("retire-float-amounts"):]
