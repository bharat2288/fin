"""The expect-back conversion step (personal-ledger 23, ticket 32; spec seam 3).

Loans to and from people fold into expectations and set-offs: each person's
rows in the old person line's own scope, walked in ROW_DAY order, become a
back or pay expectation or set off what is open the other way; an anchor
becomes an opening figure. The step runs behind the conversion runner's
backup, checks the old person line against the new records at every month
end, prints counts only, and a second run changes nothing. A down step takes
the converted records back out. From expand on, the review route's "a loan
to a person" applies the same rule in the label's own change.

Every name and figure is invented (the spec's cases 40-45a). Each test builds
its own database under tmp_path; nothing opens a real book.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

import balance_sheet
import book_type
import books_expect
import conversion
import convert_all
import convert_expect_back
import db
import expect_back
import history
from expect_fixtures import entry, let_go, set_off

OLD_SCHEMA = Path(__file__).parent / "schema_before_expect_back.sql"
TODAY = date(2026, 10, 19)
MONTHS = [f"2026-{m:02d}" for m in range(1, 11)]
SEVEN = ("expectation", "expectation_name", "set_off", "let_go", "refund_link", "sender_wording", "dismissal")


# --- the book -------------------------------------------------------------------


def _account(conn, name: str, kind: str, currency: str = "SGD") -> int:
    return conn.execute(
        "INSERT INTO accounts (name, short_name, type, currency, owner) VALUES (?, ?, ?, ?, 'Household')",
        (name, name.replace(" ", "-"), kind, currency),
    ).lastrowid


def _row(conn, account_id: int, day: str, description: str, amount_minor: int, person_id: int | None,
         *, flow: str = "movement", manual: int = 1) -> int:
    """A row on a calendar-month record, labelled through the review route's
    shape: a movement naming the person, set by hand."""
    first = day[:7] + "-01"
    held = conn.execute("SELECT id FROM statements WHERE account_id = ? AND statement_date = ?",
                        (account_id, first)).fetchone()
    statement_id = held[0] if held else conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, 'sample.csv')",
        (account_id, first)).lastrowid
    return conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type, "
        "flow_type_manual, other_side_id, cat_source) VALUES (?, ?, ?, ?, ?, ?, ?, 'manual')",
        (statement_id, day, description, amount_minor, flow, manual, person_id)).lastrowid


def seed(conn) -> dict:
    """Cases 40-43b on one book. Commits. Returns the ids by name."""
    savings = _account(conn, "Everyday savings", "bank")
    rupee = _account(conn, "Rupee card", "card", "INR")
    people = {name: _account(conn, name, "person") for name in ("Ravi", "Sam", "Parents", "Dev", "Lena")}
    ravi, parents, dev, lena = (people[n] for n in ("Ravi", "Parents", "Dev", "Lena"))
    ids = {
        "savings": savings, "rupee": rupee, **people,
        # 40: lent S$ 2,000, S$ 500 back.
        "ravi_out": _row(conn, savings, "2026-06-03", "TRANSFER TO RAVI", 200000, ravi),
        "ravi_in": _row(conn, savings, "2026-08-15", "PAYNOW FROM RAVI", -50000, ravi),
        # 43b: a rupee loan the S$ line never counted.
        "ravi_rupee": _row(conn, rupee, "2026-07-09", "TRANSFER TO RAVI INR", 1000000, ravi),
        # 42: S$ 5,000 from Parents, S$ 1,000 paid back.
        "parents_in": _row(conn, savings, "2026-03-01", "FROM PARENTS", -500000, parents),
        "parents_out": _row(conn, savings, "2026-08-01", "TO PARENTS", 100000, parents),
        # 43, 43a: more back than lent, then both directions.
        "dev_out_1": _row(conn, savings, "2026-05-01", "TO DEV", 50000, dev),
        "dev_in_1": _row(conn, savings, "2026-06-01", "FROM DEV", -60000, dev),
        "dev_out_2": _row(conn, savings, "2026-07-01", "TO DEV AGAIN", 30000, dev),
        "dev_in_2": _row(conn, savings, "2026-08-01", "FROM DEV AGAIN", -20000, dev),
        # 43b: before 1 Jan 2026, which the old line never counted.
        "lena_2025": _row(conn, savings, "2025-12-10", "TO LENA", 30000, lena),
    }
    # 41: Sam's opening figure, no rows.
    conn.execute("INSERT INTO anchors (account_id, date, amount, source) VALUES (?, '2026-01-01', 80000, 'supplied')",
                 (people["Sam"],))
    conn.commit()
    return ids


@pytest.fixture
def book(tmp_path, monkeypatch, capsys) -> Path:
    """A book in today's shape, before the step: fin starts on it empty and
    every step of the chain is run on it, then the people's rows are written."""
    path = tmp_path / "ledger.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    assert convert_all.main([str(path)]) == 0
    capsys.readouterr()
    return path


