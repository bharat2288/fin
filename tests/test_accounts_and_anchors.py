"""Accounts of every kind, anchors, and entering a figure.

Driven through the HTTP interface against a temporary database. Every name
and figure here is invented. Anchor amounts are whole minor units (cents) of
the account's currency; nothing here reads a transaction amount.
"""

import pytest

from ingest import ensure_account, ensure_statement

KINDS = ["bank", "card", "loan", "holding", "company", "person"]


def make_account(client, name: str, kind: str, **extra) -> int:
    resp = client.post("/api/accounts", json={"name": name, "type": kind, **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def accounts(client) -> dict:
    return {a["name"]: a for a in client.get("/api/accounts").get_json()}


def enter(client, account_id: int, amount, on: str = "2026-08-31", **extra):
    return client.post("/api/anchors", json={
        "account_id": account_id, "amount": amount, "date": on, **extra,
    })


def anchors(client, account_id: int | None = None) -> list[dict]:
    url = "/api/anchors" if account_id is None else f"/api/anchors?account_id={account_id}"
    return client.get(url).get_json()


# --- account kinds and owners -------------------------------------------------


def test_the_kinds_and_owners_are_listed_with_a_description_each(client):
    listed = client.get("/api/account-kinds").get_json()

    assert [k["name"] for k in listed["kinds"]] == KINDS
    assert [o["name"] for o in listed["owners"]] == ["Household", "Moom", "Kalesh"]
    assert [s["name"] for s in listed["anchor_sources"]] == ["statement", "supplied"]
    for member in listed["kinds"] + listed["owners"] + listed["anchor_sources"]:
        assert member["description"].strip(), member["name"]
    # The kinds the enter-a-figure dialog offers.
    assert [k["name"] for k in listed["kinds"] if k["takes_a_figure"]] == [
        "loan", "holding", "company", "person",
    ]


def test_an_account_can_be_created_with_each_kind(client):
    for kind in KINDS:
        make_account(client, f"Sample {kind}", kind)

    listed = accounts(client)
    assert {name: a["type"] for name, a in listed.items()} == {
        f"Sample {kind}": kind for kind in KINDS
    }
    assert {a["owner"] for a in listed.values()} == {"Household"}


def test_an_account_with_no_kind_given_is_a_household_card(client):
    resp = client.post("/api/accounts", json={"name": "Sample Plain 0001"})

    assert resp.status_code == 200
    made = accounts(client)["Sample Plain 0001"]
    assert (made["type"], made["owner"]) == ("card", "Household")


def test_an_account_can_be_owned_by_a_company(client):
    make_account(client, "Sample Business 0002", "bank", owner="Kalesh")

    assert accounts(client)["Sample Business 0002"]["owner"] == "Kalesh"


@pytest.mark.parametrize("body", [
    {"type": "credit_card"},
    {"type": "debit"},
    {"type": "savings"},
    {"type": ""},
    {"type": None},
    {"type": 3},
    {"owner": "Someone Else"},
    {"owner": ""},
    {"owner": None},
])
def test_creating_an_account_with_an_unknown_kind_or_owner_is_refused(client, body):
    resp = client.post("/api/accounts", json={"name": "Sample Refused", **body})

    assert resp.status_code == 400
    assert "unknown" in resp.get_json()["error"]
    assert accounts(client) == {}


@pytest.mark.parametrize("body", [
    {"type": "credit_card"},
    {"type": None},
    {"owner": "Someone Else"},
    {"name": "Renamed", "owner": None},
])
def test_updating_an_account_to_an_unknown_kind_or_owner_is_refused(client, body):
    account_id = make_account(client, "Sample Loan", "loan")

    resp = client.put(f"/api/accounts/{account_id}", json=body)

    assert resp.status_code == 400
    assert "unknown" in resp.get_json()["error"]
    kept = accounts(client)["Sample Loan"]
    assert (kept["type"], kept["owner"]) == ("loan", "Household")


def test_an_account_can_be_updated_to_another_kind_and_owner(client):
    account_id = make_account(client, "Sample Business Card 0003", "bank")

    resp = client.put(f"/api/accounts/{account_id}", json={"type": "card", "owner": "Kalesh"})

    assert resp.status_code == 200, resp.get_json()
    changed = accounts(client)["Sample Business Card 0003"]
    assert (changed["type"], changed["owner"]) == ("card", "Kalesh")


def test_the_import_creates_cards_and_bank_accounts_and_refuses_any_other_word(client, conn):
    ensure_account(conn, "Sample Card 0004", "card")
    ensure_account(conn, "Sample Bank 0005", "bank")
    with pytest.raises(ValueError, match="unknown account kind"):
        ensure_account(conn, "Sample Card 0006", "credit_card")
    conn.commit()  # the helper leaves the commit to its caller, as the import does

    listed = accounts(client)
    assert {name: (a["type"], a["owner"]) for name, a in listed.items()} == {
        "Sample Card 0004": ("card", "Household"),
        "Sample Bank 0005": ("bank", "Household"),
    }


def test_statement_coverage_lists_only_accounts_that_have_statements(client):
    for kind in KINDS:
        make_account(client, f"Sample {kind}", kind)

    covered = client.get("/api/statements/coverage").get_json()

    assert sorted(a["short_name"] for a in covered["accounts"]) == ["Sample bank", "Sample card"]
    assert covered["summary"]["total"] == 2


# --- P3: a supplied figure is saved and shown ---------------------------------


def test_a_figure_supplied_for_a_holding_is_saved_and_shown(client):
    home = make_account(client, "Sample Home", "holding")

    resp = enter(client, home, "1900000.00", on="2026-01-01", note="agent's estimate")

    assert resp.status_code == 200, resp.get_json()
    saved = resp.get_json()
    assert saved["created"] is True
    assert saved["anchor"]["amount_minor"] == 190000000
    assert anchors(client, home) == [{
        "id": saved["anchor"]["id"],
        "account_id": home,
        "account_name": "Sample Home",
        "kind": "holding",
        "currency": "SGD",
        "date": "2026-01-01",
        "amount_minor": 190000000,
        "source": "supplied",
        "note": "agent's estimate",
    }]
    # The account itself says what it rests on.
    assert accounts(client)["Sample Home"]["anchor"] == {
        "date": "2026-01-01", "amount_minor": 190000000, "source": "supplied",
    }


def test_a_figure_supplied_for_a_loan_is_what_is_owed_and_is_stored_negative(client):
    loan = make_account(client, "Sample Home Loan", "loan")

    resp = enter(client, loan, "902,500.05", note="from the paper statement")

    assert resp.status_code == 200, resp.get_json()
    [anchor] = anchors(client, loan)
    assert (anchor["amount_minor"], anchor["source"], anchor["note"]) == (
        -90250005, "supplied", "from the paper statement",
    )


def test_a_company_opening_amount_and_a_person_take_a_figure_of_either_sign(client):
    company = make_account(client, "Sample Company", "company")
    person = make_account(client, "Sample Friend", "person")

    assert enter(client, company, "61800", on="2026-01-01").status_code == 200
    assert enter(client, person, "-250.50", on="2026-01-01").status_code == 200

    assert [a["amount_minor"] for a in anchors(client, company)] == [6180000]
    assert [a["amount_minor"] for a in anchors(client, person)] == [-25050]


def test_an_account_with_no_figure_says_so_and_rests_on_its_latest_figure(client):
    car = make_account(client, "Sample Car", "holding")
    assert accounts(client)["Sample Car"]["anchor"] is None

    enter(client, car, "120000.00", on="2026-01-01")
    enter(client, car, "110000.00", on="2026-07-15")
    enter(client, car, "115000.00", on="2026-03-31")

    assert accounts(client)["Sample Car"]["anchor"] == {
        "date": "2026-07-15", "amount_minor": 11000000, "source": "supplied",
    }
    assert [(a["date"], a["amount_minor"]) for a in anchors(client, car)] == [
        ("2026-07-15", 11000000), ("2026-03-31", 11500000), ("2026-01-01", 12000000),
    ]


def test_the_anchor_list_covers_every_account_or_just_one(client):
    car = make_account(client, "Sample Car", "holding")
    loan = make_account(client, "Sample Auto Loan", "loan")
    enter(client, car, "120000.00")
    enter(client, loan, "118400.00")

    assert {(a["account_name"], a["amount_minor"]) for a in anchors(client)} == {
        ("Sample Car", 12000000), ("Sample Auto Loan", -11840000),
    }
    assert [a["account_name"] for a in anchors(client, loan)] == ["Sample Auto Loan"]


# --- replay: the same figure again changes nothing -----------------------------


def test_the_same_figure_again_is_accepted_and_changes_nothing(client):
    loan = make_account(client, "Sample Home Loan", "loan")
    first = enter(client, loan, "902500.00", note="from the paper statement").get_json()
    before = anchors(client)

    again = enter(client, loan, "902500", note="typed a second time")

    assert again.status_code == 200, again.get_json()
    assert again.get_json()["created"] is False
    assert again.get_json()["anchor"] == first["anchor"]
    assert anchors(client) == before
    assert before[0]["note"] == "from the paper statement"


# --- P6: a second, different anchor for the same account and date -------------


def test_a_different_figure_for_the_same_account_and_date_is_refused(client):
    loan = make_account(client, "Sample Home Loan", "loan")
    enter(client, loan, "902500.00", on="2026-08-31")
    before = anchors(client)

    resp = enter(client, loan, "902500.01", on="2026-08-31")

    assert resp.status_code == 409
    error = resp.get_json()["error"]
    assert "2026-08-31" in error and "902,500.00" in error
    assert anchors(client) == before
    assert [a["amount_minor"] for a in before] == [-90250000]


def test_a_figure_for_another_date_or_another_account_is_not_a_conflict(client):
    loan = make_account(client, "Sample Home Loan", "loan")
    other = make_account(client, "Sample Auto Loan", "loan")
    enter(client, loan, "902500.00", on="2026-08-31")

    assert enter(client, loan, "910000.00", on="2026-06-30").status_code == 200
    assert enter(client, other, "118400.00", on="2026-08-31").status_code == 200

    assert sorted((a["account_name"], a["date"], a["amount_minor"]) for a in anchors(client)) == [
        ("Sample Auto Loan", "2026-08-31", -11840000),
        ("Sample Home Loan", "2026-06-30", -91000000),
        ("Sample Home Loan", "2026-08-31", -90250000),
    ]


# --- what the endpoint will not take -------------------------------------------


@pytest.mark.parametrize("amount", [
    "12.345",       # a fraction of a cent
    "abc",
    "",
    None,
    12.5,           # a float: amounts arrive as text
    True,
    "NaN",
    "Infinity",
    "1e3",
])
def test_an_amount_that_is_not_an_exact_figure_is_refused(client, amount):
    car = make_account(client, "Sample Car", "holding")

    resp = enter(client, car, amount)

    assert resp.status_code == 400, resp.get_json()
    assert "amount" in resp.get_json()["error"]
    assert anchors(client) == []


def test_a_whole_number_amount_is_taken_as_whole_units(client):
    car = make_account(client, "Sample Car", "holding")

    assert enter(client, car, 120000).status_code == 200

    assert [a["amount_minor"] for a in anchors(client)] == [12000000]


@pytest.mark.parametrize("on", ["31 Aug 2026", "2026-02-30", "2026-8-1", "", None, 20260831])
def test_a_date_that_is_not_a_real_day_is_refused(client, on):
    car = make_account(client, "Sample Car", "holding")

    resp = enter(client, car, "120000.00", on=on)

    assert resp.status_code == 400
    assert "date" in resp.get_json()["error"]
    assert anchors(client) == []


def test_a_negative_figure_for_a_loan_is_refused(client):
    loan = make_account(client, "Sample Home Loan", "loan")

    resp = enter(client, loan, "-902500.00")

    assert resp.status_code == 400
    assert "owed" in resp.get_json()["error"]
    assert anchors(client) == []


@pytest.mark.parametrize("kind", ["bank", "card"])
def test_a_bank_account_or_card_that_has_a_statement_takes_no_supplied_figure(client, conn, kind):
    account_id = make_account(client, f"Sample {kind} 0007", kind)
    ensure_statement(conn, account_id, "2026-07-31", "sample.pdf")
    conn.commit()

    resp = enter(client, account_id, "100.00")

    assert resp.status_code == 400
    assert "statement" in resp.get_json()["error"]
    assert anchors(client) == []
    assert accounts(client)[f"Sample {kind} 0007"]["takes_a_figure"] is False


def test_a_card_with_no_statement_still_takes_no_supplied_figure(client):
    card = make_account(client, "Sample card 0008", "card")

    resp = enter(client, card, "100.00")

    assert resp.status_code == 400
    assert "statement" in resp.get_json()["error"]
    assert anchors(client) == []
    assert accounts(client)["Sample card 0008"]["takes_a_figure"] is False


def test_a_bank_account_with_no_statement_takes_a_supplied_figure(client):
    deposit = make_account(client, "Sample Deposit", "bank")
    assert accounts(client)["Sample Deposit"]["takes_a_figure"] is True

    resp = enter(client, deposit, "50,000.25", on="2026-01-01", note="from the bank's letter")

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["created"] is True
    [anchor] = anchors(client, deposit)
    assert (anchor["kind"], anchor["amount_minor"], anchor["source"], anchor["note"]) == (
        "bank", 5000025, "supplied", "from the bank's letter",
    )
    assert accounts(client)["Sample Deposit"]["anchor"] == {
        "date": "2026-01-01", "amount_minor": 5000025, "source": "supplied",
    }


def test_a_bank_account_stops_taking_a_figure_once_it_has_a_statement(client, conn):
    deposit = make_account(client, "Sample Deposit", "bank")
    assert enter(client, deposit, "50000.00", on="2026-01-01").status_code == 200
    ensure_statement(conn, deposit, "2026-07-31", "sample.pdf")
    conn.commit()

    resp = enter(client, deposit, "51000.00", on="2026-08-31")

    assert resp.status_code == 400
    assert "statement" in resp.get_json()["error"]
    assert [(a["date"], a["amount_minor"]) for a in anchors(client, deposit)] == [
        ("2026-01-01", 5000000),
    ]


def test_each_account_says_whether_it_takes_a_figure(client):
    for kind in KINDS:
        make_account(client, f"Sample {kind}", kind)

    assert {name: a["takes_a_figure"] for name, a in accounts(client).items()} == {
        "Sample bank": True, "Sample card": False, "Sample loan": True,
        "Sample holding": True, "Sample company": True, "Sample person": True,
    }


@pytest.mark.parametrize("account_id", [999, None, "1", True])
def test_a_figure_for_no_known_account_is_refused(client, account_id):
    make_account(client, "Sample Car", "holding")

    resp = enter(client, account_id, "100.00")

    assert resp.status_code in (400, 404)
    assert "account" in resp.get_json()["error"]
    assert anchors(client) == []


def test_the_source_is_never_taken_from_the_request(client):
    car = make_account(client, "Sample Car", "holding")

    enter(client, car, "120000.00", source="statement")

    assert [a["source"] for a in anchors(client)] == ["supplied"]


def test_an_account_with_a_figure_cannot_be_deleted(client):
    car = make_account(client, "Sample Car", "holding")
    enter(client, car, "120000.00")

    resp = client.delete(f"/api/accounts/{car}")

    assert resp.status_code == 400
    assert "figure" in resp.get_json()["error"]
    assert "Sample Car" in accounts(client)
    assert len(anchors(client)) == 1
