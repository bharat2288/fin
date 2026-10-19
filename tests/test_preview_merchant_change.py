"""The preview: a merchant's book or type (fin-surfaces 49; spec 24, the
preview, cases 11-14, 19-21).

A change to a merchant's book or type that would relabel past rows is shown
first: how many rows, how many of them were checked, that chosen rows stay,
for a book the money that moves between books in S$ at fin's saved month-end
rates and the months it touches, the newest three examples, and a token that
stamps what the preview saw. The route writes nothing until it is told past:
apply (the rows change too, keeping their sources) or past: only_new (only
new rows; the kept field of each past row becomes checked). A stale token is
refused. Teaching from the Queue goes through the same preview.

Each case is one request through fin's public route (the Flask test client,
or mcp_tools.call for chat), then what the rows and the merchant say
afterwards, values and sources, the preview's count against the rows the
write changed, the change entry and what Undo restores. The database is the
temporary one; every merchant, name and figure is invented. fin has no
"Household goods" type: case 12 uses Home in its place.
"""

import json
import re
import subprocess
import time

import pytest

import db
import mcp_tools
from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    merchant, rule, statement, type_id,
)
from test_client_seams import APP_JS, NODE, needs_node
from trend_fixtures import account, seed_rate
from trend_fixtures import statement as month_statement

CHAT_ACTOR = "chat:sample-subject"
MONTHS = ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
          "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]


def add_row(conn, statement_id, description, amount_minor, *, service_id, book="Household",
            type_name="Travel", sources=("merchant", "merchant"), day="2026-09-10", flow="expense"):
    mark = "manual" if {"checked", "chosen"} & set(sources) else "service_default"
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id,"
        " service_id, cat_source, flow_type, book_source, type_source)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (statement_id, day, description, amount_minor, book,
         type_id(conn, type_name) if type_name else None, service_id, mark, flow,
         sources[0] if book else None, sources[1] if type_name else None),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def labels(conn, ids) -> dict:
    """Each row's (book, type id, book source, type source)."""
    marks = ",".join("?" * len(ids))
    return {r[0]: tuple(r[1:]) for r in conn.execute(
        f"SELECT id, book, type_id, book_source, type_source FROM transactions WHERE id IN ({marks})",
        list(ids))}


