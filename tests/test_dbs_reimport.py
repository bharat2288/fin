"""DBS statements imported again: what an older import left out comes in, and
nothing comes in twice.

Before the default skip was dropped, an import left out every row it took for
a card payment or a transfer: the bill payments on the cards, the DBSC- bill
payments and funds transfers on the bank accounts. Importing those statements
again brings the missing rows in. Three things stood in the way, each tested
here:

- a DBS or POSB bank statement whose rows run onto a second page carries its
  balance forward on its last page as a "Total Balance Carried Forward" line,
  which was not read, so the statement did not tie and was refused;
- a card's rows first imported from a DBS CSV export carry a longer wording
  ("SAMPLE GROCER SINGAPORE SG") than its PDF statement ("SAMPLE GROCER"),
  so the duplicate check took each for a new row;
- money back on a card, or on a bank account's debit card, came in as income.

Through the HTTP interface with a stand-in parser, the classifier directly,
and the real DBS bank reader over synthetic page text. Every name and figure
is invented.
"""

import pytest

import account_kind
import parse_dbs
import tie
from flow import REVIEW, ClassifierContext, classify_flow
from test_import_that_ties import confirm, stand_in, stored_rows, upload  # noqa: F401
from test_parser_balances import _read

CARD = "SAMPLE VISA CARD 0009"
BANK = "Sample Bank 0002"


# --- the bank statement's last page --------------------------------------------------

PAGE1 = "\n".join([
    "DBS Bank Ltd",
    "Transaction Details",
    "as at 31 Aug 2026",
    "My Account Account No. 000-00000-0",
    "Date Description Withdrawal (-) Deposit (+) Balance",
    "CURRENCY: SINGAPORE DOLLAR",
    "Balance Brought Forward SGD 1,000.00",
    "03/08/2026 Advice Bill Payment 100.00 900.00",
    "DBSC-0000000000000009 : I-BANK",
    "Balance Carried Forward SGD 900.00",
])
PAGE2 = "\n".join([
    "My Account Account No. 000-00000-0",
    "Date Description Withdrawal (-) Deposit (+) Balance",
    "Balance Brought Forward SGD 900.00",
    "05/08/2026 Advice Funds Transfer 500.25 1,400.25",
    "000-00000-5:IB",
    "31/08/2026 Interest Earned 0.10 1,400.35",
    "Total Balance Carried Forward in SGD: 100.00 500.35 1,400.35",
    "Date Description Withdrawal (-) Deposit (+) Balance",
    "CURRENCY: UNITED STATES DOLLAR",
    "Balance Brought Forward USD 0.25",
    "Total Balance Carried Forward in USD: 0.00 0.00 0.25",
])


@pytest.mark.parametrize("total_line", [
    "Total Balance Carried Forward in SGD: 100.00 500.35 1,400.35",   # DBS
    "Total Balance Carried Forward: 100.00 500.35 1,400.35",          # POSB
])
def test_a_statement_whose_last_page_carries_a_total_line_ties(monkeypatch, total_line):
    page2 = PAGE2.replace("Total Balance Carried Forward in SGD: 100.00 500.35 1,400.35", total_line)

    stmt = _read(monkeypatch, parse_dbs, parse_dbs.parse_bank_statement, [PAGE1, page2])

    assert [t.amount_minor for t in stmt.transactions] == [10000, -50025, -10]
    assert (stmt.opening_minor, stmt.closing_minor) == (100000, 140035)
    assert tie.check(stmt)["difference_minor"] == 0


def test_a_total_line_in_another_currency_is_not_the_closing_balance(monkeypatch):
    page2 = PAGE2.replace("Total Balance Carried Forward in SGD: 100.00 500.35 1,400.35\n", "")

    stmt = _read(monkeypatch, parse_dbs, parse_dbs.parse_bank_statement, [PAGE1, page2])

    assert stmt.closing_minor == 90000
    with pytest.raises(tie.DoesNotTie):
        tie.check(stmt)


# --- a card first imported from its CSV export ---------------------------------------

# The card's August rows as its CSV export worded them, without the payment
# the old import left out.
CSV_ROWS = [
    ("2026-08-03", "SAMPLE GROCER SINGAPORE SG", 14000),
    ("2026-08-05", "SAMPLE CAFE SINGAPORE SGP", 1595),
    ("2026-08-12", "SAMPLE REF 0000000001 SINGAPORE SG", -2500),
    ("2026-08-12", "SAMPLE REF 0000000002 SINGAPORE SG", -2500),
]
# The same rows as the PDF statement words them, with the payment.
PDF_ROWS = [
    ("2026-08-03", "SAMPLE GROCER", 14000),
    ("2026-08-05", "SAMPLE CAFE", 1595),
    ("2026-08-10", "BILL PAYMENT - DBS INTERNET/WIRELESS", -50000),
    ("2026-08-12", "SAMPLE REF 0000000002", -2500),
    ("2026-08-12", "SAMPLE REF 0000000001", -2500),
]


def test_a_statement_imported_again_over_its_csv_rows_adds_only_the_missing_payment(
    client, conn, stand_in
):
    stand_in(CSV_ROWS, account=CARD, kind="credit_card")
    confirm(client, upload(client))
    # Owed 200.00 before; 155.95 spent, 500.00 paid, 50.00 back: 194.05 in credit.
    stand_in(PDF_ROWS, -20000, 19405, account=CARD, kind="credit_card")

    resp = confirm(client, upload(client))

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert (body["transactions_saved"], body["duplicates_skipped"]) == (1, 4)
    assert stored_rows(conn) == CSV_ROWS[:2] + [PDF_ROWS[2]] + CSV_ROWS[2:]


