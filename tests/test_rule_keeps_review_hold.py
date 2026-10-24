"""fin-redesign 06: a rule match no longer lifts the review hold (case 15, ADR-009).

A transfer on a household bank account that nothing knows waits on the review
list. A merchant rule used to take it off the list just by matching it: a big
FAST payment a rule named became spending. A rule that states no flow labels
only: the row keeps its merchant link and still waits. Every path that works a
row's flow out is checked here (import preview and confirm, a rule added,
edited or re-run, a teach, a plan), and so are the rows the hold never held,
which keep the flow they have today.

Rows reach the book through fin's routes, as the operator's do. Every name and
figure is invented.
"""

import pytest

import convert_movements
import db
from test_movements_and_review import (  # noqa: F401 (ledger is a fixture)
    BANK, CARD, bring_in, confirm, household_spend, ledger, listed, review_list, row, spending_type, upload,
)

BUYBACK = "FAST PAYMENT TO SAMPLE HOLDCO PTE LTD REF 880123 OTHR"
BUYBACK_SGD = 48210.37
TUTOR = "PAYNOW TO SAMPLE TUTOR REF 5520"


def make_merchant(client, name: str, type_name: str = "Shopping") -> int:
    resp = client.post("/api/services", json={
        "name": name, "book": "Household", "type_id": spending_type(client, type_name),
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def add_rule(client, pattern: str, service_id: int, **extra) -> int:
    resp = client.post("/api/rules", json={"pattern": pattern, "service_id": service_id, **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def held(client, description: str) -> tuple:
    """(flow, merchant, by hand) as the row's list reads them."""
    tx = row(client, description)
    return tx["flow_type"], tx["service_name"], bool(tx["flow_type_manual"])


def waiting_count(client) -> int:
    return client.get("/api/review").get_json()["waiting"]


def lifted_as_before(description: str) -> None:
    """Put a row back as a rule match left it before this change: spending.
    The book a backup holds has rows like this; nothing written now does."""
    conn = db.get_connection()
    conn.execute("UPDATE transactions SET flow_type = 'expense' WHERE description = ?", (description,))
    conn.commit()
    conn.close()


# --- case 15: the import ----------------------------------------------------------


def test_case_15_a_fast_payment_a_rule_names_waits_in_review_with_its_merchant(client, ledger):
    holdco = make_merchant(client, "Sample Holdco")
    add_rule(client, "SAMPLE HOLDCO", holdco)

    preview = upload(client, BANK, [(14, BUYBACK, BUYBACK_SGD)])
    shown = preview["groups"][0]["transactions"][0]
    assert (shown["flow_type"], shown["service_id"], shown["status"]) == ("review", holdco, "review")
    assert confirm(client, preview).status_code == 200

    assert held(client, BUYBACK) == ("review", "Sample Holdco", False)
    assert [tx["description"] for tx in review_list(client)] == [BUYBACK]
    assert waiting_count(client) == 1
    assert household_spend(client) == 0


def test_a_confirm_that_brings_no_flow_still_holds_a_row_a_rule_names(client, ledger):
    holdco = make_merchant(client, "Sample Holdco")
    add_rule(client, "SAMPLE HOLDCO", holdco)
    preview = upload(client, BANK, [(14, BUYBACK, BUYBACK_SGD)])
    del preview["groups"][0]["transactions"][0]["flow_type"]

    assert confirm(client, preview).status_code == 200

    assert held(client, BUYBACK) == ("review", "Sample Holdco", False)


def test_a_type_chosen_in_the_preview_still_takes_a_named_row_off_the_list(client, ledger):
    holdco = make_merchant(client, "Sample Holdco")
    add_rule(client, "SAMPLE HOLDCO", holdco)
    preview = upload(client, BANK, [(14, BUYBACK, BUYBACK_SGD)])
    preview["groups"][0]["transactions"][0]["type_id"] = spending_type(client, "Education")

    assert confirm(client, preview).status_code == 200

    assert held(client, BUYBACK) == ("expense", "Sample Holdco", False)
    assert review_list(client) == []


def test_the_import_of_a_held_row_is_one_change_and_undo_takes_it_back(client, ledger):
    holdco = make_merchant(client, "Sample Holdco")
    add_rule(client, "SAMPLE HOLDCO", holdco)

    resp = confirm(client, upload(client, BANK, [(14, BUYBACK, BUYBACK_SGD)]))
    assert resp.status_code == 200
    undone = client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")

    assert undone.status_code == 200, undone.get_json()
    assert listed(client) == []
    assert waiting_count(client) == 0


# --- a rule added, re-run or edited ----------------------------------------------------


def test_a_rule_added_over_a_waiting_row_names_it_and_leaves_it_waiting(client, ledger):
    bring_in(client, BANK, [(14, BUYBACK, BUYBACK_SGD)])
    holdco = make_merchant(client, "Sample Holdco")

    add_rule(client, "SAMPLE HOLDCO", holdco, past="apply")
    assert client.post("/api/rules/drift", json={"past": "apply"}).status_code == 200

    assert held(client, BUYBACK) == ("review", "Sample Holdco", False)
    assert waiting_count(client) == 1


def test_a_rule_edit_that_reaches_a_row_an_old_rule_lifted_puts_it_back_in_review(client, ledger):
    tutor = make_merchant(client, "Sample Tutor", "Education")
    rule_id = add_rule(client, "SAMPLE TUTOR", tutor)
    bring_in(client, BANK, [(9, TUTOR, 640.00)])
    lifted_as_before(TUTOR)
    coach = make_merchant(client, "Sample Coach", "Education")

    resp = client.put(f"/api/rules/{rule_id}", json={"service_id": coach, "past": "apply"})

    assert resp.status_code == 200, resp.get_json()
    assert held(client, TUTOR) == ("review", "Sample Coach", False)


def test_a_rule_edit_leaves_a_flow_set_by_hand(client, ledger):
    tutor = make_merchant(client, "Sample Tutor", "Education")
    rule_id = add_rule(client, "SAMPLE TUTOR", tutor)
    bring_in(client, BANK, [(9, TUTOR, 640.00)])
    tx = row(client, TUTOR)
    assert client.post(f"/api/review/{tx['id']}/label", json={
        "choice": "spending", "type_id": spending_type(client, "Education"),
    }).status_code == 200
    coach = make_merchant(client, "Sample Coach", "Education")

    assert client.put(f"/api/rules/{rule_id}", json={"service_id": coach, "past": "apply"}).status_code == 200

    assert held(client, TUTOR)[0::2] == ("expense", True)


# --- a teach and a plan ---------------------------------------------------------------


def test_a_teach_that_states_no_flow_names_a_waiting_row_and_leaves_it_waiting(client, ledger):
    bring_in(client, BANK, [(14, BUYBACK, BUYBACK_SGD)])

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": row(client, BUYBACK)["id"], "service_name": "Sample Holdco", "pattern": "SAMPLE HOLDCO",
        "type_id": spending_type(client, "Shopping"), "book": "Household",
    })

    assert resp.status_code == 200, resp.get_json()
    assert held(client, BUYBACK) == ("review", "Sample Holdco", False)


def test_a_teach_that_states_a_flow_takes_the_row_off_the_list(client, ledger):
    bring_in(client, BANK, [(14, BUYBACK, BUYBACK_SGD)])

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": row(client, BUYBACK)["id"], "service_name": "Sample Holdco", "pattern": "SAMPLE HOLDCO",
        "type_id": spending_type(client, "Shopping"), "book": "Household", "flow_type": "expense",
    })

    assert resp.status_code == 200, resp.get_json()
    assert held(client, BUYBACK) == ("expense", "Sample Holdco", True)