def open_book(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def seeded(path: Path) -> dict:
    conn = open_book(path)
    try:
        return seed(conn)
    finally:
        conn.close()


def query(path: Path, sql: str, params=()) -> list[tuple]:
    conn = open_book(path)
    try:
        return [tuple(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def dump(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def backups(path: Path, step: str = "expect-back") -> list[str]:
    return sorted(p.name for p in path.parent.iterdir() if p.name.startswith(f"{path.name}.pre-{step}-"))


def person_lines(path: Path) -> dict:
    """Each person's old line on the sheet at every month end: what the
    conversion and the down step must leave as it was."""
    conn = open_book(path)
    try:
        lines = {}
        for month in MONTHS:
            for section in balance_sheet.sheet(conn, month, today=TODAY)["sections"]:
                for line in section["lines"]:
                    if line.get("kind") == "person":
                        lines[(line["name"], month)] = line["balance_minor"]
        return lines
    finally:
        conn.close()


def net_worths(path: Path) -> dict:
    conn = open_book(path)
    try:
        return {m: balance_sheet.sheet(conn, m, today=TODAY)["net_worth_minor"] for m in MONTHS}
    finally:
        conn.close()


def expectations(path: Path) -> list[tuple]:
    """(row id or opening person, direction, amount, source, names) for every expectation."""
    return query(path, (
        "SELECT COALESCE(e.row_id, 'opening:' || e.person_id), e.direction, e.amount_minor, e.source, "
        "(SELECT GROUP_CONCAT(n.person_id) FROM expectation_name n WHERE n.expectation_id = e.id) "
        "FROM expectation e ORDER BY e.id"))


def set_offs(path: Path) -> list[tuple]:
    """(payment row, the expectation's row or opening, amount, source)."""
    return query(path, (
        "SELECT so.payment_row_id, COALESCE(e.row_id, 'opening:' || e.person_id), so.amount_minor, so.source "
        "FROM set_off so LEFT JOIN expectation e ON e.id = so.expectation_id ORDER BY so.id"))


def own(path: Path, row_id: int) -> int:
    return query(path, "SELECT own_minor FROM row_parts WHERE row_id = ?", (row_id,))[0][0]


def books(path: Path, on: str = "2026-10-31") -> dict:
    conn = open_book(path)
    try:
        shown = books_expect.sections(conn, on, today=TODAY)
    finally:
        conn.close()
    return {s["key"]: s for s in shown["sections"]}


def run(path: Path, capsys, *args) -> tuple[int, str]:
    code = convert_expect_back.main([*args, str(path)])
    return code, capsys.readouterr().out


# --- 40-43b: the conversion -------------------------------------------------------


def test_case_40_a_loan_with_a_partial_repayment(book, capsys):
    ids = seeded(book)
    lines, worth = person_lines(book), net_worths(book)

    code, out = run(book, capsys)

    assert code == 0, out
    assert (ids["ravi_out"], "back", 200000, "converted", str(ids["Ravi"])) in expectations(book)
    assert (ids["ravi_in"], ids["ravi_out"], 50000, "converted") in set_offs(book)
    ravi = next(x for x in books(book)["expected_back"]["lines"] if x["title"] == "Loan to Ravi")
    shown = (f"{ravi['title']} · {expect_back.short(ravi['open_minor'])} of "
             f"{expect_back.short(ravi['amount_minor'])} open · {ravi['age_days']} d")
    assert shown == "Loan to Ravi · S$ 1,500 of S$ 2,000 open · 138 d"
    # Readers and totals do not change at expand: every person's old line, and
    # net worth, at every month end, to the cent.
    assert person_lines(book) == lines
    assert net_worths(book) == worth
    # A dated backup first.
    assert backups(book) == [f"ledger.db.pre-expect-back-{date.today():%Y%m%d}.bak"]


def test_case_40_the_step_prints_counts_only(book, capsys):
    seeded(book)

    code, out = run(book, capsys)

    assert code == 0
    assert "persons: 4" in out
    assert "rows converted: 8" in out
    assert "expectations: 5 (opening figures: 1)" in out
    assert "set-offs: 4" in out
    for name in ("Ravi", "Sam", "Parents", "Dev", "Lena", "TRANSFER", "PAYNOW"):
        assert name not in out
    for amount in ("2,000", "2000", "200000", "1,500", "5,000", "500000"):
        assert amount not in out


def test_case_40a_rows_set_by_hand_convert(book, capsys):
    ids = seeded(book)
    assert query(book, "SELECT COUNT(*) FROM transactions WHERE flow_type_manual = 1 AND other_side_id IS NOT NULL") \
        == [(10,)]

    run(book, capsys)

    converted = {r[0] for r in expectations(book)} | {r[0] for r in set_offs(book)}
    assert {ids["ravi_out"], ids["ravi_in"], ids["parents_in"], ids["parents_out"]} <= converted


def test_case_41_an_opening_figure(book, capsys):
    ids = seeded(book)

    run(book, capsys)

    assert (f"opening:{ids['Sam']}", "back", 80000, "converted", str(ids["Sam"])) in expectations(book)
    sam = next(x for x in books(book)["expected_back"]["lines"] if x["title"] == "Opening figure · Sam")
    assert (sam["date"], sam["open_minor"], sam["age_days"]) == ("2026-01-01", 80000, 291)


def test_case_42_a_loan_from_a_person(book, capsys):
    ids = seeded(book)

    run(book, capsys)

    parents = [r for r in expectations(book) if r[4] == str(ids["Parents"])]
    assert parents == [(ids["parents_in"], "pay", 500000, "converted", str(ids["Parents"]))]
    assert [s for s in set_offs(book) if s[1] == ids["parents_in"]] == [
        (ids["parents_out"], ids["parents_in"], 100000, "converted")]
    to_pay = books(book)["to_pay_back"]["lines"]
    assert [(x["title"], x["open_minor"]) for x in to_pay] == [("From Parents", 400000)]


def test_case_43_more_back_than_lent_leaves_the_rest_to_label(book, capsys):
    ids = seeded(book)

    code, out = run(book, capsys)

    dev = [r for r in expectations(book) if r[4] == str(ids["Dev"])]
    assert dev == [
        (ids["dev_out_1"], "back", 50000, "converted", str(ids["Dev"])),
        (ids["dev_out_2"], "back", 30000, "converted", str(ids["Dev"])),
    ]
    assert [s for s in set_offs(book) if s[1] in (ids["dev_out_1"], ids["dev_out_2"])] == [
        (ids["dev_in_1"], ids["dev_out_1"], 50000, "converted"),
        (ids["dev_in_2"], ids["dev_out_2"], 20000, "converted"),
    ]
    # The S$ 100.00 left stays on the 1 Jun row as its own part to label.
    assert own(book, ids["dev_in_1"]) == -10000
    assert "1 row with money left to label" in out


def test_case_43a_dev_continued_ties_to_the_old_line(book, capsys):
    ids = seeded(book)
    old = person_lines(book)

    run(book, capsys)

    # Old line at 31 Oct: lent S$ 800, paid back S$ 800 -> S$ 0.
    assert old[("Dev", "2026-10")] == 0
    # New: open back 100 - open pay 0 - unplaced in 100 = 0.
    open_back = sum(x["open_minor"] for x in books(book)["expected_back"]["lines"] if x["names"] == ["Dev"])
    open_pay = sum(x["open_minor"] for x in books(book).get("to_pay_back", {"lines": []})["lines"]
                   if x["names"] == ["Dev"])
    unplaced_in = -own(book, ids["dev_in_1"])
    assert (open_back, open_pay, unplaced_in) == (10000, 0, 10000)
    assert open_back - open_pay - unplaced_in == old[("Dev", "2026-10")]


def test_two_loans_then_a_repayment_sets_off_the_oldest_first(book, capsys):
    conn = open_book(book)
    savings = _account(conn, "Everyday savings", "bank")
    kim = _account(conn, "Kim", "person")
    first = _row(conn, savings, "2026-02-02", "TO KIM", 10000, kim)
    second = _row(conn, savings, "2026-03-03", "TO KIM AGAIN", 5000, kim)
    back = _row(conn, savings, "2026-04-04", "FROM KIM", -12000, kim)
    conn.commit()
    conn.close()

    assert run(book, capsys)[0] == 0

    # A second loan out is a second back expectation, never a set-off of the first.
    assert expectations(book) == [(first, "back", 10000, "converted", str(kim)),
                                  (second, "back", 5000, "converted", str(kim))]
    assert set_offs(book) == [(back, first, 10000, "converted"), (back, second, 2000, "converted")]
    assert [x["open_minor"] for x in books(book)["expected_back"]["lines"]] == [3000]


def test_the_invariant_holds_for_every_person_at_every_month_end(book, capsys):
    ids = seeded(book)
    run(book, capsys)

    conn = open_book(book)
    try:
        lines = convert_expect_back.person_lines(conn)
    finally:
        conn.close()

    # Every month end from January to the month of the book's latest row.
    months = sorted({month for _, month in lines})
    assert months == [balance_sheet.month_end(f"2026-{m:02d}").isoformat() for m in range(1, 9)]
    assert all(old == new for old, new in lines.values())
    # Spot figures the old line gives: Ravi before and after his repayment.
    ravi = {month: old for (person, month), (old, _) in lines.items() if person == ids["Ravi"]}
    assert (ravi["2026-05-31"], ravi["2026-06-30"], ravi["2026-08-31"]) == (0, 200000, 150000)


def test_case_43b_other_currency_and_pre_2026_rows_are_counted_and_left_alone(book, capsys):
    ids = seeded(book)

    code, out = run(book, capsys)

    assert "rows in another currency: 1" in out
    assert "rows before 1 Jan 2026: 1" in out
    touched = {r[0] for r in expectations(book)} | {r[0] for r in set_offs(book)}
    assert ids["ravi_rupee"] not in touched and ids["lena_2025"] not in touched


# --- 44: re-run, labels after expand, an outside write ---------------------------------


def test_case_44_run_twice_the_second_run_is_a_no_op(book, capsys):
    seeded(book)
    run(book, capsys)
    before, written = dump(book), backups(book)

    code, out = run(book, capsys)

    assert code == 0
    assert "already-applied" in out
    assert dump(book) == before
    assert backups(book) == written


def test_case_44_a_book_with_no_person_rows_reads_as_applied(book):
    conn = open_book(book)
    try:
        assert convert_expect_back.STEP.is_applied(conn)
        assert "expect-back" not in conversion.not_applied(conn)
    finally:
        conn.close()
    assert conversion.run_step(book, convert_expect_back.STEP)["status"] == "already-applied"


def _label(client, row_id, person_id):
    return client.post(f"/api/review/{row_id}/label", json={"choice": "loan_to_person", "account_id": person_id})


def _review_row(conn, account_id, day, description, amount_minor) -> int:
    row_id = _row(conn, account_id, day, description, amount_minor, None, flow="review", manual=0)
    conn.commit()
    return row_id


@pytest.fixture
def lane(temp_db):
    """fin's own temporary book, after expand: Ravi and two waiting transfers."""
    conn = db.get_connection()
    try:
        savings = _account(conn, "Everyday savings", "bank")
        ravi = _account(conn, "Ravi", "person")
        conn.commit()
        out = _review_row(conn, savings, "2026-06-03", "TRANSFER 0001", 200000)
        back = _review_row(conn, savings, "2026-08-15", "PAYNOW 0002", -50000)
    finally:
        conn.close()
    return {"savings": savings, "Ravi": ravi, "out": out, "back": back, "path": temp_db}


def test_case_44_a_label_after_expand_writes_its_records_in_the_same_change(client, lane):
    path = lane["path"]

    first = _label(client, lane["out"], lane["Ravi"])
    assert first.status_code == 200, first.get_json()
    second = _label(client, lane["back"], lane["Ravi"])
    assert second.status_code == 200, second.get_json()

    assert expectations(path) == [(lane["out"], "back", 200000, "converted", str(lane["Ravi"]))]
    assert set_offs(path) == [(lane["back"], lane["out"], 50000, "converted")]
    conn = open_book(path)
    try:
        changed = {r[0] for r in conn.execute("SELECT tbl FROM change_rows WHERE entry_id = ?",
                                              (int(first.headers["X-Fin-Change"]),))}
        assert {"transactions", "expectation", "expectation_name"} <= changed
        # Nothing is left for the step: the label converted its row.
        assert convert_expect_back.STEP.is_applied(conn)
    finally:
        conn.close()

    # Undoing the label takes its set-off with it, in the label's one change.
    assert client.post(f"/api/history/{second.headers['X-Fin-Change']}/undo").status_code == 200
    assert set_offs(path) == []
    assert query(path, "SELECT flow_type, other_side_id FROM transactions WHERE id = ?",
                 (lane["back"],)) == [("review", None)]
    assert client.post(f"/api/history/{first.headers['X-Fin-Change']}/undo").status_code == 200
    assert expectations(path) == []


def test_case_44_an_outside_write_leaves_the_step_planned_and_fin_will_not_start(lane):
    conn = open_book(lane["path"])
    conn.execute("UPDATE transactions SET flow_type = 'movement', other_side_id = ?, flow_type_manual = 1 "
                 "WHERE id = ?", (lane["Ravi"], lane["out"]))
    conn.commit()
    conn.close()

    with pytest.raises(db.DatabaseNotConverted, match="expect-back"):
        db.init_db()

    conn = open_book(lane["path"])
    try:
        assert db._needs_converting(conn)
        assert "expect-back" in conversion.not_applied(conn)
    finally:
        conn.close()
    # convert_all runs it; then fin starts.
    assert convert_all.main([str(lane["path"])]) == 0
    db.init_db()
    assert expectations(lane["path"]) == [(lane["out"], "back", 200000, "converted", str(lane["Ravi"]))]


def test_a_book_from_before_expect_back_converts_and_starts(tmp_path, monkeypatch, capsys):
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA.read_text())
    book_type.seed_types(conn)  # as fin's starts on that shape seeded them
    conn.commit()
    conn.close()
    ids = seeded(path)
    monkeypatch.setattr(db, "DB_PATH", path)

    with pytest.raises(db.DatabaseNotConverted, match="change-history"):
        db.init_db()
    assert convert_all.main([str(path)]) == 0
    db.init_db()  # starts

    assert (ids["ravi_out"], "back", 200000, "converted", str(ids["Ravi"])) in expectations(path)
    assert backups(path, "change-history") and backups(path)
    assert conversion.run_step(path, convert_expect_back.STEP)["status"] == "already-applied"


def test_the_step_goes_last_in_the_chain():
    assert conversion.CHAIN[-1] == ("expect-back", "convert_expect_back")
    assert conversion.chain_step("convert_expect_back") is convert_expect_back.STEP


# --- 45: refusals ------------------------------------------------------------------


def test_case_45_a_person_with_two_anchors_is_refused_and_the_backup_kept(book, capsys):
    ids = seeded(book)
    conn = open_book(book)
    conn.execute("INSERT INTO anchors (account_id, date, amount, source) VALUES (?, '2026-04-01', 50000, 'supplied')",
                 (ids["Sam"],))
    conn.commit()
    conn.close()
    before = dump(book)

    code, out = run(book, capsys)

    assert code == 1
    assert "1 person has more than one anchor" in out
    assert dump(book) == before
    assert len(backups(book)) == 1
    # Still planned: fin will not start on it.
    conn = open_book(book)
    try:
        assert not convert_expect_back.STEP.is_applied(conn)
    finally:
        conn.close()


def test_the_step_fails_whole_when_an_old_line_would_change(book, capsys):
    # Ravi's repayment already placed by hand against a dinner the old line
    # never counted: the row is skipped, and Ravi's line would no longer tie.
    ids = seeded(book)
    conn = open_book(book)
    card = _account(conn, "Rewards card", "card")
    dinner = _row(conn, card, "2026-07-02", "HARBOUR GRILL", 30000, None, flow="expense", manual=0)
    jay = _account(conn, "Jay", "person")
    conn.commit()
    with entry(conn, "Expect back"):
        made = conn.execute("INSERT INTO expectation (row_id, direction, amount_minor, currency) "
                            "VALUES (?, 'back', 20000, 'SGD')", (dinner,)).lastrowid
        conn.execute("INSERT INTO expectation_name (expectation_id, person_id) VALUES (?, ?)", (made, jay))
    set_off(conn, ids["ravi_in"], made, 20000)
    conn.close()
    before = dump(book)

    code, out = run(book, capsys)

    assert code == 1
    assert "1 person's line would change at 1 month end" in out
    assert "Ravi" not in out
    assert dump(book) == before


# --- 45a: the down step --------------------------------------------------------------


def test_case_45a_the_down_step_restores_every_old_person_line(book, capsys):
    seeded(book)
    lines, worth = person_lines(book), net_worths(book)
    run(book, capsys)
    assert expectations(book)

    code, out = run(book, capsys, "--down")

    assert code == 0, out
    assert query(book, "SELECT COUNT(*) FROM expectation") == [(0,)]
    assert query(book, "SELECT COUNT(*) FROM expectation_name") == [(0,)]
    assert query(book, "SELECT COUNT(*) FROM set_off") == [(0,)]
    assert person_lines(book) == lines
    assert net_worths(book) == worth
    assert len(backups(book, "expect-back-down")) == 1
    # The step is planned again: fin would not start until it runs.
    conn = open_book(book)
    try:
        assert not convert_expect_back.STEP.is_applied(conn)
    finally:
        conn.close()
    # Run again, it changes nothing.
    assert run(book, capsys, "--down")[1].count("already-applied") == 1


def test_case_45a_the_down_step_is_refused_while_hand_changes_rest_on_converted_items(book, capsys):
    ids = seeded(book)
    run(book, capsys)
    conn = open_book(book)
    ravi = conn.execute("SELECT id FROM expectation WHERE row_id = ?", (ids["ravi_out"],)).fetchone()[0]
    sam = conn.execute("SELECT id FROM expectation WHERE person_id = ?", (ids["Sam"],)).fetchone()[0]
    paid = _row(conn, ids["savings"], "2026-09-10", "PAYNOW 0003", -10000, None, flow="review", manual=0)
    conn.commit()
    set_off(conn, paid, ravi, 10000)                    # a hand set-off
    let_go(conn, sam, "gift", "2026-10-19")             # a let-go
    conn.close()
    before = dump(book)

    code, out = run(book, capsys, "--down")

    assert code == 1
    assert "2 converted items have hand changes: unlink or undo them first" in out
    assert dump(book) == before
    assert backups(book, "expect-back-down") == []


def test_case_45a_an_amount_edit_on_a_converted_item_counts_as_a_hand_change(book, capsys):
    ids = seeded(book)
    run(book, capsys)
    conn = open_book(book)
    parents = conn.execute("SELECT id FROM expectation WHERE row_id = ?", (ids["parents_in"],)).fetchone()[0]
    with entry(conn, "Changed what is to pay back"):
        conn.execute("UPDATE expectation SET amount_minor = 450000 WHERE id = ?", (parents,))
    conn.close()

    code, out = run(book, capsys, "--down")

    assert code == 1
    assert "1 converted item has hand changes: unlink or undo them first" in out

    # Undone, it is no longer a hand change.
    conn = open_book(book)
    with entry(conn, "Changed what is to pay back"):
        conn.execute("UPDATE expectation SET amount_minor = 500000 WHERE id = ?", (parents,))
    conn.close()
    assert run(book, capsys, "--down")[0] == 0
    assert history.TRACKED  # the edits were recorded in the change history