def every_row(conn) -> dict:
    return {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT id, book, type_id, service_id, book_source, type_source, cat_source FROM transactions")}


def merchant_label(conn, svc) -> tuple:
    return tuple(conn.execute("SELECT book, type_id FROM services WHERE id = ?", (svc,)).fetchone())


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def changed(before: dict, after: dict) -> set:
    return {i for i in before if before[i] != after.get(i)}


@pytest.fixture
def indigo(conn, statement):
    """IndigoAir, Household · Travel. 25 past rows: 17 from the merchant (one
    on a rupee card), 6 checked, 2 chosen; 23 of them over the eleven months
    Nov 2025 - Sep 2026, S$ 9,840.00 in all at the saved month-end rates."""
    svc = merchant(conn, "IndigoAir", "Household", "Travel")
    rule(conn, "INDIGOAIR", svc)
    rupee_card = account(conn, "Sample Rupee Card", currency="INR")
    rupee = month_statement(conn, rupee_card, "2026-09")
    seed_rate(conn, "INR/SGD", "2026-09-30", "0.0155")
    seed_rate(conn, "INR/SGD", "2026-08-30", "0.0170")   # an older rate, never the one used
    reachable, checked, n = [], [], 0
    for month in MONTHS:
        for day in ("05", "20"):
            amount = 44500 if (month, day) == ("2026-09", "20") else 44000
            sources = ("checked", "checked") if n % 4 == 1 and len(checked) < 6 else ("merchant", "merchant")
            tx = add_row(conn, statement, f"INDIGOAIR 6E {n:03d}", amount, service_id=svc,
                         sources=sources, day=f"{month}-{day}")
            (checked if sources[0] == "checked" else reachable).append(tx)
            n += 1
    reachable.append(add_row(conn, rupee, "INDIGOAIR 6E DEL", 1_000_000, service_id=svc,
                             day="2026-09-25"))          # Rs 10,000.00 = S$ 155.00
    chosen = [add_row(conn, statement, "INDIGOAIR 6E KEPT", 30000, service_id=svc,
                      sources=("chosen", "merchant"), day=day) for day in ("2026-09-28", "2026-08-28")]
    assert (len(reachable), len(checked), len(chosen)) == (17, 6, 2)
    return {"svc": svc, "reachable": reachable, "checked": checked, "chosen": chosen,
            "rupee": reachable[-1], "statement": statement}


def preview_of(client, svc, body) -> dict:
    resp = client.put(f"/api/services/{svc}", json=body)
    assert resp.status_code == 409, resp.get_json()
    return resp.get_json()


# --- case 11: a merchant's book changes ----------------------------------------------------


def test_case_11_a_book_change_previews_and_writes_nothing(client, conn, indigo):
    svc = indigo["svc"]
    before, history_before = every_row(conn), entries(client)

    body = preview_of(client, svc, {"book": "Moom"})

    p = body["preview"]
    assert "23 past rows" in body["error"] and "nothing was saved" in body["error"].lower()
    assert p["subject"] == {"kind": "merchant", "id": svc, "name": "IndigoAir"}
    assert p["change"] == [{"field": "book", "from": "Household", "to": "Moom"}]
    assert (p["count"], p["checked"], p["chosen"]) == (23, 6, 2)
    assert p["money"] == [{"from": "Household", "to": "Moom", "amount_minor": 984000}]
    assert p["left_out"] == []
    assert p["months"] == {"count": 11, "first": "2025-11", "last": "2026-09"}
    assert [(x["date"], x["amount_minor"], x["currency"], x["now"], x["after"]) for x in p["examples"]] == [
        ("2026-09-25", 1_000_000, "INR", "Household", "Moom"),
        ("2026-09-20", 44500, "SGD", "Household", "Moom"),
        ("2026-09-05", 44000, "SGD", "Household", "Moom"),
    ]
    assert p["others"] == []
    assert isinstance(p["token"], str) and p["token"]
    # Nothing written: no row, no merchant, no change entry.
    assert every_row(conn) == before
    assert merchant_label(conn, svc) == ("Household", type_id(conn, "Travel"))
    assert entries(client) == history_before


def test_case_11_apply_moves_the_23_and_keeps_their_sources(client, conn, indigo):
    svc = indigo["svc"]
    token = preview_of(client, svc, {"book": "Moom"})["preview"]["token"]
    before, history_before = every_row(conn), entries(client)

    resp = client.put(f"/api/services/{svc}", json={"book": "Moom", "past": "apply", "token": token})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 23
    after = every_row(conn)
    assert len(changed(before, after)) == 23            # the preview's count
    travel = type_id(conn, "Travel")
    assert set(labels(conn, indigo["reachable"]).values()) == {("Moom", travel, "merchant", "merchant")}
    assert set(labels(conn, indigo["checked"]).values()) == {("Moom", travel, "checked", "checked")}
    assert set(labels(conn, indigo["chosen"]).values()) == {("Household", travel, "chosen", "merchant")}
    assert merchant_label(conn, svc) == ("Moom", travel)
    new = entries(client)
    assert len(new) == len(history_before) + 1
    assert new[0]["rows_by_table"] == {"services": 1, "transactions": 23}
    assert new[0]["via"] == "app"


def test_case_11_only_new_rows_keeps_the_23_household_with_their_book_checked(client, conn, indigo):
    svc = indigo["svc"]
    token = preview_of(client, svc, {"book": "Moom"})["preview"]["token"]
    before = every_row(conn)

    resp = client.put(f"/api/services/{svc}", json={"book": "Moom", "past": "only_new", "token": token})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["checked"] == 17               # the 6 were checked already
    travel = type_id(conn, "Travel")
    assert merchant_label(conn, svc) == ("Moom", travel)
    assert set(labels(conn, indigo["reachable"]).values()) == {("Household", travel, "checked", "merchant")}
    assert set(labels(conn, indigo["checked"]).values()) == {("Household", travel, "checked", "checked")}
    assert set(labels(conn, indigo["chosen"]).values()) == {("Household", travel, "chosen", "merchant")}
    assert len(changed(before, every_row(conn))) == 17
    # A later change previews them again: they are still reachable, now checked.
    again = preview_of(client, svc, {"book": "Kalesh"})["preview"]
    assert (again["count"], again["checked"], again["chosen"]) == (23, 23, 2)


def test_case_11_cancel_is_the_preview_alone_and_nothing_is_written(client, conn, indigo):
    """Cancel sends nothing: what stands is the preview's own request."""
    before, history_before = every_row(conn), entries(client)
    preview_of(client, indigo["svc"], {"book": "Moom"})
    preview_of(client, indigo["svc"], {"book": "Moom"})
    assert every_row(conn) == before and entries(client) == history_before


def test_a_rupee_row_with_no_saved_rate_is_named_as_left_out(client, conn, indigo):
    conn.execute("DELETE FROM rates WHERE date = '2026-09-30'")
    conn.commit()

    p = preview_of(client, indigo["svc"], {"book": "Moom"})["preview"]

    assert p["money"] == [{"from": "Household", "to": "Moom", "amount_minor": 984000 - 15500}]
    assert [(x["currency"], x["amount_minor"], x["month"]) for x in p["left_out"]] == [("INR", 1_000_000, "2026-09")]


# --- case 12: a merchant's type changes ----------------------------------------------------


@pytest.fixture
def amazon(conn, statement):
    """Amazon, Household · Shopping: 172 rows from the merchant, 40 checked,
    9 whose type is chosen."""
    svc = merchant(conn, "Sample Amazon", "Household", "Shopping")
    shopping = type_id(conn, "Shopping")
    plan = [("merchant", "merchant")] * 172 + [("checked", "checked")] * 40 + [("merchant", "chosen")] * 9
    rows = [(statement, f"2026-{1 + i % 9:02d}-{1 + i % 27:02d}", f"SAMPLE AMZN {i:04d}", 1000 + i, "Household",
             shopping, svc, "manual" if "merchant" != s[1] or s[0] != "merchant" else "service_default",
             "expense", s[0], s[1]) for i, s in enumerate(plan)]
    conn.executemany(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id,"
        " service_id, cat_source, flow_type, book_source, type_source) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return svc


def test_case_12_a_type_change_previews_count_checked_and_examples_without_money(client, conn, amazon):
    home, shopping = type_id(conn, "Home"), type_id(conn, "Shopping")

    p = preview_of(client, amazon, {"type_id": home})["preview"]

    assert p["change"] == [{"field": "type_id", "from": "Shopping", "to": "Home"}]
    assert (p["count"], p["checked"], p["chosen"]) == (212, 40, 9)
    assert p["money"] is None and p["months"] is None
    assert len(p["examples"]) == 3
    assert all((x["now"], x["after"]) == ("Shopping", "Home") for x in p["examples"])
    dates = [x["date"] for x in p["examples"]]
    newest = [r[0] for r in conn.execute(
        "SELECT date FROM transactions WHERE type_source != 'chosen' ORDER BY date DESC, id DESC LIMIT 3")]
    assert dates == newest

    before = every_row(conn)
    resp = client.put(f"/api/services/{amazon}", json={"type_id": home, "past": "apply", "token": p["token"]})
    assert resp.status_code == 200, resp.get_json()
    assert len(changed(before, every_row(conn))) == 212
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE type_id = ? AND type_source = 'chosen'",
                        (shopping,)).fetchone()[0] == 9


# --- case 13: the same change from the Queue -----------------------------------------------


def queue_teach(conn, indigo):
    """A new IndigoAir row with no type, taught from the Queue (Another type…
    on the merchant fin holds), its default changed to Moom."""
    tx = add_row(conn, indigo["statement"], "INDIGOAIR 6E NEW", 41000, service_id=None, book=None,
                 type_name=None, day="2026-10-02")
    return tx, {"tx_id": tx, "service_id": indigo["svc"], "service_name": "IndigoAir", "book": "Moom",
                "type_id": type_id(conn, "Travel"), "apply_scope": "service_default",
                "pattern": "INDIGOAIR", "match_type": "contains"}


def test_case_13_teaching_from_the_queue_previews_the_same_numbers(client, conn, indigo):
    tx, body = queue_teach(conn, indigo)
    sheet = preview_of(client, indigo["svc"], {"book": "Moom"})["preview"]
    before, history_before = every_row(conn), entries(client)

    resp = client.post("/api/transactions/resolve", json=body)

    assert resp.status_code == 409, resp.get_json()
    p = resp.get_json()["preview"]
    for key in ("subject", "change", "count", "checked", "chosen", "money", "months", "examples", "token"):
        assert p[key] == sheet[key], key
    assert every_row(conn) == before and entries(client) == history_before
    assert merchant_label(conn, indigo["svc"])[0] == "Household"


def test_case_13_teaching_with_apply_reaches_the_past_rows_in_one_change(client, conn, indigo):
    tx, body = queue_teach(conn, indigo)
    token = client.post("/api/transactions/resolve", json=body).get_json()["preview"]["token"]
    before, history_before = every_row(conn), entries(client)

    resp = client.post("/api/transactions/resolve", json={**body, "past_rows": "apply", "token": token})

    assert resp.status_code == 200, resp.get_json()
    after = every_row(conn)
    assert changed(before, after) - {tx} == set(indigo["reachable"] + indigo["checked"])
    travel = type_id(conn, "Travel")
    assert labels(conn, [tx])[tx] == ("Moom", travel, "merchant", "merchant")
    assert set(labels(conn, indigo["checked"]).values()) == {("Moom", travel, "checked", "checked")}
    assert set(labels(conn, indigo["chosen"]).values()) == {("Household", travel, "chosen", "merchant")}
    assert merchant_label(conn, indigo["svc"]) == ("Moom", travel)
    assert len(entries(client)) == len(history_before) + 1


def test_case_13_teaching_with_only_new_rows_checks_the_past_rows(client, conn, indigo):
    tx, body = queue_teach(conn, indigo)

    resp = client.post("/api/transactions/resolve", json={**body, "past_rows": "only_new"})

    assert resp.status_code == 200, resp.get_json()
    travel = type_id(conn, "Travel")
    assert labels(conn, [tx])[tx] == ("Moom", travel, "merchant", "merchant")
    assert set(labels(conn, indigo["reachable"]).values()) == {("Household", travel, "checked", "merchant")}
    assert merchant_label(conn, indigo["svc"]) == ("Moom", travel)


def test_teaching_that_keeps_the_merchants_default_asks_nothing(client, conn, indigo):
    tx, body = queue_teach(conn, indigo)

    resp = client.post("/api/transactions/resolve", json={**body, "book": "Household"})

    assert resp.status_code == 200, resp.get_json()
    assert labels(conn, [tx])[tx][0] == "Household"


def test_a_resolve_answer_that_is_not_apply_or_only_new_is_refused(client, conn, indigo):
    _, body = queue_teach(conn, indigo)
    before = every_row(conn)

    resp = client.post("/api/transactions/resolve", json={**body, "past_rows": "yes"})

    assert resp.status_code == 400
    assert "apply" in resp.get_json()["error"] and "only_new" in resp.get_json()["error"]
    assert every_row(conn) == before


# --- case 14: no past rows to change -------------------------------------------------------


def test_case_14_a_rename_or_a_note_saves_without_a_preview(client, conn, indigo):
    resp = client.put(f"/api/services/{indigo['svc']}", json={"name": "Indigo Air", "notes": "low-cost"})

    assert resp.status_code == 200, resp.get_json()
    assert conn.execute("SELECT name, notes FROM services WHERE id = ?", (indigo["svc"],)).fetchone()[:] == (
        "Indigo Air", "low-cost")


def test_case_14_a_default_changed_when_every_row_is_chosen_saves_without_a_preview(client, conn, statement):
    svc = merchant(conn, "Sample Kept Air", "Household", "Travel")
    kept = [add_row(conn, statement, "SAMPLE KEPT AIR", 9000, service_id=svc, sources=("chosen", "chosen"))
            for _ in range(3)]
    before = labels(conn, kept)

    resp = client.put(f"/api/services/{svc}", json={"book": "Moom", "type_id": type_id(conn, "Transport")})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 0
    assert labels(conn, kept) == before
    assert merchant_label(conn, svc) == ("Moom", type_id(conn, "Transport"))


def test_a_merchant_answer_that_is_not_apply_or_only_new_is_refused(client, conn, indigo):
    before = every_row(conn)

    resp = client.put(f"/api/services/{indigo['svc']}", json={"book": "Moom", "past": "all"})

    assert resp.status_code == 400
    assert "apply" in resp.get_json()["error"] and "only_new" in resp.get_json()["error"]
    assert every_row(conn) == before and merchant_label(conn, indigo["svc"])[0] == "Household"


def test_a_row_with_no_type_is_given_the_merchants_and_never_counted(client, conn, statement):
    """A row that waits for a type is not relabelled by a new default but
    labelled: the sheet gives it the type whatever the answer, as before the
    preview, and the preview counts only rows that carry a label."""
    svc = merchant(conn, "Sample Grocer", "Household", "Shopping")
    untyped = [add_row(conn, statement, "SAMPLE GROCER", 900, service_id=svc, type_name=None) for _ in range(2)]
    typed = add_row(conn, statement, "SAMPLE GROCER", 1200, service_id=svc, type_name="Shopping")
    groceries = type_id(conn, "Groceries")

    p = preview_of(client, svc, {"type_id": groceries})["preview"]
    assert p["count"] == 1

    resp = client.put(f"/api/services/{svc}", json={"type_id": groceries, "past": "only_new"})

    assert resp.status_code == 200, resp.get_json()
    assert set(labels(conn, untyped).values()) == {("Household", groceries, "merchant", "merchant")}
    assert labels(conn, [typed])[typed] == ("Household", type_id(conn, "Shopping"), "merchant", "checked")


def test_teaching_a_merchants_first_type_over_rows_with_none_asks_nothing(client, conn, statement):
    svc = merchant(conn, "Sample Ads")
    tx = add_row(conn, statement, "SAMPLE*ADS 1", 5000, service_id=svc, book=None, type_name=None)
    other = add_row(conn, statement, "SAMPLE*ADS 2", 5000, service_id=svc, book=None, type_name=None)

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx, "service_id": svc, "service_name": "Sample Ads", "book": "Moom",
        "type_id": type_id(conn, "Advertising"), "pattern": "SAMPLE*ADS", "apply_scope": "service_default"})

    assert resp.status_code == 200, resp.get_json()
    assert labels(conn, [other])[other][:2] == ("Moom", type_id(conn, "Advertising"))


