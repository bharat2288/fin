"""Types in the app (fin RUL-002; walk-fixes 2026-10-22, ticket 06): the
operator adds a spending type and fixes a type's wording in fin, each one
entry in the change history that can be undone. Renaming, merging and
removing a type stay code changes; the types code reads by name stay locked.

Driven through the HTTP interface against a temporary book. Every merchant,
figure and name here is invented, and the key is a made-up word.
"""

import ast
import collections
import socket
import sqlite3
from pathlib import Path

import pytest

import book_type
import db
import history
import mcp_tools
import suggest
from test_type_suggestion import FakeModel, NOT_A_KEY, prepare_and_send, row, statement_on

CHILDCARE = {
    "name": "Childcare",
    "parent": "Kids",
    "one_off": False,
    "covers": "nannies, after-school care, holiday camps",
    "not_for": "school fees",
}
DINING_COVERS = "restaurants, bars, fast food, food delivery, coffee shops, cafes, bakeries and drinks shops"
FIXED_COVERS = DINING_COVERS + ", hawker centres"
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args, **kwargs):
        raise OSError("tests make no network calls")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def types(client) -> dict[str, dict]:
    """The type list as the screens read it, keyed by display name."""
    return {t["display_name"]: t for t in client.get("/api/types").get_json()}


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def type_id(client, display: str) -> int:
    return types(client)[display]["id"]


def add(client, **body):
    """Add a type: the parent is named by the screen's display name and sent
    as its id, as the sheet sends it."""
    sent = dict(body)
    parent = sent.pop("parent", None)
    sent["parent_id"] = type_id(client, parent) if parent else None
    return client.post("/api/types", json=sent)


def fix(client, display: str, **body):
    return client.put(f"/api/types/{type_id(client, display)}", json=body)


def book_rows(temp_db) -> tuple:
    """Every type row and every change entry, read straight from the book."""
    conn = sqlite3.connect(str(temp_db))
    try:
        return (
            conn.execute("SELECT * FROM types ORDER BY id").fetchall(),
            conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM change_rows").fetchone()[0],
        )
    finally:
        conn.close()


# --- case 21: add a type ----------------------------------------------------


def test_21_an_added_type_is_one_entry_and_shows_everywhere_a_type_is_read(client):
    resp = add(client, **CHILDCARE)

    assert resp.status_code == 201, resp.get_json()
    [entry] = entries(client)
    assert (entry["via"], entry["summary"], entry["rows"]) == ("app", "Added the type Kids > Childcare", 1)
    added = types(client)["Kids > Childcare"]
    assert resp.get_json()["type"]["id"] == added["id"]
    assert (added["parent_name"], added["default_one_off"], added["covers"], added["not_for"]) == (
        "Kids", 0, CHILDCARE["covers"], "school fees",
    )
    assert (added["added_in_fin"], added["locked"], added["proposed_book"]) == (True, False, "Household")
    assert types(client)["Kids"]["added_in_fin"] is False


def test_21_a_one_off_top_level_type_with_no_not_for_is_added(client):
    resp = add(client, name="Weddings", one_off=True, covers="wedding venues, caterers, gowns")

    assert resp.status_code == 201, resp.get_json()
    added = types(client)["Weddings"]
    assert (added["parent_id"], added["default_one_off"], added["not_for"]) == (None, 1, None)


# --- case 22: add refused, nothing written ----------------------------------


@pytest.mark.parametrize("body, reason", [
    ({"name": "dining"}, "Dining is already a type"),
    ({"name": "  Golf "}, "Fitness > Golf is already a type"),
    ({"name": "Kids > Childcare"}, "a type's name cannot hold \">\""),
    ({"name": "Other"}, "Other is already a type"),
    ({"name": "None of these"}, "\"None of these\" is what Jev answers when no type fits"),
    ({"name": "none_of_these"}, "\"None of these\" is what Jev answers when no type fits"),
    ({"name": "   "}, "a type needs a name"),
    ({"name": "Childcare", "covers": "  "}, "a type needs words for what it covers"),
    ({"name": "Childcare", "parent": "Fitness > Golf"}, "a type can go only under a top-level spending type"),
    ({"name": "C" * 41}, "a type's name can be at most 40 characters"),
    ({"name": "Childcare", "covers": "c" * 301}, "what a type covers can be at most 300 characters"),
    ({"name": "Childcare", "not_for": "n" * 301}, "what a type is not for can be at most 300 characters"),
    ({"name": "Childcare", "one_off": "yes"}, "one_off must be true or false"),
    ({"name": "Childcare", "kind": "income"}, "only a spending type can be added here"),
])
def test_22_an_add_that_breaks_a_rule_is_refused_in_words_and_writes_nothing(client, temp_db, body, reason):
    before = book_rows(temp_db)

    resp = add(client, **{"covers": "something", **body})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == reason
    assert book_rows(temp_db) == before


