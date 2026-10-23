"""The browser keeps fin's CSS and JS until a file changes (speed-pass-2026-10-23
ticket 01, spec case list A, cases 1-5).

The page route serves static/index.html with every /static/<file> reference
given ?v=<short hash of that file's bytes>; a static response asked for with
the file's current version may be kept a year, the page itself never, and a
static file asked for without its version is still served, revalidated each
time. Everything runs through the public routes: the Flask test client for
cases 1-4, the composed hosted process (gate outermost) for case 5.

Every book is a tmp file; nothing here opens the repo's fin.db.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

import app as fin_app
from gate_tokens import AUTH_REQUIRED, app_token, book, composed, header, send  # noqa: F401 (book: fixture)

STATIC = Path(__file__).resolve().parent.parent / "static"
YEAR = "public, max-age=31536000, immutable"
REF = re.compile(r'(?:src|href)="/static/([^"]+)"')


def _refs(html: str) -> dict[str, str]:
    """{file: version} for every /static/ reference in a served page."""
    out = {}
    for ref in REF.findall(html):
        name, _, query = ref.partition("?")
        assert query.startswith("v="), f"{name} served without a version"
        out[name] = query[2:]
    return out


def _page(client) -> str:
    response = client.get("/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


# ---------------------------------------------------------------- case 1

def test_every_static_reference_on_the_page_carries_a_version(client):
    written = REF.findall((STATIC / "index.html").read_text(encoding="utf-8"))
    served = _refs(_page(client))

    # every file the repo's page names, and nothing else; the repo copy stays plain
    assert sorted(served) == sorted(written)
    assert len(served) >= 30
    assert all(re.fullmatch(r"[0-9a-f]{10}", v) for v in served.values())


def test_a_versioned_static_file_is_kept_a_year(client):
    version = _refs(_page(client))["app.js"]

    response = client.get(f"/static/app.js?v={version}")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == YEAR
    assert response.get_data() == (STATIC / "app.js").read_bytes()


def test_a_version_that_is_not_the_files_own_is_not_kept(client):
    # a page from before a deploy asking for a file after it: the bytes are
    # today's, so they must not be pinned a year under yesterday's version
    response = client.get("/static/app.js?v=0000000000")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache"


# ---------------------------------------------------------------- case 2

def test_changing_a_files_bytes_changes_its_version_and_only_its(client, tmp_path, monkeypatch):
    before_dir = tmp_path / "before"
    after_dir = tmp_path / "after"
    shutil.copytree(STATIC, before_dir)
    shutil.copytree(STATIC, after_dir)
    with open(after_dir / "app.js", "ab") as f:
        f.write(b"\n// a deploy\n")

    monkeypatch.setattr(fin_app.app, "static_folder", str(before_dir))
    before = _refs(_page(client))
    monkeypatch.setattr(fin_app.app, "static_folder", str(after_dir))
    after = _refs(_page(client))

    assert before["app.js"] != after["app.js"]
    assert {k: v for k, v in before.items() if k != "app.js"} == {k: v for k, v in after.items() if k != "app.js"}
    # the new version is kept a year, and serves the new bytes
    response = client.get(f"/static/app.js?v={after['app.js']}")
    assert response.headers["Cache-Control"] == YEAR
    assert response.get_data().endswith(b"// a deploy\n")


# ---------------------------------------------------------------- case 3

def test_the_page_itself_is_always_asked_for_fresh(client):
    response = client.get("/")

    assert response.headers["Cache-Control"] == "no-cache"
    assert response.headers["Content-Type"].startswith("text/html")


# ---------------------------------------------------------------- case 4

@pytest.mark.parametrize("name", ["app.js", "styles.css", "favicon.svg"])
def test_an_unversioned_static_file_is_still_served_revalidated(client, name):
    response = client.get(f"/static/{name}")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.get_data() == (STATIC / name).read_bytes()


# ---------------------------------------------------------------- case 5

def test_the_hosted_gate_still_guards_a_versioned_static_file(book):
    page = send(composed(), "GET", "/", header(app_token()))
    assert page.status_code == 200
    path = f"/static/app.js?v={_refs(page.text)['app.js']}"

    refused = send(composed(), "GET", path)
    assert refused.status_code == 401
    assert refused.json() == AUTH_REQUIRED

    served = send(composed(), "GET", path, header(app_token()))
    assert served.status_code == 200
    assert served.headers["cache-control"] == YEAR
