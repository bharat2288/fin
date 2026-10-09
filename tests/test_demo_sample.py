"""The public sample (demo_serve.py, demo_gate.py; brief-public-demos).

The sample is a separate deployment that must never reach a real book: it
refuses the real instance's settings, lets nobody in without a code sent to
their email, gives each visitor a book of their own copied from the made-up
one, and keeps the chat tools and statement upload shut.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date
from pathlib import Path

import httpx
import pytest

import app as fin_app
import db
import demo_gate
import demo_serve
import seed_mock_data

SECRET = "s" * 40


class Clock:
    def __init__(self, start: float = 1_800_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


# --- Settings -----------------------------------------------------------------


def _env(**extra):
    return {"DEMO_SECRET": SECRET, "DEMO_DATA_DIR": "/data", "DEMO_RESEND_API_KEY": "k", "DEMO_MAIL_FROM": "a@b.co", **extra}


@pytest.mark.parametrize(
    "name",
    ["FIN_DB_PATH", "FIN_ACCESS_APP_AUD", "FIN_BACKUP_BUCKET", "FIN_SEED_OBJECT_KEY", "FIN_HDFC_PDF_PASSWORD", "OPENROUTER_API_KEY"],
)
def test_the_sample_refuses_the_real_instances_settings(name):
    with pytest.raises(demo_serve.StartupRefused) as refused:
        demo_serve.load_demo_config(_env(**{name: "a-secret-value"}))
    assert name in str(refused.value)
    assert "a-secret-value" not in str(refused.value)


def test_the_sample_names_what_it_is_missing():
    with pytest.raises(demo_serve.StartupRefused) as refused:
        demo_serve.load_demo_config({})
    for name in ("DEMO_SECRET", "DEMO_DATA_DIR", "DEMO_RESEND_API_KEY", "DEMO_MAIL_FROM"):
        assert name in str(refused.value)


def test_a_short_secret_is_refused():
    with pytest.raises(demo_serve.StartupRefused):
        demo_serve.load_demo_config(_env(DEMO_SECRET="short"))


def test_local_mode_is_loopback_only():
    with pytest.raises(demo_serve.StartupRefused):
        demo_serve.load_demo_config({"DEMO_SECRET": SECRET, "DEMO_LOCAL": "1", "DEMO_HOST": "0.0.0.0"})
    config = demo_serve.load_demo_config({"DEMO_SECRET": SECRET, "DEMO_LOCAL": "1"})
    assert config.host == "127.0.0.1" and config.local


# --- The made-up book -----------------------------------------------------------


def test_six_months_end_where_asked():
    assert seed_mock_data.six_months_ending((2026, 3)) == [(2025, 10), (2025, 11), (2025, 12), (2026, 1), (2026, 2), (2026, 3)]
    assert seed_mock_data.six_months_ending((2026, 1))[0] == (2025, 8)
    assert demo_serve.last_whole_month(date(2026, 1, 5)) == (2025, 12)


def test_the_template_covers_the_six_months_to_the_last_whole_month(tmp_path):
    template = demo_serve.build_template(tmp_path, today=date(2026, 10, 8))
    conn = sqlite3.connect(template)
    first, last = conn.execute("SELECT MIN(date), MAX(date) FROM transactions").fetchone()
    conn.close()
    assert first >= "2026-04-01" and last <= "2026-09-30" and last >= "2026-09-01"
    assert not Path(f"{template}-wal").exists()


# --- The sample, end to end -------------------------------------------------------


@pytest.fixture
def sample(tmp_path, monkeypatch):
    """The composed sample on a temporary data folder, codes to the log."""
    monkeypatch.setattr(fin_app.app, "wsgi_app", fin_app.app.wsgi_app)
    monkeypatch.setitem(fin_app.app.config, "LOCAL_DEV", False)
    monkeypatch.setitem(fin_app.app.config, "DEMO", False)
    monkeypatch.setattr(db, "DB_PATH", db.DB_PATH)
    config = demo_serve.load_demo_config({"DEMO_SECRET": SECRET, "DEMO_LOCAL": "1", "DEMO_DATA_DIR": str(tmp_path / "data")})
    template = demo_serve.build_template(config.data_dir, today=date(2026, 10, 8))
    _flask, asgi, gate = demo_serve.build_demo_app(config, template)
    clock = Clock()
    gate._clock = clock
    gate.control._clock = clock
    gate.books._clock = clock
    yield gate, clock
    db.invalidate_rules_cache()


def _run(coro):
    return asyncio.run(coro)


async def _client(gate):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="https://fin-demo.test")


def _last_code(gate, email):
    sent = [m for m in gate._settings.sender.sent if m["to"] == email]
    return sent[-1]["text"].split("Your code is ")[1][:6]


async def _sign_in(client, gate, email):
    await client.post("/demo/sign-in", data={"email": email})
    answer = await client.post("/demo/verify", data={"email": email, "code": _last_code(gate, email)})
    assert answer.status_code == 303
    return answer


def test_signed_out_visitors_get_the_sign_in_page_and_nothing_else(sample):
    gate, _clock = sample

    async def go():
        async with await _client(gate) as client:
            page = await client.get("/")
            api = await client.get("/api/accounts")
            mcp = await client.post("/mcp", json={})
            form = await client.get("/demo/sign-in")
            return page, api, mcp, form

    page, api, mcp, form = _run(go())
    assert page.status_code == 303 and page.headers["location"] == "/demo/sign-in"
    assert api.status_code == 401
    assert mcp.status_code == 404
    assert form.status_code == 200 and "Email me a code" in form.text


def test_a_code_signs_in_once_and_records_the_email(sample):
    gate, _clock = sample

    async def go():
        async with await _client(gate) as client:
            await client.post("/demo/sign-in", data={"email": " Visitor@Example.COM "})
            code = _last_code(gate, "visitor@example.com")
            wrong = await client.post("/demo/verify", data={"email": "visitor@example.com", "code": "000000" if code != "000000" else "111111"})
            right = await client.post("/demo/verify", data={"email": "visitor@example.com", "code": code})
            again = await client.post("/demo/verify", data={"email": "visitor@example.com", "code": code})
            accounts = await client.get("/api/accounts")
            home = await client.get("/")
            return wrong, right, again, accounts, home

    wrong, right, again, accounts, home = _run(go())
    assert wrong.status_code == 400
    assert right.status_code == 303 and "demo_session=" in right.headers["set-cookie"]
    assert "HttpOnly" in right.headers["set-cookie"]
    assert again.status_code == 400
    assert accounts.status_code == 200 and len(accounts.json()) == 5
    assert "fin sample · made-up figures · visitor@example.com" in home.text
    assert [s["email"] for s in gate.control.signups()] == ["visitor@example.com"]


def test_five_wrong_tries_spend_the_code(sample):
    gate, _clock = sample
    email = "v@example.com"
    code, _ = gate.control.issue_code(email, 10)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(demo_gate.MAX_CODE_TRIES):
        assert not gate.control.check_code(email, wrong)
    assert not gate.control.check_code(email, code)


def test_a_code_expires(sample):
    gate, clock = sample
    code, _ = gate.control.issue_code("v@example.com", 10)
    clock.now += demo_gate.CODE_LIFETIME_SECONDS + 1
    assert not gate.control.check_code("v@example.com", code)


def test_one_code_a_minute_and_a_cap_an_hour(sample):
    gate, clock = sample
    assert gate.control.issue_code("a@example.com", 2)[1] == ""
    assert gate.control.issue_code("a@example.com", 2) == (None, "recent")
    assert gate.control.issue_code("b@example.com", 2)[1] == ""
    assert gate.control.issue_code("c@example.com", 2) == (None, "busy")
    clock.now += 3601
    assert gate.control.issue_code("c@example.com", 2)[1] == ""


def test_a_tampered_or_old_session_is_refused():
    now = 1_800_000_000.0
    session = demo_gate.make_session(SECRET.encode(), "v@example.com", now)
    assert demo_gate.read_session(SECRET.encode(), session, now) == "v@example.com"
    payload, mac = session.split(".")
    forged = demo_gate.make_session(b"another-secret" * 3, "boss@example.com", now)
    assert demo_gate.read_session(SECRET.encode(), forged.split(".")[0] + "." + mac, now) is None
    assert demo_gate.read_session(SECRET.encode(), session, now + demo_gate.SESSION_SECONDS + 1) is None


def test_each_visitor_works_on_a_book_of_their_own(sample):
    gate, _clock = sample

    async def go():
        async with await _client(gate) as a, await _client(gate) as b:
            await _sign_in(a, gate, "a@example.com")
            await _sign_in(b, gate, "b@example.com")
            made = await a.post("/api/accounts", json={"name": "A's own account"})
            return made, (await a.get("/api/accounts")).json(), (await b.get("/api/accounts")).json()

    made, seen_by_a, seen_by_b = _run(go())
    assert made.status_code in (200, 201)
    assert "A's own account" in [x["name"] for x in seen_by_a]
    assert "A's own account" not in [x["name"] for x in seen_by_b]
    template = sqlite3.connect(gate._settings.template_book)
    assert template.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 5
    template.close()


def test_reset_starts_the_visitor_again_from_the_template(sample):
    gate, _clock = sample

    async def go():
        async with await _client(gate) as a:
            await _sign_in(a, gate, "a@example.com")
            await a.post("/api/accounts", json={"name": "Gone after reset"})
            reset = await a.post("/demo/reset")
            return reset, (await a.get("/api/accounts")).json()

    reset, accounts = _run(go())
    assert reset.status_code == 303
    assert "Gone after reset" not in [x["name"] for x in accounts]


def test_statement_upload_is_shut(sample):
    gate, _clock = sample

    async def go():
        async with await _client(gate) as a:
            await _sign_in(a, gate, "a@example.com")
            return await a.post("/api/import/upload", files={"file": ("x.csv", b"a,b")})

    answer = _run(go())
    assert answer.status_code == 403 and "upload is off" in answer.json()["error"]


def test_idle_books_are_swept_and_the_signup_stays(sample):
    gate, clock = sample
    gate.books.book_for("old@example.com")
    gate.control.record_sign_in("old@example.com")
    clock.now += 15 * 86400
    gate.books.book_for("new@example.com")
    assert gate.books.sweep() == 1
    assert not gate.books.folder_for("old@example.com").exists()
    assert gate.books.folder_for("new@example.com").exists()
    assert [s["email"] for s in gate.control.signups()] == ["old@example.com"]


def test_a_request_with_no_book_is_refused(sample):
    """VisitorBook never lets the app open a book of its own choosing."""
    client = fin_app.app.test_client()
    assert client.get("/api/accounts").status_code == 403


def test_the_operator_hears_of_a_new_signup_once(tmp_path, monkeypatch, sample):
    gate, _clock = sample
    object.__setattr__(gate._settings, "notify_email", "owner@example.com")

    async def go():
        async with await _client(gate) as a:
            await _sign_in(a, gate, "a@example.com")
            await _sign_in(a, gate, "a@example.com")

    _run(go())
    notices = [m for m in gate._settings.sender.sent if m["to"] == "owner@example.com"]
    assert len(notices) == 1 and "a@example.com" in notices[0]["text"]


def test_the_sample_shows_no_backup_warning(sample):
    gate, _clock = sample

    async def go():
        async with await _client(gate) as a:
            await _sign_in(a, gate, "a@example.com")
            return (await a.get("/api/backups/status")).json()

    assert not _run(go()).get("warning")


def test_the_code_page_says_where_to_look_and_sends_another(sample):
    gate, clock = sample

    async def go():
        async with await _client(gate) as client:
            first = await client.post("/demo/sign-in", data={"email": "v@example.com"})
            clock.now += demo_gate.CODE_RESEND_SECONDS + 1
            again = await client.post("/demo/sign-in", data={"email": "v@example.com"})
            return first, again

    first, again = _run(go())
    assert "Spam" in first.text and "Send another code" in first.text
    assert again.status_code == 200
    assert len([m for m in gate._settings.sender.sent if m["to"] == "v@example.com"]) == 2
