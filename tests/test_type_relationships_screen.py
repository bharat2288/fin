"""Type relationships 06: Lists › Types shows and edits the relationships
(spec F, cases 22-23; the screen side of 3, 6, 7, 10 and 13).

The seam is the page itself, as in test_types_screen.py: the real
`static/app.js` runs under node against the real Flask app on a throwaway
port over a temporary book. What the screen sends is what the server
receives; what the server answers is what the screen draws. Each type's Edit
sheet holds its name, wording and settings; the "fin's own filings" panel
holds a type picker per slot, whose move goes through the preview. Every
name and figure here is invented or the declaration's own.
"""

import html as htmllib
import json
import re
import sqlite3
import threading

import pytest
from werkzeug.serving import make_server

import app as fin_app
import db
from test_type_keys import book_before, every_row, kept_every_row
from test_type_slots import a_year_of_interest, added  # noqa: F401 (a_year_of_interest is a fixture)
from test_types_screen import FILL, as_shown, NODE, STYLES, api, entries, listed, run, server, type_rows, words  # noqa: F401 (fixtures)

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the screen is drawn only in JavaScript")

INTEREST = {"name": "Interest", "covers": "interest charged on the household's loans"}
PRESENTS = {"name": "Presents", "covers": "presents bought for others"}


def spending_part(html):
    """The spending types' table: before the filings panel and the income kinds."""
    return html.split("fin’s own filings", 1)[0]


def edit(server, t, **change):
    """Open a type's Edit sheet, type the change, press Save."""
    return run(server, FILL + f"""
        await ACT['type-edit']({{ dataset: {{ id: '{t["id"]}' }} }});
        const opened = sheet();
        fill({as_shown(t, **change)});
        await press(opened);
        return {{ opened, after: sheet(), toasts: __toasts, view: __byId('view').innerHTML }};
    """)


def slots(api):
    return {s["slot"]: s for s in api.get("/api/types/slots").get_json()["slots"]}


# Move a slot from the panel: pick the type, let the preview open, answer it
# (null: close the sheet). The picker is a stand-in with the value picked.
MOVE = FILL + """
const move = async (slot, typeId, answer) => {
    await viewLists('types');
    const picked = { value: String(typeId), dataset: { slot } };
    const going = ACT['slot-move'](picked);
    await __settle();
    const asked = sheet();
    if (answer === null) closeSheet(); else if (asked) await ACT['preview-answer']({ dataset: { past: answer } });
    await going; await __settle();
    return { asked, after: sheet(), toasts: __toasts, view: __byId('view').innerHTML };
};
"""


# --- case 22: the table -------------------------------------------------------------


