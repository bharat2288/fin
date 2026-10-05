"""The access gate: fin's in-app lock (fin-online spec D1; copied from folio's modules/access_gate.py).

Every request must carry Cloudflare Access's signed login note, the
`Cf-Access-Jwt-Assertion` header, verified here against the team's public keys
for the gate that owns the request's path and for the operator's email.
Anything else is refused with one of two fixed bodies before any route runs.

Why fin checks at all when Cloudflare Access sits in front: on Railway a
request sent straight to Railway's edge, naming the custom domain, reaches the
server with no login (folio-live 05's phone test). Access alone does not guard
fin; this module does.

Two gates, one per Access application:
- the chat gate owns `/mcp` and everything under it (chat clients);
- the app gate owns every other path (the operator's browser).
`gate_for_request` is the one function that decides which gate owns a request;
ticket 21 (the two-gate isolation proof) kept it path-based.

Per-gate acceptance (spec Amendments 2026-10-01, ticket 65): the chat gate
needs the chat AUD; the app gate needs the app AUD AND no `oauth` claim,
because a chat (Managed OAuth) login's token can carry both AUDs.

The upload credential (fin-online, operator ruling 2026-10-05): a Cloudflare
Access service token, behind a third Access application whose policy reaches
only the two upload routes. Its JWT carries `common_name` (the token's client
id) and no email. It is accepted on exactly UPLOAD_ROUTES, with the upload
AUD and the configured client id, and refused (403) on every other path. With
its two settings unset the service token is refused everywhere. Its writes
are a chat client's: stamped `fin.via='chat'`, `fin.actor='Claude Code
(upload)'` (the MCP thread's ruling); the browser stays app/fin.

The chat tools (mcp_tools.py) run fin's own routes in-process, after the chat
gate verified the client at /mcp. They hand Flask the chat identity under
CHAT_CALL_ENVIRON_KEY, an environ key no HTTP request can set (a2wsgi builds
the environ from the scope, and a client's headers only ever become HTTP_*
strings); RequireGateIdentity admits a chat identity only from there.

Refusals: 401 when the token is missing, malformed, badly signed, from another
issuer or outside its validity window; 403 when it is valid but for the wrong
gate (including an OAuth-issued login on an app-gate path) or the wrong email.
The reason code is logged. The token and its claims never are.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

# jwt (PyJWT) and requests are imported where they are used, so that the
# desk app (python app.py, local-dev) starts with fin's requirements from
# before hosting: only a gate that verifies a token needs them.


logger = logging.getLogger("fin.access")

APP_GATE = "app"
CHAT_GATE = "chat"

# Where the gate places the verified identity: a key in the ASGI scope, which
# the WSGI adapter carries into Flask's environ as `asgi.scope`. A client can
# set headers but never scope keys, so this cannot be forged from outside.
ACCESS_IDENTITY_SCOPE_KEY = "fin.access_identity"

ACCESS_HEADER = b"cf-access-jwt-assertion"

AUTH_REQUIRED_STATUS = 401
AUTH_REQUIRED_BODY = {"error": "authentication required"}
ACCESS_DENIED_STATUS = 403
ACCESS_DENIED_BODY = {"error": "access denied"}

LOCAL_DEV_EMAIL = "local-dev"
LEEWAY_SECONDS = 60
# Refetch the key set for an unknown key id at most this often (D1).
KEY_REFETCH_INTERVAL_SECONDS = 300
# While no key set is held at all, retry the fetch at most this often, so a
# failed fetch at boot does not lock the operator out for five minutes and a
# stream of tokens cannot turn into a stream of fetches.
KEY_RETRY_INTERVAL_SECONDS = 10
KEY_FETCH_TIMEOUT_SECONDS = 5

_TEAM_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TEAM_SUFFIX = ".cloudflareaccess.com"


class GateConfigError(ValueError):
    """A gate setting is present but unusable. The message names the setting, never its value."""


@dataclass(frozen=True)
class GateSettings:
    """The gate's settings (environment only; see serve.py). The upload pair
    is optional: both or neither."""

    team_domain: str
    app_aud: str
    chat_aud: str
    operator_email: str
    upload_aud: str = ""
    upload_client_id: str = ""

    @property
    def team(self) -> str:
        """The team name, accepting either `team` or `team.cloudflareaccess.com`."""
        value = self.team_domain.strip().lower()
        if value.startswith("https://"):
            value = value[len("https://"):]
        value = value.rstrip("/")
        if value.endswith(_TEAM_SUFFIX):
            value = value[: -len(_TEAM_SUFFIX)]
        if not _TEAM_NAME.match(value):
            raise GateConfigError("FIN_ACCESS_TEAM_DOMAIN is not a Cloudflare Access team name")
        return value

    @property
    def issuer(self) -> str:
        return f"https://{self.team}{_TEAM_SUFFIX}"

    @property
    def certs_url(self) -> str:
        return f"{self.issuer}/cdn-cgi/access/certs"

    def aud_for(self, gate: str) -> str:
        return self.chat_aud if gate == CHAT_GATE else self.app_aud


@dataclass(frozen=True)
class AccessIdentity:
    """The verified identity of one request.

    `oauth` is True when the verified token carries an `oauth` claim: Access
    marks every OAuth-issued (chat, Managed OAuth) login with it, and a
    browser login never has it (ticket 21). A chat login can carry the app
    gate's AUD too, so only this flag tells it from the operator's browser
    (folio's spec, Amendments 2026-10-01). The gate refuses such a login
    on every app-gate path (ticket 65), so an app-gate identity always has
    `oauth` False.
    """

    email: str
    gate: str
    client: str | None = None
    oauth: bool = False

    @property
    def via(self) -> str:
        """`app` or `chat`: the side of fin the request came through. The
        upload credential passes the app gate but is a chat client's (Claude
        Code), so its writes are stamped as chat."""
        if self.gate == APP_GATE and self.client == UPLOAD_ACTOR:
            return CHAT_GATE
        return self.gate

    @property
    def actor(self) -> str:
        """Who acts: the client the token names, else fin itself (the browser)."""
        return self.client or APP_ACTOR


# The two routes the upload credential may reach: (method, exact path).
UPLOAD_ROUTES = frozenset({("POST", "/api/import/upload"), ("POST", "/api/import/confirm")})
APP_ACTOR = "fin"
UPLOAD_ACTOR = "Claude Code (upload)"

# The WSGI environ keys Flask reads for who made a request (the change
# history): set on every request that reaches Flask, from the gate's identity.
VIA_ENVIRON_KEY = "fin.via"
ACTOR_ENVIRON_KEY = "fin.actor"
# Where an in-process chat call (mcp_tools.call) puts the chat identity the
# chat gate verified. Only a caller inside the process can set it.
CHAT_CALL_ENVIRON_KEY = "fin.chat_call"


def chat_call_identity(actor: str) -> AccessIdentity:
    """The identity an in-process chat call carries into Flask."""
    return AccessIdentity(email="", gate=CHAT_GATE, client=actor or "chat")


def is_upload_route(scope: dict) -> bool:
    return (scope.get("method"), scope.get("path")) in UPLOAD_ROUTES


def gate_for_request(scope: dict) -> str:
    """The one function that decides which gate owns a request.

    `/mcp` and anything under it belong to the chat gate; every other path to
    the app gate. The router sends each request to the side its gate owns, so
    a request the chat gate owns never reaches the Flask app.
    """
    path = scope.get("path", "")
    if path == "/mcp" or path.startswith("/mcp/"):
        return CHAT_GATE
    return APP_GATE


def local_dev_identity(gate: str = APP_GATE) -> AccessIdentity:
    return AccessIdentity(email=LOCAL_DEV_EMAIL, gate=gate, client=None)


def identity_from_environ(environ: dict) -> AccessIdentity | None:
    """The gate's identity as Flask sees it, or None when no gate placed one."""
    scope = environ.get("asgi.scope")
    if not isinstance(scope, dict):
        return None
    identity = scope.get(ACCESS_IDENTITY_SCOPE_KEY)
    return identity if isinstance(identity, AccessIdentity) else None


