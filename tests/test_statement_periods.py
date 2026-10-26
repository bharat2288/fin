"""Statement periods (fin-redesign 04; spec cases 10 and 11).

A statement record holds the day its period starts beside its closing day.
Case 10 is driven through the import (upload, preview, confirm) and read back
through the coverage view and missing months. Case 11 is the conversion step
that gives an existing book the column and backfills it, proved on a book built
under tmp_path from the oldest frozen schema and taken through the chain, the
way the operator's book reached today's shape. Every name and figure is
invented.
"""

from __future__ import annotations

import calendar
import io
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import conversion
import convert_statement_periods
import db
import history
import loan_interest
import missing
import parsers
from parse_dbs import ParsedStatement, ParsedTransaction

CARD = "Sample Card 0001"
OLD_SCHEMA = Path(__file__).parent / "schema_before_book_and_type.sql"
DAY = date(2026, 10, 24)


# --- months relative to today: the coverage view reads the clock ------------


def month_back(n: int, today: date | None = None) -> tuple[int, int]:
    today = today or date.today()
    y, m = divmod(today.year * 12 + today.month - 1 - n, 12)
    return y, m + 1


def first_of(n: int, today: date | None = None) -> str:
    y, m = month_back(n, today)
    return f"{y:04d}-{m:02d}-01"


def last_of(n: int, today: date | None = None) -> str:
    y, m = month_back(n, today)
    return f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"


def key(n: int, today: date | None = None) -> str:
    y, m = month_back(n, today)
    return f"{y:04d}-{m:02d}"


# --- the import ---------------------------------------------------------------


@pytest.fixture
def stand_in():
    """A stand-in parser returning the statements it is given, one per upload."""
    queue: list[ParsedStatement] = []

    def install(rows, *, opening_minor=None, closing_minor=None, opening_date=None, closing_date):
        queue.append(ParsedStatement(
            statement_type="credit_card",
            statement_date=closing_date,
            accounts=[CARD],
            filename="sample.pdf",
            transactions=[ParsedTransaction(date=on, description=what, amount_minor=amount, card_info=CARD)
                          for on, what, amount in rows],
            opening_minor=opening_minor,
            closing_minor=closing_minor,
            opening_date=opening_date,
            closing_date=closing_date if closing_minor is not None else None,
        ))

    parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    parsers._PARSERS.insert(0, {
        "name": "Stand-in", "ext": ".csv", "detect_fn": lambda _path: True,
        "parse_fn": lambda _path: [queue.pop(0)],
    })
    yield install
    parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]


def imported(client) -> None:
    resp = client.post("/api/import/upload", data={"files": (io.BytesIO(b"stand-in"), "sample.csv")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    preview = resp.get_json()
    resp = client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [{"account": g["account"], "transactions": g["transactions"], "statements": g["statements"]}
                   for g in preview["groups"]],
    })
    assert resp.status_code == 200, resp.get_json()


def two_statements(stand_in, client) -> None:
    """A one-month statement four months back, then one statement covering
    the next two months whose rows all fall in its second month. A card's
    balance is negative while it is owed."""
    stand_in([(last_of(4)[:8] + "10", "SAMPLE GROCER", 5_000)],
             opening_minor=0, closing_minor=-5_000, opening_date=first_of(4), closing_date=last_of(4))
    imported(client)
    stand_in([(last_of(2)[:8] + "12", "SAMPLE CAFE", 2_000)],
             opening_minor=-5_000, closing_minor=-7_000, opening_date=first_of(3), closing_date=last_of(2))
    imported(client)


def test_case_10_a_statement_covering_two_months_is_stored_with_its_period_start_and_end(stand_in, client, conn):
    two_statements(stand_in, client)

    held = conn.execute(
        "SELECT period_start, statement_date, printed FROM statements ORDER BY statement_date"
    ).fetchall()

    assert [tuple(r) for r in held] == [(first_of(4), last_of(4), 1), (first_of(3), last_of(2), 1)]


