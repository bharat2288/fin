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
  so saving never rewrites it (case 17). A frequency held from before is read
  as its plain equivalent when there is one (annual is yearly); one fin cannot
  read says so (state unknown_cadence, no monthly figure, no due day) and is
  never read as monthly. A write naming a frequency fin does not know is
  refused.
- **monthly**: the bill spread over the months a cycle covers, in S$, a view
  computed at read time (S$19,500 every 6 months reads S$3,250 a month).
- **due days**: the bill renews on its anchor ("Counts from", renewal_date)
  and every cycle after it; a bill holding no anchor counts from its last
  payment, a cycle on. Each renewal is worked out from the anchor, keeping its
  day or the month's last when the month is shorter (29 Feb renews on 28 Feb,
  the 31st on the 30th), so the day never drifts. No day before the anchor is
  a due day.
- **state**: the missed-bill rule. A due day is paid when a payment is seen
  from EARLY_DAYS before it; it is late once GRACE_DAYS have passed. A late,
  unpaid due day is missed when the paying account's statements cover every
  day a payment for it could have been seen in (the due day less EARLY_DAYS to
  the due day plus GRACE_DAYS: statement_period.periods, the one home of the
  days a statement covers). The rule walks back from the latest due day: the
  latest one the statements cover and no payment since makes the bill missed,
  however long ago (case 18). When no covered due day is unpaid but a later
  one is late, the bill is awaiting statement: due is the latest late due day,
  and waiting_on the month of the first statement it waits for (the first day
  not covered for the oldest late due day since its last payment). A due day
  before the account's first statement is neither: fin holds nothing for it. A bill with no paying account, or one paid
  from an account fin holds no statements for (not a bank or card, or none
  imported yet), is judged against today alone, as before: judged_by says
  which.
- **currency**: a bill's currency is its paying account's. One that differs
  is flagged (currency_mismatch) and fixed by a previewed change
  (currency_fix), never by a save (case 19).

"Today" is the household's day, in Singapore.

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

# Singapore's offset, as the Independent Reserve parser reads it. Not imported from
# there: that parser pulls in the PDF libraries, which the desk app must start without.
SINGAPORE = datetime.timezone(datetime.timedelta(hours=8))

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
# Frequencies held from before the list was declared, read as their plain
# equivalent. Read only: a write names one of FREQUENCIES.
ALIASES = {
    "annual": "yearly", "annually": "yearly", "year": "yearly",
    "semi-annual": "half-yearly", "semiannual": "half-yearly", "half yearly": "half-yearly",
    "quarter": "quarterly", "month": "monthly", "week": "weekly", "fortnightly": "biweekly",
}
UNITS = ("week", "month")

STATES = ("missed", "awaiting_statement", "on_track", "unknown_cadence", "paused", "stopped")


def today(now: datetime.datetime | None = None) -> datetime.date:
    """The household's day: the date in Singapore now (or at `now`)."""
    return (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(SINGAPORE).date()


class BillRefused(ValueError):
    """A bill write fin does not take; nothing is written."""


# --- cadence ------------------------------------------------------------------


def known(frequency: str | None) -> str | None:
    """The declared frequency a held one reads as, or None when fin cannot
    read it."""
    f = (frequency or "monthly").strip().lower()
    f = ALIASES.get(f, f)
    return f if f in FREQUENCIES else None


def cadence(frequency: str | None, periods) -> dict | None:
    """A bill's cadence as {unit, count}: half-yearly x 1 and monthly x 6 are
    both {month, 6}. None when fin cannot read the frequency."""
    f = known(frequency)
    if f is None:
        return None
    unit, step = FREQUENCIES[f]
    return {"unit": unit, "count": step * (periods or 1)}


def every(frequency: str | None, periods) -> str:
    """The cadence in words: "every month", "every 6 months", "every 2 weeks";
    "how often unknown (lunar)" for one fin cannot read."""
    c = cadence(frequency, periods)
    if c is None:
        return f"how often unknown ({frequency})"
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
        if cadence(frequency, periods) == {"unit": unit, "count": count}:
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
                       currency: str = "SGD", fx_rate: float = 1.0) -> float | None:
    """The billed amount spread over the months one cycle covers, in S$; None
    when fin cannot read the frequency."""
    f = known(frequency)
    if f is None:
        return None
    periods = periods or 1
    amount_sgd = amount * fx_rate if currency == "USD" else amount
    if f == "yearly":
        return amount_sgd / (12 * periods)
    elif f == "half-yearly":
        return amount_sgd / (6 * periods)
    elif f == "quarterly":
        return amount_sgd / (3 * periods)
    elif f == "biweekly":
        return amount_sgd * (52 / 2) / (12 * periods)  # ~2.17x per month
    elif f == "weekly":
        return amount_sgd * 52 / (12 * periods)  # ~4.33x per month
    return amount_sgd / periods  # monthly


