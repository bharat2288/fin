"""The trend pages' JSON contract, pinned.

The fixtures in tests/fixtures/trend/ are the R1 / R2 / R3 shapes of the trend
spec (Implementation Decisions, Server routes), plus the two intermediate
shapes the engine lanes hand each other (S1's series, S3's missing months).
Every client lane draws from them and every route lane must answer in them, so
a key renamed here is a failing test before it is a broken page.

Also checks the shared helpers in tests/trend_fixtures.py by reading back
through the app's own tables. Every figure, name and date is invented.
"""

import json
import re
from pathlib import Path

import pytest

import money
import rates
import trend_fixtures as tf

FIXTURES = Path(__file__).parent / "fixtures" / "trend"
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def keys(obj: dict) -> set:
    return set(obj)


# --- R1: /api/trend ---------------------------------------------------------

R1_TOP = {"subject", "span", "to", "stack", "currency", "buckets", "books",
          "stack_keys", "stack_options", "left_out", "few", "empty"}
R1_BUCKET = {"key", "label", "months", "so_far", "missing", "no_rate", "rate", "by"}
R1_BY = {"book", "spent", "back", "net", "inr_minor"}
R1_BOOK = {"book", "net", "spent", "back", "avg", "months_counted",
           "vs_prev_pct", "rows", "last_date"}
R1_STACK_KEY = {"key", "label", "id"}
R1_RATE = {"pair", "date", "exact", "text"}
R1_LEFT_OUT = {"kind", "currency", "amount_minor", "month", "reason"}
R1_SUBJECT = {"kind", "id", "name"}


@pytest.mark.parametrize("name", ["trend_merchant", "trend_type"])
def test_r1_keys_are_pinned(name):
    payload = load(name)
    # The type answer adds the merchants table (case 36, pending the spec's
    # Rulings's confirmation); the merchant answer does not carry it.
    assert keys(payload) == R1_TOP | ({"merchants"} if name == "trend_type" else set())
    assert keys(payload["subject"]) == R1_SUBJECT
    assert payload["buckets"], "a fixture with no bars draws nothing"
    for bucket in payload["buckets"]:
        assert keys(bucket) == R1_BUCKET
        assert MONTH.match(bucket["key"]) or re.match(r"^\d{4}-Q[1-4]$", bucket["key"])
        assert all(MONTH.match(m) for m in bucket["months"])
        for part in bucket["by"].values():
            assert keys(part) == R1_BY
        if bucket["rate"] is not None:
            assert keys(bucket["rate"]) == R1_RATE
    for book in payload["books"]:
        assert keys(book) == R1_BOOK
    for stack_key in payload["stack_keys"]:
        assert keys(stack_key) == R1_STACK_KEY
    for left in payload["left_out"]:
        assert keys(left) == R1_LEFT_OUT


@pytest.mark.parametrize("name", ["trend_merchant", "trend_type"])
def test_r1_amounts_are_whole_minor_units(name):
    """Money moves as integers in a stated currency, never a float."""
    payload = load(name)
    for bucket in payload["buckets"]:
        for part in bucket["by"].values():
            for field in ("spent", "back", "net"):
                assert type(part[field]) is int
            assert part["inr_minor"] is None or type(part["inr_minor"]) is int
    for book in payload["books"]:
        for field in ("net", "spent", "back", "avg"):
            assert type(book[field]) is int


@pytest.mark.parametrize("name", ["trend_merchant", "trend_type"])
def test_net_is_spent_less_refunded_everywhere(name):
    payload = load(name)
    for bucket in payload["buckets"]:
        for part in bucket["by"].values():
            assert part["net"] == part["spent"] - part["back"]
    for book in payload["books"]:
        assert book["net"] == book["spent"] - book["back"]


@pytest.mark.parametrize("name", ["trend_merchant", "trend_type"])
def test_a_books_summary_is_its_own_bars_and_books_are_never_added(name):
    """Cases 1 / 15: each book's net is the sum of its own segments; no key
    anywhere carries a figure across books."""
    payload = load(name)
    assert payload["stack"] == "book"
    for book in payload["books"]:
        segments = [b["by"][book["book"]]["net"] for b in payload["buckets"]
                    if book["book"] in b["by"]]
        assert sum(segments) == book["net"]
    text = json.dumps(payload).lower()
    assert "grand_total" not in text and '"total"' not in text


