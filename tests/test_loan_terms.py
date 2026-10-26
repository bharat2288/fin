"""Hire-purchase terms on a loan: a statement's remaining instalments read as
principal owed (fin-redesign 14, case 28).

A hire-purchase statement prints the total of the remaining instalments,
which falls by a whole instalment a month. With the loan's terms held (amount
financed, months, flat rate or interest, first instalment, the day the amount
financed is owed from, Rule of 78), the import turns that total into
principal owed, so the interest worked out between two figures is the Rule
of 78 interest of the instalments between them and Household spending falls
by the principal only. The terms are also the loan's first figure: the
amount financed, owed from their start day.

Driven through the HTTP interface (the account route, the upload and its
confirm, typed figures, the stat cards, Undo), the chat tools through
mcp_tools.call, a start (db.init_db), and the screens' own functions from
static/app.js under node. The statement goes through the real reader
(parse_dbs_loan.py) over synthetic page text. Every name, term and figure
here is invented.
"""

import json
import sqlite3
import subprocess
from calendar import monthrange
from datetime import date

import pytest

import db
import loan_interest
import loan_terms
import mcp_tools
import parse_dbs_loan
from test_client_seams import NODE, _const, _function, needs_node
from test_import_loan_figure import LOAN, accounts, confirm, make_account
from test_import_that_ties import anchors_of, upload
from test_parse_dbs_loan import LOAN_PAGE, MESSAGES_PAGE
from test_parse_hdfc import fake_open

# Invented terms: 100,000.00 over 48 months at 1.5% flat a year. Interest
# 6,000.00; 106,000.00 to repay; the level instalment 2,208.33, which the
# lender makes 2,209.00 with a last of 2,177.00. The amount financed is owed
# from 31 Dec 2025, the day before the first instalment's month.
TERMS = {"financed": "100000.00", "months": 48, "flat_rate": "1.5",
         "first_instalment": "2026-01-15", "method": "rule-of-78"}
INTEREST = 600_000
TOTAL = 10_600_000
INSTALMENT = 220_900
LAST = TOTAL - 47 * INSTALMENT          # 2,177.00


def remaining(paid: int) -> int:
    """The total of the remaining instalments after `paid` instalments."""
    return 0 if paid >= 48 else INSTALMENT * (47 - paid) + LAST


def unearned(left: int) -> int:
    """Rule of 78: the interest not yet earned with `left` instalments to pay,
    half up: 6,000.00 x left(left + 1) / (48 x 49)."""
    num, den = INTEREST * left * (left + 1), 48 * 49
    return (2 * num + den) // (2 * den)


def interest_of(k: int) -> int:
    """The Rule of 78 interest instalment k carries."""
    return unearned(49 - k) - unearned(48 - k)


def units(minor: int) -> str:
    return f"{minor // 100:,}.{minor % 100:02d}"


def due(k: int) -> str:
    """Instalment k's day: the 15th, monthly from January 2026."""
    y, m = divmod(k - 1, 12)
    return date(2026 + y, m + 1, 15).isoformat()


def month_end(k: int) -> str:
    """The day a statement after k instalments is dated: the end of instalment k's month."""
    y, m = divmod(k - 1, 12)
    year, month = 2026 + y, m + 1
    return date(year, month, monthrange(year, month)[1]).isoformat()


def printed_day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b} {d.year}"


@pytest.fixture
def summary(monkeypatch):
    """Make the uploaded PDF read as the car-loan statement for a day."""
    def install(day: str, printed: int):
        page = LOAN_PAGE.replace("30 Apr 2026", day).replace("52,340.00", units(printed))
        monkeypatch.setattr(parse_dbs_loan.pdfplumber, "open", fake_open([page, MESSAGES_PAGE]))
    return install


def set_terms(client, account_id: int, terms):
    return client.put(f"/api/accounts/{account_id}", json={"terms": terms})


def loan_with_terms(client, terms=TERMS) -> int:
    loan = make_account(client, LOAN, "loan")
    resp = set_terms(client, loan, terms)
    assert resp.status_code == 200, resp.get_json()
    return loan


def instalment_row(conn, client, loan: int, day: str, amount: int = INSTALMENT) -> int:
    bank = conn.execute("SELECT id FROM accounts WHERE name = 'Sample Bank 0002'").fetchone()
    bank_id = bank[0] if bank else make_account(client, "Sample Bank 0002", "bank")
    statement_id = conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, 'sample.csv')",
        (bank_id, day),
    ).lastrowid
    row = conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type, other_side_id)"
        " VALUES (?, ?, 'SAMPLE HIRE PURCHASE INSTALMENT', ?, 'movement', ?)",
        (statement_id, day, amount, loan),
    ).lastrowid
    conn.commit()
    return row


def pay(conn, client, loan: int, upto: int, start: int = 1) -> None:
    """Instalments start..upto, each on its day, naming the loan."""
    for k in range(start, upto + 1):
        instalment_row(conn, client, loan, due(k), INSTALMENT if k < 48 else LAST)


