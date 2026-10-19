"""The marks shown: the merchant page's tags and a row's sheet (fin-surfaces 52;
spec 24, case 9 and §Implementation Decisions, the source marks).

A row's book and its type each say where they came from. The merchant page's
state column tags a field checked (dashed) or chosen (solid), and says nothing
for one from the merchant. A row's sheet has one line per field: where it came
from and, for checked or chosen, who and when, read from the change history
(the latest entry that set that field).

Each row is read through the real /api/transactions route; the client
functions are lifted out of static/app.js and run under node with the script
on stdin. The database is the temporary one; every merchant, name and figure
is invented.
"""

import itertools
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

import mcp_tools
from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    merchant, statement, type_id,
)
from test_client_seams import APP_JS, NODE, needs_node
from test_label_source_writes import CHAT_ACTOR, check_each_row, row

STATIC = Path(__file__).resolve().parent.parent / "static"


def _function(name):
    m = re.search(rf"^function {name}\(.*\}}$", APP_JS, re.M) or re.search(rf"^function {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _const(name):
    m = re.search(rf"^(?:const|let) {name} = .*?;$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _node(expr, **data):
    """Run app.js's own source-mark functions under node (in UTC, so a
    history time's day is its date) and return what `expr` gives."""
    code = "\n".join([
        _const("MONTHS"), _function("esc"), _function("day"), _function("historyDay"),
        _function("whoName"), _const("SOURCE_WORDS"), _function("sourceTagsHTML"),
        _function("sourceLineWords"), _function("sourceLinesHTML"),
        f"const DATA = {json.dumps(data)};", f"console.log(JSON.stringify({expr}));"])
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, encoding="utf-8",
                         timeout=30, check=True, env={**os.environ, "TZ": "UTC"})
    return json.loads(out.stdout)


def text(html: str) -> str:
    """What a reader sees: the tags gone, the spaces single."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace(" ,", ",").strip()


def shown(client, tx_id) -> dict:
    return client.get(f"/api/transactions?tx_id={tx_id}").get_json()["transactions"][0]


def dated(conn, entry_id: int, at: str) -> None:
    """Put a change entry on a day of the case (synthetic: the test's own book)."""
    conn.execute("UPDATE change_entries SET at = ? WHERE id = ?", (at, entry_id))
    conn.commit()


@pytest.fixture
def indigo(conn, statement):
    """IndigoAir, usual Household · Travel, one row from the merchant."""
    svc = merchant(conn, "IndigoAir", "Household", "Travel")
    tx = row(conn, statement, "INDIGOAIR 312-4567890 MUMBAI", 642.30, service_id=svc, day="2026-10-02")
    return svc, tx


# --- case 9: the row sheet shows sources ------------------------------------------------------


@needs_node
def test_case_9_chosen_by_claude_in_chat_and_checked_by_you_read_from_the_history(client, conn, indigo):
    svc, tx = indigo
    check_each_row(conn, svc)
    checked = client.post("/api/transactions/bulk-label", json={
        "rows": [{"tx_id": tx, "book": "Household", "type_id": type_id(conn, "Travel")}]})
    assert checked.status_code == 200, checked.get_json()
    dated(conn, int(checked.headers["X-Fin-Change"]), "2026-10-09 03:00:00")
    chose = mcp_tools.call("set_label", {"tx_id": tx, "book": "Moom"}, actor=CHAT_ACTOR)
    assert chose["ok"], chose
    entry = chose["change"]["entry_id"]
    dated(conn, entry, "2026-10-12 05:30:00")

    found = shown(client, tx)

    assert found["sources"] == {"book": "chosen", "type": "checked"}
    assert found["set_by"]["book"]["entry_id"] == entry
    assert found["set_by"]["book"]["via"] == "chat"
    assert found["set_by"]["type"]["entry_id"] == int(checked.headers["X-Fin-Change"])
    assert found["set_by"]["type"]["via"] == "app"
    lines = _node("sourceLinesHTML(DATA.found)", found=found)
    assert [text(li) for li in re.findall(r"<li>.*?</li>", lines)] == [
        "Book chosen by Claude in chat, 12 Oct 2026 · see the change",
        "Type checked by you, 9 Oct 2026; follows IndigoAir",
    ]
    assert f'data-act="goto-change" data-entry="{entry}"' in lines


@needs_node
def test_case_9_a_field_chosen_before_the_history_began_and_one_from_the_merchant(client, conn, statement):
    svc = merchant(conn, "IndigoAir", "Household", "Travel")
    tx = row(conn, statement, "INDIGOAIR 6E 0042", service_id=svc, book="Moom",
             sources=("chosen", "merchant"), cat_source="manual")

    found = shown(client, tx)

    assert found["sources"] == {"book": "chosen", "type": "merchant"}
    assert found["set_by"] == {"book": None, "type": None}
    lines = _node("sourceLinesHTML(DATA.found)", found=found)
    assert [text(li) for li in re.findall(r"<li>.*?</li>", lines)] == [
        "Book chosen, before fin kept a history",
        "Type from IndigoAir",
    ]
    assert "see the change" not in lines


@needs_node
def test_the_other_sources_each_read_in_words(client, conn, statement):
    svc = merchant(conn, "IndigoAir", "Household", "Travel")
    by_rule = row(conn, statement, "INDIGOAIR 6E 0043", service_id=svc, sources=("merchant", "rule"),
                  cat_source="rule_override")
    by_wording = row(conn, statement, "PAYNOW SAMPLE STALL", sources=("wording", "wording"),
                     cat_source="fallback")
    worked = row(conn, statement, "SAMPLE LOAN INTEREST", sources=("derived", "derived"), cat_source="derived")
    untyped = row(conn, statement, "SAMPLE UNKNOWN 0001", type_name=None, sources=("merchant", None),
                  cat_source="auto")

    def words(tx):
        return [text(li) for li in re.findall(r"<li>.*?</li>", _node("sourceLinesHTML(DATA.f)", f=shown(client, tx)))]

    assert words(by_rule) == ["Book from IndigoAir", "Type from a rule for IndigoAir"]
    assert words(by_wording) == ["Book from its wording", "Type from its wording"]
    assert words(worked) == ["Book worked out from figures", "Type worked out from figures"]
    assert words(untyped) == ["Book from its merchant"]


def _facts(found, details):
    """The row sheet's facts list as openRowSheet draws it: rowFactsHTML with
    what labelledBy reads from the row and its history."""
    code = "\n".join([
        "const tag = (k, t) => `<tag ${k}>${t}</tag>`;", "function todayIso() { return '2026-10-20'; }",
        _const("MONTHS"), _function("esc"), _function("when"), _const("pageLinkKinds"), _function("pageLink"),
        _function("notCheckedTag"), _function("whoName"), _const("SKIP_FIELDS"), _function("changedFields"),
        _const("LABEL_SOURCE_WORDS"), _const("LABEL_FIELDS"), _function("labelledBy"), _function("rowFactsHTML"),
        f"const DATA = {json.dumps({'found': found, 'details': details})};",
        "console.log(JSON.stringify(rowFactsHTML(DATA.found, 'Travel', labelledBy(DATA.found, DATA.details))));"])
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, encoding="utf-8",
                         timeout=30, check=True, env={**os.environ, "TZ": "UTC"})
    return json.loads(out.stdout)


def _details(client, tx_id) -> list:
    """The row's own history, each entry opened, as rowHistory reads it."""
    listed = client.get(f"/api/history/row/transactions/{tx_id}").get_json()["entries"]
    return [client.get(f"/api/history/{e['id']}").get_json() for e in listed[:12]]


@needs_node
def test_case_9_the_row_sheet_says_each_fields_source_not_the_old_single_mark(client, conn, indigo):
    """Walk lp-r1: chat's set_label to the book the row already had changed no
    value, so the facts list fell back to the old single mark and read
    "Labelled by · by hand" above "Book chosen by Claude in chat". A field
    with a source has its own line (one line per field, mockup screen 5);
    "hand-set" is retired for chosen (decision 24, Words)."""
    _, tx = indigo
    chose = mcp_tools.call("set_label", {"tx_id": tx, "book": "Household"}, actor=CHAT_ACTOR)
    assert chose["ok"], chose
    found = shown(client, tx)
    assert found["sources"]["book"] == "chosen" and found["cat_source"] == "manual"

    facts = text(_facts(found, _details(client, tx)))

    assert "Labelled by" not in facts and "by hand" not in facts
    assert "Book Household" in facts and "Merchant IndigoAir" in facts


@needs_node
def test_a_row_with_no_book_or_type_still_says_who_labelled_it(client, conn, statement):
    """A row with neither field (a transfer) has no source line, so its facts
    keep the line saying who labelled it and when."""
    tx = row(conn, statement, "SAMPLE TRANSFER OUT", book=None, type_name=None, flow="transfer",
             sources=(None, None), cat_source="auto")
    put = client.put(f"/api/transactions/{tx}", json={"notes": "to savings"})
    assert put.status_code == 200
    found = shown(client, tx)
    assert not (found["sources"]["book"] or found["sources"]["type"])

    facts = text(_facts(found, _details(client, tx)))

    assert "Labelled by" in facts


def test_the_entry_that_set_a_field_is_the_latest_and_an_undo_takes_its_place(client, conn, indigo):
    _, tx = indigo
    first = client.put(f"/api/transactions/{tx}", json={"book": "Moom"})
    second = client.put(f"/api/transactions/{tx}", json={"book": "Kalesh"})
    assert first.status_code == second.status_code == 200
    assert shown(client, tx)["set_by"]["book"]["entry_id"] == int(second.headers["X-Fin-Change"])
    assert shown(client, tx)["set_by"]["type"] is None

    undone = client.post(f"/api/history/{second.headers['X-Fin-Change']}/undo")
    assert undone.status_code == 200
    # The book is Moom again, chosen by the first edit.
    found = shown(client, tx)
    assert (found["book"], found["sources"]["book"]) == ("Moom", "chosen")
    assert found["set_by"]["book"]["entry_id"] in (int(first.headers["X-Fin-Change"]), undone.get_json()["by"])


def test_a_hand_set_label_from_before_the_sources_reads_its_entry(client, conn, statement):
    """An entry recorded before the label-source step holds no source; the
    row's hand-set mark in it is what set the field."""
    svc = merchant(conn, "IndigoAir", "Household", "Travel")
    tx = row(conn, statement, "INDIGOAIR 6E 0044", service_id=svc, book="Moom",
             sources=("chosen", "checked"), cat_source="manual")
    before = {"id": tx, "book": "Household", "cat_source": "service_default"}
    after = {"id": tx, "book": "Moom", "cat_source": "manual"}
    conn.execute("INSERT INTO change_entries (at, via, actor, summary, open)"
                 " VALUES ('2026-03-01 08:00:00', 'app', 'fin', 'Book set', 0)")
    old = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)"
                 " VALUES (?, 'transactions', ?, 'update', ?, ?)", (old, tx, json.dumps(before), json.dumps(after)))
    conn.commit()

    found = shown(client, tx)

    assert found["set_by"]["book"]["entry_id"] == old
    assert found["set_by"]["type"]["entry_id"] == old