def test_the_average_divides_by_counted_months_through_mean_minor():
    """Cases 15 / 21: July is missing for Household, so 11 months; Larkspur 12."""
    by_book = {b["book"]: b for b in load("trend_merchant")["books"]}
    assert by_book["Household"]["months_counted"] == 11
    assert by_book["Larkspur"]["months_counted"] == 12
    assert by_book["Household"]["net"] == 264_000
    assert by_book["Household"]["avg"] == money.mean_minor(264_000, 11) == 24_000
    assert by_book["Larkspur"]["net"] == 48_840
    assert by_book["Larkspur"]["avg"] == money.mean_minor(48_840, 12) == 4_070


def test_a_missing_month_is_per_book_and_rows_that_arrived_still_count():
    payload = load("trend_merchant")
    july = next(b for b in payload["buckets"] if b["key"] == "2026-07")
    assert july["missing"] == {"Household": ["2026-07"]}
    assert "Household" not in july["by"]
    assert july["by"]["Larkspur"]["net"] == 4_200


def test_the_month_under_way_is_so_far_and_never_missing():
    payload = load("trend_merchant")
    last = payload["buckets"][-1]
    assert last["key"] == "2026-10" and last["so_far"] is True
    assert last["missing"] == {}
    assert payload["buckets"][0]["key"] == "2025-11"
    assert len(payload["buckets"]) == 12


def test_the_type_fixture_carries_rupees_at_the_saved_rate_and_a_no_rate_month():
    payload = load("trend_type")
    sep = next(b for b in payload["buckets"] if b["key"] == "2026-09")
    assert sep["rate"] == {"pair": "INR/SGD", "date": "2026-09-30", "exact": True, "text": "0.0155"}
    assert sep["by"]["Household"]["inr_minor"] == 345_000
    june = next(b for b in payload["buckets"] if b["key"] == "2026-06")
    assert june["no_rate"] is True and june["rate"] is None
    assert any(item["month"] == "2026-06" and item["amount_minor"] == 210_000
               and item["currency"] == "INR" for item in payload["left_out"])


def test_the_type_fixture_has_the_merchants_table_case_36_needs():
    """Dining: five named merchants, then "14 others", shares of the book's net."""
    table = load("trend_type")["merchants"]
    assert keys(table) == {"book", "net", "rows", "others"}
    for line in table["rows"]:
        assert keys(line) == {"id", "name", "net", "share_pct", "paid_in"}
    assert [line["name"] for line in table["rows"]] == [
        "Grab Food", "Din Tai Fung", "PS.Cafe", "Toast Box", "Swiggy"]
    assert table["rows"][-1]["paid_in"] == "INR"
    assert keys(table["others"]) == {"count", "net", "share_pct"}
    assert table["others"]["count"] == 14
    assert sum(line["net"] for line in table["rows"]) + table["others"]["net"] == table["net"] == 772_000


def test_no_stack_control_when_only_one_choice_draws_more_than_one_segment():
    """Case 18: Grab has two books and one type, so no control and book stacking."""
    merchant = load("trend_merchant")
    assert merchant["stack_options"] == [] and merchant["stack"] == "book"
    assert set(load("trend_type")["stack_options"]) == {"book", "merchant"}


def test_few_and_empty_are_null_when_the_chart_draws():
    for name in ("trend_merchant", "trend_type"):
        payload = load(name)
        assert payload["few"] is None and payload["empty"] is None


# --- R2: /api/trend/types ---------------------------------------------------

R2_TOP = {"book", "span", "to", "currency", "months", "types", "top", "missing", "left_out"}
R2_TYPE = {"id", "name", "slot", "net", "points"}
R2_POINT = {"month", "net", "estimate", "missing", "so_far"}


def test_r2_keys_are_pinned():
    payload = load("trend_types")
    assert keys(payload) == R2_TOP
    assert all(MONTH.match(m) for m in payload["months"])
    for line in payload["types"]:
        assert keys(line) == R2_TYPE
        assert [p["month"] for p in line["points"]] == payload["months"]
        for point in line["points"]:
            assert keys(point) == R2_POINT


def test_r2_top_four_have_slots_and_the_rest_are_grey():
    payload = load("trend_types")
    by_id = {t["id"]: t for t in payload["types"]}
    assert len(payload["top"]) == 4
    assert [by_id[i]["name"] for i in payload["top"]] == ["Dining", "Groceries", "Travel", "Education"]
    assert sorted(by_id[i]["slot"] for i in payload["top"]) == [1, 2, 3, 4]
    assert all(t["slot"] is None for t in payload["types"] if t["id"] not in payload["top"])


