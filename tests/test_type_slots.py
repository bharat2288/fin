"""fin's own filings, as slots (type-relationships 03, cases 9 to 12).

fin files three things itself: the loan interest it works out, a gift given
(the review list's gift, a let-go gift) and a gift received (the review
list's gift on money in). Each goes under the type its slot points at, kept
in the book and seeded at start from what the code said before (Bank &
government fees, Gifts & Donations, the income kind Gift received), never
overwriting the operator's choice. GET /api/types/slots reads them; PUT
/api/types/slots/<slot> moves one, with the relabel preview's shape: where
fin can tell the past rows it filed through the slot (loan interest: its
worked-out rows) it asks `past: apply` or `only_new`; where it cannot (a
gift's label does not record it) it says so and offers new only. Either way
one entry in Changes that Undo takes back.

Driven through the HTTP interface against a temporary book; the book from
before this effort is tests/fixtures/book_before_type_keys.sql (made by fin's
code at main f759090). Every merchant, figure and name here is invented.
"""

import sqlite3

import pytest

import book_type
import db
import history
from test_expect_letgo import harbour, let_go, on_day, story  # noqa: F401 (fixtures)
from test_loan_interest import enter, make_account, row as seed_row, statement
from test_movements_and_review import BANK, TRANSFERS, bring_in, labelled, ledger, row, waiting  # noqa: F401
from test_type_keys import book_before, every_row, kept_every_row

INTEREST = {"name": "Interest", "covers": "interest charged on the household's loans"}
PRESENTS = {"name": "Presents", "covers": "presents bought for others"}


def slots(client) -> dict[str, dict]:
    resp = client.get("/api/types/slots")
    assert resp.status_code == 200, resp.get_json()
    return {s["slot"]: s for s in resp.get_json()["slots"]}


def types(client, kind: str = "spending") -> dict[str, dict]:
    return {t["display_name"]: t for t in client.get(f"/api/types?kind={kind}").get_json()}


def added(client, body: dict) -> int:
    resp = client.post("/api/types", json=body)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["type"]["id"]


def move(client, slot: str, type_id, **body):
    return client.put(f"/api/types/slots/{slot}", json={"type_id": type_id, **body})


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def held(temp_db) -> tuple:
    """Every slot, type, row and change entry, straight from the book."""
    conn = sqlite3.connect(str(temp_db))
    try:
        return tuple(conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()
                     for t in ("type_slots", "types", "transactions", "change_entries"))
    finally:
        conn.close()


def interest_rows(client) -> list[dict]:
    """The worked-out interest rows, oldest first, as the transaction list shows them."""
    rows = client.get("/api/transactions?per_page=500").get_json()["transactions"]
    return sorted((t for t in rows if t["book_source"] == "derived"), key=lambda t: t["date"])


# A year between two figures: twelve months of interest, one row each.
OWED_START, OWED_YEAR, OWED_LATER = 50_000_000, 49_000_000, 48_900_000
INSTALMENT = 150_000


@pytest.fixture
def a_year_of_interest(client, conn) -> dict:
    """A home loan whose two figures a year apart work out twelve rows of
    interest, filed under the loan-interest slot."""
    loan = make_account(client, "Sample Home Loan", "loan")
    bank = make_account(client, "Sample Bank 0002", "bank", last_four="0002")
    stmt = statement(conn, bank, "2026-08-31")
    assert enter(client, loan, OWED_START, "2025-06-30").status_code == 200
    for month in range(7, 19):
        y, m = (2025, month) if month <= 12 else (2026, month - 12)
        seed_row(conn, stmt, "SAMPLE LOAN INSTALMENT", INSTALMENT, f"{y}-{m:02d}-15", other_side=loan)
    assert enter(client, loan, OWED_YEAR, "2026-06-30").status_code == 200
    # Two instalments after the year, for a later figure to work out.
    seed_row(conn, stmt, "SAMPLE LOAN INSTALMENT", INSTALMENT, "2026-07-15", other_side=loan)
    seed_row(conn, stmt, "SAMPLE LOAN INSTALMENT", INSTALMENT, "2026-08-15", other_side=loan)
    assert len(interest_rows(client)) == 12
    return {"loan": loan}


# --- case 9: the three slots read their current types ---------------------------------


