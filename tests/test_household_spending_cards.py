"""The dashboard's cards: household spending, each company's costs beside it,
and what is waiting for review.

Driven through the HTTP interface against the temporary database. Accounts
are made through the accounts endpoint; rows are seeded with SQL as whole
cents and read back through the endpoints the dashboard uses. Every figure,
merchant and name here is invented.
"""

import pytest

import book_type
import money

MONTH = "2026-08"
EARLIER = "2026-07"

# Whole cents. Positive is money out.
GROCER, CAFE, REBATE, STALL = 123456, 4035, -1999, 505   # the Household book
HOUSEHOLD = GROCER + CAFE + REBATE + STALL                # 125997
ADS, MAILER = 30010, 5025                                 # Moom's, paid by the household
MOOM = ADS + MAILER                                       # 35035
HOSTING = 2015                                            # Kalesh's, paid by the household
CLOUD = 77777                                             # Kalesh's, paid by Kalesh itself
WAITING_OUT, WAITING_OUT_2, WAITING_IN = 4000000, 1200050, -250025
HELD_OUT = WAITING_OUT + WAITING_OUT_2 + WAITING_IN       # 4950025, this month
WAITING_EARLIER = 2500000                                 # still waiting, from July


# --- seeding and reading ---------------------------------------------------


