"""The expect-back conversion step (personal-ledger 23, ticket 32; spec seam 3).

Loans to and from people fold into expectations and set-offs: each person's
rows in the old person line's own scope, walked in ROW_DAY order, become a
back or pay expectation or set off what is open the other way; an anchor
becomes an opening figure. The step runs behind the conversion runner's
backup, checks the old person line against the new records at every month
end, prints counts only, and a second run changes nothing. Since the
contract (ticket 47) the down step and the review route's "a loan to a
person" are retired (tests/test_expect_contract_retire.py), and the old
person line is the step's own read (convert_expect_back.old_line).

Every name and figure is invented (the spec's cases 40-45). Each test builds
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
from expect_fixtures import entry, set_off

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
    every step of the chain is run on it, its record that expect-back ran is
    taken away, then the people's rows are written."""
    path = tmp_path / "ledger.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    assert convert_all.main([str(path)]) == 0
    capsys.readouterr()
    before_the_step(path)
    return path


def before_the_step(path: Path) -> None:
    """Take away the book's record that expect-back ran, as on a book (or a
    backup) from before the step: fin records it on a new book as it starts."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DELETE FROM conversion_steps WHERE step = 'expect-back'")
        conn.commit()
    finally:
        conn.close()


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
    """Each person's old line at every month end, as the sheet counted it
    before the switch took persons off it: what the conversion must leave as
    it was."""
    conn = open_book(path)
    try:
        persons = conn.execute(
            "SELECT id, name, type, currency, status FROM accounts WHERE type = 'person'").fetchall()
        return {
            (p["name"], month): convert_expect_back.old_line(conn, p, balance_sheet.month_end(month).isoformat())
            for month in MONTHS for p in persons
        }
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
    lines = person_lines(book)

    code, out = run(book, capsys)

    assert code == 0, out
    assert (ids["ravi_out"], "back", 200000, "converted", str(ids["Ravi"])) in expectations(book)
    assert (ids["ravi_in"], ids["ravi_out"], 50000, "converted") in set_offs(book)
    ravi = next(x for x in books(book)["expected_back"]["lines"] if x["title"] == "Loan to Ravi")
    shown = (f"{ravi['title']} · {expect_back.short(ravi['open_minor'])} of "
             f"{expect_back.short(ravi['amount_minor'])} open · {ravi['age_days']} d")
    assert shown == "Loan to Ravi · S$ 1,500 of S$ 2,000 open · 138 d"
    # Every person's old line at every month end, to the cent. (Net worth
    # across the switch: tests/test_expect_switch.py, cases 40 and 43.)
    assert person_lines(book) == lines
    # A dated backup first.
    assert backups(book) == [f"ledger.db.pre-expect-back-{date.today():%Y%m%d}.bak"]


def test_case_40_the_step_prints_counts_and_names_only_the_rows_left_to_label(book, capsys):
    # The review's ruling over case 40's "counts only": the operator reads
    # Σ unplaced, so each row left with money to label is listed, with its
    # person and amount. Nothing else names a person or an amount.
    seeded(book)

    code, out = run(book, capsys)

    assert code == 0
    assert "persons: 4" in out
    assert "rows converted: 8" in out
    assert "expectations: 5 (opening figures: 1)" in out
    assert "set-offs: 4" in out
    listed = ["  2026-06-01  FROM DEV  Dev  SGD -100.00 left of SGD -600.00", "  Dev  SGD -100.00"]
    assert all(line in out.splitlines() for line in listed)
    rest = "\n".join(line for line in out.splitlines() if line not in listed)
    for name in ("Ravi", "Sam", "Parents", "Dev", "Lena", "TRANSFER", "PAYNOW"):
        assert name not in rest
    for amount in ("2,000", "2000", "200000", "1,500", "5,000", "500000", "100.00", "600.00"):
        assert amount not in rest


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
    assert "  2026-06-01  FROM DEV  Dev  SGD -100.00 left of SGD -600.00" in out


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


def _review_row(conn, account_id, day, description, amount_minor) -> int:
    row_id = _row(conn, account_id, day, description, amount_minor, None, flow="review", manual=0)
    conn.commit()
    return row_id


@pytest.fixture
def lane(temp_db):
    """fin's own temporary book: Ravi and two waiting transfers."""
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


def test_case_44_a_backup_from_before_the_step_leaves_it_planned_and_fin_will_not_start(lane):
    # A row labelled outside the app, on a book with no record that the step
    # ran: a backup restored from before it.
    conn = open_book(lane["path"])
    conn.execute("UPDATE transactions SET flow_type = 'movement', other_side_id = ?, flow_type_manual = 1 "
                 "WHERE id = ?", (lane["Ravi"], lane["out"]))
    conn.commit()
    conn.close()
    before_the_step(lane["path"])

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


# --- 44 (after the conversion): the operator's own acts never reopen it -----------------
#
# The step records that it ran, in the transaction its writes are in. After
# that, an Unlink, an Undo of the mark or a person figure entered or deleted
# leaves the book converted: fin still starts, and a second run of the chain
# re-creates nothing the operator took away.


@pytest.fixture
def app_client(book):
    """The Flask test client on the step's book (the `book` fixture points fin at it)."""
    import app as fin_app

    fin_app.app.config["TESTING"] = True
    with fin_app.app.test_client() as client:
        yield client


@pytest.fixture
def converted_book(book, capsys) -> dict:
    """Cases 40-43b, converted. Returns the ids by name."""
    ids = seeded(book)
    code, out = run(book, capsys)
    assert code == 0, out
    return ids


