"""Expect back's foundation (personal-ledger 23, ticket 25): the seven
records and their history, the own part and its gate, the shared rules in
expect_back.py, and the undo guard in history.undo.

Figures in and out through public functions and routes, on a temporary
database: undo goes through /api/history/<id>/undo as Changes sends it.
Every name and figure is invented (the spec's case list).
"""

import re
import sqlite3
from pathlib import Path

import pytest

import app as fin_app
import conversion
import convert_all
import db
import expect_back
import expect_routes
import history
import person
import refund_links
from expect_fixtures import (
    check_book, dismiss, edit, entry, harbour_set_against, last_entry, let_go, mark, october,
    opening, refund_link, row, set_off, unlink, wording,
)
from test_balance_sheet import bring_in
from trend_fixtures import type_id

SEVEN = ("expectation", "expectation_name", "set_off", "let_go", "refund_link", "sender_wording", "dismissal")


def snapshot(conn) -> dict:
    """Every record, every row and the history: what a refusal must leave as it was."""
    shown = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")]
             for t in SEVEN + ("transactions", "change_entries", "change_rows")}
    return shown


def undo(client, entry_id):
    return client.post(f"/api/history/{entry_id}/undo")


@pytest.fixture
def story(conn):
    return october(conn)


# --- the records and their history ---------------------------------------------


def test_the_seven_records_are_tracked_and_every_column_is_recorded(conn):
    assert set(SEVEN) <= set(history.TRACKED)
    assert history.has_shape(conn)
    for table in SEVEN:
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for op in ("insert", "update", "delete"):
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                               (f"history_{table}_{op}",)).fetchone()[0]
            assert set(re.findall(r"'(\w+)', (?:NEW|OLD)\.\1\b", sql)) == columns, (table, op)


def test_each_record_written_in_a_change_is_one_entry_and_undo_takes_it_back(conn, client, story):
    jay = story.people["Jay"]
    with entry(conn, "Expect back") as made:
        expectation_id = mark(conn, story.harbour, 20000, names=(jay,))
    tables = {c["table"] for c in client.get(f"/api/history/{made}").get_json()["changes"]}
    assert tables == {"expectation", "expectation_name"}

    assert undo(client, made).status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM expectation").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM expectation_name").fetchone()[0] == 0
    # Undoing the undo marks it again, with the same id.
    assert undo(client, last_entry(conn)).status_code == 200
    assert [tuple(r) for r in conn.execute("SELECT id, amount_minor FROM expectation")] == [(expectation_id, 20000)]


@pytest.mark.parametrize("sql, args", [
    ("INSERT INTO expectation (row_id, direction, amount_minor, currency) VALUES (?, 'sideways', 100, 'SGD')", "row"),
    ("INSERT INTO expectation (row_id, direction, amount_minor, currency, source) VALUES (?, 'back', 100, 'SGD', 'magic')", "row"),
    ("INSERT INTO expectation (row_id, direction, amount_minor, currency) VALUES (?, 'back', 0, 'SGD')", "row"),
    ("INSERT INTO expectation (direction, amount_minor, currency) VALUES ('back', 100, 'SGD')", None),
    ("INSERT INTO set_off (payment_row_id, amount_minor, item_amount_minor, source) VALUES (?, 100, 100, 'guess')", "row"),
    ("INSERT INTO set_off (payment_row_id, amount_minor, item_amount_minor) VALUES (?, 100, 90)", "row"),
    ("INSERT INTO refund_link (refund_row_id, purchase_row_id, source) VALUES (?, ?, 'guess')", "row2"),
    ("INSERT INTO dismissal (row_id, kind) VALUES (?, 'whatever')", "row"),
    ("INSERT INTO dismissal (row_id, kind) VALUES (?, 'refund_link')", "row"),
    ("INSERT INTO expectation (row_id, direction, amount_minor, currency) VALUES (999999, 'back', 100, 'SGD')", None),
])
def test_a_record_outside_its_vocabulary_or_pointing_at_nothing_is_refused_by_the_book(conn, story, sql, args):
    params = {"row": (story.harbour,), "row2": (story.klook, story.harbour), None: ()}[args]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, params)
    conn.rollback()


