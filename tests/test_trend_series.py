"""S1, the net series (fin-surfaces 25; spec 15, Implementation Decisions S1).

trend_series.series(conn, subject, start, end, stack_by) answers, per month,
per book, per stack key and per currency, {spent, back, net}. Every test
seeds synthetic rows through tests/trend_fixtures.py and reads the figures
back through the function, never the SQL.

The spec's mockup names some types fin does not declare. The stand-ins here:
"Household goods" is Home, "Books" is Education, "Electronics" is
Entertainment. Book names are free text (trend_fixtures.py): Larkspur is the
mockup's company.
"""

import json
from pathlib import Path

import pytest

import trend_fixtures as tf
import trend_series

FIXTURES = Path(__file__).parent / "fixtures" / "trend"


class Book:
    """The check book's accounts, with one statement per account and month."""

    def __init__(self, conn):
        self.conn = conn
        self.everyday = tf.account(conn, "Everyday card")
        self.larkspur = tf.account(conn, "Larkspur card", owner="Larkspur")
        self.rupee = tf.account(conn, "Rupee card", currency="INR")
        self._statements = {}

    def stmt(self, account_id, month):
        key = (account_id, month)
        if key not in self._statements:
            self._statements[key] = tf.statement(self.conn, account_id, month)
        return self._statements[key]

    def add(self, account_id, day, amount_minor, description="ROW", **kw):
        return tf.row(self.conn, self.stmt(account_id, day[:7]), description,
                      amount_minor, day=day, **kw)


def cell(spent, back):
    return {"spent": spent, "back": back, "net": spent - back}


def every_cell(series):
    for books in series.values():
        for keys in books.values():
            for currencies in keys.values():
                yield from currencies.values()


# --- Case 1: the merchant page chart --------------------------------------


@pytest.fixture
def grab(conn):
    b = Book(conn)
    grab = tf.merchant(conn, "Grab", "Household", "Transport")
    for day, amt in (("2026-09-03", 8_000), ("2026-09-12", 7_000), ("2026-09-25", 6_240),
                     ("2026-09-26", -1_800)):
        b.add(b.everyday, day, amt, "GRAB", service_id=grab, book="Household",
              type_name="Transport")
    for day, amt in (("2026-09-05", 3_000), ("2026-09-19", 3_100)):
        b.add(b.larkspur, day, amt, "GRAB", service_id=grab, book="Larkspur",
              type_name="Transport")
    return grab


def test_case_1_grab_september_is_two_segments_never_summed(conn, grab):
    got = trend_series.series(conn, ("merchant", grab), "2025-11", "2026-10", "book")
    assert got["subject"] == {"kind": "merchant", "id": grab, "name": "Grab"}
    assert (got["start"], got["end"], got["stack_by"]) == ("2025-11", "2026-10", "book")
    sep = got["series"]["2026-09"]
    assert sep == {
        "Household": {"Household": {"SGD": cell(21_240, 1_800)}},
        "Larkspur": {"Larkspur": {"SGD": cell(6_100, 0)}},
    }
    assert sep["Household"]["Household"]["SGD"]["net"] == 19_440
    assert all(c["net"] != 25_540 for c in every_cell(got["series"])), \
        "books are never added together"


def test_case_1_every_month_of_the_span_is_present_even_without_rows(conn, grab):
    got = trend_series.series(conn, ("merchant", grab), "2025-11", "2026-10", "book")
    assert list(got["series"]) == [f"2025-{m}" for m in ("11", "12")] + \
        [f"2026-{m:02d}" for m in range(1, 11)]
    assert got["series"]["2026-08"] == {}


def test_case_1_stack_by_type_keeps_each_book_apart(conn, grab):
    sep = trend_series.series(conn, ("merchant", grab), "2026-09", "2026-09", "type")["series"]["2026-09"]
    assert sep == {
        "Household": {"Transport": {"SGD": cell(21_240, 1_800)}},
        "Larkspur": {"Transport": {"SGD": cell(6_100, 0)}},
    }


def test_rows_outside_the_span_are_not_counted(conn, grab):
    got = trend_series.series(conn, ("merchant", grab), "2026-10", "2026-10", "book")
    assert got["series"] == {"2026-10": {}}


# --- Case 6: a refund larger than the month's spend -----------------------


