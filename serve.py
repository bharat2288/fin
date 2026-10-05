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

The gate is enforced by default. With any of its settings missing the process
refuses to start, naming the variable (never a value). The laptop's
`python app.py` is the other local-dev path and is unchanged.

Start order (D3, D6): the seed step, when asked for, before the book is
opened (a book that exists refuses: a second boot with the seed variables
still set does not start); then init_db, which refuses an unconverted book;
then serve.
"""

from __future__ import annotations

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
    backups = {"backup": backup, "backup_missing": backup_missing, "seed": seed}

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


def open_book(config: RuntimeConfig) -> None:
    """Point fin at the book and run init_db, which refuses an unconverted one
    with its own message (what to run); an empty path creates an empty book."""
    if config.db_path is not None:
        db.DB_PATH = Path(config.db_path)
        db.invalidate_rules_cache()
    try:
        db.init_db()
    except db.DatabaseNotConverted as refused:
        raise StartupRefused(str(refused)) from None


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
