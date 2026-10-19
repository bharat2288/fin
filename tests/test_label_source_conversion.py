"""The book source and the type source: declared once, and today's rows
converted (personal-ledger 48; spec 24, cases 32-35; spec seam 2).

A row's book and its type each say where they came from: from the merchant,
from a rule, from the wording, checked, chosen, or worked out; empty when the
field is empty. The conversion step fills the two marks from `cat_source`
through the conversion runner, comparing a hand-set field with what its
merchant or winning rule gives now. `cat_source` stays (expand).

Every merchant, figure and name is invented. Each book is built under
tmp_path from the oldest frozen schema and taken through the chain, the way
the operator's book reached today's shape; nothing opens a real book.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import book_type
import conversion
import convert_all
import convert_label_source
import db
import history

OLD_SCHEMA = Path(__file__).parent / "schema_before_book_and_type.sql"
NAME = convert_label_source.NAME


# --- the book -------------------------------------------------------------------


def chain_until(path: Path, stop_before: str) -> None:
    """Every step of the chain before `stop_before`, through the runner."""
    for name, module_name in conversion.CHAIN:
        if name == stop_before:
            return
        conversion.run_step(path, conversion.chain_step(module_name))
    raise AssertionError(f"{stop_before} is not in the chain")


def open_book(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def blank(tmp_path: Path) -> Path:
    """A book in the oldest shape, no rows."""
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA.read_text())
    conn.close()
    return path


class Book:
    """Writes the rows of a book in today's shape, as fin's writers left them."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.types = book_type.spending_type_ids(conn)
        self.card = self._account("Sample Card 0001", "card")
        self.bank = self._account("Sample Bank 0002", "bank")
        self.statements: dict = {}

    def _account(self, name: str, kind: str) -> int:
        return self.conn.execute(
            "INSERT INTO accounts (name, short_name, type, currency, owner) VALUES (?, ?, ?, 'SGD', 'Household')",
            (name, name.replace(" ", "-"), kind),
        ).lastrowid

    def merchant(self, name: str, book: str | None, type_name: str | None) -> int:
        return self.conn.execute(
            "INSERT INTO services (name, book, type_id) VALUES (?, ?, ?)",
            (name, book, self.types[type_name] if type_name else None),
        ).lastrowid

    def rule(self, pattern: str, service_id: int, *, type_name: str | None = None,
             book: str | None = None, priority: int = 0) -> int:
        return self.conn.execute(
            "INSERT INTO merchant_rules (pattern, service_id, match_type, confidence, priority,"
            " book_override, type_override_id) VALUES (?, ?, 'contains', 'auto', ?, ?, ?)",
            (pattern, service_id, priority, book, self.types[type_name] if type_name else None),
        ).lastrowid

    def row(self, description: str, amount_minor: int, *, book: str | None, type_name: str | None,
            service_id: int | None, source: str | None, day: str = "2026-05-10",
            account: int | None = None, flow: str = "expense") -> int:
        account = account or self.card
        first = day[:7] + "-01"
        key = (account, first)
        if key not in self.statements:
            self.statements[key] = self.conn.execute(
                "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, 'sample.csv')",
                key,
            ).lastrowid
        return self.conn.execute(
            "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id,"
            " service_id, cat_source, flow_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.statements[key], day, description, amount_minor, book,
             self.types[type_name] if type_name else None, service_id, source, flow),
        ).lastrowid


