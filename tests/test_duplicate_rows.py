"""Duplicate rows (fin RUL-026): how a twin gets in, and taking one out.

HDFC 3528 holds same-day pairs with adjacent ids (#4667/#4668, ₹1.05L on
2 Sep; #4671/#4672, Airtel ₹429.82 on 11 Sep): one import inserted both. Two
of its statements cover September (2026-04_to_2026-09 and the 26 Oct
download), and an import reads up to eight files at once. Confirm counted a
row's copies across every statement in the import and inserted that many,
less what the book held: a row two overlapping statements both print came in
twice. Each statement ties on its own, so nothing refused it.

A row may be removed only when it has a twin: another row on the same
account, the same day and the same amount. The removal is one change in
Changes, and its Undo puts the row back as it was.

Statements are synthetic page text in place of pdfplumber, as
test_rupee_account_and_rates does; nothing opens a real statement. Every
name and number is invented. Amounts are paise, positive for money out.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess

import pytest

import chat_writes
import mcp_tools
import parse_hdfc
import rates
from test_client_seams import NODE, needs_node
from test_source_marks import _const, _function

HDFC = "HDFC Bank Savings 1234"
HEADER = "Date Narration Chq./Ref.No. ValueDt WithdrawalAmt. DepositAmt. ClosingBalance"


def _page(period: str, rows: list[str], summary: str) -> list[str]:
    return ["\n".join([
        "HDFC BANK LIMITED",
        "SAMPLE HOLDER",
        "Account No : 00000000001234",
        period,
        HEADER,
        *rows,
        "STATEMENT SUMMARY :-",
        "Opening Balance Dr Count Cr Count Debits Credits Closing Bal",
        summary,
    ])]


GROCER = "02/08/26 UPI-SAMPLE GROCER-PAYMENT 0000000000000001 02/08/26 1,500.00 4,98,500.00"
# Two coffees the same day, the same wording: a genuine pair one statement prints twice.
CAFE_1 = "02/08/26 UPI-SAMPLE CAFE 0000000000000009 02/08/26 250.00 4,98,250.00"
CAFE_2 = "02/08/26 UPI-SAMPLE CAFE 0000000000000009 02/08/26 250.00 4,98,000.00"
PAYEE = "02/09/26 NEFT DR-SAMPLE PAYEE 0000000000000002 02/09/26 1,05,000.00 3,93,000.00"
PHONE = "11/09/26 UPI-SAMPLE PHONE CO 0000000000000003 11/09/26 429.82 3,92,570.18"
SALARY = "05/10/26 NEFT CR-SAMPLE EMPLOYER 0000000000000004 05/10/26 10,000.00 4,02,570.18"

# August and September: opening Rs 5,00,000.00.
AUG_SEP = _page(
    "From : 01/08/2026 To : 30/09/2026",
    [GROCER, CAFE_1, CAFE_2, PAYEE, PHONE],
    "5,00,000.00 5 0 1,07,429.82 0.00 3,92,570.18",
)
# A download from 1 September: it prints September's rows again.
SEP_OCT = _page(
    "From : 01/09/2026 To : 26/10/2026",
    [PAYEE, PHONE, SALARY],
    "4,98,000.00 2 1 1,05,429.82 10,000.00 4,02,570.18",
)


class FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self, **_kwargs):
        return self._text


class FakePdf:
    def __init__(self, pages):
        self.pages = [FakePage(text) for text in pages]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def close(self):
        pass


@pytest.fixture(autouse=True)
def nothing_reaches_outside(monkeypatch):
    monkeypatch.setattr(rates, "_http_get", lambda _url: pytest.fail("a test tried to reach the network"))
    monkeypatch.delenv(parse_hdfc.PASSWORD_VARIABLE, raising=False)


def upload(client, monkeypatch, *statements: list[str]) -> dict:
    """One upload request carrying each statement as a file of its own: a
    stand-in file's bytes say which statement it is."""
    def opener(path, password=None, **_kwargs):
        with open(path, "rb") as f:
            return FakePdf(statements[int(f.read())])

    monkeypatch.setattr(parse_hdfc.pdfplumber, "open", opener)
    resp = client.post(
        "/api/import/upload",
        data={"files": [(io.BytesIO(str(n).encode()), f"sample-{n}.pdf") for n in range(len(statements))]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_json()
    preview = resp.get_json()
    assert preview["errors"] == [], preview["errors"]
    return preview


def bring_in(client, monkeypatch, *statements: list[str]) -> dict:
    preview = upload(client, monkeypatch, *statements)
    resp = client.post("/api/import/confirm", json={"import_id": preview["import_id"], "groups": preview["groups"]})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def held(conn) -> list[tuple[str, str, int]]:
    return [tuple(r) for r in conn.execute(
        "SELECT t.date, t.description, t.amount_minor FROM transactions t "
        "JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id "
        "WHERE a.name = ? ORDER BY t.date, t.id", (HDFC,))]


EVERY_ROW_ONCE = [
    ("2026-08-02", "UPI-SAMPLE GROCER-PAYMENT", 150000),
    ("2026-08-02", "UPI-SAMPLE CAFE", 25000),
    ("2026-08-02", "UPI-SAMPLE CAFE", 25000),
    ("2026-09-02", "NEFT DR-SAMPLE PAYEE", 10500000),
    ("2026-09-11", "UPI-SAMPLE PHONE CO", 42982),
    ("2026-10-05", "NEFT CR-SAMPLE EMPLOYER", -1000000),
]


# --- how the twins got in -------------------------------------------------------


def test_two_overlapping_statements_in_one_import_bring_each_row_in_once(client, conn, monkeypatch):
    done = bring_in(client, monkeypatch, AUG_SEP, SEP_OCT)

    assert held(conn) == EVERY_ROW_ONCE
    # The second statement's copies of September are said as duplicates skipped.
    assert (done["transactions_saved"], done["duplicates_skipped"]) == (6, 2)


def test_the_same_two_statements_imported_one_at_a_time_bring_each_row_in_once(client, conn, monkeypatch):
    bring_in(client, monkeypatch, AUG_SEP)
    bring_in(client, monkeypatch, SEP_OCT)

    assert held(conn) == EVERY_ROW_ONCE


def test_a_pair_one_statement_prints_twice_comes_in_twice_whatever_else_the_import_holds(client, conn, monkeypatch):
    bring_in(client, monkeypatch, SEP_OCT, AUG_SEP)

    assert [r for r in held(conn) if r[1] == "UPI-SAMPLE CAFE"] == [("2026-08-02", "UPI-SAMPLE CAFE", 25000)] * 2


def test_both_statements_printing_a_pair_in_their_overlap_bring_the_pair_in_once(client, conn, monkeypatch):
    aug_sep = _page(
        "From : 01/08/2026 To : 30/09/2026",
        ["02/08/26 UPI-SAMPLE GROCER-PAYMENT 0000000000000001 02/08/26 1,500.00 4,98,500.00",
         "02/09/26 NEFT DR-SAMPLE PAYEE 0000000000000002 02/09/26 1,05,000.00 3,93,500.00",
         "03/09/26 UPI-SAMPLE CAFE 0000000000000009 03/09/26 250.00 3,93,250.00",
         "03/09/26 UPI-SAMPLE CAFE 0000000000000009 03/09/26 250.00 3,93,000.00"],
        "5,00,000.00 4 0 1,07,000.00 0.00 3,93,000.00",
    )
    sep_oct = _page(
        "From : 01/09/2026 To : 26/10/2026",
        ["02/09/26 NEFT DR-SAMPLE PAYEE 0000000000000002 02/09/26 1,05,000.00 3,93,500.00",
         "03/09/26 UPI-SAMPLE CAFE 0000000000000009 03/09/26 250.00 3,93,250.00",
         "03/09/26 UPI-SAMPLE CAFE 0000000000000009 03/09/26 250.00 3,93,000.00"],
        "4,98,500.00 3 0 1,05,500.00 0.00 3,93,000.00",
    )
    bring_in(client, monkeypatch, aug_sep, sep_oct)

    assert [r for r in held(conn) if r[1] == "UPI-SAMPLE CAFE"] == [("2026-09-03", "UPI-SAMPLE CAFE", 25000)] * 2


def test_a_row_each_of_two_statements_prints_outside_the_others_period_comes_in_twice(client, conn, monkeypatch):
    """A card prints a late posting on the next statement under its own
    date: the same purchase twice, one on each side of the closing day, is
    two purchases, not one read twice."""
    aug = _page(
        "From : 01/08/2026 To : 31/08/2026",
        ["31/08/26 UPI-SAMPLE CAFE 0000000000000009 31/08/26 250.00 4,99,750.00"],
        "5,00,000.00 1 0 250.00 0.00 4,99,750.00",
    )
    sep = _page(
        "From : 01/09/2026 To : 30/09/2026",
        ["31/08/26 UPI-SAMPLE CAFE 0000000000000009 01/09/26 250.00 4,99,500.00"],
        "4,99,750.00 1 0 250.00 0.00 4,99,500.00",
    )
    done = bring_in(client, monkeypatch, aug, sep)

    assert held(conn) == [("2026-08-31", "UPI-SAMPLE CAFE", 25000)] * 2
    assert done["duplicates_skipped"] == 0


# --- taking a twin out ----------------------------------------------------------

ACTOR = "chat:sample-subject"


def chat(name, **args):
    return mcp_tools.call(name, args, actor=ACTOR)


def row_id(conn, description: str, nth: int = 0) -> int:
    return conn.execute("SELECT id FROM transactions WHERE description = ? ORDER BY id", (description,)).fetchall()[nth][0]


def copied_onto(conn, tx_id: int, statement_closing: str) -> int:
    """A copy of a row filed under another statement of its account, as the
    import before this fix brought a second statement's copy in."""
    statement_id = conn.execute(
        "SELECT s2.id FROM transactions t JOIN statements s ON s.id = t.statement_id "
        "JOIN statements s2 ON s2.account_id = s.account_id AND s2.statement_date = ? WHERE t.id = ?",
        (statement_closing, tx_id)).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type, book, type_id, "
        "service_id, book_source, type_source) SELECT ?, date, description, amount_minor, flow_type, book, type_id, "
        "service_id, book_source, type_source FROM transactions WHERE id = ?", (statement_id, tx_id))
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def twins(client, conn, monkeypatch):
    """The two statements imported one at a time, and the 2 Sep payment
    copied onto the second, as HDFC 3528's #4667/#4668 came in."""
    bring_in(client, monkeypatch, AUG_SEP)
    bring_in(client, monkeypatch, SEP_OCT)
    payee = row_id(conn, "NEFT DR-SAMPLE PAYEE")
    return {"payee": payee, "copy": copied_onto(conn, payee, "2026-10-26"),
            "cafe": (row_id(conn, "UPI-SAMPLE CAFE"), row_id(conn, "UPI-SAMPLE CAFE", 1)),
            "grocer": row_id(conn, "UPI-SAMPLE GROCER-PAYMENT"),
            "account_id": conn.execute("SELECT id FROM accounts WHERE name = ?", (HDFC,)).fetchone()[0]}


def every_row(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute("SELECT * FROM transactions ORDER BY id")]


def entry_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0]


