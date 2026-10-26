"""Books the operator creates (fin RUL-021; fin-redesign 22, spec part 2 cases 9
and 13): a book is a row in the book, added on Lists › Books, one entry in the
change history that can be undone. Every reader, chat tool and owner rule reads
the book list from the book, so a new book is listed, labelled, filtered and
totalled without a code change, and Household, Moom and Kalesh read exactly as
before.

Driven through the HTTP interface against a temporary book. Every merchant,
figure and name here is invented; "Film" is the spec's own example.
"""

import sqlite3

import pytest

import account_kind
import book_type
import db
import mcp_tools
from tests.trend_fixtures import account, merchant, row, statement

FILM = {"name": "Film", "description": "costs of the film, whichever account paid them"}
SEEDED = ["Household", "Moom", "Kalesh"]


def books(client) -> list[dict]:
    resp = client.get("/api/books")
    assert resp.status_code == 200
    return resp.get_json()


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def add(client, **body):
    return client.post("/api/books", json=body)


def stored(temp_db) -> tuple:
    """Every book row and every change entry, read straight from the book."""
    conn = sqlite3.connect(str(temp_db))
    try:
        return (
            conn.execute("SELECT * FROM books ORDER BY id").fetchall(),
            conn.execute("SELECT COUNT(*) FROM change_entries").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM change_rows").fetchone()[0],
        )
    finally:
        conn.close()


# --- the seeded books --------------------------------------------------------


def test_a_new_book_holds_the_three_declared_books_in_order(client):
    listed = books(client)
    assert [b["name"] for b in listed] == SEEDED
    assert [b["description"] for b in listed] == [d for _, d in book_type.BOOKS]
    assert [b["added_in_fin"] for b in listed] == [False, False, False]


def test_a_restart_seeds_no_book_twice_and_keeps_an_added_one(client, temp_db):
    assert add(client, **FILM).status_code == 201
    before = stored(temp_db)
    db.init_db()
    assert stored(temp_db) == before
    assert [b["name"] for b in books(client)] == SEEDED + ["Film"]


# --- case 9: the operator creates a book ---------------------------------------


def test_9_an_added_book_is_one_entry_and_is_listed(client):
    resp = add(client, **FILM)

    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["book"]["name"] == "Film"
    [entry] = entries(client)
    assert (entry["via"], entry["summary"], entry["rows"]) == ("app", "Added the book Film", 1)
    film = books(client)[-1]
    assert (film["name"], film["description"], film["added_in_fin"]) == ("Film", FILM["description"], True)


def test_9_the_chat_books_tool_lists_an_added_book_and_no_chat_tool_adds_one(client):
    add(client, **FILM)
    listed = mcp_tools.call("books", {}, actor="chat:sample-subject")["result"]
    assert [b["name"] for b in listed] == SEEDED + ["Film"]
    [tool] = [t for t in mcp_tools.TOOLS if t.name == "books"]
    assert "Kalesh" not in tool.description  # the list is the book's, not the description's


def test_9_a_row_merchant_rule_and_type_take_the_added_book(client, conn):
    add(client, **FILM)
    card = statement(conn, account(conn, "Sample Card 0001"), "2026-09")
    shop = merchant(conn, "SAMPLE PROPS HIRE")
    tx = row(conn, card, "SAMPLE PROPS HIRE", 120000, service_id=shop, day="2026-09-04")

    assert client.put(f"/api/transactions/{tx}", json={"book": "Film"}).status_code == 200
    assert conn.execute("SELECT book FROM transactions WHERE id = ?", (tx,)).fetchone()[0] == "Film"
    resp = client.put(f"/api/services/{shop}", json={"book": "Film"})
    assert resp.status_code == 200, resp.get_json()
    assert conn.execute("SELECT book FROM services WHERE id = ?", (shop,)).fetchone()[0] == "Film"

    dining = book_type.spending_type_ids(conn)["Dining"]
    resp = client.put(f"/api/types/{dining}", json={"usual_book": "Film"})
    assert resp.status_code == 200, resp.get_json()


