"""The account-kinds conversion step, proved on a temporary database in the
shape from before it.

The old shape is today's schema with what this step adds taken away again
(the accounts' owner column and the anchors table), so the fixture follows
every other table as it changes. No transaction row is written or read here.
Every name is invented.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

import app as fin_app
import conversion
import convert_account_kinds
import db

DAY = date(2026, 3, 14)

CREATED = [
    ("Moom", "company"),
    ("Kalesh", "company"),
    ("Home", "holding"),
    ("Rented property", "holding"),
    ("Car", "holding"),
    ("Crypto held outside fin", "holding"),
    ("UOB home loan", "loan"),
    ("DBS auto loan", "loan"),
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


@pytest.fixture
def old(tmp_path: Path) -> Path:
    """A database from before the step, holding accounts of every old type."""
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(db.SCHEMA_PATH.read_text())
    conn.executescript("""
        DROP TABLE anchors;
        ALTER TABLE accounts DROP COLUMN owner;
        INSERT INTO accounts (id, name, short_name, type, last_four, currency, status) VALUES
            (1, 'Sample Card 0001', 'Sample-0001', 'credit_card', '0001', 'SGD', 'active'),
            (2, 'Sample Bank 0002', 'Sample-0002', 'bank', '0002', 'SGD', 'active'),
            (3, 'Sample Debit 0003', 'Sample-0003', 'debit', '0003', 'SGD', 'archived'),
            (4, 'DBS Business 000000004', 'Sample-Biz-0004', 'bank', '0004', 'SGD', 'active'),
            (5, 'Sample Debit 0005', 'Kalesh-Debit-0005', 'debit', '0005', 'SGD', 'active'),
            (6, 'Sample kalesh Card 0006', 'Sample-0006', 'credit_card', '0006', 'SGD', 'active');
        INSERT INTO statements (id, account_id, statement_date) VALUES
            (1, 1, '2026-01-01'), (2, 2, '2026-01-01'), (3, 4, '2026-01-01');
    """)
    conn.commit()
    conn.close()
    return path


def convert(path: Path, **kwargs) -> dict:
    return conversion.run_step(path, convert_account_kinds.STEP, today=DAY, **kwargs)


def accounts(path: Path) -> list[tuple]:
    return query(path, "SELECT id, name, type, owner FROM accounts ORDER BY id")


# --- what the step does ---------------------------------------------------------


def test_credit_cards_and_debit_cards_become_cards(old):
    report = convert(old)

    assert report["status"] == "applied"
    assert query(old, "SELECT id, type FROM accounts WHERE id <= 6 ORDER BY id") == [
        (1, "card"), (2, "bank"), (3, "card"), (4, "bank"), (5, "card"), (6, "card"),
    ]


def test_the_kalesh_account_and_card_are_marked_as_kaleshs(old):
    convert(old)

    assert query(old, "SELECT id, owner FROM accounts WHERE id <= 6 ORDER BY id") == [
        (1, "Household"), (2, "Household"), (3, "Household"),
        (4, "Kalesh"), (5, "Kalesh"), (6, "Kalesh"),
    ]


def test_the_companies_holdings_and_loans_are_created_with_no_figure(old):
    convert(old)

    made = query(
        old, "SELECT name, type, owner, currency, status FROM accounts WHERE id > 6 ORDER BY id"
    )
    assert made == [(name, kind, "Household", "SGD", "active") for name, kind in CREATED]
    assert query(old, "SELECT COUNT(*) FROM anchors") == [(0,)]


def test_nothing_else_about_an_existing_account_changes(old):
    before = query(
        old, "SELECT id, name, short_name, last_four, currency, status, created_at"
             " FROM accounts ORDER BY id"
    )
    statements = query(old, "SELECT * FROM statements ORDER BY id")

    report = convert(old)

    assert query(
        old, "SELECT id, name, short_name, last_four, currency, status, created_at"
             " FROM accounts WHERE id <= 6 ORDER BY id"
    ) == before
    assert query(old, "SELECT * FROM statements ORDER BY id") == statements
    assert report["before"]["rows"]["accounts"] == 6
    assert report["after"]["rows"]["accounts"] == 14
    for table in ("statements", "transactions", "services", "merchant_rules", "subscriptions"):
        assert report["after"]["rows"][table] == report["before"]["rows"][table], table
    assert report["after"]["account_totals_cents"] == report["before"]["account_totals_cents"]


def test_an_account_already_there_is_not_created_a_second_time(old):
    run(old, "INSERT INTO accounts (name, short_name, type) VALUES ('Car', 'Car', 'holding')")

    convert(old)

    assert query(old, "SELECT COUNT(*) FROM accounts WHERE name = 'Car'") == [(1,)]
    assert sorted(query(old, "SELECT name, type FROM accounts WHERE id > 6")) == sorted(CREATED)


def test_a_converted_database_has_the_shape_of_a_new_one(old, tmp_path):
    convert(old)
    fresh = tmp_path / "fresh.db"
    conn = sqlite3.connect(str(fresh))
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
            "unique": {
                t: sorted(query(path, f"SELECT \"unique\", origin FROM pragma_index_list('{t}')"))
                for t in tables
            },
        }

    assert shape(old) == shape(fresh)
    assert query(old, "PRAGMA foreign_key_check") == []


# --- replay and refusal ---------------------------------------------------------


def test_run_twice_changes_nothing(old):
    convert(old)
    after_first = dump(old)
    files = sorted(p.name for p in old.parent.iterdir())

    second = convert(old)

    assert second["status"] == "already-applied"
    assert second["backup"] is None
    assert dump(old) == after_first
    assert sorted(p.name for p in old.parent.iterdir()) == files


def test_it_runs_behind_its_backup_and_refuses_without_one(old):
    before = dump(old)

    with pytest.raises(conversion.ConversionRefused, match="backup"):
        convert(old, backup=lambda source, target: None)
    assert dump(old) == before

    report = convert(old)
    assert Path(report["backup"]).name == "ledger.db.pre-account-kinds-20260314.bak"
    assert dump(Path(report["backup"])) == before


def test_an_account_of_a_type_nobody_declared_stops_the_step(old):
    run(old, "INSERT INTO accounts (name, short_name, type) VALUES ('Sample Odd', 'Odd', 'wallet')")
    before = dump(old)

    with pytest.raises(conversion.ConversionFailed, match="UndeclaredKind"):
        convert(old)

    assert dump(old) == before


# --- the command ------------------------------------------------------------------


def test_the_command_reports_what_it_did(old, capsys):
    assert convert_account_kinds.main([str(old)]) == 0

    out = capsys.readouterr().out
    assert "step account-kinds: applied" in out
    assert "backup:" in out
    assert "4 credit-card and debit accounts are now cards" in out
    assert "owned by Kalesh: Sample-Biz-0004, Kalesh-Debit-0005, Sample-0006" in out
    assert "created with no figure: Moom, Kalesh, Home, Rented property, Car, " \
           "Crypto held outside fin, UOB home loan, DBS auto loan" in out


def test_the_command_run_again_says_so(old, capsys):
    convert_account_kinds.main([str(old)])
    capsys.readouterr()

    assert convert_account_kinds.main([str(old)]) == 0

    assert "step account-kinds: already-applied" in capsys.readouterr().out


def test_the_command_needs_a_database_path(tmp_path):
    assert convert_account_kinds.main([]) == 2
    assert convert_account_kinds.main([str(tmp_path / "missing.db")]) == 1


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

    with pytest.raises(db.DatabaseNotConverted, match="convert_account_kinds.py"):
        started_on(old)

    assert dump(old) == before


def test_the_app_serves_a_converted_database_and_takes_a_figure(old, started_on):
    convert(old)

    client = started_on(old)

    listed = {a["name"]: a for a in client.get("/api/accounts").get_json()}
    assert (listed["Sample Card 0001"]["type"], listed["Sample Card 0001"]["owner"]) == (
        "card", "Household",
    )
    assert (listed["DBS Business 000000004"]["type"], listed["DBS Business 000000004"]["owner"]) == (
        "bank", "Kalesh",
    )
    for name, kind in CREATED:
        assert (listed[name]["type"], listed[name]["owner"], listed[name]["anchor"]) == (
            kind, "Household", None,
        ), name

    resp = client.post("/api/anchors", json={
        "account_id": listed["UOB home loan"]["id"], "amount": "910000.00", "date": "2026-06-30",
    })
    assert resp.status_code == 200, resp.get_json()
    again = {a["name"]: a for a in client.get("/api/accounts").get_json()}
    assert again["UOB home loan"]["anchor"] == {
        "date": "2026-06-30", "amount_minor": -91000000, "source": "supplied",
    }


def test_a_new_database_takes_the_step_and_gains_the_accounts(temp_db):
    report = convert(temp_db)

    assert report["status"] == "applied"
    assert query(temp_db, "SELECT name, type FROM accounts ORDER BY id") == CREATED
    assert convert(temp_db)["status"] == "already-applied"
