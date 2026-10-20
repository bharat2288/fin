"""Flow writers respect the records (personal-ledger 23, ticket 28).

A row an expectation or a set-off rests on is protected: pair matching leaves
it alone; the review label, the bulk label and the row edit refuse to make it
something the sheet already counts (a transfer, a card payment, a movement
naming a non-person, a company's cost), naming the record; labelling its own
part stays allowed; deleting the account it is on is refused in named words;
and importing its statement again keeps every row, expectation and set-off.

Seam 1 of the spec: the Flask test client on a temporary database, with the
check book's October story (tests/expect_fixtures.py). A refusal writes
nothing: no change entry, every table as it was. Every name and figure is
invented.
"""

from __future__ import annotations

import io

import pytest

import parsers
from expect_fixtures import (
    check_book, harbour_set_against, let_go, mark, october, opening, refund_link, row, set_off, wording,
)
from parse_dbs import ParsedStatement, ParsedTransaction
from trend_fixtures import type_id

SEVEN = ("expectation", "expectation_name", "set_off", "let_go", "refund_link", "sender_wording", "dismissal")

JAY_HELD = "S$ 120 of this row is set against HARBOUR GRILL; unlink it first."


def snapshot(conn) -> dict:
    """Every record, every row and account, and the history."""
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")]
            for t in SEVEN + ("transactions", "accounts", "change_entries", "change_rows")}


def refused(conn, send, words: str):
    """`send()` is refused with exactly `words` and nothing is written."""
    before = snapshot(conn)
    resp = send()
    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json()["error"] == words
    assert "X-Fin-Change" not in resp.headers
    assert snapshot(conn) == before


def bank(conn, name: str = "Second savings") -> int:
    """Another of the household's bank accounts."""
    account_id = conn.execute(
        "INSERT INTO accounts (name, short_name, type, currency, owner) VALUES (?, ?, 'bank', 'SGD', 'Household')",
        (name, name.replace(" ", "-")),
    ).lastrowid
    conn.commit()
    return account_id


@pytest.fixture
def story(conn):
    return october(conn)


@pytest.fixture
def jay_held(conn, story):
    """Case 53c: HARBOUR GRILL S$ 200.00 expected back from Jay and Kim; Jay's
    S$ 120.00 wholly set against it, its flow still review."""
    harbour = mark(conn, story.harbour, 20000, names=(story.people["Jay"], story.people["Kim"]))
    set_off(conn, story.jay_0412, harbour, 12000)
    return harbour


def flow_of(conn, row_id: int) -> tuple:
    return tuple(conn.execute(
        "SELECT flow_type, other_side_id, COALESCE(book, 'Household') FROM transactions WHERE id = ?",
        (row_id,)).fetchone())


# --- pair matching ---------------------------------------------------------------


def test_pair_matching_pairs_the_move_when_nothing_rests_on_the_row(client, conn, story):
    # The control: without a set-off, Jay's S$ 120.00 and a S$ 120.00 move out
    # two days later on another household account are a pair.
    other = bank(conn)
    move = row(conn, story.book, other, "2026-10-12", "FUNDS TRANSFER 5521", 12000, flow="review")
    resp = client.post("/api/pair-matching")
    assert resp.get_json()["paired"] == 1
    assert flow_of(conn, story.jay_0412) == ("transfer", other, "Household")
    assert flow_of(conn, move)[1] == story.book.savings


def test_pair_matching_leaves_a_set_off_row_alone(client, conn, story, jay_held):
    # Case 53c: Jay's row is wholly set off, so it is nobody's pair.
    other = bank(conn)
    move = row(conn, story.book, other, "2026-10-12", "FUNDS TRANSFER 5521", 12000, flow="review")
    resp = client.post("/api/pair-matching")
    assert resp.status_code == 200
    assert resp.get_json()["paired"] == 0
    assert flow_of(conn, story.jay_0412) == ("review", None, "Household")
    assert flow_of(conn, move) == ("review", None, "Household")


