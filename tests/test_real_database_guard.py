"""The guard in conftest.py: no test may open the repository's real database.

The guard is proved against a stand-in: each test points the guard (and the
app's DB_PATH) at a file under tmp_path, so if the guard ever regresses these
tests open a throwaway file, never the operator's database. One separate test
pins what the guard protects by default.
"""

import sqlite3
from pathlib import Path

import pytest

import conftest
import db


@pytest.fixture
def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A path under tmp_path that the guard treats as the real database."""
    path = (tmp_path / "guarded" / "fin.db").resolve()
    path.parent.mkdir()
    monkeypatch.setattr(conftest, "REAL_DB_PATH", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


def test_the_guard_protects_the_database_beside_the_app():
    assert conftest.REAL_DB_PATH == Path(db.__file__).resolve().parent / "fin.db"


def test_opening_the_real_database_fails_the_test(stand_in):
    with pytest.raises(pytest.fail.Exception, match="real database"):
        db.get_connection()  # no temp_db fixture: DB_PATH is the guarded path

    # Refused before the file was opened, so it was never created.
    assert not stand_in.exists()


def test_opening_the_real_database_by_a_relative_name_fails_the_test(stand_in, monkeypatch):
    monkeypatch.chdir(stand_in.parent)

    with pytest.raises(pytest.fail.Exception, match="real database"):
        sqlite3.connect("fin.db")

    assert not stand_in.exists()


def test_opening_the_real_database_by_uri_fails_the_test(stand_in):
    with pytest.raises(pytest.fail.Exception, match="real database"):
        sqlite3.connect(f"{stand_in.as_uri()}?mode=ro", uri=True)


def test_temporary_and_in_memory_databases_are_untouched_by_the_guard(tmp_path, conn):
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    sqlite3.connect(":memory:").close()
    sqlite3.connect(str(tmp_path / "fin.db")).close()
