"""Personal-ledger 35: the expectation's page and the open list (E1 reads, D3).

Two reads on the expect-back blueprint, both read-only:

- GET /api/expectations/<id>: one expectation's meter and its history lines
  (Marked, each set-off, refund lines, a let-go, the open rest named per Who
  is still out), each line carrying only the actions the page draws.
- GET /api/expectations: the open list (direction, person, currency, nearest
  a date, or a payment row), which Back for... and Books read.

Figures go in through seeded records (tests/expect_fixtures.py) and come out
through the routes, then through the page's drawing under node, the payload
handed over as the route gave it. Every name, figure and date is invented;
the database is the temp one conftest gives.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import db
import expect_back
import expectation_page_api
from expect_fixtures import (dismiss, entry, let_go, mark, october, opening, refund_link, row,
                             set_off, wording)

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
FIXTURES = Path(__file__).parent / "fixtures" / "expect"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
PAGE_JS = (STATIC / "expect-page.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

TODAY = "2026-10-19"
TABLES = ("expectation", "expectation_name", "set_off", "let_go", "refund_link", "sender_wording",
          "dismissal", "change_entries", "change_rows", "transactions")


@pytest.fixture(autouse=True)
def today(monkeypatch):
    """Today is 19 Oct 2026, as the case list's."""
    import datetime
    monkeypatch.setattr(expectation_page_api, "_today", lambda: datetime.date(2026, 10, 19))


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def shape(value):
    """A payload's shape: its keys, all the way down (lists by their first item)."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return None


def same_keys(got, want, path="$"):
    """Every dict in `got` has exactly the keys the contract fixture's has, where both exist."""
    if isinstance(want, dict) and isinstance(got, dict):
        assert set(got) == set(want), f"{path}: {sorted(set(got) ^ set(want))}"
        for k in want:
            same_keys(got[k], want[k], f"{path}.{k}")
    elif isinstance(want, list) and isinstance(got, list) and want and got:
        for i, g in enumerate(got):
            # the page's lines differ by kind: each is held to the fixture's line of its kind
            like = [w for w in want if isinstance(w, dict) and isinstance(g, dict) and "kind" in g
                    and w.get("kind") == g["kind"]] or want
            same_keys(g, like[0], f"{path}[{i}]")