def test_9_an_unknown_book_is_still_refused_on_a_row(client, conn):
    card = statement(conn, account(conn, "Sample Card 0001"), "2026-09")
    tx = row(conn, card, "SAMPLE PROPS HIRE", 120000, day="2026-09-04")
    assert client.put(f"/api/transactions/{tx}", json={"book": "Film"}).status_code == 400
    dining = book_type.spending_type_ids(conn)["Dining"]
    assert client.put(f"/api/types/{dining}", json={"usual_book": "Film"}).status_code == 400


def test_9_the_added_book_is_filtered_and_totalled_on_its_own(client, conn):
    add(client, **FILM)
    card = statement(conn, account(conn, "Sample Card 0001"), "2026-09")
    row(conn, card, "SAMPLE PROPS HIRE", 120000, book="Film", type_name="Shopping", day="2026-09-04")
    row(conn, card, "SAMPLE GROCER", 4550, book="Household", type_name="Groceries", day="2026-09-05")

    listed = client.get("/api/transactions?month=2026-09&book=Film").get_json()
    rows = listed["transactions"] if isinstance(listed, dict) else listed
    assert [r["description"] for r in rows] == ["SAMPLE PROPS HIRE"]

    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-09").get_json()
    assert (cards["film"], cards["household"]) == (1200.0, 45.5)

    resp = client.get("/api/trend/types?book=Film&span=6M&to=2026-09")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["book"] == "Film"
    assert client.get("/api/trend/types?book=Nowhere&span=6M&to=2026-09").status_code == 404


def test_9_an_added_book_is_an_account_owner(client):
    add(client, **FILM)
    owners = [o["name"] for o in client.get("/api/account-kinds").get_json()["owners"]]
    assert owners == SEEDED + ["Film"]
    resp = client.post("/api/accounts", json={"name": "Sample Film Bank 0009", "type": "bank",
                                              "last_four": "0009", "owner": "Film"})
    assert resp.status_code == 200, resp.get_json()


def test_9_a_cost_in_the_added_book_moves_its_company_balance_not_household_spending(client, conn):
    add(client, **FILM)
    resp = client.post("/api/accounts", json={"name": "Film", "type": "company"})
    assert resp.status_code == 200, resp.get_json()
    card = statement(conn, account(conn, "Sample Card 0001"), "2026-08")
    row(conn, card, "SAMPLE CAMERA HIRE", 250000, book="Film", type_name="Shopping", day="2026-08-12")

    sheet = client.get("/api/balance-sheet?month=2026-08").get_json()
    companies = next(s for s in sheet["sections"] if s["name"] == "companies")
    film = next(l for l in companies["lines"] if l["name"] == "Film")
    assert film["made_of"]["paid_for_minor"] == 250000
    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-08").get_json()
    assert (cards["household"], cards["film"]) == (0, 2500.0)


# --- Household, Moom and Kalesh read exactly as before ---------------------------


def test_the_seeded_books_read_the_same_after_a_book_is_added(client, conn):
    card = statement(conn, account(conn, "Sample Card 0001"), "2026-09")
    moom_bank = statement(conn, account(conn, "Sample Company Bank 0005", type="bank", owner="Moom"), "2026-09")
    row(conn, card, "SAMPLE GROCER", 4550, book="Household", type_name="Groceries", day="2026-09-05")
    row(conn, card, "SAMPLE ADS", 30010, book="Moom", type_name="Advertising", day="2026-09-06")
    row(conn, card, "SAMPLE HOSTING", 2015, book="Kalesh", type_name="Software & AI tools", day="2026-09-07")
    row(conn, moom_bank, "SAMPLE STOCKIST", 99999, book="Moom", type_name="Stock purchases", day="2026-09-08")

    def read():
        cards = client.get("/api/dashboard/stat-cards?ref_month=2026-09&history=3").get_json()
        trend = {b: client.get(f"/api/trend/types?book={b}&span=6M&to=2026-09").get_json() for b in SEEDED}
        monthly = {b: client.get(f"/api/dashboard/monthly?book={b}&start=2026-07-01&end=2026-09-30").get_json()
                   for b in SEEDED}
        return cards, trend, monthly, books(client)

    cards, trend, monthly, listed = read()
    add(client, **FILM)
    cards_after, trend_after, monthly_after, listed_after = read()

    film_keys = {k for k in cards_after if "film" in k}
    assert {k: v for k, v in cards_after.items() if k not in film_keys and k != "history"} == {
        k: v for k, v in cards.items() if k != "history"}
    assert [{k: v for k, v in h.items() if "film" not in k} for h in cards_after["history"]] == cards["history"]
    assert (trend_after, monthly_after, listed_after[:3]) == (trend, monthly, listed)