def import_statement(client, summary, day: str, printed: int) -> dict:
    summary(printed_day(day) if "-" in day else day, printed)
    preview = upload(client, f"{day}.pdf")
    resp = confirm(client, preview)
    assert resp.status_code == 200, resp.get_json()
    return preview


def refused_import(client, summary, day: str, printed: int):
    summary(printed_day(day), printed)
    resp = confirm(client, upload(client, f"{day}.pdf"))
    assert resp.status_code == 409, resp.get_json()
    return resp.get_json()["error"]


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def undo_latest(client):
    return client.post(f"/api/history/{entries(client)[0]['id']}/undo")


def as_sent(terms: dict) -> dict:
    """Terms as GET shows them, sent back as the sheet would send them unchanged."""
    out = {"financed": units(terms["financed_minor"]).replace(",", ""), "months": terms["months"],
           "first_instalment": terms["first_instalment"], "owed_from": terms["owed_from"],
           "method": terms["method"]}
    if terms["flat_rate"] is not None:
        out["flat_rate"] = terms["flat_rate"]
    else:
        out["interest"] = units(terms["interest_minor"]).replace(",", "")
    return out


# --- the terms, on the account ---------------------------------------------------


def test_terms_set_on_a_loan_are_read_back_as_entered_with_the_instalment(client):
    loan_with_terms(client)

    account = accounts(client)[LOAN]
    assert account["terms"] == {
        "financed_minor": 10_000_000, "interest_minor": INTEREST, "flat_rate": "1.5", "months": 48,
        "first_instalment": "2026-01-15", "owed_from": "2025-12-31", "method": "rule-of-78",
        "instalment_minor": 220_833,
    }
    assert account["terms_locked"] is None


def test_the_rate_is_held_exactly_as_entered(client):
    loan_with_terms(client, {**TERMS, "flat_rate": "1.50"})

    assert accounts(client)[LOAN]["terms"]["flat_rate"] == "1.50"


def test_the_interest_may_be_given_instead_and_is_held_exactly(client):
    terms = {**TERMS, "interest": "6,000.01"}
    del terms["flat_rate"]

    loan_with_terms(client, terms)

    held = accounts(client)[LOAN]["terms"]
    assert (held["interest_minor"], held["flat_rate"]) == (600_001, None)


def test_the_day_the_amount_financed_is_owed_from_may_be_given(client):
    loan_with_terms(client, {**TERMS, "owed_from": "2025-12-20"})

    assert accounts(client)[LOAN]["terms"]["owed_from"] == "2025-12-20"


def test_an_account_with_no_terms_reads_none(client):
    make_account(client, LOAN, "loan")
    make_account(client, "Sample Bank 0002", "bank")

    assert {a["terms"] for a in accounts(client).values()} == {None}


def test_setting_terms_is_one_change_and_undo_takes_them_off_exactly(client):
    loan_with_terms(client)

    assert undo_latest(client).status_code == 200

    assert accounts(client)[LOAN]["terms"] is None


def test_changing_terms_and_undoing_puts_the_old_terms_back(client):
    loan = loan_with_terms(client)
    before = accounts(client)[LOAN]["terms"]
    assert set_terms(client, loan, {**TERMS, "months": 60}).status_code == 200
    assert accounts(client)[LOAN]["terms"]["months"] == 60

    assert undo_latest(client).status_code == 200

    assert accounts(client)[LOAN]["terms"] == before


def test_terms_set_to_none_are_taken_off(client):
    loan = loan_with_terms(client)

    assert set_terms(client, loan, None).status_code == 200

    assert accounts(client)[LOAN]["terms"] is None


@pytest.mark.parametrize("change, said", [
    ({"months": 0}, "the months must be a whole number"),
    ({"financed": "0"}, "the amount financed must be more than nothing"),
    ({"financed": "lots"}, "the amount financed is not an amount"),
    ({"method": "straight-line"}, "unknown method"),
    ({"first_instalment": "Jan 2026"}, "the first instalment's date is a day"),
    ({"interest": "6000.00"}, "give the flat rate a year or the interest over the term, one of them"),
    ({"flat_rate": "-1"}, "the flat rate is a percent a year above 0"),
    ({"owed_from": "2026-01-15"}, "the day the amount financed is owed from comes before the first instalment"),
])
def test_terms_out_of_range_are_refused_and_nothing_is_written(client, change, said):
    loan = make_account(client, LOAN, "loan")
    count = len(entries(client))

    resp = set_terms(client, loan, {**TERMS, **change})

    assert resp.status_code == 400
    assert said in resp.get_json()["error"]
    assert accounts(client)[LOAN]["terms"] is None
    assert len(entries(client)) == count


def test_terms_on_an_account_that_is_no_loan_are_refused(client):
    bank = make_account(client, "Sample Bank 0002", "bank")

    resp = set_terms(client, bank, TERMS)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == (
        "Sample Bank 0002 is a bank account; hire-purchase terms go on a loan. Nothing was saved."
    )
    assert accounts(client)["Sample Bank 0002"]["terms"] is None


