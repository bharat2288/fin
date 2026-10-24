"""The single mark retired (personal-ledger 51; spec 24, the contract step;
spec seams 1 and 2).

Nothing reads `cat_source` any more: a row's book and its type each carry
their own source. The contract step drops the column from an existing book
through the conversion runner, with its history triggers, behind a backup,
in one transaction. It keeps the row count, every book, type and source, and
each account's total. An empty field's source read the row's old mark while
the mark was there (a row named by hand on the review list, with no book and
no type, read as chosen), so the step writes that reading into the field
before the mark goes, and every field reads the same source after as before.

The label-source step (personal-ledger 48) and this one each record that
they ran (conversion.record_applied) and read the record: what the operator
does afterwards (a row written with no source, an import) never reads as a
conversion still to run, so it never keeps fin from starting. A book never
converted still reads as pending.

Every merchant, figure and name is invented. Each book is built under
tmp_path from the oldest frozen schema and taken through the chain, the way
the operator's book reached today's shape; nothing opens a real book.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

import app as fin_app
import conversion
import convert_all
import convert_label_source
import db
import history
import retire_single_mark
from test_balance_sheet import bring_in
from test_book_and_type_switch import merchant, statement  # noqa: F401 (statement is a fixture)
from test_label_source_conversion import OLD_SCHEMA, Book, chain_until, open_book, seed_case_32

NAME = retire_single_mark.NAME
MARK = "cat_source"
ROOT = Path(__file__).resolve().parent.parent


# --- the books ------------------------------------------------------------------


@pytest.fixture
def todays(tmp_path: Path) -> Path:
    """A book in today's shape, before the label-source step: every earlier
    step applied, then case 32's rows written with their one mark."""
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA.read_text())
    conn.close()
    chain_until(path, convert_label_source.NAME)
    conn = open_book(path)
    try:
        seed_case_32(conn)
    finally:
        conn.close()
    return path


def expand_rows(path: Path) -> dict:
    """Rows the expand step left reading their old mark for an empty field
    (or for a field no switched writer gave a source), written after the
    label-source step. Returns their ids."""
    conn = open_book(path)
    try:
        b = Book(conn)
        stall = b.merchant("Sample Stall", "Household", "Dining")
        b.rule("SAMPLE STALL", stall)
        ids = {
            # Named by hand on the review list: a movement, no book, no type.
            "named": b.row("SAMPLE STALL TRANSFER", 4000, book=None, type_name=None,
                           service_id=None, source="manual", flow="movement"),
            # A hand-set row whose book is empty (it reads as Household).
            "empty_book": b.row("SAMPLE STALL 11", 700, book=None, type_name="Dining",
                                service_id=stall, source="manual"),
            # A writer not yet switched: a type and its mark, no source.
            "unsourced": b.row("SAMPLE STALL 12", 800, book="Moom", type_name="Dining",
                               service_id=stall, source="manual"),
            # An empty row the rules gave nothing: it stays without a source.
            "empty": b.row("NOTHING HERE", 100, book=None, type_name=None, service_id=None, source="auto"),
        }
        conn.execute("UPDATE transactions SET flow_type_manual = 1 WHERE id = ?", (ids["named"],))
        for i in ids.values():
            conn.execute("UPDATE transactions SET book_source = NULL, type_source = NULL WHERE id = ?", (i,))
        conn.commit()
        return ids
    finally:
        conn.close()


@pytest.fixture
def expanded(todays: Path) -> Path:
    """The book as personal-ledger 48 left it: sources filled, the mark kept."""
    conversion.run_step(todays, convert_label_source.STEP)
    return todays


def columns(path: Path) -> list[str]:
    conn = open_book(path)
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
    finally:
        conn.close()


