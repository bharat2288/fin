"""Book and type everywhere: every relabel path writes them, every screen's
endpoint reads them, and the category is gone.

Driven through the HTTP interface against the temporary database. Rows are
seeded with SQL (there is no endpoint that inserts one outside an import) and
read back through the endpoints the operator's screens use. Every figure,
merchant and name here is invented.
"""

import io

import pytest

import app as fin_app
import book_type
import db
import money
import parsers
from flow import ClassifierContext, classify_flow
from parse_dbs import ParsedStatement, ParsedTransaction


# --- seeding and reading ---------------------------------------------------


def type_id(conn, name: str) -> int:
    """A spending type's id from its display name ('Fitness > Golf')."""
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


def rule(conn, pattern: str, service_id: int) -> int:
    conn.execute(
        "INSERT INTO merchant_rules (pattern, service_id, match_type, confidence)"
        " VALUES (?, ?, 'contains', 'confirmed')",
        (pattern, service_id),
    )
    conn.commit()
    db.invalidate_rules_cache()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def row(
    conn,
    statement_id: int,
    description: str,
    amount: float = 25.0,
    *,
    book: str | None = None,
    type_name: str | None = None,
    service_id: int | None = None,
    cat_source: str = "auto",
    flow: str = "expense",
    day: str = "2026-04-10",
) -> int:
    """A row of `amount` dollars, stored as its whole cents."""
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor,"
        " book, type_id, service_id, cat_source, flow_type)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (statement_id, day, description, money.exact_minor(amount), book,
         type_id(conn, type_name) if type_name else None, service_id, cat_source, flow),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def shown(client, tx_id: int) -> dict:
    """One row as the transaction list serves it."""
    payload = client.get("/api/transactions?per_page=500").get_json()
    return next(t for t in payload["transactions"] if t["id"] == tx_id)


def label(client, tx_id: int) -> tuple:
    """(book, type, provenance) of one row, as the transaction list serves it."""
    tx = shown(client, tx_id)
    return (tx["book"], tx["display_type"], tx["cat_source"])


def merchant_shown(client, service_id: int) -> dict:
    return next(s for s in client.get("/api/services").get_json() if s["id"] == service_id)


def rule_shown(client, rule_id: int) -> dict:
    return next(r for r in client.get("/api/rules").get_json() if r["id"] == rule_id)


# --- P4: resolve (three variants) -------------------------------------------


def test_resolve_for_this_row_only_writes_book_and_type_and_leaves_the_merchant(
    client, conn, statement
):
    cafe = merchant(conn, "Sample Cafe", "Household", "Dining")
    tx = row(conn, statement, "SAMPLE CAFE LAPTOP STAND")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx,
        "service_id": cafe,
        "book": "Kalesh",
        "type_id": type_id(conn, "Equipment"),
        "pattern": "SAMPLE CAFE",
        "apply_scope": "transaction",
    })

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["rule_id"] is None
    assert label(client, tx) == ("Kalesh", "Equipment", "manual")
    assert shown(client, tx)["service_id"] == cafe
    kept = merchant_shown(client, cafe)
    assert (kept["book"], kept["display_type"], kept["rule_count"]) == ("Household", "Dining", 0)


def test_resolve_as_a_rule_override_writes_the_rule_and_every_matching_row(
    client, conn, statement
):
    market = merchant(conn, "Sample Market", "Household", "Shopping")
    tx = row(conn, statement, "SAMPLE MARKET WHOLESALE 0042")
    also = row(conn, statement, "SAMPLE MARKET WHOLESALE 0077")
    elsewhere = row(conn, statement, "CORNER STALL")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx,
        "service_id": market,
        "book": "Moom",
        "type_id": type_id(conn, "Stock purchases"),
        "pattern": "SAMPLE MARKET WHOLESALE",
        "apply_scope": "rule",
    })

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["backfilled"] == 2
    made = rule_shown(client, body["rule_id"])
    assert (made["book_override"], made["type_override_id"]) == (
        "Moom", type_id(conn, "Stock purchases"),
    )
    assert (made["book"], made["display_type"]) == ("Moom", "Stock purchases")
    assert label(client, tx) == ("Moom", "Stock purchases", "rule_override")
    assert label(client, also) == ("Moom", "Stock purchases", "rule_override")
    assert shown(client, elsewhere)["type_id"] is None
    # The merchant's own default is not the rule's.
    kept = merchant_shown(client, market)
    assert (kept["book"], kept["display_type"]) == ("Household", "Shopping")


def test_resolve_as_the_merchant_default_writes_the_merchant_and_the_row(
    client, conn, statement
):
    gym = merchant(conn, "Sample Gym", "Household", "Shopping")
    tx = row(conn, statement, "SAMPLE GYM MEMBERSHIP")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx,
        "service_id": gym,
        "book": "Household",
        "type_id": type_id(conn, "Fitness"),
        "pattern": "SAMPLE GYM",
        "apply_scope": "service_default",
    })

    assert resp.status_code == 200, resp.get_json()
    assert label(client, tx) == ("Household", "Fitness", "service_default")
    changed = merchant_shown(client, gym)
    assert (changed["book"], changed["display_type"], changed["rule_count"]) == (
        "Household", "Fitness", 1,
    )
    plain = rule_shown(client, resp.get_json()["rule_id"])
    assert (plain["book_override"], plain["type_override_id"]) == (None, None)


def test_resolve_with_no_label_given_takes_the_merchants(client, conn, statement):
    ads = merchant(conn, "Sample Ads", "Moom", "Advertising")
    tx = row(conn, statement, "SMPL ADS 0042")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx, "service_id": ads, "apply_scope": "transaction",
    })

    assert resp.status_code == 200, resp.get_json()
    assert label(client, tx) == ("Moom", "Advertising", "manual")