def test_case_10_the_coverage_view_no_longer_marks_the_first_month_missing(stand_in, client, conn):
    two_statements(stand_in, client)

    coverage = client.get("/api/statements/coverage?months=6").get_json()
    [card] = [a["id"] for a in coverage["accounts"]]

    assert [m for m in (key(4), key(3), key(2)) if coverage["matrix"][str(card)][m]["imported"]] \
        == [key(4), key(3), key(2)]
    assert missing.missing_months(conn, [card], [key(4), key(3), key(2)], date.today()) == []


def test_a_month_still_missing_between_two_statements_is_still_missing(stand_in, client, conn):
    """Periods hold the months they reach and no others: a gap stays a gap."""
    stand_in([(last_of(5)[:8] + "10", "SAMPLE GROCER", 5_000)],
             opening_minor=0, closing_minor=-5_000, opening_date=first_of(5), closing_date=last_of(5))
    imported(client)
    stand_in([(last_of(2)[:8] + "12", "SAMPLE CAFE", 2_000)],
             opening_minor=-5_000, closing_minor=-7_000, opening_date=first_of(3), closing_date=last_of(2))
    imported(client)
    card = conn.execute("SELECT id FROM accounts WHERE name = ?", (CARD,)).fetchone()[0]

    coverage = client.get("/api/statements/coverage?months=6").get_json()

    assert coverage["matrix"][str(card)][key(4)] == {"imported": False}
    assert missing.missing_months(conn, [card], [key(4), key(3)], date.today()) == [key(4)]


def test_a_source_that_states_no_balance_makes_month_records_starting_on_the_1st(stand_in, client, conn):
    stand_in([(last_of(3)[:8] + "20", "SAMPLE GROCER", 5_000), (last_of(2)[:8] + "03", "SAMPLE CAFE", 2_000)],
             closing_date=last_of(2))
    imported(client)

    held = conn.execute("SELECT period_start, statement_date, printed FROM statements ORDER BY statement_date")

    assert [tuple(r) for r in held] == [(first_of(3), first_of(3), 0), (first_of(2), first_of(2), 0)]


def test_a_printed_statement_that_states_no_opening_day_starts_after_the_one_before(stand_in, client, conn):
    two_statements(stand_in, client)
    stand_in([(last_of(1)[:8] + "12", "SAMPLE CAFE", 3_000)],
             opening_minor=-7_000, closing_minor=-10_000, closing_date=last_of(1))
    imported(client)

    held = conn.execute("SELECT period_start FROM statements WHERE statement_date = ?", (last_of(1),))

    # Read, not stored: it follows the statement before it, whenever that is
    # imported or undone.
    assert held.fetchone()[0] is None
    assert read_start(conn, last_of(1)) == first_of(1)


def test_derived_loan_interest_records_start_the_day_after_the_figure_before(client, conn):
    loan = client.post("/api/accounts", json={"name": "Sample Loan 0009", "type": "loan"}).get_json()["id"]
    for owed, on in (("1000.00", "2026-06-30"), ("990.00", "2026-08-31")):
        assert client.post("/api/anchors", json={"account_id": loan, "amount": owed, "date": on}).status_code == 200
    bank = client.post("/api/accounts", json={"name": "Sample Bank 0002", "type": "bank"}).get_json()["id"]
    sid = conn.execute("INSERT INTO statements (account_id, statement_date, filename, printed, period_start)"
                       " VALUES (?, '2026-08-01', 'sample.csv', 0, '2026-08-01')", (bank,)).lastrowid
    conn.execute("INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type,"
                 " other_side_id) VALUES (?, '2026-08-05', 'SAMPLE LOAN INSTALMENT', 2500, 'movement', ?)",
                 (sid, loan))
    conn.commit()
    loan_interest.derive(conn)
    conn.commit()

    held = conn.execute("SELECT period_start, statement_date FROM statements WHERE filename = ?",
                        (loan_interest.DERIVED_STATEMENT,)).fetchall()

    assert [tuple(r) for r in held] == [("2026-07-01", "2026-08-31")]


# --- the conversion (case 11) -------------------------------------------------


def chain_until(path: Path, stop_before: str) -> None:
    """Every step of the chain before `stop_before`, through the runner."""
    for name, module_name in conversion.CHAIN:
        if name == stop_before:
            return
        conversion.run_step(path, conversion.chain_step(module_name), today=DAY)
    raise AssertionError(f"{stop_before} is not in the chain")


