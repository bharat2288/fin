"""Span and buckets for the trend routes (fin-surfaces 28; spec 15).

Turns a span chip (6M, 12M, YTD, 2Y, All) and an optional `to=` month into
the bars a trend draws, plus the previous span to compare against. Pure: no
database and no clock; the caller passes `today` and, for All and the
previous-span test, the month of the first row. Months travel as "YYYY-MM"
strings. Shared by /api/trend, /api/trend/types and
/api/accounts/<id>/balances.
"""

import datetime
import re

SPANS = ("6M", "12M", "YTD", "2Y", "All")
DEFAULT_SPAN = "12M"
QUARTERS_AFTER = 24  # past this many months, a bar is a quarter

_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class SpanError(ValueError):
    """A bad query. `status` is the HTTP code; the text is the reason."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _index(month: str) -> int:
    y, m = int(month[:4]), int(month[5:7])
    return y * 12 + (m - 1)


def _month(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _range(start: int, end: int) -> list[str]:
    return [_month(i) for i in range(start, end + 1)]


def parse_month(value: str) -> str:
    if not isinstance(value, str) or not _MONTH.match(value):
        raise SpanError("to must be a month like 2026-10")
    return value


def _label(month: str) -> str:
    return f"{_NAMES[int(month[5:7]) - 1]} {month[:4]}"


def _quarter(month: str) -> tuple[int, int]:
    return int(month[:4]), (int(month[5:7]) - 1) // 3 + 1


def _bucket(key: str, label: str, months: list[str], today_month: str, quarter: bool) -> dict:
    so_far = today_month in months
    outlined = quarter and so_far and months[-1] == today_month and _quarter_open(months[-1])
    note = None
    if outlined:
        n = len(months)
        note = f"{n} month{'s' if n != 1 else ''} of this quarter so far"
    return {"key": key, "label": label, "months": months, "so_far": so_far,
            "outlined": bool(outlined), "note": note}


def _quarter_open(month: str) -> bool:
    """True while the quarter holding `month` has months still to come."""
    return int(month[5:7]) % 3 != 0


def _buckets(months: list[str], today_month: str) -> tuple[list[dict], int]:
    if len(months) <= QUARTERS_AFTER:
        return [_bucket(m, _label(m), [m], today_month, False) for m in months], 1
    groups: dict[tuple[int, int], list[str]] = {}
    for m in months:
        groups.setdefault(_quarter(m), []).append(m)
    return [_bucket(f"{y}-Q{q}", f"Q{q} {y}", ms, today_month, True)
            for (y, q), ms in groups.items()], 3


def resolve(span: str | None, to: str | None = None, *, today: datetime.date,
            first_month: str | None = None) -> dict:
    """The plan for one span.

    `span` None means the default (12M); anything else must be a chip.
    `to` is the last month ("YYYY-MM"), default the month of `today`.
    `first_month` is the month of the first row (needed by All, and to tell
    whether a previous span exists).

    Returns {span, start, end, buckets, line_factor, previous}. Each bucket is
    {key, label, months, so_far, outlined, note}; `line_factor` is how many
    months one bar stands for (multiply a monthly average by it to draw the
    average line); `previous` is {start, end, months} or None.
    """
    span = DEFAULT_SPAN if span is None else span
    if span not in SPANS:
        raise SpanError("span must be one of 6M, 12M, YTD, 2Y, All")
    today_month = f"{today.year:04d}-{today.month:02d}"
    end = _index(today_month if to is None else parse_month(to))

    if span == "6M":
        start = end - 5
    elif span == "12M":
        start = end - 11
    elif span == "2Y":
        start = end - 23
    elif span == "YTD":
        start = (end // 12) * 12
    else:  # All
        start = _index(first_month) if first_month else end
        start = min(start, end)

    months = _range(start, end)
    buckets, factor = _buckets(months, today_month)

    previous = None
    if span != "All" and first_month:
        length = end - start + 1
        shift = 12 if span == "YTD" else length
        p_start, p_end = start - shift, end - shift
        if p_start >= _index(first_month):
            previous = {"start": _month(p_start), "end": _month(p_end),
                        "months": _range(p_start, p_end)}

    return {"span": span, "start": months[0], "end": months[-1], "buckets": buckets,
            "line_factor": factor, "previous": previous}
