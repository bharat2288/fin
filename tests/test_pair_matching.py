"""Pair matching between household accounts, through the HTTP interface.

Rows reach the ledger the way the operator's do: a statement is uploaded and
confirmed, and the import pairs what it can. The statement itself is a
stand-in parser returning invented rows. Every name and figure is invented.
"""

import io

import pytest

import money
import parsers
from parse_dbs import ParsedStatement, ParsedTransaction

BANK = "Sample Bank 0002"
SAVINGS = "Sample Savings 0009"
CARD = "Sample Card 0001"

# Wording the classifier reads as each flow, with nothing naming an account.
WAITING_OUT = "FAST PAYMENT REF 771203 OTHR"            # review, on a household bank account
WAITING_IN = "INWARD PAYNOW FROM SAMPLE SENDER REF 4410"
MOVE_OUT = "FUNDS TRANSFER TO XXXX0009"                 # transfer: names one of my own accounts
MOVE_IN = "FUNDS TRANSFER FROM XXXX0002"
PAYOFF_BANK = "PAYMENT TO CITI CREDIT CARD REF 8801"    # payment, the bank side
PAYOFF_CARD = "PAYMENT - ATM/INTERNET"                  # payment, the card side


# --- helpers --------------------------------------------------------------------


def make_account(client, name: str, kind: str, **extra) -> int:
    resp = client.post("/api/accounts", json={"name": name, "type": kind, **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


@pytest.fixture
def ledger(client) -> dict:
    """Two household bank accounts and a household card."""
    return {
        BANK: make_account(client, BANK, "bank", last_four="0002"),
        SAVINGS: make_account(client, SAVINGS, "bank", last_four="0009"),
        CARD: make_account(client, CARD, "card", last_four="0001"),
    }


def bring_in(client, account: str, rows: list[tuple], kind: str = "bank") -> None:
    """Upload and confirm a statement of (date, description, amount) rows."""
    def parse(_path):
        return [ParsedStatement(
            statement_type=kind,
            statement_date="2026-08-31",
            accounts=[account],
            filename="sample.csv",
            transactions=[
                ParsedTransaction(date=day, description=description, amount_minor=money.to_minor(amount))
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
    preview = resp.get_json()
    for group in preview["groups"]:
        for tx in group["transactions"]:
            tx["_skip"] = False
    resp = client.post(
        "/api/import/confirm", json={"import_id": preview["import_id"], "groups": preview["groups"]}
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["transactions_saved"] == len(rows)


def listed(client, **params) -> list[dict]:
    query = "&".join(f"{k}={v}" for k, v in {"per_page": 200, "sort": "date", "sort_dir": "asc", **params}.items())
    return client.get(f"/api/transactions?{query}").get_json()["transactions"]


def row(client, account: str, day: str) -> dict:
    found = [tx for tx in listed(client) if (tx["account_name"], tx["date"]) == (account, day)]
    assert len(found) == 1, (account, day)
    return found[0]


def what_it_is(tx: dict) -> tuple:
    return (tx["flow_type"], tx["other_side_name"])


def waiting(client) -> list[tuple]:
    return [(tx["account_name"], tx["date"]) for tx in listed(client, flow="review")]


def match(client) -> dict:
    resp = client.post("/api/pair-matching")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def cents(rows: list[dict]) -> int:
    return sum(money.to_minor(tx["amount_sgd"]) for tx in rows)


def spending_type(client, name: str) -> int:
    return {t["display_name"]: t["id"] for t in client.get("/api/types").get_json()}[name]


# --- one candidate: each row names the other's account ----------------------------


def test_a_move_between_two_household_accounts_is_paired_at_import(client, ledger):
    bring_in(client, BANK, [("2026-08-10", MOVE_OUT, 5000.25)])
    assert what_it_is(row(client, BANK, "2026-08-10")) == ("transfer", None)  # its partner is not in yet

    bring_in(client, SAVINGS, [("2026-08-11", MOVE_IN, -5000.25)])

    out, back = row(client, BANK, "2026-08-10"), row(client, SAVINGS, "2026-08-11")
    assert what_it_is(out) == ("transfer", SAVINGS)
    assert what_it_is(back) == ("transfer", BANK)
    assert (out["other_side_id"], back["other_side_id"]) == (ledger[SAVINGS], ledger[BANK])
    assert (out["flow_type_manual"], back["flow_type_manual"]) == (0, 0)
    # No money moved: the amounts are as imported and the pair nets to nothing.
    assert (money.to_minor(out["amount_sgd"]), money.to_minor(back["amount_sgd"])) == (500025, -500025)
    assert cents(listed(client)) == 0


def test_a_waiting_row_that_finds_its_pair_leaves_the_review_list(client, ledger):
    bring_in(client, BANK, [("2026-08-10", WAITING_OUT, 1200.50), ("2026-08-20", WAITING_OUT, 77.00)])
    assert waiting(client) == [(BANK, "2026-08-10"), (BANK, "2026-08-20")]

    bring_in(client, SAVINGS, [("2026-08-12", WAITING_IN, -1200.50)])

    assert what_it_is(row(client, BANK, "2026-08-10")) == ("transfer", SAVINGS)
    assert what_it_is(row(client, SAVINGS, "2026-08-12")) == ("transfer", BANK)
    assert waiting(client) == [(BANK, "2026-08-20")]
    assert client.get("/api/review").get_json()["waiting"] == 1
    assert cents(listed(client)) == 7700  # 1,200.50 out, 1,200.50 in, 77.00 out


def test_a_card_payoff_is_paired_and_a_waiting_bank_side_becomes_a_payment(client, ledger):
    bring_in(client, BANK, [
        ("2026-08-10", PAYOFF_BANK, 3210.99),
        ("2026-08-15", WAITING_OUT, 640.10),
    ])
    bring_in(client, CARD, [
        ("2026-08-11", PAYOFF_CARD, -3210.99),
        ("2026-08-16", PAYOFF_CARD, -640.10),
    ], kind="credit_card")

    assert what_it_is(row(client, BANK, "2026-08-10")) == ("payment", CARD)
    assert what_it_is(row(client, CARD, "2026-08-11")) == ("payment", BANK)
    assert what_it_is(row(client, BANK, "2026-08-15")) == ("payment", CARD)  # was waiting
    assert what_it_is(row(client, CARD, "2026-08-16")) == ("payment", BANK)
    assert waiting(client) == []
    assert cents(listed(client)) == 0


# --- several candidates ------------------------------------------------------------


def test_of_several_candidates_the_nearest_date_wins(client, ledger):
    """Repeated equal payments: each goes to the row nearest in date."""
    bring_in(client, BANK, [("2026-08-10", WAITING_OUT, 900.00), ("2026-08-13", WAITING_OUT, 900.00)])
    bring_in(client, SAVINGS, [("2026-08-11", WAITING_IN, -900.00), ("2026-08-14", WAITING_IN, -900.00)])

    paired = {(tx["account_name"], tx["date"]): what_it_is(tx) for tx in listed(client)}
    assert paired == {
        (BANK, "2026-08-10"): ("transfer", SAVINGS),
        (SAVINGS, "2026-08-11"): ("transfer", BANK),
        (BANK, "2026-08-13"): ("transfer", SAVINGS),
        (SAVINGS, "2026-08-14"): ("transfer", BANK),
    }
    assert cents(listed(client)) == 0


def test_the_nearest_of_two_candidates_is_paired_and_the_other_waits(client, ledger):
    bring_in(client, BANK, [("2026-08-10", WAITING_OUT, 900.00), ("2026-08-12", WAITING_OUT, 900.00)])
    bring_in(client, SAVINGS, [("2026-08-13", WAITING_IN, -900.00)])

    assert what_it_is(row(client, BANK, "2026-08-12")) == ("transfer", SAVINGS)
    assert what_it_is(row(client, SAVINGS, "2026-08-13")) == ("transfer", BANK)
    assert waiting(client) == [(BANK, "2026-08-10")]


def test_an_exact_tie_leaves_the_rows_on_the_review_list(client, ledger):
    bring_in(client, BANK, [("2026-08-10", WAITING_OUT, 900.00), ("2026-08-12", WAITING_OUT, 900.00)])
    bring_in(client, SAVINGS, [("2026-08-11", WAITING_IN, -900.00)])  # one day from each

    assert waiting(client) == [(BANK, "2026-08-10"), (SAVINGS, "2026-08-11"), (BANK, "2026-08-12")]
    assert {tx["other_side_id"] for tx in listed(client)} == {None}
    assert match(client)["paired"] == 0


# --- what is not a pair ------------------------------------------------------------


def test_amounts_that_differ_by_a_cent_are_not_matched(client, ledger):
    bring_in(client, BANK, [("2026-08-10", WAITING_OUT, 1200.50)])
    bring_in(client, SAVINGS, [("2026-08-10", WAITING_IN, -1200.49)])

    assert waiting(client) == [(BANK, "2026-08-10"), (SAVINGS, "2026-08-10")]
    assert match(client)["paired"] == 0


def test_two_rows_of_the_same_sign_are_not_matched(client, ledger):
    bring_in(client, BANK, [("2026-08-10", WAITING_OUT, 1200.50)])
    bring_in(client, SAVINGS, [("2026-08-10", WAITING_OUT, 1200.50)])

    assert len(waiting(client)) == 2


def test_three_days_apart_is_matched_and_four_is_not(client, ledger):
    bring_in(client, BANK, [("2026-08-10", WAITING_OUT, 310.00), ("2026-08-30", WAITING_OUT, 420.00)])
    bring_in(client, SAVINGS, [("2026-08-13", WAITING_IN, -310.00), ("2026-09-03", WAITING_IN, -420.00)])

    assert what_it_is(row(client, BANK, "2026-08-10")) == ("transfer", SAVINGS)
    assert what_it_is(row(client, SAVINGS, "2026-08-13")) == ("transfer", BANK)
    assert waiting(client) == [(BANK, "2026-08-30"), (SAVINGS, "2026-09-03")]


def test_two_rows_on_the_same_account_are_not_matched(client, ledger):
    bring_in(client, BANK, [("2026-08-10", WAITING_OUT, 500.00), ("2026-08-11", WAITING_IN, -500.00)])

    assert len(waiting(client)) == 2
    assert match(client)["paired"] == 0


def test_a_companys_account_is_not_a_household_account(client, ledger):
    make_account(client, "Sample Business Bank 0007", "bank", owner="Kalesh", last_four="0007")
    bring_in(client, BANK, [("2026-08-10", MOVE_OUT, 2500.00)])
    bring_in(client, "Sample Business Bank 0007", [("2026-08-10", MOVE_IN, -2500.00)])

    assert {what_it_is(tx) for tx in listed(client)} == {("transfer", None)}
    assert match(client)["paired"] == 0


def test_spending_and_income_rows_are_never_paired(client, ledger):
    bring_in(client, CARD, [("2026-08-10", "SAMPLE BISTRO 0042", 80.25)], kind="credit_card")
    bring_in(client, BANK, [("2026-08-10", "INTEREST EARNED", -80.25)])

    assert match(client)["paired"] == 0
    assert sorted((tx["flow_type"], tx["other_side_id"]) for tx in listed(client)) == [
        ("expense", None), ("income", None),
    ]


# --- on demand ---------------------------------------------------------------------


def test_matching_on_demand_pairs_what_a_label_untied(client, ledger):
    """Two equal payments tie for one receipt. The operator says what one of
    them was; matching again pairs the other."""
    bring_in(client, BANK, [("2026-08-10", WAITING_OUT, 900.00), ("2026-08-12", WAITING_OUT, 900.00)])
    bring_in(client, SAVINGS, [("2026-08-11", WAITING_IN, -900.00)])
    first = row(client, BANK, "2026-08-10")
    assert client.post(f"/api/review/{first['id']}/label", json={
        "choice": "spending", "type_id": spending_type(client, "Education"),
    }).status_code == 200
    before = cents(listed(client))

    result = match(client)

    assert result["paired"] == 1
    assert what_it_is(row(client, BANK, "2026-08-12")) == ("transfer", SAVINGS)
    assert what_it_is(row(client, SAVINGS, "2026-08-11")) == ("transfer", BANK)
    assert what_it_is(row(client, BANK, "2026-08-10")) == ("expense", None)
    assert waiting(client) == []
    assert cents(listed(client)) == before == 90000


# --- P11 replay: twice changes nothing, and a hand-set row is never touched --------


def test_matching_twice_changes_nothing(client, ledger):
    bring_in(client, BANK, [
        ("2026-08-10", MOVE_OUT, 5000.25),
        ("2026-08-12", PAYOFF_BANK, 3210.99),
        ("2026-08-14", WAITING_OUT, 900.00),
        ("2026-08-16", WAITING_OUT, 900.00),
        ("2026-08-20", WAITING_OUT, 77.00),
    ])
    bring_in(client, SAVINGS, [
        ("2026-08-11", MOVE_IN, -5000.25),
        ("2026-08-15", WAITING_IN, -900.00),     # ties between the 14th and the 16th
    ])
    bring_in(client, CARD, [("2026-08-13", PAYOFF_CARD, -3210.99)], kind="credit_card")
    after_import = listed(client)
    assert sum(1 for tx in after_import if tx["other_side_id"]) == 4

    first, second = match(client), match(client)

    assert (first["paired"], second["paired"]) == (0, 0)
    assert listed(client) == after_import
    assert cents(listed(client)) == 97700  # 900.00 + 900.00 + 77.00 out, 900.00 in


def test_a_hand_set_row_is_never_touched_and_is_nobodys_pair(client, ledger):
    spare = make_account(client, "Sample Deposit 0005", "bank", last_four="0005")
    bring_in(client, BANK, [
        ("2026-08-10", WAITING_OUT, 1200.50),   # labelled: a move to the deposit account
        ("2026-08-20", WAITING_OUT, 640.10),    # labelled: spending
        ("2026-08-25", MOVE_OUT, 300.00),       # flow set by hand
    ])
    labelled_move = row(client, BANK, "2026-08-10")
    assert client.post(f"/api/review/{labelled_move['id']}/label", json={
        "choice": "own_account", "account_id": spare,
    }).status_code == 200
    assert client.post(f"/api/review/{row(client, BANK, '2026-08-20')['id']}/label", json={
        "choice": "spending", "type_id": spending_type(client, "Education"),
    }).status_code == 200
    assert client.post("/api/transactions/resolve", json={
        "tx_id": row(client, BANK, "2026-08-25")["id"], "service_name": "Sample Mover",
        "type_id": spending_type(client, "Education"), "apply_scope": "transaction",
        "flow_type": "transfer",
    }).status_code == 200
    by_hand = [tx for tx in listed(client) if tx["account_name"] == BANK]
    assert [tx["flow_type_manual"] for tx in by_hand] == [1, 1, 1]

    # Each has what would be its pair on another household account.
    bring_in(client, SAVINGS, [
        ("2026-08-10", WAITING_IN, -1200.50),
        ("2026-08-20", WAITING_IN, -640.10),
        ("2026-08-25", MOVE_IN, -300.00),
    ])
    first, second = match(client), match(client)

    assert (first["paired"], second["paired"]) == (0, 0)
    assert [tx for tx in listed(client) if tx["account_name"] == BANK] == by_hand
    assert what_it_is(row(client, BANK, "2026-08-10")) == ("transfer", "Sample Deposit 0005")
    assert what_it_is(row(client, BANK, "2026-08-20")) == ("expense", None)
    assert what_it_is(row(client, BANK, "2026-08-25")) == ("transfer", None)
    assert [what_it_is(tx) for tx in listed(client) if tx["account_name"] == SAVINGS] == [
        ("review", None), ("review", None), ("transfer", None),
    ]
    assert cents(listed(client)) == 0
