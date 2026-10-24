"""The default merchant rules are seeded once, so a rule the operator deletes stays deleted.

Before this, every start (and so every deploy) walked db.DEFAULT_MERCHANT_RULES
and added back any pattern that was missing: the catch-all `MOOM` rule the
operator deleted in the 24 Oct 2026 Queue session would have come back on the
next deploy, and with it the share buyback it once pulled into Moom spending.
Now a marker (seeded_once, DEFAULT_RULES_SEED) records the seeding: a new book
gets every default rule at its first start, and a book that already holds
rules gains only the marker. Every row and name here is invented.
"""

import sqlite3

import db
from test_paynow_rules import rules
from test_paynow_rules import ALREADY, MADE
from test_type_keys import book_before


def _marked(path) -> bool:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT 1 FROM seeded_once WHERE seed = ?", (db.DEFAULT_RULES_SEED,)
        ).fetchone() is not None
    finally:
        conn.close()


def test_a_new_book_gets_every_default_rule_at_its_first_start(client):
    held = rules(client)

    assert {p for p, _, _ in db.DEFAULT_MERCHANT_RULES} <= set(held)


def test_a_deleted_default_rule_stays_deleted_after_a_start(client):
    moom = rules(client)["MOOM"]

    resp = client.delete(f"/api/rules/{moom['id']}", json={"past": "only_new"})
    assert resp.status_code == 200, resp.get_json()
    db.init_db()

    assert "MOOM" not in rules(client)
    assert "XERO" in rules(client)


def test_a_default_rule_whose_wording_was_changed_is_not_added_back(client):
    tesla = rules(client)["TESLA PREMIUM"]

    resp = client.put(f"/api/rules/{tesla['id']}", json={"pattern": "TESLA PREMIUM CONNECTIVITY"})
    assert resp.status_code == 200, resp.get_json()
    db.init_db()

    held = rules(client)
    assert "TESLA PREMIUM" not in held
    assert "TESLA PREMIUM CONNECTIVITY" in held


def _patterns(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT pattern FROM merchant_rules")}
    finally:
        conn.close()


def test_a_book_from_before_gains_only_the_marker(tmp_path, monkeypatch):
    path = book_before(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DELETE FROM merchant_rules WHERE pattern = 'MOOM'")
        conn.commit()
    finally:
        conn.close()
    before = _patterns(path)

    db.init_db()
    db.init_db()

    after = _patterns(path)
    assert "MOOM" not in after
    assert _marked(path)
    # Only the PayNow wordings' own one-time seeding adds rules to this book.
    assert after - before == {p for p, _, _ in MADE} - ALREADY