def test_pair_matching_leaves_a_marked_row_alone(client, conn, story):
    # A row with an expectation on it (a pay one, on money in) is skipped too.
    mark(conn, story.lena_2210, 6000, names=(story.people["Lena"],))
    other = bank(conn)
    row(conn, story.book, other, "2026-10-13", "FUNDS TRANSFER 5522", 6000, flow="review")
    assert client.post("/api/pair-matching").get_json()["paired"] == 0
    assert flow_of(conn, story.lena_2210) == ("review", None, "Household")


# --- the review label ------------------------------------------------------------


def label(client, row_id: int, **body):
    return client.post(f"/api/review/{row_id}/label", json=body)


def test_the_review_label_refuses_a_move_between_own_accounts(client, conn, story, jay_held):
    refused(conn, lambda: label(client, story.jay_0412, choice="own_account", account_id=story.book.everyday_card),
            JAY_HELD)


def test_the_review_label_refuses_a_movement_naming_a_company_or_a_loan(client, conn, story, jay_held):
    refused(conn, lambda: label(client, story.jay_0412, choice="company", account_id=story.book.company), JAY_HELD)
    refused(conn, lambda: label(client, story.jay_0412, choice="loan_repayment", account_id=story.book.home_loan),
            JAY_HELD)


def test_the_review_label_refuses_a_company_book(client, conn, story, jay_held):
    # Spending in a company's book is the company's cost: counted whole on its line.
    refused(conn, lambda: label(client, story.jay_0412, choice="spending",
                                type_id=type_id(conn, "Dining"), book="Moom"), JAY_HELD)


def test_the_review_label_allows_labelling_the_own_part(client, conn, story, jay_held):
    # A gift received: income. One change, undoable.
    resp = label(client, story.jay_0412, choice="gift")
    assert resp.status_code == 200, resp.get_json()
    assert flow_of(conn, story.jay_0412) == ("income", None, "Household")
    entry_id = int(resp.headers["X-Fin-Change"])
    assert client.post(f"/api/history/{entry_id}/undo").status_code == 200
    assert flow_of(conn, story.jay_0412) == ("review", None, "Household")
    # Income likewise. (A person's movement is no label since the contract:
    # tests/test_expect_contract_retire.py.)
    salary = conn.execute("SELECT id FROM types WHERE kind = 'income' ORDER BY id").fetchone()[0]
    assert label(client, story.jay_0412, choice="income", income_kind_id=salary).status_code == 200


def test_the_review_label_names_each_set_off_of_a_row(client, conn, story):
    harbour_set_against(conn, story)
    refused(conn, lambda: label(client, story.kim_7781, choice="own_account", account_id=story.book.everyday_card),
            "This row has S$ 60 set against HARBOUR GRILL and S$ 10 set against KLOOK TRAVEL; unlink those first.")


def test_the_review_label_names_a_waiting_set_off(client, conn, story):
    set_off(conn, story.lena_2210, None, 6000, person_id=story.people["Lena"])
    refused(conn, lambda: label(client, story.lena_2210, choice="own_account", account_id=story.book.everyday_card),
            "S$ 60 of this row is waiting for its row; unlink it first.")


def test_the_review_label_refuses_to_move_a_marked_row(client, conn, story):
    mark(conn, story.harbour, 20000, names=(story.people["Jay"],))
    refused(conn, lambda: label(client, story.harbour, choice="own_account", account_id=story.book.savings),
            "S$ 200 of this row is expected back; undo the mark first.")
    refused(conn, lambda: label(client, story.harbour, choice="spending",
                                type_id=type_id(conn, "Dining"), book="Moom"),
            "S$ 200 of this row is expected back; undo the mark first.")
    # Still spending, in the Household: its own part, labelled.
    resp = label(client, story.harbour, choice="spending", type_id=type_id(conn, "Groceries"))
    assert resp.status_code == 200, resp.get_json()


def test_the_review_label_refuses_to_move_a_row_to_pay_back(client, conn, story):
    mark(conn, story.lena_2210, 6000, names=(story.people["Lena"],))
    refused(conn, lambda: label(client, story.lena_2210, choice="own_account", account_id=story.book.everyday_card),
            "S$ 60 of this row is to pay back; undo the mark first.")


