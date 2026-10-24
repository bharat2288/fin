"""fin-redesign 07: a rule may state a flow and an other side (case 16, ADR-009).

A merchant rule may say what a row is as well as label it: its flow, and for
a movement or a transfer the account on the other side. Who says what a row
is, in order: the operator by hand, then the rule, then the classifier. A
rule that states a flow says it of a new row as it is imported and, when the
operator applies the rule to past rows, of every past row it labels but one
whose flow was set by hand; the rule's preview lists every row whose flow or
other side would change before anything applies, and Apply moves exactly
those. The case: the hire-purchase wording of a car loan's instalments set to
"repayment of the car loan", so a new instalment arrives as a movement
against the loan, not as spending.

Rows reach the book through fin's routes, as the operator's do; chat through
mcp_tools.call. Every name, wording and figure is invented.
"""

import pytest

import db
import loan_interest
import mcp_tools
from test_client_seams import APP_JS, needs_node
from test_movements_and_review import (  # noqa: F401 (ledger is a fixture)
    BANK, bring_in, confirm, household_spend, ledger, make_account, row, upload,
)
from test_preview_rules_and_merges import _function, _node
from test_rule_keeps_review_hold import BUYBACK, BUYBACK_SGD, add_rule, held, make_merchant

WORDING = "HPR900001X"
LOAN = "Sample car loan"


def instalment(n: int) -> str:
    return f"HP {WORDING} SAMPLE MOTOR FINANCE {n:02d}"


def what(client, description: str) -> tuple:
    """(flow, other side's name, by hand) as the row's list reads them."""
    tx = row(client, description)
    return tx["flow_type"], tx["other_side_name"], bool(tx["flow_type_manual"])


def rule_of(client, rule_id: int) -> dict:
    return {r["id"]: r for r in client.get("/api/rules").get_json()}[rule_id]


@pytest.fixture
def car(client, ledger) -> dict:
    """The car loan, its instalments' merchant and rule (stating no flow yet),
    two past instalments taken as spending, one whose flow the operator set
    by hand, and a row the rule does not match."""
    loan = make_account(client, LOAN, "loan")
    finance = make_merchant(client, "Sample Motor Finance", "Transport")
    rule_id = add_rule(client, WORDING, finance)
    bring_in(client, BANK, [(3, instalment(6), 1850.00), (4, instalment(7), 1850.00),
                            (5, instalment(5), 1850.00), (6, "NETS SAMPLE GROCER 0042", 64.10)])
    by_hand = row(client, instalment(5))
    assert client.post("/api/transactions/resolve", json={
        "tx_id": by_hand["id"], "service_id": finance, "type_id": by_hand["type_id"], "book": "Household",
        "apply_scope": "transaction", "flow_type": "expense",
    }).status_code == 200
    assert what(client, instalment(6)) == ("expense", None, False)
    return {"loan": loan, "finance": finance, "rule": rule_id,
            "past": [row(client, instalment(n))["id"] for n in (7, 6)]}


def as_movement(car) -> dict:
    return {"flow": "movement", "other_side_id": car["loan"]}


# --- case 16 --------------------------------------------------------------------------


def test_case_16_the_preview_lists_every_past_row_whose_flow_would_change_before_anything_applies(client, car):
    before = client.get("/api/transactions?per_page=200").get_json()["transactions"]

    resp = client.put(f"/api/rules/{car['rule']}", json=as_movement(car))

    assert resp.status_code == 409
    preview = resp.get_json()["preview"]
    assert [f["id"] for f in preview["flows"]] == car["past"]
    assert [(f["now"], f["after"]) for f in preview["flows"]] == [
        ("spending", f"a movement · {LOAN}"), ("spending", f"a movement · {LOAN}")]
    assert preview["count"] == 2
    # Nothing applied: the rule states no flow yet and every row is as it was.
    assert client.get("/api/transactions?per_page=200").get_json()["transactions"] == before
    assert rule_of(client, car["rule"])["flow"] is None


