"""A stable key for every declared type (type-relationships 01, cases 1 and 2).

Each type and income kind book_type.py declares keeps a key, its declared
name frozen, in its own table, so the code finds the type it means after the
operator renames it. A renamed type is written straight into the book here,
standing in for the rename the app gains in ticket 04.

The book from before this change (tests/fixtures/book_before_type_keys.sql)
was made by fin's code at main f759090 (its init_db, one type added in the
app, four rows) and dumped as SQL. Everything else is driven through the HTTP
interface against a temporary book. Every merchant, figure and name here is
invented.
"""

import ast
import sqlite3
from pathlib import Path

import book_type
import convert_flow_rules
import convert_party_link
import convert_rule_flow
import db
import history
import suggest
from test_expect_letgo import harbour, let_go, on_day, story  # noqa: F401 (fixtures)
from test_loan_interest import OWED_AUG, derived, enter, setup
from test_movements_and_review import (  # noqa: F401 (fixtures)
    BANK, TRANSFERS, bring_in, labelled, ledger, listed, row, waiting, what_it_is,
)

REPO = Path(__file__).resolve().parent.parent
BEFORE = Path(__file__).parent / "fixtures" / "book_before_type_keys.sql"
# The same start makes ticket 02's settings table (tests/test_type_settings.py) and
# ticket 03's slots table (tests/test_type_slots.py); the markers' table, which
# ticket 05's marker that the PayNow wordings were made rules
# (tests/test_paynow_rules.py) goes in, the flow-rules step made already
# (fin-redesign 08, book_before).
NEW_TABLES = {"type_keys", "type_settings", "type_slots"}


def book_before(tmp_path, monkeypatch) -> Path:
    """The book main made, as a file fin is pointed at: brought through the
    conversion steps added since (rule-flow, fin-redesign 07; flow-rules, 08;
    party-link, 09), which add to its shape and its rules and change no row,
    as the operator's book is before a start."""
    path = tmp_path / "before.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(BEFORE.read_text(encoding="utf-8"))
    convert_rule_flow.STEP.apply(conn)
    convert_flow_rules.STEP.apply(conn)
    convert_party_link.STEP.apply(conn)
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.invalidate_rules_cache()
    return path


def every_row(path: Path) -> dict[str, list[tuple]]:
    """Every row of every table, by table."""
    conn = sqlite3.connect(str(path))
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in tables}
    finally:
        conn.close()


def kept_every_row(before: dict, after: dict) -> None:
    """Every row the book held is there unchanged; the start added rows only
    to merchants and rules (ticket 05's PayNow wording rules) and the marks
    of its seeding (the flow-rules step made their table)."""
    for table, rows in before.items():
        assert set(rows) <= set(after[table]), table
        if table not in ("services", "merchant_rules", "seeded_once"):
            assert after[table] == rows, table


def keys_held(path: Path) -> dict[tuple[str, str], int]:
    conn = sqlite3.connect(str(path))
    try:
        return {(kind, key): type_id for kind, key, type_id in conn.execute(
            "SELECT kind, key, type_id FROM type_keys")}
    finally:
        conn.close()


def rename(path: Path, kind: str, old: str, new: str) -> None:
    """A type's name changed in the book, as ticket 04's rename will."""
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("UPDATE types SET name = ? WHERE kind = ? AND name = ?", (new, kind, old)).rowcount == 1
        conn.commit()
    finally:
        conn.close()


def types(client, kind: str = "spending") -> dict[str, dict]:
    return {t["display_name"]: t for t in client.get(f"/api/types?kind={kind}").get_json()}


# --- case 1: a book from before starts, and every declared type gains its key ---


def test_case_1_a_book_from_before_starts_every_declared_type_gains_its_key_and_no_row_changes(
        tmp_path, monkeypatch, client):
    path = book_before(tmp_path, monkeypatch)
    held = every_row(path)
    # The ids the book holds for each declared name, read before fin starts.
    named = {(t[1], t[2]): t[0] for t in held["types"]}  # (kind, name) -> id

    db.init_db()
    db.init_db()  # a second start changes nothing either

    after = every_row(path)
    kept_every_row(held, after)
    assert set(after) - set(held) == NEW_TABLES
    declared = [("spending", n) for n, *_ in book_type.SPENDING_TYPES] + \
               [("income", n) for n, _ in book_type.INCOME_KINDS]
    assert keys_held(path) == {k: named[k] for k in declared}
    # The type added in the app has no key.
    assert named[("spending", "Childcare")] not in keys_held(path).values()
    conn = sqlite3.connect(str(path))
    try:
        assert history.has_shape(conn)
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    finally:
        conn.close()
    assert {f"history_type_keys_{op}" for op in ("insert", "update", "delete")} <= triggers
    assert types(client)["Kids > Childcare"]["added_in_fin"] is True


