"""Retiring the float amounts, proved on a temporary database in the old shape.

The step runs after the minor-units step on a database built from the frozen
copy of the schema as it was before either (schema_before_minor_units.sql). It
drops the float amount from rows and the float thresholds from rules; the
whole cents beside them are what is left. Every figure, merchant and name here
is invented.
"""

import dataclasses
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import app as fin_app
import conversion
import convert_account_kinds
import convert_minor_units
import convert_movements
import convert_printed_statements
import db
import retire_float_amounts

DAY = date(2026, 3, 14)
OLD_SCHEMA = Path(__file__).parent / "schema_before_minor_units.sql"

FLOAT_COLUMNS = (
    ("transactions", "amount_sgd"),
    ("merchant_rules", "min_amount"),
    ("merchant_rules", "max_amount"),
)

# Each row's float amount and the whole cents it is.
#   id: (statement, float amount, cents, flow)
ROWS = {
    1: (1, 12.30, 1230, "expense"),
    2: (1, 40.05, 4005, "expense"),
    # What a float sum leaves behind: stored as 0.30000000000000004.
    3: (1, 0.1 + 0.2, 30, "expense"),
    4: (1, -19.99, -1999, "refund"),
    5: (2, 0.29, 29, "expense"),
    6: (2, -3100.10, -310010, "income"),
    7: (2, 4.35, 435, "expense"),
    # A row whose statement is gone: it belongs to no account.
    8: (99, 5.00, 500, "expense"),
}
CARD_TOTAL = 1230 + 4005 + 30 - 1999   # 3266
BANK_TOTAL = 29 - 310010 + 435         # -309546
TOTALS = {1: CARD_TOTAL, 2: BANK_TOTAL, None: 500}

# Rule thresholds: id -> (min, max) as floats, and as whole cents.
RULES = {
    1: ((None, None), (None, None)),
    2: ((100.00, None), (10000, None)),
    3: ((0.1 + 0.2, 4.35), (30, 435)),
}


def run(path: Path, sql: str, params=()) -> int:
    conn = sqlite3.connect(str(path))
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


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


def files(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir())


def columns(path: Path, table: str) -> list[str]:
    return [r[0] for r in query(path, f"SELECT name FROM pragma_table_info('{table}')")]


@pytest.fixture
def old(tmp_path: Path) -> Path:
    """A database in the old shape: float amounts only."""
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA.read_text())
    conn.executescript("""
        INSERT INTO accounts (id, name, short_name, type) VALUES
            (1, 'Sample Card 0001', 'Sample-0001', 'credit_card'),
            (2, 'Sample Bank 0002', 'Sample-0002', 'bank');
        INSERT INTO statements (id, account_id, statement_date) VALUES
            (1, 1, '2026-01-31'), (2, 2, '2026-01-31');
        INSERT INTO services (id, name) VALUES (1, 'Sample Cafe'), (2, 'Sample Landlord');
    """)
    conn.executemany(
        "INSERT INTO transactions (id, statement_id, date, description, amount_sgd, flow_type,"
        " amount_foreign, currency_foreign) VALUES (?, ?, '2026-01-10', ?, ?, ?, ?, ?)",
        [
            (row_id, statement, f"SAMPLE ROW {row_id}", amount, flow,
             *((29.50, "USD") if row_id == 2 else (None, None)))
            for row_id, (statement, amount, _, flow) in ROWS.items()
        ],
    )
    conn.executemany(
        "INSERT INTO merchant_rules (id, pattern, service_id, min_amount, max_amount)"
        " VALUES (?, ?, ?, ?, ?)",
        [(rule_id, f"SAMPLE RULE {rule_id}", 1, *floats) for rule_id, (floats, _) in RULES.items()],
    )
    conn.commit()
    conn.close()
    return path


def to_cents(path: Path) -> dict:
    return conversion.run_step(path, convert_minor_units.STEP, today=DAY)


