"""Walk-fixes 07: Lists › Types adds a type and fixes a type's wording, and says so
(spec D case 15, T cases 21-28, the screen side).

The seam is the page itself. The real `static/app.js` runs under node, in a vm with a
stand-in document, against the real Flask app on a throwaway port over a temporary book:
what the screen sends is what the server receives, and what the server answers is what
the screen draws. A sheet's fields are stand-in elements the test fills, as the operator
would type into them. Every name and wording here is invented or the declaration's own.
"""

import html as htmllib
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

import app as fin_app

STATIC = Path(__file__).resolve().parent.parent / "static"
APP_JS_PATH = STATIC / "app.js"
STYLES = (STATIC / "styles.css").read_text(encoding="utf-8")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the screen is drawn only in JavaScript")

DINING_COVERS = "restaurants, bars, fast food, food delivery, coffee shops, cafes, bakeries and drinks shops"
FIXED_COVERS = DINING_COVERS + ", hawker centres"

# app.js in a vm. Elements are found by id and made on first ask, so a sheet's fields
# keep what the test types into them; the toasts are kept in order. Long timers (a
# toast's own removal) never fire, so the run ends when the page's work does.
HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const [, appJs, base, body] = process.argv;
const mk = () => {
  const e = { innerHTML: '', textContent: '', value: '', checked: false, disabled: false, dataset: {}, kids: [], style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, removeAttribute() {}, getAttribute: () => null, addEventListener() {},
    querySelector: () => mk(), querySelectorAll: () => [], focus() {}, remove() {} };
  e.appendChild = k => { e.kids.push(k); return k; };
  return e;
};
const els = {};
const byId = id => (els[id] = els[id] || mk());
const toasts = [];
byId('toasts').appendChild = box => { toasts.push({ words: box.innerHTML, bad: box.className.includes('bad'), undo: box.kids.some(k => k.textContent === 'Undo') }); return box; };
const doc = {
  addEventListener() {}, removeEventListener() {}, querySelectorAll: () => [], documentElement: mk(), body: mk(),
  querySelector: sel => (sel.startsWith('#') ? byId(sel.slice(1)) : mk()),
  getElementById: id => byId(id), createElement: () => mk(),
};
const store = {};
const ctx = {
  console, document: doc, Date, Intl, URLSearchParams, FormData: class {}, innerWidth: 1366,
  setTimeout: (fn, ms) => (ms > 1000 ? 0 : setTimeout(fn, ms)),
  localStorage: { getItem: k => store[k] ?? null, setItem(k, v) { store[k] = v; } },
  location: { hash: '#/books/lists/types' }, matchMedia: () => ({ matches: false, addEventListener() {} }),
  getComputedStyle: () => ({ getPropertyValue: () => '' }), addEventListener() {}, scrollTo() {},
  confirm: () => true, fetch: (u, o) => fetch(new URL(u, base), o),
  __els: els, __byId: byId, __toasts: toasts, __settle: () => new Promise(r => setTimeout(r, 300)),
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(appJs, 'utf8'), ctx, { filename: 'app.js' });
(async () => {
  const out = await vm.runInContext('(async () => {' + body + '})()', ctx);
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def run(base, body):
    done = subprocess.run([NODE, "-e", HARNESS, str(APP_JS_PATH), base, body],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture
def server(temp_db):
    """The real app on a throwaway local port, over the temporary book."""
    srv = make_server("127.0.0.1", 0, fin_app.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()


@pytest.fixture
def api(temp_db):
    fin_app.app.config["TESTING"] = True
    with fin_app.app.test_client() as c:
        yield c


def words(fragment):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def type_rows(html):
    """Lists › Types' rows, by the name each row shows."""
    return {words(m.group(1)): m.group(0) for m in re.finditer(r'<tr><td class="sl-name"><b>([^<]*)</b>.*?</tr>', html, re.S)}


def listed(api):
    return {t["display_name"]: t for t in api.get("/api/types").get_json()}


def entries(api):
    return api.get("/api/history").get_json()["entries"]


def as_shown(t, **change):
    """Every field of a type's Edit sheet as the sheet shows it, with `change` typed over it."""
    shown = {"ty-name": t["name"], "ty-covers": t["covers"] or "", "ty-notfor": t["not_for"] or "",
             "ty-book": t["usual_book"], "ty-oneoff": bool(t["default_one_off"]), "ty-each": bool(t["check_each_row"])}
    shown.update(change)
    return json.dumps(shown)


# Fill a sheet's fields as the operator types them, then press its Save.
FILL = """
const fill = values => Object.entries(values).forEach(([id, v]) => { const e = __byId(id); if (typeof v === 'boolean') e.checked = v; else e.value = v; });
const sheet = () => __byId('sheets').innerHTML;
const press = async html => { const m = /data-act="(type-[\\w-]+-save)"(?: data-id="(\\d*)")?/.exec(html); await ACT[m[1]]({ dataset: { id: m[2] || '' } }); await __settle(); };
"""


# --- case 15 ---------------------------------------------------------------------


def test_case_15_the_types_card_says_what_is_true_and_offers_add(server):
    html = run(server, "return await viewLists('types');")
    said = words(html)
    assert ("One list serves every book. Add a type, or change a type's name, wording or settings, here. "
            "Merging or removing a type is a code change.") in said.replace("’", "'")
    assert "ask Claude in chat" not in said
    assert re.search(r'<button class="btn"[^>]*data-act="type-add"[^>]*>.*?Add a type</button>', html, re.S)


# --- case 25 (walk-fixes), now queue-naming cases 21-22 (fin RUL-004) -------------


def test_every_spending_type_offers_edit_and_others_name_is_set_in_code(server, api):
    html = run(server, "return await viewLists('types');")
    # The spending table only: an income kind may share a name ("Other").
    rows = type_rows(html.split("Income kinds", 1)[0])
    shown = listed(api)
    for locked in ("Advertising", "Other", "Gifts & Donations"):
        assert re.search(rf'<button[^>]*data-act="type-edit" data-id="{shown[locked]["id"]}"[^>]*>Edit</button>', rows[locked]), locked
        assert "Set in code" not in words(rows[locked]), locked
    # Only Other's name stays as declared (type-relationships 04): code finds
    # every other type it means by its key, so those are renamed in the app.
    assert "name set in code" in words(rows["Other"])
    for renamed in ("Advertising", "Gifts & Donations"):
        assert "name set in code" not in words(rows[renamed]), renamed
    dining = shown["Dining"]
    assert re.search(rf'<button[^>]*data-act="type-edit" data-id="{dining["id"]}"[^>]*>Edit</button>', rows["Dining"])
    assert "set in code" not in words(rows["Dining"]).lower()
    # The income kinds stay set in code: no fix offered.
    kinds = html.split("Income kinds", 1)
    if len(kinds) == 2 and "<tbody>" in kinds[1]:
        assert 'data-act="type-edit"' not in kinds[1]
        assert "Set in code" in words(kinds[1])


def test_case_21_advertisings_wording_fixed_from_the_screen_is_one_change(server, api):
    ad = listed(api)["Advertising"]
    fixed = "paid adverts and promotion on search, social media, marketplaces and podcasts"
    out = run(server, FILL + f"""
        await ACT['type-edit']({{ dataset: {{ id: '{ad["id"]}' }} }});
        const opened = sheet();
        fill({as_shown(ad, **{"ty-covers": fixed})});
        await press(opened);
        return {{ opened, after: sheet(), toasts: __toasts, view: __byId('view').innerHTML }};
    """)
    # The Edit sheet holds the name too (type-relationships 06): Advertising's
    # name is no longer the code's. Only the wording changed, so only it is sent.
    assert 'id="ty-name"' in out["opened"]
    assert "Its name is set in code" not in words(out["opened"])
    [entry] = entries(api)
    assert entry["summary"] == "Fixed the wording of Advertising"
    [toast] = out["toasts"]
    assert not toast["bad"] and toast["undo"]
    assert out["after"] == ""
    after = listed(api)["Advertising"]
    assert (after["name"], after["covers"], after["not_for"], after["locked"]) == ("Advertising", fixed, ad["not_for"], False)
    assert "podcasts" in words(type_rows(out["view"])["Advertising"])


# --- cases 21 and 23 -------------------------------------------------------------


def test_case_21_add_a_type_from_the_screen_lands_marked_and_reaches_the_pickers(server, api):
    kids = listed(api)["Kids"]["id"]
    out = run(server, FILL + f"""
        await ACT['type-add']({{ dataset: {{}} }});
        const opened = sheet();
        fill({{ 'ty-name': 'Childcare', 'ty-parent': '{kids}', 'ty-oneoff': false,
               'ty-covers': 'nannies, after-school care, holiday camps', 'ty-notfor': 'school fees' }});
        await press(opened);
        const r = await refs();
        return {{ opened, after: sheet(), toasts: __toasts, view: __byId('view').innerHTML,
                 picker: typeOptions(r.types, null) }};
    """)
    # The sheet asks for the five things, under a top-level type, one-off unticked.
    for field in ("ty-name", "ty-parent", "ty-oneoff", "ty-covers", "ty-notfor"):
        assert f'id="{field}"' in out["opened"], field
    assert re.search(rf'<option value="{kids}">Kids</option>', out["opened"])
    assert not re.search(r'id="ty-oneoff"[^>]*checked', out["opened"])
    # It lands: one change, named, offered with Undo; the sheet closes.
    [entry] = entries(api)
    assert entry["summary"] == "Added the type Kids > Childcare"
    [toast] = out["toasts"]
    assert not toast["bad"] and toast["undo"]
    assert "Added the type Kids > Childcare" in htmllib.unescape(toast["words"])
    assert out["after"] == ""
    # It shows: marked added in fin, its usual book Household, and in every type picker.
    row = type_rows(out["view"])["Kids > Childcare"]
    assert "added in fin" in words(row)
    assert "Household" in words(row)
    assert "nannies, after-school care, holiday camps" in words(row)
    assert ">Kids &gt; Childcare</option>" in out["picker"]
    added = listed(api)["Kids > Childcare"]
    assert (added["covers"], added["not_for"], added["default_one_off"]) == ("nannies, after-school care, holiday camps", "school fees", 0)


def test_case_21_a_one_off_ticked_is_sent_as_one_off(server, api):
    run(server, FILL + """
        await ACT['type-add']({ dataset: {} });
        const opened = sheet();
        fill({ 'ty-name': 'Moving house', 'ty-parent': '', 'ty-oneoff': true, 'ty-covers': 'movers, packing boxes', 'ty-notfor': '' });
        await press(opened);
        return null;
    """)
    added = listed(api)["Moving house"]
    assert (added["parent_id"], added["default_one_off"], added["not_for"]) == (None, 1, None)


# --- case 22 ---------------------------------------------------------------------


@pytest.mark.parametrize("name, covers, reason", [
    ("dining", "eating out", "Dining is already a type"),
    ("Childcare", "", "a type needs words for what it covers"),
])
def test_case_22_a_refusal_shows_the_servers_reason_and_keeps_the_sheet_open(server, api, name, covers, reason):
    out = run(server, FILL + f"""
        await ACT['type-add']({{ dataset: {{}} }});
        const opened = sheet();
        fill({{ 'ty-name': {json.dumps(name)}, 'ty-parent': '', 'ty-oneoff': false, 'ty-covers': {json.dumps(covers)}, 'ty-notfor': '' }});
        await press(opened);
        return {{ opened, after: sheet(), toasts: __toasts }};
    """)
    [toast] = out["toasts"]
    assert toast["bad"] and not toast["undo"]
    assert reason in htmllib.unescape(toast["words"])
    assert out["after"] == out["opened"]          # still open, as typed
    assert entries(api) == []                     # nothing written


# --- case 24 ---------------------------------------------------------------------


def test_case_24_fix_the_wording_from_the_screen_lands_and_shows(server, api):
    dining = listed(api)["Dining"]
    out = run(server, FILL + f"""
        await ACT['type-edit']({{ dataset: {{ id: '{dining["id"]}' }} }});
        const opened = sheet();
        // The operator adds to what it covers and leaves the rest as the sheet shows it.
        fill({as_shown(dining, **{"ty-covers": FIXED_COVERS})});
        await press(opened);
        return {{ opened, after: sheet(), toasts: __toasts, view: __byId('view').innerHTML }};
    """)
    # The sheet holds the wording as it is, and says what it cannot change.
    assert DINING_COVERS in htmllib.unescape(out["opened"])
    assert "Merging or removing a type is a code change." in words(out["opened"])
    [entry] = entries(api)
    assert entry["summary"] == "Fixed the wording of Dining"
    [toast] = out["toasts"]
    assert not toast["bad"] and toast["undo"] and "Fixed the wording of Dining" in toast["words"]
    assert out["after"] == ""
    assert listed(api)["Dining"]["covers"] == FIXED_COVERS
    assert listed(api)["Dining"]["not_for"] == dining["not_for"]
    assert "hawker centres" in words(type_rows(out["view"])["Dining"])


def test_case_24_the_fix_sheet_fills_its_fields_with_the_wording(server, api):
    dining = listed(api)["Dining"]
    out = run(server, f"""
        await ACT['type-edit']({{ dataset: {{ id: '{dining["id"]}' }} }});
        return __byId('sheets').innerHTML;
    """)
    covers = re.search(r'<textarea[^>]*id="ty-covers"[^>]*>(.*?)</textarea>', out, re.S)
    assert covers and htmllib.unescape(covers.group(1)) == DINING_COVERS
    notfor = re.search(r'<textarea[^>]*id="ty-notfor"[^>]*>(.*?)</textarea>', out, re.S)
    assert notfor and htmllib.unescape(notfor.group(1)) == (dining["not_for"] or "")


# --- Changes: a types row named, its wording before and after, its Undo -----------


def test_changes_names_a_types_row_shows_its_wording_and_undo_puts_it_back(server, api):
    dining = listed(api)["Dining"]
    resp = api.put(f"/api/types/{dining['id']}", json={"covers": FIXED_COVERS})
    assert resp.status_code == 200
    [entry] = entries(api)
    out = run(server, f"""
        const entry = await get('/api/history/{entry["id"]}', {{ fresh: true }});
        const html = await changeRowsHTML(entry);
        await undoEntry({entry["id"]});
        return {{ html, toasts: __toasts }};
    """)
    said = words(out["html"])
    assert "type Dining" in said
    assert re.search(r"covers\s*</td>", out["html"])
    was = re.search(r'<td class="c-was">(.*?)</td>', out["html"], re.S).group(1)
    now = re.search(r'<td class="c-now">(.*?)</td>', out["html"], re.S).group(1)
    assert words(was) == DINING_COVERS and words(now) == FIXED_COVERS
    assert listed(api)["Dining"]["covers"] == DINING_COVERS
    assert not out["toasts"][0]["bad"]


def test_changes_names_an_added_type_by_its_display_name(server, api):
    kids = listed(api)["Kids"]["id"]
    resp = api.post("/api/types", json={"name": "Childcare", "parent_id": kids, "covers": "nannies"})
    assert resp.status_code == 201
    [entry] = entries(api)
    html = run(server, f"""
        return await changeRowsHTML(await get('/api/history/{entry["id"]}', {{ fresh: true }}));
    """)
    assert "type Kids > Childcare" in words(html)
    # An added type shows the wording it came with, against "not there".
    covers = re.search(r'<td class="c-field">covers</td><td class="c-was">(.*?)</td>\s*<td class="c-now">(.*?)</td>', html, re.S)
    assert covers, html
    assert words(covers.group(1)) == "not there" and words(covers.group(2)) == "nannies"


def test_a_refused_undo_names_the_type_that_blocks_it_as_every_screen_does(server, api):
    kids = listed(api)["Kids"]["id"]
    resp = api.post("/api/types", json={"name": "Childcare", "parent_id": kids, "covers": "nannies"})
    assert resp.status_code == 201
    resp = api.put(f"/api/types/{listed(api)['Kids > Childcare']['id']}", json={"covers": "nannies, creches"})
    assert resp.status_code == 200
    html = run(server, "return await viewChanges();")
    refusal = re.search(r'<div class="notice refusal">(.*?)<button', html, re.S)
    assert refusal, html
    said = words(refusal.group(1))
    assert "fixed the wording of Kids > Childcare" in said, said


# --- the phone -------------------------------------------------------------------


def test_the_phone_puts_edit_in_the_rows_end_cell_and_every_control_is_44px(server):
    html = run(server, "return await viewLists('types');")
    # The spending table only: an income kind shares Other's name.
    rows = type_rows(html.split("Income kinds", 1)[0])
    cell = re.search(r'<td class="([^"]*)"><button[^>]*class="([^"]*)"[^>]*data-act="type-edit"', rows["Dining"])
    assert cell and "sl-end" in cell.group(1).split() and {"btn", "sm"} <= set(cell.group(2).split())
    assert "sl-end" in re.search(r'<td class="([^"]*)">[^<]*<span[^>]*>name set in code', rows["Other"]).group(1).split()
    # A small control and a plain button are each the 44px tap target on a phone.
    assert re.search(r"--tap: 44px", STYLES)
    assert re.search(r"@media \(max-width: 720px\) \{ :root \{[^}]*--ctl-sm: var\(--tap\)", STYLES)
    assert re.search(r"\.btn\.sm \{ min-height: var\(--ctl-sm\)", STYLES)
    assert "table.sl-stack td.sl-end { display: block; grid-column: 2; grid-row: 2;" in STYLES