class AccessRefused(Exception):
    """A refusal. `reason` is a fixed code, safe to log; nothing else is carried."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _unauthenticated(reason: str) -> AccessRefused:
    return AccessRefused(AUTH_REQUIRED_STATUS, reason)


def _denied(reason: str) -> AccessRefused:
    return AccessRefused(ACCESS_DENIED_STATUS, reason)


def fetch_team_jwks(certs_url: str) -> dict:
    import requests

    response = requests.get(certs_url, timeout=KEY_FETCH_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


class AccessKeys:
    """The team's public keys: fetched once, cached in memory, refetched when a
    token names an unknown key id (at most once per five minutes). If the keys
    cannot be fetched, lookups fail and every request is refused until they can.
    """

    def __init__(
        self,
        fetch_jwks: Callable[[], dict],
        *,
        clock: Callable[[], float] = time.monotonic,
        refetch_interval: float = KEY_REFETCH_INTERVAL_SECONDS,
        retry_interval: float = KEY_RETRY_INTERVAL_SECONDS,
    ):
        self._fetch_jwks = fetch_jwks
        self._clock = clock
        self._refetch_interval = refetch_interval
        self._retry_interval = retry_interval
        self._keys: dict[str, Any] | None = None
        self._last_attempt: float | None = None
        self._lock = threading.Lock()

    def _may_fetch(self) -> bool:
        if self._last_attempt is None:
            return True
        interval = self._refetch_interval if self._keys is not None else self._retry_interval
        return self._clock() - self._last_attempt >= interval

    def _fetch(self) -> None:
        self._last_attempt = self._clock()
        try:
            import jwt

            key_set = jwt.PyJWKSet.from_dict(self._fetch_jwks())
            keys = {
                key.key_id: key.key
                for key in key_set.keys
                if key.key_id and key.key_type == "RSA"
            }
        except Exception:
            # Fail closed: keep whatever was held before (possibly nothing).
            logger.warning("access keys unavailable: fetch-failed")
            return
        if keys:
            self._keys = keys
        else:
            logger.warning("access keys unavailable: no-usable-keys")

    def key_for(self, kid: str):
        with self._lock:
            if self._keys is None or kid not in self._keys:
                if self._may_fetch():
                    self._fetch()
            if self._keys is None:
                raise _unauthenticated("keys-unavailable")
            key = self._keys.get(kid)
            if key is None:
                raise _unauthenticated("unknown-key-id")
            return key


class AccessVerifier:
    """Verifies one token for the gate that owns the request (D1)."""

    def __init__(self, settings: GateSettings, keys: AccessKeys):
        self._settings = settings
        self._issuer = settings.issuer
        self._operator_email = settings.operator_email.strip().lower()
        self._keys = keys

    def verify(self, token: str | None, gate: str, *, upload_route: bool = False) -> AccessIdentity:
        import jwt

        if not token:
            raise _unauthenticated("missing-token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise _unauthenticated("malformed-token") from None
        if header.get("alg") != "RS256":
            raise _unauthenticated("wrong-algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise _unauthenticated("missing-key-id")
        key = self._keys.key_for(kid)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iss"], "verify_aud": False},
            )
        except jwt.ExpiredSignatureError:
            raise _unauthenticated("expired") from None
        except jwt.ImmatureSignatureError:
            raise _unauthenticated("not-yet-valid") from None
        except jwt.InvalidIssuerError:
            raise _unauthenticated("wrong-issuer") from None
        except jwt.InvalidSignatureError:
            raise _unauthenticated("bad-signature") from None
        except jwt.PyJWTError:
            raise _unauthenticated("invalid-token") from None

        audiences = claims.get("aud")
        if isinstance(audiences, str):
            audiences = [audiences]
        if not isinstance(audiences, list):
            raise _denied("wrong-gate")
        oauth = "oauth" in claims
        upload_aud = self._settings.upload_aud
        if "common_name" in claims:
            # A service token's login. Only the upload credential, only on
            # the upload routes.
            configured = upload_aud and self._settings.upload_client_id
            if not (configured and upload_route and gate == APP_GATE and upload_aud in audiences):
                raise _denied("service-token-off-upload")
            if oauth or claims["common_name"] != self._settings.upload_client_id:
                raise _denied("wrong-service-token")
            return AccessIdentity(email="", gate=APP_GATE, client=UPLOAD_ACTOR)
        accepted = {self._settings.aud_for(gate)}
        if upload_route and upload_aud:
            # The upload routes sit under the third Access application, so the
            # operator's browser login there may carry its AUD instead.
            accepted.add(upload_aud)
        if not accepted & set(audiences):
            raise _denied("wrong-gate")
        if gate == APP_GATE and oauth:
            # A chat (Managed OAuth) login's token can carry the app gate's
            # AUD too (ticket 21), so the AUD alone cannot keep it off app
            # routes. The `oauth` claim's presence marks every OAuth-issued
            # login and no browser's; its contents are never read.
            raise _denied("oauth-login-on-app-gate")
        email = claims.get("email")
        if not isinstance(email, str) or email.strip().lower() != self._operator_email:
            raise _denied("wrong-email")

        client = None
        if gate == CHAT_GATE:
            # Which claim names the chat client is not yet known (ticket 21
            # records it). Until then the client is the token's subject plus
            # "chat" (spec D4).
            subject = claims.get("sub")
            client = f"chat:{subject}" if isinstance(subject, str) and subject else "chat"
        return AccessIdentity(email=email.strip().lower(), gate=gate, client=client, oauth=oauth)


ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]


def _json_bytes(body: dict) -> bytes:
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


async def send_refusal(send, status: int) -> None:
    body = _json_bytes(AUTH_REQUIRED_BODY if status == AUTH_REQUIRED_STATUS else ACCESS_DENIED_BODY)
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _header_values(scope: dict, name: bytes) -> list[bytes]:
    return [value for key, value in scope.get("headers", []) if key.lower() == name]


class AccessGate:
    """ASGI middleware, outermost in the process: no route runs before it."""

    def __init__(self, app: ASGIApp, *, verifier: AccessVerifier | None, local_dev: bool = False):
        if verifier is None and not local_dev:
            raise GateConfigError("the access gate needs its settings unless local-dev is on")
        self._app = app
        self._verifier = verifier
        self._local_dev = local_dev

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind == "lifespan":
            await self._app(scope, receive, send)
            return
        if kind == "websocket":
            # fin serves no websockets; refuse before accepting.
            await send({"type": "websocket.close", "code": 1008})
            return
        if kind != "http":
            return

        gate = gate_for_request(scope)
        if self._local_dev:
            identity = local_dev_identity(gate)
        else:
            try:
                # In a worker thread: a key fetch blocks on the network, and
                # the one event loop must keep serving meanwhile.
                identity = await asyncio.to_thread(self._verify, scope, gate)
            except AccessRefused as refused:
                logger.info("access refused: gate=%s reason=%s", gate, refused.reason)
                await send_refusal(send, refused.status)
                return
            except Exception:
                # Fail closed on anything unforeseen; the exception text is
                # never logged, since it could carry token material.
                logger.error("access refused: gate=%s reason=verifier-error", gate)
                await send_refusal(send, AUTH_REQUIRED_STATUS)
                return

        await self._app({**scope, ACCESS_IDENTITY_SCOPE_KEY: identity}, receive, send)

    def _verify(self, scope: dict, gate: str) -> AccessIdentity:
        values = _header_values(scope, ACCESS_HEADER)
        if len(values) > 1:
            raise _unauthenticated("several-tokens")
        token = values[0].decode("latin-1").strip() if values else None
        return self._verifier.verify(token, gate, upload_route=is_upload_route(scope))


class RequireGateIdentity:
    """WSGI middleware around the Flask app: the second layer (D1).

    The gate, outermost in the composed process, places the verified identity
    on the ASGI scope, which the WSGI adapter carries into the environ. A
    request that reaches Flask without it (Flask served without the gate by a
    misconfigured start command) is refused unless `config["LOCAL_DEV"]` is
    on, and a chat-gate identity arriving over HTTP is refused always. The
    one chat identity admitted is an in-process chat call's, under
    CHAT_CALL_ENVIRON_KEY, and it is taken before the HTTP identity. No view
    runs on a refusal. Client headers are never trusted here. A request let through carries `fin.via`
    and `fin.actor` in its environ, from the identity. It wraps `app.wsgi_app`
    rather than adding a request hook, so the app's own hooks never see a
    refused request.
    """

    def __init__(self, wsgi_app, config):
        self._app = wsgi_app
        self._config = config

    def __call__(self, environ, start_response):
        chat_call = environ.get(CHAT_CALL_ENVIRON_KEY)
        if isinstance(chat_call, AccessIdentity) and chat_call.gate == CHAT_GATE:
            environ[VIA_ENVIRON_KEY] = chat_call.via
            environ[ACTOR_ENVIRON_KEY] = chat_call.actor
            return self._app(environ, start_response)
        identity = identity_from_environ(environ)
        if identity is None and self._config.get("LOCAL_DEV"):
            identity = local_dev_identity(APP_GATE)
        if identity is not None and identity.gate == APP_GATE:
            environ[VIA_ENVIRON_KEY] = identity.via
            environ[ACTOR_ENVIRON_KEY] = identity.actor
            return self._app(environ, start_response)
        status = AUTH_REQUIRED_STATUS if identity is None else ACCESS_DENIED_STATUS
        body = _json_bytes(AUTH_REQUIRED_BODY if identity is None else ACCESS_DENIED_BODY)
        start_response(
            "401 UNAUTHORIZED" if status == AUTH_REQUIRED_STATUS else "403 FORBIDDEN",
            [("Content-Type", "application/json"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")],
        )
        return [body]