@pytest.mark.parametrize("parent_id", [9999, "Kids", True])
def test_22_a_parent_that_is_not_a_spending_type_is_refused(client, temp_db, parent_id):
    before = book_rows(temp_db)
    resp = client.post("/api/types", json={"name": "Childcare", "covers": "x", "parent_id": parent_id})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "a type can go only under a top-level spending type"
    assert book_rows(temp_db) == before


def test_22_an_income_kind_is_not_a_parent(client, temp_db):
    salary = client.get("/api/types?kind=income").get_json()[0]["id"]
    before = book_rows(temp_db)
    resp = client.post("/api/types", json={"name": "Bonus", "covers": "x", "parent_id": salary})
    assert resp.status_code == 400
    assert book_rows(temp_db) == before


# --- case 23: two adds of one name ------------------------------------------


def test_23_the_second_of_two_adds_is_refused_by_the_name(client):
    first = add(client, **CHILDCARE)
    second = add(client, **CHILDCARE)

    assert first.status_code == 201
    assert second.status_code == 400
    assert second.get_json()["error"] == "Kids > Childcare is already a type"
    assert [t for t in types(client) if t.endswith("Childcare")] == ["Kids > Childcare"]
    assert len(entries(client)) == 1


# --- case 24: fix the wording -----------------------------------------------


def test_24_a_wording_fix_is_one_entry_with_before_and_after(client):
    resp = fix(client, "Dining", covers=FIXED_COVERS, not_for="supermarkets")

    assert resp.status_code == 200, resp.get_json()
    [entry] = entries(client)
    assert (entry["summary"], entry["rows"]) == ("Fixed the wording of Dining", 1)
    [change] = client.get(f"/api/history/{entry['id']}").get_json()["changes"]
    assert (change["table"], change["op"], change["display_name"]) == ("types", "update", "Dining")
    assert (change["before"]["covers"], change["after"]["covers"]) == (DINING_COVERS, FIXED_COVERS)
    assert types(client)["Dining"]["covers"] == FIXED_COVERS


def test_24_not_for_alone_can_be_fixed_or_emptied(client):
    assert fix(client, "Dining", not_for="  ").status_code == 200
    assert types(client)["Dining"]["not_for"] is None
    assert types(client)["Dining"]["covers"] == DINING_COVERS


def test_24_the_same_wording_again_writes_no_entry(client):
    assert fix(client, "Dining", covers=DINING_COVERS).status_code == 200
    assert entries(client) == []


def test_24_an_added_type_can_have_its_wording_fixed(client):
    add(client, **CHILDCARE)
    assert fix(client, "Kids > Childcare", covers="nannies and holiday camps").status_code == 200
    assert types(client)["Kids > Childcare"]["covers"] == "nannies and holiday camps"


# --- case 25: fix refused ---------------------------------------------------


LOCKED = ["Advertising", "Stock purchases", "Payment fees", "Software & AI tools",
          "Bank & government fees", "Gifts & Donations", "Other"]


@pytest.mark.parametrize("display", LOCKED)
def test_25_a_type_code_reads_by_name_is_locked_and_its_wording_refused(client, temp_db, display):
    assert types(client)[display]["locked"] is True
    before = book_rows(temp_db)

    resp = fix(client, display, covers="anything")

    assert resp.status_code == 400
    assert resp.get_json()["error"] == f"{display} is set in code: fin reads it by name"
    assert book_rows(temp_db) == before


@pytest.mark.parametrize("extra", [
    {"name": "Eating out"}, {"parent_id": 1}, {"one_off": True}, {"kind": "income"}, {"default_one_off": 1},
])
def test_25_any_field_but_the_wording_is_refused(client, temp_db, extra):
    before = book_rows(temp_db)

    resp = fix(client, "Dining", covers=FIXED_COVERS, **extra)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "renaming, merging or removing a type is a code change"
    assert book_rows(temp_db) == before


