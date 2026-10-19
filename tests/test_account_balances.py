"""S4, account balances (fin-surfaces 30; spec 15, Server S4): an account's
month-end balance and its money in and out per month, the series the account
page's chart draws.

Figures in and out through account_balances.balances() on the `conn` fixture,
seeded with tests/trend_fixtures.py. Cases 35, 37, 38, 56 and 61. Amounts are
whole cents; a bank balance is positive; a row's amount is positive for money
out. Every account, merchant and figure is invented.
"""

from __future__ import annotations

import pytest

import account_balances
import balance_sheet
import card_balance
from trend_fixtures import account, anchor, month_end, row, statement

START_NOTE = "Balances start on 1 Jan 2026, as on the balance sheet"
NO_BALANCE = "No balance yet: import a statement or add an anchor"


def months_between(first: str, last: str) -> list[str]:
    year, mon = int(first[:4]), int(first[5:])
    out = []
    while f"{year:04d}-{mon:02d}" <= last:
        out.append(f"{year:04d}-{mon:02d}")
        year, mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
    return out


def account_row(conn, account_id: int):
    """The account row balances() takes, as balance_on does."""
    return conn.execute(
        "SELECT id, type, currency FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()


def by_month(result: dict) -> dict:
    return {m["month"]: m for m in result["months"]}


def printed_statement(conn, account_id: int, day: str) -> int:
    """A statement as printed: closing on `day`, holding the rows it printed."""
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename, printed) VALUES (?, ?, ?, 1)",
        (account_id, day, f"printed-{day}.pdf"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def a_usual_month(conn, account_id: int, month: str) -> None:
    """S$ 11,000.00 in and S$ 10,500.00 out: salary in, a transfer and a bill out."""
    sid = statement(conn, account_id, month)
    row(conn, sid, "SALARY", -1_100_000, flow="income", day=f"{month}-01")
    row(conn, sid, "TO EVERYDAY CARD", 1_000_000, flow="transfer", day=f"{month}-05")
    row(conn, sid, "UTILITIES", 50_000, day=f"{month}-20")


@pytest.fixture
def everyday_savings(conn):
    """Everyday savings, a S$ bank account, Nov 2024 to Oct 2026, every month's
    statement in. Its balance on 31 Jan 2026 is S$ 50,600.00; it gains
    S$ 500.00 a month to August's S$ 54,100.00; September brings S$ 11,550.00
    in and S$ 10,650.00 out, to S$ 55,000.00."""
    savings = account(conn, "Everyday savings", type="bank")
    for month in months_between("2024-11", "2026-10"):
        if month == "2026-09":
            sid = statement(conn, savings, month)
            row(conn, sid, "SALARY", -1_100_000, flow="income", day="2026-09-01")
            row(conn, sid, "INTEREST", -10_000, flow="income", day="2026-09-30")
            row(conn, sid, "REFUND HOME INSURANCE", -45_000, flow="refund", day="2026-09-18")
            row(conn, sid, "TO EVERYDAY CARD", 1_000_000, flow="transfer", day="2026-09-05")
            row(conn, sid, "UTILITIES", 65_000, day="2026-09-20")
        else:
            a_usual_month(conn, savings, month)
    anchor(conn, savings, "2026-01-31", 5_060_000)
    return savings


# --- Case 37: balance and in / out ------------------------------------------

def test_case_37_september_balance_and_in_out(conn, everyday_savings):
    months = months_between("2025-11", "2026-10")
    got = by_month(account_balances.balances(conn, account_row(conn, everyday_savings), months))

    assert [m for m in got] == months
    sep = got["2026-09"]
    assert (sep["balance"], sep["in"], sep["out"]) == (5_500_000, 1_155_000, 1_065_000)
    assert got["2026-08"]["balance"] == 5_410_000


def test_case_37_every_month_reconciles(conn, everyday_savings):
    """The month before's balance, plus in, less out, is the month's balance,
    on every pair of month ends the line draws."""
    months = months_between("2026-01", "2026-09")
    got = account_balances.balances(conn, account_row(conn, everyday_savings), months)["months"]
    for before, month in zip(got, got[1:]):
        assert before["balance"] + month["in"] - month["out"] == month["balance"], month["month"]


def test_case_37_balance_is_balance_on_at_each_calendar_month_end(conn, everyday_savings):
    months = months_between("2026-01", "2026-10")
    acct = account_row(conn, everyday_savings)
    got = account_balances.balances(conn, acct, months)["months"]
    assert [m["balance"] for m in got] == [
        balance_sheet.balance_on(conn, acct, month_end(m)) for m in months
    ]


def test_case_37_in_and_out_count_a_row_on_the_day_its_statement_counts_it(conn):
    """A late-posting row (dated 30 Jul, printed on the statement that closes
    5 Sep, after the one that closed 5 Aug) counts on 6 Aug, as the balance
    counts it, so August's in / out and the month ends still reconcile."""
    savings = account(conn, "Everyday savings", type="bank")
    printed_statement(conn, savings, "2026-08-05")
    anchor(conn, savings, "2026-08-05", 1_000_000)
    sep = printed_statement(conn, savings, "2026-09-05")
    row(conn, sep, "LATE POSTING", 20_000, day="2026-07-30")
    row(conn, sep, "SALARY", -300_000, flow="income", day="2026-09-01")

    got = account_balances.balances(conn, account_row(conn, savings), ["2026-07", "2026-08", "2026-09"])
    months = by_month(got)
    assert (months["2026-07"]["in"], months["2026-07"]["out"]) == (0, 0)
    assert (months["2026-08"]["in"], months["2026-08"]["out"]) == (0, 20_000)
    assert months["2026-08"]["balance"] == 980_000
    assert months["2026-08"]["balance"] + months["2026-09"]["in"] - months["2026-09"]["out"] \
        == months["2026-09"]["balance"] == 1_280_000


def test_a_month_with_no_rows_is_zero_in_and_out_not_left_out(conn, everyday_savings):
    """A month in the list with no rows still comes back, at 0 in and 0 out,
    and keeps its balance, beside a month that has rows."""
    got = by_month(account_balances.balances(
        conn, account_row(conn, everyday_savings), ["2026-09", "2026-11"]))
    assert (got["2026-09"]["in"], got["2026-09"]["out"]) == (1_155_000, 1_065_000)
    assert (got["2026-11"]["in"], got["2026-11"]["out"]) == (0, 0)
    assert got["2026-11"]["balance"] == 5_550_000  # October's S$ 500.00 gain, then nothing


# --- Case 35: no anchor yet ------------------------------------------------

def test_case_35_no_balance_before_the_first_anchor(conn):
    card = account(conn, "Everyday card")
    for month in ("2026-01", "2026-02", "2026-03", "2026-04"):
        sid = statement(conn, card, month)
        row(conn, sid, "GROCER", 20_000, day=f"{month}-10")
        row(conn, sid, "GROCER", 30_000, day=f"{month}-20")
    anchor(conn, card, "2026-03-15", -80_000)

    got = by_month(account_balances.balances(
        conn, account_row(conn, card), ["2026-01", "2026-02", "2026-03", "2026-04"]))

    assert got["2026-01"]["balance"] is None and got["2026-02"]["balance"] is None
    assert got["2026-03"]["balance"] == -110_000  # S$ 800.00 owed, less the 20 Mar S$ 300.00
    assert got["2026-04"]["balance"] == -160_000
    # The gap is a gap: it has rows, and no balance, and no note claiming one.
    assert got["2026-01"]["out"] == 50_000
    assert got["2026-01"]["note"] is None
    assert account_balances.balances(conn, account_row(conn, card), ["2026-01"])["no_balance"] is None


# --- Case 56: before the balance sheet starts --------------------------------

def test_case_56_two_years_draw_in_out_and_start_the_line_in_january(conn, everyday_savings):
    """An anchor dated before 1 Jan 2026 still shows nothing before the sheet
    starts; the line starts at 31 Jan 2026."""
    anchor(conn, everyday_savings, "2025-06-30", 4_000_000)
    months = months_between("2024-11", "2026-10")
    got = account_balances.balances(conn, account_row(conn, everyday_savings), months)["months"]

    assert len(got) == 24
    assert all(m["in"] == 1_100_000 or m["month"] == "2026-09" for m in got)
    assert all(m["out"] > 0 for m in got)
    before = [m for m in got if m["month"] < "2026-01"]
    assert len(before) == 14
    assert all(m["balance"] is None and m["note"] == START_NOTE for m in before)
    jan = by_month({"months": got})["2026-01"]
    assert jan["balance"] == 5_060_000 and jan["note"] is None


# --- Case 61: an account with no anchor at all -------------------------------

def test_case_61_no_anchor_at_all(conn):
    card = account(conn, "Rewards card")
    for month in ("2026-08", "2026-09"):
        sid = statement(conn, card, month)
        row(conn, sid, "BOOKSHOP", 4_500, day=f"{month}-03")
        row(conn, sid, "REFUND BOOKSHOP", -1_500, flow="refund", day=f"{month}-09")

    got = account_balances.balances(conn, account_row(conn, card), ["2026-08", "2026-09"])

    assert got["no_balance"] == NO_BALANCE
    assert all(m["balance"] is None for m in got["months"])
    assert [(m["in"], m["out"]) for m in got["months"]] == [(1_500, 4_500), (1_500, 4_500)]


# --- Case 38: a split card's part --------------------------------------------

MAIN, PART = "Sample Infinite Card 3696", "Sample Infinite Card 7436 (MK)"


@pytest.fixture
def split_card(conn, monkeypatch):
    """A card held as one bill, rows split by cardholder: S$ 1,000.00 owed on
    31 Jul 2026, anchored on the main cardholder's account; August brings
    S$ 200.00 on the main account and S$ 300.00 on the part. A stale anchor
    on the part is never read."""
    monkeypatch.setattr(card_balance, "CARDHOLDER_ACCOUNTS", {
        ("7436", "SAMPLE HOLDER"): MAIN,
        ("7436", "OTHER HOLDER"): PART,
    })
    monkeypatch.setattr(card_balance, "BALANCE_ACCOUNTS", {"7436": MAIN})
    main = account(conn, MAIN, last_four="3696")
    part = account(conn, PART, last_four="7436")
    anchor(conn, main, "2026-07-31", -100_000)
    anchor(conn, part, "2026-07-31", -999_900)
    row(conn, statement(conn, main, "2026-08"), "GROCER", 20_000, day="2026-08-10")
    part_aug = statement(conn, part, "2026-08")
    row(conn, part_aug, "FLORIST", 30_000, day="2026-08-12")
    row(conn, part_aug, "REFUND FLORIST", -5_000, flow="refund", day="2026-08-14")
    return {"main": main, "part": part}


def test_case_38_a_part_charts_the_whole_card_and_says_so(conn, split_card):
    got = account_balances.balances(conn, account_row(conn, split_card["part"]), ["2026-07", "2026-08"])

    assert got["charted_id"] == split_card["main"]
    assert got["card"]["text"] == "the whole card: parts 3696 and 7436"
    assert [p["id"] for p in got["card"]["parts"]] == [split_card["main"], split_card["part"]]
    months = by_month(got)
    assert months["2026-07"]["balance"] == -100_000
    assert (months["2026-08"]["in"], months["2026-08"]["out"]) == (5_000, 50_000)
    assert months["2026-08"]["balance"] == -145_000
    assert months["2026-07"]["balance"] + months["2026-08"]["in"] - months["2026-08"]["out"] \
        == months["2026-08"]["balance"]


def test_case_38_the_main_account_charts_the_same_whole_card(conn, split_card):
    whole = account_balances.balances(conn, account_row(conn, split_card["main"]), ["2026-07", "2026-08"])
    part = account_balances.balances(conn, account_row(conn, split_card["part"]), ["2026-07", "2026-08"])
    assert whole["months"] == part["months"]
    assert whole["card"]["text"] == "the whole card: parts 3696 and 7436"


def test_an_account_on_no_split_card_charts_itself(conn, everyday_savings):
    got = account_balances.balances(conn, account_row(conn, everyday_savings), ["2026-09"])
    assert got["charted_id"] == everyday_savings and got["card"] is None
