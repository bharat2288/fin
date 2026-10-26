"""Convert on start: hosted fin, asked by FIN_CONVERT_ON_START=1, runs
convert_all on the book before init_db, behind each step's own backup; unset
or 0 it refuses an unconverted book as before. serve.main() runs for real on a
tmp volume, uvicorn.run recording the app instead of serving. Every figure,
merchant and name is invented.
"""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
from pathlib import Path

import pytest

import app as fin_app
import conversion
import db
import retire_float_amounts
import serve

OLD_SCHEMA = Path(__file__).parent / "schema_before_book_and_type.sql"
VARIABLE = "FIN_CONVERT_ON_START"
STEP_NAMES = [name for name, _ in conversion.CHAIN]
# What the steps' own reports carry that the hosted log must not: names, row
# text and amounts (12345 + 6789 = 19134 cents on account 1).
PRIVATE = ("Invented Card 0101", "Invented Bank 0202", "INVENTED CAFE", "INVENTED GROCER", "12345", "19134", "6789")


@pytest.fixture
def boot(monkeypatch):
    import uvicorn

    monkeypatch.setattr(db, "DB_PATH", db.DB_PATH)
    monkeypatch.setitem(fin_app.app.config, "LOCAL_DEV", True)
    monkeypatch.setitem(fin_app.app.config, "BACKUP_STORE", None)
    served = []
    monkeypatch.setattr(uvicorn, "run", lambda asgi, **_kw: served.append(asgi))
    return served


def _old_book(tmp_path: Path) -> Path:
    """A book in the oldest shape, as the conversion tests build one."""
    path = tmp_path / "volume" / "fin.db"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA.read_text())
    conn.executescript("""
        INSERT INTO categories (id, name, parent_id) VALUES (1, 'Dining', NULL), (2, 'Other', NULL);
        INSERT INTO accounts (id, name, short_name, type) VALUES
            (1, 'Invented Card 0101', 'Inv-0101', 'credit_card'),
            (2, 'Invented Bank 0202', 'Inv-0202', 'bank');
        INSERT INTO statements (id, account_id, statement_date) VALUES (1, 1, '2026-01-01'), (2, 2, '2026-01-01');
        INSERT INTO services (id, name, category_id) VALUES (1, 'Invented Cafe', 1);
        INSERT INTO transactions
            (id, statement_id, date, description, amount_sgd, category_id, service_id, cat_source, flow_type)
        VALUES
            (1, 1, '2026-01-10', 'INVENTED CAFE', 123.45, 1, 1, 'service_default', 'expense'),
            (2, 1, '2026-01-11', 'INVENTED GROCER', 67.89, 2, NULL, 'auto', 'expense'),
            (3, 2, '2026-01-12', 'INVENTED GROCER', 4.00, 2, NULL, 'auto', NULL);
    """)
    conn.commit()
    conn.close()
    return path


def _converted_book(tmp_path: Path) -> Path:
    path = tmp_path / "volume" / "fin.db"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.executescript(db.SCHEMA_PATH.read_text())
    # A new book's first start seeds the flow lists as rules and marks it
    # (db.seed_flow_rules); a book without the mark reads as unconverted.
    db.seed_flow_rules(conn)
    conn.commit()
    conn.close()
    return path


def _env(book: Path, **extra) -> dict:
    return {"FIN_LOCAL_DEV": "1", "FIN_DB_PATH": str(book), **extra}


def _backups(book: Path) -> list[str]:
    return sorted(p.name for p in book.parent.iterdir() if p.name.endswith(".bak"))