def test_a_second_not_a_payback_on_one_row_is_refused_by_the_book(conn, story):
    dismiss(conn, story.jay_0412)
    with pytest.raises(sqlite3.IntegrityError):
        dismiss(conn, story.jay_0412)


def test_a_book_from_before_the_records_will_not_start_then_converts_and_starts(tmp_path, monkeypatch):
    # A book in the shape fin had before ticket 25: every table and trigger
    # but the seven records, their triggers, their indexes and row_parts.
    path = tmp_path / "ledger.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    old = sqlite3.connect(str(path))
    old.execute("DROP VIEW row_parts")
    for table in SEVEN:
        for op in ("insert", "update", "delete"):
            old.execute(f"DROP TRIGGER history_{table}_{op}")
    for table in reversed(SEVEN):
        old.execute(f"DROP TABLE {table}")
    old.execute("INSERT INTO accounts (name, short_name, type) VALUES ('Sample Bank', 'Sample-Bank', 'bank')")
    old.execute("INSERT INTO statements (account_id, statement_date) VALUES (1, '2026-10-31')")
    old.execute("INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type) "
                "VALUES (1, '2026-10-02', 'SAMPLE GROCER', 4500, 'expense')")
    old.commit()
    old.close()

    with pytest.raises(db.DatabaseNotConverted, match="change-history"):
        db.init_db()

    assert convert_all.main([str(path)]) == 0
    assert list(tmp_path.glob("ledger.db.pre-change-history-*.bak"))
    db.init_db()  # starts

    conn = db.get_connection()
    try:
        assert history.has_shape(conn)
        assert conversion.not_applied(conn) == []
        assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")} >= set(SEVEN)
        assert [tuple(r) for r in conn.execute(
            "SELECT amount_minor, expected_minor, placed_minor, own_minor FROM row_parts")] == [(4500, 0, 0, 4500)]
    finally:
        conn.close()
    # A second run changes nothing.
    assert conversion.run_step(path, conversion.chain_step("convert_change_history"))["status"] == "already-applied"


# --- the own part and its gate ---------------------------------------------------


def test_with_the_gate_off_the_own_part_is_the_bare_amount(conn, story):
    harbour_set_against(conn, story)
    assert expect_back.SWITCHED is False
    read = dict(conn.execute(f"SELECT t.id, {expect_back.own_sql('t')} FROM transactions t").fetchall())
    bare = dict(conn.execute("SELECT id, amount_minor FROM transactions").fetchall())
    assert read == bare
    assert conn.execute(expect_back.let_go_sql()).fetchall() == []


def test_row_parts_gives_each_row_its_expected_placed_and_own_part(conn, story):
    harbour_set_against(conn, story)
    shown = expect_back.parts(conn, [story.harbour, story.klook, story.jay_0412, story.kim_7781, story.golden])
    assert shown[story.harbour] == {"amount_minor": 30000, "expected_minor": 20000, "placed_minor": 0, "own_minor": 10000}
    assert shown[story.klook] == {"amount_minor": 18000, "expected_minor": 18000, "placed_minor": 0, "own_minor": 0}
    assert shown[story.jay_0412] == {"amount_minor": -12000, "expected_minor": 0, "placed_minor": -12000, "own_minor": 0}
    assert shown[story.kim_7781] == {"amount_minor": -7000, "expected_minor": 0, "placed_minor": -7000, "own_minor": 0}
    assert shown[story.golden] == {"amount_minor": 9600, "expected_minor": 0, "placed_minor": 0, "own_minor": 9600}
    # Lena's S$ 60.00 placing S$ 40.00 keeps S$ 20.00 of its own (case 13).
    airbnb = mark(conn, story.airbnb, 4000, names=(story.people["Lena"],))
    set_off(conn, story.lena_2210, airbnb, 4000)
    assert expect_back.parts(conn, [story.lena_2210])[story.lena_2210]["own_minor"] == -2000
    # A To pay back item on money in carries the row's sign (case 18).
    parents = row(conn, story.book, story.book.savings, "2026-03-01", "FROM PARENTS", -500000, flow="income")
    mark(conn, parents, 500000, names=(story.people["Parents"],))
    assert expect_back.parts(conn, [parents])[parents] == {
        "amount_minor": -500000, "expected_minor": -500000, "placed_minor": 0, "own_minor": 0}


