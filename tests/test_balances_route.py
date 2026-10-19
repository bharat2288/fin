"""R3, GET /api/accounts/<id>/balances (fin-surfaces 41; spec 15, Server
routes R3): an account's month-end balance, its estimate where a statement is
missing, money in and out per month, the month under way, and the head card's
month end, in the account's currency or in S$ at each month's saved rate.

Figures in and out through the route on the Flask test client, seeded with
tests/trend_fixtures.py. Cases 28 (the no-fetch read on a rupee account), 29,
37, 49, 54, 56, 61 and 63, and the shape test against the contract fixtures.
Today is 19 Oct 2026. Amounts are whole minor units (cents, paise); a row's
amount is positive for money out. Every account and figure is invented.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

import account_balances_api
import rates
from trend_fixtures import account, anchor, row, seed_rate, statement

FIXTURES = Path(__file__).parent / "fixtures" / "trend"
TODAY = datetime.date(2026, 10, 19)
START_NOTE = "Balances start on 1 Jan 2026, as on the balance sheet"
SWITCH_NOTE = "S$ at each month's end-of-month rate"


@pytest.fixture(autouse=True)
def nineteenth_of_october(monkeypatch):
    monkeypatch.setattr(account_balances_api, "today", lambda: TODAY)


@pytest.fixture
def no_fetch(monkeypatch):
    """Case 28 / 50: a GET read never reaches outside fin for a rate."""

    def refuse(_url):
        pytest.fail("the balances read tried to fetch a rate")

    monkeypatch.setattr(rates, "_http_get", refuse)


def months_between(first: str, last: str) -> list[str]:
    year, mon = int(first[:4]), int(first[5:])
    out = []
    while f"{year:04d}-{mon:02d}" <= last:
        out.append(f"{year:04d}-{mon:02d}")
        year, mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
    return out


def get(client, account_id: int, query: str = ""):
    return client.get(f"/api/accounts/{account_id}/balances{query}")


def answer(client, account_id: int, query: str = "") -> dict:
    resp = get(client, account_id, query)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def by_month(payload: dict) -> dict:
    return {m["month"]: m for m in payload["months"]}


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# --- seeds -------------------------------------------------------------------


@pytest.fixture
def everyday_savings(conn):
    """Everyday savings, a S$ bank account, statements Nov 2024 to Sep 2026,
    and October's rows so far on a mid-month export (no October month-end
    statement can exist on 19 Oct). Balance S$ 50,600.00 on 31 Jan 2026; a
    usual month is S$ 11,000.00 in and S$ 10,500.00 out, so August ends at
    S$ 54,100.00; September brings S$ 11,550.00 in and S$ 10,650.00 out, every
    row counted, transfers included."""
    savings = account(conn, "Everyday savings", type="bank")
    for month in months_between("2024-11", "2026-09"):
        sid = statement(conn, savings, month)
        if month == "2026-09":
            row(conn, sid, "SALARY", -1_100_000, flow="income", day="2026-09-01")
            row(conn, sid, "INTEREST", -10_000, flow="income", day="2026-09-30")
            row(conn, sid, "REFUND HOME INSURANCE", -45_000, flow="refund", day="2026-09-18")
            row(conn, sid, "TO EVERYDAY CARD", 1_000_000, flow="transfer", day="2026-09-05")
            row(conn, sid, "UTILITIES", 65_000, day="2026-09-20")
        else:
            row(conn, sid, "SALARY", -1_100_000, flow="income", day=f"{month}-01")
            row(conn, sid, "TO EVERYDAY CARD", 1_000_000, flow="transfer", day=f"{month}-05")
            row(conn, sid, "UTILITIES", 50_000, day=f"{month}-20")
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, ?)",
        (savings, "2026-10-15", "export-2026-10-15.csv"),
    )
    conn.commit()
    october = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row(conn, october, "SALARY", -1_100_000, flow="income", day="2026-10-01")
    row(conn, october, "TO EVERYDAY CARD", 1_000_000, flow="transfer", day="2026-10-05")
    anchor(conn, savings, "2026-01-31", 5_060_000)
    return savings


@pytest.fixture
def rupee_savings(conn):
    """Rupee savings, a ₹ bank account, statements Nov 2025 to Sep 2026; a
    usual month ₹ 50,000 in and ₹ 48,000 out; September ₹ 62,400 in and
    ₹ 48,750 out to a month end of ₹ 5,20,000. Month-end rates 0.0155 saved
    for every month end from Nov 2025 to Sep 2026."""
    savings = account(conn, "Rupee savings", type="bank", currency="INR")
    for month in months_between("2025-11", "2026-09"):
        sid = statement(conn, savings, month)
        money_in, money_out = (6_240_000, 4_875_000) if month == "2026-09" else (5_000_000, 4_800_000)
        row(conn, sid, "SALARY", -money_in, flow="income", day=f"{month}-01")
        row(conn, sid, "RENT", money_out, day=f"{month}-10")
    anchor(conn, savings, "2026-09-30", 52_000_000)
    for month in months_between("2025-11", "2026-09"):
        seed_rate(conn, "INR/SGD", _end(month), "0.0155")
    return savings


def _end(month: str) -> str:
    import calendar
    year, mon = int(month[:4]), int(month[5:])
    return f"{month}-{calendar.monthrange(year, mon)[1]:02d}"


# --- the shape ---------------------------------------------------------------


@pytest.mark.parametrize("fixture_name, seed, query", [
    ("balances_sgd", "everyday_savings", "?span=12M&to=2026-10"),
    ("balances_inr", "rupee_savings", "?span=12M&to=2026-10&cur=INR"),
])
def test_the_answer_has_the_contract_fixtures_keys(client, request, fixture_name, seed, query):
    account_id = request.getfixturevalue(seed)
    got, pinned = answer(client, account_id, query), load(fixture_name)
    assert set(got) == set(pinned)
    assert set(got["account"]) == set(pinned["account"])
    assert set(got["head"]) == set(pinned["head"])
    assert [m["month"] for m in got["months"]] == [m["month"] for m in pinned["months"]]
    for month in got["months"]:
        assert set(month) == set(pinned["months"][0])
        assert type(month["in"]) is int and type(month["out"]) is int
        if month["rate"] is not None:
            assert set(month["rate"]) == set(pinned["months"][0]["rate"] or month["rate"])
    assert (got["span"], got["to"], got["cur"]) == (pinned["span"], pinned["to"], pinned["cur"])


# --- case 37: balance and in / out -------------------------------------------


def test_case_37_the_balance_line_and_in_out_reconcile(client, everyday_savings):
    payload = answer(client, everyday_savings, "?span=12M")
    months = by_month(payload)
    assert list(months) == months_between("2025-11", "2026-10")
    sep, aug = months["2026-09"], months["2026-08"]
    assert (sep["balance"], sep["in"], sep["out"]) == (5_500_000, 1_155_000, 1_065_000)
    assert aug["balance"] == 5_410_000
    assert aug["balance"] + sep["in"] - sep["out"] == sep["balance"]
    assert all(m["estimate"] is None and m["missing"] is False for m in payload["months"])
    assert payload["account"] == {"id": everyday_savings, "name": "Everyday savings",
                                  "currency": "SGD", "type": "bank", "part_of": None}
    assert payload["cur"] == "SGD" and payload["note"] is None
    assert all(m["rate"] is None for m in payload["months"])


# --- case 54: the month under way --------------------------------------------


def test_case_54_october_is_so_far_with_bars_and_no_balance_point(client, everyday_savings):
    payload = answer(client, everyday_savings)
    october = by_month(payload)["2026-10"]
    assert october["so_far"] is True and october["missing"] is False
    assert october["balance"] is None and october["estimate"] is None
    assert (october["in"], october["out"]) == (1_100_000, 1_000_000)
    assert [m["month"] for m in payload["months"] if m["so_far"]] == ["2026-10"]
    assert payload["head"] == {"balance": 5_500_000, "date": "2026-09-30"}
    assert payload["to"] == "2026-10" and payload["span"] == "12M"


# --- case 56: before the balance sheet starts --------------------------------


def test_case_56_two_years_draws_every_bar_and_the_line_from_january(client, everyday_savings):
    months = answer(client, everyday_savings, "?span=2Y")["months"]
    assert [m["month"] for m in months] == months_between("2024-11", "2026-10")
    assert all(m["in"] > 0 and m["out"] > 0 for m in months)
    before = [m for m in months if m["month"] < "2026-01"]
    assert len(before) == 14
    assert all(m["balance"] is None and m["estimate"] is None and m["note"] == START_NOTE
               for m in before)
    january = next(m for m in months if m["month"] == "2026-01")
    assert (january["balance"], january["note"]) == (5_060_000, None)


def test_all_reaches_back_to_the_first_row(client, everyday_savings):
    payload = answer(client, everyday_savings, "?span=All")
    assert payload["months"][0]["month"] == "2024-11"
    assert payload["months"][-1]["month"] == "2026-10"
    assert len(payload["months"]) == 24


# --- cases 32 / 54 on the route: a missing month and its estimate -----------


def test_a_missing_month_has_no_balance_but_an_estimate_between_its_neighbours(client, conn):
    spare = account(conn, "Spare savings", type="bank")
    for month in months_between("2026-01", "2026-09"):
        if month != "2026-07":
            statement(conn, spare, month)
    anchor(conn, spare, "2026-06-30", 5_230_000)
    anchor(conn, spare, "2026-08-31", 5_410_000)
    anchor(conn, spare, "2026-09-30", 5_500_000)
    payload = answer(client, spare)
    months = by_month(payload)
    july = months["2026-07"]
    assert july["missing"] is True and july["balance"] is None
    assert july["estimate"] == 5_320_000
    assert (months["2026-06"]["balance"], months["2026-08"]["balance"]) == (5_230_000, 5_410_000)
    assert [m["month"] for m in payload["months"] if m["missing"]] == ["2026-07"]
    assert [m["month"] for m in payload["months"] if m["estimate"] is not None] == ["2026-07"]
    assert payload["head"] == {"balance": 5_500_000, "date": "2026-09-30"}


# --- case 61: no anchor at all -----------------------------------------------


def test_case_61_no_anchor_means_no_balance_anywhere_and_the_bars_still_draw(client, conn):
    plain = account(conn, "Plain savings", type="bank")
    for month in months_between("2026-01", "2026-09"):
        sid = statement(conn, plain, month)
        row(conn, sid, "SALARY", -300_000, flow="income", day=f"{month}-01")
        row(conn, sid, "BILLS", 120_000, day=f"{month}-15")
    payload = answer(client, plain)
    assert all(m["balance"] is None and m["estimate"] is None for m in payload["months"])
    assert payload["head"] == {"balance": None, "date": None}
    sep = by_month(payload)["2026-09"]
    assert (sep["in"], sep["out"]) == (300_000, 120_000)


# --- case 29: a rupee account opens in ₹ -------------------------------------


def test_case_29_a_rupee_account_opens_in_rupees_with_each_months_rate(client, rupee_savings):
    payload = answer(client, rupee_savings)
    assert payload["cur"] == "INR" and payload["note"] == SWITCH_NOTE
    sep = by_month(payload)["2026-09"]
    assert (sep["balance"], sep["in"], sep["out"]) == (52_000_000, 6_240_000, 4_875_000)
    assert sep["rate"] == {"pair": "INR/SGD", "date": "2026-09-30", "exact": True, "text": "0.0155"}
    assert payload["head"] == {"balance": 52_000_000, "date": "2026-09-30"}


def test_case_29_the_switch_values_each_month_in_sgd_at_its_month_end_rate(client, rupee_savings):
    payload = answer(client, rupee_savings, "?cur=SGD")
    assert payload["cur"] == "SGD" and payload["note"] == SWITCH_NOTE
    sep = by_month(payload)["2026-09"]
    # ₹ 5,20,000 at 0.0155 is S$ 8,060.00; ₹ 62,400 is S$ 967.20; ₹ 48,750 is
    # S$ 755.625, rounded half-even to S$ 755.62.
    assert (sep["balance"], sep["in"], sep["out"]) == (806_000, 96_720, 75_562)
    assert sep["rate"]["text"] == "0.0155"
    assert payload["head"] == {"balance": 806_000, "date": "2026-09-30"}


def test_case_29_an_sgd_account_has_no_switch(client, everyday_savings):
    resp = get(client, everyday_savings, "?cur=INR")
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "cur must be SGD for this account"}
    assert answer(client, everyday_savings, "?cur=SGD")["cur"] == "SGD"


# --- case 28 / 50: opening never fetches, on a seeded no-rate month ----------


def test_case_28_a_month_with_no_saved_rate_is_left_out_of_sgd_and_nothing_is_fetched(
        client, conn, no_fetch):
    savings = account(conn, "Rupee savings", type="bank", currency="INR")
    for month in months_between("2026-01", "2026-09"):
        sid = statement(conn, savings, month)
        row(conn, sid, "SALARY", -5_000_000, flow="income", day=f"{month}-01")
        row(conn, sid, "RENT", 4_800_000, day=f"{month}-10")
    anchor(conn, savings, "2026-05-31", 40_000_000)
    for month in months_between("2026-01", "2026-09"):
        if month != "2026-06":
            seed_rate(conn, "INR/SGD", _end(month), "0.0155")
    saved_before = conn.execute("SELECT pair, date, rate FROM rates ORDER BY date").fetchall()

    in_sgd = by_month(answer(client, savings, "?span=12M&to=2026-10&cur=SGD"))
    june = in_sgd["2026-06"]
    assert june["rate"] is None
    assert (june["balance"], june["estimate"], june["in"], june["out"]) == (None, None, None, None)
    assert june["note"] == "No S$ figure: no saved rate for June 2026"
    assert in_sgd["2026-07"]["balance"] == 626_200  # ₹ 4,04,000 at 0.0155 is S$ 6,262.00

    in_rupees = by_month(answer(client, savings, "?span=12M&to=2026-10&cur=INR"))
    assert in_rupees["2026-06"]["rate"] is None
    assert in_rupees["2026-06"]["balance"] == 40_200_000
    assert in_rupees["2026-06"]["note"] is None

    assert conn.execute("SELECT pair, date, rate FROM rates ORDER BY date").fetchall() == saved_before


# --- case 49: deleted elsewhere ----------------------------------------------


def test_case_49_an_account_deleted_elsewhere_answers_404(client, conn):
    gone = account(conn, "Old savings", type="bank")
    assert client.delete(f"/api/accounts/{gone}").status_code == 200
    resp = get(client, gone)
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "This account is no longer in fin"}


# --- case 63: a bad query ----------------------------------------------------


@pytest.mark.parametrize("query, reason", [
    ("?span=9M", "span must be one of 6M, 12M, YTD, 2Y, All"),
    ("?to=2026-13", "to must be a month like 2026-10"),
    ("?cur=EUR", "cur must be SGD or INR for this account"),
])
def test_case_63_a_bad_query_answers_400_with_its_reason(client, rupee_savings, query, reason):
    resp = get(client, rupee_savings, query)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": reason}


def test_case_63_an_unknown_account_answers_404(client, temp_db):
    resp = get(client, 99999, "?span=12M")
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "This account is no longer in fin"}
