"""The preview: rules and merges (fin-surfaces 50; spec 24, the preview,
cases 15-18 and 19-20 for these changes).

A rule's edit, a rule added, a rule deleted and a merge of merchants each go
through the same preview as a merchant's book or type (fin-surfaces 49):
without `past` and with past rows to change, the route writes nothing and
answers 409 with the preview; `past: apply` writes what the preview counted;
`past: only_new` keeps the past rows' values and marks their fields checked
(a merge still moves them to the new merchant; a deleted rule's rows keep
their labels). A stale token is refused.

A rule change reaches the rows whose winning rule, after the change, differs
from before, or whose label from that rule changes: a rule's priority and
amount limits are honoured, and a row another rule wins is not overwritten.
Rows another merchant holds are named apart (the warning line).

Each case is one request through fin's public route (the Flask test client,
or mcp_tools.call for chat), then what the rows, merchants and rules say
afterwards, values and sources, the preview's count against the rows the
write changed, the change entry and what Undo restores. The database is the
temporary one; every merchant, wording and figure is invented, and no
wording here is one of fin's seeded rules.
"""

import json
import re
import subprocess

import pytest

import app as fin_app
import db
import mcp_tools
import relabel_preview
from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    merchant, statement, type_id,
)
from test_client_seams import APP_JS, NODE, needs_node

CHAT_ACTOR = "chat:sample-subject"


def add_rule(conn, pattern, service_id, *, priority=0, match_type="contains", min_minor=None,
             max_minor=None, book_override=None, type_override=None) -> int:
    rid = conn.execute(
        "INSERT INTO merchant_rules (pattern, service_id, match_type, confidence, priority,"
        " min_amount_minor, max_amount_minor, book_override, type_override_id)"
        " VALUES (?, ?, ?, 'confirmed', ?, ?, ?, ?, ?)",
        (pattern, service_id, match_type, priority, min_minor, max_minor, book_override,
         type_id(conn, type_override) if type_override else None),
    ).lastrowid
    conn.commit()
    db.invalidate_rules_cache()
    return rid


def add_row(conn, statement_id, description, amount_minor=4000, *, service_id=None, book="Household",
            type_name="Travel", sources=("merchant", "merchant"), day="2026-09-10"):
    """A row as fin holds it after the label-source step; a row with no
    merchant has no book, no type and no source."""
    if service_id is None and sources == ("merchant", "merchant"):
        book, type_name = None, None
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id,"
        " service_id, flow_type, book_source, type_source)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'expense', ?, ?)",
        (statement_id, day, description, amount_minor, book,
         type_id(conn, type_name) if type_name else None, service_id,
         sources[0] if book else None, sources[1] if type_name else None),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def held(conn, ids) -> dict:
    """Each row's (service id, book, type id, book source, type source)."""
    marks = ",".join("?" * len(ids))
    return {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT id, service_id, book, type_id, book_source, type_source FROM transactions"
        f" WHERE id IN ({marks})", list(ids))}


def every_row(conn) -> dict:
    return {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT id, book, type_id, service_id, book_source, type_source, flow_type"
        " FROM transactions")}


def every_rule(conn) -> dict:
    return {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT id, pattern, match_type, service_id, priority, min_amount_minor, max_amount_minor,"
        " book_override, type_override_id FROM merchant_rules")}


def every_merchant(conn) -> dict:
    return {r[0]: tuple(r[1:]) for r in conn.execute("SELECT id, name, book, type_id FROM services")}


def book_state(conn) -> tuple:
    return every_row(conn), every_rule(conn), every_merchant(conn)


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def changed(before: dict, after: dict) -> set:
    return {i for i in before if before[i] != after.get(i)}


def previewed(resp) -> dict:
    assert resp.status_code == 409, resp.get_json()
    body = resp.get_json()
    assert "nothing was saved" in body["error"].lower()
    return body["preview"]


def undo(client, resp):
    undone = client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")
    assert undone.status_code == 200, undone.get_json()
    return undone


# --- case 15: a rule's wording edited, reaching another merchant --------------------------


@pytest.fixture
def indigo(conn, statement):
    """IndigoAir's rule INDIGOAIR (priority 1) and Air India's AIR INDIA.
    Four rows now Air India's read "AIR INDIA INDIGO LOUNGE", one row with no
    merchant reads "SAMPLE INDIGO 6E BLR", and three rows are IndigoAir's
    already."""
    travel = "Travel"
    indigo = merchant(conn, "IndigoAir", "Household", travel)
    air_india = merchant(conn, "Air India", "Household", travel)
    rid = add_rule(conn, "INDIGOAIR", indigo, priority=1)
    add_rule(conn, "AIR INDIA", air_india)
    theirs = [add_row(conn, statement, f"AIR INDIA INDIGO LOUNGE {n}", 4200 + n, service_id=air_india,
                      day=f"2026-0{5 + n}-14") for n in range(4)]
    loose = add_row(conn, statement, "SAMPLE INDIGO 6E BLR", 18990, day="2026-05-09")
    own = [add_row(conn, statement, f"INDIGOAIR 312-45{n}", 41000, service_id=indigo, day="2026-09-0{n + 1}")
           for n in range(3)]
    return {"indigo": indigo, "air_india": air_india, "rule": rid, "theirs": theirs, "loose": loose,
            "own": own}