@pytest.fixture
def amazon(conn):
    b = Book(conn)
    amazon = tf.merchant(conn, "Amazon SG", "Household", "Home")  # init_db seeds "Amazon"
    hh = dict(service_id=amazon, book="Household")
    b.add(b.everyday, "2026-07-08", 62_000, "AMAZON", type_name="Home", **hh)
    b.add(b.everyday, "2026-07-15", 2_500, "AMAZON", type_name="Education", **hh)
    b.add(b.everyday, "2026-08-04", 3_000, "AMAZON", type_name="Home", **hh)
    b.add(b.everyday, "2026-08-09", 9_500, "AMAZON", type_name="Groceries", **hh)
    b.add(b.everyday, "2026-08-20", -59_000, "AMAZON REFUND", type_name="Home", **hh)
    b.add(b.larkspur, "2026-08-11", 2_000, "AMAZON", service_id=amazon, book="Larkspur",
          type_name="Office")
    return amazon


def test_case_6_by_type_august_goes_below_the_line_for_household_goods(conn, amazon):
    aug = trend_series.series(conn, ("merchant", amazon), "2026-07", "2026-08", "type")["series"]["2026-08"]
    assert aug["Household"] == {
        "Groceries": {"SGD": cell(9_500, 0)},
        "Home": {"SGD": cell(3_000, 59_000)},
    }
    assert aug["Household"]["Home"]["SGD"]["net"] == -56_000
    assert aug["Larkspur"] == {"Office": {"SGD": cell(2_000, 0)}}


def test_case_6_by_book_august_is_household_minus_465_and_july_keeps_its_purchase(conn, amazon):
    got = trend_series.series(conn, ("merchant", amazon), "2026-07", "2026-08", "book")["series"]
    assert got["2026-08"]["Household"] == {"Household": {"SGD": cell(12_500, 59_000)}}
    assert got["2026-08"]["Household"]["Household"]["SGD"]["net"] == -46_500
    # The refund lands in the month it arrives; July is not reduced by it.
    assert got["2026-07"]["Household"] == {"Household": {"SGD": cell(64_500, 0)}}
    by_type = trend_series.series(conn, ("merchant", amazon), "2026-07", "2026-07", "type")["series"]
    assert by_type["2026-07"]["Household"]["Home"]["SGD"] == cell(62_000, 0)


def test_case_6_the_span_net_includes_the_below_zero_month(conn, amazon):
    got = trend_series.series(conn, ("merchant", amazon), "2026-07", "2026-08", "book")["series"]
    household = sum(got[m]["Household"]["Household"]["SGD"]["net"] for m in ("2026-07", "2026-08"))
    assert household == 64_500 - 46_500


# --- Cases 9, 11, 52: one September, read through every subject ----------


@pytest.fixture
def september(conn):
    """Household and Larkspur, Sep 2026, with the rows that must stay out."""
    b = Book(conn)
    tidy = tf.merchant(conn, "Tidy Storage", "Household", "Home", hidden=True)
    swiggy = tf.merchant(conn, "Swiggy", "Household", "Dining")
    shop = tf.merchant(conn, "Shopfront", "Household", "Shopping")
    hh = dict(book="Household")
    b.add(b.everyday, "2026-09-02", 35_000, "SHOPFRONT", service_id=shop, type_name="Shopping", **hh)
    b.add(b.everyday, "2026-09-09", 25_000, "SHOPFRONT", service_id=shop, type_name="Shopping", **hh)
    b.add(b.everyday, "2026-09-21", -25_000, "SHOPFRONT REFUND", service_id=shop,
          type_name="Shopping", **hh)
    b.add(b.everyday, "2026-09-14", -8_000, "GADGET HUB REFUND", type_name="Entertainment", **hh)
    b.add(b.everyday, "2026-09-05", 150_000, "FAIRPRICE", type_name="Groceries", **hh)
    b.add(b.everyday, "2026-09-06", 100_000, "HAWKER", type_name="Dining")  # no book: Household
    b.add(b.everyday, "2026-09-07", 4_500, "TIDY STORAGE", service_id=tidy, type_name="Home", **hh)
    b.add(b.everyday, "2026-09-08", 108_500, "AIRLINE", type_name="Travel", **hh)
    # The rupee row stays in rupees (paise), in its account's currency.
    b.add(b.rupee, "2026-09-18", 345_000, "SWIGGY", service_id=swiggy, type_name="Dining", **hh)
    # Larkspur: S$ 1,160.00 on its own card, S$ 320.00 paid from household accounts.
    lk = dict(book="Larkspur", type_name="Office")
    b.add(b.larkspur, "2026-09-03", 90_000, "OFFICE RENT", **lk)
    b.add(b.larkspur, "2026-09-10", 30_000, "STATIONERY", **lk)
    b.add(b.larkspur, "2026-09-11", -4_000, "STATIONERY REFUND", **lk)
    b.add(b.everyday, "2026-09-12", 32_000, "SOFTWARE", book="Larkspur",
          type_name="Software & AI tools")
    # Out of every figure: a Household row on a company-owned account, a card
    # payment, income, a transfer, and a row in October.
    b.add(b.larkspur, "2026-09-15", 7_700, "LUNCH", type_name="Dining", **hh)
    b.add(b.everyday, "2026-09-28", -200_000, "PAYMENT THANK YOU", flow="payment", **hh)
    b.add(b.everyday, "2026-09-29", -50_000, "INTEREST", flow="income", **hh)
    b.add(b.everyday, "2026-09-30", 60_000, "TO SAVINGS", flow="transfer", **hh)
    b.add(b.everyday, "2026-10-02", 9_900, "FAIRPRICE", type_name="Groceries", **hh)
    return {"tidy": tidy, "swiggy": swiggy}


