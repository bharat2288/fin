"""fin — Personal Finance Tracker

Flask backend serving the 4-tab SPA (Dashboard, Import, History, Merchant Rules)
and API endpoints for statement parsing, categorization, and visualization.

Usage:
    py app.py                  # Start on port 8450
    py app.py --port 8450      # Explicit port
"""

import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from flask import Flask, jsonify, request, send_from_directory

from db import get_connection, init_db, categorize_transaction, invalidate_rules_cache


@contextmanager
def get_db():
    """Context manager wrapping get_connection() for automatic cleanup."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

app = Flask(__name__, static_folder="static", static_url_path="/static")


def format_category_display(parent: str | None, child: str | None) -> str:
    """Format 'Parent > Child' category display string."""
    if parent:
        return f"{parent} > {child}"
    return child or ""


def category_scope_expr(cat_alias: str = "c", parent_alias: str = "p") -> str:
    """Return SQL CASE expression deriving scope from a category's root."""
    return (
        "CASE "
        f"WHEN {cat_alias}.name = 'Kalesh' OR {parent_alias}.name = 'Kalesh' THEN 'kalesh' "
        f"WHEN COALESCE({parent_alias}.is_personal, {cat_alias}.is_personal, 1) = 0 THEN 'moom' "
        "ELSE 'personal' END"
    )


def _requested_scope(args) -> str | None:
    """Resolve the requested scope from new or legacy query params."""
    scope = (args.get("scope") or "").strip().lower()
    if scope in {"personal", "moom", "kalesh"}:
        return scope
    if args.get("personal_only") == "true":
        return "personal"
    if args.get("moom_only") == "true":
        return "moom"
    if args.get("kalesh_only") == "true":
        return "kalesh"
    return None


def _build_match_condition(match_type: str) -> str:
    """Return SQL WHERE fragment for merchant rule matching."""
    if match_type == "contains":
        return "UPPER(description) LIKE '%' || ? || '%'"
    elif match_type == "startswith":
        return "UPPER(description) LIKE ? || '%'"
    return "UPPER(description) = ?"


_GENERIC_TRANSFER_PATTERNS = {
    "PAYNOW",
    "PAYNOW TRANSFER",
    "ICT PAYNOW",
    "ICT PAYNOW TRANSFER",
    "TOP-UP TO PAYLAH",
    "TOP UP TO PAYLAH",
    "MAXED OUT FROM PAYLAH",
    "TRANSFER",
    "BANK TRANSFER",
    "I-BANK",
}


def _normalize_pattern_text(text: str | None) -> str:
    """Normalize a rule pattern or description fragment for guard checks."""
    return re.sub(r"\s+", " ", (text or "").upper()).strip()


def _looks_transfer_like_description(description: str | None) -> bool:
    """Return True when a raw description is rail-like, not merchant-like."""
    normalized = _normalize_pattern_text(description)
    if not normalized:
        return False
    if any(token in normalized for token in ("PAYNOW", "PAYLAH", "I-BANK", ":IB")):
        return True
    if re.match(r"^FT\d+[A-Z0-9-]*", normalized):
        return True
    return False


def _is_generic_rule_pattern(pattern: str | None) -> bool:
    """Return True for reusable rules that are too generic to be safe."""
    normalized = _normalize_pattern_text(pattern)
    if not normalized:
        return False
    if normalized in _GENERIC_TRANSFER_PATTERNS:
        return True
    if re.fullmatch(r"FT\d+[A-Z0-9:-]*", normalized):
        return True
    if normalized.startswith("DBSC-") and "I-BANK" in normalized:
        return True
    return False


def _rule_pattern_error(pattern: str | None) -> str | None:
    """Return a user-facing validation error for unsafe rule patterns."""
    if _is_generic_rule_pattern(pattern):
        return (
            "Pattern is too generic for PayNow/transfer traffic. "
            "Leave it blank for a one-off resolution or use a specific counterparty name."
        )
    return None


def _paynow_fallback_category_id(description: str | None, conn) -> int | None:
    """Return fallback category_id for PayNow descriptions when no rule matches."""
    if not description or "PAYNOW" not in description.upper():
        return None
    from ingest import categorize_bank_paynow
    _, cat_name = categorize_bank_paynow(description)
    if not cat_name:
        return None
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (cat_name,)).fetchone()
    return row["id"] if row else None


def _classify_flow_for_tx(
    conn,
    description: str,
    amount_sgd: float,
    category_id: int | None,
    *,
    flow_ctx=None,
    cats_by_id: dict[int, str] | None = None,
) -> str:
    """Classify a transaction using the shared flow_type model."""
    from flow import build_context, classify_flow

    if flow_ctx is None:
        flow_ctx = build_context(conn)
    if cats_by_id is None:
        cats_by_id = {
            row["id"]: row["name"]
            for row in conn.execute("SELECT id, name FROM categories").fetchall()
        }
    return classify_flow(
        {
            "description": description,
            "amount_sgd": amount_sgd,
            "category_name": cats_by_id.get(category_id) if category_id else None,
        },
        flow_ctx,
    )


def _expense_visibility_filter(service_alias: str = "svc") -> str:
    """SQL clause excluding services hidden from dashboard and expense tables."""
    return f"({service_alias}.exclude_from_expense_views IS NULL OR {service_alias}.exclude_from_expense_views = 0)"


def _crud_insert(conn, sql: str, params: tuple, entity: str, post_commit=None) -> tuple:
    """Execute an INSERT, commit, return (response, status_code).

    On success returns ({"id": N, "success": True}, 200).
    On failure returns ({"error": "..."}, 400).
    post_commit is called after commit if provided (e.g. cache invalidation).
    """
    try:
        cur = conn.execute(sql, params)
        new_id = cur.lastrowid
        conn.commit()
        if post_commit:
            post_commit()
        return jsonify({"id": new_id, "success": True}), 200
    except Exception as e:
        app.logger.warning("Failed to create %s: %s", entity, e)
        return jsonify({"error": f"Failed to create {entity}"}), 400


def _build_update_sets(data: dict | None, allowed: list[str]) -> tuple[list[str], list]:
    """Build SET clause fragments and params from allowed fields present in data."""
    if not data:
        return [], []
    sets = []
    params = []
    for field in allowed:
        if field in data:
            sets.append(f"{field} = ?")
            params.append(data[field])
    return sets, params


def _crud_update(table: str, entity_id: int, data: dict | None, allowed: list[str], post_commit=None):
    """Validate and execute a simple UPDATE by allowed field list.

    Returns Flask response tuple. Handles empty/no-fields-to-update errors.
    """
    sets, params = _build_update_sets(data, allowed)
    if not sets:
        return jsonify({"error": "No fields to update"}), 400
    with get_db() as conn:
        params.append(entity_id)
        conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        if post_commit:
            post_commit()
    return jsonify({"success": True})


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
    with get_db() as conn:
        rows = conn.execute(
            "SELECT c.id, c.name, c.parent_id, c.is_personal, p.name as parent_name, "
            f"{category_scope_expr('c', 'p')} as scope "
            "FROM categories c LEFT JOIN categories p ON c.parent_id = p.id "
            "ORDER BY COALESCE(p.name, c.name), c.parent_id IS NOT NULL, c.name"
        ).fetchall()
    return jsonify([{
        "id": r["id"],
        "name": r["name"],
        "parent_id": r["parent_id"],
        "parent_name": r["parent_name"],
        "is_personal": r["is_personal"],
        "scope": r["scope"],
        "display_name": format_category_display(r["parent_name"], r["name"]),
    } for r in rows])


