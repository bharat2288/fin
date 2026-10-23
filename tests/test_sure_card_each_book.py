"""Queue-naming 04: Check each row pre-fills the usual book from the latest row
(spec D, case 19).

A sure line set to Check each row whose type proposes no book (Software & AI
tools asks for one) used to block its save on the usual book, though each row
is checked with its own. Now, with no usual book chosen, the usual book is the
one the card's latest row carries (by date, then id), shown with the prefill
hint, and the save goes; a usual book the operator picks still wins.

The seam is the page: the real `static/app.js` runs under node in the vm of
tests/test_types_screen.py, against the real Flask app on a throwaway port over a
temporary book. The test sets the line to Check each row and presses its ✓ as
the operator would (ACT['sure-set'], ACT['sure-book'], ACT['sure-save']); what
the server stored is read back from the book. Jev's answer is stored directly;
nothing reaches the model. Every wording, merchant and figure is invented.
"""

import html as htmllib
import json
import re
import subprocess

import pytest

import suggest
from test_preview_merchant_change import add_row
from test_queue_guesses import NOT_A_KEY, answer, statement_on
from test_suggested_name import TIMERS
from test_types_screen import APP_JS_PATH, NODE, server  # noqa: F401 (server is a fixture)

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the card is drawn only in JavaScript")

WORDING = "SAMPLEAI PRO SUBSCRIPTION"
TYPE = "Software & AI tools"      # asks for a book: no usual book until one is picked


@pytest.fixture(autouse=True)
def jev_on(monkeypatch):
    """Guesses on (a made-up key), and the model replaced by a refusal."""
    monkeypatch.setenv(suggest.KEY_ENV, NOT_A_KEY)

    def refuse(body, key):
        raise AssertionError("the sure card asked the model")

    monkeypatch.setattr(suggest, "post", refuse)


@pytest.fixture
def rows(conn):
    """Five Sampleai rows with no merchant and no type, that Jev is sure are
    Software & AI tools. Four carry a book: the latest by date is a tie on
    2026-09-20, Kalesh then Moom (Moom has the higher id); a Household row
    dated earlier is added last (the highest id, not the latest). The row
    dated last carries no book."""
    card = statement_on(conn, "card", "0001")

    def put(day, book):
        return add_row(conn, card, WORDING, 1999, service_id=None, book=book, type_name=None, day=day,
                       sources=("chosen", None))

    made = [put("2026-09-01", "Household"), put("2026-09-20", "Kalesh"), put("2026-09-20", "Moom"),
            put("2026-09-05", "Household"), put("2026-09-25", None)]
    answer(conn, suggest.merchant_string(WORDING, "expense", "card"), TYPE, 0.99)
    return made


def run(base, body):
    done = subprocess.run([NODE, "-e", TIMERS, str(APP_JS_PATH), base, body],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# The Queue drawn and its one sure line opened; `steps` run, then the line's
# top row is handed back as drawn, with whether its ✓ is enabled.
LINE = r"""
await viewQueue();
await __settle();
const g = sureList[0];
sureShown.add(g.key);
const line = () => ({ top: sureRowHTML(g, queueModel.r.books, 0).split('<tr class="sl-det"')[0], ready: sureReady(g) });
const set = v => ACT['sure-set']({ dataset: { key: g.key, v } });
const usual = value => ACT['sure-book']({ dataset: { key: g.key }, value });
const save = async () => { await ACT['sure-save']({ dataset: { key: g.key } }); await __settle(); };
const same = line();
%s
"""


def book_picker(top):
    found = re.search(r'<select[^>]*data-change="sure-book"[^>]*>(.*?)</select>(.*?)</td>', top, re.S)
    assert found, "the line has no book picker"
    chosen = re.search(r'<option value="([^"]*)" selected', found.group(1))
    return chosen and chosen.group(1), found.group(2)


def hint_shown(after):
    """The prefill hint's words when it shows; '' when hidden or absent."""
    found = re.search(r'<span class="hint sl-hint"([^>]*)>(.*?)</span>', after, re.S)
    return "" if not found or "hidden" in found.group(1) else htmllib.unescape(found.group(2))


def merchant_held(conn, tx):
    """The merchant the row was saved to: its name, its usual book, and whether it checks each row."""
    return tuple(conn.execute(
        "SELECT s.name, s.book, s.review_each_time FROM transactions t JOIN services s ON s.id = t.service_id"
        " WHERE t.id = ?", (tx,)).fetchone() or ())


# --- case 19 ---------------------------------------------------------------------


def test_case_19_check_each_row_prefills_the_usual_book_from_the_latest_row(server, conn, rows):
    out = run(server, LINE % "set('each'); const each = line(); usual('Kalesh'); const picked = line(); await save();"
                             " return { same, each, picked, toasts: __toasts };")

    # Every row the same: no usual book, as before; the save waits for one.
    assert book_picker(out["same"]["top"])[0] is None
    assert out["same"]["ready"] is False

    # Check each row: the latest row that carries a book is Moom's (2026-09-20,
    # the higher id), shown as a prefill, and the save is enabled.
    chosen, after = book_picker(out["each"]["top"])
    assert chosen == "Moom"
    assert hint_shown(after) == "Moom on the latest row"
    assert out["each"]["ready"] is True
    assert not re.search(r'<button[^>]*data-act="sure-save"[^>]*disabled', out["each"]["top"])

    # The operator changes it: a chosen usual book wins, the hint goes, and the
    # save stores it as the merchant's usual book.
    chosen, after = book_picker(out["picked"]["top"])
    assert chosen == "Kalesh"
    assert hint_shown(after) == ""
    assert out["picked"]["ready"] is True
    assert not any(t["bad"] for t in out["toasts"]), out["toasts"]
    assert merchant_held(conn, rows[0]) == ("Sampleai Pro Subscription", "Kalesh", 1)


def test_case_19_the_latest_rows_own_book_wins_over_its_pickers_prefill(server, conn, rows):
    """The card is a company's (Kalesh), so every row's picker would start on
    Kalesh; the usual book is still the latest row's own, Moom (review F3)."""
    account = conn.execute("SELECT s.account_id FROM transactions t JOIN statements s ON s.id = t.statement_id"
                           " WHERE t.id = ?", (rows[0],)).fetchone()[0]
    conn.execute("UPDATE accounts SET owner = 'Kalesh' WHERE id = ?", (account,))
    conn.commit()

    out = run(server, LINE % "set('each'); const each = line();"
                             " return { each, prefill: queueModel.prefills.get(%d) };" % rows[2])

    assert out["prefill"]["book"] == "Kalesh"     # the row's own picker starts on the company's book
    chosen, after = book_picker(out["each"]["top"])
    assert chosen == "Moom"
    assert hint_shown(after) == "Moom on the latest row"
