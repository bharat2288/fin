"""fin-redesign 07: where a rule's say over a row's flow stops (ADR-009).

A rule that states a flow says what its rows are, but not of every row it
reaches:

- A row a record of expect back rests on (an expectation on it, or a payment
  set off against one) keeps its flow on every rule path, as pair matching
  leaves it alone: a rule's Apply, a teach, a resolve, the drift's Apply. The
  preview lists each such row as kept, in the words a hand label's refusal
  names its record with.
- Re-deriving a row's flow on a rule edit is limited to rows whose stored flow
  is still what fin worked out: what the rules and the classifier gave it
  before the edit. A row whose flow came from elsewhere (an older book's
  refund, a mark placed after the preview) keeps it, and the preview's list
  is exactly what Apply writes.
- An account a rule names as its other side is not deleted from under it.
- A rule's flow changed without its other side drops a held other side the
  new flow does not allow; an archived account is no rule's other side.
- A teach whose wording is a rule that states a flow gives its backfilled
  rows that flow, under the same limits.

Rows reach the book through fin's routes, as the operator's do; chat through
mcp_tools.call. Every name, wording and figure is invented.
"""

import pytest

import account_kind
import app as fin_app
import db
import loan_interest
from test_client_seams import needs_node
from test_movements_and_review import (  # noqa: F401 (ledger is a fixture)
    BANK, CARD, bring_in, ledger, make_account, row, spending_type,
)
from test_preview_rules_and_merges import _function, _node
from test_rule_keeps_review_hold import TUTOR, add_rule, held, lifted, make_merchant  # noqa: F401 (lifted)
from test_rule_states_flow import (  # noqa: F401 (car is a fixture)
    LOAN, WORDING, as_movement, car, chat, instalment, rule_of, what,
)

FRIEND = "Sample Friend"
FRIEND_PAYS = "PAYNOW FROM SAMPLE FRIEND REF 0101"
EXPECTED = "S$ 500 of this row is expected back; undo the mark first."


def mark(client, tx_id: int, amount: str, person: int) -> int:
    """Expect some back… on a row, from one person, through the mark route."""
    resp = client.post(f"/api/transactions/{tx_id}/expectation", json={
        "direction": "back", "all": False, "amount": amount, "names": [person], "new_names": [], "note": None})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["expectation"]["id"]


def set_off(client, tx_id: int, expectation_id: int, amount: str) -> None:
    """Back for…: a payment set off against an expectation, through its route."""
    resp = client.post(f"/api/transactions/{tx_id}/set-offs", json={
        "placements": [{"expectation_id": expectation_id, "amount": amount}], "waiting": None})
    assert resp.status_code == 200, resp.get_json()


def every_row(client) -> list[dict]:
    return client.get("/api/transactions?per_page=200").get_json()["transactions"]


def as_an_older_book(sql: str, *params) -> None:
    """Put the book as one written before holds it (a backup has rows like
    this; nothing written now does)."""
    conn = db.get_connection()
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def kept_of(preview: dict) -> list[tuple]:
    return [(k["id"], k["now"], k["would"], k["why"]) for k in preview["flows_kept"]]


@pytest.fixture
def marked(client, ledger, car) -> dict:
    """The car fixture, the newer past instalment (06) marked: S$ 500 of it
    expected back from Sample Friend."""
    friend = make_account(client, FRIEND, "person")
    six, seven = row(client, instalment(6))["id"], row(client, instalment(7))["id"]
    return {**ledger, **car, "friend": friend, "six": six, "seven": seven,
            "expectation": mark(client, six, "500.00", friend)}


# --- a row a record rests on keeps its flow --------------------------------------------


