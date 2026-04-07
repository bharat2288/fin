from pathlib import Path

import pytest

import app as fin_app
import db


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
