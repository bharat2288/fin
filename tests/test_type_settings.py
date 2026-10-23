"""A type's settings, as data (type-relationships 02, cases 3 to 8).

Each spending type has a usual book (a book, or ask) and says whether a new
merchant of it starts as Check each row. Both are kept per type in the book,
seeded at start from what the code said before (Advertising, Stock purchases
and Payment fees: Moom; Software & AI tools: ask; every other declared type:
Household), never overwriting the operator's value. One-off by default is the
type's existing column. The operator changes all three on PUT /api/types/<id>,
one entry in Changes that Undo takes back; a change reaches new merchants
only (fin RUL-006 Q3).

Driven through the HTTP interface against a temporary book; the book from
before this effort is tests/fixtures/book_before_type_keys.sql (made by fin's
code at main f759090). Every merchant, figure and name here is invented.
"""

import sqlite3

import pytest

import book_type
import db
import history
from test_book_and_type_switch import confirm as confirm_with, fake_statement, upload  # noqa: F401 (fixture)
from test_movements_and_review import BANK, bring_in, ledger, row  # noqa: F401 (fixture)
from test_type_keys import book_before, every_row, kept_every_row

CHILDCARE = {"name": "Childcare", "covers": "nannies, after-school care, holiday camps"}


def types(client) -> dict[str, dict]:
    return {t["display_name"]: t for t in client.get("/api/types").get_json()}


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def change(client, display: str, **body):
    return client.put(f"/api/types/{types(client)[display]['id']}", json=body)


def merchants(client) -> dict[str, dict]:
    return {s["name"]: s for s in client.get("/api/services").get_json()}


def teach_new(client, description: str, name: str, display: str, **extra):
    """Teach a row from the Queue as a new merchant of a type, no book sent."""
    return client.post("/api/transactions/resolve", json={
        "tx_id": row(client, description)["id"], "service_name": name,
        "type_id": types(client)[display]["id"], **extra,
    })


def held(temp_db) -> tuple:
    """Every type, setting, merchant and change entry, straight from the book."""
    conn = sqlite3.connect(str(temp_db))
    try:
        return tuple(conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()
                     for t in ("types", "type_settings", "services", "change_entries"))
    finally:
        conn.close()


# --- case 3: the usual book is read, changed, and reaches new merchants only ---


def test_case_3_advertisings_usual_book_changes_to_household_for_new_merchants_only(client, ledger):
    assert types(client)["Advertising"]["usual_book"] == "Moom"
    bring_in(client, BANK, [(3, "SAMPLE ADS NETWORK 0042", 80.00), (4, "SAMPLE PROMO BOARD 0077", 45.00)])
    assert teach_new(client, "SAMPLE ADS NETWORK 0042", "Sample Ads Network", "Advertising").status_code == 200
    assert merchants(client)["Sample Ads Network"]["book"] == "Moom"
    before = len(entries(client))

    resp = change(client, "Advertising", usual_book="Household")

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["type"]["usual_book"] == "Household"
    assert types(client)["Advertising"]["usual_book"] == "Household"
    assert types(client)["Advertising"]["proposed_book"] == "Household"
    assert len(entries(client)) == before + 1
    assert entries(client)[0]["summary"] == "Changed Advertising: usual book Household"
    # A new merchant typed Advertising is proposed Household ...
    assert teach_new(client, "SAMPLE PROMO BOARD 0077", "Sample Promo Board", "Advertising").status_code == 200
    assert merchants(client)["Sample Promo Board"]["book"] == "Household"
    assert row(client, "SAMPLE PROMO BOARD 0077")["book"] == "Household"
    # ... and the one fin held keeps its book, as does its row.
    assert merchants(client)["Sample Ads Network"]["book"] == "Moom"
    assert row(client, "SAMPLE ADS NETWORK 0042")["book"] == "Moom"


# --- case 4: a type that asks still asks ------------------------------------------