def test_a_plan_that_names_a_waiting_row_leaves_it_waiting(client, ledger):
    bring_in(client, BANK, [(14, BUYBACK, BUYBACK_SGD)])
    items = [{"tx_ids": [row(client, BUYBACK)["id"]], "service_name": "Sample Holdco", "pattern": "SAMPLE HOLDCO",
              "type_id": spending_type(client, "Shopping"), "book": "Household"}]
    token = client.post("/api/plans/preview", json={"items": items}).get_json()["token"]

    resp = client.post("/api/plans/apply", json={"items": items, "token": token})

    assert resp.status_code == 200, resp.get_json()
    assert held(client, BUYBACK) == ("review", "Sample Holdco", False)


# --- what the hold never held keeps its flow -------------------------------------------

# (account, description, amount, the flow it has with a rule matching it)
NEVER_HELD = [
    (CARD, "SAMPLE BISTRO 0042", 80.25, "expense"),                                  # card spending
    (BANK, "NETS SAMPLE GROCER 0042", 64.10, "expense"),                             # bank spending, no transfer wording
    (BANK, "BILL PAYMENT DBSC-4119110012340001 :IB SAMPLE", 1500.00, "payment"),     # card payment, bank side
    (BANK, "PAYMENT TO CITI CREDIT CARD SAMPLE REF 7710", 900.00, "payment"),        # card payment by wording
    (BANK, "FAST PAYMENT REFUND SAMPLE SHOP REF 3301", -120.00, "refund"),           # a refund
    (BANK, "FAST PAYMENT TO XXXX0001 SAMPLE REF 4410", 2500.00, "transfer"),         # own account
]


