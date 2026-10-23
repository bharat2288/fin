"""Queue-naming 03: several wordings in one save (spec C, cases 14-18).

A teach (POST /api/transactions/resolve) takes, beside its one `pattern`, a
list of other wordings (`wordings`, each recognised as `contains`). Saving
makes one rule per wording for the merchant, all in the one change entry, and
Undo of that entry takes every rule away and the merchant if the save made it.
A wording that would be refused (empty, the same as another on the request,
or refused as a pattern is) refuses the whole request: nothing is written and
no entry is made. A wording that reaches another merchant's rows goes through
the preview as the pattern does, one question for all of them.

On the card, the opened sure line's recognises line gets "+ another wording":
each added wording has its own field, its own live count and overlap warning
(the reach read), and a remove link; an empty wording, or one the card already
has, is refused before saving.

The route is crossed with the Flask test client; the card is the real
`static/app.js` under node against the real Flask app on a throwaway port over
the temporary book (tests/test_sure_card_name.py's harness), and what the
server stored is read back from the book. Every wording, merchant and figure
is invented.
"""

import html as htmllib
import json
import re
import subprocess

import pytest

import suggest
from test_book_and_type_switch import merchant, rule, statement, type_id  # noqa: F401 (statement is a fixture)
from test_preview_merchant_change import add_row, entries, every_row
from test_queue_guesses import NOT_A_KEY, answer, statement_on
from test_suggested_name import TIMERS
from test_types_screen import APP_JS_PATH, NODE, server  # noqa: F401 (server is a fixture)

TAUGHT = "SP RHODE SKIN RHODESKIN.COM CA"


@pytest.fixture
def book(conn, statement):  # noqa: F811
    """The Rhode Skin row being taught; one more row with no merchant that only
    the second wording matches; and a row Sample Gifts holds whose wording
    holds RHODESKIN, which no rule of Sample Gifts' matches."""
    tx = add_row(conn, statement, TAUGHT, 53234, service_id=None, book=None, type_name=None, day="2026-10-02")
    loose = add_row(conn, statement, "RHODESKIN.COM SG ORDER", 4100, service_id=None, book=None, type_name=None,
                    day="2026-09-12")
    gifts = merchant(conn, "Sample Gifts", "Moom", "Shopping")
    rule(conn, "SAMPLEGIFTS", gifts)
    held = add_row(conn, statement, "RHODESKIN GIFT CARD", 2500, service_id=gifts, book="Moom",
                   type_name="Shopping", day="2026-08-01")
    return {"tx": tx, "loose": loose, "gifts": gifts, "held": held}


def teach(conn, tx, pattern="RHODE SKIN", **more):
    return {"tx_id": tx, "service_name": "Rhode", "type_id": type_id(conn, "Health & Beauty"), "book": "Household",
            "apply_scope": "service_default", "pattern": pattern, "match_type": "contains", **more}


def rules_of(conn, name):
    return sorted(tuple(r) for r in conn.execute(
        "SELECT r.pattern, r.match_type FROM merchant_rules r JOIN services s ON s.id = r.service_id"
        " WHERE s.name = ?", (name,)))


def counts(conn):
    return tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("services", "merchant_rules"))


# --- case 17: two wordings, one change entry, one Undo -----------------------------------------


def test_case_17_two_wordings_save_two_rules_in_one_change_entry(client, conn, book):
    history_before = entries(client)

    resp = client.post("/api/transactions/resolve", json=teach(conn, book["tx"], wordings=["RHODESKIN.COM SG"]))

    assert resp.status_code == 200, resp.get_json()
    assert rules_of(conn, "Rhode") == [("RHODE SKIN", "contains"), ("RHODESKIN.COM SG", "contains")]
    # The row the second wording matches is labelled in the same save.
    beauty = type_id(conn, "Health & Beauty")
    got = conn.execute("SELECT s.name, t.book, t.type_id FROM transactions t JOIN services s ON s.id = t.service_id"
                       " WHERE t.id IN (?, ?) ORDER BY t.id", (book["tx"], book["loose"])).fetchall()
    assert [tuple(r) for r in got] == [("Rhode", "Household", beauty)] * 2
    assert book["loose"] in resp.get_json()["backfilled_ids"]
    assert len(resp.get_json()["rule_ids"]) == 2
    new = entries(client)
    assert len(new) == len(history_before) + 1
    assert str(new[0]["id"]) == resp.headers["X-Fin-Change"]


