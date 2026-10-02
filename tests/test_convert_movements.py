"""The movements conversion step, proved on a temporary database in the shape
from before it.

The old shape is today's schema with what this step adds taken away again
(the rows' other side), so the fixture follows every other table as it
changes. Every name and figure is invented.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

import app as fin_app
import conversion
import convert_movements
import db
import money

DAY = date(2026, 3, 14)

# Accounts as the account-kinds step leaves them. (id, name, kind, owner)
ACCOUNTS = [
    (1, "Sample Bank 0002", "bank", "Household"),
    (2, "Sample Card 0001", "card", "Household"),
    (3, "Sample Business Bank 0007", "bank", "Kalesh"),
    (4, "Moom", "company", "Household"),
    (5, "Kalesh", "company", "Household"),
    (6, "Crypto held outside fin", "holding", "Household"),
    (7, "UOB home loan", "loan", "Household"),
    (8, "DBS auto loan", "loan", "Household"),
    (9, "Car", "holding", "Household"),
]
MOOM, CRYPTO, HOME_LOAN, AUTO_LOAN = 4, 6, 7, 8

# Merchants. (id, name, book)
SERVICES = [(1, "UOB Home Loan", "Household"), (2, "Car Loan", "Household"), (3, "Sample Landlord", "Household")]

# (id, account, description, amount, flow before, manual, service, typed, book)
#   -> what the step makes of it: (flow, other side)
ROWS = [
    # The rules that name an other side, and the two that make income.
    ((1, 1, "INWARD PAYNOW FROM MOOM PTE LTD REF 3001", -18250.75, "income", 0, None, False, None), ("movement", MOOM)),
    ((2, 1, "GIRO SALARY MOOM PTE LTD", -9000.00, "income", 0, None, False, None), ("income", None)),
    ((3, 1, "INWARD PAYNOW FROM KALESH INC REF 3002", -6500.00, "transfer", 0, None, False, None), ("income", None)),
    ((4, 1, "INWARD CREDIT INDEPENDENT RESERVE SG REF 3003", -42000.00, "transfer", 0, None, False, None), ("movement", CRYPTO)),
    ((5, 1, "SAMPLE HOME LOAN INSTALMENT 08", 8800.00, "expense", 0, 1, False, "Household"), ("movement", HOME_LOAN)),
    ((6, 1, "SAMPLE CAR LOAN INSTALMENT 08", 2000.00, "expense", 0, 2, False, "Household"), ("movement", AUTO_LOAN)),
    # Unlabelled bank transfers go to the review list.
    ((7, 1, "OUTWARD TELEGRAPHIC TRANSFER REF 550012", 40000.00, "expense", 0, None, False, None), ("review", None)),
    ((8, 1, "FAST PAYMENT REF 771203 OTHR", 25000.00, "expense", 0, None, False, None), ("review", None)),
    ((9, 1, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410", -250000.00, "income", 0, None, False, None), ("review", None)),
    # A flow set by hand is never touched, whatever the rules would say.
    ((10, 1, "INWARD PAYNOW FROM MOOM PTE LTD REF 3009", -700.10, "income", 1, None, False, None), ("income", None)),
    ((11, 1, "INWARD CREDIT INDEPENDENT RESERVE SG REF 3010", -1500.00, "income", 1, None, False, None), ("income", None)),
    ((12, 1, "SAMPLE CAR LOAN INSTALMENT 07", 2000.00, "expense", 1, 2, False, "Household"), ("expense", None)),
    ((13, 1, "PAYNOW TO SAMPLE PAYEE REF 9931", 12000.50, "expense", 1, None, False, None), ("expense", None)),
    ((14, 1, "INWARD PAYNOW FROM KALESH INC REF 3011", -6500.00, "transfer", 1, None, False, None), ("transfer", None)),
    # A company's cost keeps its flow as spending: its book says whose it is.
    ((15, 1, "PAYNOW TO SAMPLE SUPPLIER REF 6001", 3100.00, "expense", 0, None, False, "Moom"), ("expense", None)),
    ((16, 2, "SAMPLE ADVERTS 0042", 910.40, "expense", 0, None, False, "Moom"), ("expense", None)),
    # A transfer a merchant rule or a type already labelled is spending.
    ((17, 1, "PAYNOW TO SAMPLE LANDLORD REF 1200", 4200.00, "expense", 0, 3, True, "Household"), ("expense", None)),
    ((18, 1, "PAYNOW TO SAMPLE TUTOR REF 1201", 310.40, "expense", 0, None, True, "Household"), ("expense", None)),
    # Not on a household bank account: left as it is.
    ((19, 2, "PAYNOW TO SAMPLE PAYEE REF 9932", 55.00, "expense", 0, None, False, None), ("expense", None)),
    ((20, 3, "PAYNOW TO SAMPLE PAYEE REF 9933", 900.00, "expense", 0, None, False, None), ("expense", None)),
    ((21, 3, "INWARD PAYNOW FROM MOOM PTE LTD REF 3012", -480.00, "income", 0, None, False, None), ("income", None)),
    # Already a move between own accounts or a card payoff, or no transfer at all.
    ((22, 1, "PAYNOW TO SAMPLE PAYEE REF 9934", 75.00, "transfer", 0, None, False, None), ("transfer", None)),
    ((23, 1, "INTEREST EARNED", -12.34, "income", 0, None, False, None), ("income", None)),
    ((24, 1, "POINT-OF-SALE SAMPLE CARD SHOP 0042", 61.10, "expense", 0, None, False, None), ("expense", None)),
]


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


def old_schema() -> str:
    """Today's schema without the column this step adds."""
    lines = db.SCHEMA_PATH.read_text().splitlines()
    kept = [line for line in lines if not line.strip().startswith("other_side_id ")]
    assert len(kept) == len(lines) - 1
    return "\n".join(kept)