@pytest.mark.parametrize("type_name, proposed", [
    ("Advertising", "Moom"),
    ("Stock purchases", "Moom"),
    ("Payment fees", "Moom"),
    ("Dining", "Household"),
    ("Bank & government fees", "Household"),
])
def test_book_for_an_unknown_merchant_is_proposed_by_its_type(
    client, conn, statement, type_name, proposed
):
    tx = row(conn, statement, "NEW SAMPLE MERCHANT")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx,
        "service_name": "New Sample Merchant",
        "type_id": type_id(conn, type_name),
        "apply_scope": "service_default",
    })

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["book"] == proposed
    assert label(client, tx) == (proposed, type_name, "service_default")
    made = merchant_shown(client, resp.get_json()["service_id"])
    assert (made["book"], made["display_type"]) == (proposed, type_name)


def test_software_and_ai_tools_asks_for_the_book_and_writes_nothing_without_it(
    client, conn, statement
):
    tx = row(conn, statement, "NEW SAMPLE HOSTING")
    body = {
        "tx_id": tx,
        "service_name": "New Sample Hosting",
        "type_id": type_id(conn, "Software & AI tools"),
        "apply_scope": "service_default",
    }

    refused = client.post("/api/transactions/resolve", json=body)

    assert refused.status_code == 400
    assert "book" in refused.get_json()["error"]
    assert shown(client, tx)["type_id"] is None
    assert not [s for s in client.get("/api/services").get_json()
                if s["name"] == "New Sample Hosting"]

    said = client.post("/api/transactions/resolve", json={**body, "book": "Kalesh"})
    assert said.status_code == 200, said.get_json()
    assert label(client, tx) == ("Kalesh", "Software & AI tools", "service_default")


def test_the_paying_account_never_decides_the_book(client, conn):
    # A company-owned account: the row is still proposed by its type.
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four)"
        " VALUES ('Sample Kalesh Business 0002', 'Kalesh-Biz-0002', 'bank', '0002')"
    )
    account_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO statements (account_id, statement_date) VALUES (?, '2026-04-01')",
        (account_id,),
    )
    conn.commit()
    business_statement = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    tx = row(conn, business_statement, "NEW SAMPLE DINER")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx,
        "service_name": "New Sample Diner",
        "type_id": type_id(conn, "Dining"),
        "apply_scope": "transaction",
    })

    assert resp.status_code == 200, resp.get_json()
    assert label(client, tx) == ("Household", "Dining", "manual")


@pytest.mark.parametrize("bad", [
    {"book": "Personal"},
    {"book": "moom"},
    {"type_id": 99999},
    {"type_id": "Dining"},
])
def test_resolve_refuses_an_unknown_book_or_type(client, conn, statement, bad):
    cafe = merchant(conn, "Sample Cafe", "Household", "Dining")
    tx = row(conn, statement, "SAMPLE CAFE")
    body = {
        "tx_id": tx, "service_id": cafe, "apply_scope": "service_default",
        "book": "Household", "type_id": type_id(conn, "Groceries"), **bad,
    }

    resp = client.post("/api/transactions/resolve", json=body)

    assert resp.status_code == 400
    assert shown(client, tx)["type_id"] is None
    kept = merchant_shown(client, cafe)
    assert (kept["book"], kept["display_type"]) == ("Household", "Dining")


def test_an_income_kind_is_not_a_type_a_spending_row_can_take(client, conn, statement):
    salary = conn.execute(
        "SELECT id FROM types WHERE kind = 'income' AND name = 'Salary'"
    ).fetchone()[0]
    tx = row(conn, statement, "SAMPLE CAFE")

    resp = client.put(f"/api/transactions/{tx}", json={"type_id": salary})

    assert resp.status_code == 400
    assert shown(client, tx)["type_id"] is None


# --- P4: transaction update ---------------------------------------------------


def test_transaction_update_writes_book_and_type_and_marks_the_row_manual(
    client, conn, statement
):
    cafe = merchant(conn, "Sample Cafe", "Household", "Dining")
    tx = row(conn, statement, "SAMPLE CAFE", book="Household", type_name="Dining",
             service_id=cafe, cat_source="service_default")

    resp = client.put(f"/api/transactions/{tx}", json={
        "book": "Moom", "type_id": type_id(conn, "Office"),
    })

    assert resp.status_code == 200, resp.get_json()
    assert label(client, tx) == ("Moom", "Office", "manual")
    # A row overrides both; the merchant keeps its default.
    kept = merchant_shown(client, cafe)
    assert (kept["book"], kept["display_type"]) == ("Household", "Dining")


def test_transaction_update_can_override_the_book_alone(client, conn, statement):
    tx = row(conn, statement, "SAMPLE CAFE", book="Household", type_name="Dining")

    resp = client.put(f"/api/transactions/{tx}", json={"book": "Kalesh"})

    assert resp.status_code == 200, resp.get_json()
    assert label(client, tx) == ("Kalesh", "Dining", "manual")


@pytest.mark.parametrize("bad", [{"book": "Business"}, {"type_id": 99999}])
def test_transaction_update_refuses_an_unknown_book_or_type(client, conn, statement, bad):
    tx = row(conn, statement, "SAMPLE CAFE", book="Household", type_name="Dining")

    resp = client.put(f"/api/transactions/{tx}", json={"notes": "sample note", **bad})

    assert resp.status_code == 400
    after = shown(client, tx)
    assert (after["book"], after["display_type"], after["notes"]) == ("Household", "Dining", None)