def test_case_17_undo_removes_both_rules_and_the_merchant_the_save_made(client, conn, book):
    before, held = every_row(conn), counts(conn)
    resp = client.post("/api/transactions/resolve", json=teach(conn, book["tx"], wordings=["RHODESKIN.COM SG"]))
    assert resp.status_code == 200, resp.get_json()
    assert len(rules_of(conn, "Rhode")) == 2

    undone = client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")

    assert undone.status_code == 200, undone.get_json()
    assert counts(conn) == held
    assert rules_of(conn, "Rhode") == []
    assert every_row(conn) == before


def test_case_17_undo_keeps_a_merchant_fin_already_held(client, conn, book):
    rhode = merchant(conn, "Rhode", "Household", "Health & Beauty")
    held = counts(conn)
    resp = client.post("/api/transactions/resolve",
                       json=teach(conn, book["tx"], service_id=rhode, wordings=["RHODESKIN.COM SG"]))
    assert resp.status_code == 200, resp.get_json()
    assert len(rules_of(conn, "Rhode")) == 2

    client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")

    assert counts(conn) == held
    assert conn.execute("SELECT name FROM services WHERE id = ?", (rhode,)).fetchone()[0] == "Rhode"


# --- case 18: a refused wording writes nothing --------------------------------------------------


@pytest.mark.parametrize("wordings, said", [
    (["PAYNOW"], "too generic"),                       # refused as a pattern is
    (["  "], "wording"),                               # empty
    (["rhode skin"], "twice"),                         # the pattern again, ignoring case
    (["RHODESKIN", "Rhodeskin"], "twice"),             # the same wording twice
    ("RHODESKIN", "list"),                             # not a list
    ([42], "wording"),                                 # not words
    ([f"RHODEWORD{i}" for i in range(21)], "at most"),  # more than a save takes (review F5)
])
def test_case_18_one_refused_wording_writes_nothing_and_makes_no_entry(client, conn, book, wordings, said):
    before, held, history_before = every_row(conn), counts(conn), entries(client)

    resp = client.post("/api/transactions/resolve", json=teach(conn, book["tx"], wordings=wordings))

    assert resp.status_code == 400, resp.get_json()
    assert said in resp.get_json()["error"].lower()
    assert every_row(conn) == before and counts(conn) == held and entries(client) == history_before


def test_case_18_other_wordings_go_with_a_rule_not_with_this_row_only(client, conn, book):
    before, held = every_row(conn), counts(conn)

    resp = client.post("/api/transactions/resolve",
                       json=teach(conn, book["tx"], apply_scope="transaction", wordings=["RHODESKIN"]))

    assert resp.status_code == 400, resp.get_json()
    assert every_row(conn) == before and counts(conn) == held


# --- case 15: a wording that reaches another merchant's rows ------------------------------------


def test_case_15_a_second_wording_taking_another_merchants_row_is_asked_first(client, conn, book):
    before, history_before = every_row(conn), entries(client)

    resp = client.post("/api/transactions/resolve", json=teach(conn, book["tx"], wordings=["RHODESKIN"]))

    assert resp.status_code == 409, resp.get_json()
    p = resp.get_json()["preview"]
    assert p["others"] == [{"service_id": book["gifts"], "name": "Sample Gifts", "count": 1}]
    assert p["count"] == 1 and p["token"]
    assert every_row(conn) == before and entries(client) == history_before

    resp = client.post("/api/transactions/resolve", json=teach(
        conn, book["tx"], wordings=["RHODESKIN"], past_rows="apply", token=p["token"]))

    assert resp.status_code == 200, resp.get_json()
    svc = resp.get_json()["service_id"]
    assert conn.execute("SELECT service_id FROM transactions WHERE id = ?", (book["held"],)).fetchone()[0] == svc
    assert rules_of(conn, "Rhode") == [("RHODE SKIN", "contains"), ("RHODESKIN", "contains")]