def _dump(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def _waiting(book: Path) -> list[str]:
    conn = sqlite3.connect(book)
    try:
        return conversion.not_applied(conn)
    finally:
        conn.close()


def _columns(book: Path, table: str) -> set[str]:
    conn = sqlite3.connect(book)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_asked_for_it_converts_the_book_behind_its_backups_then_serves(tmp_path, boot, caplog, capsys):
    caplog.set_level(logging.INFO, logger="fin.serve")
    book = _old_book(tmp_path)

    assert serve.main(_env(book, **{VARIABLE: "1"})) == 0

    assert boot and db.DB_PATH == book
    assert _waiting(book) == []
    assert not db._needs_converting(sqlite3.connect(book))
    written = _backups(book)
    assert any(name.startswith("fin.db.pre-book-and-type-") for name in written)
    assert any(name.startswith("fin.db.pre-minor-units-") for name in written)
    # The log names each step; it carries counts, never a name, a row or an amount.
    logged = caplog.text
    for name in STEP_NAMES:
        assert name in logged
    assert "rows before" in logged and f"all {len(STEP_NAMES)} steps applied" in logged
    out = capsys.readouterr()
    for private in PRIVATE:
        assert private not in logged and private not in out.out and private not in out.err


@pytest.mark.parametrize("value", [None, "0", ""], ids=["unset", "0", "empty"])
def test_not_asked_for_it_refuses_as_before_and_names_the_variable(tmp_path, boot, capsys, value):
    book = _old_book(tmp_path)
    before = _dump(book)
    env = _env(book) if value is None else _env(book, **{VARIABLE: value})

    assert serve.main(env) == 2

    err = capsys.readouterr().err
    assert "has not been through every conversion step" in err
    assert f"python convert_all.py {book}" in err
    assert VARIABLE in err
    assert boot == []
    assert _dump(book) == before and _backups(book) == []


@pytest.mark.parametrize("value", ["yes", "true", "2", "on", "convert"])
def test_any_other_value_refuses_to_start_naming_the_variable_never_the_value(tmp_path, boot, capsys, value):
    book = _old_book(tmp_path)
    before = _dump(book)

    with pytest.raises(serve.StartupRefused) as refused:
        serve.load_runtime_config(_env(book, **{VARIABLE: value}))
    assert VARIABLE in str(refused.value) and value not in str(refused.value).replace(VARIABLE, "")

    assert serve.main(_env(book, **{VARIABLE: value})) == 2
    err = capsys.readouterr().err
    assert VARIABLE in err and boot == []
    assert _dump(book) == before and _backups(book) == []


def test_a_step_that_breaks_its_invariant_stops_the_start_and_the_book_is_not_served(
    tmp_path, boot, capsys, caplog, monkeypatch
):
    caplog.set_level(logging.INFO, logger="fin.serve")
    monkeypatch.setattr(
        retire_float_amounts,
        "STEP",
        dataclasses.replace(retire_float_amounts.STEP, invariant=lambda before, after: ["forced 12345"]),
    )
    book = _old_book(tmp_path)

    assert serve.main(_env(book, **{VARIABLE: "1"})) == 2

    err = capsys.readouterr().err
    assert "fin refused to start" in err
    assert "retire-float-amounts" in err and "ConversionFailed" in err
    assert "not run: account-kinds" in err
    assert boot == []
    # Steps before it applied, each behind its backup; it rolled back; none after it ran.
    written = _backups(book)
    assert any("pre-book-and-type" in name for name in written)
    assert any("pre-minor-units" in name for name in written)
    assert not any("account-kinds" in name for name in written)
    assert "amount_sgd" in _columns(book, "transactions")
    assert "owner" not in _columns(book, "accounts")
    assert _waiting(book)[0] == "retire-float-amounts"
    for private in PRIVATE:
        assert private not in err and private not in caplog.text


def test_already_converted_it_says_so_writes_no_backup_and_serves(tmp_path, boot, caplog):
    caplog.set_level(logging.INFO, logger="fin.serve")
    book = _converted_book(tmp_path)

    assert serve.main(_env(book, **{VARIABLE: "1"})) == 0

    assert boot and _backups(book) == []
    assert "nothing to convert" in caplog.text


def test_asked_for_it_on_a_volume_with_no_book_yet_starts_with_an_empty_one(tmp_path, boot, caplog):
    caplog.set_level(logging.INFO, logger="fin.serve")
    book = tmp_path / "volume" / "fin.db"
    book.parent.mkdir()

    assert serve.main(_env(book, **{VARIABLE: "1"})) == 0

    assert boot and book.exists() and _backups(book) == []
    assert "nothing to convert" in caplog.text
