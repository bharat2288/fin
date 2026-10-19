"""Span and buckets (fin-surfaces 28; spec 15, cases 19, 20, 23, 63).

`trend_span` is pure: no database, no clock. Today is injected as 19 Oct 2026
and every merchant, month and figure here is invented.
"""

import datetime

import pytest

import trend_span
from trend_span import SpanError

TODAY = datetime.date(2026, 10, 19)


def keys(plan):
    return [b["key"] for b in plan["buckets"]]


def months_of(plan):
    return [m for b in plan["buckets"] for m in b["months"]]


def test_12m_is_twelve_bars_nov_to_oct_with_october_so_far():
    plan = trend_span.resolve("12M", today=TODAY)
    assert keys(plan) == ["2025-11", "2025-12"] + [f"2026-{m:02d}" for m in range(1, 11)]
    assert len(plan["buckets"]) == 12
    assert [b["label"] for b in plan["buckets"]][0] == "Nov 2025"
    flags = [b["so_far"] for b in plan["buckets"]]
    assert flags == [False] * 11 + [True]


def test_default_span_is_12m():
    assert trend_span.resolve(None, today=TODAY)["span"] == "12M"


def test_6m_is_may_to_october():
    plan = trend_span.resolve("6M", today=TODAY)
    assert keys(plan) == [f"2026-{m:02d}" for m in range(5, 11)]


def test_ytd_is_ten_bars():
    plan = trend_span.resolve("YTD", today=TODAY)
    assert keys(plan) == [f"2026-{m:02d}" for m in range(1, 11)]


def test_2y_is_twenty_four_monthly_bars():
    plan = trend_span.resolve("2Y", today=TODAY)
    assert len(plan["buckets"]) == 24
    assert keys(plan)[0] == "2024-11" and keys(plan)[-1] == "2026-10"
    assert plan["line_factor"] == 1


def test_to_moves_the_window_and_nothing_is_so_far_in_the_past():
    plan = trend_span.resolve("6M", to="2026-03", today=TODAY)
    assert keys(plan) == ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]
    assert not any(b["so_far"] for b in plan["buckets"])


def test_all_in_quarters_with_one_under_way():
    plan = trend_span.resolve("All", first_month="2024-01", today=TODAY)
    assert len(plan["buckets"]) == 12
    assert keys(plan)[0] == "2024-Q1" and keys(plan)[-1] == "2026-Q4"
    assert plan["buckets"][0]["label"] == "Q1 2024"
    assert len(months_of(plan)) == 34
    last = plan["buckets"][-1]
    assert last["months"] == ["2026-10"]
    assert last["so_far"] and last["outlined"]
    assert last["note"] == "1 month of this quarter so far"
    assert not any(b["outlined"] for b in plan["buckets"][:-1])
    assert plan["line_factor"] == 3


def test_a_quarter_with_two_months_so_far_says_so():
    plan = trend_span.resolve("All", first_month="2024-01", to="2026-11", today=datetime.date(2026, 11, 2))
    assert plan["buckets"][-1]["note"] == "2 months of this quarter so far"


def test_a_complete_quarter_is_not_outlined():
    plan = trend_span.resolve("All", first_month="2024-01", today=datetime.date(2026, 12, 31))
    assert not plan["buckets"][-1]["outlined"] and plan["buckets"][-1]["note"] is None


def test_a_first_quarter_that_starts_late_is_not_outlined():
    plan = trend_span.resolve("All", first_month="2023-02", today=TODAY)
    first = plan["buckets"][0]
    assert first["months"] == ["2023-02", "2023-03"]
    assert not first["outlined"]


def test_all_of_up_to_24_months_stays_monthly():
    plan = trend_span.resolve("All", first_month="2025-01", today=TODAY)
    assert len(plan["buckets"]) == 22 and plan["line_factor"] == 1


def test_all_with_no_rows_is_just_the_month_under_way():
    plan = trend_span.resolve("All", first_month=None, today=TODAY)
    assert keys(plan) == ["2026-10"]


def test_previous_12m_is_the_twelve_months_before():
    plan = trend_span.resolve("12M", first_month="2024-01", today=TODAY)
    assert plan["previous"]["start"] == "2024-11" and plan["previous"]["end"] == "2025-10"
    assert len(plan["previous"]["months"]) == 12


def test_previous_ytd_is_the_same_months_a_year_earlier():
    plan = trend_span.resolve("YTD", first_month="2024-01", today=TODAY)
    assert plan["previous"]["start"] == "2025-01" and plan["previous"]["end"] == "2025-10"


def test_no_previous_on_all():
    assert trend_span.resolve("All", first_month="2024-01", today=TODAY)["previous"] is None


def test_no_previous_when_it_reaches_before_the_first_row():
    assert trend_span.resolve("12M", first_month="2025-03", today=TODAY)["previous"] is None
    assert trend_span.resolve("12M", first_month=None, today=TODAY)["previous"] is None


def test_previous_that_starts_exactly_at_the_first_row_is_kept():
    assert trend_span.resolve("12M", first_month="2024-11", today=TODAY)["previous"] is not None


@pytest.mark.parametrize("span", ["9M", "12m", "", "all", "Year"])
def test_bad_span_is_a_400_with_the_reason(span):
    with pytest.raises(SpanError) as e:
        trend_span.resolve(span, today=TODAY)
    assert e.value.status == 400
    assert str(e.value) == "span must be one of 6M, 12M, YTD, 2Y, All"


@pytest.mark.parametrize("to", ["2026-13", "2026-00", "2026", "oct", "2026-1", "2026-10-01", ""])
def test_bad_month_is_a_400_with_the_reason(to):
    with pytest.raises(SpanError) as e:
        trend_span.resolve("12M", to=to, today=TODAY)
    assert e.value.status == 400
    assert str(e.value) == "to must be a month like 2026-10"