def test_switched_on_the_own_part_and_the_let_go_terms_are_read(conn, story, monkeypatch):
    held = harbour_set_against(conn, story)
    let_go(conn, held["harbour"], "gift", "2026-10-19", to_whom_id=story.people["Kim"])
    monkeypatch.setattr(expect_back, "SWITCHED", True)

    read = dict(conn.execute(f"SELECT t.id, {expect_back.own_sql()} FROM transactions t").fetchall())
    assert (read[story.harbour], read[story.klook], read[story.jay_0412], read[story.golden]) == (10000, 0, 0, 9600)
    [term] = [dict(r) for r in conn.execute(expect_back.let_go_sql())]
    assert (term["day"], term["book"], term["flow_type"], term["amount_minor"], term["currency"]) == (
        "2026-10-19", "Household", "expense", 2000, "SGD")
    assert term["type_id"] == type_id(conn, "Gifts & Donations")
    assert term["account_id"] == story.book.rewards_card


def test_own_sql_takes_only_a_known_alias():
    with pytest.raises(ValueError):
        expect_back.own_sql("t; DROP TABLE transactions")


# --- eligibility and the machine, through the guarded writes ---------------------


def refused(conn, words, write, *args, **kwargs):
    """The write is refused with exactly these words and writes nothing."""
    before = snapshot(conn)
    with pytest.raises(expect_back.Refused) as raised:
        write(conn, *args, **kwargs)
    conn.rollback()
    assert str(raised.value) == words
    assert snapshot(conn) == before


def test_a_mark_writes_one_expectation_and_its_names(conn, story):
    jay, kim = story.people["Jay"], story.people["Kim"]
    expectation_id = expect_back.insert_expectation(conn, story.harbour, "back", 20000, names=[jay, kim])
    conn.commit()
    assert expect_back.state(conn, expectation_id) == "open"
    assert expect_back.open_minor(conn, expectation_id) == 20000
    assert {r[0] for r in conn.execute("SELECT person_id FROM expectation_name")} == {jay, kim}
    assert conn.execute("SELECT currency FROM expectation").fetchone()[0] == "SGD"
    # A rupee row's expectation is in rupees (case 9a).
    swiggy = expect_back.insert_expectation(conn, story.swiggy, "back", 180000, names=[jay])
    assert conn.execute("SELECT currency FROM expectation WHERE id = ?", (swiggy,)).fetchone()[0] == "INR"


@pytest.mark.parametrize("rowname, direction, amount, words", [
    ("harbour", "back", 35000, "more than the row's S$ 300.00"),                    # case 3
    ("harbour", "back", 0, "the amount must be more than nothing"),                 # case 4
    ("jay_0412", "back", 12000, "money in can only be expected to pay back"),       # case 5
    ("harbour", "pay", 1000, "money out can only be expected back"),
    ("card_payoff", "back", 1000, "a move between your own accounts"),              # case 5
    ("company_row", "back", 1000,
     "only Household spending can be expected back: this row is the company's cost"),  # case 9b
    ("loan_row", "back", 1000, "this move already counts on that account's line"),  # case 9b
])
def test_a_forbidden_birth_is_refused_in_the_machines_words(conn, story, rowname, direction, amount, words):
    refused(conn, words, expect_back.insert_expectation, getattr(story, rowname), direction, amount)


def test_a_second_mark_on_a_row_is_refused(conn, story):
    expect_back.insert_expectation(conn, story.harbour, "back", 20000)
    conn.commit()
    refused(conn, "this row already has an expectation: edit that one",
            expect_back.insert_expectation, story.harbour, "back", 1000)


