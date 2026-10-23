"""Queue-naming 02: the sure card's Name field, and attaching to a merchant fin has
(spec B, cases 9-12).

The opened Jev-is-sure line carries the merchant's name in a field, filled with
fin's suggestion and editable, with a chooser of the merchants fin holds. A name
that is one of them, ignoring case, says "Saves to <name>, already in fin: <type> ·
<book>", the line's type and book show that merchant's, and the save uses them; an
empty name blocks the save with "Give it a name".

The seam is the page: the real `static/app.js` runs under node in the vm of
tests/test_types_screen.py, against the real Flask app on a throwaway port over a
temporary book. The test types into the Name field as the operator would (the
field's own handler, ACT['sure-name']) and presses the line's ✓ (ACT['sure-save']);
what the server stored afterwards is read back from the book. Jev's answer is
stored directly, as a send stores it; nothing reaches the model. Every wording,
merchant and figure is invented.
"""

import html as htmllib
import json
import re
import subprocess

import pytest

import suggest
from test_book_and_type_switch import merchant, type_id
from test_preview_merchant_change import add_row
from test_queue_guesses import NOT_A_KEY, answer, statement_on
from test_suggested_name import TIMERS
from test_types_screen import APP_JS_PATH, NODE, server  # noqa: F401 (server is a fixture)

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the card is drawn only in JavaScript")

WORDING = "SP RHODE SKIN RHODESKIN.COM CA"


@pytest.fixture(autouse=True)
def jev_on(monkeypatch):
    """Guesses on (a made-up key), and the model replaced by a refusal: the
    card reads Jev's stored answer and never asks."""
    monkeypatch.setenv(suggest.KEY_ENV, NOT_A_KEY)

    def refuse(body, key):
        raise AssertionError("the sure card asked the model")

    monkeypatch.setattr(suggest, "post", refuse)


@pytest.fixture
def waiting(conn):
    """One Rhode Skin row on a card, no merchant and no type, that Jev is sure
    is Health & Beauty: the sure card's one line."""
    card = statement_on(conn, "card", "0001")
    tx = add_row(conn, card, WORDING, 53234, service_id=None, book=None, type_name=None, day="2026-10-02")
    answer(conn, suggest.merchant_string(WORDING, "expense", "card"), "Health & Beauty", 0.99)
    return tx