@pytest.fixture
def old(tmp_path: Path) -> Path:
    """A database from before the step, holding a row for every rule."""
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(old_schema())
    conn.executemany(
        "INSERT INTO accounts (id, name, short_name, type, owner) VALUES (?, ?, ?, ?, ?)",
        [(i, name, name, kind, owner) for i, name, kind, owner in ACCOUNTS],
    )
    conn.executemany(
        "INSERT INTO statements (id, account_id, statement_date) VALUES (?, ?, '2026-08-01')",
        [(1, 1), (2, 2), (3, 3)],
    )
    conn.executemany("INSERT INTO services (id, name, book) VALUES (?, ?, ?)", SERVICES)
    conn.execute(
        "INSERT INTO types (id, kind, name, covers) VALUES (900, 'spending', 'Sample type', 'a stand-in')"
    )
    conn.executemany(
        "INSERT INTO transactions (id, statement_id, date, description, amount_minor, flow_type, "
        "flow_type_manual, service_id, type_id, book, cat_source) VALUES (?, ?, '2026-08-15', ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (i, account, description, money.to_minor(amount), flow, manual, service,
             900 if typed else None, book,
             "manual" if manual else "auto")
            for (i, account, description, amount, flow, manual, service, typed, book), _ in ROWS
        ],
    )
    conn.commit()
    conn.close()
    return path


def convert(path: Path, **kwargs) -> dict:
    return conversion.run_step(path, convert_movements.STEP, today=DAY, **kwargs)


def flows(path: Path) -> dict:
    return {
        r[0]: (r[1], r[2])
        for r in query(path, "SELECT id, flow_type, other_side_id FROM transactions ORDER BY id")
    }


# --- what the step does ---------------------------------------------------------


def test_each_row_is_reclassified_by_its_rule_or_left_alone(old):
    report = convert(old)

    assert report["status"] == "applied"
    assert flows(old) == {row[0]: after for row, after in ROWS}


def test_a_row_with_a_manual_flow_is_never_touched(old):
    manual = "SELECT * FROM transactions WHERE flow_type_manual = 1 ORDER BY id"
    old_columns = len(query(old, "SELECT * FROM pragma_table_info('transactions')"))
    before = query(old, manual)

    convert(old)

    assert len(before) == 5
    assert [row[:old_columns] for row in query(old, manual)] == before
    assert query(old, "SELECT DISTINCT other_side_id FROM transactions WHERE flow_type_manual = 1") == [(None,)]


def test_nothing_but_the_flow_and_the_other_side_changes(old):
    everything_else = (
        "SELECT id, statement_id, date, description, amount_minor, service_id, type_id, book, "
        "cat_source, flow_type_manual, is_one_off, notes FROM transactions ORDER BY id"
    )
    before = query(old, everything_else)

    convert(old)

    assert query(old, everything_else) == before


def test_no_row_is_lost_and_every_account_total_is_the_same_to_the_cent(old):
    totals = (
        "SELECT s.account_id, COUNT(*), SUM(t.amount_minor) "
        "FROM transactions t JOIN statements s ON s.id = t.statement_id GROUP BY s.account_id"
    )
    before = query(old, totals)

    report = convert(old)

    assert before == [(1, 20, -23691619), (2, 2, 96540), (3, 2, 42000)]
    assert query(old, totals) == before
    assert report["before"] == report["after"]
    assert report["after"]["rows"]["transactions"] == len(ROWS)
    assert report["after"]["account_totals_cents"] == {1: -23691619, 2: 96540, 3: 42000}


