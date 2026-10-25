"""Hire-purchase terms on a loan: a statement's remaining instalments read as
principal owed (fin-redesign 14, case 28).

A hire-purchase statement prints the total of the remaining instalments,
which falls by a whole instalment a month. With the loan's terms held (amount
financed, months, flat rate or interest, first instalment, Rule of 78), the
import turns that total into principal owed, so the interest worked out
between two figures is the Rule of 78 interest of the instalments between
them and Household spending falls by the principal only.

Driven through the HTTP interface (the account route, the upload and its
confirm, the stat cards, Undo), the chat tools through mcp_tools.call, and
the screens' own functions from static/app.js under node. The statement goes
through the real reader (parse_dbs_loan.py) over synthetic page text. Every
name, term and figure here is invented.
"""

import json
import subprocess

import pytest

import loan_interest
import mcp_tools
import parse_dbs_loan
from test_client_seams import NODE, _const, _function, needs_node
from test_import_loan_figure import LOAN, accounts, confirm, make_account
from test_import_that_ties import anchors_of, upload
from test_parse_dbs_loan import LOAN_PAGE, MESSAGES_PAGE
from test_parse_hdfc import fake_open

# Invented terms: 100,000.00 over 48 months at 1.5% flat a year. Interest
# 6,000.00; 106,000.00 to repay; the level instalment 2,208.33, which the
# lender makes 2,209.00 with a last of 2,177.00.
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


def units(minor: int) -> str:
    return f"{minor // 100:,}.{minor % 100:02d}"


@pytest.fixture
def summary(monkeypatch):
    """Make the uploaded PDF read as the car-loan statement for a day."""
    def install(day: str, printed: int):
        page = LOAN_PAGE.replace("30 Apr 2026", day).replace("52,340.00", units(printed))
        monkeypatch.setattr(parse_dbs_loan.pdfplumber, "open", fake_open([page, MESSAGES_PAGE]))
    return install


def set_terms(client, account_id: int, terms):
    return client.put(f"/api/accounts/{account_id}", json={"terms": terms})


def loan_with_terms(client) -> int:
    loan = make_account(client, LOAN, "loan")
    assert set_terms(client, loan, TERMS).status_code == 200
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


def import_statement(client, summary, day: str, printed: int) -> dict:
    summary(day, printed)
    preview = upload(client, f"{day}.pdf")
    resp = confirm(client, preview)
    assert resp.status_code == 200, resp.get_json()
    return preview


# --- the terms, on the account ---------------------------------------------------


def test_terms_set_on_a_loan_are_read_back_with_the_instalment_and_rate(client):
    loan = loan_with_terms(client)

    assert accounts(client)[LOAN]["terms"] == {
        "financed_minor": 10_000_000, "interest_minor": INTEREST, "months": 48,
        "first_instalment": "2026-01-15", "method": "rule-of-78",
        "instalment_minor": 220_833, "flat_rate": "1.5",
    }
    assert loan


def test_the_interest_may_be_given_instead_of_the_flat_rate(client):
    loan = make_account(client, LOAN, "loan")
    terms = {**TERMS, "interest": "6000.00"}
    del terms["flat_rate"]

    assert set_terms(client, loan, terms).status_code == 200
    assert accounts(client)[LOAN]["terms"]["interest_minor"] == INTEREST
    assert accounts(client)[LOAN]["terms"]["flat_rate"] == "1.5"


def test_an_account_with_no_terms_reads_none(client):
    make_account(client, LOAN, "loan")
    make_account(client, "Sample Bank 0002", "bank")

    assert {a["terms"] for a in accounts(client).values()} == {None}


def test_setting_terms_is_one_change_and_undo_takes_them_off_exactly(client):
    loan = loan_with_terms(client)
    latest = client.get("/api/history").get_json()["entries"][0]

    assert client.post(f"/api/history/{latest['id']}/undo").status_code == 200

    assert accounts(client)[LOAN]["terms"] is None
    assert loan


def test_changing_terms_and_undoing_puts_the_old_terms_back(client):
    loan = loan_with_terms(client)
    before = accounts(client)[LOAN]["terms"]
    assert set_terms(client, loan, {**TERMS, "months": 60}).status_code == 200
    assert accounts(client)[LOAN]["terms"]["months"] == 60

    latest = client.get("/api/history").get_json()["entries"][0]
    assert client.post(f"/api/history/{latest['id']}/undo").status_code == 200

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
])
def test_terms_out_of_range_are_refused_and_nothing_is_written(client, change, said):
    loan = make_account(client, LOAN, "loan")
    entries = len(client.get("/api/history").get_json()["entries"])

    resp = set_terms(client, loan, {**TERMS, **change})

    assert resp.status_code == 400
    assert said in resp.get_json()["error"]
    assert accounts(client)[LOAN]["terms"] is None
    assert len(client.get("/api/history").get_json()["entries"]) == entries


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