def test_case_15_a_wording_edit_previews_the_five_rows_naming_air_indias_and_writes_nothing(
        client, conn, indigo):
    before, history_before = book_state(conn), entries(client)

    p = previewed(client.put(f"/api/rules/{indigo['rule']}", json={"pattern": "INDIGO"}))

    assert p["subject"]["kind"] == "rule" and p["subject"]["name"] == "INDIGO"
    assert p["subject"]["merchant"] == {"id": indigo["indigo"], "name": "IndigoAir"}
    assert [(c["field"], c["from"], c["to"]) for c in p["change"]] == [("pattern", "INDIGOAIR", "INDIGO")]
    assert (p["count"], p["checked"], p["chosen"]) == (5, 0, 0)
    assert p["others"] == [{"service_id": indigo["air_india"], "name": "Air India", "count": 4}]
    assert p["unheld"] == 1
    assert p["money"] is None and p["months"] is None       # no book moves
    assert [(x["date"], x["now"], x["after"]) for x in p["examples"]] == [
        ("2026-08-14", "Air India · Travel", "IndigoAir · Travel"),
        ("2026-07-14", "Air India · Travel", "IndigoAir · Travel"),
        ("2026-06-14", "Air India · Travel", "IndigoAir · Travel"),
    ]
    assert p["token"]
    assert book_state(conn) == before and entries(client) == history_before


def test_case_15_apply_moves_all_five_in_one_change(client, conn, indigo):
    token = previewed(client.put(f"/api/rules/{indigo['rule']}", json={"pattern": "INDIGO"}))["token"]
    before, history_before = every_row(conn), entries(client)

    resp = client.put(f"/api/rules/{indigo['rule']}", json={"pattern": "INDIGO", "past": "apply", "token": token})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 5
    after = every_row(conn)
    assert changed(before, after) == set(indigo["theirs"]) | {indigo["loose"]}   # the preview's 5
    travel = type_id(conn, "Travel")
    assert set(held(conn, indigo["theirs"] + [indigo["loose"]]).values()) == {
        (indigo["indigo"], "Household", travel, "merchant", "merchant")}
    assert every_rule(conn)[indigo["rule"]][0] == "INDIGO"
    new = entries(client)
    assert len(new) == len(history_before) + 1
    assert new[0]["rows_by_table"] == {"merchant_rules": 1, "transactions": 5}
    assert new[0]["via"] == "app"


def test_case_15_only_new_rows_moves_none_and_marks_their_fields_checked(client, conn, indigo):
    before = every_row(conn)

    resp = client.put(f"/api/rules/{indigo['rule']}", json={"pattern": "INDIGO", "past": "only_new"})

    assert resp.status_code == 200, resp.get_json()
    travel = type_id(conn, "Travel")
    assert set(held(conn, indigo["theirs"]).values()) == {
        (indigo["air_india"], "Household", travel, "checked", "checked")}
    # The row with no merchant has no field to check: it still waits in No type.
    assert held(conn, [indigo["loose"]])[indigo["loose"]] == (None, None, None, None, None)
    assert changed(before, every_row(conn)) == set(indigo["theirs"])
    assert every_rule(conn)[indigo["rule"]][0] == "INDIGO"


def test_case_19_undo_of_a_rule_apply_restores_the_rows_and_the_rule(client, conn, indigo):
    before = book_state(conn)
    resp = client.put(f"/api/rules/{indigo['rule']}", json={"pattern": "INDIGO", "past": "apply"})
    assert resp.status_code == 200, resp.get_json()

    undo(client, resp)

    assert book_state(conn) == before
    # The cache holds the rule as it was again: a new row reads it so.
    assert db.first_rule("SAMPLE INDIGO 6E DEL", conn) is None


def test_case_20_a_rule_preview_goes_stale_when_another_rule_changes(client, conn, indigo):
    token = previewed(client.put(f"/api/rules/{indigo['rule']}", json={"pattern": "INDIGO"}))["token"]
    air_rule = next(i for i, r in every_rule(conn).items() if r[0] == "AIR INDIA")
    assert client.put(f"/api/rules/{air_rule}", json={"priority": 3}).status_code == 200
    before, history_before = book_state(conn), entries(client)

    resp = client.put(f"/api/rules/{indigo['rule']}", json={"pattern": "INDIGO", "past": "apply", "token": token})

    assert resp.status_code == 409 and resp.get_json()["stale"] is True
    assert resp.get_json()["error"] == "The rule INDIGOAIR changed since this preview; look again"
    assert book_state(conn) == before and entries(client) == history_before


# --- a rule edit honours priority and amount limits ----------------------------------------