def test_a_loan_with_terms_is_not_retyped(client):
    loan = loan_with_terms(client)

    resp = client.put(f"/api/accounts/{loan}", json={"type": "bank"})

    assert resp.status_code == 400
    assert "hire-purchase terms" in resp.get_json()["error"]
    assert accounts(client)[LOAN]["type"] == "loan"


def test_deleting_a_loan_with_terms_takes_them_too_and_undo_puts_both_back(client):
    loan = loan_with_terms(client)
    terms = accounts(client)[LOAN]["terms"]

    assert client.delete(f"/api/accounts/{loan}").status_code == 200
    assert LOAN not in accounts(client)
    assert undo_latest(client).status_code == 200

    assert accounts(client)[LOAN]["terms"] == terms


# --- the lock: terms do not change under figures worked out with them -------------------

LOCKED = ("this loan holds 1 figure from its statements, worked out under the terms it has; undo "
          "those imports before changing its terms. Nothing was saved.")


@pytest.fixture
def locked(client, conn, summary):
    """A loan with terms, its first instalment paid and its first statement in."""
    loan = loan_with_terms(client)
    terms_entry = entries(client)[0]["id"]
    pay(conn, client, loan, 1)
    import_statement(client, summary, month_end(1), remaining(1))
    return {"loan": loan, "terms_entry": terms_entry}


def test_terms_are_not_changed_once_the_loan_holds_figures_from_its_statements(client, locked):
    resp = set_terms(client, locked["loan"], {**TERMS, "months": 60})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == LOCKED
    assert accounts(client)[LOAN]["terms"]["months"] == 48
    assert accounts(client)[LOAN]["terms_locked"] == LOCKED


def test_undoing_the_terms_under_figures_worked_out_with_them_is_refused_in_the_locks_words(client, locked):
    held = accounts(client)[LOAN]["terms"]

    resp = client.post(f"/api/history/{locked['terms_entry']}/undo")

    assert resp.status_code in (400, 409)
    assert resp.get_json()["error"] == LOCKED
    assert accounts(client)[LOAN]["terms"] == held


def test_a_locked_loan_is_renamed_and_its_unchanged_terms_resent_change_nothing(client, locked):
    held = accounts(client)[LOAN]["terms"]

    renamed = client.put(f"/api/accounts/{locked['loan']}",
                         json={"name": "Sample Car Loan", "last_four": "0001", "status": "active"})
    resent = client.put(f"/api/accounts/{locked['loan']}", json={"name": "Sample Car Loan", "terms": as_sent(held)})

    assert (renamed.status_code, resent.status_code) == (200, 200)
    assert accounts(client)["Sample Car Loan"]["terms"] == held
    assert accounts(client)["Sample Car Loan"]["last_four"] == "0001"


def test_a_loan_whose_statements_came_in_without_terms_says_so(client, summary):
    loan = make_account(client, LOAN, "loan")
    import_statement(client, summary, month_end(1), remaining(1))

    resp = set_terms(client, loan, TERMS)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == (
        "this loan holds 1 figure from its statements, imported as printed without terms; undo "
        "those imports before giving it terms. Nothing was saved."
    )


# --- the import: remaining instalments read as principal owed ---------------------


def test_with_terms_the_preview_says_the_printed_total_and_the_principal_owed(client, summary):
    loan_with_terms(client)
    summary("31 Jan 2026", remaining(1))

    [figure] = upload(client, "2026-01.pdf")["figures"]

    assert figure == {
        "account": LOAN, "kind": "loan", "date": "2026-01-31", "currency": "SGD",
        "new_account": False, "held": False,
        "printed_minor": remaining(1), "instalments_left": 47, "months": 48,
        "left_by_schedule": 47, "ahead_minor": 0, "unearned_minor": unearned(47), "method": "rule-of-78",
        "owed_minor": remaining(1) - unearned(47),
    }


def test_with_terms_the_confirm_writes_the_principal_owed(client, conn, summary):
    loan = loan_with_terms(client)
    pay(conn, client, loan, 1)

    import_statement(client, summary, month_end(1), remaining(1))

    assert [(a["account_id"], a["amount_minor"]) for a in anchors_of(client)] == [
        (loan, -(remaining(1) - unearned(47))),
    ]


def test_without_terms_the_figure_is_imported_as_printed(client, summary):
    make_account(client, LOAN, "loan")
    summary("31 Jan 2026", remaining(1))

    [figure] = upload(client, "2026-01.pdf")["figures"]

    assert "unearned_minor" not in figure
    assert figure["owed_minor"] == remaining(1)


def test_before_the_first_instalment_the_principal_owed_is_the_amount_financed(client, summary):
    loan_with_terms(client)
    summary("31 Dec 2025", remaining(0))

    [figure] = upload(client, "2025-12.pdf")["figures"]

    assert (figure["instalments_left"], figure["owed_minor"]) == (48, 10_000_000)


