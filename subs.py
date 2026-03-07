"""Subscription viewer for fin.

Shows active subscriptions, monthly burn, and upcoming renewals.

Usage:
    py subs.py                # Active subscriptions + monthly burn
    py subs.py --all          # Include deactivated/paused
    py subs.py --personal     # Exclude Moom (business)
    py subs.py --renewals     # Show upcoming renewals (next 90 days)
"""

import sys
from datetime import date, timedelta

from db import get_connection, init_db


def get_subscriptions(conn, active_only: bool = True, personal_only: bool = False) -> list[dict]:
    """Fetch subscriptions from the database."""
    where = []
    if active_only:
        where.append("s.status = 'active'")
    if personal_only:
        where.append("c.is_personal = 1")

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""

    rows = conn.execute(f"""
        SELECT s.*, c.name as category_name, c.is_personal
        FROM subscriptions s
        LEFT JOIN categories c ON s.category_id = c.id
        {where_clause}
        ORDER BY c.name, s.service
    """).fetchall()

    return [dict(r) for r in rows]


def monthly_equivalent(amount_sgd: float, frequency: str, periods: int) -> float:
    """Convert a billed amount to its monthly equivalent."""
    if frequency == "monthly":
        return amount_sgd / periods if periods > 1 else amount_sgd
    elif frequency == "yearly":
        return amount_sgd / (12 * periods)
    elif frequency == "quarterly":
        return amount_sgd / (3 * periods)
    return amount_sgd


def print_subscriptions(subs: list[dict], show_inactive: bool = False) -> None:
    """Print formatted subscription list with monthly burn."""
    active = [s for s in subs if s["status"] == "active"]
    inactive = [s for s in subs if s["status"] != "active"]

    print(f"\n{'='*80}")
    print(f"  ACTIVE SUBSCRIPTIONS ({len(active)})")
    print(f"{'='*80}")
    print(f"  {'Service':<28} {'Category':<15} {'Billed':>10} {'Freq':<12} {'Monthly':>8}")
    print(f"  {'-'*73}")

    total_monthly = 0
    personal_monthly = 0
    moom_monthly = 0

    for s in active:
        freq_label = s["frequency"]
        if s["periods"] and s["periods"] > 1:
            freq_label = f"{s['periods']}x {freq_label}"

        mo = monthly_equivalent(s["amount_sgd"], s["frequency"], s["periods"] or 1)
        total_monthly += mo
        if s["is_personal"]:
            personal_monthly += mo
        else:
            moom_monthly += mo

        cat = s["category_name"] or "?"
        print(f"  {s['service']:<28} {cat:<15} {s['amount_sgd']:>10.2f} {freq_label:<12} {mo:>8.2f}")

    print(f"  {'-'*73}")
    print(f"  {'MONTHLY BURN':<28} {'':15} {'':>10} {'':12} {total_monthly:>8.2f}")
    if moom_monthly > 0:
        print(f"    Personal: {personal_monthly:>8.2f}/mo  |  Moom: {moom_monthly:>8.2f}/mo")
    print()

    if show_inactive and inactive:
        print(f"  INACTIVE / DEACTIVATED ({len(inactive)})")
        print(f"  {'-'*73}")
        for s in inactive:
            cat = s["category_name"] or "?"
            print(f"  {s['service']:<28} {cat:<15} {s['status']:<12} last: {s['last_paid'] or '?'}")
        print()


def print_renewals(subs: list[dict], days: int = 90) -> None:
    """Print subscriptions renewing in the next N days."""
    today = date.today()
    cutoff = today + timedelta(days=days)

    upcoming = []
    for s in subs:
        if s["status"] != "active" or not s["renewal_date"]:
            continue
        try:
            renewal = date.fromisoformat(s["renewal_date"])
        except ValueError:
            continue
        if today <= renewal <= cutoff:
            upcoming.append((renewal, s))

    # Also flag overdue renewals
    overdue = []
    for s in subs:
        if s["status"] != "active" or not s["renewal_date"]:
            continue
        try:
            renewal = date.fromisoformat(s["renewal_date"])
        except ValueError:
            continue
        if renewal < today:
            overdue.append((renewal, s))

    print(f"\n{'='*80}")
    print(f"  UPCOMING RENEWALS (next {days} days)")
    print(f"{'='*80}")

    if overdue:
        print(f"\n  OVERDUE:")
        for renewal, s in sorted(overdue, key=lambda x: x[0]):
            days_ago = (today - renewal).days
            print(f"  {renewal}  {s['service']:<28} SGD {s['amount_sgd']:>8.2f}  ({days_ago}d overdue)")

    if upcoming:
        print(f"\n  UPCOMING:")
        for renewal, s in sorted(upcoming, key=lambda x: x[0]):
            days_until = (renewal - today).days
            print(f"  {renewal}  {s['service']:<28} SGD {s['amount_sgd']:>8.2f}  (in {days_until}d)")
    elif not overdue:
        print("  No renewals in the next 90 days.")

    print()


if __name__ == "__main__":
    init_db()
    conn = get_connection()

    args = sys.argv[1:]
    show_all = "--all" in args
    personal_only = "--personal" in args
    show_renewals = "--renewals" in args

    subs = get_subscriptions(conn, active_only=not show_all, personal_only=personal_only)

    if show_renewals:
        print_renewals(subs)
    else:
        print_subscriptions(subs, show_inactive=show_all)

    conn.close()