def test_case_22_the_types_table_shows_each_types_usual_book_one_off_and_check_each_row(server, api):
    assert api.put(f"/api/types/{listed(api)['Medical']['id']}", json={"check_each_row": True}).status_code == 200
    html = run(server, "return await viewLists('types');")
    head = re.search(r"<thead>(.*?)</thead>", spending_part(html), re.S).group(1)
    assert [words(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)][:6] == [
        "Type", "Covers", "Not for", "Usual book", "One-off", "Check each row"]
    rows = type_rows(spending_part(html))
    cells = lambda name: [words(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", rows[name], re.S)]
    assert cells("Advertising")[3:6] == ["Moom", "", ""]
    assert cells("Software & AI tools")[3] == "ask"
    assert cells("Medical")[3:6] == ["Household", "", "yes"]
    # Every spending type but Other opens its Edit sheet; Other's meaning is fixed.
    shown = listed(api)
    for name in ("Advertising", "Dining", "Medical"):
        assert re.search(rf'<button[^>]*data-act="type-edit" data-id="{shown[name]["id"]}"[^>]*>Edit</button>', rows[name]), name
    assert "not placed yet: fixed" in words(rows["Other"])
    # The page says what is true now: renaming is here, merging and removing are code.
    assert ("Add a type, or change a type's name, wording or settings, here. "
            "Merging or removing a type is a code change.") in words(html).replace("’", "'")


def test_case_22_the_filings_panel_holds_a_type_picker_per_slot_of_the_slots_kind(server, api):
    html = run(server, "return await viewLists('types');")
    panel = html.split("fin’s own filings", 1)[1].split("Income kinds", 1)[0]
    said = words(panel)
    for line in ("Loan interest worked out from your figures", "A gift given", "A gift received"):
        assert line in said, line
    pickers = dict(re.findall(r'<select[^>]*data-change="slot-move" data-slot="(\w+)"[^>]*>(.*?)</select>', panel, re.S))
    assert list(pickers) == ["loan_interest", "gift_given", "gift_received"]
    picked = {slot: words(re.search(r"<option[^>]*selected[^>]*>(.*?)</option>", opts).group(1)) for slot, opts in pickers.items()}
    assert picked == {"loan_interest": "Bank & government fees", "gift_given": "Gifts & Donations", "gift_received": "Gift received"}
    offered = {slot: [words(o) for o in re.findall(r"<option[^>]*>(.*?)</option>", opts)] for slot, opts in pickers.items()}
    # A spending slot offers spending types, an income slot income kinds; never Other.
    assert "Dining" in offered["loan_interest"] and "Salary" not in offered["loan_interest"]
    assert "Salary" in offered["gift_received"] and "Dining" not in offered["gift_received"]
    assert all("Other" not in o for o in offered.values())


# --- case 22: the Edit sheet ----------------------------------------------------------


def test_case_22_the_edit_sheet_holds_name_wording_and_settings_as_they_are(server, api):
    ad = listed(api)["Advertising"]
    out = run(server, f"""
        await ACT['type-edit']({{ dataset: {{ id: '{ad["id"]}' }} }});
        return __byId('sheets').innerHTML;
    """)
    assert re.search(r'<input[^>]*id="ty-name"[^>]*value="Advertising"', out)
    assert htmllib.unescape(re.search(r'<textarea[^>]*id="ty-covers"[^>]*>(.*?)</textarea>', out, re.S).group(1)) == ad["covers"]
    assert re.search(r'id="ty-notfor"', out)
    book = re.search(r'<select id="ty-book">(.*?)</select>', out, re.S).group(1)
    assert [v for v in re.findall(r'<option value="([^"]*)"', book)] == ["Household", "Moom", "Kalesh", "ask"]
    assert re.search(r'<option value="Moom" selected>', book)
    assert re.search(r'id="ty-oneoff"', out) and not re.search(r'id="ty-oneoff"[^>]*checked', out)
    assert re.search(r'id="ty-each"', out) and not re.search(r'id="ty-each"[^>]*checked', out)
    assert 'data-act="type-edit-save"' in out


def test_case_3_usual_book_changed_from_the_sheet_is_one_change_and_redraws(server, api):
    ad = listed(api)["Advertising"]
    out = edit(server, ad, **{"ty-book": "Household"})
    [entry] = entries(api)
    assert entry["summary"] == "Changed Advertising: usual book Household"
    [toast] = out["toasts"]
    assert not toast["bad"] and toast["undo"]
    assert out["after"] == ""
    assert listed(api)["Advertising"]["usual_book"] == "Household"
    assert listed(api)["Advertising"]["covers"] == ad["covers"]
    row = type_rows(spending_part(out["view"]))["Advertising"]
    assert [words(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)][3] == "Household"


def test_cases_6_and_7_one_off_and_check_each_row_from_the_same_sheet_are_one_change(server, api):
    medical = listed(api)["Medical"]
    out = edit(server, medical, **{"ty-oneoff": True, "ty-each": True})
    [entry] = entries(api)
    assert entry["summary"] == "Changed Medical: one-off by default, Check each row by default"
    assert not out["toasts"][0]["bad"] and out["toasts"][0]["undo"]
    after = listed(api)["Medical"]
    assert (after["default_one_off"], after["check_each_row"]) == (1, True)


def test_case_13_rename_from_the_sheet_is_one_change_and_every_row_shows_it(server, api):
    dining = listed(api)["Dining"]
    out = edit(server, dining, **{"ty-name": "Food"})
    [entry] = entries(api)
    assert entry["summary"] == "Renamed Dining to Food"
    [toast] = out["toasts"]
    assert not toast["bad"] and toast["undo"] and "Renamed Dining to Food" in htmllib.unescape(toast["words"])
    assert listed(api)["Food"]["id"] == dining["id"]
    rows = type_rows(spending_part(out["view"]))
    assert "Food" in rows and "Dining" not in rows


def test_case_22_a_refused_edit_shows_the_servers_words_keeps_the_sheet_and_writes_nothing(server, api):
    dining = listed(api)["Dining"]
    out = edit(server, dining, **{"ty-name": "medical"})
    [toast] = out["toasts"]
    assert toast["bad"] and not toast["undo"]
    assert "Medical is already a type" in htmllib.unescape(toast["words"])
    assert out["after"] == out["opened"]
    assert entries(api) == []


def test_an_edit_that_changes_nothing_writes_nothing_and_says_so(server, api):
    out = edit(server, listed(api)["Dining"])
    [toast] = out["toasts"]
    assert not toast["bad"] and not toast["undo"]
    assert "Nothing to save" in htmllib.unescape(toast["words"])
    assert entries(api) == []


def test_case_22_others_sheet_fixes_its_wording_only_and_says_its_meaning_is_fixed(server, api):
    other = listed(api)["Other"]
    out = run(server, FILL + f"""
        await ACT['type-edit']({{ dataset: {{ id: '{other["id"]}' }} }});
        const opened = sheet();
        fill({{ 'ty-covers': 'anything not placed yet', 'ty-notfor': {json.dumps(other["not_for"] or "")} }});
        await press(opened);
        return {{ opened, toasts: __toasts }};
    """)
    for field in ("ty-name", "ty-book", "ty-oneoff", "ty-each"):
        assert f'id="{field}"' not in out["opened"], field
    assert "not placed yet: its name and settings are fixed" in words(out["opened"])
    [entry] = entries(api)
    assert entry["summary"] == "Fixed the wording of Other"
    assert listed(api)["Other"]["covers"] == "anything not placed yet"


# --- case 10 (and 11): a slot moved from the panel goes through the preview --------------


def test_case_10_moving_loan_interest_previews_its_past_rows_and_apply_moves_them(server, client, a_year_of_interest):
    api = client  # one test client: the fixture's
    interest = added(api, INTEREST)
    out = run(server, MOVE + f"return await move('loan_interest', {interest}, 'apply');")
    asked = words(out["asked"])
    assert "12 past rows fin filed as loan interest" in asked
    assert re.search(r'data-act="preview-answer" data-past="only_new"', out["asked"])
    assert re.search(r'data-act="preview-answer" data-past="apply"[^>]*>Apply to 12 past rows<', out["asked"])
    assert entries(api)[0]["summary"] == "fin files loan interest under Interest, with its 12 past rows"
    [toast] = out["toasts"]
    assert not toast["bad"] and toast["undo"]
    assert "fin files loan interest under Interest" in htmllib.unescape(toast["words"])
    assert out["after"] == ""
    assert slots(api)["loan_interest"]["display_type"] == "Interest"


def test_case_10_only_new_rows_from_the_panel_leaves_the_past_rows(server, client, a_year_of_interest):
    api = client  # one test client: the fixture's
    interest = added(api, INTEREST)
    out = run(server, MOVE + f"return await move('loan_interest', {interest}, 'only_new');")
    assert "12 past rows fin filed as loan interest" in words(out["asked"])
    assert entries(api)[0]["summary"] == "fin files loan interest under Interest, new rows only"
    assert not out["toasts"][0]["bad"] and out["toasts"][0]["undo"]
    assert slots(api)["loan_interest"]["display_type"] == "Interest"


def test_case_11_a_gift_slot_says_it_cannot_tell_its_past_rows_and_offers_new_only(server, api):
    presents = added(api, PRESENTS)
    out = run(server, MOVE + f"return await move('gift_given', {presents}, 'only_new');")
    assert "fin cannot tell which past rows it filed as gifts given" in words(out["asked"])
    assert 'data-past="apply"' not in out["asked"]
    assert re.search(r'data-act="preview-answer" data-past="only_new"', out["asked"])
    assert entries(api)[0]["summary"] == "fin files gifts given under Presents, new rows only"
    assert slots(api)["gift_given"]["display_type"] == "Presents"


def test_a_slot_preview_closed_writes_nothing_and_the_picker_redraws_as_it_was(server, client, a_year_of_interest):
    api = client  # one test client: the fixture's
    interest = added(api, INTEREST)
    before = len(entries(api))
    out = run(server, MOVE + f"return await move('loan_interest', {interest}, null);")
    assert out["asked"]
    assert len(entries(api)) == before
    assert slots(api)["loan_interest"]["display_type"] == "Bank & government fees"
    panel = out["view"].split("fin’s own filings", 1)[1]
    picker = re.search(r'data-slot="loan_interest"[^>]*>(.*?)</select>', panel, re.S).group(1)
    assert words(re.search(r"<option[^>]*selected[^>]*>(.*?)</option>", picker).group(1)) == "Bank & government fees"


def test_a_slot_with_no_past_rows_moves_without_asking(server, api):
    interest = added(api, INTEREST)
    out = run(server, MOVE + f"return await move('loan_interest', {interest}, 'apply');")
    assert out["asked"] == ""
    assert entries(api)[0]["summary"] == "fin files loan interest under Interest"
    assert not out["toasts"][0]["bad"] and out["toasts"][0]["undo"]


# --- Changes names these changes plainly --------------------------------------------------


def change_html(server, api):
    entry = entries(api)[0]
    return run(server, f"return await changeRowsHTML(await get('/api/history/{entry['id']}', {{ fresh: true }}));")


def test_changes_names_a_settings_change_by_its_type_and_its_settings_in_words(server, api):
    medical = listed(api)["Medical"]
    assert api.put(f"/api/types/{medical['id']}", json={"usual_book": "ask", "check_each_row": True}).status_code == 200
    html = change_html(server, api)
    said = words(html)
    assert "type Medical" in said and "type_settings" not in said
    lines = {words(f): (words(w), words(n)) for f, w, n in re.findall(
        r'<td class="c-field">(.*?)</td><td class="c-was">(.*?)</td>\s*<td class="c-now">(.*?)</td>', html, re.S)}
    assert lines["usual book"] == ("Household", "ask")
    assert lines["Check each row by default"] == ("no", "yes")


def test_changes_names_a_slot_move_and_an_income_kind_by_its_name(server, api):
    salary = listed_income(api)["Salary"]["id"]
    resp = api.put("/api/types/slots/gift_received", json={"type_id": salary, "past": "only_new"})
    assert resp.status_code == 200, resp.get_json()
    html = change_html(server, api)
    said = words(html)
    assert "what fin files as gifts received" in said and "type_slots" not in said
    [(was, now)] = [(words(w), words(n)) for w, n in re.findall(
        r'<td class="c-field">type</td><td class="c-was">(.*?)</td>\s*<td class="c-now">(.*?)</td>', html, re.S)]
    assert (was, now) == ("Gift received", "Salary")


def listed_income(api):
    return {t["display_name"]: t for t in api.get("/api/types?kind=income").get_json()}


# --- case 23: the phone -------------------------------------------------------------------


def test_case_23_on_a_phone_edit_sits_in_the_rows_end_and_every_control_is_44px(server):
    html = run(server, "return await viewLists('types');")
    rows = type_rows(spending_part(html))
    cell = re.search(r'<td class="([^"]*)"><button[^>]*class="([^"]*)"[^>]*data-act="type-edit"', rows["Dining"])
    assert cell and "sl-end" in cell.group(1).split() and {"btn", "sm"} <= set(cell.group(2).split())
    # The slot pickers are the 44px select on a phone, and stack under their words.
    assert re.search(r'<select class="sel"[^>]*data-change="slot-move"', html)
    assert re.search(r"select\.sel \{[^}]*min-height: var\(--ctl-sm\)", STYLES)
    assert re.search(r"@media \(max-width: 720px\) \{[^@]*\.slot-row \{[^}]*grid-template-columns: minmax\(0, 1fr\)", STYLES)


# --- a book made before this effort -------------------------------------------------------


def test_a_book_made_before_this_effort_starts_and_draws_types_and_filings_changing_no_row(tmp_path, monkeypatch):
    path = book_before(tmp_path, monkeypatch)
    before = every_row(path)
    db.init_db()
    srv = make_server("127.0.0.1", 0, fin_app.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        html = run(f"http://127.0.0.1:{srv.server_port}", "return await viewLists('types');")
    finally:
        srv.shutdown()
    assert "Advertising" in type_rows(spending_part(html))
    assert len(re.findall(r'data-change="slot-move"', html)) == 3
    kept_every_row(before, every_row(path))
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0] == before["change_entries"].__len__()
    finally:
        conn.close()
