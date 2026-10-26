"""fin-redesign 05: book and type only on spending and refund rows (case 14).

Book says whose spending a row is and type what kind; a row that is neither
spending nor a refund is no one's spending. So every writer that is not the
operator's hand leaves both off such a row: the import, a teach (its own row,
its backfill, the rows a taught wording takes), a chat plan, a merchant's
change, a rule's change and the drift. The merchant link stays on every
flow. A row already in the book keeps what it holds until a correction
clears it (the four Moom salary credits keep theirs until the item-4
cleanup); a write that moves a row's flow out of spending empties its book
and type in the same change, as the hand label does, and its preview says
so wherever it lists the flow move.

Rows reach the book through fin's routes, as the operator's do; chat through
mcp_tools.call. Every name, wording and figure is invented. The one older
row is written as a backup holds it (book_with_an_older_credit).
"""

import pytest

import db
from test_client_seams import needs_node
from test_movements_and_review import (  # noqa: F401 (ledger is a fixture)
    BANK, CARD, bring_in, ledger, make_account, row, spending_type, upload,
)
from test_preview_rules_and_merges import _function, _node
from test_rule_flow_every_path import (  # noqa: F401 (finance, fees and instalment are fixtures)
    FINANCE, LOAN, agreement, call, every_row, fees, finance, instalment, lines, what,
)
from test_rule_keeps_review_hold import add_rule
from test_rule_states_flow import as_movement, car  # noqa: F401 (car is a fixture)
import test_rule_states_flow as states

HEALTH = "Sample Health Co"
SALARY = "SAMPLE HEALTH CO SALARY"
NO_LABEL = "no book or type"


def stored(description: str) -> dict:
    """The row's flow, merchant and label as the book stores them."""
    conn = db.get_connection()
    try:
        found = conn.execute(
            "SELECT id, flow_type, other_side_id, service_id, book, type_id, book_source, type_source"
            " FROM transactions WHERE description = ?", (description,)).fetchall()
    finally:
        conn.close()
    assert len(found) == 1, description
    return dict(found[0])


def label_of(description: str) -> tuple:
    held = stored(description)
    return held["book"], held["type_id"], held["book_source"], held["type_source"]


# --- case 14: a salary credit from the company's merchant -----------------------------------


@pytest.fixture
def health(client, ledger) -> dict:
    """The company's merchant, in the company's book with a spending type,
    and the rule that knows its wording; a card fee it charged (spending)."""
    resp = client.post("/api/services", json={"name": HEALTH, "book": "Moom",
                                              "type_id": spending_type(client, "People")})
    assert resp.status_code == 200, resp.get_json()
    svc = resp.get_json()["id"]
    add_rule(client, "SAMPLE HEALTH CO", svc)
    bring_in(client, BANK, [(2, "SAMPLE HEALTH CO CARD FEE", 12.00)])
    return {"svc": svc, "people": spending_type(client, "People")}


@pytest.fixture
def book_with_an_older_credit(client, health) -> dict:
    """A salary credit as the book before this change holds one: income,
    carrying its merchant's book and type, from the merchant."""
    bring_in(client, BANK, [(1, f"{SALARY} JUL", -14125.00)])
    conn = db.get_connection()
    conn.execute("UPDATE transactions SET book = 'Moom', type_id = ? WHERE description = ?",
                 (health["people"], f"{SALARY} JUL"))
    conn.commit()
    conn.close()
    assert stored(f"{SALARY} JUL")["flow_type"] == "income"
    return health


def test_case_14_a_new_salary_credit_comes_in_with_its_merchant_and_no_book_or_type(
        client, book_with_an_older_credit):
    preview = upload(client, BANK, [(25, f"{SALARY} AUG", -14125.00)])
    shown = preview["groups"][0]["transactions"][0]
    assert (shown["flow_type"], shown["service_id"]) == ("income", book_with_an_older_credit["svc"])
    assert (shown["book"], shown["type_id"], shown["type_name"], shown["status"]) == (None, None, None, "income")

    resp = client.post("/api/import/confirm", json={"import_id": preview["import_id"],
                                                    "groups": preview["groups"]})

    assert resp.status_code == 200, resp.get_json()
    new = stored(f"{SALARY} AUG")
    assert (new["flow_type"], new["service_id"]) == ("income", book_with_an_older_credit["svc"])
    assert label_of(f"{SALARY} AUG") == (None, None, None, None)
    # The older credit keeps its label until the correction clears it.
    assert label_of(f"{SALARY} JUL")[:2] == ("Moom", book_with_an_older_credit["people"])


