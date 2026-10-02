"""Amounts to whole minor units, proved on a temporary database in the old shape.

The step adds an integer amount beside the float every row still carries and
fills it. The database is built from the frozen copy of the schema as it was
before the step (schema_before_minor_units.sql). Every figure, merchant and
name here is invented.
"""

import dataclasses
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import conversion
import convert_minor_units

DAY = date(2026, 3, 14)
OLD_SCHEMA = Path(__file__).parent / "schema_before_minor_units.sql"

# Each row's float amount and the whole cents it must become.
#   id: (statement, float amount, cents)
ROWS = {
    1: (1, 12.30, 1230),
    2: (1, 40.25, 4025),
    # What a float sum leaves behind: stored as 0.30000000000000004.
    3: (1, 0.1 + 0.2, 30),
    4: (1, -19.99, -1999),
    5: (1, 67.43, 6743),
    6: (1, 1234.56, 123456),
    # 0.29 * 100 is 28.999999999999996 as a float.
    7: (2, 0.29, 29),
    8: (2, -3100.10, -310010),
    # 4.35 * 100 is 434.99999999999994 as a float; truncating gives 434.
    9: (2, 4.35, 435),
    # Exactly half a cent as a float: half-even gives 12, half-up would give 13.
    10: (2, 0.125, 12),
    11: (2, -0.375, -38),
    # A row whose statement is gone: it belongs to no account.
    12: (99, 5.00, 500),
}
CARD_TOTAL = 1230 + 4025 + 30 - 1999 + 6743 + 123456   # 133485
BANK_TOTAL = 29 - 310010 + 435 + 12 - 38              # -309572
TOTALS = {1: CARD_TOTAL, 2: BANK_TOTAL, None: 500}

# The foreign side of the card rows: (foreign amount, currency as stored).
FOREIGN = {
    1: (9.00, "USD"),
    2: (29.50, "U. S. DOLLAR"),
    3: (0.22, "usd"),
    4: (15.00, "AUSTRALIAN DOLLAR"),
    5: (49.00, "U.S. DOLLAR"),
    6: (30500.00, "BAHT"),
    7: (12.00, "ZORKMID"),
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
        "INSERT INTO transactions"
        " (id, statement_id, date, description, amount_sgd, amount_foreign, currency_foreign)"
        " VALUES (?, ?, '2026-01-10', ?, ?, ?, ?)",
        [
            (row_id, statement, f"SAMPLE ROW {row_id}", amount, *FOREIGN.get(row_id, (None, None)))
            for row_id, (statement, amount, _) in ROWS.items()
        ],
    )
    conn.executemany(
        "INSERT INTO merchant_rules (id, pattern, service_id, min_amount, max_amount)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (1, "SAMPLE CAFE", 1, None, None),
            (2, "SAMPLE CAFE BEANS", 1, 100.00, None),
            (3, "SAMPLE LANDLORD", 2, 0.1 + 0.2, 4.35),
            (4, "SAMPLE LANDLORD REFUND", 2, None, -19.99),
        ],
    )
    conn.commit()
    conn.close()
    return path


def convert(path: Path, step: conversion.Step = convert_minor_units.STEP, **kwargs) -> dict:
    return conversion.run_step(path, step, today=DAY, **kwargs)


def spoiled(spoil) -> conversion.Step:
    """The real step, with something done wrong after its own work."""

    def apply(conn):
        convert_minor_units.STEP.apply(conn)
        spoil(conn)

    # A wrong amount would read as not yet applied; that check is put aside so
    # the invariant alone decides.
    return dataclasses.replace(
        convert_minor_units.STEP, apply=apply, is_applied=lambda conn: False
    )


# --- every row gains its amount in whole cents -----------------------------