def make_account(client, name: str, kind: str, **extra) -> int:
    resp = client.post("/api/accounts", json={"name": name, "type": kind, **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def statement(conn, account_id: int) -> int:
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename)"
        " VALUES (?, ?, 'sample.csv')",
        (account_id, f"{MONTH}-31"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def row(
    conn,
    statement_id: int,
    description: str,
    cents: int,
    *,
    book: str | None = None,
    type_name: str | None = None,
    flow: str = "expense",
    day: str = f"{MONTH}-10",
) -> int:
    """A row whose amount is stored as whole cents, and nothing else."""
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor,"
        " book, type_id, flow_type)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (statement_id, day, description, cents, book,
         book_type.spending_type_ids(conn)[type_name] if type_name else None, flow),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def cents(shown) -> int:
    """A figure a payload shows, as the whole cents it states."""
    assert money.is_whole_minor(shown), f"{shown!r} is not a whole number of cents"
    return money.to_minor(shown)


def cards(client, query: str = "") -> dict:
    return client.get(f"/api/dashboard/stat-cards?ref_month={MONTH}{query}").get_json()


@pytest.fixture
def month(client, conn) -> dict:
    """One month with rows in all three books, a refund, transfers waiting for
    review, and a company's cost on that company's own account."""
    bank = statement(conn, make_account(client, "Sample Bank 0002", "bank", last_four="0002"))
    card = statement(conn, make_account(client, "Sample Card 0001", "card", last_four="0001"))
    kalesh_bank = statement(conn, make_account(
        client, "Sample Company Bank 0007", "bank", last_four="0007", owner="Kalesh",
    ))

    ids = {
        # The Household book. A row no one has placed in a book is the household's.
        "grocer": row(conn, card, "SAMPLE GROCER", GROCER, book="Household", type_name="Groceries"),
        "cafe": row(conn, card, "SAMPLE CAFE", CAFE, book="Household", type_name="Dining"),
        "rebate": row(conn, card, "CASH REBATE", REBATE, book="Household", type_name="Dining",
                      flow="refund"),
        "stall": row(conn, bank, "CORNER STALL", STALL),
        # The companies' costs, paid from the household's card.
        "ads": row(conn, card, "SMPL ADS", ADS, book="Moom", type_name="Advertising"),
        "mailer": row(conn, card, "SAMPLE MAILER", MAILER, book="Moom"),
        "hosting": row(conn, card, "SAMPLE HOSTING", HOSTING, book="Kalesh",
                       type_name="Software & AI tools"),
        # Kalesh's cost on Kalesh's own account: on no card.
        "cloud": row(conn, kalesh_bank, "SAMPLE CLOUD", CLOUD, book="Kalesh",
                     type_name="Software & AI tools"),
        # Waiting for review: two out, one in, and one left over from July.
        "waiting_out": row(conn, bank, "OUTWARD TELEGRAPHIC TRANSFER REF 550012", WAITING_OUT,
                           flow="review", day=f"{MONTH}-12"),
        "waiting_out_2": row(conn, bank, "PAYNOW TO SAMPLE PAYEE REF 9931", WAITING_OUT_2,
                             flow="review", day=f"{MONTH}-21"),
        "waiting_in": row(conn, bank, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410", WAITING_IN,
                          flow="review", day=f"{MONTH}-03"),
        "waiting_earlier": row(conn, bank, "FAST PAYMENT REF 771203 OTHR", WAITING_EARLIER,
                               flow="review", day=f"{EARLIER}-28"),
    }
    # Not spending, and not waiting: in no figure on any card.
    row(conn, bank, "TRANSFER TO OWN ACCOUNT", 90000, flow="transfer")
    row(conn, bank, "CARD PAYOFF", 80000, flow="payment")
    row(conn, bank, "SAMPLE EMPLOYER PAY", -400000, flow="income")
    row(conn, bank, "CAPITAL INTO SAMPLE COMPANY", 500000, flow="movement")
    return ids


# --- P20 projection: household spending, and what is held out of it ---------------


def test_household_spending_is_the_household_books_spending_and_refunds(client, month):
    shown = cards(client)

    # 1,234.56 + 40.35 - 19.99 + 5.05: no company's row and no waiting transfer.
    assert shown["household"] == 1259.97
    assert cents(shown["household"]) == HOUSEHOLD
    assert shown["household_rows"] == 4


def test_the_headline_states_the_transfers_held_out_of_it(client, month):
    shown = cards(client)

    # This month's waiting transfers: 40,000.00 and 12,000.50 out, 2,500.25 in.
    assert shown["held_out_count"] == 3
    assert shown["held_out_total"] == 49500.25
    assert cents(shown["held_out_total"]) == HELD_OUT
    # The one still waiting from July is held out of July, not of this month.
    earlier = client.get(f"/api/dashboard/stat-cards?ref_month={EARLIER}").get_json()
    assert (earlier["held_out_count"], earlier["held_out_total"]) == (1, 25000.0)
    assert (earlier["household"], earlier["household_rows"]) == (0, 0)


def test_each_companys_costs_paid_from_household_accounts_sit_beside_it(client, month):
    shown = cards(client)

    # 300.10 + 50.25 for Moom; 20.15 for Kalesh. The 777.77 Kalesh paid from
    # its own account is on no card.
    assert (shown["moom"], shown["kalesh"]) == (350.35, 20.15)
    assert (cents(shown["moom"]), cents(shown["kalesh"])) == (MOOM, HOSTING)
    # Beside the headline, never in it.
    assert cents(shown["household"]) == HOUSEHOLD


def test_the_to_review_card_counts_what_is_waiting_and_the_rows_with_no_type(client, month):
    shown = cards(client)

    # Everything waiting, whenever it is dated: this month's three and July's one.
    assert shown["waiting"] == 4
    assert shown["waiting_total"] == 74500.25
    assert cents(shown["waiting_total"]) == HELD_OUT + WAITING_EARLIER
    # Spending rows with no type this month: the corner stall and Moom's mailer.
    assert shown["untyped"] == 2


def test_the_review_list_states_the_total_it_holds_out_of_spending(client, month):
    info = client.get("/api/review").get_json()

    assert (info["waiting"], info["waiting_total"]) == (4, 74500.25)
    # It is the figure the to-review card shows.
    shown = cards(client)
    assert (shown["waiting"], shown["waiting_total"]) == (info["waiting"], info["waiting_total"])


def test_what_is_waiting_is_stated_as_money_out_and_money_in_apart(client, month):
    shown = cards(client)

    # Everything waiting: 40,000.00, 12,000.50 and July's 25,000.00 out; 2,500.25 in.
    assert (shown["waiting_out_count"], shown["waiting_out_total"]) == (3, 77000.5)
    assert (shown["waiting_in_count"], shown["waiting_in_total"]) == (1, 2500.25)
    assert cents(shown["waiting_out_total"]) == WAITING_OUT + WAITING_OUT_2 + WAITING_EARLIER
    assert cents(shown["waiting_in_total"]) == -WAITING_IN
    # The two sides are every waiting row, and the net figure is out less in.
    assert shown["waiting_out_count"] + shown["waiting_in_count"] == shown["waiting"]
    assert cents(shown["waiting_out_total"]) - cents(shown["waiting_in_total"]) == cents(
        shown["waiting_total"])

    # What the month's headline is held short of, the same way.
    assert (shown["held_out_out_count"], shown["held_out_out_total"]) == (2, 52000.5)
    assert (shown["held_out_in_count"], shown["held_out_in_total"]) == (1, 2500.25)
    assert shown["held_out_out_count"] + shown["held_out_in_count"] == shown["held_out_count"]
    assert cents(shown["held_out_out_total"]) - cents(shown["held_out_in_total"]) == cents(
        shown["held_out_total"])

    # The review list's heading states the same two sides as the card.
    info = client.get("/api/review").get_json()
    for key in ("waiting_out_count", "waiting_out_total", "waiting_in_count", "waiting_in_total"):
        assert info[key] == shown[key]


def test_a_month_with_nothing_waiting_states_both_sides_as_nothing(client, month):
    shown = cards(client, "").copy()
    june = client.get("/api/dashboard/stat-cards?ref_month=2026-06").get_json()

    assert (june["held_out_out_count"], june["held_out_out_total"]) == (0, 0)
    assert (june["held_out_in_count"], june["held_out_in_total"]) == (0, 0)
    # The review list is not the month's: it is unchanged.
    assert june["waiting_out_count"] == shown["waiting_out_count"]


def test_a_labelled_transfer_leaves_what_is_held_out_and_joins_its_book(client, conn, month):
    before = cards(client)
    dining = book_type.spending_type_ids(conn)["Dining"]
    advertising = book_type.spending_type_ids(conn)["Advertising"]

    # 12,000.50 was household spending; 40,000.00 was Moom's cost.
    for tx, type_id in ((month["waiting_out_2"], dining), (month["waiting_out"], advertising)):
        resp = client.post(f"/api/review/{tx}/label", json={"choice": "spending", "type_id": type_id})
        assert resp.status_code == 200, resp.get_json()

    after = cards(client)
    assert (after["held_out_count"], after["held_out_total"]) == (1, -2500.25)
    assert (after["waiting"], after["waiting_total"]) == (2, 22499.75)
    assert (after["household"], after["household_rows"]) == (13260.47, 5)
    assert cents(after["household"]) == cents(before["household"]) + WAITING_OUT_2
    # Moom's cost went to Moom's card, not into the headline.
    assert cents(after["moom"]) == cents(before["moom"]) + WAITING_OUT
    assert after["kalesh"] == before["kalesh"]


def test_the_account_filter_narrows_the_month_and_not_the_review_list(client, conn, month):
    kalesh_bank = conn.execute(
        "SELECT id FROM accounts WHERE name = 'Sample Company Bank 0007'"
    ).fetchone()[0]

    shown = cards(client, f"&account_id={kalesh_bank}")

    # Kalesh's own account carries nothing the household paid or is waiting on.
    assert (shown["household"], shown["household_rows"]) == (0, 0)
    assert (shown["moom"], shown["kalesh"]) == (0, 0)
    assert (shown["held_out_count"], shown["held_out_total"]) == (0, 0)
    assert (shown["waiting"], shown["waiting_total"]) == (4, 74500.25)


# --- the charts agree with the headline -------------------------------------------


def test_the_household_view_of_the_charts_adds_up_to_the_headline(client, month):
    shown = cards(client)
    view = f"start={MONTH}-01&end={MONTH}-31&book=Household"

    monthly = client.get(f"/api/dashboard/monthly?{view}").get_json()
    assert monthly == {MONTH: {"Groceries": 1234.56, "Dining": 20.36, "No type": 5.05}}
    assert sum(cents(v) for v in monthly[MONTH].values()) == cents(shown["household"])

    donut = client.get(f"/api/dashboard/types?{view}").get_json()
    assert {d["type"]: (d["total"], d["count"]) for d in donut} == {
        "Groceries": (1234.56, 1), "Dining": (20.36, 2), "No type": (5.05, 1),
    }
    assert sum(cents(d["total"]) for d in donut) == cents(shown["household"])
    assert sum(d["count"] for d in donut) == shown["household_rows"]

    listed = client.get(
        f"/api/transactions?{view}&expense_only=true&per_page=500"
    ).get_json()["transactions"]
    assert sum(cents(t["amount_sgd"]) for t in listed) == cents(shown["household"])
    assert len(listed) == shown["household_rows"]


def test_history_gives_each_cards_figure_month_by_month(client, month):
    """Home's book tiles draw twelve months each: the figures are the cards'
    own, month by month, oldest first, and never one added across books."""
    shown = cards(client, "&history=3")
    assert [h["month"] for h in shown["history"]] == ["2026-06", "2026-07", MONTH]
    last = shown["history"][-1]
    assert (last["household"], last["moom"], last["kalesh"]) == (shown["household"], shown["moom"], shown["kalesh"])
    assert last["loan_principal"] == shown["loan_principal"]
    assert shown["history"][0]["household"] == 0
    assert "history" not in cards(client)
    assert len(cards(client, "&history=99")["history"]) == 24
    assert "history" not in cards(client, "&history=x")


def test_each_card_states_money_out_and_refunds_back_apart(client, month):
    shown = cards(client)

    assert cents(shown["household_out"]) == GROCER + CAFE + STALL
    assert cents(shown["household_back"]) == -REBATE
    assert (shown["household_out_rows"], shown["household_back_rows"]) == (3, 1)
    assert cents(shown["moom_out"]) == MOOM
    assert cents(shown["moom_back"]) == 0
    # The net figure stays as it was.
    assert cents(shown["household"]) == HOUSEHOLD


def test_a_book_with_no_row_and_no_statement_for_the_month_is_marked_missing(client, conn):
    card_id = make_account(client, "Sample Card 0003", "card", last_four="0003")
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename)"
        " VALUES (?, ?, 'sample.csv')",
        (card_id, f"{EARLIER}-31"),
    )
    conn.commit()
    july = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row(conn, july, "SMPL ADS", ADS, book="Moom", type_name="Advertising", day=f"{EARLIER}-10")

    shown = cards(client)

    assert shown["moom_missing"] is True
    assert shown["kalesh_missing"] is False

    # Once August's statement is in, an empty month is a real zero.
    statement(conn, card_id)
    assert cards(client)["moom_missing"] is False
