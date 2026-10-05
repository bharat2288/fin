"""Every request to fin carries a verified Access login, or is refused
(fin-online D1, D2). Copied from folio's tests/test_access_gate.py and adapted
to fin's routes and names; deviations are noted where they occur.

Proof map rows:
- refusal · every route, no token: a census over the composed app's full route
  table (every Flask rule, static, `/mcp`, one unknown path), each 401 with the
  fixed body, the book unchanged. The census reads the live URL map.
- refusal · bad token, cross-gate, OAuth login on app paths.
- allowed · pass-through: the browser reaches app routes; a chat token /mcp.
- the upload credential (operator ruling 2026-10-05): a service token reaches
  exactly the two upload routes and is refused on every other.
- refusal · fail-closed start; allowed · local dev.

Every book is a tmp file; nothing here opens the repo's fin.db.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from flask import Flask, jsonify

import app as fin_app
import serve
from access_gate import (
    ACCESS_IDENTITY_SCOPE_KEY,
    ACTOR_ENVIRON_KEY,
    UPLOAD_ROUTES,
    VIA_ENVIRON_KEY,
    AccessIdentity,
    GateSettings,
    gate_for_request,
    identity_from_environ,
)
from gate_tokens import (
    ACCESS_DENIED,
    APP_AUD,
    AUTH_REQUIRED,
    CHAT_AUD,
    FOREIGN_KEY,
    GATE,
    KID,
    OPERATOR,
    TEAM,
    TEAM_JWKS,
    TEAM_KEY,
    UPLOAD_AUD,
    UPLOAD_CLIENT_ID,
    app_token,
    book,  # noqa: F401 (fixture)
    chat_token,
    composed,
    dump,
    header,
    jwks,
    mint,
    new_key,
    send,
    send_all,
    upload_token,
)

ROOT = Path(__file__).resolve().parent.parent
READ = ("GET", "/api/books")  # a plain read; fin has no health route (D1)


def _sample_path(rule) -> str:
    def fill(match):
        converter = match.group(1) or "default"
        if converter == "int":
            return "1"
        if converter == "path":
            return "styles.css"
        return "x"

    return re.sub(r"<(?:(\w+):)?(\w+)>", fill, rule.rule)


def _census(flask_app=fin_app.app) -> list[tuple[str, str, str]]:
    """(endpoint, method, concrete path) for every method of every Flask rule."""
    return [
        (rule.endpoint, method, _sample_path(rule))
        for rule in flask_app.url_map.iter_rules()
        for method in sorted((rule.methods or set()) - {"HEAD"})
    ]


MCP_PATHS = [("POST", "/mcp"), ("GET", "/mcp"), ("POST", "/mcp/"), ("GET", "/mcp/tools")]
UNKNOWN_PATH = ("GET", "/no-such-route")


def _assert_refused(response, status, body):
    assert response.status_code == status, (response.request.method, response.request.url.path, response.text)
    assert response.json() == body
    assert response.headers["content-type"].startswith("application/json")


def _not_refused(response) -> bool:
    return not (
        response.status_code in (401, 403)
        and response.headers.get("content-type", "").startswith("application/json")
        and response.json() in (AUTH_REQUIRED, ACCESS_DENIED)
    )


def _reached_flask(monkeypatch) -> list[str]:
    """Record every request that gets past the gate into Flask. (folio adds a
    before-request hook; fin's one app has served requests already, so the
    recorder wraps its WSGI callable instead.)"""
    reached: list[str] = []
    inner = fin_app.app.wsgi_app

    def record(environ, start_response):
        reached.append(environ["PATH_INFO"])
        return inner(environ, start_response)

    monkeypatch.setattr(fin_app.app, "wsgi_app", record)
    return reached


def _probe(monkeypatch, endpoint: str) -> None:
    """Swap one view for a probe answering who the request ran as."""
    from flask import request

    def probe(*_args, **_kwargs):
        identity = identity_from_environ(request.environ)
        return jsonify(
            {
                "email": identity.email,
                "gate": identity.gate,
                "via": request.environ.get(VIA_ENVIRON_KEY),
                "actor": request.environ.get(ACTOR_ENVIRON_KEY),
            }
        )

    monkeypatch.setitem(fin_app.app.view_functions, endpoint, probe)


# ---------------------------------------------------------------- no token

def test_every_route_without_a_token_is_refused_401_and_writes_nothing(book, monkeypatch):
    reached = _reached_flask(monkeypatch)
    census = _census()
    endpoints = {endpoint for endpoint, _method, _path in census}
    assert {"static", "index", "api_import_upload", "api_backups_run"} <= endpoints
    assert len(endpoints) == len({r.endpoint for r in fin_app.app.url_map.iter_rules()}) >= 57

    requests_ = [(method, path, {}) for _endpoint, method, path in census]
    requests_ += [(method, path, {}) for method, path in MCP_PATHS]
    requests_ += [(*UNKNOWN_PATH, {})]
    before = dump(book)

    for response in send_all(composed(), requests_):
        _assert_refused(response, 401, AUTH_REQUIRED)
    assert reached == []
    assert dump(book) == before


def test_a_route_added_later_is_swept_by_the_census():
    # Deviation from folio: fin's one module-level app cannot take a new rule
    # once it has served, so a fresh Flask app stands in. The gate is outermost
    # and knows no routes, so any rule it is given is refused the same way.
    other = Flask("added_later")
    other.add_url_rule("/api/added-later", "added_later", lambda: jsonify({"leak": True}), methods=["GET", "POST"])

    census = _census(other)
    assert ("added_later", "POST", "/api/added-later") in census
    for response in send_all(composed(flask_app=other), [(m, p, {}) for e, m, p in census if e == "added_later"]):
        _assert_refused(response, 401, AUTH_REQUIRED)


def test_the_token_is_read_from_the_header_never_a_cookie(book):
    async def run():
        transport = httpx.ASGITransport(app=composed())
        async with httpx.AsyncClient(transport=transport, base_url="http://fin.test") as client:
            client.cookies.set("CF_Authorization", app_token())
            return await client.get(READ[1])

    _assert_refused(asyncio.run(run()), 401, AUTH_REQUIRED)


# ---------------------------------------------------------------- bad token

ROUTE_CLASSES = {
    "page": ("GET", "/"),
    "app read": READ,
    "app write": ("POST", "/api/accounts"),
    "upload": ("POST", "/api/import/upload"),
    "static asset": ("GET", "/static/styles.css"),
    "chat": ("POST", "/mcp"),
    "unknown": UNKNOWN_PATH,
}


def _right_aud(path: str) -> str:
    return CHAT_AUD if path == "/mcp" or path.startswith("/mcp/") else APP_AUD


BAD_TOKENS = {
    "signed by a foreign key under the team's key id": (lambda aud: mint(aud=aud, key=FOREIGN_KEY), 401, AUTH_REQUIRED),
    "signed by a foreign key under an unknown key id": (lambda aud: mint(aud=aud, key=FOREIGN_KEY, kid="foreign-kid"), 401, AUTH_REQUIRED),
    "expired beyond the leeway": (lambda aud: mint(aud=aud, exp=int(time.time()) - 120), 401, AUTH_REQUIRED),
    "not valid yet beyond the leeway": (lambda aud: mint(aud=aud, nbf=int(time.time()) + 300), 401, AUTH_REQUIRED),
    "no expiry": (lambda aud: mint(aud=aud, drop=("exp",)), 401, AUTH_REQUIRED),
    "wrong issuer": (lambda aud: mint(aud=aud, iss="https://otherteam.cloudflareaccess.com"), 401, AUTH_REQUIRED),
    "unsigned (alg none)": (lambda aud: mint(aud=aud, algorithm="none"), 401, AUTH_REQUIRED),
    "malformed": (lambda aud: "not.a.jwt", 401, AUTH_REQUIRED),
    "empty": (lambda aud: "", 401, AUTH_REQUIRED),
    "valid, for another email": (lambda aud: mint(aud=aud, email="someone@example.test"), 403, ACCESS_DENIED),
    "valid, with no email claim": (lambda aud: mint(aud=aud, drop=("email",)), 403, ACCESS_DENIED),
    "valid, for another Access application": (lambda aud: mint(aud="some-other-app-aud"), 403, ACCESS_DENIED),
}


@pytest.mark.parametrize("token_kind", sorted(BAD_TOKENS))
def test_a_bad_token_is_refused_on_every_route_class_writing_nothing(book, token_kind):
    factory, status, body = BAD_TOKENS[token_kind]
    before = dump(book)

    responses = send_all(
        composed(), [(method, path, header(factory(_right_aud(path)))) for method, path in ROUTE_CLASSES.values()]
    )

    for response in responses:
        _assert_refused(response, status, body)
    assert dump(book) == before


def test_the_operator_email_matches_case_insensitively(book):
    assert send(composed(), *READ, header(app_token(email="Operator@Example.TEST"))).status_code == 200


def test_a_token_expired_within_the_60s_leeway_is_still_accepted(book):
    assert send(composed(), *READ, header(app_token(exp=int(time.time()) - 30))).status_code == 200


def test_a_refusal_logs_its_reason_and_never_the_token_or_its_claims(book, caplog):
    token = app_token(email="someone@example.test")

    with caplog.at_level(logging.DEBUG):
        response = send(composed(), *READ, header(token))

    _assert_refused(response, 403, ACCESS_DENIED)
    assert "access refused" in caplog.text
    assert token not in caplog.text
    assert token.split(".")[1] not in caplog.text
    assert "someone@example.test" not in caplog.text
    assert OPERATOR not in caplog.text


# ---------------------------------------------------------------- cross-gate

def test_a_chat_gate_token_is_refused_on_every_app_route(book, monkeypatch):
    reached = _reached_flask(monkeypatch)
    requests_ = [(method, path, header(chat_token())) for _e, method, path in _census()]
    requests_.append((*UNKNOWN_PATH, header(chat_token())))
    before = dump(book)

    for response in send_all(composed(), requests_):
        _assert_refused(response, 403, ACCESS_DENIED)
    assert reached == []
    assert dump(book) == before


def _mcp_probe(seen: list):
    async def probe(scope, receive, send_):
        seen.append(scope.get(ACCESS_IDENTITY_SCOPE_KEY))
        await send_({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send_({"type": "http.response.body", "body": b"mcp reached"})

    return probe


def test_an_app_gate_token_is_refused_on_mcp(book):
    seen: list = []

    for response in send_all(composed(mcp_app=_mcp_probe(seen)), [(m, p, header(app_token())) for m, p in MCP_PATHS]):
        _assert_refused(response, 403, ACCESS_DENIED)
    assert seen == []


def test_one_function_decides_the_gate_by_path():
    def scope(path):
        return {"type": "http", "path": path, "headers": [(b"host", b"fin.bhasuri.ai")]}

    assert gate_for_request(scope("/mcp")) == "chat"
    assert gate_for_request(scope("/mcp/")) == "chat"
    assert gate_for_request(scope("/mcp/tools/call")) == "chat"
    assert gate_for_request(scope("/mcp/../api/books")) == "chat"
    assert gate_for_request(scope("/")) == "app"
    assert gate_for_request(scope("/mcpx")) == "app"
    assert gate_for_request(scope("/api/mcp")) == "app"
    assert gate_for_request(scope("/MCP")) == "app"
    assert gate_for_request(scope("//mcp")) == "app"


def test_a_chat_owned_path_never_reaches_flask_even_with_dot_segments(book, monkeypatch):
    reached = _reached_flask(monkeypatch)
    seen: list = []
    asgi = composed(mcp_app=_mcp_probe(seen))

    # httpx normalises dot segments away, so this drives the ASGI boundary
    # directly with the path as a server can hand it over.
    async def run():
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/mcp/../api/accounts",
            "raw_path": b"/mcp/../api/accounts",
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"fin.test"), (b"cf-access-jwt-assertion", chat_token().encode())],
            "client": ("127.0.0.1", 50000),
            "server": ("fin.test", 80),
        }
        sent = []
        received = iter([{"type": "http.request", "body": b"{}", "more_body": False}])

        async def receive():
            return next(received, {"type": "http.disconnect"})

        async def send_(message):
            sent.append(message)

        await asgi(scope, receive, send_)
        return sent

    sent = asyncio.run(run())

    assert sent[0]["status"] == 200
    assert len(seen) == 1 and reached == []


# ---------------------------------------------------------------- pass-through

def test_an_app_gate_token_reaches_every_app_read_route(book):
    reads = [(m, p, header(app_token())) for _e, m, p in _census() if m == "GET"]
    assert len(reads) >= 25

    for response in send_all(composed(), reads):
        assert _not_refused(response), response.request.url.path


def test_an_app_gate_token_reaches_an_app_write_route(book):
    async def run():
        transport = httpx.ASGITransport(app=composed())
        async with httpx.AsyncClient(transport=transport, base_url="http://fin.test") as client:
            return await client.post("/api/accounts", headers=header(app_token()), json={"name": "Gate Account"})

    assert asyncio.run(run()).status_code in (200, 201)
    names = [row[1] for row in dump(book)["accounts"]]
    assert "Gate Account" in names


def test_the_browser_reaches_flask_as_the_app_and_fin(book, monkeypatch):
    _probe(monkeypatch, "api_books")

    response = send(composed(), *READ, header(app_token(email="Operator@Example.test")))

    assert response.status_code == 200
    assert response.json() == {"email": OPERATOR, "gate": "app", "via": "app", "actor": "fin"}


def test_a_chat_gate_token_reaches_mcp_with_the_verified_identity(book):
    seen: list = []

    response = send(composed(mcp_app=_mcp_probe(seen)), "POST", "/mcp", header(chat_token()))

    assert response.status_code == 200
    assert response.text == "mcp reached"
    assert [(i.email, i.gate, i.via, i.actor) for i in seen] == [(OPERATOR, "chat", "chat", "chat:fake-subject-1")]


# ---------------------------------------------------------------- OAuth logins on app paths

OAUTH_CLAIM = {"client": "fake-chat-client"}
BOTH_AUDS = [APP_AUD, CHAT_AUD]


def test_an_oauth_login_carrying_both_auds_is_refused_on_every_app_route(book, monkeypatch, caplog):
    reached = _reached_flask(monkeypatch)
    requests_ = [(m, p, header(mint(aud=BOTH_AUDS, oauth=OAUTH_CLAIM))) for _e, m, p in _census()]
    requests_.append((*UNKNOWN_PATH, header(mint(aud=BOTH_AUDS, oauth=OAUTH_CLAIM))))
    before = dump(book)

    with caplog.at_level(logging.INFO, logger="fin.access"):
        responses = send_all(composed(), requests_)

    for response in responses:
        _assert_refused(response, 403, ACCESS_DENIED)
    assert reached == []
    assert caplog.text.count("access refused: gate=app reason=oauth-login-on-app-gate") == len(requests_)
    assert dump(book) == before


@pytest.mark.parametrize("oauth_value", [OAUTH_CLAIM, {}, "", None, "x"])
@pytest.mark.parametrize("auds", [BOTH_AUDS, [APP_AUD], [APP_AUD, UPLOAD_AUD]])
def test_any_oauth_claim_is_refused_on_an_app_route_whatever_its_value(book, auds, oauth_value):
    before = dump(book)
    routes = (READ, ("POST", "/api/accounts"), ("POST", "/api/import/upload"))

    for response in send_all(composed(), [(m, p, header(mint(aud=auds, oauth=oauth_value))) for m, p in routes]):
        _assert_refused(response, 403, ACCESS_DENIED)
    assert dump(book) == before


def test_a_browser_login_carrying_both_auds_passes_on_every_app_read_route(book):
    reads = [(m, p, header(mint(aud=BOTH_AUDS))) for _e, m, p in _census() if m == "GET"]

    for response in send_all(composed(), reads):
        assert _not_refused(response), response.request.url.path


@pytest.mark.parametrize("auds", [[CHAT_AUD], BOTH_AUDS])
def test_a_chat_token_with_an_oauth_claim_passes_on_mcp(book, auds):
    seen: list = []

    response = send(composed(mcp_app=_mcp_probe(seen)), "POST", "/mcp", header(mint(aud=auds, oauth=OAUTH_CLAIM)))

    assert response.status_code == 200
    assert [(i.email, i.gate, i.oauth) for i in seen] == [(OPERATOR, "chat", True)]


# ---------------------------------------------------------------- the upload credential

UPLOAD_ENDPOINTS = {"api_import_upload", "api_import_confirm"}


def test_the_upload_routes_are_exactly_the_two_import_posts():
    rules = {(m, p) for e, m, p in _census() if e in UPLOAD_ENDPOINTS and m == "POST"}
    assert rules == set(UPLOAD_ROUTES)


@pytest.mark.parametrize("endpoint", sorted(UPLOAD_ENDPOINTS))
def test_the_service_token_reaches_each_upload_route_as_claude_code(book, monkeypatch, endpoint):
    _probe(monkeypatch, endpoint)
    path = "/api/import/upload" if endpoint == "api_import_upload" else "/api/import/confirm"

    response = send(composed(), "POST", path, header(upload_token()))

    assert response.status_code == 200, response.text
    assert response.json() == {"email": "", "gate": "app", "via": "app", "actor": "Claude Code (upload)"}


def test_the_service_token_is_refused_on_every_other_route(book, monkeypatch, caplog):
    reached = _reached_flask(monkeypatch)
    census = [(m, p) for _e, m, p in _census() if (m, p) not in UPLOAD_ROUTES]
    requests_ = [(m, p, header(upload_token())) for m, p in census + MCP_PATHS + [UNKNOWN_PATH]]
    requests_ += [(m, p, header(upload_token(aud=[UPLOAD_AUD, APP_AUD, CHAT_AUD]))) for m, p in census + MCP_PATHS]
    # The upload paths by another method, or with a trailing slash.
    requests_ += [("GET", "/api/import/upload", header(upload_token())), ("POST", "/api/import/upload/", header(upload_token()))]
    before = dump(book)

    with caplog.at_level(logging.INFO, logger="fin.access"):
        responses = send_all(composed(), requests_)

    for response in responses:
        _assert_refused(response, 403, ACCESS_DENIED)
    assert reached == []
    assert caplog.text.count("reason=service-token-off-upload") == len(requests_)
    assert dump(book) == before


@pytest.mark.parametrize(
    "token",
    [
        lambda: upload_token(common_name="another-client.access"),
        lambda: upload_token(aud=APP_AUD),
        lambda: upload_token(oauth=OAUTH_CLAIM),
        lambda: upload_token(key=FOREIGN_KEY),
    ],
    ids=["another-service-token", "app-aud-not-upload-aud", "with-oauth", "foreign-key"],
)
def test_a_wrong_service_token_is_refused_on_the_upload_routes(book, token):
    before = dump(book)

    for response in send_all(composed(), [("POST", p, header(token())) for _m, p in sorted(UPLOAD_ROUTES)]):
        assert response.status_code in (401, 403)
        assert response.json() in (AUTH_REQUIRED, ACCESS_DENIED)
    assert dump(book) == before


def test_without_the_upload_settings_the_service_token_is_refused_everywhere(book):
    gate = GateSettings(team_domain=TEAM, app_aud=APP_AUD, chat_aud=CHAT_AUD, operator_email=OPERATOR)

    for response in send_all(composed(gate=gate), [("POST", p, header(upload_token())) for _m, p in sorted(UPLOAD_ROUTES)]):
        _assert_refused(response, 403, ACCESS_DENIED)


def test_the_browser_through_the_upload_application_reaches_the_upload_routes_only(book, monkeypatch):
    # The third Access application owns the two paths, so the operator's
    # browser login there may carry the upload AUD alone.
    _probe(monkeypatch, "api_import_upload")
    asgi = composed()

    allowed = send(asgi, "POST", "/api/import/upload", header(mint(aud=UPLOAD_AUD)))
    elsewhere = send(asgi, "POST", "/api/accounts", header(mint(aud=UPLOAD_AUD)))

    assert allowed.json()["actor"] == "fin"
    _assert_refused(elsewhere, 403, ACCESS_DENIED)


# ---------------------------------------------------------------- keys: cached, refetched, closed

class _Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class _KeySource:
    def __init__(self, keys, *, failing=False):
        self.jwks = keys
        self.failing = failing
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.failing:
            raise OSError("certs endpoint unreachable")
        return self.jwks


def test_keys_are_fetched_once_and_cached(book):
    source = _KeySource(TEAM_JWKS)
    responses = send_all(composed(fetch_jwks=source, clock=_Clock()), [(*READ, header(app_token()))] * 3)

    assert [r.status_code for r in responses] == [200, 200, 200]
    assert source.calls == 1


def test_an_unknown_key_id_refetches_at_most_once_per_five_minutes(book):
    rotated = new_key()
    source = _KeySource(TEAM_JWKS)
    clock = _Clock()
    asgi = composed(fetch_jwks=source, clock=clock)
    assert send(asgi, *READ, header(app_token())).status_code == 200
    assert source.calls == 1

    source.jwks = jwks((TEAM_KEY, KID), (rotated, "rotated-kid"))
    clock.now += 301
    assert send(asgi, *READ, header(app_token(key=rotated, kid="rotated-kid"))).status_code == 200
    assert source.calls == 2

    clock.now += 60
    _assert_refused(send(asgi, *READ, header(mint(key=FOREIGN_KEY, kid="another-kid"))), 401, AUTH_REQUIRED)
    assert source.calls == 2

    clock.now += 300
    send(asgi, *READ, header(mint(key=FOREIGN_KEY, kid="another-kid")))
    assert source.calls == 3


def test_unfetchable_keys_refuse_every_request_until_they_can_be_fetched(book):
    source = _KeySource(TEAM_JWKS, failing=True)
    clock = _Clock()
    asgi = composed(fetch_jwks=source, clock=clock)
    before = dump(book)

    for method, path in (READ, ("POST", "/api/accounts"), ("POST", "/mcp")):
        _assert_refused(send(asgi, method, path, header(mint(aud=_right_aud(path)))), 401, AUTH_REQUIRED)
    assert dump(book) == before

    source.failing = False
    clock.now += 301
    assert send(asgi, *READ, header(app_token())).status_code == 200


def test_a_malformed_key_set_fails_closed(book):
    source = _KeySource({"keys": [{"kty": "RSA", "kid": KID, "n": "!!", "e": "AQAB"}]})

    _assert_refused(send(composed(fetch_jwks=source, clock=_Clock()), *READ, header(app_token())), 401, AUTH_REQUIRED)


# ---------------------------------------------------------------- second layer: Flask without the gate

def test_flask_served_without_the_gate_refuses_every_route(book):
    client = fin_app.app.test_client()
    before = dump(book)

    for _endpoint, method, path in _census() + [("unknown", *UNKNOWN_PATH)]:
        # A client-supplied Access header is never trusted by Flask itself.
        response = client.open(path, method=method, json={}, headers=header(app_token()))
        assert response.status_code == 401, (method, path)
        assert response.get_json() == AUTH_REQUIRED
    assert dump(book) == before


def test_flask_refuses_a_chat_identity_that_reaches_it(book):
    scope = {ACCESS_IDENTITY_SCOPE_KEY: AccessIdentity(email=OPERATOR, gate="chat", client="chat:fake")}

    response = fin_app.app.test_client().get(READ[1], environ_base={"asgi.scope": scope})

    assert response.status_code == 403
    assert response.get_json() == ACCESS_DENIED


# ---------------------------------------------------------------- fail-closed start

FULL_ENV = {
    "FIN_ACCESS_TEAM_DOMAIN": TEAM,
    "FIN_ACCESS_APP_AUD": APP_AUD,
    "FIN_ACCESS_CHAT_AUD": CHAT_AUD,
    "FIN_OPERATOR_EMAIL": OPERATOR,
    "FIN_UPLOAD_AUD": UPLOAD_AUD,
    "FIN_UPLOAD_CLIENT_ID": UPLOAD_CLIENT_ID,
    "FIN_DB_PATH": "/volume/fin.db",
}
REQUIRED = ["FIN_ACCESS_TEAM_DOMAIN", "FIN_ACCESS_APP_AUD", "FIN_ACCESS_CHAT_AUD", "FIN_OPERATOR_EMAIL", "FIN_DB_PATH"]


def test_a_complete_enforced_environment_loads_with_the_gate_on():
    config = serve.load_runtime_config({**FULL_ENV, "PORT": "8080"})

    assert config.local_dev is False
    assert config.host == "0.0.0.0"
    assert config.port == 8080
    assert config.db_path == "/volume/fin.db"
    assert config.gate == GATE


def test_the_upload_pair_is_optional_both_or_neither():
    env = {k: v for k, v in FULL_ENV.items() if not k.startswith("FIN_UPLOAD_")}
    assert serve.load_runtime_config(env).gate.upload_aud == ""

    for present, absent in (("FIN_UPLOAD_AUD", "FIN_UPLOAD_CLIENT_ID"), ("FIN_UPLOAD_CLIENT_ID", "FIN_UPLOAD_AUD")):
        with pytest.raises(serve.StartupRefused) as refused:
            serve.load_runtime_config({**env, present: FULL_ENV[present]})
        assert absent in str(refused.value)
        assert FULL_ENV[present] not in str(refused.value)


@pytest.mark.parametrize("missing", REQUIRED)
@pytest.mark.parametrize("how", ["absent", "empty"])
def test_a_missing_gate_setting_refuses_to_start_naming_the_variable(missing, how):
    env = dict(FULL_ENV)
    if how == "absent":
        env.pop(missing)
    else:
        env[missing] = "  "

    with pytest.raises(serve.StartupRefused) as refused:
        serve.load_runtime_config(env)

    message = str(refused.value)
    assert missing in message
    for name, value in FULL_ENV.items():
        if name != missing:
            assert value not in message


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "fin.bhasuri.ai", "::"])
def test_local_dev_with_a_non_loopback_host_refuses_to_start(host):
    with pytest.raises(serve.StartupRefused) as refused:
        serve.load_runtime_config({"FIN_LOCAL_DEV": "1", "FIN_HOST": host})

    assert "FIN_LOCAL_DEV" in str(refused.value)
    assert "loopback" in str(refused.value)


def test_an_unreadable_local_dev_flag_refuses_to_start():
    with pytest.raises(serve.StartupRefused) as refused:
        serve.load_runtime_config({**FULL_ENV, "FIN_LOCAL_DEV": "maybe"})

    assert "FIN_LOCAL_DEV" in str(refused.value)


def test_a_bad_port_refuses_to_start():
    with pytest.raises(serve.StartupRefused) as refused:
        serve.load_runtime_config({**FULL_ENV, "PORT": "http"})

    assert "PORT" in str(refused.value)


def _child_env(extra: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("FIN_") and k != "PORT"}
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def test_the_service_process_refuses_to_start_naming_the_missing_variable():
    env = {k: v for k, v in FULL_ENV.items() if k != "FIN_ACCESS_CHAT_AUD"}

    result = subprocess.run(
        [sys.executable, str(ROOT / "serve.py")], cwd=ROOT, env=_child_env(env), capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 2
    assert "FIN_ACCESS_CHAT_AUD" in result.stderr
    for value in env.values():
        assert value not in result.stderr
        assert value not in result.stdout


def test_the_service_process_refuses_local_dev_on_a_public_host():
    result = subprocess.run(
        [sys.executable, str(ROOT / "serve.py")],
        cwd=ROOT,
        env=_child_env({"FIN_LOCAL_DEV": "1", "FIN_HOST": "0.0.0.0"}),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 2
    assert "FIN_LOCAL_DEV" in result.stderr


# ---------------------------------------------------------------- local dev

def test_local_dev_on_loopback_serves_without_a_token_as_fin(book, monkeypatch):
    config = serve.load_runtime_config({"FIN_LOCAL_DEV": "1", "FIN_HOST": "127.0.0.1", "FIN_DB_PATH": str(book)})
    assert config.local_dev is True and config.gate is None
    flask_app, asgi = serve.build_asgi_app(config)
    _probe(monkeypatch, "api_books")

    probe = send(asgi, *READ)

    assert probe.json() == {"email": "local-dev", "gate": "app", "via": "app", "actor": "fin"}
    assert flask_app.config["LOCAL_DEV"] is True


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_local_dev_accepts_each_loopback_host(host):
    config = serve.load_runtime_config({"FIN_LOCAL_DEV": "1", "FIN_HOST": host})

    assert config.local_dev is True
    assert config.host == host


def test_local_dev_defaults_to_the_loopback_host():
    assert serve.load_runtime_config({"FIN_LOCAL_DEV": "1"}).host == "127.0.0.1"


def test_python_app_py_serves_on_loopback_as_local_dev(monkeypatch, temp_db):
    # Deviation from folio: fin's app.py takes no --host; it always binds
    # loopback and turns local-dev on for itself.
    ran = {}
    monkeypatch.setitem(fin_app.app.config, "LOCAL_DEV", False)
    monkeypatch.setattr(fin_app.app, "run", lambda **kw: ran.update(kw, local_dev=fin_app.app.config["LOCAL_DEV"]))

    assert fin_app.main(["--port", "8450"]) == 0
    assert ran["host"] == "127.0.0.1" and ran["local_dev"] is True
