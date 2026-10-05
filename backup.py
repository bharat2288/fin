"""The book's off-site copy and the seed step (fin-online D3).

Copied from folio's modules/backup.py and adapted: fin has no settings table,
so the backup's status is a small JSON file beside the book.

Backup - the nightly job (NightlyBackup, started by serve.py) and the
operator's "back up now" (POST /api/backups/run, app gate) both call
`run_backup`:

    SQLite online backup API -> a temporary file beside the book (the volume)
    -> integrity check -> gzip -> one PutObject under a new key per run
       (fin/YYYY/MM/fin-<UTC timestamp>.db.gz) -> the temporary file removed

The upload makes exactly one PutObject and no read of any kind (no
HeadObject, GetObject or list), so fin's key can be a Write Only key: a
leaked key cannot read the backups. The control against overwrite is the
bucket, not this code: Object Lock in compliance mode with versioning keeps a
locked version from being deleted or replaced for its retention period. Each
run also takes a new key, and one run at a time runs in this process.

The outcome is written to backup-status.json beside the book (last attempt,
last success, last error as a fixed code, last object key), by this module
only. The status is a log, not a state machine; it is not backed up.

Seed - at boot, before the book is opened: only when no book exists and both
seed variables are set, download the object with the seed's own key, verify
its sha256, write it, then boot. It refuses (writing nothing) when a book
already exists or the hash does not match.

Settings come from the environment only (credential SOP). The store is any
S3-compatible endpoint; the code holds no provider literal. The backup five:

    FIN_BACKUP_ENDPOINT           the S3 endpoint URL
    FIN_BACKUP_REGION             the region requests are signed for (SigV4)
    FIN_BACKUP_BUCKET             the backup bucket
    FIN_BACKUP_ACCESS_KEY_ID      fin's Write Only, bucket-scoped key id
    FIN_BACKUP_SECRET_ACCESS_KEY  its secret

With any of them missing, backups are disabled and the status says so; fin
still runs (absence is loud, not fatal). The seed four, set only for the
one-time move and deleted right after it:

    FIN_SEED_OBJECT_KEY           the object to seed the book from (a raw or gzip SQLite file)
    FIN_SEED_SHA256               the sha256 of the book file (after gunzip)
    FIN_SEED_ACCESS_KEY_ID        a temporary read-only key scoped to the same bucket
    FIN_SEED_SECRET_ACCESS_KEY    its secret

The seed uses the backup's endpoint, region and bucket and its own key, never
the backup key; the backup never uses the seed key.

Values are never logged, printed or returned; messages name variables only.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Protocol



logger = logging.getLogger(__name__)

# (variable, S3Settings field). Where the store is: shared by backup and seed.
LOCATION_VARIABLES = (
    ("FIN_BACKUP_ENDPOINT", "endpoint_url"),
    ("FIN_BACKUP_REGION", "region"),
    ("FIN_BACKUP_BUCKET", "bucket"),
)
BACKUP_KEY_VARIABLES = (
    ("FIN_BACKUP_ACCESS_KEY_ID", "access_key_id"),
    ("FIN_BACKUP_SECRET_ACCESS_KEY", "secret_access_key"),
)
SEED_KEY_VARIABLES = (
    ("FIN_SEED_ACCESS_KEY_ID", "access_key_id"),
    ("FIN_SEED_SECRET_ACCESS_KEY", "secret_access_key"),
)
BACKUP_VARIABLES = LOCATION_VARIABLES + BACKUP_KEY_VARIABLES
SEED_STORE_VARIABLES = LOCATION_VARIABLES + SEED_KEY_VARIABLES
SEED_OBJECT_KEY_VARIABLE = "FIN_SEED_OBJECT_KEY"
SEED_SHA256_VARIABLE = "FIN_SEED_SHA256"

BACKUP_JOB_ID = "nightly_backup"

# The backup's log: a JSON file beside the book. Written by run_backup only.
STATUS_FILE = "backup-status.json"
LAST_ATTEMPT_KEY = "last_attempt_at"
LAST_SUCCESS_KEY = "last_success_at"
LAST_ERROR_KEY = "last_error"
LAST_OBJECT_KEY = "last_object_key"
BACKUP_STATUS_KEYS = (LAST_ATTEMPT_KEY, LAST_SUCCESS_KEY, LAST_ERROR_KEY, LAST_OBJECT_KEY)

# code -> (fixed message, HTTP status for "back up now"). A closed set: the
# status and every response carry one of these, never exception text.
BACKUP_ERRORS = MappingProxyType(
    {
        "not_configured": ("backups not configured", 503),
        "snapshot_failed": ("the backup copy could not be taken", 500),
        "integrity_failed": ("the backup copy failed its integrity check", 500),
        "upload_failed": ("the backup could not be uploaded", 502),
    }
)

OVERDUE_AFTER = timedelta(hours=36)
NOT_CONFIGURED_WARNING = "backups not configured"
NEVER_SUCCEEDED_WARNING = "no backup has succeeded yet"
OVERDUE_WARNING = "no successful backup in the last 36 hours"

GZIP_MAGIC = b"\x1f\x8b"
_BOOK_SIDECARS = ("-journal", "-wal", "-shm")

# One backup at a time in this process (the nightly job and "back up now").
_RUN_LOCK = threading.Lock()


class BackupFailed(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

    @property
    def message(self) -> str:
        return BACKUP_ERRORS[self.code][0]

    @property
    def status(self) -> int:
        return BACKUP_ERRORS[self.code][1]


class StoreOperationRefused(Exception):
    """The S3 adapter was asked to send an operation outside its closed set.
    Carries the operation's name only."""


