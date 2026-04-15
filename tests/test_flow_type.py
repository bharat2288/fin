"""Tests for flow_type transaction model (ADR v2)."""

import db
from flow import ClassifierContext, classify_flow


CTX_OWN_ONLY = ClassifierContext(
    own_aliases=("SURI BHARAT", "MILI KALE", "KALESH INC", "XXXX018277"),
    linked_cc_patterns=("DBSC-%7436", "7436"),
)


def test_flow_type_column_exists(conn):
    """Bullet 1: flow_type and flow_type_manual columns present after init_db."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    assert "flow_type" in cols
    assert "flow_type_manual" in cols


def test_flow_type_manual_defaults_to_zero(conn):
    """Bullet 1: flow_type_manual has default 0."""
    info = {row["name"]: row for row in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    assert info["flow_type_manual"]["dflt_value"] in (0, "0")


# ----- Bullet 2: canonical cases -----

def test_canonical_rent_income():
    facts = {
        "description": "Inward Credit-FAST OTHR Other A C J &/OR A C#",
        "amount_sgd": -10200.00,
        "category_name": "Rental Income",
    }
    assert classify_flow(facts, CTX_OWN_ONLY) == "income"


def test_canonical_self_transfer():
    facts = {
        "description": "TRANSFER OF FUND TRF SURI BHARAT I-BANK XXXX018277-1",
        "amount_sgd": -25000.00,
        "category_name": None,
    }
    assert classify_flow(facts, CTX_OWN_ONLY) == "transfer"


def test_canonical_aws_expense():
    facts = {
        "description": "BUSINESS ADVANCE CARD TRANSACTION BAT AMAZON WEB SERVICES SI SGP 02NOV",
        "amount_sgd": 87.56,
        "category_name": "Online",
    }
    assert classify_flow(facts, CTX_OWN_ONLY) == "expense"


def test_canonical_cash_rebate_refund():
    facts = {
        "description": "BUSINESS ADVANCE CARD TRANSACTION BAT Cash Rebate 15OCT 4096-3620",
        "amount_sgd": -0.88,
        "category_name": "Refunds",
    }
    assert classify_flow(facts, CTX_OWN_ONLY) == "refund"


def test_canonical_cc_payoff_payment():
    facts = {
        "description": "DBSC-4119110062437436 : I-BANK REF: 17725077050087806737",
        "amount_sgd": 3200.00,
        "category_name": None,
    }
    assert classify_flow(facts, CTX_OWN_ONLY) == "payment"


# ----- Bullet 3: precedence -----

def test_payment_beats_transfer():
    """Linked-CC payoff that also contains an own-alias should classify as payment."""
    facts = {
        "description": "DBSC-4119110062437436 SURI BHARAT I-BANK REF: 17741498686319",
        "amount_sgd": 3200.00,
        "category_name": None,
    }
    assert classify_flow(facts, CTX_OWN_ONLY) == "payment"


def test_refund_beats_income():
    """Negative cash rebate is refund, not income."""
    facts = {
        "description": "Some Merchant Cash Rebate",
        "amount_sgd": -5.00,
        "category_name": None,
    }
    assert classify_flow(facts, CTX_OWN_ONLY) == "refund"


def test_transfer_beats_income_for_own_alias_inflow():
    """Inflow from own counterparty is transfer, not income."""
    facts = {
        "description": "Inward PayNow OTHER SURI BHARAT SGD 4990",
        "amount_sgd": -4990.00,
        "category_name": None,
    }
    assert classify_flow(facts, CTX_OWN_ONLY) == "transfer"


# ----- Bullet 4: backfill script -----

def test_backfill_populates_all_rows(conn, tmp_path):
    """All NULL flow_type rows get populated by the classifier."""
    from backfill_flow_type import backfill
    # Seed an account + statement
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four, status) "
        "VALUES ('Test Bank', 'Test-Bank', 'bank', '9999', 'active')"
    )
    acc = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, '2026-04-01', 't')",
        (acc,),
    )
    stmt = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # 5 rows covering each flow_type. Legacy is_payment/is_transfer cols
    # are dropped after grep gate (ADR v2 bullet 10) so we no longer seed them.
    seed = [
        "Inward Credit A C J rent",                         # income
        "TRANSFER OF FUND TRF SURI BHARAT I-BANK",          # transfer
        "AMAZON WEB SERVICES",                              # expense
        "BAT Cash Rebate Something",                        # refund
        "DBSC-5420891100884777 : I-BANK REF",               # payment (via linked CC)
    ]
    amounts = [-10200, -500, 87.56, -0.88, 1500]
    # Add the CC account so build_context picks up last_four 4777 as linked_cc
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four, status) "
        "VALUES ('Womans Card', 'Test-4777', 'credit_card', '4777', 'active')"
    )
    for desc, amt in zip(seed, amounts):
        conn.execute(
            "INSERT INTO transactions (statement_id, date, description, amount_sgd) "
            "VALUES (?, '2026-04-01', ?, ?)",
            (stmt, desc, amt),
        )
    conn.commit()

    review_csv = tmp_path / "review.csv"
    result = backfill(conn, review_csv_path=review_csv)

    # All rows written, no NULLs left
    assert result["rows_written"] == 5
    nulls = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE flow_type IS NULL"
    ).fetchone()[0]
    assert nulls == 0

    # Each flow type got its row
    by = result["by_flow_type"]
    assert by == {"income": 1, "transfer": 1, "expense": 1, "refund": 1, "payment": 1}


def _seed_one_tx(conn, description="x", amount=1.0):
    conn.execute("INSERT INTO accounts (name, short_name, type, last_four, status) "
                 "VALUES ('A', 'A', 'bank', '0000', 'active')")
    acc = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO statements (account_id, statement_date, filename) "
                 "VALUES (?, '2025-04-01', 't')", (acc,))
    stmt = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO services (name) VALUES ('Svc')")
    svc = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    cat = conn.execute("SELECT id FROM categories WHERE name = 'Shopping'").fetchone()[0]
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_sgd, flow_type) "
        "VALUES (?, '2025-04-15', ?, ?, 'expense')",
        (stmt, description, amount),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0], svc, cat


def test_resolve_accepts_flow_type_override_and_sets_manual_flag(client, conn):
    """Bullet 9a: POST /api/transactions/resolve with flow_type sets flow_type_manual=1."""
    tx_id, svc_id, cat_id = _seed_one_tx(conn)
    resp = client.post(
        "/api/transactions/resolve",
        json={
            "tx_id": tx_id,
            "service_id": svc_id,
            "category_id": cat_id,
            "apply_scope": "transaction",
            "flow_type": "transfer",
        },
    )
    assert resp.status_code == 200, resp.get_json()
    row = conn.execute(
        "SELECT flow_type, flow_type_manual FROM transactions WHERE id = ?", (tx_id,)
    ).fetchone()
    assert row["flow_type"] == "transfer"
    assert row["flow_type_manual"] == 1


def test_recategorize_preserves_flow_type_manual_rows(client, conn):
    """Bullet 9b: /api/rules/recategorize does not touch flow_type on manual rows."""
    tx_id, _, _ = _seed_one_tx(conn)
    # Manually override flow_type
    conn.execute(
        "UPDATE transactions SET flow_type = 'transfer', flow_type_manual = 1, cat_source = 'manual' WHERE id = ?",
        (tx_id,),
    )
    conn.commit()

    resp = client.post("/api/rules/recategorize")
    assert resp.status_code == 200

    row = conn.execute(
        "SELECT flow_type, flow_type_manual FROM transactions WHERE id = ?", (tx_id,)
    ).fetchone()
    assert row["flow_type"] == "transfer"  # preserved
    assert row["flow_type_manual"] == 1


def test_refund_reduces_monthly_spend_in_same_month(client, conn):
    """Bullet 8: $100 expense + $-30 refund in same month → spend total = $70."""
    conn.execute("INSERT INTO accounts (name, short_name, type, last_four, status) "
                 "VALUES ('A', 'A', 'bank', '0000', 'active')")
    acc = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO statements (account_id, statement_date, filename) "
                 "VALUES (?, '2025-04-01', 't')", (acc,))
    stmt = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for desc, amt, ft in [("purchase", 100.0, "expense"), ("cash rebate", -30.0, "refund")]:
        conn.execute(
            "INSERT INTO transactions (statement_id, date, description, amount_sgd, flow_type) "
            "VALUES (?, '2025-04-15', ?, ?, ?)",
            (stmt, desc, amt, ft),
        )
    conn.commit()
    resp = client.get("/api/dashboard/stat-cards?ref_month=2025-04")
    assert resp.get_json()["spend"] == 70.0


def test_dashboard_spend_uses_flow_type_and_preserves_exclude(client, conn):
    """Bullet 7: Dashboard spend totals come from flow_type IN ('expense','refund'),
    and exclude_from_expense_views service filter still applies.
    """
    # Seed: 1 expense, 1 income, 1 transfer, 1 expense excluded via service.
    conn.execute("INSERT INTO accounts (name, short_name, type, last_four, status) "
                 "VALUES ('A', 'A', 'bank', '0000', 'active')")
    acc = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO statements (account_id, statement_date, filename) "
                 "VALUES (?, '2025-04-01', 't')", (acc,))
    stmt = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute("INSERT INTO services (name, category_id, exclude_from_expense_views) "
                 "VALUES ('Excluded Loan', NULL, 1)")
    excluded_svc = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    rows = [
        ("Groceries buy", 100.0, "expense", None),
        ("Rent received", -1000.0, "income", None),
        ("Self paynow", -50.0, "transfer", None),
        ("Home loan EMI", 9446.52, "expense", excluded_svc),  # excluded from totals
    ]
    for desc, amt, ft, svc in rows:
        conn.execute(
            "INSERT INTO transactions (statement_id, date, description, amount_sgd, flow_type, service_id) "
            "VALUES (?, '2025-04-15', ?, ?, ?, ?)",
            (stmt, desc, amt, ft, svc),
        )
    conn.commit()

    resp = client.get("/api/dashboard/stat-cards?ref_month=2025-04")
    assert resp.status_code == 200
    data = resp.get_json()
    # Spend should be 100.0 only (income excluded, transfer excluded, loan-svc excluded)
    assert data["spend"] == 100.0


def test_import_preview_kept_default_for_income_skipped_default_for_transfer(client, conn):
    """Bullet 6: Preview entry _skip is driven by flow_type, not is_transfer.

    Rent received (income) must NOT be skipped; self-transfer must be skipped.
    This is the slice that closes the motivating failure mode.
    """
    import io, json, parsers

    # Build a fake CSV parser that returns two txns: one rent, one self-transfer.
    # We register it as a fallback so the test file "fake.csv" hits it.
    from parse_dbs import ParsedStatement, ParsedTransaction
    from parsers import register

    def fake_detect(fp): return True
    def fake_parse(fp):
        return [ParsedStatement(
            statement_type="bank",
            statement_date="2025-04-01",
            accounts=["Test Bank Account"],
            filename="fake.csv",
            transactions=[
                ParsedTransaction(
                    date="2025-04-09",
                    description="Inward Credit-FAST OTHR Other A C J &/OR A C#",
                    amount_sgd=-10200.0,
                    is_transfer=True,  # legacy flag (incorrect)
                ),
                ParsedTransaction(
                    date="2025-04-10",
                    description="TRANSFER OF FUND TRF SURI BHARAT I-BANK XXXX018277-1",
                    amount_sgd=-500.0,
                    is_transfer=True,
                ),
            ],
        )]
    # Register before the real DBS CSV fallback
    parsers._PARSERS.insert(0, {"name": "Fake Test CSV", "ext": ".csv", "detect_fn": fake_detect, "parse_fn": fake_parse})
    try:
        data = {"files": (io.BytesIO(b"dummy"), "fake.csv")}
        resp = client.post("/api/import/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        body = resp.get_json()
        entries = body["groups"][0]["transactions"]
        # Sort by date for deterministic assertions
        entries.sort(key=lambda e: e["date"])
        rent, self_xfer = entries

        assert rent["flow_type"] == "income"
        assert rent["_skip"] is False       # rent must NOT be default-skipped
        assert self_xfer["flow_type"] == "transfer"
        assert self_xfer["_skip"] is True   # self-transfer must be default-skipped
    finally:
        # Clean up fake parser
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Fake Test CSV"]


def test_parser_post_parse_classification_sets_flow_type(conn):
    """Bullet 5: After parsing, ParsedTransaction has flow_type set via classify_flow."""
    from parse_dbs import ParsedTransaction
    from flow import build_context, classify_flow

    # Synthetic parsed txns covering all 5 flow types
    txns = [
        ParsedTransaction(date="2025-04-09", description="Inward Credit-FAST A C J tenant rent", amount_sgd=-10200, is_transfer=True),
        ParsedTransaction(date="2025-04-10", description="TRANSFER OF FUND TRF SURI BHARAT I-BANK", amount_sgd=-1000, is_transfer=True),
        ParsedTransaction(date="2025-04-11", description="BUSINESS ADVANCE CARD TRANSACTION BAT AMAZON WEB SERVICES", amount_sgd=87.56),
        ParsedTransaction(date="2025-04-12", description="BAT Cash Rebate", amount_sgd=-0.88, is_payment=True),
    ]

    ctx = build_context(conn)
    for t in txns:
        t.flow_type = classify_flow(
            {"description": t.description, "amount_sgd": t.amount_sgd, "category_name": None},
            ctx,
        )

    assert txns[0].flow_type == "income"
    assert txns[1].flow_type == "transfer"
    assert txns[2].flow_type == "expense"
    assert txns[3].flow_type == "refund"


def test_backfill_skips_already_populated_rows(conn):
    """Rows that already have flow_type are left alone."""
    from backfill_flow_type import backfill
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four, status) "
        "VALUES ('X', 'X', 'bank', '0000', 'active')"
    )
    acc = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, '2026-04-01', 't')",
        (acc,),
    )
    stmt = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_sgd, flow_type) "
        "VALUES (?, '2026-04-01', 'x', 1.0, 'expense')",
        (stmt,),
    )
    conn.commit()

    result = backfill(conn)
    assert result["rows_scanned"] == 0


def test_transfer_does_not_swallow_refund_with_own_alias_false_positive():
    """Cash Rebate wording beats a false own-alias hit (e.g., a random string containing XXXX)."""
    facts = {
        "description": "Cash Rebate via SOMEONE",
        "amount_sgd": -1.00,
        "category_name": "Refunds",
    }
    assert classify_flow(facts, CTX_OWN_ONLY) == "refund"
