"""Personal-ledger 42: the Queue offers a row for a waiting payment (E6's waiting half, D6's card).

Lena pays S$ 100.00 for the BBQ before it is charged and it is left waiting for its row
(ticket 40's Nothing yet). When a statement brings rows in, the lane read
GET /api/queue/paybacks offers, for each waiting set-off, the rows that fit E6's waiting
signals: a money-out spending row in the payment's currency with no expectation, dated on or
after the payment and within 90 days of it, its amount at least the waiting amount, from an
import after the payment was set waiting. Nearest the payment first, a row whose text holds a
name on the set-off first of all; every fit offered. ✓ posts ticket 40's attach route; nothing
links without a tap.

Seam 1 of the 23 spec ("E6 through its route: 17, 17a, 17b"): the Flask test client on a
temporary database, figures in and out in whole minor units, the write one change undone from
Changes. The D6 card runs under node (static/expect-queue.js after the shells it builds on,
app.js's money and day helpers lifted out of the real file, the script on stdin), and its
request is posted to the real route.

Every name and figure is invented; the book is the case list's October story.
"""

from __future__ import annotations

import json

import pytest

import expect_back
import payback_queue
from expect_fixtures import mark, october, row
from test_expect_contract import load
from test_payback_queue import needs_node, run, text

BBQ = "for the BBQ, when it is charged"
BEFORE = "2026-10-01 08:00:00"      # when the October story's rows came in
SET_WAITING = "2026-10-17 12:00:00"  # when Lena's payment was set waiting
LATER = "2026-10-25 09:00:00"        # the next statement's import


# ---------------------------------------------------------------------------
# The book
# ---------------------------------------------------------------------------

@pytest.fixture
def book(client, conn):
    """The October story with Lena's S$ 100.00 for the BBQ (17 Oct) left waiting, named for
    Lena, through ticket 40's route. Every row so far came in before it was set waiting."""
    o = october(conn)
    b = o.book
    o.lena_bbq = row(conn, b, b.savings, "2026-10-17", "PAYNOW FROM LENA 2210", -10000, flow="review")
    conn.execute("UPDATE transactions SET created_at = ?", (BEFORE,))
    conn.commit()
    resp = client.post(f"/api/transactions/{o.lena_bbq}/waiting", json={"person_id": o.people["Lena"], "note": BBQ})
    assert resp.status_code == 200, resp.get_json()
    o.waiting = resp.get_json()["set_off"]["id"]
    conn.execute("UPDATE set_off SET made_at = ? WHERE id = ?", (SET_WAITING, o.waiting))
    conn.commit()
    return o


def arrives(conn, o, account_id, day, description, amount_minor, *, imported=LATER, **kw) -> int:
    """A row brought in by an import at `imported`."""
    row_id = row(conn, o.book, account_id, day, description, amount_minor, **kw)
    conn.execute("UPDATE transactions SET created_at = ? WHERE id = ?", (imported, row_id))
    conn.commit()
    return row_id


def lane(client) -> dict:
    resp = client.get("/api/queue/paybacks")
    assert resp.status_code == 200, resp.get_json()
    assert "X-Fin-Change" not in resp.headers
    f = resp.get_json()
    check_shape(f)
    return f


def check_shape(f: dict) -> None:
    """The fixture's shape (tests/fixtures/expect/queue_paybacks.json)."""
    fixture = load("queue_paybacks")["waiting_offers"][0]
    for w in f["waiting_offers"]:
        assert set(w) == set(fixture)
        assert set(w["payment"]) == set(fixture["payment"])
        assert w["person"] is None or set(w["person"]) == set(fixture["person"])
        for r in w["rows"]:
            assert set(r) == set(fixture["rows"][0])
            assert r["amount_minor"] >= w["amount_minor"] and r["date"] >= w["payment"]["date"]
            assert r["currency"] == w["currency"]


def offer(f: dict, set_off_id: int) -> dict:
    found = [w for w in f["waiting_offers"] if w["set_off_id"] == set_off_id]
    assert len(found) == 1
    return found[0]


def offered(client, set_off_id: int) -> list[tuple[str, str]]:
    return [(r["description"], r["date"]) for r in offer(lane(client), set_off_id)["rows"]]


def entries(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0]


def records(conn) -> tuple:
    return (conn.execute("SELECT * FROM expectation ORDER BY id").fetchall(),
            conn.execute("SELECT * FROM set_off ORDER BY id").fetchall(), entries(conn))


def own(conn, row_id) -> int:
    return expect_back.parts(conn, [row_id])[row_id]["own_minor"]


# ---------------------------------------------------------------------------
# Case 17 (offer half): the BBQ row arrives and is offered; ✓ attaches it
# ---------------------------------------------------------------------------