class SeedRefused(Exception):
    """The seed step refuses; nothing was written. The message names
    variables, never their values."""


# ---------------------------------------------------------------- settings

@dataclass(frozen=True)
class S3Settings:
    """One S3-compatible bucket and the key used to reach it. The key never
    appears in the repr."""

    endpoint_url: str
    region: str
    bucket: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)


def _read(environ: Mapping[str, str], name: str) -> str:
    return (environ.get(name) or "").strip()


def _load_settings(
    environ: Mapping[str, str], variables: tuple[tuple[str, str], ...]
) -> tuple[S3Settings | None, tuple[str, ...]]:
    missing = tuple(name for name, _field in variables if not _read(environ, name))
    if missing:
        return None, missing
    return S3Settings(**{attr: _read(environ, name) for name, attr in variables}), ()


def load_backup_settings(environ: Mapping[str, str]) -> tuple[S3Settings | None, tuple[str, ...]]:
    """(the backup's settings, or None when any is missing; the missing names).
    Built from the backup key only."""
    return _load_settings(environ, BACKUP_VARIABLES)


def load_seed_store_settings(environ: Mapping[str, str]) -> tuple[S3Settings | None, tuple[str, ...]]:
    """(the seed's settings, or None when any is missing; the missing names).
    The backup's endpoint, region and bucket with the seed key only."""
    return _load_settings(environ, SEED_STORE_VARIABLES)


# ---------------------------------------------------------------- the object store

class ObjectStore(Protocol):
    """What a backup needs of the store: one write. No read, so a Write Only
    key suffices."""

    def put(self, key: str, body: bytes) -> None: ...


class SeedSource(Protocol):
    """What the seed needs of the store: one read."""

    def get(self, key: str) -> bytes: ...


