"""Movements, the other side, and the review list, through the HTTP interface.

Rows reach the ledger the way the operator's do: a statement is uploaded and
confirmed. The statement itself is a stand-in parser returning invented rows.
Every name and figure is invented.
"""

import io

import pytest

import money
import parsers
from parse_dbs import ParsedStatement, ParsedTransaction

BANK = "Sample Bank 0002"
CARD = "Sample Card 0001"
MONTH = "2026-08"

FLOWS = ["expense", "income", "transfer", "payment", "refund", "movement", "review"]


# --- helpers --------------------------------------------------------------------


def make_account(client, name: str, kind: str, **extra) -> int:
    resp = client.post("/api/accounts", json={"name": name, "type": kind, **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def account_named(client, name: str) -> dict | None:
    return {a["name"]: a for a in client.get("/api/accounts").get_json()}.get(name)


@pytest.fixture
def ledger(client) -> dict:
    """The accounts the ledger starts with: a household bank account and card,
    the two companies, the crypto holding and the two loans."""
    return {
        BANK: make_account(client, BANK, "bank", last_four="0002"),
        CARD: make_account(client, CARD, "card", last_four="0001"),
        "Moom": make_account(client, "Moom", "company"),
        "Kalesh": make_account(client, "Kalesh", "company"),
        "Crypto held outside fin": make_account(client, "Crypto held outside fin", "holding"),
        "UOB home loan": make_account(client, "UOB home loan", "loan"),
        "DBS auto loan": make_account(client, "DBS auto loan", "loan"),
    }


def upload(client, account: str, rows: list[tuple], kind: str = "bank") -> dict:
    """Upload a statement of (day, description, amount) rows for one account
    and return the preview."""
    def parse(_path):
        return [ParsedStatement(
            statement_type=kind,
            statement_date=f"{MONTH}-31",
            accounts=[account],
            filename="sample.csv",
            transactions=[
                ParsedTransaction(date=f"{MONTH}-{day:02d}", description=description,
                                  amount_minor=money.to_minor(amount))
                for day, description, amount in rows
            ],
        )]

    parsers._PARSERS.insert(0, {
        "name": "Stand-in", "ext": ".csv", "detect_fn": lambda _path: True, "parse_fn": parse,
    })
    try:
        resp = client.post(
            "/api/import/upload",
            data={"files": (io.BytesIO(b"stand-in"), "sample.csv")},
            content_type="multipart/form-data",
        )
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def confirm(client, preview: dict):
    """Confirm a preview with every row kept."""
    groups = preview["groups"]
    for group in groups:
        for tx in group["transactions"]:
            tx["_skip"] = False
    return client.post("/api/import/confirm", json={"import_id": preview["import_id"], "groups": groups})


def bring_in(client, account: str, rows: list[tuple], kind: str = "bank") -> None:
    resp = confirm(client, upload(client, account, rows, kind))
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["transactions_saved"] == len(rows)


def listed(client, **params) -> list[dict]:
    query = "&".join(f"{k}={v}" for k, v in {"per_page": 200, **params}.items())
    return client.get(f"/api/transactions?{query}").get_json()["transactions"]


def row(client, description: str) -> dict:
    found = [tx for tx in listed(client) if tx["description"] == description]
    assert len(found) == 1, description
    return found[0]


def review_list(client) -> list[dict]:
    """The review list as the screen reads it: largest first."""
    return listed(client, flow="review", sort="amount", sort_dir="desc")


def label(client, description: str, **body):
    return client.post(f"/api/review/{row(client, description)['id']}/label", json=body)


def labelled(client, description: str, **body) -> dict:
    resp = label(client, description, **body)
    assert resp.status_code == 200, resp.get_json()
    return row(client, description)


def what_it_is(tx: dict) -> tuple:
    return (tx["flow_type"], tx["other_side_name"], tx["book"], tx["display_type"])


def spending_type(client, name: str) -> int:
    return {t["display_name"]: t["id"] for t in client.get("/api/types").get_json()}[name]


def income_kind(client, name: str) -> int:
    return {t["name"]: t["id"] for t in client.get("/api/types?kind=income").get_json()}[name]


def household_spend(client) -> float:
    return client.get(f"/api/dashboard/stat-cards?ref_month={MONTH}").get_json()["household"]


def total_of(rows: list[dict]) -> float:
    return round(sum(tx["amount_sgd"] for tx in rows), 2)


TRANSFERS = [
    (12, "OUTWARD TELEGRAPHIC TRANSFER REF 550012", 40000.00),
    (3, "FAST PAYMENT REF 771203 OTHR", 25000.00),
    (21, "PAYNOW TO SAMPLE PAYEE REF 9931", 12000.50),
]


@pytest.fixture
def waiting(client, ledger) -> dict:
    """Three transfers on the review list, and one typed card purchase."""
    bring_in(client, BANK, TRANSFERS)
    bring_in(client, CARD, [(5, "SAMPLE BISTRO 0042", 80.25)], kind="credit_card")
    return ledger


# --- the flow list: declared once --------------------------------------------------


def test_the_flow_list_is_declared_once_with_a_description_each(client):
    flows = client.get("/api/flows").get_json()

    assert [f["name"] for f in flows] == FLOWS
    assert all(f["description"] for f in flows)


@pytest.mark.parametrize("flow", ["movement", "review"])
def test_the_two_new_flows_are_accepted_where_a_flow_is_set_by_hand(client, waiting, flow):
    tx = row(client, "SAMPLE BISTRO 0042")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx["id"], "service_name": "Sample Bistro",
        "type_id": spending_type(client, "Dining"), "flow_type": flow,
    })

    assert resp.status_code == 200, resp.get_json()
    assert row(client, "SAMPLE BISTRO 0042")["flow_type"] == flow