def dump(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def backups(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.glob(f"{path.name}.pre-{NAME}-*.bak"))


# The source an empty one read while the mark was there (label_source in the
# expand step); `merchant` for any other mark, and for none.
EXPAND_READING = {"manual": "chosen", "derived": "derived", "rule_override": "rule", "fallback": "wording"}


def readings(path: Path) -> dict:
    """Each row's (book, type, book source, type source), each source as fin
    reads it: its own, or what an empty one reads as (the old mark while the
    book still has it, else from the merchant)."""
    conn = open_book(path)
    try:
        has_mark = MARK in {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
        out = {}
        for r in conn.execute("SELECT * FROM transactions"):
            fallback = EXPAND_READING.get(r[MARK] or "auto", "merchant") if has_mark else "merchant"
            out[r["id"]] = (r["book"], r["type_id"], r["book_source"] or fallback, r["type_source"] or fallback)
        return out
    finally:
        conn.close()


def account_totals(path: Path) -> dict:
    conn = open_book(path)
    try:
        return dict(conn.execute(
            "SELECT s.account_id, SUM(t.amount_minor) FROM transactions t"
            " JOIN statements s ON s.id = t.statement_id GROUP BY s.account_id").fetchall())
    finally:
        conn.close()


def recorded(path: Path, step: str) -> bool:
    conn = open_book(path)
    try:
        return conversion.recorded(conn, step)
    finally:
        conn.close()


def not_applied(path: Path) -> list[str]:
    conn = open_book(path)
    try:
        return conversion.not_applied(conn)
    finally:
        conn.close()


# --- the chain ---------------------------------------------------------------------


def test_the_step_runs_right_after_label_source():
    names = [name for name, _ in conversion.CHAIN]
    assert names[names.index(NAME) - 1:names.index(NAME) + 1] == [convert_label_source.NAME, NAME]
    assert conversion.chain_step(dict(conversion.CHAIN)[NAME]) is retire_single_mark.STEP


# --- the drop ----------------------------------------------------------------------


def test_the_mark_is_dropped_with_its_history_triggers(expanded):
    report = conversion.run_step(expanded, retire_single_mark.STEP)

    assert report["status"] == "applied"
    assert MARK not in columns(expanded)
    conn = open_book(expanded)
    try:
        wanted = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
        for op in ("insert", "update", "delete"):
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                               (f"history_transactions_{op}",)).fetchone()[0]
            assert MARK not in sql, op
            for column in wanted:
                assert f"'{column}', " in sql, (op, column)
    finally:
        conn.close()


def test_row_count_every_book_type_and_source_and_each_accounts_total_are_kept(expanded):
    expand_rows(expanded)
    before, totals = readings(expanded), account_totals(expanded)

    conversion.run_step(expanded, retire_single_mark.STEP)

    assert readings(expanded) == before
    assert account_totals(expanded) == totals


def test_an_empty_field_keeps_the_source_the_old_mark_gave_it(expanded):
    """The mark read chosen for a field a hand-set row left empty; with the
    mark gone, the field says so itself. A field the rules gave nothing still
    has no source, and a valued field no writer gave a source gets the one it
    read."""
    ids = expand_rows(expanded)

    conversion.run_step(expanded, retire_single_mark.STEP)

    conn = open_book(expanded)
    try:
        got = {i: tuple(conn.execute("SELECT book_source, type_source FROM transactions WHERE id = ?",
                                     (i,)).fetchone()) for i in ids.values()}
    finally:
        conn.close()
    assert got[ids["named"]] == ("chosen", "chosen")
    assert got[ids["empty_book"]] == ("chosen", "chosen")
    assert got[ids["unsourced"]] == ("chosen", "chosen")
    assert got[ids["empty"]] == (None, None)


def test_a_row_named_by_hand_is_not_the_rules_to_take_after_the_drop(expanded, monkeypatch):
    """Seam 1: a row named by hand with no book or type, whose wording a rule
    knows, was never the rules' to take (its mark read chosen). After the
    drop, the rows that differ from their rule, applied, leave it as it was."""
    ids = expand_rows(expanded)
    assert convert_all.main([str(expanded)]) == 0
    monkeypatch.setattr(db, "DB_PATH", expanded)
    db.invalidate_rules_cache()
    db.init_db()
    fin_app.app.config["TESTING"] = True
    with fin_app.app.test_client() as client:
        drift = client.get("/api/rules/drift").get_json()
        applied = client.post("/api/rules/drift", json={"past": "apply", "token": drift["token"]})
    db.invalidate_rules_cache()
    assert applied.status_code == 200, applied.get_json()
    conn = open_book(expanded)
    try:
        after = conn.execute("SELECT service_id, book, type_id, book_source, type_source FROM transactions"
                             " WHERE id = ?", (ids["named"],)).fetchone()
    finally:
        conn.close()
    assert tuple(after) == (None, None, None, "chosen", "chosen")


def test_a_second_run_changes_nothing(expanded):
    first = conversion.run_step(expanded, retire_single_mark.STEP)
    after_first = dump(expanded)
    second = conversion.run_step(expanded, retire_single_mark.STEP)

    assert (first["status"], second["status"]) == ("applied", "already-applied")
    assert dump(expanded) == after_first
    assert len(backups(expanded)) == 1


def test_without_its_backup_it_refuses_and_changes_nothing(expanded):
    was = dump(expanded)

    with pytest.raises(conversion.ConversionRefused, match="backup"):
        conversion.run_step(expanded, retire_single_mark.STEP, backup=lambda source, target: None)

    assert dump(expanded) == was
    assert MARK in columns(expanded)


def test_it_will_not_run_before_the_label_source_step(todays):
    was = dump(todays)

    with pytest.raises(conversion.ConversionRefused, match=convert_label_source.NAME):
        conversion.run_step(todays, retire_single_mark.STEP)

    assert dump(todays) == was
    assert not backups(todays)


def test_a_book_through_every_step_has_the_columns_of_a_new_one(todays, conn):
    assert convert_all.main([str(todays)]) == 0
    new = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
    assert set(columns(todays)) == new
    assert MARK not in new


# --- the whole chain: a hand-set row with an empty field (review fix) ------------------


@pytest.fixture
def hand_set_empty(todays: Path) -> dict:
    """Hand-set rows with an empty field, written with their one mark on the
    book in today's shape: the label-source step and this one, run by the one
    command, give them their sources. Returns their ids and the merchants'."""
    conn = open_book(todays)
    try:
        b = Book(conn)
        stall = b.merchant("Sample Stall", "Household", "Dining")
        bare = b.merchant("Sample Bare Stall", None, "Dining")
        moom = b.merchant("Sample Office Stall", "Moom", "Dining")
        grocer = b.merchant("Sample Grocer", "Household", None)
        ids = {
            "stall": stall, "grocer": grocer,
            # A spending row's empty book reads as Household: equal to the
            # merchant's (or to the book a merchant with none offers), checked.
            "null_book": b.row("SAMPLE STALL 21", 700, book=None, type_name="Dining",
                               service_id=stall, source="manual"),
            "null_book_bare": b.row("SAMPLE BARE STALL 22", 700, book=None, type_name="Dining",
                                    service_id=bare, source="manual"),
            # Different from the merchant's: chosen.
            "null_book_moom": b.row("SAMPLE OFFICE STALL 23", 700, book=None, type_name="Dining",
                                    service_id=moom, source="manual"),
            # A spending row never typed: it waits in the Queue for a type.
            "untyped": b.row("SAMPLE GROCER 24", 1500, book="Household", type_name=None,
                             service_id=grocer, source="manual"),
            "untyped_no_merchant": b.row("SOMEWHERE HAND SET 25", 900, book="Household", type_name=None,
                                         service_id=None, source="manual"),
            # Named by hand on the review list: a movement, no book, no type.
            "movement": b.row("SAMPLE STALL TRANSFER 26", 4000, book=None, type_name=None,
                              service_id=None, source="manual", flow="movement"),
        }
        conn.execute("UPDATE transactions SET flow_type_manual = 1 WHERE id = ?", (ids["movement"],))
        conn.commit()
        return ids
    finally:
        conn.close()


def sources_of(path: Path, ids) -> dict:
    conn = open_book(path)
    try:
        return {i: tuple(conn.execute("SELECT book_source, type_source FROM transactions WHERE id = ?",
                                      (i,)).fetchone()) for i in ids}
    finally:
        conn.close()


def test_a_hand_set_spending_rows_empty_book_is_compared_as_household(todays, hand_set_empty):
    """Case 32's rule for the book a NULL reads as: equal to the merchant's
    book (or to the one a merchant with none offers) is checked, so a later
    fix still reaches it; different is chosen."""
    ids = hand_set_empty

    assert convert_all.main([str(todays)]) == 0

    got = sources_of(todays, [ids["null_book"], ids["null_book_bare"], ids["null_book_moom"]])
    assert got[ids["null_book"]] == ("checked", "checked")
    assert got[ids["null_book_bare"]] == ("checked", "checked")
    assert got[ids["null_book_moom"]] == ("chosen", "checked")


def test_a_hand_set_spending_row_never_typed_gets_no_type_source(todays, hand_set_empty):
    """A source is empty when the field is: a spending row waiting for a type
    is given none, so it stays reachable."""
    ids = hand_set_empty

    assert convert_all.main([str(todays)]) == 0

    got = sources_of(todays, [ids["untyped"], ids["untyped_no_merchant"]])
    assert got[ids["untyped"]] == ("checked", None)
    assert got[ids["untyped_no_merchant"]] == ("chosen", None)


def test_the_step_refuses_a_source_written_onto_a_waiting_rows_empty_type(todays, hand_set_empty, monkeypatch):
    """The invariant, stated: every field reads as before, but a spending
    row's empty type, which holds the source it held. A step that wrote the
    mark's chosen there is rolled back."""
    ids = hand_set_empty
    conversion.run_step(todays, convert_label_source.STEP)
    was = dump(todays)
    planned = retire_single_mark.planned

    def writes_chosen_there(conn):
        return [*planned(conn), ("checked", "chosen", ids["untyped"])]

    monkeypatch.setattr(retire_single_mark, "planned", writes_chosen_there)
    with pytest.raises(conversion.ConversionFailed, match="waiting for a type"):
        conversion.run_step(todays, retire_single_mark.STEP)
    assert dump(todays) == was


def test_a_hand_set_row_never_typed_is_reached_by_its_merchants_new_type(todays, hand_set_empty, monkeypatch):
    """Seam 1 after the whole chain: the merchant's new type, applied to its
    past rows, types the row that waited in the Queue for one."""
    ids = hand_set_empty
    assert convert_all.main([str(todays)]) == 0
    monkeypatch.setattr(db, "DB_PATH", todays)
    db.invalidate_rules_cache()
    db.init_db()
    conn = open_book(todays)
    groceries = conn.execute("SELECT id FROM types WHERE name = 'Groceries'").fetchone()[0]
    conn.close()
    fin_app.app.config["TESTING"] = True
    with fin_app.app.test_client() as client:
        waiting = client.get("/api/transactions?types=__untyped__&per_page=500").get_json()["transactions"]
        asked = client.put(f"/api/services/{ids['grocer']}", json={"type_id": groceries})
        preview = asked.get_json()["preview"]
        applied = client.put(f"/api/services/{ids['grocer']}",
                             json={"type_id": groceries, "past": "apply", "token": preview["token"]})
    db.invalidate_rules_cache()

    assert ids["untyped"] in {t["id"] for t in waiting}
    assert asked.status_code == 409 and preview["count"] == 1
    assert applied.status_code == 200, applied.get_json()
    conn = open_book(todays)
    try:
        after = conn.execute("SELECT type_id, type_source FROM transactions WHERE id = ?",
                             (ids["untyped"],)).fetchone()
    finally:
        conn.close()
    assert tuple(after) == (groceries, "merchant")


def test_a_movement_named_by_hand_keeps_chosen_on_its_empty_fields(todays, hand_set_empty):
    """Personal-ledger 51's protection, through the whole chain: a movement's
    empty book and type are the operator's choice, so no later rule gives it
    a merchant, a book or a type."""
    ids = hand_set_empty

    assert convert_all.main([str(todays)]) == 0

    assert sources_of(todays, [ids["movement"]])[ids["movement"]] == ("chosen", "chosen")


# --- applied is recorded, and read from the record ------------------------------------


def test_each_step_records_that_it_ran_in_its_own_transaction(todays):
    conversion.run_step(todays, convert_label_source.STEP)
    assert recorded(todays, convert_label_source.NAME)
    assert not recorded(todays, NAME)

    conversion.run_step(todays, retire_single_mark.STEP)
    assert recorded(todays, NAME)


def test_a_step_rolled_back_records_nothing(expanded):
    with pytest.raises(conversion.ConversionRefused):
        conversion.run_step(expanded, retire_single_mark.STEP, backup=lambda source, target: None)
    assert not recorded(expanded, NAME)


def test_a_row_written_with_no_source_after_label_source_does_not_make_it_pending_again(expanded):
    """The label-source step reads its record, not the rows: a row a writer
    left without a source is not a conversion still to run."""
    expand_rows(expanded)

    assert convert_label_source.NAME not in not_applied(expanded)
    assert conversion.run_step(expanded, convert_label_source.STEP)["status"] == "already-applied"
    assert conversion.run_step(expanded, retire_single_mark.STEP)["status"] == "applied"


def test_a_never_converted_book_reads_both_steps_pending_and_fin_names_them(todays, monkeypatch):
    assert [n for n in not_applied(todays) if n in (convert_label_source.NAME, NAME)] == [
        convert_label_source.NAME, NAME]
    monkeypatch.setattr(db, "DB_PATH", todays)
    with pytest.raises(db.DatabaseNotConverted) as refused:
        db.init_db()
    assert convert_label_source.NAME in str(refused.value) and NAME in str(refused.value)


def test_fin_will_not_start_on_a_book_that_still_carries_the_mark_and_names_this_step(expanded, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", expanded)
    with pytest.raises(db.DatabaseNotConverted) as refused:
        db.init_db()
    message = str(refused.value)
    assert NAME in message and f"{convert_label_source.NAME} (" not in message


def test_ordinary_edits_after_the_conversion_never_keep_fin_from_starting(todays, monkeypatch):
    """After the one command: a row written with no source and a statement
    imported through the app. fin starts, every step reads as applied, and
    the command run again converts nothing."""
    assert convert_all.main([str(todays)]) == 0
    first_backups = sorted(p.name for p in todays.parent.glob("*.bak"))
    monkeypatch.setattr(db, "DB_PATH", todays)
    db.invalidate_rules_cache()
    db.init_db()

    conn = open_book(todays)
    statement = conn.execute("SELECT id FROM statements LIMIT 1").fetchone()[0]
    dining = conn.execute("SELECT id FROM types WHERE name = 'Dining'").fetchone()[0]
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id, flow_type)"
        " VALUES (?, '2026-09-01', 'WRITTEN WITH NO SOURCE', 450, 'Household', ?, 'expense')",
        (statement, dining))
    conn.commit()
    conn.close()
    fin_app.app.config["TESTING"] = True
    with fin_app.app.test_client() as client:
        bring_in(client, "Sample Bank 0003", [("2026-09-02", "GRAB RIDE 77", 1300),
                                               ("2026-09-03", "SOMEWHERE NEW", 900)])

    db.invalidate_rules_cache()
    db.init_db()
    db.invalidate_rules_cache()
    assert not_applied(todays) == []
    assert convert_all.main([str(todays)]) == 0
    assert sorted(p.name for p in todays.parent.glob("*.bak")) == first_backups