def remove(client, tx_id: int):
    return client.post(f"/api/transactions/{tx_id}/remove-duplicate")


def test_twins_are_listed_by_account_day_and_amount_with_the_statement_each_came_on(client, twins):
    got = client.get(f"/api/duplicates?account_id={twins['account_id']}").get_json()["twins"]

    assert [(g["date"], g["amount_minor"], [r["id"] for r in g["rows"]]) for g in got] == [
        ("2026-09-02", 10500000, [twins["payee"], twins["copy"]]),
        ("2026-08-02", 25000, list(twins["cafe"])),
    ]
    payee, cafe = got
    assert (payee["account"], payee["currency"]) == (HDFC, "INR")
    # Each copy on a statement of its own, both covering the day: one payment read twice.
    assert [(r["statement"]["from"], r["statement"]["to"]) for r in payee["rows"]] == [
        ("2026-08-01", "2026-09-30"), ("2026-09-01", "2026-10-26")]
    assert payee["statements"] == 2 and "two statements" in payee["reading"]
    # One statement printed both: they may be two real payments.
    assert cafe["statements"] == 1 and "One statement printed both" in cafe["reading"]


def test_a_row_lists_its_own_twins_and_a_row_with_none_lists_nothing(client, twins):
    mine = client.get(f"/api/duplicates?tx_id={twins['copy']}").get_json()["twins"]
    assert [[r["id"] for r in g["rows"]] for g in mine] == [[twins["payee"], twins["copy"]]]
    assert client.get(f"/api/duplicates?tx_id={twins['grocer']}").get_json()["twins"] == []


