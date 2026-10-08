"""fin's public sample: anyone signs in with an email and gets a made-up book.

    python demo_serve.py

A separate deployment from the real fin (brief-public-demos D1): its own
Railway service and volume, started by this file instead of serve.py. It never
holds the real book or any of the real instance's keys, and refuses to start
when any FIN_* variable (or OPENROUTER_API_KEY) is set, so a copied variable
set cannot turn it into a door to the real book.

    demo gate (outermost: sign-in, the visitor's book) -> the Flask app

Settings (environment only; names here, values never):

    DEMO_SECRET           signs the session cookie and names the books; 32+ characters
    DEMO_DATA_DIR         the volume: the control file (sign-ups) and the books
    DEMO_RESEND_API_KEY   the code mail's sender (Resend)
    DEMO_MAIL_FROM        the From line, e.g. "fin sample <sample@mail.bhasuri.ai>"
    DEMO_NOTIFY_EMAIL     optional: one mail here per new sign-up
    DEMO_LOCAL            1: on loopback only, codes go to the log, the cookie is not Secure
    DEMO_HOST, PORT       bind address (0.0.0.0 / Railway's PORT; 127.0.0.1 locally)

At boot the template book is written afresh from seed_mock_data.py, covering
the six months up to the last whole month; each visitor's first sign-in copies
it. Off in the sample: /mcp, statement upload, type suggestions, backups.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

import db
from access_gate import ACCESS_IDENTITY_SCOPE_KEY, APP_GATE, UPLOAD_ROUTES, AccessIdentity
from demo_gate import (
    BOOK_SCOPE_KEY,
    VISITOR_SCOPE_KEY,
    ConsoleSender,
    DemoGate,
    DemoSettings,
    ResendSender,
)

logger = logging.getLogger("fin.demo")

SECRET_VARIABLE = "DEMO_SECRET"
DATA_DIR_VARIABLE = "DEMO_DATA_DIR"
RESEND_KEY_VARIABLE = "DEMO_RESEND_API_KEY"
MAIL_FROM_VARIABLE = "DEMO_MAIL_FROM"
NOTIFY_VARIABLE = "DEMO_NOTIFY_EMAIL"
LOCAL_VARIABLE = "DEMO_LOCAL"
HOST_VARIABLE = "DEMO_HOST"
PORT_VARIABLE = "PORT"

# The real instance's settings. Any of them present means this is not a
# sample's environment; the names are checked, never the values.
REAL_PREFIXES = ("FIN_",)
REAL_NAMES = ("OPENROUTER_API_KEY",)

MIN_SECRET_LENGTH = 32
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
DEFAULT_LOCAL_DATA_DIR = Path(__file__).parent / "demo-data"
BOOK_FILENAME = "fin.db"
SWEEP_INTERVAL_SECONDS = 24 * 3600

BLURB = "Personal finances from bank statements: spending by type, subscriptions, and every change undoable."
UPLOAD_OFF = (
    "Statement upload is off in the sample, so nobody's real statements end up here. "
    "The sample book already holds six months of made-up statements."
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}


class StartupRefused(Exception):
    """The sample must not start. The message names variables, never their values."""


@dataclass(frozen=True)
class DemoConfig:
    local: bool
    host: str
    port: int
    secret: bytes
    data_dir: Path
    resend_key: str | None
    mail_from: str | None
    notify_email: str | None


def _read(environ: Mapping[str, str], name: str) -> str:
    return (environ.get(name) or "").strip()


def load_demo_config(environ: Mapping[str, str]) -> DemoConfig:
    real = sorted(
        name
        for name, value in environ.items()
        if (name.startswith(REAL_PREFIXES) or name in REAL_NAMES) and (value or "").strip()
    )
    if real:
        raise StartupRefused(
            "the sample never runs with the real fin's settings; remove these from its environment: "
            + ", ".join(real)
        )

    local_raw = _read(environ, LOCAL_VARIABLE).lower()
    if local_raw not in _TRUE | _FALSE:
        raise StartupRefused(f"{LOCAL_VARIABLE} must be 1 or 0")
    local = local_raw in _TRUE

    host = _read(environ, HOST_VARIABLE) or ("127.0.0.1" if local else "0.0.0.0")
    if local and host.lower() not in LOOPBACK_HOSTS:
        raise StartupRefused(f"{LOCAL_VARIABLE} prints sign-in codes to the log, so {HOST_VARIABLE} must be a loopback host")

    port_raw = _read(environ, PORT_VARIABLE)
    try:
        port = int(port_raw) if port_raw else 8000
    except ValueError:
        raise StartupRefused(f"{PORT_VARIABLE} must be a port number") from None
    if not 0 < port < 65536:
        raise StartupRefused(f"{PORT_VARIABLE} must be a port number")

    missing = []
    secret = _read(environ, SECRET_VARIABLE)
    if not secret:
        missing.append(SECRET_VARIABLE)
    data_dir = _read(environ, DATA_DIR_VARIABLE)
    if not data_dir and not local:
        missing.append(DATA_DIR_VARIABLE)
    resend_key = _read(environ, RESEND_KEY_VARIABLE) or None
    mail_from = _read(environ, MAIL_FROM_VARIABLE) or None
    if not local:
        missing.extend(name for name, value in ((RESEND_KEY_VARIABLE, resend_key), (MAIL_FROM_VARIABLE, mail_from)) if not value)
    if missing:
        raise StartupRefused(f"the sample needs these settings: {', '.join(missing)}")
    if len(secret) < MIN_SECRET_LENGTH:
        raise StartupRefused(f"{SECRET_VARIABLE} must be at least {MIN_SECRET_LENGTH} characters")

    return DemoConfig(
        local=local,
        host=host,
        port=port,
        secret=secret.encode("utf-8"),
        data_dir=Path(data_dir) if data_dir else DEFAULT_LOCAL_DATA_DIR,
        resend_key=resend_key,
        mail_from=mail_from,
        notify_email=_read(environ, NOTIFY_VARIABLE) or None,
    )


def last_whole_month(today: date) -> tuple[int, int]:
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


def build_template(data_dir: Path, today: date | None = None) -> Path:
    """Writes the sample book afresh and returns its path. Visitors' books
    are copies of it; theirs are not touched."""
    import seed_mock_data

    folder = data_dir / "template"
    folder.mkdir(parents=True, exist_ok=True)
    template = folder / BOOK_FILENAME
    partial = folder / (BOOK_FILENAME + ".building")
    for stale in (partial, Path(f"{partial}-wal"), Path(f"{partial}-shm")):
        stale.unlink(missing_ok=True)
    with db.using_book(partial):
        seed_mock_data.build(last_whole_month(today or date.today()), quiet=True)
    # One file: fold the write-ahead log in before the rename.
    import sqlite3

    conn = sqlite3.connect(str(partial))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()
    partial.replace(template)
    return template


def demo_settings(config: DemoConfig, template: Path) -> DemoSettings:
    sender = ConsoleSender() if config.local else ResendSender(config.resend_key, config.mail_from)
    return DemoSettings(
        app_name="fin",
        blurb=BLURB,
        secret=config.secret,
        data_dir=config.data_dir,
        template_book=template,
        book_filename=BOOK_FILENAME,
        sender=sender,
        secure_cookie=not config.local,
        notify_email=config.notify_email,
        hidden_prefixes=("/mcp",),
        refused_routes=UPLOAD_ROUTES,
        refused_message=UPLOAD_OFF,
    )


def _json_refusal(start_response, status: str, text: bytes):
    start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(text))), ("Cache-Control", "no-store")])
    return [text]


class VisitorBook:
    """WSGI middleware, outermost around the Flask app: every request works
    on the book the demo gate named, for the whole of the request (the answer
    is read in full inside it). A request with no book is refused, so nothing
    in the sample ever opens a book of its own choosing."""

    def __init__(self, wsgi_app):
        self._app = wsgi_app

    def __call__(self, environ, start_response):
        scope = environ.get("asgi.scope")
        book = scope.get(BOOK_SCOPE_KEY) if isinstance(scope, dict) else None
        if not book:
            return _json_refusal(start_response, "403 FORBIDDEN", b'{"error":"access denied"}')
        with db.using_book(book):
            result = self._app(environ, start_response)
            try:
                body = b"".join(result)
            finally:
                close = getattr(result, "close", None)
                if close is not None:
                    close()
        return [body]


def _with_identity(app):
    """Hands the Flask app's own gate check (RequireGateIdentity) the visitor
    as an app-gate identity, on the scope where the real gate puts it."""

    async def inner(scope, receive, send):
        if scope.get("type") == "http":
            email = scope.get(VISITOR_SCOPE_KEY)
            if email:
                scope = {**scope, ACCESS_IDENTITY_SCOPE_KEY: AccessIdentity(email=email, gate=APP_GATE)}
        await app(scope, receive, send)

    return inner


def build_demo_app(config: DemoConfig, template: Path):
    """(the Flask app, the composed ASGI app, the gate)."""
    from a2wsgi import WSGIMiddleware

    from app import app as flask_app

    db.DB_PATH = template  # never opened by a request: VisitorBook refuses one with no book
    db.invalidate_rules_cache()
    flask_app.config["LOCAL_DEV"] = False
    flask_app.config["BACKUP_STORE"] = None
    flask_app.config["DEMO"] = True
    if not isinstance(flask_app.wsgi_app, VisitorBook):
        flask_app.wsgi_app = VisitorBook(flask_app.wsgi_app)
    gate = DemoGate(_with_identity(WSGIMiddleware(flask_app)), demo_settings(config, template))
    return flask_app, gate, gate


def _sweep_forever(gate: DemoGate, stop: threading.Event) -> None:
    while not stop.wait(SWEEP_INTERVAL_SECONDS):
        try:
            removed = gate.books.sweep()
            logger.info("idle sample books removed: %d", removed)
        except Exception:
            logger.error("idle sample book sweep failed")


def main(environ: Mapping[str, str] | None = None) -> int:
    import os

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s %(message)s")
    try:
        config = load_demo_config(os.environ if environ is None else environ)
    except StartupRefused as refused:
        print(f"fin sample refused to start: {refused}", file=sys.stderr)
        return 2

    started = time.monotonic()
    template = build_template(config.data_dir)
    logger.info("sample book written in %.1fs", time.monotonic() - started)

    import uvicorn

    _flask_app, asgi, gate = build_demo_app(config, template)
    logger.info("idle sample books removed: %d", gate.books.sweep())
    stop = threading.Event()
    threading.Thread(target=_sweep_forever, args=(gate, stop), name="demo-sweep", daemon=True).start()
    try:
        uvicorn.run(asgi, host=config.host, port=config.port, workers=1, lifespan="off", server_header=False, proxy_headers=False)
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