def test_case_17_the_row_that_arrives_is_offered(client, conn, book):
    b = book.book
    cold = arrives(conn, book, b.everyday_card, "2026-10-21", "COLD STORAGE", 26000, type_name="Groceries")
    w = offer(lane(client), book.waiting)
    assert w == {
        "set_off_id": book.waiting,
        "person": {"account_id": book.people["Lena"], "name": "Lena"},
        "amount_minor": 10000, "currency": "SGD",
        # All of the payment waits: nothing is left on it to place.
        "payment": {"id": book.lena_bbq, "description": "PAYNOW FROM LENA 2210", "date": "2026-10-17",
                    "account_name": "Everyday savings", "amount_minor": -10000, "currency": "SGD",
                    "left_minor": 0},
        "rows": [{"id": cold, "description": "COLD STORAGE", "date": "2026-10-21",
                  "account_name": "Everyday card", "amount_minor": 26000, "currency": "SGD"}],
    }
    # The payment is not in the paybacks: it has nothing left to place.
    assert book.lena_bbq not in [p["payment"]["id"] for p in lane(client)["paybacks"]]


def test_case_17_before_any_row_arrives_nothing_is_offered(client, conn, book):
    # Case 17c's start: the waiting set-off is in the lane with no row to offer.
    assert offer(lane(client), book.waiting)["rows"] == []


@needs_node
def test_case_17_check_attaches_through_the_attach_route_as_one_change(client, conn, book):
    b = book.book
    cold = arrives(conn, book, b.everyday_card, "2026-10-21", "COLD STORAGE", 26000, type_name="Groceries")
    w = offer(lane(client), book.waiting)
    words = text(run(f"Q.stepHTML({json.dumps({'paybacks': [], 'waiting_offers': [w]})}, [])"))
    assert "Back for… 1" in words
    assert "PAYNOW FROM LENA 2210" in words
    assert "Its row: COLD STORAGE, 21 Oct · S$ 260?" in words
    assert "Expect back S$ 100 and set Lena's S$ 100 against it?" in words
    assert "✓ Set it against COLD STORAGE Another…" in words
    body = run(f"Q.attachRequest({json.dumps(w)}, {json.dumps(w['rows'][0])})")
    assert body == {"row_id": cold}

    before = entries(conn)
    resp = client.post(f"/api/set-offs/{book.waiting}/attach", json=body)
    assert resp.status_code == 200, resp.get_json()
    assert entries(conn) == before + 1
    x = resp.get_json()["expectation"]
    assert (x["amount_minor"], x["direction"], x["state"]) == (10000, "back", "closed")
    assert expect_back.open_minor(conn, x["id"]) == 0
    assert resp.get_json()["waiting"] is None
    assert own(conn, cold) == 16000           # the row's own part, S$ 160.00 Groceries
    assert own(conn, book.lena_bbq) == 0      # still wholly placed, now against the BBQ row
    # Nothing waits any more: the offer has left the lane.
    assert lane(client)["waiting_offers"] == []

    # One change, undone from Changes: Lena's S$ 100 waits again and the row is offered again.
    assert client.post(f"/api/history/{int(resp.headers['X-Fin-Change'])}/undo").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM expectation WHERE row_id = ?", (cold,)).fetchone()[0] == 0
    assert own(conn, cold) == 26000
    again, = lane(client)["waiting_offers"]
    assert again["amount_minor"] == 10000 and [r["id"] for r in again["rows"]] == [cold]


# ---------------------------------------------------------------------------
# Case 17a (offer half): a smaller row is not offered
# ---------------------------------------------------------------------------

def test_case_17a_a_smaller_row_is_not_offered(client, conn, book):
    b = book.book
    arrives(conn, book, b.rewards_card, "2026-10-21", "COLD STORAGE", 8000, type_name="Groceries")
    assert offered(client, book.waiting) == []    # S$ 80 < S$ 100 waiting


def test_case_17a_after_a_hand_split_the_rest_is_offered_rows_its_size(client, conn, book):
    """Attached by hand to the S$ 80 row (ticket 40's split), S$ 20 still waits and keeps the
    time it was set waiting: a S$ 30 row from a later import is offered for it."""
    b = book.book
    small = arrives(conn, book, b.rewards_card, "2026-10-21", "COLD STORAGE", 8000, type_name="Groceries")
    rest = client.post(f"/api/set-offs/{book.waiting}/attach", json={"row_id": small}).get_json()["waiting"]
    snack = arrives(conn, book, b.rewards_card, "2026-10-23", "SNACK BAR", 3000, type_name="Dining")
    w = offer(lane(client), rest["id"])
    assert w["amount_minor"] == 2000
    assert [r["id"] for r in w["rows"]] == [snack]


# ---------------------------------------------------------------------------
# Case 17b: two rows fit; nearest first, a row naming Lena first of all
# ---------------------------------------------------------------------------

