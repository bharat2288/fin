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
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from db import get_connection, init_db, categorize_transaction

app = Flask(__name__, static_folder="static", static_url_path="/static")


def mask_card_number(text: str) -> str:
    """Replace full card numbers (4-4-4-4 or 16 digits) with masked version showing last 4."""
    # Pattern: 4 groups of 4 digits separated by dashes or spaces
    text = re.sub(
        r'\b(\d{4})[-\s](\d{4})[-\s](\d{4})[-\s](\d{4})\b',
        r'****-****-****-\4',
        text,
    )
    # Pattern: 16 consecutive digits
    text = re.sub(r'\b\d{12}(\d{4})\b', r'************\1', text)
    return text

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
        "SELECT id, name, short_name, type, last_four, currency FROM accounts ORDER BY name"
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["name"] = mask_card_number(d["name"])
        d["short_name"] = mask_card_number(d["short_name"])
        result.append(d)
    return jsonify(result)


@app.route("/api/accounts", methods=["POST"])
def api_accounts_create():
    """Create a new account."""
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Account name is required"}), 400

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO accounts (name, short_name, type, last_four, currency) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                data["name"],
                data.get("short_name", data["name"]),
                data.get("type", "credit_card"),
                data.get("last_four"),
                data.get("currency", "SGD"),
            ),
        )
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({"id": new_id, "success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/accounts/<int:acct_id>", methods=["PUT"])
def api_accounts_update(acct_id):
    """Update an account."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    allowed = ["name", "short_name", "type", "last_four", "currency"]
    sets = []
    params = []
    for field in allowed:
        if field in data:
            sets.append(f"{field} = ?")
            params.append(data[field])

    if not sets:
        return jsonify({"error": "No fields to update"}), 400

    conn = get_connection()
    params.append(acct_id)
    conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/accounts/<int:acct_id>", methods=["DELETE"])
def api_accounts_delete(acct_id):
    """Delete an account. Refuses if statements reference it."""
    conn = get_connection()
    stmt_count = conn.execute(
        "SELECT COUNT(*) FROM statements WHERE account_id = ?", (acct_id,)
    ).fetchone()[0]
    if stmt_count > 0:
        conn.close()
        return jsonify({
            "error": f"Cannot delete: {stmt_count} statement(s) reference this account"
        }), 400

    conn.execute("DELETE FROM accounts WHERE id = ?", (acct_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Services API
# ---------------------------------------------------------------------------

@app.route("/api/services")
def api_services():
    """List all services with category info and transaction/rule counts."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT s.*, c.name as category_name, c.is_personal,
               p.name as parent_name,
               (SELECT COUNT(*) FROM transactions t WHERE t.service_id = s.id
                AND t.is_payment = 0 AND t.is_transfer = 0) as txn_count,
               (SELECT COUNT(*) FROM merchant_rules mr WHERE mr.service_id = s.id) as rule_count
        FROM services s
        LEFT JOIN categories c ON s.category_id = c.id
        LEFT JOIN categories p ON c.parent_id = p.id
        ORDER BY s.name
    """).fetchall()
    # Fetch all rule patterns grouped by service_id
    rule_rows = conn.execute(
        "SELECT service_id, pattern, match_type FROM merchant_rules WHERE service_id IS NOT NULL ORDER BY pattern"
    ).fetchall()
    rules_by_svc: dict[int, list[dict]] = {}
    for rr in rule_rows:
        rules_by_svc.setdefault(rr["service_id"], []).append(
            {"pattern": rr["pattern"], "match_type": rr["match_type"]}
        )

    result = []
    for r in rows:
        d = dict(r)
        d["display_category"] = f"{d['parent_name']} > {d['category_name']}" if d["parent_name"] else (d["category_name"] or "")
        d["rules"] = rules_by_svc.get(d["id"], [])
        result.append(d)
    conn.close()
    return jsonify(result)


@app.route("/api/services/bulk-rename", methods=["POST"])
def api_services_bulk_rename():
    """Bulk rename services. Body: { renames: [{id, name}, ...] }"""
    data = request.get_json()
    renames = data.get("renames", [])
    if not renames:
        return jsonify({"error": "No renames provided"}), 400

    conn = get_connection()
    updated = 0
    errors = []
    for item in renames:
        svc_id = item.get("id")
        new_name = (item.get("name") or "").strip()
        if not svc_id or not new_name:
            continue
        try:
            conn.execute("UPDATE services SET name = ? WHERE id = ?", (new_name, svc_id))
            updated += 1
        except Exception as e:
            errors.append(f"{item.get('name')}: {e}")
    conn.commit()
    conn.close()
    return jsonify({"updated": updated, "errors": errors})


@app.route("/api/services", methods=["POST"])
def api_services_create():
    """Create a new service."""
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Service name is required"}), 400
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO services (name, category_id, notes) VALUES (?, ?, ?)",
            (data["name"], data.get("category_id"), data.get("notes")),
        )
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({"id": new_id, "success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/services/<int:svc_id>", methods=["PUT"])
def api_services_update(svc_id):
    """Update a service."""
    data = request.get_json()
    conn = get_connection()
    allowed = ["name", "category_id", "notes", "is_one_off"]
    sets = []
    params = []
    for field in allowed:
        if field in data:
            sets.append(f"{field} = ?")
            params.append(data[field])
    if not sets:
        conn.close()
        return jsonify({"error": "Nothing to update"}), 400
    params.append(svc_id)
    try:
        conn.execute(f"UPDATE services SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/services/<int:svc_id>/merge", methods=["POST"])
def api_services_merge(svc_id):
    """Merge source service into target: reassign all FKs, delete source."""
    data = request.get_json()
    target_id = data.get("target_id")
    if not target_id or int(target_id) == svc_id:
        return jsonify({"error": "Invalid merge target"}), 400
    target_id = int(target_id)

    conn = get_connection()
    # Verify both exist
    source = conn.execute("SELECT name FROM services WHERE id = ?", (svc_id,)).fetchone()
    target = conn.execute("SELECT name FROM services WHERE id = ?", (target_id,)).fetchone()
    if not source or not target:
        conn.close()
        return jsonify({"error": "Service not found"}), 404

    # Reassign all references from source → target
    txn_count = conn.execute(
        "UPDATE transactions SET service_id = ? WHERE service_id = ?",
        (target_id, svc_id),
    ).rowcount
    rule_count = conn.execute(
        "UPDATE merchant_rules SET service_id = ? WHERE service_id = ?",
        (target_id, svc_id),
    ).rowcount
    sub_count = conn.execute(
        "UPDATE subscriptions SET service_id = ? WHERE service_id = ?",
        (target_id, svc_id),
    ).rowcount

    # Delete the now-orphaned source service
    conn.execute("DELETE FROM services WHERE id = ?", (svc_id,))
    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "merged": {
            "source": source["name"],
            "target": target["name"],
            "transactions": txn_count,
            "rules": rule_count,
            "subscriptions": sub_count,
        },
    })


@app.route("/api/services/<int:svc_id>", methods=["DELETE"])
def api_services_delete(svc_id):
    """Delete a service if no transactions/subscriptions reference it."""
    conn = get_connection()
    refs = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE service_id = ?", (svc_id,)
    ).fetchone()[0]
    sub_refs = conn.execute(
        "SELECT COUNT(*) FROM subscriptions WHERE service_id = ?", (svc_id,)
    ).fetchone()[0]
    if refs > 0 or sub_refs > 0:
        conn.close()
        return jsonify({"error": f"Service has {refs} transactions and {sub_refs} subscriptions"}), 400
    conn.execute("DELETE FROM services WHERE id = ?", (svc_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/services/<int:svc_id>/transactions")
def api_service_transactions(svc_id):
    """Get all transactions for a specific service."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT t.id, t.date, t.description, t.amount_sgd,
               t.amount_foreign, t.currency_foreign,
               c.name as category_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.service_id = ?
          AND t.is_payment = 0 AND t.is_transfer = 0
        ORDER BY t.date DESC
    """, (svc_id,)).fetchall()
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
        LEFT JOIN services svc ON t.service_id = svc.id
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


@app.route("/api/dashboard/stat-cards")
def api_dashboard_stat_cards():
    """Stat cards: single month spend + delta vs 3-month rolling average.

    Auto-picks reference month using the 15th rule:
      - If today >= 15th, ref = previous month
      - If today < 15th, ref = two months ago
    Override with ?ref_month=YYYY-MM.

    Respects: personal_only, moom_only, exclude_anomaly, account_id
    """
    # Determine reference month
    ref_month_param = request.args.get("ref_month")
    if ref_month_param:
        ref_y, ref_m = int(ref_month_param[:4]), int(ref_month_param[5:7])
    else:
        today = date.today()
        ref_y, ref_m = today.year, today.month
        if today.day >= 15:
            ref_m -= 1
            if ref_m == 0:
                ref_m, ref_y = 12, ref_y - 1
        else:
            ref_m -= 2
            if ref_m <= 0:
                ref_m, ref_y = ref_m + 12, ref_y - 1

    # Build date ranges
    ref_start = f"{ref_y:04d}-{ref_m:02d}-01"
    # Last day of ref month
    if ref_m == 12:
        ref_end_date = date(ref_y + 1, 1, 1) - timedelta(days=1)
    else:
        ref_end_date = date(ref_y, ref_m + 1, 1) - timedelta(days=1)
    ref_end = ref_end_date.strftime("%Y-%m-%d")

    # 3-month avg: the 3 months before ref_month
    avg_months = []
    ay, am = ref_y, ref_m
    for _ in range(3):
        am -= 1
        if am == 0:
            am, ay = 12, ay - 1
        avg_months.append((ay, am))
    avg_months.reverse()  # chronological order

    # Filters (account, anomaly — but NOT start/end, personal/moom applied below)
    conn = get_connection()
    personal_only = request.args.get("personal_only") == "true"
    moom_only = request.args.get("moom_only") == "true"
    exclude_anomaly = request.args.get("exclude_anomaly") == "true"
    account_id = request.args.get("account_id")

    exclude_one_off = request.args.get("exclude_one_off") == "true"

    extra_filters = ""
    extra_params = []
    if exclude_anomaly:
        extra_filters += " AND t.is_anomaly = 0"
    if exclude_one_off:
        extra_filters += " AND (svc.is_one_off IS NULL OR svc.is_one_off = 0)"
    if account_id:
        extra_filters += " AND s.account_id = ?"
        extra_params.append(int(account_id))

    def query_month(y: int, m: int) -> dict:
        """Query spend totals for a single month."""
        start = f"{y:04d}-{m:02d}-01"
        if m == 12:
            end_d = date(y + 1, 1, 1) - timedelta(days=1)
        else:
            end_d = date(y, m + 1, 1) - timedelta(days=1)
        end = end_d.strftime("%Y-%m-%d")

        params = [start, end] + extra_params
        row = conn.execute(f"""
            SELECT
                SUM(CASE WHEN amount_sgd > 0 AND is_payment = 0 AND is_transfer = 0
                         THEN amount_sgd ELSE 0 END) as total,
                SUM(CASE WHEN amount_sgd > 0 AND is_payment = 0 AND is_transfer = 0
                         AND c.is_personal = 1 THEN amount_sgd ELSE 0 END) as personal,
                SUM(CASE WHEN amount_sgd > 0 AND is_payment = 0 AND is_transfer = 0
                         AND c.is_personal = 0 THEN amount_sgd ELSE 0 END) as moom,
                COUNT(CASE WHEN t.category_id IS NULL AND is_payment = 0 AND is_transfer = 0
                           THEN 1 END) as uncategorized,
                COUNT(CASE WHEN is_payment = 0 AND is_transfer = 0 THEN 1 END) as tx_count
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN services svc ON t.service_id = svc.id
            JOIN statements s ON t.statement_id = s.id
            WHERE is_payment = 0 AND is_transfer = 0
              AND t.date >= ? AND t.date <= ?
              {extra_filters}
        """, params).fetchone()
        return {
            "total": round(row["total"] or 0, 2),
            "personal": round(row["personal"] or 0, 2),
            "moom": round(row["moom"] or 0, 2),
            "uncategorized": row["uncategorized"] or 0,
            "tx_count": row["tx_count"] or 0,
        }

    # Query reference month
    ref_data = query_month(ref_y, ref_m)

    # Query 3 prior months for rolling average
    avg_data = [query_month(y, m) for y, m in avg_months]
    n = len([d for d in avg_data if d["tx_count"] > 0]) or 1  # only months with data
    avg_total = round(sum(d["total"] for d in avg_data) / n, 2)
    avg_personal = round(sum(d["personal"] for d in avg_data) / n, 2)
    avg_moom = round(sum(d["moom"] for d in avg_data) / n, 2)

    conn.close()

    # Pick which spend to feature based on filter
    if moom_only:
        spend = ref_data["moom"]
        avg_spend = avg_moom
    elif personal_only:
        spend = ref_data["personal"]
        avg_spend = avg_personal
    else:
        spend = ref_data["total"]
        avg_spend = avg_total

    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    ref_label = f"{month_names[ref_m]} {ref_y}"

    return jsonify({
        "ref_month": f"{ref_y:04d}-{ref_m:02d}",
        "ref_label": ref_label,
        "spend": spend,
        "personal": ref_data["personal"],
        "moom": ref_data["moom"],
        "uncategorized": ref_data["uncategorized"],
        "tx_count": ref_data["tx_count"],
        "avg_spend": avg_spend,
        "avg_personal": avg_personal,
        "avg_moom": avg_moom,
        "avg_months": n,
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
        LEFT JOIN services svc ON t.service_id = svc.id
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
        LEFT JOIN services svc ON t.service_id = svc.id
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

    # Additional filters — single category (legacy) or multi-category
    category = request.args.get("category")
    categories_str = request.args.get("categories")

    if category == "__uncategorized__":
        filters += " AND t.category_id IS NULL"
    elif category:
        filters += " AND c.name = ?"
        params.append(category)
    elif categories_str:
        # Multi-category filter (from chart selection or multi-select dropdown)
        cat_list = [c.strip() for c in categories_str.split(",") if c.strip()]
        has_uncat = "__uncategorized__" in cat_list
        if has_uncat:
            cat_list.remove("__uncategorized__")
        if cat_list:
            placeholders = ",".join("?" * len(cat_list))
            cat_cond = f"(c.name IN ({placeholders}) OR COALESCE(p.name, c.name) IN ({placeholders}))"
            if has_uncat:
                filters += f" AND ({cat_cond} OR t.category_id IS NULL)"
            else:
                filters += f" AND {cat_cond}"
            params.extend(cat_list * 2)
        elif has_uncat:
            filters += " AND t.category_id IS NULL"

    # Chart-driven date narrowing (adds to dashboard date filter)
    chart_start = request.args.get("chart_start")
    if chart_start:
        filters += " AND t.date >= ?"
        params.append(chart_start)
    chart_end = request.args.get("chart_end")
    if chart_end:
        filters += " AND t.date <= ?"
        params.append(chart_end)

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
        "service": "svc.name",
        "account": "a.name",
        "amount": "t.amount_sgd",
    }
    order_col = valid_sorts.get(sort_col, "t.date")
    if sort_dir not in ("ASC", "DESC"):
        sort_dir = "DESC"

    # Count total (p join needed for multi-category COALESCE filter)
    count_row = conn.execute(f"""
        SELECT COUNT(*) as cnt
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN categories p ON c.parent_id = p.id
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
            a.name as account_name,
            t.service_id,
            svc.name as service_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN categories p ON c.parent_id = p.id
        LEFT JOIN services svc ON t.service_id = svc.id
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
        if tx.get("account_name"):
            tx["account_name"] = mask_card_number(tx["account_name"])
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

    exclude_one_off = args.get("exclude_one_off")
    if exclude_one_off == "true":
        filters += " AND (svc.is_one_off IS NULL OR svc.is_one_off = 0)"

    account_id = args.get("account_id")
    if account_id:
        filters += " AND s.account_id = ?"
        params.append(int(account_id))

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

                cat_id, svc_id = categorize_transaction(tx.description, conn, amount=tx.amount_sgd)

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
                    "service_id": svc_id,
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
            "account": mask_card_number(account_name),
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
                        "currency_foreign, category_id, service_id, is_payment, is_transfer, is_anomaly) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            statement_id,
                            tx["date"],
                            tx["description"],
                            tx["amount_sgd"],
                            tx.get("amount_foreign"),
                            tx.get("currency_foreign"),
                            tx.get("category_id"),
                            tx.get("service_id"),
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

    result = []
    for r in rows:
        accounts_raw = json.loads(r["accounts"]) if r["accounts"] else []
        result.append({
            "id": r["id"],
            "filenames": json.loads(r["filenames"]) if r["filenames"] else [],
            "accounts": [mask_card_number(a) for a in accounts_raw],
            "status": r["status"],
            "total_lines": r["total_lines"],
            "categorized_lines": r["categorized_lines"],
            "result": json.loads(r["result_json"]) if r["result_json"] else None,
            "created_at": r["created_at"],
        })
    return jsonify(result)


# ---------------------------------------------------------------------------
# Merchant Rules CRUD
# ---------------------------------------------------------------------------

@app.route("/api/rules")
def api_rules():
    """List all merchant rules with parent/sub category info."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT mr.id, mr.pattern, mr.match_type, mr.confidence,
               mr.priority, mr.min_amount, mr.max_amount,
               c.id as category_id, c.name as category_name,
               c.parent_id,
               p.name as parent_name
        FROM merchant_rules mr
        JOIN categories c ON mr.category_id = c.id
        LEFT JOIN categories p ON c.parent_id = p.id
        ORDER BY COALESCE(p.name, c.name), c.name, mr.priority DESC, mr.pattern
    """).fetchall()
    conn.close()
    return jsonify([{
        "id": r["id"],
        "pattern": r["pattern"],
        "match_type": r["match_type"],
        "confidence": r["confidence"],
        "priority": r["priority"],
        "min_amount": r["min_amount"],
        "max_amount": r["max_amount"],
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
            "INSERT INTO merchant_rules (pattern, category_id, match_type, confidence, priority, min_amount, max_amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                data["pattern"],
                data["category_id"],
                data.get("match_type", "contains"),
                "confirmed",
                data.get("priority", 0),
                data.get("min_amount"),
                data.get("max_amount"),
            ),
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
    if "priority" in data:
        sets.append("priority = ?")
        params.append(data["priority"])
    if "min_amount" in data:
        sets.append("min_amount = ?")
        params.append(data["min_amount"])
    if "max_amount" in data:
        sets.append("max_amount = ?")
        params.append(data["max_amount"])

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


@app.route("/api/rules/recategorize", methods=["POST"])
def api_rules_recategorize():
    """Re-run all merchant rules against existing transactions.

    Also syncs each rule's category_id to its service's category_id,
    so rules don't carry stale direct categories.
    """
    conn = get_connection()

    # Step 1: Sync rule categories to their service's category
    synced = conn.execute("""
        UPDATE merchant_rules SET category_id = s.category_id
        FROM services s
        WHERE merchant_rules.service_id = s.id
          AND s.category_id IS NOT NULL
          AND merchant_rules.category_id != s.category_id
    """).rowcount

    # Step 2: Re-run all rules against existing transactions
    rows = conn.execute("""
        SELECT id, description, amount_sgd, category_id, service_id
        FROM transactions
        WHERE is_payment = 0 AND is_transfer = 0
    """).fetchall()

    updated = 0
    unchanged = 0
    for tx in rows:
        new_cat, new_svc = categorize_transaction(tx["description"], conn, amount=tx["amount_sgd"])
        if new_cat and (new_cat != tx["category_id"] or new_svc != tx["service_id"]):
            conn.execute(
                "UPDATE transactions SET category_id = ?, service_id = ? WHERE id = ?",
                (new_cat, new_svc, tx["id"]),
            )
            updated += 1
        else:
            unchanged += 1

    conn.commit()
    conn.close()
    return jsonify({"updated": updated, "unchanged": unchanged, "rules_synced": synced})


# ---------------------------------------------------------------------------
# Subscriptions API
# ---------------------------------------------------------------------------

# FX rate cache
_fx_cache = {"rate": 1.35, "fetched_at": None}


def _get_usd_sgd_rate() -> float:
    """Get current USD→SGD rate with hourly caching."""
    import time
    now = time.time()
    if _fx_cache["fetched_at"] and (now - _fx_cache["fetched_at"]) < 3600:
        return _fx_cache["rate"]
    try:
        import urllib.request
        with urllib.request.urlopen("https://open.er-api.com/v6/latest/USD", timeout=5) as resp:
            data = json.loads(resp.read())
            rate = data["rates"]["SGD"]
            _fx_cache["rate"] = rate
            _fx_cache["fetched_at"] = now
            return rate
    except Exception:
        return _fx_cache["rate"]  # fallback


def _monthly_equivalent(amount: float, frequency: str, periods: int,
                        currency: str = "SGD", fx_rate: float = 1.0) -> float:
    """Convert billed amount to monthly SGD equivalent."""
    periods = periods or 1
    amount_sgd = amount * fx_rate if currency == "USD" else amount
    if frequency == "yearly":
        return amount_sgd / (12 * periods)
    elif frequency == "quarterly":
        return amount_sgd / (3 * periods)
    return amount_sgd / periods  # monthly


@app.route("/api/subscriptions")
def api_subscriptions():
    """List all subscriptions with category info and transaction enrichment."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT s.*, c.name as category_name, c.is_personal,
               p.name as parent_name,
               a.short_name as account_short_name, a.name as account_name,
               svc.name as service_name
        FROM subscriptions s
        LEFT JOIN categories c ON s.category_id = c.id
        LEFT JOIN categories p ON c.parent_id = p.id
        LEFT JOIN accounts a ON s.account_id = a.id
        LEFT JOIN services svc ON s.service_id = svc.id
        ORDER BY
            CASE s.status WHEN 'active' THEN 0 ELSE 1 END,
            s.renewal_date
    """).fetchall()

    fx_rate = _get_usd_sgd_rate()

    # Compute 90-day cutoff for rolling averages
    cutoff_90d = (date.today() - timedelta(days=90)).isoformat()

    result = []
    for r in rows:
        d = dict(r)
        pat = (d["match_pattern"] or "").upper()

        # Enrich from transactions if match_pattern exists
        if pat:
            # Most recent transaction match (with ID for linking)
            tx = conn.execute("""
                SELECT t.id, t.date, t.amount_sgd
                FROM transactions t
                WHERE UPPER(t.description) LIKE '%' || ? || '%'
                  AND t.is_payment = 0 AND t.is_transfer = 0
                  AND t.amount_sgd > 0
                ORDER BY t.date DESC LIMIT 1
            """, (pat,)).fetchone()
            if tx:
                d["tx_last_paid"] = tx["date"]
                d["tx_amount"] = round(tx["amount_sgd"], 2)
                d["tx_id"] = tx["id"]
            else:
                d["tx_last_paid"] = None
                d["tx_amount"] = None
                d["tx_id"] = None

            # Rolling average from monthly sums (handles split payments)
            monthly_sums = conn.execute("""
                SELECT SUBSTR(t.date, 1, 7) as ym, SUM(t.amount_sgd) as month_total
                FROM transactions t
                WHERE UPPER(t.description) LIKE '%' || ? || '%'
                  AND t.is_payment = 0 AND t.is_transfer = 0
                  AND t.amount_sgd > 0
                  AND t.date >= ?
                GROUP BY SUBSTR(t.date, 1, 7)
                ORDER BY ym DESC
            """, (pat, cutoff_90d)).fetchall()
            d["tx_months_90d"] = len(monthly_sums)
            if monthly_sums:
                totals = [r["month_total"] for r in monthly_sums]
                d["tx_avg_90d"] = round(sum(totals) / len(totals), 2)
            else:
                d["tx_avg_90d"] = None
        else:
            d["tx_last_paid"] = None
            d["tx_amount"] = None
            d["tx_id"] = None
            d["tx_avg_90d"] = None
            d["tx_months_90d"] = 0

        # Last paid amount from transactions: latest month's sum (handles split payments)
        if pat and d.get("tx_months_90d"):
            d["tx_amount"] = round(monthly_sums[0]["month_total"], 2)

        # Billed = configured amount per cycle (source of truth)
        amt = d.get("amount") or d.get("amount_sgd") or 0
        cur = d.get("currency") or "SGD"

        # Monthly equivalent from configured amount
        # Use 90d avg if variable (monthly sums vary >10%), otherwise from configured amount
        if d["tx_avg_90d"] and d["tx_months_90d"] >= 2:
            totals = [r["month_total"] for r in monthly_sums]
            mn, mx = min(totals), max(totals)
            if mx > mn * 1.10:
                d["is_variable"] = True
                d["monthly_sgd"] = round(d["tx_avg_90d"], 2)
            else:
                d["is_variable"] = False
                d["monthly_sgd"] = round(_monthly_equivalent(amt, d["frequency"], d["periods"], cur, fx_rate), 2)
        else:
            d["is_variable"] = False
            d["monthly_sgd"] = round(_monthly_equivalent(amt, d["frequency"], d["periods"], cur, fx_rate), 2)

        d["display_category"] = f"{d['parent_name']} > {d['category_name']}" if d["parent_name"] else (d["category_name"] or "")
        # Use live service name from JOIN, fall back to stale text field
        if d.get("service_name"):
            d["service"] = d["service_name"]
        d["fx_rate"] = fx_rate

        # Anchor-based renewal: advance from renewal_date anchor until future
        effective_last_paid = d.get("tx_last_paid") or d.get("last_paid")
        d["computed_renewal"] = _advance_renewal(
            d.get("renewal_date"), effective_last_paid, d["frequency"], d["periods"]
        )

        result.append(d)

    conn.close()
    return jsonify(result)


@app.route("/api/subscriptions", methods=["POST"])
def api_subscriptions_create():
    """Add a new subscription."""
    data = request.get_json()
    if not data or not data.get("service"):
        return jsonify({"error": "Service name is required"}), 400

    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO subscriptions
                (service, service_id, category_id, amount, currency, frequency, periods,
                 account_id, last_paid, renewal_date, status, link, notes, match_pattern)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["service"],
            data.get("service_id"),
            data.get("category_id"),
            data.get("amount", 0),
            data.get("currency", "SGD"),
            data.get("frequency", "monthly"),
            data.get("periods", 1),
            data.get("account_id"),
            data.get("last_paid"),
            data.get("renewal_date"),
            data.get("status", "active"),
            data.get("link"),
            data.get("notes"),
            data.get("match_pattern", data["service"].upper()),
        ))
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({"id": new_id, "success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/subscriptions/<int:sub_id>", methods=["PUT"])
def api_subscriptions_update(sub_id):
    """Update a subscription."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    allowed = ["service", "service_id", "category_id", "amount", "currency", "frequency",
               "periods", "account_id", "last_paid", "renewal_date", "status",
               "link", "notes", "match_pattern"]
    sets = []
    params = []
    for field in allowed:
        if field in data:
            sets.append(f"{field} = ?")
            params.append(data[field])

    if not sets:
        return jsonify({"error": "No fields to update"}), 400

    conn = get_connection()
    params.append(sub_id)
    conn.execute(f"UPDATE subscriptions SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/subscriptions/<int:sub_id>", methods=["DELETE"])
def api_subscriptions_delete(sub_id):
    """Delete a subscription."""
    conn = get_connection()
    conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/subscriptions/enrich", methods=["POST"])
def api_subscriptions_enrich():
    """Update all subscriptions with latest transaction data.

    Also auto-advances renewal_date when last_paid is past the current renewal.
    """
    conn = get_connection()
    subs = conn.execute(
        "SELECT id, match_pattern, frequency, periods, renewal_date, currency "
        "FROM subscriptions WHERE match_pattern IS NOT NULL"
    ).fetchall()
    updated = 0
    renewals_advanced = 0
    for s in subs:
        tx = conn.execute("""
            SELECT t.date, t.amount_sgd
            FROM transactions t
            WHERE UPPER(t.description) LIKE '%' || ? || '%'
              AND t.is_payment = 0 AND t.is_transfer = 0
              AND t.amount_sgd > 0
            ORDER BY t.date DESC LIMIT 1
        """, (s["match_pattern"].upper(),)).fetchone()
        if tx:
            new_renewal = _advance_renewal(
                s["renewal_date"], tx["date"], s["frequency"], s["periods"]
            )
            # Update last_paid and renewal; only update amount for SGD subs
            # (USD subs keep their configured USD amount — the SGD tx amount is a conversion)
            if (s["currency"] or "SGD") == "SGD":
                conn.execute(
                    "UPDATE subscriptions SET last_paid = ?, amount = ?, renewal_date = ? WHERE id = ?",
                    (tx["date"], round(tx["amount_sgd"], 2), new_renewal, s["id"]),
                )
            else:
                conn.execute(
                    "UPDATE subscriptions SET last_paid = ?, renewal_date = ? WHERE id = ?",
                    (tx["date"], new_renewal, s["id"]),
                )
            updated += 1
            if new_renewal != s["renewal_date"]:
                renewals_advanced += 1
    conn.commit()
    conn.close()
    return jsonify({"success": True, "updated": updated, "renewals_advanced": renewals_advanced})


def _advance_renewal(
    current_renewal: str | None,
    last_paid: str | None,
    frequency: str,
    periods: int,
) -> str | None:
    """Anchor-based renewal: advance renewal_date forward by frequency until it's
    in the future relative to TODAY (not last_paid).

    This prevents payment delays from skewing the renewal calendar.
    If no renewal anchor exists, seed from last_paid + one cycle.
    """
    today = date.today()

    if not current_renewal:
        if not last_paid:
            return None
        # No anchor — seed from last_paid + one cycle
        renewal = _add_billing_period(date.fromisoformat(last_paid), frequency, periods)
    else:
        renewal = date.fromisoformat(current_renewal)

    # Step forward until renewal is in the future
    while renewal <= today:
        renewal = _add_billing_period(renewal, frequency, periods)

    return renewal.isoformat()


def _add_billing_period(d: date, frequency: str, periods: int) -> date:
    """Add one billing period to a date."""
    periods = periods or 1
    if frequency == "yearly":
        return d.replace(year=d.year + periods)
    elif frequency == "quarterly":
        month = d.month + (3 * periods)
        year = d.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(d.day, 28)  # Safe day for all months
        return d.replace(year=year, month=month, day=day)
    else:  # monthly
        month = d.month + periods
        year = d.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(d.day, 28)
        return d.replace(year=year, month=month, day=day)


@app.route("/api/fx-rate")
def api_fx_rate():
    """Get current USD→SGD exchange rate."""
    return jsonify({"usd_sgd": _get_usd_sgd_rate()})


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