# --- case 19: Undo of an Apply -------------------------------------------------------------


def test_case_19_undo_of_an_apply_restores_every_row_and_the_merchant_with_their_sources(client, conn, indigo):
    svc = indigo["svc"]
    before, merchant_before = every_row(conn), merchant_label(conn, svc)
    resp = client.put(f"/api/services/{svc}", json={"book": "Moom", "past": "apply"})
    assert resp.status_code == 200

    undone = client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")

    assert undone.status_code == 200, undone.get_json()
    assert every_row(conn) == before
    assert merchant_label(conn, svc) == merchant_before


def test_case_19_undo_is_refused_naming_a_later_change_to_the_same_rows(client, conn, indigo):
    svc = indigo["svc"]
    resp = client.put(f"/api/services/{svc}", json={"book": "Moom", "past": "apply"})
    later = client.put(f"/api/transactions/{indigo['reachable'][0]}", json={"type_id": type_id(conn, "Transport")})
    after = every_row(conn)

    undone = client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")

    assert undone.status_code == 409
    assert f"change {later.headers['X-Fin-Change']}" in undone.get_json()["error"]
    assert every_row(conn) == after


# --- case 20: two previews racing ----------------------------------------------------------


def test_case_20_a_stale_preview_is_refused_and_writes_nothing(client, conn, indigo):
    svc = indigo["svc"]
    tab_a = preview_of(client, svc, {"book": "Moom"})["preview"]["token"]
    tab_b = client.put(f"/api/services/{svc}", json={"type_id": type_id(conn, "Transport"), "past": "apply"})
    assert tab_b.status_code == 200
    before, history_before = every_row(conn), entries(client)

    resp = client.put(f"/api/services/{svc}", json={"book": "Moom", "past": "apply", "token": tab_a})

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "IndigoAir changed since this preview; look again"
    assert resp.get_json()["stale"] is True
    assert every_row(conn) == before and entries(client) == history_before
    assert merchant_label(conn, svc)[0] == "Household"


