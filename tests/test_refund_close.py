"""Personal-ledger 43: a refund closes an expectation; Unlink with dismissal;
a paid-back refund is asked (spec 23, E7).

When a linked purchase carries an open back expectation, the link's own write
places min(refund, open) as a set-off with source `refund`: the one set-off fin
makes without a tap. The rest nets in its month. A rest beyond what is left of
the purchase's own part plus its open amount is asked (keep it, or To pay
back). Unlink on a refund link removes the link and its set-off and writes a
`refund_link` dismissal, so a re-run never relinks the pair.

Seam 1 (the HTTP routes through the Flask test client, the import confirm
included), figures in and out: cases 36, 36a, 37, 37a, 37b, and the history
each write makes. Every name and figure is invented.
"""

from __future__ import annotations

import pytest

import expect_back
from expect_fixtures import harbour_set_against, let_go, mark, october, row, set_off
from test_balance_sheet import bring_in


@pytest.fixture
def o(conn):
    return october(conn)


def entries(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute("SELECT id, summary FROM change_entries ORDER BY id")]


def links(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "SELECT id, refund_row_id, purchase_row_id FROM refund_link ORDER BY id")]


def refund_set_offs(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "SELECT payment_row_id, expectation_id, amount_minor, source FROM set_off "
        "WHERE source = 'refund' ORDER BY id")]


def dismissals(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute("SELECT row_id, kind, purchase_row_id FROM dismissal ORDER BY id")]


def own(conn, row_id) -> int:
    return expect_back.parts(conn, [row_id])[row_id]["own_minor"]


def run(client):
    resp = client.post("/api/refund-links/run")
    assert resp.status_code == 200, resp.get_json()
    return resp


def undo(client, change):
    done = client.post(f"/api/history/{change}/undo")
    assert done.status_code == 200, done.get_json()


def asked(client) -> list[dict]:
    return client.get("/api/refund-links/asks").get_json()["asks"]


def paid_back(client) -> list[dict]:
    return [a for a in asked(client) if a["kind"] == "paid_back"]


def klook_refund(conn, o, on="2026-10-04", amount=3000):
    return row(conn, o.book, o.book.everyday_card, on, "KLOOK TRAVEL REFUND", -amount, flow="refund")


def harbour_refund(conn, o, on="2026-10-20", amount=5000):
    return row(conn, o.book, o.book.rewards_card, on, "HARBOUR GRILL REFUND", -amount, flow="refund")


def klook_from_kim(conn, o) -> int:
    """Case 2 and 11's KLOOK: all S$ 180.00 expected back from Kim, S$ 10.00
    of Kim's payment on it, S$ 170.00 open."""
    klook = mark(conn, o.klook, 18000, names=(o.people["Kim"],), note="concert tickets")
    set_off(conn, o.kim_7781, klook, 1000)
    return klook


def klook_closed(conn, o) -> int:
    """KLOOK wholly back from Kim: S$ 180.00 placed, own part S$ 0."""
    klook = mark(conn, o.klook, 18000, names=(o.people["Kim"],))
    kim = row(conn, o.book, o.book.savings, "2026-10-16", "PAYNOW FROM KIM L 7782", -18000, flow="review")
    set_off(conn, kim, klook, 18000)
    return klook


# --- case 36: a refund closes an expectation ----------------------------------


def test_case_36_the_link_sets_the_refund_off_against_what_is_open(client, conn, o):
    klook = klook_from_kim(conn, o)
    back = klook_refund(conn, o)
    resp = run(client)
    [(link_id, refund_id, purchase_id)] = links(conn)
    assert (refund_id, purchase_id) == (back, o.klook)
    assert refund_set_offs(conn) == [(back, klook, 3000, "refund")]
    assert expect_back.open_minor(conn, klook) == 14000
    assert own(conn, back) == 0
    assert paid_back(client) == []
    # One change: the link and its set-off.
    change = int(resp.headers["X-Fin-Change"])
    tables = {r[0] for r in conn.execute("SELECT tbl FROM change_rows WHERE entry_id = ?", (change,))}
    assert {"refund_link", "set_off"} <= tables