def test_every_row_gains_its_amount_in_whole_cents(old):
    report = convert(old)

    assert report["status"] == "applied"
    assert dict(query(old, "SELECT id, amount_minor FROM transactions")) == {
        row_id: cents for row_id, (_, _, cents) in ROWS.items()
    }
    assert {type(c) for (c,) in query(old, "SELECT amount_minor FROM transactions")} == {int}


def test_half_a_cent_rounds_to_the_even_cent_not_up(old):
    convert(old)

    assert dict(query(old, "SELECT id, amount_minor FROM transactions WHERE id IN (10, 11)")) == {
        10: 12,   # 0.125 -> 12, not 13
        11: -38,  # -0.375 -> -38
    }


def test_float_amounts_are_left_exactly_as_they_were(old):
    before = query(old, "SELECT id, amount_sgd FROM transactions ORDER BY id")

    convert(old)

    assert query(old, "SELECT id, amount_sgd FROM transactions ORDER BY id") == before


# --- P14 conservation: row count and per-account totals, to the cent -------


def test_conversion_keeps_row_count_and_each_accounts_total_to_the_cent(old):
    report = convert(old)

    before, after = report["before"], report["after"]
    assert before["rows"]["transactions"] == 12
    assert after["rows"] == before["rows"]
    # The old amounts, each rounded to its cent, per account ...
    assert before["account_totals_cents"] == TOTALS
    # ... and the new integers add up to the same, per account.
    assert after["account_totals_minor"] == TOTALS
    assert after["rows_without_minor"] == 0
    # Read from the database itself, not the report.
    assert dict(query(
        old,
        "SELECT s.account_id, SUM(t.amount_minor) FROM transactions t"
        " LEFT JOIN statements s ON s.id = t.statement_id GROUP BY s.account_id",
    )) == TOTALS
    assert sum(TOTALS.values()) == query(old, "SELECT SUM(amount_minor) FROM transactions")[0][0]


def test_before_the_step_no_row_has_an_integer_amount(old):
    report = convert(old)

    assert report["before"]["account_totals_minor"] == {}
    assert report["before"]["rows_without_minor"] == 12


def test_an_integer_amount_one_cent_out_fails_and_nothing_is_kept(old):
    before = dump(old)
    step = spoiled(
        lambda conn: conn.execute("UPDATE transactions SET amount_minor = 1229 WHERE id = 1")
    )

    with pytest.raises(conversion.ConversionFailed, match="account 1"):
        convert(old, step)

    assert dump(old) == before


def test_two_errors_that_cancel_across_accounts_still_fail(old):
    """The total over all rows is right; each account's is one cent out."""
    before = dump(old)

    def move_a_cent(conn):
        conn.execute("UPDATE transactions SET amount_minor = amount_minor + 1 WHERE id = 1")
        conn.execute("UPDATE transactions SET amount_minor = amount_minor - 1 WHERE id = 7")

    with pytest.raises(conversion.ConversionFailed, match="account 1.*account 2"):
        convert(old, spoiled(move_a_cent))

    assert dump(old) == before


def test_a_row_left_without_its_integer_amount_fails(old):
    before = dump(old)
    step = spoiled(
        lambda conn: conn.execute("UPDATE transactions SET amount_minor = NULL WHERE id = 12")
    )

    with pytest.raises(conversion.ConversionFailed, match="1 row"):
        convert(old, step)

    assert dump(old) == before


def test_a_lost_row_fails(old):
    before = dump(old)
    step = spoiled(lambda conn: conn.execute("DELETE FROM transactions WHERE id = 3"))

    with pytest.raises(conversion.ConversionFailed, match="row count of transactions"):
        convert(old, step)

    assert dump(old) == before


def test_a_changed_float_amount_fails(old):
    before = dump(old)
    step = spoiled(
        lambda conn: conn.execute(
            "UPDATE transactions SET amount_sgd = 12.31, amount_minor = 1231 WHERE id = 1"
        )
    )

    with pytest.raises(conversion.ConversionFailed, match="account 1"):
        convert(old, step)

    assert dump(old) == before