def test_a_note_alone_leaves_the_label_and_its_provenance(client, conn, statement):
    tx = row(conn, statement, "SAMPLE CAFE", book="Household", type_name="Dining",
             cat_source="service_default")

    resp = client.put(f"/api/transactions/{tx}", json={"notes": "sample note"})

    assert resp.status_code == 200
    assert label(client, tx) == ("Household", "Dining", "service_default")


# --- P4: rule create and update ----------------------------------------------


def test_rule_create_can_override_the_type_and_the_book(client, conn):
    market = merchant(conn, "Sample Market", "Household", "Shopping")

    resp = client.post("/api/rules", json={
        "pattern": "SAMPLE MARKET WHOLESALE",
        "service_id": market,
        "type_override_id": type_id(conn, "Stock purchases"),
        "book_override": "Moom",
    })

    assert resp.status_code == 200, resp.get_json()
    made = rule_shown(client, resp.get_json()["id"])
    assert (made["book_override"], made["type_override_id"]) == (
        "Moom", type_id(conn, "Stock purchases"),
    )
    assert (made["book"], made["display_type"]) == ("Moom", "Stock purchases")


def test_a_rule_with_no_override_shows_its_merchants_book_and_type(client, conn):
    golf = merchant(conn, "Sample Golf Club", "Household", "Fitness > Golf")

    resp = client.post("/api/rules", json={"pattern": "SAMPLE GOLF", "service_id": golf})

    assert resp.status_code == 200, resp.get_json()
    made = rule_shown(client, resp.get_json()["id"])
    assert (made["book_override"], made["type_override_id"]) == (None, None)
    assert (made["book"], made["display_type"]) == ("Household", "Fitness > Golf")


@pytest.mark.parametrize("bad", [{"book_override": "Business"}, {"type_override_id": 99999}])
def test_rule_create_and_update_refuse_an_unknown_book_or_type(client, conn, bad):
    market = merchant(conn, "Sample Market", "Household", "Shopping")
    kept = rule(conn, "SAMPLE MARKET", market)

    created = client.post("/api/rules", json={
        "pattern": "SAMPLE MARKET WHOLESALE", "service_id": market, **bad,
    })
    updated = client.put(f"/api/rules/{kept}", json=bad)

    assert created.status_code == 400
    assert updated.status_code == 400
    rules = [r for r in client.get("/api/rules").get_json() if r["service_id"] == market]
    assert [(r["pattern"], r["book_override"], r["type_override_id"]) for r in rules] == [
        ("SAMPLE MARKET", None, None)
    ]


def test_rule_update_rewrites_book_and_type_on_its_rows_and_leaves_manual_ones(
    client, conn, statement
):
    market = merchant(conn, "Sample Market", "Household", "Shopping")
    rule_id = rule(conn, "SAMPLE MARKET", market)
    inherited = row(conn, statement, "SAMPLE MARKET 0042", book="Household",
                    type_name="Shopping", service_id=market, cat_source="service_default")
    by_hand = row(conn, statement, "SAMPLE MARKET 0077", book="Kalesh",
                  type_name="Equipment", service_id=market, cat_source="manual")

    resp = client.put(f"/api/rules/{rule_id}", json={
        "type_override_id": type_id(conn, "Stock purchases"), "book_override": "Moom",
    })

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 1
    assert label(client, inherited) == ("Moom", "Stock purchases", "rule_override")
    assert label(client, by_hand) == ("Kalesh", "Equipment", "manual")

    # Taking the override off again hands the rows back to the merchant.
    resp = client.put(f"/api/rules/{rule_id}", json={
        "type_override_id": None, "book_override": None,
    })
    assert resp.status_code == 200, resp.get_json()
    assert label(client, inherited) == ("Household", "Shopping", "service_default")
    assert label(client, by_hand) == ("Kalesh", "Equipment", "manual")


def test_a_rule_overriding_the_type_alone_keeps_the_merchants_book(client, conn, statement):
    hosting = merchant(conn, "Sample Hosting", "Kalesh", "Software & AI tools")
    rule_id = rule(conn, "SAMPLE HOSTING", hosting)
    tx = row(conn, statement, "SAMPLE HOSTING HARDWARE", service_id=hosting,
             cat_source="service_default")

    resp = client.put(f"/api/rules/{rule_id}", json={
        "type_override_id": type_id(conn, "Equipment"),
    })

    assert resp.status_code == 200, resp.get_json()
    assert label(client, tx) == ("Kalesh", "Equipment", "rule_override")


# --- P4: recategorize-all ------------------------------------------------------


def test_recategorize_all_writes_book_and_type_from_the_rules_and_keeps_manual_rows(
    client, conn, statement
):
    ads = merchant(conn, "Sample Ads", "Moom", "Advertising")
    rule(conn, "SMPL ADS", ads)
    unlabelled = row(conn, statement, "SMPL ADS 0042")
    stale = row(conn, statement, "SMPL ADS 0077", book="Household", type_name="Shopping",
                cat_source="service_default")
    by_hand = row(conn, statement, "SMPL ADS 0099", book="Kalesh", type_name="Advertising",
                  cat_source="manual")

    resp = client.post("/api/rules/recategorize")

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert (body["updated"], body["skipped_manual"]) == (2, 1)
    for tx in (unlabelled, stale):
        assert label(client, tx) == ("Moom", "Advertising", "service_default")
        assert shown(client, tx)["service_id"] == ads
    assert label(client, by_hand) == ("Kalesh", "Advertising", "manual")
    assert shown(client, by_hand)["service_id"] is None

    # Run again: nothing left to change.
    again = client.post("/api/rules/recategorize").get_json()
    assert (again["updated"], again["unchanged"]) == (0, 2)


