"""The expect-back step's plan, read-only (personal-ledger 23, review fixes).

A step that refuses on the real book keeps fin from starting, and the remedy
(one figure per person, a row moved to the Household's book) is in fin. So
the operator reads the plan first, on a copy of the book:

    python convert_expect_back.py --plan <book>
    python convert_all.py --plan <book>

It opens the book read-only, writes nothing, takes no backup, and prints what
the step would make, every refusal with the person and the rows that cause
it, and every row that would be left with money to label, with the total per
person (the one change to net worth the spec declares, Σ unplaced).

Every name and figure is invented; each book is built under tmp_path.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import book_type
import convert_all
import convert_expect_back
from test_convert_expect_back import OLD_SCHEMA, _account, _row, book, open_book, seeded  # noqa: F401  (book is a fixture)


def fingerprint(path: Path) -> tuple[str, list[str]]:
    """The book's bytes, and every file beside it (a backup, a -wal or -shm)."""
    return hashlib.sha256(path.read_bytes()).hexdigest(), sorted(p.name for p in path.parent.iterdir())


def plan(path: Path, capsys, command=convert_expect_back.main) -> tuple[int, str]:
    code = command(["--plan", str(path)])
    return code, capsys.readouterr().out


def would_refuse(path: Path) -> dict:
    """Cases 40-43b, plus a second figure for Sam and a row naming Ravi in
    the company's book: two refusals."""
    ids = seeded(path)
    conn = open_book(path)
    conn.execute("INSERT INTO anchors (account_id, date, amount, source) VALUES (?, '2026-04-01', 50000, 'supplied')",
                 (ids["Sam"],))
    conn.execute("UPDATE transactions SET book = 'Moom' WHERE id = ?", (ids["ravi_out"],))
    conn.commit()
    conn.close()
    return ids


def test_plan_on_a_book_that_would_refuse_prints_each_refusal_and_writes_nothing(book, capsys):
    would_refuse(book)
    before = fingerprint(book)

    code, out = plan(book, capsys)

    assert code == 1, out
    assert fingerprint(book) == before  # byte-identical; no backup, no -wal or -shm
    assert "nothing was written" in out
    assert "the step would refuse: 1 person has more than one anchor" in out
    assert "  Sam: 2026-01-01 SGD 800.00; 2026-04-01 SGD 500.00" in out
    assert "the step would refuse: 1 row naming a person is in a company's book" in out
    assert "  Ravi: 2026-06-03  TRANSFER TO RAVI  SGD +2,000.00  in Moom's book" in out


def test_plan_on_a_book_that_converts_prints_the_counts_and_writes_nothing(book, capsys):
    seeded(book)
    before = fingerprint(book)

    code, out = plan(book, capsys)

    assert code == 0, out
    assert fingerprint(book) == before
    assert "persons: 4" in out
    assert "expectations: 5 (opening figures: 1)" in out
    assert "set-offs: 4" in out
    assert "would refuse" not in out
    # Planning is not converting: the step still reads as pending.
    conn = open_book(book)
    try:
        assert not convert_expect_back.STEP.is_applied(conn)
    finally:
        conn.close()


def test_plan_on_a_book_from_before_expect_back_reads_it_as_the_chain_would_leave_it(tmp_path, capsys):
    # The production book's shape: change-history has not made the records
    # yet. The plan runs the steps before expect-back on a copy in memory.
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA.read_text())
    book_type.seed_types(conn)
    conn.commit()
    conn.close()
    seeded(path)
    before = fingerprint(path)

    code, out = plan(path, capsys)

    assert code == 0, out
    assert fingerprint(path) == before
    assert "change-history" in out
    assert "expectations: 5 (opening figures: 1)" in out


def test_convert_all_plan_names_the_steps_to_run_and_prints_the_plan(tmp_path, capsys):
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA.read_text())
    book_type.seed_types(conn)
    conn.commit()
    conn.close()
    would_refuse(path)
    before = fingerprint(path)

    code, out = plan(path, capsys, convert_all.main)

    assert code == 1, out
    assert fingerprint(path) == before
    [waiting] = [line for line in out.splitlines() if line.startswith("steps still to run, in order: ")]
    assert waiting.endswith("change-history, expect-back, label-source"), waiting
    assert "  Sam: 2026-01-01 SGD 800.00; 2026-04-01 SGD 500.00" in out


def test_plan_on_a_book_with_no_file_writes_nothing(tmp_path, capsys):
    code, out = plan(tmp_path / "missing.db", capsys)
    assert code == 1
    assert "no database at" in out
    assert list(tmp_path.iterdir()) == []


# --- the rows left to label, listed (Σ unplaced) ---------------------------------


def jay_pays_back_more(path: Path) -> None:
    """FROM JAY S$ 100.00 in on 1 Mar (a loan from him), then TO JAY S$ 500.00
    out on 5 Mar: S$ 100.00 sets off, S$ 400.00 is left on the 5 Mar row."""
    conn = open_book(path)
    savings = _account(conn, "Everyday savings", "bank")
    jay = _account(conn, "Jay", "person")
    _row(conn, savings, "2026-03-01", "FROM JAY", -10000, jay)
    _row(conn, savings, "2026-03-05", "TO JAY", 50000, jay)
    conn.commit()
    conn.close()


LISTED = "money left to label, row by row (money out +, money in -):"
TOTALS = "total left to label, per person:"


def test_plan_lists_each_row_left_to_label_and_the_total_per_person(book, capsys):
    jay_pays_back_more(book)

    code, out = plan(book, capsys)

    assert code == 0, out
    lines = out.splitlines()
    assert "1 row with money left to label" in lines
    at = lines.index(LISTED)
    assert lines[at + 1:at + 4] == [
        "  2026-03-05  TO JAY  Jay  SGD +400.00 left of SGD +500.00",
        TOTALS,
        "  Jay  SGD +400.00",
    ]


def test_plan_totals_each_person_on_their_own(book, capsys):
    seeded(book)  # Dev: S$ 100.00 left on the 1 Jun row, money in
    jay_pays_back_more(book)

    code, out = plan(book, capsys)

    lines = out.splitlines()
    assert "2 rows with money left to label" in lines
    at = lines.index(LISTED)
    assert lines[at + 1:at + 6] == [
        "  2026-03-05  TO JAY  Jay  SGD +400.00 left of SGD +500.00",
        "  2026-06-01  FROM DEV  Dev  SGD -100.00 left of SGD -600.00",
        TOTALS,
        "  Dev  SGD -100.00",
        "  Jay  SGD +400.00",
    ]


def test_the_step_itself_lists_the_rows_left_to_label(book, capsys):
    jay_pays_back_more(book)

    code = convert_expect_back.main([str(book)])
    out = capsys.readouterr().out

    assert code == 0, out
    assert "  2026-03-05  TO JAY  Jay  SGD +400.00 left of SGD +500.00" in out
    assert "  Jay  SGD +400.00" in out


def test_with_nothing_left_to_label_no_list_is_printed(book, capsys):
    conn = open_book(book)
    savings = _account(conn, "Everyday savings", "bank")
    kim = _account(conn, "Kim", "person")
    _row(conn, savings, "2026-02-02", "TO KIM", 10000, kim)
    conn.commit()
    conn.close()

    code, out = plan(book, capsys)

    assert "0 rows with money left to label" in out
    assert LISTED not in out and TOTALS not in out