def test_case_17b_two_rows_both_offered_nearest_first(client, conn, book):
    b = book.book
    fair = arrives(conn, book, b.everyday_card, "2026-10-22", "FAIRPRICE", 14000, type_name="Groceries")
    cold = arrives(conn, book, b.everyday_card, "2026-10-21", "COLD STORAGE", 26000, type_name="Groceries")
    assert [r["id"] for r in offer(lane(client), book.waiting)["rows"]] == [cold, fair]


def test_case_17b_a_row_naming_lena_ranks_first_of_all(client, conn, book):
    b = book.book
    cold = arrives(conn, book, b.everyday_card, "2026-10-21", "COLD STORAGE", 26000, type_name="Groceries")
    fair = arrives(conn, book, b.everyday_card, "2026-10-22", "FAIRPRICE", 14000, type_name="Groceries")
    named = arrives(conn, book, b.rewards_card, "2026-11-02", "BBQ PIT LENA HOUSE", 12000, type_name="Dining")
    assert [r["id"] for r in offer(lane(client), book.waiting)["rows"]] == [named, cold, fair]


def test_case_17b_nothing_links_without_a_tap(client, conn, book):
    b = book.book
    arrives(conn, book, b.everyday_card, "2026-10-21", "COLD STORAGE", 26000, type_name="Groceries")
    arrives(conn, book, b.everyday_card, "2026-10-22", "FAIRPRICE", 14000, type_name="Groceries")
    before = records(conn)
    for _ in range(2):
        assert len(offer(lane(client), book.waiting)["rows"]) == 2
    assert records(conn) == before


@needs_node
def test_case_17b_the_card_draws_a_check_for_each_with_another(client, conn, book):
    b = book.book
    cold = arrives(conn, book, b.everyday_card, "2026-10-21", "COLD STORAGE", 26000, type_name="Groceries")
    fair = arrives(conn, book, b.everyday_card, "2026-10-22", "FAIRPRICE", 14000, type_name="Groceries")
    w = offer(lane(client), book.waiting)
    html = run(f"Q.stepHTML({json.dumps({'paybacks': [], 'waiting_offers': [w]})}, [])")
    words = text(html)
    assert "Its row, one of these? Two fit:" in words
    assert "Expect back S$ 100 and set Lena's S$ 100 against it?" in words
    assert words.index("✓ COLD STORAGE, 21 Oct · S$ 260") < words.index("✓ FAIRPRICE, 22 Oct · S$ 140")
    assert "Another…" in words
    # Another… opens ticket 40's attach-by-hand drawer for this set-off.
    assert f'data-xb-waiting="{book.waiting}"' in html
    assert [run(f"Q.attachRequest({json.dumps(w)}, {json.dumps(r)})") for r in w["rows"]] == [
        {"row_id": cold}, {"row_id": fair}]
    # The second ✓, posted: FAIRPRICE carries the expectation, COLD STORAGE is untouched.
    resp = client.post(f"/api/set-offs/{book.waiting}/attach", json={"row_id": fair})
    assert resp.status_code == 200, resp.get_json()
    assert (own(conn, fair), own(conn, cold)) == (4000, 26000)


# ---------------------------------------------------------------------------
# The waiting signals, each on its own
# ---------------------------------------------------------------------------

def test_only_rows_that_fit_every_signal_are_offered(client, conn, book):
    b, p = book.book, book.people
    fits = arrives(conn, book, b.everyday_card, "2026-10-21", "COLD STORAGE", 26000, type_name="Groceries")
    on_day_0 = arrives(conn, book, b.everyday_card, "2026-10-17", "SAME DAY MART", 10000, type_name="Groceries")
    on_day_90 = arrives(conn, book, b.everyday_card, "2027-01-15", "DAY NINETY MART", 10000, type_name="Groceries")
    # Left out, one signal each:
    arrives(conn, book, b.everyday_card, "2026-10-16", "THE DAY BEFORE", 26000, type_name="Groceries")
    arrives(conn, book, b.everyday_card, "2027-01-16", "DAY NINETY ONE", 26000, type_name="Groceries")
    arrives(conn, book, b.rupee_card, "2026-10-21", "RUPEE MART", 2600000, type_name="Groceries")
    marked = arrives(conn, book, b.everyday_card, "2026-10-21", "MARKED ALREADY", 26000, type_name="Dining")
    mark(conn, marked, 5000, names=(p["Jay"],))
    arrives(conn, book, b.everyday_card, "2026-10-21", "IMPORTED BEFORE", 26000, type_name="Groceries",
            imported="2026-10-17 11:59:59")
    arrives(conn, book, b.everyday_card, "2026-10-21", "MOOM SUPPLIER", 26000, type_name="Dining", book_name="Moom")
    arrives(conn, book, b.savings, "2026-10-21", "LOAN INSTALMENT", 90000, flow="movement", other_side=b.home_loan)
    arrives(conn, book, b.savings, "2026-10-21", "PAYMENT TO REWARDS CARD", 40000, flow="payment",
            other_side=b.rewards_card)
    arrives(conn, book, b.savings, "2026-10-21", "PAYNOW TO RAVI", 30000, flow="movement", other_side=p["Ravi"])
    arrives(conn, book, b.savings, "2026-10-21", "UNLABELLED OUT", 30000, flow="review")
    arrives(conn, book, b.savings, "2026-10-21", "PAYNOW FROM JAY", -30000, flow="review")
    assert [r["id"] for r in offer(lane(client), book.waiting)["rows"]] == [on_day_0, fits, on_day_90]


