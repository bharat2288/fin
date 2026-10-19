"""Ways in, lane W2 (fin-surfaces 42; spec 15 cases 45, 47).

Spending's row lists link the merchant, type and account names; the By merchant
and By type rows carry a link to their page while the row still narrows to the
flat view. Rows come through the real /api/transactions route on the shared
trend fixtures; the client functions are lifted out of static/app.js and run
under node, as test_ways_in_w1 does. Every name and figure is invented.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import trend_fixtures as tf

APP_JS = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def _function(name):
    m = re.search(rf"^function {name}\(.*\}}$", APP_JS, re.M) or re.search(rf"^function {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _const(name):
    m = re.search(rf"^(?:const|let) {name} = .*?;$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _run(setup, expr, **data):
    prelude = ("globalThis.window = globalThis; const day = d => d; const tag = (k, t) => `<tag ${k}>${t}</tag>`;"
               " const icon = n => `<i ${n}></i>`; const rowMark = () => ''; const notCheckedTag = () => '';"
               " const toMinor = (a, c) => Math.round(Number(a) * 100); const money = (m, c) => (m / 100).toFixed(2);")
    code = "\n".join([prelude, _function("esc"), _const("pageLinkKinds"), _function("pageLink"), _function("spendRowTr"),
                      _function("spendGroupNameCell"), f"const DATA = {json.dumps(data)};", setup,
                      f"console.log(JSON.stringify({expr}));"])
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
    return json.loads(out.stdout)


TYPE_ON = "pageLinkKinds.type = id => `#/books/type/${id}`;"


@pytest.fixture
def seeded(conn, client):
    acct = tf.account(conn, "Everyday card")
    st = tf.statement(conn, acct, "2026-04")
    svc = tf.merchant(conn, "Grab", "Household", "Dining")
    tx = tf.row(conn, st, "GRAB RIDE", 1250, service_id=svc, book="Household", type_name="Dining")
    bare = tf.row(conn, st, "CASH WITHDRAWAL", 500, book="Household")
    read = lambda i: client.get(f"/api/transactions?tx_id={i}").get_json()["transactions"][0]
    return {"acct": acct, "svc": svc, "tid": tf.type_id(conn, "Dining"), "tx": read(tx), "bare": read(bare)}


@needs_node
def test_spending_row_links_merchant_type_and_account(seeded):
    html = _run(TYPE_ON, "spendRowTr(DATA.tx)", tx=seeded["tx"])
    assert f'<a class="link" href="#/books/merchant/{seeded["svc"]}">GRAB RIDE</a>' in html
    assert f'<a class="link" href="#/books/type/{seeded["tid"]}">Dining</a>' in html
    assert f'<a class="link" href="#/books/account/{seeded["acct"]}">Everyday card</a>' in html
    assert 'data-act="row"' in html  # the row still opens its sheet; actTarget lets a link click through


@needs_node
def test_spending_row_without_merchant_or_type_shows_text_unlinked(seeded):
    html = _run(TYPE_ON, "spendRowTr(DATA.tx)", tx=seeded["bare"])
    assert "CASH WITHDRAWAL" in html and "/books/merchant/" not in html
    assert "/books/type/" not in html and "no type" in html
    assert "/books/account/" in html


@needs_node
def test_spending_row_type_stays_plain_until_type_pages_are_on(seeded):
    html = _run("", "spendRowTr(DATA.tx)", tx=seeded["tx"])
    assert "/books/type/" not in html and "Dining" in html and "/books/merchant/" in html


@needs_node
def test_by_merchant_row_links_its_merchant(seeded):
    g = {"name": "Grab", "list": [seeded["tx"]]}
    assert _run("", "spendGroupNameCell(DATA.g, 'merchant', false, [])", g=g) == f'<a class="link" href="#/books/merchant/{seeded["svc"]}">Grab</a>'
    bare = {"name": "CASH WITHDRAWAL", "list": [seeded["bare"]]}
    assert _run("", "spendGroupNameCell(DATA.g, 'merchant', false, [])", g=bare) == "CASH WITHDRAWAL"


@needs_node
def test_by_type_row_links_its_type_and_folded_parent(seeded):
    g = {"name": "Dining", "list": [seeded["tx"]]}
    assert _run(TYPE_ON, "spendGroupNameCell(DATA.g, 'type', false, [])", g=g) == f'<a class="link" href="#/books/type/{seeded["tid"]}">Dining</a>'
    folded = {"name": "Food", "list": [dict(seeded["tx"], type_id=99, parent_type="Food")]}
    types = [{"id": 5, "name": "Food", "parent_id": None}, {"id": 99, "name": "Dining", "parent_id": 5}]
    assert _run(TYPE_ON, "spendGroupNameCell(DATA.g, 'type', true, DATA.types)", g=folded, types=types) == '<a class="link" href="#/books/type/5">Food</a>'
    none = {"name": "No type", "list": [seeded["bare"]]}
    assert _run(TYPE_ON, "spendGroupNameCell(DATA.g, 'type', false, [])", g=none) == "<tag nofig>No type</tag>"


def test_group_rows_still_narrow_on_a_row_click():
    assert re.search(r'<tr class="clickable" data-act="spend-drill" data-key="\$\{esc\(g\.name\)\}" data-view="\$\{sp\.view\}"><td>\$\{nameCell\(g\)\}</td>', APP_JS)
    assert "const nameCell = g => spendGroupNameCell(g, sp.view, fold, r.types);" in APP_JS
