"""Release 2, ten times (personal-ledger 23, ticket 46; spec case 55).

"On the check book copied ten times (about 1,300 rows, 2,000 expectations,
3,000 set-offs), Back for… opens with one read, the Queue's payback
suggestions are one read for the lane, and Books' sections one read each."

The check book here is one copy of case 48's set (tests/test_books_expect.py:
the AIRBNB fixture, cases 1, 2, 9, 9a, 10, 11, 17, 19, 40, 41, 42) with the
volume the spec names per tenth: about 130 rows, 200 expectations and 300
set-offs, and two payments still open for the Queue. It is read through the
app's own routes once, then copied nine more times into the same database and
read again: every route runs exactly the statements it ran on the one copy
(one read per drawer, lane and section, never one per item), and the answers
are ten times as long, so the ten-fold book is the one that was read.

The lanes each pinned their own half (tests/test_expect_page.py,
test_payback_queue.py, test_books_expect.py) on their own functions; this is
the release's read of the three surfaces through their routes, on one book.
Every name and figure is invented; nothing opens the real database.
"""

from __future__ import annotations

import pytest

import db
from expect_fixtures import Book, check_book, entry, row
from test_books_expect import case_48

MONTH = "2026-10"
DINNERS = 56        # per copy: a marked dinner and the payment set against it, plus four opening figures
OPENINGS = 135      # per copy: opening figures, about 1.7 set-offs each
COPIES = 10


def one_copy(conn, b: Book, n: int) -> dict:
    """One tenth of the ten-fold book. `n` tells the copies apart."""
    tag = f" #{n}" if n else ""
    ids = case_48(conn, b, rates=(n == 0), tag=tag).ids
    people = list(b.people.values())
    with entry(conn, f"Copy {n} of the check book"):
        openings = []
        for i in range(OPENINGS):
            e = conn.execute(
                "INSERT INTO expectation (person_id, opening_date, direction, amount_minor, currency, source) "
                "VALUES (?, '2026-01-01', 'back', 9000, 'SGD', 'converted')", (people[i % len(people)],)).lastrowid
            conn.execute("INSERT INTO expectation_name (expectation_id, person_id) VALUES (?, ?)",
                         (e, people[i % len(people)]))
            openings.append(e)
        k = 0
        for i in range(DINNERS):
            day = f"2026-{9 + i % 2:02d}-{1 + i % 27:02d}"
            dinner = row(conn, b, b.everyday_card, day, f"SAMPLE DINNER {i}{tag}", 30000, type_name="Dining")
            e = conn.execute("INSERT INTO expectation (row_id, direction, amount_minor, currency) "
                             "VALUES (?, 'back', 20000, 'SGD')", (dinner,)).lastrowid
            conn.execute("INSERT INTO expectation_name (expectation_id, person_id) VALUES (?, ?)",
                         (e, people[i % len(people)]))
            paid = row(conn, b, b.savings, day, f"PAYNOW FROM SAMPLE {i}{tag}", -7000, flow="review")
            conn.execute("INSERT INTO set_off (payment_row_id, expectation_id, amount_minor, item_amount_minor) "
                         "VALUES (?, ?, 3000, 3000)", (paid, e))
            for _ in range(4):
                conn.execute("INSERT INTO set_off (payment_row_id, expectation_id, amount_minor, item_amount_minor) "
                             "VALUES (?, ?, 1000, 1000)", (paid, openings[k % OPENINGS]))
                k += 1
    # Two payments still open, for Back for… and the Queue: Jay's S$ 40.00 (GOLDEN
    # VILLAGE fits it, case 32's shape) and an unnamed S$ 45.00 (case 30).
    ids["jay_open"] = row(conn, b, b.savings, "2026-10-18", f"PAYNOW FROM JAY TAN 0533{tag}", -4000, flow="review")
    ids["inward"] = row(conn, b, b.savings, "2026-10-07", f"INWARD PAYNOW 9921{tag}", -4500, flow="review")
    return ids


def volume(conn) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("transactions", "expectation", "set_off")}


@pytest.fixture
def traced(monkeypatch):
    """Every statement the app runs through db.get_connection, in order."""
    seen: list[str] = []
    real = db.get_connection

    def tracing():
        c = real()
        c.set_trace_callback(seen.append)
        return c

    monkeypatch.setattr(db, "get_connection", tracing)
    return seen


def reads(client, seen: list, url: str) -> tuple[dict, dict]:
    """GET one route: its answer, and the statements it ran (all of them, the
    reads, and the reads of the expect-back records)."""
    seen.clear()
    resp = client.get(url)
    assert resp.status_code == 200, (url, resp.get_json())
    selects = [q for q in seen if q.lstrip().upper().startswith(("SELECT", "WITH"))]
    return resp.get_json(), {
        "statements": len(seen),
        "reads": len(selects),
        "record reads": sum(any(t in q for t in ("expectation", "set_off", "let_go")) for q in selects),
    }


def read_all(client, seen, ids) -> dict:
    back_for, back_for_n = reads(client, seen, f"/api/transactions/{ids['jay_open']}/back-for")
    queue, queue_n = reads(client, seen, "/api/queue/paybacks")
    sheet, sheet_n = reads(client, seen, f"/api/balance-sheet?month={MONTH}")
    sections = {s["key"]: s for s in sheet["expect"]["sections"]}
    return {
        "counts": {"Back for…": back_for_n, "the Queue's paybacks": queue_n, "Books": sheet_n},
        "sizes": {
            "Back for… items": len(back_for["items"]),
            "Queue payments": len(queue["paybacks"]),
            "Queue suggestions": sum(len(p["suggestions"]) for p in queue["paybacks"]),
            **{f"Books {k} lines": len(s["lines"]) for k, s in sections.items()},
        },
        "totals": {k: s["total_minor"] for k, s in sections.items()},
    }


def test_case_55_back_for_the_queue_and_books_read_the_same_at_ten_times(client, conn, traced):
    b = check_book(conn)
    ids = one_copy(conn, b, 0)
    tenth = volume(conn)
    assert tenth == {"transactions": 129, "expectation": 200, "set_off": 288}
    once = read_all(client, traced, ids)

    for n in range(1, COPIES):
        one_copy(conn, b, n)
    assert volume(conn) == {t: COPIES * v for t, v in tenth.items()}   # 1,290 rows, 2,000 items, 2,880 set-offs
    tenfold = read_all(client, traced, ids)

    # One read per drawer, lane and section: the statements run do not grow with the book.
    assert tenfold["counts"] == once["counts"], (once["counts"], tenfold["counts"])
    # And the ten-fold book is what was read: every list ten times as long, every section total ten times.
    assert once["sizes"]["Back for… items"] > 150
    assert once["sizes"]["Queue suggestions"] >= 1
    for name, size in once["sizes"].items():
        if name == "Queue suggestions":
            # Each of the ten open Jay payments now fits each copy's GOLDEN VILLAGE.
            assert tenfold["sizes"][name] == COPIES * COPIES * size, name
        else:
            assert tenfold["sizes"][name] == COPIES * size, name
    assert tenfold["totals"] == {k: COPIES * v for k, v in once["totals"].items()}
    # Books is built twice by the route (at the month's end, and at its start for the
    # month check), each within the sections' own bound of 8 statements (test_books_expect):
    # a fixed handful of record reads, never one per item.
    assert once["counts"]["Books"]["record reads"] <= 2 * 8, once["counts"]["Books"]