def test_recategorize_all_applies_a_rules_override(client, conn, statement):
    market = merchant(conn, "Sample Market", "Household", "Shopping")
    rule_id = rule(conn, "SAMPLE MARKET WHOLESALE", market)
    conn.execute(
        "UPDATE merchant_rules SET book_override = 'Moom', type_override_id = ? WHERE id = ?",
        (type_id(conn, "Stock purchases"), rule_id),
    )
    conn.commit()
    db.invalidate_rules_cache()
    tx = row(conn, statement, "SAMPLE MARKET WHOLESALE 0042")

    client.post("/api/rules/recategorize")

    assert label(client, tx) == ("Moom", "Stock purchases", "rule_override")


def test_the_paynow_fallback_gives_a_type_and_the_book_its_type_proposes(
    client, conn, statement
):
    # No merchant rule knows this payee; the fixed PayNow wording list does.
    tx = row(conn, statement, "PayNow Transfer 0042 To: INLAND REVENUE AUTHORITY", 310.0)

    client.post("/api/rules/recategorize")

    assert label(client, tx) == ("Household", "Tax", "fallback")
    assert shown(client, tx)["service_id"] is None


# --- P4: service update and merge ----------------------------------------------


def test_service_update_writes_book_and_type_to_its_inherited_rows_only(
    client, conn, statement
):
    hosting = merchant(conn, "Sample Hosting", "Household", "Subscriptions")
    inherited = row(conn, statement, "SAMPLE HOSTING 1", book="Household",
                    type_name="Subscriptions", service_id=hosting, cat_source="service_default")
    unlabelled = row(conn, statement, "SAMPLE HOSTING 2", service_id=hosting)
    by_rule = row(conn, statement, "SAMPLE HOSTING 3", book="Household", type_name="Equipment",
                  service_id=hosting, cat_source="rule_override")
    by_hand = row(conn, statement, "SAMPLE HOSTING 4", book="Moom", type_name="Office",
                  service_id=hosting, cat_source="manual")

    resp = client.put(f"/api/services/{hosting}", json={
        "book": "Kalesh", "type_id": type_id(conn, "Software & AI tools"),
    })

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 2
    changed = merchant_shown(client, hosting)
    assert (changed["book"], changed["display_type"]) == ("Kalesh", "Software & AI tools")
    assert label(client, inherited) == ("Kalesh", "Software & AI tools", "service_default")
    assert label(client, unlabelled)[:2] == ("Kalesh", "Software & AI tools")
    assert label(client, by_rule) == ("Household", "Equipment", "rule_override")
    assert label(client, by_hand) == ("Moom", "Office", "manual")


def test_service_update_of_the_book_alone_moves_its_rows_to_that_book(
    client, conn, statement
):
    hosting = merchant(conn, "Sample Hosting", "Household", "Software & AI tools")
    tx = row(conn, statement, "SAMPLE HOSTING", book="Household",
             type_name="Software & AI tools", service_id=hosting, cat_source="service_default")

    resp = client.put(f"/api/services/{hosting}", json={"book": "Kalesh"})

    assert resp.status_code == 200, resp.get_json()
    assert label(client, tx) == ("Kalesh", "Software & AI tools", "service_default")


def test_service_update_that_leaves_the_default_as_it_was_relabels_nothing(
    client, conn, statement
):
    # The edit screen sends the whole merchant back on a rename.
    mailer = merchant(conn, "SMPL*MAILER", "Moom", None)
    placed_since = row(conn, statement, "SMPL*MAILER 1", book="Moom",
                       type_name="Software & AI tools", service_id=mailer)

    resp = client.put(f"/api/services/{mailer}", json={
        "name": "Sample Mailer", "book": "Moom", "type_id": None, "notes": "sample note",
    })

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 0
    assert merchant_shown(client, mailer)["name"] == "Sample Mailer"
    assert label(client, placed_since)[:2] == ("Moom", "Software & AI tools")


@pytest.mark.parametrize("bad", [{"book": "Business"}, {"type_id": 99999}])
def test_service_create_and_update_refuse_an_unknown_book_or_type(client, conn, bad):
    cafe = merchant(conn, "Sample Cafe", "Household", "Dining")

    created = client.post("/api/services", json={"name": "Sample Other Cafe", **bad})
    updated = client.put(f"/api/services/{cafe}", json={"name": "Renamed", **bad})

    assert created.status_code == 400
    assert updated.status_code == 400
    names = {s["name"] for s in client.get("/api/services").get_json()}
    assert "Sample Other Cafe" not in names and "Renamed" not in names
    kept = merchant_shown(client, cafe)
    assert (kept["book"], kept["display_type"]) == ("Household", "Dining")


def test_service_create_takes_the_book_its_type_proposes_or_asks(client, conn):
    ads = client.post("/api/services", json={
        "name": "Sample Ads", "type_id": type_id(conn, "Advertising"),
    })
    asked = client.post("/api/services", json={
        "name": "Sample Hosting", "type_id": type_id(conn, "Software & AI tools"),
    })
    said = client.post("/api/services", json={
        "name": "Sample Hosting", "type_id": type_id(conn, "Software & AI tools"),
        "book": "Household",
    })

    assert ads.status_code == 200, ads.get_json()
    assert merchant_shown(client, ads.get_json()["id"])["book"] == "Moom"
    assert asked.status_code == 400
    assert said.status_code == 200, said.get_json()
    assert merchant_shown(client, said.get_json()["id"])["book"] == "Household"