def seed_case_32(conn: sqlite3.Connection) -> dict:
    """Case 32 on one book. Commits. Returns the row ids by kind."""
    b = Book(conn)
    grab = b.merchant("Grab", "Household", "Transport")
    shop = b.merchant("Sample Shop", "Household", "Shopping")
    b.rule("GRAB", grab)
    b.rule("SAMPLE SHOP", shop)
    # A rule that overrides the type only, ahead of the merchant's own rule.
    b.rule("SAMPLE SHOP PRIME", shop, type_name="Subscriptions", priority=10)
    ids = {
        # Grab's 40 hand-set rows: 34 equal to Grab's, 6 moved to Moom.
        "grab_same": [b.row(f"GRAB RIDE {i:02d}", 1200 + i, book="Household", type_name="Transport",
                            service_id=grab, source="manual") for i in range(34)],
        "grab_moom": [b.row(f"GRAB RIDE M{i}", 2400 + i, book="Moom", type_name="Transport",
                            service_id=grab, source="manual") for i in range(6)],
        "default": b.row("SAMPLE SHOP 0042", 5500, book="Household", type_name="Shopping",
                         service_id=shop, source="service_default"),
        "auto": b.row("GRAB RIDE A", 900, book="Household", type_name="Transport",
                      service_id=grab, source="auto"),
        "override": b.row("SAMPLE SHOP PRIME 7", 1499, book="Household", type_name="Subscriptions",
                          service_id=shop, source="rule_override"),
        "fallback": b.row("PAYNOW TO SAMPLE STALL", 800, book="Household", type_name="Dining",
                          service_id=None, source="fallback", account=b.bank),
        "derived": b.row("LOAN INTEREST", 31000, book="Household", type_name="Bank & government fees",
                         service_id=None, source="derived", account=b.bank),
        "manual_no_merchant": b.row("CORNER KIOSK", 650, book="Household", type_name="Dining",
                                    service_id=None, source="manual"),
        "no_type": b.row("SOMEWHERE UNPLACED", 300, book="Household", type_name=None,
                         service_id=None, source="auto"),
        "empty": b.row("NOTHING SET", 200, book=None, type_name=None, service_id=None, source="auto"),
        "income": b.row("SALARY SAMPLE", -500000, book=None, type_name=None, service_id=None,
                        source=None, account=b.bank, flow="income"),
    }
    conn.commit()
    return ids


@pytest.fixture
def todays(blank: Path) -> Path:
    """A book in today's shape: every step before this one applied, then the
    rows of case 32 written."""
    chain_until(blank, NAME)
    conn = open_book(blank)
    try:
        seed_case_32(conn)
    finally:
        conn.close()
    return blank


def labels(path: Path) -> dict:
    conn = open_book(path)
    try:
        return {r["id"]: (r["book"], r["type_id"], r["cat_source"])
                for r in conn.execute("SELECT id, book, type_id, cat_source FROM transactions")}
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


def sources(path: Path) -> dict:
    conn = open_book(path)
    try:
        return {r["id"]: (r["book_source"], r["type_source"])
                for r in conn.execute("SELECT id, book_source, type_source FROM transactions")}
    finally:
        conn.close()