def test_case_16_apply_moves_exactly_the_rows_listed_and_leaves_a_flow_set_by_hand(client, car):
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]

    resp = client.put(f"/api/rules/{car['rule']}",
                      json={**as_movement(car), "past": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 2
    assert what(client, instalment(6)) == ("movement", LOAN, False)
    assert what(client, instalment(7)) == ("movement", LOAN, False)
    assert what(client, instalment(5)) == ("expense", None, True)
    assert what(client, "NETS SAMPLE GROCER 0042") == ("expense", None, False)
    assert household_spend(client) == 1850.00 + 64.10


def test_case_16_a_new_instalment_arrives_as_a_movement_against_the_loan(client, car):
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]
    client.put(f"/api/rules/{car['rule']}", json={**as_movement(car), "past": "apply", "token": preview["token"]})

    imported = upload(client, BANK, [(9, instalment(8), 1850.00)])
    shown = imported["groups"][0]["transactions"][0]
    assert (shown["flow_type"], shown["other_side_id"], shown["status"]) == ("movement", car["loan"], "typed")
    assert confirm(client, imported).status_code == 200

    assert what(client, instalment(8)) == ("movement", LOAN, False)
    assert row(client, instalment(8))["service_name"] == "Sample Motor Finance"


def test_only_new_rows_keeps_the_past_flows_and_a_new_row_takes_the_rule(client, car):
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]

    resp = client.put(f"/api/rules/{car['rule']}",
                      json={**as_movement(car), "past": "only_new", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert what(client, instalment(6)) == ("expense", None, False)
    assert (rule_of(client, car["rule"])["flow"], rule_of(client, car["rule"])["other_side_id"]) == (
        "movement", car["loan"])
    bring_in(client, BANK, [(9, instalment(8), 1850.00)])
    assert what(client, instalment(8)) == ("movement", LOAN, False)


def test_a_rule_added_with_a_flow_lists_and_moves_the_past_rows_it_wins(client, car):
    body = {"pattern": f"{WORDING} SAMPLE MOTOR", "service_id": car["finance"], **as_movement(car)}

    asked = client.post("/api/rules", json=body)

    assert asked.status_code == 409
    preview = asked.get_json()["preview"]
    assert [f["id"] for f in preview["flows"]] == car["past"]
    resp = client.post("/api/rules", json={**body, "past": "apply", "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert [what(client, instalment(n)) for n in (5, 6, 7)] == [
        ("expense", None, True), ("movement", LOAN, False), ("movement", LOAN, False)]


def test_the_apply_is_one_change_and_undo_puts_the_rule_and_the_rows_back(client, car):
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]
    resp = client.put(f"/api/rules/{car['rule']}",
                      json={**as_movement(car), "past": "apply", "token": preview["token"]})
    entry = resp.headers["X-Fin-Change"]

    undone = client.post(f"/api/history/{entry}/undo")

    assert undone.status_code == 200, undone.get_json()
    assert what(client, instalment(6)) == ("expense", None, False)
    assert what(client, instalment(7)) == ("expense", None, False)
    assert (rule_of(client, car["rule"])["flow"], rule_of(client, car["rule"])["other_side_id"]) == (None, None)


def test_a_rule_that_stops_stating_a_flow_gives_the_rows_back_to_the_classifier(client, car):
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]
    client.put(f"/api/rules/{car['rule']}", json={**as_movement(car), "past": "apply", "token": preview["token"]})

    asked = client.put(f"/api/rules/{car['rule']}", json={"flow": None})

    assert asked.status_code == 409
    back = asked.get_json()["preview"]
    assert [(f["id"], f["now"], f["after"]) for f in back["flows"]] == [
        (i, f"a movement · {LOAN}", "spending") for i in car["past"]]
    resp = client.put(f"/api/rules/{car['rule']}", json={"flow": None, "past": "apply", "token": back["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert what(client, instalment(6)) == ("expense", None, False)
    assert (rule_of(client, car["rule"])["flow"], rule_of(client, car["rule"])["other_side_id"]) == (None, None)


# --- the rule says before the classifier -------------------------------------------------


def test_a_rule_that_states_a_flow_takes_a_transfer_off_the_review_list(client, ledger):
    holdco = make_merchant(client, "Sample Holdco")
    add_rule(client, "SAMPLE HOLDCO", holdco, flow="movement", other_side_id=ledger["Moom"])

    bring_in(client, BANK, [(14, BUYBACK, BUYBACK_SGD)])

    assert what(client, BUYBACK) == ("movement", "Moom", False)
    assert held(client, BUYBACK)[1] == "Sample Holdco"
    assert client.get("/api/review").get_json()["waiting"] == 0


def test_a_rule_that_states_spending_beats_the_card_payment_wording(client, ledger):
    sample = make_merchant(client, "Sample Card Shop")
    add_rule(client, "SAMPLE CARD SHOP", sample, flow="expense")
    wording = "PAYMENT TO CITI CREDIT CARD SAMPLE CARD SHOP 7710"

    bring_in(client, BANK, [(7, wording, 90.00)])

    assert what(client, wording) == ("expense", None, False)


def test_a_rule_edit_that_states_a_flow_reaches_rows_the_classifier_made_transfers(client, ledger):
    own = make_merchant(client, "Sample Own Move")
    rule_id = add_rule(client, "XXXX0001 SAMPLE", own)
    wording = "FAST PAYMENT TO XXXX0001 SAMPLE REF 4410"
    bring_in(client, BANK, [(7, wording, 2500.00)])
    assert what(client, wording)[0] == "transfer"

    asked = client.put(f"/api/rules/{rule_id}", json={"flow": "movement", "other_side_id": ledger["Kalesh"]})

    assert asked.status_code == 409
    preview = asked.get_json()["preview"]
    assert [(f["now"], f["after"]) for f in preview["flows"]] == [("a transfer", "a movement · Kalesh")]
    resp = client.put(f"/api/rules/{rule_id}", json={"flow": "movement", "other_side_id": ledger["Kalesh"],
                                                     "past": "apply", "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert what(client, wording) == ("movement", "Kalesh", False)


# --- what a rule may state ------------------------------------------------------------------


@pytest.fixture
def kinds(client, ledger) -> dict:
    return {
        **ledger,
        "loan": make_account(client, LOAN, "loan"),
        "person": make_account(client, "Sample Friend", "person"),
        "untracked": make_account(client, "Sample Family Account", "untracked"),
        "company card": make_account(client, "Sample Company Card 0009", "card", owner="Moom"),
    }


@pytest.mark.parametrize("body, said", [
    ({"flow": "capital"}, "unknown flow"),
    ({"flow": "review"}, "review"),
    ({"flow": "movement"}, "names its other side"),
    ({"flow": "transfer"}, "names its other side"),
    ({"flow": "movement", "other_side_id": "person"}, "person"),
    ({"flow": "transfer", "other_side_id": "person"}, "person"),
    ({"flow": "transfer", "other_side_id": "loan"}, "bank or card or untracked"),
    ({"flow": "movement", "other_side_id": BANK}, "loan or holding or company"),
    ({"flow": "transfer", "other_side_id": "company card"}, "household"),
    ({"flow": "expense", "other_side_id": "loan"}, "names no other side"),
    ({"other_side_id": "loan"}, "goes with a flow"),
    ({"flow": "movement", "other_side_id": 987654}, "unknown other side"),
    ({"flow": "movement", "other_side_id": True}, "unknown other side"),
])
def test_a_rule_that_states_what_fin_does_not_allow_is_refused_and_writes_nothing(client, kinds, body, said):
    finance = make_merchant(client, "Sample Motor Finance", "Transport")
    rule_id = add_rule(client, WORDING, finance)
    bring_in(client, BANK, [(3, instalment(6), 1850.00)])
    body = {k: kinds.get(v, v) if isinstance(v, str) and k == "other_side_id" else v for k, v in body.items()}
    rules_before = client.get("/api/rules").get_json()
    rows_before = client.get("/api/transactions?per_page=200").get_json()["transactions"]

    added = client.post("/api/rules", json={"pattern": "SAMPLE MOTOR", "service_id": finance,
                                           "past": "apply", **body})
    edited = client.put(f"/api/rules/{rule_id}", json={"past": "apply", **body})

    for resp in (added, edited):
        assert resp.status_code == 400
        assert said in resp.get_json()["error"]
    assert client.get("/api/rules").get_json() == rules_before
    assert client.get("/api/transactions?per_page=200").get_json()["transactions"] == rows_before


@pytest.mark.parametrize("flow_name, side", [
    ("transfer", BANK), ("transfer", "untracked"), ("payment", None), ("payment", BANK),
    ("movement", "loan"), ("movement", "Moom"), ("movement", "Crypto held outside fin"),
    ("expense", None), ("income", None), ("refund", None),
])
def test_a_rule_may_state_any_flow_but_review_with_an_other_side_its_flow_allows(client, kinds, flow_name, side):
    finance = make_merchant(client, "Sample Motor Finance", "Transport")
    other = kinds.get(side)

    rule_id = add_rule(client, WORDING, finance, flow=flow_name, other_side_id=other)

    assert (rule_of(client, rule_id)["flow"], rule_of(client, rule_id)["other_side_id"]) == (flow_name, other)


def test_the_rules_list_names_each_rules_flow_and_other_side(client, kinds):
    finance = make_merchant(client, "Sample Motor Finance", "Transport")
    rule_id = add_rule(client, WORDING, finance, flow="movement", other_side_id=kinds["loan"])

    listed = rule_of(client, rule_id)

    assert (listed["flow"], listed["other_side_id"], listed["other_side_name"]) == ("movement", kinds["loan"], LOAN)


# --- chat -------------------------------------------------------------------------------------


def chat(tool, **args):
    return mcp_tools.call(tool, args, actor="chat:sample-subject")


def test_chat_states_a_flow_through_change_rule_with_the_preview_first(client, car):
    asked = chat("change_rule", rule_id=car["rule"], **as_movement(car))

    assert not asked["ok"]
    preview = asked["preview"]
    assert [f["id"] for f in preview["flows"]] == car["past"]
    assert what(client, instalment(6)) == ("expense", None, False)

    done = chat("change_rule", rule_id=car["rule"], **as_movement(car), past="apply", token=preview["token"])

    assert done["ok"], done
    assert what(client, instalment(6)) == ("movement", LOAN, False)


def test_chat_adds_a_rule_that_states_a_flow(client, ledger):
    loan = make_account(client, LOAN, "loan")
    finance = make_merchant(client, "Sample Motor Finance", "Transport")

    done = chat("add_rule", pattern=WORDING, service_id=finance, flow="movement", other_side_id=loan)

    assert done["ok"], done
    bring_in(client, BANK, [(9, instalment(8), 1850.00)])
    assert what(client, instalment(8)) == ("movement", LOAN, False)


def test_the_book_holds_what_the_rule_states(client, car):
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]
    client.put(f"/api/rules/{car['rule']}", json={**as_movement(car), "past": "apply", "token": preview["token"]})

    conn = db.get_connection()
    try:
        stored = conn.execute("SELECT flow, other_side_id FROM merchant_rules WHERE id = ?",
                              (car["rule"],)).fetchone()
    finally:
        conn.close()
    assert tuple(stored) == ("movement", car["loan"])


# --- what was paid on the loan ----------------------------------------------------------


def test_the_loans_interest_is_worked_out_again_when_the_apply_names_the_loan(client, car):
    assert client.post("/api/anchors", json={"account_id": car["loan"], "amount": "91000.00",
                                             "date": "2026-07-31"}).status_code == 200
    assert client.post("/api/anchors", json={"account_id": car["loan"], "amount": "88000.00",
                                             "date": "2026-08-31"}).status_code == 200
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]

    resp = client.put(f"/api/rules/{car['rule']}",
                      json={**as_movement(car), "past": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    conn = db.get_connection()
    try:
        assert loan_interest.derive(conn) == 0      # already in line with the instalments
        worked_out = conn.execute("SELECT COUNT(*) FROM transactions WHERE book_source = 'derived'").fetchone()[0]
    finally:
        conn.rollback()
        conn.close()
    assert worked_out > 0


# --- Lists › Rules ------------------------------------------------------------------------


@needs_node
def test_the_rules_drawer_says_what_the_rows_become_and_the_sheet_sends_the_flow():
    code = "\n".join([
        "const esc = s => String(s);",
        _function("keptWords"), _function("flowsLine"),
        "console.log(JSON.stringify(flowsLine({ flows: [",
        "  { now: 'spending', after: 'a movement · Sample car loan' },",
        "  { now: 'spending', after: 'a movement · Sample car loan' },",
        "  { now: 'a transfer', after: 'a movement · Sample car loan' }] })));",
    ])
    assert _node(code) == ("<p class=\"sub\">What the rows are: 2 spending → a movement · Sample car loan; "
                           "1 a transfer → a movement · Sample car loan.</p>")
    start = APP_JS.index("ACT['rule-save'] =")
    save = APP_JS[start:APP_JS.index("\n};", start)]
    assert "flow: v('rl-flow') || null" in save and "other_side_id:" in save