def test_service_merge_writes_the_targets_book_and_type_to_the_rows_it_takes(
    client, conn, statement
):
    source = merchant(conn, "SMPL*HOSTING SG", "Household", "Shopping")
    target = merchant(conn, "Sample Hosting", "Kalesh", "Software & AI tools")
    rule(conn, "SMPL*HOSTING", source)
    inherited = row(conn, statement, "SMPL*HOSTING 1", book="Household", type_name="Shopping",
                    service_id=source, cat_source="service_default")
    by_hand = row(conn, statement, "SMPL*HOSTING 2", book="Moom", type_name="Office",
                  service_id=source, cat_source="manual")
    already = row(conn, statement, "SAMPLE HOSTING 3", book="Kalesh",
                  type_name="Software & AI tools", service_id=target,
                  cat_source="service_default")

    resp = client.post(f"/api/services/{source}/merge", json={"target_id": target})

    assert resp.status_code == 200, resp.get_json()
    merged = resp.get_json()["merged"]
    assert (merged["transactions"], merged["rules"], merged["relabelled"]) == (2, 1, 1)
    assert label(client, inherited) == ("Kalesh", "Software & AI tools", "service_default")
    assert label(client, by_hand) == ("Moom", "Office", "manual")
    assert label(client, already) == ("Kalesh", "Software & AI tools", "service_default")
    for tx in (inherited, by_hand, already):
        assert shown(client, tx)["service_id"] == target


# --- P4: import confirm ---------------------------------------------------------


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
                    ParsedTransaction(
                        date=day, description=description, amount_minor=money.exact_minor(amount)
                    )
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


def confirm(client, preview: dict, **extra):
    return client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [{"account": g["account"], "transactions": g["transactions"]}
                   for g in preview["groups"]],
        **extra,
    })


def by_description(client) -> dict:
    payload = client.get("/api/transactions?per_page=500").get_json()
    return {t["description"]: t for t in payload["transactions"]}


def test_import_preview_proposes_book_and_type_and_confirm_writes_them(
    client, conn, fake_statement
):
    ads = merchant(conn, "Sample Ads", "Moom", "Advertising")
    rule(conn, "SMPL ADS", ads)
    fake_statement([
        ("2026-04-03", "SMPL ADS 0042", 120.00),
        ("2026-04-04", "CORNER STALL", 4.20),
    ])

    preview = upload(client)

    entries = {e["description"]: e for e in preview["groups"][0]["transactions"]}
    known = entries["SMPL ADS 0042"]
    assert (known["book"], known["type_id"], known["type_name"], known["status"]) == (
        "Moom", type_id(conn, "Advertising"), "Advertising", "typed",
    )
    unknown = entries["CORNER STALL"]
    assert (unknown["book"], unknown["type_id"], unknown["status"]) == (None, None, "untyped")
    assert (preview["stats"]["typed"], preview["stats"]["untyped"]) == (1, 1)
    [listed] = [s for s in preview["services"] if s["id"] == ads]
    assert (listed["book"], listed["type_id"], listed["type_name"]) == (
        "Moom", type_id(conn, "Advertising"), "Advertising",
    )

    resp = confirm(client, preview)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["transactions_saved"] == 2
    saved = by_description(client)
    assert (saved["SMPL ADS 0042"]["book"], saved["SMPL ADS 0042"]["display_type"]) == (
        "Moom", "Advertising",
    )
    assert saved["SMPL ADS 0042"]["service_id"] == ads
    assert saved["CORNER STALL"]["type_id"] is None


def test_import_confirm_writes_the_label_the_operator_set_on_a_row(
    client, conn, fake_statement
):
    fake_statement([("2026-04-04", "CORNER STALL", 4.20)])
    preview = upload(client)
    entry = preview["groups"][0]["transactions"][0]
    entry.update(book="Kalesh", type_id=type_id(conn, "Office"), cat_source="manual")

    resp = confirm(client, preview)

    assert resp.status_code == 200, resp.get_json()
    saved = by_description(client)["CORNER STALL"]
    assert (saved["book"], saved["display_type"], saved["cat_source"]) == (
        "Kalesh", "Office", "manual",
    )


def test_import_confirm_makes_a_new_merchant_with_book_and_type(client, conn, fake_statement):
    fake_statement([("2026-04-04", "NEW SAMPLE SUPPLIER", 88.00)])
    preview = upload(client)
    stock = type_id(conn, "Stock purchases")
    preview["groups"][0]["transactions"][0].update(book="Moom", type_id=stock)

    resp = confirm(client, preview, new_services=[{
        "name": "New Sample Supplier", "type_id": stock,
        "description": "NEW SAMPLE SUPPLIER",
    }])

    assert resp.status_code == 200, resp.get_json()
    [made] = [s for s in client.get("/api/services").get_json()
              if s["name"] == "New Sample Supplier"]
    # No book was sent for the merchant: its type proposes it.
    assert (made["book"], made["display_type"], made["rule_count"]) == (
        "Moom", "Stock purchases", 1,
    )
    saved = by_description(client)["NEW SAMPLE SUPPLIER"]
    assert (saved["service_id"], saved["book"], saved["display_type"]) == (
        made["id"], "Moom", "Stock purchases",
    )


@pytest.mark.parametrize("bad", [{"book": "Business"}, {"type_id": 99999}])
def test_import_confirm_refuses_an_unknown_book_or_type_and_writes_nothing(
    client, conn, fake_statement, bad
):
    fake_statement([
        ("2026-04-03", "CORNER STALL", 4.20),
        ("2026-04-04", "SAMPLE CAFE", 6.10),
    ])
    preview = upload(client)
    preview["groups"][0]["transactions"][1].update(bad)

    resp = confirm(client, preview)

    assert resp.status_code == 400
    assert client.get("/api/transactions").get_json()["total"] == 0
    assert client.get("/api/accounts").get_json() == []