def test_the_review_label_still_labels_rows_with_no_records(client, conn, story, jay_held):
    resp = label(client, story.lena_2210, choice="own_account", account_id=story.book.everyday_card)
    assert resp.status_code == 200, resp.get_json()
    assert flow_of(conn, story.lena_2210) == ("transfer", story.book.everyday_card, "Household")


# --- the bulk label and the row edit ---------------------------------------------


def test_the_bulk_label_refuses_a_company_book_on_a_set_off_row_whole(client, conn, story, jay_held):
    dining = type_id(conn, "Dining")
    refused(conn, lambda: client.post("/api/transactions/bulk-label", json={"rows": [
        {"tx_id": story.golden, "book": "Moom", "type_id": dining},
        {"tx_id": story.jay_0412, "book": "Moom"},
    ]}), JAY_HELD)
    refused(conn, lambda: client.post("/api/transactions/bulk-label", json={"rows": [
        {"tx_id": story.harbour, "book": "Kalesh", "type_id": dining},
    ]}), "S$ 200 of this row is expected back; undo the mark first.")


def test_the_bulk_label_labels_the_own_part(client, conn, story, jay_held):
    groceries = type_id(conn, "Groceries")
    resp = client.post("/api/transactions/bulk-label", json={"rows": [
        {"tx_id": story.harbour, "book": "Household", "type_id": groceries},
        {"tx_id": story.golden, "book": "Moom"},
    ]})
    assert resp.status_code == 200, resp.get_json()
    assert conn.execute("SELECT type_id FROM transactions WHERE id = ?", (story.harbour,)).fetchone()[0] == groceries
    assert flow_of(conn, story.golden)[2] == "Moom"


def test_the_row_edit_refuses_a_company_book_on_a_set_off_row(client, conn, story, jay_held):
    refused(conn, lambda: client.put(f"/api/transactions/{story.jay_0412}", json={"book": "Moom"}), JAY_HELD)
    refused(conn, lambda: client.put(f"/api/transactions/{story.harbour}", json={"book": "Moom"}),
            "S$ 200 of this row is expected back; undo the mark first.")


def test_the_row_edit_labels_the_own_part(client, conn, story, jay_held):
    groceries = type_id(conn, "Groceries")
    for body in ({"notes": "dinner"}, {"type_id": groceries}, {"book": "Household"}, {"book": None}):
        resp = client.put(f"/api/transactions/{story.harbour}", json=body)
        assert resp.status_code == 200, (body, resp.get_json())
    assert client.put(f"/api/transactions/{story.golden}", json={"book": "Moom"}).status_code == 200


# --- deletes ---------------------------------------------------------------------


def delete_account(client, account_id: int):
    return client.delete(f"/api/accounts/{account_id}")


def test_deleting_the_account_an_expectation_rests_on_is_refused_by_name(client, conn, story):
    # Case 53a (delete half): HARBOUR GRILL, S$ 180 set against it.
    harbour_set_against(conn, story)
    refused(conn, lambda: delete_account(client, story.book.rewards_card),
            "HARBOUR GRILL is expected back with S$ 180 set against it. Unlink those and undo the mark first.")


def test_deleting_the_account_a_marked_row_is_on_is_refused_by_name(client, conn, story):
    mark(conn, story.golden, 6400)
    refused(conn, lambda: delete_account(client, story.book.rewards_card),
            "GOLDEN VILLAGE is expected back. Undo the mark first.")


def test_deleting_the_account_a_payment_is_on_is_refused_by_name(client, conn, story, jay_held):
    refused(conn, lambda: delete_account(client, story.book.savings),
            "PAYNOW FROM JAY TAN 0412 has S$ 120 set against HARBOUR GRILL. Unlink it first.")


def test_deleting_a_person_named_on_an_expectation_is_refused_by_name(client, conn, story, jay_held):
    refused(conn, lambda: delete_account(client, story.people["Kim"]),
            "Kim is named on HARBOUR GRILL. Take the name off it first.")