@app.route("/api/categories", methods=["POST"])
def api_categories_create():
    """Create a new category or subcategory."""
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Name is required"}), 400

    with get_db() as conn:
        return _crud_insert(
            conn,
            "INSERT INTO categories (name, parent_id, is_personal) VALUES (?, ?, ?)",
            (data["name"], data.get("parent_id"), data.get("is_personal", 1)),
            "category",
        )


@app.route("/api/accounts")
def api_accounts():
    """List all accounts."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, short_name, type, last_four, currency, status FROM accounts ORDER BY name"
        ).fetchall()
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

    with get_db() as conn:
        return _crud_insert(
            conn,
            "INSERT INTO accounts (name, short_name, type, last_four, currency) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                data["name"],
                data.get("short_name", data["name"]),
                data.get("type", "credit_card"),
                data.get("last_four"),
                data.get("currency", "SGD"),
            ),
            "account",
        )


@app.route("/api/accounts/<int:acct_id>", methods=["PUT"])
def api_accounts_update(acct_id):
    """Update an account."""
    return _crud_update("accounts", acct_id, request.get_json(),
                        ["name", "short_name", "type", "last_four", "currency", "status"])


@app.route("/api/accounts/<int:acct_id>", methods=["DELETE"])
def api_accounts_delete(acct_id):
    """Delete an account. Refuses if statements reference it."""
    with get_db() as conn:
        stmt_count = conn.execute(
            "SELECT COUNT(*) FROM statements WHERE account_id = ?", (acct_id,)
        ).fetchone()[0]
        if stmt_count > 0:
            return jsonify({
                "error": f"Cannot delete: {stmt_count} statement(s) reference this account"
            }), 400

        conn.execute("DELETE FROM accounts WHERE id = ?", (acct_id,))
        conn.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Services API
# ---------------------------------------------------------------------------

@app.route("/api/services")
def api_services():
    """List all services with category info and transaction/rule counts."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.*, c.name as category_name, c.is_personal,
                   """ + category_scope_expr("c", "p") + """ as scope,
                   p.name as parent_name,
                   (SELECT COUNT(*) FROM transactions t WHERE t.service_id = s.id
                    AND COALESCE(t.flow_type, 'expense') NOT IN ('transfer', 'payment')) as txn_count,
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
        d["display_category"] = format_category_display(d["parent_name"], d["category_name"])
        d["rules"] = rules_by_svc.get(d["id"], [])
        result.append(d)
    return jsonify(result)


@app.route("/api/services/bulk-rename", methods=["POST"])
def api_services_bulk_rename():
    """Bulk rename services. Body: { renames: [{id, name}, ...] }"""
    data = request.get_json()
    renames = data.get("renames", [])
    if not renames:
        return jsonify({"error": "No renames provided"}), 400

    with get_db() as conn:
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
    return jsonify({"updated": updated, "errors": errors})


@app.route("/api/services", methods=["POST"])
def api_services_create():
    """Create a new service."""
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Service name is required"}), 400
    with get_db() as conn:
        return _crud_insert(
            conn,
            "INSERT INTO services (name, category_id, notes, exclude_from_expense_views) VALUES (?, ?, ?, ?)",
            (
                data["name"],
                data.get("category_id"),
                data.get("notes"),
                data.get("exclude_from_expense_views", 0),
            ),
            "service",
        )


@app.route("/api/services/<int:svc_id>", methods=["PUT"])
def api_services_update(svc_id):
    """Update a service. Auto re-categorizes affected transactions on category change."""
    data = request.get_json()
    with get_db() as conn:
        sets, params = _build_update_sets(
            data,
            ["name", "category_id", "notes", "is_one_off", "exclude_from_expense_views"],
        )
        if not sets:
            return jsonify({"error": "Nothing to update"}), 400
        params.append(svc_id)
        try:
            conn.execute(f"UPDATE services SET {', '.join(sets)} WHERE id = ?", params)

            # Auto re-categorize: if category changed, update all linked transactions
            recategorized = 0
            if "category_id" in data:
                new_cat_id = data["category_id"]
                cur = conn.execute(
                    "UPDATE transactions SET category_id = ? "
                    "WHERE service_id = ? AND category_id != ? "
                    "AND COALESCE(cat_source, 'auto') IN ('auto', 'service_default')",
                    (new_cat_id, svc_id, new_cat_id),
                )
                recategorized = cur.rowcount

            conn.commit()
            invalidate_rules_cache()  # service category change affects cached category_id
            return jsonify({"success": True, "recategorized": recategorized})
        except Exception as e:
            app.logger.warning("Failed to update service: %s", e)
            return jsonify({"error": "Failed to update service"}), 400


@app.route("/api/services/<int:svc_id>/merge", methods=["POST"])
def api_services_merge(svc_id):
    """Merge source service into target: reassign all FKs, delete source."""
    data = request.get_json()
    target_id = data.get("target_id")
    if not target_id or int(target_id) == svc_id:
        return jsonify({"error": "Invalid merge target"}), 400
    target_id = int(target_id)

    with get_db() as conn:
        # Verify both exist
        source = conn.execute("SELECT name FROM services WHERE id = ?", (svc_id,)).fetchone()
        target = conn.execute("SELECT name FROM services WHERE id = ?", (target_id,)).fetchone()
        if not source or not target:
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
        invalidate_rules_cache()  # merge reassigns rule service_ids

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
    with get_db() as conn:
        refs = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE service_id = ?", (svc_id,)
        ).fetchone()[0]
        sub_refs = conn.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE service_id = ?", (svc_id,)
        ).fetchone()[0]
        if refs > 0 or sub_refs > 0:
            return jsonify({"error": f"Service has {refs} transactions and {sub_refs} subscriptions"}), 400
        conn.execute("DELETE FROM services WHERE id = ?", (svc_id,))
        conn.commit()
    return jsonify({"success": True})


@app.route("/api/services/<int:svc_id>/transactions")
def api_service_transactions(svc_id):
    """Get all transactions for a specific service."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.id, t.date, t.description, t.amount_sgd,
                   t.amount_foreign, t.currency_foreign,
                   c.name as category_name
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.service_id = ?
              AND COALESCE(t.flow_type, 'expense') NOT IN ('transfer', 'payment')
            ORDER BY t.date DESC
        """, (svc_id,)).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Dashboard API
# ---------------------------------------------------------------------------

