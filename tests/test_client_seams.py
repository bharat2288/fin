"""Ticket 23: the client seams the trend pages plug into, with no visible change.

The seams live in static/app.js and nowhere else, so the functions are lifted out of the
real file and run under node (as test_suggest_pattern does); the rest is read off the files.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

NEW_JS = ["trend.js", "trend-bars.js", "trend-line.js", "merchant-trend.js", "type-page.js", "account-trend.js", "types-over-time.js"]
NEW_CSS = ["trend.css", "trend-bars.css", "trend-line.css"]


def _function(name):
    m = re.search(rf"^function {name}\(.*\}}$", APP_JS, re.M) or re.search(rf"^function {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _const(name):
    m = re.search(rf"^(?:const|let) {name} = .*?;$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _node(code):
    # The script goes in on stdin: on Windows a long -e argument passes the command-line limit.
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, check=True, encoding="utf-8")
    return json.loads(out.stdout)


PRELUDE = "const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;'); const icon = n => `<i ${n}></i>`; globalThis.window = globalThis;"


@needs_node
def test_registerRoute_adds_a_route_the_router_can_find():
    code = "\n".join([PRELUDE, r"const ROUTES = [[/^\/?$/, 'home', () => 'h']];", _function("registerRoute"),
        r"registerRoute(/^\/books\/type\/(\d+)$/, m => 'type ' + m[1]);",
        "const path = '/books/type/7'; const r = ROUTES.find(([re]) => re.test(path));",
        "console.log(JSON.stringify([ROUTES.length, r[1], r[2](path.match(r[0]))]));"])
    assert _node(code) == [2, "books", "type 7"]


@needs_node
def test_booksNav_lists_only_what_has_been_pushed():
    def nav(push):
        return _node("\n".join([PRELUDE, _const("booksNavExtra"), _function("booksNav"), push,
            "console.log(JSON.stringify(booksNav('sheet')));"]))
    base = nav("")
    assert "#/books/types" not in base and "#/books/type" not in base
    assert base.count("<a ") == 5
    more = nav("booksNavExtra.push(['types', '#/books/types', 'Types', 'x']);")
    assert more.count("<a ") == 6 and 'href="#/books/types"' in more


@needs_node
def test_pageLink_is_plain_text_for_type_until_it_is_turned_on():
    def link(kind, setup=""):
        return _node("\n".join([PRELUDE, _const("pageLinkKinds"), _function("pageLink"), setup,
            f"console.log(JSON.stringify(pageLink('{kind}', 7, 'A <b>')));"]))
    assert link("merchant") == '<a class="link" href="#/books/merchant/7">A &lt;b></a>'
    assert link("account") == '<a class="link" href="#/books/account/7">A &lt;b></a>'
    assert link("type") == "A &lt;b>"
    assert link("type", "pageLinkKinds.type = id => `#/books/type/${id}`;") == '<a class="link" href="#/books/type/7">A &lt;b></a>'
    assert link("nonsense") == "A &lt;b>"


def test_mount_slots_and_month_state_are_in_the_views():
    assert "window.mountMerchantTrend?.()" in APP_JS
    assert "window.mountAccountTrend?.()" in APP_JS
    assert re.search(r"const merchantPage = \{[^}]*month: null", APP_JS)
    assert re.search(r"const accountPage = \{[^}]*month: null", APP_JS)
    assert "&month=${" in APP_JS or "month=${" in APP_JS


def test_the_month_narrowing_is_off_by_default_in_the_transaction_reads():
    # no month set -> no month param on the URL
    assert re.search(r"merchantPage\.month|mp\.month", APP_JS)
    assert re.search(r"accountPage\.month", APP_JS)


def test_every_new_tag_follows_app_js():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    app = html.index('src="/static/app.js"')
    for f in NEW_JS:
        i = html.find(f'src="/static/{f}"')
        assert i > app, f"{f} missing or before app.js"
    # trend.js first: the others register into its namespace
    assert html.index('src="/static/trend.js"') < html.index('src="/static/trend-bars.js"') < html.index('src="/static/trend-line.js"')
    for f in NEW_CSS:
        assert f'href="/static/{f}"' in html


@pytest.mark.parametrize("name", NEW_JS + NEW_CSS)
def test_each_new_file_exists(name):
    assert (STATIC / name).is_file()


@needs_node
def test_trend_js_holds_the_namespace_and_registerMode_only():
    code = "globalThis.window = globalThis;" + (STATIC / "trend.js").read_text(encoding="utf-8") + """
    const seen = [];
    window.Trend.registerMode('bars', () => seen.push('b'));
    window.Trend.registerMode('line', () => seen.push('l'));
    console.log(JSON.stringify([Object.keys(window.Trend).sort(), Object.keys(window.Trend.modes || {}).sort()]));"""
    keys, modes = _node(code)
    assert "registerMode" in keys and modes == ["bars", "line"]


def test_reserved_blocks_in_the_stylesheets():
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")
    for n in ("spending-net", "home-net", "links"):
        assert f"/* ==== reserved: {n} ====" in styles
    trend = (STATIC / "trend.css").read_text(encoding="utf-8")
    for n in ("merchant", "account", "type", "types"):
        assert f"/* ==== reserved: {n} ====" in trend