def retire(path: Path, step: conversion.Step = retire_float_amounts.STEP, **kwargs) -> dict:
    return conversion.run_step(path, step, today=DAY, **kwargs)


def spoiled(spoil) -> conversion.Step:
    """The real step, with something done wrong after its own work."""

    def apply(conn):
        retire_float_amounts.STEP.apply(conn)
        spoil(conn)

    return dataclasses.replace(retire_float_amounts.STEP, apply=apply)


# --- what the step removes, and what it keeps ---------------------------------


def test_the_float_amounts_are_dropped_and_the_whole_cents_stay(old):
    to_cents(old)

    report = retire(old)

    assert report["status"] == "applied"
    for table, column in FLOAT_COLUMNS:
        assert column not in columns(old, table), (table, column)
    assert query(old, "SELECT id, amount_minor FROM transactions ORDER BY id") == [
        (row_id, cents) for row_id, (_, _, cents, _) in ROWS.items()
    ]
    assert query(
        old, "SELECT id, min_amount_minor, max_amount_minor FROM merchant_rules ORDER BY id"
    ) == [(rule_id, *cents) for rule_id, (_, cents) in RULES.items()]
    # Nothing left in the schema names a float amount.
    for (sql,) in query(old, "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"):
        assert "amount_sgd" not in sql and "min_amount " not in sql and "max_amount " not in sql, sql


def test_every_other_value_is_kept(old):
    to_cents(old)
    kept = ("SELECT id, statement_id, date, description, amount_foreign, currency_foreign,"
            " service_id, is_one_off, cat_source, flow_type, flow_type_manual, notes, created_at,"
            " book, type_id, amount_minor FROM transactions ORDER BY id")
    kept_rules = ("SELECT id, pattern, service_id, match_type, confidence, priority, created_at,"
                  " book_override, type_override_id, min_amount_minor, max_amount_minor"
                  " FROM merchant_rules ORDER BY id")
    rows_before, rules_before = query(old, kept), query(old, kept_rules)

    retire(old)

    assert query(old, kept) == rows_before
    assert query(old, kept_rules) == rules_before
    assert query(old, "SELECT amount_foreign, currency_foreign FROM transactions WHERE id = 2") == [
        (29.50, "USD")
    ]


# --- conservation: row count and each account's total, to the cent -------------


def test_dropping_the_floats_keeps_row_count_and_each_accounts_total_to_the_cent(old):
    to_cents(old)
    counts = {
        table: query(old, f"SELECT COUNT(*) FROM {table}")[0][0]
        for table in ("transactions", "merchant_rules", "accounts", "statements", "services")
    }

    report = retire(old)

    # The floats that are about to go, each rounded to its cent, per account ...
    assert report["before"]["account_totals_cents"] == TOTALS
    # ... are what the whole cents that stay add up to.
    assert report["before"]["account_totals_minor"] == TOTALS
    assert report["after"]["account_totals_minor"] == TOTALS
    assert sum(report["after"]["account_totals_minor"].values()) == CARD_TOTAL + BANK_TOTAL + 500
    for table, n in counts.items():
        assert report["after"]["rows"][table] == n, table
        assert query(old, f"SELECT COUNT(*) FROM {table}")[0][0] == n, table
    assert {
        (None if account is None else account): total
        for account, total in query(
            old,
            "SELECT s.account_id, SUM(t.amount_minor) FROM transactions t"
            " LEFT JOIN statements s ON s.id = t.statement_id GROUP BY s.account_id",
        )
    } == TOTALS


def test_an_integer_amount_moved_by_a_cent_fails_and_nothing_is_dropped(old):
    to_cents(old)
    before = dump(old)
    step = spoiled(
        lambda conn: conn.execute("UPDATE transactions SET amount_minor = 1229 WHERE id = 1")
    )

    with pytest.raises(conversion.ConversionFailed, match="account 1"):
        retire(old, step)

    assert dump(old) == before