@pytest.mark.parametrize("printed, left", [
    (LAST, 1),                                    # the last, smaller instalment is one
    (220_800 * 46 + 10_600_000 - 220_800 * 47, 47),  # a lender rounding the instalment down
    (remaining(5) + 5_000, 43),                   # a small fee added to the total
    (INSTALMENT * 40, 40),                        # exactly 40 whole instalments
    (remaining(9) - 30_000, 39),                  # a 300.00 benefit taken off the total
])
def test_the_instalments_left_are_the_nearest_whole_number(client, summary, printed, left):
    loan_with_terms(client)
    summary("30 Nov 2026", printed)

    [figure] = upload(client, "2026-11.pdf")["figures"]

    assert figure["instalments_left"] == left
    assert figure["unearned_minor"] == unearned(left)


# The level instalment, half up: 106,000.00 / 48 = 2,208.33.
LEVEL = 220_833


def test_a_statement_that_took_off_the_next_instalment_early_adds_it_back(client, summary):
    # Dated before the 15th's instalment, but the lender has taken it off: it
    # is still owed on the 10th, so the principal owed is the schedule's after
    # the one instalment paid by then.
    loan_with_terms(client)
    summary("10 Feb 2026", remaining(2))

    [figure] = upload(client, "2026-02.pdf")["figures"]

    assert (figure["instalments_left"], figure["left_by_schedule"]) == (47, 47)
    assert figure["ahead_minor"] == LEVEL
    assert figure["unearned_minor"] == unearned(47)
    assert figure["owed_minor"] == remaining(2) + LEVEL - unearned(47)


def test_a_statement_two_instalments_ahead_is_read_as_printed(client, summary):
    loan_with_terms(client)
    summary("10 Feb 2026", remaining(3))

    [figure] = upload(client, "2026-02.pdf")["figures"]

    assert (figure["instalments_left"], figure["left_by_schedule"], figure["ahead_minor"]) == (45, 47, 0)
    assert figure["owed_minor"] == remaining(3) - unearned(45)


def test_the_same_statement_again_is_held_under_the_terms(client, locked, summary):
    summary("31 Jan 2026", remaining(1))
    again = upload(client, "2026-01.pdf")

    assert again["errors"] == []
    assert again["figures"][0]["held"] is True


def test_a_negative_total_of_remaining_instalments_is_refused_plainly():
    terms = loan_terms.checked(TERMS, "SGD")

    with pytest.raises(ValueError, match="below nothing"):
        loan_terms.principal_owed(terms, -100, "2026-01-31")


# --- the confirm writes under the terms held then, or refuses -------------------------

CHANGED = ("DBS auto loan's hire-purchase terms changed since this file was read, so the figure "
           "shown is not what would be written. Read the file again. Nothing was imported.")


def test_terms_changed_between_upload_and_confirm_refuse_the_confirm(client, summary):
    loan = loan_with_terms(client)
    summary("31 Dec 2025", remaining(0))
    preview = upload(client, "2025-12.pdf")
    assert set_terms(client, loan, {**TERMS, "flat_rate": "2"}).status_code == 200

    resp = confirm(client, preview)

    assert (resp.status_code, resp.get_json()["error"]) == (409, CHANGED)
    assert anchors_of(client) == []


def test_terms_set_after_the_upload_refuse_the_confirm(client, summary):
    loan = make_account(client, LOAN, "loan")
    summary("31 Dec 2025", remaining(0))
    preview = upload(client, "2025-12.pdf")
    assert set_terms(client, loan, TERMS).status_code == 200

    resp = confirm(client, preview)

    assert (resp.status_code, resp.get_json()["error"]) == (409, CHANGED)
    assert anchors_of(client) == []


def test_terms_taken_off_after_the_upload_refuse_the_confirm(client, summary):
    loan = loan_with_terms(client)
    summary("31 Dec 2025", remaining(0))
    preview = upload(client, "2025-12.pdf")
    assert set_terms(client, loan, None).status_code == 200

    resp = confirm(client, preview)

    assert (resp.status_code, resp.get_json()["error"]) == (409, CHANGED)
    assert anchors_of(client) == []


# --- what is worked out: the Rule of 78, from the terms' own first figure ---------------


@pytest.mark.parametrize("k", [1, 13, 48])
def test_two_statements_one_instalment_apart_reconcile_to_the_rule_of_78(client, conn, summary, k):
    loan = loan_with_terms(client)
    pay(conn, client, loan, k - 1)
    import_statement(client, summary, month_end(k - 1) if k > 1 else "2025-12-31", remaining(k - 1))
    pay(conn, client, loan, k, start=k)

    import_statement(client, summary, month_end(k), remaining(k))

    period = loan_interest.periods(conn, loan)[-1]
    paid = INSTALMENT if k < 48 else LAST
    assert period["to"] == month_end(k)
    assert period["interest_minor"] == interest_of(k)
    assert period["fell_minor"] == paid - interest_of(k)


