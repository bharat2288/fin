"""S2, the month-end value (fin-surfaces 15, ticket 26).

`balance_sheet.valued` values an amount kept in one currency in the currency
of the totals, at the latest saved rate on or before a day. With `not_before`
a rate dated before that day counts as no rate (ruling 4: a rate from before
the month is never used for it). The trend pages pass a month's first day;
the balance sheet does the same for every month it shows.

Figures go in and come out through the public function, and through
`balance_sheet.sheet` for the balance sheet's own use of the rule. Every rate
is seeded; nothing is ever fetched.
"""

from __future__ import annotations

import pytest

import balance_sheet
import rates
from tests import trend_fixtures as tf

PAIR = "INR/SGD"


def _no_fetch(monkeypatch):
    """Fail any reach outside for a rate, the way the rates tests do."""
    def refuse(url):
        raise AssertionError(f"a rate was fetched: {url}")
    monkeypatch.setattr(rates, "_http_get", refuse)


def _saved_rates(conn):
    return conn.execute("SELECT pair, date, rate FROM rates ORDER BY pair, date").fetchall()


# --- case 26: rupees in S$ at the month-end rate ---------------------------------


def test_rupees_are_valued_at_the_month_end_rate_rounded_half_even(conn, monkeypatch):
    """Dining, Sep 2026: Swiggy's Rs 3,450 at the 30 Sep rate 0.0155 is
    S$ 53.475, rounded half-even to S$ 53.48."""
    _no_fetch(monkeypatch)
    tf.seed_rate(conn, PAIR, "2026-09-30", "0.0155")

    shown = balance_sheet.valued(conn, 345_000, "INR", "2026-09-30", not_before="2026-09-01")

    assert shown["value_minor"] == 5348
    assert shown["left_out"] is None
    rate = shown["rate"]
    assert (rate["pair"], rate["date"], rate["exact"]) == (PAIR, "2026-09-30", True)
    assert rate["rate"] == "0.0155"
    assert rate["text"] == "1 INR = S$ 0.0155, rate of 2026-09-30"


def test_half_even_rounds_a_half_cent_down_to_the_even_cent(conn):
    """Rs 1,250.00 x 0.0153 = S$ 19.125 exactly: half-even gives S$ 19.12,
    where half-up would give S$ 19.13."""
    tf.seed_rate(conn, PAIR, "2026-09-30", "0.0153")

    assert balance_sheet.valued(conn, 125_000, "INR", "2026-09-30", "2026-09-01")["value_minor"] == 1912


# --- case 27: a rate from earlier in the month ------------------------------------


def test_a_rate_from_earlier_in_the_month_is_used_and_named(conn, monkeypatch):
    """August's last saved rate is 28 Aug at 0.0154: Swiggy's Rs 2,000 is S$ 30.80,
    and the rate says it is the 28 Aug one, not the month end's."""
    _no_fetch(monkeypatch)
    tf.seed_rate(conn, PAIR, "2026-08-10", "0.0150")
    tf.seed_rate(conn, PAIR, "2026-08-28", "0.0154")

    shown = balance_sheet.valued(conn, 200_000, "INR", "2026-08-31", not_before="2026-08-01")

    assert shown["value_minor"] == 3080
    assert shown["left_out"] is None
    assert (shown["rate"]["date"], shown["rate"]["exact"]) == ("2026-08-28", False)
    assert shown["rate"]["text"] == (
        "1 INR = S$ 0.0154, rate of 2026-08-28, the latest saved on or before 2026-08-31")


# --- case 28: a month with no saved rate ------------------------------------------


def test_a_month_with_no_saved_rate_is_left_out_and_nothing_is_fetched(conn, monkeypatch):
    """No rate is saved in June 2026 (May's is): Swiggy's June Rs 2,100 has no
    S$ value, is left out saying why, and valuing it reaches nothing outside
    and saves nothing."""
    _no_fetch(monkeypatch)
    tf.seed_rate(conn, PAIR, "2026-05-31", "0.0152")
    before = _saved_rates(conn)

    shown = balance_sheet.valued(conn, 210_000, "INR", "2026-06-30", not_before="2026-06-01")

    assert shown["value_minor"] is None and shown["rate"] is None
    assert shown["left_out"] == "in INR, no saved INR/SGD rate from 2026-06-01 to 2026-06-30"
    assert _saved_rates(conn) == before


def test_without_not_before_an_earlier_months_rate_is_still_the_latest_saved(conn):
    """The bound is the caller's: with none, the latest rate on or before the
    day is used, however old, and the rate says so."""
    tf.seed_rate(conn, PAIR, "2026-05-31", "0.0152")

    shown = balance_sheet.valued(conn, 210_000, "INR", "2026-06-30")

    assert shown["value_minor"] == 3192
    assert (shown["rate"]["date"], shown["rate"]["exact"]) == ("2026-05-31", False)