def test_a_stale_preview_is_refused_even_when_nothing_is_left_to_change(client, conn, indigo):
    svc = indigo["svc"]
    tab_a = preview_of(client, svc, {"book": "Moom"})["preview"]["token"]
    client.put(f"/api/services/{svc}", json={"book": "Moom", "past": "apply"})

    resp = client.put(f"/api/services/{svc}", json={"book": "Moom", "past": "apply", "token": tab_a})

    assert resp.status_code == 409 and resp.get_json()["stale"] is True


def test_a_row_changed_after_the_preview_makes_it_stale(client, conn, indigo):
    svc = indigo["svc"]
    token = preview_of(client, svc, {"book": "Moom"})["preview"]["token"]
    client.put(f"/api/transactions/{indigo['reachable'][3]}", json={"book": "Kalesh"})

    resp = client.put(f"/api/services/{svc}", json={"book": "Moom", "past": "apply", "token": token})

    assert resp.status_code == 409 and resp.get_json()["stale"] is True


# --- chat: one entry via chat; refused writes nothing --------------------------------------


def test_chat_change_merchant_that_would_change_past_rows_writes_nothing_and_says_why(client, conn, indigo):
    before, history_before = every_row(conn), entries(client)

    got = mcp_tools.call("change_merchant", {"service_id": indigo["svc"], "book": "Moom"}, actor=CHAT_ACTOR)

    assert got["ok"] is False and "23 past rows" in got["error"]
    assert every_row(conn) == before and entries(client) == history_before