def test_the_terms_are_the_first_figure_so_the_first_statement_works_interest_out(client, conn, summary):
    # No typed figure: the amount financed is owed from 31 Dec 2025.
    # 6,000.00 x 48 / 1,176 = 244.897...: 244.90 of interest, 1,964.10 of principal.
    loan = loan_with_terms(client)
    pay(conn, client, loan, 1)

    import_statement(client, summary, month_end(1), remaining(1))

    [period] = loan_interest.periods(conn, loan)
    assert (period["from"], period["to"]) == ("2025-12-31", "2026-01-31")
    assert (period["interest_minor"], period["fell_minor"]) == (24_490, 196_410)


def test_undoing_the_terms_before_any_statement_takes_their_interest_too(client, conn):
    loan = loan_with_terms(client)
    terms_entry = entries(client)[0]["id"]
    pay(conn, client, loan, 1)
    client.post("/api/anchors", json={"account_id": loan, "amount": units(10_000_000 - 196_410).replace(",", ""),
                                      "date": "2026-01-31"})
    assert [p["interest_minor"] for p in loan_interest.periods(conn, loan)] == [24_490]
    figure_entry = entries(client)[0]["id"]

    assert client.post(f"/api/history/{figure_entry}/undo").status_code == 200
    assert client.post(f"/api/history/{terms_entry}/undo").status_code == 200

    assert loan_interest.periods(conn, loan) == []
    assert [t for t in client.get("/api/transactions?per_page=500").get_json()["transactions"]
            if t["book_source"] == "derived"] == []


def test_household_spending_falls_by_the_principal_only(client, conn, summary):
    loan = loan_with_terms(client)
    pay(conn, client, loan, 1)
    import_statement(client, summary, month_end(1), remaining(1))

    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-01").get_json()

    assert cards["household"] == 244.90
    assert (cards["loan_instalments"], cards["loan_interest"], cards["loan_principal"]) == (
        2209.00, 244.90, 1964.10)


def test_an_instalment_with_a_benefit_off_it_still_fits_and_comes_out_as_less_interest(client, conn, summary):
    loan = loan_with_terms(client)
    instalment_row(conn, client, loan, due(1), INSTALMENT - 30_000)
    pay(conn, client, loan, 3, start=2)

    import_statement(client, summary, month_end(3), remaining(3))

    [period] = loan_interest.periods(conn, loan)
    assert period["interest_minor"] == interest_of(1) + interest_of(2) + interest_of(3) - 30_000


# --- the safety net: a figure that does not fit the terms is named ----------------------


def test_a_typed_total_of_remaining_instalments_is_named_and_the_import_refused(client, conn, summary):
    loan = loan_with_terms(client)
    pay(conn, client, loan, 3)
    assert client.post("/api/anchors", json={"account_id": loan, "amount": units(remaining(2)).replace(",", ""),
                                             "date": "2026-02-28"}).status_code == 200
    before = anchors_of(client)

    error = refused_import(client, summary, month_end(3), remaining(3))

    assert error.startswith(
        "DBS auto loan's figure of S$ 101,582.00 for 28 Feb 2026 does not fit its hire-purchase terms: "
        "with 2 instalments naming it by then, they leave about S$ 96,068.03 of principal owed.")
    assert "It looks like the total of the remaining instalments, not principal owed" in error
    assert error.endswith("Nothing was imported.")
    assert anchors_of(client) == before


def test_statements_before_the_instalments_name_the_loan_are_refused(client, summary):
    loan_with_terms(client)

    error = refused_import(client, summary, month_end(2), remaining(2))

    assert "with 0 instalments naming it by then" in error
    assert "label the instalments as repayments of the loan" in error
    assert anchors_of(client) == []


def test_a_figure_one_instalment_out_is_named(client, conn, summary):
    # Two instalments due by the statement's day, one of them not labelled.
    loan = loan_with_terms(client)
    pay(conn, client, loan, 1)

    error = refused_import(client, summary, month_end(2), remaining(2))

    assert error.startswith("DBS auto loan's figure of ")
    assert "with 1 instalment naming it by then" in error


def test_a_statement_ahead_with_an_instalment_not_labelled_is_still_named(client, conn, summary):
    loan = loan_with_terms(client)
    pay(conn, client, loan, 1)

    error = refused_import(client, summary, month_end(2), remaining(3))

    assert "with 1 instalment naming it by then" in error
    assert anchors_of(client) == []


# --- a lender whose month-end statement takes off next month's instalment ------------
#
# The DBS car-loan shape: dated the month's end, it already leaves out the
# instalment due on the 15th of the month after. Each month-end statement
# after k instalments prints remaining(k + 1).


def test_month_end_statements_one_ahead_import_as_principal_after_the_instalments_paid(client, conn, summary):
    loan = loan_with_terms(client)
    pay(conn, client, loan, 4)

    import_statement(client, summary, month_end(4), remaining(5))

    assert [a["amount_minor"] for a in anchors_of(client)] == [
        -(remaining(5) + LEVEL - unearned(44)),
    ]
    assert loan_interest.misfit(conn, loan, LOAN, "SGD") is None


