"""Walk-fixes 02: an edit icon on each Merchants row.

The row is drawn and its clicks are dispatched in static/app.js, so the row's markup
(merchantListRow), the click dispatch (actTarget) and the Edit sheet's open and save
(ACT.merchant, ACT['merchant-save']) are lifted out of the real file and run under node,
as test_merchant_page_reads does. What is pinned is what the operator gets: which sheet a
click opens, and what a save from it does.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
STYLES = (STATIC / "styles.css").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the row is drawn only in JavaScript")


def _function(name, prefix="function"):
    m = re.search(rf"^{prefix} {name}\(.*\}}$", APP_JS, re.M) or re.search(rf"^{prefix} {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _act(key):
    m = re.search(rf"^ACT{re.escape(key)} = async el => \{{.*?^\}};$", APP_JS, re.M | re.S)
    assert m, f"ACT{key} not found in static/app.js"
    return m.group(0)


def _node(code):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


PRELUDE = r"""
globalThis.window = globalThis;
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const icon = n => `<svg data-icon="${n}"></svg>`;
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
""" + "\n"

GRAB = {"id": 7, "name": "Grab", "book": "Household", "type_id": 3, "type_name": "Rides", "display_type": "Rides",
        "txn_count": 42, "rule_count": 3, "review_each_time": 0, "exclude_from_expense_views": 0, "is_one_off": 0}


def _row(s, renaming=False):
    return _node("\n".join([PRELUDE, _function("tag"), _function("merchantListRow"),
        f"console.log(JSON.stringify(merchantListRow({json.dumps(s)}, {{ renaming: {json.dumps(renaming)}, renames: {{}} }})));"]))


# A tiny DOM: just enough of closest/contains for actTarget, built from the row's own markup.
FAKE_DOM = r"""
function parse(html) {
    const root = { tag: '#root', attrs: {}, kids: [], parent: null };
    let at = root;
    const re = /<(\/?)([a-z0-9]+)([^>]*?)(\/?)>/gi;
    let m;
    while ((m = re.exec(html))) {
        if (m[1]) { at = at.parent || at; continue; }
        const attrs = {};
        for (const a of m[3].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) attrs[a[1]] = a[2] ?? '';
        const el = { tag: m[2].toLowerCase(), attrs, kids: [], parent: at };
        at.kids.push(el);
        if (!m[4]) at = el;
    }
    return root;
}
function matches(el, sel) {
    if (sel === '[data-act]') return 'data-act' in el.attrs;
    if (sel === 'a[href]') return el.tag === 'a' && 'href' in el.attrs;
    throw new Error('selector ' + sel);
}
function dress(el) {
    el.closest = sel => { for (let e = el; e && e.tag !== '#root'; e = e.parent) if (matches(e, sel)) return e; return null; };
    el.contains = other => { for (let e = other; e; e = e.parent) if (e === el) return true; return false; };
    el.dataset = Object.fromEntries(Object.entries(el.attrs).filter(([k]) => k.startsWith('data-')).map(([k, v]) => [k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase()), v]));
    el.kids.forEach(dress);
    return el;
}
function find(el, pred) {
    if (pred(el)) return el;
    for (const k of el.kids) { const f = find(k, pred); if (f) return f; }
    return null;
}
"""


@needs_node
def test_case_1_the_pencil_opens_the_edit_sheet_and_the_rest_of_the_row_the_page():
    out = _node("\n".join([PRELUDE, FAKE_DOM, _function("tag"), _function("merchantListRow"), _function("actTarget"), f"""
        const html = `<table><tbody>${{merchantListRow({json.dumps(GRAB)}, {{ renaming: false, renames: {{}} }})}}</tbody></table>`;
        const dom = dress(parse(html));
        const pencil = find(dom, e => e.tag === 'button' && e.attrs['aria-label'] === 'Edit Grab');
        const glyph = pencil && find(pencil, e => e.tag === 'svg');
        const nameCell = find(dom, e => e.tag === 'td');
        const hit = t => {{ const el = actTarget(t); return el && [el.dataset.act, el.dataset.id]; }};
        console.log(JSON.stringify({{ found: !!pencil, onButton: pencil && hit(pencil), onGlyph: glyph && hit(glyph), onName: hit(nameCell) }}));
    """]))
    assert out["found"], "no button labelled 'Edit Grab' in the row"
    # The pencil, or its glyph, opens Edit the merchant for Grab; the row's merchant-page act never sees it.
    assert out["onButton"] == ["merchant", "7"]
    assert out["onGlyph"] == ["merchant", "7"]
    # Anywhere else on the row still opens the merchant page.
    assert out["onName"] == ["merchant-page", "7"]


@needs_node
def test_case_1_no_pencil_while_renaming_several():
    html = _row(GRAB, renaming=True)
    assert 'aria-label="Edit Grab"' not in html
    assert 'data-act="merchant"' not in html
    assert 'data-act="merchant-page"' not in html
    assert 'data-rename="7"' in html


@needs_node
def test_case_2_the_phone_row_carries_the_pencil_as_a_44px_target():
    html = _row(GRAB)
    # The pencil sits in the stacked row's end cell, which the phone draws beside the row's line.
    cell = re.search(r'<td class="([^"]*)">\s*<button([^>]*)aria-label="Edit Grab"', html)
    assert cell, html
    assert "sl-end" in cell.group(1).split()
    classes = re.search(r'class="([^"]*)"', cell.group(2)).group(1).split()
    assert {"btn", "sm", "icon"} <= set(classes)
    # On a phone a small control and an icon button are each the 44px tap target, both ways.
    assert re.search(r"@media \(max-width: 720px\) \{ :root \{[^}]*--ctl-sm: var\(--tap\)", STYLES)
    assert re.search(r"--tap: 44px", STYLES)
    assert re.search(r"\.btn\.sm \{ min-height: var\(--ctl-sm\)", STYLES)
    assert re.search(r"\.btn\.icon \{ width: var\(--ctl-sm\); min-width: var\(--ctl-sm\)", STYLES)
    # And the stacked row places an end cell in its own column, so nothing scrolls sideways.
    assert "table.sl-stack td.sl-end { display: block; grid-column: 2; grid-row: 2;" in STYLES


@needs_node
def test_case_3_a_save_from_the_rows_sheet_toasts_with_undo_closes_and_redraws():
    out = _node("\n".join([PRELUDE, FAKE_DOM, _function("tag"), _function("merchantListRow"), _function("actTarget"), f"""
        const log = [];
        let services = [{json.dumps(GRAB)}, {{ ...{json.dumps(GRAB)}, id: 8, name: 'Gojek' }}];
        const get = async url => {{ if (url === '/api/services') return services; return []; }};
        const refs = async () => ({{ types: [], books: [] }});
        async function servicesList() {{ return get('/api/services'); }}
        const merchantFields = (r, s) => `<input id="mc-name" value="${{esc(s.name)}}">`;
        const toast = (m, o = {{}}) => log.push(['toast', m, o.undo ?? null]);
        let sheet = null;
        const openSheet = (title, body) => {{ sheet = {{ title, body }}; log.push(['open', title]); }};
        const closeSheet = () => {{ sheet = null; log.push(['close']); }};
        const refreshFrame = () => log.push(['frame']);
        const previewedWords = (r, done) => done;
        const fields = {{ 'mc-name': 'Grab', 'mc-book': 'Moom', 'mc-type': '3', 'mc-notes': '' }};
        const $ = sel => ({{ value: fields[sel.slice(1)] ?? '', checked: false }});
        const sent = [];
        const sendPreviewed = async (method, url, body) => {{
            sent.push([method, url, body.book]);
            services = services.map(s => s.id === 7 ? {{ ...s, book: body.book }} : s);
            return {{ ok: true, status: 200, data: {{}}, change: 501 }};
        }};
        let drawn = '';
        const rerender = async () => {{ drawn = (await servicesList()).map(s => merchantListRow(s, {{ renaming: false, renames: {{}} }})).join(''); log.push(['redraw']); }};
        const ACT = {{}};
        {_act(".merchant")}
        {_act("['merchant-save']")}
        (async () => {{
        const dom = dress(parse(`<table><tbody>${{merchantListRow(services[0], {{ renaming: false, renames: {{}} }})}}</tbody></table>`));
        const pencil = find(dom, e => e.tag === 'button' && e.attrs['aria-label'] === 'Edit Grab');
        if (!pencil) {{ console.log(JSON.stringify({{ opened: null }})); return; }}
        const el = actTarget(pencil);
        await ACT[el.dataset.act](el);
        const opened = sheet && sheet.title;
        const save = /data-act="merchant-save" data-id="(\\d+)"/.exec(sheet.body);
        await ACT['merchant-save']({{ dataset: {{ id: save[1] }} }});
        await new Promise(r => setTimeout(r, 0));
        const grabRow = /<tr[^>]*data-id="7"[\\s\\S]*?<\\/tr>/.exec(drawn)[0];
        console.log(JSON.stringify({{ opened, sent, log, grabBook: /<td>Moom<\\/td>/.test(grabRow) }}));
        }})();
    """]))
    assert out["opened"] == "Grab"
    assert out["sent"] == [["PUT", "/api/services/7", "Moom"]]
    assert ["toast", "Merchant saved", 501] in out["log"]
    assert out["log"].index(["close"]) < out["log"].index(["redraw"])
    assert out["grabBook"], "the redrawn list does not show Grab's new book"
