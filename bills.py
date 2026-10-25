"""Bills: what a bill reads, its one home (fin-redesign 10; spec cases 17-19).

A bill (the subscriptions table; Bill on screen) is the home of cadence: what
it costs a cycle, how often it renews (frequency x periods), the account that
pays it and the rows that pay it (match_pattern). What it reads is worked out
here at read time and stored nowhere, and the app's Bills screen, the Queue
and Home (GET /api/subscriptions) and the chat's bills tool (GET /api/bills)
all read it from here:

- **cadence**: frequency x periods as a unit and a count ("every 6 months");
  half-yearly and monthly x 6 are the same cadence. The sheet saves a cadence
  (`every`) and the bill keeps the pair it holds when the meaning is the same,
  so saving never rewrites it (case 17). A frequency fin does not know is
  refused, never read as monthly.
- **monthly**: the bill spread over the months a cycle covers, in S$, a view
  computed at read time (S$19,500 every 6 months reads S$3,250 a month).
- **state**: the missed-bill rule. The bill's last due day is its next renewal
  less one cycle. It is on track when a payment is seen from EARLY_DAYS before
  it, or while it is not yet GRACE_DAYS late. Otherwise it is missed only when
  the paying account's statements cover every day a payment could have been
  seen in (the due day less EARLY_DAYS to the due day plus GRACE_DAYS:
  statement_period.periods, the one home of which days a statement covers);
  until they do it reads awaiting statement (case 18). A bill with no paying
  account, or one paid from an account fin holds no statements for, is read
  against today alone, as before.
- **currency**: a bill's currency is its paying account's. One that differs
  is flagged (currency_mismatch) and fixed by a previewed change
  (currency_fix), never by a save that does not say so (case 19).

This module reads; app.py's routes write.
"""

from __future__ import annotations

import calendar
import datetime
import hashlib
import sqlite3
from datetime import date, timedelta

import account_kind
import book_type
import money
import statement_period
from scope import _TYPE_JOINS, book_expr

# A bill is late when no payment is seen this many days after it was due.
GRACE_DAYS = 7
# A payment seen up to this many days before the due day pays it.
EARLY_DAYS = 5

# The frequencies a bill may be held in: each a unit and how many of it one
# period is. A bill renews every periods x that.
FREQUENCIES = {
    "weekly": ("week", 1),
    "biweekly": ("week", 2),
    "monthly": ("month", 1),
    "quarterly": ("month", 3),
    "half-yearly": ("month", 6),
    "yearly": ("month", 12),
}
UNITS = ("week", "month")

STATES = ("missed", "awaiting_statement", "on_track", "paused", "stopped")


def today() -> datetime.date:
    return datetime.date.today()


class BillRefused(ValueError):
    """A bill write fin does not take; nothing is written."""


# --- cadence ------------------------------------------------------------------


def cadence(frequency: str | None, periods) -> dict:
    """A bill's cadence as {unit, count}: half-yearly x 1 and monthly x 6 are
    both {month, 6}. An unknown frequency held from before reads as itself."""
    unit, step = FREQUENCIES.get(frequency or "monthly", (None, 1))
    count = step * (periods or 1)
    return {"unit": unit or frequency, "count": count}


def every(frequency: str | None, periods) -> str:
    """The cadence in words: "every month", "every 6 months", "every 2 weeks"."""
    c = cadence(frequency, periods)
    return f"every {c['unit']}" if c["count"] == 1 else f"every {c['count']} {c['unit']}s"


def held_as(unit, count) -> tuple[str, int]:
    """The (frequency, periods) a cadence is held in when a bill takes it new:
    the frequency that is one period of it when there is one, else the unit's
    own x the count."""
    if unit not in UNITS or not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise BillRefused(f"fin does not know a bill renewing every {count!r} {unit!r}; nothing was saved")
    for frequency, (u, step) in FREQUENCIES.items():
        if u == unit and step == count:
            return frequency, 1
    if unit == "month" and count % 12 == 0:
        return "yearly", count // 12
    return ("monthly" if unit == "month" else "weekly"), count