def test_chat_change_merchant_with_no_past_rows_affected_is_one_entry_via_chat(client, conn, indigo):
    got = mcp_tools.call("change_merchant", {"service_id": indigo["svc"], "notes": "budget"}, actor=CHAT_ACTOR)

    assert got["ok"] is True, got
    entry = entries(client)[0]
    assert (entry["via"], entry["actor"]) == ("chat", CHAT_ACTOR)


# --- volume --------------------------------------------------------------------------------


def test_the_preview_runs_within_a_second_over_fifty_thousand_rows(client, conn, statement):
    svc = merchant(conn, "Sample Volume Air", "Household", "Travel")
    rule(conn, "SAMPLE VOLUME AIR", svc)
    # A rule of its own that overrides the book: its rows keep theirs.
    conn.execute("INSERT INTO merchant_rules (pattern, service_id, match_type, confidence, book_override)"
                 " VALUES ('SAMPLE VOLUME AIR 9', ?, 'contains', 'confirmed', 'Kalesh')", (svc,))
    conn.commit()
    db.invalidate_rules_cache()
    travel = type_id(conn, "Travel")
    conn.executemany(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id,"
        " service_id, cat_source, flow_type, book_source, type_source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(statement, f"20{24 + i % 3}-{1 + i % 12:02d}-{1 + i % 28:02d}", f"SAMPLE VOLUME AIR {i}", 500 + i % 900,
          "Household", travel, svc, "service_default", "expense",
          ("merchant", "checked", "chosen")[i % 3], "merchant") for i in range(50_000)])
    conn.commit()

    # The request's own CPU time: other suites share the desk while this one
    # runs, and the wall clock would time them too.
    took = []
    for _ in range(3):
        start = time.process_time()
        p = preview_of(client, svc, {"book": "Moom"})["preview"]
        took.append(time.process_time() - start)
    overridden = sum(1 for i in range(50_000) if i % 3 != 2 and str(i).startswith("9"))
    assert p["count"] == 33_334 - overridden and p["chosen"] == 16_666
    assert min(took) < 1.0, took