def test_terms_are_not_changed_once_the_loan_holds_figures_from_its_statements(client, summary):
    loan = loan_with_terms(client)
    import_statement(client, summary, "31 Jan 2026", remaining(1))

    resp = set_terms(client, loan, {**TERMS, "months": 60})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == (
        "this loan holds 1 figure from its statements, worked out under the terms it has; undo "
        "those imports before changing its terms. Nothing was saved."
    )
    assert accounts(client)[LOAN]["terms"]["months"] == 48


def test_deleting_a_loan_with_terms_takes_them_too_and_undo_puts_both_back(client):
    loan = loan_with_terms(client)
    terms = accounts(client)[LOAN]["terms"]

    assert client.delete(f"/api/accounts/{loan}").status_code == 200
    assert LOAN not in accounts(client)
    latest = client.get("/api/history").get_json()["entries"][0]
    assert client.post(f"/api/history/{latest['id']}/undo").status_code == 200

    assert accounts(client)[LOAN]["terms"] == terms


# --- the import: remaining instalments read as principal owed ---------------------


def test_with_terms_the_preview_says_the_printed_total_and_the_principal_owed(client, summary):
    loan_with_terms(client)
    summary("31 Jan 2026", remaining(1))

    [figure] = upload(client, "2026-01.pdf")["figures"]

    assert figure == {
        "account": LOAN, "kind": "loan", "date": "2026-01-31", "currency": "SGD",
        "new_account": False, "held": False,
        "printed_minor": remaining(1), "instalments_left": 47, "months": 48,
        "left_by_schedule": 47, "unearned_minor": unearned(47), "method": "rule-of-78",
        "owed_minor": remaining(1) - unearned(47),
    }


def test_with_terms_the_confirm_writes_the_principal_owed(client, summary):
    loan = loan_with_terms(client)

    import_statement(client, summary, "31 Jan 2026", remaining(1))

    assert [(a["account_id"], a["amount_minor"]) for a in anchors_of(client)] == [
        (loan, -(remaining(1) - unearned(47))),
    ]


def test_without_terms_the_figure_is_imported_as_printed(client, summary):
    make_account(client, LOAN, "loan")
    summary("31 Jan 2026", remaining(1))

    [figure] = upload(client, "2026-01.pdf")["figures"]

    assert "printed_minor" not in figure
    assert figure["owed_minor"] == remaining(1)


def test_before_the_first_instalment_the_principal_owed_is_the_amount_financed(client, summary):
    loan_with_terms(client)
    summary("31 Dec 2025", remaining(0))

    [figure] = upload(client, "2025-12.pdf")["figures"]

    assert (figure["instalments_left"], figure["owed_minor"]) == (48, 10_000_000)


def test_the_last_smaller_instalment_is_still_one_left(client, summary):
    loan_with_terms(client)
    summary("30 Nov 2029", LAST)

    [figure] = upload(client, "2029-11.pdf")["figures"]

    assert (figure["instalments_left"], figure["unearned_minor"]) == (1, unearned(1))


def test_a_statement_ahead_of_the_schedule_says_both_counts(client, summary):
    # Dated before the 15th's instalment, but the lender has taken it off.
    loan_with_terms(client)
    summary("10 Feb 2026", remaining(2))

    [figure] = upload(client, "2026-02.pdf")["figures"]

    assert (figure["instalments_left"], figure["left_by_schedule"]) == (46, 47)


def test_the_same_statement_again_is_held_under_the_terms(client, summary):
    loan_with_terms(client)
    import_statement(client, summary, "31 Jan 2026", remaining(1))

    summary("31 Jan 2026", remaining(1))
    again = upload(client, "2026-01.pdf")

    assert again["errors"] == []
    assert again["figures"][0]["held"] is True


@pytest.mark.parametrize("k", [1, 13, 48])
def test_two_statements_one_instalment_apart_reconcile_to_the_rule_of_78(client, conn, summary, k):
    # Statement after k - 1 instalments, instalment k paid, statement after k.
    loan = loan_with_terms(client)
    before_day, paid_day, after_day = "2026-03-31", "2026-04-15", "2026-04-30"
    import_statement(client, summary, "31 Mar 2026", remaining(k - 1))
    paid = INSTALMENT if k < 48 else LAST
    instalment_row(conn, client, loan, paid_day, paid)

    import_statement(client, summary, "30 Apr 2026", remaining(k))

    [period] = loan_interest.periods(conn, loan)
    interest_k = unearned(49 - k) - unearned(48 - k)
    assert (period["from"], period["to"]) == (before_day, after_day)
    assert period["interest_minor"] == interest_k
    assert period["fell_minor"] == paid - interest_k