def test_a_rate_saved_on_the_first_day_of_the_month_counts(conn):
    tf.seed_rate(conn, PAIR, "2026-06-01", "0.0152")

    shown = balance_sheet.valued(conn, 210_000, "INR", "2026-06-30", not_before="2026-06-01")

    assert shown["value_minor"] == 3192 and shown["rate"]["date"] == "2026-06-01"


def test_a_rate_saved_only_after_the_day_is_not_used(conn):
    tf.seed_rate(conn, PAIR, "2026-07-01", "0.0153")

    shown = balance_sheet.valued(conn, 210_000, "INR", "2026-06-30", not_before="2026-06-01")

    assert shown["value_minor"] is None
    assert shown["left_out"] == "in INR, no saved INR/SGD rate from 2026-06-01 to 2026-06-30"


def test_with_no_rate_at_all_and_no_bound_the_reason_says_on_or_before(conn):
    shown = balance_sheet.valued(conn, 210_000, "INR", "2026-06-30")

    assert shown["value_minor"] is None
    assert shown["left_out"] == "in INR, no saved INR/SGD rate on or before 2026-06-30"


# --- case 62: a currency fin keeps no rate for ------------------------------------


@pytest.mark.parametrize("not_before", [None, "2026-09-01"])
def test_a_currency_fin_keeps_no_rate_for_is_left_out(conn, monkeypatch, not_before):
    """A US$ 40.00 row is left out of every S$ figure, saying why, even with
    rates saved for the rupee."""
    _no_fetch(monkeypatch)
    tf.seed_rate(conn, PAIR, "2026-09-30", "0.0155")

    shown = balance_sheet.valued(conn, 4_000, "USD", "2026-09-30", not_before)

    assert shown == {
        "value_minor": None,
        "rate": None,
        "left_out": "in USD, a currency fin keeps no rate for",
    }


def test_an_amount_already_in_the_currency_of_the_totals_needs_no_rate(conn):
    shown = balance_sheet.valued(conn, 64_000, "SGD", "2026-06-30", not_before="2026-06-01")

    assert shown == {"value_minor": 64_000, "rate": None, "left_out": None}


# --- ruling 4: the balance sheet uses the same strict rule ------------------------


@pytest.fixture
def rupee_savings(conn):
    """A rupee bank account with a balance of Rs 100,000.00 from 30 June."""
    acct = tf.account(conn, "Rupee savings", type="bank", currency="INR")
    tf.anchor(conn, acct, "2026-06-30", 10_000_000, source="supplied")
    return acct


def _line(shown, name):
    return next(
        line for section in shown["sections"] for line in section["lines"] if line["name"] == name
    )


def test_the_balance_sheet_values_a_month_with_a_rate_saved_in_it(conn, rupee_savings):
    tf.seed_rate(conn, PAIR, "2026-06-30", "0.0155")

    line = _line(balance_sheet.sheet(conn, "2026-06"), "Rupee savings")

    assert (line["value_minor"], line["in_total"]) == (155_000, True)
    assert line["rate"]["date"] == "2026-06-30"


def test_the_balance_sheet_does_not_carry_last_months_rate_into_this_month(conn, rupee_savings):
    """June's rate is the latest saved on or before 31 July, but it is from
    before July: July's sheet shows the rupees only and leaves them out."""
    tf.seed_rate(conn, PAIR, "2026-06-30", "0.0155")

    shown = balance_sheet.sheet(conn, "2026-07")
    line = _line(shown, "Rupee savings")

    assert (line["balance_minor"], line["value_minor"], line["rate"]) == (10_000_000, None, None)
    assert line["in_total"] is False
    assert line["left_out"] == "in INR, no saved INR/SGD rate from 2026-07-01 to 2026-07-31"
    assert "Rupee savings" in shown["note"]

    # Nor is July's currency change worked out on June's rate.
    change = shown["currency_change"]
    assert change["minor"] is None
    assert change["lines"][0]["why"] == "no saved INR/SGD rate from 2026-07-01 to 2026-07-31"


def test_the_balance_sheets_currency_change_needs_each_ends_rate_in_its_own_month(conn, rupee_savings):
    """July's change runs from 30 June to 31 July: June's rate for the start and
    July's for the end. A June rate alone gives July no end rate; May's alone
    gives it no start rate."""
    tf.seed_rate(conn, PAIR, "2026-05-31", "0.0150")
    tf.seed_rate(conn, PAIR, "2026-07-31", "0.0156")

    change = balance_sheet.sheet(conn, "2026-07")["currency_change"]

    assert change["minor"] is None
    assert change["lines"][0]["why"] == "no saved INR/SGD rate from 2026-06-01 to 2026-06-30"

    tf.seed_rate(conn, PAIR, "2026-06-30", "0.0155")
    change = balance_sheet.sheet(conn, "2026-07")["currency_change"]

    # Rs 100,000.00 at 0.0156 less at 0.0155: S$ 1,560.00 - S$ 1,550.00.
    assert change["minor"] == 1_000
    assert (change["lines"][0]["start"]["date"], change["lines"][0]["end"]["date"]) == (
        "2026-06-30", "2026-07-31")