def test_two_errors_that_cancel_across_accounts_still_fail(old):
    to_cents(old)
    before = dump(old)

    def move_a_cent(conn):
        conn.execute("UPDATE transactions SET amount_minor = amount_minor + 1 WHERE id = 1")
        conn.execute("UPDATE transactions SET amount_minor = amount_minor - 1 WHERE id = 5")

    with pytest.raises(conversion.ConversionFailed, match="account 1.*account 2"):
        retire(old, spoiled(move_a_cent))

    assert dump(old) == before


def test_a_lost_row_fails(old):
    to_cents(old)
    before = dump(old)
    step = spoiled(lambda conn: conn.execute("DELETE FROM transactions WHERE id = 3"))

    with pytest.raises(conversion.ConversionFailed, match="row count of transactions"):
        retire(old, step)

    assert dump(old) == before


def test_a_row_left_without_its_amount_fails(old):
    to_cents(old)
    before = dump(old)
    step = spoiled(
        lambda conn: conn.execute("UPDATE transactions SET amount_minor = NULL WHERE id = 8")
    )

    with pytest.raises(conversion.ConversionFailed, match="1 row"):
        retire(old, step)

    assert dump(old) == before


def test_a_rule_threshold_moved_by_a_cent_fails(old):
    to_cents(old)
    before = dump(old)
    step = spoiled(
        lambda conn: conn.execute("UPDATE merchant_rules SET max_amount_minor = 434 WHERE id = 3")
    )

    with pytest.raises(conversion.ConversionFailed, match="max_amount_minor"):
        retire(old, step)

    assert dump(old) == before


# --- order: the floats go only once every amount is held in whole cents ----------


def test_it_will_not_run_before_the_minor_units_step(old):
    before = dump(old)

    with pytest.raises(conversion.ConversionFailed, match="MinorUnitsNotApplied"):
        retire(old)

    assert dump(old) == before
    for table, column in FLOAT_COLUMNS:
        assert column in columns(old, table)


def test_it_will_not_run_while_a_row_has_no_whole_cents_yet(old):
    to_cents(old)
    # A row the old app added since: a float amount and no integer.
    run(
        old,
        "INSERT INTO transactions (id, statement_id, date, description, amount_sgd)"
        " VALUES (20, 1, '2026-02-02', 'SAMPLE LATER ROW', 8.80)",
    )
    before = dump(old)

    with pytest.raises(conversion.ConversionFailed, match="MinorUnitsNotApplied"):
        retire(old)
    assert dump(old) == before

    to_cents(old)
    assert retire(old)["status"] == "applied"
    assert query(old, "SELECT amount_minor FROM transactions WHERE id = 20") == [(880,)]


def test_it_will_not_run_while_a_rows_whole_cents_disagree_with_its_float(old):
    to_cents(old)
    run(old, "UPDATE transactions SET amount_minor = 1231 WHERE id = 1")
    before = dump(old)

    with pytest.raises(conversion.ConversionFailed, match="MinorUnitsNotApplied"):
        retire(old)

    assert dump(old) == before


def test_it_will_not_guess_at_a_database_with_only_some_of_the_floats_gone(old):
    to_cents(old)
    run(old, "ALTER TABLE merchant_rules DROP COLUMN max_amount")
    before = dump(old)

    with pytest.raises(conversion.ConversionFailed, match="PartlyRetired"):
        retire(old)

    assert dump(old) == before


# --- replay and refusal ---------------------------------------------------------


def test_step_run_twice_changes_nothing(old):
    to_cents(old)
    retire(old)
    after_first = dump(old)
    files_after_first = files(old)

    second = retire(old)

    assert second["status"] == "already-applied"
    assert second["backup"] is None
    assert dump(old) == after_first
    assert files(old) == files_after_first
    # And the step before it reads as done on a database with no floats left.
    assert to_cents(old)["status"] == "already-applied"
    assert files(old) == files_after_first


