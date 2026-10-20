"""R1, GET /api/trend: a merchant's or a type's trend, assembled from the
pieces (fin-surfaces 39; spec 15, Server routes R1).

    GET /api/trend?subject=merchant|type&id=&span=&to=&stack=&book=

Every rule the page shows is worked out here, so every rule is tested in
Python: the bars (trend_span's months or quarters), each bar's net per stack
key (trend_series, one grouped query whatever the span), the months missing
for each book (missing.missing_months, over the accounts carrying that book's
rows in the span), rupees in S$ at the saved month-end rate
(trend_series.valued, a rate from before the month being no rate), each
book's own net, average and comparison (trend_summary), and for a type the
merchants table.

Books are never added together: every figure is one book's. A month with no
saved rate is marked and its rupees named in `left_out`, never guessed; a
currency fin keeps no rate for is left out of every S$ figure the same way.
Nothing here fetches: a GET only reads what is saved.

The answer's keys are the contract fixtures' (tests/fixtures/trend/
trend_merchant.json and trend_type.json); where the balance sheet's shapes
differ, the fixtures win (the build's rulings): a bar's `rate` is
{pair, date, exact, text} with `text` the bare rate.
"""

from __future__ import annotations

import datetime
import re
import sqlite3
from decimal import ROUND_HALF_EVEN, Decimal

from flask import jsonify, request

import account_kind
import balance_sheet
import book_type
import missing
import money
import trend_series
import trend_span
import trend_summary
from scope import book_expr

TOTAL = balance_sheet.TOTAL_CURRENCY  # every figure here is S$
SUBJECTS = ("merchant", "type")
# A subject's other stacking, beside "book": a merchant's rows stack by type,
# a type's by merchant (spec, Solution 4).
OTHER_STACK = {"merchant": "type", "type": "merchant"}
TOP_MERCHANTS = 4      # a type page stacks its top four merchants and Other
NAMED_SHARE_PCT = 5    # the merchants table names every merchant at 5% or more
FEW_ROWS, FEW_MONTHS = 3, 2  # under either, a line instead of a chart

NO_RATE = "no_rate"            # a rate fin keeps, none saved for the month
NO_RATE_KEPT = "no_rate_kept"  # a currency fin keeps no rate for

_WHOLE = re.compile(r"^\d+$")
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December")


def _today() -> datetime.date:
    """Today. Tests replace this; nothing else here reads the clock."""
    return datetime.date.today()


def register(bp) -> None:
    """Add R1 to the trend blueprint."""
    bp.add_url_rule("/api/trend", "trend", _view, methods=["GET"])


def _view():
    from db import get_db

    args = request.args
    try:
        with get_db() as conn:
            body = trend(conn, args.get("subject"), args.get("id"), span=args.get("span"),
                         to=args.get("to"), stack=args.get("stack"), book=args.get("book"),
                         narrow=args.get("months"), today=_today())
    except trend_span.SpanError as refused:
        return jsonify({"error": str(refused)}), refused.status
    return jsonify(body)


# ---------------------------------------------------------------------------
# Small words
# ---------------------------------------------------------------------------

def _month_name(month: str) -> str:
    return f"{_MONTH_NAMES[int(month[5:7]) - 1]} {month[:4]}"


def _book_order(book: str) -> tuple:
    return (book != book_type.DEFAULT_BOOK, book)


def _pct(part: int, whole: int) -> int:
    """A share as a whole percent, rounded half-even."""
    exact = Decimal(part) * 100 / Decimal(whole)
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def _rate(shown: dict) -> dict:
    """The balance sheet's rate as the contract carries it: the bare rate as
    `text` (the fixtures win over the balance sheet's own sentence)."""
    return {"pair": shown["pair"], "date": shown["date"], "exact": shown["exact"],
            "text": shown["rate"]}


# ---------------------------------------------------------------------------
# The query
# ---------------------------------------------------------------------------