def test_a_book_from_before_that_never_started_since_keys_its_types_on_the_conversion_path(tmp_path):
    """seed_types makes the key table it fills, so a conversion step that
    seeds types on a book with no such table still works."""
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    try:
        conn.execute(book_type.TYPES_TABLE_SQL)
        book_type.seed_types(conn)
        assert conn.execute("SELECT COUNT(*) FROM type_keys").fetchone()[0] == \
            len(book_type.SPENDING_TYPES) + len(book_type.INCOME_KINDS)
    finally:
        conn.close()


# --- case 2: a renamed declared type is not seeded again; seed rules still find it ---


def test_case_2_a_renamed_type_is_not_seeded_again_and_the_seed_rules_still_find_it(
        tmp_path, monkeypatch, client):
    path = book_before(tmp_path, monkeypatch)
    db.init_db()
    rename(path, "spending", "Dining", "Food")
    conn = sqlite3.connect(str(path))
    food = conn.execute("SELECT id FROM types WHERE name = 'Food'").fetchone()[0]
    # A seed rule taken out, with its merchant, is put back when the defaults
    # are seeded: on a book with no rules and no seeding marker (a book that
    # holds rules is seeded once, test_seed_rules_once.py).
    svc = conn.execute("SELECT service_id FROM merchant_rules WHERE pattern = 'CULINA'").fetchone()[0]
    conn.execute("DELETE FROM merchant_rules")
    conn.execute("DELETE FROM services WHERE id = ?", (svc,))
    conn.execute("DELETE FROM seeded_once WHERE seed = ?", (db.DEFAULT_RULES_SEED,))
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM types").fetchone()[0]
    conn.close()

    db.init_db()

    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM types").fetchone()[0] == count
        assert conn.execute("SELECT 1 FROM types WHERE name = 'Dining'").fetchone() is None
        assert conn.execute(
            "SELECT s.type_id FROM merchant_rules r JOIN services s ON s.id = r.service_id"
            " WHERE r.pattern = 'CULINA'").fetchone()[0] == food
    finally:
        conn.close()
    assert "Food" in types(client) and "Dining" not in types(client)
    assert types(client)["Food"]["added_in_fin"] is False


# --- what code reads by key keeps working after a rename -------------------------


def test_a_renamed_type_keeps_the_book_it_proposes(client, temp_db):
    rename(temp_db, "spending", "Advertising", "Ads")
    rename(temp_db, "spending", "Software & AI tools", "Software")

    shown = types(client)

    assert shown["Ads"]["proposed_book"] == "Moom"
    assert shown["Software"]["proposed_book"] is None
    assert shown["Ads"]["added_in_fin"] is False


def test_a_new_merchant_of_a_renamed_asking_type_still_needs_a_book(client, temp_db, ledger):
    rename(temp_db, "spending", "Software & AI tools", "Software")
    bring_in(client, "Sample Card 0001", [(5, "SAMPLE CLOUD HOST 0042", 30.00)], kind="credit_card")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": row(client, "SAMPLE CLOUD HOST 0042")["id"], "service_name": "Sample Cloud Host",
        "type_id": types(client)["Software"]["id"],
    })

    assert resp.status_code == 400
    assert "book is required" in resp.get_json()["error"]


def test_loan_interest_is_filed_under_bank_fees_renamed(client, conn, temp_db):
    made = setup(client, conn)
    rename(temp_db, "spending", "Bank & government fees", "Bank charges")

    assert enter(client, made["loan"], OWED_AUG, "2026-08-31").status_code == 200

    assert {t["type"] for t in derived(client)} == {"Bank charges"}


def test_a_gift_given_on_the_review_list_takes_gifts_renamed(client, waiting, temp_db):
    rename(temp_db, "spending", "Gifts & Donations", "Gifts")

    tx = labelled(client, TRANSFERS[1][1], choice="gift")

    assert what_it_is(tx) == ("expense", None, "Household", "Gifts")