def still_converted(path: Path, capsys) -> None:
    """fin starts, the step reads as applied, and the chain skips it."""
    db.init_db()  # no DatabaseNotConverted
    conn = open_book(path)
    try:
        assert convert_expect_back.STEP.is_applied(conn)
        assert not db._needs_converting(conn)
        assert "expect-back" not in conversion.not_applied(conn)
    finally:
        conn.close()
    before = dump(path)
    assert convert_all.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "step expect-back: already-applied" in out, out
    assert dump(path) == before


def test_the_step_records_that_it_ran_and_a_book_before_it_reads_as_pending(book, capsys):
    seeded(book)
    conn = open_book(book)
    try:
        assert not conversion.recorded(conn, "expect-back")
        assert not convert_expect_back.STEP.is_applied(conn)
        assert db._needs_converting(conn)
    finally:
        conn.close()

    assert run(book, capsys)[0] == 0

    assert query(book, "SELECT step FROM conversion_steps") == [("expect-back",)]


def test_a_row_written_straight_into_a_converted_book_is_not_converted_again(converted_book, book, capsys):
    # The cost of the record: such a row cannot be told apart from one the
    # operator unlinked. Restoring a backup from before the step is the way
    # to bring rows the step should convert.
    conn = open_book(book)
    _row(conn, converted_book["savings"], "2026-09-09", "TO RAVI LATER", 10000, converted_book["Ravi"])
    conn.commit()
    conn.close()
    made = expectations(book)

    still_converted(book, capsys)
    assert expectations(book) == made


def test_unlinking_a_converted_set_off_leaves_the_book_converted(converted_book, book, app_client, capsys):
    ids = converted_book
    [(set_off_id,)] = query(book, "SELECT id FROM set_off WHERE payment_row_id = ?", (ids["ravi_in"],))

    resp = app_client.delete(f"/api/set-offs/{set_off_id}")

    assert resp.status_code == 200, resp.get_json()
    left = set_offs(book)
    assert (ids["ravi_in"], ids["ravi_out"], 50000, "converted") not in left
    still_converted(book, capsys)


def test_a_rerun_after_unlink_recreates_no_set_off(converted_book, book, app_client, capsys):
    ids = converted_book
    [(set_off_id,)] = query(book, "SELECT id FROM set_off WHERE payment_row_id = ?", (ids["ravi_in"],))
    assert app_client.delete(f"/api/set-offs/{set_off_id}").status_code == 200
    left, made = set_offs(book), expectations(book)

    code, out = run(book, capsys)

    assert code == 0, out
    assert "already-applied" in out
    assert set_offs(book) == left
    assert expectations(book) == made


def test_undoing_the_mark_on_a_converted_expectation_leaves_the_book_converted(
        converted_book, book, app_client, capsys):
    ids = converted_book
    [(sam,)] = query(book, "SELECT id FROM expectation WHERE row_id IS NULL AND person_id = ?", (ids["Sam"],))

    resp = app_client.delete(f"/api/expectations/{sam}")

    assert resp.status_code == 200, resp.get_json()
    still_converted(book, capsys)
    # The opening figure the operator took away is not made again.
    assert not [x for x in expectations(book) if x[0] == f"opening:{ids['Sam']}"]


def test_a_person_figure_entered_after_the_conversion_leaves_the_book_converted(
        converted_book, book, app_client, capsys):
    ids = converted_book

    resp = app_client.post("/api/anchors", json={"account_id": ids["Lena"], "amount": "250.00",
                                                 "date": "2026-09-01"})

    assert resp.status_code in (200, 201), resp.get_json()
    still_converted(book, capsys)
    assert not [x for x in expectations(book) if x[0] == f"opening:{ids['Lena']}"]


def test_a_person_figure_entered_from_chat_leaves_the_book_converted(converted_book, book, capsys):
    import mcp_tools

    ids = converted_book

    answer = mcp_tools.call("enter_figure", {"account_id": ids["Lena"], "amount": "250.00", "date": "2026-09-01"},
                            actor="chat:sample-subject")

    assert answer["ok"], answer
    still_converted(book, capsys)


def test_a_person_figure_deleted_after_the_conversion_leaves_the_book_converted(book, app_client, capsys):
    conn = open_book(book)
    savings = _account(conn, "Everyday savings", "bank")
    kim = _account(conn, "Kim", "person")
    # Kim's figure on 1 Mar leaves out the 2 Feb row; deleting the figure
    # would bring that row into the old line's scope.
    _row(conn, savings, "2026-02-02", "TO KIM", 5000, kim)
    _row(conn, savings, "2026-04-04", "FROM KIM", -10000, kim)
    figure = conn.execute("INSERT INTO anchors (account_id, date, amount, source) "
                          "VALUES (?, '2026-03-01', 10000, 'supplied')", (kim,)).lastrowid
    conn.commit()
    conn.close()
    assert run(book, capsys)[0] == 0
    made = expectations(book)

    resp = app_client.delete(f"/api/anchors/{figure}")

    assert resp.status_code == 200, resp.get_json()
    still_converted(book, capsys)
    assert expectations(book) == made


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


def test_the_step_goes_after_change_history_and_before_label_source_in_the_chain():
    names = [name for name, _ in conversion.CHAIN]
    assert conversion.CHAIN[names.index("expect-back")] == ("expect-back", "convert_expect_back")
    assert names.index("change-history") < names.index("expect-back") < names.index("label-source")
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
    assert f"python convert_expect_back.py --plan {book}" in out  # who, read-only
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
