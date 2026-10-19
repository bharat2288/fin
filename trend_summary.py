"""Per-book summary for the trend pages (fin-surfaces 29; spec 15, Shared
rules: Averages; Solution 3 and 5).

Pure: no database and no clock. It reads S1's series shape,
{month: {book: {stack_key: {currency: {spent, back, net}}}}}, and the missing
months per book, {book: [months]}, as R1's buckets carry them (S3 answers per
account; which accounts a book counts is the caller's). Amounts are whole
minor units; a refund is in `back`, and `net` = spent - back.

Each book gets its own net, spent, refunded and average; nothing here adds
books together. A book's average is its net over its counted months, through
money.mean_minor (half-even). Counted months are the span's months not missing
for that book, the month under way included as it stands.

Only one currency is summed (S$ by default). Rupees reach the summary already
valued in S$ by the caller; any other currency left in the series is left out,
and naming it in `left_out` is the caller's.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

import book_type
import money


def _order(book: str) -> tuple:
    return (book != book_type.DEFAULT_BOOK, book)


def _sums(series: dict, months: list[str], currency: str) -> dict[str, dict]:
    sums: dict[str, dict] = {}
    for month in months:
        for book, keys in series.get(month, {}).items():
            for by_currency in keys.values():
                amounts = by_currency.get(currency)
                if amounts is None:
                    continue
                s = sums.setdefault(book, {"spent": 0, "back": 0, "net": 0})
                s["spent"] += amounts["spent"]
                s["back"] += amounts["back"]
                s["net"] += amounts["net"]
    return sums


def book_figures(series: dict, months: list[str], missing: dict | None = None,
                 *, currency: str = "SGD") -> list[dict]:
    """Each book with rows in `months`: {book, net, spent, back, missing,
    months_counted, avg}. `months` is a span or one bar's months (a quarter);
    `missing` is that book's missing months inside them; `avg` is None when
    every month is missing. Household first, then the books by name."""
    missing = missing or {}
    span = set(months)
    rows = []
    for book, s in sorted(_sums(series, months, currency).items(), key=lambda kv: _order(kv[0])):
        gone = sorted(span.intersection(missing.get(book, ())))
        counted = len(span) - len(gone)
        rows.append({
            "book": book,
            "net": s["net"],
            "spent": s["spent"],
            "back": s["back"],
            "missing": gone,
            "months_counted": counted,
            "avg": money.mean_minor(s["net"], counted) if counted else None,
        })
    return rows


def vs_previous_pct(net: int, previous_net: int) -> int | None:
    """The change against the previous span, as a whole percent rounded
    half-even. None when the previous span nets to zero or below: a change
    against nothing, or against money that came back, is left out, never
    '+inf%'."""
    if previous_net <= 0:
        return None
    pct = (Decimal(net) - Decimal(previous_net)) * 100 / Decimal(previous_net)
    return int(pct.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def summary(series: dict, months: list[str], missing: dict | None = None, *,
            previous: dict | None = None, currency: str = "SGD") -> list[dict]:
    """book_figures for the span, each with `vs_prev_pct`. `previous` is
    {months, series} for the previous span (trend_span.resolve's `previous`
    with its series), or None on All or when it reaches before the subject's
    first row; then every comparison is None."""
    rows = book_figures(series, months, missing, currency=currency)
    before = _sums(previous["series"], previous["months"], currency) if previous else {}
    for row in rows:
        prev = before.get(row["book"])
        row["vs_prev_pct"] = vs_previous_pct(row["net"], prev["net"]) if prev else None
    return rows


def average_line(avg: int | None, line_factor: int) -> int | None:
    """Where the dashed average line sits: the book's monthly average times
    the months one bar stands for (trend_span's `line_factor`: 1, or 3 for
    quarters)."""
    return None if avg is None else avg * line_factor