def test_the_first_instalments_split(client, conn, summary):
    # 6,000.00 x 48 / 1,176 = 244.897...: 244.90 of interest, 1,964.10 of principal.
    loan = loan_with_terms(client)
    import_statement(client, summary, "31 Dec 2025", remaining(0))
    instalment_row(conn, client, loan, "2026-01-15")

    import_statement(client, summary, "31 Jan 2026", remaining(1))

    [period] = loan_interest.periods(conn, loan)
    assert (period["interest_minor"], period["fell_minor"]) == (24_490, 196_410)


def test_household_spending_falls_by_the_principal_only(client, conn, summary):
    loan = loan_with_terms(client)
    import_statement(client, summary, "31 Dec 2025", remaining(0))
    instalment_row(conn, client, loan, "2026-01-15")
    import_statement(client, summary, "31 Jan 2026", remaining(1))

    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-01").get_json()

    assert cards["household"] == 244.90
    assert (cards["loan_instalments"], cards["loan_interest"], cards["loan_principal"]) == (
        2209.00, 244.90, 1964.10)


# --- the chat: an account made with its terms ----------------------------------------


def test_the_chat_makes_a_loan_with_its_terms_in_one_change(client):
    answer = mcp_tools.call("create_account", {"name": LOAN, "kind": "loan", "terms": TERMS},
                            actor="chat:sample-subject")

    assert answer["ok"] is True, answer
    assert accounts(client)[LOAN]["terms"]["interest_minor"] == INTEREST
    latest = client.get("/api/history").get_json()["entries"][0]
    assert client.post(f"/api/history/{latest['id']}/undo").status_code == 200
    assert LOAN not in accounts(client)


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


def preview_html(preview: dict) -> str:
    code = "\n".join([
        "const icon = n => `<i ${n}></i>`;",
        _function("esc"), _const("SIGNS"), _const("DIGITS"), "const formatters = {};",
        _function("digitsOf"), _function("groupFor"), _function("money"), _function("toMinor"),
        _const("MONTHS"), _function("day"), _function("plural"), _function("tag"),
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
def test_the_preview_says_when_the_schedule_counts_differently():
    html = preview_html({"groups": [], "errors": [], "figures": [{**FIGURE, "left_by_schedule": 48}]})

    assert "the terms' dates leave 48" in html


@needs_node
def test_a_figure_without_terms_is_shown_as_owed():
    plain = {k: FIGURE[k] for k in ("account", "kind", "date", "currency", "new_account", "held")}
    html = preview_html({"groups": [], "errors": [], "figures": [{**plain, "owed_minor": 5_234_000}]})

    assert "owed" in html and "S$ 52,340.00" in html
    assert "principal owed" not in html


def terms_fields_html(terms) -> str:
    code = "\n".join([
        _function("esc"), _function("termsFieldsHTML"),
        f"console.log(JSON.stringify(termsFieldsHTML({json.dumps(terms)})));",
    ])
    return _node(code)


@needs_node
def test_the_account_sheet_offers_a_loans_terms_filled_in():
    shown_terms = {"financed_minor": 10_000_000, "interest_minor": INTEREST, "months": 48,
                   "first_instalment": "2026-01-15", "method": "rule-of-78",
                   "instalment_minor": 220_833, "flat_rate": "1.5"}

    html = terms_fields_html(shown_terms)

    assert "Hire-purchase terms" in html
    for field, value in (("ac-hp-financed", "100000.00"), ("ac-hp-months", "48"),
                         ("ac-hp-rate", "1.5"), ("ac-hp-first", "2026-01-15")):
        assert f'id="{field}"' in html and f'value="{value}"' in html


@needs_node
def test_the_account_sheet_offers_empty_terms_for_a_loan_without():
    html = terms_fields_html(None)

    assert 'id="ac-hp-financed"' in html and 'value=""' in html


# --- an existing book gains the table through the change-history step ----------------


def test_a_book_from_before_the_terms_waits_for_the_change_history_step_which_makes_them(temp_db, capsys):
    import sqlite3

    import conversion
    import convert_all
    import db

    db.init_db()
    assert convert_all.main([str(temp_db)]) == 0     # every step applied, as the operator's book is
    conn = sqlite3.connect(str(temp_db))
    for op in ("insert", "update", "delete"):
        conn.execute(f"DROP TRIGGER history_loan_terms_{op}")
    conn.execute("DROP TABLE loan_terms")
    conn.commit()
    rows = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert db._needs_converting(conn)
    assert conversion.not_applied(conn) == ["change-history"]
    conn.close()
    capsys.readouterr()

    assert convert_all.main([str(temp_db)]) == 0
    assert "step change-history: applied" in capsys.readouterr().out
    assert convert_all.main([str(temp_db)]) == 0
    assert "step change-history: already-applied" in capsys.readouterr().out

    conn = sqlite3.connect(str(temp_db))
    try:
        assert not db._needs_converting(conn)
        assert conn.execute("SELECT COUNT(*) FROM loan_terms").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == rows
    finally:
        conn.close()