class S3ObjectStore:
    """A bucket on any S3-compatible endpoint, reached with one key. The
    client is built on first use; building it reaches no network.

    Checksums: `request_checksum_calculation="when_required"` keeps botocore
    from adding its flexible checksums (an aws-chunked body with a CRC
    trailer and x-amz-sdk-checksum-algorithm), which not every S3-compatible
    API accepts; `put` sends an explicit Content-MD5 instead, the integrity
    header an Object Lock bucket asks for, which the store checks the body
    against.

    One request per call: each method sends exactly one request and nothing
    else. Retries are off (`total_max_attempts=1`; botocore reads
    `max_attempts` as retries, so 1 there would still send twice): in a
    versioned Object Lock bucket every PutObject that lands is a retained
    version, and a retry after a lost response would write a second one. The
    next run, under a new key, is the retry. Region redirects are not
    followed: botocore's redirector
    would answer a redirect by sending HeadBucket (a read a Write Only key is
    denied) or by re-sending the PutObject to another region; here the call
    fails with the store's own error instead. And the client sends only the
    operations named in `_SENT_OPERATIONS`; any other, including one botocore
    issues itself, is refused before it is sent."""

    _SENT_OPERATIONS = frozenset({"PutObject", "GetObject"})

    def __init__(self, settings: S3Settings):
        self._settings = settings
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            session = boto3.session.Session(
                aws_access_key_id=self._settings.access_key_id,
                aws_secret_access_key=self._settings.secret_access_key,
                region_name=self._settings.region,
            )
            self._client = session.client(
                "s3",
                endpoint_url=self._settings.endpoint_url,
                config=Config(
                    signature_version="s3v4",
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                    retries={"total_max_attempts": 1, "mode": "standard"},
                ),
            )
            self._client.meta.events.register("before-call.s3", self._one_request_per_call)
        return self._client

    @classmethod
    def _one_request_per_call(cls, model, context, **_kwargs):
        """Runs before every call the client makes. Refuses an operation
        outside the closed set; for the rest, marks the call as already
        redirected, which botocore's region redirector honours by not
        redirecting it again (no HeadBucket, no second send)."""
        if model.name not in cls._SENT_OPERATIONS:
            raise StoreOperationRefused(model.name)
        context.setdefault("s3_redirect", {})["redirected"] = True

    def put(self, key: str, body: bytes) -> None:
        """One PutObject, nothing else."""
        self.client.put_object(
            Bucket=self._settings.bucket,
            Key=key,
            Body=body,
            ContentType="application/gzip",
            ContentMD5=base64.b64encode(hashlib.md5(body).digest()).decode("ascii"),
        )

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self._settings.bucket, Key=key)["Body"].read()


# ---------------------------------------------------------------- backup

def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def backup_object_key(now_iso: str) -> str:
    """fin/YYYY/MM/fin-<UTC timestamp>.db.gz for the run at `now_iso`."""
    now = _parse_iso(now_iso)
    return f"fin/{now:%Y}/{now:%m}/fin-{now:%Y%m%dT%H%M%SZ}.db.gz"


def _snapshot(db_path: Path, target: Path) -> None:
    """SQLite's online backup API: a consistent copy while the app runs."""
    source = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()


def _integrity_ok(path: Path) -> bool:
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("PRAGMA integrity_check")] == ["ok"]
    finally:
        conn.close()


def status_path(db_path) -> Path:
    return Path(db_path).parent / STATUS_FILE