@app.route("/api/dashboard/stat-cards")
def api_dashboard_stat_cards():
    """Stat cards: single month spend + delta vs 3-month rolling average.

    Auto-picks reference month using the 15th rule:
      - If today >= 15th, ref = previous month
      - If today < 15th, ref = two months ago
    Override with ?ref_month=YYYY-MM.

    Respects: scope, personal_only, moom_only, kalesh_only, exclude_one_off, account_id
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

    # 3-month avg: the 3 months before ref_month
    avg_months = []
    ay, am = ref_y, ref_m
    for _ in range(3):
        am -= 1
        if am == 0:
            am, ay = 12, ay - 1
        avg_months.append((ay, am))
    avg_months.reverse()  # chronological order

    scope = _requested_scope(request.args)
    account_id = request.args.get("account_id")
    exclude_one_off = request.args.get("exclude_one_off") == "true"

    extra_filters = ""
    extra_params = []
    extra_filters += f" AND {_expense_visibility_filter('svc')}"
    if exclude_one_off:
        # Exclude both transaction-level and service-level one-offs
        extra_filters += " AND t.is_one_off = 0 AND (svc.is_one_off IS NULL OR svc.is_one_off = 0)"
    if account_id:
        try:
            extra_filters += " AND s.account_id = ?"
            extra_params.append(int(account_id))
        except (ValueError, TypeError):
            pass

    with get_db() as conn:
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
                    SUM(CASE WHEN t.flow_type IN ('expense', 'refund')
                             THEN amount_sgd ELSE 0 END) as total,
                    SUM(CASE WHEN t.flow_type IN ('expense', 'refund')
                             AND {category_scope_expr('c', 'p')} = 'personal' THEN amount_sgd ELSE 0 END) as personal,
                    SUM(CASE WHEN t.flow_type IN ('expense', 'refund')
                             AND {category_scope_expr('c', 'p')} = 'moom' THEN amount_sgd ELSE 0 END) as moom,
                    SUM(CASE WHEN t.flow_type IN ('expense', 'refund')
                             AND {category_scope_expr('c', 'p')} = 'kalesh' THEN amount_sgd ELSE 0 END) as kalesh,
                    COUNT(CASE WHEN t.category_id IS NULL AND t.flow_type IN ('expense', 'refund')
                               THEN 1 END) as uncategorized,
                    COUNT(CASE WHEN t.flow_type IN ('expense', 'refund') THEN 1 END) as tx_count
                FROM transactions t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN categories p ON c.parent_id = p.id
                LEFT JOIN services svc ON t.service_id = svc.id
                JOIN statements s ON t.statement_id = s.id
                WHERE t.flow_type IN ('expense', 'refund')
                  AND t.date >= ? AND t.date <= ?
                  {extra_filters}
            """, params).fetchone()
            return {
                "total": round(row["total"] or 0, 2),
                "personal": round(row["personal"] or 0, 2),
                "moom": round(row["moom"] or 0, 2),
                "kalesh": round(row["kalesh"] or 0, 2),
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
        avg_kalesh = round(sum(d["kalesh"] for d in avg_data) / n, 2)

    # Pick which spend to feature based on filter
    if scope == "moom":
        spend = ref_data["moom"]
        avg_spend = avg_moom
    elif scope == "kalesh":
        spend = ref_data["kalesh"]
        avg_spend = avg_kalesh
    elif scope == "personal":
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
        "kalesh": ref_data["kalesh"],
        "uncategorized": ref_data["uncategorized"],
        "tx_count": ref_data["tx_count"],
        "avg_spend": avg_spend,
        "avg_personal": avg_personal,
        "avg_moom": avg_moom,
        "avg_kalesh": avg_kalesh,
        "avg_months": n,
    })


@app.route("/api/dashboard/monthly")
def api_dashboard_monthly():
    """Spending by category over time for stacked bar chart.

    Query params: start, end, scope, personal_only, moom_only, kalesh_only, exclude_one_off, granularity
    granularity: 'monthly' (default), 'weekly', 'quarterly'
    """
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

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT
                {time_bucket} as period,
                {cat_expr} as category,
                {category_scope_expr('c', 'p')} as scope,
                SUM(t.amount_sgd) as total
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN categories p ON c.parent_id = p.id
            LEFT JOIN services svc ON t.service_id = svc.id
            JOIN statements s ON t.statement_id = s.id
            WHERE t.flow_type IN ('expense', 'refund') {filters}
            GROUP BY period, category
            ORDER BY period, total DESC
        """, params).fetchall()

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

    Query params: start, end, scope, personal_only, moom_only, kalesh_only, exclude_one_off, group_parent
    """
    filters, params = _build_filters(request.args)

    group_parent = request.args.get("group_parent", "true") == "true"

    if group_parent:
        cat_expr = "COALESCE(p.name, c.name)"
    else:
        cat_expr = "c.name"

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT
                {cat_expr} as category,
                {category_scope_expr('c', 'p')} as scope,
                SUM(t.amount_sgd) as total,
                COUNT(*) as count
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN categories p ON c.parent_id = p.id
            LEFT JOIN services svc ON t.service_id = svc.id
            JOIN statements s ON t.statement_id = s.id
            WHERE t.flow_type IN ('expense', 'refund') {filters}
            GROUP BY category
            ORDER BY total DESC
        """, params).fetchall()

    return jsonify([{
        "category": r["category"] or "Other",
        "scope": r["scope"],
        "is_personal": 1 if r["scope"] == "personal" else 0,
        "total": round(r["total"], 2),
        "count": r["count"],
    } for r in rows])


# ---------------------------------------------------------------------------
# Transactions API
# ---------------------------------------------------------------------------

@app.route("/api/transactions")
def api_transactions():
    """Paginated transaction list with filters.

    Query params: start, end, scope, personal_only, moom_only, kalesh_only, exclude_one_off,
                  category, account_id, month, page, per_page, search
    """
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

    if request.args.get("expense_only") == "true":
        filters += " AND t.flow_type IN ('expense', 'refund')"

    month = request.args.get("month")
    if month:
        filters += " AND strftime('%Y-%m', t.date) = ?"
        params.append(month)

    search = request.args.get("search")
    if search:
        filters += " AND (t.description LIKE ? OR svc.name LIKE ? OR c.name LIKE ? OR p.name LIKE ?)"
        search_param = f"%{search}%"
        params.extend([search_param] * 4)

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

    with get_db() as conn:
        # Count total (p join needed for multi-category COALESCE filter)
        count_row = conn.execute(f"""
            SELECT COUNT(*) as cnt
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN categories p ON c.parent_id = p.id
            LEFT JOIN services svc ON t.service_id = svc.id
            JOIN statements s ON t.statement_id = s.id
            WHERE 1=1 {filters}
        """, params).fetchone()

        # Fetch page — include parent category for "Parent > Sub" display
        rows = conn.execute(
            f"""
            SELECT
                t.id, t.date, t.description, t.amount_sgd,
                t.amount_foreign, t.currency_foreign,
                c.name as category, c.is_personal,
                p.name as parent_category,
                {category_scope_expr("c", "p")} as scope,
                t.is_one_off, COALESCE(t.flow_type, 'expense') as flow_type, t.flow_type_manual,
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
            """,
            params + [per_page, offset],
        ).fetchall()

    txns = []
    for r in rows:
        tx = dict(r)
        tx["display_category"] = format_category_display(r["parent_category"], r["category"])
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
    """Update a transaction's notes, category, or one-off flag."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    sets, values = _build_update_sets(data, ["notes", "category_id", "is_one_off"])

    # If category or service is being changed, mark as manual
    if "category_id" in data or "service_id" in data:
        sets.append("cat_source = 'manual'")

    if not sets:
        return jsonify({"error": "No valid fields to update"}), 400

    with get_db() as conn:
        values.append(tx_id)
        conn.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()
    return jsonify({"ok": True, "id": tx_id})