def test_a_person_movement_and_money_in_take_their_directions(conn, story):
    ravi, parents = story.people["Ravi"], story.people["Parents"]
    loan = row(conn, story.book, story.book.savings, "2026-06-03", "TRANSFER TO RAVI", 200000,
               flow="movement", other_side=ravi)
    from_parents = row(conn, story.book, story.book.savings, "2026-03-01", "FROM PARENTS", -500000,
                       flow="movement", other_side=parents)
    assert expect_back.refusal_to_mark(conn, loan, "back") is None
    assert expect_back.refusal_to_mark(conn, from_parents, "pay") is None
    assert expect_back.refusal_to_mark(conn, story.jay_0412, "pay") is None


def test_the_open_rest_moves_through_the_machine(conn, story):
    held = harbour_set_against(conn, story)
    harbour, klook = held["harbour"], held["klook"]
    assert (expect_back.state(conn, harbour), expect_back.open_minor(conn, harbour)) == ("partly_back", 2000)
    assert expect_back.state(conn, klook) == "partly_back"
    assert expect_back.payment_left_minor(conn, story.kim_7781) == 0

    # Edit down, not below what is back (case 7).
    expect_back.edit_amount(conn, harbour, 19000)
    conn.commit()
    assert expect_back.open_minor(conn, harbour) == 1000
    refused(conn, "S$ 180.00 has already come back", expect_back.edit_amount, harbour, 15000)

    # Placing more than is open (case 15), then all of it: closed.
    payment = row(conn, story.book, story.book.savings, "2026-10-20", "PAYNOW FROM KIM L 7790", -3000, flow="review")
    refused(conn, "HARBOUR GRILL has S$ 10.00 open", expect_back.insert_set_off, payment, harbour, 3000)
    expect_back.insert_set_off(conn, payment, harbour, 1000)
    conn.commit()
    assert expect_back.state(conn, harbour) == "closed"
    refused(conn, "nothing open on HARBOUR GRILL", expect_back.insert_set_off, payment, harbour, 500)  # case 16
    refused(conn, "nothing open on HARBOUR GRILL", expect_back.insert_let_go, harbour, "gift",
            book="Household", type_id=type_id(conn, "Gifts & Donations"), counts_on="2026-10-19")  # case 26a
    refused(conn, "Can't undo the mark yet: Jay's S$ 120, Kim's S$ 60 and Kim's S$ 10 are set against it. "
                  "Unlink those first.", expect_back.delete_expectation, harbour)  # case 21
    # Raising a closed item opens it again (case 26c).
    expect_back.edit_amount(conn, harbour, 20000)
    conn.commit()
    assert (expect_back.state(conn, harbour), expect_back.open_minor(conn, harbour)) == ("partly_back", 1000)

    # Let go: the whole open rest, and then nothing else moves until it is undone (cases 24, 26, 26b, 26c).
    gift = type_id(conn, "Gifts & Donations")
    expect_back.insert_let_go(conn, harbour, "gift", book="Household", type_id=gift, counts_on="2026-10-19",
                              to_whom_id=story.people["Kim"])
    conn.commit()
    assert conn.execute("SELECT amount_minor FROM let_go").fetchone()[0] == 1000
    assert (expect_back.state(conn, harbour), expect_back.open_minor(conn, harbour)) == ("let_go", 0)
    since = "let go since; undo the let-go first"
    refused(conn, since, expect_back.insert_set_off, payment, harbour, 500)
    refused(conn, since, expect_back.delete_set_off, held["jay_on_harbour"])
    refused(conn, since, expect_back.edit_amount, harbour, 25000)
    refused(conn, since, expect_back.delete_expectation, harbour)
    expect_back.delete_let_go(conn, harbour)
    conn.commit()
    assert (expect_back.state(conn, harbour), expect_back.open_minor(conn, harbour)) == ("partly_back", 1000)

    # Unlink, then undo the mark once nothing is set against it (cases 22, 23).
    for set_off_id in [r[0] for r in conn.execute("SELECT id FROM set_off WHERE expectation_id = ?", (harbour,))]:
        expect_back.delete_set_off(conn, set_off_id)
    conn.commit()
    assert expect_back.state(conn, harbour) == "open"
    expect_back.delete_expectation(conn, harbour)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM expectation WHERE id = ?", (harbour,)).fetchone()[0] == 0