def checked_cadence(data: dict, frequency: str | None = None, periods=None) -> dict:
    """The frequency and periods a bill write sets, from `data`: its cadence
    (`every`: {unit, count}) kept as the bill holds it (`frequency`, `periods`)
    when that means the same, else as held_as gives it; or a frequency and
    periods given as they are, checked. Refused when fin does not know it."""
    out = {}
    if "every" in data:
        given = data["every"]
        if not isinstance(given, dict):
            raise BillRefused("a bill's cadence is a unit and a count; nothing was saved")
        unit, count = given.get("unit"), given.get("count")
        pair = held_as(unit, count)
        if frequency in FREQUENCIES and cadence(frequency, periods) == {"unit": unit, "count": count}:
            pair = (frequency, periods or 1)
        out["frequency"], out["periods"] = pair
        return out
    if "frequency" in data:
        if data["frequency"] not in FREQUENCIES:
            raise BillRefused(f"fin does not know the frequency {data['frequency']!r}; nothing was saved")
        out["frequency"] = data["frequency"]
    if "periods" in data:
        p = data["periods"]
        if p is not None and (not isinstance(p, int) or isinstance(p, bool) or p < 1):
            raise BillRefused("a bill renews every one or more periods; nothing was saved")
        out["periods"] = p or 1
    return out


def monthly_equivalent(amount: float, frequency: str, periods: int,
                       currency: str = "SGD", fx_rate: float = 1.0) -> float:
    """The billed amount spread over the months one cycle covers, in S$."""
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


# --- renewals -----------------------------------------------------------------


def add_billing_period(d: date, frequency: str, periods: int) -> date:
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


def advance_renewal(current_renewal: str | None, last_paid: str | None,
                    frequency: str, periods: int, on: date | None = None) -> str | None:
    """Anchor-based renewal: advance renewal_date forward by frequency until
    it's in the future relative to today (not last_paid).

    This prevents payment delays from skewing the renewal calendar.
    If no renewal anchor exists, seed from last_paid + one cycle.
    """
    on = on or today()
    if not current_renewal:
        if not last_paid:
            return None
        renewal = add_billing_period(date.fromisoformat(last_paid), frequency, periods)
    else:
        renewal = date.fromisoformat(current_renewal)
    while renewal <= on:
        renewal = add_billing_period(renewal, frequency, periods)
    return renewal.isoformat()


def cycle_before(renewal: str, frequency: str, periods: int) -> str:
    """The renewal one cycle before `renewal`: the day the bill was last due.
    A month-based cycle keeps the day, or the month's last when it is shorter."""
    d = date.fromisoformat(renewal)
    unit, step = FREQUENCIES.get(frequency, ("month", 1))
    count = step * (periods or 1)
    if unit == "week":
        return (d - timedelta(weeks=count)).isoformat()
    months = d.year * 12 + d.month - 1 - count
    y, m = divmod(months, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1])).isoformat()


# --- the read -----------------------------------------------------------------


