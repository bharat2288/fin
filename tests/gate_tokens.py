"""Shared by the hosting tests: a fake Access team, tokens minted with a test
RSA key pair, the composed app on a tmp book, and an HTTP driver. Nothing
here reaches Cloudflare; every setting is a fake."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

import app as fin_app
import db
import serve
from access_gate import GateSettings

TEAM = "testteam"
ISSUER = "https://testteam.cloudflareaccess.com"
APP_AUD = "app-aud-fake-0001"
CHAT_AUD = "chat-aud-fake-0002"
UPLOAD_AUD = "upload-aud-fake-0003"
UPLOAD_CLIENT_ID = "upload-client-fake.access"
OPERATOR = "operator@example.test"
KID = "fake-kid-1"

# The spec's two fixed refusal bodies (D1), written out here, never imported.
AUTH_REQUIRED = {"error": "authentication required"}
ACCESS_DENIED = {"error": "access denied"}

MUTATING_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


def new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


TEAM_KEY = new_key()
FOREIGN_KEY = new_key()


def jwk(private_key, kid: str) -> dict:
    out = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    out.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return out


def jwks(*entries) -> dict:
    return {"keys": [jwk(key, kid) for key, kid in entries]}


TEAM_JWKS = jwks((TEAM_KEY, KID))


def mint(*, aud=APP_AUD, key=TEAM_KEY, kid=KID, algorithm="RS256", drop=(), **overrides) -> str:
    now = int(time.time())
    claims = {
        "aud": list(aud) if isinstance(aud, (list, tuple)) else [aud],
        "email": OPERATOR,
        "exp": now + 600,
        "iat": now,
        "nbf": now,
        "iss": ISSUER,
        "sub": "fake-subject-1",
        "type": "app",
    }
    claims.update(overrides)
    for name in drop:
        claims.pop(name, None)
    signing_key = key if algorithm.startswith("RS") else None
    return jwt.encode(claims, signing_key, algorithm=algorithm, headers={"kid": kid})


def app_token(**overrides) -> str:
    return mint(aud=APP_AUD, **overrides)


def chat_token(**overrides) -> str:
    return mint(aud=CHAT_AUD, **overrides)


def upload_token(**overrides) -> str:
    """A service token's login: the upload AUD, `common_name`, no email."""
    claims = {"aud": UPLOAD_AUD, "common_name": UPLOAD_CLIENT_ID, "sub": "", "drop": ("email",), **overrides}
    return mint(**claims)


def header(token: str) -> dict:
    return {"Cf-Access-Jwt-Assertion": token}


GATE = GateSettings(
    team_domain=TEAM,
    app_aud=APP_AUD,
    chat_aud=CHAT_AUD,
    operator_email=OPERATOR,
    upload_aud=UPLOAD_AUD,
    upload_client_id=UPLOAD_CLIENT_ID,
)


@pytest.fixture
def book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp book that fin's one Flask app serves, with the gate's second
    layer on (LOCAL_DEV off) and backups not configured."""
    db_path = tmp_path / "book" / "fin.db"
    db_path.parent.mkdir()
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setitem(fin_app.app.config, "LOCAL_DEV", False)
    monkeypatch.setitem(fin_app.app.config, "BACKUP_STORE", None)
    monkeypatch.setitem(fin_app.app.config, "TESTING", True)
    db.invalidate_rules_cache()
    db.init_db()
    yield db_path
    db.invalidate_rules_cache()


def composed(*, gate=GATE, fetch_jwks=None, mcp_app=None, clock=None, flask_app=None):
    return serve.compose(
        flask_app or fin_app.app,
        gate_settings=gate,
        local_dev=False,
        fetch_jwks=fetch_jwks or (lambda: TEAM_JWKS),
        mcp_app=mcp_app,
        clock=clock,
    )


def send_all(asgi, requests_: list[tuple[str, str, dict]]) -> list[httpx.Response]:
    """Drive the composed ASGI app over HTTP, one response per request."""

    async def run():
        transport = httpx.ASGITransport(app=asgi)
        async with httpx.AsyncClient(transport=transport, base_url="http://fin.test") as client:
            out = []
            for method, path, headers in requests_:
                body = b"{}" if method in MUTATING_METHODS else None
                hdrs = dict(headers)
                if body is not None:
                    hdrs["Content-Type"] = "application/json"
                out.append(await client.request(method, path, headers=hdrs, content=body))
            return out

    return asyncio.run(run())


def send(asgi, method, path, headers=None) -> httpx.Response:
    return send_all(asgi, [(method, path, headers or {})])[0]


@asynccontextmanager
async def running(asgi):
    """Drive the ASGI lifespan as the server does: startup before the first
    request, shutdown after the last (the MCP side needs it)."""
    inbox: asyncio.Queue = asyncio.Queue()
    outbox: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(asgi({"type": "lifespan", "asgi": {"version": "3.0"}}, inbox.get, outbox.put))
    await inbox.put({"type": "lifespan.startup"})
    started = await outbox.get()
    assert started["type"] == "lifespan.startup.complete", started
    try:
        yield
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        await outbox.get()
        await task


def dump(db_path) -> dict:
    """Every row of every table, in rowid order: the book's whole logical content."""
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {table: conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in tables}
    finally:
        conn.close()