def test_deleting_a_person_on_a_waiting_set_off_is_refused_by_name(client, conn, story):
    set_off(conn, story.lena_2210, None, 6000, person_id=story.people["Lena"])
    refused(conn, lambda: delete_account(client, story.people["Lena"]),
            "Lena is named on PAYNOW FROM LENA 2210, waiting for its row. Unlink it first.")


def test_deleting_a_person_with_an_opening_figure_a_gift_or_a_wording_is_refused_by_name(client, conn, story):
    opening(conn, story.people["Sam"], "2026-01-01", 80000)
    refused(conn, lambda: delete_account(client, story.people["Sam"]),
            "Sam has an opening figure expected back or to pay back. Undo that change first.")
    golden = mark(conn, story.golden, 6400)
    let_go(conn, golden, "gift", "2026-10-19", to_whom_id=story.people["Lena"])
    refused(conn, lambda: delete_account(client, story.people["Lena"]),
            "Lena is named on the let-go of GOLDEN VILLAGE. Undo the let-go first.")
    wording(conn, "RAVI K", story.people["Ravi"])
    refused(conn, lambda: delete_account(client, story.people["Ravi"]),
            "Ravi is learnt from the wording 'RAVI K'. Undo that change first.")


def test_deleting_the_account_a_linked_refund_is_on_is_refused_by_name(client, conn, story):
    refund = row(conn, story.book, story.book.everyday_card, "2026-10-04", "KLOOK TRAVEL REFUND", -3000,
                 flow="refund")
    refund_link(conn, refund, story.klook)
    refused(conn, lambda: delete_account(client, story.book.everyday_card),
            "KLOOK TRAVEL is linked as a refund. Unlink it first.")


def test_an_account_with_no_records_deletes_as_before(client, conn, story, jay_held):
    # A person with nothing on them goes; one in one change, undoable.
    resp = delete_account(client, story.people["Dev"])
    assert resp.status_code == 200, resp.get_json()
    assert client.post(f"/api/history/{int(resp.headers['X-Fin-Change'])}/undo").status_code == 200
    # An account with statements keeps its old refusal.
    resp = delete_account(client, story.book.everyday_card)
    assert resp.status_code == 400
    assert "statement(s) reference this account" in resp.get_json()["error"]


# --- re-import -------------------------------------------------------------------


def import_statement(client, account: str, rows: list[tuple], *, kind: str, opening: int, closing: int,
                     closing_date: str = "2026-10-31") -> dict:
    """Upload and confirm one statement of (date, description, minor) rows, as
    the operator does; returns what the confirm said."""
    def parse(_path):
        return [ParsedStatement(
            statement_type=kind, statement_date=closing_date, accounts=[account], filename="sample.csv",
            transactions=[ParsedTransaction(date=on, description=text, amount_minor=amount, card_info=account)
                          for on, text, amount in rows],
            opening_minor=opening, closing_minor=closing,
            opening_date=closing_date[:8] + "01", closing_date=closing_date,
        )]

    parsers._PARSERS.insert(0, {"name": "Stand-in", "ext": ".csv", "detect_fn": lambda _p: True, "parse_fn": parse})
    try:
        resp = client.post("/api/import/upload", data={"files": (io.BytesIO(b"stand-in"), "sample.csv")},
                           content_type="multipart/form-data")
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    assert resp.status_code == 200, resp.get_json()
    preview = resp.get_json()
    resp = client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [{"account": g["account"], "transactions": g["transactions"],
                    "statements": g.get("statements", [])} for g in preview["groups"]],
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


CARD_ROWS = [("2026-10-02", "HARBOUR GRILL", 30000), ("2026-10-05", "KOPI CORNER", 800)]
BANK_ROWS = [("2026-10-10", "PAYNOW FROM JAY TAN 0412", -12000), ("2026-10-15", "PAYNOW FROM KIM L 7781", -6000)]