@pytest.mark.parametrize("account, description, amount, flow_name", NEVER_HELD)
def test_a_row_the_hold_never_held_keeps_its_flow_with_a_rule_matching_it(
        client, ledger, account, description, amount, flow_name):
    sample = make_merchant(client, "Sample Everything")
    add_rule(client, "SAMPLE", sample)

    bring_in(client, account, [(7, description, amount)], kind="credit_card" if account == CARD else "bank")

    assert held(client, description)[0] == flow_name
    assert row(client, description)["service_name"] == "Sample Everything"


@pytest.mark.parametrize("account, description, amount, flow_name", NEVER_HELD)
def test_a_rule_added_later_leaves_the_flow_of_a_row_the_hold_never_held(
        client, ledger, account, description, amount, flow_name):
    bring_in(client, account, [(7, description, amount)], kind="credit_card" if account == CARD else "bank")
    before = held(client, description)[0]

    add_rule(client, "SAMPLE", make_merchant(client, "Sample Everything"), past="apply")

    assert (before, held(client, description)[0]) == (flow_name, flow_name)


def test_a_loan_instalment_a_rule_names_is_still_a_movement_naming_its_loan(client, ledger):
    loan = client.post("/api/services", json={"name": "Car Loan"}).get_json()["id"]
    add_rule(client, "SAMPLE CAR LOAN", loan)

    bring_in(client, BANK, [(6, "FAST PAYMENT SAMPLE CAR LOAN INSTALMENT 08", 2000.00)])

    tx = row(client, "FAST PAYMENT SAMPLE CAR LOAN INSTALMENT 08")
    assert (tx["flow_type"], tx["other_side_name"]) == ("movement", "DBS auto loan")


# --- the book as it stands ----------------------------------------------------------------


def test_the_movements_conversion_still_reads_as_applied_on_a_book_with_a_lifted_row(client, ledger):
    holdco = make_merchant(client, "Sample Holdco")
    add_rule(client, "SAMPLE HOLDCO", holdco)
    bring_in(client, BANK, [(14, BUYBACK, BUYBACK_SGD)])
    lifted_as_before(BUYBACK)

    conn = db.get_connection()
    try:
        assert convert_movements.planned(conn) == []
    finally:
        conn.close()