# --- the screen: the merchant sheet's Save opens the one-confirm drawer -------------------


def _function(name):
    """A function lifted out of static/app.js: a one-liner, or up to its
    closing brace at the start of a line."""
    found = (re.search(rf"^(?:async )?function {name}\(.*\}}$", APP_JS, re.M)
             or re.search(rf"^(?:async )?function {name}\(.*?^\}}$", APP_JS, re.M | re.S))
    assert found, f"{name} not found in static/app.js"
    return found.group(0)


def _const(name):
    found = re.search(rf"^const {name} = .*?;$", APP_JS, re.M | re.S)
    assert found, f"{name} not found in static/app.js"
    return found.group(0)


def _node(code):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30,
                         check=True, encoding="utf-8")
    return json.loads(out.stdout)


PRELUDE = r"""
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const icon = n => '';
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const LONG_MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
function plural(n, one, many) { return `${n} ${n === 1 ? one : (many || one + 's')}`; }
function money(minor, cur = 'SGD') { const s = {SGD: 'S$', INR: '₹'}[cur]; return `${s} ${(Math.abs(minor) / 100).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`; }
function day(iso) { const [y, m, d] = iso.split('-').map(Number); return `${d} ${MONTHS[m - 1]} ${y}`; }
"""

SAMPLE = {
    "subject": {"kind": "merchant", "id": 4, "name": "IndigoAir"},
    "change": [{"field": "book", "from": "Household", "to": "Moom"}],
    "count": 23, "checked": 6, "chosen": 2,
    "money": [{"from": "Household", "to": "Moom", "amount_minor": 984000}], "left_out": [],
    "months": {"count": 11, "first": "2025-11", "last": "2026-09"},
    "examples": [
        {"id": 1, "date": "2026-09-25", "amount_minor": 1000000, "currency": "INR", "now": "Household", "after": "Moom", "source": "merchant"},
        {"id": 2, "date": "2026-09-20", "amount_minor": 44500, "currency": "SGD", "now": "Household", "after": "Moom", "source": "checked"},
        {"id": 3, "date": "2026-09-05", "amount_minor": 44000, "currency": "SGD", "now": "Household", "after": "Moom", "source": "merchant"},
    ],
    "others": [], "token": "t0",
}


