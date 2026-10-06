"""The month check on the balance sheet, through the HTTP interface against a
temporary database.

For the month shown: net worth at the previous month-end, plus income, less
spending (with the loan interest worked out for the month), plus currency
change, set against net worth at the month-end. The difference is unexplained.

Statements reach the ledger the way the operator's do: uploaded and confirmed
through a stand-in parser that returns invented rows and balances, each row
labelled in the preview as the operator would. Figures and rates are entered
through their endpoints. Every name, figure and rate is invented. Amounts are
whole minor units (cents, or paise on the rupee account); a row's amount is
positive for money out; a balance is as the household sees it, so what is owed
is negative.
"""

import io
from decimal import Decimal

import book_type
import money
import parsers
from parse_dbs import ParsedStatement, ParsedTransaction

AUGUST = "2026-08"

BANK = "Sample Bank 0002"
CARD = "Sample Card 0001"
RUPEE = "Sample Rupee Savings"
LOAN = "Sample Home Loan"
CRYPTO = "Crypto held outside fin"
MOOM = "Moom"

WAITING = "FAST PAYMENT REF 771203 OTHR"     # a bank transfer nobody has labelled
GROCER = "SAMPLE GROCER"                     # the household's spending, planted missing


# --- the month: every kind of movement ---------------------------------------
#
# Cents unless said. At the end of July: the bank 50,000.00, the card owes
# 1,000.00, the rupee account Rs 100,000.00 at 0.0152 (S$ 1,520.00), the loan
# owes 910,000.00, the crypto holding is worth 95,000.00, Moom holds nothing.

BANK_JULY, CARD_JULY, RUPEES_JULY = 5_000_000, -100_000, 10_000_000
OWED_JULY, OWED_AUGUST = 91_000_000, 90_250_000
CRYPTO_FIGURE = 9_500_000
RATE_JULY, RATE_AUGUST = "0.0152", "0.01534"

SALARY = -600_000             # in
INSTALMENT = 880_000          # out, naming the loan: the loan fell 7,500.00, so 1,300.00 is interest
SALE = -2_000_000             # in, naming the crypto holding
PAID_BACK = -100_000          # in, Moom paying the household back
PAYOFF = 100_000              # the bank side of paying the card
SPENT = 45_678                # the household's groceries, on the card
REFUND = -2_050               # some of it came back
ADS = 123_456                 # Moom's cost, on the household's card
RUPEE_INTEREST = -31_025      # Rs 310.25 in
RUPEE_SPENT = 150_000         # Rs 1,500.00 out

INTEREST = INSTALMENT - (OWED_JULY - OWED_AUGUST)                 # 1,300.00
# The rupees at the end rate: Rs 100,000.00 is S$ 1,534.00; with the interest,
# Rs 100,310.25 is S$ 1,538.76; less the spending, Rs 98,810.25 is S$ 1,515.75.
RUPEE_INCOME_SGD = 153_876 - 153_400                              # 4.76
RUPEE_SPENDING_SGD = 153_876 - 151_575                            # 23.01
CURRENCY_CHANGE = 153_400 - 152_000                               # 14.00
INCOME = -SALARY + RUPEE_INCOME_SGD                               # 6,004.76
SPENDING = SPENT + REFUND + INTEREST + RUPEE_SPENDING_SGD         # 1,759.29

NET_WORTH_JULY = BANK_JULY + CARD_JULY + 152_000 - OWED_JULY + CRYPTO_FIGURE + 0


# --- helpers --------------------------------------------------------------------