def test_a_rules_apply_keeps_the_flow_of_a_row_an_expectation_rests_on_and_lists_it_kept(client, marked):
    asked = client.put(f"/api/rules/{marked['rule']}", json=as_movement(marked))

    assert asked.status_code == 409
    preview = asked.get_json()["preview"]
    assert [f["id"] for f in preview["flows"]] == [marked["seven"]]
    assert kept_of(preview) == [(marked["six"], "spending", f"a movement · {LOAN}", EXPECTED)]

    resp = client.put(f"/api/rules/{marked['rule']}",
                      json={**as_movement(marked), "past": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert what(client, instalment(6)) == ("expense", None, False)
    assert what(client, instalment(7)) == ("movement", LOAN, False)
    assert row(client, instalment(6))["service_name"] == "Sample Motor Finance"


def test_a_rule_added_with_a_flow_keeps_the_flow_of_a_payment_set_off_against_an_expectation(client, marked):
    bring_in(client, BANK, [(12, FRIEND_PAYS, -500.00)])
    pays = row(client, FRIEND_PAYS)
    assert pays["flow_type"] == "review"
    set_off(client, pays["id"], marked["expectation"], "500.00")
    friend_pay = make_merchant(client, "Sample Friend Pay")
    body = {"pattern": "SAMPLE FRIEND", "service_id": friend_pay, "flow": "transfer", "other_side_id": marked[CARD]}

    asked = client.post("/api/rules", json=body)

    assert asked.status_code == 409
    preview = asked.get_json()["preview"]
    assert preview["flows"] == []
    assert kept_of(preview) == [(pays["id"], "waiting for review", f"a transfer · {CARD}",
                                 f"S$ 500 of this row is set against {instalment(6)}; unlink it first.")]
    resp = client.post("/api/rules", json={**body, "past": "apply", "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert what(client, FRIEND_PAYS) == ("review", None, False)


def test_a_resolve_under_a_rule_saved_for_new_rows_leaves_the_flows_only_new_rows_left(client, marked):
    # Only new rows left both instalments spending while the rule says they
    # are the loan's: a resolve of either moves neither, marked or not.
    preview = client.put(f"/api/rules/{marked['rule']}", json=as_movement(marked)).get_json()["preview"]
    assert client.put(f"/api/rules/{marked['rule']}", json={
        **as_movement(marked), "past": "only_new", "token": preview["token"]}).status_code == 200

    for tx_id in (marked["six"], marked["seven"]):
        held = next(tx for tx in every_row(client) if tx["id"] == tx_id)
        resp = client.post("/api/transactions/resolve", json={
            "tx_id": tx_id, "service_id": marked["finance"], "type_id": held["type_id"],
            "apply_scope": "transaction"})
        assert resp.status_code == 200, resp.get_json()

    assert what(client, instalment(6)) == ("expense", None, False)
    assert what(client, instalment(7)) == ("expense", None, False)


def test_the_drifts_apply_leaves_the_flows_only_new_rows_left(client, marked):
    preview = client.put(f"/api/rules/{marked['rule']}", json=as_movement(marked)).get_json()["preview"]
    assert client.put(f"/api/rules/{marked['rule']}", json={
        **as_movement(marked), "past": "only_new", "token": preview["token"]}).status_code == 200
    # An older default: the merchant's type is not the one its rows read.
    as_an_older_book("UPDATE services SET type_id = ? WHERE id = ?",
                     spending_type(client, "Shopping"), marked["finance"])
    as_an_older_book("UPDATE transactions SET type_source = 'merchant', book_source = 'merchant' "
                     "WHERE id IN (?, ?)", marked["six"], marked["seven"])

    drift = client.get("/api/rules/drift").get_json()

    assert {e["id"] for e in drift["examples"]} >= {marked["six"], marked["seven"]}
    assert drift["flows"] == []
    assert drift["flows_kept"] == []
    resp = client.post("/api/rules/drift", json={"past": "apply", "token": drift["token"]})
    assert resp.status_code == 200, resp.get_json()
    for n in (6, 7):
        assert what(client, instalment(n)) == ("expense", None, False)
        assert row(client, instalment(n))["type_id"] == spending_type(client, "Shopping")


def test_a_rule_whose_only_reach_is_a_kept_row_asks_nothing_and_names_it(client, marked):
    # The rule reaches the marked instalment alone and would change only its
    # flow: nothing changes, so nothing is asked, and the answer names it.
    body = {"pattern": f"{WORDING} SAMPLE MOTOR FINANCE 06", "service_id": marked["finance"], **as_movement(marked)}

    resp = client.post("/api/rules", json=body)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 0
    assert kept_of(resp.get_json()) == [(marked["six"], "spending", f"a movement · {LOAN}", EXPECTED)]
    assert what(client, instalment(6)) == ("expense", None, False)


@needs_node
def test_the_rules_drawer_names_each_row_kept_and_why():
    code = "\n".join([
        "const esc = s => String(s);",
        "const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];",
        "const SIGNS = { SGD: 'S$' }; const digitsOf = cur => 2; const formatters = {};",
        _function("groupFor"), _function("money"), _function("day"), _function("keptWords"), _function("flowsLine"),
        "console.log(JSON.stringify(flowsLine({ flows: [], flows_kept: [",
        "  { date: '2026-06-05', amount_minor: 185000, currency: 'SGD', description: 'HP SAMPLE 06', now: 'spending',",
        "    why: 'S$ 500 of this row is expected back; undo the mark first.',",
        "    because: 'S$ 500 of it is expected back (undo that mark to move it)' }] })));",
    ])
    assert _node(code) == ("<p class=\"sub\">5 Jun 2026 · S$ 1,850.00 · HP SAMPLE 06 stays spending, "
                           "because S$ 500 of it is expected back (undo that mark to move it).</p>")


# --- only a flow fin worked out is worked out again -------------------------------------

SHOP = "SAMPLE SHOP 0001"
SHOP_BACK = "SAMPLE SHOP CREDIT 0002"


@pytest.fixture
def older_refund(client, ledger) -> dict:
    """Sample Shop's rule; a purchase on the card and money back from the
    shop on the card, which an older book holds as a refund (the classifier
    today makes such a credit income)."""
    shop = make_merchant(client, "Sample Shop")
    rule_id = add_rule(client, "SAMPLE SHOP", shop)
    bring_in(client, CARD, [(3, SHOP, 40.00), (4, SHOP_BACK, -25.00)], kind="card")
    assert what(client, SHOP_BACK)[0] == "income"
    as_an_older_book("UPDATE transactions SET flow_type = 'refund' WHERE description = ?", SHOP_BACK)
    return {"shop": shop, "rule": rule_id, "other": make_merchant(client, "Sample Shop Two"),
            "back": row(client, SHOP_BACK)["id"]}


def test_a_rule_edit_leaves_a_flow_the_old_rules_did_not_give(client, older_refund):
    edit = {"service_id": older_refund["other"]}
    asked = client.put(f"/api/rules/{older_refund['rule']}", json=edit)

    assert asked.status_code == 409
    preview = asked.get_json()["preview"]
    assert preview["count"] == 2
    assert preview["flows"] == []
    resp = client.put(f"/api/rules/{older_refund['rule']}", json={**edit, "past": "apply", "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert what(client, SHOP_BACK) == ("refund", None, False)
    assert row(client, SHOP_BACK)["service_name"] == "Sample Shop Two"


def test_a_rule_that_states_a_flow_leaves_a_flow_the_old_rules_did_not_give(client, older_refund):
    # The purchase's flow is the classifier's, so the rule's say moves it;
    # the refund's is not, so it stays.
    asked = client.put(f"/api/rules/{older_refund['rule']}", json={"flow": "income"})

    assert asked.status_code == 409
    preview = asked.get_json()["preview"]
    assert [(f["id"], f["now"], f["after"]) for f in preview["flows"]] == [
        (row(client, SHOP)["id"], "spending", "income")]
    resp = client.put(f"/api/rules/{older_refund['rule']}",
                      json={"flow": "income", "past": "apply", "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert what(client, SHOP) == ("income", None, False)
    assert what(client, SHOP_BACK) == ("refund", None, False)


def test_the_drifts_apply_leaves_a_flow_the_rules_did_not_give(client, older_refund):
    as_an_older_book("UPDATE services SET type_id = ? WHERE id = ?",
                     spending_type(client, "Dining"), older_refund["shop"])

    drift = client.get("/api/rules/drift").get_json()

    assert drift["count"] == 2
    assert drift["flows"] == []
    resp = client.post("/api/rules/drift", json={"past": "apply", "token": drift["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert what(client, SHOP_BACK) == ("refund", None, False)
    assert row(client, SHOP_BACK)["type_id"] == spending_type(client, "Dining")


def test_the_preview_lists_exactly_what_apply_writes(client, car):
    before = {tx["id"]: tx for tx in every_row(client)}
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]

    client.put(f"/api/rules/{car['rule']}", json={**as_movement(car), "past": "apply", "token": preview["token"]})

    moved = [i for i, tx in ((tx["id"], tx) for tx in every_row(client))
             if (tx["flow_type"], tx["other_side_id"]) != (before[i]["flow_type"], before[i]["other_side_id"])]
    assert sorted(f["id"] for f in preview["flows"]) == sorted(moved)


def test_a_mark_placed_after_the_preview_makes_its_token_stale_and_writes_nothing(client, car):
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]
    mark(client, row(client, instalment(6))["id"], "500.00", make_account(client, FRIEND, "person"))
    before = every_row(client)

    resp = client.put(f"/api/rules/{car['rule']}", json={**as_movement(car), "past": "apply", "token": preview["token"]})

    assert resp.status_code == 409
    assert resp.get_json()["stale"] is True
    assert every_row(client) == before
    assert rule_of(client, car["rule"])["flow"] is None


# --- an account a rule names -------------------------------------------------------------


def test_an_account_a_rule_names_as_its_other_side_is_not_deleted(client, ledger):
    holding = make_account(client, "Sample Holding", "holding")
    sample = make_merchant(client, "Sample Broker")
    rule_id = add_rule(client, "SAMPLE BROKER", sample, flow="movement", other_side_id=holding)
    rules_before = client.get("/api/rules").get_json()

    resp = client.delete(f"/api/accounts/{holding}")

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Cannot delete: the rule SAMPLE BROKER names this account as its other side"
    assert any(a["id"] == holding for a in client.get("/api/accounts").get_json())
    assert client.get("/api/rules").get_json() == rules_before
    assert rule_of(client, rule_id)["other_side_id"] == holding


# --- a rule's flow changed without its other side ----------------------------------------


@pytest.fixture
def loan_rule(client, ledger) -> dict:
    loan = make_account(client, LOAN, "loan")
    finance = make_merchant(client, "Sample Motor Finance", "Transport")
    return {"loan": loan, "rule": add_rule(client, WORDING, finance, flow="movement", other_side_id=loan)}


@pytest.mark.parametrize("flow_name", ["expense", "payment"])
def test_chat_changes_a_movement_rules_flow_and_drops_the_other_side_it_no_longer_allows(client, loan_rule,
                                                                                         flow_name):
    done = chat("change_rule", rule_id=loan_rule["rule"], flow=flow_name)

    assert done["ok"], done
    assert (rule_of(client, loan_rule["rule"])["flow"], rule_of(client, loan_rule["rule"])["other_side_id"]) == (
        flow_name, None)


def test_a_flow_that_needs_an_other_side_still_asks_for_one_when_the_held_one_will_not_do(client, loan_rule):
    resp = client.put(f"/api/rules/{loan_rule['rule']}", json={"flow": "transfer"})

    assert resp.status_code == 400
    assert "names its other side" in resp.get_json()["error"]
    assert rule_of(client, loan_rule["rule"])["flow"] == "movement"


def test_a_held_other_side_the_new_flow_allows_is_kept(client, ledger):
    sample = make_merchant(client, "Sample Card Shop")
    rule_id = add_rule(client, "SAMPLE CARD SHOP", sample, flow="transfer", other_side_id=ledger[CARD])

    resp = client.put(f"/api/rules/{rule_id}", json={"flow": "payment"})

    assert resp.status_code == 200, resp.get_json()
    assert (rule_of(client, rule_id)["flow"], rule_of(client, rule_id)["other_side_id"]) == ("payment", ledger[CARD])


def test_an_archived_account_is_no_rules_other_side(client, ledger):
    old = make_account(client, "Sample Old Card 0003", "card", last_four="0003")
    assert client.put(f"/api/accounts/{old}", json={"status": "archived"}).status_code == 200
    sample = make_merchant(client, "Sample Card Shop")
    rule_id = add_rule(client, "SAMPLE CARD SHOP", sample)
    rules_before = client.get("/api/rules").get_json()

    added = client.post("/api/rules", json={"pattern": "SAMPLE OLD", "service_id": sample, "flow": "transfer",
                                           "other_side_id": old})
    edited = client.put(f"/api/rules/{rule_id}", json={"flow": "transfer", "other_side_id": old})
    by_chat = chat("change_rule", rule_id=rule_id, flow="transfer", other_side_id=old)

    for resp in (added, edited):
        assert resp.status_code == 400
        assert "archived" in resp.get_json()["error"]
    assert not by_chat["ok"] and "archived" in str(by_chat)
    assert client.get("/api/rules").get_json() == rules_before


# --- a teach whose wording is a rule that states a flow ------------------------------------


def test_a_teach_under_a_rule_saved_for_new_rows_leaves_the_flows_only_new_rows_left(client, ledger):
    # Two instalments came in before any rule knew them: the merchant the
    # import made for them, no type, spending. The rule that knows them now
    # says they are the loan's, saved for new rows only: the past rows keep
    # spending, and a teach of the rule's wording, which labels them, keeps
    # it too (the target row and the row it backfills).
    loan = make_account(client, LOAN, "loan")
    bring_in(client, BANK, [(3, instalment(6), 1850.00), (4, instalment(7), 1850.00)])
    six, seven = row(client, instalment(6)), row(client, instalment(7))
    made = six["service_id"]
    assert (six["type_id"], what(client, instalment(7))) == (None, ("expense", None, False))
    body = {"pattern": WORDING, "service_id": made, "flow": "movement", "other_side_id": loan}
    token = client.post("/api/rules", json=body).get_json()["preview"]["token"]
    assert client.post("/api/rules", json={**body, "past": "only_new", "token": token}).status_code == 200

    for scope in ("service_default", "rule"):
        teach = {"tx_id": six["id"], "service_id": made, "type_id": spending_type(client, "Transport"),
                 "pattern": WORDING, "apply_scope": scope}
        resp = client.post("/api/transactions/resolve", json=teach)
        assert resp.status_code == 200, resp.get_json()

    assert row(client, instalment(7))["type_id"] == spending_type(client, "Transport")
    assert what(client, instalment(6)) == ("expense", None, False)
    assert what(client, instalment(7)) == ("expense", None, False)


# --- strict: a later change leaves what Only new rows left ---------------------------------
# A row's flow is worked out again only where it is what fin worked out under
# the rules before the change: the stated flow of the rule that labelled it,
# where that rule states one, else the classifier's. Only new rows leaves the
# classifier's flow under a rule that states another, so no later change
# moves it.


def only_new(client, car) -> None:
    preview = client.put(f"/api/rules/{car['rule']}", json=as_movement(car)).get_json()["preview"]
    assert client.put(f"/api/rules/{car['rule']}", json={
        **as_movement(car), "past": "only_new", "token": preview["token"]}).status_code == 200


def test_a_later_edits_apply_leaves_the_flows_only_new_rows_left(client, car):
    only_new(client, car)
    edit = {"type_override_id": spending_type(client, "Shopping")}

    asked = client.put(f"/api/rules/{car['rule']}", json=edit)

    assert asked.status_code == 409
    preview = asked.get_json()["preview"]
    assert {e["id"] for e in preview["examples"]} >= set(car["past"])
    assert (preview["flows"], preview["flows_kept"]) == ([], [])
    resp = client.put(f"/api/rules/{car['rule']}", json={**edit, "past": "apply", "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    for n in (6, 7):
        assert what(client, instalment(n)) == ("expense", None, False)
        assert row(client, instalment(n))["type_id"] == spending_type(client, "Shopping")


def test_a_resolve_leaves_a_refund_an_older_book_holds_on_the_row_it_names(client, older_refund):
    # The classifier would make the credit income; the book holds a refund
    # fin did not work out, so the resolve that names the row keeps it.
    resp = client.post("/api/transactions/resolve", json={
        "tx_id": older_refund["back"], "service_id": older_refund["shop"],
        "type_id": spending_type(client, "Dining"), "apply_scope": "transaction"})

    assert resp.status_code == 200, resp.get_json()
    assert what(client, SHOP_BACK) == ("refund", None, False)
    assert row(client, SHOP_BACK)["type_id"] == spending_type(client, "Dining")


# --- fin's own flow is still worked out again (each would fail if it never were) ----------


def test_a_no_flow_rules_apply_still_works_out_fins_own_flow_again(client, lifted):
    # The rule deleted, the PayNow loses its merchant, and with it what kept it
    # off the review list. Its spending is still the classifier's, so Apply
    # works it out again: it waits.
    tutor = row(client, TUTOR)["id"]

    asked = client.delete(f"/api/rules/{lifted['rule']}")

    assert asked.status_code == 409
    preview = asked.get_json()["preview"]
    assert [(f["id"], f["now"], f["after"]) for f in preview["flows"]] == [(tutor, "spending", "waiting for review")]
    resp = client.delete(f"/api/rules/{lifted['rule']}", json={"past": "apply", "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert held(client, TUTOR) == ("review", None, False)


def test_the_drifts_apply_still_works_out_fins_own_flow_again(client, lifted):
    as_an_older_book("DELETE FROM merchant_rules WHERE id = ?", lifted["rule"])
    tutor = row(client, TUTOR)["id"]

    drift = client.get("/api/rules/drift").get_json()

    assert [(f["id"], f["now"], f["after"]) for f in drift["flows"]] == [(tutor, "spending", "waiting for review")]
    resp = client.post("/api/rules/drift", json={"past": "apply", "token": drift["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert held(client, TUTOR) == ("review", None, False)


# --- kept only where a hand label would refuse the same move -----------------------------


def test_a_rules_apply_moves_a_set_off_payments_flow_where_a_hand_label_would(client, marked):
    # Income is the payment's own part: a hand label may say so of a row set
    # off against an expectation, so the rule's Apply does too.
    bring_in(client, BANK, [(12, FRIEND_PAYS, -500.00)])
    pays = row(client, FRIEND_PAYS)
    set_off(client, pays["id"], marked["expectation"], "500.00")
    friend_pay = make_merchant(client, "Sample Friend Pay")
    body = {"pattern": "SAMPLE FRIEND", "service_id": friend_pay, "flow": "income"}

    asked = client.post("/api/rules", json=body)

    assert asked.status_code == 409
    preview = asked.get_json()["preview"]
    assert [(f["id"], f["now"], f["after"]) for f in preview["flows"]] == [(pays["id"], "waiting for review", "income")]
    assert preview["flows_kept"] == []
    resp = client.post("/api/rules", json={**body, "past": "apply", "token": preview["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert what(client, FRIEND_PAYS) == ("income", None, False)


# --- a teach that would move a backfilled row's flow asks first ----------------------------

LONGER = f"{WORDING} SAMPLE MOTOR"


@pytest.fixture
def ruled(client, ledger) -> dict:
    """Two instalments the import made a merchant for (no type), then a rule
    of their wording saying they are the loan's, applied: both movements
    against the loan, still waiting for a type. A teach of a longer wording
    (a rule that states no flow, winning both) would give them the
    classifier's spending."""
    loan = make_account(client, LOAN, "loan")
    bring_in(client, BANK, [(3, instalment(6), 1850.00), (4, instalment(7), 1850.00)])
    six = row(client, instalment(6))
    body = {"pattern": WORDING, "service_id": six["service_id"], "flow": "movement", "other_side_id": loan}
    token = client.post("/api/rules", json=body).get_json()["preview"]["token"]
    assert client.post("/api/rules", json={**body, "past": "apply", "token": token}).status_code == 200
    seven = row(client, instalment(7))
    assert (what(client, instalment(7)), seven["type_id"]) == (("movement", LOAN, False), None)
    return {"loan": loan, "six": six["id"], "seven": seven["id"],
            "teach": {"tx_id": six["id"], "service_id": six["service_id"], "pattern": LONGER,
                      "type_id": spending_type(client, "Transport"), "apply_scope": "service_default"}}


def test_a_teach_that_would_move_a_backfilled_rows_flow_asks_first_and_writes_nothing(client, ruled):
    before, rules_before = every_row(client), client.get("/api/rules").get_json()

    asked = client.post("/api/transactions/resolve", json=ruled["teach"])

    assert asked.status_code == 409
    preview = asked.get_json()["preview"]
    assert [(f["id"], f["now"], f["after"]) for f in preview["flows"]] == [
        (ruled["seven"], f"a movement · {LOAN}", "spending")]
    assert preview["flows_kept"] == []
    assert every_row(client) == before
    assert client.get("/api/rules").get_json() == rules_before

    resp = client.post("/api/transactions/resolve",
                       json={**ruled["teach"], "past_rows": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert ruled["seven"] in resp.get_json()["backfilled_ids"]
    assert what(client, instalment(7)) == ("expense", None, False)
    assert row(client, instalment(7))["type_id"] == spending_type(client, "Transport")


def test_a_teach_answered_only_new_rows_leaves_the_backfilled_movement_unlabelled_and_its_flow(client, ruled):
    preview = client.post("/api/transactions/resolve", json=ruled["teach"]).get_json()["preview"]

    resp = client.post("/api/transactions/resolve",
                       json={**ruled["teach"], "past_rows": "only_new", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert what(client, instalment(7)) == ("movement", LOAN, False)
    # Only new rows leaves the movement as it is: its merchant, no type (fin-redesign 05).
    assert row(client, instalment(7))["type_id"] is None


def test_a_teach_that_moves_a_flow_works_out_the_loans_interest_again(client, ruled):
    for amount, day in (("91000.00", "2026-07-31"), ("88000.00", "2026-08-31")):
        assert client.post("/api/anchors", json={"account_id": ruled["loan"], "amount": amount,
                                                 "date": day}).status_code == 200

    resp = client.post("/api/transactions/resolve", json={**ruled["teach"], "past_rows": "apply"})

    assert resp.status_code == 200, resp.get_json()
    assert what(client, instalment(7)) == ("expense", None, False)
    conn = db.get_connection()
    try:
        assert loan_interest.derive(conn) == 0      # already in line with the instalments left
    finally:
        conn.rollback()
        conn.close()


# --- an account a rule names is not changed into one the rule cannot name ------------------

COMPANY = account_kind.OWNER_NAMES[1]


@pytest.fixture
def broker(client, ledger) -> dict:
    holding = make_account(client, "Sample Holding", "holding")
    sample = make_merchant(client, "Sample Broker")
    return {"holding": holding,
            "rule": add_rule(client, "SAMPLE BROKER", sample, flow="movement", other_side_id=holding)}


def account_of(client, acct_id: int) -> dict:
    return {a["id"]: a for a in client.get("/api/accounts").get_json()}[acct_id]


@pytest.mark.parametrize("change, why", [
    ({"status": "archived"}, fin_app.RULE_ARCHIVED_REFUSED),
    ({"type": "person"}, fin_app.RULE_PERSON_REFUSED),
    ({"type": "bank"}, "A movement's other side is one of the household's accounts of kind loan or holding or company."),
    ({"owner": COMPANY}, "A movement's other side is one of the household's accounts of kind loan or holding or company."),
])
def test_an_account_a_rule_names_is_not_made_one_the_rule_cannot_name(client, broker, change, why):
    before, rules_before = account_of(client, broker["holding"]), client.get("/api/rules").get_json()

    resp = client.put(f"/api/accounts/{broker['holding']}", json={**change, "name": "Sample Holding Two"})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == f"The rule SAMPLE BROKER names this account as its other side. {why}"
    assert account_of(client, broker["holding"]) == before
    assert client.get("/api/rules").get_json() == rules_before


@pytest.mark.parametrize("change", [{"name": "Sample Holding Two"}, {"type": "loan"}, {"type": "company"}])
def test_an_account_a_rule_names_may_change_where_the_rule_still_holds(client, broker, change):
    resp = client.put(f"/api/accounts/{broker['holding']}", json=change)

    assert resp.status_code == 200, resp.get_json()
    assert {k: account_of(client, broker["holding"])[k] for k in change} == change


# --- the rules screen names the rows kept when it asked nothing ---------------------------


@needs_node
def test_a_rule_saved_with_rows_kept_says_so_in_its_toast():
    code = "\n".join([
        "const esc = s => String(s);",
        "function plural(n, one, many) { return `${n} ${n === 1 ? one : (many || one + 's')}`; }",
        "let said = null; const toast = (m, o) => { said = [m, o]; }; const refreshFrame = () => {};",
        "function sentence(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }",
        _function("flowCounts"), _function("previewedWords"), _function("doneWords"), _function("previewedDone"),
        "const done = previewedDone({ ok: true, change: 7, data: { recategorized: 0, flows_kept: [",
        "  { description: 'HP SAMPLE 06', now: 'spending', why: 'S$ 500 of this row is expected back; undo the mark first.' }] } },",
        "  'Rule added');",
        "console.log(JSON.stringify({ done, said }));",
    ])
    # Counts only: the row and why are the drawer's (and the answer's flows_kept).
    assert _node(code) == {"done": True, "said": ["Rule added. 1 row kept as it is.", {"undo": 7}]}