@app.route("/api/transactions/resolve", methods=["POST"])
def api_resolve_transaction():
    """Resolve an uncategorized transaction: find-or-create service, create rule, update tx.

    Accepts:
        tx_id: transaction ID to resolve
        service_name: existing or new service name
        service_id: existing service ID (optional — if provided, service_name ignored for lookup)
        category_id: category for the service (used only when creating new service)
        pattern: merchant rule pattern (auto-suggested from description)
        match_type: 'contains' (default) or 'startswith'

    Flow:
        1. Find existing service by service_id or name, or create new one
        2. Create merchant rule linking pattern → service → category
        3. Update the transaction with service_id + category_id
        4. Backfill any other NULL-service transactions matching the new rule
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    tx_id = data.get("tx_id")
    service_name = (data.get("service_name") or "").strip()
    service_id = data.get("service_id")
    category_id = data.get("category_id")
    pattern = (data.get("pattern") or "").strip()
    match_type = data.get("match_type", "contains")
    apply_scope = data.get("apply_scope") or "service_default"

    if not tx_id:
        return jsonify({"error": "tx_id required"}), 400
    if not service_name and not service_id:
        return jsonify({"error": "service_name or service_id required"}), 400
    if apply_scope == "rule" and not pattern:
        return jsonify({"error": "pattern required for rule override"}), 400
    pattern_error = _rule_pattern_error(pattern)
    if pattern and apply_scope in {"rule", "service_default"} and pattern_error:
        return jsonify({"error": pattern_error}), 400

    with get_db() as conn:
        try:
            # Step 1: Resolve service — find existing or create new
            if service_id:
                svc = conn.execute(
                    "SELECT id, category_id FROM services WHERE id = ?", (service_id,)
                ).fetchone()
                if not svc:
                    return jsonify({"error": f"Service ID {service_id} not found"}), 404
                service_id = svc["id"]
                # Service-default resolution can update the service category.
                if apply_scope == "service_default" and category_id and category_id != svc["category_id"]:
                    conn.execute("UPDATE services SET category_id = ? WHERE id = ?",
                                 (category_id, service_id))
                else:
                    category_id = category_id or svc["category_id"]
            else:
                # Look up by name (case-insensitive)
                existing = conn.execute(
                    "SELECT id, category_id FROM services WHERE UPPER(name) = ?",
                    (service_name.upper(),),
                ).fetchone()
                if existing:
                    service_id = existing["id"]
                    if apply_scope == "service_default" and category_id and category_id != existing["category_id"]:
                        conn.execute("UPDATE services SET category_id = ? WHERE id = ?",
                                     (category_id, service_id))
                    else:
                        category_id = category_id or existing["category_id"]
                else:
                    # Create new service — category_id is required
                    if not category_id:
                        return jsonify({"error": "category_id required for new service"}), 400
                    conn.execute(
                        "INSERT INTO services (name, category_id) VALUES (?, ?)",
                        (service_name, category_id),
                    )
                    service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Step 2: Create merchant rule if pattern provided (optional for PayNow/transfers)
            rule_id = None
            backfilled = 0
            if pattern and apply_scope in {"rule", "service_default"}:
                rule_exists = conn.execute(
                    "SELECT id FROM merchant_rules WHERE UPPER(pattern) = ?",
                    (pattern.upper(),),
                ).fetchone()
                override_category_id = category_id if apply_scope == "rule" else None
                if not rule_exists:
                    conn.execute(
                        "INSERT INTO merchant_rules (pattern, service_id, category_override_id, match_type, confidence) "
                        "VALUES (?, ?, ?, ?, 'confirmed')",
                        (pattern, service_id, override_category_id, match_type),
                    )
                    rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                else:
                    rule_id = rule_exists["id"]
                    conn.execute(
                        "UPDATE merchant_rules SET service_id = ?, category_override_id = ? WHERE id = ?",
                        (service_id, override_category_id, rule_id),
                    )

                # Backfill other transactions matching this pattern with NULL service
                match_cond = _build_match_condition(match_type)
                cur = conn.execute(
                    f"UPDATE transactions SET service_id = ?, category_id = ?, cat_source = ? "
                    f"WHERE service_id IS NULL AND {match_cond}",
                    (
                        service_id,
                        category_id,
                        "rule_override" if apply_scope == "rule" else "service_default",
                        pattern.upper(),
                    ),
                )
                backfilled = cur.rowcount

            # Step 3: Update the target transaction with explicit provenance.
            # flow_type override is independent of category resolution (ADR v2):
            #   - if caller supplies flow_type, set it + mark flow_type_manual=1
            #   - otherwise leave flow_type alone
            tx_cat_source = {
                "transaction": "manual",
                "rule": "rule_override",
                "service_default": "service_default",
            }.get(apply_scope, "manual")
            flow_type = data.get("flow_type")
            tx_row = conn.execute(
                "SELECT description, amount_sgd, flow_type_manual FROM transactions WHERE id = ?",
                (tx_id,),
            ).fetchone()
            if flow_type is not None:
                if flow_type not in ("expense", "income", "transfer", "payment", "refund"):
                    return jsonify({"error": f"invalid flow_type: {flow_type}"}), 400
                conn.execute(
                    "UPDATE transactions SET category_id = ?, service_id = ?, cat_source = ?, "
                    "flow_type = ?, flow_type_manual = 1 WHERE id = ?",
                    (category_id, service_id, tx_cat_source, flow_type, tx_id),
                )
            elif tx_row and not tx_row["flow_type_manual"]:
                flow_type = _classify_flow_for_tx(
                    conn,
                    tx_row["description"],
                    tx_row["amount_sgd"],
                    category_id,
                )
                conn.execute(
                    "UPDATE transactions SET category_id = ?, service_id = ?, cat_source = ?, "
                    "flow_type = ? WHERE id = ?",
                    (category_id, service_id, tx_cat_source, flow_type, tx_id),
                )
            else:
                conn.execute(
                    "UPDATE transactions SET category_id = ?, service_id = ?, cat_source = ? WHERE id = ?",
                    (category_id, service_id, tx_cat_source, tx_id),
                )

            conn.commit()
            invalidate_rules_cache()
            return jsonify({
                "success": True,
                "service_id": service_id,
                "rule_id": rule_id,
                "category_id": category_id,
                "backfilled": backfilled,
            })
        except Exception as e:
            app.logger.warning("Failed to resolve transaction: %s", e)
            return jsonify({"error": "Failed to resolve transaction"}), 400


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

    scope = _requested_scope(args)
    if scope:
        filters += f" AND {category_scope_expr('c', 'p')} = ?"
        params.append(scope)

    exclude_one_off = args.get("exclude_one_off")
    if exclude_one_off == "true":
        # Exclude both transaction-level and service-level one-offs
        filters += " AND t.is_one_off = 0 AND (svc.is_one_off IS NULL OR svc.is_one_off = 0)"

    filters += f" AND {_expense_visibility_filter('svc')}"

    account_id = args.get("account_id")
    if account_id:
        try:
            filters += " AND s.account_id = ?"
            params.append(int(account_id))
        except (ValueError, TypeError):
            pass

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

    all_groups = {}  # account_name -> list of transaction dicts
    errors = []
    filenames = []

    # Save uploaded files to temp dir for parsing
    with get_db() as conn, tempfile.TemporaryDirectory() as tmpdir:
        saved_paths = []
        for f in files:
            if not f.filename:
                continue
            safe_name = f.filename
            save_path = os.path.join(tmpdir, safe_name)
            f.save(save_path)
            saved_paths.append((save_path, safe_name))
            filenames.append(safe_name)

        # Detect and parse each file via parser registry
        from parsers import auto_detect_and_parse, handle_vantage_split
        parsed_statements = []
        for save_path, filename in saved_paths:
            try:
                stmts = auto_detect_and_parse(save_path)
                parsed_statements.extend(stmts)
            except Exception as e:
                errors.append({"file": filename, "error": str(e)})

        # Handle Vantage MK/BS split if both exports present
        parsed_statements = handle_vantage_split(parsed_statements)

        # Post-parse: classify flow_type for every transaction (ADR v2).
        # Single shared classifier — parsers are fact-extractors only.
        from flow import build_context, classify_flow
        flow_ctx = build_context(conn)

        # Group transactions by account and categorize
        cats = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM categories").fetchall()}
        cats_by_name = {v: k for k, v in cats.items()}
        svcs = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM services").fetchall()}

        for stmt in parsed_statements:
            for tx in stmt.transactions:
                account = tx.card_info or stmt.accounts[0] if stmt.accounts else "Unknown"

                cat_id, svc_id, cat_source = categorize_transaction(
                    tx.description,
                    conn,
                    amount=tx.amount_sgd,
                )

                # For bank statements, try PayNow rules
                if cat_id is None:
                    paynow_cat_id = _paynow_fallback_category_id(tx.description, conn)
                    if paynow_cat_id:
                        cat_id = paynow_cat_id
                        cat_source = "fallback"

                # Classify flow_type post-parse with category context
                tx.flow_type = classify_flow(
                    {
                        "description": tx.description,
                        "amount_sgd": tx.amount_sgd,
                        "category_name": cats.get(cat_id) if cat_id else None,
                    },
                    flow_ctx,
                )

                entry = {
                    "date": tx.date,
                    "description": tx.description,
                    "amount_sgd": tx.amount_sgd,
                    "amount_foreign": tx.amount_foreign,
                    "currency_foreign": tx.currency_foreign,
                    "category_id": cat_id,
                    "service_id": svc_id,
                    "category_name": cats.get(cat_id) if cat_id else None,
                    "service_name": svcs.get(svc_id) if svc_id else None,
                    "cat_source": cat_source,
                    "flow_type": tx.flow_type,
                    "account": account,
                    "status": "categorized" if cat_id else (
                        "transfer" if tx.flow_type == "transfer"
                        else "payment" if tx.flow_type == "payment"
                        else "uncategorized"
                    ),
                    # Default-skip: transfers + CC payments (non-spend events)
                    "_skip": tx.flow_type in ("transfer", "payment"),
                }

                if account not in all_groups:
                    all_groups[account] = []
                all_groups[account].append(entry)

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

    # Save preview to batch_imports and fetch services list in one connection
    with get_db() as conn:
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

        services_rows = conn.execute(
            "SELECT s.id, s.name, s.category_id, COALESCE(c.name, '') as category_name "
            "FROM services s LEFT JOIN categories c ON s.category_id = c.id "
            "ORDER BY s.name"
        ).fetchall()
    services_list = [
        {"id": r["id"], "name": r["name"], "category_id": r["category_id"], "category_name": r["category_name"]}
        for r in services_rows
    ]

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
        "services": services_list,
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

    total_saved = 0
    total_duplicates = 0
    accounts_created = []
    rules_skipped_generic = 0

    with get_db() as conn:
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

                # Create per-month statement records so coverage matrix reflects each month.
                # A multi-month CSV (e.g. Citi Oct-Dec) creates 3 statement records.
                filename = f"import_{import_id}_{account_name[:30]}"
                month_stmt_ids: dict[str, int] = {}
                for tx in active_txns:
                    ym = tx["date"][:7] if tx.get("date") else datetime.now().strftime("%Y-%m")
                    if ym not in month_stmt_ids:
                        stmt_date = f"{ym}-01"
                        sid, _ = ensure_statement(conn, account_id, stmt_date, filename)
                        month_stmt_ids[ym] = sid

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

                    # Link each transaction to its month's statement record
                    for tx in import_by_key[key][:to_insert]:
                        tx_month = tx["date"][:7] if tx.get("date") else datetime.now().strftime("%Y-%m")
                        statement_id = month_stmt_ids[tx_month]
                        conn.execute(
                            "INSERT INTO transactions "
                            "(statement_id, date, description, amount_sgd, amount_foreign, "
                            "currency_foreign, category_id, service_id, "
                            "is_one_off, cat_source, flow_type) "
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
                                1 if tx.get("is_one_off") else 0,
                                tx.get("cat_source"),
                                tx.get("flow_type"),
                            ),
                        )
                        total_saved += 1

                total_duplicates += duplicates_skipped
                conn.commit()

            # Create new services submitted from the preview
            # Each entry: {name, category_id, description} — description becomes the merchant rule
            new_services = data.get("new_services", [])
            services_created = 0
            for ns in new_services:
                svc_name = (ns.get("name") or "").strip()
                cat_id = ns.get("category_id")
                desc = (ns.get("description") or "").strip()
                if not svc_name:
                    continue
                # Dedup: skip if service already exists
                existing = conn.execute(
                    "SELECT id FROM services WHERE UPPER(name) = ?", (svc_name.upper(),)
                ).fetchone()
                if existing:
                    svc_id = existing["id"]
                else:
                    conn.execute(
                        "INSERT INTO services (name, category_id) VALUES (?, ?)",
                        (svc_name, cat_id),
                    )
                    svc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    services_created += 1
                # Create merchant rule from description → service
                if desc:
                    pattern = desc.upper().strip()
                    if _looks_transfer_like_description(desc) or _is_generic_rule_pattern(pattern):
                        rules_skipped_generic += 1
                    else:
                        rule_exists = conn.execute(
                            "SELECT id FROM merchant_rules WHERE UPPER(pattern) = ?", (pattern,)
                        ).fetchone()
                        if not rule_exists:
                            conn.execute(
                                "INSERT INTO merchant_rules (pattern, service_id, match_type, confidence) "
                                "VALUES (?, ?, 'contains', 'confirmed')",
                                (pattern, svc_id),
                            )
                # Backfill service_id on transactions in this import that match this description
                conn.execute(
                    "UPDATE transactions SET service_id = ? WHERE UPPER(description) = ? AND service_id IS NULL",
                    (svc_id, desc.upper()),
                )
            conn.commit()

            # Save new merchant rules
            rules_added = 0
            for rule in new_rules:
                try:
                    # Rules require service_id — skip if not provided
                    if not rule.get("service_id"):
                        continue
                    pattern_error = _rule_pattern_error(rule.get("pattern"))
                    if pattern_error:
                        rules_skipped_generic += 1
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO merchant_rules (pattern, service_id, match_type, confidence) "
                        "VALUES (?, ?, ?, 'confirmed')",
                        (rule["pattern"], rule["service_id"], rule.get("match_type", "contains")),
                    )
                    rules_added += 1
                except Exception as e:
                    app.logger.warning("Failed to insert rule '%s': %s", rule.get("pattern"), e)
            conn.commit()
            invalidate_rules_cache()

            # Update batch_imports record
            result_summary = {
                "transactions_saved": total_saved,
                "duplicates_skipped": total_duplicates,
                "accounts": accounts_created,
                "rules_added": rules_added,
                "services_created": services_created,
                "rules_skipped_generic": rules_skipped_generic,
            }
            conn.execute(
                "UPDATE batch_imports SET status = 'committed', result_json = ? WHERE id = ?",
                (json.dumps(result_summary), import_id),
            )
            conn.commit()

            return jsonify({
                "success": True,
                "transactions_saved": total_saved,
                "duplicates_skipped": total_duplicates,
                "rules_added": rules_added,
                "accounts": accounts_created,
                "rules_skipped_generic": rules_skipped_generic,
            })

        except Exception as e:
            conn.execute(
                "UPDATE batch_imports SET status = 'failed', result_json = ? WHERE id = ?",
                (json.dumps({"error": str(e)}), import_id),
            )
            conn.commit()
            app.logger.warning("Import failed: %s", e)
            return jsonify({"error": "Import failed"}), 500


@app.route("/api/import/history")
def api_import_history():
    """List past imports."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM batch_imports ORDER BY created_at DESC"
        ).fetchall()

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
# Statement Coverage
# ---------------------------------------------------------------------------