def test_it_runs_behind_its_backup_and_refuses_without_one(old):
    to_cents(old)
    before = dump(old)

    with pytest.raises(conversion.ConversionRefused, match="backup"):
        retire(old, backup=lambda source, target: None)
    assert dump(old) == before

    report = retire(old)
    assert Path(report["backup"]).name == "ledger.db.pre-retire-float-amounts-20260314.bak"
    assert dump(Path(report["backup"])) == before


def test_a_new_database_reads_as_already_retired(temp_db):
    report = conversion.run_step(temp_db, retire_float_amounts.STEP, today=DAY)

    assert report["status"] == "already-applied"
    for table, column in FLOAT_COLUMNS:
        assert column not in columns(temp_db, table), (table, column)


def test_a_database_through_both_steps_has_the_shape_of_a_new_one(old, tmp_path):
    # The row planted on a statement that is not there, and nothing else.
    dangling = query(old, "PRAGMA foreign_key_check")
    assert dangling == [("transactions", 8, "statements", 1)]
    to_cents(old)
    retire(old)
    # And through the two steps that come after them, which add to the shape.
    conversion.run_step(old, convert_account_kinds.STEP, today=DAY)
    conversion.run_step(old, convert_movements.STEP, today=DAY)
    conversion.run_step(old, convert_printed_statements.STEP, today=DAY)
    fresh = tmp_path / "fresh.db"
    conn = sqlite3.connect(str(fresh))
    conn.executescript(db.SCHEMA_PATH.read_text())
    conn.close()
    # What the next start does to a converted database: the schema script's
    # CREATE IF NOT EXISTS adds any table no step makes (the suggestion
    # answers) and leaves every table the steps shaped as it is.
    conn = sqlite3.connect(str(old))
    conn.executescript(db.SCHEMA_PATH.read_text())
    conn.close()

    def shape(path: Path) -> dict:
        tables = [r[0] for r in query(
            path, "SELECT name FROM sqlite_master WHERE type = 'table'"
                  " AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        return {
            "tables": {
                t: query(path, f"SELECT name, type, \"notnull\", dflt_value, pk"
                               f" FROM pragma_table_info('{t}') ORDER BY cid")
                for t in tables
            },
            "foreign_keys": {
                t: sorted(query(path, f"SELECT \"table\", \"from\", \"to\""
                                      f" FROM pragma_foreign_key_list('{t}')"))
                for t in tables
            },
            "indexes": sorted(query(
                path, "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'"
                      " AND name NOT LIKE 'sqlite_%'"
            )),
        }

    assert shape(old) == shape(fresh)
    assert query(old, "PRAGMA foreign_key_check") == dangling


# --- the default measure of the runner, once the float is gone --------------------


def test_a_later_step_is_still_held_to_each_accounts_total(old):
    """A step with no measure of its own is measured by the runner's default,
    which totals the whole cents when there is no float left to total."""
    to_cents(old)
    retire(old)

    marked = conversion.Step(
        name="mark-rows",
        apply=lambda conn: conn.execute("UPDATE transactions SET notes = 'seen'"),
        is_applied=lambda conn: conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE notes IS NOT 'seen'"
        ).fetchone()[0] == 0,
    )
    report = conversion.run_step(old, marked, today=DAY)
    assert report["before"]["account_totals_cents"] == TOTALS
    assert report["after"]["account_totals_cents"] == TOTALS

    before = dump(old)
    moves_a_cent = conversion.Step(
        name="move-a-cent",
        apply=lambda conn: conn.execute(
            "UPDATE transactions SET amount_minor = amount_minor + 1, notes = 'moved' WHERE id = 1"
        ),
        is_applied=lambda conn: conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE notes = 'moved'"
        ).fetchone()[0] == 1,
    )
    with pytest.raises(conversion.ConversionFailed, match="total of account 1 changed"):
        conversion.run_step(old, moves_a_cent, today=DAY)
    assert dump(old) == before


