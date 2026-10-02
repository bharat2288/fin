"""Where a row's book and type came from (cat_source), and which writers may
overwrite which rows."""

import db


def _type_id(conn, name: str) -> int:
    return conn.execute(
        "SELECT id FROM types WHERE kind = 'spending' AND name = ?", (name,)
    ).fetchone()[0]


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


def test_match_merchant_uses_service_default_provenance(conn):
    dining_id = _type_id(conn, "Dining")
    conn.execute(
        "INSERT INTO services (name, book, type_id) VALUES (?, 'Household', ?)",
        ("Mixed Merchant", dining_id),
    )
    service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO merchant_rules (pattern, service_id, match_type, confidence) VALUES (?, ?, 'contains', 'confirmed')",
        ("MIXED MERCHANT", service_id),
    )
    conn.commit()
    db.invalidate_rules_cache()

    found = db.match_merchant("Mixed Merchant Orchard", conn, amount=12.0)

    assert (found["book"], found["type_id"]) == ("Household", dining_id)
    assert found["service_id"] == service_id
    assert found["cat_source"] == "service_default"


def test_match_merchant_uses_rule_override_provenance(conn):
    dining_id = _type_id(conn, "Dining")
    shopping_id = _type_id(conn, "Shopping")
    conn.execute(
        "INSERT INTO services (name, book, type_id) VALUES (?, 'Household', ?)",
        ("Mixed Override Merchant", dining_id),
    )
    service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        INSERT INTO merchant_rules
            (pattern, service_id, type_override_id, match_type, confidence)
        VALUES (?, ?, ?, 'contains', 'confirmed')
        """,
        ("MIXED OVERRIDE", service_id, shopping_id),
    )
    conn.commit()
    db.invalidate_rules_cache()

    found = db.match_merchant("Mixed Override Apparel", conn, amount=80.0)

    # The rule overrides the type; the book is still the merchant's.
    assert (found["book"], found["type_id"]) == ("Household", shopping_id)
    assert found["service_id"] == service_id
    assert found["cat_source"] == "rule_override"


def test_match_merchant_with_no_rule_gives_nothing(conn):
    found = db.match_merchant("NO SUCH SAMPLE MERCHANT", conn, amount=5.0)

    assert found == {
        "book": None, "type_id": None, "service_id": None,
        "cat_source": None, "review_each_time": False,
    }


def test_service_type_update_only_relabels_service_default_rows(client):
    conn = db.get_connection()
    try:
        dining_id = _type_id(conn, "Dining")
        shopping_id = _type_id(conn, "Shopping")
        groceries_id = _type_id(conn, "Groceries")
        statement_id = _statement_id(conn)

        conn.execute(
            "INSERT INTO services (name, book, type_id) VALUES (?, 'Household', ?)",
            ("Scoped Merchant", dining_id),
        )
        service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        rows = [
            ("2026-04-01", "Scoped Merchant Lunch", dining_id, "service_default"),
            ("2026-04-02", "Scoped Merchant Shirt", shopping_id, "rule_override"),
            ("2026-04-03", "Scoped Merchant Gift", shopping_id, "manual"),
        ]
        for tx_date, description, type_id, cat_source in rows:
            conn.execute(
                """
                INSERT INTO transactions
                    (statement_id, date, description, amount_sgd, book, type_id, service_id, cat_source)
                VALUES (?, ?, ?, 25.0, 'Household', ?, ?, ?)
                """,
                (statement_id, tx_date, description, type_id, service_id, cat_source),
            )
        conn.commit()
    finally:
        conn.close()

    resp = client.put(f"/api/services/{service_id}", json={"type_id": groceries_id})
    assert resp.status_code == 200
    assert resp.get_json()["recategorized"] == 1

    conn = db.get_connection()
    try:
        rows = conn.execute(
            """
            SELECT description, type_id, cat_source
            FROM transactions
            WHERE service_id = ?
            ORDER BY date
            """,
            (service_id,),
        ).fetchall()
    finally:
        conn.close()

    by_desc = {row["description"]: (row["type_id"], row["cat_source"]) for row in rows}
    assert by_desc["Scoped Merchant Lunch"] == (groceries_id, "service_default")
    assert by_desc["Scoped Merchant Shirt"] == (shopping_id, "rule_override")
    assert by_desc["Scoped Merchant Gift"] == (shopping_id, "manual")


def test_resolve_transaction_scope_does_not_mutate_service_default(client):
    conn = db.get_connection()
    try:
        dining_id = _type_id(conn, "Dining")
        shopping_id = _type_id(conn, "Shopping")
        statement_id = _statement_id(conn)

        conn.execute(
            "INSERT INTO services (name, book, type_id) VALUES (?, 'Household', ?)",
            ("Scoped Resolve Merchant", dining_id),
        )
        service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO transactions
                (statement_id, date, description, amount_sgd, type_id, service_id, cat_source)
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
            "type_id": shopping_id,
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
            "SELECT book, type_id FROM services WHERE id = ?",
            (service_id,),
        ).fetchone()
        rule_count = conn.execute(
            "SELECT COUNT(*) FROM merchant_rules WHERE service_id = ?",
            (service_id,),
        ).fetchone()[0]
        tx = conn.execute(
            "SELECT book, type_id, service_id, cat_source FROM transactions WHERE id = ?",
            (tx_id,),
        ).fetchone()
    finally:
        conn.close()

    assert (service["book"], service["type_id"]) == ("Household", dining_id)
    assert rule_count == 0
    assert (tx["book"], tx["type_id"], tx["service_id"], tx["cat_source"]) == (
        "Household",
        shopping_id,
        service_id,
        "manual",
    )


def test_recategorize_all_recomputes_inferred_and_preserves_manual(client):
    conn = db.get_connection()
    try:
        dining_id = _type_id(conn, "Dining")
        shopping_id = _type_id(conn, "Shopping")
        groceries_id = _type_id(conn, "Groceries")
        statement_id = _statement_id(conn)

        conn.execute(
            "INSERT INTO services (name, book, type_id) VALUES (?, 'Household', ?)",
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
        for tx_date, description, type_id, tx_service_id, cat_source in rows:
            conn.execute(
                """
                INSERT INTO transactions
                    (statement_id, date, description, amount_sgd, book, type_id, service_id, cat_source)
                VALUES (?, ?, ?, 42.0, 'Household', ?, ?, ?)
                """,
                (statement_id, tx_date, description, type_id, tx_service_id, cat_source),
            )
        conn.execute(
            "UPDATE merchant_rules SET type_override_id = ? WHERE id = ?",
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
            SELECT description, type_id, service_id, cat_source
            FROM transactions
            WHERE service_id = ?
            ORDER BY date
            """,
            (service_id,),
        ).fetchall()
    finally:
        conn.close()

    by_desc = {
        row["description"]: (row["type_id"], row["service_id"], row["cat_source"])
        for row in rows
    }
    assert by_desc["Recat Merchant Order"] == (shopping_id, service_id, "rule_override")
    assert by_desc["Recat Merchant Manual"] == (groceries_id, service_id, "manual")


