"""fin — Personal Finance Tracker

Flask backend serving the 4-tab SPA (Dashboard, Import, History, Merchant Rules)
and API endpoints for statement parsing, categorization, and visualization.

Usage:
    py app.py                  # Start on port 8450
    py app.py --port 8450      # Explicit port
"""

import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from db import get_connection, init_db, categorize_transaction

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Max upload size: 10MB
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024


# ---------------------------------------------------------------------------
# Static file serving
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

@app.route("/api/categories")
def api_categories():
    """List all categories as a flat list with parent info."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT c.id, c.name, c.parent_id, c.is_personal, p.name as parent_name "
        "FROM categories c LEFT JOIN categories p ON c.parent_id = p.id "
        "ORDER BY COALESCE(p.name, c.name), c.parent_id IS NOT NULL, c.name"
    ).fetchall()
    conn.close()
    return jsonify([{
        "id": r["id"],
        "name": r["name"],
        "parent_id": r["parent_id"],
        "parent_name": r["parent_name"],
        "is_personal": r["is_personal"],
        "display_name": f"{r['parent_name']} > {r['name']}" if r["parent_name"] else r["name"],
    } for r in rows])


@app.route("/api/categories", methods=["POST"])
def api_categories_create():
    """Create a new category or subcategory."""
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Name is required"}), 400

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO categories (name, parent_id, is_personal) VALUES (?, ?, ?)",
            (data["name"], data.get("parent_id"), data.get("is_personal", 1)),
        )
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({"success": True, "id": new_id})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/accounts")
def api_accounts():
    """List all accounts."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, short_name, type, last_four FROM accounts ORDER BY name"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Dashboard API
# ---------------------------------------------------------------------------

@app.route("/api/dashboard/summary")
def api_dashboard_summary():
    """Stat card data with optional filters.

    Query params: start, end, personal_only, exclude_anomaly
    """
    conn = get_connection()
    filters, params = _build_filters(request.args)

    base = f"""
        SELECT
            COUNT(*) as total_transactions,
            SUM(CASE WHEN amount_sgd > 0 AND is_payment = 0 AND is_transfer = 0 THEN amount_sgd ELSE 0 END) as total_spend,
            SUM(CASE WHEN amount_sgd > 0 AND is_payment = 0 AND is_transfer = 0 AND c.is_personal = 1 THEN amount_sgd ELSE 0 END) as personal_spend,
            SUM(CASE WHEN amount_sgd > 0 AND is_payment = 0 AND is_transfer = 0 AND c.is_personal = 0 THEN amount_sgd ELSE 0 END) as moom_spend,
            COUNT(CASE WHEN t.category_id IS NULL AND is_payment = 0 AND is_transfer = 0 THEN 1 END) as uncategorized
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN statements s ON t.statement_id = s.id
        WHERE is_payment = 0 AND is_transfer = 0 {filters}
    """
    row = conn.execute(base, params).fetchone()
    conn.close()

    return jsonify({
        "total_transactions": row["total_transactions"] or 0,
        "total_spend": round(row["total_spend"] or 0, 2),
        "personal_spend": round(row["personal_spend"] or 0, 2),
        "moom_spend": round(row["moom_spend"] or 0, 2),
        "uncategorized": row["uncategorized"] or 0,
    })


