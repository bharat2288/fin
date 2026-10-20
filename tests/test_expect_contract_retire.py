"""The contract (personal-ledger 23, ticket 47; spec, The conversion step 3).

"a loan to a person" leaves the review choices, its guess tally and its chat
mirror; the review card offers Expect some back… (money out) and Expect to
pay back (money in) in its place. A write naming a person as a movement's
other side is refused in named words and writes nothing: the review label
(the retired choice, or a person's account under another choice) and the
flow edit (the resolve route's flow_type). The sheet no longer reads person
anchors, which are kept: only the conversion reads them, to prove its
records against each person's old line. The down step is retired; the way
back is the backup the step takes before it runs. Converted rows keep their
flow and other side as history.

Seam 1 (the Flask test client on a temporary database, with the check
book's October story), seam 3 (the conversion step on a book under
tmp_path), seam 4 (mcp_tools.call) and seam 5 (drawChoices lifted out of
static/app.js and run under node, the script on stdin). Every name and
figure is invented.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import balance_sheet
import convert_expect_back
import mcp_tools
import review
from expect_fixtures import mark, october, row, set_off
from test_convert_expect_back import backups, book, dump, open_book, run, seeded  # noqa: F401  (book is a fixture)
from trend_fixtures import anchor, type_id

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

WORDS = ("A person is no longer a movement's other side: mark the row Expect some back… "
         "(money out) or Expect to pay back (money in).")
SEVEN = ("expectation", "expectation_name", "set_off", "let_go", "refund_link", "sender_wording", "dismissal")


def snapshot(conn) -> dict:
    """Every record, row, account, merchant and rule, and the history."""
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")]
            for t in SEVEN + ("transactions", "accounts", "services", "merchant_rules",
                              "anchors", "change_entries", "change_rows")}


def refused(conn, send, words: str = WORDS):
    """`send()` is refused with exactly `words` and nothing is written."""
    before = snapshot(conn)
    resp = send()
    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json()["error"] == words
    assert "X-Fin-Change" not in resp.headers
    assert snapshot(conn) == before


@pytest.fixture
def story(conn):
    return october(conn)


@pytest.fixture
def converted(conn, story):
    """Case 40 as the conversion left it: Ravi's S$ 2,000.00 out on 3 Jun, a
    movement naming him set by hand, carrying a converted back expectation;
    his S$ 500.00 back on 15 Aug set off against it."""
    b, ravi = story.book, story.people["Ravi"]
    out = row(conn, b, b.savings, "2026-06-03", "TRANSFER TO RAVI", 200000, flow="movement", other_side=ravi)
    back = row(conn, b, b.savings, "2026-08-15", "PAYNOW FROM RAVI", -50000, flow="movement", other_side=ravi)
    conn.execute("UPDATE transactions SET flow_type_manual = 1, book_source = 'chosen', type_source = 'chosen' WHERE id IN (?, ?)",
                 (out, back))
    conn.commit()
    loan = mark(conn, out, 200000, names=(ravi,), source="converted")
    set_off(conn, back, loan, 50000, source="converted")
    return {"out": out, "back": back, "loan": loan}


def label(client, tx_id: int, **body):
    return client.post(f"/api/review/{tx_id}/label", json=body)


def flow_edit(client, conn, tx_id: int, flow_type: str):
    """The flow edit: the resolve route with a flow_type, for this row only."""
    return client.post("/api/transactions/resolve", json={
        "tx_id": tx_id, "service_name": "Sample Payee", "type_id": type_id(conn, "Dining"),
        "apply_scope": "transaction", "flow_type": flow_type,
    })


# --- 44 (contract half): the choice retires --------------------------------------


def test_the_review_choices_no_longer_offer_a_loan_to_a_person(client):
    choices = client.get("/api/review").get_json()["choices"]
    assert [c["name"] for c in choices] == [
        "spending", "gift", "own_account", "company", "loan_repayment", "income"]
    assert not [c for c in choices if c["asks"] == "person" or "person" in c["kinds"]]


def test_a_loan_to_person_label_is_refused_in_named_words_and_writes_nothing(client, conn, story):
    refused(conn, lambda: label(client, story.jay_0412, choice="loan_to_person", account_id=story.people["Jay"]))
    # A new name is not made into a person either.
    refused(conn, lambda: label(client, story.jay_0412, choice="loan_to_person", person="Mei"))
    assert conn.execute("SELECT COUNT(*) FROM accounts WHERE UPPER(name) = 'MEI'").fetchone()[0] == 0


@pytest.mark.parametrize("choice", ["own_account", "company", "loan_repayment"])
def test_a_label_naming_a_person_under_another_choice_is_refused_in_the_same_words(client, conn, story, choice):
    refused(conn, lambda: label(client, story.jay_0412, choice=choice, account_id=story.people["Jay"]))


def test_labelling_a_row_some_other_way_still_writes_one_undoable_change(client, conn, story):
    resp = label(client, story.jay_0412, choice="company", account_id=story.book.company)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["other_side_id"] == story.book.company
    assert client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo").status_code == 200
    assert tuple(conn.execute("SELECT flow_type, other_side_id FROM transactions WHERE id = ?",
                              (story.jay_0412,)).fetchone()) == ("review", None)


# --- the flow edit -----------------------------------------------------------------


def test_a_flow_edit_making_a_person_row_a_movement_is_refused_and_writes_nothing(client, conn, story, converted):
    # The converted loan keeps its flow and other side as history; a flow edit
    # that would (again) make it a movement naming Ravi is refused whole.
    refused(conn, lambda: flow_edit(client, conn, converted["out"], "movement"))
    refused(conn, lambda: flow_edit(client, conn, converted["back"], "movement"))
    # A row that names Ravi but is no movement now: making it one is refused too.
    conn.execute("UPDATE transactions SET flow_type = 'expense' WHERE id = ?", (converted["out"],))
    conn.commit()
    refused(conn, lambda: flow_edit(client, conn, converted["out"], "movement"))


def test_a_flow_edit_naming_no_person_still_writes(client, conn, story):
    resp = flow_edit(client, conn, story.harbour, "movement")
    assert resp.status_code == 200, resp.get_json()
    assert conn.execute("SELECT flow_type FROM transactions WHERE id = ?", (story.harbour,)).fetchone()[0] == "movement"


def test_converted_records_and_their_history_are_untouched(client, conn, story, converted):
    before = snapshot(conn)
    label(client, converted["out"], choice="loan_to_person", account_id=story.people["Ravi"])
    flow_edit(client, conn, converted["out"], "movement")
    client.post(f"/api/review/{converted['back']}/label", json={"choice": "company", "account_id": story.people["Ravi"]})
    assert snapshot(conn) == before
    assert tuple(conn.execute("SELECT flow_type, other_side_id FROM transactions WHERE id = ?",
                              (converted["out"],)).fetchone()) == ("movement", story.people["Ravi"])


# --- the guess tally ---------------------------------------------------------------


def _labelled_alike(conn, story, description: str, other_side: int, n: int = 3) -> int:
    """`n` rows money in with this text, labelled by hand as movements naming
    `other_side`, and one more waiting. Returns the waiting row."""
    b = story.book
    for day in range(1, n + 1):
        made = row(conn, b, b.savings, f"2026-07-{day:02d}", description, -10000, flow="movement",
                   other_side=other_side)
        conn.execute("UPDATE transactions SET flow_type_manual = 1, book_source = 'chosen', type_source = 'chosen' WHERE id = ?", (made,))
    conn.commit()
    return row(conn, b, b.savings, "2026-07-20", description, -10000, flow="review")


def test_past_loans_to_a_person_are_never_guessed_again(client, conn, story):
    waiting = _labelled_alike(conn, story, "PAYNOW FROM RAVI", story.people["Ravi"])
    assert client.get(f"/api/review/{waiting}/suggestion").get_json() == {"guess": None}


def test_the_guess_tally_still_guesses_a_choice_that_stands(client, conn, story):
    waiting = _labelled_alike(conn, story, "FROM MOOM", story.book.company)
    guess = client.get(f"/api/review/{waiting}/suggestion").get_json()["guess"]
    assert (guess["choice"], guess["body"], guess["agree"], guess["of"]) == (
        "company", {"choice": "company", "account_id": story.book.company}, 3, 3)


# --- the chat mirror ---------------------------------------------------------------


def test_chat_cannot_label_a_loan_to_a_person(client, conn, story):
    tool = mcp_tools.BY_NAME["label_review"]
    assert "loan_to_person" not in tool.description
    assert "mark_expect_back" in tool.description
    assert "person" not in tool.input_schema["properties"]
    before = snapshot(conn)
    answer = mcp_tools.call("label_review", {"tx_id": story.jay_0412, "choice": "loan_to_person",
                                             "account_id": story.people["Jay"]}, actor="chat:sample-subject")
    assert answer == {"ok": False, "error": WORDS}
    assert snapshot(conn) == before


# --- the sheet reads no person anchor; anchors are kept --------------------------------


def test_the_sheet_reads_no_person_anchor_and_the_anchor_is_kept(conn, story):
    sam = story.people["Sam"]
    anchor(conn, sam, "2026-01-01", 80000, source="supplied")
    shown = balance_sheet.sheet(conn, "2026-10")
    assert "Sam" not in [line["name"] for s in shown["sections"] for line in s["lines"]]
    # Sam's S$ 800.00 figure is in no total: taking it away moves nothing.
    with_figure = shown["net_worth_minor"]
    conn.execute("DELETE FROM anchors WHERE account_id = ?", (sam,))
    conn.commit()
    assert balance_sheet.sheet(conn, "2026-10")["net_worth_minor"] == with_figure
    # A person's line is no line the sheet works out.
    person = conn.execute("SELECT id, name, type, currency, status FROM accounts WHERE id = ?", (sam,)).fetchone()
    with pytest.raises(ValueError):
        balance_sheet._counterparty_line(conn, person, balance_sheet.month_end("2026-10"), "2026-10", str)


def test_the_conversion_still_reads_person_anchors_and_keeps_them(book, capsys):
    ids = seeded(book)
    code, out = run(book, capsys)
    assert code == 0, out
    conn = open_book(book)
    try:
        # Sam's figure is kept, and the old lines the step proved its records
        # against are the conversion's own read: S$ 800.00 for Sam; S$ 1,500.00
        # for Ravi (S$ 2,000.00 lent, S$ 500.00 back); S$ -4,000.00 for Parents.
        assert [tuple(r) for r in conn.execute("SELECT date, amount FROM anchors WHERE account_id = ?",
                                                (ids["Sam"],))] == [("2026-01-01", 80000)]
        persons = {r["id"]: r for r in conn.execute(
            "SELECT id, name, type, currency, status FROM accounts WHERE type = 'person'")}
        assert convert_expect_back.old_line(conn, persons[ids["Sam"]], "2026-10-31") == 80000
        assert convert_expect_back.old_line(conn, persons[ids["Ravi"]], "2026-10-31") == 150000
        assert convert_expect_back.old_line(conn, persons[ids["Parents"]], "2026-10-31") == -400000
        assert convert_expect_back.old_line(conn, persons[ids["Ravi"]], "2026-07-31") == 200000
        lines = convert_expect_back.person_lines(conn)
        assert all(old == new for old, new in lines.values())
    finally:
        conn.close()


# --- 45a (contract half): the down step is retired ---------------------------------------


def test_the_down_step_is_no_longer_offered(book, capsys):
    seeded(book)
    assert run(book, capsys)[0] == 0
    before = dump(book)

    code, out = run(book, capsys, "--down")

    assert code == 2
    assert "the down step is retired" in out
    assert dump(book) == before
    assert backups(book, "expect-back-down") == []


# --- the review card: Expect some back… / Expect to pay back (seam 5) -------------------


def _function(name: str) -> str:
    m = re.search(rf"^function {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _const(name: str) -> str:
    m = re.search(rf"^const {name} = .*?;$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _choices(amount_minor: int, *, more: bool) -> str:
    """drawChoices for one waiting row of this amount, with the real choices."""
    choices = [{"name": c.name, "label": c.label, "flow": c.flow, "asks": c.asks, "kinds": list(c.kinds),
                "description": c.description} for c in review.CHOICES]
    step = {"r": {"review": {"choices": choices}}, "choice": None, "more": more, "guessed": False,
            "row": {"id": 7, "amount_minor": amount_minor}, "group": [{"id": 7}]}
    code = "\n".join([
        "const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/\"/g,'&quot;');",
        "const els = {}; const $ = sel => (els[sel] = els[sel] || { innerHTML: '', hidden: false });",
        "function sentence(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }",
        _const("CHOICE_ORDER"), _const("COUNT_WORDS"),
        f"let step = {json.dumps(step)};",
        _function("drawChoices"),
        "drawChoices();",
        "console.log(JSON.stringify(els['#ww-choices'].innerHTML));",
    ])
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, check=True,
                         encoding="utf-8")
    return json.loads(out.stdout)


@needs_node
def test_the_review_card_offers_expect_some_back_on_money_out():
    html = _choices(30000, more=True)
    assert "loan to a person" not in html
    tile = re.search(r'<button[^>]*data-act="expect-mark"[^>]*>.*?</button>', html, re.S)
    assert tile, html
    assert 'data-tx="7"' in tile.group(0)
    assert "Expect some back…" in tile.group(0)
    assert "Expect to pay back" not in html


@needs_node
def test_the_review_card_offers_expect_to_pay_back_on_money_in():
    html = _choices(-30000, more=True)
    tile = re.search(r'<button[^>]*data-act="expect-mark"[^>]*>.*?</button>', html, re.S)
    assert tile and "Expect to pay back" in tile.group(0), html
    assert "Expect some back" not in html


@needs_node
def test_folded_the_review_card_names_it_in_its_place():
    html = _choices(30000, more=False)
    # Folded under "…more", in the place "a loan to a person" had.
    assert "A gift, expect some back…, a loan repayment…" in html
    assert "four more" in html
    assert 'data-act="expect-mark"' not in html