# --- refusal: an unknown flow is refused at every writer that accepts one ----------


def test_resolve_refuses_a_flow_that_is_not_declared(client, waiting):
    before = row(client, "SAMPLE BISTRO 0042")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": before["id"], "service_name": "Sample Bistro",
        "type_id": spending_type(client, "Dining"), "flow_type": "capital",
    })

    assert resp.status_code == 400
    assert "capital" in resp.get_json()["error"]
    assert row(client, "SAMPLE BISTRO 0042") == before


def test_the_import_refuses_a_flow_that_is_not_declared_and_writes_nothing(client, ledger):
    preview = upload(client, BANK, TRANSFERS)
    preview["groups"][0]["transactions"][1]["flow_type"] = "capital"

    resp = confirm(client, preview)

    assert resp.status_code == 400
    assert "capital" in resp.get_json()["error"]
    assert listed(client) == []


def test_the_import_refuses_an_other_side_that_is_no_account(client, ledger):
    preview = upload(client, BANK, TRANSFERS)
    preview["groups"][0]["transactions"][0]["other_side_id"] = 987654

    resp = confirm(client, preview)

    assert resp.status_code == 400
    assert listed(client) == []


def test_a_label_that_is_not_one_of_the_choices_is_refused(client, waiting):
    before = row(client, TRANSFERS[0][1])

    resp = label(client, TRANSFERS[0][1], choice="write_off")

    assert resp.status_code == 400
    assert row(client, TRANSFERS[0][1]) == before


@pytest.mark.parametrize("body", [
    {"choice": "spending"},                                  # no type
    {"choice": "spending", "type_id": 987654},               # no such type
    {"choice": "spending", "type_id": "Dining"},             # a name, not a type
    {"choice": "spending", "type_id": 1, "book": "Acme"},    # no such book
    {"choice": "income"},                                    # no kind
    {"choice": "income", "income_kind_id": 987654},
    {"choice": "own_account"},                               # no account
    {"choice": "own_account", "account_id": 987654},
    {"choice": "company"},
    {"choice": "loan_to_person"},                            # nobody named
    {"choice": "loan_to_person", "person": "   "},
    {"choice": "loan_repayment"},
])
def test_a_label_missing_what_it_asks_for_is_refused_and_changes_nothing(client, waiting, body):
    before = listed(client)

    resp = label(client, TRANSFERS[0][1], **body)

    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json()["error"]
    assert listed(client) == before
    assert account_named(client, "   ") is None