def test_case_4_software_reads_ask_and_a_new_merchant_of_it_needs_a_book(client, ledger):
    bring_in(client, BANK, [(5, "SAMPLE CLOUD HOST 0042", 30.00)])

    shown = types(client)["Software & AI tools"]
    resp = teach_new(client, "SAMPLE CLOUD HOST 0042", "Sample Cloud Host", "Software & AI tools")

    assert (shown["usual_book"], shown["proposed_book"]) == ("ask", None)
    assert resp.status_code == 400
    assert "book is required" in resp.get_json()["error"]


def test_a_type_set_to_ask_makes_a_new_merchant_of_it_need_a_book(client, ledger):
    bring_in(client, BANK, [(5, "SAMPLE PET SHOP 0042", 30.00)])
    assert change(client, "Pet", usual_book="ask").status_code == 200

    resp = teach_new(client, "SAMPLE PET SHOP 0042", "Sample Pet Shop", "Pet")

    assert resp.status_code == 400
    assert "book is required" in resp.get_json()["error"]


# --- case 5: an added type proposes Household until changed ----------------------


def test_case_5_an_added_type_proposes_household_until_its_usual_book_is_changed(client, ledger):
    kids = types(client)["Kids"]["id"]
    assert client.post("/api/types", json={**CHILDCARE, "parent_id": kids}).status_code == 201
    bring_in(client, BANK, [(6, "SAMPLE NANNY CO 0042", 300.00), (7, "SAMPLE PLAY CAMP 0077", 120.00)])

    assert types(client)["Kids > Childcare"]["usual_book"] == "Household"
    assert teach_new(client, "SAMPLE NANNY CO 0042", "Sample Nanny Co", "Kids > Childcare").status_code == 200
    assert merchants(client)["Sample Nanny Co"]["book"] == "Household"

    assert change(client, "Kids > Childcare", usual_book="Kalesh").status_code == 200

    assert types(client)["Kids > Childcare"]["usual_book"] == "Kalesh"
    assert teach_new(client, "SAMPLE PLAY CAMP 0077", "Sample Play Camp", "Kids > Childcare").status_code == 200
    assert merchants(client)["Sample Play Camp"]["book"] == "Kalesh"
    assert merchants(client)["Sample Nanny Co"]["book"] == "Household"


# --- case 6: one-off by default changes through the same route --------------------


def test_case_6_one_off_by_default_changes_as_one_entry(client):
    before = len(entries(client))

    resp = change(client, "Travel", one_off=True)

    assert resp.status_code == 200, resp.get_json()
    assert types(client)["Travel"]["default_one_off"] == 1
    assert len(entries(client)) == before + 1
    assert entries(client)[0]["summary"] == "Changed Travel: one-off by default"
    assert change(client, "Travel", one_off=False).status_code == 200
    assert types(client)["Travel"]["default_one_off"] == 0
    assert entries(client)[0]["summary"] == "Changed Travel: running by default"


# --- case 7: Check each row by default reaches new merchants only ------------------


def test_case_7_check_each_row_by_default_on_medical_reaches_new_merchants_only(client, ledger):
    bring_in(client, BANK, [(8, "SAMPLE CLINIC 0042", 90.00), (9, "SAMPLE DENTAL 0077", 150.00)])
    assert teach_new(client, "SAMPLE CLINIC 0042", "Sample Clinic", "Medical").status_code == 200
    assert types(client)["Medical"]["check_each_row"] is False
    before = len(entries(client))

    resp = change(client, "Medical", check_each_row=True)

    assert resp.status_code == 200, resp.get_json()
    assert types(client)["Medical"]["check_each_row"] is True
    assert len(entries(client)) == before + 1
    assert entries(client)[0]["summary"] == "Changed Medical: Check each row by default"
    assert teach_new(client, "SAMPLE DENTAL 0077", "Sample Dental", "Medical").status_code == 200
    assert merchants(client)["Sample Dental"]["review_each_time"] == 1
    assert merchants(client)["Sample Clinic"]["review_each_time"] == 0


