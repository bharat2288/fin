"""fin-redesign 07 and 08, the final review: every rule path moves a flow the
same way, and says so (ADR-009).

A rule's say over a row's flow reaches the row on every path that labels it,
under the same limits (relabel_preview.flow_writes): a teach's backfilled
rows, the rows a taught wording takes from another merchant, a rule moved to
another merchant, a row labelled for itself with a merchant whose rules state
a flow, and a chat plan. Each moves only what its preview or its answer
lists; a row a record rests on is kept, named in the same words wherever it
is named; and a refund that a rule moves off refund leaves its purchase as a
hand label's would, kept only where the hand label would be refused.

Rows reach the book through fin's routes, as the operator's do; chat through
mcp_tools.call. Every name, wording and figure is invented.
"""

import pytest

import db
import loan_interest
import mcp_tools
from test_movements_and_review import BANK, CARD, bring_in, make_account, row, spending_type
from test_rule_keeps_review_hold import add_rule, make_merchant

LOAN = "Sample auto loan"
FINANCE = "Sample Auto Finance"
FRIEND = "Sample Friend"
EXPECTED = "S$ 500 of this row is expected back; undo the mark first."
# The same record, as a kept row's drawer line says why (relabel_preview.Kept.because).
BECAUSE = "S$ 500 of it is expected back (undo that mark to move it)"
ACTOR = "chat:sample-subject"


def what(client, description: str) -> tuple:
    """(flow, other side's name, merchant's name) as the row's list reads them."""
    tx = row(client, description)
    return tx["flow_type"], tx["other_side_name"], tx["service_name"]


def rules(client) -> dict[str, dict]:
    return {r["pattern"]: r for r in client.get("/api/rules").get_json()}


def every_row(client) -> list[dict]:
    return client.get("/api/transactions?per_page=200").get_json()["transactions"]


def lines(listed: list[dict], key: str = "after") -> list[tuple]:
    return [(f["id"], f["now"], f[key]) for f in listed]


def kept_of(listed: list[dict]) -> list[tuple]:
    return [(k["id"], k["now"], k["would"], k["why"]) for k in listed]


def mark(client, tx_id: int, person: int, direction: str = "back") -> None:
    """Expect S$ 500 of a row back from one person (or to pay it back), through the mark route."""
    resp = client.post(f"/api/transactions/{tx_id}/expectation", json={
        "direction": direction, "all": False, "amount": "500.00", "names": [person], "new_names": [],
        "note": None})
    assert resp.status_code == 200, resp.get_json()


def call(tool_name, **args):
    return mcp_tools.call(tool_name, args, actor=ACTOR)


@pytest.fixture
def finance(client) -> dict:
    """A household bank, the car loan, and its merchant, whose one rule says
    its rows repay the loan."""
    bank = make_account(client, BANK, "bank", last_four="0002")
    loan = make_account(client, LOAN, "loan")
    svc = make_merchant(client, FINANCE, "Transport")
    add_rule(client, "SAMPLE AUTO FINANCE", svc, flow="movement", other_side_id=loan)
    return {"bank": bank, "loan": loan, "svc": svc}


def agreement(n: int) -> str:
    return f"SAMPLE HP AGREEMENT {n}"


# --- 1: a teach whose only question is a backfilled row's flow ------------------------------


