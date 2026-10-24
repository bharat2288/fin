"""Saying what a transfer was marks its book and type chosen (personal-ledger
50; spec 24, case 7).

The review list's label (Queue > Transfer? > This was...) is a deliberate act:
the book and the type it writes are each chosen, and the flow stays set by
hand as before. Nothing automatic reaches them afterwards: not a merchant's
change, not a rule added, edited or re-run. Each case is one request through
fin's public route (the Flask test client, or mcp_tools.call for chat), then
what the row says afterwards, value and source, the change entry, and what
Undo restores. The database is the temporary one; every merchant, name and
figure is invented.
"""

import pytest

import mcp_tools
import review
from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    merchant, statement, type_id,
)
from test_label_source_writes import CHAT_ACTOR, entries, held, mark, one_change, row, shown, undo

TRANSFER = "PAYNOW TO SAMPLE PAYEE REF 9931"


def flow_of(client, tx_id) -> tuple:
    tx = shown(client, tx_id)
    return (tx["flow_type"], tx["flow_type_manual"], tx["other_side_id"])


def account(conn, name: str, kind: str, owner: str = "Household") -> int:
    conn.execute("INSERT INTO accounts (name, short_name, type, owner) VALUES (?, ?, ?, ?)",
                 (name, name, kind, owner))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


@pytest.fixture
def payee(conn):
    """A merchant fin knows a book for but no type, so its transfers wait."""
    return merchant(conn, "Sample Payee", "Household", None)


@pytest.fixture
def waiting_row(conn, statement, payee):
    """A transfer on the review list: its book from the merchant, no type."""
    return row(conn, statement, TRANSFER, 120.50, book="Household", type_name=None, service_id=payee,
               sources=("merchant", None), flow="review")


def label(client, tx_id, **body):
    return client.post(f"/api/review/{tx_id}/label", json=body)


# --- case 7: spending, a gift ---------------------------------------------------------------


def test_case_7_a_transfer_said_to_be_spending_has_its_book_and_type_chosen(client, conn, waiting_row):
    before = entries(client)

    resp = label(client, waiting_row, choice="spending", type_id=type_id(conn, "Dining"))

    assert resp.status_code == 200, resp.get_json()
    assert held(client, waiting_row) == ("Household", "Dining", "chosen", "chosen")
    assert flow_of(client, waiting_row) == ("expense", 1, None)
    assert mark(client, waiting_row) == "manual"
    change = one_change(client, before)
    assert (change["via"], change["id"]) == ("app", int(resp.headers["X-Fin-Change"]))

    undo(client, resp)
    assert held(client, waiting_row) == ("Household", "", "merchant", None)
    assert flow_of(client, waiting_row) == ("review", 0, None)
    assert mark(client, waiting_row) == "service_default"


def test_case_7_a_book_named_on_the_label_is_chosen_with_the_type(client, conn, waiting_row):
    resp = label(client, waiting_row, choice="spending", type_id=type_id(conn, "Stock purchases"), book="Moom")

    assert resp.status_code == 200, resp.get_json()
    assert held(client, waiting_row) == ("Moom", "Stock purchases", "chosen", "chosen")


def test_case_7_a_gift_given_has_its_book_and_type_chosen(client, conn, waiting_row):
    resp = label(client, waiting_row, choice="gift")

    assert resp.status_code == 200, resp.get_json()
    assert held(client, waiting_row) == ("Household", "Gifts & Donations", "chosen", "chosen")
    assert flow_of(client, waiting_row) == ("expense", 1, None)


def test_case_7_a_gift_received_has_its_kind_chosen_and_its_empty_book_chosen(client, conn, statement):
    tx = row(conn, statement, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410", -500.00, book=None,
             type_name=None, flow="review")

    resp = label(client, tx, choice="gift")

    assert resp.status_code == 200, resp.get_json()
    tx_shown = shown(client, tx)
    assert (tx_shown["flow_type"], tx_shown["book_source"], tx_shown["type_source"]) == ("income", "chosen", "chosen")


def test_case_7_income_has_its_kind_chosen(client, conn, statement):
    tx = row(conn, statement, "INWARD PAYNOW FROM SAMPLE TENANT REF 4411", -3100.00, book=None,
             type_name=None, flow="review")
    rental = conn.execute("SELECT id FROM types WHERE kind = 'income' AND name = 'Rental'").fetchone()[0]

    resp = label(client, tx, choice="income", income_kind_id=rental)

    assert resp.status_code == 200, resp.get_json()
    assert (shown(client, tx)["type_id"], shown(client, tx)["type_source"]) == (rental, "chosen")


# --- a movement: no book or type, each chosen empty (personal-ledger 51) ------------------------