@app.route("/api/statements/coverage")
def api_statements_coverage():
    """Return 6-month coverage matrix: active accounts × recent months.

    Shows which accounts have statements imported for each month,
    and which are missing. Uses statement_date month as the key.
    """
    months_count = int(request.args.get("months", 6))

    # Build list of last N months (YYYY-MM format)
    today = datetime.now()
    months = []
    y, m = today.year, today.month
    for _ in range(months_count):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()  # oldest first

    with get_db() as conn:
        # Get active accounts
        accounts = conn.execute(
            "SELECT id, short_name, type FROM accounts "
            "WHERE status = 'active' OR status IS NULL "
            "ORDER BY type, short_name"
        ).fetchall()
        accounts = [dict(a) for a in accounts]

        # Get all statements for active accounts in the date range
        min_month = months[0] + "-01"
        statements = conn.execute(
            "SELECT s.account_id, s.statement_date, s.filename, s.imported_at "
            "FROM statements s "
            "JOIN accounts a ON s.account_id = a.id "
            "WHERE (a.status = 'active' OR a.status IS NULL) "
            "  AND s.statement_date >= ? "
            "ORDER BY s.statement_date",
            (min_month,),
        ).fetchall()

    # Build matrix: {account_id: {month: {imported, date, filename}}}
    matrix = {}
    for acct in accounts:
        matrix[acct["id"]] = {}
        for m in months:
            matrix[acct["id"]][m] = {"imported": False}

    for stmt in statements:
        acct_id = stmt["account_id"]
        # Extract YYYY-MM from statement_date
        stmt_month = stmt["statement_date"][:7]
        if acct_id in matrix and stmt_month in matrix[acct_id]:
            matrix[acct_id][stmt_month] = {
                "imported": True,
                "date": stmt["imported_at"],
                "filename": stmt["filename"],
            }

    # Summary targets the previous month (most recent closed billing cycle)
    # On March 12th, you want to know if Feb is fully covered, not March
    pm_y, pm_m = today.year, today.month - 1
    if pm_m == 0:
        pm_m = 12
        pm_y -= 1
    target_month = f"{pm_y:04d}-{pm_m:02d}"
    covered = sum(
        1 for acct in accounts if matrix[acct["id"]].get(target_month, {}).get("imported")
    )

    return jsonify({
        "months": months,
        "accounts": accounts,
        "matrix": matrix,
        "summary": {
            "target_month": target_month,
            "covered": covered,
            "total": len(accounts),
        },
    })


