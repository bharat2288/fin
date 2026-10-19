"""Per-book summary (fin-surfaces 29; spec 15, cases 15, 20, 21, 22, 23, 53).

`trend_summary` is pure: it reads S1's series shape and S3's missing months
(per book, as R1's buckets carry them) and never a database. The 12M figures
come from the shared fixture tests/fixtures/trend/trend_series.json; the
longer spans (All, the previous 12M) are built here on top of it. Today is
19 Oct 2026 and every merchant, month and figure is invented.
"""

import copy
import datetime
import json
from pathlib import Path

import money
import trend_span
import trend_summary

FIXTURES = Path(__file__).parent / "fixtures" / "trend"
TODAY = datetime.date(2026, 10, 19)
JULY_MISSING = {"Household": ["2026-07"]}


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def grab_12m():
    """Grab, Nov 2025 to Oct 2026: S1's series and its months."""
    fixture = load("trend_series")
    plan = trend_span.resolve("12M", today=TODAY)
    months = [m for b in plan["buckets"] for m in b["months"]]
    assert months[0] == fixture["start"] and months[-1] == fixture["end"]
    return fixture["series"], months


def cell(spent, back, currency="SGD", key="Transport"):
    return {key: {currency: {"spent": spent, "back": back, "net": spent - back}}}


def household_series(nets: dict[str, int]) -> dict:
    return {m: {"Household": cell(net, 0)} for m, net in nets.items()}


def by_book(rows):
    return {r["book"]: r for r in rows}


def previous_12m():
    """Household's Nov 2024 to Oct 2025: S$ 200.00 a month, S$ 2,400.00 net."""
    plan = trend_span.resolve("12M", today=TODAY, first_month="2024-01")
    months = plan["previous"]["months"]
    assert months[0] == "2024-11" and months[-1] == "2025-10"
    return {"months": months, "series": household_series({m: 20_000 for m in months})}


# --- case 15: no grand total ------------------------------------------------

def test_each_book_has_its_own_net_and_average_and_nothing_adds_them():
    series, months = grab_12m()
    rows = trend_summary.summary(series, months, JULY_MISSING)

    assert [r["book"] for r in rows] == ["Household", "Larkspur"]
    books = by_book(rows)
    assert (books["Household"]["net"], books["Household"]["avg"]) == (264_000, 24_000)
    assert (books["Larkspur"]["net"], books["Larkspur"]["avg"]) == (48_840, 4_070)

    text = json.dumps(rows)
    assert str(264_000 + 48_840) not in text
    assert "total" not in text


# --- case 21: the average skips a book's missing month ----------------------

def test_a_books_missing_month_leaves_its_divisor_and_not_the_other_books():
    series, months = grab_12m()
    books = by_book(trend_summary.summary(series, months, JULY_MISSING))

    assert books["Household"]["months_counted"] == 11
    assert books["Household"]["missing"] == ["2026-07"]
    assert books["Larkspur"]["months_counted"] == 12
    assert books["Larkspur"]["missing"] == []


def test_the_month_under_way_counts_as_it_stands():
    series, months = grab_12m()
    without_october = trend_summary.summary(series, months[:-1], JULY_MISSING)
    with_october = trend_summary.summary(series, months, JULY_MISSING)
    before, after = by_book(without_october)["Household"], by_book(with_october)["Household"]

    assert after["months_counted"] == before["months_counted"] + 1 == 11
    assert after["net"] == before["net"] + 39_000


def test_every_average_is_mean_minor_half_even():
    # S$ 0.25 over 2 months is 12.5 cents: half-even gives 12, half-up 13.
    series = household_series({"2026-09": 25, "2026-10": 0})
    books = by_book(trend_summary.summary(series, ["2026-09", "2026-10"]))
    assert books["Household"]["avg"] == money.mean_minor(25, 2) == 12

    # 0.35 over 2 is 17.5 cents: half-even goes up to the even 18.
    series = household_series({"2026-09": 35, "2026-10": 0})
    books = by_book(trend_summary.summary(series, ["2026-09", "2026-10"]))
    assert books["Household"]["avg"] == 18


def test_a_book_with_every_month_missing_has_no_average_not_a_zero():
    series = {"2026-07": {"Household": cell(4_200, 0)}}
    books = by_book(trend_summary.summary(series, ["2026-07"], JULY_MISSING))
    assert books["Household"]["net"] == 4_200
    assert books["Household"]["months_counted"] == 0
    assert books["Household"]["avg"] is None


# --- case 22: the summary, one book -----------------------------------------

def test_one_book_summary_net_avg_vs_previous_spent_refunded():
    series, months = grab_12m()
    books = by_book(trend_summary.summary(series, months, JULY_MISSING, previous=previous_12m()))
    household = books["Household"]

    assert household["net"] == 264_000
    assert household["avg"] == 24_000
    assert household["vs_prev_pct"] == 10
    assert household["spent"] == 274_200
    assert household["back"] == 10_200
    assert household["net"] == household["spent"] - household["back"]


def test_a_fall_against_the_previous_span_is_negative():
    previous = {"months": ["2026-08"], "series": household_series({"2026-08": 20_000})}
    series = household_series({"2026-09": 15_000})
    books = by_book(trend_summary.summary(series, ["2026-09"], previous=previous))
    assert books["Household"]["vs_prev_pct"] == -25