def test_a_gift_received_on_the_review_list_takes_its_income_kind_renamed(client, ledger, temp_db):
    rename(temp_db, "income", "Gift received", "Presents")
    bring_in(client, BANK, [(9, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410", -5000.00)])

    tx = labelled(client, "INWARD PAYNOW FROM SAMPLE SENDER REF 4410", choice="gift")

    assert what_it_is(tx) == ("income", None, "Household", "Presents")


def test_a_guess_reads_earlier_gifts_as_gifts_after_a_rename(client, ledger, temp_db):
    sender = "PAYNOW TO SAMPLE NIECE REF 7700"
    bring_in(client, BANK, [(1, sender, 50.00), (2, sender, 50.00), (3, sender, 50.00)])
    first, second, third = sorted((t for t in listed(client) if t["description"] == sender),
                                  key=lambda t: t["date"])
    for tx in (first, second):
        assert client.post(f"/api/review/{tx['id']}/label", json={"choice": "gift"}).status_code == 200
    rename(temp_db, "spending", "Gifts & Donations", "Gifts")

    guess = client.get(f"/api/review/{third['id']}/suggestion").get_json()["guess"]

    assert (guess["choice"], guess["body"], guess["type"]) == ("gift", {"choice": "gift"}, "Gifts")


def test_a_gift_let_go_counts_as_gifts_renamed(conn, client, story, harbour, monkeypatch, temp_db):  # noqa: F811
    on_day(monkeypatch, "2026-10-19")
    rename(temp_db, "spending", "Gifts & Donations", "Gifts")
    dining = types(client)["Dining"]["id"]

    refused = let_go(client, harbour["harbour"], "gift", type_id=dining)
    resp = let_go(client, harbour["harbour"], "gift")

    assert refused.get_json() == {"error": "a gift counts as Gifts"}
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["let_go"]["display_type"] == "Gifts"


def test_jev_is_never_asked_other_renamed(conn, temp_db):
    rename(temp_db, "spending", "Other", "Not placed")

    asked = suggest.question(conn)["criteria"]

    assert "Not placed" not in asked and "Dining" in asked


def test_a_paynow_wording_still_types_a_row_after_its_type_is_renamed(client, ledger, temp_db):
    rename(temp_db, "spending", "Fitness", "Sport")

    bring_in(client, BANK, [(4, "PAYNOW TO LIMITLESS WELLNESS REF 31", 120.00)])

    tx = row(client, "PAYNOW TO LIMITLESS WELLNESS REF 31")
    assert (tx["book"], tx["display_type"]) == ("Household", "Sport")


# --- the sweep: code reads a declared type by its key, through book_type ---------

# Lists that name types only as labels to give, each read through the key
# helpers: the seed rules and the PayNow wordings (db.py), the old
# categories' mapping (a conversion step on books from before types), the
# demo book, and the account kinds (where "Home" is not a type).
LABEL_DATA = {"db.py", "convert_book_type.py", "seed_mock_data.py", "convert_account_kinds.py"}


def test_no_code_reads_a_declared_type_by_a_quoted_name_outside_book_type():
    declared = {n for n, *_ in book_type.SPENDING_TYPES} | {
        book_type.display_name(n, p) for n, p, *_ in book_type.SPENDING_TYPES
    } | {n for n, _ in book_type.INCOME_KINDS}
    quoted = {}
    for path in REPO.glob("*.py"):
        if path.name in LABEL_DATA or path.name == "book_type.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in declared:
                quoted.setdefault(path.name, set()).add(node.value)
    assert quoted == {}


def test_type_keys_is_tracked_and_its_triggers_come_with_a_start():
    assert "type_keys" in history.TRACKED and "type_keys" in history.ADDED_ON_START
    assert len(history.table_triggers("type_keys")) == 3


def test_the_key_table_seed_types_makes_is_the_one_schema_sql_declares(tmp_path):
    def shape(conn):
        return (conn.execute("PRAGMA table_info(type_keys)").fetchall(),
                sorted(conn.execute("PRAGMA index_list(type_keys)").fetchall()),
                conn.execute("PRAGMA foreign_key_list(type_keys)").fetchall())

    declared = sqlite3.connect(str(tmp_path / "declared.db"))
    seeded = sqlite3.connect(str(tmp_path / "seeded.db"))
    try:
        declared.executescript(db.SCHEMA_PATH.read_text())
        seeded.execute(book_type.TYPES_TABLE_SQL)
        book_type.seed_types(seeded)
        assert shape(seeded) == shape(declared)
    finally:
        declared.close()
        seeded.close()
