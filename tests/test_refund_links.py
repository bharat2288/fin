"""Personal-ledger 38: refund links (spec 23, E7, the linker).

A refund links to its purchase by itself when exactly one purchase fits: the
same merchant, within the 90 days before, with at least the refund left on it
once the refunds already linked are taken off, and no Unlink's dismissal for
the pair. A tie links nothing and is asked; a hand link is never moved. The
linker runs inside the import confirm's change and on demand; a hand link
serves the Queue's ask. A linked refund not labelled by hand takes its
purchase's book and type.

Seam 1 (the HTTP routes through the Flask test client, the import confirm
included), figures in and out: cases 34, 35 (server half), 38 and 39, and the
history each write makes. Case 34's "Household goods" is fin's type "Home":
fin has no type of that name. Every name and figure is invented.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import refund_links
from expect_fixtures import check_book, dismiss, refund_link, row
from test_balance_sheet import bring_in
from trend_fixtures import type_id

STATIC = Path(__file__).resolve().parent.parent / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


@pytest.fixture
def book(conn):
    return check_book(conn)


def links(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "SELECT refund_row_id, purchase_row_id, source FROM refund_link ORDER BY id")]


def label(conn, row_id) -> tuple:
    return tuple(conn.execute("SELECT book, type_id FROM transactions WHERE id = ?", (row_id,)).fetchone())


def entries(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute("SELECT id, summary FROM change_entries ORDER BY id")]


def run(client):
    resp = client.post("/api/refund-links/run")
    assert resp.status_code == 200, resp.get_json()
    return resp


def undo(client, resp):
    change = resp.headers["X-Fin-Change"]
    done = client.post(f"/api/history/{change}/undo")
    assert done.status_code == 200, done.get_json()


def amazon(conn, book, on="2026-07-20", amount=62000):
    return row(conn, book, book.rewards_card, on, "AMAZON.SG", amount, type_name="Home")


def refund(conn, book, on, amount, description="AMAZON.SG REFUND", account=None):
    return row(conn, book, account or book.rewards_card, on, description, -amount, flow="refund")


# --- case 34: by itself, at import --------------------------------------------


def test_case_34_the_import_links_the_one_fit_and_the_refund_takes_its_label(client, conn, book):
    purchase = amazon(conn, book)
    before = entries(conn)
    bring_in(client, "Sample Card 0001", [("2026-08-12", "AMAZON.SG REFUND", -59000)],
             kind="card", closing_date="2026-08-31")
    [refund_id] = [r[0] for r in conn.execute(
        "SELECT id FROM transactions WHERE description = 'AMAZON.SG REFUND'")]
    assert conn.execute("SELECT flow_type FROM transactions WHERE id = ?", (refund_id,)).fetchone()[0] == "refund"

    assert links(conn) == [(refund_id, purchase, "auto")]
    assert label(conn, refund_id) == ("Household", type_id(conn, "Home"))
    # The link rides the import's change: one new entry, and it holds the link.
    [(entry_id, summary)] = [e for e in entries(conn) if e not in before]
    assert summary == "Imported a statement"
    assert conn.execute("SELECT COUNT(*) FROM change_rows WHERE entry_id = ? AND tbl = 'refund_link'",
                        (entry_id,)).fetchone()[0] == 1

    shown = client.get(f"/api/refund-links?row={refund_id}").get_json()
    assert shown["words"] == "refund of 20 Jul"
    assert shown["purchase"]["id"] == purchase and shown["refund_link"]["source"] == "auto"
    assert client.get(f"/api/refund-links?row={purchase}").get_json()["refund_link"] is None

    # August's Home nets the refund; July keeps the purchase whole.
    def home(start, end):
        found = client.get(f"/api/dashboard/types?start={start}&end={end}&book=Household").get_json()
        return [t["total"] for t in found if t["type"] == "Home"]
    assert home("2026-08-01", "2026-08-31") == [-590.0]
    assert home("2026-07-01", "2026-07-31") == [620.0]


def test_undoing_the_import_takes_its_refund_link_with_it(client, conn, book):
    amazon(conn, book)
    bring_in(client, "Sample Card 0001", [("2026-08-12", "AMAZON.SG REFUND", -59000)], kind="card")
    imported = conn.execute("SELECT MAX(id) FROM change_entries").fetchone()[0]
    assert client.post(f"/api/history/{imported}/undo").status_code == 200
    assert links(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE description = 'AMAZON.SG REFUND'").fetchone()[0] == 0


def test_a_refund_labelled_by_hand_keeps_its_label(client, conn, book):
    purchase = amazon(conn, book)
    back = refund(conn, book, "2026-08-12", 59000)
    conn.execute("UPDATE transactions SET book = 'Moom', type_id = ?, cat_source = 'manual' WHERE id = ?",
                 (type_id(conn, "Office"), back))
    conn.commit()
    run(client)
    assert links(conn) == [(back, purchase, "auto")]
    assert label(conn, back) == ("Moom", type_id(conn, "Office"))


# --- what fits ------------------------------------------------------------------


def test_only_the_same_merchant_in_the_90_days_before_in_the_same_currency_fits(client, conn, book):
    amazon(conn, book, on="2026-05-13")                       # 91 days before: too old
    row(conn, book, book.rewards_card, "2026-08-13", "AMAZON.SG", 62000, type_name="Home")  # after it
    row(conn, book, book.rewards_card, "2026-08-01", "LAZADA SG", 62000, type_name="Home")  # another merchant
    row(conn, book, book.rupee_card, "2026-08-01", "AMAZON.SG", 6200000, type_name="Home")   # rupees
    back = refund(conn, book, "2026-08-12", 59000)
    run(client)
    assert links(conn) == []
    assert client.get("/api/refund-links/asks").get_json() == {"asks": []}
    edge = amazon(conn, book, on="2026-05-14")                # exactly 90 days before
    run(client)
    assert links(conn) == [(back, edge, "auto")]


def test_the_merchant_id_decides_when_both_rows_carry_one(client, conn, book):
    a = conn.execute("INSERT INTO services (name) VALUES ('Sample Shop A')").lastrowid
    b = conn.execute("INSERT INTO services (name) VALUES ('Sample Shop B')").lastrowid
    first = amazon(conn, book, on="2026-08-01")
    second = amazon(conn, book, on="2026-08-02")
    back = refund(conn, book, "2026-08-12", 5000)
    conn.execute("UPDATE transactions SET service_id = ? WHERE id IN (?, ?)", (a, first, back))
    conn.execute("UPDATE transactions SET service_id = ? WHERE id = ?", (b, second))
    conn.commit()
    run(client)
    assert links(conn) == [(back, first, "auto")]


def test_a_dismissed_pair_is_never_linked_again(client, conn, book):
    purchase = amazon(conn, book)
    back = refund(conn, book, "2026-08-12", 59000)
    dismiss(conn, back, "refund_link", purchase)
    run(client)
    assert links(conn) == []


# --- case 35: two fit, asked --------------------------------------------------


def test_case_35_a_tie_links_nothing_and_both_purchases_are_asked(client, conn, book):
    first = amazon(conn, book, on="2026-09-20", amount=14000)
    second = amazon(conn, book, on="2026-09-24", amount=9000)
    back = refund(conn, book, "2026-10-08", 9000)
    resp = run(client)
    assert resp.get_json()["linked"] == [] and "X-Fin-Change" not in resp.headers
    assert links(conn) == []
    [ask] = client.get("/api/refund-links/asks").get_json()["asks"]
    assert ask["kind"] == "which"
    assert (ask["refund"]["id"], ask["refund"]["amount_minor"], ask["refund"]["currency"]) == (back, -9000, "SGD")
    assert [(p["id"], p["date"], p["left_minor"]) for p in ask["purchases"]] == [
        (first, "2026-09-20", 14000), (second, "2026-09-24", 9000)]


def test_case_35_a_hand_link_is_one_change_undoable_and_never_moved_by_a_run(client, conn, book):
    amazon(conn, book, on="2026-09-20", amount=14000)
    second = amazon(conn, book, on="2026-09-24", amount=9000)
    back = refund(conn, book, "2026-10-08", 9000)
    resp = client.post("/api/refund-links", json={"refund_row_id": back, "purchase_row_id": second})
    assert resp.status_code == 200, resp.get_json()
    made = resp.get_json()["refund_link"]
    assert set(made) == {"id", "refund_row_id", "purchase_row_id", "source", "made_at"}
    assert (made["refund_row_id"], made["purchase_row_id"], made["source"]) == (back, second, "hand")
    assert label(conn, back) == ("Household", type_id(conn, "Home"))
    entry_id = int(resp.headers["X-Fin-Change"])
    assert dict(entries(conn))[entry_id] == "Linked a refund by hand"
    assert client.get("/api/refund-links/asks").get_json() == {"asks": []}

    run(client)
    run(client)
    assert links(conn) == [(back, second, "hand")]

    undo(client, resp)
    assert links(conn) == []
    assert label(conn, back) == (None, None)


# --- case 38: already refunded ----------------------------------------------


def test_case_38_a_refund_larger_than_what_is_left_is_not_linked(client, conn, book):
    purchase = amazon(conn, book)
    first = refund(conn, book, "2026-08-12", 59000)
    run(client)
    second = refund(conn, book, "2026-08-20", 4000)
    run(client)
    assert links(conn) == [(first, purchase, "auto")]
    assert client.get("/api/refund-links/asks").get_json() == {"asks": []}

    # Another purchase that fits alone takes it.
    other = amazon(conn, book, on="2026-08-15", amount=4000)
    run(client)
    assert links(conn) == [(first, purchase, "auto"), (second, other, "auto")]


def test_case_38_the_hand_link_is_refused_beyond_what_is_left(client, conn, book):
    purchase = amazon(conn, book)
    refund_link(conn, refund(conn, book, "2026-08-12", 59000), purchase)
    second = refund(conn, book, "2026-08-20", 4000)
    before = entries(conn)
    resp = client.post("/api/refund-links", json={"refund_row_id": second, "purchase_row_id": purchase})
    assert resp.status_code == 409
    assert resp.get_json() == {"error": "AMAZON.SG of 20 Jul has S$ 30.00 left to refund"}
    assert entries(conn) == before and len(links(conn)) == 1


# --- case 39: run twice ---------------------------------------------------------


def test_case_39_run_twice_links_nothing_new_and_moves_no_hand_link(client, conn, book):
    first = amazon(conn, book, on="2026-08-01", amount=10000)
    second = amazon(conn, book, on="2026-08-02", amount=5000)
    tied = refund(conn, book, "2026-08-04", 4000)    # fits both, until the next takes most of the first
    alone = refund(conn, book, "2026-08-05", 8000)   # fits the first alone
    held = refund(conn, book, "2026-08-06", 1000)
    refund_link(conn, held, second, source="hand")   # by hand, to the second
    resp = run(client)
    # The second pass links the refund the first pass left tied: S$ 20 is left
    # of the first purchase, less than S$ 40.
    assert [(link["refund_row_id"], link["purchase_row_id"]) for link in resp.get_json()["linked"]] == [
        (alone, first), (tied, second)]
    assert sorted(links(conn)) == sorted([(held, second, "hand"), (alone, first, "auto"), (tied, second, "auto")])

    before = entries(conn)
    again = run(client)
    assert again.get_json()["linked"] == [] and "X-Fin-Change" not in again.headers
    assert entries(conn) == before
    assert sorted(links(conn)) == sorted([(held, second, "hand"), (alone, first, "auto"), (tied, second, "auto")])


def test_the_on_demand_run_is_one_change_and_undoing_it_unlinks_and_restores_labels(client, conn, book):
    purchase = amazon(conn, book)
    back = refund(conn, book, "2026-08-12", 59000)
    resp = run(client)
    assert links(conn) == [(back, purchase, "auto")]
    assert dict(entries(conn))[int(resp.headers["X-Fin-Change"])] == "Linked refunds to their purchases"
    undo(client, resp)
    assert links(conn) == []
    assert label(conn, back) == (None, None)


def test_the_linker_called_as_the_import_calls_it_returns_what_it_made(conn, book):
    purchase = amazon(conn, book)
    back = refund(conn, book, "2026-08-12", 59000)
    made = refund_links.link_on_import(conn)
    conn.commit()
    assert [(m["refund_row_id"], m["purchase_row_id"], m["source"]) for m in made] == [(back, purchase, "auto")]
    assert refund_links.link_on_import(conn) == []


# --- hand link refusals ---------------------------------------------------------


@pytest.mark.parametrize("which, error", [
    ("not_refund", "AMAZON.SG is not a refund"),
    ("linked", "AMAZON.SG REFUND is already a refund of 20 Jul: unlink it first"),
    ("money_in", "SAMPLE REBATE REFUND is not a purchase: a refund links to money out spending"),
    ("rupees", "AMAZON.SG is in INR, the refund in SGD"),
    ("later", "AMAZON.SG of 13 Aug is after the refund"),
    ("unknown", "no such purchase row"),
])
def test_a_hand_link_is_refused_whole_and_writes_nothing(client, conn, book, which, error):
    purchase = amazon(conn, book)
    back = refund(conn, book, "2026-08-12", 5000)
    body = {"refund_row_id": back, "purchase_row_id": purchase}
    if which == "not_refund":
        body = {"refund_row_id": purchase, "purchase_row_id": purchase}
    elif which == "linked":
        refund_link(conn, back, purchase)
        body["purchase_row_id"] = amazon(conn, book, on="2026-07-25")
    elif which == "money_in":
        body["purchase_row_id"] = refund(conn, book, "2026-08-01", 100, description="SAMPLE REBATE REFUND")
    elif which == "rupees":
        body["purchase_row_id"] = row(conn, book, book.rupee_card, "2026-08-01", "AMAZON.SG", 900000)
    elif which == "later":
        body["purchase_row_id"] = amazon(conn, book, on="2026-08-13")
    elif which == "unknown":
        body["purchase_row_id"] = 99999
    before, held, labelled = entries(conn), links(conn), label(conn, back)
    resp = client.post("/api/refund-links", json=body)
    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json() == {"error": error}
    assert (entries(conn), links(conn), label(conn, back)) == (before, held, labelled)


def test_a_hand_link_needs_both_rows_named(client, conn, book):
    resp = client.post("/api/refund-links", json={"refund_row_id": "1"})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "refund_row_id and purchase_row_id are row ids"}


# --- the row sheet: "refund of 20 Jul" (expect-refund.js) ------------------------


def node(code: str):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


SHEET = r"""
globalThis.window = globalThis;
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
const asked = [];
const cells = {};
globalThis.document = { querySelectorAll: () => [] };
globalThis.get = async url => { asked.push(url); return PAYLOAD; };
"""


@needs_node
def test_the_row_sheet_reads_refund_of_its_purchase_day_from_the_server():
    shells = "\n".join((STATIC / f"{s}.js").read_text(encoding="utf-8") for s in ("expect-mark", "expect-refund"))
    payload = {"refund_link": {"id": 1, "refund_row_id": 7, "purchase_row_id": 3, "source": "auto",
                               "made_at": "2026-08-12 09:00:00"},
               "purchase": {"id": 3, "description": "AMAZON.SG", "date": "2026-07-20", "account_name": "Rewards card",
                            "amount_minor": 62000, "currency": "SGD"},
               "words": "refund of 20 Jul"}
    code = SHEET + f"const PAYLOAD = {json.dumps(payload)};\n" + shells + r"""
    (async () => {
      const refundRow = { id: 7, flow_type: 'refund' };
      const drawn = [window.expectRowActions(refundRow), window.expectRowActions({ id: 3, flow_type: 'expense' })];
      const html = drawn[0];
      const id = /data-xb-refund-of="(\d+)"/.exec(html)[1];
      cells[id] = { dataset: { xbRefundOf: id }, innerHTML: '' };
      await window.Expect.refundOf(cells[id]);
      console.log(JSON.stringify([drawn, asked, cells[id].innerHTML]));
    })();"""
    drawn, asked, filled = node(code)
    assert drawn[1] == ""
    assert 'data-xb-refund-of="7"' in drawn[0]
    assert asked == ["/api/refund-links?row=7"]
    assert "refund of 20 Jul" in filled and 'data-act="row"' in filled and 'data-tx="3"' in filled


@needs_node
def test_the_row_sheet_draws_nothing_for_a_refund_with_no_link():
    shells = "\n".join((STATIC / f"{s}.js").read_text(encoding="utf-8") for s in ("expect-mark", "expect-refund"))
    code = SHEET + 'const PAYLOAD = {"refund_link": null, "purchase": null, "words": null};\n' + shells + r"""
    (async () => {
      const cell = { dataset: { xbRefundOf: '7' }, innerHTML: '' };
      await window.Expect.refundOf(cell);
      console.log(JSON.stringify(cell.innerHTML));
    })();"""
    assert node(code) == ""
