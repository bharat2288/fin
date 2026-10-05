"""The printed-statements conversion step, proved on a temporary database in
the shape from before it: today's schema without the column it adds. Every
name and figure is invented."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

import conversion
import convert_printed_statements
import db

DAY = date(2026, 10, 5)


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


def old_schema() -> str:
    """Today's schema without the column this step adds."""
    lines = db.SCHEMA_PATH.read_text().splitlines()
    kept = [line for line in lines if not line.strip().startswith("printed ")]
    assert len(kept) == len(lines) - 1
    return "\n".join(kept)


@pytest.fixture
def old(tmp_path: Path) -> Path:
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(old_schema())
    conn.execute("INSERT INTO accounts (id, name, short_name, type) VALUES (1, 'Sample Card 0001', 'c', 'card')")
    conn.executemany(
        "INSERT INTO statements (id, account_id, statement_date) VALUES (?, 1, ?)",
        [(1, "2026-07-01"), (2, "2026-08-01")],
    )
    conn.executemany(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type)"
        " VALUES (?, ?, 'SAMPLE GROCER', ?, 'expense')",
        [(1, "2026-07-12", 1_000), (2, "2026-08-12", 2_550)],
    )
    conn.commit()
    conn.close()
    return path


def convert(path: Path, **kwargs) -> dict:
    return conversion.run_step(path, convert_printed_statements.STEP, today=DAY, **kwargs)


def test_every_existing_record_is_a_month_record_and_no_row_moves(old):
    rows = "SELECT id, statement_id, date, amount_minor FROM transactions ORDER BY id"
    before = query(old, rows)

    report = convert(old)

    assert report["status"] == "applied"
    assert report["before"] == report["after"]
    assert query(old, "SELECT id, printed FROM statements ORDER BY id") == [(1, 0), (2, 0)]
    assert query(old, rows) == before


def test_a_converted_database_has_the_shape_of_a_new_one(old, tmp_path):
    new = tmp_path / "new.db"
    conn = sqlite3.connect(str(new))
    conn.executescript(db.SCHEMA_PATH.read_text())
    conn.close()

    convert(old)

    shape = "SELECT name, type, \"notnull\", dflt_value FROM pragma_table_info('statements')"
    assert query(old, shape) == query(new, shape)


def test_run_twice_changes_nothing_and_writes_one_backup(old):
    convert(old)
    after_first = dump(old)

    again = convert(old)

    assert again["status"] == "already-applied"
    assert dump(old) == after_first
    assert len(list(old.parent.glob("*.bak"))) == 1


def test_it_refuses_without_its_backup(old):
    before = dump(old)

    with pytest.raises(conversion.ConversionRefused, match="backup copy is missing"):
        convert(old, backup=lambda source, target: None)

    assert dump(old) == before
    assert Path(convert(old)["backup"]).name == "ledger.db.pre-printed-statements-20261005.bak"


def test_the_app_will_not_start_on_a_database_from_before_the_step(old, monkeypatch):
    before = dump(old)
    monkeypatch.setattr(db, "DB_PATH", old)

    with pytest.raises(db.DatabaseNotConverted, match="convert_printed_statements.py"):
        db.init_db()

    assert dump(old) == before


def test_the_command(old, capsys, tmp_path):
    assert convert_printed_statements.main([str(old)]) == 0
    assert "step printed-statements: applied" in capsys.readouterr().out
    assert convert_printed_statements.main([str(old)]) == 0
    assert "already-applied" in capsys.readouterr().out
    assert convert_printed_statements.main([]) == 2
    assert convert_printed_statements.main([str(tmp_path / "missing.db")]) == 1
