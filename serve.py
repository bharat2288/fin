"""fin as one composed process (fin-online D1-D3; copied from folio's serve.py).

    python serve.py

One process, one worker, an ASGI server binding `0.0.0.0` on `PORT`. Inside:

    access gate (outermost)  ->  router  ->  /mcp          -> the MCP server (mcp_server.py)
                                         ->  everything else -> the Flask app (WSGI adapter)

Binding beyond loopback is safe only because of the gate. One worker, because
SQLite has one writer, the nightly backup must run once, and the key cache is
per process.

Settings come from the environment only (names here; values never):

    FIN_ACCESS_TEAM_DOMAIN     the Cloudflare Access team (`team` or `team.cloudflareaccess.com`)
    FIN_ACCESS_APP_AUD         the app gate's application AUD tag
    FIN_ACCESS_CHAT_AUD        the chat gate's application AUD tag
    FIN_OPERATOR_EMAIL         the one email allowed in
    FIN_UPLOAD_AUD, FIN_UPLOAD_CLIENT_ID
                               the upload credential: the third Access application's
                               AUD tag and its service token's client id; both or
                               neither (neither: the service token is refused everywhere)
    FIN_DB_PATH                the book (required outside local-dev)
    FIN_LOCAL_DEV              1 turns the gate off, on a loopback host only
    FIN_HOST                   bind host (default 0.0.0.0; 127.0.0.1 in local-dev)
    PORT                       bind port (default 8000; Railway sets it)
    FIN_BACKUP_ENDPOINT, FIN_BACKUP_REGION, FIN_BACKUP_BUCKET,
    FIN_BACKUP_ACCESS_KEY_ID, FIN_BACKUP_SECRET_ACCESS_KEY
                               the nightly backup's S3-compatible store and fin's
                               Write Only key; with any missing, backups are off
                               (logged, and the app shell says so) and fin still serves
    FIN_SEED_OBJECT_KEY, FIN_SEED_SHA256, FIN_SEED_ACCESS_KEY_ID, FIN_SEED_SECRET_ACCESS_KEY
                               one-time: seed a new volume's book from the bucket with
                               the seed's own read-only key; deleted after the move
    FIN_CONVERT_ON_START       1 runs `python convert_all.py <book>` before the book is
                               opened; 0 or unset never converts (the default). For
                               one deploy: set it, deploy, read the log, remove it

The gate is enforced by default. With any of its settings missing the process
refuses to start, naming the variable (never a value). The laptop's
`python app.py` is the other local-dev path and is unchanged.

Start order (D3, D6): the seed step, when asked for, before the book is
opened (a book that exists refuses: a second boot with the seed variables
still set does not start); then, only with FIN_CONVERT_ON_START=1, the
conversion steps (convert_all.py's own run: each pending step writes its dated
backup beside the book and checks its invariant; the first that fails or
refuses stops the start, leaving the book as the runner left it); then
init_db, which refuses an unconverted book; then serve.

fin does not convert a book as it starts unless the operator asks, once: the
variable is the hosted stand-in for running convert_all.py in a shell. Set it,
deploy, read the log (each step's name, its status, its backup's file name
and row counts: never a name, a row or an amount, which convert_all's own
report can carry and which stay out of the log), then remove it. Left set it
is harmless: a converted book logs that there is nothing to convert.
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable, Mapping

from a2wsgi import WSGIMiddleware
from flask import Flask

import conversion
import convert_all
import db
from access_gate import (
    CHAT_GATE,
    AccessGate,
    AccessKeys,
    AccessVerifier,
    GateConfigError,
    GateSettings,
    fetch_team_jwks,
    gate_for_request,
)
from backup import (
    SEED_OBJECT_KEY_VARIABLE,
    SEED_SHA256_VARIABLE,
    NightlyBackup,
    S3ObjectStore,
    S3Settings,
    SeedRefused,
    load_backup_settings,
    load_seed_store_settings,
    seed_book,
)


GATE_VARIABLES = (
    ("FIN_ACCESS_TEAM_DOMAIN", "team_domain"),
    ("FIN_ACCESS_APP_AUD", "app_aud"),
    ("FIN_ACCESS_CHAT_AUD", "chat_aud"),
    ("FIN_OPERATOR_EMAIL", "operator_email"),
)
UPLOAD_VARIABLES = (
    ("FIN_UPLOAD_AUD", "upload_aud"),
    ("FIN_UPLOAD_CLIENT_ID", "upload_client_id"),
)
DB_PATH_VARIABLE = db.DB_PATH_VARIABLE
LOCAL_DEV_VARIABLE = "FIN_LOCAL_DEV"
HOST_VARIABLE = "FIN_HOST"
CONVERT_ON_START_VARIABLE = "FIN_CONVERT_ON_START"
PORT_VARIABLE = "PORT"

PUBLIC_HOST = "0.0.0.0"
LOCAL_DEV_HOST = "127.0.0.1"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
DEFAULT_SERVE_PORT = 8000

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}
_SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")

logger = logging.getLogger("fin.serve")


class StartupRefused(Exception):
    """The process must not start. The message names variables, never their values."""


@dataclass(frozen=True)
class SeedRequest:
    object_key: str
    sha256: str
    # The seed's own store settings: the backup's location, the seed key.
    store: S3Settings


@dataclass(frozen=True)
class RuntimeConfig:
    local_dev: bool
    host: str
    port: int
    db_path: str | None
    gate: GateSettings | None
    backup: S3Settings | None = None
    backup_missing: tuple[str, ...] = ()
    seed: SeedRequest | None = None
    convert_on_start: bool = False


def is_loopback_host(host: str) -> bool:
    return host.strip().lower() in LOOPBACK_HOSTS


def _read(environ: Mapping[str, str], name: str) -> str:
    return (environ.get(name) or "").strip()


def load_runtime_config(environ: Mapping[str, str]) -> RuntimeConfig:
    local_dev_raw = _read(environ, LOCAL_DEV_VARIABLE).lower()
    if local_dev_raw in _TRUE:
        local_dev = True
    elif local_dev_raw in _FALSE:
        local_dev = False
    else:
        raise StartupRefused(f"{LOCAL_DEV_VARIABLE} must be 1 or 0")

    host = _read(environ, HOST_VARIABLE) or (LOCAL_DEV_HOST if local_dev else PUBLIC_HOST)
    if local_dev and not is_loopback_host(host):
        raise StartupRefused(
            f"{LOCAL_DEV_VARIABLE} turns the access gate off, so {HOST_VARIABLE} must be a loopback host "
            "(127.0.0.1, ::1 or localhost)"
        )

    port_raw = _read(environ, PORT_VARIABLE)
    try:
        port = int(port_raw) if port_raw else DEFAULT_SERVE_PORT
    except ValueError:
        raise StartupRefused(f"{PORT_VARIABLE} must be a port number") from None
    if not 0 < port < 65536:
        raise StartupRefused(f"{PORT_VARIABLE} must be a port number")

    db_path = _read(environ, DB_PATH_VARIABLE) or None
    backup, backup_missing = load_backup_settings(environ)
    seed = _load_seed_request(environ, db_path=db_path)
    convert_raw = _read(environ, CONVERT_ON_START_VARIABLE)
    if convert_raw not in ("", "0", "1"):
        raise StartupRefused(f"{CONVERT_ON_START_VARIABLE} must be 1 or 0")
    backups = {
        "backup": backup,
        "backup_missing": backup_missing,
        "seed": seed,
        "convert_on_start": convert_raw == "1",
    }

    if local_dev:
        return RuntimeConfig(local_dev=True, host=host, port=port, db_path=db_path, gate=None, **backups)

    missing = [name for name, _field in GATE_VARIABLES if not _read(environ, name)]
    if db_path is None:
        missing.append(DB_PATH_VARIABLE)
    upload_set = [name for name, _field in UPLOAD_VARIABLES if _read(environ, name)]
    if upload_set and len(upload_set) < len(UPLOAD_VARIABLES):
        missing.extend(name for name, _field in UPLOAD_VARIABLES if name not in upload_set)
    if missing:
        raise StartupRefused(f"the access gate is enforced and these settings are missing: {', '.join(missing)}")

    gate = GateSettings(**{field: _read(environ, name) for name, field in GATE_VARIABLES + UPLOAD_VARIABLES})
    try:
        gate.issuer
    except GateConfigError as exc:
        raise StartupRefused(str(exc)) from None
    return RuntimeConfig(local_dev=False, host=host, port=port, db_path=db_path, gate=gate, **backups)


def _load_seed_request(environ: Mapping[str, str], *, db_path: str | None) -> SeedRequest | None:
    """Both seed variables or neither. A seed needs the book's path, the
    store's endpoint, region and bucket, and the seed's own key: any missing
    refuses to start, naming them. The backup key never stands in."""
    object_key = _read(environ, SEED_OBJECT_KEY_VARIABLE)
    sha256 = _read(environ, SEED_SHA256_VARIABLE)
    if not object_key and not sha256:
        return None
    missing = [
        name
        for name, value in ((SEED_OBJECT_KEY_VARIABLE, object_key), (SEED_SHA256_VARIABLE, sha256))
        if not value
    ]
    if db_path is None:
        missing.append(DB_PATH_VARIABLE)
    store, store_missing = load_seed_store_settings(environ)
    missing.extend(store_missing)
    if missing:
        raise StartupRefused(f"the seed step is asked for and these settings are missing: {', '.join(missing)}")
    if not _SHA256_HEX.fullmatch(sha256):
        raise StartupRefused(f"{SEED_SHA256_VARIABLE} must be a sha256 digest (64 hex characters)")
    return SeedRequest(object_key=object_key, sha256=sha256.lower(), store=store)


def announce_backups(config: RuntimeConfig) -> None:
    """Backups missing their settings are loud, not fatal (D3)."""
    if config.backup is None:
        logger.warning("backups are disabled - not configured; missing: %s", ", ".join(config.backup_missing))


def seed_if_requested(config: RuntimeConfig, *, make_store: Callable[[S3Settings], object] | None = None) -> None:
    """The one-time seed from the bucket (D3), before the book is opened.
    Refuses to start, writing nothing, when a book exists, the object cannot
    be fetched or gunzipped, the hash does not match, or the book cannot be
    written. The store is built from the seed's own settings (the seed key,
    never the backup key), and only when a seed is asked for."""
    if config.seed is None:
        return
    store = (make_store or S3ObjectStore)(config.seed.store)
    try:
        seed_book(config.db_path, store, object_key=config.seed.object_key, expected_sha256=config.seed.sha256)
    except SeedRefused as refused:
        raise StartupRefused(str(refused)) from None


# The lines of convert_all's run that reach the log: step names, statuses,
# the backup's file name and row counts per table. Every other line of a
# step's report (account names, totals, labels, a refusal's or a failure's
# own words) is held back: it can carry a name or an amount.
_LOGGED_LINES = (
    re.compile(r"--- (step \d+ of \d+: [a-z0-9-]+) \(python [a-z0-9_]+\.py\)"),
    re.compile(r"step [a-z0-9-]+: (?:applied|already-applied)"),
    re.compile(r"rows (?:before|after): +\{(?:'[a-z_]+': \d+(?:, )?)*\}"),
    re.compile(r"stopped at step \d+ of \d+: [a-z0-9-]+\. Nothing after it was run\."),
    re.compile(r"not run: [a-z0-9-]+(?:, [a-z0-9-]+)*"),
    re.compile(r"all \d+ steps applied"),
)
_STOPPED_AT = re.compile(r"stopped at step (\d+ of \d+: [a-z0-9-]+)\. Nothing after it was run\.")
_BACKUP_LINE = re.compile(r"backup: (.+)")
# How the step ended: the runner's two outcomes, or convert_all's line for
# any other exception (its type only).
_STEP_FAILED = re.compile(r"(ConversionRefused|ConversionFailed): |([A-Z]\w*(?:Error|Exception)) raised$")
_FAILED_WORDS = {
    "ConversionRefused": "it was not started and changed nothing",
    "ConversionFailed": "it was rolled back",
}


def _needs_converting(path: Path) -> bool:
    """init_db's own test of an unconverted book, read from a copy: nothing
    is written. A path with no book yet has nothing to convert."""
    if not path.is_file():
        return False
    copy = conversion.read_copy(path)
    try:
        return db._needs_converting(copy)
    finally:
        copy.close()


def convert_if_requested(config: RuntimeConfig) -> None:
    """FIN_CONVERT_ON_START=1: run convert_all on the book, after the seed
    step and before init_db. Each pending step writes its own dated backup
    beside the book and checks its invariant (the conversion runner's
    contract). The first step that fails or refuses stops the start: the
    book stays as the runner left it, that step rolled back, earlier steps
    applied behind their backups. Logs only step names, statuses, backup
    file names and row counts."""
    if not config.convert_on_start:
        return
    path = Path(config.db_path) if config.db_path is not None else db.DB_PATH
    if not _needs_converting(path):
        logger.info(
            "%s: nothing to convert, every conversion step is applied; remove the variable",
            CONVERT_ON_START_VARIABLE,
        )
        return
    logger.info("%s: converting the book, as python convert_all.py <book> does", CONVERT_ON_START_VARIABLE)
    report = io.StringIO()
    with contextlib.redirect_stdout(report):
        code = convert_all.main([str(path)])
    lines = report.getvalue().splitlines()
    held_back = 0
    for line in lines:
        logged = _logged_line(line)
        if logged is None:
            held_back += 1
        else:
            logger.info("convert: %s", logged)
    if held_back:
        logger.info(
            "convert: %d lines of the steps' own report held back (they can carry names or amounts)", held_back
        )
    if code != 0:
        raise StartupRefused(_stopped(lines))
    logger.info("%s: done; remove the variable before the next deploy", CONVERT_ON_START_VARIABLE)


def _logged_line(line: str) -> str | None:
    backup = _BACKUP_LINE.fullmatch(line)
    if backup:
        return f"backup: {Path(backup.group(1)).name}"
    for pattern in _LOGGED_LINES:
        match = pattern.fullmatch(line)
        if match:
            return match.group(1) if match.groups() else line
    return None


def _stopped(lines: list[str]) -> str:
    """The refusal when a step does not pass: which step, how it ended, the
    steps not run. The step's own words are not repeated (names, amounts)."""
    stopped = next(filter(None, map(_STOPPED_AT.fullmatch, lines)), None)
    not_run = next((line for line in lines if line.startswith("not run: ") and _logged_line(line)), None)
    kind = None
    for line in lines:
        match = _STEP_FAILED.match(line)
        if match:
            kind = match.group(1) or f"{match.group(2)} raised"
    if stopped is None:
        return f"{CONVERT_ON_START_VARIABLE}: the conversion did not finish; the book was not opened"
    ended = f"{kind}: {_FAILED_WORDS[kind]}" if kind in _FAILED_WORDS else (kind or "it did not pass")
    return (
        f"{CONVERT_ON_START_VARIABLE}: the conversion stopped at step {stopped.group(1)} ({ended}); "
        f"nothing after it was run{f' ({not_run})' if not_run else ''}. "
        "Steps before it stay applied, each behind its dated backup beside the book. "
        "The step's own reason is not logged here (it can name accounts or carry amounts): "
        "read it by running python convert_all.py on a copy of the book."
    )


