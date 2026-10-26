"""Depreciation: a car's value worked out each month from its terms (fin RUL-029).

A car held as a holding carries its terms: what it cost, the day it was had
from, its life in whole years, and what it is worth at the end of it (the
residual). fin works out one depreciation row a month from them, on read:
straight line over the life, dated each month's last day, the first month by
the days held in it, the last month what is left so the rows add up exactly to
the cost less the residual. Nothing is stored but the terms, and no job runs:
a month's row is there once the month is read.

The car's value on the balance sheet is its cost less the depreciation dated
to the day; figures typed for it are kept and not read while it holds terms.
The month check takes the month's depreciation on its own line, outside
Household spending, so the fall in the car's value is explained. Changing
the terms works every month out again, and Undo puts the old ones back.

Driven through the HTTP interface (the account route, the balance sheet and
its month check, typed figures, Undo), a start (db.init_db), and the screens'
own functions from static/app.js under node. Every name and figure is
invented but the operator's car's terms, which are the ruling's own.
"""

import json
import sqlite3
import subprocess
from datetime import date

import pytest

import account_balances
import db
import depreciation
from test_client_seams import NODE, _const, _function, needs_node
from test_import_loan_figure import accounts, make_account

CAR = "Sample Car"

# The operator's car (RUL-029): S$241,833 from 11 Jun 2025, 10 years, nil residual.
TERMS = {"asset": "car", "cost": "241833.00", "start": "2025-06-11", "years": 10}
COST = 24_183_300
MONTHLY = 201_528            # 241,833.00 / 120 = 2,015.275, half up
FIRST = 134_352              # June 2025: 20 of its 30 days held, 1,343.5166..., half up
LAST = COST - FIRST - 119 * MONTHLY  # June 2035: what is left, 671.16


def set_terms(client, account_id: int, terms):
    return client.put(f"/api/accounts/{account_id}", json={"depreciation": terms})


def car_with_terms(client, terms=TERMS) -> int:
    car = make_account(client, CAR, "holding")
    resp = set_terms(client, car, terms)
    assert resp.status_code == 200, resp.get_json()
    return car


def enter(client, account_id: int, amount: str, on: str):
    return client.post("/api/anchors", json={"account_id": account_id, "amount": amount, "date": on})


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def undo_latest(client):
    return client.post(f"/api/history/{entries(client)[0]['id']}/undo")