def test_r2_an_estimate_exists_only_where_the_month_is_missing_and_between_known_months():
    """Cases 32 / 34 / 55: Groceries July is the straight line between June and
    August, and it is in no total."""
    payload = load("trend_types")
    for line in payload["types"]:
        points = line["points"]
        for i, p in enumerate(points):
            if p["estimate"] is not None:
                assert p["missing"] and p["net"] is None
                assert 0 < i < len(points) - 1, "never an estimate at either end"
    groceries = next(t for t in payload["types"] if t["name"] == "Groceries")
    by_month = {p["month"]: p for p in groceries["points"]}
    assert by_month["2026-06"]["net"] == 52_000 and by_month["2026-08"]["net"] == 56_000
    assert by_month["2026-07"]["estimate"] == 54_000 and by_month["2026-07"]["missing"] is True
    assert sum(p["net"] for p in groceries["points"] if p["net"] is not None) == groceries["net"]


# --- R3: /api/accounts/<id>/balances ----------------------------------------

R3_TOP = {"account", "span", "to", "cur", "months", "head", "note"}
R3_ACCOUNT = {"id", "name", "currency", "type", "part_of"}
R3_MONTH = {"month", "balance", "estimate", "in", "out", "missing", "so_far", "rate", "note"}


@pytest.mark.parametrize("name", ["balances_sgd", "balances_inr"])
def test_r3_keys_are_pinned(name):
    payload = load(name)
    assert keys(payload) == R3_TOP
    assert keys(payload["account"]) == R3_ACCOUNT
    assert keys(payload["head"]) == {"balance", "date"}
    for month in payload["months"]:
        assert keys(month) == R3_MONTH
        assert type(month["in"]) is int and type(month["out"]) is int
        assert month["balance"] is None or type(month["balance"]) is int


def test_r3_sgd_reconciles_and_estimates_july():
    """Cases 32 / 37 / 54 / 56."""
    months = {m["month"]: m for m in load("balances_sgd")["months"]}
    assert months["2026-08"]["balance"] + months["2026-09"]["in"] - months["2026-09"]["out"] \
        == months["2026-09"]["balance"] == 5_500_000
    assert months["2026-07"]["missing"] is True and months["2026-07"]["balance"] is None
    assert months["2026-07"]["estimate"] == 5_320_000
    assert months["2026-10"]["so_far"] is True and months["2026-10"]["balance"] is None
    assert months["2026-10"]["missing"] is False
    assert months["2025-11"]["balance"] is None
    assert months["2025-11"]["note"] == "Balances start on 1 Jan 2026, as on the balance sheet"


def test_r3_head_card_names_the_last_month_end_not_a_future_statement():
    assert load("balances_sgd")["head"] == {"balance": 5_500_000, "date": "2026-09-30"}


def test_r3_inr_is_in_paise_with_the_month_end_rate():
    """Case 29: 5,20,000 rupees at 0.0155 is S$ 8,060.00; the rate rides each month."""
    payload = load("balances_inr")
    assert payload["account"]["currency"] == "INR" and payload["cur"] == "INR"
    sep = next(m for m in payload["months"] if m["month"] == "2026-09")
    assert (sep["balance"], sep["in"], sep["out"]) == (52_000_000, 6_240_000, 4_875_000)
    assert sep["rate"] == {"pair": "INR/SGD", "date": "2026-09-30", "exact": True, "text": "0.0155"}
    assert payload["note"] == "S$ at each month's end-of-month rate"


# --- Intermediate shapes ----------------------------------------------------


def test_series_fixture_is_month_book_stack_key_currency():
    """S1's answer: month -> book -> stack key -> currency -> {spent, back, net}."""
    series = load("trend_series")
    assert keys(series) == {"subject", "start", "end", "stack_by", "series"}
    for month, books in series["series"].items():
        assert MONTH.match(month)
        for stack_keys in books.values():
            for currencies in stack_keys.values():
                for currency, cell in currencies.items():
                    assert currency in {"SGD", "INR", "USD"}
                    assert keys(cell) == {"spent", "back", "net"}
                    assert cell["net"] == cell["spent"] - cell["back"]
    sep = series["series"]["2026-09"]
    assert sep["Household"]["Transport"]["SGD"] == {"spent": 21_240, "back": 1_800, "net": 19_440}
    assert sep["Larkspur"]["Transport"]["SGD"]["net"] == 6_100