def test_an_account_in_another_currency_stops_the_step(old):
    """The old amounts are SGD by their column's name. An account that says
    otherwise is not converted by guessing."""
    run(old, "UPDATE accounts SET currency = 'INR' WHERE id = 2")
    before = dump(old)

    with pytest.raises(conversion.ConversionFailed, match="AccountNotInSgd"):
        convert(old)

    assert dump(old) == before


# --- rule amount thresholds -------------------------------------------------


def test_rule_thresholds_gain_whole_cents_and_keep_their_floats(old):
    floats = query(old, "SELECT id, min_amount, max_amount FROM merchant_rules ORDER BY id")

    convert(old)

    assert query(
        old, "SELECT id, min_amount_minor, max_amount_minor FROM merchant_rules ORDER BY id"
    ) == [(1, None, None), (2, 10000, None), (3, 30, 435), (4, None, -1999)]
    assert query(old, "SELECT id, min_amount, max_amount FROM merchant_rules ORDER BY id") == floats


def test_a_threshold_one_cent_out_fails(old):
    before = dump(old)
    step = spoiled(
        lambda conn: conn.execute("UPDATE merchant_rules SET min_amount_minor = 9999 WHERE id = 2")
    )

    with pytest.raises(conversion.ConversionFailed, match="threshold"):
        convert(old, step)

    assert dump(old) == before


# --- foreign currency names -------------------------------------------------


def test_foreign_currency_names_become_three_letter_codes(old):
    convert(old)

    assert dict(query(
        old, "SELECT id, currency_foreign FROM transactions WHERE currency_foreign IS NOT NULL"
    )) == {
        1: "USD",
        2: "USD",
        3: "USD",
        4: "AUD",
        5: "USD",
        6: "THB",
        # Not a name the step knows: left as it is, never guessed.
        7: "ZORKMID",
    }


def test_foreign_amounts_are_untouched(old):
    before = query(old, "SELECT id, amount_foreign FROM transactions ORDER BY id")

    convert(old)

    assert query(old, "SELECT id, amount_foreign FROM transactions ORDER BY id") == before
    assert dict(before)[6] == 30500.00


def test_a_changed_foreign_amount_fails(old):
    before = dump(old)
    step = spoiled(
        lambda conn: conn.execute("UPDATE transactions SET amount_foreign = 4900 WHERE id = 5")
    )

    with pytest.raises(conversion.ConversionFailed, match="foreign"):
        convert(old, step)

    assert dump(old) == before


# --- P12 replay: the step run twice changes nothing --------------------------


def test_step_run_twice_changes_nothing(old):
    convert(old)
    after_first = dump(old)
    files_after_first = files(old)

    second = convert(old)

    assert second["status"] == "already-applied"
    assert second["backup"] is None
    assert dump(old) == after_first
    assert files(old) == files_after_first


def test_rows_that_arrive_after_the_step_are_filled_by_running_it_again(old):
    """Nothing writes the integer amount yet, so an import after the step
    leaves rows without one. The step reads as not applied and fills them."""
    convert(old)
    run(
        old,
        "INSERT INTO transactions (id, statement_id, date, description, amount_sgd,"
        " amount_foreign, currency_foreign) VALUES (20, 1, '2026-02-02', 'SAMPLE LATER ROW',"
        " 8.80, 6.50, 'EURO')",
    )
    earlier = query(old, "SELECT id, amount_minor FROM transactions WHERE id < 20 ORDER BY id")

    report = convert(old)

    assert report["status"] == "applied"
    assert query(old, "SELECT amount_minor, currency_foreign FROM transactions WHERE id = 20") == [
        (880, "EUR")
    ]
    assert query(
        old, "SELECT id, amount_minor FROM transactions WHERE id < 20 ORDER BY id"
    ) == earlier
    assert report["after"]["account_totals_minor"] == {**TOTALS, 1: CARD_TOTAL + 880}