@app.route("/api/dashboard/monthly")
def api_dashboard_monthly():
    """Spending by category over time for stacked bar chart.

    Query params: start, end, personal_only, moom_only, exclude_anomaly, granularity
    granularity: 'monthly' (default), 'weekly', 'quarterly'
    """
    conn = get_connection()
    filters, params = _build_filters(request.args)
    granularity = request.args.get("granularity", "monthly")

    # Choose time bucket SQL expression
    if granularity == "weekly":
        # ISO week: "2025-W42"
        time_bucket = "strftime('%Y', t.date) || '-W' || printf('%02d', (strftime('%j', t.date) - 1) / 7 + 1)"
    elif granularity == "quarterly":
        time_bucket = "strftime('%Y', t.date) || '-Q' || ((CAST(strftime('%m', t.date) AS INTEGER) - 1) / 3 + 1)"
    else:
        time_bucket = "strftime('%Y-%m', t.date)"

    # group_parent=true rolls subcategories up into their parent
    group_parent = request.args.get("group_parent", "true") == "true"

    if group_parent:
        cat_expr = "COALESCE(p.name, c.name)"
    else:
        cat_expr = "c.name"

    rows = conn.execute(f"""
        SELECT
            {time_bucket} as period,
            {cat_expr} as category,
            c.is_personal,
            SUM(t.amount_sgd) as total
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN categories p ON c.parent_id = p.id
        JOIN statements s ON t.statement_id = s.id
        WHERE t.amount_sgd > 0 AND t.is_payment = 0 AND t.is_transfer = 0 {filters}
        GROUP BY period, category
        ORDER BY period, total DESC
    """, params).fetchall()
    conn.close()

    # Structure: {period: {category: total, ...}, ...}
    result = {}
    for r in rows:
        period = r["period"]
        if period not in result:
            result[period] = {}
        cat = r["category"] or "Other"
        result[period][cat] = round(result[period].get(cat, 0) + r["total"], 2)

    return jsonify(result)


@app.route("/api/dashboard/categories")
def api_dashboard_categories():
    """Category totals for donut chart.

    Query params: start, end, personal_only, exclude_anomaly, group_parent
    """
    conn = get_connection()
    filters, params = _build_filters(request.args)

    group_parent = request.args.get("group_parent", "true") == "true"

    if group_parent:
        cat_expr = "COALESCE(p.name, c.name)"
        personal_expr = "COALESCE(p.is_personal, c.is_personal)"
    else:
        cat_expr = "c.name"
        personal_expr = "c.is_personal"

    rows = conn.execute(f"""
        SELECT
            {cat_expr} as category,
            {personal_expr} as is_personal,
            SUM(t.amount_sgd) as total,
            COUNT(*) as count
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN categories p ON c.parent_id = p.id
        JOIN statements s ON t.statement_id = s.id
        WHERE t.amount_sgd > 0 AND t.is_payment = 0 AND t.is_transfer = 0 {filters}
        GROUP BY category
        ORDER BY total DESC
    """, params).fetchall()
    conn.close()

    return jsonify([{
        "category": r["category"] or "Other",
        "is_personal": r["is_personal"],
        "total": round(r["total"], 2),
        "count": r["count"],
    } for r in rows])


# ---------------------------------------------------------------------------
# Transactions API
# ---------------------------------------------------------------------------