@pytest.fixture
def old(tmp_path: Path) -> Path:
    """A book in the shape from before the step, holding every kind of
    statement record: printed statements one after another, a first printed
    statement, one after a gap, calendar-month records (one moved off the 1st
    by a printed statement closing there), and a derived loan-interest record."""
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA.read_text())
    conn.close()
    chain_until(path, convert_statement_periods.STEP.name)
    conn = sqlite3.connect(str(path))
    # Started before, as the operator's book was: what a start makes is there
    # (schema.sql's CREATE ... IF NOT EXISTS; the statements table and its
    # triggers are held already, so they keep the shape from before the step).
    conn.executescript(db.SCHEMA_PATH.read_text())

    def account(name, kind):
        return conn.execute("INSERT INTO accounts (name, short_name, type, currency, owner)"
                            " VALUES (?, ?, ?, 'SGD', 'Household')", (name, name, kind)).lastrowid

    card, bank, loan = account(CARD, "card"), account("Sample Bank 0002", "bank"), account("Sample Loan 0009", "loan")
    statements = [
        # (id, account, closing day, printed, filename)
        (1, card, "2026-03-14", 1, "import_1_card"),   # the card's first printed statement
        (2, card, "2026-04-14", 1, "import_2_card"),   # the month after
        (3, card, "2026-05-14", 1, "import_3_card"),
        (4, card, "2026-08-14", 1, "import_4_card"),   # after a gap: June and July never imported
        (5, card, "2026-09-01", 1, "import_5_card"),   # closed on the 1st, in the month after
        (6, card, "2026-09-02", 0, "import_6_card"),   # a month record moved off the 1st by it
        (7, bank, "2026-07-01", 0, "sample.csv"),
        (8, bank, "2026-08-01", 0, "sample.csv"),
        (9, loan, "2026-08-31", 0, loan_interest.DERIVED_STATEMENT),
    ]
    conn.executemany("INSERT INTO statements (id, account_id, statement_date, printed, filename)"
                     " VALUES (?, ?, ?, ?, ?)", statements)
    conn.execute("INSERT INTO anchors (account_id, date, amount, source) VALUES (?, '2026-06-30', -100000, 'supplied')",
                 (loan,))
    conn.execute("INSERT INTO anchors (account_id, date, amount, source) VALUES (?, '2026-08-31', -99000, 'supplied')",
                 (loan,))
    conn.executemany(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type) VALUES (?, ?, ?, ?, ?)",
        [(1, "2026-03-02", "SAMPLE GROCER", 1_000, "expense"), (2, "2026-04-03", "SAMPLE CAFE", 2_550, "expense"),
         (3, "2026-05-04", "SAMPLE GROCER", 1_200, "expense"), (4, "2026-08-01", "SAMPLE CAFE", 900, "expense"),
         (5, "2026-08-20", "SAMPLE GROCER", 700, "expense"), (6, "2026-09-02", "SAMPLE CAFE", 300, "expense"),
         (7, "2026-07-09", "SAMPLE EMPLOYER SALARY", -500_000, "income"),
         (8, "2026-08-09", "SAMPLE EMPLOYER SALARY", -500_000, "income"),
         (9, "2026-07-31", "Loan interest", 1_250, "expense")],
    )
    conn.commit()
    conn.close()
    return path