def test_a_new_database_reads_as_already_converted(temp_db):
    report = conversion.run_step(temp_db, convert_minor_units.STEP, today=DAY)

    assert report["status"] == "already-applied"


def test_schema_declares_the_integer_amounts(temp_db):
    """A database made today has the columns the step adds to an old one."""
    run(temp_db, "INSERT INTO accounts (id, name, short_name, type) VALUES (1, 'A', 'A', 'bank')")
    run(temp_db, "INSERT INTO statements (id, account_id, statement_date) VALUES (1, 1, '2026-01-31')")
    run(
        temp_db,
        "INSERT INTO transactions (statement_id, date, description, amount_sgd, amount_minor)"
        " VALUES (1, '2026-01-10', 'SAMPLE ROW', 7.70, 770)",
    )
    run(
        temp_db,
        "UPDATE merchant_rules SET min_amount = 1.50, min_amount_minor = 150,"
        " max_amount_minor = NULL WHERE id = 1",
    )

    report = conversion.run_step(temp_db, convert_minor_units.STEP, today=DAY)

    assert report["status"] == "already-applied"


# --- nothing reads the new amounts yet ---------------------------------------


def test_the_app_shows_the_same_figures_before_and_after(client, temp_db):
    conn = sqlite3.connect(str(temp_db))
    conn.executescript("""
        INSERT INTO accounts (id, name, short_name, type) VALUES
            (1, 'Sample Card 0001', 'Sample-0001', 'credit_card');
        INSERT INTO statements (id, account_id, statement_date) VALUES (1, 1, '2026-01-31');
        INSERT INTO transactions
            (statement_id, date, description, amount_sgd, amount_foreign, currency_foreign, flow_type)
        VALUES
            (1, '2026-01-10', 'SAMPLE CAFE', 12.30, NULL, NULL, 'expense'),
            (1, '2026-01-11', 'SAMPLE BOOK SHOP', 40.05, 29.50, 'USD', 'expense'),
            (1, '2026-01-12', 'SAMPLE REFUND', -19.99, NULL, NULL, 'refund');
        UPDATE merchant_rules SET min_amount = 10.00 WHERE id = 1;
    """)
    conn.commit()
    conn.close()
    paths = (
        "/api/transactions",
        "/api/rules",
        "/api/dashboard/stat-cards?ref_month=2026-01",
        "/api/dashboard/monthly",
    )
    before = {path: client.get(path).get_json() for path in paths}
    assert [t["amount_sgd"] for t in before["/api/transactions"]["transactions"]] != []

    report = conversion.run_step(temp_db, convert_minor_units.STEP, today=DAY)

    assert report["status"] == "applied"
    assert {path: client.get(path).get_json() for path in paths} == before
    assert sorted(query(temp_db, "SELECT amount_minor FROM transactions")) == [
        (-1999,), (1230,), (4005,)
    ]


# --- the command ---------------------------------------------------------------


def test_the_command_reports_totals_rounded_rows_and_unknown_currency_names(old, capsys):
    assert convert_minor_units.main([str(old)]) == 0

    out = capsys.readouterr().out
    assert "step minor-units: applied" in out
    assert "backup:" in out
    assert f"account 1: {CARD_TOTAL}" in out
    assert f"account 2: {BANK_TOTAL}" in out
    assert "rows on no account: 500" in out
    # 0.125 and -0.375 were not whole cents.
    assert "2 rows were not a whole number of cents" in out
    assert "ZORKMID" in out


def test_the_command_run_again_says_already_applied(old, capsys):
    convert_minor_units.main([str(old)])
    capsys.readouterr()

    assert convert_minor_units.main([str(old)]) == 0

    assert "step minor-units: already-applied" in capsys.readouterr().out


def test_the_command_needs_a_database_path(tmp_path, capsys):
    assert convert_minor_units.main([]) == 2
    assert convert_minor_units.main([str(tmp_path / "missing.db")]) == 1