def open_book(config: RuntimeConfig) -> None:
    """Point fin at the book and run init_db, which refuses an unconverted one
    with its own message (what to run), and the hosted way to run it; an
    empty path creates an empty book."""
    if config.db_path is not None:
        db.DB_PATH = Path(config.db_path)
        db.invalidate_rules_cache()
    try:
        db.init_db()
    except db.DatabaseNotConverted as refused:
        raise StartupRefused(
            f"{refused}\n"
            f"Hosted, with no shell: set {CONVERT_ON_START_VARIABLE}=1 and deploy, and serve.py runs that "
            "command before it opens the book; remove the variable after."
        ) from None


async def _serve_lifespan(receive, send, mcp_lifespan) -> None:
    """The process's lifespan. The Flask side holds none; the MCP side runs
    its session manager from startup to shutdown."""
    async with AsyncExitStack() as stack:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                if mcp_lifespan is not None:
                    try:
                        await stack.enter_async_context(mcp_lifespan())
                    except Exception:
                        await send({"type": "lifespan.startup.failed", "message": "the chat tools could not start"})
                        return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await stack.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return


def _router(mcp_app, flask_asgi):
    """Sends each request to the side its gate owns (gate_for_request)."""
    mcp_lifespan = getattr(mcp_app, "lifespan", None)

    async def route(scope, receive, send):
        if scope.get("type") == "lifespan":
            await _serve_lifespan(receive, send, mcp_lifespan)
            return
        if gate_for_request(scope) == CHAT_GATE:
            await mcp_app(scope, receive, send)
        else:
            await flask_asgi(scope, receive, send)

    return route


