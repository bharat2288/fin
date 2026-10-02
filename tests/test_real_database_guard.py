"""The guard in conftest.py: no test may open the repository's real database."""

import sqlite3
from pathlib import Path

import pytest

import db

REAL_DB = Path(db.__file__).resolve().parent / "fin.db"


def test_opening_the_real_database_fails_the_test():
    existed = REAL_DB.exists()

    with pytest.raises(pytest.fail.Exception, match="real database"):
        db.get_connection()  # no temp_db fixture: DB_PATH is the real path

    assert REAL_DB.exists() == existed


def test_opening_the_real_database_by_a_relative_name_fails_the_test(monkeypatch):
    monkeypatch.chdir(REAL_DB.parent)

    with pytest.raises(pytest.fail.Exception, match="real database"):
        sqlite3.connect("fin.db")


def test_opening_the_real_database_by_uri_fails_the_test():
    with pytest.raises(pytest.fail.Exception, match="real database"):
        sqlite3.connect(f"{REAL_DB.as_uri()}?mode=ro", uri=True)


def test_temporary_and_in_memory_databases_are_untouched_by_the_guard(tmp_path, conn):
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    sqlite3.connect(":memory:").close()
    sqlite3.connect(str(tmp_path / "fin.db")).close()