def test_the_percentage_rounds_half_even_to_a_whole_percent():
    # 2.5% and 3.5%: half-even gives 2 and 4.
    assert trend_summary.vs_previous_pct(102_500, 100_000) == 2
    assert trend_summary.vs_previous_pct(103_500, 100_000) == 4


# --- case 23: no previous span ----------------------------------------------

def test_no_previous_span_leaves_the_comparison_out():
    series, months = grab_12m()
    for row in trend_summary.summary(series, months, JULY_MISSING, previous=None):
        assert row["vs_prev_pct"] is None


def test_all_has_no_previous_span_so_no_comparison():
    plan = trend_span.resolve("All", today=TODAY, first_month="2024-01")
    assert plan["previous"] is None
    series, months = grab_12m()
    rows = trend_summary.summary(series, months, JULY_MISSING, previous=plan["previous"])
    assert all(r["vs_prev_pct"] is None for r in rows)


def test_a_book_with_nothing_in_the_previous_span_gets_no_comparison_never_infinity():
    series, months = grab_12m()
    books = by_book(trend_summary.summary(series, months, JULY_MISSING, previous=previous_12m()))
    assert books["Larkspur"]["vs_prev_pct"] is None
    assert "inf" not in json.dumps(books).lower()


def test_a_previous_span_netting_to_zero_or_below_gets_no_comparison():
    assert trend_summary.vs_previous_pct(10_000, 0) is None
    assert trend_summary.vs_previous_pct(10_000, -5_000) is None


# --- case 20: All, in quarters, one under way -------------------------------

def grab_all():
    """Household's Grab, Jan 2024 to Oct 2026: S$ 7,128.00 net in all.
    Jan-Oct 2024 S$ 208.80 a month (S$ 2,088.00), then the previous 12M
    (S$ 2,400.00), then the fixture's 12M (S$ 2,640.00, July missing)."""
    series, _ = grab_12m()
    household = {m: {"Household": copy.deepcopy(books["Household"])}
                 for m, books in series.items() if "Household" in books}
    household.update(household_series({f"2024-{m:02d}": 20_880 for m in range(1, 11)}))
    household.update(previous_12m()["series"])
    return household


def test_all_in_quarters_draws_the_line_at_the_monthly_average_times_three():
    plan = trend_span.resolve("All", today=TODAY, first_month="2024-01")
    months = [m for b in plan["buckets"] for m in b["months"]]
    assert len(months) == 34 and len(plan["buckets"]) == 12 and plan["line_factor"] == 3

    household = by_book(trend_summary.summary(grab_all(), months, JULY_MISSING))["Household"]
    assert household["net"] == 712_800
    assert household["months_counted"] == 33
    assert household["avg"] == 21_600
    assert trend_summary.average_line(household["avg"], plan["line_factor"]) == 64_800


def test_monthly_bars_draw_the_line_at_the_average_itself():
    assert trend_summary.average_line(24_000, 1) == 24_000
    assert trend_summary.average_line(None, 3) is None


# --- case 53: a quarter holding a missing month ----------------------------

def q3_series():
    return household_series({"2026-08": 25_000, "2026-09": 19_440})


def test_a_quarter_holding_a_missing_month_sums_its_other_months():
    q3 = ["2026-07", "2026-08", "2026-09"]
    household = by_book(trend_summary.book_figures(q3_series(), q3, JULY_MISSING))["Household"]

    assert household["net"] == 44_440
    assert household["missing"] == ["2026-07"]
    assert len(household["missing"]) / len(q3) == 1 / 3  # the hatch on a third of the bar


def test_the_average_line_does_not_move_when_the_bars_become_quarters():
    q3 = ["2026-07", "2026-08", "2026-09"]
    household = by_book(trend_summary.summary(q3_series(), q3, JULY_MISSING))["Household"]

    assert household["months_counted"] == 2  # July is already out of the average
    assert household["avg"] == money.mean_minor(44_440, 2)
    assert trend_summary.average_line(household["avg"], 3) == household["avg"] * 3


# --- what the summary reads ---------------------------------------------------

def test_stack_keys_inside_a_book_are_added_and_refunds_stay_netted():
    series = {"2026-08": {"Household": {
        **cell(9_500, 0, key="Groceries"),
        **cell(3_000, 59_000, key="Household goods"),
    }}}
    household = by_book(trend_summary.summary(series, ["2026-08"]))["Household"]
    assert (household["spent"], household["back"], household["net"]) == (12_500, 59_000, -46_500)


def test_only_the_named_currency_is_summed():
    series = {"2026-09": {"Household": {"Dining": {
        "SGD": {"spent": 10_000, "back": 0, "net": 10_000},
        "USD": {"spent": 4_000, "back": 0, "net": 4_000},
    }}}}
    household = by_book(trend_summary.summary(series, ["2026-09"]))["Household"]
    assert household["net"] == 10_000


def test_months_outside_the_span_are_not_read():
    series, months = grab_12m()
    books = by_book(trend_summary.summary(series, months[-3:]))
    assert books["Household"]["net"] == 19_000 + 19_440 + 39_000