def test_a_list_of_rows_carries_sources_but_not_who_set_them(client, conn, indigo):
    svc, tx = indigo
    listed = client.get(f"/api/transactions?service_id={svc}&expense_only=true").get_json()["transactions"]
    assert [(t["id"], t["sources"]) for t in listed] == [(tx, {"book": "merchant", "type": "merchant"})]
    assert "set_by" not in listed[0]


def test_a_field_with_no_source_of_its_own_reads_the_old_mark(client, conn, statement):
    """Expand: a writer not yet switched writes cat_source alone; its
    hand-set label is shown chosen, not from the merchant."""
    tx = row(conn, statement, "SAMPLE TRANSFER IN", sources=(None, None), cat_source="manual")
    assert shown(client, tx)["sources"] == {"book": "chosen", "type": "chosen"}


# --- the merchant page's tags match the row's sources, every combination ----------------------


SOURCES = ("merchant", "checked", "chosen")


@needs_node
def test_the_merchant_page_tags_match_the_rows_sources_for_every_combination(client, conn, statement):
    svc = merchant(conn, "IndigoAir", "Household", "Travel")
    combos = list(itertools.product(SOURCES, SOURCES))
    made = {row(conn, statement, f"INDIGOAIR 6E {n:04d}", service_id=svc, sources=pair,
                cat_source="manual" if set(pair) - {"merchant"} else "service_default",
                day=f"2026-09-{10 + n:02d}"): pair
            for n, pair in enumerate(combos)}

    listed = client.get(f"/api/transactions?service_id={svc}&expense_only=true&per_page=50").get_json()["transactions"]
    tags = _node("DATA.rows.map(x => [x.id, sourceTagsHTML(x.sources)])", rows=listed)

    assert len(tags) == len(combos)
    for tx_id, html in tags:
        book, kind = made[tx_id]
        want = [(f"book {book}", book) for _ in [0] if book != "merchant"]
        want += [(f"type {kind}", kind) for _ in [0] if kind != "merchant"]
        got = [(t, "chosen" if "is-chosen" in cls else "checked")
               for cls, t in re.findall(r'<span class="src-tag([^"]*)">([^<]+)</span>', html)]
        assert got == want, (book, kind, html)
        if not want:
            assert html == ""


def test_checked_tags_are_dashed_and_chosen_solid():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    dashed = re.search(r"^\.src-tag \{[^}]*\}", css, re.M)
    solid = re.search(r"^\.src-tag\.is-chosen \{[^}]*\}", css, re.M)
    assert dashed and "dashed" in dashed.group(0)
    assert solid and "solid" in solid.group(0)


# --- "hand-set" leaves the screens; the Queue keeps "not checked" ------------------------------


def test_no_ui_string_in_static_says_hand_set():
    said = [f"{p.name}:{n}" for p in STATIC.rglob("*") if p.suffix in (".js", ".html", ".css")
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"hand-?set|handset", line, re.I)]
    assert said == []


def test_the_rows_sub_line_says_what_you_set_stays_and_the_queue_keeps_not_checked():
    assert "What you set is chosen and stays; the rest follows ${esc(s.name)}." in APP_JS
    assert "tag('notchecked', 'not checked')" in APP_JS