# --- renewals -----------------------------------------------------------------


def occurrence(anchor: date, frequency: str, periods, k: int) -> date:
    """The anchor moved on k cycles (back when k is negative). A month-based
    cycle keeps the anchor's day, or the month's last when it is shorter."""
    unit, step = FREQUENCIES[known(frequency)]
    count = step * (periods or 1) * k
    if unit == "week":
        return anchor + timedelta(weeks=count)
    y, m = divmod(anchor.year * 12 + anchor.month - 1 + count, 12)
    return date(y, m + 1, min(anchor.day, calendar.monthrange(y, m + 1)[1]))


def add_billing_period(d: date, frequency: str, periods: int) -> date:
    """One billing period after `d`."""
    return occurrence(d, frequency, periods, 1)


def cycle_before(renewal: str, frequency: str, periods: int) -> str:
    """The renewal one cycle before `renewal`."""
    return occurrence(date.fromisoformat(renewal), frequency, periods, -1).isoformat()


def _anchor(renewal_date: str | None, last_paid: str | None) -> tuple[date, int] | None:
    """Where a bill's renewals count from, and the first of them that is a
    renewal: its anchor itself, or a cycle after its last payment when it
    holds none."""
    if renewal_date:
        return date.fromisoformat(renewal_date), 0
    if last_paid:
        return date.fromisoformat(last_paid), 1
    return None


def _last_k(anchor: date, first: int, frequency: str, periods, on: date) -> int:
    """The last renewal on or before `on` (first - 1 when none is)."""
    k = first
    while occurrence(anchor, frequency, periods, k) <= on:
        k += 1
    return k - 1


def advance_renewal(current_renewal: str | None, last_paid: str | None,
                    frequency: str, periods: int, on: date | None = None) -> str | None:
    """The next renewal after `on` (today): from the anchor, renewal_date, so a
    late payment never moves the calendar; with no anchor, a cycle on from the
    last payment. None when there is neither, or fin cannot read the
    frequency."""
    on = on or today()
    start = _anchor(current_renewal, last_paid)
    if start is None or known(frequency) is None:
        return None
    anchor, first = start
    return occurrence(anchor, frequency, periods, _last_k(anchor, first, frequency, periods, on) + 1).isoformat()


# --- the rule -----------------------------------------------------------------