def test_a_new_book_reads_as_converted(conn):
    """By its shape: it never held the mark, and nothing it records says so."""
    assert not conversion.recorded(conn, NAME)
    assert retire_single_mark.STEP.is_applied(conn)
    assert convert_label_source.STEP.is_applied(conn)
    assert not {convert_label_source.NAME, NAME} & set(conversion.not_applied(conn))


# --- what fin serves after the drop ---------------------------------------------------


def test_a_change_recorded_before_the_drop_shows_no_mark_and_still_undoes(expanded, monkeypatch):
    """Seam 1: an entry recorded while the mark was there carries it in its
    rows (the triggers the change-history step made then recorded every
    column); the history shows the fields the table holds now, and Undo
    writes back only those."""
    conn = open_book(expanded)
    row = conn.execute("SELECT id, book FROM transactions WHERE description = 'GRAB RIDE A'").fetchone()
    entry = history.open_entry(conn, history.VIA_APP, history.APP_ACTOR, "a change before the drop")
    conn.execute("UPDATE transactions SET book = 'Moom', book_source = 'chosen', cat_source = 'manual'"
                 " WHERE id = ?", (row["id"],))
    conn.commit()
    history.close_entry(conn, entry)
    # As the triggers of the day recorded it: the mark beside every column.
    for column, mark in (("before", "auto"), ("after", "manual")):
        recorded = json.loads(conn.execute(
            f"SELECT {column} FROM change_rows WHERE entry_id = ?", (entry,)).fetchone()[0])
        conn.execute(f"UPDATE change_rows SET {column} = ? WHERE entry_id = ?",
                     (json.dumps({**recorded, MARK: mark}), entry))
    conn.commit()
    conn.close()

    assert convert_all.main([str(expanded)]) == 0
    monkeypatch.setattr(db, "DB_PATH", expanded)
    db.invalidate_rules_cache()
    db.init_db()
    fin_app.app.config["TESTING"] = True
    with fin_app.app.test_client() as client:
        shown = client.get(f"/api/history/{entry}").get_json()
        listed = client.get("/api/history").get_json()
        undone = client.post(f"/api/history/{entry}/undo")
        served = client.get("/api/transactions?per_page=500").get_json()["transactions"]
    db.invalidate_rules_cache()

    change = shown["changes"][0]
    assert MARK not in change["before"] and MARK not in change["after"]
    assert MARK not in json.dumps(listed)
    assert undone.status_code == 200
    back = next(t for t in served if t["id"] == row["id"])
    assert back["book"] == row["book"]
    assert MARK not in back