def test_a_longer_wording_imported_over_a_shorter_one_is_the_same_row(client, conn, stand_in):
    stand_in(PDF_ROWS[:2], account=CARD, kind="credit_card")
    confirm(client, upload(client))
    stand_in(CSV_ROWS[:2], account=CARD, kind="credit_card")

    body = confirm(client, upload(client)).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"]) == (0, 2)
    assert stored_rows(conn) == PDF_ROWS[:2]


def test_a_different_merchant_on_the_same_day_and_amount_is_a_new_row(client, conn, stand_in):
    stand_in(CSV_ROWS[:1], account=CARD, kind="credit_card")
    confirm(client, upload(client))
    stand_in([("2026-08-03", "SAMPLE BAKERY", 14000)], account=CARD, kind="credit_card")

    body = confirm(client, upload(client)).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"]) == (1, 0)


def test_two_purchases_alike_held_once_bring_in_the_second(client, conn, stand_in):
    stand_in(CSV_ROWS[:1], account=CARD, kind="credit_card")
    confirm(client, upload(client))
    stand_in([PDF_ROWS[0], PDF_ROWS[0]], account=CARD, kind="credit_card")

    body = confirm(client, upload(client)).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"]) == (1, 1)


def test_a_wording_that_runs_on_into_a_longer_word_is_another_row(client, conn, stand_in):
    stand_in([("2026-08-03", "SAMPLE SHOP", 14000)], account=CARD, kind="credit_card")
    confirm(client, upload(client))
    stand_in([("2026-08-03", "SAMPLE SHOPEE", 14000)], account=CARD, kind="credit_card")

    body = confirm(client, upload(client)).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"]) == (1, 0)


def test_a_row_that_could_be_only_one_held_row_is_matched_first(client, conn, stand_in):
    # "SAMPLE GROCER" could be either held row; "SAMPLE GROCER PTE" only the first.
    held = [("2026-08-03", "SAMPLE GROCER PTE 0001", 14000), ("2026-08-03", "SAMPLE GROCER 0002", 14000)]
    stand_in(held, account=CARD, kind="credit_card")
    confirm(client, upload(client))
    stand_in([("2026-08-03", "SAMPLE GROCER", 14000), ("2026-08-03", "SAMPLE GROCER PTE", 14000)],
             account=CARD, kind="credit_card")

    body = confirm(client, upload(client)).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"]) == (0, 2)
    assert stored_rows(conn) == held


def test_a_row_held_on_another_account_is_not_this_ones(client, conn, stand_in):
    stand_in(CSV_ROWS[:1], account="SAMPLE VISA CARD 0008", kind="credit_card")
    confirm(client, upload(client))
    stand_in(PDF_ROWS[:1], account=CARD, kind="credit_card")

    body = confirm(client, upload(client)).get_json()

    assert (body["transactions_saved"], body["duplicates_skipped"]) == (1, 0)


# --- money back on a card, or a debit card ---------------------------------------------

CTX = ClassifierContext(own_aliases=("SAMPLE OWNER",))
ON_CARD = {"account_kind": "card", "account_owner": account_kind.HOUSEHOLD}
ON_BANK = {"account_kind": "bank", "account_owner": account_kind.HOUSEHOLD}


@pytest.mark.parametrize("description", ["SAMPLE FASHION - ION SINGAPORE", "SAMPLE STORE", "TIKTOK SHOP"])
def test_money_back_on_a_card_is_a_refund(description):
    assert classify_flow({"description": description, "amount_minor": -49500, **ON_CARD}, CTX) == "refund"


def test_a_card_bill_payment_is_still_a_payment():
    facts = {"description": "BILL PAYMENT - DBS INTERNET/WIRELESS", "amount_minor": -50000, **ON_CARD}
    assert classify_flow(facts, CTX) == "payment"


@pytest.mark.parametrize("description", [
    "PAYMENT-ATM/INTERNET", "PAYMENT - THANK YOU", "PAYMENT RECEIVED - THANK YOU", "GIRO PAYMENT"])
def test_a_card_bill_paid_in_other_wording_is_not_a_refund(description):
    # As before money back on a card was a refund: no rule knows the wording.
    facts = {"description": description, "amount_minor": -50000, **ON_CARD}
    assert classify_flow(facts, CTX) == "income"


def test_a_card_credit_moved_from_another_card_waits():
    facts = {"description": "INTER ACCOUNT TRANSFER", "amount_minor": -1342415, **ON_CARD}
    assert classify_flow(facts, CTX) == REVIEW


def test_spending_on_a_card_is_still_spending():
    assert classify_flow({"description": "SAMPLE STORE", "amount_minor": 4900, **ON_CARD}, CTX) == "expense"


def test_money_back_on_a_bank_accounts_debit_card_is_a_refund():
    facts = {"description": "Debit Card Transaction SAMPLE FASHION-EC SINGAPORE SGP 21JUN 0000-0000",
             "amount_minor": -91680, **ON_BANK}
    assert classify_flow(facts, CTX) == "refund"


def test_other_money_in_on_a_bank_account_is_still_income():
    facts = {"description": "Salary SAMPLE EMPLOYER PTE LTD", "amount_minor": -1338200, **ON_BANK}
    assert classify_flow(facts, CTX) == "income"


def test_a_card_refund_comes_in_as_a_refund(client, conn, stand_in):
    stand_in([("2026-08-12", "SAMPLE FASHION - ION SINGAPORE", -49500)], account=CARD, kind="credit_card")

    [group] = upload(client)["groups"]

    assert [t["flow_type"] for t in group["transactions"]] == ["refund"]