def _days(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def state(bill: dict, on: date, spans: list[tuple[str, str]] | None) -> dict:
    """The missed-bill rule (module doc): {state, due, waiting_on}. `spans`
    are the paying account's statement periods, or None when the bill is
    judged against today alone."""
    out = {"state": "on_track", "due": None, "waiting_on": None}
    if bill.get("status") == "paused":
        return {**out, "state": "paused"}
    if bill.get("status") != "active":
        return {**out, "state": "stopped"}
    frequency, periods = bill["frequency"], bill["periods"]
    if known(frequency) is None:
        return {**out, "state": "unknown_cadence"}
    seen = bill.get("tx_last_paid") or bill.get("last_paid")
    start = _anchor(bill.get("renewal_date"), seen)
    if start is None:
        return out
    anchor, first = start
    awaiting = None
    earliest = spans[0][0] if spans else None   # spans, when given, hold one period at least
    for k in range(_last_k(anchor, first, frequency, periods, on), first - 1, -1):
        due = occurrence(anchor, frequency, periods, k).isoformat()
        out["due"] = out["due"] or due
        if seen and seen >= _days(due, -EARLY_DAYS):
            break  # paid, and so is every renewal before it
        if (on - date.fromisoformat(due)).days < GRACE_DAYS:
            continue  # not late yet
        if spans is None:
            return {**out, "state": "missed", "due": due}
        if _days(due, -EARLY_DAYS) < earliest:
            break  # before the account's first statement: fin holds nothing to wait for
        gap = statement_period.first_gap(spans, _days(due, -EARLY_DAYS), _days(due, GRACE_DAYS))
        if gap is None:
            return {**out, "state": "missed", "due": due}
        # The latest late due day is the one shown; the statement waited for
        # is the first one missing, for the oldest late due day not covered.
        awaiting = {"state": "awaiting_statement", "due": awaiting["due"] if awaiting else due, "waiting_on": gap[:7]}
    return awaiting or out


# --- the read -----------------------------------------------------------------

# The rows a bill matches: its wording, spending or a refund, in its
# merchant's book. Over subscriptions s and transactions t.
_MATCHED_ROWS = """
    FROM subscriptions s
    JOIN transactions t
        ON UPPER(t.description) LIKE '%' || UPPER(s.match_pattern) || '%'
    LEFT JOIN services svc ON s.service_id = svc.id
    {joins}
    WHERE s.match_pattern IS NOT NULL AND s.match_pattern != ''
      AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund')
      AND """ + book_expr("t") + " = " + book_expr("svc")


def _matched(conn: sqlite3.Connection, cutoff_90d: str):
    """Each bill's latest matching row and its monthly sums over 90 days."""
    latest = conn.execute("""
        WITH matched AS (
            SELECT s.id as sub_id,
                   t.id as tx_id, t.date as tx_date, t.amount_minor,
                   ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY t.date DESC) as rn
            """ + _MATCHED_ROWS.format(joins="") + """
        )
        SELECT sub_id, tx_id, tx_date, amount_minor
        FROM matched WHERE rn = 1
    """).fetchall()
    monthly = conn.execute("""
        SELECT s.id as sub_id,
               SUBSTR(t.date, 1, 7) as ym,
               SUM(t.amount_minor) as month_total
        """ + _MATCHED_ROWS.format(joins="") + """
          AND t.date >= ?
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
            spread = monthly_equivalent(amt, d["frequency"], d["periods"], cur, fx_rate)
            d["monthly_sgd"] = None if spread is None else round(spread, 2)

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
            spans = spans_of[d["account_id"]] or None
        d.update(state(d, on, spans))
        d["judged_by"] = "statements" if spans is not None else "today"
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
        "waiting_on": bill["waiting_on"],
        "judged_by": bill["judged_by"],
        "last_paid": bill.get("tx_last_paid") or bill.get("last_paid"),
        "covered_to": bill["covered_to"],
        "next": bill["computed_renewal"] if bill.get("status") == "active" else None,
    }


def counts(shown: list[dict]) -> dict:
    return {s: sum(1 for b in shown if b["state"] == s) for s in STATES}


# --- the currency fix ----------------------------------------------------------

_NOT_GIVEN = object()


def currency_fix(conn: sqlite3.Connection, bill_id: int, fx_rate: float, amount=_NOT_GIVEN) -> dict:
    """The previewed change that puts a flagged bill in its paying account's
    currency: before and after, where the new amount comes from (basis), and a
    token stamping the bill and the amount the preview proposed. The proposed
    amount is the last payment the bill matches (its wording, spending, in its
    merchant's book: the rows read gives it) on an account in that currency,
    else the old one at the held US$ rate, else the same figure. `amount`,
    when given, replaces it and must be a figure. Refused (BillRefused) when
    there is nothing to fix."""
    held = conn.execute(
        "SELECT s.id, s.amount, s.currency, s.account_id, a.currency AS account_currency, "
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
    proposed, basis = held["amount"], {"kind": "kept"}
    last = conn.execute(
        "SELECT t.date, t.amount_minor "
        + _MATCHED_ROWS.format(joins="JOIN statements st ON st.id = t.statement_id "
                                     "JOIN accounts pa ON pa.id = st.account_id")
        + " AND s.id = ? AND pa.currency = ? AND t.amount_minor > 0 ORDER BY t.date DESC, t.id DESC LIMIT 1",
        (bill_id, target),
    ).fetchone()
    if last is not None:
        proposed, basis = money.from_minor(last["amount_minor"], target), {"kind": "last_payment", "date": last["date"]}
    elif current == "USD" and target == "SGD":
        proposed, basis = round(held["amount"] * fx_rate, 2), {"kind": "rate", "rate": fx_rate}
    token = hashlib.sha256(repr(("bill-currency", held["id"], held["amount"], current, held["account_id"],
                                 target, proposed)).encode()).hexdigest()[:16]
    new = proposed
    if amount is not _NOT_GIVEN:
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
            raise BillRefused("a bill's amount is a figure above nothing; nothing was changed")
        try:
            money.exact_minor(amount, target)
        except ValueError:
            raise BillRefused(f"{amount} is finer than {target}'s smallest unit; nothing was changed") from None
        new, basis = float(amount), {"kind": "given"}
    return {
        "bill_id": held["id"],
        "paid_from": held["account_name"],
        "before": {"amount": held["amount"], "currency": current},
        "after": {"amount": new, "currency": target},
        "basis": basis,
        "token": token,
    }
