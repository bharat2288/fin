"""A car-loan statement imports as the loan's figure, never as rows.

Driven through the HTTP interface: the DBS car-loan summary is uploaded, the
preview read, and the preview confirmed, as for any statement; and through the
upload command. The summary goes through the real reader (parse_dbs_loan.py)
over synthetic page text. Every name and figure is invented.

What the summary states is what is owed on the car loan on its day. The
import writes it on the loan account as a statement's figure (an anchor:
owed, so negative), dated the summary's day, and loan interest is worked out
between two figures from the instalments the bank rows paid
(loan_interest.py).
"""

import json
import subprocess

import pytest

import fin_upload
import loan_interest
import parse_dbs_loan
from test_client_seams import NODE, _const, _function, needs_node
from test_fin_upload import send  # noqa: F401 (a fixture)
from test_import_that_ties import anchors_of, held, upload
from test_parse_dbs_loan import LOAN_PAGE, MESSAGES_PAGE
from test_parse_hdfc import fake_open

LOAN = "DBS auto loan"
APRIL_OWED = 5234000     # 52,340.00
MAY_OWED = 5150000       # 51,500.00: fell 840.00
INSTALMENT = 100000      # 1,000.00 paid in May


def page_for(day: str, owed: str) -> str:
    return (
        LOAN_PAGE.replace("30 Apr 2026", day)
        .replace("52,340.00", owed)
    )


@pytest.fixture
def summary(monkeypatch):
    """Make the uploaded PDF read as the car-loan summary for a day."""
    def install(day: str = "30 Apr 2026", owed: str = "52,340.00"):
        monkeypatch.setattr(parse_dbs_loan.pdfplumber, "open", fake_open([page_for(day, owed), MESSAGES_PAGE]))
    return install


def make_account(client, name: str, kind: str) -> int:
    resp = client.post("/api/accounts", json={"name": name, "type": kind})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def confirm(client, preview: dict, **extra):
    return client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [
            {"account": g["account"], "transactions": g["transactions"]} for g in preview["groups"]
        ],
        **extra,
    })


def accounts(client) -> dict:
    return {a["name"]: a for a in client.get("/api/accounts").get_json()}


def test_the_summary_previews_what_is_owed_on_its_day_and_no_rows(client, summary):
    make_account(client, LOAN, "loan")
    summary()

    preview = upload(client, "2026-04.pdf")

    assert preview["errors"] == []
    assert preview["groups"] == []
    assert preview["figures"] == [{
        "account": LOAN, "kind": "loan", "date": "2026-04-30", "owed_minor": APRIL_OWED,
        "currency": "SGD", "new_account": False, "held": False,
    }]


def test_confirm_writes_the_figure_on_the_loan_as_the_statements_and_no_rows(client, conn, summary):
    loan_id = make_account(client, LOAN, "loan")
    summary()
    before = held(conn)

    resp = confirm(client, upload(client, "2026-04.pdf"))

    assert resp.status_code == 200, resp.get_json()
    assert (resp.get_json()["transactions_saved"], resp.get_json()["figures_written"]) == (0, 1)
    [anchor] = anchors_of(client)
    assert (anchor["account_id"], anchor["date"], anchor["amount_minor"], anchor["source"]) == (
        loan_id, "2026-04-30", -APRIL_OWED, "statement",
    )
    assert held(conn) == {**before, "anchors": before["anchors"] + 1}


def test_the_figures_import_is_one_change_and_undo_takes_the_figure_back(client, summary):
    make_account(client, LOAN, "loan")
    summary()
    confirm(client, upload(client, "2026-04.pdf"))

    latest = client.get("/api/history").get_json()["entries"][0]
    assert latest["summary"] == "Imported a statement"
    assert client.post(f"/api/history/{latest['id']}/undo").status_code == 200

    assert anchors_of(client) == []


def test_with_no_loan_account_yet_the_confirm_makes_one_of_kind_loan(client, summary):
    summary()

    preview = upload(client, "2026-04.pdf")
    assert preview["figures"][0]["new_account"] is True
    resp = confirm(client, preview)

    assert resp.status_code == 200, resp.get_json()
    assert accounts(client)[LOAN]["type"] == "loan"
    assert [a["amount_minor"] for a in anchors_of(client)] == [-APRIL_OWED]