def test_a_spending_label_changed_to_a_movement_chooses_the_emptied_fields(
        client, conn, waiting_row):
    """With the single mark retired, a field said empty by hand is chosen
    itself, so no merchant or rule fills it later."""
    company = account(conn, "Sample Venture", "company")
    label(client, waiting_row, choice="spending", type_id=type_id(conn, "Dining"))

    resp = label(client, waiting_row, choice="company", account_id=company)

    assert resp.status_code == 200, resp.get_json()
    tx = shown(client, waiting_row)
    assert (tx["type_id"], tx["book_source"], tx["type_source"]) == (None, "chosen", "chosen")
    assert flow_of(client, waiting_row) == ("movement", 1, company)
    assert mark(client, waiting_row) == "manual"


# --- a later merchant or rule change never reaches it ----------------------------------------


def test_a_later_change_to_the_merchant_never_reaches_a_labelled_transfer(client, conn, waiting_row, payee):
    label(client, waiting_row, choice="spending", type_id=type_id(conn, "Dining"))

    resp = client.put(f"/api/services/{payee}", json={"book": "Moom", "type_id": type_id(conn, "Travel")})

    assert resp.status_code == 200, resp.get_json()
    assert held(client, waiting_row) == ("Household", "Dining", "chosen", "chosen")


def test_a_later_rule_added_edited_or_the_drift_applied_never_reaches_a_labelled_transfer(client, conn, waiting_row):
    label(client, waiting_row, choice="spending", type_id=type_id(conn, "Dining"))
    catch_all = merchant(conn, "Sample Catch-all", "Moom", "Shopping")

    added = client.post("/api/rules", json={"pattern": "SAMPLE PAYEE", "service_id": catch_all})
    edited = client.put(f"/api/rules/{added.get_json()['id']}", json={"pattern": "PAYNOW TO SAMPLE"})
    rerun = client.post("/api/rules/drift", json={"past": "apply"})

    assert (added.status_code, edited.status_code, rerun.status_code) == (200, 200, 200)
    assert held(client, waiting_row) == ("Household", "Dining", "chosen", "chosen")
    assert flow_of(client, waiting_row) == ("expense", 1, None)


# --- chat ------------------------------------------------------------------------------------


def test_chats_label_review_chooses_both_fields_and_the_entry_is_via_chat(client, conn, waiting_row):
    done = mcp_tools.call("label_review", {"tx_id": waiting_row, "choice": "spending",
                                           "type_id": type_id(conn, "Dining")}, actor=CHAT_ACTOR)

    assert done["ok"], done
    assert held(client, waiting_row) == ("Household", "Dining", "chosen", "chosen")
    latest = entries(client)[0]
    assert (latest["id"], latest["via"], latest["actor"]) == (done["change"]["entry_id"], "chat", CHAT_ACTOR)

    assert mcp_tools.call("undo", {"entry_id": done["change"]["entry_id"]}, actor=CHAT_ACTOR)["ok"]
    assert held(client, waiting_row) == ("Household", "", "merchant", None)


def test_chats_label_review_says_what_any_row_was_and_its_change_reads_truthfully(client, conn, statement):
    # The route never asks that the row was waiting: the app's "Change what
    # this was…" sends a spending row there too. The tool and the change
    # history say so, rather than "a waiting transfer".
    said = mcp_tools.BY_NAME["label_review"].description
    assert "any row" in said and "Change what this was" in said
    assert mcp_tools.TITLES["label_review"] == "Say what a row was"
    tx = row(conn, statement, "SAMPLE FLORIST 2210", 80.00, book="Household", type_name="Dining")

    done = mcp_tools.call("label_review", {"tx_id": tx, "choice": "gift"}, actor=CHAT_ACTOR)

    assert done["ok"], done
    assert held(client, tx) == ("Household", "Gifts & Donations", "chosen", "chosen")
    latest = entries(client)[0]
    assert (latest["id"], latest["summary"]) == (done["change"]["entry_id"], "Said what a row was")


# --- a refused label writes nothing ----------------------------------------------------------


@pytest.mark.parametrize("body, why", [
    ({"choice": "loan_to_person"}, review.PERSON_MOVEMENT_REFUSED),
    ({"choice": "spending"}, "type_id is required: spending takes a type"),
])
def test_a_refused_label_writes_nothing_and_says_why(client, conn, waiting_row, body, why):
    before = entries(client)

    resp = label(client, waiting_row, **body)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == why
    assert entries(client) == before
    assert held(client, waiting_row) == ("Household", "", "merchant", None)
    assert flow_of(client, waiting_row) == ("review", 0, None)


def test_a_person_named_as_the_other_side_is_refused_and_writes_nothing(client, conn, waiting_row):
    person = account(conn, "Sample Friend", "person")
    before = entries(client)

    resp = label(client, waiting_row, choice="own_account", account_id=person)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == review.PERSON_MOVEMENT_REFUSED
    assert entries(client) == before
    assert held(client, waiting_row) == ("Household", "", "merchant", None)
