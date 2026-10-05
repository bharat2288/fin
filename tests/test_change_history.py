"""The change history: every change, from the app or a chat client, one entry
that can be undone (fin-surfaces 01, Q3 and Q4).

Driven through the HTTP interface against a temporary database. Every name
and figure is invented.
"""

import re
import sqlite3
from pathlib import Path

import pytest

import access_gate
import conversion
import convert_change_history
import db
import history
from test_balance_sheet import bring_in, enter, make_account
from test_import_that_ties import stand_in, upload  # noqa: F401 (a fixture)

BANK = "Sample Bank 0002"
RENO = "Sample Reno 0003"
ROWS = [("2026-08-03", "SAMPLE GROCER", 14000), ("2026-08-28", "SAMPLE CAFE", 15950)]


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def a_row(client) -> dict:
    return client.get("/api/transactions?per_page=50").get_json()["transactions"][0]


def type_ids(client) -> list[int]:
    listed = client.get("/api/types").get_json()
    flat = listed if isinstance(listed, list) else listed.get("types", [])
    ids = []
    for t in flat:
        ids.append(t["id"])
        ids.extend(c["id"] for c in t.get("children", []))
    return ids


@pytest.fixture
def imported(client):
    bring_in(client, BANK, ROWS, opening=100000, closing=100000 - 14000 - 15950)
    return client


def label(client, tx_id: int, **body):
    return client.put(f"/api/transactions/{tx_id}", json=body)


# --- what is recorded -------------------------------------------------------


def test_a_fresh_book_has_no_history(client):
    assert entries(client) == []


def test_reading_records_nothing(imported):
    before = entries(imported)
    imported.get("/api/balance-sheet?month=2026-08")
    imported.get("/api/transactions")
    assert entries(imported) == before


def test_a_label_set_in_the_app_is_one_entry_with_before_and_after(imported):
    tx = a_row(imported)
    resp = label(imported, tx["id"], book="Moom")
    assert resp.status_code == 200
    assert resp.headers["X-Fin-Change-Rows"] == "1"

    [latest] = entries(imported)[:1]
    assert (latest["via"], latest["actor"], latest["summary"], latest["rows"]) == (
        "app", "fin", "Changed a row's labels", 1,
    )
    shown = imported.get(f"/api/history/{latest['id']}").get_json()
    [change] = shown["changes"]
    assert (change["table"], change["row_id"], change["op"]) == ("transactions", tx["id"], "update")
    assert change["before"]["book"] != "Moom"
    assert (change["after"]["book"], change["after"]["cat_source"]) == ("Moom", "manual")


def test_a_write_that_changes_nothing_leaves_no_entry(imported):
    tx = a_row(imported)
    label(imported, tx["id"], book="Moom")
    count = len(entries(imported))
    label(imported, tx["id"], book="Moom")
    assert len(entries(imported)) == count


def test_a_refused_write_leaves_no_entry(imported):
    count = len(entries(imported))
    resp = label(imported, a_row(imported)["id"], book="Nobody")
    assert resp.status_code == 400
    assert len(entries(imported)) == count


def test_an_import_is_one_entry_and_hides_the_file_name(client):
    bring_in(client, BANK, ROWS, opening=100000, closing=100000 - 14000 - 15950)
    imports = [e for e in entries(client) if e["summary"] == "Imported a statement"]
    assert len(imports) == 1
    shown = client.get(f"/api/history/{imports[0]['id']}").get_json()
    tables = {c["table"] for c in shown["changes"]}
    assert {"transactions", "statements", "anchors"} <= tables
    for change in shown["changes"]:
        if change["table"] == "statements":
            assert "filename" not in (change["after"] or {})


def test_a_chat_write_is_stamped_with_its_client(imported):
    tx = a_row(imported)
    imported.put(
        f"/api/transactions/{tx['id']}", json={"book": "Kalesh"},
        environ_base={access_gate.CHAT_CALL_ENVIRON_KEY: access_gate.chat_call_identity("chat:sample-subject")},
    )
    latest = entries(imported)[0]
    assert (latest["via"], latest["actor"]) == ("chat", "chat:sample-subject")


def test_a_chat_call_with_no_subject_is_labelled_chat_as_the_gate_labels_it():
    assert access_gate.chat_call_identity("").actor == "chat"
    assert access_gate.chat_call_identity("chat:sample-subject").via == "chat"


def test_only_the_gate_or_a_chat_call_says_who_asked(imported):
    # fin.via and fin.actor are set by Flask's second layer from the identity
    # it admitted, so a caller putting them in the environ directly is not
    # believed.
    tx = a_row(imported)
    imported.put(f"/api/transactions/{tx['id']}", json={"book": "Kalesh"},
                 environ_base={"fin.via": "chat", "fin.actor": "Claude"})
    latest = entries(imported)[0]
    assert (latest["via"], latest["actor"]) == ("app", "fin")


