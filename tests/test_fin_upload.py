"""The upload command (fin-surfaces 01): a Claude Code session sends a folder
of statements straight to fin's import, one file at a time, so the model
never handles their contents.

The command is driven against the app in-process through a sender that wraps
the Flask test client, with a stand-in parser. Every name and figure is
invented.
"""

import json

import pytest

import access_gate
import fin_upload
from test_import_that_ties import BANK, CLOSING, OPENING, ROWS, stand_in  # noqa: F401 (a fixture)


@pytest.fixture
def send(client):
    """Send as the upload command does, arriving with the identity the hosted
    gate gives a service-token request (Flask's second layer reads it)."""
    identity = access_gate.AccessIdentity(email="", gate=access_gate.APP_GATE, client=access_gate.UPLOAD_ACTOR)
    environ = {"asgi.scope": {access_gate.ACCESS_IDENTITY_SCOPE_KEY: identity}}

    def sender(path, body, content_type):
        resp = client.post(path, data=body, content_type=content_type, environ_base=environ)
        return resp.status_code, resp.get_json(silent=True)

    return sender


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "statement-aug.csv").write_bytes(b"stand-in")
    (tmp_path / "notes.txt").write_text("not a statement")
    return tmp_path


def rows_held(client) -> int:
    return client.get("/api/transactions?per_page=100").get_json()["total"]


def test_a_folder_that_ties_is_imported_and_recorded_as_the_upload(client, stand_in, send, folder, capsys):
    stand_in(ROWS, OPENING, CLOSING)

    assert fin_upload.main([str(folder)], send=send) == 0

    out = capsys.readouterr().out
    assert "statement-aug.csv: imported 5 new rows" in out
    assert "notes.txt" not in out
    assert rows_held(client) == 5
    latest = client.get("/api/history").get_json()["entries"][0]
    assert (latest["summary"], latest["via"], latest["actor"]) == (
        "Imported a statement", "chat", "Claude Code (upload)",
    )


def test_sending_the_same_folder_again_adds_nothing(client, stand_in, send, folder, capsys):
    stand_in(ROWS, OPENING, CLOSING)
    fin_upload.main([str(folder)], send=send)

    assert fin_upload.main([str(folder)], send=send) == 0

    assert "imported 0 new rows (5 already there)" in capsys.readouterr().out
    assert rows_held(client) == 5


def test_a_statement_that_does_not_tie_is_refused_and_nothing_is_written(
    client, stand_in, send, folder, capsys
):
    stand_in(ROWS, OPENING, CLOSING + 1)

    assert fin_upload.main([str(folder)], send=send) == 1

    assert "refused, nothing imported" in capsys.readouterr().out
    assert rows_held(client) == 0
    assert client.get("/api/history").get_json()["entries"] == []


def test_a_dry_run_imports_nothing(client, stand_in, send, folder, capsys):
    stand_in(ROWS, OPENING, CLOSING)

    assert fin_upload.main(["--dry-run", str(folder)], send=send) == 0

    assert f"would import {BANK}: 5 rows (ties)" in capsys.readouterr().out
    assert rows_held(client) == 0


def test_refused_credentials_say_which_variables_to_check(folder, capsys):
    def gate(path, body, content_type):
        return 401, {"error": "authentication required"}

    assert fin_upload.main([str(folder)], send=gate) == 1
    assert "authentication required" in capsys.readouterr().out

    def bare(path, body, content_type):
        return 403, None

    assert fin_upload.main([str(folder)], send=bare) == 1
    assert "FIN_UPLOAD_CLIENT_ID" in capsys.readouterr().out


def test_it_needs_somewhere_to_send_and_something_to_send(monkeypatch, folder, capsys):
    monkeypatch.delenv("FIN_URL", raising=False)
    assert fin_upload.main([str(folder)]) == 2
    assert "FIN_URL" in capsys.readouterr().out
    assert fin_upload.main([]) == 2
    assert fin_upload.main([str(folder / "missing")], send=lambda *a: (200, {})) == 2


def test_the_service_token_goes_in_the_gate_headers(monkeypatch, tmp_path):
    seen = {}

    class Answer:
        status = 200

        def read(self):
            return json.dumps({"errors": [], "groups": []}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout):
        seen.update({k.lower(): v for k, v in req.header_items()})
        seen["url"] = req.full_url
        return Answer()

    monkeypatch.setattr(fin_upload, "_open", fake_urlopen)
    send = fin_upload.http_sender("https://fin.example.invalid/", "id-value", "secret-value")
    send("/api/import/upload", b"x", "application/octet-stream")
    assert seen["url"] == "https://fin.example.invalid/api/import/upload"
    assert (seen["cf-access-client-id"], seen["cf-access-client-secret"]) == ("id-value", "secret-value")


@pytest.mark.parametrize("url", [
    "http://fin.example.invalid",
    "http://10.0.0.5:8450",
    "ftp://fin.example.invalid",
    "fin.example.invalid",
])
def test_the_secret_is_never_sent_but_over_https_or_to_loopback(monkeypatch, url):
    monkeypatch.setattr(fin_upload, "_open", lambda *a, **k: pytest.fail("something was sent"))

    with pytest.raises(fin_upload.InsecureUrl):
        fin_upload.http_sender(url, "id-value", "secret-value")


@pytest.mark.parametrize("url", [
    "https://fin.example.invalid", "http://127.0.0.1:8450", "http://localhost:8450", "http://[::1]:8450",
])
def test_https_and_loopback_take_the_secret(url):
    fin_upload.http_sender(url, "id-value", "secret-value")


def test_plain_http_without_a_secret_is_the_desk_app():
    fin_upload.http_sender("http://desk.example.invalid:8450", None, None)


def test_an_insecure_fin_url_is_a_usage_error_naming_the_variable(monkeypatch, folder, capsys):
    monkeypatch.setenv("FIN_URL", "http://fin.example.invalid")
    monkeypatch.setenv("FIN_UPLOAD_CLIENT_ID", "id-value")
    monkeypatch.setenv("FIN_UPLOAD_CLIENT_SECRET", "secret-value")
    monkeypatch.setattr(fin_upload, "_open", lambda *a, **k: pytest.fail("something was sent"))

    assert fin_upload.main([str(folder)]) == 2
    out = capsys.readouterr().out
    assert "FIN_URL" in out and "https" in out and "secret-value" not in out


def test_a_redirect_is_never_followed_so_the_token_goes_nowhere_else(monkeypatch):
    # A real local server answering 302 to another address: the sender must
    # stop there, and the address it names must never be asked.
    import http.server
    import threading

    asked = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            asked.append((self.path, self.headers.get("CF-Access-Client-Secret")))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            self.do_POST()

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        send = fin_upload.http_sender(f"http://127.0.0.1:{server.server_port}", "id-value", "secret-value")
        status, _body = send("/api/import/upload", b"x", "application/octet-stream")
    finally:
        server.shutdown()

    assert status == 302
    assert asked == [("/api/import/upload", "secret-value")]
