"""Whole minor units everywhere: rows are stored, added and compared as whole
cents, and every screen's endpoint shows the decimal figure those cents make.

Driven through the HTTP interface against the temporary database. Rows are
seeded with SQL as whole cents and read back through the endpoints the
operator's screens use. Every figure, merchant and name here is invented.
"""

import io
from datetime import date, timedelta

import pytest

import app as fin_app
import book_type
import db
import money
import parsers
from parse_dbs import ParsedStatement, ParsedTransaction
from previews import store_preview


# --- seeding and reading ---------------------------------------------------


def type_id(conn, name: str) -> int:
    return book_type.spending_type_ids(conn)[name]


@pytest.fixture
def statement(conn) -> int:
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four)"
        " VALUES ('Sample Card 0001', 'Sample-0001', 'credit_card', '0001')"
    )
    account_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename)"
        " VALUES (?, '2026-04-01', 'sample.csv')",
        (account_id,),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def merchant(conn, name: str, book: str | None = None, type_name: str | None = None) -> int:
    conn.execute(
        "INSERT INTO services (name, book, type_id) VALUES (?, ?, ?)",
        (name, book, type_id(conn, type_name) if type_name else None),
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
    service_id: int | None = None,
    flow: str = "expense",
    day: str = "2026-04-10",
) -> int:
    """A row whose amount is stored as whole cents, and nothing else."""
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor,"
        " book, type_id, service_id, flow_type)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (statement_id, day, description, cents, book,
         type_id(conn, type_name) if type_name else None, service_id, flow),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def cents(shown) -> int:
    """A figure a payload shows, as the whole cents it states."""
    assert money.is_whole_minor(shown), f"{shown!r} is not a whole number of cents"
    return money.to_minor(shown)


def listed(client, query: str = "") -> list[dict]:
    return client.get(f"/api/transactions?per_page=500{query}").get_json()["transactions"]


# One month of rows. Several of these figures do not survive float addition:
# 0.10 + 0.20 is 0.30000000000000004 and 2.20 + 1.10 is 3.3000000000000003.
DINING = (10, 20, 435, -1999)             # the last is a refund
GROCERIES = (29, 110, 123456)
ADVERTISING = (220, 110)
HOUSEHOLD = sum(DINING) + sum(GROCERIES)  # 122061
MOOM = sum(ADVERTISING)                   # 330


@pytest.fixture
def month(conn, statement) -> dict:
    cafe = merchant(conn, "Sample Cafe", "Household", "Dining")
    ids = {"cafe": cafe, "dining": [], "groceries": [], "advertising": []}
    for amount in DINING:
        ids["dining"].append(row(
            conn, statement, "SAMPLE CAFE", amount, book="Household", type_name="Dining",
            service_id=cafe, flow="refund" if amount < 0 else "expense",
        ))
    for amount in GROCERIES:
        ids["groceries"].append(row(
            conn, statement, "SAMPLE GROCER", amount, book="Household", type_name="Groceries",
        ))
    for amount in ADVERTISING:
        ids["advertising"].append(row(
            conn, statement, "SMPL ADS", amount, book="Moom", type_name="Advertising",
        ))
    # Not spending: neither is in any spending figure.
    row(conn, statement, "TRANSFER TO OWN ACCOUNT", 90000, flow="transfer")
    row(conn, statement, "SAMPLE EMPLOYER PAY", -400000, flow="income")
    return ids


# --- P19 projection: every figure shown is derived from the whole cents ----------


def test_stat_cards_show_each_books_cents_and_they_add_up(client, month):
    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-04").get_json()

    assert (cards["household"], cards["moom"], cards["kalesh"]) == (1220.61, 3.3, 0)
    assert cards["spend"] == 1223.91
    assert cents(cards["household"]) + cents(cards["moom"]) + cents(cards["kalesh"]) == (
        HOUSEHOLD + MOOM
    )
    assert cents(cards["spend"]) == HOUSEHOLD + MOOM


def test_stat_card_average_is_rounded_half_even_to_the_cent(client, conn, statement):
    # Two earlier months with rows: 1.00 and 1.05. Their mean is 1.025, half a
    # cent; half-even gives 1.02.
    row(conn, statement, "SAMPLE CAFE", 100, book="Household", type_name="Dining", day="2026-02-10")
    row(conn, statement, "SAMPLE CAFE", 60, book="Household", type_name="Dining", day="2026-03-10")
    row(conn, statement, "SAMPLE CAFE", 45, book="Household", type_name="Dining", day="2026-03-11")
    # And a mean of 1.035 in Moom's book, which half-even takes up to 1.04.
    row(conn, statement, "SMPL ADS", 100, book="Moom", type_name="Advertising", day="2026-02-10")
    row(conn, statement, "SMPL ADS", 107, book="Moom", type_name="Advertising", day="2026-03-10")

    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-04").get_json()

    assert cards["avg_months"] == 2
    assert (cards["avg_household"], cards["avg_moom"]) == (1.02, 1.04)
    # The total's mean is taken from the total's cents: (200 + 212) / 2.
    assert cards["avg_spend"] == 2.06