@app.route("/api/transactions")
def api_transactions():
    """Paginated transaction list with filters.

    Query params: start, end, personal_only, exclude_anomaly,
                  category, account_id, month, page, per_page, search
    """
    conn = get_connection()
    filters, params = _build_filters(request.args)

    # Additional filters
    category = request.args.get("category")
    if category:
        filters += " AND c.name = ?"
        params.append(category)

    account_id = request.args.get("account_id")
    if account_id:
        filters += " AND s.account_id = ?"
        params.append(int(account_id))

    month = request.args.get("month")
    if month:
        filters += " AND strftime('%Y-%m', t.date) = ?"
        params.append(month)

    search = request.args.get("search")
    if search:
        filters += " AND t.description LIKE ?"
        params.append(f"%{search}%")

    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    offset = (page - 1) * per_page

    # Sort column — whitelist valid columns to prevent SQL injection
    sort_col = request.args.get("sort", "date")
    sort_dir = request.args.get("sort_dir", "desc").upper()
    valid_sorts = {
        "date": "t.date",
        "description": "t.description",
        "category": "c.name",
        "account": "a.name",
        "amount": "t.amount_sgd",
    }
    order_col = valid_sorts.get(sort_col, "t.date")
    if sort_dir not in ("ASC", "DESC"):
        sort_dir = "DESC"

    # Count total
    count_row = conn.execute(f"""
        SELECT COUNT(*) as cnt
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN statements s ON t.statement_id = s.id
        WHERE 1=1 {filters}
    """, params).fetchone()

    # Fetch page — include parent category for "Parent > Sub" display
    rows = conn.execute(f"""
        SELECT
            t.id, t.date, t.description, t.amount_sgd,
            t.amount_foreign, t.currency_foreign,
            c.name as category, c.is_personal,
            p.name as parent_category,
            t.is_payment, t.is_transfer, t.is_anomaly,
            t.notes,
            a.name as account_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN categories p ON c.parent_id = p.id
        JOIN statements s ON t.statement_id = s.id
        JOIN accounts a ON s.account_id = a.id
        WHERE 1=1 {filters}
        ORDER BY {order_col} {sort_dir}, t.date DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset]).fetchall()
    conn.close()

    txns = []
    for r in rows:
        tx = dict(r)
        # Build "Parent > Sub" display category
        if r["parent_category"]:
            tx["display_category"] = f"{r['parent_category']} > {r['category']}"
        else:
            tx["display_category"] = r["category"]
        txns.append(tx)

    return jsonify({
        "transactions": txns,
        "total": count_row["cnt"],
        "page": page,
        "per_page": per_page,
        "pages": (count_row["cnt"] + per_page - 1) // per_page,
    })


@app.route("/api/transactions/<int:tx_id>", methods=["PUT"])
def api_update_transaction(tx_id: int):
    """Update a transaction's notes (and optionally category, anomaly flag)."""
    data = request.get_json()
    conn = get_connection()

    # Build SET clause from allowed fields
    allowed = {"notes": "notes", "category_id": "category_id", "is_anomaly": "is_anomaly"}
    sets = []
    values = []
    for key, col in allowed.items():
        if key in data:
            sets.append(f"{col} = ?")
            values.append(data[key])

    if not sets:
        conn.close()
        return jsonify({"error": "No valid fields to update"}), 400

    values.append(tx_id)
    conn.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "id": tx_id})


def _build_filters(args) -> tuple[str, list]:
    """Build SQL WHERE clause fragments from common query params."""
    filters = ""
    params = []

    start = args.get("start")
    if start:
        filters += " AND t.date >= ?"
        params.append(start)

    end = args.get("end")
    if end:
        filters += " AND t.date <= ?"
        params.append(end)

    personal_only = args.get("personal_only")
    if personal_only == "true":
        filters += " AND c.is_personal = 1"

    moom_only = args.get("moom_only")
    if moom_only == "true":
        filters += " AND c.is_personal = 0"

    exclude_anomaly = args.get("exclude_anomaly")
    if exclude_anomaly == "true":
        filters += " AND t.is_anomaly = 0"

    return filters, params


# ---------------------------------------------------------------------------
# Import API
# ---------------------------------------------------------------------------