def page(client, expectation_id: int) -> dict:
    resp = client.get(f"/api/expectations/{expectation_id}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def open_list(client, **query) -> dict:
    q = "&".join(f"{k}={v}" for k, v in query.items())
    resp = client.get(f"/api/expectations{'?' + q if q else ''}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def counts(conn) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


# ---------------------------------------------------------------------------
# The story: the case list's October, with every record its cases need
# ---------------------------------------------------------------------------

def story(conn):
    """Cases 1, 2, 9, 10, 11 and the check book's AIRBNB and Ravi fixtures."""
    o = october(conn)
    b, p = o.book, o.people
    s = {"o": o}
    s["harbour"] = mark(conn, o.harbour, 20000, names=(p["Jay"], p["Kim"]))
    s["klook"] = mark(conn, o.klook, 18000, names=(p["Kim"],), note="concert tickets")
    s["golden"] = mark(conn, o.golden, 6400, names=(p["Jay"], p["Lena"]))
    s["airbnb"] = mark(conn, o.airbnb, 62000, names=(p["Lena"],))
    lena_jul = row(conn, b, b.savings, "2026-07-10", "PAYNOW FROM LENA 1188", -58000, flow="review")
    set_off(conn, lena_jul, s["airbnb"], 58000)
    s["ravi_row"] = row(conn, b, b.savings, "2026-06-03", "PAYNOW TO RAVI", 200000, flow="movement",
                        other_side=p["Ravi"])
    ravi_back = row(conn, b, b.savings, "2026-08-15", "PAYNOW FROM RAVI", -50000, flow="movement",
                    other_side=p["Ravi"])
    s["ravi"] = mark(conn, s["ravi_row"], 200000, names=(p["Ravi"],), source="converted")
    set_off(conn, ravi_back, s["ravi"], 50000, source="converted")
    s["jay_on_harbour"] = set_off(conn, o.jay_0412, s["harbour"], 12000)
    with entry(conn, "Set off"):
        s["kim_on_harbour"] = set_off(conn, o.kim_7781, s["harbour"], 6000)
        s["kim_on_klook"] = set_off(conn, o.kim_7781, s["klook"], 1000)
    return s


# ---------------------------------------------------------------------------
# Case 12: several payments, one item
# ---------------------------------------------------------------------------

def test_case_12_harbour_page_meter_lines_and_an_open_rest_that_names_nobody(client, conn):
    s = story(conn)
    f = page(client, s["harbour"])
    same_keys(f, fixture("page"))
    e = f["expectation"]
    assert (e["amount_minor"], e["set_off_minor"], e["let_go_minor"], e["open_minor"]) == (20000, 18000, 0, 2000)
    assert e["state"] == "partly_back" == expect_back.state(conn, s["harbour"])
    assert [n["name"] for n in e["names"]] == ["Jay", "Kim"]
    assert e["day"] == "2026-10-02" and e["row"]["description"] == "HARBOUR GRILL"
    assert e["row"]["display_type"] == "Dining" and e["row"]["account_name"] == "Rewards card"
    assert (f["as_at"], f["age_days"], f["back_pct"], f["own_minor"]) == (TODAY, 17, 90, 10000)
    assert [x["kind"] for x in f["lines"]] == ["marked", "set_off", "set_off", "open"]
    marked, jay, kim, rest = f["lines"]
    assert (marked["date"], marked["amount_minor"], marked["actions"]) == ("2026-10-02", 20000, ["undo_mark"])
    assert (jay["id"], jay["date"], jay["person"], jay["amount_minor"]) == (s["jay_on_harbour"], "2026-10-10", "Jay", 12000)
    assert jay["elsewhere"] == [] and jay["actions"] == ["unlink"]
    assert (kim["date"], kim["person"], kim["amount_minor"], kim["payment"]["amount_minor"]) == ("2026-10-15", "Kim", 6000, -7000)
    assert kim["elsewhere"] == [{"expectation_id": s["klook"], "title": "KLOOK TRAVEL", "amount_minor": 1000, "currency": "SGD"}]
    assert rest == {"kind": "open", "amount_minor": 2000, "currency": "SGD", "name": None, "actions": ["let_go"]}


def test_case_12_golden_village_after_case_32_names_lena_alone(client, conn):
    s = story(conn)
    o = s["o"]
    jay_0533 = row(conn, o.book, o.book.savings, "2026-10-25", "PAYNOW FROM JAY TAN 0533", -4000, flow="review")
    set_off(conn, jay_0533, s["golden"], 4000)
    f = page(client, s["golden"])
    assert f["lines"][-1] == {"kind": "open", "amount_minor": 2400, "currency": "SGD", "name": "Lena", "actions": ["let_go"]}
    assert f["lines"][1]["person"] == "Jay"


def test_who_is_still_out_reads_a_learnt_wording_as_paying(client, conn):
    """Lena's payment carries no 'LENA' in its text, but her learnt wording: she has paid."""
    s = story(conn)
    o = s["o"]
    wording(conn, "L WONG", o.people["Lena"])
    lw = row(conn, o.book, o.book.savings, "2026-10-26", "PAYNOW FROM L WONG 7001", -1000, flow="review")
    set_off(conn, lw, s["golden"], 1000)
    f = page(client, s["golden"])
    assert f["lines"][1]["person"] == "Lena"
    assert f["lines"][-1]["name"] == "Jay"


def test_the_payer_on_each_line_agrees_with_the_shared_rule(client, conn):
    s = story(conn)
    o = s["o"]
    f = page(client, s["harbour"])
    assert [x["person"] for x in f["lines"] if x["kind"] == "set_off"] == [
        expect_back.payer_name(conn, o.jay_0412), expect_back.payer_name(conn, o.kim_7781)]


# ---------------------------------------------------------------------------
# Case 26a: a closed item offers no Let it go...; let-go and opening figures
# ---------------------------------------------------------------------------

def test_case_26a_klook_closed_has_no_open_line_and_no_let_go(client, conn):
    s = story(conn)
    o = s["o"]
    kim_more = row(conn, o.book, o.book.savings, "2026-10-16", "PAYNOW FROM KIM L 7782", -17000, flow="review")
    set_off(conn, kim_more, s["klook"], 17000)
    f = page(client, s["klook"])
    assert f["expectation"]["state"] == "closed" and f["expectation"]["open_minor"] == 0
    assert f["back_pct"] == 100 and f["own_minor"] == 0
    assert "open" not in [x["kind"] for x in f["lines"]]
    assert not any("let_go" in x["actions"] for x in f["lines"])


def test_case_24_let_go_line_replaces_the_open_rest(client, conn):
    s = story(conn)
    o = s["o"]
    lid = let_go(conn, s["harbour"], "gift", "2026-10-19", to_whom_id=o.people["Kim"])
    f = page(client, s["harbour"])
    same_keys(f, fixture("page_let_go"))
    assert f["expectation"]["state"] == "let_go" and f["expectation"]["let_go_minor"] == 2000
    assert f["lines"][-1] == {"kind": "let_go", "id": lid, "date": "2026-10-19", "amount_minor": 2000,
                              "currency": "SGD", "became": "gift", "to": "Kim", "book": "Household",
                              "display_type": "Gifts & Donations", "actions": ["undo_let_go"]}
    assert "open" not in [x["kind"] for x in f["lines"]]


def test_case_41_an_opening_figure_has_no_row_and_names_its_person(client, conn):
    o = october(conn)
    sam = opening(conn, o.people["Sam"], "2026-01-01", 80000)
    f = page(client, sam)
    e = f["expectation"]
    assert e["row"] is None and e["opening"] == {"account_id": o.people["Sam"], "name": "Sam", "date": "2026-01-01"}
    assert e["day"] == "2026-01-01" and f["age_days"] == 291 and f["own_minor"] is None
    assert [x["kind"] for x in f["lines"]] == ["marked", "open"]
    assert f["lines"][-1]["name"] == "Sam"


def test_a_page_for_no_such_expectation_is_refused(client, conn):
    october(conn)
    resp = client.get("/api/expectations/999")
    assert resp.status_code == 404 and set(resp.get_json()) == {"error"}


# ---------------------------------------------------------------------------
# Case 36: a refund that closes an expectation draws as a refund line
# ---------------------------------------------------------------------------

def test_case_36_a_refund_set_off_is_a_refund_line(client, conn):
    s = story(conn)
    o = s["o"]
    refund = row(conn, o.book, o.book.everyday_card, "2026-10-04", "KLOOK TRAVEL REFUND", -3000,
                 flow="refund", type_name="Entertainment")
    with entry(conn, "Linked a refund"):
        refund_link(conn, refund, o.klook)
        rid = set_off(conn, refund, s["klook"], 3000, source="refund")
    f = page(client, s["klook"])
    same_keys(f, fixture("page_refund"))
    assert [x["kind"] for x in f["lines"]] == ["marked", "refund", "set_off", "open"]
    line = f["lines"][1]
    assert (line["id"], line["date"], line["purchase_date"], line["amount_minor"]) == (rid, "2026-10-04", "2026-09-28", 3000)
    assert line["refund"] == {"id": refund, "description": "KLOOK TRAVEL REFUND", "amount_minor": -3000, "currency": "SGD"}
    assert line["actions"] == ["unlink"]
    assert f["expectation"]["open_minor"] == 14000


# ---------------------------------------------------------------------------
# The open list: Back for... and Books read it
# ---------------------------------------------------------------------------

def test_case_10_open_list_for_jays_payment_nearest_first(client, conn):
    s = story(conn)
    o = s["o"]
    # undo the October set-offs' effect on what is open: case 10 reads before them
    conn.execute("DELETE FROM set_off WHERE payment_row_id IN (?, ?)", (o.jay_0412, o.kim_7781))
    conn.commit()
    f = open_list(client, payment=o.jay_0412)
    same_keys(f, fixture("open_list"))
    assert (f["direction"], f["near"]) == ("back", "2026-10-10")
    assert [x["row"]["description"] for x in f["items"]] == [
        "HARBOUR GRILL", "KLOOK TRAVEL", "GOLDEN VILLAGE", "AIRBNB", "PAYNOW TO RAVI"]
    assert [(x["open_minor"], x["amount_minor"]) for x in f["items"]] == [
        (20000, 20000), (18000, 18000), (6400, 6400), (4000, 62000), (150000, 200000)]
    assert [x["state"] for x in f["items"]] == [expect_back.state(conn, x["id"]) for x in f["items"]]
    pay = f["payment"]
    assert (pay["id"], pay["amount_minor"], pay["own_minor"], pay["left_minor"]) == (o.jay_0412, -12000, -12000, 12000)
    assert pay["sender"] == {"wording": "JAY TAN", "person": None}


def test_open_list_payment_part_placed_and_the_learnt_sender(client, conn):
    s = story(conn)
    o = s["o"]
    wording(conn, "JAY TAN", o.people["Jay"])
    f = open_list(client, payment=o.jay_0412)
    assert f["payment"]["placed_minor"] == -12000 and f["payment"]["left_minor"] == 0
    assert f["payment"]["sender"] == {"wording": "JAY TAN", "person": {"account_id": o.people["Jay"], "name": "Jay"}}
    # HARBOUR has S$ 20 open after cases 10 and 11; KLOOK S$ 170
    assert [(x["row"]["description"], x["open_minor"]) for x in f["items"]][:2] == [
        ("HARBOUR GRILL", 2000), ("KLOOK TRAVEL", 17000)]


def test_open_list_leaves_out_closed_and_let_go_items(client, conn):
    """Case 16: a closed item is not listed; case 26: a let-go one neither."""
    s = story(conn)
    o = s["o"]
    kim_more = row(conn, o.book, o.book.savings, "2026-10-16", "PAYNOW FROM KIM L 7782", -17000, flow="review")
    set_off(conn, kim_more, s["klook"], 17000)
    let_go(conn, s["harbour"], "gift", "2026-10-19", to_whom_id=o.people["Kim"])
    titles = [x["row"]["description"] for x in open_list(client)["items"]]
    assert "KLOOK TRAVEL" not in titles and "HARBOUR GRILL" not in titles
    assert titles == ["PAYNOW TO RAVI", "AIRBNB", "GOLDEN VILLAGE"]  # oldest first with no date to be near


def test_open_list_filters_direction_person_currency_and_near(client, conn):
    s = story(conn)
    o = s["o"]
    parents_row = row(conn, o.book, o.book.savings, "2026-03-01", "FROM PARENTS", -500000, flow="movement",
                      other_side=o.people["Parents"])
    parents = mark(conn, parents_row, 500000, names=(o.people["Parents"],), source="converted")
    swiggy = mark(conn, o.swiggy, 180000, names=(o.people["Jay"],))
    sam = opening(conn, o.people["Sam"], "2026-01-01", 80000)

    assert [x["id"] for x in open_list(client, direction="pay")["items"]] == [parents]
    back = open_list(client, direction="back")["items"]
    assert parents not in [x["id"] for x in back] and back[0]["id"] == sam
    assert [x["id"] for x in open_list(client, currency="INR")["items"]] == [swiggy]
    assert {x["id"] for x in open_list(client, person=o.people["Lena"])["items"]} == {s["golden"], s["airbnb"]}
    near = open_list(client, near="2026-09-23")
    assert near["near"] == "2026-09-23" and near["direction"] is None and near["payment"] is None
    assert [x["id"] for x in near["items"]][:3] == [s["golden"], s["klook"], s["harbour"]]
    assert [x["id"] for x in open_list(client, person=o.people["Sam"])["items"]] == [sam]


def test_open_list_for_money_out_lists_what_is_to_pay_back(client, conn):
    """Case 18: a money-out row's Back for... lists To pay back items."""
    o = october(conn)
    parents_row = row(conn, o.book, o.book.savings, "2026-03-01", "FROM PARENTS", -500000, flow="movement",
                      other_side=o.people["Parents"])
    parents = mark(conn, parents_row, 500000, names=(o.people["Parents"],))
    out = row(conn, o.book, o.book.savings, "2026-08-01", "TRANSFER TO PARENTS", 100000, flow="movement",
              other_side=o.people["Parents"])
    f = open_list(client, payment=out)
    assert f["direction"] == "pay" and [x["id"] for x in f["items"]] == [parents]
    assert f["items"][0]["row"]["amount_minor"] == -500000


@pytest.mark.parametrize("query,status", [
    ("direction=sideways", 400), ("near=19-10-2026", 400), ("person=x", 400), ("payment=99999", 404),
    ("currency=sgd%3B", 400),
])
def test_open_list_refuses_what_it_cannot_read(client, conn, query, status):
    october(conn)
    resp = client.get(f"/api/expectations?{query}")
    assert resp.status_code == status and set(resp.get_json()) == {"error"}


def test_open_list_refuses_a_direction_the_payment_cannot_set_off(client, conn):
    o = october(conn)
    resp = client.get(f"/api/expectations?payment={o.jay_0412}&direction=pay")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "money in sets off what is expected back"


# ---------------------------------------------------------------------------
# Read-only: no write, no change entry
# ---------------------------------------------------------------------------

def test_both_reads_write_nothing_and_make_no_change(client, conn, monkeypatch):
    s = story(conn)
    o = s["o"]
    dismiss(conn, o.lena_2210)
    before = counts(conn)
    seen: list[str] = []
    real = db.get_connection

    def traced():
        c = real()
        c.set_trace_callback(seen.append)
        return c

    monkeypatch.setattr(db, "get_connection", traced)
    page(client, s["harbour"])
    open_list(client, payment=o.jay_0412)
    open_list(client, direction="back", near="2026-10-01")
    assert seen, "the reads went through db.get_connection"
    writes = [q for q in seen if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|BEGIN)", q, re.I)]
    assert writes == []
    assert counts(conn) == before


# ---------------------------------------------------------------------------
# Case 55 (lane half): one read each at ten times
# ---------------------------------------------------------------------------

def _ten_times(conn, s):
    """Copies the story's expectations to about 2,000 items, 3,000 set-offs and 1,300 rows."""
    o = s["o"]
    stmt = o.book.statements[(o.book.rewards_card, "2026-10")]
    pay_stmt = o.book.statements[(o.book.savings, "2026-10")]
    people = list(o.people.values())
    for i in range(650):
        out = conn.execute("INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type) "
                           "VALUES (?, ?, ?, ?, 'expense')", (stmt, f"2026-10-{1 + i % 28:02d}", f"SAMPLE DINNER {i}", 30000)).lastrowid
        pay = conn.execute("INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type) "
                           "VALUES (?, ?, ?, ?, 'review')", (pay_stmt, f"2026-10-{1 + i % 28:02d}", f"PAYNOW FROM SAMPLE {i}", -30000)).lastrowid
        e1 = conn.execute("INSERT INTO expectation (row_id, direction, amount_minor, currency) VALUES (?, 'back', 20000, 'SGD')",
                          (out,)).lastrowid
        e2 = conn.execute("INSERT INTO expectation (person_id, opening_date, direction, amount_minor, currency) "
                          "VALUES (?, '2026-01-01', 'back', 9000, 'SGD')", (people[i % len(people)],)).lastrowid
        e3 = conn.execute("INSERT INTO expectation (person_id, opening_date, direction, amount_minor, currency) "
                          "VALUES (?, '2026-02-01', 'back', 9000, 'SGD')", (people[(i + 1) % len(people)],)).lastrowid
        for e in (e1, e2, e3):
            conn.execute("INSERT INTO expectation_name (expectation_id, person_id) VALUES (?, ?)", (e, people[i % len(people)]))
        for e, amount in ((e1, 3000), (e2, 1000), (e3, 1000), (s["harbour"], 1)):
            if e == s["harbour"] and i % 10:
                continue
            conn.execute("INSERT INTO set_off (payment_row_id, expectation_id, amount_minor, item_amount_minor) "
                         "VALUES (?, ?, ?, ?)", (pay, e, amount, amount))
    conn.commit()


def _reads(client, monkeypatch, url) -> int:
    seen: list[str] = []
    real = db.get_connection

    def traced():
        c = real()
        c.set_trace_callback(seen.append)
        return c

    with monkeypatch.context() as m:
        m.setattr(db, "get_connection", traced)
        resp = client.get(url)
    assert resp.status_code == 200
    return len([q for q in seen if q.lstrip().upper().startswith(("SELECT", "WITH"))])


def test_case_55_page_and_open_list_read_the_same_few_times_at_ten_times(client, conn, monkeypatch):
    s = story(conn)
    o = s["o"]
    small = {u: _reads(client, monkeypatch, u) for u in (
        f"/api/expectations/{s['harbour']}", f"/api/expectations?payment={o.jay_0412}", "/api/expectations")}
    _ten_times(conn, s)
    assert conn.execute("SELECT COUNT(*) FROM expectation").fetchone()[0] >= 1950
    assert conn.execute("SELECT COUNT(*) FROM set_off").fetchone()[0] >= 2000
    big = {u: _reads(client, monkeypatch, u) for u in small}
    assert big == small, (small, big)
    assert all(n <= 8 for n in big.values()), big
    f = page(client, s["harbour"])
    assert len([x for x in f["lines"] if x["kind"] == "set_off"]) == 2 + 65
    assert len(open_list(client)["items"]) >= 1950


# ---------------------------------------------------------------------------
# The page under node: what the operator reads, drawn from the route's payload
# ---------------------------------------------------------------------------

def _app_function(name: str) -> str:
    m = (re.search(rf"^(?:async )?function {name}\(.*\}}$", APP_JS, re.M)
         or re.search(rf"^(?:async )?function {name}\(.*?^\}}$", APP_JS, re.M | re.S))
    assert m, name
    return m.group(0)


def _app_const(name: str) -> str:
    m = re.search(rf"^const {name} = .*$", APP_JS, re.M)
    assert m, name
    return m.group(0)


PRELUDE = "\n".join([
    "globalThis.window = globalThis;",
    _app_const("SIGNS"), _app_const("DIGITS"), _app_const("formatters"), _app_const("MONTHS"),
    _app_function("esc"), _app_function("digitsOf"), _app_function("groupFor"), _app_function("money"),
    _app_function("day"), _app_function("plural"),
    "const icon = () => ''; const ACT = {}; const registerRoute = () => {};",
    "window.Expect = { slots: {}, words: {}, fill() {} };",
])


def draw(payload: dict) -> str:
    code = PRELUDE + "\n" + PAGE_JS + f"\nconsole.log(JSON.stringify(window.Expect.page.html({json.dumps(payload)})));"
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&amp;", "&").replace("&#39;", "'").strip()


def lines_text(html: str) -> list[str]:
    return [text(m) for m in re.findall(r'<div class="xb-hl[^"]*"[^>]*>(.*?)</div>', html, re.S)]


@needs_node
def test_case_12_drawn_harbour_meter_bar_and_lines(client, conn):
    s = story(conn)
    html = draw(page(client, s["harbour"]))
    words = text(html)
    assert "S$ 20 still open" in words
    assert "S$ 180 back of S$ 200 expected · open 17 days" in words
    assert re.search(r'class="xb-meter__back" style="width:\s*90%', html)
    marked, jay, kim, rest = lines_text(html)
    assert marked.startswith("2 Oct Marked") and marked.endswith("Undo the mark")
    assert jay.startswith("10 Oct Jay") and "S$ 120.00" in jay and jay.endswith("Unlink")
    assert kim.startswith("15 Oct Kim") and "S$ 60.00" in kim and "S$ 70, S$ 10 of it went to Klook" in kim
    assert rest == "open S$ 20.00 Let it go…"


@needs_node
def test_case_12_drawn_golden_village_open_rest_names_lena(client, conn):
    s = story(conn)
    o = s["o"]
    jay_0533 = row(conn, o.book, o.book.savings, "2026-10-25", "PAYNOW FROM JAY TAN 0533", -4000, flow="review")
    set_off(conn, jay_0533, s["golden"], 4000)
    assert lines_text(draw(page(client, s["golden"])))[-1] == "open S$ 24.00 · Lena Let it go…"


@needs_node
def test_case_26a_drawn_closed_klook_has_no_let_go(client, conn):
    s = story(conn)
    o = s["o"]
    kim_more = row(conn, o.book, o.book.savings, "2026-10-16", "PAYNOW FROM KIM L 7782", -17000, flow="review")
    set_off(conn, kim_more, s["klook"], 17000)
    html = draw(page(client, s["klook"]))
    assert "Let it go" not in html and "data-act=\"let-go\"" not in html
    assert "Closed" in text(html) and "S$ 180 back of S$ 180 expected" in text(html)


@needs_node
def test_case_36_drawn_refund_line(client, conn):
    s = story(conn)
    o = s["o"]
    refund = row(conn, o.book, o.book.everyday_card, "2026-10-04", "KLOOK TRAVEL REFUND", -3000,
                 flow="refund", type_name="Entertainment")
    with entry(conn, "Linked a refund"):
        refund_link(conn, refund, o.klook)
        set_off(conn, refund, s["klook"], 3000, source="refund")
    lines = lines_text(draw(page(client, s["klook"])))
    assert lines[1] == "4 Oct refund of 28 Sep S$ 30.00 Unlink"


@needs_node
def test_case_24_drawn_let_go_reads_closed_with_its_undo(client, conn):
    s = story(conn)
    let_go(conn, s["harbour"], "gift", "2026-10-19", to_whom_id=s["o"].people["Kim"])
    html = draw(page(client, s["harbour"]))
    assert "Closed" in text(html) and "S$ 20 let go as a gift to Kim" in text(html)
    assert lines_text(html)[-1].startswith("19 Oct Let go") and lines_text(html)[-1].endswith("Undo")
    assert "Let it go" not in html


@needs_node
def test_buttons_carry_the_contract_urls_and_ids(client, conn):
    s = story(conn)
    html = draw(page(client, s["harbour"]))
    routes = fixture("routes")["routes"]
    assert routes["undo_mark"]["path"] == "/api/expectations/<id>"
    assert f'data-act="xb-undo-mark" data-id="{s["harbour"]}"' in html
    assert f'data-act="xb-unlink" data-id="{s["jay_on_harbour"]}"' in html
    assert f'data-act="let-go" data-id="{s["harbour"]}"' in html


@needs_node
def test_the_page_draws_the_contract_fixtures_too():
    """The fixtures the client lanes built against draw without a route."""
    for name in ("page", "page_let_go", "page_refund"):
        assert "xb-hl" in draw(fixture(name))


@needs_node
def test_the_click_handlers_post_to_the_contract_urls():
    calls = []
    code = PRELUDE + "\n" + r"""
    const sent = [];
    async function act(method, url) { sent.push([method, url]); return { ok: true, change: 1 }; }
    function rerender() {}
    """ + PAGE_JS + r"""
    (async () => {
      await ACT['xb-undo-mark']({ dataset: { id: '4' } });
      await ACT['xb-unlink']({ dataset: { id: '11' } });
      await ACT['xb-undo-let-go']({ dataset: { id: '4' } });
      console.log(JSON.stringify(sent));
    })();"""
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    calls = json.loads(out.stdout)
    routes = fixture("routes")["routes"]
    want = [(routes["undo_mark"], "4"), (routes["unlink"], "11"), (routes["undo_let_go"], "4")]
    assert calls == [[r["method"], r["path"].replace("<id>", i)] for r, i in want]
