"""The balance sheet, through the HTTP interface against a temporary database.

Rows and statement balances reach the ledger the way the operator's do: a
statement is uploaded and confirmed (a stand-in parser returns invented rows
and balances), and figures are entered through the anchors endpoint. Every
name and figure is invented. Amounts are whole minor units (cents); a row's
amount is positive for money out; a balance is as the household sees it, so
what is owed is negative.
"""

import io

import pytest

import money
import parsers
from parse_dbs import ParsedStatement, ParsedTransaction

BANK = "Sample Bank 0002"
CARD = "Sample Card 0001"

SPENT = "SAMPLE GROCER"                       # spending, on any account
WAITING = "FAST PAYMENT REF 771203 OTHR"      # a bank transfer nobody has labelled


# --- helpers --------------------------------------------------------------------


def make_account(client, name: str, kind: str, **extra) -> int:
    resp = client.post("/api/accounts", json={"name": name, "type": kind, **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def enter(client, account_id: int, amount: str, on: str) -> None:
    resp = client.post("/api/anchors", json={"account_id": account_id, "amount": amount, "date": on})
    assert resp.status_code == 200, resp.get_json()


def bring_in(client, account: str, rows: list[tuple], *, kind: str = "bank",
             opening: int | None = None, closing: int | None = None,
             closing_date: str = "2026-08-31", names: dict | None = None) -> None:
    """Upload and confirm one statement of (date, description, cents) rows.

    With `opening` and `closing` the statement states its balances, is checked
    and writes its closing balance as an anchor; without them it is a source
    that states none. `names` maps a row's description to the account a
    movement names as its other side.
    """
    stated = opening is not None

    def parse(_path):
        return [ParsedStatement(
            statement_type=kind,
            statement_date=closing_date,
            accounts=[account],
            filename="sample.csv",
            transactions=[
                ParsedTransaction(date=on, description=description, amount_minor=amount,
                                  card_info=account)
                for on, description, amount in rows
            ],
            opening_minor=opening,
            closing_minor=closing,
            opening_date=closing_date[:8] + "01" if stated else None,
            closing_date=closing_date if stated else None,
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
    for group in preview["groups"]:
        for tx in group["transactions"]:
            tx["_skip"] = False
            if names and tx["description"] in names:
                tx["flow_type"] = "movement"
                tx["other_side_id"] = names[tx["description"]]
    resp = client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [
            {"account": g["account"], "transactions": g["transactions"],
             "statements": g.get("statements", [])}
            for g in preview["groups"]
        ],
    })
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["transactions_saved"] == len(rows)


def sheet(client, month: str) -> dict:
    resp = client.get(f"/api/balance-sheet?month={month}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def lines(shown: dict) -> dict:
    return {line["name"]: line for section in shown["sections"] for line in section["lines"]}


def section(shown: dict, name: str) -> dict:
    return next(s for s in shown["sections"] if s["name"] == name)


def rows_between(client, account: str, after: str, upto: str) -> int:
    """What the account's rows dated after one day and up to another add up to, in cents."""
    listed = client.get("/api/transactions?per_page=500").get_json()["transactions"]
    return sum(
        money.to_minor(tx["amount_sgd"]) for tx in listed
        if tx["account_name"] == account and after < tx["date"] <= upto
    )


@pytest.fixture
def bank(client) -> int:
    return make_account(client, BANK, "bank", last_four="0002")


@pytest.fixture
def card(client) -> int:
    return make_account(client, CARD, "card", last_four="0001")


# --- P15: a balance is its anchor less the rows after it ---------------------------


def test_a_bank_balance_is_its_statement_balance_less_the_rows_since(client, bank):
    # July's statement: 50,000.00 less 8,799.50 out is 41,200.50. Its last
    # row is dated on the closing day, and is inside the balance it states.
    bring_in(client, BANK, [("2026-07-10", SPENT, 800000), ("2026-07-31", SPENT + " LAST DAY", 79950)],
             opening=5000000, closing=4120050, closing_date="2026-07-31")
    # August, from a source that states no balance: 1,200.25 out, 300.00 in,
    # and one row dated after the month shown.
    bring_in(client, BANK, [
        ("2026-08-05", SPENT + " A", 120025),
        ("2026-08-20", "SAMPLE EMPLOYER SALARY", -30000),
        ("2026-09-02", SPENT + " B", 5500),
    ])

    line = lines(sheet(client, "2026-08"))[BANK]

    assert line["balance_minor"] == 4120050 - 120025 + 30000 == 4030025
    assert line["balance"] == "S$ 40,300.25"
    assert line["rests_on"] == {
        "date": "2026-07-31", "source": "statement", "label": "statement",
        "age_days": 31, "in_month": False,
    }
    assert (line["rows_since"], line["since"]) == (2, "+ 2 rows since")
    # Conservation: the balance and the rows since add back to the anchor.
    assert line["balance_minor"] + rows_between(client, BANK, "2026-07-31", "2026-08-31") == 4120050


def test_a_balance_on_its_anchor_date_is_the_anchor_and_later_months_roll_on(client, bank):
    bring_in(client, BANK, [("2026-07-10", SPENT, 879950)],
             opening=5000000, closing=4120050, closing_date="2026-07-31")
    bring_in(client, BANK, [("2026-08-05", SPENT + " A", 120025), ("2026-09-02", SPENT + " B", 5500)])

    july = lines(sheet(client, "2026-07"))[BANK]
    september = lines(sheet(client, "2026-09"))[BANK]

    assert (july["balance_minor"], july["rows_since"], july["since"]) == (4120050, 0, None)
    assert july["rests_on"]["age_days"] == 0 and july["rests_on"]["in_month"] is True
    assert september["balance_minor"] == 4120050 - 120025 - 5500 == 3994525
    assert september["rests_on"]["date"] == "2026-07-31"
    assert september["rests_on"]["age_days"] == 61


def test_a_card_shows_its_statement_balance_rolled_forward_and_negative(client, card):
    # Owed 1,000.00; 5,620.10 of purchases and a 200.00 payment: owed 6,420.10.
    bring_in(client, CARD, [("2026-08-03", SPENT, 562010), ("2026-08-09", "PAYMENT - ATM/INTERNET", -20000)],
             kind="card", opening=-100000, closing=-642010, closing_date="2026-08-14")
    bring_in(client, CARD, [("2026-08-20", SPENT + " A", 10000), ("2026-08-25", SPENT + " REFUND", -4000)],
             kind="card")

    shown = sheet(client, "2026-08")
    line = lines(shown)[CARD]

    assert line["balance_minor"] == -642010 - 10000 + 4000 == -648010
    assert line["balance"] == "S$ -6,480.10"
    assert line["rests_on"] == {
        "date": "2026-08-14", "source": "statement", "label": "statement",
        "age_days": 17, "in_month": True,
    }
    assert line["since"] == "+ 2 rows since"
    assert line["balance_minor"] + rows_between(client, CARD, "2026-08-14", "2026-08-31") == -642010
    cards = section(shown, "cards")
    assert cards["heading"] == "Cards (owed)" and cards["owed"] is True
    assert (cards["total_minor"], cards["total"]) == (-648010, "S$ -6,480.10")


def test_an_account_with_no_anchor_on_or_before_the_date_has_no_balance(client, bank):
    bring_in(client, BANK, [("2026-07-10", SPENT, 879950)],
             opening=5000000, closing=4120050, closing_date="2026-07-31")

    shown = sheet(client, "2026-06")
    line = lines(shown)[BANK]

    assert line["balance_minor"] is None and line["balance"] == "no figure"
    assert line["rests_on"] is None
    assert line["in_total"] is False and line["left_out"] == "no figure"
    assert shown["net_worth_minor"] == 0
    assert BANK in shown["note"] and "no figure" in shown["note"]


def test_the_latest_anchor_on_or_before_the_date_is_the_one_used(client):
    car = make_account(client, "Sample Car", "holding")
    enter(client, car, "120000.00", "2026-01-01")
    enter(client, car, "110000.00", "2026-07-15")
    enter(client, car, "105000.00", "2026-09-10")

    assert [
        (lines(sheet(client, month))["Sample Car"]["balance_minor"],
         lines(sheet(client, month))["Sample Car"]["rests_on"]["date"])
        for month in ("2026-06", "2026-07", "2026-08", "2026-09")
    ] == [
        (12000000, "2026-01-01"), (11000000, "2026-07-15"),
        (11000000, "2026-07-15"), (10500000, "2026-09-10"),
    ]


@pytest.mark.parametrize("month", ["2025-12", "2024-06"])
def test_nothing_is_shown_for_a_date_before_the_start(client, bank, month):
    enter(client, bank, "50000.00", "2025-11-30")

    resp = client.get(f"/api/balance-sheet?month={month}")

    assert resp.status_code == 400
    body = resp.get_json()
    assert "1 January 2026" in body["error"]
    assert "sections" not in body and "net_worth_minor" not in body


@pytest.mark.parametrize("month", ["2026-13", "2026-8", "August 2026", "2026-08-31", ""])
def test_a_month_that_is_not_a_month_is_refused(client, month):
    resp = client.get(f"/api/balance-sheet?month={month}")

    assert resp.status_code == 400
    assert "month" in resp.get_json()["error"]


def test_the_first_month_shown_is_january_2026(client, bank):
    enter(client, bank, "50000.00", "2026-01-01")

    shown = sheet(client, "2026-01")

    assert (shown["month"], shown["as_at"]) == ("2026-01", "2026-01-31")
    assert lines(shown)[BANK]["balance_minor"] == 5000000


# --- loans and holdings: a supplied figure and the movements naming it -------------


def test_a_loan_falls_by_its_instalments_and_a_holding_by_its_sales(client, bank):
    loan = make_account(client, "Sample Home Loan", "loan")
    crypto = make_account(client, "Sample Crypto", "holding")
    enter(client, loan, "910000.00", "2026-06-30")
    enter(client, crypto, "95000.00", "2026-06-30")
    # June's statement leaves 100,000.00 in the bank.
    bring_in(client, BANK, [("2026-06-10", SPENT, 250000)],
             opening=10250000, closing=10000000, closing_date="2026-06-30")
    # Since: two instalments of 8,800.00 and a 20,000.00 sale paid into the bank.
    bring_in(client, BANK, [
        ("2026-07-05", "SAMPLE LENDER INSTALMENT JUL", 880000),
        ("2026-08-05", "SAMPLE LENDER INSTALMENT AUG", 880000),
        ("2026-08-18", "SAMPLE EXCHANGE WITHDRAWAL", -2000000),
    ], names={
        "SAMPLE LENDER INSTALMENT JUL": loan,
        "SAMPLE LENDER INSTALMENT AUG": loan,
        "SAMPLE EXCHANGE WITHDRAWAL": crypto,
    })

    june, august = sheet(client, "2026-06"), sheet(client, "2026-08")
    shown = lines(august)

    assert shown["Sample Home Loan"]["balance_minor"] == -91000000 + 880000 + 880000 == -89240000
    assert shown["Sample Home Loan"]["balance"] == "S$ -892,400.00"
    assert shown["Sample Home Loan"]["rests_on"] == {
        "date": "2026-06-30", "source": "supplied", "label": "your figure",
        "age_days": 62, "in_month": False,
    }
    assert shown["Sample Home Loan"]["since"] == "2 instalments since"
    assert shown["Sample Home Loan"]["check"] is None  # a supplied figure has no check
    assert shown["Sample Crypto"]["balance_minor"] == 9500000 - 2000000 == 7500000
    assert shown["Sample Crypto"]["since"] == "1 sale since"
    assert shown["Sample Crypto"]["check"] is None
    assert shown[BANK]["balance_minor"] == 10000000 - 880000 - 880000 + 2000000 == 10240000
    loans = section(august, "loans")
    assert loans["heading"] == "Loans (owed)" and loans["owed"] is True
    # Money only changed form: net worth is the same before and after, to the cent.
    assert june["net_worth_minor"] == 10000000 - 91000000 + 9500000 == -71500000
    assert august["net_worth_minor"] == june["net_worth_minor"]
    assert august["net_worth"] == "S$ -715,000.00"


def test_a_movement_dated_on_or_before_a_figure_does_not_move_it(client, bank):
    loan = make_account(client, "Sample Home Loan", "loan")
    bring_in(client, BANK, [
        ("2026-06-30", "SAMPLE LENDER INSTALMENT JUN", 880000),
        ("2026-09-05", "SAMPLE LENDER INSTALMENT SEP", 880000),
    ], names={"SAMPLE LENDER INSTALMENT JUN": loan, "SAMPLE LENDER INSTALMENT SEP": loan})
    enter(client, loan, "910000.00", "2026-06-30")

    line = lines(sheet(client, "2026-08"))["Sample Home Loan"]

    assert (line["balance_minor"], line["rows_since"], line["since"]) == (-91000000, 0, None)


# --- P21: what each line shows, and what the totals count --------------------------


def test_the_sheet_shows_each_line_its_anchor_and_check_and_adds_up(client, bank, card):
    loan = make_account(client, "Sample Home Loan", "loan")
    home = make_account(client, "Sample Home", "holding")
    make_account(client, "Sample Car", "holding")  # no figure yet
    bring_in(client, BANK, [("2026-08-10", SPENT, 879950)],
             opening=5000000, closing=4120050, closing_date="2026-08-31")
    bring_in(client, CARD, [("2026-08-03", SPENT, 542010)],
             kind="card", opening=-100000, closing=-642010, closing_date="2026-08-14")
    enter(client, loan, "910000.00", "2026-06-30")
    enter(client, home, "1900000.00", "2026-01-01")

    shown = sheet(client, "2026-08")

    assert (shown["month"], shown["as_at"], shown["currency"]) == ("2026-08", "2026-08-31", "SGD")
    assert [(s["name"], s["heading"], s["owed"]) for s in shown["sections"]] == [
        ("cash", "Cash and deposits", False),
        ("cards", "Cards (owed)", True),
        ("loans", "Loans (owed)", True),
        ("holdings", "Holdings", False),
    ]
    assert {
        name: (line["kind"], line["balance"], line["rests_on"] and (
            line["rests_on"]["label"], line["rests_on"]["date"], line["rests_on"]["age_days"]))
        for name, line in lines(shown).items()
    } == {
        BANK: ("bank", "S$ 41,200.50", ("statement", "2026-08-31", 0)),
        CARD: ("card", "S$ -6,420.10", ("statement", "2026-08-14", 17)),
        "Sample Home Loan": ("loan", "S$ -910,000.00", ("your figure", "2026-06-30", 62)),
        "Sample Home": ("holding", "S$ 1,900,000.00", ("your figure", "2026-01-01", 242)),
        "Sample Car": ("holding", "no figure", None),
    }
    assert {s["name"]: (s["total_minor"], s["total"]) for s in shown["sections"]} == {
        "cash": (4120050, "S$ 41,200.50"),
        "cards": (-642010, "S$ -6,420.10"),
        "loans": (-91000000, "S$ -910,000.00"),
        "holdings": (190000000, "S$ 1,900,000.00"),
    }
    # Every section total is the sum of its lines in the total, and net worth
    # is the sum of the sections.
    for s in shown["sections"]:
        assert s["total_minor"] == sum(l["balance_minor"] for l in s["lines"] if l["in_total"])
    assert shown["net_worth_minor"] == sum(s["total_minor"] for s in shown["sections"])
    assert shown["net_worth_minor"] == 4120050 - 642010 - 91000000 + 190000000 == 102478040
    assert shown["net_worth"] == "S$ 1,024,780.40"
    # The holding with no figure is left out, and the total says so.
    car = lines(shown)["Sample Car"]
    assert (car["balance_minor"], car["in_total"], car["left_out"]) == (None, False, "no figure")
    assert section(shown, "holdings")["left_out"] == [{"name": "Sample Car", "why": "no figure"}]
    assert shown["left_out"] == [{"name": "Sample Car", "why": "no figure"}]
    assert shown["note"] == "Left out of the total: Sample Car (no figure)"


def test_a_sheet_with_nothing_left_out_says_nothing(client, bank):
    enter(client, bank, "50000.00", "2026-01-01")

    shown = sheet(client, "2026-08")

    assert shown["left_out"] == [] and shown["note"] is None
    assert shown["net_worth_minor"] == 5000000


def test_an_account_in_another_currency_is_shown_in_it_and_left_out_of_the_total(client, bank):
    rupee = make_account(client, "Sample Rupee Savings", "bank", currency="INR")
    enter(client, bank, "50000.00", "2026-08-31")
    enter(client, rupee, "1250000.00", "2026-08-31")

    shown = sheet(client, "2026-08")
    line = lines(shown)["Sample Rupee Savings"]

    assert (line["currency"], line["balance_minor"], line["balance"]) == (
        "INR", 125000000, "Rs 1,250,000.00",
    )
    assert line["in_total"] is False
    assert "INR" in line["left_out"]
    assert section(shown, "cash")["total_minor"] == 5000000
    assert shown["net_worth_minor"] == 5000000
    assert "Sample Rupee Savings" in shown["note"] and "INR" in shown["note"]


def test_only_the_households_bank_card_loan_and_holding_accounts_are_on_the_sheet(client, bank):
    business = make_account(client, "Sample Business 0003", "bank", owner="Kalesh")
    company = make_account(client, "Sample Company", "company")
    person = make_account(client, "Sample Friend", "person")
    archived = make_account(client, "Sample Closed 0004", "bank")
    client.put(f"/api/accounts/{archived}", json={"status": "archived"})
    for account_id in (bank, business, company, person):
        enter(client, account_id, "1000.00", "2026-08-31")

    shown = sheet(client, "2026-08")

    assert list(lines(shown)) == [BANK]
    assert shown["net_worth_minor"] == 100000


def test_the_sheet_says_how_many_transfers_wait_for_review(client, bank):
    assert sheet(client, "2026-08")["review_waiting"] == 0

    bring_in(client, BANK, [("2026-08-10", WAITING, 120050), ("2026-08-12", SPENT, 500)])

    assert sheet(client, "2026-08")["review_waiting"] == 1


# --- P22: ties, off by the exact amount, or not checked ----------------------------


def test_an_account_whose_rows_carry_one_statement_balance_to_the_next_ties(client, bank):
    bring_in(client, BANK, [("2026-07-10", SPENT, 879950)],
             opening=5000000, closing=4120050, closing_date="2026-07-31")
    bring_in(client, BANK, [("2026-08-05", SPENT + " A", 120025), ("2026-08-20", "SAMPLE EMPLOYER SALARY", -30000)],
             opening=4120050, closing=4030025, closing_date="2026-08-31")

    line = lines(sheet(client, "2026-08"))[BANK]

    assert line["check"] == {"status": "ties", "difference_minor": 0, "text": "ties"}
    assert line["balance_minor"] == 4030025 and line["rows_since"] == 0


def test_an_account_month_whose_rows_miss_the_statement_is_off_by_the_exact_amount(client, bank):
    bring_in(client, BANK, [("2026-07-10", SPENT, 879950)],
             opening=5000000, closing=4120050, closing_date="2026-07-31")
    # August's statement ties on its own figures, but opens 140.00 above what
    # July closed on: a row between the two is missing from the ledger.
    bring_in(client, BANK, [("2026-08-05", SPENT + " A", 120025)],
             opening=4134050, closing=4014025, closing_date="2026-08-31")

    line = lines(sheet(client, "2026-08"))[BANK]

    # The rows held carry July's balance to 40,000.25; the statement says 40,140.25.
    assert 4120050 - 120025 - 4014025 == -14000
    assert line["check"] == {"status": "off", "difference_minor": -14000, "text": "off by S$ 140.00"}
    # The statement is trusted: the balance is the one it states.
    assert line["balance_minor"] == 4014025


def test_an_account_off_by_one_cent_says_one_cent(client, card):
    bring_in(client, CARD, [("2026-07-03", SPENT, 100000)],
             kind="card", opening=0, closing=-100000, closing_date="2026-07-14")
    bring_in(client, CARD, [("2026-08-03", SPENT + " A", 5000)],
             kind="card", opening=-99999, closing=-104999, closing_date="2026-08-14")

    line = lines(sheet(client, "2026-08"))[CARD]

    assert line["check"] == {"status": "off", "difference_minor": -1, "text": "off by S$ 0.01"}


def test_a_source_that_states_no_balance_is_not_checked_and_says_why(client, card):
    bring_in(client, CARD, [("2026-08-03", SPENT, 562010)], kind="card")

    line = lines(sheet(client, "2026-08"))[CARD]

    assert line["check"] == {
        "status": "not_checked", "difference_minor": None,
        "text": "not checked (no statement balance held)",
    }
    assert line["balance_minor"] is None and line["in_total"] is False


def test_the_first_statement_balance_held_is_not_checked_and_says_why(client, bank):
    bring_in(client, BANK, [("2026-07-10", SPENT, 879950)],
             opening=5000000, closing=4120050, closing_date="2026-07-31")

    line = lines(sheet(client, "2026-07"))[BANK]

    assert line["check"] == {
        "status": "not_checked", "difference_minor": None,
        "text": "not checked (no earlier balance to check against)",
    }
    assert line["balance_minor"] == 4120050


def test_a_bank_account_resting_on_a_supplied_figure_has_no_check(client, bank):
    enter(client, bank, "50000.00", "2026-08-31")

    line = lines(sheet(client, "2026-08"))[BANK]

    assert line["check"] is None
    assert line["rests_on"]["label"] == "your figure"


def test_reading_the_sheet_changes_nothing(client, conn, bank):
    bring_in(client, BANK, [("2026-07-10", SPENT, 879950)],
             opening=5000000, closing=4120050, closing_date="2026-07-31")

    def held():
        return [
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("accounts", "statements", "transactions", "anchors")
        ]

    before = held()
    first = sheet(client, "2026-08")

    assert sheet(client, "2026-08") == first
    assert held() == before
