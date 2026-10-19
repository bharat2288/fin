"""S3: missing months and straight-line estimates (fin-surfaces 27; spec 15).

Cases 31, 32, 33, 34, 54 and 55, through missing.missing_months and
missing.estimate. Today is passed in, never read from the clock. Synthetic
accounts and statements only, through tests/trend_fixtures.py.
"""

import datetime
import json
from pathlib import Path

import missing
from tests import trend_fixtures as tf

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "trend" / "trend_missing.json").read_text(encoding="utf-8")
)
TODAY = datetime.date.fromisoformat(FIXTURE["today"])  # 19 Oct 2026
SPAN_12M = FIXTURE["months"]  # Nov 2025 to Oct 2026


def statements(conn, account_id, months):
    for month in months:
        tf.statement(conn, account_id, month)


def dated_statement(conn, account_id, day):
    """A statement dated `day` rather than its month's last day."""
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, ?)",
        (account_id, day, f"sample-{day}.csv"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def grab_cards(conn):
    """Case 31: the Everyday card's July statement is not in; the Larkspur card's is."""
    everyday = tf.account(conn, "Everyday card")
    larkspur = tf.account(conn, "Larkspur card", owner="Moom")
    statements(conn, everyday, [m for m in SPAN_12M[:-1] if m != "2026-07"])
    statements(conn, larkspur, SPAN_12M[:-1])
    return everyday, larkspur


# --- missing_months ---------------------------------------------------------


def test_case_31_july_is_missing_on_the_everyday_card_and_not_on_larkspurs(conn):
    everyday, larkspur = grab_cards(conn)
    assert missing.missing_months(conn, [everyday], SPAN_12M, TODAY) \
        == FIXTURE["missing_months"]["everyday_card"] == ["2026-07"]
    assert missing.missing_months(conn, [larkspur], SPAN_12M, TODAY) \
        == FIXTURE["missing_months"]["larkspur_card"] == []


def test_a_month_is_missing_when_any_account_in_the_set_has_not_sent(conn):
    """Missing is decided per book over the accounts the caller passes."""
    everyday, larkspur = grab_cards(conn)
    assert missing.missing_months(conn, [larkspur, everyday], SPAN_12M, TODAY) == ["2026-07"]


def test_rows_that_arrived_do_not_make_a_missing_month_present(conn):
    """Case 31 / 55: a July row on the August statement still counts, and July
    is still missing (unlike stat-cards, there is no "no row in the month" test)."""
    everyday, _ = grab_cards(conn)
    august = conn.execute(
        "SELECT id FROM statements WHERE account_id = ? AND statement_date = '2026-08-31'",
        (everyday,),
    ).fetchone()[0]
    grab = tf.merchant(conn, "Grab", book="Household", type_name="Transport")
    tf.row(conn, august, "GRAB 15 JUL", 1_250, service_id=grab, day="2026-07-15")
    assert missing.missing_months(conn, [everyday], SPAN_12M, TODAY) == ["2026-07"]


def test_case_54_the_month_under_way_is_never_missing(conn):
    """Everyday savings on 19 Oct 2026: statements through September; October's
    is not due yet."""
    savings = tf.account(conn, "Everyday savings", type="bank")
    statements(conn, savings, SPAN_12M[:-1])
    assert missing.missing_months(conn, [savings], SPAN_12M, TODAY) == []


def test_the_month_under_way_is_never_missing_even_on_its_first_day(conn):
    savings = tf.account(conn, "Everyday savings", type="bank")
    statements(conn, savings, ["2026-07", "2026-08"])
    first_of_october = datetime.date(2026, 10, 1)
    assert missing.missing_months(conn, [savings], SPAN_12M, first_of_october) == ["2026-09"]


def test_months_after_today_are_never_missing(conn):
    """A span that runs past today (to= in the future) marks nothing ahead."""
    card = tf.account(conn, "Everyday card")
    statements(conn, card, ["2026-08", "2026-09"])
    assert missing.missing_months(conn, [card], ["2026-09", "2026-10", "2026-11"], TODAY) == []


def test_case_34_a_due_statement_at_the_end_of_the_span_is_missing(conn):
    """September's statement is due on 19 Oct and not in: missing (the line
    leaves it open; see the estimate tests)."""
    card = tf.account(conn, "Everyday card")
    statements(conn, card, SPAN_12M[:-2])  # through August
    assert missing.missing_months(conn, [card], SPAN_12M, TODAY) == ["2026-09"]


def test_months_before_the_first_imported_statement_are_not_missing(conn):
    card = tf.account(conn, "Everyday card")
    statements(conn, card, ["2026-03", "2026-04", "2026-06", "2026-07", "2026-08", "2026-09"])
    assert missing.missing_months(conn, [card], SPAN_12M, TODAY) == ["2026-05"]


def test_the_first_statement_counts_even_when_it_falls_before_the_span(conn):
    card = tf.account(conn, "Everyday card")
    statements(conn, card, ["2025-01"] + SPAN_12M[2:-1])  # nothing in Nov or Dec 2025
    assert missing.missing_months(conn, [card], SPAN_12M, TODAY) == ["2025-11", "2025-12"]


def test_a_statement_belongs_to_the_month_of_its_date(conn):
    """A statement dated 3 Jul fills July, not June."""
    card = tf.account(conn, "Everyday card")
    statements(conn, card, ["2026-05", "2026-08", "2026-09"])
    dated_statement(conn, card, "2026-07-03")
    assert missing.missing_months(conn, [card], SPAN_12M, TODAY) == ["2026-06"]


def test_an_account_with_no_statement_is_never_missing(conn):
    card = tf.account(conn, "New card")
    assert missing.missing_months(conn, [card], SPAN_12M, TODAY) == []


def test_an_inactive_account_is_never_missing(conn):
    card = tf.account(conn, "Old card", status="archived")
    statements(conn, card, ["2025-11", "2025-12"])
    assert missing.missing_months(conn, [card], SPAN_12M, TODAY) == []


def test_an_account_of_a_kind_without_statements_is_never_missing(conn):
    loan = tf.account(conn, "Home loan", type="loan")
    statements(conn, loan, ["2025-11"])
    assert missing.missing_months(conn, [loan], SPAN_12M, TODAY) == []


def test_no_accounts_and_no_months(conn):
    card = tf.account(conn, "Everyday card")
    statements(conn, card, ["2025-11"])
    assert missing.missing_months(conn, [], SPAN_12M, TODAY) == []
    assert missing.missing_months(conn, [card], [], TODAY) == []


# --- estimate ---------------------------------------------------------------


def test_estimate_matches_the_contract_fixture():
    est = FIXTURE["estimate"]
    assert missing.estimate(est["points"]) == est["expected"]


def test_case_32_an_estimate_on_a_line():
    """June S$ 52,300.00, July missing, August S$ 54,100.00 -> S$ 53,200.00."""
    assert missing.estimate([5_230_000, None, 5_410_000]) == [5_230_000, 5_320_000, 5_410_000]


def test_case_33_two_missing_months_sit_on_the_straight_line():
    """June S$ 52,300.00, September S$ 55,000.00 -> S$ 53,200.00, S$ 54,100.00."""
    assert missing.estimate([5_230_000, None, None, 5_500_000]) \
        == [5_230_000, 5_320_000, 5_410_000, 5_500_000]


def test_case_34_a_gap_at_either_end_is_left_open():
    assert missing.estimate([None, 5_230_000, None, 5_410_000, None]) \
        == [None, 5_230_000, 5_320_000, 5_410_000, None]
    assert missing.estimate([None, None, 5_230_000]) == [None, None, 5_230_000]
    assert missing.estimate([5_230_000, None, None]) == [5_230_000, None, None]


def test_case_54_the_line_ends_at_the_last_month_end():
    """Everyday savings, 12M on 19 Oct: September's S$ 55,000.00 is the last
    point; July is bridged; October (so far) has no point and none is drawn."""
    line = [None, None, 5_000_000, 5_050_000, 5_100_000, 5_150_000, 5_200_000,
            5_230_000, None, 5_410_000, 5_500_000, None]
    assert missing.estimate(line) == [None, None, 5_000_000, 5_050_000, 5_100_000, 5_150_000,
                                      5_200_000, 5_230_000, 5_320_000, 5_410_000, 5_500_000, None]


def test_case_55_groceries_july_on_types_over_time(conn):
    """Household, 12M: the Everyday card's July statement is not in. The caller
    blanks the missing month's point; June S$ 520.00, August S$ 560.00 ->
    S$ 540.00, which no total counts."""
    everyday, _ = grab_cards(conn)
    nets = {m: 0 for m in SPAN_12M}
    nets.update({"2026-06": 52_000, "2026-07": 0, "2026-08": 56_000})
    gaps = set(missing.missing_months(conn, [everyday], SPAN_12M, TODAY))
    points = [None if m in gaps else nets[m] for m in SPAN_12M]
    line = dict(zip(SPAN_12M, missing.estimate(points)))
    assert line["2026-07"] == 54_000
    assert sum(nets.values()) == 108_000, "the estimate is in no total"


def test_a_line_below_zero_is_bridged_too():
    """Case 44: a type's refunds can take a month below zero."""
    assert missing.estimate([-8_000, None, 4_000]) == [-8_000, -2_000, 4_000]


def test_an_estimate_between_cents_rounds_half_even():
    """Minor units in, minor units out, half-even as money.mean_minor."""
    assert missing.estimate([0, None, 1]) == [0, 0, 1]
    assert missing.estimate([1, None, 2]) == [1, 2, 2]
    assert missing.estimate([0, None, None, 1]) == [0, 0, 1, 1]


def test_estimate_leaves_a_line_with_no_gaps_and_an_empty_line_alone():
    assert missing.estimate([]) == []
    assert missing.estimate([None, None]) == [None, None]
    assert missing.estimate([1, 2, 3]) == [1, 2, 3]


def test_estimate_does_not_change_its_input():
    points = [5_230_000, None, 5_410_000]
    missing.estimate(points)
    assert points == [5_230_000, None, 5_410_000]