def test_a_pay_item_is_let_go_only_as_a_gift_to_me(conn, story):
    from_parents = row(conn, story.book, story.book.savings, "2026-03-01", "FROM PARENTS", -500000,
                       flow="movement", other_side=story.people["Parents"])
    owed = expect_back.insert_expectation(conn, from_parents, "pay", 500000)
    conn.commit()
    refused(conn, "money in was never spent: let it go only as a gift to me", expect_back.insert_let_go,
            owed, "mine", book="Household", type_id=type_id(conn, "Dining"), counts_on="2026-10-19")


def test_a_payment_is_money_the_other_way_and_never_more_than_it_has_left(conn, story):
    held = harbour_set_against(conn, story)
    refused(conn, "a payment for HARBOUR GRILL is money in", expect_back.insert_set_off,
            story.golden, held["harbour"], 1000)
    refused(conn, "S$ 20 is more than the S$ 0 left on this payment", expect_back.insert_set_off,
            story.jay_0412, held["harbour"], 2000)
    swiggy = expect_back.insert_expectation(conn, story.swiggy, "back", 180000)
    conn.commit()
    refused(conn, "SWIGGY · Mumbai is in INR: give the rate and both amounts", expect_back.insert_set_off,
            story.lena_2210, swiggy, 1000)


def test_a_waiting_set_off_waits_and_then_counts_against_its_row(conn, story):
    # Case 17e: Lena's S$ 100.00 on 30 Sep waits; the row arrives on 2 Oct.
    lena = story.people["Lena"]
    paid = row(conn, story.book, story.book.savings, "2026-09-30", "PAYNOW FROM LENA 2210", -10000, flow="review")
    waiting = expect_back.insert_set_off(conn, paid, None, 10000, person_id=lena, note="for the BBQ")
    conn.commit()
    assert expect_back.set_off_state(conn, waiting) == "waiting"
    assert expect_back.payment_left_minor(conn, paid) == 0
    assert [(w["id"], w["amount_minor"]) for w in expect_back.open_as_at(conn, "2026-09-30")["waiting"]] == [
        (waiting, 10000)]

    bbq = row(conn, story.book, story.book.rewards_card, "2026-10-02", "COLD STORAGE", 26000, type_name="Groceries")
    item = mark(conn, bbq, 10000, names=(lena,))
    conn.execute("UPDATE set_off SET expectation_id = ? WHERE id = ?", (item, waiting))
    conn.commit()
    assert expect_back.set_off_state(conn, waiting) == "placed"

    september = expect_back.open_as_at(conn, "2026-09-30")
    assert [w["amount_minor"] for w in september["waiting"]] == [10000]
    assert item not in [x["id"] for x in september["expectations"]]
    october_end = expect_back.open_as_at(conn, "2026-10-31")
    assert october_end["waiting"] == []
    [shown] = [x for x in october_end["expectations"] if x["id"] == item]
    assert (shown["set_off_minor"], shown["open_minor"], shown["day"]) == (10000, 0, "2026-10-02")
    assert expect_back.state(conn, item) == "closed"


def test_open_as_at_counts_set_offs_and_let_gos_by_their_own_days(conn, story):
    held = harbour_set_against(conn, story)
    sam = opening(conn, story.people["Sam"], "2026-01-01", 80000)
    let_go(conn, held["harbour"], "gift", "2026-11-03")

    def open_on(day):
        return {x["id"]: x["open_minor"] for x in expect_back.open_as_at(conn, day)["expectations"]}

    assert open_on("2026-09-30") == {sam: 80000, held["klook"]: 18000}   # HARBOUR's row is 2 Oct
    assert open_on("2026-10-09")[held["harbour"]] == 20000              # Jay paid on 10 Oct
    assert open_on("2026-10-31")[held["harbour"]] == 2000               # and Kim on 15 Oct
    assert open_on("2026-11-30")[held["harbour"]] == 0                  # let go on 3 Nov
    assert [x["id"] for x in expect_back.open_as_at(conn, "2026-10-31")["expectations"]] == [
        sam, held["klook"], held["harbour"]]                            # oldest first