def test_a_rule_edit_leaves_a_row_a_higher_priority_rule_wins_and_one_outside_its_limits(
        client, conn, statement):
    shop = merchant(conn, "Sample Shop", "Household", "Shopping")
    club = merchant(conn, "Sample Shop Club", "Moom", "Office")
    rid = add_rule(conn, "SAMPLE SHOP", shop, max_minor=10000)
    add_rule(conn, "SAMPLE SHOP VIP", club, priority=5)
    small = add_row(conn, statement, "SAMPLE SHOP 1", 5000, service_id=shop, type_name="Shopping")
    big = add_row(conn, statement, "SAMPLE SHOP 2", 50000, service_id=shop, type_name="Shopping")
    vip = add_row(conn, statement, "SAMPLE SHOP VIP 3", 5000, service_id=club, book="Moom", type_name="Office")
    groceries = type_id(conn, "Groceries")

    p = previewed(client.put(f"/api/rules/{rid}", json={"type_override_id": groceries}))
    assert p["count"] == 1 and p["others"] == []
    assert [(x["now"], x["after"]) for x in p["examples"]] == [
        ("Sample Shop · Shopping", "Sample Shop · Groceries")]
    before = every_row(conn)

    resp = client.put(f"/api/rules/{rid}", json={"type_override_id": groceries, "past": "apply",
                                                 "token": p["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert changed(before, every_row(conn)) == {small}
    assert held(conn, [small])[small] == (shop, "Household", groceries, "merchant", "rule")
    assert held(conn, [big])[big][2] == type_id(conn, "Shopping")
    assert held(conn, [vip])[vip][:3] == (club, "Moom", type_id(conn, "Office"))


def test_a_rule_edit_whose_rows_are_all_chosen_saves_without_a_preview(client, conn, statement):
    shop = merchant(conn, "Sample Kept Shop", "Household", "Shopping")
    rid = add_rule(conn, "SAMPLE KEPT SHOP", shop)
    kept = add_row(conn, statement, "SAMPLE KEPT SHOP 1", service_id=shop, type_name="Shopping",
                   sources=("chosen", "chosen"))
    before = held(conn, [kept])

    resp = client.put(f"/api/rules/{rid}", json={"type_override_id": type_id(conn, "Groceries")})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 0
    assert held(conn, [kept]) == before


# --- one rule order: the rule's preview and the teach's rank rules alike ----------------------


def test_on_a_tie_the_rule_preview_and_the_teach_preview_pick_the_same_winner(client, conn, statement):
    """Two rules of one priority and one length both match a row: the older
    (lower id) wins it, in db.match_order. A rule's edit (fin-surfaces 50)
    and a wording taught from the Queue (fin-surfaces 53) both read that
    order, so both count the row for the older rule's wording and neither
    counts it for the younger's."""
    air_india = merchant(conn, "Sample Tie Air", "Moom", "Transport")
    lounge = merchant(conn, "Sample Tie Lounge", "Moom", "Transport")
    indigo = merchant(conn, "Sample Tie Indigo", "Household", "Travel")
    older = add_rule(conn, "AIRINDIGO", air_india)
    younger = add_rule(conn, "INDIGOAIR", lounge)
    assert older < younger
    tied = add_row(conn, statement, "AIRINDIGO INDIGOAIR LOUNGE", 4200, service_id=air_india,
                   book="Moom", type_name="Transport")
    waiting = add_row(conn, statement, "SAMPLE AIRINDIGO PNR", 9900, day="2026-09-20")

    def taught(pattern):
        resp = client.get("/api/rules/reach", query_string={
            "pattern": pattern, "match_type": "contains", "tx_id": waiting, "service_id": indigo})
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()

    # The older rule's wording: both previews take the tied row from its merchant.
    rule_p = previewed(client.put(f"/api/rules/{older}", json={"service_id": indigo}))
    teach_p = previewed(client.post("/api/transactions/resolve", json={
        "tx_id": waiting, "service_id": indigo, "type_id": type_id(conn, "Travel"), "book": "Household",
        "apply_scope": "service_default", "pattern": "AIRINDIGO", "match_type": "contains"}))
    named = [{"service_id": air_india, "name": "Sample Tie Air", "count": 1}]
    assert rule_p["others"] == named and teach_p["others"] == named
    assert taught("AIRINDIGO")["others"] == named
    assert [x["id"] for x in rule_p["examples"]] == [waiting, tied]   # the rule also labels the waiting row
    assert [x["id"] for x in teach_p["examples"]] == [tied]           # the teach previews only rows it takes

    # The younger rule's wording: neither reaches the row the older rule wins.
    assert taught("INDIGOAIR")["count"] == 0
    before = every_row(conn)
    resp = client.put(f"/api/rules/{younger}", json={"service_id": indigo})
    assert resp.status_code == 200, resp.get_json()          # no past row to change: saved, no preview
    assert resp.get_json()["recategorized"] == 0 and every_row(conn) == before


# --- case 16: a rule added ------------------------------------------------------------------


@pytest.fixture
def rides(conn, statement):
    """Seven rows with no merchant and two of Grab's, all reading TAXI."""
    taxi = merchant(conn, "Sample Taxi", "Household", "Transport")
    grab = merchant(conn, "Grab", "Household", "Transport")
    add_rule(conn, "SAMPLE GRAB", grab)
    loose = [add_row(conn, statement, f"SAMPLE TAXI SG {n}", 1500 + n, day=f"2026-09-{10 + n}")
             for n in range(7)]
    grabs = [add_row(conn, statement, f"SAMPLE GRAB TAXI {n}", 2000, service_id=grab, type_name="Transport",
                     day=f"2026-08-0{n + 1}") for n in range(2)]
    return {"taxi": taxi, "grab": grab, "loose": loose, "grabs": grabs}


def test_case_16_a_rule_added_previews_nine_rows_and_writes_nothing(client, conn, rides):
    before, history_before = book_state(conn), entries(client)
    body = {"pattern": "TAXI", "service_id": rides["taxi"], "priority": 1}

    p = previewed(client.post("/api/rules", json=body))

    assert p["subject"]["kind"] == "rule" and p["subject"]["id"] is None
    assert [(c["field"], c["from"], c["to"]) for c in p["change"]] == [("rule", None, "TAXI")]
    assert (p["count"], p["unheld"]) == (9, 7)
    assert p["others"] == [{"service_id": rides["grab"], "name": "Grab", "count": 2}]
    assert p["examples"][0]["now"] == "no merchant" and p["examples"][0]["after"] == "Sample Taxi · Transport"
    assert book_state(conn) == before and entries(client) == history_before


def test_case_16_apply_labels_the_nine_and_undo_takes_the_rule_away(client, conn, rides):
    body = {"pattern": "TAXI", "service_id": rides["taxi"], "priority": 1}
    token = previewed(client.post("/api/rules", json=body))["token"]
    before_state = book_state(conn)

    resp = client.post("/api/rules", json={**body, "past": "apply", "token": token})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 9
    transport = type_id(conn, "Transport")
    assert set(held(conn, rides["loose"] + rides["grabs"]).values()) == {
        (rides["taxi"], "Household", transport, "merchant", "merchant")}
    assert entries(client)[0]["rows_by_table"] == {"merchant_rules": 1, "transactions": 9}

    undo(client, resp)
    assert book_state(conn) == before_state


def test_a_rule_added_leaves_a_row_named_by_hand_with_no_book_or_type(client, conn, rides, statement):
    """A row the operator named on the review list (a movement) has no book
    or type, each chosen empty (personal-ledger 51): a rule never takes it."""
    named = conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, book_source, type_source,"
        " flow_type, flow_type_manual) VALUES (?, '2026-09-01', 'SAMPLE TAXI FLEET TRANSFER', 90000,"
        " 'chosen', 'chosen', 'movement', 1)",
        (statement,)).lastrowid
    conn.commit()
    body = {"pattern": "TAXI", "service_id": rides["taxi"], "priority": 1}

    p = previewed(client.post("/api/rules", json=body))
    assert p["count"] == 9
    before = every_row(conn)[named]
    assert client.post("/api/rules", json={**body, "past": "apply", "token": p["token"]}).status_code == 200
    assert every_row(conn)[named] == before


def test_a_rule_added_that_reaches_no_row_saves_as_today(client, conn, rides):
    resp = client.post("/api/rules", json={"pattern": "SAMPLE NOWHERE", "service_id": rides["taxi"]})

    assert resp.status_code == 200, resp.get_json()
    assert any(r[0] == "SAMPLE NOWHERE" for r in every_rule(conn).values())


# --- case 17: a rule deleted ----------------------------------------------------------------


@pytest.fixture
def cafe(conn, statement):
    """SAMPLE CAFE (priority 2) labels nine rows: six read "SAMPLE CAFE
    ORCHARD", which Sample Orchard's CAFE ORCHARD gives once it is gone, and
    three read "SAMPLE CAFE BUGIS", which no other rule knows."""
    cafe = merchant(conn, "Sample Cafe", "Household", "Dining")
    orchard = merchant(conn, "Sample Orchard", "Moom", "Office")
    rid = add_rule(conn, "SAMPLE CAFE", cafe, priority=2)
    add_rule(conn, "CAFE ORCHARD", orchard)
    to_orchard = [add_row(conn, statement, f"SAMPLE CAFE ORCHARD {n}", 900, service_id=cafe, type_name="Dining",
                          day=f"2026-07-{10 + n}") for n in range(6)]
    to_none = [add_row(conn, statement, f"SAMPLE CAFE BUGIS {n}", 700, service_id=cafe, type_name="Dining",
                       day=f"2026-06-{10 + n}") for n in range(3)]
    return {"cafe": cafe, "orchard": orchard, "rule": rid, "to_orchard": to_orchard, "to_none": to_none}


def test_case_17_a_rule_deleted_previews_the_nine_rows_it_labels(client, conn, cafe):
    before, history_before = book_state(conn), entries(client)

    p = previewed(client.delete(f"/api/rules/{cafe['rule']}"))

    assert p["subject"]["kind"] == "rule" and p["subject"]["name"] == "SAMPLE CAFE"
    assert [(c["field"], c["from"], c["to"]) for c in p["change"]] == [("rule", "SAMPLE CAFE", None)]
    assert (p["count"], p["labelled"]) == (9, 9)
    assert p["money"] == [{"from": "Household", "to": "Moom", "amount_minor": 5400}]
    assert p["months"] == {"count": 1, "first": "2026-07", "last": "2026-07"}
    assert book_state(conn) == before and entries(client) == history_before


def test_case_17_apply_relabels_by_the_rules_left_or_leaves_no_merchant(client, conn, cafe):
    token = previewed(client.delete(f"/api/rules/{cafe['rule']}"))["token"]
    before = every_row(conn)

    resp = client.delete(f"/api/rules/{cafe['rule']}", json={"past": "apply", "token": token})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 9
    assert changed(before, every_row(conn)) == set(cafe["to_orchard"] + cafe["to_none"])
    assert set(held(conn, cafe["to_orchard"]).values()) == {
        (cafe["orchard"], "Moom", type_id(conn, "Office"), "merchant", "merchant")}
    # No rule left: no merchant, waiting in the Queue for a type.
    assert set(held(conn, cafe["to_none"]).values()) == {(None, None, None, None, None)}
    assert cafe["rule"] not in every_rule(conn)
    assert entries(client)[0]["rows_by_table"] == {"merchant_rules": 1, "transactions": 9}


def test_case_17_only_new_rows_keeps_their_labels_checked(client, conn, cafe):
    resp = client.delete(f"/api/rules/{cafe['rule']}", json={"past": "only_new"})

    assert resp.status_code == 200, resp.get_json()
    assert set(held(conn, cafe["to_orchard"] + cafe["to_none"]).values()) == {
        (cafe["cafe"], "Household", type_id(conn, "Dining"), "checked", "checked")}
    assert cafe["rule"] not in every_rule(conn)


def test_case_19_undo_of_a_delete_apply_restores_the_rule_and_the_rows(client, conn, cafe):
    before = book_state(conn)
    resp = client.delete(f"/api/rules/{cafe['rule']}", json={"past": "apply"})
    assert resp.status_code == 200, resp.get_json()

    undo(client, resp)

    assert book_state(conn) == before


def test_deleting_a_rule_that_is_no_longer_there_is_refused_and_writes_nothing(client, conn, cafe):
    before, history_before = book_state(conn), entries(client)

    resp = client.delete("/api/rules/99999", json={"past": "apply"})

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "This rule is no longer in fin"
    assert book_state(conn) == before and entries(client) == history_before


# --- case 18: merging merchants -------------------------------------------------------------


@pytest.fixture
def grab_food(conn, statement):
    """Grab Food (Household · Dining) with its rule, 30 rows from the
    merchant and 2 chosen, merged into Grab (Household · Transport)."""
    grab = merchant(conn, "Grab", "Household", "Transport")
    food = merchant(conn, "Grab Food", "Household", "Dining")
    rid = add_rule(conn, "SAMPLE GRABFOOD", food)
    plain = [add_row(conn, statement, f"SAMPLE GRABFOOD {n}", 1200 + n, service_id=food, type_name="Dining",
                     day=f"2026-{1 + n % 9:02d}-{1 + n % 27:02d}") for n in range(30)]
    chosen = [add_row(conn, statement, f"SAMPLE GRABFOOD KEPT {n}", 900, service_id=food, type_name="Dining",
                      sources=("chosen", "chosen")) for n in range(2)]
    return {"grab": grab, "food": food, "rule": rid, "plain": plain, "chosen": chosen}


def test_case_18_a_merge_previews_thirty_rows_with_grabs_labels_and_writes_nothing(client, conn, grab_food):
    before, history_before = book_state(conn), entries(client)

    p = previewed(client.post(f"/api/services/{grab_food['food']}/merge", json={"target_id": grab_food["grab"]}))

    assert p["subject"] == {"kind": "merge", "id": grab_food["food"], "name": "Grab Food",
                            "into": {"id": grab_food["grab"], "name": "Grab"}}
    assert (p["count"], p["checked"], p["chosen"]) == (30, 0, 2)
    assert {(x["now"], x["after"]) for x in p["examples"]} == {("Household · Dining", "Household · Transport")}
    assert p["money"] is None
    assert book_state(conn) == before and entries(client) == history_before


def test_case_18_apply_gives_the_thirty_grabs_labels(client, conn, grab_food):
    token = previewed(client.post(f"/api/services/{grab_food['food']}/merge",
                                  json={"target_id": grab_food["grab"]}))["token"]
    before = every_row(conn)

    resp = client.post(f"/api/services/{grab_food['food']}/merge",
                       json={"target_id": grab_food["grab"], "past": "apply", "token": token})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["merged"]["relabelled"] == 30
    transport, dining = type_id(conn, "Transport"), type_id(conn, "Dining")
    assert set(held(conn, grab_food["plain"]).values()) == {
        (grab_food["grab"], "Household", transport, "merchant", "merchant")}
    assert set(held(conn, grab_food["chosen"]).values()) == {
        (grab_food["grab"], "Household", dining, "chosen", "chosen")}
    assert grab_food["food"] not in every_merchant(conn)
    assert every_rule(conn)[grab_food["rule"]][2] == grab_food["grab"]
    # Every one of the 32 moved; the 30 the preview counted took Grab's type.
    assert len(changed(before, every_row(conn))) == 32
    assert entries(client)[0]["rows_by_table"] == {"merchant_rules": 1, "services": 1, "transactions": 32}


def test_case_18_only_new_rows_moves_all_32_and_keeps_their_labels_the_30_checked(client, conn, grab_food):
    resp = client.post(f"/api/services/{grab_food['food']}/merge",
                       json={"target_id": grab_food["grab"], "past": "only_new"})

    assert resp.status_code == 200, resp.get_json()
    dining = type_id(conn, "Dining")
    assert set(held(conn, grab_food["plain"]).values()) == {
        (grab_food["grab"], "Household", dining, "checked", "checked")}
    assert set(held(conn, grab_food["chosen"]).values()) == {
        (grab_food["grab"], "Household", dining, "chosen", "chosen")}
    assert grab_food["food"] not in every_merchant(conn)


def test_a_merge_counts_a_row_with_no_type_and_only_new_rows_leaves_it_waiting(client, conn, grab_food, statement):
    """A Grab Food row waiting for a type: Apply gives it Grab's type, so
    the preview counts it; Only new rows moves it and leaves its labels."""
    waiting = add_row(conn, statement, "SAMPLE GRABFOOD 2210", 700, service_id=grab_food["food"],
                      book=None, type_name=None)
    p = previewed(client.post(f"/api/services/{grab_food['food']}/merge", json={"target_id": grab_food["grab"]}))
    assert p["count"] == 31

    resp = client.post(f"/api/services/{grab_food['food']}/merge",
                       json={"target_id": grab_food["grab"], "past": "only_new", "token": p["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert held(conn, [waiting])[waiting] == (grab_food["grab"], None, None, None, None)


def test_a_merge_apply_relabels_the_rows_its_preview_counted(client, conn, grab_food, statement):
    waiting = add_row(conn, statement, "SAMPLE GRABFOOD 2210", 700, service_id=grab_food["food"],
                      book=None, type_name=None)
    p = previewed(client.post(f"/api/services/{grab_food['food']}/merge", json={"target_id": grab_food["grab"]}))

    resp = client.post(f"/api/services/{grab_food['food']}/merge",
                       json={"target_id": grab_food["grab"], "past": "apply", "token": p["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["merged"]["relabelled"] == p["count"] == 31
    # An empty book reads as Household, Grab's own: only the type is given.
    assert held(conn, [waiting])[waiting] == (grab_food["grab"], None, type_id(conn, "Transport"), None, "merchant")


def test_case_19_undo_of_a_merge_restores_the_merchant_its_rule_and_rows(client, conn, grab_food):
    before = book_state(conn)
    resp = client.post(f"/api/services/{grab_food['food']}/merge",
                       json={"target_id": grab_food["grab"], "past": "apply"})
    assert resp.status_code == 200, resp.get_json()

    undo(client, resp)

    assert book_state(conn) == before


def test_case_20_a_merge_preview_goes_stale_when_the_target_changes(client, conn, grab_food):
    token = previewed(client.post(f"/api/services/{grab_food['food']}/merge",
                                  json={"target_id": grab_food["grab"]}))["token"]
    assert client.put(f"/api/services/{grab_food['grab']}", json={"type_id": type_id(conn, "Travel")}).status_code == 200
    before = book_state(conn)

    resp = client.post(f"/api/services/{grab_food['food']}/merge",
                       json={"target_id": grab_food["grab"], "past": "apply", "token": token})

    assert resp.status_code == 409 and resp.get_json()["stale"] is True
    assert resp.get_json()["error"] == "Grab Food changed since this preview; look again"
    assert book_state(conn) == before


# --- refusals write nothing and say why ----------------------------------------------------


@pytest.mark.parametrize("ask", ["edit", "add", "delete", "merge"])
def test_an_answer_that_is_not_apply_or_only_new_is_refused(client, conn, indigo, grab_food, ask):
    before, history_before = book_state(conn), entries(client)
    resp = {
        "edit": lambda: client.put(f"/api/rules/{indigo['rule']}", json={"pattern": "INDIGO", "past": "yes"}),
        "add": lambda: client.post("/api/rules", json={"pattern": "INDIGO", "service_id": indigo["indigo"],
                                                       "past": "yes"}),
        "delete": lambda: client.delete(f"/api/rules/{indigo['rule']}", json={"past": "yes"}),
        "merge": lambda: client.post(f"/api/services/{grab_food['food']}/merge",
                                     json={"target_id": grab_food["grab"], "past": "yes"}),
    }[ask]()

    assert resp.status_code == 400
    assert "apply" in resp.get_json()["error"] and "only_new" in resp.get_json()["error"]
    assert book_state(conn) == before and entries(client) == history_before


def test_a_rule_for_a_merchant_fin_does_not_hold_is_refused(client, conn, indigo):
    before = book_state(conn)

    added = client.post("/api/rules", json={"pattern": "SAMPLE ELSEWHERE", "service_id": 99999})
    edited = client.put(f"/api/rules/{indigo['rule']}", json={"service_id": 99999})

    assert (added.status_code, edited.status_code) == (400, 400)
    assert added.get_json()["error"] == edited.get_json()["error"] == "This merchant is no longer in fin"
    assert book_state(conn) == before


def test_an_internal_error_in_the_rule_preview_is_never_echoed_to_the_client(client, conn, indigo, monkeypatch):
    """The house rule: no raw exception text to a client. A KeyError (a
    LookupError) inside the preview is not one of its two refusals: it falls
    to fin's generic answer, and nothing is written."""
    def broken(_conn):
        raise KeyError("internal_column_name")

    monkeypatch.setattr(relabel_preview, "_names", broken)
    monkeypatch.setitem(fin_app.app.config, "PROPAGATE_EXCEPTIONS", False)
    before = book_state(conn)

    added = client.post("/api/rules", json={"pattern": "SAMPLE ELSEWHERE", "service_id": indigo["indigo"]})
    edited = client.put(f"/api/rules/{indigo['rule']}", json={"pattern": "INDIGO"})

    for resp in (added, edited):
        assert resp.status_code == 500
        assert "internal_column_name" not in resp.get_data(as_text=True)
    assert book_state(conn) == before


# --- chat: refused writes nothing; a write with no rows to change is one entry -------------


def test_chat_change_rule_that_would_change_past_rows_writes_nothing_and_says_why(client, conn, indigo):
    before, history_before = book_state(conn), entries(client)

    got = mcp_tools.call("change_rule", {"rule_id": indigo["rule"], "pattern": "INDIGO"}, actor=CHAT_ACTOR)

    assert got["ok"] is False and "5 past rows" in got["error"]
    assert book_state(conn) == before and entries(client) == history_before


def test_chat_merge_merchants_that_would_change_past_rows_writes_nothing(client, conn, grab_food):
    before = book_state(conn)

    got = mcp_tools.call("merge_merchants", {"source_id": grab_food["food"], "target_id": grab_food["grab"]},
                         actor=CHAT_ACTOR)

    assert got["ok"] is False and "30 past rows" in got["error"]
    assert book_state(conn) == before


def test_chat_add_rule_reaching_no_row_is_one_entry_via_chat(client, conn, rides):
    got = mcp_tools.call("add_rule", {"pattern": "SAMPLE NOWHERE", "service_id": rides["taxi"]},
                         actor=CHAT_ACTOR)

    assert got["ok"] is True, got
    entry = entries(client)[0]
    assert (entry["via"], entry["actor"]) == ("chat", CHAT_ACTOR)


# --- the screens: the rule sheet, its delete and the merge go through the preview -----------


def _function(name):
    found = (re.search(rf"^(?:async )?function {name}\(.*\}}$", APP_JS, re.M)
             or re.search(rf"^(?:async )?function {name}\(.*?^\}}$", APP_JS, re.M | re.S))
    assert found, f"{name} not found in static/app.js"
    return found.group(0)


def _const(name):
    found = re.search(rf"^const {name} = .*?;$", APP_JS, re.M | re.S)
    assert found, f"{name} not found in static/app.js"
    return found.group(0)


def _node(code):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30,
                         check=True, encoding="utf-8")
    return json.loads(out.stdout)


PRELUDE = r"""
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const icon = n => '';
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const LONG_MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
function plural(n, one, many) { return `${n} ${n === 1 ? one : (many || one + 's')}`; }
function money(minor, cur = 'SGD') { const s = {SGD: 'S$', INR: '₹'}[cur]; return `${s} ${(Math.abs(minor) / 100).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`; }
function day(iso) { const [y, m, d] = iso.split('-').map(Number); return `${d} ${MONTHS[m - 1]} ${y}`; }
"""

EXAMPLES = [
    {"id": 1, "date": "2026-08-14", "amount_minor": 4200, "currency": "SGD", "now": "Air India · Travel",
     "after": "IndigoAir · Travel", "source": "merchant"},
    {"id": 2, "date": "2026-05-09", "amount_minor": 18990, "currency": "SGD", "now": "no merchant",
     "after": "IndigoAir · Travel", "source": None},
]
RULE_EDIT = {
    "subject": {"kind": "rule", "id": 7, "name": "INDIGO", "verb": "edit",
                "merchant": {"id": 4, "name": "IndigoAir"}},
    "change": [{"field": "pattern", "label": "Wording", "from": "INDIGOAIR", "to": "INDIGO"}],
    "count": 5, "checked": 0, "chosen": 0, "money": None, "left_out": [], "months": None,
    "examples": EXAMPLES, "others": [{"service_id": 5, "name": "Air India", "count": 4}], "unheld": 1,
    "labelled": 3, "token": "t0",
}


def drawer(sample) -> dict:
    return _node("\n".join([PRELUDE, _function("monthName"), _function("bandedTable"), _const("PREVIEW_FIELD"),
                            _const("PREVIEW_TITLE"), _function("keptWords"), _function("flowsLine"), _function("previewParts"),
                            f"console.log(JSON.stringify(previewParts({json.dumps(sample)})));"]))


def text_of(html) -> str:
    return re.sub(r"<[^>]+>", "", html)


@needs_node
def test_the_rule_drawer_reads_as_mocked_with_the_warning_line():
    got = drawer(RULE_EDIT)
    text = text_of(got["body"])
    assert got["title"] == "Save the rule INDIGO?" and got["eyebrow"] == "Rule"
    assert "Wording: INDIGOAIR → INDIGO" in text
    assert "This changes 5 past rows" in text
    assert "4 rows now Air India’s would move to IndigoAir." in text
    assert 'class="warnline"' in got["body"]           # the mockup's warning line (21, screen 2)
    assert "1 row with no merchant yet would become IndigoAir." in text
    assert "Air India · Travel → IndigoAir · Travel" in text
    assert "See all" not in text                       # the rows sit with several merchants
    assert re.findall(r">([^<>]+)</button>", got["foot"]) == ["Cancel", "Only new rows", "Apply to 5 past rows"]


@needs_node
def test_the_rows_of_several_merchants_are_one_warning_line_for_a_rule_and_a_taught_wording():
    """The rule's preview (50) and the taught wording's (53) draw the rows
    another merchant holds one way: one warning line naming each merchant,
    then where the rows would move (the rule's merchant, or the merchant
    taught). See all stays for a held merchant taught a new default."""
    others = [{"service_id": 5, "name": "Air India", "count": 4}, {"service_id": 6, "name": "Vistara", "count": 1}]
    rule = drawer({**RULE_EDIT, "others": others})
    taught = drawer({**RULE_EDIT, "subject": {"kind": "merchant", "id": 4, "name": "IndigoAir"}, "others": others,
                     "change": [{"field": "book", "from": "Household", "to": "Moom"},
                                {"field": "wording", "from": None, "to": "INDIGO"}]})
    line = "4 rows now Air India’s, 1 row now Vistara’s would move to IndigoAir."
    for got in (rule, taught):
        assert got["body"].count('class="warnline"') == 1 and line in text_of(got["body"])
    assert taught["title"] == "Change IndigoAir’s book and wording?"
    assert "Recognises: INDIGO" in text_of(taught["body"]) and "See all 5 rows ›" in text_of(taught["body"])


@needs_node
def test_the_rule_drawer_for_an_add_and_a_delete():
    added = drawer({**RULE_EDIT, "subject": {**RULE_EDIT["subject"], "id": None, "verb": "add"},
                    "change": [{"field": "rule", "label": "New rule", "from": None, "to": "INDIGO"}]})
    assert added["title"] == "Add the rule INDIGO?"
    assert "New rule: INDIGO" in text_of(added["body"])
    deleted = drawer({**RULE_EDIT, "subject": {**RULE_EDIT["subject"], "verb": "delete"}, "others": [],
                      "unheld": 0, "labelled": 9, "count": 9,
                      "change": [{"field": "rule", "label": "Rule", "from": "INDIGO", "to": None}]})
    text = text_of(deleted["body"])
    assert deleted["title"] == "Delete the rule INDIGO?"
    assert "9 rows are labelled by this rule" in text and "warn" not in deleted["body"]


@needs_node
def test_the_merge_drawer_names_both_merchants():
    got = drawer({**RULE_EDIT, "subject": {"kind": "merge", "id": 8, "name": "Grab Food", "into": {"id": 3, "name": "Grab"}},
                  "change": [{"field": "book", "from": "Household", "to": "Household"},
                             {"field": "type_id", "from": "Dining", "to": "Transport"}],
                  "count": 30, "chosen": 2, "others": [], "unheld": 0})
    text = text_of(got["body"])
    assert got["title"] == "Merge Grab Food into Grab?" and got["eyebrow"] == "Merge"
    assert "Merchant: Grab Food → Grab" in text and "Type: Dining → Transport" in text
    assert "Book:" not in text                         # a book that stays is not a change line
    assert "2 you chose stay as they are" in text and "See all 30 rows ›" in text


def test_the_rule_sheet_its_delete_and_the_merge_go_through_the_preview():
    def act(name):
        start = APP_JS.index(f"ACT['{name}'] =")
        return APP_JS[start:APP_JS.index("\nACT", start + 1)]
    assert "sendPreviewed(el.dataset.id ? 'PUT' : 'POST'" in act("rule-save")
    assert "sendPreviewed('DELETE', `/api/rules/" in act("rule-delete")
    assert "sendPreviewed('POST', `/api/services/${el.dataset.id}/merge`" in act("merchant-merge")
    assert "Re-run every rule to apply a change" not in APP_JS


@needs_node
def test_a_merge_from_the_merged_merchants_page_lands_on_the_one_it_merged_into():
    """Merging from the merged-away merchant's own page goes to the page of
    the merchant it merged into: its own page is gone, and drawing it again
    asks the server for a merchant no longer there (a 404 in the console,
    build rule 21). From anywhere else the page is drawn again."""
    code = "\n".join([r"""
let drawn = 0;
const location = { hash: '' };
function rerender() { drawn += 1; }
""", _function("afterMerge"), r"""
location.hash = '#/books/merchant/182';
afterMerge(182, 171);
const fromItsPage = [location.hash, drawn];
location.hash = '#/books/lists/merchants';
afterMerge(182, 171);
console.log(JSON.stringify({ fromItsPage, fromLists: [location.hash, drawn] }));
"""])
    got = _node(code)
    assert got["fromItsPage"] == ["#/books/merchant/171", 0]
    assert got["fromLists"] == ["#/books/lists/merchants", 1]
    start = APP_JS.index("ACT['merchant-merge'] =")
    merge = APP_JS[start:APP_JS.index("\n};", start) + 3]       # the handler alone
    assert "afterMerge(" in merge and "rerender()" not in merge