def test_an_account_of_that_name_that_is_no_loan_refuses_the_file(client, conn, summary):
    make_account(client, LOAN, "card")
    summary()
    before = held(conn)

    preview = upload(client, "2026-04.pdf")

    assert preview["figures"] == []
    [error] = preview["errors"]
    assert "is a card account" in error["error"]
    assert confirm(client, preview).status_code == 200
    assert held(conn) == before


def test_the_same_figure_again_is_shown_as_held_and_writes_nothing(client, summary):
    make_account(client, LOAN, "loan")
    summary()
    confirm(client, upload(client, "2026-04.pdf"))

    again = upload(client, "2026-04.pdf")
    resp = confirm(client, again)

    assert again["figures"][0]["held"] is True
    assert resp.status_code == 200
    assert resp.get_json()["figures_written"] == 0
    assert len(anchors_of(client)) == 1


def test_a_different_figure_already_held_for_that_day_refuses_the_file(client, summary):
    loan_id = make_account(client, LOAN, "loan")
    client.post("/api/anchors", json={"account_id": loan_id, "amount": "52000.00", "date": "2026-04-30"})
    summary()

    preview = upload(client, "2026-04.pdf")

    assert preview["figures"] == []
    [error] = preview["errors"]
    assert "already has a figure of S$ 52,000.00 for 2026-04-30" in error["error"]
    assert [a["source"] for a in anchors_of(client)] == ["supplied"]


def test_the_confirm_writes_the_previews_figure_never_one_the_request_brings(client, summary):
    make_account(client, LOAN, "loan")
    summary()
    preview = upload(client, "2026-04.pdf")

    resp = confirm(client, preview, figures=[{**preview["figures"][0], "owed_minor": 1}])

    assert resp.status_code == 200, resp.get_json()
    assert [a["amount_minor"] for a in anchors_of(client)] == [-APRIL_OWED]


def test_two_summaries_work_the_interest_out_from_the_instalment_paid_between(client, conn, summary):
    loan_id = make_account(client, LOAN, "loan")
    bank_id = make_account(client, "Sample Bank 0002", "bank")
    statement_id = conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, '2026-05-31', 'sample.csv')",
        (bank_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type, other_side_id)"
        " VALUES (?, '2026-05-15', 'SAMPLE CAR LOAN INSTALMENT', ?, 'movement', ?)",
        (statement_id, INSTALMENT, loan_id),
    )
    conn.commit()

    summary("30 Apr 2026", "52,340.00")
    confirm(client, upload(client, "2026-04.pdf"))
    summary("31 May 2026", "51,500.00")
    resp = confirm(client, upload(client, "2026-05.pdf"))

    assert resp.status_code == 200, resp.get_json()
    [period] = loan_interest.periods(conn, loan_id)
    assert (period["from"], period["to"], period["paid_minor"], period["fell_minor"]) == (
        "2026-04-30", "2026-05-31", INSTALMENT, APRIL_OWED - MAY_OWED,
    )
    assert period["interest_minor"] == INSTALMENT - (APRIL_OWED - MAY_OWED)



def test_a_figure_that_falls_with_no_instalment_naming_the_loan_is_refused(client, conn, summary):
    # The instalments are still filed as spending, not as repayments of the
    # loan: the fall would be worked out as negative interest. Refused, and
    # nothing of this import is written.
    loan_id = make_account(client, LOAN, "loan")
    summary("30 Apr 2026", "52,340.00")
    assert confirm(client, upload(client, "2026-04.pdf")).status_code == 200
    summary("31 May 2026", "51,500.00")

    resp = confirm(client, upload(client, "2026-05.pdf"))

    assert resp.status_code == 409
    assert resp.get_json()["error"] == (
        "DBS auto loan fell by S$ 840.00 between 30 Apr 2026 and 31 May 2026, more than the S$ 0.00 "
        "of instalments naming it: label the instalments as repayments of the loan first. "
        "Nothing was imported."
    )
    assert [a["date"] for a in anchors_of(client) if a["account_id"] == loan_id] == ["2026-04-30"]
    assert loan_interest.periods(conn, loan_id) == []

