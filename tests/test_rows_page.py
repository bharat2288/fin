"""Ticket 19: the Rows page, every row in one list (fin-redesign spec Part 2,
the Rows page bullet; design doc case 8; mockup approved 2026-10-26).

The server side, through the public routes: `/api/transactions?every=true`
lists every row of the book whatever its flow, hidden merchants included,
each with its S$ value worked out on the one currency path (RUL-022); and
`/api/rows/summary` says what the same filters hold: the rows of each flow,
money in and out in S$ (never a spending figure), the rows with no rate, and
the months and parties there are to pick.

Every name and figure is invented.
"""

import pytest

# Aug 2026 unless said. Amounts are minor units: positive out, negative in.
SGD_CARD, SGD_BANK, RUPEE_BANK, LOAN = "Sample Card 0001", "Sample Bank 0002", "Sample Rupee Bank 0009", "Sample Car Loan"


def _account(conn, name, kind, currency="SGD", party_id=None):
    return conn.execute(
        "INSERT INTO accounts (name, short_name, type, currency, party_id) VALUES (?, ?, ?, ?, ?)",
        (name, name, kind, currency, party_id)).lastrowid


def _statement(conn, account_id, month):
    return conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, ?)",
        (account_id, f"{month}-01", f"{month}.csv")).lastrowid


