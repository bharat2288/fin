"""The Vantage header-label conversion step, proved on a temporary database in
today's shape holding rows on the card's old header-label account. The card's
names come from card_balance, declared here for an invented card "9999";
every name and figure is invented."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

import book_type
import card_balance
import conversion
import convert_account_kinds
import convert_vantage_label
import db
import history

DAY = date(2026, 10, 5)
LABEL = "SAMPLE INFINITE 9999"
BALANCE = "Sample Infinite Card 9999"
HOLDER = "Sample Infinite Card 9999 (OH)"
REAL_LABELS, REAL_BALANCES = card_balance.HEADER_LABELS, card_balance.BALANCE_ACCOUNTS


@pytest.fixture(autouse=True)
def sample_card(monkeypatch):
    monkeypatch.setattr(card_balance, "HEADER_LABELS", {"9999": LABEL})
    monkeypatch.setattr(card_balance, "BALANCE_ACCOUNTS", {"9999": BALANCE})
    monkeypatch.setattr(
        card_balance,
        "CARDHOLDER_ACCOUNTS",
        {("9999", "SAMPLE MAIN"): BALANCE, ("9999", "SAMPLE OTHER"): HOLDER},
    )


def query(path: Path, sql: str, params=()) -> list[tuple]:
    conn = sqlite3.connect(str(path))
    try:
        return [tuple(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def make_book(path: Path, *, label=True, balance=True, anchor=False) -> Path:
    conn = sqlite3.connect(str(path))
    conn.executescript(history.without_history(db.SCHEMA_PATH.read_text()))
    book_type.seed_types(conn)
    convert_account_kinds.STEP.apply(conn)
    if balance:
        conn.execute("INSERT INTO accounts (id, name, short_name, type) VALUES (101, ?, 'b', 'card')", (BALANCE,))
    conn.execute("INSERT INTO accounts (id, name, short_name, type) VALUES (102, ?, 'h', 'card')", (HOLDER,))
    conn.execute("INSERT INTO accounts (id, name, short_name, type) VALUES (104, 'Sample Bank 0004', 's', 'bank')")
    if label:
        conn.execute("INSERT INTO accounts (id, name, short_name, type) VALUES (103, ?, 'l', 'card')", (LABEL,))
    stmts = [(11, 102, "2026-08-15", 1)]
    if balance:
        # A printed statement both accounts hold, and a month record.
        stmts += [(1, 101, "2026-08-15", 1), (2, 101, "2026-06-01", 0)]
    if label:
        stmts += [(3, 103, "2026-08-15", 1), (4, 103, "2026-07-15", 1), (5, 103, "2026-06-01", 0)]
    conn.executemany(
        "INSERT INTO statements (id, account_id, statement_date, printed) VALUES (?, ?, ?, ?)", stmts
    )
    rows = [(11, "2026-08-02", "SAMPLE CAFE", 900)]
    if balance:
        rows += [(1, "2026-08-03", "SAMPLE GROCER", 1_000), (2, "2026-06-04", "SAMPLE PARKING", 300)]
    if label:
        rows += [
            (3, "2026-08-10", "PAYMENT - THANK YOU", -50_000),
            (3, "2026-08-14", "ANNUAL FEE", 19_620),
            (4, "2026-07-10", "PAYMENT - THANK YOU", -40_000),
            (5, "2026-06-20", "SAMPLE BOOKSHOP", 2_500),
        ]
    conn.executemany(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type)"
        " VALUES (?, ?, ?, ?, 'expense')",
        rows,
    )
    if label:
        # A bank row naming the label as its other side, and a subscription on it.
        conn.execute("INSERT INTO statements (id, account_id, statement_date, printed) VALUES (6, 104, '2026-08-01', 0)")
        conn.execute(
            "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type, other_side_id)"
            " VALUES (6, '2026-08-10', 'TO SAMPLE CARD', 50000, 'movement', 103)"
        )
        conn.execute(
            "INSERT INTO subscriptions (amount, frequency, account_id) VALUES (12.0, 'monthly', 103)"
        )
        if anchor:
            conn.execute(
                "INSERT INTO anchors (account_id, date, amount, source) VALUES (103, '2026-08-15', -1000, 'statement')"
            )
    conn.commit()
    conn.close()
    return path


def convert(path: Path) -> dict:
    return conversion.run_step(path, convert_vantage_label.STEP, today=DAY)


def on_account(path: Path, account_id: int) -> list[tuple]:
    return query(
        path,
        "SELECT t.date, t.description, t.amount_minor FROM transactions t "
        "JOIN statements s ON s.id = t.statement_id WHERE s.account_id = ? ORDER BY t.date, t.id",
        (account_id,),
    )


def test_the_book_is_ready_for_this_step(tmp_path):
    path = make_book(tmp_path / "ledger.db")
    conn = sqlite3.connect(str(path))
    try:
        assert conversion.not_applied(conn) == ["vantage-header-label", "change-history"]
    finally:
        conn.close()


def test_every_label_row_moves_to_the_balance_account_and_the_label_goes(tmp_path):
    path = make_book(tmp_path / "ledger.db")
    ids_before = query(path, "SELECT id FROM transactions ORDER BY id")

    report = convert(path)

    assert report["status"] == "applied"
    assert Path(report["backup"]).is_file()
    assert on_account(path, 101) == [
        ("2026-06-04", "SAMPLE PARKING", 300),
        ("2026-06-20", "SAMPLE BOOKSHOP", 2_500),
        ("2026-07-10", "PAYMENT - THANK YOU", -40_000),
        ("2026-08-03", "SAMPLE GROCER", 1_000),
        ("2026-08-10", "PAYMENT - THANK YOU", -50_000),
        ("2026-08-14", "ANNUAL FEE", 19_620),
    ]
    # Merged into the balance account's statement for the day and its month
    # record; the July statement it had none for moved over whole.
    assert query(path, "SELECT id, account_id, statement_date, printed FROM statements WHERE account_id = 101 ORDER BY statement_date") == [
        (2, 101, "2026-06-01", 0),
        (4, 101, "2026-07-15", 1),
        (1, 101, "2026-08-15", 1),
    ]
    assert query(path, "SELECT id FROM accounts WHERE name = ?", (LABEL,)) == []
    assert query(path, "SELECT id FROM transactions ORDER BY id") == ids_before
    assert query(path, "SELECT other_side_id FROM transactions WHERE description = 'TO SAMPLE CARD'") == [(101,)]
    assert query(path, "SELECT account_id FROM subscriptions") == [(101,)]
    # The other cardholder's account is untouched.
    assert on_account(path, 102) == [("2026-08-02", "SAMPLE CAFE", 900)]


def test_the_command_reports_how_many_rows_moved(tmp_path, capsys):
    path = make_book(tmp_path / "ledger.db")
    assert convert_vantage_label.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "step vantage-header-label: applied" in out
    assert "rows moved to the balance account: 4" in out


def test_a_second_run_is_already_applied_and_writes_nothing(tmp_path, capsys):
    path = make_book(tmp_path / "ledger.db")
    convert(path)
    backups = sorted(tmp_path.glob("*.bak"))
    before = query(path, "SELECT * FROM transactions ORDER BY id")

    assert convert_vantage_label.main([str(path)]) == 0

    assert "already-applied" in capsys.readouterr().out
    assert sorted(tmp_path.glob("*.bak")) == backups
    assert query(path, "SELECT * FROM transactions ORDER BY id") == before


def test_a_book_with_no_label_account_needs_nothing_moved(tmp_path, capsys):
    path = make_book(tmp_path / "ledger.db", label=False)
    assert convert_vantage_label.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "already-applied" in out
    assert "rows moved to the balance account: 0" in out
    assert list(tmp_path.glob("*.bak")) == []


def test_it_refuses_when_the_balance_account_is_missing(tmp_path):
    path = make_book(tmp_path / "ledger.db", balance=False)
    before = query(path, "SELECT * FROM transactions ORDER BY id")

    with pytest.raises(conversion.ConversionRefused, match="balance account 'Sample Infinite Card 9999'.*missing"):
        convert(path)

    assert query(path, "SELECT * FROM transactions ORDER BY id") == before
    assert list(tmp_path.glob("*.bak")) == []


def test_it_refuses_and_lists_a_row_already_on_the_balance_account(tmp_path, capsys):
    path = make_book(tmp_path / "ledger.db")
    conn = sqlite3.connect(str(path))
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type)"
        " VALUES (1, '2026-08-14', 'ANNUAL FEE', 19620, 'expense')"
    )
    conn.commit()
    conn.close()
    before = query(path, "SELECT * FROM transactions ORDER BY id")

    assert convert_vantage_label.main([str(path)]) == 1

    out = capsys.readouterr().out
    assert "ConversionRefused" in out
    assert "1 row on the header-label account already sit" in out
    assert "2026-08-14, 19620 minor units, 'ANNUAL FEE'" in out
    assert query(path, "SELECT * FROM transactions ORDER BY id") == before
    assert query(path, "SELECT id FROM accounts WHERE name = ?", (LABEL,)) == [(103,)]
    assert list(tmp_path.glob("*.bak")) == []


def test_a_label_with_an_anchor_is_archived_not_removed(tmp_path):
    path = make_book(tmp_path / "ledger.db", anchor=True)

    convert(path)

    assert query(path, "SELECT status FROM accounts WHERE id = 103") == [("archived",)]
    assert on_account(path, 103) == []
    assert query(path, "SELECT account_id FROM anchors") == [(103,)]
    assert convert(path)["status"] == "already-applied"


def test_the_label_is_matched_by_its_exact_name_not_its_last_four(tmp_path):
    path = make_book(tmp_path / "ledger.db", label=False)
    convert(path)
    # The cardholder accounts share the card's digits and keep their rows.
    assert query(path, "SELECT name FROM accounts WHERE id IN (101, 102) ORDER BY id") == [(BALANCE,), (HOLDER,)]
    assert on_account(path, 102) == [("2026-08-02", "SAMPLE CAFE", 900)]


def test_the_real_card_declares_its_header_label_as_parse_dbs_writes_it():
    import parse_dbs

    assert parse_dbs._normalize_card_header("DBS VANTAGE VISA INFINITE CARD NO.: 4119 1100 2233 7436") == REAL_LABELS["7436"]
    assert set(REAL_LABELS) <= set(REAL_BALANCES)