def test_removing_a_twin_is_one_change_and_its_twin_stays(client, conn, twins):
    resp = remove(client, twins["copy"])

    assert resp.status_code == 200, resp.get_json()
    assert held(conn) == EVERY_ROW_ONCE
    assert conn.execute("SELECT id FROM transactions WHERE id = ?", (twins["payee"],)).fetchone() is not None
    entry_id = int(resp.headers["X-Fin-Change"])
    assert resp.headers["X-Fin-Change-Rows"] == "1"
    via, summary = conn.execute("SELECT via, summary FROM change_entries WHERE id = ?", (entry_id,)).fetchone()
    assert via == "app"
    assert summary.startswith("Removed a duplicate row: NEFT DR-SAMPLE PAYEE")
    assert f"#{twins['payee']} stays" in summary


def test_undo_puts_the_removed_row_back_as_it_was(client, conn, twins):
    before = every_row(conn)
    entry_id = int(remove(client, twins["copy"]).headers["X-Fin-Change"])

    assert client.post(f"/api/history/{entry_id}/undo").status_code == 200
    assert every_row(conn) == before


def test_a_row_with_no_twin_is_refused_and_nothing_changes(client, conn, twins):
    before, entries = every_row(conn), entry_count(conn)
    resp = remove(client, twins["grocer"])

    assert resp.status_code == 409
    assert "no twin" in resp.get_json()["error"]
    assert (every_row(conn), entry_count(conn)) == (before, entries)