def test_the_transaction_list_carries_no_mark(client, conn, statement):
    conn.execute("INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type)"
                 " VALUES (?, '2026-04-10', 'SAMPLE ROW', 100, 'expense')", (statement,))
    conn.commit()
    payload = client.get("/api/transactions?per_page=5").get_json()
    assert payload["transactions"] and all(MARK not in t for t in payload["transactions"])


def test_a_movement_named_by_hand_after_the_drop_is_never_the_rules_to_take(client, conn, statement):
    """Case 7 with the mark gone: This was… chooses the book and the type,
    the empty ones too, so a rule added later for the row's wording, applied
    to past rows, does not give it a merchant, a book or a type."""
    tx = conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type)"
        " VALUES (?, '2026-04-10', 'PAYNOW TO SAMPLE PAYEE REF 9931', 12050, 'review')", (statement,)).lastrowid
    company = conn.execute("INSERT INTO accounts (name, short_name, type, owner)"
                           " VALUES ('Sample Venture', 'Sample Venture', 'company', 'Household')").lastrowid
    conn.commit()
    catch_all = merchant(conn, "Sample Catch-all", "Moom", "Shopping")

    named = client.post(f"/api/review/{tx}/label", json={"choice": "company", "account_id": company})
    added = client.post("/api/rules", json={"pattern": "SAMPLE PAYEE", "service_id": catch_all, "past": "apply"})

    assert (named.status_code, added.status_code) == (200, 200), (named.get_json(), added.get_json())
    after = conn.execute("SELECT service_id, book, type_id, book_source, type_source, flow_type"
                         " FROM transactions WHERE id = ?", (tx,)).fetchone()
    assert tuple(after) == (None, None, None, "chosen", "chosen", "movement")


