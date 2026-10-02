import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

import pytest

import app as fin_app
import db

# The operator's real database, captured before any fixture repoints DB_PATH.
REAL_DB_PATH = Path(db.DB_PATH).resolve()


def _database_file(database) -> Path | None:
    """The file a sqlite3.connect() argument names, or None for in-memory."""
    name = database.decode() if isinstance(database, bytes) else str(database)
    if name.startswith("file:"):
        name = url2pathname(unquote(urlsplit(name).path))
    if name in ("", ":memory:"):
        return None
    return Path(name).resolve()


@pytest.fixture(autouse=True)
def real_database_guard(monkeypatch: pytest.MonkeyPatch):
    """Fail any test that opens the real database file.

    Every test gets this. It sits on sqlite3.connect, so it catches the app's
    own helper, a script that opens "fin.db" by a relative name, and the
    conversion runner alike, before the file is opened or created.
    """
    real_connect = sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        if _database_file(database) == REAL_DB_PATH:
            pytest.fail(
                f"test tried to open the real database ({REAL_DB_PATH}); "
                "use the temp_db fixture or a file under tmp_path"
            )
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "test_fin.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.invalidate_rules_cache()
    db.init_db()
    yield db_path
    db.invalidate_rules_cache()


@pytest.fixture
def conn(temp_db: Path):
    conn = db.get_connection()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def client(temp_db: Path):
    fin_app.app.config["TESTING"] = True
    with fin_app.app.test_client() as client:
        yield client
