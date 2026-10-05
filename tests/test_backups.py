"""The nightly backup, its status, and the one-time seed (fin-online D3, D6).
Adapted from folio's tests/test_backups.py and test_backup_write_only.py:
fin's status is backup-status.json beside the book, not a settings table.

Every store here is a fake or botocore's Stubber; nothing reaches B2. Every
setting is a MOCK_ name; every book is a tmp file.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import app as fin_app
import backup
import conversion
import db
import history
import serve
from backup import (
    BACKUP_JOB_ID,
    BackupFailed,
    NightlyBackup,
    S3ObjectStore,
    StoreOperationRefused,
    backup_object_key,
    backup_status,
    load_backup_settings,
    run_backup,
    seed_book,
)
from gate_tokens import APP_AUD, book, composed, dump, header, mint, send  # noqa: F401 (fixture)

NOW_ISO = "2026-10-01T18:30:00Z"
MOCK_BACKUP_ENV = {
    "FIN_BACKUP_ENDPOINT": "https://s3.mock-region-004.example.test",
    "FIN_BACKUP_REGION": "mock-region-004",
    "FIN_BACKUP_BUCKET": "MOCK_BUCKET",
    "FIN_BACKUP_ACCESS_KEY_ID": "MOCK_BACKUP_KEY_ID",
    "FIN_BACKUP_SECRET_ACCESS_KEY": "MOCK_BACKUP_SECRET",
}
MOCK_SEED_KEY_ENV = {
    "FIN_SEED_ACCESS_KEY_ID": "MOCK_SEED_KEY_ID",
    "FIN_SEED_SECRET_ACCESS_KEY": "MOCK_SEED_SECRET",
}
OLD_SCHEMA = Path(__file__).parent / "schema_before_book_and_type.sql"


class FakeObjectStore:
    """An in-memory versioned bucket: a put never replaces anything."""

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.versions: dict[str, list[bytes]] = {k: [bytes(v)] for k, v in (objects or {}).items()}

    @property
    def objects(self) -> dict[str, bytes]:
        return {key: versions[-1] for key, versions in self.versions.items()}

    def put(self, key: str, body: bytes) -> None:
        self.versions.setdefault(key, []).append(bytes(body))

    def get(self, key: str) -> bytes:
        return self.versions[key][-1]


class FailingUploadStore(FakeObjectStore):
    def put(self, key: str, body: bytes) -> None:
        raise ConnectionError("MOCK network down")


def _with_rows(db_path: Path) -> Path:
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO accounts (name, short_name, type, currency) VALUES ('Mock Account', 'Mock', 'bank', 'SGD')")
    conn.commit()
    conn.close()
    return db_path


def _files(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir()}


# ---------------------------------------------------------------- the backup

def test_the_backup_writes_a_gzip_object_equal_to_the_book(book):
    _with_rows(book)
    store = FakeObjectStore()

    status = run_backup(book, store, clock=lambda: NOW_ISO)

    [(key, body)] = store.objects.items()
    assert key == "fin/2026/10/fin-20261001T183000Z.db.gz" == status["last_object_key"]
    # The temporary copy is gone; the status file sits beside the book.
    assert _files(book.parent) <= {"fin.db", "fin.db-wal", "fin.db-shm", "backup-status.json"}
    restored = book.parent.parent / "restored.db"
    restored.write_bytes(gzip.decompress(body))
    conn = sqlite3.connect(restored)
    assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    conn.close()
    assert dump(restored) == dump(book)


def test_the_status_is_written_beside_the_book_and_read_by_the_route(book, client):
    run_backup(book, FakeObjectStore(), clock=lambda: NOW_ISO)

    recorded = json.loads((book.parent / "backup-status.json").read_text())
    assert recorded == {
        "last_attempt_at": NOW_ISO,
        "last_success_at": NOW_ISO,
        "last_error": None,
        "last_object_key": "fin/2026/10/fin-20261001T183000Z.db.gz",
    }
    status = backup_status(book, configured=True, now_iso="2026-10-02T06:30:00Z")
    assert (status["overdue"], status["warning"]) == (False, None)


def test_a_second_run_writes_a_new_object_and_never_overwrites_the_first(book):
    store = FakeObjectStore()
    run_backup(book, store, clock=lambda: NOW_ISO)
    run_backup(book, store, clock=lambda: "2026-10-02T18:30:00Z")

    assert sorted(store.objects) == [
        "fin/2026/10/fin-20261001T183000Z.db.gz",
        "fin/2026/10/fin-20261002T183000Z.db.gz",
    ]


def test_a_failed_upload_records_a_fixed_code_and_leaves_no_temp_file(book, caplog):
    run_backup(book, FakeObjectStore(), clock=lambda: NOW_ISO)

    with pytest.raises(BackupFailed) as failed:
        run_backup(book, FailingUploadStore(), clock=lambda: "2026-10-02T18:30:00Z")

    assert failed.value.code == "upload_failed"
    status = backup_status(book, configured=True, now_iso="2026-10-02T18:30:00Z")
    assert status["last_error"] == "upload_failed"
    assert status["last_success_at"] == NOW_ISO
    assert not [name for name in _files(book.parent) if name.startswith(".fin-backup")]
    assert "MOCK network down" not in caplog.text


def test_a_book_that_fails_its_integrity_check_is_never_uploaded(book, monkeypatch):
    store = FakeObjectStore()
    monkeypatch.setattr(backup, "_integrity_ok", lambda _path: False)

    with pytest.raises(BackupFailed) as failed:
        run_backup(book, store, clock=lambda: NOW_ISO)

    assert failed.value.code == "integrity_failed"
    assert store.objects == {}


def test_overdue_after_36_hours_without_a_success(book):
    run_backup(book, FakeObjectStore(), clock=lambda: NOW_ISO)

    assert backup_status(book, configured=True, now_iso="2026-10-03T06:30:00Z")["overdue"] is False
    late = backup_status(book, configured=True, now_iso="2026-10-03T06:31:00Z")
    assert late["overdue"] is True
    assert late["warning"] == "no successful backup in the last 36 hours"


def test_a_configured_book_with_no_success_yet_is_overdue(book):
    status = backup_status(book, configured=True, now_iso=NOW_ISO)

    assert (status["overdue"], status["warning"]) == (True, "no backup has succeeded yet")


def test_back_up_now_runs_a_backup_and_reports_the_status(temp_db, client, monkeypatch):
    store = FakeObjectStore()
    monkeypatch.setitem(fin_app.app.config, "BACKUP_STORE", store)

    response = client.post("/api/backups/run")

    assert response.status_code == 200
    assert response.get_json()["last_object_key"] in store.objects
    assert client.get("/api/backups/status").get_json()["warning"] is None


def test_back_up_now_answers_a_failed_upload_with_a_fixed_message(temp_db, client, monkeypatch):
    monkeypatch.setitem(fin_app.app.config, "BACKUP_STORE", FailingUploadStore())

    response = client.post("/api/backups/run")

    assert response.status_code == 502
    assert response.get_json() == {"error": "the backup could not be uploaded", "code": "upload_failed"}


def test_the_nightly_job_runs_at_18_30_utc_and_logs_a_failure_without_raising(book):
    jobs = NightlyBackup()
    jobs.start(book, FakeObjectStore())
    try:
        trigger = str(jobs.job(BACKUP_JOB_ID).trigger)
        assert "hour='18'" in trigger and "minute='30'" in trigger
    finally:
        jobs.shutdown()

    backup.backup_tick(book, FailingUploadStore(), clock=lambda: NOW_ISO)
    assert backup_status(book, configured=True)["last_error"] == "upload_failed"


def test_backup_object_keys_follow_the_dated_layout():
    assert backup_object_key("2026-01-05T02:03:04Z") == "fin/2026/01/fin-20260105T020304Z.db.gz"


# ---------------------------------------------------------------- write-only upload

def _stubbed_store(env=MOCK_BACKUP_ENV):
    from botocore.stub import Stubber

    settings, _missing = load_backup_settings(env)
    store = S3ObjectStore(settings)
    return store, Stubber(store.client)


def test_a_run_against_a_write_only_key_makes_exactly_one_put_object_and_no_read(book):
    # The Stubber answers only what is queued: any HeadObject, GetObject or
    # list the run made would fail it with "unexpected call".
    store, stub = _stubbed_store()
    stub.add_response("put_object", {})

    with stub:
        run_backup(book, store, clock=lambda: NOW_ISO)
        stub.assert_no_pending_responses()

    assert backup_status(book, configured=True, now_iso=NOW_ISO)["last_error"] is None


def test_the_put_carries_content_md5_under_the_bucket_and_the_secret_never_shows():
    store, stub = _stubbed_store()
    stub.add_response(
        "put_object",
        {},
        expected_params={
            "Bucket": "MOCK_BUCKET",
            "Key": "fin/k.db.gz",
            "Body": b"payload",
            "ContentType": "application/gzip",
            "ContentMD5": "Mhw89IbtUJFk7eweGYH+yA==",
        },
    )
    with stub:
        store.put("fin/k.db.gz", b"payload")
        stub.assert_no_pending_responses()
    assert store.client.meta.endpoint_url == MOCK_BACKUP_ENV["FIN_BACKUP_ENDPOINT"]
    assert "MOCK_BACKUP_SECRET" not in repr(store._settings)


def test_the_adapter_refuses_any_operation_outside_put_and_get():
    store, _stub = _stubbed_store()

    with pytest.raises(StoreOperationRefused):
        store.client.head_object(Bucket="MOCK_BUCKET", Key="fin/k.db.gz")
    with pytest.raises(StoreOperationRefused):
        store.client.list_objects_v2(Bucket="MOCK_BUCKET")


# ---------------------------------------------------------------- backups not configured

@pytest.mark.parametrize("missing", list(MOCK_BACKUP_ENV))
def test_any_missing_backup_variable_turns_backups_off_and_fin_still_serves(missing, book, monkeypatch, caplog):
    env = {"FIN_LOCAL_DEV": "1", "FIN_DB_PATH": str(book), **MOCK_BACKUP_ENV, missing: ""}
    config = serve.load_runtime_config(env)
    assert config.backup is None and config.backup_missing == (missing,)

    with caplog.at_level("WARNING"):
        serve.announce_backups(config)
    flask_app, _asgi = serve.build_asgi_app(config)
    client = flask_app.test_client()

    assert missing in caplog.text and "MOCK_" not in caplog.text
    assert flask_app.config["BACKUP_STORE"] is None
    assert client.get("/api/books").status_code == 200
    status = client.get("/api/backups/status").get_json()
    # Local-dev: backups off is expected, so no warning; "back up now" still says why not.
    assert (status["configured"], status["warning"]) == (False, None)
    assert client.post("/api/backups/run").get_json() == {"error": "backups not configured"}


def test_hosted_without_backups_the_app_shell_is_warned(book):
    # The book fixture serves as hosted: LOCAL_DEV off, no backup store.
    response = send(composed(), "GET", "/api/backups/status", header(mint(aud=APP_AUD)))

    assert response.status_code == 200
    assert (response.json()["configured"], response.json()["warning"]) == (False, "backups not configured")


@pytest.mark.parametrize("local_dev, warning", [(False, "backups not configured"), (True, None)])
def test_not_configured_warns_only_when_hosted(book, local_dev, warning):
    assert backup_status(book, configured=False, local_dev=local_dev)["warning"] == warning


def test_in_local_dev_a_configured_backup_still_warns_when_overdue(book):
    status = backup_status(book, configured=True, local_dev=True)
    assert status["warning"] == "no backup has succeeded yet"


# ---------------------------------------------------------------- the seed

def _laptop_book(tmp_path, schema: str | None = None) -> tuple[bytes, str]:
    """A book as the laptop holds it once every conversion step has run:
    schema.sql's shape, the change history's tables and triggers included.
    `schema` builds an older shape instead."""
    path = tmp_path / "laptop" / "fin.db"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.executescript(schema if schema is not None else db.SCHEMA_PATH.read_text())
    conn.close()
    data = path.read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def test_the_converted_laptop_book_keeps_the_change_history(tmp_path):
    data, _digest = _laptop_book(tmp_path)
    conn = sqlite3.connect(tmp_path / "laptop" / "fin.db")
    try:
        assert history.has_shape(conn)
        assert "change-history" not in conversion.not_applied(conn)
        assert conversion.CHAIN[-1][0] == "change-history"
        assert not db._needs_converting(conn)
    finally:
        conn.close()


@pytest.mark.parametrize("compressed", [False, True], ids=["raw", "gzip"])
def test_the_seed_writes_a_file_whose_sha256_equals_the_laptop_book(tmp_path, compressed):
    data, digest = _laptop_book(tmp_path)
    store = FakeObjectStore({"seed/fin.db": gzip.compress(data) if compressed else data})
    target = tmp_path / "volume" / "fin.db"

    assert seed_book(target, store, object_key="seed/fin.db", expected_sha256=digest.upper()) == digest
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
    assert _files(target.parent) == {"fin.db"}


def test_the_seed_refuses_a_hash_mismatch_and_writes_nothing(tmp_path):
    data, digest = _laptop_book(tmp_path)
    target = tmp_path / "volume" / "fin.db"
    target.parent.mkdir()

    with pytest.raises(backup.SeedRefused, match="sha256"):
        seed_book(target, FakeObjectStore({"seed/fin.db": data + b"x"}), object_key="seed/fin.db", expected_sha256=digest)
    assert _files(target.parent) == set()


@pytest.mark.parametrize("existing", ["fin.db", "fin.db-wal", "fin.db-shm", "fin.db-journal"])
def test_the_seed_refuses_when_a_book_already_exists(tmp_path, existing):
    data, digest = _laptop_book(tmp_path)
    target = tmp_path / "volume" / "fin.db"
    target.parent.mkdir()
    (target.parent / existing).write_bytes(b"the book on the volume")

    with pytest.raises(backup.SeedRefused, match="already exists"):
        seed_book(target, FakeObjectStore({"seed/fin.db": data}), object_key="seed/fin.db", expected_sha256=digest)
    assert (target.parent / existing).read_bytes() == b"the book on the volume"
    assert _files(target.parent) == {existing}


@pytest.fixture
def boot(tmp_path, monkeypatch):
    """serve.main() run for real on a tmp volume, with the S3 store a fake,
    uvicorn.run recording the app instead of serving, and no scheduler."""
    import uvicorn

    monkeypatch.setattr(db, "DB_PATH", db.DB_PATH)
    monkeypatch.setitem(fin_app.app.config, "LOCAL_DEV", True)
    monkeypatch.setitem(fin_app.app.config, "BACKUP_STORE", None)
    state = {"served": [], "stores": [], "nightly": []}

    def store_for(settings):
        state["stores"].append(settings.access_key_id)
        return state["store"]

    monkeypatch.setattr(serve, "S3ObjectStore", store_for)
    monkeypatch.setattr(uvicorn, "run", lambda asgi, **_kw: state["served"].append(asgi))
    monkeypatch.setattr(NightlyBackup, "start", lambda self, path, store: state["nightly"].append(Path(path)))
    return state


def _seed_env(tmp_path, digest, **extra):
    return {
        "FIN_LOCAL_DEV": "1",
        "FIN_DB_PATH": str(tmp_path / "volume" / "fin.db"),
        **MOCK_BACKUP_ENV,
        **MOCK_SEED_KEY_ENV,
        "FIN_SEED_OBJECT_KEY": "seed/fin.db",
        "FIN_SEED_SHA256": digest,
        **extra,
    }


def test_boot_seeds_with_the_seed_key_then_opens_and_serves_the_book(tmp_path, boot):
    data, digest = _laptop_book(tmp_path)
    boot["store"] = FakeObjectStore({"seed/fin.db": data})
    volume = tmp_path / "volume" / "fin.db"

    assert serve.main(_seed_env(tmp_path, digest)) == 0

    assert boot["served"] and db.DB_PATH == volume
    # The seed client was built from the seed key; the backup client from the backup key.
    assert boot["stores"] == ["MOCK_SEED_KEY_ID", "MOCK_BACKUP_KEY_ID"]
    assert boot["nightly"] == [volume]
    assert fin_app.app.config["BACKUP_STORE"] is boot["store"]


def test_a_second_boot_with_the_seed_variables_still_set_refuses_to_start(tmp_path, boot, capsys):
    data, digest = _laptop_book(tmp_path)
    boot["store"] = FakeObjectStore({"seed/fin.db": data})
    assert serve.main(_seed_env(tmp_path, digest)) == 0
    seeded = (tmp_path / "volume" / "fin.db").read_bytes()

    assert serve.main(_seed_env(tmp_path, digest)) == 2

    assert "already exists" in capsys.readouterr().err
    assert (tmp_path / "volume" / "fin.db").read_bytes() == seeded
    assert len(boot["served"]) == 1


def test_a_hash_mismatch_refuses_to_start_and_writes_nothing(tmp_path, boot, capsys):
    data, digest = _laptop_book(tmp_path)
    boot["store"] = FakeObjectStore({"seed/fin.db": data + b"tampered"})

    assert serve.main(_seed_env(tmp_path, digest)) == 2

    err = capsys.readouterr().err
    assert "fin refused to start" in err and digest not in err and "MOCK" not in err
    assert not (tmp_path / "volume" / "fin.db").exists() and boot["served"] == []


def test_an_unconverted_seed_stops_the_boot_with_init_dbs_own_message(tmp_path, boot, capsys):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript(OLD_SCHEMA.read_text())
    conn.close()
    data = old.read_bytes()
    boot["store"] = FakeObjectStore({"seed/fin.db": data})

    assert serve.main(_seed_env(tmp_path, hashlib.sha256(data).hexdigest())) == 2

    err = capsys.readouterr().err
    assert "has not been through every conversion step" in err
    assert "convert_all.py" in err
    assert boot["served"] == []
    # The seeded book stays, its content unchanged, for the conversion runner.
    # (Not byte for byte: get_connection's journal_mode=WAL rewrites the header
    # before init_db's refusal, as on the laptop.)
    assert dump(tmp_path / "volume" / "fin.db") == dump(old)


def test_a_seed_without_the_change_history_stops_the_boot_as_unconverted(tmp_path, boot, capsys):
    # Every step run but the last: the book has no change_entries, no
    # change_rows and none of the history triggers.
    data, digest = _laptop_book(tmp_path, history.without_history(db.SCHEMA_PATH.read_text()))
    boot["store"] = FakeObjectStore({"seed/fin.db": data})
    laptop = tmp_path / "laptop" / "fin.db"

    assert serve.main(_seed_env(tmp_path, digest)) == 2

    err = capsys.readouterr().err
    assert "has not been through every conversion step" in err
    assert "change-history (convert_change_history.py)" in err
    assert boot["served"] == []
    assert dump(tmp_path / "volume" / "fin.db") == dump(laptop)


def test_without_the_seed_variables_boot_never_builds_a_seed_client(tmp_path, boot):
    boot["store"] = FakeObjectStore()
    env = {"FIN_LOCAL_DEV": "1", "FIN_DB_PATH": str(tmp_path / "fin.db"), **MOCK_BACKUP_ENV}

    assert serve.main(env) == 0

    assert boot["stores"] == ["MOCK_BACKUP_KEY_ID"]
    assert (tmp_path / "fin.db").exists()  # init_db made an empty probe book


@pytest.mark.parametrize(
    "change, named",
    [
        ({"FIN_SEED_SHA256": ""}, "FIN_SEED_SHA256"),
        ({"FIN_SEED_OBJECT_KEY": ""}, "FIN_SEED_OBJECT_KEY"),
        ({"FIN_SEED_SHA256": "not-a-digest"}, "FIN_SEED_SHA256"),
        ({"FIN_BACKUP_BUCKET": ""}, "FIN_BACKUP_BUCKET"),
        ({"FIN_SEED_ACCESS_KEY_ID": ""}, "FIN_SEED_ACCESS_KEY_ID"),
        ({"FIN_SEED_SECRET_ACCESS_KEY": ""}, "FIN_SEED_SECRET_ACCESS_KEY"),
        ({"FIN_DB_PATH": ""}, "FIN_DB_PATH"),
    ],
)
def test_a_seed_asked_for_without_its_settings_refuses_to_start_writing_nothing(tmp_path, boot, capsys, change, named):
    boot["store"] = FakeObjectStore()

    assert serve.main({**_seed_env(tmp_path, "a" * 64), **change}) == 2

    err = capsys.readouterr().err
    assert named in err and "MOCK_" not in err and "not-a-digest" not in err
    assert boot["stores"] == [] and not (tmp_path / "volume").exists()


def test_the_backup_key_is_no_stand_in_for_a_missing_seed_key(tmp_path):
    env = {k: v for k, v in _seed_env(tmp_path, "a" * 64).items() if k not in MOCK_SEED_KEY_ENV}

    with pytest.raises(serve.StartupRefused) as refused:
        serve.load_runtime_config(env)

    assert "FIN_SEED_ACCESS_KEY_ID" in str(refused.value) and "FIN_SEED_SECRET_ACCESS_KEY" in str(refused.value)