@pytest.fixture
def book(conn):
    """Rows of every flow on four accounts, a hidden merchant, a party linked
    to a loan, a rupee row with a rate and one without."""
    ids = {}
    svc = lambda name, hidden=0: conn.execute(
        "INSERT INTO services (name, exclude_from_expense_views) VALUES (?, ?)", (name, hidden)).lastrowid
    grocer, stream, lender, employer = svc("Sample Grocer"), svc("Sample Stream", 1), svc("Sample Lender"), svc("Sample Employer")
    card = _account(conn, SGD_CARD, "card")
    bank = _account(conn, SGD_BANK, "bank")
    rupee = _account(conn, RUPEE_BANK, "bank", "INR")
    loan = _account(conn, LOAN, "loan", party_id=lender)
    st = {(a, m): _statement(conn, a, m) for a in (card, bank, rupee, loan) for m in ("2026-08", "2026-09")}

    def row(key, account, day, words, minor, flow, book=None, service=None, other=None):
        ids[key] = conn.execute(
            "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type, book, "
            "service_id, other_side_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (st[(account, day[:7])], day, words, minor, flow, book, service, other)).lastrowid

    row("grocer", card, "2026-08-05", "SAMPLE GROCER", 5000, "expense", "Household", grocer)
    row("stream", card, "2026-08-06", "SAMPLE STREAM", 1200, "expense", "Household", stream)
    row("moom", card, "2026-08-07", "SAMPLE MOOM SUPPLY", 4000, "expense", "Moom")
    row("refund", card, "2026-08-08", "SAMPLE GROCER REFUND", -2000, "refund", "Household", grocer)
    row("pay_out", bank, "2026-08-20", "PAYMENT TO SAMPLE CARD", 50000, "payment", other=card)
    row("pay_in", card, "2026-08-20", "PAYMENT RECEIVED", -50000, "payment", other=bank)
    row("instalment", bank, "2026-08-15", "SAMPLE LOAN INSTALMENT", 100000, "movement", other=loan)
    row("on_loan", loan, "2026-08-15", "INSTALMENT RECEIVED", -100000, "movement")
    row("salary", bank, "2026-08-31", "SAMPLE SALARY", -200000, "income", service=employer)
    row("inward", rupee, "2026-08-10", "INWARD REMITTANCE", -1000000, "review")
    row("rupee_spend", rupee, "2026-09-03", "SAMPLE BOOKS MUMBAI", 200000, "expense", "Household")
    ids.update(card=card, bank=bank, rupee=rupee, loan=loan, lender=lender, grocer_party=grocer)
    # S$0.0150 a rupee from 7 Aug; September has no rate.
    conn.execute("INSERT INTO rates (pair, date, rate, source, fetched_at) "
                 "VALUES ('INR/SGD', '2026-08-07', '0.0150', 'entered', '2026-10-26T00:00:00Z')")
    conn.commit()
    return ids


def every(client, **filters):
    q = "&".join(f"{k}={v}" for k, v in {"every": "true", "per_page": 200, **filters}.items())
    resp = client.get(f"/api/transactions?{q}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def summary(client, **filters):
    q = "&".join(f"{k}={v}" for k, v in filters.items())
    resp = client.get(f"/api/rows/summary?{q}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def ids_of(listing):
    return {r["id"] for r in listing["transactions"]}


ROW_KEYS = ("grocer", "stream", "moom", "refund", "pay_out", "pay_in", "instalment", "on_loan", "salary",
            "inward", "rupee_spend")


def test_every_row_of_the_book_is_listed_whatever_its_flow(client, conn, book):
    listing = every(client)

    held = {r[0] for r in conn.execute("SELECT id FROM transactions")}
    assert ids_of(listing) == held == {book[k] for k in ROW_KEYS}
    assert listing["total"] == len(held)


def test_a_hidden_merchants_row_is_listed_and_says_it_is_hidden(client, book):
    rows = {r["id"]: r for r in every(client, month="2026-08")["transactions"]}

    assert rows[book["stream"]]["hidden"] is True
    assert rows[book["grocer"]]["hidden"] is False


def test_each_row_carries_its_own_amount_and_its_sgd_value_or_none(client, book):
    rows = {r["id"]: r for r in every(client)["transactions"]}

    # A rupee row keeps its rupees; its S$ value is at the rate of its day.
    assert (rows[book["inward"]]["amount_minor"], rows[book["inward"]]["currency"]) == (-1000000, "INR")
    assert rows[book["inward"]]["sgd_minor"] == -15000
    # September has no rate: no value, never guessed.
    assert rows[book["rupee_spend"]]["sgd_minor"] is None
    # An S$ row is its own amount.
    assert rows[book["pay_out"]]["sgd_minor"] == 50000


def test_a_flow_lists_the_rows_of_that_flow_only(client, book):
    assert ids_of(every(client, flow="review")) == {book["inward"]}
    assert ids_of(every(client, flow="movement")) == {book["instalment"], book["on_loan"]}


def test_a_book_leaves_out_the_flows_that_carry_no_book(client, book):
    assert ids_of(every(client, book="Moom")) == {book["moom"]}
    assert ids_of(every(client, book="Household", month="2026-08")) == {book["grocer"], book["stream"], book["refund"]}


def test_a_party_picks_its_merchants_rows_and_the_rows_on_or_naming_its_accounts(client, book):
    # The lender is the loan's party: the instalment names the loan, and the
    # loan's own row is on it.
    assert ids_of(every(client, party_id=book["lender"])) == {book["instalment"], book["on_loan"]}
    assert ids_of(every(client, party_id=book["grocer_party"])) == {book["grocer"], book["refund"]}


def test_the_account_and_the_month_narrow_the_list(client, book):
    assert ids_of(every(client, account_id=book["bank"])) == {book["pay_out"], book["instalment"], book["salary"]}
    assert ids_of(every(client, month="2026-09")) == {book["rupee_spend"]}


def test_the_summary_counts_each_flow_under_the_other_filters(client, book):
    got = summary(client, month="2026-08", account_id=book["card"], flow="expense")

    # The flow chips count what each flow would list with the other filters as
    # they are; the figures are for the flow picked.
    assert got["flows"] == {"expense": 3, "refund": 1, "payment": 1}
    assert got["rows"] == 3


def test_the_summary_says_money_in_and_out_in_sgd_and_the_rows_with_no_rate(client, book):
    aug = summary(client, month="2026-08")
    sep = summary(client, month="2026-09")

    # In: refund 20 + card payment 500 + loan 1,000 + salary 2,000 + ₹10,000 at 0.0150 (150).
    assert aug["in_sgd_minor"] == 2000 + 50000 + 100000 + 200000 + 15000
    # Out: 50 + 12 + 40 + 500 + 1,000.
    assert aug["out_sgd_minor"] == 5000 + 1200 + 4000 + 50000 + 100000
    assert (aug["rows"], aug["unvalued"], aug["waiting"]) == (10, 0, 1)
    assert (sep["rows"], sep["unvalued"], sep["out_sgd_minor"]) == (1, 1, 0)


def test_the_summary_names_the_months_and_the_parties_there_are_to_pick(client, book):
    got = summary(client, month="2026-08")

    assert got["months"] == ["2026-09", "2026-08"]
    assert got["latest_month"] == "2026-09"
    names = [p["name"] for p in got["parties"]]
    assert names == sorted(names, key=str.lower)
    assert {"Sample Grocer", "Sample Stream", "Sample Lender", "Sample Employer"} == set(names)


def test_an_unknown_flow_is_refused(client, book):
    assert client.get("/api/rows/summary?flow=spending").status_code == 400


def test_the_waiting_count_is_the_queues_whatever_flow_is_picked(client, book):
    # The rows waiting for a label are the Queue's (_waiting_rows), under the
    # filters other than the flow: picking Spending does not hide them.
    assert summary(client, month="2026-08", flow="expense")["waiting"] == 1
    assert summary(client, month="2026-08", account_id=book["card"])["waiting"] == 0