def columns(path: Path) -> set[str]:
    conn = open_book(path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
    finally:
        conn.close()


def dump(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


# --- the declaration --------------------------------------------------------------


def test_the_six_sources_are_declared_once_with_their_words():
    assert book_type.SOURCE_NAMES == ("merchant", "rule", "wording", "checked", "chosen", "derived")
    assert all(words for _, words in book_type.SOURCES)


def test_a_writer_may_store_a_declared_source_or_none_for_an_empty_field():
    for name in book_type.SOURCE_NAMES:
        assert book_type.checked_source(name) == name
    assert book_type.checked_source(None) is None


@pytest.mark.parametrize("value", ["manual", "auto", "Chosen", "", 3])
def test_an_unknown_source_is_refused_by_the_check_every_writer_uses(value):
    with pytest.raises(book_type.UnknownSource):
        book_type.checked_source(value)


@pytest.mark.parametrize("column", ["book_source", "type_source"])
def test_a_new_book_refuses_an_unknown_source_from_any_writer(conn, column):
    b = Book(conn)
    row = b.row("SAMPLE", 100, book="Household", type_name="Dining", service_id=None, source="auto")
    for name in book_type.SOURCE_NAMES:
        conn.execute(f"UPDATE transactions SET {column} = ? WHERE id = ?", (name, row))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"UPDATE transactions SET {column} = 'manual' WHERE id = ?", (row,))


def table_sql(conn) -> str:
    return conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'transactions'").fetchone()[0]


def test_schema_and_the_step_declare_the_two_columns_alike(conn, todays):
    """schema.sql's CHECK holds the declared sources, and the step adds the
    columns as schema.sql declares them."""
    conversion.run_step(todays, convert_label_source.STEP)
    converted = open_book(todays)
    try:
        for column in convert_label_source.SOURCE_COLUMNS:
            declared = convert_label_source.column_sql(column)
            assert declared in table_sql(conn)
            assert declared in table_sql(converted)
            assert all(f"'{name}'" in declared for name in book_type.SOURCE_NAMES)
    finally:
        converted.close()


# --- case 32: today's rows converted ------------------------------------------------


def test_case_32_each_row_gets_the_source_its_mark_and_its_merchant_give(todays):
    conn = open_book(todays)
    try:
        ids = {k: v for k, v in seed_ids(conn).items()}
    finally:
        conn.close()
    before_labels, before_totals = labels(todays), account_totals(todays)

    report = conversion.run_step(todays, convert_label_source.STEP)

    assert report["status"] == "applied"
    got = sources(todays)
    assert {got[i] for i in ids["grab_same"]} == {("checked", "checked")}
    assert {got[i] for i in ids["grab_moom"]} == {("chosen", "checked")}
    assert got[ids["default"]] == ("merchant", "merchant")
    assert got[ids["auto"]] == ("merchant", "merchant")
    assert got[ids["override"]] == ("merchant", "rule")
    assert got[ids["fallback"]] == ("wording", "wording")
    assert got[ids["derived"]] == ("derived", "derived")
    assert got[ids["manual_no_merchant"]] == ("chosen", "chosen")
    assert got[ids["no_type"]] == ("merchant", None)
    assert got[ids["empty"]] == (None, None)
    assert got[ids["income"]] == (None, None)
    # Row count, every book, every type and cat_source itself unchanged.
    assert labels(todays) == before_labels
    assert account_totals(todays) == before_totals
    assert "cat_source" in columns(todays)


def seed_ids(conn: sqlite3.Connection) -> dict:
    """The case-32 row ids, read back by description."""
    by = {r["description"]: r["id"] for r in conn.execute("SELECT id, description FROM transactions")}
    return {
        "grab_same": [by[f"GRAB RIDE {i:02d}"] for i in range(34)],
        "grab_moom": [by[f"GRAB RIDE M{i}"] for i in range(6)],
        "default": by["SAMPLE SHOP 0042"],
        "auto": by["GRAB RIDE A"],
        "override": by["SAMPLE SHOP PRIME 7"],
        "fallback": by["PAYNOW TO SAMPLE STALL"],
        "derived": by["LOAN INTEREST"],
        "manual_no_merchant": by["CORNER KIOSK"],
        "no_type": by["SOMEWHERE UNPLACED"],
        "empty": by["NOTHING SET"],
        "income": by["SALARY SAMPLE"],
    }


def test_a_rule_override_on_both_fields_marks_both_from_the_rule(blank):
    chain_until(blank, NAME)
    conn = open_book(blank)
    b = Book(conn)
    hosting = b.merchant("Sample Hosting", "Household", "Software & AI tools")
    b.rule("SAMPLE HOSTING", hosting)
    b.rule("SAMPLE HOSTING TEAM", hosting, type_name="Subscriptions", book="Moom", priority=5)
    row = b.row("SAMPLE HOSTING TEAM 1", 4000, book="Moom", type_name="Subscriptions",
                service_id=hosting, source="rule_override")
    conn.commit()
    conn.close()

    conversion.run_step(blank, convert_label_source.STEP)

    assert sources(blank)[row] == ("rule", "rule")


def test_a_rule_override_whose_rule_is_gone_reads_each_field_against_its_merchant(blank):
    """Ruling (deviation): with no rule of the row's merchant matching now,
    a field equal to the merchant's is from the merchant, a different one from
    a rule; the rule that set it is not there to ask."""
    chain_until(blank, NAME)
    conn = open_book(blank)
    b = Book(conn)
    shop = b.merchant("Sample Shop", "Household", "Shopping")
    row = b.row("SAMPLE SHOP OLD PRIME", 1499, book="Household", type_name="Subscriptions",
                service_id=shop, source="rule_override")
    conn.commit()
    conn.close()

    conversion.run_step(blank, convert_label_source.STEP)

    assert sources(blank)[row] == ("merchant", "rule")


def test_a_hand_set_row_is_compared_with_what_its_winning_rule_gives_now(blank):
    """A hand-set row whose label equals its rule's override is checked; one
    that differs from it is chosen, even where it equals the merchant's own."""
    chain_until(blank, NAME)
    conn = open_book(blank)
    b = Book(conn)
    shop = b.merchant("Sample Shop", "Household", "Shopping")
    b.rule("SAMPLE SHOP", shop)
    b.rule("SAMPLE SHOP PRIME", shop, type_name="Subscriptions", priority=10)
    as_rule = b.row("SAMPLE SHOP PRIME 1", 1499, book="Household", type_name="Subscriptions",
                    service_id=shop, source="manual")
    as_merchant = b.row("SAMPLE SHOP PRIME 2", 1499, book="Household", type_name="Shopping",
                        service_id=shop, source="manual")
    conn.commit()
    conn.close()

    conversion.run_step(blank, convert_label_source.STEP)

    got = sources(blank)
    assert got[as_rule] == ("checked", "checked")
    assert got[as_merchant] == ("checked", "chosen")


def test_a_rule_of_another_merchant_winning_now_does_not_decide_a_hand_set_row(blank):
    """The row's own merchant is what fin offers it: a hand-set row filed under
    Grab is compared with Grab's label, not with the rule another merchant
    wins its wording with today."""
    chain_until(blank, NAME)
    conn = open_book(blank)
    b = Book(conn)
    grab = b.merchant("Grab", "Household", "Transport")
    food = b.merchant("Grab Food", "Household", "Dining")
    b.rule("GRAB", food)
    row = b.row("GRAB RIDE 9", 1200, book="Household", type_name="Transport",
                service_id=grab, source="manual")
    conn.commit()
    conn.close()

    conversion.run_step(blank, convert_label_source.STEP)

    assert sources(blank)[row] == ("checked", "checked")


def test_a_merchant_with_no_book_offers_the_book_an_empty_book_reads_as(blank):
    chain_until(blank, NAME)
    conn = open_book(blank)
    b = Book(conn)
    stall = b.merchant("Sample Stall", None, "Dining")
    row = b.row("SAMPLE STALL 3", 700, book=book_type.DEFAULT_BOOK, type_name="Dining",
                service_id=stall, source="manual")
    moom = b.row("SAMPLE STALL 4", 700, book="Moom", type_name="Dining",
                 service_id=stall, source="manual")
    conn.commit()
    conn.close()

    conversion.run_step(blank, convert_label_source.STEP)

    assert sources(blank)[row] == ("checked", "checked")
    assert sources(blank)[moom] == ("chosen", "checked")


def test_the_converted_book_records_both_sources_in_its_history(todays):
    conversion.run_step(todays, convert_label_source.STEP)
    conn = open_book(todays)
    try:
        row = conn.execute("SELECT id FROM transactions WHERE description = 'GRAB RIDE A'").fetchone()[0]
        entry = history.open_entry(conn, history.VIA_APP, history.APP_ACTOR, "a test change")
        conn.execute("UPDATE transactions SET book_source = 'chosen', book = 'Moom' WHERE id = ?", (row,))
        conn.commit()
        history.close_entry(conn, entry)
        before, after = conn.execute(
            "SELECT before, after FROM change_rows WHERE entry_id = ?", (entry,)).fetchone()
    finally:
        conn.close()
    assert (json.loads(before)["book_source"], json.loads(after)["book_source"]) == ("merchant", "chosen")
    assert json.loads(after)["type_source"] == "merchant"


def test_every_column_of_the_converted_book_is_in_its_three_history_triggers(todays):
    conversion.run_step(todays, convert_label_source.STEP)
    conn = open_book(todays)
    try:
        wanted = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
        for op in ("insert", "update", "delete"):
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                               (f"history_transactions_{op}",)).fetchone()[0]
            for column in wanted:
                assert f"'{column}', " in sql, (op, column)
    finally:
        conn.close()