@pytest.mark.parametrize("choice, wrong", [
    ("own_account", "Moom"),              # a company is not my own account
    ("own_account", BANK),                # the account the row is on
    ("company", "UOB home loan"),
    ("company", CARD),
    ("loan_repayment", "Moom"),
    ("loan_repayment", "Crypto held outside fin"),
    ("loan_to_person", "Kalesh"),
])
def test_a_label_naming_an_account_of_the_wrong_kind_is_refused(client, waiting, choice, wrong):
    before = listed(client)

    resp = label(client, TRANSFERS[0][1], choice=choice, account_id=waiting[wrong])

    assert resp.status_code == 400, resp.get_json()
    assert listed(client) == before


def test_an_income_kind_is_not_a_spending_type_and_the_reverse(client, waiting):
    before = listed(client)

    as_spending = label(client, TRANSFERS[0][1], choice="spending", type_id=income_kind(client, "Salary"))
    as_income = label(
        client, TRANSFERS[0][1], choice="income", income_kind_id=spending_type(client, "Dining")
    )

    assert (as_spending.status_code, as_income.status_code) == (400, 400)
    assert listed(client) == before


def test_a_label_for_no_such_row_is_refused(client, waiting):
    resp = client.post("/api/review/987654/label", json={
        "choice": "company", "account_id": waiting["Moom"],
    })

    assert resp.status_code == 404


# --- what lands on the review list -------------------------------------------------