def compose(
    flask_app: Flask,
    *,
    gate_settings: GateSettings | None,
    local_dev: bool,
    fetch_jwks: Callable[[], dict] | None = None,
    mcp_app=None,
    clock: Callable[[], float] | None = None,
):
    """The composed ASGI app: the gate outermost, then the router."""
    if mcp_app is None:
        from mcp_server import ChatEndpoint

        mcp_app = ChatEndpoint(local_dev=local_dev)
    router = _router(mcp_app, WSGIMiddleware(flask_app))
    if local_dev:
        return AccessGate(router, verifier=None, local_dev=True)
    if gate_settings is None:
        raise GateConfigError("the access gate needs its settings unless local-dev is on")
    fetch = fetch_jwks or partial(fetch_team_jwks, gate_settings.certs_url)
    keys = AccessKeys(fetch, clock=clock or time.monotonic)
    return AccessGate(router, verifier=AccessVerifier(gate_settings, keys), local_dev=False)


def build_asgi_app(config: RuntimeConfig, *, fetch_jwks: Callable[[], dict] | None = None):
    """(the Flask app, the composed ASGI app) for a loaded runtime config.
    Configures fin's one Flask app in place: its gate mode and backup store
    (the backup key only, never the seed's)."""
    from app import app as flask_app

    flask_app.config["LOCAL_DEV"] = config.local_dev
    flask_app.config["BACKUP_STORE"] = S3ObjectStore(config.backup) if config.backup is not None else None
    asgi = compose(flask_app, gate_settings=config.gate, local_dev=config.local_dev, fetch_jwks=fetch_jwks)
    return flask_app, asgi


def main(environ: Mapping[str, str] | None = None) -> int:
    import os

    # The gate logs each refusal's reason code (never the token or its claims).
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s %(message)s")
    try:
        config = load_runtime_config(os.environ if environ is None else environ)
        announce_backups(config)
        # Before the book is opened: opening it would create an empty one.
        seed_if_requested(config)
        # After the seed, before the book is opened; only when asked for.
        convert_if_requested(config)
        open_book(config)
    except StartupRefused as refused:
        print(f"fin refused to start: {refused}", file=sys.stderr)
        return 2

    import uvicorn

    flask_app, asgi = build_asgi_app(config)
    nightly = NightlyBackup()
    if flask_app.config["BACKUP_STORE"] is not None:
        nightly.start(db.DB_PATH, flask_app.config["BACKUP_STORE"])
    try:
        uvicorn.run(
            asgi,
            host=config.host,
            port=config.port,
            workers=1,
            # On: the MCP server's session manager runs for the process's lifetime.
            lifespan="on",
            server_header=False,
            proxy_headers=False,
        )
    finally:
        nightly.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