def _read_status(db_path) -> dict:
    try:
        recorded = json.loads(status_path(db_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: recorded.get(key) for key in BACKUP_STATUS_KEYS} if isinstance(recorded, dict) else {}


def _write_status(db_path: Path, values: Mapping[str, str | None]) -> None:
    """Merge the run's values into the status file, atomically (a temporary
    file beside it, then a rename)."""
    status = {**_read_status(db_path), **values}
    target = status_path(db_path)
    fd, tmp_name = tempfile.mkstemp(prefix=".fin-backup-status-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(status, handle)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _record_status(db_path: Path, values: Mapping[str, str | None]) -> None:
    """Record the run's status. When the file cannot be written (read-only,
    a full disk), the run's outcome still stands and still reaches the caller
    as its fixed code; the status keeps its earlier record, and a fixed reason
    is logged with the exception's type only."""
    try:
        _write_status(db_path, values)
    except OSError as exc:
        logger.error("backup: the status could not be recorded - %s", type(exc).__name__)


def _step_failed(code: str, exc: BaseException) -> BackupFailed:
    """A failed step's fixed code. The log names the exception's type only:
    its text can carry paths or values."""
    logger.error("backup: %s - %s", code, type(exc).__name__)
    return BackupFailed(code)


def _copy_and_upload(db_path: Path, store: ObjectStore, key: str) -> None:
    # Every step's failure becomes one of the fixed codes, so run_backup can
    # record it: nothing escapes as a raw exception.
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".fin-backup-", suffix=".db", dir=db_path.parent)
        os.close(fd)
    except OSError as exc:
        raise _step_failed("snapshot_failed", exc) from None
    tmp = Path(tmp_name)
    try:
        try:
            _snapshot(db_path, tmp)
        except (sqlite3.Error, OSError) as exc:
            raise _step_failed("snapshot_failed", exc) from None
        try:
            intact = _integrity_ok(tmp)
        except (sqlite3.Error, OSError) as exc:
            raise _step_failed("integrity_failed", exc) from None
        if not intact:
            raise BackupFailed("integrity_failed")
        try:
            body = gzip.compress(tmp.read_bytes(), mtime=0)
        except OSError as exc:
            raise _step_failed("snapshot_failed", exc) from None
        try:
            # One PutObject and no read: a Write Only key can back up. A
            # second put to a key adds a version in the locked bucket.
            store.put(key, body)
        except Exception as exc:
            logger.error("backup: upload failed - %s", type(exc).__name__)
            raise BackupFailed("upload_failed") from None
    finally:
        # A copy that cannot be removed is left on the volume, logged, and
        # never replaces the run's outcome.
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.error("backup: the temporary copy could not be removed - %s", type(exc).__name__)


def run_backup(db_path, store: ObjectStore, *, clock: Callable[[], str] = _utc_now_iso) -> dict:
    """Back the book up once. Returns the status; raises BackupFailed (its
    code already recorded in the status) when the run fails."""
    db_path = Path(db_path)
    with _RUN_LOCK:
        now_iso = clock()
        key = backup_object_key(now_iso)
        try:
            _copy_and_upload(db_path, store, key)
        except BackupFailed as failed:
            _record_status(db_path, {LAST_ATTEMPT_KEY: now_iso, LAST_ERROR_KEY: failed.code})
            logger.error("backup: failed - %s", failed.code)
            raise
        _record_status(
            db_path,
            {LAST_ATTEMPT_KEY: now_iso, LAST_SUCCESS_KEY: now_iso, LAST_ERROR_KEY: None, LAST_OBJECT_KEY: key},
        )
        logger.info("backup: uploaded %s", key)
    return backup_status(db_path, configured=True, now_iso=now_iso)


def backup_status(db_path, *, configured: bool, now_iso: str | None = None) -> dict:
    """The backup status GET /api/backups/status answers and the app shell's
    warning reads."""
    now_iso = now_iso or _utc_now_iso()
    rows = {key: value or None for key, value in _read_status(db_path).items()}
    last_success = rows.get(LAST_SUCCESS_KEY)
    last_error = rows.get(LAST_ERROR_KEY)
    if not configured:
        overdue, warning = False, NOT_CONFIGURED_WARNING
    elif last_success is None:
        overdue, warning = True, NEVER_SUCCEEDED_WARNING
    elif _parse_iso(now_iso) - _parse_iso(last_success) > OVERDUE_AFTER:
        overdue, warning = True, OVERDUE_WARNING
    else:
        overdue, warning = False, None
    return {
        "configured": configured,
        "last_attempt_at": rows.get(LAST_ATTEMPT_KEY),
        "last_success_at": last_success,
        "last_error": last_error,
        "last_error_message": BACKUP_ERRORS[last_error][0] if last_error in BACKUP_ERRORS else None,
        "last_object_key": rows.get(LAST_OBJECT_KEY),
        "overdue": overdue,
        "warning": warning,
    }


# ---------------------------------------------------------------- seed

def _book_exists(target: Path) -> bool:
    return target.exists() or any(Path(f"{target}{suffix}").exists() for suffix in _BOOK_SIDECARS)


_BOOK_EXISTS_MESSAGE = (
    "a book already exists at FIN_DB_PATH; the seed never overwrites a book. "
    f"Remove {SEED_OBJECT_KEY_VARIABLE} and {SEED_SHA256_VARIABLE}"
)
_GZIP_UNREADABLE_MESSAGE = (
    f"the seed object named by {SEED_OBJECT_KEY_VARIABLE} is not a readable gzip file; nothing was written"
)
_WRITE_FAILED_MESSAGE = "the book could not be written at FIN_DB_PATH; nothing was written"


def seed_book(db_path, store: SeedSource, *, object_key: str, expected_sha256: str) -> str:
    """Write the book from the seed object, once. Returns the verified sha256.
    Refuses, writing nothing, when a book (or its journal) already exists or
    when the object's sha256 is not the expected one."""
    target = Path(db_path)
    if _book_exists(target):
        raise SeedRefused(_BOOK_EXISTS_MESSAGE)
    try:
        body = store.get(object_key)
    except Exception as exc:
        logger.error("seed: download failed - %s", type(exc).__name__)
        raise SeedRefused(
            f"the seed object named by {SEED_OBJECT_KEY_VARIABLE} could not be downloaded; nothing was written"
        ) from None
    if body[:2] == GZIP_MAGIC:
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError, zlib.error) as exc:
            # zlib.error (a damaged deflate stream) is not an OSError.
            logger.error("seed: gunzip failed - %s", type(exc).__name__)
            raise SeedRefused(_GZIP_UNREADABLE_MESSAGE) from None
    digest = hashlib.sha256(body).hexdigest()
    if digest != expected_sha256.strip().lower():
        raise SeedRefused(f"the seed object's sha256 does not match {SEED_SHA256_VARIABLE}; nothing was written")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".fin-seed-", suffix=".db", dir=target.parent)
    except OSError as exc:
        logger.error("seed: the book could not be written - %s", type(exc).__name__)
        raise SeedRefused(_WRITE_FAILED_MESSAGE) from None
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        if _book_exists(target):
            raise SeedRefused(_BOOK_EXISTS_MESSAGE)
        try:
            # A hard link never replaces an existing file: if a book appeared
            # meanwhile, this refuses instead of overwriting it.
            os.link(tmp, target)
        except FileExistsError:
            raise SeedRefused(_BOOK_EXISTS_MESSAGE) from None
    except OSError as exc:
        # The write, the fsync or the link failed (a full or read-only volume,
        # a filesystem without hard links). SeedRefused is not an OSError, so
        # the refusals above pass through unchanged.
        logger.error("seed: the book could not be written - %s", type(exc).__name__)
        raise SeedRefused(_WRITE_FAILED_MESSAGE) from None
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    logger.info("seed: the book was written from the seed object; sha256 verified")
    return digest