def test_case_36_the_close_rides_the_import_s_change(client, conn, o):
    klook = klook_from_kim(conn, o)
    before = entries(conn)
    bring_in(client, "Everyday card", [("2026-10-04", "KLOOK TRAVEL REFUND", -3000)],
             kind="card", closing_date="2026-10-31")
    [refund_id] = [r[0] for r in conn.execute(
        "SELECT id FROM transactions WHERE description = 'KLOOK TRAVEL REFUND'")]
    assert refund_set_offs(conn) == [(refund_id, klook, 3000, "refund")]
    [(entry_id, summary)] = [e for e in entries(conn) if e not in before]
    assert summary == "Imported a statement"
    assert conn.execute("SELECT COUNT(*) FROM change_rows WHERE entry_id = ? AND tbl = 'set_off'",
                        (entry_id,)).fetchone()[0] == 1
    # Undoing the import takes the set-off with it; the expectation is open again.
    undo(client, entry_id)
    assert refund_set_offs(conn) == [] and links(conn) == []
    assert expect_back.open_minor(conn, klook) == 17000


def test_a_hand_link_closes_the_expectation_the_same_way(client, conn, o):
    klook = klook_from_kim(conn, o)
    back = klook_refund(conn, o)
    conn.execute("INSERT INTO dismissal (row_id, kind, purchase_row_id) VALUES (?, 'refund_link', ?)",
                 (back, o.klook))  # so the run cannot link it first
    conn.commit()
    resp = client.post("/api/refund-links", json={"refund_row_id": back, "purchase_row_id": o.klook})
    assert resp.status_code == 200, resp.get_json()
    assert refund_set_offs(conn) == [(back, klook, 3000, "refund")]


def test_a_re_run_adds_nothing(client, conn, o):
    klook_from_kim(conn, o)
    klook_refund(conn, o)
    run(client)
    before = (entries(conn), refund_set_offs(conn), links(conn))
    resp = run(client)
    assert "X-Fin-Change" not in resp.headers
    assert (entries(conn), refund_set_offs(conn), links(conn)) == before


# --- case 36a: Unlink stays unlinked --------------------------------------------


def test_case_36a_unlink_removes_link_and_set_off_and_writes_the_dismissal(client, conn, o):
    klook = klook_from_kim(conn, o)
    back = klook_refund(conn, o)
    run(client)
    [(link_id, _, _)] = links(conn)
    resp = client.post(f"/api/refund-links/{link_id}/unlink")
    assert resp.status_code == 200, resp.get_json()
    change = int(resp.headers["X-Fin-Change"])
    assert [e for e in entries(conn) if e[0] == change] == [(change, "Unlinked a refund")]
    assert links(conn) == [] and refund_set_offs(conn) == []
    assert dismissals(conn) == [(back, "refund_link", o.klook)]
    assert expect_back.open_minor(conn, klook) == 17000
    assert own(conn, back) == -3000

    # Run again: it stays unlinked, and nothing is written.
    again = run(client)
    assert "X-Fin-Change" not in again.headers
    assert links(conn) == [] and refund_set_offs(conn) == []

    # Undo the Unlink: relinked, set off again, the dismissal gone.
    undo(client, change)
    assert [(r, p) for _, r, p in links(conn)] == [(back, o.klook)]
    assert refund_set_offs(conn) == [(back, klook, 3000, "refund")]
    assert dismissals(conn) == []
    assert expect_back.open_minor(conn, klook) == 14000


def test_unlink_of_a_link_with_no_set_off_writes_the_dismissal_only(client, conn, o):
    back = klook_refund(conn, o)
    run(client)
    [(link_id, _, _)] = links(conn)
    resp = client.post(f"/api/refund-links/{link_id}/unlink")
    assert resp.status_code == 200
    assert links(conn) == [] and dismissals(conn) == [(back, "refund_link", o.klook)]