def _days(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def state(bill: dict, on: date, spans: list[tuple[str, str]] | None) -> tuple[str, str | None]:
    """The missed-bill rule: (state, the day it was last due). `spans` are the
    paying account's statement periods, or None when it has no account fin
    holds statements for."""
    if bill.get("status") == "paused":
        return "paused", None
    if bill.get("status") != "active":
        return "stopped", None
    renewal = bill.get("computed_renewal")
    if not renewal:
        return "on_track", None
    due = cycle_before(renewal, bill["frequency"], bill["periods"])
    seen = bill.get("tx_last_paid") or bill.get("last_paid")
    if seen and seen >= _days(due, -EARLY_DAYS):
        return "on_track", due
    if (on - date.fromisoformat(due)).days < GRACE_DAYS:
        return "on_track", due
    if spans is not None and not statement_period.covers(spans, _days(due, -EARLY_DAYS), _days(due, GRACE_DAYS)):
        return "awaiting_statement", due
    return "missed", due


def _matched(conn: sqlite3.Connection, cutoff_90d: str):
    """Each bill's latest matching row and its monthly sums over 90 days. A
    bill matches only rows in its merchant's book."""
    latest = conn.execute("""
        WITH matched AS (
            SELECT s.id as sub_id,
                   t.id as tx_id, t.date as tx_date, t.amount_minor,
                   ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY t.date DESC) as rn
            FROM subscriptions s
            JOIN transactions t
                ON UPPER(t.description) LIKE '%' || UPPER(s.match_pattern) || '%'
            LEFT JOIN services svc ON s.service_id = svc.id
            WHERE s.match_pattern IS NOT NULL AND s.match_pattern != ''
              AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund')
              AND """ + book_expr("t") + " = " + book_expr("svc") + """
        )
        SELECT sub_id, tx_id, tx_date, amount_minor
        FROM matched WHERE rn = 1
    """).fetchall()
    monthly = conn.execute("""
        SELECT s.id as sub_id,
               SUBSTR(t.date, 1, 7) as ym,
               SUM(t.amount_minor) as month_total
        FROM subscriptions s
        JOIN transactions t
            ON UPPER(t.description) LIKE '%' || UPPER(s.match_pattern) || '%'
        LEFT JOIN services svc ON s.service_id = svc.id
        WHERE s.match_pattern IS NOT NULL AND s.match_pattern != ''
          AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund')
          AND t.date >= ?
          AND """ + book_expr("t") + " = " + book_expr("svc") + """
        GROUP BY s.id, SUBSTR(t.date, 1, 7)
        ORDER BY s.id, ym DESC
    """, (cutoff_90d,)).fetchall()
    by_sub: dict[int, list[dict]] = {}
    for r in monthly:
        by_sub.setdefault(r["sub_id"], []).append({"ym": r["ym"], "month_total": r["month_total"]})
    return {r["sub_id"]: dict(r) for r in latest}, by_sub


def read(conn: sqlite3.Connection, fx_rate: float, on: date | None = None) -> list[dict]:
    """Every bill as the screens and the chat read it, active ones first. A
    bill carries no label of its own: its book and type are its merchant's."""
    on = on or today()
    cutoff_90d = (on - timedelta(days=90)).isoformat()
    rows = conn.execute("""
        SELECT s.*,
               """ + book_expr("svc") + """ as merchant_book,
               svc.type_id as merchant_type_id,
               ty.name as type_name, tp.name as parent_type,
               a.short_name as account_short_name, a.name as account_name,
               a.currency as account_currency, a.type as account_kind,
               svc.name as service_name
        FROM subscriptions s
        LEFT JOIN accounts a ON s.account_id = a.id
        LEFT JOIN services svc ON s.service_id = svc.id
        """ + _TYPE_JOINS.format(owner="svc") + """
        ORDER BY
            CASE s.status WHEN 'active' THEN 0 ELSE 1 END,
            s.renewal_date
    """).fetchall()
    latest_tx, monthly_by_sub = _matched(conn, cutoff_90d)
    spans_of: dict[int, list] = {}

    result = []
    for r in rows:
        d = dict(r)
        d["book"] = d.pop("merchant_book")
        d["type_id"] = d.pop("merchant_type_id")
        pat = (d["match_pattern"] or "").upper()
        monthly_sums = monthly_by_sub.get(d["id"], []) if pat else []

        # A month_total is whole minor units, and so is every figure worked
        # out from the rows until it is put on the payload.
        d.update(tx_last_paid=None, tx_amount=None, tx_id=None, tx_avg_90d=None, tx_months_90d=0)
        if pat:
            tx = latest_tx.get(d["id"])
            if tx:
                d["tx_last_paid"] = tx["tx_date"]
                d["tx_amount"] = money.from_minor(tx["amount_minor"])
                d["tx_id"] = tx["tx_id"]
            d["tx_months_90d"] = len(monthly_sums)
            if monthly_sums:
                totals = [m["month_total"] for m in monthly_sums]
                d["tx_avg_90d"] = money.from_minor(money.mean_minor(sum(totals), len(totals)))
        # Last paid amount: latest month's sum (handles split payments)
        if pat and d.get("tx_months_90d"):
            d["tx_amount"] = money.from_minor(monthly_sums[0]["month_total"])

        amt = d.get("amount") or 0
        cur = d.get("currency") or "SGD"
        # Monthly: the 90-day average when the payments vary (>10%), else the bill's own.
        d["is_variable"] = False
        if d["tx_avg_90d"] and d["tx_months_90d"] >= 2:
            totals = [m["month_total"] for m in monthly_sums]
            if max(totals) * 100 > min(totals) * 110:
                d["is_variable"] = True
                d["monthly_sgd"] = d["tx_avg_90d"]
        if not d["is_variable"]:
            d["monthly_sgd"] = round(monthly_equivalent(amt, d["frequency"], d["periods"], cur, fx_rate), 2)

        d["display_type"] = book_type.display_name(d["type_name"], d["parent_type"]) if d["type_name"] else ""
        d["fx_rate"] = fx_rate
        d["every"] = every(d["frequency"], d["periods"])
        d["cadence"] = cadence(d["frequency"], d["periods"])
        d["computed_renewal"] = advance_renewal(
            d.get("renewal_date"), d.get("tx_last_paid") or d.get("last_paid"), d["frequency"], d["periods"], on
        )

        kind = d.pop("account_kind")
        spans = None
        if d.get("account_id") is not None and kind in account_kind.STATEMENT_KINDS:
            if d["account_id"] not in spans_of:
                spans_of[d["account_id"]] = statement_period.periods(conn, d["account_id"])
            spans = spans_of[d["account_id"]]
        d["state"], d["due"] = state(d, on, spans)
        d["covered_to"] = max(until for _, until in spans) if spans else None
        d["currency_mismatch"] = bool(d.get("account_currency")) and d["account_currency"] != cur
        result.append(d)
    return result


def brief(bill: dict) -> dict:
    """A bill as the chat reads it: what it costs and how often, what it reads
    a month, who pays it, and the missed-bill rule's verdict."""
    return {
        "id": bill["id"],
        "merchant": bill.get("service_name") or bill.get("match_pattern"),
        "book": bill["book"],
        "type": bill["display_type"] or None,
        "amount": bill["amount"],
        "currency": bill.get("currency") or "SGD",
        "every": bill["every"],
        "monthly_sgd": bill["monthly_sgd"],
        "varies": bill["is_variable"],
        "paid_from": bill.get("account_name"),
        "account_id": bill.get("account_id"),
        "account_currency": bill.get("account_currency"),
        "currency_mismatch": bill["currency_mismatch"],
        "status": bill.get("status"),
        "state": bill["state"],
        "due": bill["due"],
        "last_paid": bill.get("tx_last_paid") or bill.get("last_paid"),
        "covered_to": bill["covered_to"],
        "next": bill["computed_renewal"] if bill.get("status") == "active" else None,
    }


def counts(shown: list[dict]) -> dict:
    return {s: sum(1 for b in shown if b["state"] == s) for s in STATES}


# --- the currency fix ----------------------------------------------------------


def currency_fix(conn: sqlite3.Connection, bill_id: int, fx_rate: float, amount=None) -> dict:
    """The previewed change that puts a flagged bill in its paying account's
    currency: before and after, where the new amount comes from (basis), and a token
    stamping the bill as the preview saw it. Without `amount` the new amount
    is the last payment seen on an account in that currency, else the old
    one at the held US$ rate, else the same figure. Refused (BillRefused)
    when there is nothing to fix."""
    held = conn.execute(
        "SELECT s.id, s.amount, s.currency, s.account_id, s.match_pattern, a.currency AS account_currency, "
        "a.name AS account_name FROM subscriptions s LEFT JOIN accounts a ON a.id = s.account_id WHERE s.id = ?",
        (bill_id,),
    ).fetchone()
    if held is None:
        raise LookupError("no such bill")
    current = held["currency"] or "SGD"
    target = held["account_currency"]
    if not target:
        raise BillRefused("this bill has no paying account to take a currency from; nothing was changed")
    if target == current:
        raise BillRefused("this bill is already in its paying account's currency; nothing was changed")
    if target not in money.MINOR_UNIT_DIGITS:
        raise BillRefused(f"fin keeps no figure in {target}; nothing was changed")
    token = hashlib.sha256(repr(("bill-currency", held["id"], held["amount"], current, held["account_id"],
                                 target)).encode()).hexdigest()[:16]
    if amount is not None:
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
            raise BillRefused("a bill's amount is a figure above nothing; nothing was changed")
        try:
            money.exact_minor(amount, target)
        except ValueError:
            raise BillRefused(f"{amount} is finer than {target}'s smallest unit; nothing was changed") from None
        new, basis = float(amount), {"kind": "given"}
    else:
        new, basis = held["amount"], {"kind": "kept"}
        pattern = (held["match_pattern"] or "").upper()
        last = conn.execute("""
            SELECT t.date, t.amount_minor FROM transactions t
            JOIN statements st ON st.id = t.statement_id
            JOIN accounts a ON a.id = st.account_id
            WHERE ? != '' AND UPPER(t.description) LIKE '%' || ? || '%'
              AND COALESCE(t.flow_type, 'expense') = 'expense' AND a.currency = ? AND t.amount_minor > 0
            ORDER BY t.date DESC, t.id DESC LIMIT 1
        """, (pattern, pattern, target)).fetchone()
        if last is not None:
            new, basis = money.from_minor(last["amount_minor"], target), {"kind": "last_payment", "date": last["date"]}
        elif current == "USD" and target == "SGD":
            new, basis = round(held["amount"] * fx_rate, 2), {"kind": "rate", "rate": fx_rate}
    return {
        "bill_id": held["id"],
        "paid_from": held["account_name"],
        "before": {"amount": held["amount"], "currency": current},
        "after": {"amount": new, "currency": target},
        "basis": basis,
        "token": token,
    }
