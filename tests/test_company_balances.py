"""The companies-and-people section of the balance sheet: the household's
money in each company and with each person, and what each balance is made of.

Driven through the HTTP interface against the temporary database. Accounts
are made through the accounts endpoint and opening figures through the
anchors endpoint; rows are seeded with SQL as whole cents and read back
through the balance sheet. Every figure, merchant and name here is invented.
Amounts are whole cents; a row's amount is positive for money out.
"""

import pytest

import book_type

AUGUST = "2026-08"
SECTION = "companies"

# Moom: an opening figure on 31 March, then what the household paid and moved.
OPENING = 5000000                       # 50,000.00, entered for 31 March
ADS, MAILER, AD_CREDIT = 30010, 5025, -1010
PAID_FOR_MOOM = ADS + MAILER + AD_CREDIT    # 340.25
CAPITAL = 2000000                       # 20,000.00 put in, in June
PAID_BACK = 1250050                     # 12,500.50 paid back, in June
MOOM = OPENING + PAID_FOR_MOOM + CAPITAL - PAID_BACK   # 57,839.75

# Kalesh: no opening figure.
HOSTING = 2015
# A friend who was lent money.
LENT, REPAID = 300000, 100000
FRIEND = LENT - REPAID


# --- seeding and reading ---------------------------------------------------


def make_account(client, name: str, kind: str, **extra) -> int:
    resp = client.post("/api/accounts", json={"name": name, "type": kind, **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def enter(client, account_id: int, amount: str, on: str) -> None:
    resp = client.post("/api/anchors", json={"account_id": account_id, "amount": amount, "date": on})
    assert resp.status_code == 200, resp.get_json()


def statement(conn, account_id: int) -> int:
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename)"
        " VALUES (?, '2026-08-31', 'sample.csv')",
        (account_id,),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def row(conn, statement_id: int, day: str, description: str, cents: int, *,
        book: str | None = None, type_name: str | None = None,
        flow: str = "expense", names: int | None = None) -> None:
    """A row stored as whole cents. `names` is the account a movement names
    as its other side."""
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor,"
        " book, type_id, flow_type, other_side_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (statement_id, day, description, cents, book,
         book_type.spending_type_ids(conn)[type_name] if type_name else None, flow, names),
    )
    conn.commit()


