"""The rule shown when teaching (fin-surfaces 53; spec 24, screens
(teaching), cases 27-31).

Wherever a merchant is taught (the sure card, Pick a type, "This was…") the
line says the wording its rule will recognise, with a live count of the rows
that wording would label: the preview function's count (relabel_preview),
read through GET /api/rules/reach, which writes nothing. A wording that
matches only the row being taught (a booking number kept in it), nothing,
or rows another merchant holds says so at once. Saving a wording that would
take another merchant's rows goes through the preview: without an answer
nothing is written and the rows it would move come back, named by merchant;
past_rows: apply moves them to the merchant taught, keeping their sources;
only_new leaves them where they are, their fields checked.

Each case is one request through fin's public route (the Flask test client),
then what the rows say afterwards, the change entry and what Undo restores.
The client's words are run under node, the script on stdin. The database is
the temporary one; every merchant, wording and figure is invented.
"""

import json
import re
import subprocess

import pytest

from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    merchant, rule, statement, type_id,
)
from test_client_seams import APP_JS, NODE, needs_node
from test_preview_merchant_change import add_row, entries, every_row, labels

TAUGHT = "INDIGOAIR 312-4567890 MUMBAI"


@pytest.fixture
def book(conn, statement):
    """Six IndigoAir rows waiting with no merchant (the sure card's line, the
    one being taught among them); four rows Air India holds whose wording
    holds INDIGO and no rule of Air India's matches; one more row with no
    merchant whose wording holds INDIGO; and a Vistara row its own longer
    rule wins, which no shorter wording takes."""
    waiting = [add_row(conn, statement, f"INDIGOAIR 312-45{n:05d} MUMBAI", 58810 + n, service_id=None,
                       book=None, type_name=None, day=f"2026-09-{n + 1:02d}") for n in range(5)]
    tx = add_row(conn, statement, TAUGHT, 64230, service_id=None, book=None, type_name=None, day="2026-10-02")
    waiting.append(tx)
    air_india = merchant(conn, "Air India", "Moom", "Transport")
    rule(conn, "AIRINDIA", air_india)
    held = [add_row(conn, statement, "AIR INDIA INDIGO LOUNGE", 4200 + n, service_id=air_india, book="Moom",
                    type_name="Transport", day=f"2026-0{n + 5}-14") for n in range(4)]
    loose = add_row(conn, statement, "INDIGO 6E PNR", 18990, service_id=None, book=None, type_name=None,
                    day="2026-05-09")
    vistara = merchant(conn, "Vistara", "Household", "Travel")
    rule(conn, "INDIGO VISTA", vistara)
    won = add_row(conn, statement, "INDIGO VISTA LOUNGE", 7000, service_id=vistara, day="2026-04-01")
    return {"tx": tx, "waiting": waiting, "air_india": air_india, "held": held, "loose": loose,
            "vistara": vistara, "won": won}


def reach(client, pattern, tx, **more):
    resp = client.get("/api/rules/reach", query_string={"pattern": pattern, "match_type": "contains",
                                                        "tx_id": tx, "service_name": "Indigoair", **more})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def teach(conn, tx, pattern, **more):
    return {"tx_id": tx, "service_name": "Indigoair", "type_id": type_id(conn, "Travel"), "book": "Household",
            "apply_scope": "service_default", "pattern": pattern, "match_type": "contains", **more}


# --- case 27: the line reads the wording and its rows -----------------------------------------


def test_case_27_the_wording_counts_the_lines_rows_and_reading_it_writes_nothing(client, conn, book):
    before, history_before = every_row(conn), entries(client)

    got = reach(client, "INDIGOAIR", book["tx"])

    assert (got["pattern"], got["count"], got["only_this"], got["others"]) == ("INDIGOAIR", 6, False, [])
    assert every_row(conn) == before and entries(client) == history_before


# --- case 28: a wording that matches only its own row ----------------------------------------


def test_case_28_a_wording_with_the_booking_number_matches_only_this_row(client, conn, book):
    got = reach(client, TAUGHT, book["tx"])

    assert (got["count"], got["only_this"], got["others"]) == (1, True, [])


