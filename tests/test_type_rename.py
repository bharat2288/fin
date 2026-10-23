"""Renaming a type in the app (type-relationships 04, cases 13 to 17).

PUT /api/types/<id> takes `name` for a spending type, checked as an added
type's name is. The type keeps its id, so its merchants, rules and rows keep
pointing at it, and code keeps finding it by its key. Jev's stored answers
name types by display name ('Fitness > Golf'): a rename re-points them, a
parent's rename its sub-types' too, and Undo points them back. Other cannot
be renamed (its meaning is fixed), nor an income kind; merging and removing
stay code changes (fin RUL-006 Q5).

Driven through the HTTP interface against a temporary book; the book from
before this effort is tests/fixtures/book_before_type_keys.sql (made by fin's
code at main f759090). Every merchant, figure and name here is invented, and
the key is a made-up word.
"""

import socket
import sqlite3

import pytest

import db
import suggest
from test_type_keys import book_before, every_row, kept_every_row
from test_type_suggestion import NOT_A_KEY, FakeModel, prepare_and_send, row, statement_on


@pytest.fixture(autouse=True)
def network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args, **kwargs):
        raise OSError("tests make no network calls")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    monkeypatch.setenv(suggest.KEY_ENV, NOT_A_KEY)
    fake = FakeModel()
    monkeypatch.setattr(suggest, "post", fake)
    return fake


def types(client) -> dict[str, dict]:
    return {t["display_name"]: t for t in client.get("/api/types").get_json()}


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def rename(client, display: str, name, **extra):
    return client.put(f"/api/types/{types(client)[display]['id']}", json={"name": name, **extra})


def undo_last(client):
    resp = client.post(f"/api/history/{entries(client)[0]['id']}/undo")
    assert resp.status_code == 200, resp.get_json()


def offered(client, tx: int) -> tuple[str, list[tuple[str, int]]]:
    """What the resolve dialog is offered for a row: the route and each type
    by (name, id)."""
    got = client.get(f"/api/transactions/{tx}/suggestion").get_json()
    return got["route"], [(t["name"], t["type_id"]) for t in got["types"]]


def answers(client) -> dict[str, str]:
    """Each stored answer's pick, by merchant string."""
    return {a["merchant"]: a["pick"] for a in client.get("/api/suggestions/answers").get_json()}


def held(temp_db) -> tuple:
    """Every type, merchant, rule, row, stored answer and change entry,
    straight from the book."""
    conn = sqlite3.connect(str(temp_db))
    try:
        return tuple(conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in (
            "types", "type_keys", "services", "merchant_rules", "transactions", "suggestion_answers",
            "change_entries"))
    finally:
        conn.close()


@pytest.fixture
def asked(client, conn, model):
    """Two card rows Jev has answered: a noodle bar he is sure is Dining, and
    a driving range he thinks is Golf, perhaps Fitness or Entertainment."""
    card = statement_on(conn, "card")
    noodles = row(conn, card, "SAMPLE NOODLE BAR")
    range_ = row(conn, card, "SAMPLE DRIVING RANGE")
    model.answer("SAMPLE NOODLE BAR", "Dining", 0.95)
    model.answer("SAMPLE DRIVING RANGE", "Fitness > Golf", 0.6, **{"Fitness": 0.2, "Entertainment": 0.1})
    assert prepare_and_send(client)["sent"] == 2
    return {"noodles": noodles, "range": range_}


# --- case 13: Dining to Food, everywhere ---------------------------------------------


def test_case_13_dining_renamed_to_food_is_one_entry_and_reads_food_everywhere(client, temp_db, asked):
    dining = types(client)["Dining"]["id"]
    conn = sqlite3.connect(str(temp_db))
    labels = [conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()
              for t in ("services", "merchant_rules", "transactions", "type_keys")]
    conn.close()

    resp = rename(client, "Dining", "Food")

    assert resp.status_code == 200, resp.get_json()
    assert (resp.get_json()["type"]["id"], resp.get_json()["type"]["display_name"]) == (dining, "Food")
    [entry] = [e for e in entries(client) if e["summary"].startswith("Renamed")]
    assert (entry["summary"], entry["rows"]) == ("Renamed Dining to Food", 1)
    assert entries(client)[0]["id"] == entry["id"]
    shown = types(client)
    assert shown["Food"]["id"] == dining and "Dining" not in shown
    # Jev's stored answer that picked Dining now offers Food, by Dining's id.
    assert offered(client, asked["noodles"]) == ("prefill", [("Food", dining)])
    assert answers(client)["SAMPLE NOODLE BAR"] == "Food"
    # Merchants, rules, rows and keys are untouched: they hold the id.
    conn = sqlite3.connect(str(temp_db))
    try:
        assert [conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()
                for t in ("services", "merchant_rules", "transactions", "type_keys")] == labels
    finally:
        conn.close()