@pytest.mark.parametrize("scope", ["rule", "service_default"])
def test_a_teach_whose_only_question_is_a_backfilled_rows_flow_previews_it_and_applies_on_apply(
        client, finance, scope):
    bring_in(client, BANK, [(3, agreement(1021), 1850.00), (4, agreement(1022), 1850.00)])
    first, second = row(client, agreement(1021))["id"], row(client, agreement(1022))["id"]
    teach = {"tx_id": first, "service_id": finance["svc"], "pattern": "SAMPLE HP AGREEMENT",
             "match_type": "contains", "apply_scope": scope}
    before, rules_before = every_row(client), client.get("/api/rules").get_json()

    asked = client.post("/api/transactions/resolve", json=teach)

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    assert lines(preview["flows"]) == [(second, "spending", f"a movement · {LOAN}")]
    assert preview["flows_kept"] == []
    assert every_row(client) == before
    assert client.get("/api/rules").get_json() == rules_before

    resp = client.post("/api/transactions/resolve",
                       json={**teach, "past_rows": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert second in resp.get_json()["backfilled_ids"]
    assert what(client, agreement(1021)) == ("movement", LOAN, FINANCE)
    assert what(client, agreement(1022)) == ("movement", LOAN, FINANCE)


# --- 2: a row kept, a record resting on it, named wherever it is named ----------------------


def test_a_teachs_preview_names_a_backfilled_row_it_keeps_a_record_resting_on_it(client, finance):
    friend = make_account(client, FRIEND, "person")
    bring_in(client, BANK, [(3, agreement(1021), 1850.00), (4, agreement(1022), 1850.00),
                            (5, agreement(1023), 1850.00)])
    first, second, third = (row(client, agreement(n))["id"] for n in (1021, 1022, 1023))
    mark(client, third, friend)
    teach = {"tx_id": first, "service_id": finance["svc"], "pattern": "SAMPLE HP AGREEMENT",
             "match_type": "contains", "apply_scope": "service_default"}

    asked = client.post("/api/transactions/resolve", json=teach)

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    assert lines(preview["flows"]) == [(second, "spending", f"a movement · {LOAN}")]
    assert kept_of(preview["flows_kept"]) == [(third, "spending", f"a movement · {LOAN}", EXPECTED)]
    assert preview["flows_kept"][0]["date"] == "2026-08-05"
    assert preview["flows_kept"][0]["currency"] == "SGD"
    assert (preview["flows_kept"][0]["amount_minor"], preview["flows_kept"][0]["because"]) == (185000, BECAUSE)

    resp = client.post("/api/transactions/resolve",
                       json={**teach, "past_rows": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert what(client, agreement(1022)) == ("movement", LOAN, FINANCE)
    assert what(client, agreement(1023)) == ("expense", None, FINANCE)
    assert kept_of(resp.get_json()["flows_kept"]) == [(third, "spending", f"a movement · {LOAN}", EXPECTED)]
    assert resp.get_json()["flows_kept"][0]["because"] == BECAUSE
    assert first not in resp.get_json()["backfilled_ids"]


def test_a_row_labelled_for_itself_that_a_record_rests_on_keeps_its_flow_and_the_answer_says_why(
        client, finance):
    friend = make_account(client, FRIEND, "person")
    bring_in(client, BANK, [(5, agreement(1023), 1850.00)])
    third = row(client, agreement(1023))["id"]
    mark(client, third, friend)

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": third, "service_id": finance["svc"], "apply_scope": "transaction"})

    assert resp.status_code == 200, resp.get_json()
    assert what(client, agreement(1023)) == ("expense", None, FINANCE)
    answer = resp.get_json()
    assert kept_of(answer["flows_kept"]) == [(third, "spending", f"a movement · {LOAN}", EXPECTED)]
    assert (answer["flows_kept"][0]["date"], answer["flows_kept"][0]["currency"]) == ("2026-08-05", "SGD")
    assert answer.get("flows", []) == []


# --- 3: the rows a taught wording takes, and a rule moved to another merchant ---------------


@pytest.fixture
def fees(client, finance) -> dict:
    """Two agreement rows another merchant labels (its rule states no flow,
    so they are spending), and one no rule knows, waiting to be taught."""
    fee_co = make_merchant(client, "Sample Fee Co", "Transport")
    add_rule(client, "HP AGREEMENT 10", fee_co)
    bring_in(client, BANK, [(3, agreement(1021), 1850.00), (4, agreement(1022), 1850.00),
                            (5, agreement(2001), 1850.00)])
    assert what(client, agreement(1021)) == ("expense", None, "Sample Fee Co")
    taught = row(client, agreement(2001))
    return {**finance, "fee_co": fee_co, "taken": [row(client, agreement(n))["id"] for n in (1022, 1021)],
            "teach": {"tx_id": taught["id"], "service_id": finance["svc"], "pattern": "SAMPLE HP AGREEMENT",
                      "match_type": "contains", "apply_scope": "service_default"}}


def test_the_rows_a_taught_wording_takes_are_listed_in_flows_and_apply_moves_them(client, fees):
    for amount, day in (("91000.00", "2026-07-31"), ("88000.00", "2026-08-31")):
        assert client.post("/api/anchors", json={"account_id": fees["loan"], "amount": amount,
                                                 "date": day}).status_code == 200
    asked = client.post("/api/transactions/resolve", json=fees["teach"])

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    assert lines(preview["flows"]) == [(i, "spending", f"a movement · {LOAN}") for i in fees["taken"]]

    resp = client.post("/api/transactions/resolve",
                       json={**fees["teach"], "past_rows": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    for n in (1021, 1022, 2001):
        assert what(client, agreement(n)) == ("movement", LOAN, FINANCE)
    conn = db.get_connection()
    try:
        assert loan_interest.derive(conn) == 0      # already worked out again with the moved rows
    finally:
        conn.rollback()
        conn.close()


def test_rows_a_taught_wording_takes_keep_their_flow_and_merchant_on_only_new_rows(client, fees):
    preview = client.post("/api/transactions/resolve", json=fees["teach"]).get_json()["preview"]

    resp = client.post("/api/transactions/resolve",
                       json={**fees["teach"], "past_rows": "only_new", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert what(client, agreement(1021)) == ("expense", None, "Sample Fee Co")
    assert what(client, agreement(1022)) == ("expense", None, "Sample Fee Co")
    assert what(client, agreement(2001)) == ("movement", LOAN, FINANCE)


def test_a_rule_moved_to_another_merchant_takes_that_merchants_stated_flow_and_previews_the_moves(
        client, finance):
    bring_in(client, BANK, [(3, "SAMPLE AUTO FINANCE 01", 1850.00), (4, "SAMPLE AUTO FINANCE 02", 1850.00)])
    first, second = row(client, "SAMPLE AUTO FINANCE 01")["id"], row(client, "SAMPLE AUTO FINANCE 02")["id"]
    assert what(client, "SAMPLE AUTO FINANCE 02") == ("movement", LOAN, FINANCE)
    teach = {"tx_id": first, "service_name": "Sample Garage", "type_id": spending_type(client, "Transport"),
             "pattern": "SAMPLE AUTO FINANCE", "match_type": "contains", "apply_scope": "service_default"}

    asked = client.post("/api/transactions/resolve", json=teach)

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    assert lines(preview["flows"]) == [(second, f"a movement · {LOAN}", "spending")]
    assert rules(client)["SAMPLE AUTO FINANCE"]["flow"] == "movement"

    resp = client.post("/api/transactions/resolve",
                       json={**teach, "past_rows": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    moved = rules(client)["SAMPLE AUTO FINANCE"]
    assert (moved["flow"], moved["other_side_id"], moved["service_id"]) == (None, None, resp.get_json()["service_id"])
    assert what(client, "SAMPLE AUTO FINANCE 02") == ("expense", None, "Sample Garage")
    assert what(client, "SAMPLE AUTO FINANCE 01") == ("expense", None, "Sample Garage")
    assert lines(resp.get_json()["flows"]) == [(first, f"a movement · {LOAN}", "spending")]


def test_a_rule_moved_to_a_merchant_whose_rules_state_a_flow_states_it(client, finance):
    other_loan = make_account(client, "Sample second loan", "loan")
    second_finance = make_merchant(client, "Sample Second Finance", "Transport")
    add_rule(client, "SAMPLE SECOND FINANCE", second_finance, flow="movement", other_side_id=other_loan)
    bring_in(client, BANK, [(3, "SAMPLE AUTO FINANCE 01", 1850.00)])
    first = row(client, "SAMPLE AUTO FINANCE 01")["id"]
    teach = {"tx_id": first, "service_id": second_finance, "pattern": "SAMPLE AUTO FINANCE",
             "match_type": "contains", "apply_scope": "service_default"}

    resp = client.post("/api/transactions/resolve", json={**teach, "past_rows": "apply"})

    assert resp.status_code == 200, resp.get_json()
    moved = rules(client)["SAMPLE AUTO FINANCE"]
    assert (moved["flow"], moved["other_side_id"]) == ("movement", other_loan)
    assert what(client, "SAMPLE AUTO FINANCE 01") == ("movement", "Sample second loan", "Sample Second Finance")


# --- 4: a refund a rule moves off refund leaves its purchase, as a hand label's does --------


@pytest.fixture
def fare(client) -> dict:
    """A fare bought on a card and its refund, which the import linked to it."""
    bank = make_account(client, BANK, "bank", last_four="0002")
    card = make_account(client, CARD, "card", last_four="0001")
    airline = make_merchant(client, "Sample Air", "Travel")
    add_rule(client, "SAMPLE AIR", airline)
    return {"bank": bank, "card": card, "airline": airline}


def bring_in_the_refund(client, fare) -> dict:
    bring_in(client, CARD, [(20, "SAMPLE AIR REFUND 4471", -948.00)], kind="card")
    refund = row(client, "SAMPLE AIR REFUND 4471")
    assert refund["flow_type"] == "refund"
    assert link_of(client, refund["id"]) is not None
    return refund


def link_of(client, tx_id: int):
    return client.get(f"/api/refund-links?row={tx_id}").get_json()["refund_link"]


def test_a_rule_that_moves_a_linked_refund_off_refund_drops_its_link_in_its_change_and_undo_restores_it(
        client, fare):
    bring_in(client, CARD, [(3, "SAMPLE AIR BOOKING 4471", 948.00)], kind="card")
    refund = bring_in_the_refund(client, fare)
    body = {"pattern": "SAMPLE AIR REFUND", "service_id": fare["airline"], "flow": "income"}
    preview = client.post("/api/rules", json=body).get_json()["preview"]
    assert lines(preview["flows"]) == [(refund["id"], "a refund", "income")]

    resp = client.post("/api/rules", json={**body, "past": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert row(client, "SAMPLE AIR REFUND 4471")["flow_type"] == "income"
    assert link_of(client, refund["id"]) is None
    undone = client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")
    assert undone.status_code == 200, undone.get_json()
    assert row(client, "SAMPLE AIR REFUND 4471")["flow_type"] == "refund"
    assert link_of(client, refund["id"]) is not None


def test_a_rule_moves_a_refund_whose_link_placed_a_set_off_as_a_hand_label_would(client, fare):
    friend = make_account(client, FRIEND, "person")
    bring_in(client, CARD, [(3, "SAMPLE AIR BOOKING 4471", 948.00)], kind="card")
    mark(client, row(client, "SAMPLE AIR BOOKING 4471")["id"], friend)
    refund = bring_in_the_refund(client, fare)
    conn = db.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM set_off WHERE payment_row_id = ?",
                            (refund["id"],)).fetchone()[0] == 1
    finally:
        conn.close()
    body = {"pattern": "SAMPLE AIR REFUND", "service_id": fare["airline"], "flow": "transfer",
            "other_side_id": fare["bank"]}

    preview = client.post("/api/rules", json=body).get_json()["preview"]

    assert lines(preview["flows"]) == [(refund["id"], "a refund", f"a transfer · {BANK}")]
    assert preview["flows_kept"] == []
    resp = client.post("/api/rules", json={**body, "past": "apply", "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert row(client, "SAMPLE AIR REFUND 4471")["flow_type"] == "transfer"
    assert link_of(client, refund["id"]) is None
    conn = db.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM set_off WHERE payment_row_id = ?",
                            (refund["id"],)).fetchone()[0] == 0
    finally:
        conn.close()


def test_a_rule_keeps_a_linked_refund_where_a_hand_label_would_be_refused_in_its_words(client, fare):
    friend = make_account(client, FRIEND, "person")
    bring_in(client, CARD, [(3, "SAMPLE AIR BOOKING 4471", 948.00)], kind="card")
    refund = bring_in_the_refund(client, fare)
    mark(client, refund["id"], friend, direction="pay")
    salary = next(t["id"] for t in client.get("/api/types?kind=income").get_json() if t["name"] == "Salary")
    by_hand = client.post(f"/api/review/{refund['id']}/label", json={"choice": "income", "income_kind_id": salary})
    assert by_hand.status_code == 400
    body = {"pattern": "SAMPLE AIR REFUND", "service_id": fare["airline"], "flow": "income"}

    asked = client.post("/api/rules", json=body)

    preview = asked.get_json()["preview"] if asked.status_code == 409 else None
    if preview is None:                                   # nothing else to ask: saved, the row named
        assert asked.status_code == 200, asked.get_json()
        named = asked.get_json()["flows_kept"]
    else:
        assert preview["flows"] == []
        named = preview["flows_kept"]
    assert kept_of(named) == [(refund["id"], "a refund", "income", by_hand.get_json()["error"])]
    # The refusal's own facts, as one clause the drawer's sentence ends with.
    assert ";" not in named[0]["because"] and not named[0]["because"].endswith(".")
    assert named[0]["because"].startswith("SAMPLE AIR REFUND 4471 is to pay back")
    assert link_of(client, refund["id"]) is not None
    assert row(client, "SAMPLE AIR REFUND 4471")["flow_type"] == "refund"


# --- 6: a row labelled for itself moves a flow only where it says so -----------------------


@pytest.fixture
def instalment(client, finance) -> dict:
    bring_in(client, BANK, [(9, agreement(3001), 1850.00)])
    return {**finance, "tx": row(client, agreement(3001))["id"]}


def test_a_row_labelled_for_itself_whose_flow_its_merchant_moves_says_so_in_the_answer(client, instalment):
    resp = client.post("/api/transactions/resolve", json={
        "tx_id": instalment["tx"], "service_id": instalment["svc"], "apply_scope": "transaction"})

    assert resp.status_code == 200, resp.get_json()
    assert what(client, agreement(3001)) == ("movement", LOAN, FINANCE)
    flows = resp.get_json()["flows"]
    assert lines(flows) == [(instalment["tx"], "spending", f"a movement · {LOAN}")]
    assert (flows[0]["date"], flows[0]["currency"]) == ("2026-08-09", "SGD")


def test_chats_plan_preview_lists_the_flows_its_rows_take_and_apply_moves_only_those(client, instalment):
    items = [{"tx_ids": [instalment["tx"]], "service_id": instalment["svc"]}]

    shown = call("plan_preview", items=items)

    assert shown["ok"], shown
    [item] = shown["result"]["items"]
    assert lines(item["flows"]) == [(instalment["tx"], "spending", f"a movement · {LOAN}")]
    assert what(client, agreement(3001)) == ("expense", None, None)
    applied = call("plan_apply", items=items, token=shown["result"]["token"])
    assert applied["ok"], applied
    assert what(client, agreement(3001)) == ("movement", LOAN, FINANCE)


def test_a_plan_whose_flows_changed_since_its_preview_is_refused(client, instalment):
    items = [{"tx_ids": [instalment["tx"]], "service_id": instalment["svc"]}]
    token = call("plan_preview", items=items)["result"]["token"]
    # The merchant's rules no longer agree on a flow: the row would stay spending.
    add_rule(client, "SAMPLE AUTO FEE", instalment["svc"])

    applied = call("plan_apply", items=items, token=token)

    assert not applied["ok"]
    assert what(client, agreement(3001)) == ("expense", None, None)


# --- a kept row's why, as the drawer says it -------------------------------------------------


@pytest.mark.parametrize("why, said", [
    (EXPECTED, BECAUSE),
    ("S$ 60 of this row is to pay back; undo the mark first.", "S$ 60 of it is to pay back (undo that mark to move it)"),
    ("S$ 180 of this row is set against HARBOUR GRILL; unlink it first.",
     "S$ 180 of it is set against HARBOUR GRILL (unlink it to move it)"),
    ("This row has S$ 20 waiting for its row and S$ 30 set against HARBOUR GRILL; unlink those first.",
     "it has S$ 20 waiting for its row and S$ 30 set against HARBOUR GRILL (unlink those to move it)"),
    ("SAMPLE AIR REFUND 4471 is to pay back since; undo the mark on it first",
     "SAMPLE AIR REFUND 4471 is to pay back since (undo that mark on it to move it)"),
    ("Nothing to undo here.", "nothing to undo here"),
])
def test_a_kept_rows_why_is_the_refusals_facts_in_one_plain_clause(why, said):
    import relabel_preview
    assert relabel_preview.because(why) == said