def test_case_28_on_a_line_of_rows_only_this_one_is_any_of_them(client, conn, book):
    """The sure card asks for its line's rows: the one row matched is one of
    them, whichever the save is pressed for."""
    resp = client.get("/api/rules/reach", query_string=[("pattern", TAUGHT), ("service_name", "Indigoair"),
                                                         *(("tx_id", t) for t in book["waiting"])])
    elsewhere = client.get("/api/rules/reach", query_string={"pattern": "INDIGO 6E PNR", "tx_id": book["tx"]})

    assert resp.get_json()["only_this"] is True
    assert (elsewhere.get_json()["count"], elsewhere.get_json()["only_this"]) == (1, False)


def test_case_28_saving_it_is_still_allowed_and_labels_only_this_row(client, conn, book):
    resp = client.post("/api/transactions/resolve", json=teach(conn, book["tx"], TAUGHT))

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["backfilled_ids"] in ([], [book["tx"]])
    assert labels(conn, [book["tx"]])[book["tx"]][:2] == ("Household", type_id(conn, "Travel"))
    others = [t for t in book["waiting"] if t != book["tx"]]
    assert set(labels(conn, others).values()) == {(None, None, None, None)}


# --- case 29: a wording that reaches another merchant's rows ---------------------------------


def test_case_29_a_wording_that_reaches_another_merchants_rows_names_them(client, conn, book):
    got = reach(client, "INDIGO", book["tx"])

    assert (got["count"], got["only_this"]) == (11, False)
    assert got["others"] == [{"service_id": book["air_india"], "name": "Air India", "count": 4}]


def test_case_29_a_row_a_longer_rule_wins_is_neither_counted_nor_taken(client, conn, book):
    got = reach(client, "INDIGO", book["tx"])

    assert book["won"] not in [o["service_id"] for o in got["others"]]
    assert all(o["name"] != "Vistara" for o in got["others"]) and got["count"] == 11


def test_case_29_saving_it_writes_nothing_and_returns_the_preview_of_the_4(client, conn, book):
    before, history_before = every_row(conn), entries(client)

    resp = client.post("/api/transactions/resolve", json=teach(conn, book["tx"], "INDIGO"))

    assert resp.status_code == 409, resp.get_json()
    body = resp.get_json()
    assert "4 past rows" in body["error"] and "nothing was saved" in body["error"].lower()
    p = body["preview"]
    assert p["subject"] == {"kind": "merchant", "id": None, "name": "Indigoair"}
    assert p["change"] == [{"field": "wording", "from": None, "to": "INDIGO"}]
    assert (p["count"], p["checked"], p["chosen"]) == (4, 0, 0)
    assert p["others"] == [{"service_id": book["air_india"], "name": "Air India", "count": 4}]
    assert [(x["now"], x["after"]) for x in p["examples"]] == [("Air India · Transport", "Indigoair · Travel")] * 3
    assert p["token"]
    assert every_row(conn) == before and entries(client) == history_before
    assert conn.execute("SELECT COUNT(*) FROM services WHERE name = 'Indigoair'").fetchone()[0] == 0


def test_case_29_apply_teaches_as_today_and_moves_the_4_keeping_their_sources(client, conn, book):
    token = client.post("/api/transactions/resolve", json=teach(conn, book["tx"], "INDIGO")).get_json()["preview"]["token"]
    history_before = entries(client)

    resp = client.post("/api/transactions/resolve",
                       json=teach(conn, book["tx"], "INDIGO", past_rows="apply", token=token))

    assert resp.status_code == 200, resp.get_json()
    svc = resp.get_json()["service_id"]
    travel = type_id(conn, "Travel")
    held = conn.execute(
        f"SELECT service_id, book, type_id, book_source, type_source FROM transactions"
        f" WHERE id IN ({','.join('?' * 4)})", book["held"]).fetchall()
    assert {tuple(r) for r in held} == {(svc, "Household", travel, "merchant", "merchant")}
    # Teaching as today: the waiting rows and the loose one take the label.
    taught = book["waiting"] + [book["loose"]]
    assert {labels(conn, taught)[t][:2] for t in taught} == {("Household", travel)}
    assert conn.execute("SELECT service_id FROM transactions WHERE id = ?", (book["won"],)).fetchone()[0] == book["vistara"]
    new = entries(client)
    assert len(new) == len(history_before) + 1 and new[0]["via"] == "app"