def test_import_preview_falls_back_to_the_paynow_wording_for_a_type(
    client, conn, fake_statement
):
    fake_statement([("2026-04-05", "PayNow Transfer 0042 To: INLAND REVENUE AUTHORITY", 310.0)])

    entry = upload(client)["groups"][0]["transactions"][0]

    assert (entry["book"], entry["type_name"], entry["cat_source"], entry["service_id"]) == (
        "Household", "Tax", "fallback", None,
    )


# --- the merchant's review-each-time mark -----------------------------------------


def test_a_merchant_carries_a_review_each_time_mark(client, conn, statement, fake_statement):
    hotel = merchant(conn, "Sample Hotel", "Household", "Travel")
    rule(conn, "SAMPLE HOTEL", hotel)
    plain = merchant(conn, "Sample Cafe", "Household", "Dining")
    rule(conn, "SAMPLE CAFE", plain)
    assert merchant_shown(client, hotel)["review_each_time"] == 0

    resp = client.put(f"/api/services/{hotel}", json={"review_each_time": 1})

    assert resp.status_code == 200, resp.get_json()
    assert merchant_shown(client, hotel)["review_each_time"] == 1
    # The mark changes no label: the merchant keeps its default type.
    assert merchant_shown(client, hotel)["display_type"] == "Travel"

    # Its rows are recognised, take the default, and are flagged each time.
    fake_statement([
        ("2026-04-06", "SAMPLE HOTEL RESTAURANT", 64.00),
        ("2026-04-07", "SAMPLE CAFE", 6.10),
    ])
    preview = upload(client)
    entries = {e["description"]: e for e in preview["groups"][0]["transactions"]}
    marked = entries["SAMPLE HOTEL RESTAURANT"]
    assert (marked["service_id"], marked["type_name"], marked["review_each_time"]) == (
        hotel, "Travel", True,
    )
    assert entries["SAMPLE CAFE"]["review_each_time"] is False
    assert preview["stats"]["review_each_time"] == 1

    confirm(client, preview)
    saved = by_description(client)
    assert saved["SAMPLE HOTEL RESTAURANT"]["review_each_time"] == 1
    assert saved["SAMPLE CAFE"]["review_each_time"] == 0


def test_a_new_merchant_can_be_made_with_the_mark(client, conn):
    resp = client.post("/api/services", json={
        "name": "Sample Marketplace", "book": "Household",
        "type_id": type_id(conn, "Shopping"), "review_each_time": 1,
    })

    assert resp.status_code == 200, resp.get_json()
    assert merchant_shown(client, resp.get_json()["id"])["review_each_time"] == 1


# --- a subscription takes book and type from its merchant ------------------------


@pytest.fixture
def fixed_rate(monkeypatch: pytest.MonkeyPatch):
    # The subscriptions screen asks an outside service for a live rate.
    monkeypatch.setattr(fin_app, "_get_usd_sgd_rate", lambda: 1.35)


def subscription_shown(client, sub_id: int) -> dict:
    return next(s for s in client.get("/api/subscriptions").get_json() if s["id"] == sub_id)


def test_a_subscription_takes_book_and_type_from_its_merchant(client, conn, fixed_rate):
    hosting = merchant(conn, "Sample Hosting", "Kalesh", "Software & AI tools")

    resp = client.post("/api/subscriptions", json={
        "service_id": hosting, "amount": 12.00, "frequency": "monthly",
        # A subscription carries no label of its own: these are not taken.
        "book": "Household", "type_id": type_id(conn, "Dining"),
    })

    assert resp.status_code == 200, resp.get_json()
    sub_id = resp.get_json()["id"]
    sub = subscription_shown(client, sub_id)
    assert (sub["book"], sub["type_id"], sub["display_type"]) == (
        "Kalesh", type_id(conn, "Software & AI tools"), "Software & AI tools",
    )

    # Relabel the merchant and the subscription follows.
    client.put(f"/api/services/{hosting}", json={
        "book": "Moom", "type_id": type_id(conn, "Payment fees"),
    })
    sub = subscription_shown(client, sub_id)
    assert (sub["book"], sub["display_type"]) == ("Moom", "Payment fees")

    # So does a subscription moved to another merchant.
    cafe = merchant(conn, "Sample Cafe", "Household", "Dining")
    client.put(f"/api/subscriptions/{sub_id}", json={"service_id": cafe})
    sub = subscription_shown(client, sub_id)
    assert (sub["book"], sub["display_type"]) == ("Household", "Dining")


def test_a_subscription_matches_only_rows_in_its_merchants_book(
    client, conn, statement, fixed_rate
):
    hosting = merchant(conn, "Sample Hosting", "Kalesh", "Software & AI tools")
    sub_id = client.post("/api/subscriptions", json={
        "service_id": hosting, "amount": 12.00, "frequency": "monthly",
        "match_pattern": "SAMPLE HOSTING",
    }).get_json()["id"]
    from datetime import date, timedelta
    recent = (date.today() - timedelta(days=5)).isoformat()
    older = (date.today() - timedelta(days=9)).isoformat()
    row(conn, statement, "SAMPLE HOSTING HOME PLAN", 9.00, book="Household",
        type_name="Software & AI tools", day=recent)
    same_book = row(conn, statement, "SAMPLE HOSTING TEAM PLAN", 12.00, book="Kalesh",
                    type_name="Software & AI tools", day=older)

    sub = subscription_shown(client, sub_id)

    assert (sub["tx_id"], sub["tx_last_paid"], sub["tx_amount"]) == (same_book, older, 12.00)


