"""An account whose balance moves by rows on other accounts fills its page
(the operator's question of 27 Oct 2026: a company kept in rupees showed its
balance, but "No balance yet" in the chart and "No rows." under Rows).

A company, a loan, a holding and a bank holding no statement have no
statement of their own: their balance is worked out from the rows on the
household's other accounts that name them (balance_sheet.balance_rows). The
page's Rows list (/api/transactions with balance=1) lists exactly those rows,
and its chart (/api/accounts/<id>/balances) draws that balance at each month
end, with money in and out from the account's side: capital paid out of a
bank is money in to the company. A company needs no figure; its chart is its
line on the balance sheet at each month end. The made-of line sums in the
company's own currency.

Nothing is stored or converted. Every name and figure here is invented.
Amounts are whole minor units; a row's amount is positive for money out.
"""

from __future__ import annotations

import datetime
import json
import re

import pytest

import account_balances_api
from test_client_seams import needs_node
from test_expect_readers_lists import PRELUDE, app_function, run_node
from test_loan_interest import (
    AUG_INSTALMENT, AUG_INTEREST, JULY_INSTALMENT, JULY_INTEREST, OWED_AUG, enter, setup,
)
from trend_fixtures import account, row, statement

TODAY = datetime.date(2026, 10, 19)
CAPITAL = 2_958_000_000          # ₹2,95,80,000.00 put into the film company in August
ADS, CREDIT = 30_010, -1_010     # S$300.10 paid for Moom, S$10.10 back as a credit
PUT_IN, PAID_BACK = 2_000_000, 1_250_050   # S$20,000.00 into Moom, S$12,500.50 back


@pytest.fixture(autouse=True)
def nineteenth_of_october(monkeypatch):
    monkeypatch.setattr(account_balances_api, "today", lambda: TODAY)


def naming(conn, tx: int, other: int, flow: str = "movement") -> int:
    conn.execute("UPDATE transactions SET other_side_id = ?, flow_type = ? WHERE id = ?", (other, flow, tx))
    conn.commit()
    return tx


def listed(client, account_id: int, query: str = "") -> list[dict]:
    resp = client.get(f"/api/transactions?account_id={account_id}&per_page=50&page=1{query}&balance=1")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["transactions"]


def chart(client, account_id: int) -> dict:
    resp = client.get(f"/api/accounts/{account_id}/balances?span=6M")
    assert resp.status_code == 200, resp.get_json()
    return {m["month"]: m for m in resp.get_json()["months"]}


def sheet_line(client, account_id: int, month: str) -> dict:
    shown = client.get(f"/api/balance-sheet?month={month}").get_json()
    return next(l for s in shown["sections"] for l in s["lines"] if l["account_id"] == account_id)


@pytest.fixture
def film(conn):
    """A company kept in rupees, with ₹2,95,80,000.00 put into it in August
    from the household's rupee bank; a S$ movement naming it from a S$ bank,
    which its line does not count (another currency), and a rupee movement
    from an account the household does not own."""
    company = account(conn, "Sample Film Company", type="company", currency="INR")
    rupees = statement(conn, account(conn, "Sample Rupee Bank 0009", type="bank", currency="INR"), "2026-08")
    dollars = statement(conn, account(conn, "Sample Bank 0002", type="bank"), "2026-08")
    theirs = statement(conn, account(conn, "Sample Partner Bank 0007", type="bank", currency="INR",
                                     owner="Moom"), "2026-08")
    put_in = naming(conn, row(conn, rupees, "NEFT DR SAMPLE FILM", CAPITAL, day="2026-08-12"), company)
    naming(conn, row(conn, dollars, "SAMPLE FILM FEE", 50_000, day="2026-08-13"), company)
    naming(conn, row(conn, theirs, "NEFT DR SAMPLE PARTNER", 1_000_000, day="2026-08-14"), company)
    row(conn, rupees, "UPI-SAMPLE GROCER", 40_000, day="2026-08-15")
    return {"company": company, "put_in": put_in}


def test_a_companys_rows_are_the_rows_its_line_counts(client, film):
    rows = listed(client, film["company"])

    assert [r["id"] for r in rows] == [film["put_in"]]
    assert rows[0]["account_name"] == "Sample Rupee Bank 0009"
    # Without balance=1 the list is the account's own statements' rows, as before: none.
    assert client.get(f"/api/transactions?account_id={film['company']}").get_json()["transactions"] == []