def _checked(kind, ref, span, to, stack, today) -> tuple[int, str]:
    """The query's own refusals (400), before anything is read."""
    if kind not in SUBJECTS:
        raise trend_span.SpanError(f"subject must be one of {', '.join(SUBJECTS)}")
    if not isinstance(ref, (str, int)) or isinstance(ref, bool) or not _WHOLE.match(str(ref)):
        raise trend_span.SpanError("id must be a whole number")
    trend_span.resolve(span, to, today=today)  # a bad span or month, with its reason
    other = OTHER_STACK[kind]
    if stack is not None and stack not in ("book", other):
        raise trend_span.SpanError(f"stack must be one of book, {other}")
    return int(ref), other


def _facts(conn: sqlite3.Connection, where: str, ref) -> list[sqlite3.Row]:
    """The subject's rows over all time, grouped by book, account, currency,
    merchant and month: how many, and the last date. One grouped query over
    the rows trend_series counts (expense and refund, on the accounts each
    book counts), with its own filters."""
    return conn.execute(
        f"""
        SELECT {book_expr('t')} AS row_book,
               s.account_id AS account_id,
               COALESCE(a.currency, '{TOTAL}') AS row_currency,
               t.service_id AS service_id,
               COALESCE(svc.name, :no_merchant) AS service_name,
               strftime('%Y-%m', t.date) AS row_month,
               COUNT(*) AS n,
               MAX(t.date) AS last_date
        FROM transactions t
        JOIN statements s ON s.id = t.statement_id
        JOIN accounts a ON a.id = s.account_id
        LEFT JOIN services svc ON svc.id = t.service_id
        WHERE t.flow_type IN ('expense', 'refund')
          AND {trend_series._ACCOUNTS_COUNTED}
          AND {where}
        GROUP BY row_book, s.account_id, row_currency, t.service_id, row_month
        """,
        {"subject": ref, "household": book_type.DEFAULT_BOOK,
         "household_owner": account_kind.HOUSEHOLD, "no_merchant": trend_series.NO_MERCHANT},
    ).fetchall()


# ---------------------------------------------------------------------------
# One currency: rupees in S$ at the month-end rate
# ---------------------------------------------------------------------------

def _in_sgd(conn: sqlite3.Connection, raw: dict) -> tuple[dict, dict, dict, dict]:
    """S1's series (month -> book -> key -> currency -> {spent, back, net})
    in S$ only.

    Each cell is valued by trend_series.valued, the rule R2 shares: at the
    latest saved rate on or before the month end and not before its first day
    (ruling 4), spent and refunded apart, so net stays spent less refunded to
    the cent. Answers (series in S$, rupees per month/book/key, the rate each
    month used, what was left out per (month, book, currency)). Every book and
    key with rows keeps an S$ entry, zero when nothing of it could be valued."""
    sgd: dict = {}
    inr: dict = {}
    rate_of: dict = {}
    left: dict = {}
    for month, by_book in raw.items():
        for book, by_key in by_book.items():
            for key, by_currency in by_key.items():
                cell = {"spent": 0, "back": 0, "net": 0}
                for currency, amount in by_currency.items():
                    if currency == "INR":
                        inr.setdefault(month, {}).setdefault(book, {}).setdefault(key, 0)
                        inr[month][book][key] += amount["net"]
                    got = trend_series.valued(conn, amount, currency, month)
                    if got["net"] is None:
                        out = left.setdefault((month, book, currency), {
                            "kind": NO_RATE if currency in money.MINOR_UNIT_DIGITS else NO_RATE_KEPT,
                            "amount_minor": 0,
                            "reason": got["left_out"],
                        })
                        out["amount_minor"] += amount["net"]
                        continue
                    cell["spent"] += got["spent"]
                    cell["back"] += got["back"]
                    if got["rate"] is not None:
                        rate_of[month] = got["rate"]
                cell["net"] = cell["spent"] - cell["back"]
                sgd.setdefault(month, {}).setdefault(book, {})[key] = {TOTAL: cell}
    return sgd, inr, rate_of, left