# --- case 13: refusals -----------------------------------------------------------


@pytest.mark.parametrize("body, reason", [
    ({"name": "Moom", "description": "again"}, "Moom is already a book"),
    ({"name": "moom", "description": "again"}, "Moom is already a book"),
    ({"name": "  Film  ", "description": "x"}, None),  # trimmed: accepted as Film (checked below)
    ({"name": "", "description": "x"}, "a book needs a name"),
    ({"name": "Film"}, "say what the book is for"),
    ({"name": "Film", "description": "   "}, "say what the book is for"),
    ({"name": "Ask", "description": "x"}, "ask is a word fin keeps for itself; choose another name"),
    ({"name": "Total", "description": "x"}, "total is a word fin keeps for itself; choose another name"),
    ({"name": "Film_2", "description": "x"}, "a book's name is letters, digits, spaces, & and -"),
    ({"name": "F" * 31, "description": "x"}, "a book's name is at most 30 characters"),
    ({"name": "Film", "description": "x", "owner": "Moom"}, "unknown field: owner"),
])
def test_13_an_add_that_breaks_a_rule_is_refused_in_words_and_writes_nothing(client, temp_db, body, reason):
    before = stored(temp_db)
    resp = add(client, **body)
    if reason is None:
        assert resp.status_code == 201
        assert books(client)[-1]["name"] == "Film"
        return
    assert (resp.status_code, resp.get_json()) == (400, {"error": reason})
    assert stored(temp_db) == before


def test_13_a_book_name_already_taken_is_refused_the_second_time(client, temp_db):
    assert add(client, **FILM).status_code == 201
    before = stored(temp_db)
    resp = add(client, name="FILM", description="again")
    assert (resp.status_code, resp.get_json()) == (400, {"error": "Film is already a book"})
    assert stored(temp_db) == before


def test_no_stat_card_key_is_a_word_a_book_could_take(client):
    """A book's figures ride the stat cards under its name in lower case; a name
    that is already a key there is refused (book_type.RESERVED)."""
    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-09&history=1").get_json()
    own = {b.lower() for b in SEEDED}
    keys = set(cards) | {k for h in cards["history"] for k in h}
    theirs = {k for k in keys if not own & set(k.split("_"))}
    first_words = {k.split("_")[0] for k in theirs}
    assert first_words <= set(book_type.RESERVED), sorted(first_words - set(book_type.RESERVED))


# --- undo ------------------------------------------------------------------------


def test_undoing_an_add_nothing_uses_removes_the_book(client, temp_db):
    add(client, **FILM)
    [added] = entries(client)
    assert client.post(f"/api/history/{added['id']}/undo").status_code == 200
    assert [b["name"] for b in books(client)] == SEEDED


@pytest.mark.parametrize("use", ["row", "merchant", "account", "type"])
def test_undoing_an_add_something_uses_is_refused_and_writes_nothing(client, conn, temp_db, use):
    add(client, **FILM)
    [added] = entries(client)
    if use == "row":
        card = statement(conn, account(conn, "Sample Card 0001"), "2026-09")
        row(conn, card, "SAMPLE PROPS HIRE", 120000, book="Film", day="2026-09-04")
    elif use == "merchant":
        merchant(conn, "SAMPLE PROPS HIRE", book="Film")
    elif use == "account":
        account(conn, "Sample Film Bank 0009", type="bank", owner="Film")
    else:
        dining = book_type.spending_type_ids(conn)["Dining"]
        assert client.put(f"/api/types/{dining}", json={"usual_book": "Film"}).status_code == 200
    before = stored(temp_db)
    resp = client.post(f"/api/history/{added['id']}/undo")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "something is filed under the book Film; change that first"
    assert stored(temp_db) == before