def test_an_unlabelled_transfer_on_a_household_bank_account_lands_on_review(client, ledger):
    preview = upload(client, BANK, TRANSFERS + [(9, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410", -250000.00)])

    flows = {tx["description"]: tx["flow_type"] for tx in preview["groups"][0]["transactions"]}
    assert set(flows.values()) == {"review"}
    assert all(tx["_skip"] is False for tx in preview["groups"][0]["transactions"])


def test_the_review_list_shows_the_waiting_transfers_largest_first(client, waiting):
    rows = review_list(client)

    assert [(tx["description"], tx["amount_sgd"], tx["account_name"]) for tx in rows] == [
        ("OUTWARD TELEGRAPHIC TRANSFER REF 550012", 40000.00, BANK),
        ("FAST PAYMENT REF 771203 OTHR", 25000.00, BANK),
        ("PAYNOW TO SAMPLE PAYEE REF 9931", 12000.50, BANK),
    ]
    assert client.get("/api/review").get_json()["waiting"] == 3


def test_rows_on_review_are_held_out_of_household_spending(client, waiting):
    assert household_spend(client) == 80.25
    assert total_of(listed(client)) == 77080.75  # every row is still in the ledger


def test_a_transfer_a_merchant_rule_knows_is_spending_not_review(client, ledger):
    svc = client.post("/api/services", json={
        "name": "Sample Landlord", "book": "Household", "type_id": spending_type(client, "Rent"),
    }).get_json()["id"]
    assert client.post("/api/rules", json={"pattern": "SAMPLE LANDLORD", "service_id": svc}).status_code == 200

    bring_in(client, BANK, [
        (1, "PAYNOW TO SAMPLE LANDLORD REF 1200", 4200.00),
        (2, "PAYNOW TO SINGAPORE LIFE REF 1201", 310.40),   # the payee wording gives a type
    ])

    assert what_it_is(row(client, "PAYNOW TO SAMPLE LANDLORD REF 1200")) == (
        "expense", None, "Household", "Rent",
    )
    assert what_it_is(row(client, "PAYNOW TO SINGAPORE LIFE REF 1201")) == (
        "expense", None, "Household", "Insurance",
    )
    assert review_list(client) == []
    assert household_spend(client) == 4510.40


def test_a_card_purchase_with_no_type_is_spending_as_before(client, ledger):
    bring_in(client, CARD, [(4, "PAYNOW TO SAMPLE PAYEE REF 9931", 55.00)], kind="credit_card")

    assert row(client, "PAYNOW TO SAMPLE PAYEE REF 9931")["flow_type"] == "expense"
    assert review_list(client) == []


def test_a_transfer_on_a_companys_bank_account_does_not_land_on_review(client, ledger):
    make_account(client, "Sample Business Bank 0007", "bank", owner="Kalesh", last_four="0007")

    bring_in(client, "Sample Business Bank 0007", [(4, "PAYNOW TO SAMPLE PAYEE REF 9931", 900.00)])

    assert row(client, "PAYNOW TO SAMPLE PAYEE REF 9931")["flow_type"] == "expense"
    assert review_list(client) == []


def test_a_bank_row_that_is_not_a_transfer_is_classified_as_before(client, ledger):
    bring_in(client, BANK, [
        (6, "INTEREST EARNED", -12.34),
        (7, "POINT-OF-SALE SAMPLE GROCER 0042", 61.10),
        (8, "Inward Credit-FAST OTHR Other SAMPLE TENANT", -3100.00),
    ])

    assert [(tx["description"], tx["flow_type"]) for tx in listed(client, sort="date", sort_dir="asc")] == [
        ("INTEREST EARNED", "income"),
        ("POINT-OF-SALE SAMPLE GROCER 0042", "expense"),
        ("Inward Credit-FAST OTHR Other SAMPLE TENANT", "income"),
    ]


def test_a_holding_or_company_account_is_not_an_alias_for_my_own_accounts(client, ledger):
    """An account that is not a bank account or card never makes a row a move
    between my own accounts: 'Car' is inside 'CARD'."""
    make_account(client, "Car", "holding")
    make_account(client, "Home", "holding")

    bring_in(client, CARD, [
        (4, "SAMPLE CARD SHOP HOMEWARE 0042", 19.90),
        (5, "MOOM SAMPLE SUPPLIES", 44.00),
    ], kind="credit_card")

    assert {tx["flow_type"] for tx in listed(client)} == {"expense"}


# --- the rules that name the other side at import ---------------------------------


RULED = [
    (1, "INWARD PAYNOW FROM MOOM PTE LTD REF 3001", -18250.75),
    (2, "GIRO SALARY MOOM PTE LTD", -9000.00),
    (3, "INWARD PAYNOW FROM KALESH INC REF 3002", -6500.00),
    (4, "INWARD CREDIT INDEPENDENT RESERVE SG REF 3003", -42000.00),
    (5, "SAMPLE HOME LOAN INSTALMENT 08", 8800.00),
    (6, "SAMPLE CAR LOAN INSTALMENT 08", 2000.00),
]


@pytest.fixture
def loan_merchants(client, ledger) -> dict:
    """The two loan merchants as the ledger knows them, each with a rule."""
    for name, pattern in (("UOB Home Loan", "SAMPLE HOME LOAN"), ("Car Loan", "SAMPLE CAR LOAN")):
        svc = client.post("/api/services", json={"name": name}).get_json()["id"]
        assert client.post("/api/rules", json={"pattern": pattern, "service_id": svc}).status_code == 200
    return ledger


def test_rows_the_rules_know_name_their_other_side_at_import(client, loan_merchants):
    bring_in(client, BANK, RULED)

    assert {tx["description"]: (tx["flow_type"], tx["other_side_name"]) for tx in listed(client)} == {
        "INWARD PAYNOW FROM MOOM PTE LTD REF 3001": ("movement", "Moom"),
        "GIRO SALARY MOOM PTE LTD": ("income", None),
        "INWARD PAYNOW FROM KALESH INC REF 3002": ("income", None),
        "INWARD CREDIT INDEPENDENT RESERVE SG REF 3003": ("movement", "Crypto held outside fin"),
        "SAMPLE HOME LOAN INSTALMENT 08": ("movement", "UOB home loan"),
        "SAMPLE CAR LOAN INSTALMENT 08": ("movement", "DBS auto loan"),
    }
    assert all(tx["flow_type_manual"] == 0 for tx in listed(client))
    assert review_list(client) == []
    assert household_spend(client) == 0  # an instalment is no longer spending
    assert total_of(listed(client)) == -64950.75


def test_the_same_wording_on_a_card_names_no_other_side(client, loan_merchants):
    bring_in(client, CARD, [(1, "INWARD PAYNOW FROM MOOM PTE LTD REF 3001", -18250.75)], kind="credit_card")

    assert (row(client, RULED[0][1])["flow_type"], row(client, RULED[0][1])["other_side_name"]) == (
        "income", None,
    )


# --- allowed: each review-list label writes the stated flow and other side ---------


def test_the_choices_are_listed_with_a_description_each(client):
    choices = client.get("/api/review").get_json()["choices"]

    assert [(c["name"], c["flow"]) for c in choices] == [
        ("spending", "expense"),
        ("gift", "expense"),
        ("own_account", "transfer"),
        ("company", "movement"),
        ("loan_to_person", "movement"),
        ("loan_repayment", "movement"),
        ("income", "income"),
    ]
    assert all(c["label"] and c["description"] for c in choices)


def test_labelled_spending_takes_its_type_and_counts_as_household_spending(client, waiting):
    tx = labelled(client, TRANSFERS[2][1], choice="spending", type_id=spending_type(client, "Education"))

    assert what_it_is(tx) == ("expense", None, "Household", "Education")
    assert (tx["flow_type_manual"], tx["cat_source"]) == (1, "manual")
    assert household_spend(client) == 12080.75  # 80.25 + 12,000.50, to the cent
    assert len(review_list(client)) == 2


def test_labelled_spending_can_be_a_companys(client, waiting):
    tx = labelled(
        client, TRANSFERS[2][1],
        choice="spending", type_id=spending_type(client, "Stock purchases"), book="Moom",
    )

    assert what_it_is(tx) == ("expense", None, "Moom", "Stock purchases")
    assert household_spend(client) == 80.25


def test_spending_whose_type_does_not_say_whose_it_is_needs_a_book(client, waiting):
    software = spending_type(client, "Software & AI tools")

    refused = label(client, TRANSFERS[2][1], choice="spending", type_id=software)
    tx = labelled(client, TRANSFERS[2][1], choice="spending", type_id=software, book="Kalesh")

    assert refused.status_code == 400
    assert what_it_is(tx) == ("expense", None, "Kalesh", "Software & AI tools")


def test_a_gift_given_is_spending_under_gifts(client, waiting):
    tx = labelled(client, TRANSFERS[1][1], choice="gift")

    assert what_it_is(tx) == ("expense", None, "Household", "Gifts & Donations")
    assert tx["flow_type_manual"] == 1
    assert household_spend(client) == 25080.25


def test_a_gift_received_is_income(client, waiting):
    bring_in(client, BANK, [(9, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410", -5000.00)])

    tx = labelled(client, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410", choice="gift")

    assert what_it_is(tx) == ("income", None, "Household", "Gift received")
    assert household_spend(client) == 80.25


def test_a_move_to_my_own_account_is_a_transfer_naming_that_account(client, waiting):
    savings = make_account(client, "Sample Savings 0009", "bank", last_four="0009")

    tx = labelled(client, TRANSFERS[0][1], choice="own_account", account_id=savings)

    assert what_it_is(tx) == ("transfer", "Sample Savings 0009", "Household", "")
    assert (tx["other_side_id"], tx["flow_type_manual"]) == (savings, 1)
    assert household_spend(client) == 80.25


def test_money_into_a_company_is_a_movement_naming_the_company(client, waiting):
    tx = labelled(client, TRANSFERS[1][1], choice="company", account_id=waiting["Kalesh"])

    assert what_it_is(tx) == ("movement", "Kalesh", "Household", "")
    assert tx["flow_type_manual"] == 1


def test_a_loan_to_a_new_person_creates_their_account(client, waiting):
    assert account_named(client, "Sample Friend") is None

    resp = label(client, TRANSFERS[2][1], choice="loan_to_person", person="  Sample Friend ")

    assert resp.status_code == 200, resp.get_json()
    friend = account_named(client, "Sample Friend")
    assert (friend["type"], friend["owner"], friend["anchor"]) == ("person", "Household", None)
    assert resp.get_json()["created_account"] is True
    tx = row(client, TRANSFERS[2][1])
    assert what_it_is(tx) == ("movement", "Sample Friend", "Household", "")
    assert tx["other_side_id"] == friend["id"]


def test_a_second_loan_to_the_same_person_uses_the_account_they_have(client, waiting):
    labelled(client, TRANSFERS[2][1], choice="loan_to_person", person="Sample Friend")
    people = len(client.get("/api/accounts").get_json())

    by_name = label(client, TRANSFERS[1][1], choice="loan_to_person", person="sample friend")
    friend = account_named(client, "Sample Friend")
    by_account = label(client, TRANSFERS[0][1], choice="loan_to_person", account_id=friend["id"])

    assert (by_name.status_code, by_account.status_code) == (200, 200)
    assert by_name.get_json()["created_account"] is False
    assert len(client.get("/api/accounts").get_json()) == people
    assert {tx["other_side_id"] for tx in listed(client) if tx["flow_type"] == "movement"} == {friend["id"]}


def test_a_loan_repayment_is_a_movement_naming_the_loan(client, waiting):
    tx = labelled(client, TRANSFERS[2][1], choice="loan_repayment", account_id=waiting["DBS auto loan"])

    assert what_it_is(tx) == ("movement", "DBS auto loan", "Household", "")
    assert tx["flow_type_manual"] == 1
    assert household_spend(client) == 80.25


def test_income_takes_its_kind(client, waiting):
    bring_in(client, BANK, [(9, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410", -3100.00)])

    tx = labelled(
        client, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410",
        choice="income", income_kind_id=income_kind(client, "Rental"),
    )

    assert what_it_is(tx) == ("income", None, "Household", "Rental")
    assert tx["flow_type_manual"] == 1


def test_labelling_every_row_empties_the_list_and_moves_no_money(client, waiting):
    before = total_of(listed(client))

    labelled(client, TRANSFERS[0][1], choice="company", account_id=waiting["Moom"])
    labelled(client, TRANSFERS[1][1], choice="loan_to_person", person="Sample Friend")
    labelled(client, TRANSFERS[2][1], choice="gift")

    assert review_list(client) == []
    assert client.get("/api/review").get_json()["waiting"] == 0
    assert total_of(listed(client)) == before == 77080.75
    assert household_spend(client) == 12080.75


def test_a_label_can_be_changed_and_the_last_one_stands(client, waiting):
    labelled(client, TRANSFERS[0][1], choice="company", account_id=waiting["Moom"])

    tx = labelled(client, TRANSFERS[0][1], choice="spending", type_id=spending_type(client, "Travel"))

    assert what_it_is(tx) == ("expense", None, "Household", "Travel")
    assert tx["other_side_id"] is None


def test_an_account_named_as_an_other_side_cannot_be_deleted(client, waiting):
    labelled(client, TRANSFERS[2][1], choice="loan_to_person", person="Sample Friend")
    friend = account_named(client, "Sample Friend")["id"]

    resp = client.delete(f"/api/accounts/{friend}")

    assert resp.status_code == 400
    assert account_named(client, "Sample Friend") is not None


# --- replay: a rule re-run leaves what was set by hand -----------------------------


def test_recategorize_all_and_a_rule_rerun_leave_hand_set_rows_unchanged(client, waiting):
    bring_in(client, BANK, [(9, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410", -3100.00)])
    labelled(client, TRANSFERS[0][1], choice="company", account_id=waiting["Moom"])
    labelled(client, TRANSFERS[1][1], choice="loan_to_person", person="Sample Friend")
    labelled(client, TRANSFERS[2][1], choice="spending", type_id=spending_type(client, "Education"))
    labelled(
        client, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410",
        choice="income", income_kind_id=income_kind(client, "Rental"),
    )
    # A hand-set flow on a row the rules labelled: the flow is manual, the label is not.
    bistro = row(client, "SAMPLE BISTRO 0042")
    assert client.post("/api/transactions/resolve", json={
        "tx_id": bistro["id"], "service_name": "Sample Bistro", "pattern": "SAMPLE BISTRO",
        "type_id": spending_type(client, "Dining"), "flow_type": "movement",
    }).status_code == 200
    before = listed(client, sort="date", sort_dir="asc")

    # A rule that now matches every one of the transfers.
    svc = client.post("/api/services", json={
        "name": "Sample Catch-all", "book": "Household", "type_id": spending_type(client, "Shopping"),
    }).get_json()["id"]
    rule = client.post("/api/rules", json={"pattern": "REF", "service_id": svc}).get_json()["id"]
    first = client.post("/api/rules/recategorize")
    rerun = client.put(f"/api/rules/{rule}", json={"priority": 5})
    second = client.post("/api/rules/recategorize")

    assert (first.status_code, rerun.status_code, second.status_code) == (200, 200, 200)
    assert rerun.get_json()["recategorized"] == 0
    assert listed(client, sort="date", sort_dir="asc") == before
    assert row(client, "SAMPLE BISTRO 0042")["flow_type"] == "movement"


def test_recategorize_all_keeps_the_other_side_the_rules_gave(client, loan_merchants):
    bring_in(client, BANK, RULED + TRANSFERS)
    before = listed(client, sort="date", sort_dir="asc")

    first = client.post("/api/rules/recategorize").get_json()
    second = client.post("/api/rules/recategorize").get_json()

    assert (first["updated"], second["updated"]) == (0, 0)
    assert listed(client, sort="date", sort_dir="asc") == before


def test_a_new_rule_takes_a_waiting_row_off_the_review_list(client, waiting):
    svc = client.post("/api/services", json={
        "name": "Sample Payee", "book": "Household", "type_id": spending_type(client, "Home"),
    }).get_json()["id"]
    client.post("/api/rules", json={"pattern": "SAMPLE PAYEE", "service_id": svc})

    client.post("/api/rules/recategorize")

    assert what_it_is(row(client, TRANSFERS[2][1])) == ("expense", None, "Household", "Home")
    assert [tx["description"] for tx in review_list(client)] == [TRANSFERS[0][1], TRANSFERS[1][1]]


def test_a_waiting_row_given_a_type_in_the_preview_is_saved_as_spending(client, ledger):
    preview = upload(client, BANK, TRANSFERS)
    typed = preview["groups"][0]["transactions"][2]
    assert typed["flow_type"] == "review"
    typed.update(type_id=spending_type(client, "Education"), book="Household")

    assert confirm(client, preview).status_code == 200

    assert what_it_is(row(client, TRANSFERS[2][1])) == ("expense", None, "Household", "Education")
    assert [tx["description"] for tx in review_list(client)] == [TRANSFERS[0][1], TRANSFERS[1][1]]
    assert household_spend(client) == 12000.50


# --- fin's guess for a waiting transfer: how the same text was labelled before --------

LANDLORD = "PAYNOW TO SAMPLE LANDLORD REF 4400"


def _landlord_rows(client) -> list[dict]:
    return sorted((tx for tx in listed(client) if tx["description"] == LANDLORD), key=lambda tx: tx["date"])


def test_a_transfer_gets_a_guess_once_two_earlier_rows_were_labelled_the_same_way(client, ledger):
    bring_in(client, BANK, [(1, LANDLORD, 2100.00), (2, LANDLORD, 2100.00), (3, LANDLORD, 2100.00)])
    first, second, third = _landlord_rows(client)
    rent = spending_type(client, "Rent")

    assert client.get(f"/api/review/{third['id']}/suggestion").get_json() == {"guess": None}
    client.post(f"/api/review/{first['id']}/label", json={"choice": "spending", "type_id": rent})
    # one earlier label is not enough to guess from
    assert client.get(f"/api/review/{third['id']}/suggestion").get_json() == {"guess": None}
    client.post(f"/api/review/{second['id']}/label", json={"choice": "spending", "type_id": rent})

    guess = client.get(f"/api/review/{third['id']}/suggestion").get_json()["guess"]
    assert (guess["choice"], guess["type"], guess["book"], guess["agree"], guess["of"], guess["probability"]) == (
        "spending", "Rent", "Household", 2, 2, 1.0)
    # the body is what the label takes, and saving it labels the row that way
    resp = client.post(f"/api/review/{third['id']}/label", json=guess["body"])
    assert resp.status_code == 200, resp.get_json()
    assert what_it_is(row_by_id(client, third["id"])) == ("expense", None, "Household", "Rent")


def test_a_guess_names_the_account_a_move_went_to_and_needs_most_labels_to_agree(client, ledger):
    bring_in(client, BANK, [(1, LANDLORD, 500.00), (2, LANDLORD, 500.00), (3, LANDLORD, 500.00), (4, LANDLORD, 500.00)])
    a, b, c, d = _landlord_rows(client)
    client.post(f"/api/review/{a['id']}/label", json={"choice": "own_account", "account_id": ledger[CARD]})
    client.post(f"/api/review/{b['id']}/label", json={"choice": "own_account", "account_id": ledger[CARD]})
    client.post(f"/api/review/{c['id']}/label", json={"choice": "company", "account_id": ledger["Moom"]})

    guess = client.get(f"/api/review/{d['id']}/suggestion").get_json()["guess"]
    assert (guess["choice"], guess["other_side"], guess["agree"], guess["of"]) == ("own_account", CARD, 2, 3)
    assert guess["body"] == {"choice": "own_account", "account_id": ledger[CARD]}

    client.post(f"/api/review/{b['id']}/label", json={"choice": "company", "account_id": ledger["Moom"]})
    # now two of three say company, one says own account: 2/3 still agrees enough
    assert client.get(f"/api/review/{d['id']}/suggestion").get_json()["guess"]["choice"] == "company"


def test_a_guess_never_crosses_money_in_and_money_out(client, ledger):
    bring_in(client, BANK, [(1, LANDLORD, -300.00), (2, LANDLORD, -300.00), (3, LANDLORD, 300.00)])
    rows = _landlord_rows(client)
    ins = [tx for tx in rows if tx["amount_sgd"] < 0]
    out = [tx for tx in rows if tx["amount_sgd"] > 0][0]
    for tx in ins:
        client.post(f"/api/review/{tx['id']}/label", json={"choice": "own_account", "account_id": ledger[CARD]})

    assert client.get(f"/api/review/{out['id']}/suggestion").get_json() == {"guess": None}
    assert client.get("/api/review/987654/suggestion").status_code == 404


def row_by_id(client, tx_id: int) -> dict:
    return [tx for tx in listed(client) if tx["id"] == tx_id][0]