def _left_out(left: dict, months: list[str]) -> list[dict]:
    """What the S$ figures of the span leave out, each book's apart (never
    added across books), in month order."""
    span = set(months)
    items = []
    for (month, book, currency), out in sorted(
            left.items(), key=lambda kv: (kv[0][0], _book_order(kv[0][1]), kv[0][2])):
        if month not in span:
            continue
        reason = (f"no saved rate for {_month_name(month)}" if out["kind"] == NO_RATE
                  else out["reason"])
        items.append({"kind": out["kind"], "currency": currency,
                      "amount_minor": out["amount_minor"], "month": month, "reason": reason})
    return items


# ---------------------------------------------------------------------------
# Stack keys: a book, or a book's type or merchant
# ---------------------------------------------------------------------------

def _span_nets(sgd: dict, months: list[str]) -> dict[str, dict[str, int]]:
    """book -> key -> net over `months`, in S$."""
    nets: dict = {}
    for month in months:
        for book, by_key in sgd.get(month, {}).items():
            for key, cell in by_key.items():
                nets.setdefault(book, {}).setdefault(key, 0)
                nets[book][key] += cell[TOTAL]["net"]
    return nets


def _stack_map(stack: str, kind: str, nets: dict, ids: dict) -> tuple[list[dict], dict]:
    """(stack_keys in drawing order, (book, series key) -> stack key).

    By book, a book is its key. Otherwise a key is one book's type or
    merchant, so books stay apart; its label is the type or merchant, and the
    book rides each segment. On a type page a book's merchants outside its
    top four of the span by net are one "Other (N merchants)"."""
    keys, mapping = [], {}
    books = sorted(nets, key=_book_order)
    if stack == "book":
        for book in books:
            keys.append({"key": book, "label": book, "id": None})
            for name in nets[book]:
                mapping[(book, name)] = book
        return keys, mapping

    for book in books:
        ranked = sorted(nets[book].items(), key=lambda kv: (-kv[1], kv[0]))
        named = ranked if kind == "merchant" else ranked[:TOP_MERCHANTS]
        rest = [] if kind == "merchant" else ranked[TOP_MERCHANTS:]
        for name, _net in named:
            ref = ids.get(name)
            key = f"{book}|{stack}:{ref}" if ref is not None else f"{book}|{stack}:none"
            keys.append({"key": key, "label": name, "id": ref})
            mapping[(book, name)] = key
        if rest:
            key = f"{book}|other"
            label = f"Other ({len(rest)} merchant{'s' if len(rest) != 1 else ''})"
            keys.append({"key": key, "label": label, "id": None})
            for name, _net in rest:
                mapping[(book, name)] = key
    return keys, mapping


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------

def _buckets(plan: dict, sgd: dict, inr: dict, rate_of: dict, left_out: list[dict],
             missing_by_book: dict, mapping: dict) -> list[dict]:
    no_rate_months = {item["month"] for item in left_out if item["kind"] == NO_RATE}
    out = []
    for b in plan["buckets"]:
        by: dict = {}
        for month in b["months"]:
            for book, by_key in sgd.get(month, {}).items():
                for name, cell in by_key.items():
                    key = mapping[(book, name)]
                    part = by.setdefault(key, {"book": book, "spent": 0, "back": 0,
                                               "net": 0, "inr_minor": None})
                    part["spent"] += cell[TOTAL]["spent"]
                    part["back"] += cell[TOTAL]["back"]
                    rupees = inr.get(month, {}).get(book, {}).get(name)
                    if rupees is not None:
                        part["inr_minor"] = (part["inr_minor"] or 0) + rupees
        for part in by.values():
            part["net"] = part["spent"] - part["back"]
        gone = {book: [m for m in b["months"] if m in months]
                for book, months in missing_by_book.items()}
        rated = [rate_of[m] for m in b["months"] if m in rate_of]
        out.append({
            "key": b["key"],
            "label": b["label"],
            "months": b["months"],
            "so_far": b["so_far"],
            "missing": {book: ms for book, ms in gone.items() if ms},
            "no_rate": any(m in no_rate_months for m in b["months"]),
            # A quarter names the rate of its last month that used one.
            "rate": _rate(rated[-1]) if rated else None,
            "by": by,
        })
    return out