def household_september(conn):
    return trend_series.series(conn, ("book", "Household"), "2026-09", "2026-09", "type")


def test_case_9_shopping_nets_its_refund_inside_its_own_type(conn, september):
    got = household_september(conn)
    assert got["subject"] == {"kind": "book", "id": "Household", "name": "Household"}
    sep = got["series"]["2026-09"]["Household"]
    assert sep["Shopping"]["SGD"] == cell(60_000, 25_000)
    assert sep["Shopping"]["SGD"]["net"] == 35_000


def test_case_9_a_group_of_refunds_only_nets_below_zero(conn, september):
    sep = household_september(conn)["series"]["2026-09"]["Household"]
    assert sep["Entertainment"]["SGD"] == {"spent": 0, "back": 8_000, "net": -8_000}


def test_case_11_household_september_is_3900_on_s_accounts_with_the_hidden_merchant(conn, september):
    got = household_september(conn)["series"]["2026-09"]
    assert set(got) == {"Household"}, "a book subject reads that book only"
    sep = got["Household"]
    sgd = sum(c["SGD"]["net"] for c in sep.values() if "SGD" in c)
    assert sgd == 390_000
    assert sep["Home"]["SGD"] == cell(4_500, 0), "Tidy Storage is hidden, and still counts"
    assert sep["Dining"]["SGD"] == cell(100_000, 0), "a row with no book reads as Household"
    # The rupee row takes its account's currency and is never added to S$.
    assert sep["Dining"]["INR"] == cell(345_000, 0)
    assert all(set(c) <= {"SGD", "INR"} for c in sep.values())


def test_case_11_the_hidden_merchant_counts_on_its_own_subject(conn, september):
    got = trend_series.series(conn, ("merchant", september["tidy"]), "2026-09", "2026-09", "book")
    assert got["series"]["2026-09"] == {"Household": {"Household": {"SGD": cell(4_500, 0)}}}


def test_case_52_a_company_book_counts_every_account(conn, september):
    got = trend_series.series(conn, ("book", "Larkspur"), "2026-09", "2026-09", "type")
    sep = got["series"]["2026-09"]
    assert set(sep) == {"Larkspur"}
    assert sep["Larkspur"] == {
        "Office": {"SGD": cell(120_000, 4_000)},
        "Software & AI tools": {"SGD": cell(32_000, 0)},
    }
    assert sum(c["SGD"]["net"] for c in sep["Larkspur"].values()) == 148_000
    by_book = trend_series.series(conn, ("book", "Larkspur"), "2026-09", "2026-09", "book")
    assert by_book["series"]["2026-09"] == {"Larkspur": {"Larkspur": {"SGD": cell(152_000, 4_000)}}}


