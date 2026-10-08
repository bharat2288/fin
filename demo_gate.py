"""The public sample's front door: sign in with an emailed code, one book per visitor.

Used only by demo_serve.py. The real app's gate (access_gate.py) is not
involved: the sample is a separate deployment that never holds a real book
(brief-public-demos D1). This module is written without anything of fin's
own, so folio carries a copy of it, as fin carries folio's access gate.

    visitor -> /demo/sign-in (email) -> a six-digit code by email
            -> /demo/verify (code)   -> a signed session cookie (30 days)
            -> every other path: the app, on the visitor's own book

What it keeps, in one SQLite file beside the books (the control file):
- `signups`: each verified email, first and last sign-in, how many sign-ins.
  This is the list the operator collects (demo_signups.py reads it).
- `codes`: the code waiting for each email, hashed, with its expiry and tries.
- `sends`: when each code mail went out, for the per-hour cap.

A code lasts CODE_LIFETIME_SECONDS, dies after MAX_CODE_TRIES wrong tries,
and one email gets at most one code a minute; the whole sample sends at most
`max_codes_per_hour` (protecting the sending domain). Emails, codes and the
cookie never reach the log.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Awaitable, Callable, Protocol
from urllib.parse import parse_qs

logger = logging.getLogger("demo.gate")

PREFIX = "/demo"
SIGN_IN_PATH = f"{PREFIX}/sign-in"
VERIFY_PATH = f"{PREFIX}/verify"
SIGN_OUT_PATH = f"{PREFIX}/sign-out"
RESET_PATH = f"{PREFIX}/reset"
PRIVACY_PATH = f"{PREFIX}/privacy"
WHO_PATH = f"{PREFIX}/who"

COOKIE_NAME = "demo_session"
SESSION_SECONDS = 30 * 24 * 3600
CODE_LIFETIME_SECONDS = 10 * 60
CODE_RESEND_SECONDS = 60
MAX_CODE_TRIES = 5
DEFAULT_MAX_CODES_PER_HOUR = 60
DEFAULT_IDLE_DAYS = 14
MAX_FORM_BYTES = 4096
# How often a visitor's book has its last-used time moved forward.
TOUCH_INTERVAL_SECONDS = 3600

# Where the visitor's identity and book ride into the app: ASGI scope keys,
# which no client can set (only headers come from outside).
VISITOR_SCOPE_KEY = "demo.visitor_email"
BOOK_SCOPE_KEY = "demo.book_path"

# Deliberately plain: one @, something on each side, a dot in the domain.
_EMAIL = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")

SIGNED_OUT_API_BODY = {"error": "sign in to the sample first"}


class MailSender(Protocol):
    def send(self, *, to: str, subject: str, text: str) -> None: ...


class MailFailed(Exception):
    """The code mail did not go out. Carries no provider text."""


@dataclass
class ResendSender:
    """Resend's HTTP API (https://resend.com): one POST per mail, no retry."""

    api_key: str
    sender: str
    timeout: float = 10.0

    def send(self, *, to: str, subject: str, text: str) -> None:
        import requests

        try:
            response = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"from": self.sender, "to": [to], "subject": subject, "text": text},
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise MailFailed("unreachable") from None
        if response.status_code >= 300:
            logger.warning("code mail refused: status=%s", response.status_code)
            raise MailFailed("refused")


@dataclass
class ConsoleSender:
    """Local only (DEMO_LOCAL=1 on loopback): the code goes to the log."""

    sent: list[dict] = field(default_factory=list)

    def send(self, *, to: str, subject: str, text: str) -> None:
        self.sent.append({"to": to, "subject": subject, "text": text})
        logger.warning("DEMO_LOCAL mail (not sent): %s", text.splitlines()[0] if text else "")