@app.route("/api/import/upload", methods=["POST"])
def api_import_upload():
    """Accept statement files, parse, categorize, return preview.

    Accepts multipart form data with one or more files.
    Returns grouped preview by account with categorization status.
    """
    if "files" not in request.files:
        return jsonify({"error": "No files uploaded"}), 400

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    conn = get_connection()
    init_db()

    all_groups = {}  # account_name -> list of transaction dicts
    errors = []
    filenames = []

    # Save uploaded files to temp dir for parsing
    with tempfile.TemporaryDirectory() as tmpdir:
        saved_paths = []
        for f in files:
            if not f.filename:
                continue
            safe_name = f.filename
            save_path = os.path.join(tmpdir, safe_name)
            f.save(save_path)
            saved_paths.append((save_path, safe_name))
            filenames.append(safe_name)

        # Detect and parse each file
        # _auto_detect_and_parse returns a list (multi-card CSVs produce multiple statements)
        parsed_statements = []
        for save_path, filename in saved_paths:
            try:
                stmts = _auto_detect_and_parse(save_path)
                parsed_statements.extend(stmts)
            except Exception as e:
                errors.append({"file": filename, "error": str(e)})

        # Handle Vantage MK/BS split if both exports present
        parsed_statements = _handle_vantage_split(parsed_statements)

        # Group transactions by account and categorize
        cats = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM categories").fetchall()}
        cats_by_name = {v: k for k, v in cats.items()}

        for stmt in parsed_statements:
            for tx in stmt.transactions:
                if tx.is_payment:
                    continue

                account = tx.card_info or stmt.accounts[0] if stmt.accounts else "Unknown"

                cat_id = categorize_transaction(tx.description, conn)

                # For bank statements, try PayNow rules
                if cat_id is None and "PAYNOW" in tx.description.upper():
                    from ingest import categorize_bank_paynow
                    _, cat_name = categorize_bank_paynow(tx.description)
                    if cat_name:
                        cat_id = cats_by_name.get(cat_name)

                entry = {
                    "date": tx.date,
                    "description": tx.description,
                    "amount_sgd": tx.amount_sgd,
                    "amount_foreign": tx.amount_foreign,
                    "currency_foreign": tx.currency_foreign,
                    "category_id": cat_id,
                    "category_name": cats.get(cat_id) if cat_id else None,
                    "is_payment": tx.is_payment,
                    "is_transfer": tx.is_transfer,
                    "account": account,
                    "status": "categorized" if cat_id else ("transfer" if tx.is_transfer else "uncategorized"),
                    "_skip": tx.is_transfer,  # default skip transfers
                }

                if account not in all_groups:
                    all_groups[account] = []
                all_groups[account].append(entry)

    conn.close()

    # Build response
    groups = []
    total = 0
    categorized = 0
    uncategorized = 0
    skipped = 0

    for account_name, txns in all_groups.items():
        group_cat = sum(1 for t in txns if t["status"] == "categorized")
        group_uncat = sum(1 for t in txns if t["status"] == "uncategorized")
        group_skip = sum(1 for t in txns if t["_skip"])

        groups.append({
            "account": account_name,
            "transactions": txns,
            "categorized": group_cat,
            "uncategorized": group_uncat,
            "skipped": group_skip,
            "total": len(txns),
        })

        total += len(txns)
        categorized += group_cat
        uncategorized += group_uncat
        skipped += group_skip

    # Save preview to batch_imports
    conn = get_connection()
    conn.execute(
        "INSERT INTO batch_imports (filenames, accounts, status, total_lines, categorized_lines) VALUES (?, ?, 'preview', ?, ?)",
        (
            json.dumps(filenames),
            json.dumps(list(all_groups.keys())),
            total,
            categorized,
        ),
    )
    conn.commit()
    import_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    return jsonify({
        "import_id": import_id,
        "groups": groups,
        "stats": {
            "total": total,
            "categorized": categorized,
            "uncategorized": uncategorized,
            "skipped": skipped,
        },
        "errors": errors,
        "filenames": filenames,
    })