def test_a_row_already_carrying_a_set_off_offers_only_its_room(client, conn, book):
    """A money-out row partly placed against something to pay back has less room than its
    amount: offered only when its room still holds the whole waiting amount."""
    b, p = book.book, book.people
    debt = mark(conn, row(conn, b, b.savings, "2026-09-01", "FROM PARENTS", -400000, flow="income"),
                400000, names=(p["Parents"],))
    roomy = arrives(conn, book, b.everyday_card, "2026-10-21", "COLD STORAGE", 26000, type_name="Groceries")
    tight = arrives(conn, book, b.everyday_card, "2026-10-22", "FAIRPRICE", 14000, type_name="Groceries")
    for paid, amount in ((roomy, 15000), (tight, 5000)):
        resp = client.post(f"/api/transactions/{paid}/set-offs", json={
            "placements": [{"expectation_id": debt, "amount": f"{amount // 100}.00"}], "waiting": None})
        assert resp.status_code == 200, resp.get_json()
    # COLD STORAGE: S$ 110 of room still holds S$ 100; FAIRPRICE: S$ 90 of room does not.
    assert [r["id"] for r in offer(lane(client), book.waiting)["rows"]] == [roomy]


def test_a_waiting_set_off_with_no_person_is_offered_unranked_by_name(client, conn, book):
    b = book.book
    inward = row(conn, b, b.savings, "2026-10-18", "INWARD PAYNOW 9921", -5000, flow="review")
    resp = client.post(f"/api/transactions/{inward}/waiting", json={})
    assert resp.status_code == 200, resp.get_json()
    anon = resp.get_json()["set_off"]["id"]
    conn.execute("UPDATE set_off SET made_at = ? WHERE id = ?", (SET_WAITING, anon))
    conn.commit()
    cold = arrives(conn, book, b.everyday_card, "2026-10-21", "COLD STORAGE", 26000, type_name="Groceries")
    w = offer(lane(client), anon)
    assert w["person"] is None and w["amount_minor"] == 5000 and [r["id"] for r in w["rows"]] == [cold]


@needs_node
def test_the_card_words_a_set_off_with_no_person():
    w = load("queue_paybacks")["waiting_offers"][0] | {"person": None}
    words = text(run(f"Q.stepHTML({json.dumps({'paybacks': [], 'waiting_offers': [w]})}, [])"))
    assert "Expect back S$ 100 and set the S$ 100 waiting against it?" in words


@needs_node
def test_a_waiting_set_off_with_no_row_draws_no_card():
    w = load("queue_paybacks")["waiting_offers"][0] | {"rows": []}
    assert run(f"Q.stepHTML({json.dumps({'paybacks': [], 'waiting_offers': [w]})}, [])") == ""


# ---------------------------------------------------------------------------
# Case 55 (lane half): still one read for the lane, whatever its size
# ---------------------------------------------------------------------------

def reads(conn) -> int:
    seen = []
    conn.set_trace_callback(lambda sql: seen.append(sql) if sql.lstrip().upper().startswith(("SELECT", "WITH")) else None)
    try:
        payback_queue.paybacks(conn)
    finally:
        conn.set_trace_callback(None)
    return len(seen)


def test_case_55_waiting_offers_add_one_read_whatever_their_number(client, conn, book):
    b = book.book
    small = reads(conn)
    for i in range(20):
        paid = row(conn, b, b.savings, f"2026-10-{1 + i:02d}", f"PAYNOW FROM LENA {i:04d}", -1000 - i, flow="review")
        assert client.post(f"/api/transactions/{paid}/waiting", json={"person_id": book.people["Lena"]}).status_code == 200
    conn.execute("UPDATE set_off SET made_at = ?", (SET_WAITING,))
    conn.commit()
    for i in range(20):
        arrives(conn, book, b.everyday_card, f"2026-10-{18 + i % 10:02d}", f"SHOP {i}", 5000 + i, type_name="Dining")
    assert len(lane(client)["waiting_offers"]) == 21
    assert reads(conn) == small <= 5