def test_case_29_only_new_rows_leaves_the_4_with_air_india_their_fields_checked(client, conn, book):
    resp = client.post("/api/transactions/resolve",
                       json=teach(conn, book["tx"], "INDIGO", past_rows="only_new"))

    assert resp.status_code == 200, resp.get_json()
    held = conn.execute(
        f"SELECT service_id, book, type_id, book_source, type_source FROM transactions"
        f" WHERE id IN ({','.join('?' * 4)})", book["held"]).fetchall()
    assert {tuple(r) for r in held} == {(book["air_india"], "Moom", type_id(conn, "Transport"), "checked", "checked")}
    assert labels(conn, [book["loose"]])[book["loose"]][:2] == ("Household", type_id(conn, "Travel"))


def test_case_29_undo_of_the_apply_restores_the_rows_merchants_and_rules(client, conn, book):
    before = every_row(conn)
    services = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
    rules = conn.execute("SELECT COUNT(*) FROM merchant_rules").fetchone()[0]
    resp = client.post("/api/transactions/resolve", json=teach(conn, book["tx"], "INDIGO", past_rows="apply"))
    assert resp.status_code == 200, resp.get_json()

    undone = client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")

    assert undone.status_code == 200, undone.get_json()
    assert every_row(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM services").fetchone()[0] == services
    assert conn.execute("SELECT COUNT(*) FROM merchant_rules").fetchone()[0] == rules


def test_case_29_a_stale_preview_is_refused_and_writes_nothing(client, conn, book):
    token = client.post("/api/transactions/resolve", json=teach(conn, book["tx"], "INDIGO")).get_json()["preview"]["token"]
    client.put(f"/api/transactions/{book['held'][0]}", json={"book": "Kalesh"})
    before, history_before = every_row(conn), entries(client)

    resp = client.post("/api/transactions/resolve",
                       json=teach(conn, book["tx"], "INDIGO", past_rows="apply", token=token))

    assert resp.status_code == 409 and resp.get_json()["stale"] is True
    assert "look again" in resp.get_json()["error"]
    assert every_row(conn) == before and entries(client) == history_before


def test_a_chosen_row_of_another_merchant_is_left_where_it_is_and_counted_apart(client, conn, book, statement):
    kept = add_row(conn, statement, "AIR INDIA INDIGO LOUNGE", 5100, service_id=book["air_india"], book="Kalesh",
                   type_name="Transport", sources=("chosen", "merchant"), day="2026-09-30")

    got = reach(client, "INDIGO", book["tx"])
    p = client.post("/api/transactions/resolve", json=teach(conn, book["tx"], "INDIGO")).get_json()["preview"]

    assert got["others"][0]["count"] == 4
    assert (p["count"], p["chosen"]) == (4, 1)
    resp = client.post("/api/transactions/resolve",
                       json=teach(conn, book["tx"], "INDIGO", past_rows="apply", token=p["token"]))
    assert resp.status_code == 200, resp.get_json()
    assert conn.execute("SELECT service_id, book FROM transactions WHERE id = ?", (kept,)).fetchone()[:] == (
        book["air_india"], "Kalesh")


def test_a_taken_row_whose_book_is_the_taught_merchants_moves_no_money(client, conn, book):
    """A row a taught wording moves to a merchant whose book is the row's own
    moves no money (relabel_preview._book_moves, the one test fin-surfaces 50
    and 53 share): taught as Moom, Air India's four Moom rows show no money
    line; taught as Household with one of them already Household, only the
    other three move."""
    p = client.post("/api/transactions/resolve", json=teach(conn, book["tx"], "INDIGO", book="Moom")).get_json()["preview"]
    assert (p["count"], p["money"], p["months"]) == (4, None, None)

    conn.execute("UPDATE transactions SET book = 'Household' WHERE id = ?", (book["held"][0],))
    conn.commit()
    p = client.post("/api/transactions/resolve", json=teach(conn, book["tx"], "INDIGO")).get_json()["preview"]
    moving = book["held"][1:]
    total = sum(r[0] for r in conn.execute(
        f"SELECT amount_minor FROM transactions WHERE id IN ({','.join('?' * len(moving))})", moving))
    assert p["count"] == 4
    assert p["money"] == [{"from": "Moom", "to": "Household", "amount_minor": total}]
    assert p["months"]["count"] == 3


def test_a_wording_on_a_held_merchant_with_a_new_default_is_one_preview(client, conn, book, statement):
    """Teaching a merchant fin holds a new default, by a wording that also
    reaches another merchant's rows: one question, both sets of rows."""
    indigo = merchant(conn, "Indigoair", "Household", "Travel")
    rule(conn, "INDIGOAIR", indigo)
    own = add_row(conn, statement, "INDIGOAIR 6E OLD", 30000, service_id=indigo, day="2026-03-03")

    resp = client.post("/api/transactions/resolve",
                       json=teach(conn, book["tx"], "INDIGO", service_id=indigo, book="Moom"))

    assert resp.status_code == 409, resp.get_json()
    p = resp.get_json()["preview"]
    assert p["count"] == 5 and p["others"][0]["count"] == 4
    assert {c["field"] for c in p["change"]} == {"book", "wording"}
    resp = client.post("/api/transactions/resolve", json=teach(
        conn, book["tx"], "INDIGO", service_id=indigo, book="Moom", past_rows="apply", token=p["token"]))
    assert resp.status_code == 200, resp.get_json()
    assert labels(conn, [own])[own][0] == "Moom"
    assert {r[0] for r in conn.execute(
        f"SELECT service_id FROM transactions WHERE id IN ({','.join('?' * 4)})", book["held"])} == {indigo}


# --- case 30: a wording that matches nothing --------------------------------------------------


def test_case_30_a_wording_that_matches_nothing_counts_none(client, conn, book):
    got = reach(client, "NOSUCHWORDING", book["tx"])

    assert (got["count"], got["only_this"], got["others"]) == (0, False, [])


def test_the_reach_of_an_empty_or_too_generic_wording_is_refused_in_words(client, conn, book):
    empty = client.get("/api/rules/reach", query_string={"pattern": "  ", "tx_id": book["tx"]})
    generic = client.get("/api/rules/reach", query_string={"pattern": "PAYNOW", "tx_id": book["tx"]})
    odd = client.get("/api/rules/reach", query_string={"pattern": "INDIGO", "match_type": "regex"})

    assert empty.status_code == 400 and "wording" in empty.get_json()["error"]
    assert generic.status_code == 400 and "too generic" in generic.get_json()["error"]
    assert odd.status_code == 400 and "contains" in odd.get_json()["error"]


def test_the_reach_of_a_wording_an_existing_rule_holds_counts_that_rules_rows(client, conn, book, statement):
    """Teaching by a wording fin already has a rule for re-points that rule:
    its rows are the ones it reaches, Air India's among them when it is Air
    India's rule."""
    rule(conn, "INDIGO LOUNGE", book["air_india"])
    got = reach(client, "INDIGO LOUNGE", book["tx"])

    assert got["count"] == 4 and got["others"] == [{"service_id": book["air_india"], "name": "Air India", "count": 4}]


def test_the_live_count_runs_within_a_second_over_fifty_thousand_rows(client, conn, statement):
    import time
    held = merchant(conn, "Sample Volume Air", "Household", "Travel")
    rule(conn, "SAMPLE VOLUME AIR 9", held)          # a longer rule that wins some of the rows
    travel = type_id(conn, "Travel")
    conn.executemany(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id,"
        " service_id, flow_type, book_source, type_source) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(statement, f"20{24 + i % 3}-{1 + i % 12:02d}-{1 + i % 28:02d}", f"SAMPLE VOLUME AIR {i}", 500 + i % 900,
          "Household", travel, held, "expense", "merchant", "merchant") for i in range(50_000)])
    conn.commit()

    took = []
    for _ in range(3):
        start = time.process_time()
        got = reach(client, "SAMPLE VOLUME", None, service_name="Sample Other Air")
        took.append(time.process_time() - start)
    won = sum(1 for i in range(50_000) if str(i).startswith("9"))
    assert got["count"] == 50_000 - won and got["others"][0]["count"] == 50_000 - won
    assert min(took) < 1.0, took