def test_a_confirm_that_brings_a_book_and_type_for_an_income_row_stores_neither(client, health):
    preview = upload(client, BANK, [(25, f"{SALARY} AUG", -14125.00)])
    preview["groups"][0]["transactions"][0].update(book="Moom", type_id=health["people"])

    resp = client.post("/api/import/confirm", json={"import_id": preview["import_id"],
                                                    "groups": preview["groups"]})

    assert resp.status_code == 200, resp.get_json()
    assert label_of(f"{SALARY} AUG") == (None, None, None, None)
    assert stored(f"{SALARY} AUG")["service_id"] == health["svc"]


def test_case_14_the_drift_leaves_an_existing_credit_whose_label_differs_from_its_rule(
        client, book_with_an_older_credit):
    rent = spending_type(client, "Rent")
    conn = db.get_connection()
    conn.execute("UPDATE transactions SET type_id = ? WHERE description = ?", (rent, f"{SALARY} JUL"))
    conn.commit()
    conn.close()

    assert client.get("/api/rules/drift").get_json()["count"] == 0
    resp = client.post("/api/rules/drift", json={"past": "apply"})

    assert resp.status_code == 200, resp.get_json()
    assert label_of(f"{SALARY} JUL")[:2] == ("Moom", rent)


def test_case_14_a_merchant_change_and_the_drift_leave_an_existing_credit_as_it_is(
        client, book_with_an_older_credit):
    svc = book_with_an_older_credit["svc"]
    services = spending_type(client, "Professional services")

    asked = client.put(f"/api/services/{svc}", json={"type_id": services})

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    # Only the card fee, the merchant's one spending row, is the change's to relabel.
    assert preview["count"] == 1
    assert [x["id"] for x in preview["examples"]] == [stored("SAMPLE HEALTH CO CARD FEE")["id"]]
    resp = client.put(f"/api/services/{svc}", json={"type_id": services, "past": "apply",
                                                     "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 1
    assert label_of("SAMPLE HEALTH CO CARD FEE")[1] == services
    assert label_of(f"{SALARY} JUL")[:2] == ("Moom", book_with_an_older_credit["people"])

    # Its type now differs from what its rule gives: still not the drift's.
    drift = client.get("/api/rules/drift").get_json()
    assert drift["count"] == 0
    assert client.post("/api/rules/drift", json={"past": "apply"}).status_code == 200
    assert label_of(f"{SALARY} JUL")[:2] == ("Moom", book_with_an_older_credit["people"])


def test_a_rule_added_over_an_income_row_gives_it_the_merchant_and_no_label(client, ledger):
    bring_in(client, BANK, [(25, "SAMPLE LETTING AGENT RENT IN", -3200.00)])
    assert stored("SAMPLE LETTING AGENT RENT IN")["flow_type"] == "income"
    svc = client.post("/api/services", json={"name": "Sample Letting Agent", "book": "Household",
                                             "type_id": spending_type(client, "Rent")}).get_json()["id"]

    asked = client.post("/api/rules", json={"pattern": "SAMPLE LETTING AGENT", "service_id": svc})

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    assert preview["count"] == 1
    assert preview["examples"][0]["after"] == "Sample Letting Agent · no type"
    resp = client.post("/api/rules", json={"pattern": "SAMPLE LETTING AGENT", "service_id": svc,
                                           "past": "apply", "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert stored("SAMPLE LETTING AGENT RENT IN")["service_id"] == svc
    assert label_of("SAMPLE LETTING AGENT RENT IN") == (None, None, None, None)


# --- a rule that moves spending rows out of spending ----------------------------------------


def test_a_rules_preview_says_the_rows_it_moves_lose_their_book_and_type(client, car):
    resp = client.put(f"/api/rules/{car['rule']}", json=as_movement(car))

    assert resp.status_code == 409
    body = resp.get_json()
    preview = body["preview"]
    assert [(f["id"], f["now"], f["after"], f["label_cleared"]) for f in preview["flows"]] == [
        (i, "spending", f"a movement · {states.LOAN}", "Household · Transport") for i in car["past"]]
    assert [(x["now"], x["after"]) for x in preview["examples"]] == [
        ("Sample Motor Finance · Transport", f"Sample Motor Finance · {NO_LABEL}")] * 2
    # A row leaving spending moves to no other book: no money moves between books.
    assert preview["money"] is None
    assert "book and type are emptied" in body["error"]


def test_a_rules_apply_empties_the_book_and_type_of_the_rows_it_moves_and_undo_puts_them_back(
        client, car):
    before = {i: stored(states.instalment(n)) for i, n in zip(car["past"], (7, 6))}
    by_hand = stored(states.instalment(5))
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]

    resp = client.put(f"/api/rules/{car['rule']}",
                      json={**as_movement(car), "past": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    for n in (6, 7):
        held = stored(states.instalment(n))
        assert (held["flow_type"], held["other_side_id"], held["service_id"]) == (
            "movement", car["loan"], car["finance"])
        assert label_of(states.instalment(n)) == (None, None, None, None)
    assert stored(states.instalment(5)) == by_hand          # its flow set by hand: not the rule's

    undone = client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")

    assert undone.status_code == 200, undone.get_json()
    assert {i: stored(states.instalment(n)) for i, n in zip(car["past"], (7, 6))} == before


def test_only_new_rows_leaves_the_rows_a_rule_would_move_with_their_book_and_type(client, car):
    before = {n: label_of(states.instalment(n))[:2] for n in (6, 7)}
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]

    resp = client.put(f"/api/rules/{car['rule']}",
                      json={**as_movement(car), "past": "only_new", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert {n: label_of(states.instalment(n))[:2] for n in (6, 7)} == before
    assert all(stored(states.instalment(n))["flow_type"] == "expense" for n in (6, 7))


def test_a_stale_answer_to_a_rule_that_would_empty_labels_writes_nothing(client, car):
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]
    bring_in(client, BANK, [(8, states.instalment(8), 1850.00)])     # a row the preview never saw
    before = every_row(client)

    resp = client.put(f"/api/rules/{car['rule']}",
                      json={**as_movement(car), "past": "apply", "token": preview["token"]})

    assert resp.status_code == 409 and resp.get_json()["stale"]
    assert every_row(client) == before


def test_a_rule_that_stops_stating_a_movement_gives_its_rows_back_their_label(client, car):
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]
    client.put(f"/api/rules/{car['rule']}", json={**as_movement(car), "past": "apply", "token": preview["token"]})
    assert label_of(states.instalment(6)) == (None, None, None, None)
    unstated = {"flow": None, "other_side_id": None}

    asked = client.put(f"/api/rules/{car['rule']}", json=unstated)

    assert asked.status_code == 409, asked.get_json()
    flows = asked.get_json()["preview"]["flows"]
    assert [(f["now"], f["after"], f["label_cleared"]) for f in flows] == [
        (f"a movement · {states.LOAN}", "spending", None)] * 2
    resp = client.put(f"/api/rules/{car['rule']}", json={**unstated, "past": "apply",
                                                          "token": asked.get_json()["preview"]["token"]})
    assert resp.status_code == 200, resp.get_json()
    for n in (6, 7):
        assert stored(states.instalment(n))["flow_type"] == "expense"
        assert label_of(states.instalment(n)) == (
            "Household", spending_type(client, "Transport"), "merchant", "merchant")


# --- a teach and a plan ----------------------------------------------------------------------


def test_rows_a_taught_wording_takes_into_a_movement_lose_their_book_and_type(client, fees):
    asked = client.post("/api/transactions/resolve", json=fees["teach"])

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    assert [(f["id"], f["now"], f["after"], f["label_cleared"]) for f in preview["flows"]] == [
        (i, "spending", f"a movement · {LOAN}", "Household · Transport") for i in fees["taken"]]
    assert {x["id"]: (x["now"], x["after"]) for x in preview["examples"]} == {
        i: ("Sample Fee Co · Transport", f"{FINANCE} · {NO_LABEL}") for i in fees["taken"]}

    resp = client.post("/api/transactions/resolve",
                       json={**fees["teach"], "past_rows": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    for n in (1021, 1022, 2001):
        assert what(client, agreement(n)) == ("movement", LOAN, FINANCE)
        assert label_of(agreement(n)) == (None, None, None, None)


def test_rows_a_taught_wording_would_take_keep_their_label_on_only_new_rows(client, fees):
    preview = client.post("/api/transactions/resolve", json=fees["teach"]).get_json()["preview"]

    resp = client.post("/api/transactions/resolve",
                       json={**fees["teach"], "past_rows": "only_new", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    transport = spending_type(client, "Transport")
    for n in (1021, 1022):
        assert what(client, agreement(n)) == ("expense", None, "Sample Fee Co")
        assert label_of(agreement(n))[1] == transport


def test_a_teachs_backfilled_row_it_moves_into_a_movement_takes_no_label(client, finance):
    bring_in(client, BANK, [(3, agreement(1021), 1850.00), (4, agreement(1022), 1850.00)])
    first, second = stored(agreement(1021))["id"], stored(agreement(1022))["id"]
    teach = {"tx_id": first, "service_id": finance["svc"], "pattern": "SAMPLE HP AGREEMENT",
             "match_type": "contains", "apply_scope": "service_default"}

    asked = client.post("/api/transactions/resolve", json=teach)

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    assert [(x["id"], x["after"]) for x in preview["examples"]] == [(second, f"{FINANCE} · no type")]
    resp = client.post("/api/transactions/resolve",
                       json={**teach, "past_rows": "apply", "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    for n in (1021, 1022):
        assert what(client, agreement(n)) == ("movement", LOAN, FINANCE)
        assert label_of(agreement(n)) == (None, None, None, None)


def test_a_row_taught_for_itself_that_its_merchant_makes_a_movement_takes_no_label(client, instalment):
    resp = client.post("/api/transactions/resolve", json={
        "tx_id": instalment["tx"], "service_id": instalment["svc"], "apply_scope": "transaction"})

    assert resp.status_code == 200, resp.get_json()
    assert what(client, agreement(3001)) == ("movement", LOAN, FINANCE)
    assert label_of(agreement(3001)) == (None, None, None, None)
    assert [f["label_cleared"] for f in resp.get_json()["flows"]] == [None]   # it had none to empty


def test_a_teach_with_a_flow_out_of_spending_empties_the_rows_label(client, car):
    tx = stored(states.instalment(6))["id"]

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx, "service_id": car["finance"], "type_id": spending_type(client, "Transport"),
        "apply_scope": "transaction", "flow_type": "income"})

    assert resp.status_code == 200, resp.get_json()
    held = stored(states.instalment(6))
    assert (held["flow_type"], held["service_id"]) == ("income", car["finance"])
    assert label_of(states.instalment(6)) == (None, None, None, None)


def test_chats_plan_moves_its_row_into_a_movement_with_no_label_and_its_preview_says_so(client, finance):
    bring_in(client, BANK, [(9, agreement(3002), 1850.00)])
    held = stored(agreement(3002))
    # A row labelled spending by hand before it was taught (chat's set_label).
    assert client.put(f"/api/transactions/{held['id']}",
                      json={"type_id": spending_type(client, "Transport")}).status_code == 200
    items = [{"tx_ids": [held["id"]], "service_id": finance["svc"], "pattern": "SAMPLE HP AGREEMENT",
              "past_rows": "apply"}]

    shown = call("plan_preview", items=items)

    assert shown["ok"], shown
    [item] = shown["result"]["items"]
    assert [(f["now"], f["after"], f["label_cleared"]) for f in item["flows"]] == [
        ("spending", f"a movement · {LOAN}", "Household · Transport")]
    assert label_of(agreement(3002))[1] == spending_type(client, "Transport")
    applied = call("plan_apply", items=items, token=shown["result"]["token"])
    assert applied["ok"], applied
    assert what(client, agreement(3002)) == ("movement", LOAN, FINANCE)
    assert label_of(agreement(3002)) == (None, None, None, None)


# --- the drawer -------------------------------------------------------------------------------


@needs_node
def test_the_drawer_says_rows_a_move_takes_out_of_spending_keep_no_book_or_type():
    code = "\n".join([
        "const esc = s => String(s);",
        _function("keptWords"), _function("flowsLine"),
        "console.log(JSON.stringify(flowsLine({ flows: [",
        "  { now: 'spending', after: 'a movement · Sample car loan', label_cleared: 'Household · Transport' },",
        "  { now: 'spending', after: 'a movement · Sample car loan', label_cleared: 'Moom · Transport' },",
        "  { now: 'spending', after: 'a movement · Sample car loan', label_cleared: null }] })));",
    ])
    assert _node(code) == ("<p class=\"sub\">What the rows are: 2 spending → a movement · Sample car loan, "
                           "with no book or type; 1 spending → a movement · Sample car loan.</p>")


def test_a_taught_rows_own_example_says_it_keeps_no_label_where_the_teach_makes_it_a_movement(client, fees):
    taught = fees["taken"][0]
    teach = {**fees["teach"], "tx_id": taught}

    asked = client.post("/api/transactions/resolve", json=teach)

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    # The row taught, the other row taken, and the row with no merchant the teach backfills.
    assert {x["id"]: x["after"] for x in preview["examples"]} == {
        **{i: f"{FINANCE} · {NO_LABEL}" for i in fees["taken"]},
        stored(agreement(2001))["id"]: f"{FINANCE} · no type"}
    resp = client.post("/api/transactions/resolve",
                       json={**teach, "past_rows": "apply", "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert [f["label_cleared"] for f in resp.get_json()["flows"]] == ["Household · Transport"]
    for n in (1021, 1022):
        assert what(client, agreement(n)) == ("movement", LOAN, FINANCE)
        assert label_of(agreement(n)) == (None, None, None, None)


# --- review of fin-redesign 05 ------------------------------------------------------------------


AIR = "SAMPLE AIR CREDIT 991"


def test_case_20_a_credit_that_came_in_as_income_relabelled_a_refund_takes_its_merchants_label(client, ledger):
    resp = client.post("/api/services", json={"name": "Sample Air", "book": "Household",
                                              "type_id": spending_type(client, "Travel")})
    assert resp.status_code == 200, resp.get_json()
    add_rule(client, "SAMPLE AIR", resp.get_json()["id"])
    bring_in(client, CARD, [(3, AIR, -948.00)], kind="card")
    assert stored(AIR)["flow_type"] == "income" and label_of(AIR) == (None, None, None, None)

    said = client.post(f"/api/review/{stored(AIR)['id']}/label", json={"choice": "refund"})

    assert said.status_code == 200, said.get_json()
    assert stored(AIR)["flow_type"] == "refund"
    # Keeping Travel (case 20): the merchant's label, the operator's choice.
    assert label_of(AIR) == ("Household", spending_type(client, "Travel"), "chosen", "chosen")
    assert client.get("/api/queue/summary").get_json()["untyped"] == {"cards": 0, "rows": 0}


def chosen_by_hand(client, description: str, type_name: str, book: str = "Household") -> int:
    """The row's book and type set by hand (the row sheet, chat's set_label): each chosen."""
    tx = stored(description)["id"]
    resp = client.put(f"/api/transactions/{tx}", json={"type_id": spending_type(client, type_name), "book": book})
    assert resp.status_code == 200, resp.get_json()
    assert label_of(description)[2:] == ("chosen", "chosen")
    return tx


def test_adr_008_a_rule_never_moves_the_flow_of_a_row_whose_type_was_chosen(client, car):
    kept_id = chosen_by_hand(client, states.instalment(6), "Home")
    before = stored(states.instalment(6))

    asked = client.put(f"/api/rules/{car['rule']}", json=as_movement(car))

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    assert [f["id"] for f in preview["flows"]] == [stored(states.instalment(7))["id"]]
    assert [(k["id"], k["now"], k["would"], k["because"]) for k in preview["flows_kept"]] == [
        (kept_id, "spending", f"a movement · {states.LOAN}", "you chose its book and type")]
    assert "1 row keeps its flow" in asked.get_json()["error"]
    resp = client.put(f"/api/rules/{car['rule']}",
                      json={**as_movement(car), "past": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert stored(states.instalment(6)) == before
    assert label_of(states.instalment(6)) == ("Household", spending_type(client, "Home"), "chosen", "chosen")
    assert stored(states.instalment(7))["flow_type"] == "movement"


def test_adr_008_a_choice_made_after_the_preview_makes_its_answer_stale(client, car):
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]
    chosen_by_hand(client, states.instalment(6), "Home")
    before = every_row(client)

    resp = client.put(f"/api/rules/{car['rule']}",
                      json={**as_movement(car), "past": "apply", "token": preview["token"]})

    assert resp.status_code == 409 and resp.get_json()["stale"]
    assert every_row(client) == before


def test_adr_008_a_plans_backfill_never_moves_the_flow_of_a_row_whose_book_was_chosen(client, finance):
    bring_in(client, BANK, [(3, agreement(1021), 1850.00), (4, agreement(1022), 1850.00)])
    taught, other = stored(agreement(1021))["id"], stored(agreement(1022))["id"]
    resp = client.put(f"/api/transactions/{other}", json={"book": "Moom"})
    assert resp.status_code == 200, resp.get_json()
    assert label_of(agreement(1022))[0::2] == ("Moom", "chosen")
    items = [{"tx_ids": [taught], "service_id": finance["svc"], "pattern": "SAMPLE HP AGREEMENT",
              "past_rows": "apply"}]

    shown = call("plan_preview", items=items)

    assert shown["ok"], shown
    [item] = shown["result"]["items"]
    assert [f["id"] for f in item["flows"]] == [taught]
    applied = call("plan_apply", items=items, token=shown["result"]["token"])
    assert applied["ok"], applied
    assert what(client, agreement(1021)) == ("movement", LOAN, FINANCE)
    # The row whose book the operator chose stays spending, in that book, with the merchant's type.
    assert what(client, agreement(1022)) == ("expense", None, FINANCE)
    assert label_of(agreement(1022)) == ("Moom", spending_type(client, "Transport"), "chosen", "merchant")


def test_adr_008_a_plan_whose_wording_takes_another_merchants_rows_leaves_one_whose_type_was_chosen(
        client, fees):
    chosen_by_hand(client, agreement(1022), "Home")
    before = stored(agreement(1022))
    items = [{"tx_ids": [fees["teach"]["tx_id"]], "service_id": fees["svc"], "pattern": "SAMPLE HP AGREEMENT",
              "past_rows": "apply"}]

    shown = call("plan_preview", items=items)

    assert shown["ok"], shown
    applied = call("plan_apply", items=items, token=shown["result"]["token"])
    assert applied["ok"], applied
    assert what(client, agreement(1021)) == ("movement", LOAN, FINANCE)
    assert stored(agreement(1022)) == before


def test_a_review_row_from_a_company_merchant_is_the_companys_money(client, health):
    import expect_back
    bring_in(client, BANK, [(5, "PAYNOW TRANSFER SAMPLE HEALTH CO", -300.00)])
    held = stored("PAYNOW TRANSFER SAMPLE HEALTH CO")
    assert (held["flow_type"], held["service_id"], held["book"]) == ("review", health["svc"], None)

    conn = db.get_connection()
    try:
        why = expect_back.refusal_to_mark(conn, held["id"], "pay")
    finally:
        conn.close()

    assert why is not None and "this row is the company's" in why
    assert client.get("/api/queue/paybacks").get_json()["paybacks"] == []


def test_a_salary_credit_from_a_company_merchant_is_the_companys_money(client, health):
    import expect_back
    bring_in(client, BANK, [(25, f"{SALARY} AUG", -14125.00)])
    held = stored(f"{SALARY} AUG")
    assert (held["flow_type"], held["book"]) == ("income", None)

    conn = db.get_connection()
    try:
        why = expect_back.refusal_to_mark(conn, held["id"], "pay")
    finally:
        conn.close()

    assert why is not None and "this row is the company's" in why


def test_a_type_sent_for_a_waiting_money_in_row_at_confirm_leaves_it_waiting(client, ledger):
    preview = upload(client, BANK, [(4, "PAYNOW TRANSFER FROM SAMPLE PERSON", -50.00)])
    shown = preview["groups"][0]["transactions"][0]
    assert shown["flow_type"] == "review"
    shown["type_id"] = spending_type(client, "Travel")

    resp = client.post("/api/import/confirm", json={"import_id": preview["import_id"], "groups": preview["groups"]})

    assert resp.status_code == 200, resp.get_json()
    assert stored("PAYNOW TRANSFER FROM SAMPLE PERSON")["flow_type"] == "review"
    assert label_of("PAYNOW TRANSFER FROM SAMPLE PERSON") == (None, None, None, None)


def test_a_teach_on_an_income_row_answers_with_what_it_stored(client, ledger):
    bring_in(client, BANK, [(6, "SAMPLE DIVIDEND CO 42", -77.00)])
    held = stored("SAMPLE DIVIDEND CO 42")
    assert held["flow_type"] == "income"

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": held["id"], "service_name": "Sample Dividend", "type_id": spending_type(client, "Travel"),
        "apply_scope": "transaction"})

    assert resp.status_code == 200, resp.get_json()
    assert (resp.get_json()["book"], resp.get_json()["type_id"]) == (None, None)
    assert label_of("SAMPLE DIVIDEND CO 42") == (None, None, None, None)


def test_a_waiting_row_paired_into_a_card_payment_keeps_no_book_or_type(client, ledger):
    bring_in(client, BANK, [(10, "PAYNOW TRANSFER TO SAMPLE PAYEE", 420.00)])
    # As a book before this change holds a waiting row: with a label.
    conn = db.get_connection()
    conn.execute("UPDATE transactions SET book = 'Household', type_id = ?, book_source = 'merchant',"
                 " type_source = 'merchant' WHERE description = ?",
                 (spending_type(client, "Travel"), "PAYNOW TRANSFER TO SAMPLE PAYEE"))
    conn.commit()
    conn.close()
    assert stored("PAYNOW TRANSFER TO SAMPLE PAYEE")["flow_type"] == "review"

    bring_in(client, CARD, [(11, "PAYMENT - ATM/INTERNET SAMPLE 0001", -420.00)], kind="card")

    paired = stored("PAYNOW TRANSFER TO SAMPLE PAYEE")
    assert paired["flow_type"] == "payment" and paired["other_side_id"] is not None
    assert label_of("PAYNOW TRANSFER TO SAMPLE PAYEE") == (None, None, None, None)


def test_the_question_counts_only_rows_leaving_spending_as_leaving_it(client, car):
    # An older book's income row the rule matches, holding a label.
    bring_in(client, BANK, [(9, states.instalment(9), -1850.00)])
    conn = db.get_connection()
    conn.execute("UPDATE transactions SET flow_type = 'income', book = 'Household', type_id = ?"
                 " WHERE description = ?", (spending_type(client, "Transport"), states.instalment(9)))
    conn.commit()
    conn.close()

    asked = client.put(f"/api/rules/{car['rule']}", json=as_movement(car))

    assert asked.status_code == 409, asked.get_json()
    flows = {f["id"]: f for f in asked.get_json()["preview"]["flows"]}
    assert stored(states.instalment(9))["id"] in flows
    assert "2 of them leaving spending" in asked.get_json()["error"]


@needs_node
def test_this_was_offers_the_merchants_type_first_on_a_waiting_row_only():
    code = "\n".join([
        _function("offeredType"),
        "const services = [{ id: 7, type_id: 21 }];",
        "console.log(JSON.stringify([",
        "  offeredType({ flow_type: 'review', service_id: 7, type_id: null }, services),",
        "  offeredType({ flow_type: 'review', service_id: 7, type_id: 30 }, services),",
        "  offeredType({ flow_type: 'refund', service_id: 7, type_id: null }, services),",
        "  offeredType({ flow_type: 'expense', service_id: 7, type_id: null }, services),",
        "  offeredType({ flow_type: 'review', service_id: null, type_id: null }, services)]));",
    ])
    assert _node(code) == [21, 30, None, None, None]


# --- the polish pass (the operator's screen review, ui-review-05) ------------------------------


@needs_node
def test_polish_a_teachs_drawer_counts_the_row_taught_apart_from_the_others(client, fees):
    from test_preview_rules_and_merges import drawer_for, text_of
    taught = fees["taken"][0]
    preview = client.post("/api/transactions/resolve", json={**fees["teach"], "tx_id": taught}).get_json()["preview"]
    assert taught not in [f["id"] for f in preview["flows"]]

    got = drawer_for(preview, {"taught": taught})
    text = text_of(got["body"])

    assert f"This changes {preview['count']} past rows" in text
    # The count is the row taught and the others; the flow line counts the others.
    assert preview["count"] == len(preview["flows"]) + 1
    assert "What the other rows are: 1 spending → a movement" in text, text
    # The row taught is marked among the examples, so the count above reads as it and the others.
    import re
    mine = next(r for r in re.findall(r"<tr>.*?</tr>", got["body"], re.S) if "this row" in r)
    assert "1,850.00" in mine