# --- the upload command ----------------------------------------------------------


def test_the_upload_command_imports_the_loans_figure(client, send, summary, tmp_path, capsys):  # noqa: F811
    make_account(client, LOAN, "loan")
    summary()
    (tmp_path / "2026-04.pdf").write_bytes(b"stand-in")

    assert fin_upload.main([str(tmp_path)], send=send) == 0

    assert "2026-04.pdf: imported 0 new rows (0 already there) and 1 figure" in capsys.readouterr().out
    assert [a["amount_minor"] for a in anchors_of(client)] == [-APRIL_OWED]


def test_a_dry_run_of_the_upload_command_says_the_figure_it_would_write(client, send, summary, tmp_path, capsys):  # noqa: F811
    make_account(client, LOAN, "loan")
    summary()
    (tmp_path / "2026-04.pdf").write_bytes(b"stand-in")

    assert fin_upload.main(["--dry-run", str(tmp_path)], send=send) == 0

    assert "2026-04.pdf: would import DBS auto loan: figure for 2026-04-30" in capsys.readouterr().out
    assert anchors_of(client) == []



# --- the import screen ------------------------------------------------------------


def preview_html(preview: dict) -> str:
    """The preview's own HTML, run under node from the real static/app.js (as
    test_client_seams does)."""
    code = "\n".join([
        "const icon = n => `<i ${n}></i>`;",
        _function("esc"), _const("SIGNS"), _const("DIGITS"), "const formatters = {};",
        _function("digitsOf"), _function("groupFor"), _function("money"), _function("toMinor"),
        _const("MONTHS"), _function("day"), _function("plural"), _function("tag"),
        _const("RULE_FLOWS"), _function("flowSaid"),
        _function("stmtSum"), _function("loanFigureSum"), _function("importPreviewHTML"),
        f"console.log(JSON.stringify(importPreviewHTML({json.dumps(preview)})));",
    ])
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30,
                         check=True, encoding="utf-8")
    return json.loads(out.stdout)


FIGURE = {
    "account": LOAN, "kind": "loan", "date": "2026-04-30", "owed_minor": APRIL_OWED,
    "currency": "SGD", "new_account": False, "held": False,
}


@needs_node
def test_the_import_screen_shows_the_loans_figure_and_offers_to_import_it():
    html = preview_html({"groups": [], "errors": [], "figures": [FIGURE]})

    assert "DBS auto loan" in html and "30 Apr 2026" in html and "S$ 52,340.00" in html
    assert 'data-act="import-confirm"' in html
    assert "Import 1 figure" in html
    assert "Nothing to import" not in html


@needs_node
def test_a_figure_already_held_is_shown_as_held_and_not_offered():
    html = preview_html({"groups": [], "errors": [], "figures": [{**FIGURE, "held": True}]})

    assert "already held" in html
    assert 'data-act="import-confirm"' not in html
    assert "Nothing to import" in html


# --- the polish pass (the operator's screen review, ui-review-31 f) ------------------------


@needs_node
def test_polish_a_row_that_is_not_spending_says_what_it_is_in_the_apps_words():
    rows = [{"date": "2026-04-0%d" % (i + 1), "description": f"SAMPLE ROW {i}", "amount_sgd": 10.0 + i, "flow_type": flow,
             "type_name": None} for i, flow in enumerate(["movement", "payment", "transfer", "income", "refund"])]
    group = {"account": "Sample Bank 0002", "currency": "SGD", "tie": "none", "total": 5, "statements": [],
             "transactions": rows, "typed": 0, "untyped": 5}
    html = preview_html({"groups": [group], "errors": [], "figures": []})
    import re
    labels = re.findall(r'<td class="row-label">(.*?)</td>', html, re.S)

    assert [re.sub(r"<[^>]+>", "", label) for label in labels] == [
        "a movement", "a card payment", "a transfer", "income", "a refund"]