def test_a_teach_that_says_every_row_the_same_wins_over_the_types_default(client, ledger):
    bring_in(client, BANK, [(9, "SAMPLE DENTAL 0077", 150.00)])
    assert change(client, "Medical", check_each_row=True).status_code == 200

    resp = teach_new(client, "SAMPLE DENTAL 0077", "Sample Dental", "Medical", check_each_row=False)

    assert resp.status_code == 200, resp.get_json()
    assert merchants(client)["Sample Dental"]["review_each_time"] == 0


def test_a_merchant_added_on_its_own_takes_the_types_check_each_row_unless_told(client):
    assert change(client, "Medical", check_each_row=True).status_code == 200
    medical = types(client)["Medical"]["id"]

    told = client.post("/api/services", json={"name": "Sample Physio", "type_id": medical, "review_each_time": 0})
    untold = client.post("/api/services", json={"name": "Sample Clinic", "type_id": medical})

    assert told.status_code == untold.status_code == 200
    assert merchants(client)["Sample Physio"]["review_each_time"] == 0
    assert merchants(client)["Sample Clinic"]["review_each_time"] == 1


def test_a_merchant_made_at_import_takes_its_types_settings(client, conn, fake_statement):  # noqa: F811
    assert change(client, "Medical", check_each_row=True, usual_book="Kalesh").status_code == 200
    medical = types(client)["Medical"]["id"]
    fake_statement([("2026-04-04", "NEW SAMPLE CLINIC", 88.00)])
    preview = upload(client)
    preview["groups"][0]["transactions"][0].update(type_id=medical)

    resp = confirm_with(client, preview, new_services=[{
        "name": "New Sample Clinic", "type_id": medical, "description": "NEW SAMPLE CLINIC",
    }])

    assert resp.status_code == 200, resp.get_json()
    made = merchants(client)["New Sample Clinic"]
    assert (made["book"], made["review_each_time"]) == ("Kalesh", 1)


# --- case 8: Undo restores the value ------------------------------------------------


def test_case_8_undo_of_a_settings_change_restores_the_old_values(client):
    assert change(client, "Advertising", usual_book="Kalesh", one_off=True, check_each_row=True).status_code == 200
    assert entries(client)[0]["summary"] == (
        "Changed Advertising: usual book Kalesh, one-off by default, Check each row by default")

    resp = client.post(f"/api/history/{entries(client)[0]['id']}/undo")

    assert resp.status_code == 200, resp.get_json()
    shown = types(client)["Advertising"]
    assert (shown["usual_book"], shown["default_one_off"], shown["check_each_row"]) == ("Moom", 0, False)


def test_undo_of_an_added_types_first_setting_reads_household_again(client):
    kids = types(client)["Kids"]["id"]
    client.post("/api/types", json={**CHILDCARE, "parent_id": kids})
    assert change(client, "Kids > Childcare", usual_book="Moom").status_code == 200

    assert client.post(f"/api/history/{entries(client)[0]['id']}/undo").status_code == 200

    assert types(client)["Kids > Childcare"]["usual_book"] == "Household"


def test_undoing_the_add_of_a_type_whose_settings_changed_since_is_refused_in_words(client, temp_db):
    kids = types(client)["Kids"]["id"]
    client.post("/api/types", json={**CHILDCARE, "parent_id": kids})
    added = entries(client)[0]
    assert change(client, "Kids > Childcare", usual_book="Moom").status_code == 200
    before = held(temp_db)

    resp = client.post(f"/api/history/{added['id']}/undo")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "Kids > Childcare's settings were changed since; undo that first"
    assert held(temp_db) == before


# --- refusals: out of vocabulary, in words, writing nothing ---------------------------