def preview_html(sample) -> dict:
    return _node("\n".join([PRELUDE, _function("monthName"), _function("bandedTable"), _const("PREVIEW_FIELD"), _function("previewParts"),
                            f"console.log(JSON.stringify(previewParts({json.dumps(sample)})));"]))


@needs_node
def test_the_drawer_reads_as_mocked_for_a_book_change():
    got = preview_html(SAMPLE)
    text = re.sub(r"<[^>]+>", "", got["body"])
    assert got["title"] == "Change IndigoAir’s book to Moom?"
    assert got["eyebrow"] == "IndigoAir · book"
    assert "This changes 23 past rows" in text
    assert "S$ 9,840.00" in text and "Household → Moom" in text
    assert "11" in text and "Nov 2025 – Sep 2026" in text
    assert "6 you checked" in text and "2 you chose stay as they are" in text
    assert text.count("Moom") >= 4                     # the line, the tile, the examples
    assert "25 Sep 2026" in text and "₹ 10,000.00" in text
    assert "See all 23 rows ›" in text
    assert 'class="prev-facts"' in got["body"]
    foot = got["foot"]
    labels_in_order = re.findall(r">([^<>]+)</button>", foot)
    assert labels_in_order == ["Cancel", "Only new rows", "Apply to 23 past rows"]
    assert 'class="btn ghost"' in foot and 'class="btn primary"' in foot
    assert "autofocus" not in foot