def test_a_companys_chart_draws_its_line_at_each_month_end_with_the_capital_as_money_in(client, film):
    months = chart(client, film["company"])

    assert months["2026-07"]["balance"] == 0
    assert (months["2026-08"]["in"], months["2026-08"]["out"]) == (CAPITAL, 0)
    assert months["2026-08"]["balance"] == CAPITAL
    assert months["2026-09"]["balance"] == CAPITAL
    assert months["2026-09"]["balance"] == sheet_line(client, film["company"], "2026-09")["balance_minor"]


def test_a_companys_chart_has_a_head_and_no_no_balance(client, film):
    resp = client.get(f"/api/accounts/{film['company']}/balances?span=6M").get_json()

    assert resp["head"] == {"balance": CAPITAL, "date": "2026-09-30"}
    assert resp["account"]["currency"] == "INR"


@pytest.fixture
def moom(client, conn):
    """Moom, a company that is a book: S$300.10 paid for it on a card and a
    S$10.10 credit back, both filed under Moom; S$20,000.00 put in and
    S$12,500.50 paid back through the bank, in September."""
    company = account(conn, "Moom", type="company")
    card = statement(conn, account(conn, "Sample Card 0001", type="card", last_four="0001"), "2026-09")
    bank = statement(conn, account(conn, "Sample Bank 0003", type="bank", last_four="0003"), "2026-09")
    ids = {
        "ads": row(conn, card, "SAMPLE ADS", ADS, book="Moom", type_name="Advertising", day="2026-09-03"),
        "credit": row(conn, card, "SAMPLE AD CREDIT", CREDIT, book="Moom", type_name="Advertising",
                      flow="refund", day="2026-09-04"),
        "put_in": naming(conn, row(conn, bank, "SAMPLE TO MOOM", PUT_IN, day="2026-09-10"), company),
        "back": naming(conn, row(conn, bank, "SAMPLE FROM MOOM", -PAID_BACK, day="2026-09-20"), company),
    }
    row(conn, card, "SAMPLE GROCER", 5_000, type_name="Groceries", day="2026-09-05")
    return {"company": company, **ids}


def test_a_company_books_costs_count_as_money_in_and_what_it_paid_back_as_money_out(client, moom):
    rows = {r["id"] for r in listed(client, moom["company"])}
    months = chart(client, moom["company"])

    assert rows == {moom["ads"], moom["credit"], moom["put_in"], moom["back"]}
    assert months["2026-09"]["in"] == ADS + PUT_IN
    assert months["2026-09"]["out"] == -CREDIT + PAID_BACK
    assert months["2026-09"]["balance"] == ADS + CREDIT + PUT_IN - PAID_BACK
    assert months["2026-09"]["balance"] == sheet_line(client, moom["company"], "2026-09")["balance_minor"]


def test_money_in_sorts_from_the_pages_side(client, moom):
    by_in = listed(client, moom["company"], "&sort=amount_in&sort_dir=desc")

    # Biggest money in first (the S$20,000.00 put in), then the ads; the
    # rows that are money out from Moom's side come last.
    assert [r["id"] for r in by_in][:2] == [moom["put_in"], moom["ads"]]
    assert {r["id"] for r in by_in[2:]} == {moom["credit"], moom["back"]}


def test_a_loans_rows_are_its_instalments_and_its_interest(client, conn):
    made = setup(client, conn)
    assert enter(client, made["loan"], OWED_AUG, "2026-08-31").status_code == 200

    rows = listed(client, made["loan"])
    months = chart(client, made["loan"])

    assert sorted(r["amount_minor"] for r in rows) == sorted(
        [JULY_INSTALMENT, AUG_INSTALMENT, JULY_INTEREST, AUG_INTEREST])
    assert (months["2026-07"]["in"], months["2026-07"]["out"]) == (JULY_INSTALMENT, JULY_INTEREST)
    assert (months["2026-08"]["in"], months["2026-08"]["out"]) == (AUG_INSTALMENT, AUG_INTEREST)
    assert months["2026-08"]["balance"] == sheet_line(client, made["loan"], "2026-08")["balance_minor"]