@pytest.mark.parametrize("column", ["book_source", "type_source"])
def test_the_converted_book_refuses_an_unknown_source_from_any_writer(todays, column):
    conversion.run_step(todays, convert_label_source.STEP)
    conn = open_book(todays)
    try:
        row = conn.execute("SELECT id FROM transactions WHERE description = 'GRAB RIDE A'").fetchone()[0]
        for name in book_type.SOURCE_NAMES:
            conn.execute(f"UPDATE transactions SET {column} = ? WHERE id = ?", (name, row))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE transactions SET {column} = 'manual' WHERE id = ?", (row,))
    finally:
        conn.rollback()
        conn.close()


def test_a_mark_the_step_does_not_know_is_refused_naming_it_and_nothing_is_changed(todays):
    conn = open_book(todays)
    conn.execute("UPDATE transactions SET cat_source = 'by-guess' WHERE description = 'GRAB RIDE A'")
    conn.commit()
    conn.close()
    was = dump(todays)

    with pytest.raises(conversion.ConversionRefused, match="by-guess"):
        conversion.run_step(todays, convert_label_source.STEP)

    assert dump(todays) == was
    assert not list(todays.parent.glob(f"*pre-{NAME}*"))


# --- case 33: run twice ---------------------------------------------------------------


def test_case_33_a_second_run_changes_nothing(todays):
    first = conversion.run_step(todays, convert_label_source.STEP)
    after_first = dump(todays)
    second = conversion.run_step(todays, convert_label_source.STEP)

    assert (first["status"], second["status"]) == ("applied", "already-applied")
    assert dump(todays) == after_first
    assert len(list(todays.parent.glob(f"*pre-{NAME}*"))) == 1