@app.route("/api/import/confirm", methods=["POST"])
def api_import_confirm():
    """Commit previewed transactions to the database.

    Expects JSON body:
    {
        "import_id": int,
        "groups": [
            {
                "account": "account name",
                "transactions": [
                    {
                        "date": "YYYY-MM-DD",
                        "description": "...",
                        "amount_sgd": 123.45,
                        "amount_foreign": null,
                        "currency_foreign": null,
                        "category_id": 5,
                        "_skip": false
                    }, ...
                ]
            }, ...
        ],
        "new_rules": [
            {"pattern": "MERCHANT", "category_id": 5, "match_type": "contains"}, ...
        ]
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    import_id = data.get("import_id")
    groups = data.get("groups", [])
    new_rules = data.get("new_rules", [])

    conn = get_connection()
    total_saved = 0
    total_duplicates = 0
    accounts_created = []

    try:
        for group in groups:
            account_name = group["account"]
            txns = group["transactions"]

            # Filter out skipped transactions
            active_txns = [t for t in txns if not t.get("_skip", False)]
            if not active_txns:
                continue

            # Ensure account exists
            from ingest import ensure_account, ensure_statement
            stmt_type = "credit_card"  # default; could detect from account name
            if "bank" in account_name.lower() or "one account" in account_name.lower() or "home" in account_name.lower():
                stmt_type = "bank"

            account_id = ensure_account(conn, account_name, stmt_type)
            accounts_created.append(account_name)

            # Determine statement date from transactions
            dates = [t["date"] for t in active_txns if t.get("date")]
            stmt_date = max(dates) if dates else datetime.now().strftime("%Y-%m-%d")

            # Create statement record
            filename = f"import_{import_id}_{account_name[:30]}"
            statement_id = ensure_statement(conn, account_id, stmt_date, filename)

            if statement_id is None:
                # Already imported — try with a unique date
                stmt_date_unique = stmt_date + f"_import{import_id}"
                conn.execute(
                    "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, ?)",
                    (account_id, stmt_date_unique, filename),
                )
                conn.commit()
                statement_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # --- Deduplication ---
            # Group import transactions by (date, description, amount) to handle
            # genuine same-day repeats (e.g. two identical coffees).
            # For each group, count how many already exist in DB for this account.
            # Only insert (import_count - existing_count), minimum 0.
            from collections import Counter

            import_counts = Counter()
            import_by_key = {}  # key -> [list of tx dicts]
            for tx in active_txns:
                key = (tx["date"], tx["description"], tx["amount_sgd"])
                import_counts[key] += 1
                import_by_key.setdefault(key, []).append(tx)

            duplicates_skipped = 0
            for key, import_count in import_counts.items():
                date_val, desc_val, amount_val = key

                # Count existing matches for this account
                existing = conn.execute(
                    """SELECT COUNT(*) FROM transactions t
                       JOIN statements s ON t.statement_id = s.id
                       WHERE s.account_id = ?
                         AND t.date = ? AND t.description = ? AND t.amount_sgd = ?""",
                    (account_id, date_val, desc_val, amount_val),
                ).fetchone()[0]

                # Only insert the net-new ones
                to_insert = max(0, import_count - existing)
                skipped = import_count - to_insert
                duplicates_skipped += skipped

                # Insert from the end of the list (order doesn't matter)
                for tx in import_by_key[key][:to_insert]:
                    conn.execute(
                        "INSERT INTO transactions "
                        "(statement_id, date, description, amount_sgd, amount_foreign, "
                        "currency_foreign, category_id, is_payment, is_transfer, is_anomaly) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            statement_id,
                            tx["date"],
                            tx["description"],
                            tx["amount_sgd"],
                            tx.get("amount_foreign"),
                            tx.get("currency_foreign"),
                            tx.get("category_id"),
                            1 if tx.get("is_payment") else 0,
                            1 if tx.get("is_transfer") else 0,
                            1 if tx.get("is_anomaly") else 0,
                        ),
                    )
                    total_saved += 1

            total_duplicates += duplicates_skipped
            conn.commit()

        # Save new merchant rules
        rules_added = 0
        for rule in new_rules:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO merchant_rules (pattern, category_id, match_type, confidence) "
                    "VALUES (?, ?, ?, 'confirmed')",
                    (rule["pattern"], rule["category_id"], rule.get("match_type", "contains")),
                )
                rules_added += 1
            except Exception:
                pass
        conn.commit()

        # Update batch_imports record
        result_summary = {
            "transactions_saved": total_saved,
            "duplicates_skipped": total_duplicates,
            "accounts": accounts_created,
            "rules_added": rules_added,
        }
        conn.execute(
            "UPDATE batch_imports SET status = 'committed', result_json = ? WHERE id = ?",
            (json.dumps(result_summary), import_id),
        )
        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "transactions_saved": total_saved,
            "duplicates_skipped": total_duplicates,
            "rules_added": rules_added,
            "accounts": accounts_created,
        })

    except Exception as e:
        conn.execute(
            "UPDATE batch_imports SET status = 'failed', result_json = ? WHERE id = ?",
            (json.dumps({"error": str(e)}), import_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"error": str(e)}), 500


@app.route("/api/import/history")
def api_import_history():
    """List past imports."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM batch_imports ORDER BY created_at DESC"
    ).fetchall()
    conn.close()

    return jsonify([{
        "id": r["id"],
        "filenames": json.loads(r["filenames"]) if r["filenames"] else [],
        "accounts": json.loads(r["accounts"]) if r["accounts"] else [],
        "status": r["status"],
        "total_lines": r["total_lines"],
        "categorized_lines": r["categorized_lines"],
        "result": json.loads(r["result_json"]) if r["result_json"] else None,
        "created_at": r["created_at"],
    } for r in rows])