def test_monthly_chart_and_donut_show_the_cents_of_each_type(client, month):
    monthly = client.get(
        "/api/dashboard/monthly?start=2026-01-01&end=2026-12-31"
    ).get_json()
    assert monthly == {"2026-04": {"Groceries": 1235.95, "Advertising": 3.3, "Dining": -15.34}}
    assert sum(cents(v) for v in monthly["2026-04"].values()) == HOUSEHOLD + MOOM

    donut = client.get("/api/dashboard/types?start=2026-01-01&end=2026-12-31").get_json()
    assert {d["type"]: (d["total"], d["count"]) for d in donut} == {
        "Groceries": (1235.95, 3), "Advertising": (3.3, 2), "Dining": (-15.34, 4),
    }
    assert sum(cents(d["total"]) for d in donut) == HOUSEHOLD + MOOM


def test_transaction_list_shows_each_rows_cents_with_book_and_type(client, month):
    rows = {t["id"]: t for t in listed(client)}

    shown = [rows[i] for i in month["dining"]]
    assert [t["amount_sgd"] for t in shown] == [0.1, 0.2, 4.35, -19.99]
    assert {(t["book"], t["display_type"]) for t in shown} == {("Household", "Dining")}
    assert [rows[i]["amount_sgd"] for i in month["advertising"]] == [2.2, 1.1]
    assert {rows[i]["book"] for i in month["advertising"]} == {"Moom"}

    # The spending rows on the list add up to the stat card, to the cent.
    spending = listed(client, "&expense_only=true")
    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-04").get_json()
    assert sum(cents(t["amount_sgd"]) for t in spending) == cents(cards["spend"])


def test_transaction_list_sorts_by_amount(client, month):
    amounts = [t["amount_sgd"] for t in listed(client, "&sort=amount&sort_dir=asc")]

    assert amounts == sorted(amounts)
    assert (amounts[0], amounts[-1]) == (-4000.0, 1234.56)


def test_a_merchants_rows_show_their_cents(client, month):
    rows = client.get(f"/api/services/{month['cafe']}/transactions").get_json()

    assert sorted(t["amount_sgd"] for t in rows) == [-19.99, 0.1, 0.2, 4.35]
    assert {(t["book"], t["display_type"]) for t in rows} == {("Household", "Dining")}


@pytest.fixture
def fixed_rate(monkeypatch: pytest.MonkeyPatch):
    # The subscriptions screen asks an outside service for a live rate.
    monkeypatch.setattr(fin_app, "_get_usd_sgd_rate", lambda: 1.35)