def test_case_9_the_three_slots_read_what_fin_files_under_each(client):
    shown = slots(client)

    assert list(shown) == ["loan_interest", "gift_given", "gift_received"]
    assert {s: (v["kind"], v["display_type"]) for s, v in shown.items()} == {
        "loan_interest": ("spending", "Bank & government fees"),
        "gift_given": ("spending", "Gifts & Donations"),
        "gift_received": ("income", "Gift received"),
    }
    assert [v["says"] for v in shown.values()] == [
        "fin files loan interest under Bank & government fees",
        "fin files gifts given under Gifts & Donations",
        "fin files gifts received under Gift received",
    ]
    assert shown["loan_interest"]["type_id"] == types(client)["Bank & government fees"]["id"]
    assert [v["past_known"] for v in shown.values()] == [True, False, False]


# --- case 10: moving loan interest previews its past rows ----------------------------------


def test_case_10_moving_loan_interest_previews_its_twelve_past_rows_and_writes_nothing(
        client, a_year_of_interest, temp_db):
    interest = added(client, INTEREST)
    before = held(temp_db)

    resp = move(client, "loan_interest", interest)

    assert resp.status_code == 409
    body = resp.get_json()
    assert "12 past rows fin filed as loan interest" in body["error"]
    assert body["preview"]["count"] == 12
    assert body["preview"]["offers"] == ["apply", "only_new"]
    assert body["preview"]["change"] == [{"field": "type_id", "from": "Bank & government fees", "to": "Interest"}]
    assert body["preview"]["token"]
    assert held(temp_db) == before


def test_case_10_apply_moves_the_past_rows_too_as_one_entry_and_new_interest_follows(
        client, a_year_of_interest):
    interest = added(client, INTEREST)
    token = move(client, "loan_interest", interest).get_json()["preview"]["token"]
    count = len(entries(client))

    resp = move(client, "loan_interest", interest, past="apply", token=token)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 12
    assert len(entries(client)) == count + 1
    assert entries(client)[0]["summary"] == "fin files loan interest under Interest, with its 12 past rows"
    assert slots(client)["loan_interest"]["display_type"] == "Interest"
    assert {t["display_type"] for t in interest_rows(client)} == {"Interest"}
    # New interest goes to Interest.
    assert enter(client, a_year_of_interest["loan"], OWED_LATER, "2026-08-31").status_code == 200
    rows = interest_rows(client)
    assert len(rows) == 14
    assert {t["display_type"] for t in rows} == {"Interest"}


def test_case_10_only_new_leaves_the_past_rows_as_one_entry_and_new_interest_goes_to_interest(
        client, a_year_of_interest):
    interest = added(client, INTEREST)
    count = len(entries(client))

    resp = move(client, "loan_interest", interest, past="only_new")

    assert resp.status_code == 200, resp.get_json()
    assert len(entries(client)) == count + 1
    assert entries(client)[0]["summary"] == "fin files loan interest under Interest, new rows only"
    assert {t["display_type"] for t in interest_rows(client)} == {"Bank & government fees"}
    # A later figure works the interest out again: the year already filed
    # keeps its type, the two new months go to Interest.
    assert enter(client, a_year_of_interest["loan"], OWED_LATER, "2026-08-31").status_code == 200
    rows = interest_rows(client)
    assert [t["display_type"] for t in rows] == ["Bank & government fees"] * 12 + ["Interest"] * 2


def test_a_stale_token_is_refused_and_writes_nothing(client, a_year_of_interest, temp_db):
    interest = added(client, INTEREST)
    token = move(client, "loan_interest", interest).get_json()["preview"]["token"]
    # The rows change after the preview: a later figure adds two.
    assert enter(client, a_year_of_interest["loan"], OWED_LATER, "2026-08-31").status_code == 200
    before = held(temp_db)

    resp = move(client, "loan_interest", interest, past="apply", token=token)

    assert resp.status_code == 409
    assert resp.get_json()["stale"] is True
    assert held(temp_db) == before


def test_undo_of_a_slot_move_restores_the_slot_and_the_rows(client, a_year_of_interest):
    interest = added(client, INTEREST)
    assert move(client, "loan_interest", interest, past="apply").status_code == 200

    resp = client.post(f"/api/history/{entries(client)[0]['id']}/undo")

    assert resp.status_code == 200, resp.get_json()
    assert slots(client)["loan_interest"]["display_type"] == "Bank & government fees"
    assert {t["display_type"] for t in interest_rows(client)} == {"Bank & government fees"}


