"""Receipts on the household's rupee account that are not income (flow.py's
rupee rules): an own remittance from SGD, a fixed deposit's principal coming
back, a mutual-fund redemption. Defaults the operator can overturn.

The wording is shaped like HDFC's, with every name, code, number and figure
invented. Amounts are whole paise; a negative amount is money received.
"""

import pytest

import account_kind
import flow
from test_rupee_account_and_rates import HDFC, HEADER, bring_in, nothing_reaches_outside, upload  # noqa: F401

CTX = flow.ClassifierContext()


def on_rupee_account(description, amount=-1_000_000, owner=account_kind.HOUSEHOLD, kind="bank"):
    facts = {"date": "2026-05-04", "description": description, "amount_minor": amount,
             "account_kind": kind, "account_owner": owner}
    return flow.classify_row(facts, CTX)


REMITTANCE = "NEFT CR-DBSS0IN0001-SAMPLE HOLDER-SAMPLE HOLDER-0000OP0000000000(P0000)FAMILY EXPENSE/SAVINGS"


@pytest.mark.parametrize("description", [
    REMITTANCE,
    # the routing code alone
    "NEFT CR-DBSS0IN0001-SAMPLE HOLDER-SAMPLE HOLDER-0000OP0000000000",
])
def test_an_own_remittance_from_sgd_waits_for_review_never_income(description):
    # Its SGD leg cannot be labelled automatically, so both legs are the
    # operator's to label with each other's account.
    assert on_rupee_account(description) == (flow.REVIEW, None)


def test_a_fixed_deposits_principal_coming_back_is_a_movement():
    assert on_rupee_account("IB FD PREMAT PRINCIPAL-0001") == (flow.MOVEMENT, None)


@pytest.mark.parametrize("description", [
    "IB FD PREMAT INT PAID-0001",
    "INTEREST PAID TILL 31-MAY-2026",
])
def test_a_fixed_deposits_interest_stays_income(description):
    assert on_rupee_account(description) == ("income", None)


def test_a_fund_redemption_waits_for_review():
    assert on_rupee_account("RTGS CR-SAMP0000001-SAMPLE MUTUAL FUND-REDEMPTION A/C-0000000001") == (flow.REVIEW, None)


@pytest.mark.parametrize("description", [
    "IB FUNDS TRANSFER CR-00000000001234-SAMPLE RELATIVE",
    "NEFT CR-SAMP0000001-SAMPLE RELATIVE-SAMPLE HOLDER-SAMPN00000000000",
    # an RTGS receipt that is not a redemption
    "RTGS CR-SAMP0000001-SAMPLE RELATIVE-SAMPLE HOLDER",
    # another bank's routing code that only starts like DBS's
    "NEFT CR-DBSX0IN0001-SAMPLE RELATIVE-SAMPLE HOLDER",
])
def test_family_receipts_and_other_wording_stay_income(description):
    assert on_rupee_account(description) == ("income", None)


@pytest.mark.parametrize("description", [REMITTANCE, "IB FD PREMAT PRINCIPAL-0001"])
def test_the_rules_read_money_received_on_a_household_bank_account_only(description):
    assert on_rupee_account(description, amount=1_000_000)[0] == "expense"
    assert on_rupee_account(description, owner="Sample Company")[0] == "income"


STATEMENT = ["\n".join([
    "HDFC BANK LIMITED",
    "SAMPLE HOLDER",
    "Account No : 00000000001234",
    "From : 01/04/2026 To : 30/06/2026",
    HEADER,
    "04/04/26 NEFT CR-DBSS0IN0001-SAMPLE HOLDER-SAMPLE HOLDER-0000OP0000000000 0000000000000001 04/04/26 10,000.00 1,10,000.00",
    "(P0000)FAMILY EXPENSE/SAVINGS",
    "10/04/26 IB FD PREMAT PRINCIPAL-0001 0000000000000002 10/04/26 50,000.00 1,60,000.00",
    "10/04/26 IB FD PREMAT INT PAID-0001 0000000000000003 10/04/26 1,000.00 1,61,000.00",
    "20/04/26 RTGS CR-SAMP0000001-SAMPLE FUND-REDEMPTION A/C-0000000001 0000000000000004 20/04/26 20,000.00 1,81,000.00",
    "STATEMENT SUMMARY :-",
    "Opening Balance Dr Count Cr Count Debits Credits Closing Bal",
    "1,00,000.00 0 4 0.00 81,000.00 1,81,000.00",
])]


def test_an_imported_rupee_statement_lands_each_receipt_in_its_flow(client, monkeypatch):
    preview = upload(client, monkeypatch, pages=STATEMENT)
    assert preview["errors"] == [], preview["errors"]
    resp = client.post("/api/import/confirm", json={"import_id": preview["import_id"], "groups": preview["groups"]})
    assert resp.status_code == 200, resp.get_json()

    listed = client.get("/api/transactions?per_page=50").get_json()["transactions"]
    # The listing shows each row's amount in its own currency's major units.
    flows = {tx["amount_sgd"]: tx["flow_type"] for tx in listed if tx["account_name"] == HDFC}
    assert flows == {
        -10_000.00: "review",    # the own remittance
        -50_000.00: "movement",  # the deposit's principal
        -1_000.00: "income",     # its interest
        -20_000.00: "review",    # the fund redemption
    }