def _few(facts: list, sgd: dict, first: str | None, last: str | None) -> dict | None:
    """The line instead of a chart: under 3 rows, or rows in under 2 months,
    over all time (not the span). Each book's net apart; `net` is the one
    figure only when every row is one book's."""
    rows = sum(f["n"] for f in facts)
    months = {f["row_month"] for f in facts}
    if rows >= FEW_ROWS and len(months) >= FEW_MONTHS:
        return None
    nets = _span_nets(sgd, sorted(months))
    by_book = [{"book": book, "net": sum(nets[book].values())}
               for book in sorted(nets, key=_book_order)]
    return {"rows": rows, "first_month": first, "last_month": last,
            "net": by_book[0]["net"] if len(by_book) == 1 else None, "books": by_book}


def _narrowed(months, plan) -> list[str] | None:
    """The months a type's merchants table is narrowed to, or None for the span."""
    if months is None:
        return None
    asked = [m.strip() for m in str(months).split(",") if m.strip()]
    in_span = {m for b in plan["buckets"] for m in b["months"]}
    if not asked or any(m not in in_span for m in asked):
        raise trend_span.SpanError("months must be months of the span, as YYYY-MM")
    return sorted(set(asked))


def _merchants(nets: dict, book: str, book_net: int, paid_in: dict, ids: dict) -> dict:
    """Merchants in a type for one book and span, largest first: every one at
    5% of the book's net or more named, the rest one "N others" line, each of
    them under 5%. Shares are of that book's net."""
    ranked = sorted(nets.get(book, {}).items(), key=lambda kv: (-kv[1], kv[0]))
    if book_net > 0:
        named = [(n, v) for n, v in ranked if v * 100 >= NAMED_SHARE_PCT * book_net]
        rest = [(n, v) for n, v in ranked if v * 100 < NAMED_SHARE_PCT * book_net]
    else:  # no share of nothing: every merchant named, none as a share
        named, rest = ranked, []

    def share(v: int):
        return _pct(v, book_net) if book_net > 0 else None

    rows = [{"id": ids.get(name), "name": name, "net": net, "share_pct": share(net),
             "paid_in": paid_in.get((book, name))} for name, net in named]
    others = None
    if rest:
        rest_net = sum(v for _, v in rest)
        others = {"count": len(rest), "net": rest_net, "share_pct": share(rest_net)}
    return {"book": book, "net": book_net, "rows": rows, "others": others}