def test_a_move_with_no_past_rows_saves_without_asking(client):
    interest = added(client, INTEREST)

    resp = move(client, "loan_interest", interest)

    assert resp.status_code == 200, resp.get_json()
    assert slots(client)["loan_interest"]["display_type"] == "Interest"


def test_the_same_type_again_writes_no_entry(client):
    count = len(entries(client))

    resp = move(client, "gift_given", types(client)["Gifts & Donations"]["id"])

    assert resp.status_code == 200, resp.get_json()
    assert len(entries(client)) == count


# --- case 11: a slot whose past rows cannot be told apart offers new only --------------------


def test_case_11_a_gift_given_cannot_tell_its_past_rows_and_offers_new_only(client, waiting, temp_db):
    labelled(client, TRANSFERS[1][1], choice="gift")
    presents = added(client, PRESENTS)
    before = held(temp_db)

    asked = move(client, "gift_given", presents)
    apply = move(client, "gift_given", presents, past="apply")

    assert asked.status_code == 409
    assert asked.get_json()["error"] == (
        "Nothing was saved yet: fin cannot tell which past rows it filed as gifts given, "
        "so only new rows can go under Presents (past: only_new).")
    assert asked.get_json()["preview"]["offers"] == ["only_new"]
    assert asked.get_json()["preview"]["past_known"] is False
    assert apply.status_code == 400
    assert apply.get_json()["error"] == (
        "fin cannot tell which past rows it filed as gifts given: only new rows can go under Presents")
    assert held(temp_db) == before


def test_case_11_new_only_moves_the_gift_slot_as_one_entry_and_new_gifts_follow(client, waiting):
    old = labelled(client, TRANSFERS[1][1], choice="gift")
    presents = added(client, PRESENTS)
    count = len(entries(client))

    resp = move(client, "gift_given", presents, past="only_new")

    assert resp.status_code == 200, resp.get_json()
    assert len(entries(client)) == count + 1
    assert entries(client)[0]["summary"] == "fin files gifts given under Presents, new rows only"
    new = labelled(client, TRANSFERS[2][1], choice="gift")
    assert (new["flow_type"], new["book"], new["display_type"]) == ("expense", "Household", "Presents")
    # The gift labelled before the move keeps its type.
    assert old["display_type"] == row(client, TRANSFERS[1][1])["display_type"] == "Gifts & Donations"