def test_unlink_refusals_write_nothing(client, conn, o):
    assert client.post("/api/refund-links/999/unlink").status_code == 404
    # The purchase let go since the refund closed part of it: refused, named.
    klook = klook_from_kim(conn, o)
    klook_refund(conn, o)
    run(client)
    let_go(conn, klook, "gift", "2026-10-19", to_whom_id=o.people["Kim"])
    [(link_id, _, _)] = links(conn)
    before = (entries(conn), links(conn), refund_set_offs(conn), dismissals(conn))
    resp = client.post(f"/api/refund-links/{link_id}/unlink")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "let go since; undo the let-go first"
    assert (entries(conn), links(conn), refund_set_offs(conn), dismissals(conn)) == before


# --- case 37: a refund larger than what is open -----------------------------------


def test_case_37_the_open_rest_closes_and_the_rest_nets_unasked(client, conn, o):
    made = harbour_set_against(conn, o)
    back = harbour_refund(conn, o)
    run(client)
    assert refund_set_offs(conn) == [(back, made["harbour"], 2000, "refund")]
    assert expect_back.open_minor(conn, made["harbour"]) == 0
    assert expect_back.state(conn, made["harbour"]) == expect_back.CLOSED
    assert own(conn, back) == -3000          # nets as a Dining refund in its month
    assert paid_back(client) == []           # S$ 30 <= HARBOUR's own part S$ 100


# --- case 37a: a refund of a purchase already paid back ---------------------------


def test_case_37a_a_refund_of_a_purchase_already_paid_back_is_asked(client, conn, o):
    klook_closed(conn, o)
    back = klook_refund(conn, o, on="2026-10-25")
    run(client)
    [(link_id, _, _)] = links(conn)
    assert refund_set_offs(conn) == []
    assert own(conn, back) == -3000          # nets until answered
    [ask] = paid_back(client)
    assert ask["refund_link"] == link_id
    assert ask["refund"]["id"] == back and ask["purchase"]["id"] == o.klook
    assert ask["amount_minor"] == 3000
    assert ask["names"] == [{"id": o.people["Kim"], "name": "Kim"}]
    assert ask["words"] == "Refund of a purchase already paid back: keep it, or To pay back?"
    assert run(client).get_json()["asks"] == 1


def test_case_37a_to_pay_back_makes_a_pay_expectation_named_for_the_payer(client, conn, o):
    klook_closed(conn, o)
    back = klook_refund(conn, o, on="2026-10-25")
    run(client)
    [(link_id, _, _)] = links(conn)
    resp = client.post(f"/api/refund-links/{link_id}/pay-back")
    assert resp.status_code == 200, resp.get_json()
    change = int(resp.headers["X-Fin-Change"])
    assert [e for e in entries(conn) if e[0] == change] == [(change, "To pay back a refund")]
    [(x_id, direction, amount)] = [tuple(r) for r in conn.execute(
        "SELECT id, direction, amount_minor FROM expectation WHERE row_id = ?", (back,))]
    assert (direction, amount) == ("pay", 3000)
    assert [r[0] for r in conn.execute("SELECT person_id FROM expectation_name WHERE expectation_id = ?",
                                       (x_id,))] == [o.people["Kim"]]
    assert own(conn, back) == 0              # nets nowhere: To pay back +S$ 30.00
    assert paid_back(client) == []
    # Undo: asked again.
    undo(client, change)
    assert conn.execute("SELECT COUNT(*) FROM expectation WHERE row_id = ?", (back,)).fetchone()[0] == 0
    assert len(paid_back(client)) == 1


def test_case_37a_keep_it_stays_netted_and_is_not_asked_again(client, conn, o):
    klook_closed(conn, o)
    back = klook_refund(conn, o, on="2026-10-25")
    run(client)
    [(link_id, _, _)] = links(conn)
    resp = client.post(f"/api/refund-links/{link_id}/keep")
    assert resp.status_code == 200, resp.get_json()
    change = int(resp.headers["X-Fin-Change"])
    assert [e for e in entries(conn) if e[0] == change] == [(change, "Kept a refund")]
    assert own(conn, back) == -3000
    assert paid_back(client) == []
    assert conn.execute("SELECT COUNT(*) FROM expectation WHERE row_id = ?", (back,)).fetchone()[0] == 0
    undo(client, change)
    assert len(paid_back(client)) == 1