def test_the_last_of_a_pair_is_refused_once_its_twin_is_gone(client, conn, twins):
    assert remove(client, twins["copy"]).status_code == 200
    resp = remove(client, twins["payee"])

    assert resp.status_code == 409 and "no twin" in resp.get_json()["error"]
    assert held(conn) == EVERY_ROW_ONCE


def test_the_same_day_and_amount_on_another_account_is_no_twin(client, conn, twins):
    made = client.post("/api/accounts", json={"name": "HDFC Bank Savings 9999", "type": "bank", "currency": "INR"})
    assert made.status_code == 200, made.get_json()
    other = made.get_json()["id"]
    statement = conn.execute("INSERT INTO statements (account_id, statement_date, printed) VALUES (?, '2026-08-31', 1)",
                             (other,)).lastrowid
    conn.execute("INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type) "
                 "VALUES (?, '2026-08-02', 'UPI-SAMPLE GROCER-PAYMENT', 150000, 'expense')", (statement,))
    conn.commit()

    assert client.get(f"/api/duplicates?tx_id={twins['grocer']}").get_json()["twins"] == []
    assert remove(client, twins["grocer"]).status_code == 409


def test_a_row_another_record_rests_on_is_refused_naming_it_and_its_twin_may_go(client, conn, twins):
    from expect_fixtures import mark

    first, second = twins["cafe"]
    mark(conn, first, 25000)
    before = every_row(conn)

    resp = remove(client, first)
    assert resp.status_code == 409
    assert "expected back" in resp.get_json()["error"] and f"#{second}" in resp.get_json()["error"]
    assert every_row(conn) == before
    listed = client.get(f"/api/duplicates?tx_id={first}").get_json()["twins"][0]["rows"]
    assert [bool(r["refusal"]) for r in listed] == [True, False]

    assert remove(client, second).status_code == 200


@pytest.mark.parametrize("rest", [
    "INSERT INTO folio_link (subject, subject_id, target, folio_id) VALUES ('row', {row}, 'tx', 7)",
    "INSERT INTO fx_pair (out_row_id, in_row_id, state) VALUES ({row}, {other}, 'declined')",
])
def test_a_row_a_link_names_by_id_is_refused(client, conn, twins, rest):
    conn.execute(rest.format(row=twins["copy"], other=twins["grocer"]))
    conn.commit()
    before = every_row(conn)

    resp = remove(client, twins["copy"])
    assert resp.status_code == 409 and f"Remove its twin #{twins['payee']} instead" in resp.get_json()["error"]
    assert every_row(conn) == before