def run(base, body):
    done = subprocess.run([NODE, "-e", TIMERS, str(APP_JS_PATH), base, body],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# The Queue drawn, its one sure line opened; then `steps` run, and the line's
# top row and detail are handed back as drawn, with whether its ✓ is enabled.
OPEN = r"""
await viewQueue();
await __settle();
const g = sureList[0];
const line = () => {
    const html = sureRowHTML(g, queueModel.r.books, 0);
    return { top: html.split('<tr class="sl-det"')[0], detail: sureDetailHTML(g, 0), ready: sureReady(g) };
};
sureShown.add(g.key);
const name = value => ACT['sure-name']({ dataset: { sureName: g.key }, value });
const save = async () => { await ACT['sure-save']({ dataset: { key: g.key } }); await __settle(); };
const opened = line();
%s
return { opened, after: line(), toasts: __toasts };
"""


def words(fragment):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def name_field(detail):
    found = re.search(r'<input[^>]*data-sure-name="[^"]*"[^>]*>', detail)
    assert found, "the opened line has no Name field"
    return found.group(0)


def stored(conn, tx):
    """What the book holds for the row: its merchant's name, its book and its type id."""
    return tuple(conn.execute(
        "SELECT s.name, t.book, t.type_id FROM transactions t LEFT JOIN services s ON s.id = t.service_id"
        " WHERE t.id = ?", (tx,)).fetchone())


# --- case 9 ----------------------------------------------------------------------


def test_case_9_the_field_holds_the_suggestion_and_the_save_stores_the_edited_name(server, conn, waiting):
    out = run(server, OPEN % "name('Rhode'); await save();")

    field = name_field(out["opened"]["detail"])
    assert 'value="Rhode Skin"' in field
    assert words(out["opened"]["top"]).startswith("Rhode Skin")
    assert stored(conn, waiting) == ("Rhode", "Household", type_id(conn, "Health & Beauty"))
    assert conn.execute("SELECT COUNT(*) FROM services WHERE name = 'Rhode Skin'").fetchone()[0] == 0


def services_held(conn):
    return conn.execute("SELECT id, name, book, type_id FROM services ORDER BY id").fetchall()


def book_picker(top):
    found = re.search(r'<select[^>]*data-change="sure-book"[^>]*>(.*?)</select>', top, re.S)
    assert found, "the line has no book picker"
    chosen = re.search(r'<option value="([^"]*)" selected', found.group(1))
    return found.group(0).split(">", 1)[0], chosen and chosen.group(1)


# --- case 10 ---------------------------------------------------------------------


def test_case_10_a_name_matching_a_merchant_ignoring_case_attaches_and_uses_its_type_and_book(
        server, conn, waiting):
    rhode = merchant(conn, "Rhode", "Moom", "Shopping")
    before = [tuple(r) for r in services_held(conn)]

    out = run(server, OPEN % "name('rhode'); const typed = line(); await save(); return { typed, toasts: __toasts };")

    typed = out["typed"]
    assert "Saves to Rhode, already in fin: Shopping · Moom" in words(typed["detail"])
    # The line's type and book show the merchant's, the book held to it.
    assert words(re.search(r'<td class="sl-ty">(.*?)</td>', typed["top"], re.S).group(1)) == "Shopping in fin"
    picker, chosen = book_picker(typed["top"])
    assert chosen == "Moom" and " disabled" in picker
    # The line names the merchant as fin holds it, not as typed.
    assert words(typed["top"]).startswith("Rhode ")
    assert "Every new row from Rhode will be Shopping · Moom" in words(typed["detail"])
    # The save attaches to Rhode with its own type and book: no new merchant,
    # Rhode's own default unchanged, and no question asked first.
    assert not any(t["bad"] for t in out["toasts"]), out["toasts"]
    # The toast names the merchant as fin holds it too (review F4).
    said = " ".join(t["words"] for t in out["toasts"])
    assert "Saved Rhode as " in said and "Saved rhode" not in said
    assert stored(conn, waiting) == ("Rhode", "Moom", type_id(conn, "Shopping"))
    assert [tuple(r) for r in services_held(conn)] == before
    assert conn.execute("SELECT service_id FROM transactions WHERE id = ?", (waiting,)).fetchone()[0] == rhode


# --- case 11 ---------------------------------------------------------------------


def test_case_11_the_chooser_offers_the_merchants_fin_holds_and_choosing_one_attaches(server, conn, waiting):
    held = merchant(conn, "Rhode Beauty Ltd", "Kalesh", "Health & Beauty")
    names = {r["name"] for r in services_held(conn)}

    out = run(server, OPEN % r"""
        const card = sureCardHTML(queueModel.r.books);
        const list = /<datalist id="([^"]+)">(.*?)<\/datalist>/s.exec(card);
        const offered = [...list[2].matchAll(/<option value="([^"]*)">/g)].map(m => m[1]);
        name(offered.find(o => o.startsWith('Rhode')) || '');
        const chosen = line();
        await save();
        return { list: list[1], offered, field: opened.detail, chosen, toasts: __toasts };
    """)

    # The Name field is tied to the one chooser, which lists every merchant fin holds.
    assert f'list="{out["list"]}"' in name_field(out["field"])
    assert set(htmllib.unescape(o) for o in out["offered"]) == names
    assert "Saves to Rhode Beauty Ltd, already in fin: Health & Beauty · Kalesh" in words(out["chosen"]["detail"])
    assert not any(t["bad"] for t in out["toasts"]), out["toasts"]
    assert stored(conn, waiting) == ("Rhode Beauty Ltd", "Kalesh", type_id(conn, "Health & Beauty"))
    assert conn.execute("SELECT service_id FROM transactions WHERE id = ?", (waiting,)).fetchone()[0] == held


# --- case 12 ---------------------------------------------------------------------


def test_case_12_an_empty_name_blocks_the_save_with_the_words(server, conn, waiting):
    before = [tuple(r) for r in services_held(conn)]

    out = run(server, OPEN % "name('   '); const emptied = line(); await save(); return { emptied, toasts: __toasts };")

    emptied = out["emptied"]
    assert emptied["ready"] is False
    assert "Give it a name" in words(re.search(r'data-sure-said="[^"]*"[^>]*>(.*?)</p>', emptied["detail"], re.S).group(1))
    assert re.search(r'<button[^>]*data-act="sure-save"[^>]*disabled', emptied["top"])
    # Pressed anyway, nothing is written.
    assert stored(conn, waiting) == (None, None, None)
    assert [tuple(r) for r in services_held(conn)] == before