def sheet(client, month: str) -> dict:
    resp = client.get(f"/api/balance-sheet?month={month}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def car_line(client, month: str) -> dict:
    return next(l for s in sheet(client, month)["sections"] for l in s["lines"] if l["name"] == CAR)


def held(terms: dict) -> dict:
    """Terms as checked and stored."""
    return depreciation.checked(terms, "SGD")


# --- the schedule --------------------------------------------------------------------


def test_the_operators_car_loses_2015_28_a_month_from_its_first_full_month():
    rows = depreciation.schedule(held(TERMS))

    by_month = {r["month"]: r for r in rows}
    assert by_month["2025-07"]["amount_minor"] == MONTHLY
    assert by_month["2026-10"] == {"month": "2026-10", "date": "2026-10-31", "amount_minor": MONTHLY}


def test_the_first_month_is_by_the_days_held_in_it():
    first = depreciation.schedule(held(TERMS))[0]

    # 11 to 30 June: 20 of June's 30 days.
    assert first == {"month": "2025-06", "date": "2025-06-30", "amount_minor": FIRST}


def test_the_last_month_takes_what_is_left_and_the_rows_add_up_to_the_cost_exactly():
    rows = depreciation.schedule(held(TERMS))

    # June 2025 to June 2035: the life ends on 10 Jun 2035.
    assert len(rows) == 121
    assert rows[-1] == {"month": "2035-06", "date": "2035-06-30", "amount_minor": LAST}
    assert sum(r["amount_minor"] for r in rows) == COST
    assert all(r["amount_minor"] == MONTHLY for r in rows[1:-1])


def test_every_row_is_dated_its_months_last_day():
    for r in depreciation.schedule(held(TERMS)):
        y, m = map(int, r["month"].split("-"))
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        assert date.fromisoformat(r["date"]).toordinal() == nxt.toordinal() - 1


def test_a_residual_is_kept_and_only_the_rest_is_written_off():
    terms = held({"asset": "car", "cost": "100000.00", "start": "2026-01-01", "years": 5,
                  "residual": "10000.00"})

    rows = depreciation.schedule(terms)

    # 90,000.00 over 60 months, from a first month held whole.
    assert len(rows) == 60
    assert {r["amount_minor"] for r in rows} == {150_000}
    assert (rows[0]["month"], rows[-1]["month"]) == ("2026-01", "2030-12")
    assert depreciation.value_on(terms, "2031-06-30") == 1_000_000


def test_a_car_had_from_a_months_last_day_has_one_day_in_its_first_month():
    terms = held({"asset": "car", "cost": "31000.00", "start": "2026-01-31", "years": 1})

    rows = depreciation.schedule(terms)

    # 31,000.00 over 12 months is 2,583.333...; one of January's 31 days is 83.33.
    assert rows[0] == {"month": "2026-01", "date": "2026-01-31", "amount_minor": 8_333}
    assert rows[-1]["month"] == "2027-01"
    assert sum(r["amount_minor"] for r in rows) == 3_100_000


def test_the_value_is_the_cost_less_the_rows_dated_to_the_day():
    terms = held(TERMS)

    # Before the car was had there is no value; on its first day, its cost.
    assert depreciation.value_on(terms, "2025-06-10") is None
    assert depreciation.value_on(terms, "2025-06-11") == COST
    # A row counts from its own day, the month's last.
    assert depreciation.value_on(terms, "2025-06-29") == COST
    assert depreciation.value_on(terms, "2025-06-30") == COST - FIRST
    # The end of October 2026: June 2025 and the sixteen months to October.
    assert depreciation.value_on(terms, "2026-10-31") == COST - FIRST - 16 * MONTHLY == 20_824_500
    # After its life, its residual: nothing.
    assert depreciation.value_on(terms, "2040-01-01") == 0


def test_the_depreciation_between_two_days_is_the_rows_dated_after_one_up_to_the_other():
    terms = held(TERMS)

    assert depreciation.between(terms, "2026-09-30", "2026-10-31") == MONTHLY
    assert depreciation.between(terms, "2026-10-01", "2026-10-30") == 0
    assert depreciation.between(terms, "2025-05-31", "2025-07-31") == FIRST + MONTHLY


# --- the terms, on the account ----------------------------------------------------------


def test_terms_set_on_a_holding_are_read_back_with_the_monthly_amount(client):
    car_with_terms(client)

    assert accounts(client)[CAR]["depreciation"] == {
        "asset": "car", "cost_minor": COST, "start": "2025-06-11", "years": 10, "residual_minor": 0,
        "monthly_minor": MONTHLY, "ends": "2035-06-10",
    }


def test_an_account_with_no_terms_reads_none(client):
    make_account(client, CAR, "holding")
    make_account(client, "Sample Bank 0002", "bank")

    assert {a["depreciation"] for a in accounts(client).values()} == {None}


def test_setting_terms_writes_no_row_and_no_statement(client, conn):
    def counts():
        return [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("transactions", "statements", "anchors")]
    before = counts()

    car_with_terms(client)
    sheet(client, "2026-10")

    assert counts() == before


def test_setting_terms_is_one_change_and_undo_takes_them_off(client):
    car_with_terms(client)
    made = len(entries(client))

    assert undo_latest(client).status_code == 200

    assert accounts(client)[CAR]["depreciation"] is None
    assert len(entries(client)) == made + 1


def test_changing_the_terms_works_every_month_out_again_and_undo_puts_them_back(client):
    car = car_with_terms(client)
    october = car_line(client, "2026-10")["balance_minor"]

    assert set_terms(client, car, {**TERMS, "years": 5}).status_code == 200
    shorter = car_line(client, "2026-10")
    # 241,833.00 over 60 months: 4,030.55 a month; 20 days of June 2025, half up.
    assert accounts(client)[CAR]["depreciation"]["monthly_minor"] == 403_055
    assert shorter["balance_minor"] == COST - 268_703 - 16 * 403_055

    assert undo_latest(client).status_code == 200
    assert car_line(client, "2026-10")["balance_minor"] == october == 20_824_500


def test_terms_set_to_none_are_taken_off(client):
    car = car_with_terms(client)

    assert set_terms(client, car, None).status_code == 200

    assert accounts(client)[CAR]["depreciation"] is None


def test_the_same_terms_again_change_nothing(client):
    car = car_with_terms(client)
    made = len(entries(client))

    assert set_terms(client, car, dict(TERMS)).status_code == 200

    assert len(entries(client)) == made


@pytest.mark.parametrize("change, said", [
    ({"cost": "lots"}, "the cost is not an amount"),
    ({"cost": "0"}, "the cost must be more than nothing"),
    ({"residual": "241833.00"}, "the value at the end of its life must be less than its cost"),
    ({"residual": "-1"}, "the value at the end of its life is not an amount"),
    ({"years": 0}, "the years are a whole number from 1 to 50"),
    ({"years": 2.5}, "the years are a whole number from 1 to 50"),
    ({"years": 51}, "the years are a whole number from 1 to 50"),
    ({"start": "11 Jun 2025"}, "the day it was had from is a day, YYYY-MM-DD"),
    ({"asset": "boat"}, "fin depreciates cars only"),
])
def test_terms_out_of_range_are_refused_and_nothing_is_written(client, change, said):
    car = make_account(client, CAR, "holding")
    before = len(entries(client))

    resp = set_terms(client, car, {**TERMS, **change})

    assert resp.status_code == 400
    assert said in resp.get_json()["error"] and "nothing was saved" in resp.get_json()["error"]
    assert accounts(client)[CAR]["depreciation"] is None
    assert len(entries(client)) == before


def test_terms_on_an_account_that_is_no_holding_are_refused(client):
    bank = make_account(client, "Sample Bank 0002", "bank")

    resp = set_terms(client, bank, TERMS)

    assert resp.status_code == 400
    assert "depreciation terms go on a holding" in resp.get_json()["error"]


def test_terms_on_a_holding_kept_in_another_currency_are_refused(client):
    resp = client.post("/api/accounts", json={"name": CAR, "type": "holding", "currency": "INR"})
    car = resp.get_json()["id"]

    resp = set_terms(client, car, TERMS)

    assert resp.status_code == 400
    assert "in S$ only" in resp.get_json()["error"]


def test_a_holding_with_terms_is_not_retyped(client):
    car = car_with_terms(client)

    resp = client.put(f"/api/accounts/{car}", json={"type": "bank"})

    assert resp.status_code == 400
    assert "depreciation terms" in resp.get_json()["error"]
    assert accounts(client)[CAR]["type"] == "holding"


def test_deleting_a_car_with_terms_takes_them_too_and_undo_puts_both_back(client):
    car = car_with_terms(client)

    assert client.delete(f"/api/accounts/{car}").status_code == 200
    assert CAR not in accounts(client)

    assert undo_latest(client).status_code == 200
    assert accounts(client)[CAR]["depreciation"]["cost_minor"] == COST


# --- the balance sheet ----------------------------------------------------------------


def test_the_cars_line_is_its_cost_less_depreciation_to_the_month_end(client):
    car_with_terms(client)

    line = car_line(client, "2026-10")

    assert line["balance_minor"] == line["value_minor"] == 20_824_500
    assert line["in_total"] is True
    assert line["rests_on"]["source"] == "terms"
    assert line["rests_on"]["date"] == "2025-06-11"
    assert line["depreciation"] == {
        "cost_minor": COST, "start": "2025-06-11", "to_date_minor": FIRST + 16 * MONTHLY,
        "month_minor": MONTHLY, "monthly_minor": MONTHLY,
    }
    assert line["since"] == "less S$ 33,588.00 depreciation since 11 Jun 2025"


def test_figures_typed_for_the_car_are_kept_and_not_read_while_it_holds_terms(client, conn):
    car = make_account(client, CAR, "holding")
    assert enter(client, car, "210260.28", "2026-09-30").status_code == 200

    assert set_terms(client, car, TERMS).status_code == 200

    assert car_line(client, "2026-10")["balance_minor"] == 20_824_500
    assert conn.execute("SELECT COUNT(*) FROM anchors WHERE account_id = ?", (car,)).fetchone()[0] == 1
    # Taken off, the figure is read again.
    assert set_terms(client, car, None).status_code == 200
    assert car_line(client, "2026-10")["balance_minor"] == 21_026_028


def test_a_car_with_terms_takes_no_new_figure(client):
    car = car_with_terms(client)

    resp = enter(client, car, "200000.00", "2026-10-31")

    assert resp.status_code == 400
    assert "worked out from its depreciation terms" in resp.get_json()["error"]
    assert accounts(client)[CAR]["takes_a_figure"] is False


def test_a_month_ending_before_the_car_was_had_shows_no_value_for_it(client):
    car_with_terms(client, {**TERMS, "start": "2026-03-05"})

    line = car_line(client, "2026-02")

    assert line["balance_minor"] is None and line["in_total"] is False
    assert line["left_out"] == "no value before 5 Mar 2026, the day its depreciation terms start"


def test_the_month_end_balances_follow_the_terms(client, conn):
    car = car_with_terms(client)

    charted = account_balances.balances(
        conn, {"id": car, "type": "holding", "currency": "SGD"}, ["2026-09", "2026-10"])

    assert charted["no_balance"] is None
    assert [m["balance"] for m in charted["months"]] == [20_824_500 + MONTHLY, 20_824_500]


# --- the month check ------------------------------------------------------------------


def test_two_typed_figures_leave_the_cars_fall_unexplained_without_terms(client):
    car = make_account(client, CAR, "holding")
    enter(client, car, "210260.28", "2026-09-30")
    enter(client, car, "207018.18", "2026-10-31")

    shown = sheet(client, "2026-10")["month_check"]

    assert shown["unexplained_minor"] == -324_210
    assert shown["depreciation_minor"] == 0


def test_with_terms_the_months_depreciation_explains_the_fall_outside_household_spending(client):
    car = make_account(client, CAR, "holding")
    enter(client, car, "210260.28", "2026-09-30")
    enter(client, car, "207018.18", "2026-10-31")
    assert set_terms(client, car, TERMS).status_code == 200

    shown = sheet(client, "2026-10")["month_check"]

    assert shown["depreciation_minor"] == MONTHLY
    assert shown["depreciation"] == "S$ 2,015.28"
    assert shown["depreciation_lines"] == [{"account_id": car, "name": CAR, "amount_minor": MONTHLY,
                                            "amount": "S$ 2,015.28"}]
    assert shown["spending_minor"] == 0
    assert shown["expected_minor"] == shown["opening_minor"] - MONTHLY
    assert shown["unexplained_minor"] == 0


def test_the_month_the_car_was_had_in_leaves_it_out_of_both_sides(client):
    car_with_terms(client, {**TERMS, "start": "2026-03-05"})

    shown = sheet(client, "2026-03")["month_check"]

    assert shown["depreciation_minor"] == 0
    assert [e["name"] for e in shown["left_out"]] == [CAR]
    assert shown["unexplained_minor"] == 0


def test_household_spending_on_the_dashboard_has_no_depreciation_in_it(client):
    make_account(client, CAR, "holding")
    before = client.get("/api/dashboard/stat-cards?ref_month=2026-10&history=3").get_json()

    set_terms(client, accounts(client)[CAR]["id"], TERMS)

    assert client.get("/api/dashboard/stat-cards?ref_month=2026-10&history=3").get_json() == before


# --- a start ---------------------------------------------------------------------------


def test_a_start_gives_a_book_the_terms_table_empty_and_changes_no_row(client, temp_db, conn):
    make_account(client, CAR, "holding")
    rows = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    conn.close()

    db.init_db()

    conn = sqlite3.connect(str(temp_db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM depreciation_terms").fetchone()[0] == 0
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        assert {f"history_depreciation_terms_{op}" for op in ("insert", "update", "delete")} <= triggers
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == rows
    finally:
        conn.close()


# --- the screens ------------------------------------------------------------------------


def _node(code: str):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30,
                         check=True, encoding="utf-8")
    return json.loads(out.stdout)


@needs_node
def test_the_sheet_sends_the_terms_only_when_the_operator_changed_them():
    held_fields = {"cost": "241,833.00", "start": "2025-06-11", "years": "10", "residual": ""}
    code = "\n".join([
        _function("depChange"),
        f"const held = {json.dumps(held_fields)};",
        "const said = x => x === undefined ? 'unchanged' : x;",
        "console.log(JSON.stringify([",
        "  depChange(held, {...held}),",
        "  depChange(held, {cost: '', start: '', years: '', residual: ''}),",
        "  depChange(held, {...held, years: '5'}),",
        "  depChange({cost: '', start: '', years: '', residual: ''}, {cost: '241,833', start: '2025-06-11', years: '10', residual: ''}),",
        "].map(said)));",
    ])

    unchanged, cleared, changed, fresh = _node(code)

    assert unchanged == "unchanged"
    assert cleared is None
    assert changed == {"asset": "car", "cost": "241833.00", "start": "2025-06-11", "years": 5, "residual": "0"}
    assert fresh == {"asset": "car", "cost": "241833", "start": "2025-06-11", "years": 10, "residual": "0"}


@needs_node
def test_a_line_resting_on_terms_says_so_and_is_never_stale():
    code = "\n".join([
        "const icon = n => `<i ${n}></i>`;",
        _function("esc"), _const("MONTHS"), _function("day"), _function("tag"),
        "const STALE_DAYS = 45; const S = { month: '2026-10' };",
        _function("restsOn"),
        "console.log(JSON.stringify(restsOn({ account_id: 5, rests_on: { date: '2025-06-11', source: 'terms', age_days: 507 } })));",
    ])

    said = _node(code)

    assert "its depreciation terms from 11 Jun 2025" in said
    assert "stale" not in said


# --- the review's guards ------------------------------------------------------------------


def test_a_loan_with_hire_purchase_terms_is_not_made_a_holding_by_a_depreciation_save(client):
    loan = make_account(client, "Sample Car Loan", "loan")
    hp = {"financed": "100000.00", "months": 48, "flat_rate": "1.5", "first_instalment": "2026-01-15"}
    assert client.put(f"/api/accounts/{loan}", json={"terms": hp}).status_code == 200

    resp = client.put(f"/api/accounts/{loan}", json={"type": "holding", "depreciation": None})

    assert resp.status_code == 400
    assert "hire-purchase terms" in resp.get_json()["error"]
    assert accounts(client)["Sample Car Loan"]["type"] == "loan"


def test_a_car_with_terms_is_not_made_a_loan_by_a_hire_purchase_save(client):
    car = car_with_terms(client)

    resp = client.put(f"/api/accounts/{car}", json={"type": "loan", "terms": None})

    assert resp.status_code == 400
    assert "depreciation terms" in resp.get_json()["error"]
    assert accounts(client)[CAR]["type"] == "holding"


def test_terms_cleared_and_the_kind_changed_in_one_save_are_taken(client):
    car = car_with_terms(client)

    resp = client.put(f"/api/accounts/{car}", json={"type": "bank", "depreciation": None})

    assert resp.status_code == 200, resp.get_json()
    assert (accounts(client)[CAR]["type"], accounts(client)[CAR]["depreciation"]) == ("bank", None)


def test_a_car_with_terms_is_not_moved_to_another_currency(client):
    car = car_with_terms(client)

    resp = client.put(f"/api/accounts/{car}", json={"currency": "INR"})

    assert resp.status_code == 400
    assert "depreciation terms" in resp.get_json()["error"]
    assert accounts(client)[CAR]["currency"] == "SGD"


def test_terms_left_on_an_account_that_is_no_holding_are_not_read(client, conn):
    car = car_with_terms(client)
    # As an undo of an earlier retype could leave them.
    conn.execute("UPDATE accounts SET type = 'bank' WHERE id = ?", (car,))
    conn.commit()

    shown = accounts(client)[CAR]

    assert shown["depreciation"] is None and shown["takes_a_figure"] is True
    assert enter(client, car, "1000.00", "2026-10-31").status_code == 200


@needs_node
def test_a_month_where_only_depreciation_moved_is_not_said_still():
    code = "\n".join([
        _function("monthStill"),
        "console.log(JSON.stringify([monthStill({ depreciation_minor: 201528 }), monthStill({})]));",
    ])

    assert _node(code) == [False, True]


def test_a_loan_with_hire_purchase_terms_sent_no_depreciation_and_no_kind_changes_nothing(client):
    loan = make_account(client, "Sample Car Loan", "loan")
    hp = {"financed": "100000.00", "months": 48, "flat_rate": "1.5", "first_instalment": "2026-01-15"}
    assert client.put(f"/api/accounts/{loan}", json={"terms": hp}).status_code == 200

    resp = client.put(f"/api/accounts/{loan}", json={"name": "Sample Car Loan", "depreciation": None})

    assert resp.status_code == 200, resp.get_json()
    assert accounts(client)["Sample Car Loan"]["terms"] is not None