def test_a_converted_database_has_the_shape_of_a_new_one(old, tmp_path):
    new = tmp_path / "new.db"
    conn = sqlite3.connect(str(new))
    conn.executescript(db.SCHEMA_PATH.read_text())
    conn.close()

    convert(old)

    shape = "SELECT name, type, \"notnull\", dflt_value FROM pragma_table_info('transactions')"
    foreign_keys = "SELECT \"table\", \"from\", \"to\" FROM pragma_foreign_key_list('transactions') ORDER BY 1, 2"
    assert query(old, shape) == query(new, shape)
    assert query(old, foreign_keys) == query(new, foreign_keys)


def test_run_twice_changes_nothing(old):
    convert(old)
    after_first = dump(old)

    again = convert(old)

    assert again["status"] == "already-applied"
    assert again["backup"] is None
    assert dump(old) == after_first
    assert len(list(old.parent.glob("*.bak"))) == 1


def test_it_runs_behind_its_backup_and_refuses_without_one(old):
    before = dump(old)

    with pytest.raises(conversion.ConversionRefused, match="backup copy is missing"):
        convert(old, backup=lambda source, target: None)

    assert dump(old) == before
    report = convert(old)
    assert Path(report["backup"]).name == "ledger.db.pre-movements-20260314.bak"


def test_a_row_that_arrives_later_is_picked_up_by_a_second_run(old):
    convert(old)
    run(old, "INSERT INTO transactions (id, statement_id, date, description, amount_minor, flow_type) "
             "VALUES (50, 1, '2026-09-02', 'INWARD CREDIT INDEPENDENT RESERVE SG REF 3050', -90000, 'transfer')")

    report = convert(old)

    assert report["status"] == "applied"
    assert flows(old)[50] == ("movement", CRYPTO)
    assert {i: f for i, f in flows(old).items() if i != 50} == {row[0]: after for row, after in ROWS}


def test_a_database_from_before_accounts_had_an_owner_stops_the_step(old):
    run(old, "ALTER TABLE accounts DROP COLUMN owner")
    before = dump(old)

    with pytest.raises(conversion.ConversionFailed, match="AccountKindsNotConverted"):
        convert(old)

    assert dump(old) == before


def test_the_command_reports_what_it_did(old, capsys):
    assert convert_movements.main([str(old)]) == 0

    out = capsys.readouterr().out
    assert "step movements: applied" in out
    assert "backup:" in out
    assert "expense -> review: 2" in out
    assert "income -> review: 1" in out
    assert "expense -> movement: 2" in out
    assert "transfer -> movement: 1" in out
    assert "income -> movement: 1" in out
    assert "transfer -> income: 1" in out
    assert "8 rows reclassified" in out
    # Counts only: no description and no amount is printed.
    assert "SAMPLE" not in out and "40000" not in out


def test_the_command_run_again_says_so(old, capsys):
    convert_movements.main([str(old)])
    capsys.readouterr()

    assert convert_movements.main([str(old)]) == 0
    assert "step movements: already-applied" in capsys.readouterr().out


def test_the_command_needs_a_database_path(tmp_path):
    assert convert_movements.main([]) == 2
    assert convert_movements.main([str(tmp_path / "missing.db")]) == 1


# --- the app on a converted database, and on one that is not ----------------------


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


def test_the_app_will_not_start_on_a_database_from_before_the_step(old, started_on):
    before = dump(old)

    with pytest.raises(db.DatabaseNotConverted, match="convert_movements.py"):
        started_on(old)

    assert dump(old) == before


def test_the_app_shows_the_converted_rows_and_their_other_sides(old, started_on):
    convert(old)

    client = started_on(old)

    waiting = client.get("/api/transactions?flow=review&sort=amount&sort_dir=desc").get_json()["transactions"]
    assert [(tx["id"], tx["amount_sgd"]) for tx in waiting] == [(7, 40000.00), (8, 25000.00), (9, -250000.00)]
    assert client.get("/api/review").get_json()["waiting"] == 3
    movements = client.get("/api/transactions?flow=movement&sort=date").get_json()["transactions"]
    assert {tx["id"]: tx["other_side_name"] for tx in movements} == {
        1: "Moom", 4: "Crypto held outside fin", 5: "UOB home loan", 6: "DBS auto loan",
    }


def test_a_new_database_needs_no_step(temp_db):
    assert convert(temp_db)["status"] == "already-applied"