def test_a_header_cannot_claim_to_be_chat(imported):
    tx = a_row(imported)
    imported.put(f"/api/transactions/{tx['id']}", json={"book": "Kalesh"},
                 headers={"fin.via": "chat", "X-Fin-Actor": "Claude"})
    latest = entries(imported)[0]
    assert (latest["via"], latest["actor"]) == ("app", "fin")


def test_a_rows_history_lists_the_entries_that_touched_it(imported):
    tx = a_row(imported)
    label(imported, tx["id"], book="Moom")
    label(imported, tx["id"], notes="checked")
    listed = imported.get(f"/api/history/row/transactions/{tx['id']}").get_json()["entries"]
    assert [e["summary"] for e in listed[:2]] == ["Changed a row's labels", "Changed a row's labels"]
    assert imported.get("/api/history/row/sqlite_master/1").status_code == 404


# --- undo -------------------------------------------------------------------


def test_undo_puts_a_label_back_and_is_itself_an_entry(imported):
    tx = a_row(imported)
    label(imported, tx["id"], book="Moom", notes="moved")
    changed = entries(imported)[0]

    resp = imported.post(f"/api/history/{changed['id']}/undo")

    assert resp.status_code == 200, resp.get_json()
    back = next(t for t in imported.get("/api/transactions?per_page=50").get_json()["transactions"]
                if t["id"] == tx["id"])
    assert (back["book"], back["notes"], back["cat_source"]) == (tx["book"], tx["notes"], tx["cat_source"])
    undo_entry, original = entries(imported)[:2]
    assert undo_entry["summary"] == "Undid: Changed a row's labels"
    assert (undo_entry["undoes"], original["undone_by"]) == (changed["id"], undo_entry["id"])


def test_undo_is_refused_when_a_later_change_touched_the_same_row(imported):
    tx = a_row(imported)
    label(imported, tx["id"], book="Moom")
    first = entries(imported)[0]
    label(imported, tx["id"], book="Kalesh")
    second = entries(imported)[0]

    resp = imported.post(f"/api/history/{first['id']}/undo")

    assert resp.status_code == 409
    assert f"change {second['id']}" in resp.get_json()["error"]
    assert a_row(imported)["book"] == "Kalesh"
    shown = imported.get(f"/api/history/{first['id']}").get_json()
    assert shown["blocked_by"]["id"] == second["id"]
    # Undo the later one first, then the earlier one goes.
    assert imported.post(f"/api/history/{second['id']}/undo").status_code == 200
    assert imported.post(f"/api/history/{first['id']}/undo").status_code == 200
    assert a_row(imported)["book"] == tx["book"]


def test_a_change_to_other_rows_does_not_block(imported):
    first_tx, second_tx = imported.get("/api/transactions?per_page=50").get_json()["transactions"][:2]
    label(imported, first_tx["id"], book="Moom")
    first = entries(imported)[0]
    label(imported, second_tx["id"], book="Moom")
    assert imported.post(f"/api/history/{first['id']}/undo").status_code == 200


def test_undo_twice_is_refused_and_undo_of_the_undo_redoes(imported):
    tx = a_row(imported)
    label(imported, tx["id"], book="Moom")
    changed = entries(imported)[0]
    imported.post(f"/api/history/{changed['id']}/undo")

    again = imported.post(f"/api/history/{changed['id']}/undo")
    assert again.status_code == 409
    assert "already undone" in again.get_json()["error"]

    undo_entry = entries(imported)[0]
    assert imported.post(f"/api/history/{undo_entry['id']}/undo").status_code == 200
    assert a_row(imported)["book"] == "Moom"


def test_an_unknown_change_cannot_be_undone(client):
    assert client.post("/api/history/999/undo").status_code == 409
    assert client.get("/api/history/999").status_code == 404


def test_undoing_an_import_removes_its_rows_and_its_anchor(client, conn):
    bring_in(client, BANK, ROWS, opening=100000, closing=100000 - 14000 - 15950)
    imported = next(e for e in entries(client) if e["summary"] == "Imported a statement")

    resp = client.post(f"/api/history/{imported['id']}/undo")

    assert resp.status_code == 200, resp.get_json()
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM anchors").fetchone()[0] == 0


def test_undoing_an_import_is_refused_once_one_of_its_rows_was_labelled(imported):
    import_entry = next(e for e in entries(imported) if e["summary"] == "Imported a statement")
    label(imported, a_row(imported)["id"], book="Moom")
    assert imported.post(f"/api/history/{import_entry['id']}/undo").status_code == 409