def test_case_13_jev_is_asked_with_food_and_not_dining(client, conn, model):
    assert rename(client, "Dining", "Food").status_code == 200
    row(conn, statement_on(conn, "card"), "SAMPLE DUMPLING HOUSE")

    assert prepare_and_send(client)["sent"] == 1

    criteria = model.requests[-1]["questions"]["type"]["criteria"]
    assert "Food" in criteria and "Dining" not in criteria
    assert criteria["Food"]["what"].startswith("restaurants, bars")


def test_case_13_a_merchant_typed_dining_reads_food(client):
    culina = {s["name"]: s for s in client.get("/api/services").get_json()}["Culina"]
    assert rename(client, "Dining", "Food").status_code == 200

    after = {s["name"]: s for s in client.get("/api/services").get_json()}["Culina"]
    assert after["type_id"] == culina["type_id"]
    assert after["display_type"] == "Food"


def test_a_renamed_type_is_not_seeded_again_on_the_next_start(client):
    assert rename(client, "Dining", "Food").status_code == 200
    count = len(types(client))

    db.init_db()

    assert len(types(client)) == count and "Dining" not in types(client)


def test_a_type_code_means_can_be_renamed_and_keeps_its_meaning(client):
    assert rename(client, "Advertising", "Ads").status_code == 200

    shown = types(client)["Ads"]
    assert (shown["usual_book"], shown["proposed_book"], shown["locked"]) == ("Moom", "Moom", False)


def test_a_change_of_case_alone_is_a_rename(client):
    resp = rename(client, "Dining", "DINING")

    assert resp.status_code == 200, resp.get_json()
    assert "DINING" in types(client)


def test_the_same_name_again_writes_no_entry(client):
    before = len(entries(client))

    assert rename(client, "Dining", " Dining ").status_code == 200

    assert len(entries(client)) == before


def test_a_rename_with_a_setting_is_one_entry_naming_both(client):
    assert rename(client, "Dining", "Food", usual_book="Kalesh").status_code == 200

    assert entries(client)[0]["summary"] == "Changed Food: renamed from Dining, usual book Kalesh"
    assert types(client)["Food"]["usual_book"] == "Kalesh"


def test_a_rename_with_the_wording_is_one_entry_naming_both(client):
    assert rename(client, "Dining", "Food", covers="restaurants and cafes").status_code == 200

    assert entries(client)[0]["summary"] == "Renamed Dining to Food, wording fixed"


# --- case 14: a parent's rename reaches its sub-types ----------------------------------


def test_case_14_fitness_renamed_to_sport_reads_sport_golf_and_re_points_its_answers(client, asked):
    golf, fitness = types(client)["Fitness > Golf"]["id"], types(client)["Fitness"]["id"]

    assert rename(client, "Fitness", "Sport").status_code == 200

    assert entries(client)[0]["summary"] == "Renamed Fitness to Sport"
    shown = types(client)
    assert shown["Sport > Golf"]["id"] == golf and "Fitness > Golf" not in shown
    assert answers(client)["SAMPLE DRIVING RANGE"] == "Sport > Golf"
    route, types_offered = offered(client, asked["range"])
    assert route == "top3"
    assert types_offered[:2] == [("Sport > Golf", golf), ("Sport", fitness)]


def test_case_14_a_sub_types_rename_reaches_its_answers(client, asked):
    golf = types(client)["Fitness > Golf"]["id"]

    assert rename(client, "Fitness > Golf", "Driving ranges").status_code == 200

    assert entries(client)[0]["summary"] == "Renamed Fitness > Golf to Fitness > Driving ranges"
    assert answers(client)["SAMPLE DRIVING RANGE"] == "Fitness > Driving ranges"
    assert offered(client, asked["range"])[1][0] == ("Fitness > Driving ranges", golf)


# --- case 15: refused, writing nothing ------------------------------------------------


