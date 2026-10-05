"""Retiring the category tree, proved on a temporary database in the old shape.

The step runs after the book and type step on a database built from the
frozen copy of the old schema beside this file. Every figure, merchant and
name here is invented.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

import app as fin_app
import conversion
import convert_account_kinds
import convert_book_type
import convert_minor_units
import convert_printed_statements
import db
import retire_categories
import retire_float_amounts

DAY = date(2026, 3, 14)
OLD_SCHEMA = Path(__file__).parent / "schema_before_book_and_type.sql"

RETIRED_COLUMNS = (
    ("transactions", "category_id"),
    ("services", "category_id"),
    ("merchant_rules", "category_override_id"),
    ("subscriptions", "category_id"),
)


def run(path: Path, sql: str, params=()) -> int:
    conn = sqlite3.connect(str(path))
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def query(path: Path, sql: str, params=()) -> list[tuple]:
    conn = sqlite3.connect(str(path))
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


@pytest.fixture
def old(tmp_path: Path) -> Path:
    """A database in the old shape with a little of everything in it."""
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA.read_text())
    conn.executescript("""
        INSERT INTO categories (id, name, parent_id) VALUES
            (1, 'Dining', NULL), (2, 'Coffee', 1), (3, 'Moom', NULL), (4, 'Klaviyo', 3),
            (5, 'Other', NULL), (6, 'Credits', NULL), (7, 'Salary', 6), (8, 'Shopping', NULL);
        INSERT INTO accounts (id, name, short_name, type) VALUES
            (1, 'Sample Card 0001', 'Sample-0001', 'credit_card'),
            (2, 'Sample Bank 0002', 'Sample-0002', 'bank');
        INSERT INTO statements (id, account_id, statement_date) VALUES
            (1, 1, '2026-01-01'), (2, 2, '2026-01-01');
        INSERT INTO services (id, name, category_id, notes, is_one_off) VALUES
            (1, 'Sample Cafe', 2, 'sample note', 0),
            (2, 'Sample Mailer', 4, NULL, 1),
            (3, 'Sample Blank', NULL, NULL, 0);
        INSERT INTO merchant_rules (id, pattern, service_id, category_override_id, priority) VALUES
            (1, 'SAMPLE CAFE', 1, NULL, 0),
            (2, 'SAMPLE CAFE BEANS', 1, 8, 5);
        INSERT INTO subscriptions (id, service_id, category_id, amount, frequency, match_pattern)
            VALUES (1, 2, 4, 30.00, 'monthly', 'SAMPLE MAILER');
        INSERT INTO transactions
            (id, statement_id, date, description, amount_sgd, category_id, service_id,
             cat_source, flow_type, flow_type_manual, notes, is_one_off)
        VALUES
            (1, 1, '2026-01-10', 'SAMPLE CAFE', 12.30, 2, 1, 'service_default', 'expense', 0, NULL, 0),
            (2, 1, '2026-01-11', 'SAMPLE MAILER', 30.00, 4, 2, 'manual', 'expense', 0, 'kept note', 1),
            (3, 1, '2026-01-12', 'CORNER STALL', 4.20, 5, NULL, 'auto', 'expense', 0, NULL, 0),
            (4, 2, '2026-01-13', 'SAMPLE EMPLOYER PAY', -4000.00, 7, NULL, 'auto', 'income', 1, NULL, 0),
            (5, 2, '2026-01-14', 'NOTHING KNOWN', 9.99, NULL, NULL, 'auto', 'expense', 0, NULL, 0);
        -- Ids handed out and since deleted: the next id must not reuse them.
        INSERT INTO transactions (id, statement_id, date, description, amount_sgd)
            VALUES (9, 1, '2026-01-15', 'DELETED SINCE', 1.00);
        DELETE FROM transactions WHERE id = 9;
    """)
    conn.commit()
    conn.close()
    return path


def expand(path: Path) -> dict:
    return conversion.run_step(path, convert_book_type.STEP, today=DAY)


def retire(path: Path, **kwargs) -> dict:
    return conversion.run_step(path, retire_categories.STEP, today=DAY, **kwargs)


def labels(path: Path) -> dict:
    """Every label the book and type step left, by table and row."""
    return {
        "transactions": query(path, "SELECT id, book, type_id, service_id, cat_source FROM transactions ORDER BY id"),
        "services": query(path, "SELECT id, name, book, type_id FROM services ORDER BY id"),
        "merchant_rules": query(path, "SELECT id, pattern, service_id, book_override, type_override_id FROM merchant_rules ORDER BY id"),
        "subscriptions": query(path, "SELECT id, service_id, book, type_id FROM subscriptions ORDER BY id"),
    }


# --- what the step removes, and what it keeps ---------------------------------


def test_the_category_columns_and_table_are_dropped(old):
    expand(old)

    report = retire(old)

    assert report["status"] == "applied"
    tables = {r[0] for r in query(old, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "categories" not in tables
    for table, column in RETIRED_COLUMNS:
        present = {r[0] for r in query(old, f"SELECT name FROM pragma_table_info('{table}')")}
        assert column not in present, table
    # Nothing left in the schema names a category column or the table.
    leftovers = ("category_id", "category_override_id", "REFERENCES categories", "idx_transactions_category", "idx_services_category")
    for (sql,) in query(old, "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"):
        assert not [word for word in leftovers if word in sql], sql


def test_no_row_count_and_no_account_total_moves(old):
    expand(old)
    counts = {
        table: query(old, f"SELECT COUNT(*) FROM {table}")[0][0]
        for table in ("transactions", "services", "merchant_rules", "subscriptions",
                      "accounts", "statements")
    }

    report = retire(old)

    assert report["after"]["account_totals_cents"] == report["before"]["account_totals_cents"]
    assert report["after"]["account_totals_cents"] == {1: 4650, 2: -399001}
    assert "categories" in report["before"]["rows"] and "categories" not in report["after"]["rows"]
    for table, n in counts.items():
        assert report["after"]["rows"][table] == n, table
        assert query(old, f"SELECT COUNT(*) FROM {table}")[0][0] == n, table


def test_every_label_and_every_other_value_is_kept(old):
    expand(old)
    before = labels(old)
    rows_before = query(
        old,
        "SELECT id, statement_id, date, description, amount_sgd, amount_foreign,"
        " currency_foreign, service_id, is_one_off, cat_source, flow_type, flow_type_manual,"
        " notes, created_at FROM transactions ORDER BY id",
    )
    merchants_before = query(
        old, "SELECT id, name, is_one_off, exclude_from_expense_views, notes, created_at"
             " FROM services ORDER BY id"
    )
    rules_before = query(
        old, "SELECT id, pattern, service_id, match_type, confidence, priority, min_amount,"
             " max_amount, created_at FROM merchant_rules ORDER BY id"
    )
    subscriptions_before = query(
        old, "SELECT id, service_id, amount, currency, frequency, periods, account_id,"
             " last_paid, renewal_date, status, link, notes, match_pattern, created_at"
             " FROM subscriptions ORDER BY id"
    )

    retire(old)

    assert labels(old) == before
    # The labels are the mapping's, not blanks carried across.
    dining = query(old, "SELECT id FROM types WHERE kind = 'spending' AND name = 'Dining'")[0][0]
    assert before["transactions"][0] == (1, "Household", dining, 1, "service_default")
    assert before["transactions"][1][1] == "Moom"
    assert before["merchant_rules"][1][3:] == ("Household", query(
        old, "SELECT id FROM types WHERE kind = 'spending' AND name = 'Shopping'")[0][0])
    assert query(
        old,
        "SELECT id, statement_id, date, description, amount_sgd, amount_foreign,"
        " currency_foreign, service_id, is_one_off, cat_source, flow_type, flow_type_manual,"
        " notes, created_at FROM transactions ORDER BY id",
    ) == rows_before
    assert query(
        old, "SELECT id, name, is_one_off, exclude_from_expense_views, notes, created_at"
             " FROM services ORDER BY id"
    ) == merchants_before
    assert query(
        old, "SELECT id, pattern, service_id, match_type, confidence, priority, min_amount,"
             " max_amount, created_at FROM merchant_rules ORDER BY id"
    ) == rules_before
    assert query(
        old, "SELECT id, service_id, amount, currency, frequency, periods, account_id,"
             " last_paid, renewal_date, status, link, notes, match_pattern, created_at"
             " FROM subscriptions ORDER BY id"
    ) == subscriptions_before


def test_merchants_gain_the_review_each_time_mark_unset(old):
    expand(old)

    retire(old)

    assert query(old, "SELECT id, review_each_time FROM services ORDER BY id") == [
        (1, 0), (2, 0), (3, 0),
    ]


def test_an_id_already_handed_out_is_not_given_to_a_new_row(old):
    expand(old)

    retire(old)

    new_id = run(
        old,
        "INSERT INTO transactions (statement_id, date, description, amount_sgd)"
        " VALUES (1, '2026-01-20', 'SAMPLE LATER ROW', 2.00)",
    )
    assert new_id == 10


def test_a_database_through_every_step_has_the_shape_of_a_new_one(old, tmp_path):
    expand(old)
    retire(old)
    # A new database holds amounts as whole cents only: the two money steps
    # follow the two that retire the category.
    conversion.run_step(old, convert_minor_units.STEP, today=DAY)
    conversion.run_step(old, retire_float_amounts.STEP, today=DAY)
    # And through the step that came after them, which adds to the shape.
    conversion.run_step(old, convert_account_kinds.STEP, today=DAY)
    conversion.run_step(old, convert_printed_statements.STEP, today=DAY)
    fresh = tmp_path / "fresh.db"
    conn = sqlite3.connect(str(fresh))
    conn.executescript(db.SCHEMA_PATH.read_text())
    conn.close()
    # What the next start does to a converted database: the schema script's
    # CREATE IF NOT EXISTS adds any table made since the conversions (the
    # suggestion answers) and leaves every table the steps shaped as it is.
    conn = sqlite3.connect(str(old))
    conn.executescript(db.SCHEMA_PATH.read_text())
    conn.close()

    def shape(path: Path) -> dict:
        tables = [r[0] for r in query(
            path, "SELECT name FROM sqlite_master WHERE type = 'table'"
                  " AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        return {
            "tables": {
                t: query(path, f"SELECT name, type, \"notnull\", dflt_value, pk"
                               f" FROM pragma_table_info('{t}') ORDER BY cid")
                for t in tables
            },
            "foreign_keys": {
                t: sorted(query(path, f"SELECT \"table\", \"from\", \"to\""
                                      f" FROM pragma_foreign_key_list('{t}')"))
                for t in tables
            },
            "indexes": sorted(query(
                path, "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'"
                      " AND name NOT LIKE 'sqlite_%'"
            )),
        }

    assert shape(old) == shape(fresh)
    assert query(old, "PRAGMA foreign_key_check") == []


# --- replay, refusal, and the order of the two steps ---------------------------


def test_run_twice_changes_nothing(old):
    expand(old)
    retire(old)
    after_first = dump(old)
    files = sorted(p.name for p in old.parent.iterdir())

    second = retire(old)

    assert second["status"] == "already-applied"
    assert second["backup"] is None
    assert dump(old) == after_first
    assert sorted(p.name for p in old.parent.iterdir()) == files


def test_it_runs_behind_its_backup_and_refuses_without_one(old):
    expand(old)
    before = dump(old)

    with pytest.raises(conversion.ConversionRefused, match="backup"):
        retire(old, backup=lambda source, target: None)
    assert dump(old) == before

    report = retire(old)
    assert Path(report["backup"]).name == "ledger.db.pre-retire-categories-20260314.bak"
    assert dump(Path(report["backup"])) == before


def test_it_will_not_run_before_the_book_and_type_step(old):
    before = dump(old)

    with pytest.raises(conversion.ConversionRefused, match="runs after book-and-type"):
        retire(old)

    assert dump(old) == before


def test_it_will_not_run_while_rows_wait_for_the_book_and_type_step(old):
    expand(old)
    # A row the old app added since: it has a category and no label yet.
    run(
        old,
        "INSERT INTO transactions (statement_id, date, description, amount_sgd, category_id)"
        " VALUES (1, '2026-01-20', 'SAMPLE CAFE AGAIN', 5.00, 1)",
    )
    before = dump(old)

    with pytest.raises(conversion.ConversionRefused, match="runs after book-and-type"):
        retire(old)
    assert dump(old) == before

    expand(old)
    assert retire(old)["status"] == "applied"
    assert query(old, "SELECT book FROM transactions WHERE description = 'SAMPLE CAFE AGAIN'") == [
        ("Household",)
    ]


def test_a_column_the_schema_does_not_declare_stops_the_step(old):
    expand(old)
    run(old, "ALTER TABLE services ADD COLUMN sample_extra TEXT")
    before = dump(old)

    with pytest.raises(conversion.ConversionFailed, match="ColumnNotInSchema"):
        retire(old)

    assert dump(old) == before


def test_a_new_database_reads_as_already_retired(temp_db):
    report = conversion.run_step(temp_db, retire_categories.STEP, today=DAY)

    assert report["status"] == "already-applied"


# --- the command -----------------------------------------------------------------


def test_the_command_reports_counts_and_what_only_the_backup_now_holds(old, capsys):
    expand(old)

    assert retire_categories.main([str(old)]) == 0

    out = capsys.readouterr().out
    assert "step retire-categories: applied" in out
    assert "backup:" in out
    # The salary row's category, and the subscription filed apart from its merchant.
    assert "    1  rows that are not spending" in out
    assert "    0  subscriptions whose own label" in out


def test_the_command_says_which_step_to_run_first(old, capsys):
    before = dump(old)

    assert retire_categories.main([str(old)]) == 1

    assert "convert_book_type.py" in capsys.readouterr().out
    assert dump(old) == before
    assert sorted(p.name for p in old.parent.iterdir()) == ["ledger.db"]


def test_the_command_needs_a_database_path(tmp_path, capsys):
    assert retire_categories.main([]) == 2
    assert retire_categories.main([str(tmp_path / "missing.db")]) == 1


# --- the app on a converted database, and on one that is not ---------------------


@pytest.fixture
def started_on(monkeypatch: pytest.MonkeyPatch):
    """Point the app at a database file and start it."""
    def start(path: Path):
        monkeypatch.setattr(db, "DB_PATH", path)
        db.invalidate_rules_cache()
        db.init_db()
        fin_app.app.config["TESTING"] = True
        return fin_app.app.test_client()

    yield start
    db.invalidate_rules_cache()


def test_the_app_will_not_start_on_a_database_that_still_carries_categories(old, started_on):
    before = dump(old)

    with pytest.raises(db.DatabaseNotConverted, match="retire_categories.py"):
        started_on(old)
    assert dump(old) == before

    # Half way is not enough either.
    expand(old)
    half_way = dump(old)
    # The refusal names only what is still to run, starting with the next step.
    with pytest.raises(db.DatabaseNotConverted, match=r"in order:\n  1\. retire-categories"):
        started_on(old)
    assert dump(old) == half_way


def test_the_app_serves_a_converted_database_by_book_and_type(old, started_on, monkeypatch):
    monkeypatch.setattr(fin_app, "_get_usd_sgd_rate", lambda: 1.35)
    expand(old)
    retire(old)
    # The app starts only on amounts held as whole cents.
    conversion.run_step(old, convert_minor_units.STEP, today=DAY)
    conversion.run_step(old, retire_float_amounts.STEP, today=DAY)
    conversion.run_step(old, convert_account_kinds.STEP, today=DAY)
    conversion.run_step(old, convert_printed_statements.STEP, today=DAY)

    client = started_on(old)

    rows = {t["id"]: t for t in client.get("/api/transactions?per_page=50").get_json()["transactions"]}
    assert (rows[1]["book"], rows[1]["display_type"], rows[1]["cat_source"]) == (
        "Household", "Dining", "service_default",
    )
    assert (rows[2]["book"], rows[2]["display_type"], rows[2]["cat_source"]) == (
        "Moom", "Software & AI tools", "manual",
    )
    # Marked for review, and a row that never had a category: no type, the
    # household's until the operator says otherwise.
    assert (rows[3]["book"], rows[3]["type_id"]) == ("Household", None)
    assert (rows[5]["book"], rows[5]["type_id"]) == ("Household", None)
    untyped = client.get("/api/transactions?types=__untyped__").get_json()["transactions"]
    assert {t["id"] for t in untyped} == {3, 5}

    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-01").get_json()
    assert (cards["household"], cards["moom"], cards["kalesh"], cards["untyped"]) == (
        26.49, 30.00, 0, 2,
    )
    merchants = {s["name"]: s for s in client.get("/api/services").get_json()}
    assert (merchants["Sample Cafe"]["book"], merchants["Sample Cafe"]["display_type"]) == (
        "Household", "Dining",
    )
    [subscription] = client.get("/api/subscriptions").get_json()
    assert (subscription["book"], subscription["display_type"]) == ("Moom", "Software & AI tools")
    overriding = next(
        r for r in client.get("/api/rules").get_json() if r["pattern"] == "SAMPLE CAFE BEANS"
    )
    assert (overriding["book"], overriding["display_type"]) == ("Household", "Shopping")