# Up to 46: the statement after 47 has taken off the last, smaller instalment,
# so that period carries the lender's 32.00 difference from a full one.
@pytest.mark.parametrize("k", [2, 13, 46])
def test_month_end_statements_one_ahead_reconcile_to_the_rule_of_78(client, conn, summary, k):
    loan = loan_with_terms(client)
    pay(conn, client, loan, k - 1)
    import_statement(client, summary, month_end(k - 1), remaining(k))
    pay(conn, client, loan, k, start=k)

    import_statement(client, summary, month_end(k), remaining(k + 1))

    period = loan_interest.periods(conn, loan)[-1]
    assert period["to"] == month_end(k)
    assert period["interest_minor"] == interest_of(k)
    assert period["fell_minor"] == INSTALMENT - interest_of(k)


def test_the_first_statement_one_ahead_works_interest_out_from_the_terms(client, conn, summary):
    # From the amount financed on 31 Dec 2025 to 31 Mar 2026: three instalments
    # paid, their Rule of 78 interest, less the 300.00 benefit off the first,
    # less the 0.67 by which the lender's 2,209.00 instalment taken off ahead
    # is more than the level 2,208.33 added back.
    loan = loan_with_terms(client)
    instalment_row(conn, client, loan, due(1), INSTALMENT - 30_000)
    pay(conn, client, loan, 3, start=2)

    import_statement(client, summary, month_end(3), remaining(4))

    [period] = loan_interest.periods(conn, loan)
    assert (period["from"], period["to"]) == ("2025-12-31", "2026-03-31")
    assert period["interest_minor"] == interest_of(1) + interest_of(2) + interest_of(3) - 30_000 - 67


# --- a start makes the table; a book from before it reads as having no terms ----------