@pytest.mark.parametrize("display, body, reason", [
    ("Advertising", {"usual_book": "Business"}, "the usual book is one of Household, Moom, Kalesh or ask"),
    ("Advertising", {"usual_book": None}, "the usual book is one of Household, Moom, Kalesh or ask"),
    ("Advertising", {"one_off": "yes"}, "one_off must be true or false"),
    ("Advertising", {"check_each_row": 1}, "check_each_row must be true or false"),
    ("Advertising", {"usual_book": "Moom", "check_each_row": "no"}, "check_each_row must be true or false"),
    ("Advertising", {}, "say what to change about the type"),
    ("Other", {"usual_book": "Moom"}, "Other means not placed yet: its settings are fixed"),
    ("Other", {"check_each_row": True}, "Other means not placed yet: its settings are fixed"),
])
def test_a_setting_out_of_vocabulary_is_refused_in_words_and_writes_nothing(client, temp_db, display, body, reason):
    before = held(temp_db)

    resp = change(client, display, **body)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == reason
    assert held(temp_db) == before


def test_an_income_kinds_settings_are_refused(client, temp_db):
    salary = client.get("/api/types?kind=income").get_json()[0]["id"]
    before = held(temp_db)

    resp = client.put(f"/api/types/{salary}", json={"usual_book": "Moom"})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "income kinds are set in code"
    assert held(temp_db) == before


def test_the_same_value_again_writes_no_entry(client):
    before = len(entries(client))

    assert change(client, "Advertising", usual_book="Moom", check_each_row=False).status_code == 200

    assert len(entries(client)) == before


def test_wording_and_settings_together_are_one_entry(client):
    before = len(entries(client))

    assert change(client, "Dining", covers="restaurants and cafes", usual_book="Kalesh").status_code == 200

    assert len(entries(client)) == before + 1
    assert entries(client)[0]["summary"] == "Changed Dining: usual book Kalesh, wording fixed"


# --- the start: seeded from the code, never overwriting the operator ---------------


def test_a_start_keeps_the_operators_value_and_writes_no_entry(client, temp_db):
    assert change(client, "Advertising", usual_book="Household", check_each_row=True).status_code == 200
    before = len(entries(client))

    db.init_db()

    shown = types(client)["Advertising"]
    assert (shown["usual_book"], shown["check_each_row"]) == ("Household", True)
    assert len(entries(client)) == before


def test_a_book_from_before_this_effort_starts_seeds_the_settings_and_changes_no_row(
        tmp_path, monkeypatch, client):
    path = book_before(tmp_path, monkeypatch)
    rows_before = every_row(path)

    db.init_db()
    db.init_db()

    after = every_row(path)
    kept_every_row(rows_before, after)
    conn = sqlite3.connect(str(path))
    try:
        seeded = dict(conn.execute(
            "SELECT k.key, s.usual_book || '/' || s.check_each_row FROM type_settings s"
            " JOIN type_keys k ON k.type_id = s.type_id"))
        count = conn.execute("SELECT COUNT(*) FROM type_settings").fetchone()[0]
        assert history.has_shape(conn)
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    finally:
        conn.close()
    assert count == len(book_type.SPENDING_TYPES)  # every declared spending type, and no other
    assert seeded["Advertising"] == seeded["Stock purchases"] == seeded["Payment fees"] == "Moom/0"
    assert seeded["Software & AI tools"] == "ask/0"
    assert seeded["Dining"] == seeded["Other"] == seeded["Fitness"] == "Household/0"
    assert {f"history_type_settings_{op}" for op in ("insert", "update", "delete")} <= triggers
    # The type added in the app before this effort has no row, and reads Household.
    assert types(client)["Kids > Childcare"]["usual_book"] == "Household"


def test_type_settings_is_tracked_and_its_triggers_come_with_a_start():
    assert "type_settings" in history.TRACKED and "type_settings" in history.ADDED_ON_START
    assert len(history.table_triggers("type_settings")) == 3