def test_rows_fin_worked_out_are_never_twins_and_a_row_with_no_amount_lists_nothing(client, conn, twins):
    import loan_interest

    loan = client.post("/api/accounts", json={"name": "Sample Loan", "type": "loan"}).get_json()["id"]
    derived = conn.execute("INSERT INTO statements (account_id, statement_date, filename) VALUES (?, '2026-09-30', ?)",
                           (loan, loan_interest.DERIVED_STATEMENT)).lastrowid
    for _ in range(2):
        conn.execute("INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type) "
                     "VALUES (?, '2026-09-30', 'Loan interest', 1000, 'expense')", (derived,))
        conn.execute("INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type) "
                     "SELECT statement_id, '2026-07-01', 'NO AMOUNT', NULL, 'expense' FROM transactions WHERE id = ?",
                     (twins["grocer"],))
    conn.commit()

    listed = client.get("/api/duplicates")
    assert listed.status_code == 200
    assert [g["date"] for g in listed.get_json()["twins"]] == ["2026-09-02", "2026-08-02"]


def test_an_unknown_row_is_not_found(client, twins):
    assert remove(client, 999999).status_code == 404


def test_chat_lists_twins_and_removes_one_as_its_own_undoable_change(client, conn, twins):
    listed = chat("duplicate_rows", account_id=twins["account_id"])
    assert listed["ok"] and [g["date"] for g in listed["result"]["twins"]] == ["2026-09-02", "2026-08-02"]

    answer = chat("remove_duplicate", tx_id=twins["copy"])
    assert answer["ok"], answer
    assert answer["change"]["rows"] == 1
    assert tuple(conn.execute("SELECT via, actor FROM change_entries WHERE id = ?",
                              (answer["change"]["entry_id"],)).fetchone()) == ("chat", ACTOR)
    assert held(conn) == EVERY_ROW_ONCE

    assert chat("undo", entry_id=answer["change"]["entry_id"])["ok"]
    assert conn.execute("SELECT id FROM transactions WHERE id = ?", (twins["copy"],)).fetchone() is not None


def test_chat_is_refused_a_row_with_no_twin(client, conn, twins):
    before = every_row(conn)
    answer = chat("remove_duplicate", tx_id=twins["grocer"])

    assert answer["ok"] is False and "no twin" in answer["error"]
    assert every_row(conn) == before


def test_with_chat_writes_off_the_removal_is_refused_and_the_list_still_reads(client, conn, twins):
    assert client.put("/api/chat-writes", json={"enabled": False}).status_code == 200
    before = every_row(conn)

    assert chat("remove_duplicate", tx_id=twins["copy"]) == {"ok": False, "error": chat_writes.WRITES_OFF}
    assert every_row(conn) == before
    assert chat("duplicate_rows", tx_id=twins["copy"])["ok"]


# --- the row sheet's notice -----------------------------------------------------


def notice(client, tx_id: int) -> str:
    """The row sheet's twin notice as openRowSheet draws it, from the real route."""
    found = client.get(f"/api/duplicates?tx_id={tx_id}").get_json()["twins"]
    code = "\n".join([
        "function icon() { return ''; }", _const("MONTHS"), _function("esc"), _function("day"),
        _function("twinNoticeHTML"),
        f"console.log(JSON.stringify(twinNoticeHTML({json.dumps(found[0] if found else None)}, {tx_id})));"])
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, encoding="utf-8",
                         timeout=30, check=True, env={**os.environ, "TZ": "UTC"})
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", json.loads(out.stdout))).strip()


@needs_node
def test_the_row_sheet_names_the_twin_and_its_statement_and_offers_remove(client, twins):
    words = notice(client, twins["copy"])

    assert words.startswith("A twin on the same account, day and amount. "
                            "This row is on the statement 1 Sep 2026 – 26 Oct 2026.")
    assert f"#{twins['payee']} NEFT DR-SAMPLE PAYEE, on the statement 1 Aug 2026 – 30 Sep 2026" in words
    assert "most likely one payment the import read twice" in words
    assert words.endswith("Remove this row as a duplicate")


@needs_node
def test_the_row_sheet_says_why_a_twin_stays_and_says_nothing_for_a_row_with_none(client, conn, twins):
    from expect_fixtures import mark

    first, second = twins["cafe"]
    mark(conn, first, 25000)

    words = notice(client, first)
    assert "Remove this row" not in words and f"Remove its twin #{second} instead" in words
    assert notice(client, twins["grocer"]) == ""
