import db


def _category_id(conn, name: str) -> int:
    return conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()[0]


def _statement_id(conn) -> int:
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four) VALUES ('Test Card 1111', 'Test-1111', 'credit_card', '1111')"
    )
    account_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, '2026-04-01', 'test.csv')",
        (account_id,),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_categorize_transaction_uses_service_default_provenance(conn):
    dining_id = _category_id(conn, "Dining")
    conn.execute(
        "INSERT INTO services (name, category_id) VALUES (?, ?)",
        ("Mixed Merchant", dining_id),
    )
    service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO merchant_rules (pattern, service_id, match_type, confidence) VALUES (?, ?, 'contains', 'confirmed')",
        ("MIXED MERCHANT", service_id),
    )
    conn.commit()
    db.invalidate_rules_cache()

    category_id, matched_service_id, cat_source = db.categorize_transaction(
        "Mixed Merchant Orchard",
        conn,
        amount=12.0,
    )

    assert category_id == dining_id
    assert matched_service_id == service_id
    assert cat_source == "service_default"


def test_categorize_transaction_uses_rule_override_provenance(conn):
    dining_id = _category_id(conn, "Dining")
    shopping_id = _category_id(conn, "Shopping")
    conn.execute(
        "INSERT INTO services (name, category_id) VALUES (?, ?)",
        ("Mixed Override Merchant", dining_id),
    )
    service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        INSERT INTO merchant_rules
            (pattern, service_id, category_override_id, match_type, confidence)
        VALUES (?, ?, ?, 'contains', 'confirmed')
        """,
        ("MIXED OVERRIDE", service_id, shopping_id),
    )
    conn.commit()
    db.invalidate_rules_cache()

    category_id, matched_service_id, cat_source = db.categorize_transaction(
        "Mixed Override Apparel",
        conn,
        amount=80.0,
    )

    assert category_id == shopping_id
    assert matched_service_id == service_id
    assert cat_source == "rule_override"


def test_service_category_update_only_recategorizes_service_default_rows(client):
    conn = db.get_connection()
    try:
        dining_id = _category_id(conn, "Dining")
        shopping_id = _category_id(conn, "Shopping")
        groceries_id = _category_id(conn, "Groceries")
        statement_id = _statement_id(conn)

        conn.execute(
            "INSERT INTO services (name, category_id) VALUES (?, ?)",
            ("Scoped Merchant", dining_id),
        )
        service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        rows = [
            ("2026-04-01", "Scoped Merchant Lunch", dining_id, "service_default"),
            ("2026-04-02", "Scoped Merchant Shirt", shopping_id, "rule_override"),
            ("2026-04-03", "Scoped Merchant Gift", shopping_id, "manual"),
        ]
        for tx_date, description, category_id, cat_source in rows:
            conn.execute(
                """
                INSERT INTO transactions
                    (statement_id, date, description, amount_sgd, category_id, service_id, cat_source)
                VALUES (?, ?, ?, 25.0, ?, ?, ?)
                """,
                (statement_id, tx_date, description, category_id, service_id, cat_source),
            )
        conn.commit()
    finally:
        conn.close()

    resp = client.put(f"/api/services/{service_id}", json={"category_id": groceries_id})
    assert resp.status_code == 200
    assert resp.get_json()["recategorized"] == 1

    conn = db.get_connection()
    try:
        rows = conn.execute(
            """
            SELECT description, category_id, cat_source
            FROM transactions
            WHERE service_id = ?
            ORDER BY date
            """,
            (service_id,),
        ).fetchall()
    finally:
        conn.close()

    by_desc = {row["description"]: (row["category_id"], row["cat_source"]) for row in rows}
    assert by_desc["Scoped Merchant Lunch"] == (groceries_id, "service_default")
    assert by_desc["Scoped Merchant Shirt"] == (shopping_id, "rule_override")
    assert by_desc["Scoped Merchant Gift"] == (shopping_id, "manual")


def test_resolve_transaction_scope_does_not_mutate_service_default(client):
    conn = db.get_connection()
    try:
        dining_id = _category_id(conn, "Dining")
        shopping_id = _category_id(conn, "Shopping")
        statement_id = _statement_id(conn)

        conn.execute(
            "INSERT INTO services (name, category_id) VALUES (?, ?)",
            ("Scoped Resolve Merchant", dining_id),
        )
        service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO transactions
                (statement_id, date, description, amount_sgd, category_id, service_id, cat_source)
            VALUES (?, '2026-04-04', 'Scoped Resolve Merchant Apparel', 88.0, NULL, NULL, 'auto')
            """,
            (statement_id,),
        )
        tx_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    resp = client.post(
        "/api/transactions/resolve",
        json={
            "tx_id": tx_id,
            "service_id": service_id,
            "category_id": shopping_id,
            "pattern": "SCOPED RESOLVE MERCHANT",
            "match_type": "contains",
            "apply_scope": "transaction",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["rule_id"] is None

    conn = db.get_connection()
    try:
        service = conn.execute(
            "SELECT category_id FROM services WHERE id = ?",
            (service_id,),
        ).fetchone()
        rule_count = conn.execute(
            "SELECT COUNT(*) FROM merchant_rules WHERE service_id = ?",
            (service_id,),
        ).fetchone()[0]
        tx = conn.execute(
            "SELECT category_id, service_id, cat_source FROM transactions WHERE id = ?",
            (tx_id,),
        ).fetchone()
    finally:
        conn.close()

    assert service["category_id"] == dining_id
    assert rule_count == 0
    assert (tx["category_id"], tx["service_id"], tx["cat_source"]) == (
        shopping_id,
        service_id,
        "manual",
    )


def test_recategorize_all_recomputes_inferred_and_preserves_manual(client):
    conn = db.get_connection()
    try:
        dining_id = _category_id(conn, "Dining")
        shopping_id = _category_id(conn, "Shopping")
        groceries_id = _category_id(conn, "Groceries")
        statement_id = _statement_id(conn)

        conn.execute(
            "INSERT INTO services (name, category_id) VALUES (?, ?)",
            ("Recategorize Merchant", dining_id),
        )
        service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO merchant_rules
                (pattern, service_id, match_type, confidence)
            VALUES (?, ?, 'contains', 'confirmed')
            """,
            ("RECAT MERCHANT", service_id),
        )
        rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        rows = [
            ("2026-04-05", "Recat Merchant Order", dining_id, service_id, "service_default"),
            ("2026-04-06", "Recat Merchant Manual", groceries_id, service_id, "manual"),
        ]
        for tx_date, description, category_id, tx_service_id, cat_source in rows:
            conn.execute(
                """
                INSERT INTO transactions
                    (statement_id, date, description, amount_sgd, category_id, service_id, cat_source)
                VALUES (?, ?, ?, 42.0, ?, ?, ?)
                """,
                (statement_id, tx_date, description, category_id, tx_service_id, cat_source),
            )
        conn.execute(
            "UPDATE merchant_rules SET category_override_id = ? WHERE id = ?",
            (shopping_id, rule_id),
        )
        conn.commit()
    finally:
        conn.close()

    db.invalidate_rules_cache()

    resp = client.post("/api/rules/recategorize", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["updated"] == 1
    assert data["skipped_manual"] == 1

    conn = db.get_connection()
    try:
        rows = conn.execute(
            """
            SELECT description, category_id, service_id, cat_source
            FROM transactions
            WHERE service_id = ?
            ORDER BY date
            """,
            (service_id,),
        ).fetchall()
    finally:
        conn.close()

    by_desc = {
        row["description"]: (row["category_id"], row["service_id"], row["cat_source"])
        for row in rows
    }
    assert by_desc["Recat Merchant Order"] == (shopping_id, service_id, "rule_override")
    assert by_desc["Recat Merchant Manual"] == (groceries_id, service_id, "manual")


def test_rule_update_can_apply_category_override_and_recategorize(client):
    conn = db.get_connection()
    try:
        dining_id = _category_id(conn, "Dining")
        shopping_id = _category_id(conn, "Shopping")
        statement_id = _statement_id(conn)

        conn.execute(
            "INSERT INTO services (name, category_id) VALUES (?, ?)",
            ("Rule Update Merchant", dining_id),
        )
        service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO merchant_rules
                (pattern, service_id, match_type, confidence)
            VALUES (?, ?, 'contains', 'confirmed')
            """,
            ("RULE UPDATE MERCHANT", service_id),
        )
        rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO transactions
                (statement_id, date, description, amount_sgd, category_id, service_id, cat_source)
            VALUES (?, '2026-04-07', 'Rule Update Merchant Apparel', 51.0, ?, ?, 'service_default')
            """,
            (statement_id, dining_id, service_id),
        )
        tx_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    resp = client.put(
        f"/api/rules/{rule_id}",
        json={"category_override_id": shopping_id},
    )
    assert resp.status_code == 200
    assert resp.get_json()["recategorized"] == 1

    conn = db.get_connection()
    try:
        rule = conn.execute(
            "SELECT category_override_id FROM merchant_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        tx = conn.execute(
            "SELECT category_id, service_id, cat_source FROM transactions WHERE id = ?",
            (tx_id,),
        ).fetchone()
    finally:
        conn.close()

    assert rule["category_override_id"] == shopping_id
    assert (tx["category_id"], tx["service_id"], tx["cat_source"]) == (
        shopping_id,
        service_id,
        "rule_override",
    )


def test_rule_create_can_persist_category_override(client):
    conn = db.get_connection()
    try:
        dining_id = _category_id(conn, "Dining")
        shopping_id = _category_id(conn, "Shopping")
        conn.execute(
            "INSERT INTO services (name, category_id) VALUES (?, ?)",
            ("Create Override Merchant", dining_id),
        )
        service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    resp = client.post(
        "/api/rules",
        json={
            "pattern": "CREATE OVERRIDE",
            "service_id": service_id,
            "category_override_id": shopping_id,
            "match_type": "contains",
        },
    )
    assert resp.status_code == 200

    resp = client.get("/api/rules")
    assert resp.status_code == 200
    created = next(r for r in resp.get_json() if r["pattern"] == "CREATE OVERRIDE")
    assert created["service_id"] == service_id
    assert created["category_override_id"] == shopping_id
    assert created["category_id"] == shopping_id
