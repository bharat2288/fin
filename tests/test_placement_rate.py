"""Placement rate (personal-ledger 27): money's one inverse and the rate a
set-off across currencies is placed at.

The rates are INR/SGD (how many SGD one rupee is worth), so S$ -> Rs divides.
`money.convert_minor_inverse` is that division, exact Decimal and half-even
to the target's minor unit; no caller computes 1/rate itself.

`placement_rate.rate_for` picks the rate by the payment's month:
- a month that has ended uses its own month-end rate (the latest saved on or
  before its last day, a rate dated before its first day counting as no rate),
  or is refused naming the month;
- the month still running uses the latest rate saved on or before the day of
  placing, and says so.
`placement_rate.place` carries a payment's amount into the expectation's
currency at that rate.

Figures in, figures out, through the public functions. Rates are seeded as
the spec's check book has them; nothing is ever fetched, and nothing written.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import money
import placement_rate
import rates
from tests import trend_fixtures as tf

PAIR = "INR/SGD"


@pytest.fixture(autouse=True)
def no_fetch(monkeypatch):
    """Any reach outside for a rate fails the test: the rule only reads."""
    def refuse(url):
        raise AssertionError(f"a rate was fetched: {url}")
    monkeypatch.setattr(rates, "_http_get", refuse)


@pytest.fixture
def book(conn):
    """The spec's saved rates: 0.0160 on 31 Aug, 0.0155 on 30 Sep; none in
    June, none yet for October."""
    tf.seed_rate(conn, PAIR, "2026-08-31", "0.0160")
    tf.seed_rate(conn, PAIR, "2026-09-30", "0.0155")
    return conn


def _saved(conn):
    return conn.execute("SELECT pair, date, rate, source, fetched_at FROM rates ORDER BY date").fetchall()


# --- the one inverse (case 19's arithmetic) ---------------------------------------


def test_case_19_the_inverse_divides_cents_into_paise_half_even():
    """9,000 cents / 0.0155 = 580,645.16 paise -> 580,645."""
    assert money.convert_minor_inverse(9_000, Decimal("0.0155"), "SGD", "INR") == 580_645


def test_case_19_the_forward_multiply_brings_the_paise_back_to_the_cents():
    """580,645 paise x 0.0155 = 8,999.99975 cents -> 9,000."""
    assert money.convert_minor(580_645, Decimal("0.0155"), "INR", "SGD") == 9_000


def test_case_19_rs_193_55_is_left_of_rs_6000(book):
    """PURPLE LOTUS Rs 6,000.00; Jay's S$ 90.00 of 22 Sep placed on 19 Oct
    sets off Rs 5,806.45 at 0.0155 (30 Sep), leaving Rs 193.55 open."""
    placed = placement_rate.place(book, 9_000, "SGD", "INR", "2026-09-22", "2026-10-19")

    assert placed["amount_minor"] == 580_645
    assert 600_000 - placed["amount_minor"] == 19_355
    rate = placed["rate"]
    assert (rate["rate"], rate["date"], rate["running"], rate["note"]) == (
        "0.0155", "2026-09-30", False, None)


def test_the_inverse_rounds_an_exact_half_to_the_even_minor_unit():
    """1 cent / 0.08 = 12.5 paise exactly: half-even gives 12, where half-up
    would give 13; 3 cents / 0.08 = 37.5 gives 38."""
    assert money.convert_minor_inverse(1, Decimal("0.08"), "SGD", "INR") == 12
    assert money.convert_minor_inverse(3, Decimal("0.08"), "SGD", "INR") == 38


def test_the_inverse_keeps_the_sign_of_money_out():
    assert money.convert_minor_inverse(-9_000, Decimal("0.0155"), "SGD", "INR") == -580_645


@pytest.mark.parametrize("rate", [0.0155, "0.0155", Decimal("0"), Decimal("-0.0155")])
def test_the_inverse_refuses_a_float_or_a_rate_not_above_zero(rate):
    with pytest.raises(ValueError):
        money.convert_minor_inverse(9_000, rate, "SGD", "INR")


@pytest.mark.parametrize("amount", [90.0, "9000", True])
def test_the_inverse_refuses_an_amount_not_in_whole_minor_units(amount):
    with pytest.raises(ValueError):
        money.convert_minor_inverse(amount, Decimal("0.0155"), "SGD", "INR")


def test_the_inverse_refuses_a_currency_with_no_declared_minor_unit():
    with pytest.raises(money.UnknownCurrency):
        money.convert_minor_inverse(9_000, Decimal("0.0155"), "SGD", "EUR")
    with pytest.raises(money.UnknownCurrency):
        money.convert_minor_inverse(9_000, Decimal("0.0155"), "EUR", "INR")


# --- case 20: an ended month with no rate of its own -------------------------------


def test_case_20_an_ended_month_with_no_rate_is_refused_naming_the_month(book):
    """S$ 50.00 on 8 Jun against a rupee item: June has ended and has no
    saved rate (31 Aug's is after it, and nothing is before it)."""
    before = _saved(book)

    with pytest.raises(placement_rate.NoRate) as refused:
        placement_rate.place(book, 5_000, "SGD", "INR", "2026-06-08", "2026-10-19")

    assert str(refused.value) == "no saved INR/SGD rate for June 2026. Fetch it."
    # What a Fetch would ask for: June's month end, through the rates fetch.
    assert (refused.value.pair, refused.value.month, refused.value.fetch_on) == (
        PAIR, "2026-06", "2026-06-30")
    assert _saved(book) == before


def test_an_ended_month_counts_a_rate_dated_before_its_first_day_as_no_rate(book):
    """A rate saved for 31 May is the latest on or before 30 Jun, but it is
    from before June: June still has no rate."""
    tf.seed_rate(book, PAIR, "2026-05-31", "0.0157")

    with pytest.raises(placement_rate.NoRate) as refused:
        placement_rate.rate_for(book, "INR", "2026-06-08", "2026-10-19")

    assert str(refused.value) == "no saved INR/SGD rate for June 2026. Fetch it."


def test_an_ended_month_uses_its_month_end_rate_not_a_later_one(book):
    """August's payment, placed in October, uses 31 Aug's 0.0160, not 30
    Sep's 0.0155."""
    rate = placement_rate.rate_for(book, "INR", "2026-08-12", "2026-10-19")

    assert (rate["rate"], rate["value"], rate["date"], rate["month"]) == (
        "0.0160", Decimal("0.0160"), "2026-08-31", "2026-08")
    assert (rate["pair"], rate["running"], rate["note"]) == (PAIR, False, None)


# --- case 19a: the payment's month is still running ---------------------------------


def test_case_19a_the_running_month_uses_the_latest_rate_and_says_so(book):
    """TAJ BENGALURU Rs 8,000.00; Jay's S$ 90.00 of 10 Oct placed on 19 Oct
    with no October rate saved: not refused, 0.0155 (30 Sep) is used."""
    placed = placement_rate.place(book, 9_000, "SGD", "INR", "2026-10-10", "2026-10-19")

    assert placed["amount_minor"] == 580_645
    assert 800_000 - placed["amount_minor"] == 219_355
    rate = placed["rate"]
    assert (rate["rate"], rate["date"], rate["month"]) == ("0.0155", "2026-09-30", "2026-10")
    assert rate["running"] is True
    assert rate["note"] == "October has no rate yet"


def test_case_19a_the_running_month_reads_no_rate_saved_after_the_day_of_placing(book):
    """A rate saved for a day after the placing is not yet the latest on or
    before it."""
    tf.seed_rate(book, PAIR, "2026-10-25", "0.0150")

    rate = placement_rate.rate_for(book, "INR", "2026-10-10", "2026-10-19")

    assert (rate["rate"], rate["date"]) == ("0.0155", "2026-09-30")


def test_a_running_month_with_no_rate_at_all_is_refused_naming_the_day(conn):
    with pytest.raises(placement_rate.NoRate) as refused:
        placement_rate.rate_for(conn, "INR", "2026-10-10", "2026-10-19")

    assert str(refused.value) == "no saved INR/SGD rate on or before 19 Oct 2026. Fetch it."
    assert (refused.value.month, refused.value.fetch_on) == ("2026-10", "2026-10-19")


def test_the_last_day_of_the_month_is_still_the_running_month(book):
    """Placed on 31 Oct before 31 Oct's rate is saved: October has not ended."""
    rate = placement_rate.rate_for(book, "INR", "2026-10-10", "2026-10-31")

    assert (rate["rate"], rate["running"], rate["note"]) == ("0.0155", True, "October has no rate yet")


def test_a_rate_saved_inside_the_running_month_says_the_month_has_not_ended(book):
    tf.seed_rate(book, PAIR, "2026-10-15", "0.0152")

    rate = placement_rate.rate_for(book, "INR", "2026-10-10", "2026-10-19")

    assert (rate["rate"], rate["date"], rate["running"]) == ("0.0152", "2026-10-15", True)
    assert rate["note"] == "October has not ended"


# --- case 19b: expectation and payment in different months --------------------------


def test_case_19b_the_payment_month_rate_is_used_not_the_expectation_month(book):
    """CHAAT HOUSE DELHI of 20 Aug, Rs 3,000.00; Kim's S$ 31.00 of 5 Sep,
    placed after 30 Sep's rate is saved: 3,100 cents / 0.0155 = Rs 2,000.00.
    At August's 0.0160 it would be Rs 1,937.50."""
    placed = placement_rate.place(book, 3_100, "SGD", "INR", "2026-09-05", "2026-10-19")

    assert placed["amount_minor"] == 200_000
    assert placed["amount_minor"] != 193_750
    assert 300_000 - placed["amount_minor"] == 100_000
    assert (placed["rate"]["rate"], placed["rate"]["date"]) == ("0.0155", "2026-09-30")


# --- case 50b: a rupee item across two month ends -----------------------------------


def test_case_50b_a_payment_placed_before_its_month_end_rate_uses_the_one_before(book):
    """SWIGGY's Rs 1,800.00 open; 0.0150 saved on 31 Oct; Jay's S$ 9.00 of
    12 Nov placed on 20 Nov, before 30 Nov's rate is saved -> 900 / 0.0150 =
    60,000 paise, Rs 600.00 set off, Rs 1,200.00 open."""
    tf.seed_rate(book, PAIR, "2026-10-31", "0.0150")

    placed = placement_rate.place(book, 900, "SGD", "INR", "2026-11-12", "2026-11-20")

    assert placed["amount_minor"] == 60_000
    assert 180_000 - placed["amount_minor"] == 120_000
    rate = placed["rate"]
    assert (rate["rate"], rate["date"], rate["note"]) == ("0.0150", "2026-10-31", "November has no rate yet")


def test_case_50b_the_placement_is_not_restated_when_the_month_end_rate_arrives(book):
    """The record keeps what was placed; once 30 Nov's 0.0160 is saved the
    same placing made later would read November's own rate, but the rule
    never rewrites anything (it writes nothing at all)."""
    tf.seed_rate(book, PAIR, "2026-10-31", "0.0150")
    first = placement_rate.place(book, 900, "SGD", "INR", "2026-11-12", "2026-11-20")
    tf.seed_rate(book, PAIR, "2026-11-30", "0.0160")
    before = _saved(book)

    later = placement_rate.place(book, 900, "SGD", "INR", "2026-11-12", "2026-12-02")

    assert first["amount_minor"] == 60_000
    assert later["amount_minor"] == 56_250
    assert (later["rate"]["rate"], later["rate"]["running"]) == ("0.0160", False)
    assert _saved(book) == before


# --- direction and the same currency -------------------------------------------------


def test_a_rupee_payment_on_an_sgd_item_multiplies(book):
    """Rs 5,806.45 paid on 22 Sep against a S$ item -> S$ 90.00 at 0.0155."""
    placed = placement_rate.place(book, 580_645, "INR", "SGD", "2026-09-22", "2026-10-19")

    assert placed["amount_minor"] == 9_000
    assert placed["rate"]["date"] == "2026-09-30"


def test_the_same_currency_needs_no_rate(conn):
    placed = placement_rate.place(conn, 9_000, "SGD", "SGD", "2026-06-08", "2026-10-19")

    assert placed == {"amount_minor": 9_000, "rate": None}


@pytest.mark.parametrize("source, target", [("SGD", "EUR"), ("EUR", "INR")])
def test_a_currency_fin_keeps_no_rate_for_is_refused(conn, source, target):
    with pytest.raises(rates.InvalidRate):
        placement_rate.place(conn, 9_000, source, target, "2026-09-22", "2026-10-19")


@pytest.mark.parametrize("paid_on, placed_on", [("2026-9-22", "2026-10-19"), ("2026-09-22", "19 Oct")])
def test_a_day_not_written_as_yyyy_mm_dd_is_refused(book, paid_on, placed_on):
    with pytest.raises(rates.InvalidRate):
        placement_rate.rate_for(book, "INR", paid_on, placed_on)