def query(path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(str(path))
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def dump(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def convert(path: Path, **kwargs) -> dict:
    return conversion.run_step(path, convert_statement_periods.STEP, today=DAY, **kwargs)


TRANSACTIONS = "SELECT * FROM transactions ORDER BY id"
STATEMENTS = "SELECT id, account_id, statement_date, filename, imported_at, printed FROM statements ORDER BY id"


def test_case_11_every_existing_statement_gets_its_period_start(old):
    """Stored where it is known on its own; a printed statement's is read
    from the closing before it (statement_period.start), as fin reads it."""
    import statement_period

    convert(old)

    assert query(old, "SELECT id, period_start FROM statements ORDER BY id") == [
        (1, None), (2, None), (3, None), (4, None), (5, None),  # printed: read, not stored
        (6, "2026-09-01"),  # a month record: the 1st of its month, wherever it is dated
        (7, "2026-07-01"),
        (8, "2026-08-01"),
        (9, "2026-07-01"),  # derived interest: the day after the loan's figure before
    ]
    conn = sqlite3.connect(str(old))
    try:
        read = [(sid, statement_period.start(conn, *row)) for sid, *row in conn.execute(
            "SELECT id, account_id, statement_date, period_start, printed FROM statements ORDER BY id")]
    finally:
        conn.close()
    assert read == [
        (1, "2026-03-01"),  # the first printed statement: the 1st of its month
        (2, "2026-03-15"),  # the day after the previous closing
        (3, "2026-04-15"),
        (4, "2026-08-01"),  # after a gap the previous closing cannot be told: the 1st
        (5, "2026-08-15"),
        (6, "2026-09-01"),
        (7, "2026-07-01"),
        (8, "2026-08-01"),
        (9, "2026-07-01"),
    ]


def test_case_11_no_row_total_or_statement_moves(old):
    rows, statements = query(old, TRANSACTIONS), query(old, STATEMENTS)

    report = convert(old)

    assert report["status"] == "applied"
    assert report["before"]["rows"] == report["after"]["rows"]
    assert report["before"]["account_totals_cents"] == report["after"]["account_totals_cents"]
    assert query(old, TRANSACTIONS) == rows
    assert query(old, STATEMENTS) == statements


def test_case_11_the_queue_and_the_numbers_read_the_same_after_the_conversion(old, tmp_path):
    """The five invariants through fin's own routes (fin_snapshot), before and
    after. Before is the book as fin read it until now: every record with no
    period start, which reads as its statement_date's month alone."""
    import fin_snapshot

    unfilled = tmp_path / "unfilled.db"
    unfilled.write_bytes(old.read_bytes())
    conn = sqlite3.connect(str(unfilled))
    conn.execute("ALTER TABLE statements ADD COLUMN period_start TEXT")
    conn.commit()
    conn.close()

    before = fin_snapshot.snap(unfilled)
    convert(old)
    after = fin_snapshot.snap(old)

    passed, result = fin_snapshot.diff(before, after)
    assert result == {name: [] for name in result}
    assert passed


def test_run_twice_changes_nothing_and_writes_one_backup(old):
    convert(old)
    after_first = dump(old)

    again = convert(old)

    assert again["status"] == "already-applied"
    assert dump(old) == after_first
    assert len(list(old.parent.glob("*.pre-statement-periods-*.bak"))) == 1


def test_it_refuses_without_its_backup(old):
    before = dump(old)

    with pytest.raises(conversion.ConversionRefused, match="backup copy is missing"):
        convert(old, backup=lambda source, target: None)

    assert dump(old) == before
    assert Path(convert(old)["backup"]).name == "ledger.db.pre-statement-periods-20261024.bak"


def test_the_app_will_not_start_on_a_book_from_before_the_step(old, monkeypatch):
    before = dump(old)
    monkeypatch.setattr(db, "DB_PATH", old)

    with pytest.raises(db.DatabaseNotConverted, match="convert_statement_periods.py"):
        db.init_db()

    assert dump(old) == before


def test_a_converted_book_has_the_shape_of_a_new_one(old, tmp_path):
    new = tmp_path / "new.db"
    conn = sqlite3.connect(str(new))
    conn.executescript(db.SCHEMA_PATH.read_text())
    conn.close()

    convert(old)

    shape = "SELECT name, type, \"notnull\", dflt_value FROM pragma_table_info('statements')"
    triggers = "SELECT name, sql FROM sqlite_master WHERE name LIKE 'history_statements_%' ORDER BY name"
    assert query(old, shape) == query(new, shape)
    assert query(old, triggers) == query(new, triggers)
    assert "period_start" in history.table_triggers("statements")[0]


def test_the_command(old, capsys, tmp_path):
    assert convert_statement_periods.main([str(old)]) == 0
    out = capsys.readouterr().out
    assert "step statement-periods: applied" in out
    assert "month records from the 1st: 3; loan interest from the figure before: 1; " \
           "printed, read from the previous closing: 3; printed, read from the 1st: 2" in out
    assert convert_statement_periods.main([str(old)]) == 0
    assert "already-applied" in capsys.readouterr().out
    assert convert_statement_periods.main([]) == 2
    assert convert_statement_periods.main([str(tmp_path / "missing.db")]) == 1


def test_a_changed_row_fails_the_steps_invariant(old):
    """The step's own measure holds every row as it was: a changed row fails it."""
    conn = sqlite3.connect(str(old))
    try:
        measured = convert_statement_periods.STEP.measure(conn)
        conn.execute("UPDATE transactions SET date = '2026-03-03' WHERE id = 1")
        moved = convert_statement_periods.STEP.measure(conn)
    finally:
        conn.close()

    problems = convert_statement_periods.STEP.invariant(measured, moved)
    assert problems and "transaction rows changed" in problems[0]


# --- review of PR 30 ----------------------------------------------------------


def day_of(n: int, day: int) -> str:
    y, m = month_back(n)
    return f"{y:04d}-{m:02d}-{day:02d}"


def confirmed(client):
    """Upload and confirm the next stand-in statement; the confirm's response."""
    resp = client.post("/api/import/upload", data={"files": (io.BytesIO(b"stand-in"), "sample.csv")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    preview = resp.get_json()
    return client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [{"account": g["account"], "transactions": g["transactions"], "statements": g["statements"]}
                   for g in preview["groups"]],
    })


def printed_run(stand_in, client, closings) -> None:
    """Printed statements of the card, one upload each: (closing day, opening
    day or None). Each prints one row on its closing day; the balances run on."""
    owed = 0
    for closing, opening in closings:
        stand_in([(closing, "SAMPLE GROCER", 1_000)], opening_minor=owed, closing_minor=owed - 1_000,
                 opening_date=opening, closing_date=closing)
        imported(client)
        owed -= 1_000


def card_id(conn) -> int:
    return conn.execute("SELECT id FROM accounts WHERE name = ?", (CARD,)).fetchone()[0]


def read_start(conn, closing: str) -> str:
    """The first day fin reads for the card's statement closing on `closing`."""
    import statement_period

    row = conn.execute("SELECT account_id, statement_date, period_start, printed FROM statements"
                       " WHERE account_id = ? AND statement_date = ?", (card_id(conn), closing)).fetchone()
    return statement_period.start(conn, *row)


def held_and_missing(client, conn) -> tuple[list[str], list[str]]:
    """The months 5 to 1 back the coverage view marks imported, and those
    missing_months reports."""
    shown = [key(n) for n in range(5, 0, -1)]
    coverage = client.get("/api/statements/coverage?months=6").get_json()
    row = coverage["matrix"][str(card_id(conn))]
    return [m for m in shown if row[m]["imported"]], missing.missing_months(conn, [card_id(conn)], shown, date.today())


def test_one_statement_missing_from_a_16th_to_15th_cycle_is_reported(stand_in, client, conn):
    """Probe 1: a period reaching into a month does not hold it; the 15th of
    three months back never imported is missing, as it was before periods."""
    printed_run(stand_in, client, [(day_of(n, 15), day_of(n + 1, 16)) for n in (5, 4, 2, 1)])

    held, gaps = held_and_missing(client, conn)

    assert held == [key(5), key(4), key(2), key(1)]
    assert gaps == [key(3)]


def test_one_statement_missing_is_reported_when_the_parser_states_no_opening_day(stand_in, client, conn):
    """Probe 2: a card closing on the 15th with no opening day, the statement
    of three months back imported and then undone."""
    printed_run(stand_in, client, [(day_of(n, 15), None) for n in (5, 4, 3, 2, 1)])
    entry = conn.execute(
        "SELECT entry_id FROM change_rows WHERE tbl = 'statements' AND op = 'insert'"
        " AND json_extract(after, '$.statement_date') = ?", (day_of(3, 15),)
    ).fetchone()[0]

    assert client.post(f"/api/history/{entry}/undo").status_code == 200

    held, gaps = held_and_missing(client, conn)
    assert held == [key(5), key(4), key(2), key(1)]
    assert gaps == [key(3)]
    # Its neighbour reads its start again: no statement closes near enough
    # before it to tell, so from the 1st.
    assert read_start(conn, day_of(2, 15)) == day_of(2, 1)


def test_one_statement_missing_from_a_cycle_closing_on_the_1st_is_reported(stand_in, client, conn):
    printed_run(stand_in, client, [(day_of(n, 1), day_of(n + 1, 2)) for n in (5, 4, 2, 1)])

    held, gaps = held_and_missing(client, conn)

    assert held == [key(5), key(4), key(2), key(1)]
    assert gaps == [key(3)]


def test_a_start_read_from_the_closing_before_does_not_depend_on_import_order(stand_in, client, conn):
    """Probe 3: the later statement imported first still starts the day after
    the earlier one's closing, as the conversion tells it."""
    printed_run(stand_in, client, [(day_of(2, 15), None), (day_of(3, 15), None)])

    assert read_start(conn, day_of(2, 15)) == day_of(3, 16)
    assert read_start(conn, day_of(3, 15)) == day_of(3, 1)
    assert conn.execute("SELECT COUNT(*) FROM statements WHERE period_start IS NOT NULL").fetchone()[0] == 0


def test_a_start_reads_the_previous_statement_balance_as_the_balance_sheet_does(stand_in, client, conn):
    """The previous statement closed on the later of the previous printed
    closing and the previous statement balance (balance_sheet.ROW_DAY): a row
    the statement printed from before it counts on the day its period starts."""
    import balance_sheet

    printed_run(stand_in, client, [(day_of(4, 14), None)])
    conn.execute("INSERT INTO anchors (account_id, date, amount, source) VALUES (?, ?, -1000, 'statement')",
                 (card_id(conn), day_of(3, 14)))
    conn.commit()
    stand_in([(day_of(3, 10), "SAMPLE CAFE", 1_000)], opening_minor=-1_000, closing_minor=-2_000,
             closing_date=day_of(2, 14))
    imported(client)

    counted = conn.execute(f"SELECT {balance_sheet.ROW_DAY} FROM transactions t"
                           " JOIN statements s ON s.id = t.statement_id WHERE t.date = ?", (day_of(3, 10),))
    assert read_start(conn, day_of(2, 14)) == day_of(3, 15) == counted.fetchone()[0]


@pytest.mark.parametrize("opening", ["2026-02-30", "2025-7-1", "31/07/2026"])
def test_an_opening_day_that_is_not_a_real_day_refuses_the_import(stand_in, client, conn, opening):
    """As a closing day that is not one does: nothing is written, and the
    coverage view still answers."""
    stand_in([(last_of(2)[:8] + "12", "SAMPLE CAFE", 2_000)],
             opening_minor=0, closing_minor=-2_000, opening_date=opening, closing_date=last_of(2))

    resp = confirmed(client)

    assert resp.status_code == 400
    assert "YYYY-MM-DD" in resp.get_json()["error"]
    assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 0
    assert client.get("/api/statements/coverage?months=6").status_code == 200


def test_a_conversion_records_nothing_into_an_entry_the_book_left_open(old, monkeypatch):
    """A book copied while a request held its entry open keeps it open. The
    conversion is no change of the operator's: none of its writes is in an
    entry once fin starts, so no Undo takes the starts back."""
    import app as fin_app

    conn = sqlite3.connect(str(old))
    history.open_entry(conn, history.VIA_APP, history.APP_ACTOR)
    conn.close()

    convert(old)
    monkeypatch.setattr(db, "DB_PATH", old)
    db.invalidate_rules_cache()
    db.init_db()
    fin_app.app.config["TESTING"] = True
    with fin_app.app.test_client() as client:
        for shown in client.get("/api/history?limit=500").get_json()["entries"]:
            client.post(f"/api/history/{shown['id']}/undo")

    assert query(old, "SELECT COUNT(*) FROM change_rows WHERE tbl = 'statements'") == [(0,)]
    assert query(old, "SELECT COUNT(*) FROM statements WHERE printed = 0 AND period_start IS NULL") == [(0,)]
    db.invalidate_rules_cache()