# --- every reader reads the book, not a category root -----------------------------


@pytest.fixture
def spending(conn, statement) -> dict:
    """One month of rows across the three books and the flows."""
    return {
        "dining": row(conn, statement, "SAMPLE CAFE", 40.0, book="Household", type_name="Dining"),
        "golf": row(conn, statement, "SAMPLE GOLF CLUB", 60.0, book="Household",
                    type_name="Fitness > Golf"),
        "ads": row(conn, statement, "SMPL ADS", 300.0, book="Moom", type_name="Advertising"),
        "moom_software": row(conn, statement, "SAMPLE MAILER", 50.0, book="Moom",
                             type_name="Software & AI tools"),
        "kalesh_software": row(conn, statement, "SAMPLE HOSTING", 20.0, book="Kalesh",
                               type_name="Software & AI tools"),
        "moom_no_type": row(conn, statement, "SAMPLE MOOM COST", 7.0, book="Moom"),
        "nothing": row(conn, statement, "CORNER STALL", 5.0),
        "refund_no_type": row(conn, statement, "CASH REBATE", -2.0, flow="refund"),
        "transfer": row(conn, statement, "TRANSFER TO OWN ACCOUNT", 900.0, flow="transfer"),
        "payment": row(conn, statement, "CARD PAYOFF", 800.0, flow="payment"),
        "income": row(conn, statement, "SAMPLE EMPLOYER PAY", -4000.0, flow="income"),
    }


def test_stat_cards_split_spending_by_book(client, spending):
    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-04").get_json()

    # A row no one has placed in a book is read as the household's.
    assert cards["household"] == 40.0 + 60.0 + 5.0 - 2.0
    assert cards["moom"] == 300.0 + 50.0 + 7.0
    assert cards["kalesh"] == 20.0
    assert cards["spend"] == cards["household"] + cards["moom"] + cards["kalesh"]
    # Rows with no type: spending and refund rows only.
    assert cards["untyped"] == 3
    assert "personal" not in cards and "uncategorized" not in cards


@pytest.mark.parametrize("book, spend", [
    ("Household", 103.0), ("Moom", 357.0), ("Kalesh", 20.0),
])
def test_the_view_filter_reads_the_book(client, spending, book, spend):
    cards = client.get(f"/api/dashboard/stat-cards?ref_month=2026-04&book={book}").get_json()
    assert cards["spend"] == spend

    listed = client.get(
        f"/api/transactions?book={book}&expense_only=true&per_page=500"
    ).get_json()["transactions"]
    assert listed and {t["book"] for t in listed} == {book}
    assert sum(t["amount_sgd"] for t in listed) == spend

    monthly = client.get(
        f"/api/dashboard/monthly?start=2026-01-01&end=2026-12-31&book={book}"
    ).get_json()
    assert sum(monthly["2026-04"].values()) == spend

    donut = client.get(
        f"/api/dashboard/types?start=2026-01-01&end=2026-12-31&book={book}"
    ).get_json()
    assert sum(d["total"] for d in donut) == spend


def test_one_type_serves_every_book_in_the_charts(client, spending):
    donut = client.get("/api/dashboard/types?start=2026-01-01&end=2026-12-31").get_json()
    totals = {d["type"]: (d["total"], d["count"]) for d in donut}

    # Software bought by Moom and by Kalesh is one type.
    assert totals["Software & AI tools"] == (70.0, 2)
    assert totals["Advertising"] == (300.0, 1)
    # Sub-types roll up to their parent unless asked for.
    assert totals["Fitness"] == (60.0, 1)
    assert totals["No type"] == (10.0, 3)

    split = client.get(
        "/api/dashboard/types?start=2026-01-01&end=2026-12-31&group_parent=false"
    ).get_json()
    assert {d["type"] for d in split} >= {"Golf", "Dining"}

    monthly = client.get("/api/dashboard/monthly?start=2026-01-01&end=2026-12-31").get_json()
    assert monthly["2026-04"]["Software & AI tools"] == 70.0
    assert monthly["2026-04"]["No type"] == 10.0


def test_the_type_filter_on_the_transaction_list(client, spending):
    def listed(query: str) -> set:
        payload = client.get(f"/api/transactions?per_page=500&{query}").get_json()
        return {t["id"] for t in payload["transactions"]}

    assert listed("types=Software+%26+AI+tools") == {
        spending["moom_software"], spending["kalesh_software"],
    }
    # A parent type takes its sub-types with it.
    assert listed("types=Fitness") == {spending["golf"]}
    assert listed("types=Golf,Dining") == {spending["golf"], spending["dining"]}
    assert listed("types=Dining&book=Moom") == set()
    assert listed("search=golf") == {spending["golf"]}


def test_the_list_of_rows_with_no_type_holds_spending_and_refunds_only(client, spending):
    payload = client.get("/api/transactions?per_page=500&types=__untyped__").get_json()

    assert {t["id"] for t in payload["transactions"]} == {
        spending["moom_no_type"], spending["nothing"], spending["refund_no_type"],
    }
    # Transfers, payments and income carry no type and are not on it.
    assert payload["total"] == 3

    both = client.get("/api/transactions?per_page=500&types=__untyped__,Dining").get_json()
    assert {t["id"] for t in both["transactions"]} == {
        spending["moom_no_type"], spending["nothing"], spending["refund_no_type"],
        spending["dining"],
    }