def test_a_gift_received_moves_new_only_and_the_review_gift_on_money_in_follows(client, waiting):
    rental = types(client, "income")["Rental"]["id"]
    assert move(client, "gift_received", rental, past="only_new").status_code == 200

    bring_in(client, BANK, [(9, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410", -5000.00)])
    tx = labelled(client, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410", choice="gift")

    assert (tx["flow_type"], tx["display_type"]) == ("income", "Rental")


def test_a_let_go_gift_goes_under_the_gift_given_slot(conn, client, story, harbour, monkeypatch):
    presents = added(client, PRESENTS)
    assert move(client, "gift_given", presents, past="only_new").status_code == 200
    on_day(monkeypatch, "2026-10-19")

    resp = let_go(client, harbour["harbour"], "gift", to_id=story.people["Kim"])

    assert resp.status_code == 200, resp.get_json()
    assert (resp.get_json()["let_go"]["type_id"], resp.get_json()["let_go"]["display_type"]) == (presents, "Presents")


def test_the_review_guess_reads_a_gift_by_the_slot(client, waiting):
    """A transfer labelled a gift twice is guessed a gift; once the slot has
    moved, a row under the old type reads as spending of it, not a gift."""
    bring_in(client, BANK, [(22, "PAYNOW TO SAMPLE PAYEE REF 9931", 50.00)])
    rows = [t for t in client.get("/api/transactions?per_page=500&flow=review").get_json()["transactions"]
            if t["description"] == "PAYNOW TO SAMPLE PAYEE REF 9931"]
    for t in rows:
        assert client.post(f"/api/review/{t['id']}/label", json={"choice": "gift"}).status_code == 200
    bring_in(client, BANK, [(23, "PAYNOW TO SAMPLE PAYEE REF 9931", 60.00)])
    waiting_id = [t["id"] for t in client.get("/api/transactions?per_page=500&flow=review").get_json()["transactions"]
                  if t["description"] == "PAYNOW TO SAMPLE PAYEE REF 9931"][0]
    assert client.get(f"/api/review/{waiting_id}/suggestion").get_json()["guess"]["choice"] == "gift"

    assert move(client, "gift_given", added(client, PRESENTS), past="only_new").status_code == 200

    guess = client.get(f"/api/review/{waiting_id}/suggestion").get_json()["guess"]
    assert (guess["choice"], guess["type"]) == ("spending", "Gifts & Donations")


# --- case 12: a type of the wrong kind is refused, writing nothing -----------------------------


@pytest.mark.parametrize("slot, kind, name, reason", [
    ("loan_interest", "income", "Interest", "fin files loan interest under a spending type"),
    ("gift_given", "income", "Gift received", "fin files gifts given under a spending type"),
    ("gift_received", "spending", "Gifts & Donations", "fin files gifts received under an income kind"),
    ("loan_interest", "spending", "Other", "Other means not placed yet: fin files nothing under it"),
    ("gift_received", "income", "Other", "Other means not placed yet: fin files nothing under it"),
])
def test_case_12_a_type_of_the_wrong_kind_is_refused_and_writes_nothing(client, temp_db, slot, kind, name, reason):
    before = held(temp_db)

    resp = move(client, slot, types(client, kind)[name]["id"], past="only_new")

    assert resp.status_code == 400
    assert resp.get_json()["error"] == reason
    assert held(temp_db) == before


@pytest.mark.parametrize("body, reason", [
    ({"type_id": 999_999}, "no such type"),
    ({"type_id": "Interest"}, "type_id must be a type's id"),
    ({"type_id": True}, "type_id must be a type's id"),
    ({}, "type_id must be a type's id"),
    ({"type_id": 1, "past": "always"}, "past must be apply (past rows change too) or only_new (only new rows)"),
    ({"type_id": 1, "name": "Interest"}, "unknown field: name"),
])
def test_a_move_out_of_shape_is_refused_and_writes_nothing(client, temp_db, body, reason):
    before = held(temp_db)

    resp = client.put("/api/types/slots/loan_interest", json=body)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == reason
    assert held(temp_db) == before


def test_an_unknown_slot_is_not_found(client, temp_db):
    before = held(temp_db)

    resp = move(client, "salary", 1, past="only_new")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "no such slot"
    assert held(temp_db) == before


def test_undoing_the_add_of_a_type_a_slot_points_at_is_refused_in_words(client, temp_db):
    presents = added(client, PRESENTS)
    added_entry = entries(client)[0]
    assert move(client, "gift_given", presents, past="only_new").status_code == 200
    before = held(temp_db)

    resp = client.post(f"/api/history/{added_entry['id']}/undo")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "fin files gifts given under Presents; move that first"
    assert held(temp_db) == before


# --- the start: seeded from the code, never overwriting the operator ------------------------


def test_a_start_keeps_the_operators_slot_and_writes_no_entry(client):
    presents = added(client, PRESENTS)
    assert move(client, "gift_given", presents, past="only_new").status_code == 200
    count = len(entries(client))

    db.init_db()

    assert slots(client)["gift_given"]["display_type"] == "Presents"
    assert len(entries(client)) == count


def test_a_book_from_before_this_effort_starts_seeds_the_slots_and_changes_no_row(tmp_path, monkeypatch, client):
    path = book_before(tmp_path, monkeypatch)
    rows_before = every_row(path)

    db.init_db()
    db.init_db()

    after = every_row(path)
    kept_every_row(rows_before, after)
    conn = sqlite3.connect(str(path))
    try:
        seeded = dict(conn.execute(
            "SELECT s.slot, t.kind || '/' || t.name FROM type_slots s JOIN types t ON t.id = s.type_id"))
        assert history.has_shape(conn)
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    finally:
        conn.close()
    assert seeded == {
        "loan_interest": "spending/Bank & government fees",
        "gift_given": "spending/Gifts & Donations",
        "gift_received": "income/Gift received",
    }
    assert {f"history_type_slots_{op}" for op in ("insert", "update", "delete")} <= triggers
    assert slots(client)["loan_interest"]["display_type"] == "Bank & government fees"


def test_type_slots_is_tracked_and_its_triggers_come_with_a_start():
    assert "type_slots" in history.TRACKED and "type_slots" in history.ADDED_ON_START
    assert len(history.table_triggers("type_slots")) == 3


def test_every_slot_is_declared_once_with_its_kind_and_seed():
    assert [s for s, *_ in book_type.SLOTS] == ["loan_interest", "gift_given", "gift_received"]
