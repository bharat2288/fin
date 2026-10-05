"""The guard in conftest.py: no test may open the repository's real database.

The guard is proved against a stand-in: each test points the guard (and the
app's DB_PATH) at a file under tmp_path, so if the guard ever regresses these
tests open a throwaway file, never the operator's database. One separate test
pins what the guard protects by default.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import db

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A path under tmp_path that the guard treats as the real database."""
    path = (tmp_path / "guarded" / "fin.db").resolve()
    path.parent.mkdir()
    monkeypatch.setattr(conftest, "REAL_DB_PATHS", frozenset({path}))
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


def test_the_guard_protects_the_database_beside_the_app():
    assert Path(db.__file__).resolve().parent / "fin.db" in conftest.REAL_DB_PATHS


def test_fin_db_path_names_the_book_and_the_guard_protects_it_too(tmp_path):
    named = tmp_path / "volume" / "fin.db"
    beside = Path(db.__file__).resolve().parent / "fin.db"

    assert db.configured_db_path({"FIN_DB_PATH": str(named)}) == named
    assert db.configured_db_path({"FIN_DB_PATH": "  "}) == db.DEFAULT_DB_PATH
    assert db.configured_db_path({}) == db.DEFAULT_DB_PATH
    assert conftest.real_db_paths({"FIN_DB_PATH": str(named)}) == {beside, named.resolve()}


def test_a_suite_run_with_fin_db_path_set_guards_both_books(tmp_path):
    # As the suite starts on a machine where FIN_DB_PATH is set: db reads it
    # at import, and the guard captures it beside the repo's book.
    # A backslash in the path, as every Windows path has: on POSIX it is a
    # character of the folder's name.
    named = tmp_path / ("back\\slash" if os.sep == "/" else "volume") / "fin.db"
    code = (
        "import sys; sys.path[:0] = ['.', 'tests']; import db, conftest; "
        "print(db.DB_PATH); print(*sorted(str(p) for p in conftest.REAL_DB_PATHS), sep='\\n')"
    )
    env = {**os.environ, "FIN_DB_PATH": str(named)}
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stderr
    # One path per line, compared as whole paths: a printed list would show a
    # Windows path with its backslashes doubled.
    db_path, *guarded = result.stdout.splitlines()
    assert db_path == str(named)
    assert str(named.resolve()) in guarded and str(ROOT / "fin.db") in guarded
    assert not named.exists()


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