# ---------------------------------------------------------------------------
# Merchant Rules CRUD
# ---------------------------------------------------------------------------

@app.route("/api/rules")
def api_rules():
    """List all merchant rules with parent/sub category info."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT mr.id, mr.pattern, mr.match_type, mr.confidence,
               c.id as category_id, c.name as category_name,
               c.parent_id,
               p.name as parent_name
        FROM merchant_rules mr
        JOIN categories c ON mr.category_id = c.id
        LEFT JOIN categories p ON c.parent_id = p.id
        ORDER BY COALESCE(p.name, c.name), c.name, mr.pattern
    """).fetchall()
    conn.close()
    return jsonify([{
        "id": r["id"],
        "pattern": r["pattern"],
        "match_type": r["match_type"],
        "confidence": r["confidence"],
        "category_id": r["category_id"],
        "category_name": r["category_name"],
        "parent_name": r["parent_name"],
        "display_category": f"{r['parent_name']} > {r['category_name']}" if r["parent_name"] else r["category_name"],
    } for r in rows])


@app.route("/api/rules", methods=["POST"])
def api_rules_create():
    """Add a new merchant rule."""
    data = request.get_json()
    if not data or "pattern" not in data or "category_id" not in data:
        return jsonify({"error": "pattern and category_id required"}), 400

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO merchant_rules (pattern, category_id, match_type, confidence) VALUES (?, ?, ?, ?)",
            (data["pattern"], data["category_id"], data.get("match_type", "contains"), "confirmed"),
        )
        conn.commit()
        rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({"id": rule_id, "success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/rules/<int:rule_id>", methods=["PUT"])
def api_rules_update(rule_id):
    """Update a merchant rule."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    conn = get_connection()
    sets = []
    params = []

    if "pattern" in data:
        sets.append("pattern = ?")
        params.append(data["pattern"])
    if "category_id" in data:
        sets.append("category_id = ?")
        params.append(data["category_id"])
    if "match_type" in data:
        sets.append("match_type = ?")
        params.append(data["match_type"])

    if not sets:
        conn.close()
        return jsonify({"error": "No fields to update"}), 400

    params.append(rule_id)
    conn.execute(f"UPDATE merchant_rules SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/rules/<int:rule_id>", methods=["DELETE"])
def api_rules_delete(rule_id):
    """Delete a merchant rule."""
    conn = get_connection()
    conn.execute("DELETE FROM merchant_rules WHERE id = ?", (rule_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------

def _auto_detect_and_parse(filepath: str) -> list:
    """Auto-detect file format and parse.

    Always returns a list of ParsedStatement objects (may be 1 or more,
    e.g. multi-card DBS CSVs produce one statement per card).
    """
    path = Path(filepath)
    ext = path.suffix.lower()

    if ext == ".pdf":
        # Try UOB first, then DBS
        from parse_uob import detect_uob_pdf, parse_uob_pdf
        if detect_uob_pdf(filepath):
            return [parse_uob_pdf(filepath)]
        # DBS PDF
        from parse_dbs import parse_statement
        return [parse_statement(filepath)]

    elif ext == ".csv":
        # Check if Citi format (no header, starts with date)
        from parse_citi_csv import detect_citi_csv
        if detect_citi_csv(filepath):
            from parse_citi_csv import parse_citi_csv
            return [parse_citi_csv(filepath)]
        # DBS CSV — returns a list (may contain multiple cards)
        from parse_dbs_csv import parse_csv
        return parse_csv(filepath)

    else:
        raise ValueError(f"Unsupported file format: {ext}")


def _handle_vantage_split(statements: list) -> list:
    """Handle DBS Vantage MK/BS cardholder split.

    If both a BS-only export and MK+BS combined export are detected,
    cross-reference to tag each transaction as MK or BS.
    """
    # Find Vantage statements by account name
    vantage_bs = []  # BS-only (supplementary, typically card 3696)
    vantage_combined = []  # MK+BS (primary, typically card 7436)
    other = []

    for stmt in statements:
        if not stmt.accounts:
            other.append(stmt)
            continue

        acct = stmt.accounts[0].upper()
        if "VANTAGE" in acct:
            # Heuristic: the BS-only export has fewer transactions
            # OR we detect from the original folder name (not available here)
            # For now: if we see two Vantage statements with different card numbers,
            # the one with fewer transactions is BS-only
            vantage_combined.append(stmt)
        else:
            other.append(stmt)

    # If we have exactly 2 Vantage groups with different card numbers, split
    if len(vantage_combined) >= 2:
        # Group by card number
        by_card = {}
        for stmt in vantage_combined:
            card = stmt.accounts[0] if stmt.accounts else "unknown"
            if card not in by_card:
                by_card[card] = []
            by_card[card].append(stmt)

        if len(by_card) == 2:
            cards = list(by_card.keys())
            # The card with fewer total transactions is BS-only
            count0 = sum(len(s.transactions) for s in by_card[cards[0]])
            count1 = sum(len(s.transactions) for s in by_card[cards[1]])

            if count0 < count1:
                bs_card, combined_card = cards[0], cards[1]
            else:
                bs_card, combined_card = cards[1], cards[0]

            # Build BS fingerprint set
            bs_fingerprints = set()
            for stmt in by_card[bs_card]:
                for tx in stmt.transactions:
                    bs_fingerprints.add((tx.date, tx.description, tx.amount_sgd))

            # Tag combined transactions
            from parse_dbs import ParsedTransaction, ParsedStatement
            mk_txns = []
            bs_txns = []

            for stmt in by_card[combined_card]:
                for tx in stmt.transactions:
                    fp = (tx.date, tx.description, tx.amount_sgd)
                    if fp in bs_fingerprints:
                        bs_txns.append(tx)
                        bs_fingerprints.discard(fp)  # match once
                    else:
                        mk_txns.append(tx)

            # Create split statements
            combined_name = by_card[combined_card][0].accounts[0]
            mk_name = combined_name.replace(combined_name.split()[-1], combined_name.split()[-1] + " (MK)")
            bs_name = combined_name.replace(combined_name.split()[-1], combined_name.split()[-1] + " (BS)")

            # Update card_info on each transaction
            for tx in mk_txns:
                tx.card_info = mk_name
            for tx in bs_txns:
                tx.card_info = bs_name

            mk_stmt = ParsedStatement(
                statement_type="credit_card",
                statement_date=by_card[combined_card][0].statement_date,
                accounts=[mk_name],
                filename="vantage_mk_split",
                transactions=mk_txns,
            )
            bs_stmt = ParsedStatement(
                statement_type="credit_card",
                statement_date=by_card[combined_card][0].statement_date,
                accounts=[bs_name],
                filename="vantage_bs_split",
                transactions=bs_txns,
            )

            return other + [mk_stmt, bs_stmt]

    # No split needed — return as-is
    return other + vantage_combined


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="fin — Personal Finance Tracker")
    parser.add_argument("--port", type=int, default=8450)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    init_db()
    print(f"fin running at http://localhost:{args.port}")
    app.run(host="0.0.0.0", port=args.port, debug=args.debug)