@dataclass(frozen=True)
class DemoSettings:
    app_name: str  # "fin" or "folio", in page text and mail
    blurb: str  # one line under the app name on the sign-in page
    secret: bytes  # signs the cookie and names the books
    data_dir: Path  # control file + books/
    template_book: Path  # the sample book every visitor starts from
    book_filename: str  # the book's file name inside a visitor's folder
    sender: MailSender
    secure_cookie: bool = True
    notify_email: str | None = None
    max_codes_per_hour: int = DEFAULT_MAX_CODES_PER_HOUR
    idle_days: int = DEFAULT_IDLE_DAYS
    # Paths answered 404 (the chat tools) and (method, path) pairs refused
    # with a sentence (statement upload).
    hidden_prefixes: tuple[str, ...] = ()
    refused_routes: frozenset[tuple[str, str]] = frozenset()
    refused_message: str = "This is turned off in the sample."
    # GET paths whose HTML answer gets the sample bar (made-up figures, Reset,
    # Sign out) put in before </body>.
    bar_paths: frozenset[str] = frozenset({"/"})
    # Called with a new visitor's book path right after it is copied
    # (folio builds its app there); may be None.
    on_new_book: Callable[[Path], None] | None = None

    @property
    def control_path(self) -> Path:
        return self.data_dir / "demo-control.db"

    @property
    def books_dir(self) -> Path:
        return self.data_dir / "books"


def normalise_email(raw: str) -> str | None:
    email = (raw or "").strip().lower()
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        return None
    return email


# --- The control file -------------------------------------------------------


_CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS signups (
    email TEXT PRIMARY KEY,
    first_signed_in_at TEXT NOT NULL,
    last_signed_in_at TEXT NOT NULL,
    sign_ins INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS codes (
    email TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL,
    sent_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sends (
    sent_at REAL NOT NULL
);
"""


def _iso(at: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


class Control:
    """The control file. One lock: the sample is one process."""

    def __init__(self, path: Path, secret: bytes, clock: Callable[[], float] = time.time):
        self._path = Path(path)
        self._secret = secret
        self._clock = clock
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_CONTROL_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def _hash(self, email: str, code: str) -> str:
        return hmac.new(self._secret, f"code|{email}|{code}".encode(), hashlib.sha256).hexdigest()

    def issue_code(self, email: str, max_per_hour: int) -> tuple[str | None, str]:
        """(a new code for `email`, "") for the caller to send, or (None, why):
        "recent" when one went out under a minute ago, "busy" at the hourly cap."""
        now = self._clock()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT sent_at FROM codes WHERE email = ?", (email,)).fetchone()
            if row is not None and now - row["sent_at"] < CODE_RESEND_SECONDS:
                return None, "recent"
            conn.execute("DELETE FROM sends WHERE sent_at < ?", (now - 3600,))
            if conn.execute("SELECT COUNT(*) FROM sends").fetchone()[0] >= max_per_hour:
                return None, "busy"
            code = f"{secrets.randbelow(1_000_000):06d}"
            conn.execute(
                "INSERT INTO codes (email, code_hash, sent_at, expires_at, tries) VALUES (?, ?, ?, ?, 0) "
                "ON CONFLICT(email) DO UPDATE SET code_hash = excluded.code_hash, sent_at = excluded.sent_at, "
                "expires_at = excluded.expires_at, tries = 0",
                (email, self._hash(email, code), now, now + CODE_LIFETIME_SECONDS),
            )
            conn.execute("INSERT INTO sends (sent_at) VALUES (?)", (now,))
            return code, ""

    def withdraw_code(self, email: str) -> None:
        """The mail failed: the code is void and does not count against the minute."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM codes WHERE email = ?", (email,))

    def check_code(self, email: str, code: str) -> bool:
        """True once, for the right code in time; the code is then spent."""
        now = self._clock()
        code = re.sub(r"\s", "", code or "")
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM codes WHERE email = ?", (email,)).fetchone()
            if row is None or now > row["expires_at"] or row["tries"] >= MAX_CODE_TRIES:
                return False
            if not re.fullmatch(r"\d{6}", code) or not hmac.compare_digest(
                row["code_hash"], self._hash(email, code)
            ):
                conn.execute("UPDATE codes SET tries = tries + 1 WHERE email = ?", (email,))
                return False
            conn.execute("DELETE FROM codes WHERE email = ?", (email,))
            return True

    def record_sign_in(self, email: str) -> bool:
        """Notes the sign-in; True when this email is new."""
        stamp = _iso(self._clock())
        with self._lock, self._connect() as conn:
            updated = conn.execute(
                "UPDATE signups SET last_signed_in_at = ?, sign_ins = sign_ins + 1 WHERE email = ?",
                (stamp, email),
            ).rowcount
            if updated:
                return False
            conn.execute(
                "INSERT INTO signups (email, first_signed_in_at, last_signed_in_at, sign_ins) VALUES (?, ?, ?, 1)",
                (email, stamp, stamp),
            )
            return True

    def signups(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM signups ORDER BY first_signed_in_at, email").fetchall()
        return [dict(r) for r in rows]


# --- The session cookie -----------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_session(secret: bytes, email: str, now: float) -> str:
    payload = _b64(json.dumps({"e": email, "t": int(now)}, separators=(",", ":")).encode())
    mac = _b64(hmac.new(secret, f"session|{payload}".encode(), hashlib.sha256).digest())
    return f"{payload}.{mac}"


def read_session(secret: bytes, value: str | None, now: float) -> str | None:
    """The email a valid, unexpired session names, else None."""
    if not value or value.count(".") != 1:
        return None
    payload, mac = value.split(".")
    expected = _b64(hmac.new(secret, f"session|{payload}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        data = json.loads(_unb64(payload))
        email, issued = data["e"], int(data["t"])
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(email, str) or not (0 <= now - issued <= SESSION_SECONDS):
        return None
    return email


# --- The books --------------------------------------------------------------


class Books:
    """One folder per visitor under books/, named by a keyed hash of the
    email (the email itself is never a file name)."""

    def __init__(self, settings: DemoSettings, clock: Callable[[], float] = time.time):
        self._settings = settings
        self._clock = clock
        self._lock = threading.Lock()
        settings.books_dir.mkdir(parents=True, exist_ok=True)

    def folder_for(self, email: str) -> Path:
        name = hmac.new(self._settings.secret, f"book|{email}".encode(), hashlib.sha256).hexdigest()[:32]
        return self._settings.books_dir / name

    def book_for(self, email: str) -> Path:
        """The visitor's book, copied from the template when they have none."""
        folder = self.folder_for(email)
        book = folder / self._settings.book_filename
        with self._lock:
            if not book.exists():
                self._copy_template(folder, book)
            else:
                self._touch(folder)
        return book

    def reset(self, email: str) -> Path:
        folder = self.folder_for(email)
        book = folder / self._settings.book_filename
        with self._lock:
            shutil.rmtree(folder, ignore_errors=True)
            self._copy_template(folder, book)
        return book

    def _copy_template(self, folder: Path, book: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        partial = folder / (book.name + ".copying")
        source = sqlite3.connect(f"file:{self._settings.template_book}?mode=ro", uri=True)
        target = sqlite3.connect(str(partial))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        partial.replace(book)
        self._touch(folder, force=True)
        if self._settings.on_new_book is not None:
            self._settings.on_new_book(book)

    def _touch(self, folder: Path, force: bool = False) -> None:
        marker = folder / ".last-used"
        now = self._clock()
        try:
            if force or not marker.exists() or now - marker.stat().st_mtime > TOUCH_INTERVAL_SECONDS:
                marker.touch()
                os.utime(marker, (now, now))
        except OSError:
            pass

    def sweep(self) -> int:
        """Deletes every visitor folder unused for idle_days. The signups stay."""
        cutoff = self._clock() - self._settings.idle_days * 86400
        removed = 0
        with self._lock:
            for folder in self._settings.books_dir.iterdir():
                if not folder.is_dir():
                    continue
                marker = folder / ".last-used"
                try:
                    last = marker.stat().st_mtime
                except OSError:
                    last = 0
                if last < cutoff:
                    shutil.rmtree(folder, ignore_errors=True)
                    removed += 1
        return removed


# --- Pages ------------------------------------------------------------------


_STYLE = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--muted:#6b6a66;--line:#dedcd6;--accent:#2f5d50;--danger:#a33a2c}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecebe7;--muted:#a3a19b;--line:#34332f;--accent:#7fb5a3;--danger:#e0806f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:420px;margin:12vh auto 0;padding:0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:28px}
h1{font-size:22px;margin:0 0 4px}p{margin:0 0 16px}.muted{color:var(--muted);font-size:14px}
label{display:block;font-size:14px;margin-bottom:6px}
input{width:100%;font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
button{margin-top:14px;width:100%;font:inherit;font-weight:600;padding:10px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}
.error{border:1px solid var(--danger);color:var(--danger);border-radius:8px;padding:8px 12px;font-size:14px}
a{color:var(--accent)}footer{margin-top:16px;text-align:center}
"""


def _page(title: str, body: str) -> bytes:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>"
    ).encode("utf-8")


def sign_in_page(settings: DemoSettings, *, error: str = "", email: str = "") -> bytes:
    name = html.escape(settings.app_name)
    notice = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return _page(
        f"{settings.app_name} sample",
        f"""<div class="card"><h1>{name} — sample</h1>
<p class="muted">{html.escape(settings.blurb)} Every figure in it is made up.</p>{notice}
<form method="post" action="{SIGN_IN_PATH}">
<label for="email">Your email</label>
<input id="email" name="email" type="email" autocomplete="email" required value="{html.escape(email)}">
<button type="submit">Email me a code</button></form>
<p class="muted" style="margin-top:14px">We send a one-time code to this address and keep it, so we
can tell you when {name} changes. <a href="{PRIVACY_PATH}">Privacy</a></p></div>""",
    )


def code_page(settings: DemoSettings, email: str, *, error: str = "") -> bytes:
    notice = f'<p class="error">{html.escape(error)}</p>' if error else ""
    safe = html.escape(email)
    return _page(
        f"{settings.app_name} sample",
        f"""<div class="card"><h1>Check your email</h1>
<p class="muted">We sent a six-digit code to {safe}. It lasts ten minutes.</p>{notice}
<form method="post" action="{VERIFY_PATH}">
<input type="hidden" name="email" value="{safe}">
<label for="code">Code</label>
<input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9 ]*" required autofocus>
<button type="submit">Open the sample</button></form>
<footer class="muted"><a href="{SIGN_IN_PATH}">Use another email</a></footer></div>""",
    )


def privacy_page(settings: DemoSettings) -> bytes:
    name = html.escape(settings.app_name)
    return _page(
        "Privacy",
        f"""<div class="card"><h1>Privacy</h1>
<p>When you sign in to the {name} sample we keep your email address and when you signed in.
We use it to send you the sign-in code and, now and then, news about {name}. We do not sell it or
share it, and you can ask for it to be deleted by replying to any email from us.</p>
<p>The sample book you work in holds only made-up figures. It is yours alone, and it is deleted
after {settings.idle_days} days without use. Please do not type real financial details into it.</p>
<footer class="muted"><a href="{SIGN_IN_PATH}">Back</a></footer></div>""",
    )


def sample_bar(settings: DemoSettings, email: str) -> bytes:
    """The strip at the foot of the app's own page: what this is, Reset, Sign out."""
    return f"""<div id="demo-bar" style="position:fixed;left:0;right:0;bottom:0;z-index:2147483000;display:flex;
gap:12px;align-items:center;justify-content:center;flex-wrap:wrap;padding:6px 16px;font:13px/1.4 system-ui,sans-serif;
background:#1d1d1b;color:#ecebe7">
<span>{html.escape(settings.app_name)} sample · made-up figures · {html.escape(email)}</span>
<form method="post" action="{RESET_PATH}" style="margin:0" onsubmit="return confirm('Start again from a fresh sample book? Your changes go.')">
<button style="font:inherit;background:none;border:1px solid #6b6a66;color:inherit;border-radius:6px;padding:2px 10px;cursor:pointer">Reset sample</button></form>
<form method="post" action="{SIGN_OUT_PATH}" style="margin:0">
<button style="font:inherit;background:none;border:0;color:inherit;text-decoration:underline;cursor:pointer">Sign out</button></form>
</div><div style="height:36px"></div>""".encode("utf-8")


def _with_bar(send, bar: bytes):
    """Wraps `send`: an HTML answer is held whole and gets the bar before
    </body>, with its length corrected; anything else passes as it is."""
    held: dict = {}

    async def wrapped(message):
        if message["type"] == "http.response.start":
            headers = message.get("headers", [])
            ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
            if message.get("status") == 200 and ctype.startswith(b"text/html") and not any(
                k.lower() == b"content-encoding" for k, _ in headers
            ):
                held["start"] = message
                held["body"] = b""
                return
            await send(message)
            return
        if message["type"] == "http.response.body" and "start" in held:
            held["body"] += message.get("body", b"")
            if message.get("more_body"):
                return
            body = held["body"]
            index = body.lower().rfind(b"</body>")
            body = body[:index] + bar + body[index:] if index >= 0 else body + bar
            start = held.pop("start")
            headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode()))
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return
        await send(message)

    return wrapped


# --- The middleware ---------------------------------------------------------

ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]


async def _send(send, status: int, body: bytes, content_type: str, extra: list[tuple[bytes, bytes]] = ()) -> None:
    headers = [
        (b"content-type", content_type.encode()),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
        *extra,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _html(send, body: bytes, status: int = 200, extra=()) -> None:
    await _send(send, status, body, "text/html; charset=utf-8", list(extra))


async def _json(send, status: int, body: dict) -> None:
    await _send(send, status, json.dumps(body).encode(), "application/json")


async def _redirect(send, location: str, extra=()) -> None:
    await _send(send, 303, b"", "text/plain", [(b"location", location.encode()), *extra])


async def _read_form(receive) -> dict[str, str] | None:
    """The urlencoded body, or None when it is longer than MAX_FORM_BYTES."""
    body = b""
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return None
        body += message.get("body", b"")
        if len(body) > MAX_FORM_BYTES:
            return None
        if not message.get("more_body"):
            break
    fields = parse_qs(body.decode("utf-8", "replace"), max_num_fields=10)
    return {key: values[0] for key, values in fields.items() if values}


def _cookie_from(scope: dict) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == b"cookie":
            jar = SimpleCookie()
            try:
                jar.load(value.decode("latin-1"))
            except Exception:
                continue
            if COOKIE_NAME in jar:
                return jar[COOKIE_NAME].value
    return None


class DemoGate:
    """ASGI middleware, outermost in demo_serve.py's process."""

    def __init__(
        self,
        app: ASGIApp,
        settings: DemoSettings,
        *,
        control: Control | None = None,
        books: Books | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._app = app
        self._settings = settings
        self._clock = clock
        self.control = control or Control(settings.control_path, settings.secret, clock)
        self.books = books or Books(settings, clock)

    def _cookie_header(self, value: str, max_age: int) -> tuple[bytes, bytes]:
        parts = [f"{COOKIE_NAME}={value}", "Path=/", f"Max-Age={max_age}", "HttpOnly", "SameSite=Lax"]
        if self._settings.secure_cookie:
            parts.append("Secure")
        return (b"set-cookie", "; ".join(parts).encode())

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind == "lifespan":
            await self._app(scope, receive, send)
            return
        if kind != "http":
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "/")

        if any(path == p or path.startswith(p + "/") for p in self._settings.hidden_prefixes):
            await _json(send, 404, {"error": "not found"})
            return
        if path == PREFIX or path.startswith(PREFIX + "/"):
            await self._demo_route(method, path, scope, receive, send)
            return

        email = read_session(self._settings.secret, _cookie_from(scope), self._clock())
        if email is None:
            if path.startswith("/api/"):
                await _json(send, 401, SIGNED_OUT_API_BODY)
            else:
                await _redirect(send, SIGN_IN_PATH)
            return
        if (method, path) in self._settings.refused_routes:
            await _json(send, 403, {"error": self._settings.refused_message})
            return
        book = await asyncio.to_thread(self.books.book_for, email)
        if method == "GET" and path in self._settings.bar_paths:
            send = _with_bar(send, sample_bar(self._settings, email))
        await self._app({**scope, VISITOR_SCOPE_KEY: email, BOOK_SCOPE_KEY: str(book)}, receive, send)

    async def _demo_route(self, method, path, scope, receive, send):
        settings = self._settings
        if path == SIGN_IN_PATH and method == "GET":
            await _html(send, sign_in_page(settings))
        elif path == PRIVACY_PATH and method == "GET":
            await _html(send, privacy_page(settings))
        elif path == SIGN_IN_PATH and method == "POST":
            await self._sign_in(receive, send)
        elif path == VERIFY_PATH and method == "POST":
            await self._verify(receive, send)
        elif path == SIGN_OUT_PATH and method == "POST":
            await _redirect(send, SIGN_IN_PATH, [self._cookie_header("", 0)])
        elif path == RESET_PATH and method == "POST":
            email = read_session(settings.secret, _cookie_from(scope), self._clock())
            if email is None:
                await _redirect(send, SIGN_IN_PATH)
                return
            await asyncio.to_thread(self.books.reset, email)
            await _redirect(send, "/")
        elif path == WHO_PATH and method == "GET":
            email = read_session(settings.secret, _cookie_from(scope), self._clock())
            await _json(send, 200, {"email": email, "app": settings.app_name})
        else:
            await _json(send, 404, {"error": "not found"})

    async def _sign_in(self, receive, send):
        settings = self._settings
        form = await _read_form(receive)
        if form is None:
            await _html(send, sign_in_page(settings, error="That did not come through. Try again."), 400)
            return
        email = normalise_email(form.get("email", ""))
        if email is None:
            await _html(
                send,
                sign_in_page(settings, error="That does not look like an email address.", email=form.get("email", "")[:254]),
                400,
            )
            return
        code, why = await asyncio.to_thread(self.control.issue_code, email, settings.max_codes_per_hour)
        if why == "recent":
            await _html(send, code_page(settings, email, error="A code was sent a moment ago. Use that one, or wait a minute."))
            return
        if code is None:
            await _html(send, sign_in_page(settings, error="The sample is busy. Try again in a while.", email=email), 503)
            return
        try:
            await asyncio.to_thread(
                settings.sender.send,
                to=email,
                subject=f"Your {settings.app_name} sample code: {code}",
                text=f"Your code is {code}.\n\nIt opens the {settings.app_name} sample for ten minutes. "
                "If you did not ask for it, ignore this email.",
            )
        except MailFailed:
            await asyncio.to_thread(self.control.withdraw_code, email)
            await _html(send, sign_in_page(settings, error="We could not send the email. Try again shortly.", email=email), 502)
            return
        await _html(send, code_page(settings, email))

    async def _verify(self, receive, send):
        settings = self._settings
        form = await _read_form(receive)
        email = normalise_email((form or {}).get("email", ""))
        if email is None:
            await _redirect(send, SIGN_IN_PATH)
            return
        ok = await asyncio.to_thread(self.control.check_code, email, (form or {}).get("code", ""))
        if not ok:
            await _html(send, code_page(settings, email, error="That code is wrong or has expired."), 400)
            return
        new = await asyncio.to_thread(self.control.record_sign_in, email)
        if new:
            logger.info("new sample sign-up")
            if settings.notify_email:
                await asyncio.to_thread(self._notify, email)
        await asyncio.to_thread(self.books.book_for, email)
        session = make_session(settings.secret, email, self._clock())
        await _redirect(send, "/", [self._cookie_header(session, SESSION_SECONDS)])

    def _notify(self, email: str) -> None:
        try:
            self._settings.sender.send(
                to=self._settings.notify_email,
                subject=f"New {self._settings.app_name} sample sign-up",
                text=f"{email} signed in to the {self._settings.app_name} sample.",
            )
        except MailFailed:
            logger.warning("sign-up notice not sent")