# ---------------------------------------------------------------------------
# Merchant Rules CRUD
# ---------------------------------------------------------------------------

@app.route("/api/rules")
def api_rules():
    """List all merchant rules with service and category info."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT mr.id, mr.pattern, mr.match_type, mr.confidence,
                   mr.priority, mr.min_amount, mr.max_amount,
                   mr.service_id,
                   mr.category_override_id,
                   s.name as service_name,
                   COALESCE(mr.category_override_id, s.category_id) as category_id,
                   c.name as category_name,
                   c.parent_id,
                   p.name as parent_name
             FROM merchant_rules mr
             JOIN services s ON mr.service_id = s.id
             LEFT JOIN categories c ON COALESCE(mr.category_override_id, s.category_id) = c.id
             LEFT JOIN categories p ON c.parent_id = p.id
            ORDER BY COALESCE(p.name, c.name), c.name, mr.priority DESC, mr.pattern
        """).fetchall()
    return jsonify([{
        "id": r["id"],
        "pattern": r["pattern"],
        "match_type": r["match_type"],
        "confidence": r["confidence"],
        "priority": r["priority"],
        "min_amount": r["min_amount"],
        "max_amount": r["max_amount"],
        "service_id": r["service_id"],
        "service_name": r["service_name"],
        "category_override_id": r["category_override_id"],
        "category_id": r["category_id"],
        "category_name": r["category_name"],
        "parent_name": r["parent_name"],
        "display_category": format_category_display(r["parent_name"], r["category_name"]),
    } for r in rows])


@app.route("/api/rules", methods=["POST"])
def api_rules_create():
    """Add a new merchant rule. Requires service_id (category derived from service)."""
    data = request.get_json()
    if not data or "pattern" not in data or "service_id" not in data:
        return jsonify({"error": "pattern and service_id required"}), 400
    pattern_error = _rule_pattern_error(data.get("pattern"))
    if pattern_error:
        return jsonify({"error": pattern_error}), 400

    with get_db() as conn:
        return _crud_insert(
            conn,
            "INSERT INTO merchant_rules (pattern, service_id, category_override_id, match_type, confidence, priority, min_amount, max_amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["pattern"],
                data["service_id"],
                data.get("category_override_id"),
                data.get("match_type", "contains"),
                "confirmed",
                data.get("priority", 0),
                data.get("min_amount"),
                data.get("max_amount"),
            ),
            "rule",
            post_commit=invalidate_rules_cache,
        )