def test_a_book_from_before_the_terms_reads_none_and_a_start_makes_the_table(client, temp_db):
    make_account(client, LOAN, "loan")
    conn = sqlite3.connect(str(temp_db))
    for op in ("insert", "update", "delete"):
        conn.execute(f"DROP TRIGGER history_loan_terms_{op}")
    conn.execute("DROP TABLE loan_terms")
    conn.commit()
    rows = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert not db._needs_converting(conn)
    conn.close()

    assert accounts(client)[LOAN]["terms"] is None
    db.init_db()

    conn = sqlite3.connect(str(temp_db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM loan_terms").fetchone()[0] == 0
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        assert {f"history_loan_terms_{op}" for op in ("insert", "update", "delete")} <= triggers
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == rows
    finally:
        conn.close()


def test_a_loan_with_terms_whose_write_fails_is_a_plain_refusal_and_nothing_is_added(client, temp_db):
    # A book not yet started (no loan_terms table) makes the terms' write fail.
    conn = sqlite3.connect(str(temp_db))
    for op in ("insert", "update", "delete"):
        conn.execute(f"DROP TRIGGER history_loan_terms_{op}")
    conn.execute("DROP TABLE loan_terms")
    conn.commit()
    conn.close()
    before = len(entries(client))

    r = client.post("/api/accounts", json={"name": LOAN, "type": "loan", "terms": TERMS})

    assert r.status_code == 400
    assert r.get_json() == {"error": "Failed to create account"}
    assert LOAN not in accounts(client)
    assert len(entries(client)) == before


# --- the chat: an account made with its terms ----------------------------------------


def test_the_chat_makes_a_loan_with_its_terms_in_one_change(client):
    answer = mcp_tools.call("create_account", {"name": LOAN, "kind": "loan", "terms": TERMS},
                            actor="chat:sample-subject")

    assert answer["ok"] is True, answer
    assert accounts(client)[LOAN]["terms"]["interest_minor"] == INTEREST
    assert undo_latest(client).status_code == 200
    assert LOAN not in accounts(client)


def test_the_chat_may_give_the_day_the_amount_financed_is_owed_from(client):
    answer = mcp_tools.call("create_account",
                            {"name": LOAN, "kind": "loan", "terms": {**TERMS, "owed_from": "2025-12-20"}},
                            actor="chat:sample-subject")

    assert answer["ok"] is True, answer
    assert accounts(client)[LOAN]["terms"]["owed_from"] == "2025-12-20"
    tool = next(t for t in mcp_tools.TOOLS if t.name == "create_account")
    assert "owed_from" in tool.input_schema["properties"]["terms"]["properties"]


def test_the_chat_cannot_put_terms_on_an_account_that_is_no_loan(client):
    answer = mcp_tools.call("create_account", {"name": "Sample Bank 0002", "kind": "bank", "terms": TERMS},
                            actor="chat:sample-subject")

    assert answer["ok"] is False
    assert "hire-purchase terms go on a loan" in answer["error"]
    assert "Sample Bank 0002" not in accounts(client)


# --- the screens ----------------------------------------------------------------------


def _node(code: str):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30,
                         check=True, encoding="utf-8")
    return json.loads(out.stdout)


MONEY_JS = [
    "const icon = n => `<i ${n}></i>`;",
    _function("esc"), _const("SIGNS"), _const("DIGITS"), "const formatters = {};",
    _function("digitsOf"), _function("groupFor"), _function("money"), _function("toMinor"),
]


def preview_html(preview: dict) -> str:
    code = "\n".join([
        *MONEY_JS,
        _const("MONTHS"), _function("day"), _function("plural"), _function("tag"),
        _const("RULE_FLOWS"), _function("flowSaid"),
        _const("importState"), _const("IMPORT_GROUP_FILES"), _function("groupWords"), _function("laterWords"), _function("groupSize"),
        _function("stmtSum"), _function("loanFigureSum"), _function("importPreviewHTML"),
        f"console.log(JSON.stringify(importPreviewHTML({json.dumps(preview)})));",
    ])
    return _node(code)


FIGURE = {
    "account": LOAN, "kind": "loan", "date": "2026-01-31", "currency": "SGD",
    "new_account": False, "held": False,
    "printed_minor": remaining(1), "instalments_left": 47, "months": 48, "left_by_schedule": 47,
    "unearned_minor": unearned(47), "method": "rule-of-78", "owed_minor": remaining(1) - unearned(47),
}


@needs_node
def test_the_preview_shows_the_printed_total_less_the_unearned_interest():
    html = preview_html({"groups": [], "errors": [], "figures": [FIGURE]})

    assert "remaining instalments" in html and f"S$ {units(remaining(1))}" in html
    assert "interest not yet earned" in html and f"S$ {units(unearned(47))}" in html
    assert "principal owed" in html and f"S$ {units(remaining(1) - unearned(47))}" in html
    assert "47 of 48 instalments left" in html
    assert "Import 1 figure" in html


@needs_node
def test_the_preview_adds_back_an_instalment_taken_off_ahead():
    ahead = {**FIGURE, "printed_minor": remaining(2), "ahead_minor": LEVEL,
             "owed_minor": remaining(2) + LEVEL - unearned(47)}
    html = preview_html({"groups": [], "errors": [], "figures": [ahead]})

    assert "the next instalment, taken off before it is due" in html and f"S$ {units(LEVEL)}" in html
    assert f"S$ {units(remaining(2) + LEVEL - unearned(47))}" in html
    assert "taken off before it is due" not in preview_html({"groups": [], "errors": [], "figures": [FIGURE]})


@needs_node
def test_the_preview_says_when_the_schedule_counts_differently():
    html = preview_html({"groups": [], "errors": [], "figures": [{**FIGURE, "left_by_schedule": 48}]})

    assert "the terms' dates leave 48" in html


@needs_node
def test_a_figure_without_terms_is_shown_as_owed():
    plain = {k: FIGURE[k] for k in ("account", "kind", "date", "currency", "new_account", "held")}
    html = preview_html({"groups": [], "errors": [], "figures": [{**plain, "owed_minor": 5_234_000}]})

    assert "owed" in html and "S$ 52,340.00" in html
    assert "principal owed" not in html


SHOWN = {"financed_minor": 10_000_000, "interest_minor": INTEREST, "flat_rate": "1.50", "months": 48,
         "first_instalment": "2026-01-15", "owed_from": "2025-12-31", "method": "rule-of-78",
         "instalment_minor": 220_833}


def terms_fields_html(terms, lock=None) -> str:
    code = "\n".join([
        *MONEY_JS, _const("MONTHS"), _function("day"), _function("amountField"), _function("termsFieldsHTML"),
        f"console.log(JSON.stringify(termsFieldsHTML({json.dumps(terms)}, 'SGD', {json.dumps(lock)})));",
    ])
    return _node(code)


@needs_node
def test_the_account_sheet_offers_a_loans_terms_as_entered_and_formatted():
    html = terms_fields_html(SHOWN)

    assert "Hire-purchase terms" in html
    for field, value in (("ac-hp-financed", "100,000.00"), ("ac-hp-months", "48"), ("ac-hp-rate", "1.50"),
                         ("ac-hp-interest", ""), ("ac-hp-first", "2026-01-15"), ("ac-hp-from", "2025-12-31")):
        assert f'id="{field}"' in html and f'value="{value}"' in html, field
    assert "disabled" not in html


@needs_node
def test_terms_held_as_interest_show_the_interest_and_no_rate():
    html = terms_fields_html({**SHOWN, "flat_rate": None, "interest_minor": 600_001})

    assert 'id="ac-hp-rate" value=""' in html.replace("  ", " ") or ('id="ac-hp-rate"' in html and 'value="6,000.01"' in html)
    assert 'value="6,000.01"' in html


@needs_node
def test_the_account_sheet_offers_empty_terms_for_a_loan_without():
    html = terms_fields_html(None)

    assert 'id="ac-hp-financed"' in html and 'value=""' in html


@needs_node
def test_a_locked_loans_terms_are_read_only_with_the_reason():
    html = terms_fields_html(SHOWN, LOCKED)

    assert html.count(" disabled") >= 6
    assert LOCKED[:40] in html


def terms_change(held, fields):
    code = "\n".join([
        _function("termsChange"),
        f"const out = termsChange({json.dumps(held)}, {json.dumps(fields)});",
        "console.log(JSON.stringify(out === undefined ? 'unchanged' : out));",
    ])
    return _node(code)


FIELDS = {"financed": "100,000.00", "months": "48", "flat_rate": "1.50", "interest": "",
          "first_instalment": "2026-01-15", "owed_from": "2025-12-31"}
HELD_FIELDS = dict(FIELDS)


@needs_node
def test_terms_the_operator_did_not_touch_are_not_sent():
    assert terms_change(HELD_FIELDS, FIELDS) == "unchanged"


@needs_node
def test_terms_the_operator_changed_are_sent_as_typed():
    sent = terms_change(HELD_FIELDS, {**FIELDS, "flat_rate": "1.6"})

    assert sent == {"financed": "100000.00", "months": 48, "flat_rate": "1.6", "first_instalment": "2026-01-15",
                    "owed_from": "2025-12-31", "method": "rule-of-78"}


@needs_node
def test_terms_cleared_by_the_operator_are_sent_as_none():
    blank = {k: "" for k in FIELDS}

    assert terms_change(HELD_FIELDS, blank) is None
    assert terms_change(blank, blank) == "unchanged"


@needs_node
def test_the_figure_dialog_on_a_loan_with_terms_asks_for_principal_owed():
    code = "\n".join([
        _const("SIGNS"), "const OWED_KINDS = ['loan'];", _function("figureLabel"),
        "const a = {type: 'loan', currency: 'SGD', terms: {months: 48}};",
        "const b = {type: 'loan', currency: 'SGD', terms: null};",
        "console.log(JSON.stringify([figureLabel(a), figureLabel(b)]));",
    ])
    with_terms, without = _node(code)

    assert with_terms.startswith("Principal owed now, in S$")
    assert "not the statement's total of remaining instalments" in with_terms
    assert without == "What is owed now, in S$"


def test_undoing_the_terms_takes_the_interest_worked_out_from_them(client, conn):
    loan = loan_with_terms(client)
    terms_entry = entries(client)[0]["id"]
    pay(conn, client, loan, 1)
    client.post("/api/anchors", json={"account_id": loan, "amount": units(10_000_000 - 196_410).replace(",", ""),
                                      "date": "2026-01-31"})

    assert client.post(f"/api/history/{terms_entry}/undo").status_code == 200

    assert loan_interest.periods(conn, loan) == []
    assert [t for t in client.get("/api/transactions?per_page=500").get_json()["transactions"]
            if t["book_source"] == "derived"] == []


# --- the polish pass (the operator's screen review, ui-review-14) ------------------------


@needs_node
def test_polish_the_terms_hint_says_its_day_in_the_apps_own_date_format():
    html = terms_fields_html(SHOWN)
    hint = html.rsplit('<span class="hint">', 1)[1]

    assert "owed from 31 Dec 2025" in hint, hint
    assert "2025-12-31" not in hint


def figure_js():
    return ["const OWED_KINDS = ['loan'];", _const("STALE_DAYS"), _const("MONTHS"), _function("day"),
            _function("figureEmptyLine"), _function("figureCheckLine")]


@needs_node
def test_polish_a_loan_with_terms_and_no_figure_says_what_is_true():
    code = "\n".join([
        *figure_js(),
        "const a = {type: 'loan', currency: 'SGD', terms: {months: 48}};",
        "const b = {type: 'loan', currency: 'SGD', terms: null};",
        "const c = {type: 'holding', currency: 'SGD'};",
        "console.log(JSON.stringify([figureEmptyLine(a), figureEmptyLine(b), figureEmptyLine(c)]));",
    ])
    with_terms, without, holding = _node(code)

    assert "left out of net worth until it has one" not in with_terms
    assert "interest is worked out from its terms" in with_terms
    assert "joins net worth" in with_terms and "first statement" in with_terms and "principal owed" in with_terms
    assert without == holding == "No figure yet: it is left out of net worth until it has one."


@needs_node
def test_polish_the_figure_dialog_says_the_next_import_checks_a_figure_on_a_loan_with_terms():
    code = "\n".join([
        *figure_js(),
        "const a = {type: 'loan', currency: 'SGD', terms: {months: 48}};",
        "const b = {type: 'loan', currency: 'SGD', terms: null};",
        "console.log(JSON.stringify([figureCheckLine(a), figureCheckLine(b)]));",
    ])
    with_terms, without = _node(code)

    assert "nothing checks it" not in with_terms
    assert "next statement import checks it against the terms" in with_terms
    assert "nothing checks it" in without


@needs_node
def test_polish_the_loan_figure_cards_rule_of_78_note_sits_on_its_own_line():
    html = preview_html({"groups": [], "errors": [], "figures": [FIGURE]})
    import re
    row = re.search(r'<tr><td class="op">−</td><td>(.*?)</td>', html, re.S).group(1)

    assert row.startswith("interest not yet earned")
    assert '<div class="small muted">Rule of 78: 47 of 48 instalments left</div>' in row, row