def test_25_an_income_kind_is_refused(client, temp_db):
    salary = client.get("/api/types?kind=income").get_json()[0]["id"]
    before = book_rows(temp_db)
    resp = client.put(f"/api/types/{salary}", json={"covers": "pay"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "income kinds are set in code"
    assert book_rows(temp_db) == before


@pytest.mark.parametrize("body, reason", [
    ({"covers": ""}, "a type needs words for what it covers"),
    ({"covers": "c" * 301}, "what a type covers can be at most 300 characters"),
    ({}, "say what the type covers or what it is not for"),
    ({"covers": 7}, "covers must be words"),
])
def test_25_a_wording_that_breaks_a_rule_is_refused(client, temp_db, body, reason):
    before = book_rows(temp_db)
    resp = fix(client, "Dining", **body)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == reason
    assert book_rows(temp_db) == before


def test_25_an_unknown_type_is_not_found(client):
    resp = client.put("/api/types/9999", json={"covers": "x"})
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "no such type"}


# --- case 26: the restart ---------------------------------------------------


def test_26_a_restart_leaves_an_added_type_and_a_fixed_wording_and_writes_no_entry(client, temp_db):
    add(client, **CHILDCARE)
    fix(client, "Dining", covers=FIXED_COVERS)
    before = book_rows(temp_db)

    db.init_db()

    assert book_rows(temp_db) == before
    assert types(client)["Kids > Childcare"]["covers"] == CHILDCARE["covers"]
    assert types(client)["Dining"]["covers"] == FIXED_COVERS


# --- cases 27 and 28: undo --------------------------------------------------


def test_27_undoing_a_wording_fix_puts_the_old_wording_back(client):
    fix(client, "Dining", covers=FIXED_COVERS)
    fixed = entries(client)[0]

    assert client.post(f"/api/history/{fixed['id']}/undo").status_code == 200

    assert types(client)["Dining"]["covers"] == DINING_COVERS


def test_28_undoing_an_add_nothing_uses_removes_the_type(client):
    add(client, **CHILDCARE)
    added = entries(client)[0]

    assert client.post(f"/api/history/{added['id']}/undo").status_code == 200

    assert "Kids > Childcare" not in types(client)


def test_28_undoing_an_add_a_merchant_uses_is_refused_and_writes_nothing(client, temp_db):
    add(client, **CHILDCARE)
    added = entries(client)[0]
    made = client.post("/api/services", json={"name": "Sample Nanny Co", "type_id": type_id(client, "Kids > Childcare")})
    assert made.status_code in (200, 201), made.get_json()
    before = book_rows(temp_db)

    resp = client.post(f"/api/history/{added['id']}/undo")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "a merchant or row still uses Kids > Childcare; change those first"
    assert book_rows(temp_db) == before
    assert "Kids > Childcare" in types(client)


def test_28_undoing_an_add_with_a_type_added_under_it_is_refused(client, temp_db):
    add(client, name="Hobbies", covers="craft supplies, model kits")
    added = entries(client)[0]
    add(client, name="Pottery", parent="Hobbies", covers="clay, kiln time")
    before = book_rows(temp_db)

    resp = client.post(f"/api/history/{added['id']}/undo")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "a type still sits under Hobbies; undo that one first"
    assert book_rows(temp_db) == before


def test_the_columns_undo_checks_are_every_column_that_points_at_a_type(conn):
    pointing = set()
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        for fk in conn.execute(f"PRAGMA foreign_key_list({table})"):
            if fk[2] == "types" and table != "types":
                pointing.add((table, fk[3]))
    assert pointing == set(book_type.USED_BY)


def test_28_undoing_an_add_a_rule_uses_is_refused(client, temp_db):
    add(client, **CHILDCARE)
    added = entries(client)[0]
    nanny = client.post("/api/services", json={"name": "Sample Nanny Co"}).get_json()["id"]
    made = client.post("/api/rules", json={
        "pattern": "SAMPLE NANNY", "service_id": nanny,
        "type_override_id": type_id(client, "Kids > Childcare"),
    })
    assert made.status_code in (200, 201), made.get_json()
    before = book_rows(temp_db)

    resp = client.post(f"/api/history/{added['id']}/undo")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "a merchant or row still uses Kids > Childcare; change those first"
    assert book_rows(temp_db) == before


# --- case 29: chat ----------------------------------------------------------


def test_29_no_chat_tool_writes_a_type_and_the_types_tool_reads_an_added_one(client):
    any_args = collections.defaultdict(lambda: 1)
    for tool in mcp_tools.TOOLS:
        try:
            method, path, _ = tool.request(any_args)
        except Exception:
            continue
        assert not (method != "GET" and path.startswith("/api/types")), tool.name

    add(client, **CHILDCARE)
    listed = mcp_tools.call("types", {}, actor="chat:sample-subject")["result"]
    assert "Kids > Childcare" in {t["display_name"] for t in listed}


# --- case 30: a new book ----------------------------------------------------


def test_30_a_new_book_holds_exactly_the_declaration(client):
    shown = client.get("/api/types").get_json()

    assert [
        (t["name"], t["parent_name"], t["default_one_off"], t["covers"], t["not_for"]) for t in shown
    ] == [
        (name, parent, 1 if default == book_type.ONE_OFF else 0, covers, not_for)
        for name, parent, default, covers, not_for in book_type.SPENDING_TYPES
    ]
    assert not any(t["added_in_fin"] for t in shown)


# --- what Jev is sent -------------------------------------------------------


def test_jev_is_asked_with_an_added_type_and_a_fixed_wording(client, conn):
    add(client, **CHILDCARE)
    fix(client, "Dining", covers=FIXED_COVERS)

    asked = suggest.question(conn)["criteria"]

    assert asked["Kids > Childcare"] == {"what": CHILDCARE["covers"], "not_for": "school fees"}
    assert asked["Dining"]["what"] == FIXED_COVERS
    assert "Other" not in asked


def test_27_after_undo_jev_is_sent_the_old_wording(client, conn):
    fix(client, "Dining", covers=FIXED_COVERS)
    client.post(f"/api/history/{entries(client)[0]['id']}/undo")

    assert suggest.question(conn)["criteria"]["Dining"]["what"] == DINING_COVERS


def test_an_answer_naming_an_added_type_maps_to_its_id(client, conn, monkeypatch):
    monkeypatch.setenv(suggest.KEY_ENV, NOT_A_KEY)
    fake = FakeModel()
    monkeypatch.setattr(suggest, "post", fake)
    add(client, **CHILDCARE)
    tx = row(conn, statement_on(conn, "card"), "LITTLE ACORNS NANNIES")
    fake.answer("LITTLE ACORNS NANNIES", "Kids > Childcare", 0.95)

    assert prepare_and_send(client)["sent"] == 1

    assert fake.requests[0]["questions"]["type"]["criteria"]["Kids > Childcare"]["what"] == CHILDCARE["covers"]
    offered = client.get(f"/api/transactions/{tx}/suggestion").get_json()
    assert offered["route"] == "prefill"
    assert offered["types"][0]["type_id"] == type_id(client, "Kids > Childcare")


def test_an_answer_naming_a_type_the_question_did_not_hold_is_refused(conn, monkeypatch):
    def answers_with_a_stranger(body, key):
        options = list(body["questions"]["type"]["criteria"]) + ["Kids > Childcare"]
        share = 1.0 / len(options)
        return {"model": suggest.RETURNED_MODEL_ID, "answers": {"type": {
            "choice": "Kids > Childcare", "probabilities": {o: share for o in options}}}}

    monkeypatch.setattr(suggest, "post", answers_with_a_stranger)
    with pytest.raises(suggest.BadAnswer):
        suggest.ask("LITTLE ACORNS NANNIES", NOT_A_KEY, conn)


# --- the history triggers on a book from before this change ----------------


def test_a_book_from_before_this_change_gains_the_types_triggers_on_its_next_start(temp_db, client):
    conn = sqlite3.connect(str(temp_db))
    for op in ("insert", "update", "delete"):
        conn.execute(f"DROP TRIGGER history_types_{op}")
    conn.commit()
    conn.close()

    db.init_db()

    conn = sqlite3.connect(str(temp_db))
    try:
        held = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    finally:
        conn.close()
    assert {f"history_types_{op}" for op in ("insert", "update", "delete")} <= held
    add(client, **CHILDCARE)
    assert entries(client)[0]["rows"] == 1


def test_types_is_a_tracked_table():
    assert "types" in history.TRACKED


# --- the locked list --------------------------------------------------------

# Tables that name types only as labels to give (a seed rule, a PayNow payee's
# wording, the old categories' mapping, the demo book): they need the name to
# be there, which a name added in the app cannot take away, and nothing they
# do turns on a type's wording.
LABEL_DATA = {"book_type.py", "db.py", "ingest.py", "convert_book_type.py", "seed_mock_data.py",
              "convert_account_kinds.py"}


def test_every_type_code_reads_by_name_is_locked():
    declared = {n for n, *_ in book_type.SPENDING_TYPES} | {
        book_type.display_name(n, p) for n, p, *_ in book_type.SPENDING_TYPES
    }
    read_by_name = {}
    for path in REPO.glob("*.py"):
        if path.name in LABEL_DATA:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in declared:
                read_by_name.setdefault(node.value, path.name)
    unlocked = {name: where for name, where in read_by_name.items() if name not in book_type.LOCKED}
    assert unlocked == {}
    assert set(book_type.PROPOSES_MOOM + book_type.ASKS_FOR_BOOK) <= set(book_type.LOCKED)
    assert sorted(book_type.LOCKED) == sorted(LOCKED)