@pytest.mark.parametrize("name, reason", [
    ("groceries", "Groceries is already a type"),
    ("  GOLF ", "Fitness > Golf is already a type"),
    ("Other", "Other is already a type"),
    ("", "a type needs a name"),
    ("   ", "a type needs a name"),
    (None, "a type needs a name"),
    (7, "name must be words"),
    ("Food > Thai", "a type's name cannot hold \">\""),
    ("Food <b", "a type's name cannot hold \"<\""),
    ("Food\nand drink", "a type's name cannot hold a line break or control character"),
    ("Food​", "a type's name cannot hold a line break or control character"),
    ("Food‮drink", "a type's name cannot hold a line break or control character"),
    ("F" * 41, "a type's name can be at most 40 characters"),
    ("None of these", "\"None of these\" is what Jev answers when no type fits"),
])
def test_case_15_a_name_that_breaks_a_rule_is_refused_and_writes_nothing(client, temp_db, asked, name, reason):
    before = held(temp_db)

    resp = rename(client, "Dining", name)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == reason
    assert held(temp_db) == before


def test_case_15_forty_characters_is_a_name(client):
    assert rename(client, "Dining", "F" * 40).status_code == 200


def test_case_15_a_refused_name_takes_the_settings_sent_with_it_down_too(client, temp_db):
    before = held(temp_db)

    resp = rename(client, "Dining", "Groceries", usual_book="Kalesh")

    assert resp.status_code == 400
    assert held(temp_db) == before
    assert types(client)["Dining"]["usual_book"] == "Household"


# --- case 16: Undo restores the name and the answers ----------------------------------


def test_case_16_undo_of_a_rename_restores_the_name_and_the_answers(client, asked):
    dining = types(client)["Dining"]["id"]
    assert rename(client, "Dining", "Food").status_code == 200

    undo_last(client)

    assert types(client)["Dining"]["id"] == dining and "Food" not in types(client)
    assert answers(client)["SAMPLE NOODLE BAR"] == "Dining"
    assert offered(client, asked["noodles"]) == ("prefill", [("Dining", dining)])


def test_case_16_undo_of_a_parents_rename_restores_its_sub_types_answers(client, asked):
    golf = types(client)["Fitness > Golf"]["id"]
    assert rename(client, "Fitness", "Sport").status_code == 200

    undo_last(client)

    assert answers(client)["SAMPLE DRIVING RANGE"] == "Fitness > Golf"
    assert offered(client, asked["range"])[1][0] == ("Fitness > Golf", golf)


def test_undoing_the_undo_renames_again_and_re_points_again(client, asked):
    assert rename(client, "Dining", "Food").status_code == 200
    undo_last(client)

    undo_last(client)

    assert "Food" in types(client)
    assert answers(client)["SAMPLE NOODLE BAR"] == "Food"


def test_undo_of_a_rename_a_later_rename_changed_is_refused(client, temp_db):
    assert rename(client, "Dining", "Food").status_code == 200
    first = entries(client)[0]["id"]
    assert rename(client, "Food", "Eating out").status_code == 200
    before = held(temp_db)

    resp = client.post(f"/api/history/{first}/undo")

    assert resp.status_code == 409
    assert held(temp_db) == before


# --- case 17: Other and income kinds keep their names ---------------------------------


def test_case_17_other_cannot_be_renamed_and_nothing_is_written(client, temp_db):
    before = held(temp_db)

    resp = rename(client, "Other", "Not placed yet")

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Other means not placed yet: its name is fixed"
    assert held(temp_db) == before
    assert types(client)["Other"]["locked"] is True


def test_case_17_others_wording_is_still_fixed(client):
    assert client.put(f"/api/types/{types(client)['Other']['id']}",
                      json={"covers": "spending nobody has placed yet"}).status_code == 200


def test_case_17_an_income_kind_cannot_be_renamed(client, temp_db):
    salary = client.get("/api/types?kind=income").get_json()[0]["id"]
    before = held(temp_db)

    resp = client.put(f"/api/types/{salary}", json={"name": "Wages"})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "income kinds are set in code"
    assert held(temp_db) == before


@pytest.mark.parametrize("extra", [{"parent_id": 1}, {"kind": "income"}, {"default_one_off": 1}])
def test_case_17_moving_a_type_is_still_a_code_change(client, temp_db, extra):
    before = held(temp_db)

    resp = rename(client, "Dining", "Food", **extra)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "moving, merging or removing a type is a code change"
    assert held(temp_db) == before


# --- a book from before this effort --------------------------------------------------


def test_a_book_from_before_this_effort_starts_unchanged_and_renames(tmp_path, monkeypatch, client):
    path = book_before(tmp_path, monkeypatch)
    rows_before = every_row(path)

    db.init_db()
    db.init_db()

    after = every_row(path)
    kept_every_row(rows_before, after)
    golf = types(client)["Fitness > Golf"]["id"]
    assert rename(client, "Fitness", "Sport").status_code == 200
    assert types(client)["Sport > Golf"]["id"] == golf
    undo_last(client)
    assert types(client)["Fitness > Golf"]["id"] == golf