def trend(conn: sqlite3.Connection, kind, ref, *, span=None, to=None, stack=None,
          book=None, narrow=None, today: datetime.date) -> dict:
    """R1's answer for a merchant or a type. Raises trend_span.SpanError
    (status 400, or 404 for a subject no longer in fin) with the reason.

    narrow (the months= query, 'YYYY-MM,...' within the span) narrows a type's merchants table to
    the months of a bar that was clicked (case 25); the chart keeps the span."""
    ref, other = _checked(kind, ref, span, to, stack, today)
    try:
        where, subject = trend_series._subject(conn, (kind, ref))
    except LookupError:
        raise trend_span.SpanError(f"This {kind} is no longer in fin", 404) from None

    facts = _facts(conn, where, ref)
    all_months = sorted({f["row_month"] for f in facts})
    first, last = (all_months[0], all_months[-1]) if all_months else (None, None)
    plan = trend_span.resolve(span, to, today=today, first_month=first)
    months = [m for b in plan["buckets"] for m in b["months"]]
    in_span = [f for f in facts if plan["start"] <= f["row_month"] <= plan["end"]]

    # One grouped read, over the span, its previous span and (for the line
    # instead of a chart) the subject's whole life.
    low, high = plan["start"], plan["end"]
    if plan["previous"]:
        low = min(low, plan["previous"]["start"])
    if first is not None and (sum(f["n"] for f in facts) < FEW_ROWS or len(all_months) < FEW_MONTHS):
        low, high = min(low, first), max(high, last)
    raw = trend_series.series(conn, (kind, ref), low, high, stack_by=other)["series"]
    sgd, inr, rate_of, left = _in_sgd(conn, raw)

    accounts: dict[str, set] = {}
    for f in in_span:
        accounts.setdefault(f["row_book"], set()).add(f["account_id"])
    missing_by_book = {b: missing.missing_months(conn, sorted(ids), months, today)
                       for b, ids in sorted(accounts.items(), key=lambda kv: _book_order(kv[0]))}

    previous = None
    if plan["previous"]:
        previous = {"months": plan["previous"]["months"], "series": sgd}
    figures = trend_summary.summary(sgd, months, missing_by_book, previous=previous, currency=TOTAL)
    counts: dict[str, list] = {}
    for f in in_span:
        c = counts.setdefault(f["row_book"], [0, f["last_date"]])
        c[0] += f["n"]
        c[1] = max(c[1], f["last_date"])
    # A book whose figure in the span is a let-go alone (personal-ledger 23: a
    # gift let go counts in Gifts & Donations on its day) has no row in it.
    books = [{"book": r["book"], "net": r["net"], "spent": r["spent"], "back": r["back"],
              "avg": r["avg"], "months_counted": r["months_counted"],
              "vs_prev_pct": r["vs_prev_pct"], "rows": counts.get(r["book"], [0, None])[0],
              "last_date": counts.get(r["book"], [0, None])[1]} for r in figures]
    names = [b["book"] for b in books]
    if book is not None and names and book not in names:
        raise trend_span.SpanError(f"book must be one of {', '.join(names)}")

    nets = _span_nets(sgd, months)
    if kind == "merchant":
        ids = book_type.spending_type_ids(conn)
    else:
        ids = {f["service_name"]: f["service_id"] for f in facts}
    distinct_other = {name for by_key in nets.values() for name in by_key}
    options = ["book", other] if len(names) > 1 and len(distinct_other) > 1 else []
    if stack is None:
        stack = "book" if len(names) > 1 or len(distinct_other) <= 1 else other
    stack_keys, mapping = _stack_map(stack, kind, nets, ids)
    left_out = _left_out(left, months)

    answer = {
        "subject": subject,
        "span": plan["span"],
        "to": plan["end"],
        "stack": stack,
        "currency": TOTAL,
        "buckets": _buckets(plan, sgd, inr, rate_of, left_out, missing_by_book, mapping),
        "books": books,
        "stack_keys": stack_keys,
        "stack_options": options,
        "left_out": left_out,
        "few": _few(facts, sgd, first, last),
        "empty": None if in_span else {"last_date": max((f["last_date"] for f in facts), default=None)},
    }
    if kind == "type":
        narrowed = _narrowed(narrow, plan)
        table_facts = [f for f in in_span if narrowed is None or f["row_month"] in narrowed]
        table_nets = nets if narrowed is None else _span_nets(sgd, narrowed)
        paid_in: dict = {}
        for f in table_facts:
            if f["row_currency"] != TOTAL:
                paid_in.setdefault((f["row_book"], f["service_name"]), f["row_currency"])
        chosen = book if book is not None else (names[0] if names else None)
        if not in_span:
            answer["merchants"] = None
        else:
            book_net = (next(b["net"] for b in books if b["book"] == chosen) if narrowed is None
                        else sum(table_nets.get(chosen, {}).values()))
            answer["merchants"] = _merchants(table_nets, chosen, book_net, paid_in, ids)
    return answer