# --- the undo guard, through Changes ---------------------------------------------


def refused_undo(client, conn, entry_id, words):
    before = snapshot(conn)
    resp = undo(client, entry_id)
    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json()["error"] == words
    assert snapshot(conn) == before  # rolled back, and no entry written


def test_undoing_the_mark_from_changes_is_refused_while_money_is_set_against_it(client, conn, story):
    # Case 21 (Changes half): Jay's S$ 120 and Kim's S$ 60 on HARBOUR GRILL.
    jay, kim = story.people["Jay"], story.people["Kim"]
    harbour = mark(conn, story.harbour, 20000, names=(jay, kim))
    mark_entry = last_entry(conn)
    set_off(conn, story.jay_0412, harbour, 12000)
    set_off(conn, story.kim_7781, harbour, 6000)
    refused_undo(client, conn, mark_entry,
                 "Can't undo the mark yet: Jay's S$ 120 and Kim's S$ 60 are set against it. Unlink those first.")


def test_undoing_the_mark_of_a_let_go_item_is_refused(client, conn, story):
    held = harbour_set_against(conn, story)
    let_go(conn, held["harbour"], "gift", "2026-10-19")
    refused_undo(client, conn, held["mark_entry"], "let go since; undo the let-go first")


def test_undoing_an_import_a_set_off_rests_on_is_refused(client, conn):
    # Case 53a (import half).
    book = check_book(conn)
    harbour_row = row(conn, book, book.rewards_card, "2026-10-02", "HARBOUR GRILL", 30000, type_name="Dining")
    bring_in(client, "Sample Bank 0412", [("2026-10-10", "PAYNOW FROM JAY TAN 0412", -12000)],
             opening=0, closing=12000, closing_date="2026-10-31")
    imported = last_entry(conn)
    paid = conn.execute("SELECT id FROM transactions WHERE description = 'PAYNOW FROM JAY TAN 0412'").fetchone()[0]
    harbour = mark(conn, harbour_row, 20000, names=(book.people["Jay"], book.people["Kim"]))
    held = set_off(conn, paid, harbour, 12000)

    refused_undo(client, conn, imported,
                 "Can't undo this import: PAYNOW FROM JAY TAN 0412 has S$ 120 set against HARBOUR GRILL. "
                 "Unlink it first.")

    unlink(conn, held)
    assert undo(client, imported).status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE id = ?", (paid,)).fetchone()[0] == 0


def test_undoing_an_import_a_mark_rests_on_is_refused(client, conn):
    bring_in(client, "Sample Card 0300", [("2026-10-02", "HARBOUR GRILL", 30000)],
             kind="card", opening=0, closing=-30000, closing_date="2026-10-31")
    imported = last_entry(conn)
    harbour_row = conn.execute("SELECT id FROM transactions").fetchone()[0]
    mark(conn, harbour_row, 20000)
    refused_undo(client, conn, imported, "Can't undo this import: HARBOUR GRILL is expected back. Undo the mark first.")


def test_undoing_a_set_off_after_its_item_was_let_go_is_refused(client, conn, story):
    # Case 54: history.blocker lets it through; the invariant check does not.
    held = harbour_set_against(conn, story)
    let_go(conn, held["harbour"], "gift", "2026-10-19", to_whom_id=story.people["Kim"])
    assert history.blocker(conn, held["jay_entry"]) is None
    refused_undo(client, conn, held["jay_entry"], "let go since; undo the let-go first")


def test_undoing_an_unlink_after_the_payment_was_placed_elsewhere_is_refused(client, conn, story):
    held = harbour_set_against(conn, story)
    unlink(conn, held["kim_on_harbour"])
    unlinked = last_entry(conn)
    golden = mark(conn, story.golden, 6400, names=(story.people["Jay"], story.people["Lena"]))
    set_off(conn, story.kim_7781, golden, 6000)
    assert history.blocker(conn, unlinked) is None
    refused_undo(client, conn, unlinked, "Kim's payment would place S$ 130 of S$ 70")