# --- the command ---------------------------------------------------------------


def test_the_command_reports_counts_and_totals(old, capsys):
    to_cents(old)

    assert retire_float_amounts.main([str(old)]) == 0

    out = capsys.readouterr().out
    assert "step retire-float-amounts: applied" in out
    assert "backup:" in out
    assert f"account 1: {CARD_TOTAL}" in out
    assert f"account 2: {BANK_TOTAL}" in out
    assert "rows on no account: 500" in out


def test_the_command_says_which_step_to_run_first(old, capsys):
    before = dump(old)

    assert retire_float_amounts.main([str(old)]) == 1

    assert "convert_minor_units.py" in capsys.readouterr().out
    assert dump(old) == before
    assert files(old) == ["ledger.db"]


def test_the_command_run_again_says_already_applied(old, capsys):
    to_cents(old)
    retire_float_amounts.main([str(old)])
    capsys.readouterr()

    assert retire_float_amounts.main([str(old)]) == 0

    assert "step retire-float-amounts: already-applied" in capsys.readouterr().out


def test_the_command_needs_a_database_path(tmp_path, capsys):
    assert retire_float_amounts.main([]) == 2
    assert retire_float_amounts.main([str(tmp_path / "missing.db")]) == 1


# --- the app on a converted database, and on one that is not ---------------------


@pytest.fixture
def started_on(monkeypatch: pytest.MonkeyPatch):
    """Point the app at a database file and start it."""
    def start(path: Path):
        monkeypatch.setattr(db, "DB_PATH", path)
        db.invalidate_rules_cache()
        db.init_db()
        fin_app.app.config["TESTING"] = True
        return fin_app.app.test_client()

    yield start
    db.invalidate_rules_cache()


def test_the_app_will_not_start_on_a_database_that_still_carries_float_amounts(old, started_on):
    before = dump(old)

    with pytest.raises(db.DatabaseNotConverted, match="retire_float_amounts.py"):
        started_on(old)
    assert dump(old) == before

    # Half way is not enough either.
    to_cents(old)
    half_way = dump(old)
    with pytest.raises(db.DatabaseNotConverted, match="convert_minor_units.py"):
        started_on(old)
    assert dump(old) == half_way


def test_the_app_shows_a_converted_database_the_figures_its_floats_stated(old, started_on):
    to_cents(old)
    retire(old)
    # The app starts only once the accounts have their kinds and the rows can
    # name an other side: the two steps that follow the money steps.
    conversion.run_step(old, convert_account_kinds.STEP, today=DAY)
    conversion.run_step(old, convert_movements.STEP, today=DAY)
    conversion.run_step(old, convert_printed_statements.STEP, today=DAY)

    client = started_on(old)

    rows = {
        t["id"]: t["amount_sgd"]
        for t in client.get("/api/transactions?per_page=50").get_json()["transactions"]
    }
    # Row 8 has no statement, so no account, and is on no list.
    assert rows == {1: 12.3, 2: 40.05, 3: 0.3, 4: -19.99, 5: 0.29, 6: -3100.1, 7: 4.35}
    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-01").get_json()
    # Spending and refunds on the two accounts: 12.30 + 40.05 + 0.30 - 19.99 + 0.29 + 4.35.
    assert cards["spend"] == 37.3
    assert cards["household"] == 37.3
    monthly = client.get("/api/dashboard/monthly").get_json()
    assert monthly == {"2026-01": {fin_app.NO_TYPE_LABEL: 37.3}}
    rules = {r["id"]: (r["min_amount"], r["max_amount"]) for r in client.get("/api/rules").get_json()
             if r["id"] in RULES}
    assert rules == {1: (None, None), 2: (100.0, None), 3: (0.3, 4.35)}
