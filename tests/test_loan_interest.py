"""Loan interest worked out from the figures the operator supplies.

Driven through the HTTP interface against the temporary database. Loans and
bank accounts are made through the accounts endpoint, figures through the
enter-a-figure endpoint; instalment rows are seeded with SQL as whole cents
(or labelled through the review list) and everything is read back through the
endpoints the operator's screens use. Every figure and name here is invented.
"""

import money

BANK_FEES = "Bank & government fees"

# Whole cents. What is owed on each date, and the instalments between them.
OWED_JUN, OWED_AUG = 91_000_000, 90_250_000          # 910,000.00 then 902,500.00
FELL = OWED_JUN - OWED_AUG                           # 7,500.00
JULY_INSTALMENT, AUG_INSTALMENT = 880_000, 880_001   # 8,800.00 and 8,800.01
PAID = JULY_INSTALMENT + AUG_INSTALMENT              # 17,600.01
INTEREST = PAID - FELL                               # 10,100.01
JULY_INTEREST, AUG_INTEREST = 505_001, 505_000       # the odd cent goes to the first month


# --- seeding and reading ---------------------------------------------------


def make_account(client, name: str, kind: str, **extra) -> int:
    resp = client.post("/api/accounts", json={"name": name, "type": kind, **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def units(cents: int) -> str:
    """Whole cents as the operator types a figure: "910000.00"."""
    return f"{cents // 100}.{cents % 100:02d}"


def enter(client, account_id: int, owed_cents: int, on: str):
    return client.post("/api/anchors", json={
        "account_id": account_id, "amount": units(owed_cents), "date": on,
    })


def statement(conn, account_id: int, on: str = "2026-08-31") -> int:
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, 'sample.csv')",
        (account_id, on),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def row(conn, statement_id: int, description: str, cents: int, day: str, *,
        flow: str = "movement", other_side: int | None = None) -> int:
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type,"
        " other_side_id) VALUES (?, ?, ?, ?, ?, ?)",
        (statement_id, day, description, cents, flow, other_side),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def cents(shown) -> int:
    assert money.is_whole_minor(shown), f"{shown!r} is not a whole number of cents"
    return money.to_minor(shown)


def listed(client, query: str = "") -> list[dict]:
    return client.get(f"/api/transactions?per_page=500{query}").get_json()["transactions"]


def derived(client) -> list[dict]:
    """The derived interest rows, oldest first, as the transaction list shows them."""
    rows = [t for t in listed(client) if t["cat_source"] == "derived"]
    return sorted(rows, key=lambda t: t["date"])


def by_month(client) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in derived(client):
        out[t["date"][:7]] = out.get(t["date"][:7], 0) + cents(t["amount_sgd"])
    return out


def cards(client, month: str) -> dict:
    return client.get(f"/api/dashboard/stat-cards?ref_month={month}").get_json()


def setup(client, conn) -> dict:
    """A home loan, a bank account, the June figure and two instalments."""
    loan = make_account(client, "Sample Home Loan", "loan")
    bank = make_account(client, "Sample Bank 0002", "bank", last_four="0002")
    stmt = statement(conn, bank)
    assert enter(client, loan, OWED_JUN, "2026-06-30").status_code == 200
    row(conn, stmt, "SAMPLE LOAN INSTALMENT", JULY_INSTALMENT, "2026-07-15", other_side=loan)
    row(conn, stmt, "SAMPLE LOAN INSTALMENT", AUG_INSTALMENT, "2026-08-15", other_side=loan)
    return {"loan": loan, "bank": bank, "stmt": stmt}


# --- what is worked out, and said, when a figure follows an earlier one --------


def test_a_first_figure_works_nothing_out(client, conn):
    setup(client, conn)

    assert derived(client) == []
    assert cards(client, "2026-07")["loan_interest"] == 0


def test_a_following_figure_states_what_was_worked_out(client, conn):
    made = setup(client, conn)

    resp = enter(client, made["loan"], OWED_AUG, "2026-08-31")

    assert resp.status_code == 200
    body = resp.get_json()
    worked = body["worked_out"]
    assert (worked["from"], worked["to"]) == ("2026-06-30", "2026-08-31")
    assert worked["instalments"] == 2
    assert worked["paid_minor"] == PAID
    assert worked["fell_minor"] == FELL
    assert worked["interest_minor"] == INTEREST
    assert worked["months"] == [
        {"month": "2026-07", "date": "2026-07-31", "interest_minor": JULY_INTEREST},
        {"month": "2026-08", "date": "2026-08-31", "interest_minor": AUG_INTEREST},
    ]
    assert worked["negative"] is False
    assert body["message"] == (
        "Since your last figure (30 Jun 2026): 2 instalments, S$ 17,600.01 paid. "
        "The loan fell by S$ 7,500.00. "
        "Interest S$ 10,100.01 is counted as spending across July and August."
    )


def test_the_interest_is_household_spending_under_bank_fees_marked_derived(client, conn):
    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")

    rows = derived(client)

    assert [(t["date"], cents(t["amount_sgd"])) for t in rows] == [
        ("2026-07-31", JULY_INTEREST), ("2026-08-31", AUG_INTEREST),
    ]
    for t in rows:
        assert (t["book"], t["type"], t["flow_type"]) == ("Household", BANK_FEES, "expense")
        assert t["account_name"] == "Sample Home Loan"
    # It is in the household's spending for its month, and in the type's total.
    july = cards(client, "2026-07")
    assert cents(july["household"]) == JULY_INTEREST
    assert july["household_rows"] == 1
    types = client.get("/api/dashboard/types?start=2026-07-01&end=2026-08-31").get_json()
    assert [(t["type"], cents(t["total"])) for t in types] == [(BANK_FEES, INTEREST)]


# --- P17 conservation: interest plus principal equals the instalments paid ------


def test_interest_plus_principal_equals_the_instalments_per_month_and_over_the_period(client, conn):
    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")

    interest = by_month(client)
    july, august = cards(client, "2026-07"), cards(client, "2026-08")

    # Per month, to the cent.
    assert cents(july["loan_instalments"]) == JULY_INSTALMENT
    assert cents(august["loan_instalments"]) == AUG_INSTALMENT
    assert cents(july["loan_interest"]) == interest["2026-07"] == JULY_INTEREST
    assert cents(august["loan_interest"]) == interest["2026-08"] == AUG_INTEREST
    assert cents(july["loan_principal"]) == JULY_INSTALMENT - JULY_INTEREST == 374_999
    assert cents(august["loan_principal"]) == AUG_INSTALMENT - AUG_INTEREST == 375_001
    for month in (july, august):
        assert cents(month["loan_interest"]) + cents(month["loan_principal"]) == cents(
            month["loan_instalments"]
        )
    # Over the period: the parts add up to the whole, and the principal is the
    # fall in what is owed.
    assert sum(interest.values()) == INTEREST
    assert cents(july["loan_principal"]) + cents(august["loan_principal"]) == FELL
    assert sum(interest.values()) + FELL == PAID


def test_the_card_shows_the_cash_out_as_spending_plus_principal(client, conn):
    made = setup(client, conn)
    card = statement(conn, make_account(client, "Sample Card 0001", "card", last_four="0001"))
    row(conn, card, "SAMPLE GROCER", 123_456, "2026-08-10", flow="expense")
    enter(client, made["loan"], OWED_AUG, "2026-08-31")

    august = cards(client, "2026-08")

    assert cents(august["household"]) == 123_456 + AUG_INTEREST
    assert cents(august["loan_principal"]) == 375_001
    # Everything that left: the grocer and the whole instalment.
    assert cents(august["cash_out"]) == 123_456 + AUG_INSTALMENT


def test_instalments_since_the_latest_figure_are_repayment_in_full(client, conn):
    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")
    row(conn, made["stmt"], "SAMPLE LOAN INSTALMENT", 880_000, "2026-09-15", other_side=made["loan"])

    september = cards(client, "2026-09")

    assert cents(september["loan_interest"]) == 0
    assert cents(september["loan_principal"]) == 880_000


def test_the_interest_is_spread_evenly_and_the_odd_cents_still_add_up(client, conn):
    loan = make_account(client, "Sample Auto Loan", "loan")
    stmt = statement(conn, make_account(client, "Sample Bank 0002", "bank", last_four="0002"))
    enter(client, loan, 12_000_000, "2026-05-15")
    for day in ("2026-05-20", "2026-06-20", "2026-07-20", "2026-08-20"):
        row(conn, stmt, "SAMPLE AUTO INSTALMENT", 200_000, day, other_side=loan)
    # Paid before the first figure: no part of the period.
    row(conn, stmt, "SAMPLE AUTO INSTALMENT", 200_000, "2026-05-15", other_side=loan)

    # 8,000.00 paid, the loan fell by 7,000.03: interest 999.97 over four months.
    body = enter(client, loan, 12_000_000 - 700_003, "2026-08-31").get_json()

    assert body["worked_out"]["interest_minor"] == 99_997
    assert by_month(client) == {
        "2026-05": 25_000, "2026-06": 24_999, "2026-07": 24_999, "2026-08": 24_999,
    }
    assert sum(by_month(client).values()) == 99_997
    assert body["message"].endswith("across May, June, July and August.")


# --- replay and replacement ---------------------------------------------------


def test_working_it_out_again_changes_nothing(client, conn):
    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")
    before = derived(client)

    again = client.post("/api/loan-interest")
    same_figure = enter(client, made["loan"], OWED_AUG, "2026-08-31")

    assert again.status_code == 200
    assert again.get_json()["changed"] == 0
    assert same_figure.get_json()["created"] is False
    assert derived(client) == before
    assert len(before) == 2


def test_a_new_figure_inside_the_period_replaces_the_derived_rows(client, conn):
    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")

    # A figure for the end of July: the loan had fallen by 3,000.00 by then.
    body = enter(client, made["loan"], OWED_JUN - 300_000, "2026-07-31").get_json()

    # July: 8,800.00 paid less 3,000.00. August: 8,800.01 paid less 4,500.00.
    assert body["worked_out"]["interest_minor"] == 580_000
    assert by_month(client) == {"2026-07": 580_000, "2026-08": 430_001}
    assert len(derived(client)) == 2
    assert sum(by_month(client).values()) + FELL == PAID
    assert "2026-08-31" in body["message"] or "31 Aug 2026" in body["message"]


def test_an_instalment_labelled_afterwards_replaces_the_derived_rows(client, conn):
    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")
    late = row(conn, made["stmt"], "FAST PAYMENT REF 771203 OTHR", 100_000, "2026-08-20",
               flow="review")

    resp = client.post(f"/api/review/{late}/label", json={
        "choice": "loan_repayment", "account_id": made["loan"],
    })

    assert resp.status_code == 200
    assert sum(by_month(client).values()) == INTEREST + 100_000
    assert sum(by_month(client).values()) + FELL == PAID + 100_000
    assert len(derived(client)) == 2


def test_re_running_the_rules_leaves_the_derived_rows_alone(client, conn):
    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")
    before = derived(client)

    assert client.post("/api/rules/recategorize").status_code == 200

    assert derived(client) == before


# --- negative interest is recorded as it comes out, and said -------------------


def test_negative_interest_is_recorded_and_flagged_not_clamped(client, conn):
    loan = make_account(client, "Sample Home Loan", "loan")
    stmt = statement(conn, make_account(client, "Sample Bank 0002", "bank", last_four="0002"))
    enter(client, loan, OWED_JUN, "2026-06-30")
    row(conn, stmt, "SAMPLE LOAN INSTALMENT", 500_000, "2026-07-15", other_side=loan)

    # 5,000.00 paid, but the figure fell by 7,500.00.
    body = enter(client, loan, OWED_AUG, "2026-08-31").get_json()

    assert body["worked_out"]["interest_minor"] == -250_000
    assert body["worked_out"]["negative"] is True
    assert by_month(client) == {"2026-07": -125_000, "2026-08": -125_000}
    assert "S$ -2,500.00" in body["message"]
    assert "fell by more than was paid" in body["message"]


# --- the derived rows disturb no account's balance -----------------------------


def test_the_derived_rows_are_on_no_bank_account_and_move_no_figure(client, conn):
    made = setup(client, conn)
    bank_before = listed(client, f"&account_id={made['bank']}")
    enter(client, made["loan"], OWED_AUG, "2026-08-31")

    assert listed(client, f"&account_id={made['bank']}") == bank_before
    figures = client.get(f"/api/anchors?account_id={made['loan']}").get_json()
    assert [(a["date"], a["amount_minor"]) for a in figures] == [
        ("2026-08-31", -OWED_AUG), ("2026-06-30", -OWED_JUN),
    ]
    # The loan still takes a figure, and is no statement account.
    account = {a["id"]: a for a in client.get("/api/accounts").get_json()}[made["loan"]]
    assert account["takes_a_figure"] is True
    coverage = client.get("/api/statements/coverage").get_json()
    assert made["loan"] not in [a["id"] for a in coverage["accounts"]]


def test_two_figures_for_a_holding_work_out_no_interest(client):
    home = make_account(client, "Sample Home", "holding")
    enter(client, home, 190_000_000, "2026-01-01")

    body = enter(client, home, 195_000_000, "2026-08-31").get_json()

    assert body["worked_out"] is None
    assert body["message"] is None
    assert derived(client) == []