def test_importing_the_october_statement_again_keeps_every_row_and_record(client, conn):
    # Case 53b: HARBOUR keeps its row, its S$ 200.00 expectation and its
    # S$ 180.00 of set-offs; no row, expectation or set-off is added.
    book = check_book(conn)
    import_statement(client, "Sample Card 0300", CARD_ROWS, kind="card", opening=0, closing=-30800)
    import_statement(client, "Sample Bank 0412", BANK_ROWS, kind="bank", opening=0, closing=18000)
    ids = {r[1]: r[0] for r in conn.execute("SELECT id, description FROM transactions")}
    harbour = mark(conn, ids["HARBOUR GRILL"], 20000, names=(book.people["Jay"], book.people["Kim"]))
    set_off(conn, ids["PAYNOW FROM JAY TAN 0412"], harbour, 12000)
    set_off(conn, ids["PAYNOW FROM KIM L 7781"], harbour, 6000)
    records = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")] for t in SEVEN}
    rows_before = [tuple(r) for r in conn.execute(
        "SELECT id, date, description, amount_minor, flow_type, other_side_id FROM transactions ORDER BY id")]

    again = import_statement(client, "Sample Card 0300", CARD_ROWS, kind="card", opening=0, closing=-30800)
    assert again["transactions_saved"] == 0
    again = import_statement(client, "Sample Bank 0412", BANK_ROWS, kind="bank", opening=0, closing=18000)
    assert again["transactions_saved"] == 0

    assert {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")] for t in SEVEN} == records
    assert [tuple(r) for r in conn.execute(
        "SELECT id, date, description, amount_minor, flow_type, other_side_id FROM transactions ORDER BY id"
    )] == rows_before
    assert conn.execute("SELECT amount_minor FROM expectation WHERE row_id = ?",
                        (ids["HARBOUR GRILL"],)).fetchone()[0] == 20000
    assert conn.execute("SELECT SUM(amount_minor) FROM set_off WHERE expectation_id = ?",
                        (harbour,)).fetchone()[0] == 18000


# Wording the classifier reads as waiting on the review list (test_pair_matching).
WAITING_IN = "INWARD PAYNOW FROM SAMPLE SENDER REF 4410"
WAITING_OUT = "FAST PAYMENT REF 771203 OTHR"


@pytest.mark.parametrize("held", [False, True])
def test_an_import_does_not_pair_a_set_off_row(client, conn, held):
    # Case 53c through the import: the statement bringing a S$ 120.00 move out
    # two days after a S$ 120.00 PayNow in pairs the two, unless the PayNow is
    # wholly set off, when it pairs nothing.
    book = check_book(conn)
    for name, four in (("Sample Bank 0002", "0002"), ("Sample Savings 0009", "0009")):
        assert client.post("/api/accounts", json={"name": name, "type": "bank", "last_four": four}).status_code == 200
    import_statement(client, "Sample Card 0300", CARD_ROWS, kind="card", opening=0, closing=-30800)
    import_statement(client, "Sample Savings 0009", [("2026-10-10", WAITING_IN, -12000)],
                     kind="bank", opening=0, closing=12000)
    paynow = conn.execute("SELECT id FROM transactions WHERE description = ?", (WAITING_IN,)).fetchone()[0]
    assert flow_of(conn, paynow) == ("review", None, "Household")
    if held:
        harbour = conn.execute("SELECT id FROM transactions WHERE description = 'HARBOUR GRILL'").fetchone()[0]
        expectation = mark(conn, harbour, 20000, names=(book.people["Jay"], book.people["Kim"]))
        set_off(conn, paynow, expectation, 12000)
    import_statement(client, "Sample Bank 0002", [("2026-10-12", WAITING_OUT, 12000)],
                     kind="bank", opening=50000, closing=38000)
    move = conn.execute("SELECT id FROM transactions WHERE description = ?", (WAITING_OUT,)).fetchone()[0]
    if held:
        assert flow_of(conn, paynow) == ("review", None, "Household")
        assert flow_of(conn, move)[:2] == ("review", None)
    else:
        assert flow_of(conn, paynow)[0] == "transfer"
        assert flow_of(conn, move)[0] == "transfer"