def test_case_15_a_row_two_wordings_both_take_is_counted_once(client, conn, book):
    resp = client.post("/api/transactions/resolve",
                       json=teach(conn, book["tx"], wordings=["RHODESKIN", "RHODESKIN GIFT"]))

    assert resp.status_code == 409, resp.get_json()
    p = resp.get_json()["preview"]
    assert p["count"] == 1
    assert p["others"] == [{"service_id": book["gifts"], "name": "Sample Gifts", "count": 1}]


# --- the card: "+ another wording" (cases 14-17) ------------------------------------------------


@pytest.fixture
def jev_on(monkeypatch):
    """Guesses on (a made-up key), and the model replaced by a refusal: the
    card reads Jev's stored answer and never asks."""
    monkeypatch.setenv(suggest.KEY_ENV, NOT_A_KEY)

    def refuse(body, key):
        raise AssertionError("the sure card asked the model")

    monkeypatch.setattr(suggest, "post", refuse)


@pytest.fixture
def card(conn, jev_on):
    """One Rhode Skin row on a card, no merchant and no type, that Jev is sure
    is Health & Beauty (the sure card's one line); a row with no merchant that
    only RHODESKIN.COM SG matches; and a Sample Gifts row whose wording holds
    RHODESKIN."""
    on = statement_on(conn, "card", "0001")
    tx = add_row(conn, on, TAUGHT, 53234, service_id=None, book=None, type_name=None, day="2026-10-02")
    answer(conn, suggest.merchant_string(TAUGHT, "expense", "card"), "Health & Beauty", 0.99)
    loose = add_row(conn, on, "RHODESKIN.COM SG ORDER", 4100, service_id=None, book="Household",
                    type_name="Health & Beauty", day="2026-09-12")
    gifts = merchant(conn, "Sample Gifts", "Moom", "Shopping")
    rule(conn, "SAMPLEGIFTS", gifts)
    add_row(conn, on, "RHODESKIN GIFT CARD", 2500, service_id=gifts, book="Moom", type_name="Shopping",
            day="2026-08-01")
    return {"tx": tx, "loose": loose}