# ---------------------------------------------------------------- the nightly job

# 02:30 in Singapore, the operator's night.
BACKUP_HOUR_UTC = 18
BACKUP_MINUTE_UTC = 30


def backup_tick(db_path, store: ObjectStore, *, clock: Callable[[], str] = _utc_now_iso) -> None:
    """One nightly run. The outcome is in the status; a failure is logged by
    its fixed code or type, never its text, and never stops the scheduler."""
    try:
        run_backup(db_path, store, clock=clock)
    except BackupFailed as failed:
        logger.error("backup: nightly run failed - %s", failed.code)
    except Exception as exc:
        logger.error("backup: nightly run failed - %s", type(exc).__name__)


class NightlyBackup:
    """The nightly backup in its own background scheduler. serve.py starts it
    once (one worker), and only when the store is configured."""

    def __init__(self) -> None:
        self._sched = None

    def start(self, db_path, store: ObjectStore) -> None:
        if self._sched is not None and self._sched.running:
            return
        from apscheduler.schedulers.background import BackgroundScheduler

        sched = BackgroundScheduler(timezone=timezone.utc)
        sched.add_job(
            backup_tick,
            "cron",
            args=[str(db_path), store],
            hour=BACKUP_HOUR_UTC,
            minute=BACKUP_MINUTE_UTC,
            id=BACKUP_JOB_ID,
            replace_existing=True,
        )
        sched.start()
        self._sched = sched
        logger.info("backup: nightly job scheduled")

    def job(self, job_id: str = BACKUP_JOB_ID):
        return self._sched.get_job(job_id) if self._sched is not None else None

    def shutdown(self) -> None:
        if self._sched is not None and self._sched.running:
            self._sched.shutdown(wait=False)
        self._sched = None