def subscription(client, service_id: int, **fields) -> int:
    resp = client.post("/api/subscriptions", json={
        "service_id": service_id, "frequency": "monthly", **fields,
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def subscription_shown(client, sub_id: int) -> dict:
    return next(s for s in client.get("/api/subscriptions").get_json() if s["id"] == sub_id)


def test_subscription_figures_come_from_the_rows_cents(client, conn, statement, fixed_rate):
    hosting = merchant(conn, "Sample Hosting", "Kalesh", "Software & AI tools")
    sub_id = subscription(client, hosting, amount=12.50, match_pattern="SAMPLE HOSTING")
    today = date.today()
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    # Two charges this month (a split payment) and one last month.
    row(conn, statement, "SAMPLE HOSTING PLAN", 10, book="Kalesh",
        type_name="Software & AI tools", day=this_month.isoformat())
    latest = row(conn, statement, "SAMPLE HOSTING PLAN", 20, book="Kalesh",
                 type_name="Software & AI tools", day=today.isoformat())
    row(conn, statement, "SAMPLE HOSTING PLAN", 35, book="Kalesh",
        type_name="Software & AI tools", day=last_month.isoformat())

    sub = subscription_shown(client, sub_id)

    assert (sub["book"], sub["display_type"]) == ("Kalesh", "Software & AI tools")
    assert (sub["tx_id"], sub["tx_months_90d"]) == (latest, 2)
    # The latest month's charges: 0.10 + 0.20, shown as 0.3.
    assert sub["tx_amount"] == 0.3
    # The mean of the two months, 0.30 and 0.35, is 0.325: half-even, 0.32.
    assert sub["tx_avg_90d"] == 0.32
    # 0.35 is more than a tenth above 0.30, so the monthly figure is the mean.
    assert (sub["is_variable"], sub["monthly_sgd"]) == (True, 0.32)
    # The billed amount is the operator's own figure and is left as typed.
    assert sub["amount"] == 12.50


def test_a_subscription_whose_months_differ_by_exactly_a_tenth_is_not_variable(
    client, conn, statement, fixed_rate
):
    """The test for 'variable' is a comparison of amounts, made in whole
    cents: a month exactly a tenth above another is steady, and one cent
    beyond that is variable."""
    hosting = merchant(conn, "Sample Hosting", "Kalesh", "Software & AI tools")
    sub_id = subscription(client, hosting, amount=1.00, match_pattern="SAMPLE HOSTING")
    this_month = date.today().replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    row(conn, statement, "SAMPLE HOSTING PLAN", 110, book="Kalesh",
        type_name="Software & AI tools", day=this_month.isoformat())
    row(conn, statement, "SAMPLE HOSTING PLAN", 100, book="Kalesh",
        type_name="Software & AI tools", day=last_month.isoformat())

    sub = subscription_shown(client, sub_id)

    assert (sub["is_variable"], sub["monthly_sgd"]) == (False, 1.0)

    # One cent more and it is variable.
    row(conn, statement, "SAMPLE HOSTING PLAN", 1, book="Kalesh",
        type_name="Software & AI tools", day=this_month.isoformat())
    assert subscription_shown(client, sub_id)["is_variable"] is True


def test_billed_amount_and_rate_are_left_as_they_are(client, conn, fixed_rate):
    hosting = merchant(conn, "Sample Hosting", "Kalesh", "Software & AI tools")
    sub_id = subscription(client, hosting, amount=20.00, currency="USD")

    sub = subscription_shown(client, sub_id)

    assert (sub["amount"], sub["currency"], sub["fx_rate"]) == (20.00, "USD", 1.35)
    assert sub["monthly_sgd"] == 27.0


# --- import: parsers hand over whole cents, and whole cents are stored ------------


@pytest.fixture
def fake_statement():
    """Register a parser that returns the given rows for any .csv upload."""
    def install(rows):
        def parse(_path):
            return [ParsedStatement(
                statement_type="credit_card",
                statement_date="2026-04-01",
                accounts=["Sample Card 0001"],
                filename="sample.csv",
                transactions=[
                    ParsedTransaction(date=day, description=description, amount_minor=amount)
                    for day, description, amount in rows
                ],
            )]
        parsers._PARSERS.insert(0, {
            "name": "Sample Test CSV", "ext": ".csv",
            "detect_fn": lambda _path: True, "parse_fn": parse,
        })

    yield install
    parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Sample Test CSV"]


def upload(client) -> dict:
    resp = client.post(
        "/api/import/upload",
        data={"files": (io.BytesIO(b"sample"), "sample.csv")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def confirm(client, preview: dict):
    return client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [{"account": g["account"], "transactions": g["transactions"]}
                   for g in preview["groups"]],
    })


def stored(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "SELECT description, amount_minor FROM transactions ORDER BY id"
    )]


STATEMENT_ROWS = [
    ("2026-04-03", "CORNER STALL", 30),
    ("2026-04-03", "CORNER STALL", 30),       # a genuine repeat on the same day
    ("2026-04-04", "SAMPLE BOOK SHOP", 4005),
    ("2026-04-05", "CASH REBATE", -1999),
]


def test_import_shows_decimal_amounts_and_stores_whole_cents(client, conn, fake_statement):
    fake_statement(STATEMENT_ROWS)

    preview = upload(client)

    entries = preview["groups"][0]["transactions"]
    assert [e["amount_sgd"] for e in entries] == [0.3, 0.3, 40.05, -19.99]

    resp = confirm(client, preview)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["transactions_saved"] == 4
    assert stored(conn) == [
        ("CORNER STALL", 30), ("CORNER STALL", 30), ("SAMPLE BOOK SHOP", 4005),
        ("CASH REBATE", -1999),
    ]
    assert sorted(t["amount_sgd"] for t in listed(client)) == [-19.99, 0.3, 0.3, 40.05]


def test_importing_the_same_statement_again_adds_nothing(client, conn, fake_statement):
    fake_statement(STATEMENT_ROWS)
    confirm(client, upload(client))
    before = stored(conn)

    resp = confirm(client, upload(client))

    assert resp.status_code == 200, resp.get_json()
    assert (resp.get_json()["transactions_saved"], resp.get_json()["duplicates_skipped"]) == (0, 4)
    assert stored(conn) == before


def test_a_row_is_matched_to_its_stored_cents_whatever_float_the_screen_sends_back(
    client, conn, fake_statement
):
    """The same 30 cents can come back from the screen as 0.3 or as the float
    a sum leaves behind. Both are the row already stored."""
    fake_statement([("2026-04-03", "CORNER STALL", 30)])
    confirm(client, upload(client))
    preview = upload(client)
    preview["groups"][0]["transactions"][0]["amount_sgd"] = 0.1 + 0.2

    resp = confirm(client, preview)

    assert (resp.get_json()["transactions_saved"], resp.get_json()["duplicates_skipped"]) == (0, 1)
    assert stored(conn) == [("CORNER STALL", 30)]


@pytest.mark.parametrize("bad", [12.345, None, "12.30", True])
def test_confirm_refuses_an_amount_that_is_not_whole_cents_and_writes_nothing(
    client, conn, fake_statement, bad
):
    fake_statement(STATEMENT_ROWS)
    preview = upload(client)
    preview["groups"][0]["transactions"][2]["amount_sgd"] = bad
    store_preview(preview)

    resp = confirm(client, preview)

    assert resp.status_code == 400
    assert "amount" in resp.get_json()["error"]
    assert stored(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_confirm_refuses_a_row_with_no_amount(client, conn, fake_statement):
    fake_statement(STATEMENT_ROWS)
    preview = upload(client)
    del preview["groups"][0]["transactions"][0]["amount_sgd"]
    store_preview(preview)

    resp = confirm(client, preview)

    assert resp.status_code == 400
    assert stored(conn) == []


# --- rules: an amount threshold is whole cents ------------------------------------


def rule_shown(client, rule_id: int) -> dict:
    return next(r for r in client.get("/api/rules").get_json() if r["id"] == rule_id)


def test_rule_thresholds_are_shown_as_typed_and_held_as_whole_cents(client, conn):
    big = merchant(conn, "Sample Big Shop", "Household", "Shopping")

    resp = client.post("/api/rules", json={
        "pattern": "SAMPLE SHOP", "service_id": big, "min_amount": 100.10, "max_amount": None,
    })

    assert resp.status_code == 200, resp.get_json()
    rule_id = resp.get_json()["id"]
    shown = rule_shown(client, rule_id)
    assert (shown["min_amount"], shown["max_amount"]) == (100.1, None)
    assert tuple(conn.execute(
        "SELECT min_amount_minor, max_amount_minor FROM merchant_rules WHERE id = ?", (rule_id,)
    ).fetchone()) == (10010, None)

    resp = client.put(f"/api/rules/{rule_id}", json={"min_amount": None, "max_amount": 0.29})

    assert resp.status_code == 200, resp.get_json()
    shown = rule_shown(client, rule_id)
    assert (shown["min_amount"], shown["max_amount"]) == (None, 0.29)
    assert tuple(conn.execute(
        "SELECT min_amount_minor, max_amount_minor FROM merchant_rules WHERE id = ?", (rule_id,)
    ).fetchone()) == (None, 29)


@pytest.mark.parametrize("bad", [10.005, "ten"])
def test_a_rule_threshold_that_is_not_whole_cents_is_refused(client, conn, bad):
    big = merchant(conn, "Sample Big Shop", "Household", "Shopping")
    before = conn.execute("SELECT COUNT(*) FROM merchant_rules").fetchone()[0]

    resp = client.post("/api/rules", json={
        "pattern": "SAMPLE SHOP", "service_id": big, "min_amount": bad,
    })

    assert resp.status_code == 400
    assert conn.execute("SELECT COUNT(*) FROM merchant_rules").fetchone()[0] == before


def test_a_rule_threshold_is_met_at_exactly_its_cents(client, conn, fake_statement):
    """A row at the threshold matches; a row one cent under does not."""
    big = merchant(conn, "Sample Big Shop", "Household", "Shopping")
    client.post("/api/rules", json={
        "pattern": "SAMPLE SHOP", "service_id": big, "min_amount": 100.10,
    })
    fake_statement([
        ("2026-04-03", "SAMPLE SHOP A", 10010),
        ("2026-04-03", "SAMPLE SHOP B", 10009),
    ])

    entries = {e["description"]: e for e in upload(client)["groups"][0]["transactions"]}

    assert entries["SAMPLE SHOP A"]["service_id"] == big
    assert entries["SAMPLE SHOP B"]["service_id"] is None


def test_recategorize_all_reads_the_rows_cents_against_the_threshold(client, conn, statement):
    big = merchant(conn, "Sample Big Shop", "Household", "Shopping")
    client.post("/api/rules", json={
        "pattern": "SAMPLE SHOP", "service_id": big, "max_amount": 0.29,
    })
    at = row(conn, statement, "SAMPLE SHOP A", 29)
    over = row(conn, statement, "SAMPLE SHOP B", 30)

    resp = client.post("/api/rules/recategorize")

    assert resp.status_code == 200, resp.get_json()
    rows = {t["id"]: t for t in listed(client)}
    assert (rows[at]["service_id"], rows[at]["display_type"]) == (big, "Shopping")
    assert rows[over]["service_id"] is None