@needs_node
def test_the_drawer_for_a_type_change_has_no_money_or_months():
    sample = {**SAMPLE, "change": [{"field": "type_id", "from": "Shopping", "to": "Home"}], "count": 212,
              "checked": 40, "chosen": 9, "money": None, "months": None}
    got = preview_html(sample)
    text = re.sub(r"<[^>]+>", "", got["body"])
    assert got["title"] == "Change IndigoAir’s type to Home?"
    assert "This changes 212 past rows" in text and "40 you checked" in text and "9 you chose" in text
    assert "prev-facts" not in got["body"] and "S$ 9,840.00" not in text
    assert "Apply to 212 past rows" in got["foot"]


@needs_node
def test_a_previewed_write_resends_the_answer_with_the_token():
    code = "\n".join([PRELUDE, r"""
const sent = [];
let answer = 'apply';
async function send(method, url, body) {
    sent.push([method, url, body]);
    if (!body.past && !body.past_rows) return { ok: false, status: 409, data: { error: 'x', preview: { token: 'T1', count: 3 } } };
    return { ok: true, status: 200, data: { success: true }, change: 9 };
}
async function askPreview(p) { return answer; }
""", _function("sendPreviewed"), r"""
(async () => {
    const one = await sendPreviewed('PUT', '/api/services/4', { book: 'Moom' });
    answer = null;
    const cancelled = await sendPreviewed('PUT', '/api/services/4', { book: 'Moom' }, { key: 'past_rows' });
    console.log(JSON.stringify({ sent, one, cancelled }));
})();
"""])
    got = _node(code)
    assert got["sent"][0] == ["PUT", "/api/services/4", {"book": "Moom"}]
    assert got["sent"][1] == ["PUT", "/api/services/4", {"book": "Moom", "past": "apply", "token": "T1"}]
    assert len(got["sent"]) == 3                       # the cancelled one sent its preview only
    assert got["one"]["ok"] is True and got["one"]["change"] == 9
    assert got["one"]["answered"] == {"past": "apply", "count": 3}
    assert got["cancelled"]["ok"] is False and got["cancelled"]["cancelled"] is True


def test_the_merchant_sheet_and_the_queue_go_through_the_preview():
    start = APP_JS.index("ACT['merchant-save'] =")
    save = APP_JS[start:APP_JS.index("\nACT[", start + 1)]
    assert "sendPreviewed('PUT', `/api/services/" in save
    home = APP_JS[APP_JS.index("ACT['home-guess-yes'] ="):APP_JS.index("ACT['looks-right'] =")]
    assert "act('POST', '/api/transactions/resolve'" not in home
    resolve = _function("saveResolve")
    assert "sendPreviewed('POST', '/api/transactions/resolve'" in resolve and "key: 'past_rows'" in resolve
    assert "A change of book or type is written to the rows that take their label from it" not in APP_JS


def test_the_drawer_takes_focus_itself_not_a_button():
    ask = _function("askPreview")
    assert "focusSheet: true" in ask
    sheet = _function("openSheet")
    assert "focusSheet" in sheet


def test_on_a_phone_the_preview_is_a_bottom_sheet_facts_two_across_apply_on_top():
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "static" / "styles.css").read_text(encoding="utf-8")
    assert re.search(r"\.prev-facts\s*\{[^}]*grid-template-columns:\s*1fr 1fr", css)
    phone = css[css.index("/* the one confirm on a phone"):]
    assert "flex-direction: column-reverse" in phone[:400]
