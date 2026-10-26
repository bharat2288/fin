"""The rule-flow conversion step (fin-redesign 07, ADR-009), proved on a
temporary database in the shape from before it: today's schema without the
two columns it adds to merchant_rules, its history triggers recording every
other column. Every name, wording and figure is invented."""

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import book_type
import conversion
import convert_account_kinds
import convert_flow_rules
import convert_rule_flow
import db
import history

DAY = date(2026, 10, 24)
ADDED = ("flow", "other_side_id")


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
    """Today's schema without the two rule columns this step adds."""
    lines = history.without_history(db.SCHEMA_PATH.read_text()).splitlines()
    kept = [line for line in lines
            if not line.strip().startswith("flow TEXT")
            and not line.strip().startswith("other_side_id INTEGER REFERENCES accounts(id),  -- the account it names")]
    assert len(kept) == len(lines) - 2
    return "\n".join(kept)


@pytest.fixture
def old(tmp_path: Path) -> Path:
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(old_schema())
    for statement in history.schema_statements(leave_out={"merchant_rules": ADDED}):
        conn.execute(statement)
    conn.execute("INSERT INTO accounts (id, name, short_name, type) VALUES (1, 'Sample Bank 0002', 'b', 'bank')")
    conn.execute("INSERT INTO statements (id, account_id, statement_date) VALUES (1, 1, '2026-08-31')")
    conn.execute("INSERT INTO services (id, name) VALUES (1, 'Sample Motor Finance')")
    conn.execute("INSERT INTO merchant_rules (id, pattern, service_id) VALUES (1, 'HPR900001X', 1)")
    conn.executemany(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, service_id, flow_type)"
        " VALUES (1, ?, ?, ?, 1, ?)",
        [("2026-08-03", "HP HPR900001X SAMPLE 06", 185_000, "expense"),
         ("2026-08-14", "FAST PAYMENT SAMPLE HOLDCO", 4_821_037, "review")],
    )
    # The steps before this one have been applied.
    book_type.seed_types(conn)
    convert_account_kinds.STEP.apply(conn)
    conn.commit()
    conn.close()
    return path


def convert(path: Path, **kwargs) -> dict:
    return conversion.run_step(path, convert_rule_flow.STEP, today=DAY, **kwargs)


def test_every_rule_states_no_flow_and_no_row_or_rule_changes(old):
    rows = "SELECT id, description, amount_minor, flow_type, other_side_id FROM transactions ORDER BY id"
    rules = "SELECT id, pattern, service_id, match_type, priority FROM merchant_rules ORDER BY id"
    before_rows, before_rules = query(old, rows), query(old, rules)

    report = convert(old)

    assert report["status"] == "applied"
    assert query(old, "SELECT id, flow, other_side_id FROM merchant_rules") == [(1, None, None)]
    assert query(old, rows) == before_rows
    assert query(old, rules) == before_rules


def test_a_converted_database_has_the_shape_of_a_new_one(old, tmp_path):
    new = tmp_path / "new.db"
    conn = sqlite3.connect(str(new))
    conn.executescript(db.SCHEMA_PATH.read_text())
    conn.close()

    convert(old)

    shape = "SELECT name, type, \"notnull\", dflt_value FROM pragma_table_info('merchant_rules')"
    assert query(old, shape) == query(new, shape)
    triggers = "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'merchant_rules' ORDER BY name"
    assert query(old, triggers) == query(new, triggers)


def test_the_history_records_a_rules_flow_once_converted(old):
    convert(old)
    conn = sqlite3.connect(str(old))
    try:
        entry = history.open_entry(conn, history.VIA_APP, history.APP_ACTOR)
        conn.execute("UPDATE merchant_rules SET flow = 'movement' WHERE id = 1")
        history.close_entry(conn, entry)
        conn.commit()
        before, after = conn.execute(
            "SELECT before, after FROM change_rows WHERE entry_id = ?", (entry,)).fetchone()
    finally:
        conn.close()
    assert (json.loads(before)["flow"], json.loads(after)["flow"]) == (None, "movement")


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
    assert Path(convert(old)["backup"]).name == "ledger.db.pre-rule-flow-20261024.bak"


def test_the_app_will_not_start_on_a_database_from_before_the_step(old, monkeypatch):
    before = dump(old)
    monkeypatch.setattr(db, "DB_PATH", old)

    with pytest.raises(db.DatabaseNotConverted, match="convert_rule_flow.py"):
        db.init_db()

    assert dump(old) == before


def test_the_app_starts_once_converted(old, monkeypatch):
    convert(old)
    # And the step after it (fin-redesign 08).
    assert conversion.run_step(old, convert_flow_rules.STEP, today=DAY)["status"] == "applied"
    monkeypatch.setattr(db, "DB_PATH", old)

    db.init_db()

    assert query(old, "SELECT id, pattern, flow FROM merchant_rules WHERE id = 1") == [(1, "HPR900001X", None)]


def test_the_step_runs_after_retire_single_mark_in_the_chain():
    names = [name for name, _ in conversion.CHAIN]
    assert names.index(convert_rule_flow.NAME) > names.index("retire-single-mark")
    assert conversion.chain_step(dict(conversion.CHAIN)[convert_rule_flow.NAME]) is convert_rule_flow.STEP


def test_the_command(old, capsys, tmp_path):
    assert convert_rule_flow.main([str(old)]) == 0
    assert "step rule-flow: applied" in capsys.readouterr().out
    assert convert_rule_flow.main([str(old)]) == 0
    assert "already-applied" in capsys.readouterr().out
    assert convert_rule_flow.main([]) == 2
    assert convert_rule_flow.main([str(tmp_path / "missing.db")]) == 1