# --- cases 27-31: the line's words, and where it is drawn -------------------------------------


def _function(name):
    found = (re.search(rf"^(?:async )?function {name}\(.*\}}$", APP_JS, re.M)
             or re.search(rf"^(?:async )?function {name}\(.*?^\}}$", APP_JS, re.M | re.S))
    assert found, f"{name} not found in static/app.js"
    return found.group(0)


PRELUDE = r"""
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
function plural(n, one, many) { return `${n} ${n === 1 ? one : (many || one + 's')}`; }
"""


def _node(code):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30,
                         check=True, encoding="utf-8")
    return json.loads(out.stdout)


def words(calls: str) -> dict:
    code = "\n".join([PRELUDE, *(_function(n) for n in ("recWarn", "recCountHTML", "recMiniHTML", "recLineHTML")),
                      f"console.log(JSON.stringify({calls}));"])
    return _node(code)


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


@needs_node
def test_case_27_the_sure_line_and_its_detail_read_the_wording():
    got = words("{ mini: recMiniHTML('INDIGOAIR'), line: recLineHTML('k1', 'INDIGOAIR', "
                "{ count: 6, only_this: false, others: [] }, false) }")

    assert text(got["mini"]) == "recognises INDIGOAIR"
    assert text(got["line"]) == "Recognises INDIGOAIR · 6 rows · change"
    assert 'data-act="rec-edit"' in got["line"]