@app.route("/api/rules/<int:rule_id>", methods=["PUT"])
def api_rules_update(rule_id):
    """Update a merchant rule."""
    data = request.get_json()
    if "pattern" in (data or {}):
        pattern_error = _rule_pattern_error(data.get("pattern"))
        if pattern_error:
            return jsonify({"error": pattern_error}), 400
    sets, params = _build_update_sets(
        data,
        ["pattern", "service_id", "category_override_id", "match_type", "priority", "min_amount", "max_amount"],
    )
    if not sets:
        return jsonify({"error": "No fields to update"}), 400

    with get_db() as conn:
        params.append(rule_id)
        conn.execute(f"UPDATE merchant_rules SET {', '.join(sets)} WHERE id = ?", params)

        # Auto re-categorize: re-run this specific rule against transactions
        # Fetch the updated rule + service category
        rule = conn.execute(
            "SELECT mr.pattern, mr.match_type, mr.service_id, mr.category_override_id, s.category_id "
            "FROM merchant_rules mr JOIN services s ON mr.service_id = s.id "
            "WHERE mr.id = ?", (rule_id,)
        ).fetchone()
        recategorized = 0
        if rule:
            pattern_upper = rule["pattern"].upper()
            match_cond = _build_match_condition(rule["match_type"])
            effective_category_id = rule["category_override_id"] or rule["category_id"]
            effective_source = "rule_override" if rule["category_override_id"] else "service_default"
            flow_ctx = None
            cats_by_id = None
            rows = conn.execute(
                f"""
                SELECT id, description, amount_sgd, flow_type_manual
                FROM transactions
                WHERE {match_cond}
                  AND COALESCE(flow_type, 'expense') NOT IN ('transfer', 'payment')
                  AND COALESCE(cat_source, 'auto') IN ('auto', 'service_default', 'rule_override', 'fallback')
                """,
                (pattern_upper,),
            ).fetchall()
            for tx in rows:
                params = [effective_category_id, rule["service_id"], effective_source]
                sql = "UPDATE transactions SET category_id = ?, service_id = ?, cat_source = ?"
                if not tx["flow_type_manual"]:
                    if flow_ctx is None:
                        from flow import build_context

                        flow_ctx = build_context(conn)
                        cats_by_id = {
                            row["id"]: row["name"]
                            for row in conn.execute("SELECT id, name FROM categories").fetchall()
                        }
                    params.append(
                        _classify_flow_for_tx(
                            conn,
                            tx["description"],
                            tx["amount_sgd"],
                            effective_category_id,
                            flow_ctx=flow_ctx,
                            cats_by_id=cats_by_id,
                        )
                    )
                    sql += ", flow_type = ?"
                sql += " WHERE id = ?"
                params.append(tx["id"])
                conn.execute(sql, params)
                recategorized += 1

        conn.commit()
        invalidate_rules_cache()
    return jsonify({"success": True, "recategorized": recategorized})


@app.route("/api/rules/<int:rule_id>", methods=["DELETE"])
def api_rules_delete(rule_id):
    """Delete a merchant rule."""
    with get_db() as conn:
        conn.execute("DELETE FROM merchant_rules WHERE id = ?", (rule_id,))
        conn.commit()
        invalidate_rules_cache()
    return jsonify({"success": True})


@app.route("/api/rules/recategorize", methods=["POST"])
def api_rules_recategorize():
    """Re-run all merchant rules against existing transactions.

    Category derived from service (no rule-level category to sync).
    """
    with get_db() as conn:
        # Skip manually resolved transactions — only re-run on auto-categorized ones
        rows = conn.execute("""
            SELECT id, description, amount_sgd, category_id, service_id, cat_source,
                   COALESCE(flow_type, 'expense') AS flow_type, flow_type_manual
            FROM transactions
            WHERE COALESCE(flow_type, 'expense') NOT IN ('transfer', 'payment')
              AND COALESCE(cat_source, 'auto') IN ('auto', 'service_default', 'rule_override', 'fallback')
        """).fetchall()

        skipped = conn.execute("""
            SELECT COUNT(*) FROM transactions
            WHERE COALESCE(flow_type, 'expense') NOT IN ('transfer', 'payment') AND cat_source = 'manual'
        """).fetchone()[0]

        updated = 0
        unchanged = 0
        flow_ctx = None
        cats_by_id = None
        for tx in rows:
            new_cat, new_svc, new_source = categorize_transaction(
                tx["description"],
                conn,
                amount=tx["amount_sgd"],
            )
            if new_cat is None:
                new_cat = _paynow_fallback_category_id(tx["description"], conn)
                if new_cat:
                    new_svc = None
                    new_source = "fallback"
            new_flow = tx["flow_type"]
            if not tx["flow_type_manual"]:
                if flow_ctx is None:
                    from flow import build_context

                    flow_ctx = build_context(conn)
                    cats_by_id = {
                        row["id"]: row["name"]
                        for row in conn.execute("SELECT id, name FROM categories").fetchall()
                    }
                new_flow = _classify_flow_for_tx(
                    conn,
                    tx["description"],
                    tx["amount_sgd"],
                    new_cat,
                    flow_ctx=flow_ctx,
                    cats_by_id=cats_by_id,
                )
            if (
                new_cat != tx["category_id"]
                or new_svc != tx["service_id"]
                or new_source != tx["cat_source"]
                or new_flow != tx["flow_type"]
            ):
                conn.execute(
                    "UPDATE transactions SET category_id = ?, service_id = ?, cat_source = ?, flow_type = ? "
                    "WHERE id = ?",
                    (new_cat, new_svc, new_source, new_flow, tx["id"]),
                )
                updated += 1
            else:
                unchanged += 1

        conn.commit()
    return jsonify({"updated": updated, "unchanged": unchanged, "skipped_manual": skipped})


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
    elif frequency == "half-yearly":
        return amount_sgd / (6 * periods)
    elif frequency == "quarterly":
        return amount_sgd / (3 * periods)
    elif frequency == "biweekly":
        return amount_sgd * (52 / 2) / (12 * periods)  # ~2.17x per month
    elif frequency == "weekly":
        return amount_sgd * 52 / (12 * periods)  # ~4.33x per month
    return amount_sgd / periods  # monthly