def test_the_transaction_list_serves_book_and_type_and_no_category(client, spending):
    golf = shown(client, spending["golf"])

    assert (golf["book"], golf["type"], golf["parent_type"], golf["display_type"]) == (
        "Household", "Golf", "Fitness", "Fitness > Golf",
    )
    assert golf["type_id"] is not None
    unplaced = shown(client, spending["nothing"])
    assert (unplaced["book"], unplaced["type_id"], unplaced["display_type"]) == (
        "Household", None, "",
    )
    assert not {"category", "parent_category", "display_category", "scope", "is_personal"} & set(golf)

    by_type = client.get("/api/transactions?per_page=500&expense_only=true&sort=type&sort_dir=asc")
    names = [t["type"] for t in by_type.get_json()["transactions"] if t["type"]]
    assert names == sorted(names)


def test_the_merchant_rows_list_serves_book_and_type(client, conn, statement):
    cafe = merchant(conn, "Sample Cafe", "Household", "Dining")
    tx = row(conn, statement, "SAMPLE CAFE", book="Moom", type_name="Office", service_id=cafe)

    [listed] = client.get(f"/api/services/{cafe}/transactions").get_json()

    assert (listed["id"], listed["book"], listed["display_type"]) == (tx, "Moom", "Office")


# --- the type list, and the category gone ------------------------------------------


def test_the_types_master_lists_the_type_list_with_its_descriptions(client):
    types = client.get("/api/types").get_json()

    assert len(types) == 36
    by_name = {t["display_name"]: t for t in types}
    golf = by_name["Fitness > Golf"]
    assert (golf["name"], golf["parent_name"], golf["default_one_off"]) == ("Golf", "Fitness", 0)
    assert golf["parent_id"] == by_name["Fitness"]["id"]
    assert by_name["Equipment"]["default_one_off"] == 1
    assert by_name["Dining"]["covers"].startswith("restaurants, bars, fast food")
    assert by_name["Dining"]["not_for"] == "supermarkets"
    # Each says the book it proposes for a merchant no rule knows; none = ask.
    assert by_name["Advertising"]["proposed_book"] == "Moom"
    assert by_name["Dining"]["proposed_book"] == "Household"
    assert by_name["Software & AI tools"]["proposed_book"] is None
    # Income kinds are their own short list, not spending types.
    assert "Salary" not in by_name
    kinds = client.get("/api/types?kind=income").get_json()
    assert [k["name"] for k in kinds] == ["Salary", "Rental", "Interest", "Gift received", "Other"]


def test_the_books_are_served_from_their_one_declaration(client):
    books = client.get("/api/books").get_json()

    assert [b["name"] for b in books] == ["Household", "Moom", "Kalesh"]
    assert all(b["description"] for b in books)


def test_the_type_list_is_not_added_to_from_the_app(client):
    resp = client.post("/api/types", json={"name": "Hobbies"})

    assert resp.status_code == 405
    assert len(client.get("/api/types").get_json()) == 36


def test_the_category_endpoints_are_gone(client):
    assert client.get("/api/categories").status_code == 404
    assert client.post("/api/categories", json={"name": "Hobbies"}).status_code in (404, 405)
    assert client.get("/api/dashboard/categories").status_code == 404


def test_a_new_database_has_no_category_table_or_column(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "categories" not in tables
    for table in ("transactions", "services", "merchant_rules", "subscriptions"):
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert not {c for c in columns if "category" in c}, table


def test_a_new_databases_seeded_merchants_carry_book_and_type(client):
    merchants = {s["name"]: s for s in client.get("/api/services").get_json()}

    assert (merchants["Fairprice"]["book"], merchants["Fairprice"]["display_type"]) == (
        "Household", "Groceries",
    )
    assert (merchants["Manulife"]["book"], merchants["Manulife"]["display_type"]) == (
        "Household", "Insurance",
    )
    # Seeds whose old category the mapping table leaves without a type.
    assert (merchants["Klaviyo"]["book"], merchants["Klaviyo"]["display_type"]) == ("Moom", "")
    assert (merchants["Voovoo"]["book"], merchants["Voovoo"]["display_type"]) == ("Household", "")


# --- the classifier reads the wording and the flow, never a category --------------


def test_a_refund_is_known_by_its_wording_and_no_category_name_makes_one():
    ctx = ClassifierContext()

    assert classify_flow({"description": "SAMPLE SHOP CASH REBATE", "amount_minor": -300}, ctx) == "refund"
    assert classify_flow({"description": "SAMPLE SHOP REFUND", "amount_minor": -300}, ctx) == "refund"
    # An inflow whose wording says nothing is income, whatever it was filed under.
    assert classify_flow(
        {"description": "OBSCURE CREDIT ADJ", "amount_minor": -300, "category_name": "Refunds"}, ctx
    ) == "income"


def test_resolve_and_recategorize_keep_a_flow_set_by_hand(client, conn, statement):
    cafe = merchant(conn, "Sample Cafe", "Household", "Dining")
    rule(conn, "SAMPLE CAFE", cafe)
    tx = row(conn, statement, "SAMPLE CAFE CREDIT", -9.0, flow="refund")
    conn.execute("UPDATE transactions SET flow_type_manual = 1 WHERE id = ?", (tx,))
    derived = row(conn, statement, "SAMPLE CAFE CASH REBATE", -1.0, flow="income")
    conn.commit()

    client.post("/api/transactions/resolve", json={
        "tx_id": tx, "service_id": cafe, "apply_scope": "transaction",
    })
    client.post("/api/rules/recategorize")

    # Set by hand: kept. Derived: read again from the wording.
    assert shown(client, tx)["flow_type"] == "refund"
    assert shown(client, derived)["flow_type"] == "refund"
    assert label(client, derived) == ("Household", "Dining", "service_default")