@needs_node
def test_case_28_only_this_row_warns_in_the_warning_colour():
    got = words("recCountHTML({ count: 1, only_this: true, others: [] })")

    assert text(got) == "matches 1 row · only this one: a number in the wording?"
    assert 'class="warn-word"' in got


@needs_node
def test_case_29_another_merchants_rows_are_named():
    got = words("recCountHTML({ count: 11, only_this: false, others: [{ name: 'Air India', count: 4 }] })")

    assert text(got) == "matches 11 rows · also 4 rows now Air India’s"
    assert 'class="warn-word"' in got


@needs_node
def test_case_30_a_wording_that_matches_nothing_says_check_the_wording():
    got = words("{ none: recCountHTML({ count: 0, only_this: false, others: [] }),"
                " editing: recLineHTML('k1', 'ZZZ', { count: 0, only_this: false, others: [] }, true) }")

    assert text(got["none"]) == "matches no row: check the wording"
    assert 'class="warn-word"' in got["none"]
    assert re.search(r'<input[^>]*data-rec="k1"[^>]*value="ZZZ"', got["editing"])
    assert "Recognises rows containing" in text(got["editing"])


def test_case_30_a_line_whose_wording_matches_nothing_cannot_be_saved():
    assert "recBlocks(" in _function("sureReady")
    assert "recBlocks(" in _function("pickSaveHTML")


def _act(name):
    start = APP_JS.index(f"ACT['{name}'] =")
    return APP_JS[start:APP_JS.index("\nACT[", start + 1)]


def test_case_31_the_same_line_on_the_sure_card_pick_a_type_and_this_was():
    """One line, drawn by recShown (recLineHTML), on each place a merchant
    is taught."""
    assert "recLineHTML(" in _function("recShown")
    assert "recMiniHTML(" in _function("sureRowHTML")
    assert "recShown(" in _function("sureDetailHTML")
    assert "pickRec(" in _function("pickCardHTML") and "recShown(" in _function("pickRec")
    assert "wwRec()" in _act("ww-pick") and "recShown('ww'" in _function("wwRec")