def run(base, body):
    done = subprocess.run([NODE, "-e", TIMERS, str(APP_JS_PATH), base, body],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# The Queue drawn and its one sure line opened, its count read; `more()` presses
# "+ another wording" and types into the field it adds, `remove(i)` presses an
# added wording's remove; `line()` is the line as drawn now.
OPEN = r"""
await viewQueue();
await __settle();
const g = sureList[0];
sureShown.add(g.key);
sureDetailHTML(g, 0);
await __settle();
const line = () => {
    const html = sureRowHTML(g, queueModel.r.books, 0);
    return { top: html.split('<tr class="sl-det"')[0], detail: sureDetailHTML(g, 0), ready: sureReady(g) };
};
const added = () => [...sureDetailHTML(g, 0).matchAll(/data-rec-more="([^"]+)"/g)].map(m => m[1]);
const more = async value => {
    ACT['rec-more']({ dataset: { key: g.key } });
    const key = added().at(-1);
    ACT['rec-more-word']({ dataset: { recMore: key }, value });
    await __settle();
};
const type = async (i, value) => { ACT['rec-more-word']({ dataset: { recMore: added()[i] }, value }); await __settle(); };
const remove = i => ACT['rec-more-remove']({ dataset: { key: added()[i] } });
const save = async () => { await ACT['sure-save']({ dataset: { key: g.key } }); await __settle(); };
const opened = line();
%s
"""

needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the card is drawn only in JavaScript")


def words(fragment):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def added_lines(detail):
    """Each added wording as drawn: its field's value and the words after it."""
    return [(htmllib.unescape(m.group(1)), words(m.group(2)))
            for m in re.finditer(r'<input[^>]*data-rec-more="[^"]*"[^>]*value="([^"]*)"[^>]*>(.*?)</p>', detail, re.S)]


@needs_node
def test_case_14_another_wording_has_its_own_field_and_live_count(server, conn, card):
    out = run(server, OPEN % "await more('RHODESKIN.COM SG'); return { opened, after: line() };")

    assert 'data-act="rec-more"' in out["opened"]["detail"]
    assert "+ another wording" in words(out["opened"]["detail"])
    assert words(out["opened"]["detail"]).count("Recognises rows containing SP RHODE SKIN RHODESKIN.COM CA · 1 row in the book") == 1
    assert added_lines(out["after"]["detail"]) == [("RHODESKIN.COM SG", "remove matches 1 row in the book")]
    assert out["after"]["ready"] is True


@needs_node
def test_case_15_an_overlapping_wording_warns_on_its_own_line(server, conn, card):
    out = run(server, OPEN % "await more('RHODESKIN'); return { after: line() };")

    [(value, said)] = added_lines(out["after"]["detail"])
    assert value == "RHODESKIN"
    assert said == "remove matches 3 rows in the book · also 1 row now Sample Gifts’s"
    # The first wording's line says nothing of Sample Gifts: the warning is the added wording's.
    first = re.search(r'<div class="rec"[^>]*>(.*?)</div>', out["after"]["detail"], re.S).group(1)
    assert "Sample Gifts" not in words(first)


@needs_node
@pytest.mark.parametrize("typed, said", [
    ("", "Say the wording the rule recognises"),
    ("sp rhode skin rhodeskin.com ca", "Already a wording here"),
])
def test_case_16_an_empty_or_repeated_wording_is_refused_before_saving(server, conn, card, typed, said):
    before = every_row(conn)

    out = run(server, OPEN % f"await more({json.dumps(typed)}); const refused = line(); await save();"
                             " return { refused, toasts: __toasts };")

    [(_, line_said)] = added_lines(out["refused"]["detail"])
    assert said in line_said
    assert out["refused"]["ready"] is False
    assert re.search(r'<button[^>]*data-act="sure-save"[^>]*disabled', out["refused"]["top"])
    assert every_row(conn) == before
    assert rules_of(conn, "Rhode Skin") == []


@needs_node
def test_case_16_a_second_added_wording_the_same_as_the_first_is_refused(server, conn, card):
    out = run(server, OPEN % "await more('RHODESKIN.COM SG'); await more('Rhodeskin.com sg');"
                             " return { after: line() };")

    lines = added_lines(out["after"]["detail"])
    assert lines[0] == ("RHODESKIN.COM SG", "remove matches 1 row in the book")
    assert "Already a wording here" in lines[1][1]
    assert out["after"]["ready"] is False


@needs_node
def test_case_17_saving_the_line_saves_both_wordings_and_remove_takes_one_away(server, conn, card):
    out = run(server, OPEN % "await more('RHODESKIN.COM SG'); await more('SKINNY'); remove(1);"
                             " const kept = line(); await save(); return { kept, toasts: __toasts };")

    assert [v for v, _ in added_lines(out["kept"]["detail"])] == ["RHODESKIN.COM SG"]
    assert not any(t["bad"] for t in out["toasts"]), out["toasts"]
    assert rules_of(conn, "Rhode Skin") == [("RHODESKIN.COM SG", "contains"), ("SP RHODE SKIN RHODESKIN.COM CA", "contains")]
    assert conn.execute("SELECT s.name FROM transactions t JOIN services s ON s.id = t.service_id"
                        " WHERE t.id = ?", (card["loose"],)).fetchone()[0] == "Rhode Skin"


@needs_node
def test_an_added_wording_that_matches_no_row_blocks_the_save(server, conn, card):
    """As the first wording does: a typo would save a rule that never matches (review F1)."""
    before = every_row(conn)

    out = run(server, OPEN % "await more('XSS<B>X'); const typo = line(); await save();"
                             " return { typo, toasts: __toasts };")

    [(_, said)] = added_lines(out["typo"]["detail"])
    assert "matches no row" in said
    assert out["typo"]["ready"] is False
    assert re.search(r'<button[^>]*data-act="sure-save"[^>]*disabled', out["typo"]["top"])
    assert every_row(conn) == before
    assert rules_of(conn, "Rhode Skin") == []
