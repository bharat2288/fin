"""Monthly and category spending summaries from the fin database.

Usage:
    py summary.py                      # current month
    py summary.py 2025-12              # specific month
    py summary.py 2025-10 2026-02      # date range
    py summary.py --all                # all months
    py summary.py --no-one-off         # exclude one-off transactions
    py summary.py --personal           # exclude Moom (business)
"""

import sys
from collections import defaultdict

from db import get_connection, init_db


def monthly_breakdown(
    conn,
    start_month: str | None = None,
    end_month: str | None = None,
    exclude_one_off: bool = False,
    personal_only: bool = False,
) -> dict:
    """Query monthly spending by category.

    Args:
        start_month: YYYY-MM (inclusive). None = earliest.
        end_month: YYYY-MM (inclusive). None = latest.
        exclude_one_off: If True, exclude is_one_off=1 transactions.
        personal_only: If True, exclude Moom (business) category.

    Returns dict of {month: {category: total}}.
    """
    where_clauses = [
        "t.is_payment = 0",
        "t.is_transfer = 0",
        "t.amount_sgd > 0",  # only expenses, not credits
    ]
    params = []

    if start_month:
        where_clauses.append("t.date >= ?")
        params.append(f"{start_month}-01")
    if end_month:
        where_clauses.append("t.date <= ?")
        # end of month: use next month's first day
        year, month = int(end_month[:4]), int(end_month[5:7])
        if month == 12:
            params.append(f"{year + 1}-01-01")
        else:
            params.append(f"{year}-{month + 1:02d}-01")
    if exclude_one_off:
        where_clauses.append("t.is_one_off = 0")
    if personal_only:
        where_clauses.append("c.is_personal = 1")

    where = " AND ".join(where_clauses)

    rows = conn.execute(f"""
        SELECT
            substr(t.date, 1, 7) as month,
            COALESCE(c.name, 'Uncategorized') as category,
            c.is_personal,
            SUM(t.amount_sgd) as total,
            COUNT(*) as count
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE {where}
        GROUP BY month, category
        ORDER BY month, total DESC
    """, params).fetchall()

    # Structure: {month: [(category, total, count, is_personal), ...]}
    result = defaultdict(list)
    for row in rows:
        result[row["month"]].append({
            "category": row["category"],
            "total": row["total"],
            "count": row["count"],
            "is_personal": row["is_personal"],
        })

    return dict(result)


def print_monthly_summary(
    data: dict,
    personal_only: bool = False,
) -> None:
    """Print a formatted monthly spending summary."""
    if not data:
        print("No data found.")
        return

    for month in sorted(data.keys()):
        cats = data[month]
        month_total = sum(c["total"] for c in cats)
        personal_total = sum(c["total"] for c in cats if c["is_personal"])
        moom_total = sum(c["total"] for c in cats if not c["is_personal"])

        print(f"\n{'='*60}")
        print(f"  {month}  |  TOTAL: SGD {month_total:>10,.2f}", end="")
        if moom_total > 0 and not personal_only:
            print(f"  (Personal: {personal_total:,.2f} | Moom: {moom_total:,.2f})", end="")
        print()
        print(f"{'='*60}")

        for cat in cats:
            pct = (cat["total"] / month_total * 100) if month_total > 0 else 0
            marker = " *" if not cat["is_personal"] else ""
            print(f"  {cat['category']:<20s}  SGD {cat['total']:>10,.2f}  ({pct:>5.1f}%)  [{cat['count']} txns]{marker}")

    # Grand totals across all months
    if len(data) > 1:
        all_totals = defaultdict(lambda: {"total": 0, "count": 0, "is_personal": True})
        grand_total = 0
        for month, cats in data.items():
            for cat in cats:
                all_totals[cat["category"]]["total"] += cat["total"]
                all_totals[cat["category"]]["count"] += cat["count"]
                all_totals[cat["category"]]["is_personal"] = cat["is_personal"]
                grand_total += cat["total"]

        print(f"\n{'='*60}")
        print(f"  ALL MONTHS  |  TOTAL: SGD {grand_total:>10,.2f}")
        n_months = len(data)
        print(f"  Monthly avg: SGD {grand_total / n_months:>10,.2f}  ({n_months} months)")
        print(f"{'='*60}")

        sorted_cats = sorted(all_totals.items(), key=lambda x: x[1]["total"], reverse=True)
        for cat_name, info in sorted_cats:
            pct = (info["total"] / grand_total * 100) if grand_total > 0 else 0
            avg = info["total"] / n_months
            marker = " *" if not info["is_personal"] else ""
            print(f"  {cat_name:<20s}  SGD {info['total']:>10,.2f}  ({pct:>5.1f}%)  avg {avg:>8,.2f}/mo{marker}")


if __name__ == "__main__":
    init_db()
    conn = get_connection()

    args = sys.argv[1:]
    exclude_one_off = "--no-one-off" in args
    personal_only = "--personal" in args
    show_all = "--all" in args
    args = [a for a in args if not a.startswith("--")]

    if show_all:
        start_month, end_month = None, None
    elif len(args) == 2:
        start_month, end_month = args[0], args[1]
    elif len(args) == 1:
        start_month, end_month = args[0], args[0]
    else:
        # Default: current month
        from datetime import date
        today = date.today()
        start_month = end_month = f"{today.year}-{today.month:02d}"

    data = monthly_breakdown(conn, start_month, end_month, exclude_one_off, personal_only)
    print_monthly_summary(data, personal_only)
    conn.close()