@needs_node
def test_case_31_a_count_read_before_its_line_is_in_the_page_is_drawn_once_it_is():
    """Walk lp-r1: the Queue is built as a string while other reads finish,
    then put in the page. Pick a type's count (SKYWING, 11 rows, 4 of Air
    India's) came back first, found no line to draw into, and the line read
    "Recognises SKYWING · change" until something redrew it. Run under node
    with a page of stand-ins: the line goes in only after the count is
    back, and the page tells its observers, as a browser does."""
    names = ("recWarn", "recCountHTML", "recMiniHTML", "recLineHTML", "recKeyWording", "recReach",
             "recLoad", "recShown", "recDraw")
    consts = "\n".join(re.search(rf"^const {n} = .*$", APP_JS, re.M).group(0)
                       for n in ("REC_WAIT", "recWords", "recOpen", "recFound", "recCtx", "recTimers"))
    late = "\n".join(re.findall(r"^(?:const recLate|let recWatch) = .*$", APP_JS, re.M))
    helpers = "\n".join(_function(n) for n in names + (("recWhenShown",) if "function recWhenShown(" in APP_JS else ()))
    code = "\n".join([PRELUDE, r"""
const page = [], observers = [];
const $$ = sel => sel === '[data-rec-line]' ? page.filter(b => b.dataset.recLine) : [];
const $ = () => null;
class MutationObserver { constructor(cb) { this.cb = cb; } observe() { observers.push(this); } disconnect() { observers.splice(observers.indexOf(this), 1); } }
const document = { createElement: () => ({ set innerHTML(h) { this.firstElementChild = { html: h }; } }) };
const REACH = { count: 11, only_this: false, others: [{ name: 'Air India', count: 4 }], pattern: 'SKYWING' };
async function get(url) { return REACH; }
const tick = ms => new Promise(r => setTimeout(r, ms));
""", consts, late, helpers, r"""
(async () => {
    const built = recShown('pick-386', { tx: 386, txs: [386], name: 'Skywing', suggested: 'SKYWING' });
    await tick(20);                      // the count is back; the lane is still a string
    const box = { dataset: { recLine: 'pick-386' }, html: built, replaceWith(el) { this.html = el.html; } };
    page.push(box);                      // the lane goes in
    [...observers].forEach(o => o.cb([]));
    await tick(20);
    console.log(JSON.stringify({ built, shown: box.html }));
})();
"""])
    got = _node(code)

    assert text(got["built"]) == "Recognises SKYWING · change"
    assert text(got["shown"]) == "Recognises SKYWING · 11 rows · also 4 rows now Air India’s · change"


def test_the_wording_shown_is_the_one_saved():
    """Each teach sends the wording its line shows, and a line whose wording
    matches nothing is not sent."""
    assert "pattern: recWording(" in _function("saveSureGroup")
    assert "pattern: recWording(" in _act("pick-save") and "recBlocks(" in _act("pick-save")
    assert "recWording('ww'" in _act("ww-save") and "recBlocks('ww')" in _act("ww-save")
    assert 'id="rs-pattern"' not in APP_JS          # one wording, on the line, not a second field


@needs_node
def test_case_29_the_drawer_names_the_rows_it_would_take():
    from test_preview_merchant_change import SAMPLE, preview_html
    sample = {**SAMPLE, "subject": {"kind": "merchant", "id": None, "name": "Indigoair"},
              "change": [{"field": "wording", "from": None, "to": "INDIGO"}], "count": 4, "checked": 0, "chosen": 0,
              "money": None, "months": None,
              "others": [{"service_id": 7, "name": "Air India", "count": 4}],
              "examples": [{"id": 1, "date": "2026-08-14", "amount_minor": 4200, "currency": "SGD",
                            "now": "Air India · Transport", "after": "Indigoair · Travel", "source": "merchant"}]}

    got = preview_html(sample)

    body = text(got["body"])
    assert got["title"] == "Teach Indigoair the wording INDIGO?"
    assert "4 rows now Air India’s would move to Indigoair." in body
    assert 'class="warnline"' in got["body"] and "This changes 4 past rows" in body
    assert "Air India · Transport → Indigoair · Travel" in body
    assert "See all" not in body                     # a merchant not made yet has no page
    assert "Apply to 4 past rows" in got["foot"]


def test_the_count_is_read_from_the_reach_route_debounced():
    load = _function("recLoad")
    assert "/api/rules/reach?" in load and "setTimeout" in load