@app.route("/api/subscriptions")
def api_subscriptions():
    """List all subscriptions with category info and transaction enrichment."""
    fx_rate = _get_usd_sgd_rate()

    # Compute 90-day cutoff for rolling averages
    cutoff_90d = (date.today() - timedelta(days=90)).isoformat()

    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.*, c.name as category_name, c.is_personal,
                   """ + category_scope_expr("c", "p") + """ as scope,
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

        # Batch enrichment: latest tx per subscription (replaces 2 queries per sub)
        # Subscriptions only match transactions in the same derived scope.
        latest_tx_rows = conn.execute("""
        WITH matched AS (
            SELECT s.id as sub_id,
                   t.id as tx_id, t.date as tx_date, t.amount_sgd,
                   ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY t.date DESC) as rn
            FROM subscriptions s
            JOIN transactions t
                ON UPPER(t.description) LIKE '%' || UPPER(s.match_pattern) || '%'
            LEFT JOIN categories sc ON s.category_id = sc.id
            LEFT JOIN categories sp ON sc.parent_id = sp.id
            LEFT JOIN categories tc ON t.category_id = tc.id
            LEFT JOIN categories tp ON tc.parent_id = tp.id
            WHERE s.match_pattern IS NOT NULL AND s.match_pattern != ''
              AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund')
              AND """ + category_scope_expr("tc", "tp") + " = " + category_scope_expr("sc", "sp") + """
        )
        SELECT sub_id, tx_id, tx_date, amount_sgd
        FROM matched WHERE rn = 1
        """).fetchall()
        latest_tx = {r["sub_id"]: dict(r) for r in latest_tx_rows}

        # Batch enrichment: monthly sums per subscription for 90-day rolling avg
        # Same scope boundary as latest tx query.
        monthly_rows = conn.execute("""
            SELECT s.id as sub_id,
                   SUBSTR(t.date, 1, 7) as ym,
                   SUM(t.amount_sgd) as month_total
            FROM subscriptions s
            JOIN transactions t
                ON UPPER(t.description) LIKE '%' || UPPER(s.match_pattern) || '%'
            LEFT JOIN categories sc ON s.category_id = sc.id
            LEFT JOIN categories sp ON sc.parent_id = sp.id
            LEFT JOIN categories tc ON t.category_id = tc.id
            LEFT JOIN categories tp ON tc.parent_id = tp.id
            WHERE s.match_pattern IS NOT NULL AND s.match_pattern != ''
              AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund')
              AND t.date >= ?
              AND """ + category_scope_expr("tc", "tp") + " = " + category_scope_expr("sc", "sp") + """
            GROUP BY s.id, SUBSTR(t.date, 1, 7)
            ORDER BY s.id, ym DESC
        """, (cutoff_90d,)).fetchall()

    # Build lookup: sub_id → [{ym, month_total}, ...] ordered by ym DESC
    monthly_by_sub: dict[int, list[dict]] = {}
    for r in monthly_rows:
        monthly_by_sub.setdefault(r["sub_id"], []).append(
            {"ym": r["ym"], "month_total": r["month_total"]}
        )

    result = []
    for r in rows:
        d = dict(r)
        sub_id = d["id"]
        pat = (d["match_pattern"] or "").upper()
        monthly_sums = monthly_by_sub.get(sub_id, []) if pat else []

        # Enrich from batch lookups (defaults, then override if matched)
        d.update(tx_last_paid=None, tx_amount=None, tx_id=None,
                 tx_avg_90d=None, tx_months_90d=0)
        if pat:
            tx = latest_tx.get(sub_id)
            if tx:
                d["tx_last_paid"] = tx["tx_date"]
                d["tx_amount"] = round(tx["amount_sgd"], 2)
                d["tx_id"] = tx["tx_id"]
            d["tx_months_90d"] = len(monthly_sums)
            if monthly_sums:
                totals = [m["month_total"] for m in monthly_sums]
                d["tx_avg_90d"] = round(sum(totals) / len(totals), 2)

        # Last paid amount: latest month's sum (handles split payments)
        if pat and d.get("tx_months_90d"):
            d["tx_amount"] = round(monthly_sums[0]["month_total"], 2)

        # Billed = configured amount per cycle (source of truth)
        amt = d.get("amount") or 0
        cur = d.get("currency") or "SGD"

        # Monthly equivalent: use 90d avg if variable (>10% variance), else configured
        d["is_variable"] = False
        if d["tx_avg_90d"] and d["tx_months_90d"] >= 2:
            totals = [m["month_total"] for m in monthly_sums]
            mn, mx = min(totals), max(totals)
            if mx > mn * 1.10:
                d["is_variable"] = True
                d["monthly_sgd"] = round(d["tx_avg_90d"], 2)
        if not d["is_variable"]:
            d["monthly_sgd"] = round(_monthly_equivalent(amt, d["frequency"], d["periods"], cur, fx_rate), 2)

        d["display_category"] = format_category_display(d["parent_name"], d["category_name"])
        d["fx_rate"] = fx_rate

        # Anchor-based renewal: advance from renewal_date anchor until future
        effective_last_paid = d.get("tx_last_paid") or d.get("last_paid")
        d["computed_renewal"] = _advance_renewal(
            d.get("renewal_date"), effective_last_paid, d["frequency"], d["periods"]
        )

        result.append(d)

    return jsonify(result)


@app.route("/api/subscriptions", methods=["POST"])
def api_subscriptions_create():
    """Add a new subscription."""
    data = request.get_json()
    if not data or not data.get("service_id"):
        return jsonify({"error": "service_id is required"}), 400

    with get_db() as conn:
        # Derive match_pattern from service name if not provided
        match_pattern = data.get("match_pattern")
        if not match_pattern:
            svc = conn.execute("SELECT name FROM services WHERE id = ?", (data["service_id"],)).fetchone()
            match_pattern = svc["name"].upper() if svc else ""

        return _crud_insert(
            conn,
            "INSERT INTO subscriptions "
            "(service_id, category_id, amount, currency, "
            "frequency, periods, account_id, last_paid, renewal_date, status, "
            "link, notes, match_pattern) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["service_id"],
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
                match_pattern,
            ),
            "subscription",
        )


@app.route("/api/subscriptions/<int:sub_id>", methods=["PUT"])
def api_subscriptions_update(sub_id):
    """Update a subscription."""
    return _crud_update("subscriptions", sub_id, request.get_json(), [
        "service_id", "category_id", "amount", "currency", "frequency",
        "periods", "account_id", "last_paid", "renewal_date", "status",
        "link", "notes", "match_pattern",
    ])


@app.route("/api/subscriptions/<int:sub_id>", methods=["DELETE"])
def api_subscriptions_delete(sub_id):
    """Delete a subscription."""
    with get_db() as conn:
        conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
        conn.commit()
    return jsonify({"success": True})


@app.route("/api/subscriptions/enrich", methods=["POST"])
def api_subscriptions_enrich():
    """Update all subscriptions with latest transaction data.

    Also auto-advances renewal_date when last_paid is past the current renewal.
    """
    with get_db() as conn:
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
                  AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund')
                ORDER BY t.date DESC LIMIT 1
            """, (s["match_pattern"].upper(),)).fetchone()
            if tx:
                new_renewal = _advance_renewal(
                    s["renewal_date"], tx["date"], s["frequency"], s["periods"]
                )
                # Update last_paid and renewal only — never overwrite the user's
                # configured billed amount (amount is the source of truth, set manually)
                conn.execute(
                    "UPDATE subscriptions SET last_paid = ?, renewal_date = ? WHERE id = ?",
                    (tx["date"], new_renewal, s["id"]),
                )
                updated += 1
                if new_renewal != s["renewal_date"]:
                    renewals_advanced += 1
        conn.commit()
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
    elif frequency == "half-yearly":
        month = d.month + (6 * periods)
        year = d.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(d.day, 28)
        return d.replace(year=year, month=month, day=day)
    elif frequency == "quarterly":
        month = d.month + (3 * periods)
        year = d.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(d.day, 28)
        return d.replace(year=year, month=month, day=day)
    elif frequency == "biweekly":
        return d + timedelta(weeks=2 * periods)
    elif frequency == "weekly":
        return d + timedelta(weeks=1 * periods)
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
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)