def test_a_deleted_figure_comes_back_on_undo(client):
    reno = make_account(client, RENO, "bank")
    enter(client, reno, "1000.00", "2026-06-30")
    [figure] = client.get(f"/api/anchors?account_id={reno}").get_json()
    assert client.delete(f"/api/anchors/{figure['id']}").status_code == 200
    deleted = entries(client)[0]

    assert client.post(f"/api/history/{deleted['id']}/undo").status_code == 200

    assert client.get(f"/api/anchors?account_id={reno}").get_json() == [figure]


# --- the shape --------------------------------------------------------------


def test_every_column_of_every_tracked_table_is_recorded(conn):
    for table in history.TRACKED:
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        for op in ("insert", "update", "delete"):
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                (f"history_{table}_{op}",),
            ).fetchone()[0]
            recorded = set(re.findall(r"'(\w+)', (?:NEW|OLD)\.\1\b", sql))
            assert recorded == set(columns), (table, op, set(columns) ^ recorded)


def test_seeding_records_nothing(temp_db):
    db.init_db()
    conn = sqlite3.connect(str(temp_db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM change_rows").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.fixture
def converted(tmp_path, monkeypatch):
    """A book in the oldest shape taken through every step, then stripped of
    the history: the shape a real book has before this step runs."""
    import convert_all

    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript((Path(__file__).parent / "schema_before_book_and_type.sql").read_text())
    conn.close()
    assert convert_all.main([str(path)]) == 0
    conn = sqlite3.connect(str(path))
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'history_%'"
    ).fetchall():
        conn.execute(f"DROP TRIGGER {name}")
    conn.execute("DROP TABLE change_rows")
    conn.execute("DROP TABLE change_entries")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


def test_a_book_from_before_the_step_will_not_start_and_names_the_step(converted):
    with pytest.raises(db.DatabaseNotConverted, match="change-history"):
        db.init_db()


def test_the_step_adds_the_history_and_run_twice_changes_nothing(converted):
    first = conversion.run_step(converted, convert_change_history.STEP)
    second = conversion.run_step(converted, convert_change_history.STEP)

    assert (first["status"], second["status"]) == ("applied", "already-applied")
    assert first["backup"]
    conn = sqlite3.connect(str(converted))
    try:
        assert history.has_shape(conn)
    finally:
        conn.close()
    db.init_db()  # starts


def test_the_step_refuses_a_tracked_table_missing_a_column(converted):
    conn = sqlite3.connect(str(converted))
    conn.execute("ALTER TABLE rates DROP COLUMN fetched_at")
    conn.commit()
    conn.close()

    with pytest.raises(conversion.ConversionRefused, match="rates has no fetched_at"):
        conversion.run_step(converted, convert_change_history.STEP)


def test_a_later_change_that_was_undone_and_redone_still_blocks(imported):
    tx = a_row(imported)
    label(imported, tx["id"], book="Moom")
    first = entries(imported)[0]
    label(imported, tx["id"], book="Kalesh")
    second = entries(imported)[0]
    imported.post(f"/api/history/{second['id']}/undo")
    undo_entry = entries(imported)[0]
    imported.post(f"/api/history/{undo_entry['id']}/undo")  # redo: Kalesh again

    resp = imported.post(f"/api/history/{first['id']}/undo")

    assert resp.status_code == 409
    assert a_row(imported)["book"] == "Kalesh"


def test_an_import_preview_alone_records_nothing(client, stand_in):
    stand_in(ROWS, 100000, 100000 - 14000 - 15950, account=BANK)
    upload(client)
    assert entries(client) == []


def test_discarding_an_undo_puts_the_change_back_in_force_in_one_transaction(imported):
    tx = a_row(imported)
    first = label(imported, tx["id"], book="Moom")
    entry = int(first.headers["X-Fin-Change"])
    undone = imported.post(f"/api/history/{entry}/undo")
    undo_entry = int(undone.headers["X-Fin-Change"])

    conn = db.get_connection()
    try:
        with history.WRITE_LOCK:
            history.discard(conn, undo_entry)
        ids = {r[0] for r in conn.execute("SELECT id FROM change_entries")}
        undone_by = conn.execute("SELECT undone_by FROM change_entries WHERE id = ?", (entry,)).fetchone()[0]
    finally:
        conn.close()

    assert undo_entry not in ids and entry in ids
    assert undone_by is None
    assert {t["id"]: t["book"] for t in imported.get("/api/transactions?per_page=50").get_json()["transactions"]}[
        tx["id"]] == "Moom"