def test_rule_update_can_apply_type_override_and_relabel(client):
    conn = db.get_connection()
    try:
        dining_id = _type_id(conn, "Dining")
        shopping_id = _type_id(conn, "Shopping")
        statement_id = _statement_id(conn)

        conn.execute(
            "INSERT INTO services (name, book, type_id) VALUES (?, 'Household', ?)",
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
                (statement_id, date, description, amount_sgd, book, type_id, service_id, cat_source)
            VALUES (?, '2026-04-07', 'Rule Update Merchant Apparel', 51.0, 'Household', ?, ?, 'service_default')
            """,
            (statement_id, dining_id, service_id),
        )
        tx_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    resp = client.put(
        f"/api/rules/{rule_id}",
        json={"type_override_id": shopping_id},
    )
    assert resp.status_code == 200
    assert resp.get_json()["recategorized"] == 1

    conn = db.get_connection()
    try:
        rule = conn.execute(
            "SELECT type_override_id FROM merchant_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        tx = conn.execute(
            "SELECT book, type_id, service_id, cat_source FROM transactions WHERE id = ?",
            (tx_id,),
        ).fetchone()
    finally:
        conn.close()

    assert rule["type_override_id"] == shopping_id
    assert (tx["book"], tx["type_id"], tx["service_id"], tx["cat_source"]) == (
        "Household",
        shopping_id,
        service_id,
        "rule_override",
    )


def test_rule_create_can_persist_type_override(client):
    conn = db.get_connection()
    try:
        dining_id = _type_id(conn, "Dining")
        shopping_id = _type_id(conn, "Shopping")
        conn.execute(
            "INSERT INTO services (name, book, type_id) VALUES (?, 'Household', ?)",
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
            "type_override_id": shopping_id,
            "match_type": "contains",
        },
    )
    assert resp.status_code == 200

    resp = client.get("/api/rules")
    assert resp.status_code == 200
    created = next(r for r in resp.get_json() if r["pattern"] == "CREATE OVERRIDE")
    assert created["service_id"] == service_id
    assert created["type_override_id"] == shopping_id
    assert created["type_id"] == shopping_id
    assert created["book"] == "Household"