def make_account(client, name: str, kind: str, **extra) -> int:
    resp = client.post("/api/accounts", json={"name": name, "type": kind, **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def enter(client, account_id: int, amount: str, on: str) -> None:
    resp = client.post("/api/anchors", json={"account_id": account_id, "amount": amount, "date": on})
    assert resp.status_code == 200, resp.get_json()


def set_rate(client, on: str, rate: str) -> None:
    resp = client.put("/api/rates", json={"currency": "INR", "date": on, "rate": rate})
    assert resp.status_code == 200, resp.get_json()


def units(cents: int) -> str:
    """Whole minor units as the operator types a figure: "910000.00"."""
    return f"{cents // 100}.{cents % 100:02d}"


def bring_in(client, account: str, opening: int, rows: list[tuple], *, kind: str = "bank",
             closing_date: str = "2026-08-31", currency: str = "SGD") -> int:
    """Upload and confirm one statement that ties: opening less its rows is
    the closing balance, written as an anchor. Each row is (date,
    description, amount, flow, labels) and is labelled in the preview as the
    operator would: labels may give a book, a type by name and the account a
    movement names. Returns the closing balance."""
    closing = opening - sum(amount for _, _, amount, _, _ in rows)

    def parse(_path):
        return [ParsedStatement(
            statement_type=kind,
            statement_date=closing_date,
            accounts=[account],
            filename="sample.csv",
            transactions=[
                ParsedTransaction(date=on, description=description, amount_minor=amount,
                                  card_info=account)
                for on, description, amount, _, _ in rows
            ],
            opening_minor=opening,
            closing_minor=closing,
            opening_date=closing_date[:8] + "01",
            closing_date=closing_date,
            currency=currency,
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
    assert preview["errors"] == [], preview["errors"]
    labels = {description: (flow, extra) for _, description, _, flow, extra in rows}
    type_ids = None
    for group in preview["groups"]:
        for tx in group["transactions"]:
            flow, extra = labels[tx["description"]]
            if type_ids is None:
                type_ids = types(client)
            tx.update(
                _skip=False,
                flow_type=flow,
                book=extra.get("book"),
                type_id=type_ids[extra["type"]] if "type" in extra else None,
                other_side_id=extra.get("names"),
                service_id=None,
            )
    resp = client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [
            {"account": g["account"], "transactions": g["transactions"],
             "statements": g.get("statements", []), "currency": g.get("currency")}
            for g in preview["groups"]
        ],
    })
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["transactions_saved"] == len(rows)
    return closing


def types(client) -> dict:
    """Each top-level spending type's id by its name, as the type list serves them."""
    return {t["name"]: t["id"] for t in client.get("/api/types").get_json() if t["parent_id"] is None}


def sheet(client, month: str = AUGUST) -> dict:
    resp = client.get(f"/api/balance-sheet?month={month}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def check(client, month: str = AUGUST) -> dict:
    return sheet(client, month)["month_check"]


def household(type_name: str) -> dict:
    return {"book": book_type.DEFAULT_BOOK, "type": type_name}


def build_month(client, *, waiting: bool = False) -> dict:
    """August 2026 with every kind of movement in it, every row labelled,
    and every statement tying. With `waiting`, one bank transfer of 250.00 is
    left on the review list."""
    loan = make_account(client, LOAN, "loan")
    crypto = make_account(client, CRYPTO, "holding")
    moom = make_account(client, MOOM, "company")
    make_account(client, RUPEE, "bank", currency="INR")
    enter(client, loan, units(OWED_JULY), "2026-07-31")
    enter(client, crypto, units(CRYPTO_FIGURE), "2026-07-15")
    set_rate(client, "2026-07-31", RATE_JULY)
    set_rate(client, "2026-08-31", RATE_AUGUST)

    # July: one row each, so each account has a balance at the end of July.
    bring_in(client, BANK, BANK_JULY + 10_000, [
        ("2026-07-10", "SAMPLE BAKERY JUL", 10_000, "expense", household("Groceries")),
    ], closing_date="2026-07-31")
    bring_in(client, CARD, CARD_JULY + 5_000, [
        ("2026-07-12", "SAMPLE CAFE JUL", 5_000, "expense", household("Groceries")),
    ], kind="card", closing_date="2026-07-31")
    bring_in(client, RUPEE, RUPEES_JULY + 50_000, [
        ("2026-07-14", "UPI-SAMPLE GROCER JUL", 50_000, "expense", household("Groceries")),
    ], closing_date="2026-07-31", currency="INR")

    # August.
    bank_rows = [
        ("2026-08-01", "SAMPLE EMPLOYER SALARY", SALARY, "income", {}),
        ("2026-08-15", "SAMPLE LENDER INSTALMENT", INSTALMENT, "movement", {"names": loan}),
        ("2026-08-20", "INWARD CREDIT INDEPENDENT RESERVE", SALE, "movement", {"names": crypto}),
        ("2026-08-25", "PAYNOW FROM MOOM SAMPLE", PAID_BACK, "movement", {"names": moom}),
        ("2026-08-27", "PAYMENT TO SAMPLE CARD", PAYOFF, "payment", {}),
    ]
    if waiting:
        bank_rows.append(("2026-08-28", WAITING, 25_000, "review", {}))
    bring_in(client, BANK, BANK_JULY, bank_rows)
    bring_in(client, CARD, CARD_JULY, [
        ("2026-08-05", GROCER, SPENT, "expense", household("Groceries")),
        ("2026-08-09", "SAMPLE GROCER REFUND", REFUND, "refund", household("Groceries")),
        ("2026-08-11", "SMPL ADS", ADS, "expense", {"book": "Moom", "type": "Advertising"}),
        ("2026-08-28", "PAYMENT - ATM/INTERNET", -PAYOFF, "payment", {}),
    ], kind="card")
    bring_in(client, RUPEE, RUPEES_JULY, [
        ("2026-08-31", "INTEREST PAID TILL 31-AUG-2026", RUPEE_INTEREST, "income", {}),
        ("2026-08-18", "UPI-SAMPLE PHARMACY", RUPEE_SPENT, "expense", household("Groceries")),
    ], currency="INR")

    # The loan's figure for the end of August: interest is worked out.
    enter(client, loan, units(OWED_AUGUST), "2026-08-31")
    return {"loan": loan, "crypto": crypto, "moom": moom}


# --- P23: a complete month comes out at exactly nothing unexplained -------------


def test_a_complete_month_with_every_kind_of_movement_leaves_nothing_unexplained(client):
    build_month(client)

    shown = check(client)

    assert shown["available"] is True
    assert (shown["from"], shown["to"]) == ("2026-07-31", "2026-08-31")
    assert shown["opening_minor"] == NET_WORTH_JULY == sheet(client, "2026-07")["net_worth_minor"]
    assert shown["income_minor"] == INCOME
    assert shown["spending_minor"] == SPENDING
    assert shown["interest_minor"] == INTEREST
    assert shown["currency_change_minor"] == CURRENCY_CHANGE
    assert shown["expected_minor"] == NET_WORTH_JULY + INCOME - SPENDING + CURRENCY_CHANGE
    assert shown["actual_minor"] == sheet(client)["net_worth_minor"]
    assert shown["unexplained_minor"] == 0
    assert shown["unexplained"] == "S$ 0.00"
    assert shown["left_out"] == []
    assert (shown["review"]["count"], shown["review"]["out_minor"], shown["review"]["in_minor"]) == (0, 0, 0)
    assert shown["not_tying"] == []


def test_a_planted_missing_row_is_the_unexplained_amount_to_the_cent(client, conn):
    build_month(client)
    # The groceries never reached fin: the card's statement balance still
    # says they were spent.
    conn.execute("DELETE FROM transactions WHERE description = ?", (GROCER,))
    conn.commit()

    shown = check(client)

    assert shown["spending_minor"] == SPENDING - SPENT
    # Net worth is lower than the rows say by exactly the row: money left
    # that no row accounts for.
    assert shown["unexplained_minor"] == -SPENT == -45_678
    assert shown["unexplained"] == "S$ -456.78"
    # And the card's line, which no longer ties, is named beside it: its rows
    # make the balance 456.78 more than the statement says.
    (line,) = shown["not_tying"]
    assert (line["name"], line["status"], line["difference_minor"], line["text"]) == (
        CARD, "off", SPENT, "off by S$ 456.78",
    )
    assert line["date"] == "2026-08-31"


def test_a_transfer_left_unlabelled_shows_in_the_review_count_beside_the_check(client):
    build_month(client, waiting=True)

    shown = check(client)

    # The 250.00 left the bank and no label says what it became.
    assert shown["unexplained_minor"] == -25_000
    review = shown["review"]
    assert (review["count"], review["out_count"], review["out_minor"], review["out"]) == (
        1, 1, 25_000, "S$ 250.00",
    )
    assert (review["in_count"], review["in_minor"]) == (0, 0)
    # The statement ties: the row is there, only its label is missing.
    assert shown["not_tying"] == []


# --- the first month has nothing to start from ----------------------------------


def test_january_2026_has_no_previous_net_worth_and_says_so_rather_than_invent_one(client):
    bring_in(client, BANK, 5_000_000, [
        ("2026-01-05", "SAMPLE EMPLOYER SALARY", SALARY, "income", {}),
        ("2026-01-09", "SAMPLE BAKERY", 10_000, "expense", household("Groceries")),
    ], closing_date="2026-01-31")

    shown = check(client, "2026-01")

    assert shown["available"] is False
    assert (shown["opening_minor"], shown["expected_minor"], shown["unexplained_minor"]) == (None, None, None)
    assert shown["unexplained"] is None
    assert "first month" in shown["why"] and "31 Dec 2025" in shown["why"]
    # What the month's rows say is still shown.
    assert (shown["income_minor"], shown["spending_minor"]) == (600_000, 10_000)
    assert shown["actual_minor"] == 5_590_000


# --- a loan whose figures are months apart --------------------------------------
#
# Defect found building this check (ticket 19's loan balance): between two
# figures the loan's balance took its instalments in full, while the interest
# worked out from the later figure is spread over the months between. July's
# net worth then held none of July's interest while July's spending held all
# of it, so a complete July came out unexplained by its interest share, and
# August by the same amount the other way. The balance between two figures
# now falls by the principal repaid: the instalments less the interest worked
# out for the days since the figure, as the dashboard's principal card says.

OWED_JUNE = 91_000_000
JULY_INSTALMENT, AUGUST_INSTALMENT = 880_000, 880_001
JULY_INTEREST, AUGUST_INTEREST = 505_001, 505_000    # 10,100.01 over two months


def build_loan_months(client) -> int:
    loan = make_account(client, LOAN, "loan")
    enter(client, loan, units(OWED_JUNE), "2026-06-30")
    bring_in(client, BANK, 5_010_000, [
        ("2026-06-10", "SAMPLE BAKERY JUN", 10_000, "expense", household("Groceries")),
    ], closing_date="2026-06-30")
    bring_in(client, BANK, 5_000_000, [
        ("2026-07-15", "SAMPLE LENDER INSTALMENT JUL", JULY_INSTALMENT, "movement", {"names": loan}),
    ], closing_date="2026-07-31")
    bring_in(client, BANK, 5_000_000 - JULY_INSTALMENT, [
        ("2026-08-15", "SAMPLE LENDER INSTALMENT AUG", AUGUST_INSTALMENT, "movement", {"names": loan}),
    ])
    # The loan fell by 7,500.00 over the two months.
    enter(client, loan, units(OWED_JUNE - 750_000), "2026-08-31")
    return loan


def test_a_month_between_two_loan_figures_leaves_nothing_unexplained(client):
    build_loan_months(client)

    july, august = check(client, "2026-07"), check(client, "2026-08")

    assert (july["interest_minor"], august["interest_minor"]) == (JULY_INTEREST, AUGUST_INTEREST)
    assert july["unexplained_minor"] == 0
    assert august["unexplained_minor"] == 0


def test_between_two_figures_the_loan_falls_by_the_principal_repaid(client):
    build_loan_months(client)

    line = next(
        l for s in sheet(client, "2026-07")["sections"] for l in s["lines"] if l["name"] == LOAN
    )

    # 910,000.00 owed, 8,800.00 paid, of which 5,050.01 was interest.
    assert line["balance_minor"] == -OWED_JUNE + JULY_INSTALMENT - JULY_INTEREST == -90_625_001
    assert line["since"] == "1 instalment since, less S$ 5,050.01 interest worked out"
    # At the later figure the loan is that figure, as before.
    august = next(
        l for s in sheet(client, "2026-08")["sections"] for l in s["lines"] if l["name"] == LOAN
    )
    assert august["balance_minor"] == -(OWED_JUNE - 750_000)


# --- a rupee account that cannot be valued at both ends -------------------------


def sgd_month(client) -> None:
    """A bank account through July and August: salary in, groceries out."""
    bring_in(client, BANK, 5_010_000, [
        ("2026-07-10", "SAMPLE BAKERY JUL", 10_000, "expense", household("Groceries")),
    ], closing_date="2026-07-31")
    bring_in(client, BANK, 5_000_000, [
        ("2026-08-01", "SAMPLE EMPLOYER SALARY", SALARY, "income", {}),
        ("2026-08-09", "SAMPLE BAKERY", 10_000, "expense", household("Groceries")),
    ])


def rupee_august(client) -> None:
    bring_in(client, RUPEE, RUPEES_JULY, [
        ("2026-08-31", "INTEREST PAID TILL 31-AUG-2026", RUPEE_INTEREST, "income", {}),
        ("2026-08-18", "UPI-SAMPLE PHARMACY", RUPEE_SPENT, "expense", household("Groceries")),
    ], currency="INR")


def test_with_no_saved_rate_the_rupee_account_is_left_out_of_both_sides_and_said(client):
    make_account(client, RUPEE, "bank", currency="INR")
    sgd_month(client)
    bring_in(client, RUPEE, RUPEES_JULY + 50_000, [
        ("2026-07-14", "UPI-SAMPLE GROCER JUL", 50_000, "expense", household("Groceries")),
    ], closing_date="2026-07-31", currency="INR")
    rupee_august(client)

    shown = check(client)

    assert (shown["income_minor"], shown["spending_minor"], shown["currency_change_minor"]) == (
        600_000, 10_000, 0,
    )
    assert shown["unexplained_minor"] == 0
    (left,) = shown["left_out"]
    assert left["name"] == RUPEE and "no saved INR/SGD rate" in left["why"]
    assert RUPEE in shown["note"]
    # Which account it is, for its fix, and how many stay in the check.
    rupee_id = next(a["id"] for a in client.get("/api/accounts").get_json() if a["name"] == RUPEE)
    assert left["account_id"] == rupee_id
    in_sheet = [l for s in sheet(client)["sections"] for l in s["lines"]]
    # Only the bank is in the check: the rupee account, with no rate, is in
    # net worth at neither end.
    assert shown["in_check_count"] == len([l for l in in_sheet if l["in_total"]]) == 1


def test_a_rupee_account_with_no_balance_at_the_start_is_left_out_of_both_sides(client):
    make_account(client, RUPEE, "bank", currency="INR")
    set_rate(client, "2026-07-31", RATE_JULY)
    set_rate(client, "2026-08-31", RATE_AUGUST)
    sgd_month(client)
    rupee_august(client)            # its first balance is the end of August

    shown = check(client)
    worth = sheet(client)["net_worth_minor"]

    # In net worth at the end of August, but not at the end of July: taken out
    # of the check's figure for the end, as its rows are.
    assert shown["actual_minor"] == worth - 151_575
    assert shown["unexplained_minor"] == 0
    (left,) = shown["left_out"]
    assert left["name"] == RUPEE and "no balance on 2026-07-31" in left["why"]


def test_rupee_rows_at_the_end_rate_leave_no_cent_of_rounding_unexplained(client):
    """Rs 100,000.33 at the end of July and Rs 10.14 of interest in August.
    At the end rate the interest on its own is S$ 0.16 (0.1555 rounded), but
    it moves the account's SGD value from S$ 1,534.01 to S$ 1,534.16: S$ 0.15.
    The check counts what it moved, so nothing is left over."""
    opening, interest = 10_000_033, -1_014
    assert money.convert_minor(-interest, Decimal(RATE_AUGUST), "INR", "SGD") == 16
    make_account(client, RUPEE, "bank", currency="INR")
    set_rate(client, "2026-07-31", RATE_JULY)
    set_rate(client, "2026-08-31", RATE_AUGUST)
    bring_in(client, RUPEE, opening + 50_000, [
        ("2026-07-14", "UPI-SAMPLE GROCER JUL", 50_000, "expense", household("Groceries")),
    ], closing_date="2026-07-31", currency="INR")
    bring_in(client, RUPEE, opening, [
        ("2026-08-31", "INTEREST PAID TILL 31-AUG-2026", interest, "income", {}),
    ], currency="INR")

    shown = check(client)

    assert shown["actual_minor"] - shown["opening_minor"] == 153_416 - 152_001
    assert shown["currency_change_minor"] == 153_401 - 152_001
    assert shown["income_minor"] == 15
    assert shown["unexplained_minor"] == 0