# --- nothing reads or writes the mark ------------------------------------------------

# The files that still name the mark, each for a reason that outlives it:
#   convert_label_source.py — the expand step reads the mark on a book that
#                              has not been converted yet;
#   label_source.py         — the one name of the retired column: the drop
#                              step drops it, the start gate refuses a book
#                              still holding it, and the row sheet reads it
#                              in change history recorded before the
#                              label-source step.
ALLOWED = {"convert_label_source.py": None, "label_source.py": 1}


def _app_sources() -> list[Path]:
    found = [p for p in ROOT.glob("*.py")]
    found += [p for p in (ROOT / "static").rglob("*") if p.suffix in (".js", ".html", ".css")]
    found += [ROOT / "schema.sql"]
    return sorted(found)


def test_no_code_reads_or_writes_the_single_mark():
    """No module, script, page or the schema names the mark, but the two that
    reach a book from before the drop, and label_source.py only once."""
    named = {}
    for path in _app_sources():
        lines = [n for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                 if re.search(rf"\b{MARK}\b", line)]
        if lines:
            named[path.name] = lines
    unexpected = {name: lines for name, lines in named.items() if name not in ALLOWED}
    assert unexpected == {}
    assert len(named.get("label_source.py", [])) == ALLOWED["label_source.py"]