def sheet(client, month: str = AUGUST) -> dict:
    resp = client.get(f"/api/balance-sheet?month={month}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def section(shown: dict, name: str = SECTION) -> dict:
    return next(s for s in shown["sections"] if s["name"] == name)


def line(shown: dict, name: str) -> dict:
    return next(l for l in section(shown)["lines"] if l["name"] == name)


@pytest.fixture
def ledger(client, conn) -> dict:
    """Two companies and a friend, with rows that move their balances and
    rows that must not."""
    moom = make_account(client, "Moom", "company")
    kalesh = make_account(client, "Kalesh", "company")
    friend = make_account(client, "Sample Friend", "person")
    card = statement(conn, make_account(client, "Sample Card 0001", "card", last_four="0001"))
    bank = statement(conn, make_account(client, "Sample Bank 0002", "bank", last_four="0002"))
    moom_bank = statement(conn, make_account(
        client, "Sample Company Bank 0005", "bank", last_four="0005", owner="Moom"))
    kalesh_bank = statement(conn, make_account(
        client, "Sample Company Bank 0007", "bank", last_four="0007", owner="Kalesh"))

    enter(client, moom, "50000.00", "2026-03-31")

    # Moom's costs paid from the household's card, after the opening figure.
    row(conn, card, "2026-04-10", "SMPL ADS", ADS, book="Moom", type_name="Advertising")
    row(conn, card, "2026-05-02", "SAMPLE MAILER", MAILER, book="Moom")
    row(conn, card, "2026-05-20", "SMPL ADS CREDIT", AD_CREDIT, book="Moom",
        type_name="Advertising", flow="refund")
    # Capital put in, and money paid back.
    row(conn, bank, "2026-06-05", "TRANSFER TO SAMPLE COMPANY", CAPITAL, flow="movement", names=moom)
    row(conn, bank, "2026-06-25", "PAYNOW FROM SAMPLE COMPANY", -PAID_BACK, flow="movement", names=moom)

    # What must not move Moom's balance:
    # a cost dated on or before the opening figure, which the figure holds;
    row(conn, card, "2026-03-15", "SMPL ADS MARCH", 77700, book="Moom", type_name="Advertising")
    row(conn, card, "2026-03-31", "SMPL ADS LAST DAY", 1100, book="Moom", type_name="Advertising")
    # what Moom paid from its own account, and a movement on a company's account;
    row(conn, moom_bank, "2026-04-12", "SAMPLE STOCKIST", 99999, book="Moom",
        type_name="Stock purchases")
    row(conn, kalesh_bank, "2026-04-14", "TRANSFER TO SAMPLE COMPANY", 44400, flow="movement",
        names=moom)
    # salary, which is income and never touches the balance;
    row(conn, bank, "2026-04-28", "SAMPLE COMPANY SALARY", -400000, flow="income", names=moom)
    # a transfer nobody has labelled, and the household's own spending.
    row(conn, bank, "2026-05-11", "FAST PAYMENT REF 771203 OTHR", 250000, flow="review")
    row(conn, card, "2026-05-12", "SAMPLE GROCER", 123456, book="Household", type_name="Groceries")

    # Kalesh: one cost this year, one before the sheet starts.
    row(conn, card, "2026-02-03", "SAMPLE HOSTING", HOSTING, book="Kalesh",
        type_name="Software & AI tools")
    row(conn, card, "2025-12-30", "SAMPLE HOSTING DEC", 4444, book="Kalesh",
        type_name="Software & AI tools")

    # The friend: lent in April, part repaid in July.
    row(conn, bank, "2026-04-02", "PAYNOW TO SAMPLE FRIEND", LENT, flow="movement", names=friend)
    row(conn, bank, "2026-07-09", "PAYNOW FROM SAMPLE FRIEND", -REPAID, flow="movement", names=friend)
    return {"moom": moom, "kalesh": kalesh, "friend": friend}


# --- P16 conservation: a company balance and what it is made of --------------------


def test_a_company_balance_is_its_opening_figure_plus_what_was_paid_and_moved(client, ledger):
    moom = line(sheet(client), "Moom")

    made_of = moom["made_of"]
    assert made_of["opening_minor"] == OPENING
    assert made_of["paid_for_minor"] == PAID_FOR_MOOM == 34025
    assert made_of["capital_minor"] == CAPITAL
    assert made_of["paid_back_minor"] == PAID_BACK
    # 50,000.00 + 340.25 + 20,000.00 - 12,500.50
    assert moom["balance_minor"] == MOOM == 5783975
    assert moom["balance"] == "S$ 57,839.75"
    # Conservation, to the cent.
    assert moom["balance_minor"] == (
        made_of["opening_minor"] + made_of["paid_for_minor"]
        + made_of["capital_minor"] - made_of["paid_back_minor"]
    )


def test_the_company_line_says_what_the_balance_is_made_of(client, ledger):
    moom = line(sheet(client), "Moom")

    assert moom["made_of"]["text"] == (
        "opening S$ 50,000.00 · paid for it S$ 340.25 · capital S$ 20,000.00"
        " · paid back S$ 12,500.50"
    )
    assert moom["rests_on"]["date"] == "2026-03-31"
    assert moom["rests_on"]["label"] == "your figure"
    assert moom["since_label"] is None
    # Three costs and refunds, and two movements, since the figure.
    assert (moom["rows_since"], moom["since"]) == (5, "+ 5 rows since")


def test_a_company_with_no_opening_figure_is_counted_since_the_sheet_starts(client, ledger):
    kalesh = line(sheet(client), "Kalesh")

    # December's cost is before the sheet starts and is not in it.
    assert kalesh["balance_minor"] == HOSTING
    assert kalesh["balance"] == "S$ 20.15"
    assert kalesh["rests_on"] is None
    assert kalesh["since_label"] == "since 1 Jan 2026"
    made_of = kalesh["made_of"]
    assert made_of["opening_minor"] is None
    assert (made_of["paid_for_minor"], made_of["capital_minor"], made_of["paid_back_minor"]) == (
        HOSTING, 0, 0)
    assert made_of["text"] == "paid for it S$ 20.15 · capital S$ 0.00 · paid back S$ 0.00"
    assert kalesh["balance_minor"] == (
        made_of["paid_for_minor"] + made_of["capital_minor"] - made_of["paid_back_minor"]
    )


def test_a_persons_balance_is_the_movements_naming_them(client, ledger):
    friend = line(sheet(client), "Sample Friend")

    assert friend["kind"] == "person"
    assert friend["balance_minor"] == FRIEND == 200000
    made_of = friend["made_of"]
    assert (made_of["paid_for_minor"], made_of["capital_minor"], made_of["paid_back_minor"]) == (
        0, LENT, REPAID)
    assert made_of["text"] == "lent S$ 3,000.00 · paid back S$ 1,000.00"
    assert friend["since_label"] == "since 1 Jan 2026"
    assert friend["balance_minor"] == made_of["capital_minor"] - made_of["paid_back_minor"]


def test_a_company_balance_at_an_earlier_month_end_holds_only_the_rows_up_to_it(client, ledger):
    may = sheet(client, "2026-05")

    # Capital and the repayment are June's; the friend's repayment is July's.
    assert line(may, "Moom")["balance_minor"] == OPENING + PAID_FOR_MOOM
    assert line(may, "Sample Friend")["balance_minor"] == LENT
    # Before the opening figure's date there is no opening: February counts
    # from the start of the sheet, and says so.
    february = line(sheet(client, "2026-02"), "Moom")
    assert february["made_of"]["opening_minor"] is None
    assert february["since_label"] == "since 1 Jan 2026"
    assert february["balance_minor"] == 0


def test_the_section_lists_companies_and_people_and_joins_net_worth(client, ledger):
    shown = sheet(client)
    companies = section(shown)

    assert companies["heading"] == "Companies and people (our money in them)"
    assert companies["owed"] is False
    assert [l["name"] for l in companies["lines"]] == ["Kalesh", "Moom", "Sample Friend"]
    assert all(l["in_total"] for l in companies["lines"])
    assert companies["total_minor"] == MOOM + HOSTING + FRIEND == 5985990
    assert companies["total"] == "S$ 59,859.90"
    # The section is one of those net worth adds up. The household's bank and
    # card here have no balance held, so it is all of net worth.
    assert shown["net_worth_minor"] == sum(s["total_minor"] for s in shown["sections"])
    assert shown["net_worth_minor"] == companies["total_minor"]


def test_a_later_figure_for_a_company_takes_over_from_the_rows_before_it(client, ledger):
    enter(client, ledger["moom"], "61000.00", "2026-06-30")

    moom = line(sheet(client), "Moom")

    # June's capital and repayment are inside the new figure.
    assert moom["balance_minor"] == 6100000
    made_of = moom["made_of"]
    assert (made_of["opening_minor"], made_of["paid_for_minor"], made_of["capital_minor"],
            made_of["paid_back_minor"]) == (6100000, 0, 0, 0)
    assert moom["since"] is None


def test_a_companys_cost_on_an_account_in_another_currency_is_left_out_and_said(client, conn, ledger):
    rupee = statement(conn, make_account(
        client, "Sample Rupee Bank 0009", "bank", last_four="0009", currency="INR"))
    row(conn, rupee, "2026-04-20", "SAMPLE PRINTER", 5000000, book="Moom")

    moom = line(sheet(client), "Moom")

    # Rupees are never added to dollars as if they were dollars.
    assert moom["balance_minor"] == MOOM
    assert moom["rows_not_counted"] == 1
    assert moom["note"] == "1 row on an account in another currency is not counted"
    assert line(sheet(client), "Kalesh")["rows_not_counted"] == 0