def test_a_bank_with_statements_lists_only_its_own_rows(client, conn):
    savings = account(conn, "Sample Savings 0004", type="bank", last_four="0004")
    own = row(conn, statement(conn, savings, "2026-09"), "SAMPLE SALARY", -500_000, flow="income", day="2026-09-01")
    card = statement(conn, account(conn, "Sample Card 0005", type="card", last_four="0005"), "2026-09")
    naming(conn, row(conn, card, "SAMPLE PAYMENT FROM SAVINGS", -10_000, day="2026-09-02"), savings, "payment")

    assert [r["id"] for r in listed(client, savings)] == [own]
    assert (chart(client, savings)["2026-09"]["in"], chart(client, savings)["2026-09"]["out"]) == (500_000, 0)


def test_a_bank_holding_no_statement_lists_the_transfers_into_it(client, conn):
    deposit = account(conn, "Sample Fixed Deposit", type="bank")
    bank = statement(conn, account(conn, "Sample Bank 0006", type="bank", last_four="0006"), "2026-09")
    moved = naming(conn, row(conn, bank, "SAMPLE TO DEPOSIT", 300_000, day="2026-09-09"), deposit, "transfer")
    row(conn, bank, "SAMPLE GROCER", 2_000, day="2026-09-10")

    assert [r["id"] for r in listed(client, deposit)] == [moved]
    assert (chart(client, deposit)["2026-09"]["in"], chart(client, deposit)["2026-09"]["out"]) == (300_000, 0)


def test_an_account_no_longer_in_fin_lists_no_rows(client, conn):
    assert listed(client, 99_999) == []


# --- review findings -------------------------------------------------------------


def test_an_empty_account_id_filters_nothing(client, conn):
    card = statement(conn, account(conn, "Sample Card 0008", type="card", last_four="0008"), "2026-09")
    row(conn, card, "SAMPLE GROCER", 5_000, type_name="Groceries", day="2026-09-05")

    assert len(client.get("/api/transactions?account_id=").get_json()["transactions"]) == 1


def test_a_car_under_depreciation_terms_has_no_money_in_or_out(client, conn):
    from test_depreciation import car_with_terms

    car = car_with_terms(client)
    bank = statement(conn, account(conn, "Sample Bank 0010", type="bank", last_four="0010"), "2026-09")
    naming(conn, row(conn, bank, "SAMPLE CAR DEALER", 8_000_000, day="2026-09-08"), car)

    september = chart(client, car)["2026-09"]

    # Its value is its terms'; the payment naming it moves nothing.
    assert (september["in"], september["out"]) == (0, 0)
    assert september["balance"] < chart(client, car)["2026-08"]["balance"]


# --- the page ---------------------------------------------------------------------


@needs_node
def test_the_made_of_line_sums_in_the_companys_own_currency():
    code = "\n".join([
        PRELUDE, "const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;", app_function("madeOfSum"),
        "const line = {currency: 'INR', balance_minor: 2958000000, rows_since: 1,"
        " made_of: {opening_minor: null, paid_for_minor: 0, capital_minor: 2958000000, paid_back_minor: 0}};",
        "console.log(JSON.stringify(madeOfSum(line)));",
    ])
    assert run_node(code) == "+ capital INR 29580000.00 = INR 29580000.00"


@needs_node
def test_a_row_on_another_account_reads_from_the_pages_side_and_says_where_it_is():
    capital = {"id": 7, "date": "2026-08-12", "description": "NEFT DR SAMPLE FILM", "amount_sgd": 29580000.0,
               "currency": "INR", "amount_minor": 2958000000, "counted_minor": 2958000000, "flow_type": "movement",
               "account_id": 3, "account_name": "Sample Rupee Bank 0009", "book": None, "notes": None, "is_one_off": 0}
    code = "\n".join([
        PRELUDE, app_function("struckAmount"), app_function("acctRowTr"),
        f"const row = {json.dumps(capital)};",
        "console.log(JSON.stringify([acctRowTr(row, 1), acctRowTr(row, 3)]));",
    ])
    on_company, on_bank = run_node(code)

    cells = lambda html: {k: v for k, v in re.findall(r'<td class="r num row-(in|out)">(.*?)</td>', html)}
    assert cells(on_company) == {"in": "INR 29580000.00", "out": ""}
    assert "· on Sample Rupee Bank 0009" in on_company
    assert cells(on_bank) == {"in": "", "out": "INR 29580000.00"}
    assert "· on " not in on_bank