def test_the_columns_undo_checks_are_every_column_that_holds_a_book(conn):
    """Every column the schema notes as holding a book is one book_type.BOOK_USED_BY reads."""
    held = set(book_type.BOOK_USED_BY)
    assert {("transactions", "book"), ("services", "book"), ("merchant_rules", "book_override"),
            ("type_settings", "usual_book"), ("let_go", "book"), ("accounts", "owner")} <= held
    for table, column in held:
        assert column in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


# --- the readers take no book from code ------------------------------------------


def test_no_reader_takes_the_book_list_from_code():
    """BOOKS is the seed; the list itself is read from the book (fin RUL-021)."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    found = []
    for path in sorted(root.glob("*.py")):
        # The seed's home, the conversions, and the snapshot, which also reads
        # copies from before books were rows.
        if path.name.startswith("convert_") or path.name in ("book_type.py", "fin_snapshot.py"):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bBOOK_NAMES\b|book_type\.BOOKS\b|\bUSUAL_BOOKS\b|\bOWNER_NAMES\b|account_kind\.OWNERS\b", line):
                found.append(f"{path.name}:{n}")
    assert found == []
    assert not hasattr(account_kind, "OWNER_NAMES")


# --- review findings ---------------------------------------------------------------


def test_undo_never_writes_back_a_book_fin_no_longer_keeps(client, conn, temp_db):
    """Add Film, file a row there, move it to Household, undo the add, then undo
    the move: the second undo would put the row back in a book that is gone."""
    add(client, **FILM)
    card = statement(conn, account(conn, "Sample Card 0001"), "2026-09")
    tx = row(conn, card, "SAMPLE PROPS HIRE", 120000, day="2026-09-04")
    assert client.put(f"/api/transactions/{tx}", json={"book": "Film"}).status_code == 200
    assert client.put(f"/api/transactions/{tx}", json={"book": "Household"}).status_code == 200
    moved, _, added = entries(client)[:3]
    assert client.post(f"/api/history/{added['id']}/undo").status_code == 200
    before = stored(temp_db)
    resp = client.post(f"/api/history/{moved['id']}/undo")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "undoing this would file something under the book Film, which fin no longer keeps; add it again first"
    assert stored(temp_db) == before



def test_the_rows_page_filters_by_an_added_book(client, conn):
    add(client, **FILM)
    card = statement(conn, account(conn, "Sample Card 0001"), "2026-09")
    row(conn, card, "SAMPLE PROPS HIRE", 120000, book="Film", type_name="Shopping", day="2026-09-04")
    row(conn, card, "SAMPLE GROCER", 4550, book="Household", type_name="Groceries", day="2026-09-05")
    listed = client.get("/api/transactions?every=true&month=2026-09&book=Film").get_json()
    assert [r["description"] for r in listed["transactions"]] == ["SAMPLE PROPS HIRE"]



def test_chat_adds_a_book_as_the_app_does_in_one_change_it_can_undo(client):
    done = mcp_tools.call("add_book", dict(FILM), actor="chat:sample-subject")

    assert done["ok"], done
    assert [b["name"] for b in books(client)] == SEEDED + ["Film"]
    assert entries(client)[0]["summary"] == "Added the book Film"
    assert mcp_tools.call("undo", {"entry_id": done["change"]["entry_id"]}, actor="chat:sample-subject")["ok"]
    assert [b["name"] for b in books(client)] == SEEDED


def test_chat_is_refused_a_book_in_the_apps_words(client):
    answer = mcp_tools.call("add_book", {"name": "Film"}, actor="chat:sample-subject")

    assert answer["ok"] is False and answer["error"] == "say what the book is for"
