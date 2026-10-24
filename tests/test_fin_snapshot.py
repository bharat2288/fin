"""The baseline snapshot and its diff (fin_snapshot.py): what the migration's
five invariants are checked on. A book diffed against itself passes; one label
changed through the app's own route is named exactly, that row, those month
cells and the rule's rows; and taking a snapshot never writes to the book.
Every name and figure here is invented."""

import hashlib
import json

import pytest

import db
import fin_snapshot
from test_book_and_type_switch import merchant, row, rule, statement, type_id  # noqa: F401


@pytest.fixture
def book(temp_db, conn, statement):  # noqa: F811
    """Two months of groceries the book's one rule labels, one dinner, one
    row untyped."""
    conn.execute("DELETE FROM merchant_rules")
    grocer = merchant(conn, "Sample Grocer", "Household", "Groceries")
    ids = {"rule": rule(conn, "SMPL GROCER", grocer)}
    ids["april"] = row(conn, statement, "SMPL GROCER 0041", 82.40, book="Household", type_name="Groceries",
                       service_id=grocer, mark="service_default", day="2026-04-10")
    ids["may"] = row(conn, statement, "SMPL GROCER 0042", 64.15, book="Household", type_name="Groceries",
                     service_id=grocer, mark="service_default", day="2026-05-12")
    ids["dinner"] = row(conn, statement, "SMPL BISTRO", 120.00, book="Household", type_name="Dining",
                        mark="manual", day="2026-05-20")
    ids["untyped"] = row(conn, statement, "SMPL UNKNOWN SHOP", 19.99, day="2026-05-21")
    ids["path"] = temp_db
    return ids


def snap(path):
    """A snapshot as the file `snap` writes holds it."""
    return json.loads(json.dumps(fin_snapshot.snap(path), sort_keys=True))


def found(result, invariant):
    """The paths one invariant's check found moved."""
    return [path for path, _was, _now in result[invariant]]


def test_a_book_diffed_against_itself_passes_every_invariant(book, tmp_path, capsys):
    out = tmp_path / "s0.json"
    assert fin_snapshot.main(["snap", str(book["path"]), str(out)]) == 0
    again = tmp_path / "s1.json"
    assert fin_snapshot.main(["snap", str(book["path"]), str(again)]) == 0
    capsys.readouterr()

    assert fin_snapshot.main(["diff", str(out), str(again)]) == 0
    said = capsys.readouterr().out
    for name in fin_snapshot.INVARIANTS:
        assert f"{name}: PASS" in said
    assert "ALL INVARIANTS PASS" in said


def test_one_label_changed_through_the_app_is_named_exactly_by_the_diff(book, client, conn):
    before = snap(book["path"])
    resp = client.put(f"/api/transactions/{book['may']}",
                      json={"book": "Household", "type_id": type_id(conn, "Dining")})
    assert resp.status_code == 200
    after = snap(book["path"])

    ok, result = fin_snapshot.diff(before, after)

    assert not ok
    may = str(book["may"])
    # 1: that row, and only it
    assert sorted(found(result, "1 rows")) == [f"rows.{may}.book_source", f"rows.{may}.type",
                                               f"rows.{may}.type_id", f"rows.{may}.type_source"]
    # 2: May's Groceries and Dining cells, in every view of them, and May's cards only
    spending = found(result, "2 spending by book and type")
    assert spending and all(".2026-05." in p or p.startswith("spending_cards.2026-05.") for p in spending)
    assert {p.rsplit(".", 1)[1] for p in spending if p.startswith("spending_by_month.")} == {"Groceries", "Dining"}
    assert not any(".2026-04" in p for p in spending)
    # 3 and 4 do not move
    assert found(result, "3 month-end balance sheet") == []
    assert found(result, "4 expected back and waiting") == []
    # 5: the rule no longer labels the row (chosen is out of every rule's reach)
    rule_id = str(book["rule"])
    assert sorted(found(result, "5 Queue counts and rule rows")) == [f"rules.{rule_id}.rows",
                                                                     f"rules.{rule_id}.rows_labelled"]
    moved = dict((p, (was, now)) for p, was, now in result["5 Queue counts and rule rows"])
    assert moved[f"rules.{rule_id}.rows"] == ({"lost": [book["may"]]}, {"gained": []})
    assert moved[f"rules.{rule_id}.rows_labelled"] == (2, 1)


def test_snap_leaves_the_book_byte_identical_and_never_reseeds_it(book, conn):
    # init_db seeds rules on a start: a book with none must stay with none.
    conn.execute("DELETE FROM merchant_rules")
    conn.commit()
    conn.close()
    path = book["path"]
    beside = sorted(p.name for p in path.parent.iterdir())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    taken = snap(path)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert sorted(p.name for p in path.parent.iterdir()) == beside
    assert taken["rules"] == {}
    assert taken["meta"]["book_sha256"] == digest
    assert db.DB_PATH == path


def test_the_snapshot_says_its_code_and_jev_key_and_a_different_key_fails_the_queue(book, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    without = snap(book["path"])
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    with_key = snap(book["path"])

    assert without["meta"]["jev_key_set"] is False and with_key["meta"]["jev_key_set"] is True
    assert without["meta"]["code"]
    ok, result = fin_snapshot.diff(without, with_key)
    assert not ok
    assert "meta.jev_key_set" in found(result, "5 Queue counts and rule rows")


def test_snap_refuses_the_network_while_it_reads(book, monkeypatch):
    import socket

    connect = socket.create_connection
    monkeypatch.setattr(fin_snapshot, "_take", lambda *a: socket.create_connection(("example.invalid", 443)))
    with pytest.raises(OSError, match="no network"):
        fin_snapshot.snap(book["path"])
    assert socket.create_connection is connect


def test_check_reference_says_same_or_different_for_each_value(book, tmp_path, capsys):
    taken = snap(book["path"])
    month = {"Groceries": 64.15, "Dining": 120.0, "No type": 19.99}
    baseline = {
        "spending_by_month": {"Household": {"2026-04": {"Groceries": 82.4}, "2026-05": dict(month)}},
        "household_total_2026-04_to_2026-05": 286.54,
        "rules": {"count": 1, "rows_labelled_total": 2,
                  "list": [{"id": book["rule"], "pattern": "SMPL GROCER", "rows_labelled": 2}]},
    }
    *lines, counted = fin_snapshot.check_reference(taken, baseline)
    assert lines and all(line.startswith("same") for line in lines), lines
    assert counted == f"{len(lines)} same, 0 different"

    baseline["spending_by_month"]["Household"]["2026-05"]["Dining"] = 121.0
    snap_file, base_file = tmp_path / "s.json", tmp_path / "b.json"
    snap_file.write_text(json.dumps(taken))
    base_file.write_text(json.dumps(baseline))
    assert fin_snapshot.main(["check-reference", str(snap_file), str(base_file)]) == 1
    said = capsys.readouterr().out
    assert "different spending_by_month.Household.2026-05.Dining: baseline 121.00 | snapshot 120.00" in said
    assert "same      spending_by_month.Household.2026-04.Groceries" in said