def test_undoing_a_raise_after_more_was_placed_is_refused(client, conn, story):
    held = harbour_set_against(conn, story)
    edit(conn, held["harbour"], 19000)
    edit(conn, held["harbour"], 20000)
    raised = last_entry(conn)
    payment = row(conn, story.book, story.book.savings, "2026-10-20", "PAYNOW FROM KIM L 7790", -2000, flow="review")
    set_off(conn, payment, held["harbour"], 2000)
    refused_undo(client, conn, raised, "S$ 200.00 would be set against S$ 190.00 expected")


def test_an_undo_that_leaves_the_records_whole_goes_through_and_back(client, conn, story):
    held = harbour_set_against(conn, story)
    assert undo(client, held["jay_entry"]).status_code == 200
    assert expect_back.open_minor(conn, held["harbour"]) == 14000
    assert undo(client, last_entry(conn)).status_code == 200  # relinks
    assert expect_back.open_minor(conn, held["harbour"]) == 2000
    # A wording, a dismissal and a refund link undo like any change.
    for write in (lambda: wording(conn, "JAY TAN", story.people["Jay"]),
                  lambda: dismiss(conn, story.lena_2210),
                  lambda: refund_link(conn, story.lena_2210, story.airbnb, "hand")):
        write()
        assert undo(client, last_entry(conn)).status_code == 200
    assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("sender_wording", "dismissal", "refund_link")] == [0, 0, 0]


# --- the seams -------------------------------------------------------------------


def test_someone_new_is_made_once_and_reused_by_name(conn):
    # Case 8: "+ Someone new", "Mei"; an existing "MEI" is reused.
    mei, made = person.find_or_create_person(conn, "Mei")
    assert made
    again, made_again = person.find_or_create_person(conn, "  MEI ")
    assert (again, made_again) == (mei, False)
    assert tuple(conn.execute("SELECT type, owner FROM accounts WHERE id = ?", (mei,)).fetchone()) == (
        "person", "Household")
    with pytest.raises(ValueError):
        person.find_or_create_person(conn, "   ")


def test_the_review_route_finds_or_creates_its_person_through_person_py(client, conn, story):
    loan = row(conn, story.book, story.book.savings, "2026-06-03", "TRANSFER TO MEI", 200000, flow="review")
    resp = client.post(f"/api/review/{loan}/label", json={"choice": "loan_to_person", "person": "Mei"})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["created_account"] is True
    second = row(conn, story.book, story.book.savings, "2026-06-04", "TRANSFER TO MEI", 1000, flow="review")
    resp = client.post(f"/api/review/{second}/label", json={"choice": "loan_to_person", "person": "mei"})
    assert resp.get_json()["created_account"] is False
    assert resp.get_json()["other_side_id"] == person.find_person(conn, "MEI")


def test_the_import_links_refunds_once_inside_its_change(client, conn, monkeypatch):
    seen = []

    def link_on_import(c):
        seen.append((c.in_transaction,
                     c.execute("SELECT COUNT(*) FROM change_entries WHERE open = 1").fetchone()[0]))
        return []

    monkeypatch.setattr(refund_links, "link_on_import", link_on_import)
    bring_in(client, "Sample Bank 0002", [("2026-08-03", "SAMPLE GROCER", 14000)], opening=100000, closing=86000)
    assert seen == [(True, 1)]


def test_refund_links_links_nothing_yet(conn, story):
    assert refund_links.link_on_import(conn) == []


def test_the_expect_back_blueprint_is_registered_with_its_words():
    assert fin_app.app.blueprints["expect"] is expect_routes.bp
    for endpoint, words in expect_routes.SUMMARIES.items():
        assert fin_app._CHANGE_SUMMARIES[endpoint] == words
    assert all(hasattr(m, "register") and isinstance(m.SUMMARIES, dict) for m in expect_routes.MODULES)


def test_amounts_are_worded_in_their_currency():
    assert (expect_back.full(30000), expect_back.short(12000), expect_back.short(12050)) == (
        "S$ 300.00", "S$ 120", "S$ 120.50")
    assert expect_back.full(10000000, "INR") == "₹ 1,00,000.00"
    assert expect_back.short(180000, "INR") == "₹ 1,800"
