"""Ways in, lane W1 (fin-surfaces 34; spec 15 cases 45, 46, 58).

Merchant, type and account names go through pageLink in the row sheet's facts,
the Queue's untyped card and the merchant page's Rows table. The row is read
through the real /api/transactions route on the shared trend fixtures; the
client functions are lifted out of static/app.js and run under node, as
test_client_seams does. Every name and figure is invented.
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
    prelude = "globalThis.window = globalThis; const day = d => d; const tag = (k, t) => `<tag ${k}>${t}</tag>`;"
    code = "\n".join([prelude, _function("esc"), _const("pageLinkKinds"), _function("pageLink"), _function("notCheckedTag"),
                      _function("rowSheetSubHTML"), _function("rowFactsHTML"), _function("queueTitleHTML"),
                      _function("mpAccountCell"), _function("actTarget"),
                      f"const DATA = {json.dumps(data)};", setup, f"console.log(JSON.stringify({expr}));"])
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
    return json.loads(out.stdout)


TYPE_ON = "pageLinkKinds.type = id => `#/books/type/${id}`;"


@pytest.fixture
def grab(conn, client):
    """A Grab row on Everyday card, typed Transport, plus an unlinked bare row."""
    acct = tf.account(conn, "Everyday card")
    st = tf.statement(conn, acct, "2026-04")
    svc = tf.merchant(conn, "Grab", "Household", "Transport")
    tx = tf.row(conn, st, "GRAB RIDE", 1250, service_id=svc, book="Household", type_name="Transport")
    bare = tf.row(conn, st, "CASH WITHDRAWAL", 500, type_name="Dining")
    read = lambda i: client.get(f"/api/transactions?tx_id={i}").get_json()["transactions"][0]
    return {"acct": acct, "svc": svc, "tx": read(tx), "bare": read(bare)}


@needs_node
def test_row_sheet_links_merchant_account_and_type(grab):
    tx = grab["tx"]
    facts = _run(f"{TYPE_ON}", "rowFactsHTML(DATA.tx, 'Transport', 'the rules')", tx=tx)
    sub = _run("", "rowSheetSubHTML(DATA.tx, 1250)", tx=tx)
    assert f'<a class="link" href="#/books/merchant/{grab["svc"]}">Grab</a>' in facts
    assert f'<a class="link" href="#/books/type/{tx["type_id"]}">Transport</a>' in facts
    assert f'<a class="link" href="#/books/account/{grab["acct"]}">Everyday card</a>' in sub


@needs_node
def test_row_sheet_type_stays_plain_until_type_pages_are_on(grab):
    facts = _run("", "rowFactsHTML(DATA.tx, 'Transport', 'the rules')", tx=grab["tx"])
    assert "/books/type/" not in facts and "Transport" in facts
    assert "/books/merchant/" in facts


@needs_node
def test_row_with_no_merchant_or_no_type_shows_its_text_unlinked(grab):
    bare = grab["bare"]
    assert bare["service_id"] is None
    facts = _run(TYPE_ON, "rowFactsHTML(DATA.tx, 'Dining', 'not yet')", tx=bare)
    assert "none yet" in facts and "/books/merchant/" not in facts
    untyped = dict(bare, type_id=None, display_type=None)
    facts = _run(TYPE_ON, "rowFactsHTML(DATA.tx, '<tag nofig>no type</tag>', 'not yet')", tx=untyped)
    assert "/books/type/" not in facts and "no type" in facts


@needs_node
def test_queue_untyped_card_links_its_name_only_with_a_merchant(grab):
    with_m = {"kind": "untyped", "title": "GRAB RIDE", "rows": [grab["tx"]]}
    without = {"kind": "untyped", "title": "CASH WITHDRAWAL", "rows": [grab["bare"]]}
    assert _run("", "queueTitleHTML(DATA.c)", c=with_m) == f'<a class="link" href="#/books/merchant/{grab["svc"]}">GRAB RIDE</a>'
    assert _run("", "queueTitleHTML(DATA.c)", c=without) == "CASH WITHDRAWAL"
    transfer = {"kind": "transfer", "title": "To savings", "rows": [grab["tx"]]}
    assert _run("", "queueTitleHTML(DATA.c)", c=transfer) == "To savings"


@needs_node
def test_merchant_rows_table_links_the_account(grab):
    cell = _run("", "mpAccountCell(DATA.tx)", tx=grab["tx"])
    assert cell == f'<a class="link" href="#/books/account/{grab["acct"]}">Everyday card</a>'


@needs_node
def test_a_click_on_a_link_never_reaches_the_row_handler():
    # a fake DOM: a row (data-act) holding a link; closest() walks up the chain
    setup = """
        const mk = (tag, parent, dataset) => ({ dataset: dataset || {}, tagName: tag,
        closest(s) { if (s === 'a[href]') return tag === 'A' ? this : (parent && parent.closest ? parent.closest(s) : null);
                     if (s === '[data-act]') return this.dataset.act ? this : (parent ? parent.closest(s) : null); return null; } });
    const tr = mk('TR', null, { act: 'row' }); tr.contains = x => x !== tr;
    const anchorAct = mk('A', null, { act: 'go' }); anchorAct.contains = x => x === anchorAct;
    const link = mk('A', tr), cell = mk('TD', tr), btn = mk('BUTTON', null, { act: 'resolve' });
    """
    got = _run(setup, "[actTarget(link) === null, actTarget(cell) === tr, actTarget(btn) === btn, actTarget(anchorAct) === anchorAct]")
    assert got == [True, True, True, True]


def test_global_click_handler_goes_through_actTarget():
    m = re.search(r"document\.addEventListener\('click', e => \{\n\s+const el = actTarget\(e\.target\);", APP_JS)
    assert m, "the document click handler must read its target through actTarget"