def test_a_book_made_new_in_todays_shape_reads_as_converted(conn):
    assert convert_label_source.STEP.is_applied(conn)


# --- case 34: before the expect-back step ---------------------------------------------


def test_case_34_a_book_before_the_expect_back_step_is_refused_naming_it(blank):
    chain_until(blank, "expect-back")
    conn = open_book(blank)
    b = Book(conn)
    person = conn.execute(
        "INSERT INTO accounts (name, short_name, type, currency, owner) VALUES ('Ravi', 'Ravi', 'person', 'SGD', 'Household')"
    ).lastrowid
    lent = b.row("TRANSFER TO RAVI", 200000, book=None, type_name=None, service_id=None,
                 source="manual", day="2026-06-03", account=b.bank, flow="movement")
    conn.execute("UPDATE transactions SET other_side_id = ?, flow_type_manual = 1 WHERE id = ?", (person, lent))
    conn.commit()
    conn.close()
    was = dump(blank)

    with pytest.raises(conversion.ConversionRefused, match="expect-back"):
        conversion.run_step(blank, convert_label_source.STEP)

    assert dump(blank) == was
    assert not list(blank.parent.glob(f"*pre-{NAME}*"))


# --- case 35: without its backup --------------------------------------------------------


def test_case_35_without_its_backup_the_step_refuses_and_changes_nothing(todays):
    was = dump(todays)

    with pytest.raises(conversion.ConversionRefused, match="backup"):
        conversion.run_step(todays, convert_label_source.STEP, backup=lambda source, target: None)

    assert dump(todays) == was
    assert "book_source" not in columns(todays)


# --- the chain and the app's gate --------------------------------------------------------


def test_the_step_runs_last_in_the_chain_after_expect_back():
    names = [name for name, _ in conversion.CHAIN]
    assert names.index(NAME) == names.index("expect-back") + 1


def test_fin_will_not_start_on_a_book_in_todays_shape_and_names_the_step(todays, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", todays)
    with pytest.raises(db.DatabaseNotConverted, match=NAME):
        db.init_db()


def test_the_one_command_converts_todays_book_and_fin_starts_on_it(todays, monkeypatch, capsys):
    assert convert_all.main([str(todays)]) == 0
    assert f"step {NAME}: applied" in capsys.readouterr().out
    monkeypatch.setattr(db, "DB_PATH", todays)
    db.invalidate_rules_cache()
    db.init_db()
    db.invalidate_rules_cache()
    assert sources(todays)


def test_the_step_prints_counts_only(todays, capsys):
    assert convert_label_source.main([str(todays)]) == 0
    out = capsys.readouterr().out
    assert "checked" in out and "chosen" in out
    for words in ("GRAB", "Sample Shop", "Grab", "Moom", "Household"):
        assert words not in out