def test_case_52_a_household_row_on_a_company_card_is_not_household_spending(conn, september):
    dining = trend_series.series(conn, ("type", tf.type_id(conn, "Dining")), "2026-09", "2026-09", "book")
    assert dining["subject"] == {"kind": "type", "id": tf.type_id(conn, "Dining"), "name": "Dining"}
    assert dining["series"]["2026-09"] == {
        "Household": {"Household": {"SGD": cell(100_000, 0), "INR": cell(345_000, 0)}},
    }


def test_type_subject_stacks_by_merchant(conn, september):
    shopping = trend_series.series(conn, ("type", tf.type_id(conn, "Shopping")),
                                   "2026-09", "2026-09", "merchant")
    assert shopping["series"]["2026-09"] == {
        "Household": {"Shopfront": {"SGD": cell(60_000, 25_000)}},
    }
    dining = trend_series.series(conn, ("type", tf.type_id(conn, "Dining")),
                                 "2026-09", "2026-09", "merchant")
    assert dining["series"]["2026-09"]["Household"] == {
        "No merchant": {"SGD": cell(100_000, 0)},
        "Swiggy": {"INR": cell(345_000, 0)},
    }


# --- Case 44: a type below zero on its line --------------------------------


def test_case_44_a_month_whose_refunds_exceed_its_spend_dips_below_zero(conn):
    b = Book(conn)
    for day, amt in (("2026-06-10", 30_000), ("2026-07-03", 4_000), ("2026-07-22", -18_000),
                     ("2026-08-14", 12_000)):
        b.add(b.everyday, day, amt, "SHOP", book="Household", type_name="Shopping")
    got = trend_series.series(conn, ("book", "Household"), "2026-06", "2026-08", "type")["series"]
    line = [got[m]["Household"]["Shopping"]["SGD"]["net"] for m in ("2026-06", "2026-07", "2026-08")]
    assert line == [30_000, -14_000, 12_000]
    assert got["2026-07"]["Household"]["Shopping"]["SGD"] == {"spent": 4_000, "back": 18_000,
                                                             "net": -14_000}


# --- The contract fixture's figures come out of the function --------------


def test_the_contract_fixtures_figures_come_out_of_the_function(conn):
    """24's series fixture, seeded as rows, reads back through series().

    The fixture labels itself stack_by "book" but carries the type at the
    stack-key level; those figures are what stack_by "type" answers."""
    fixture = json.loads((FIXTURES / "trend_series.json").read_text(encoding="utf-8"))
    b = Book(conn)
    grab = tf.merchant(conn, "Grab", "Household", "Transport")
    card = {"Household": b.everyday, "Larkspur": b.larkspur}
    for month, books in fixture["series"].items():
        for book, keys in books.items():
            for type_name, currencies in keys.items():
                c = currencies["SGD"]
                b.add(card[book], f"{month}-10", c["spent"], "GRAB", service_id=grab,
                      book=book, type_name=type_name)
                if c["back"]:
                    b.add(card[book], f"{month}-20", -c["back"], "GRAB", service_id=grab,
                          book=book, type_name=type_name)
    got = trend_series.series(conn, ("merchant", grab), fixture["start"], fixture["end"], "type")
    assert got["series"] == fixture["series"]


# --- Refusals ---------------------------------------------------------------


def test_a_stack_outside_the_allowlist_is_refused(conn, grab):
    with pytest.raises(ValueError, match="stack_by"):
        trend_series.series(conn, ("merchant", grab), "2026-09", "2026-09", "t.date; DROP")


def test_an_unknown_subject_kind_is_refused(conn):
    with pytest.raises(ValueError, match="subject"):
        trend_series.series(conn, ("account", 1), "2026-09", "2026-09", "book")


def test_a_subject_not_in_fin_is_a_lookup_error(conn):
    with pytest.raises(LookupError):
        trend_series.series(conn, ("merchant", 99_999), "2026-09", "2026-09", "book")
    with pytest.raises(LookupError):
        trend_series.series(conn, ("type", 99_999), "2026-09", "2026-09", "book")


def test_a_bad_month_or_a_reversed_span_is_refused(conn, grab):
    with pytest.raises(ValueError):
        trend_series.series(conn, ("merchant", grab), "2026-13", "2026-09", "book")
    with pytest.raises(ValueError):
        trend_series.series(conn, ("merchant", grab), "2026-10", "2026-09", "book")