def test_missing_fixture_is_months_per_account_and_the_estimate_inputs():
    """S3's answer: missing months per account; estimate(points) in and out."""
    missing = load("trend_missing")
    assert keys(missing) == {"today", "months", "accounts", "missing_months", "estimate"}
    assert DAY.match(missing["today"])
    assert missing["missing_months"]["everyday_card"] == ["2026-07"]
    assert missing["missing_months"]["larkspur_card"] == []
    flat = [m for months in missing["missing_months"].values() for m in months]
    assert missing["today"][:7] not in flat, "the current month is never missing"
    est = missing["estimate"]
    assert keys(est) == {"points", "expected"}
    assert est["points"] == [5_230_000, None, 5_410_000]
    assert est["expected"] == [5_230_000, 5_320_000, 5_410_000]


# --- The shared helpers -----------------------------------------------------


def test_helpers_build_a_statement_a_row_and_read_back(conn):
    card = tf.account(conn, "Everyday card")
    stmt = tf.statement(conn, card, "2026-03")
    cafe = tf.merchant(conn, "Kopi Lane", book="Household", type_name="Dining")
    tf.rule(conn, "KOPI LANE", cafe)
    tx = tf.row(conn, stmt, "KOPI LANE 21 MAR", 5200, day="2026-03-21", service_id=cafe)
    got = conn.execute(
        "SELECT t.amount_minor, t.book, t.service_id, t.date, a.currency, a.owner"
        " FROM transactions t JOIN statements s ON s.id = t.statement_id"
        " JOIN accounts a ON a.id = s.account_id WHERE t.id = ?", (tx,)).fetchone()
    assert tuple(got) == (5200, None, cafe, "2026-03-21", "SGD", "Household")
    assert conn.execute("SELECT statement_date FROM statements WHERE id = ?",
                        (stmt,)).fetchone()[0] == "2026-03-31"


def test_helpers_row_takes_book_and_type_when_given(conn):
    stmt = tf.statement(conn, tf.account(conn, "Everyday card"), "2026-03")
    tx = tf.row(conn, stmt, "GRAB", 1000, book="Moom", type_name="Transport")
    book, type_name = conn.execute(
        "SELECT t.book, ty.name FROM transactions t JOIN types ty ON ty.id = t.type_id"
        " WHERE t.id = ?", (tx,)).fetchone()
    assert (book, type_name) == ("Moom", "Transport")


def test_helpers_a_refund_is_a_negative_row_and_a_rupee_account_keeps_its_currency(conn):
    card = tf.account(conn, "Rupee card", currency="INR")
    stmt = tf.statement(conn, card, "2026-09")
    tx = tf.row(conn, stmt, "REFUND", -59000, day="2026-09-05", flow="refund")
    cur, amount = conn.execute(
        "SELECT a.currency, t.amount_minor FROM transactions t"
        " JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id"
        " WHERE t.id = ?", (tx,)).fetchone()
    assert (cur, amount) == ("INR", -59000)


def test_helpers_a_statement_may_be_dated_any_month_including_leap_february(conn):
    card = tf.account(conn, "Everyday card")
    tf.statement(conn, card, "2028-02")
    dates = [r[0] for r in conn.execute("SELECT statement_date FROM statements")]
    assert dates == ["2028-02-29"]


def test_helpers_seed_a_rate_and_an_anchor(conn):
    acct = tf.account(conn, "Rupee savings", type="bank", currency="INR")
    tf.seed_rate(conn, "INR/SGD", "2026-09-30", "0.0155")
    tf.anchor(conn, acct, "2026-09-30", 52_000_000)
    assert rates.saved(conn, "INR/SGD", "2026-09-30")["rate"] == "0.0155"
    amount, source = conn.execute(
        "SELECT amount, source FROM anchors WHERE account_id = ?", (acct,)).fetchone()
    assert (amount, source) == (52_000_000, "statement")


def test_helpers_never_open_a_database_themselves():
    """The conftest guard fails any test that opens the real file; the helpers
    only ever take a connection."""
    import inspect
    source = inspect.getsource(tf)
    assert "fin.db" not in source and "connect(" not in source


def test_the_dev_harness_lists_every_fixture_and_is_not_served_by_fin():
    harness = (FIXTURES / "harness.html").read_text(encoding="utf-8")
    for fixture in FIXTURES.glob("*.json"):
        assert f"'{fixture.stem}'" in harness, f"{fixture.name} is not drawn by the harness"
    assert FIXTURES.is_relative_to(Path(__file__).parent), "fixtures live under tests/, outside /static"