def test_case_37a_a_whole_purchase_refund_asks_the_same(client, conn, o):
    klook_closed(conn, o)
    klook_refund(conn, o, on="2026-10-25", amount=18000)
    run(client)
    [ask] = paid_back(client)
    assert ask["amount_minor"] == 18000


def test_answers_refused_when_nothing_is_asked(client, conn, o):
    made = harbour_set_against(conn, o)
    harbour_refund(conn, o)
    run(client)
    [(link_id, _, _)] = links(conn)
    before = (entries(conn), dismissals(conn))
    for answer in ("keep", "pay-back"):
        resp = client.post(f"/api/refund-links/{link_id}/{answer}")
        assert resp.status_code == 409
        assert resp.get_json()["error"] == "HARBOUR GRILL REFUND is not asked: nothing to pay back on it"
    assert client.post("/api/refund-links/999/keep").status_code == 404
    assert (entries(conn), dismissals(conn)) == before
    assert expect_back.open_minor(conn, made["harbour"]) == 0


def test_answered_once_the_second_answer_is_refused(client, conn, o):
    klook_closed(conn, o)
    klook_refund(conn, o, on="2026-10-25")
    run(client)
    [(link_id, _, _)] = links(conn)
    assert client.post(f"/api/refund-links/{link_id}/pay-back").status_code == 200
    assert client.post(f"/api/refund-links/{link_id}/keep").status_code == 409
    assert client.post(f"/api/refund-links/{link_id}/pay-back").status_code == 409


def test_unlink_is_refused_while_the_refund_is_to_pay_back(client, conn, o):
    klook_closed(conn, o)
    klook_refund(conn, o, on="2026-10-25")
    run(client)
    [(link_id, _, _)] = links(conn)
    client.post(f"/api/refund-links/{link_id}/pay-back")
    before = (entries(conn), links(conn), dismissals(conn))
    resp = client.post(f"/api/refund-links/{link_id}/unlink")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == ("KLOOK TRAVEL REFUND is to pay back since; "
                                        "undo the mark on it first")
    assert (entries(conn), links(conn), dismissals(conn)) == before


def test_a_second_refund_is_asked_only_for_what_passes_the_own_part(client, conn, o):
    # HARBOUR: S$ 300.00, S$ 200.00 expected back and wholly back; own part S$ 100.00.
    harbour = mark(conn, o.harbour, 20000, names=(o.people["Jay"],))
    jay = row(conn, o.book, o.book.savings, "2026-10-11", "PAYNOW FROM JAY TAN 0413", -20000, flow="review")
    set_off(conn, jay, harbour, 20000)
    first = harbour_refund(conn, o, on="2026-10-20", amount=6000)
    second = harbour_refund(conn, o, on="2026-10-21", amount=6000)
    run(client)
    assert {r for _, r, _ in links(conn)} == {first, second}
    [ask] = paid_back(client)
    assert ask["refund"]["id"] == second and ask["amount_minor"] == 2000


# --- case 37b: a refund after a let-go ----------------------------------------------


def test_case_37b_after_a_let_go_the_refund_nets_and_the_let_go_stays(client, conn, o):
    made = harbour_set_against(conn, o)
    let_go(conn, made["harbour"], "gift", "2026-10-19", to_whom_id=o.people["Kim"])
    back = harbour_refund(conn, o)
    run(client)
    assert len(links(conn)) == 1
    assert refund_set_offs(conn) == []
    assert own(conn, back) == -5000
    assert paid_back(client) == []
    assert expect_back.state(conn, made["harbour"]) == expect_back.LET_GO
    assert conn.execute("SELECT amount_minor FROM let_go WHERE expectation_id = ?",
                        (made["harbour"],)).fetchone()[0] == 2000
