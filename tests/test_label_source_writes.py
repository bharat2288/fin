"""Every write sets the right source, per field, and the Queue reads it
(personal-ledger 49; spec 24, cases 1-6, 8, 10).

A row's book and its type each say where they came from: from the merchant,
a rule or the wording; checked (accepted as fin offered it); chosen (set by
hand); worked out. Each case is one request through fin's public route (the
Flask test client, or mcp_tools.call for chat), then what the rows say
afterwards, value and source, the change entry, and what Undo restores. The
database is the temporary one; every merchant, name and figure is invented.
"""

import json
import subprocess

import pytest

import db
import mcp_tools
from test_balance_sheet import bring_in
from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    merchant, statement, type_id,
)
from test_client_seams import APP_JS, NODE, needs_node

import money

CHAT_ACTOR = "chat:sample-subject"


def row(conn, statement_id, description, amount=25.0, *, book="Household", type_name="Travel",
        service_id=None, sources=("merchant", "merchant"), cat_source="service_default",
        day="2026-09-10", flow="expense"):
    """A row as fin holds it after the label-source step: its book and type,
    with a source for each."""
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id,"
        " service_id, cat_source, flow_type, book_source, type_source)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (statement_id, day, description, money.exact_minor(amount), book,
         type_id(conn, type_name) if type_name else None, service_id, cat_source, flow,
         sources[0] if book else None, sources[1] if type_name else None),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def check_each_row(conn, service_id):
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (service_id,))
    conn.commit()
    db.invalidate_rules_cache()


def shown(client, tx_id) -> dict:
    payload = client.get(f"/api/transactions?tx_id={tx_id}").get_json()
    return payload["transactions"][0]


def held(client, tx_id) -> tuple:
    """(book, type, book source, type source) as the list serves the row."""
    tx = shown(client, tx_id)
    return (tx["book"], tx["display_type"], tx["book_source"], tx["type_source"])


def mark(client, tx_id) -> str:
    return shown(client, tx_id)["cat_source"]


def waiting(client) -> list[int]:
    """The rows the Queue's Check each row step lists."""
    payload = client.get("/api/transactions?look=mixed&per_page=500").get_json()
    return sorted(t["id"] for t in payload["transactions"])


def entries(client) -> list[dict]:
    return client.get("/api/history").get_json()["entries"]


def undo(client, resp) -> None:
    change = resp.headers["X-Fin-Change"]
    assert client.post(f"/api/history/{change}/undo").status_code == 200


def one_change(client, before: list[dict]) -> dict:
    after = entries(client)
    assert len(after) == len(before) + 1
    return after[0]


@pytest.fixture
def indigo(conn, statement):
    """IndigoAir, Check each row, usual Household · Travel; three rows waiting."""
    svc = merchant(conn, "IndigoAir", "Household", "Travel")
    check_each_row(conn, svc)
    days = {"6 Oct": "2026-10-06", "2 Oct": "2026-10-02", "9 Sep": "2026-09-09"}
    txs = {name: row(conn, statement, "INDIGOAIR 6E", 180.0 + i, service_id=svc, day=day)
           for i, (name, day) in enumerate(days.items())}
    return svc, txs


# --- case 1: Confirm all, a book changed on one line ---------------------------------------


def test_case_1_confirm_all_checks_what_was_left_and_chooses_what_was_changed(client, conn, indigo):
    svc, txs = indigo
    travel = type_id(conn, "Travel")
    assert waiting(client) == sorted(txs.values())
    before = entries(client)

    resp = client.post("/api/transactions/bulk-label", json={"rows": [
        {"tx_id": txs["6 Oct"], "book": "Household", "type_id": travel},
        {"tx_id": txs["2 Oct"], "book": "Moom", "type_id": travel},
        {"tx_id": txs["9 Sep"], "book": "Household", "type_id": travel},
    ]})

    assert resp.status_code == 200, resp.get_json()
    assert held(client, txs["6 Oct"]) == ("Household", "Travel", "checked", "checked")
    assert held(client, txs["9 Sep"]) == ("Household", "Travel", "checked", "checked")
    assert held(client, txs["2 Oct"]) == ("Moom", "Travel", "chosen", "checked")
    assert waiting(client) == []
    assert one_change(client, before)["via"] == "app"

    undo(client, resp)
    assert [held(client, tx) for tx in txs.values()] == [("Household", "Travel", "merchant", "merchant")] * 3
    assert waiting(client) == sorted(txs.values())


# --- case 2: a line's tick with nothing changed --------------------------------------------


def test_case_2_a_tick_with_nothing_changed_checks_both_fields(client, conn, statement):
    amazon = merchant(conn, "Sample Amazon", "Household", "Shopping")
    check_each_row(conn, amazon)
    tx = row(conn, statement, "SAMPLE AMZN MKTPLACE", 219.00, type_name="Shopping", service_id=amazon,
             day="2026-09-28")
    assert waiting(client) == [tx]

    resp = client.post("/api/transactions/bulk-label", json={"rows": [
        {"tx_id": tx, "book": "Household", "type_id": type_id(conn, "Shopping")}]})

    assert resp.status_code == 200, resp.get_json()
    assert held(client, tx) == ("Household", "Shopping", "checked", "checked")
    assert waiting(client) == []


# --- case 3: Set book for ticked rows ------------------------------------------------------


@pytest.fixture
def grab(conn, statement):
    """Grab, Household · Transport, 40 rows from the merchant."""
    svc = merchant(conn, "Grab", "Household", "Transport")
    txs = [row(conn, statement, f"SAMPLE GRAB RIDE {n:04d}", 12.0 + n, type_name="Transport", service_id=svc,
               day=f"2026-0{1 + n % 9}-{10 + n % 18:02d}") for n in range(40)]
    return svc, txs


def test_case_3_set_book_chooses_the_book_and_leaves_the_type_as_it_came(client, conn, grab):
    svc, txs = grab
    ticked = txs[:6]

    resp = client.post("/api/transactions/bulk-label", json={
        "rows": [{"tx_id": tx, "book": "Moom"} for tx in ticked], "chosen": True})

    assert resp.status_code == 200, resp.get_json()
    assert [held(client, tx) for tx in ticked] == [("Moom", "Transport", "chosen", "merchant")] * 6
    assert held(client, txs[6]) == ("Household", "Transport", "merchant", "merchant")


def test_set_book_chooses_a_book_even_where_it_is_the_rows_own(client, conn, grab):
    _, txs = grab

    with_flag = client.post("/api/transactions/bulk-label", json={
        "rows": [{"tx_id": txs[0], "book": "Household"}], "chosen": True})
    without = client.post("/api/transactions/bulk-label", json={
        "rows": [{"tx_id": txs[1], "book": "Household"}]})

    assert with_flag.status_code == without.status_code == 200
    assert held(client, txs[0])[2:] == ("chosen", "merchant")
    assert held(client, txs[1])[2:] == ("checked", "merchant")


@pytest.mark.parametrize("body", [
    {"rows": [{"tx_id": 0, "book": "Moom"}], "chosen": "yes"},
    {"rows": [{"tx_id": 0, "book": "Nobody"}]},
])
def test_a_refused_bulk_label_writes_nothing_and_says_why(client, conn, grab, body):
    _, txs = grab
    body["rows"][0]["tx_id"] = txs[0]
    before = entries(client)

    resp = client.post("/api/transactions/bulk-label", json=body)

    assert resp.status_code == 400
    assert resp.get_json()["error"] in ("chosen must be true or false", "unknown book: 'Nobody'")
    assert resp.headers.get("X-Fin-Change") is None
    assert entries(client) == before
    assert held(client, txs[0]) == ("Household", "Transport", "merchant", "merchant")


@needs_node
def test_the_merchant_pages_set_book_sends_the_chosen_flag():
    import re
    found = re.search(r"^function mpSetBookBody\(.*?^\}$", APP_JS, re.M | re.S)
    assert found, "mpSetBookBody not found in static/app.js"
    code = found.group(0) + "\nconsole.log(JSON.stringify(mpSetBookBody([7, 9], 'Moom')));"
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30,
                         check=True, encoding="utf-8")
    assert json.loads(out.stdout) == {"rows": [{"tx_id": 7, "book": "Moom"}, {"tx_id": 9, "book": "Moom"}],
                                      "chosen": True}


# --- case 4: a row edited in its sheet -----------------------------------------------------


def test_case_4_a_type_edited_on_the_row_is_chosen_and_the_book_keeps_its_source(client, conn, indigo):
    _, txs = indigo
    tx = txs["6 Oct"]

    resp = client.put(f"/api/transactions/{tx}", json={"type_id": type_id(conn, "Transport")})

    assert resp.status_code == 200, resp.get_json()
    assert held(client, tx) == ("Household", "Transport", "merchant", "chosen")
    assert tx not in waiting(client)
    assert mark(client, tx) == "manual"

    undo(client, resp)
    assert held(client, tx) == ("Household", "Travel", "merchant", "merchant")


def test_case_4_the_sheets_change_what_this_was_for_this_row_chooses_only_what_changed(client, conn, indigo):
    svc, txs = indigo
    tx = txs["2 Oct"]

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx, "service_id": svc, "book": "Household", "type_id": type_id(conn, "Transport"),
        "apply_scope": "transaction"})

    assert resp.status_code == 200, resp.get_json()
    assert held(client, tx) == ("Household", "Transport", "merchant", "chosen")


# --- case 5: chat's set_label with only a book ---------------------------------------------


def test_case_5_chats_set_label_chooses_the_book_and_the_entry_is_via_chat(client, conn, indigo):
    _, txs = indigo
    tx = txs["9 Sep"]

    done = mcp_tools.call("set_label", {"tx_id": tx, "book": "Moom"}, actor=CHAT_ACTOR)

    assert done["ok"], done
    assert held(client, tx) == ("Moom", "Travel", "chosen", "merchant")
    latest = entries(client)[0]
    assert (latest["id"], latest["via"], latest["actor"]) == (done["change"]["entry_id"], "chat", CHAT_ACTOR)

    assert mcp_tools.call("undo", {"entry_id": done["change"]["entry_id"]}, actor=CHAT_ACTOR)["ok"]
    assert held(client, tx) == ("Household", "Travel", "merchant", "merchant")


# --- case 6: teaching ----------------------------------------------------------------------


def test_case_6_teaching_the_merchant_gives_both_fields_from_the_merchant(client, conn, statement):
    tx = row(conn, statement, "SAMPLE STALL 0042", book=None, type_name=None, cat_source="auto")
    also = row(conn, statement, "SAMPLE STALL 0077", book=None, type_name=None, cat_source="auto")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx, "service_name": "Sample Stall", "type_id": type_id(conn, "Dining"),
        "pattern": "SAMPLE STALL", "apply_scope": "service_default"})

    assert resp.status_code == 200, resp.get_json()
    assert held(client, tx) == ("Household", "Dining", "merchant", "merchant")
    assert held(client, also) == ("Household", "Dining", "merchant", "merchant")
    assert mark(client, tx) == "service_default"


@pytest.mark.parametrize("book, sources", [
    ("Household", ("merchant", "rule")),
    ("Moom", ("rule", "rule")),
])
def test_case_6_a_rule_override_taught_gives_the_field_it_sets_from_the_rule(client, conn, statement, book, sources):
    market = merchant(conn, "Sample Market", "Household", "Shopping")
    tx = row(conn, statement, "SAMPLE MARKET WHOLESALE 0042", book=None, type_name=None, cat_source="auto")
    also = row(conn, statement, "SAMPLE MARKET WHOLESALE 0077", book=None, type_name=None, cat_source="auto")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx, "service_id": market, "book": book, "type_id": type_id(conn, "Stock purchases"),
        "pattern": "SAMPLE MARKET WHOLESALE", "apply_scope": "rule"})

    assert resp.status_code == 200, resp.get_json()
    assert held(client, tx) == (book, "Stock purchases", *sources)
    assert held(client, also) == (book, "Stock purchases", *sources)
    assert mark(client, tx) == "rule_override"


# --- case 8: "Not now, only new rows" -----------------------------------------------------


def test_case_8_not_now_marks_the_past_checked_and_none_waits(client, conn, grab):
    svc, txs = grab
    before = [held(client, tx)[:2] for tx in txs]

    resp = client.post(f"/api/services/{svc}/check-each-row", json={"on": True, "past": "leave"})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["checked"] == 40
    assert [held(client, tx) for tx in txs] == [(*b, "checked", "checked") for b in before]
    assert waiting(client) == []

    undo(client, resp)
    assert [held(client, tx) for tx in txs] == [(*b, "merchant", "merchant") for b in before]


def test_case_8_not_now_leaves_a_chosen_field_chosen(client, conn, statement, grab):
    svc, txs = grab
    moom = row(conn, statement, "SAMPLE GRAB RIDE MOOM", book="Moom", type_name="Transport", service_id=svc,
               sources=("chosen", "merchant"), cat_source="manual")

    client.post(f"/api/services/{svc}/check-each-row", json={"on": True, "past": "leave"})

    assert held(client, moom) == ("Moom", "Transport", "chosen", "checked")


def test_case_8_teaching_check_each_row_with_not_now_marks_the_merchants_other_rows_checked(client, conn, statement, grab):
    svc, txs = grab
    new = row(conn, statement, "SAMPLE GRAB RIDE NEW", book=None, type_name=None, cat_source="auto")

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": new, "service_id": svc, "check_each_row": True, "past": "leave",
        "apply_scope": "service_default"})

    assert resp.status_code == 200, resp.get_json()
    assert all(held(client, tx)[2:] == ("checked", "checked") for tx in txs)
    assert held(client, new) == ("Household", "Transport", "merchant", "merchant")
    assert waiting(client) == [new]


# --- case 10: "book varies" ----------------------------------------------------------------


def test_case_10_book_varies_counts_only_chosen_books_that_differ(client, conn, grab, statement):
    svc, txs = grab
    other = merchant(conn, "Sample Taxi", "Household", "Transport")
    checked = [row(conn, statement, "SAMPLE TAXI", type_name="Transport", service_id=other,
                   sources=("checked", "checked"), cat_source="manual") for _ in range(2)]
    assert checked

    resp = client.post("/api/transactions/bulk-label", json={
        "rows": [{"tx_id": tx, "book": "Moom"} for tx in txs[:2]], "chosen": True})
    assert resp.status_code == 200

    assert client.get(f"/api/services/{svc}/book-varies").get_json() == {
        "suggest": True, "moved": 2, "to_book": "Moom"}
    assert client.get(f"/api/services/{other}/book-varies").get_json()["moved"] == 0


def test_case_10_checked_rows_in_another_book_are_not_counted(client, conn, statement):
    taxi = merchant(conn, "Sample Taxi", "Household", "Transport")
    txs = [row(conn, statement, "SAMPLE TAXI", book="Moom", type_name="Transport", service_id=taxi,
               sources=("checked", "checked"), cat_source="manual") for _ in range(2)]
    assert txs

    assert client.get(f"/api/services/{taxi}/book-varies").get_json() == {
        "suggest": False, "moved": 0, "to_book": None}


# --- a later change reaches checked fields and leaves chosen ones -------------------------


def test_a_merchants_type_changed_on_its_sheet_reaches_checked_fields_and_leaves_chosen_ones(client, conn, statement, indigo):
    svc, txs = indigo
    checked = row(conn, statement, "INDIGOAIR 6E", service_id=svc, sources=("checked", "checked"),
                  cat_source="manual")
    chosen = row(conn, statement, "INDIGOAIR 6E", service_id=svc, sources=("checked", "chosen"),
                 cat_source="manual")
    before = entries(client)

    resp = client.put(f"/api/services/{svc}", json={"type_id": type_id(conn, "Transport"), "past": "apply"})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 4
    assert [held(client, tx) for tx in txs.values()] == [("Household", "Transport", "merchant", "merchant")] * 3
    assert held(client, checked) == ("Household", "Transport", "checked", "checked")
    assert held(client, chosen) == ("Household", "Travel", "checked", "chosen")
    one_change(client, before)

    undo(client, resp)
    assert held(client, checked) == ("Household", "Travel", "checked", "checked")
    assert merchant_type(client, svc) == "Travel"


def merchant_type(client, svc) -> str:
    return client.get(f"/api/services/{svc}").get_json()["display_type"]


def test_a_merchants_book_change_leaves_a_book_a_rule_overrides(client, conn, statement):
    svc = merchant(conn, "Sample Air", "Household", "Travel")
    conn.execute("INSERT INTO merchant_rules (pattern, service_id, match_type, confidence, book_override)"
                 " VALUES ('SAMPLE AIR CORP', ?, 'contains', 'confirmed', 'Kalesh')", (svc,))
    conn.commit()
    db.invalidate_rules_cache()
    plain = row(conn, statement, "SAMPLE AIR 0042", service_id=svc)
    corp = row(conn, statement, "SAMPLE AIR CORP 0042", book="Kalesh", service_id=svc,
               sources=("checked", "merchant"), cat_source="manual")

    resp = client.put(f"/api/services/{svc}", json={"book": "Moom", "past": "apply"})

    assert resp.status_code == 200, resp.get_json()
    assert held(client, plain) == ("Moom", "Travel", "merchant", "merchant")
    assert held(client, corp) == ("Kalesh", "Travel", "checked", "merchant")


def test_merging_merchants_reaches_checked_fields_and_leaves_chosen_ones(client, conn, statement):
    grab = merchant(conn, "Grab", "Household", "Transport")
    food = merchant(conn, "Grab Food", "Household", "Dining")
    plain = row(conn, statement, "SAMPLE GRABFOOD 1", type_name="Dining", service_id=food)
    checked = row(conn, statement, "SAMPLE GRABFOOD 2", type_name="Dining", service_id=food,
                  sources=("checked", "checked"), cat_source="manual")
    chosen = row(conn, statement, "SAMPLE GRABFOOD 3", book="Moom", type_name="Dining", service_id=food,
                 sources=("chosen", "chosen"), cat_source="manual")

    resp = client.post(f"/api/services/{food}/merge", json={"target_id": grab})

    assert resp.status_code == 200, resp.get_json()
    assert held(client, plain) == ("Household", "Transport", "merchant", "merchant")
    assert held(client, checked) == ("Household", "Transport", "checked", "checked")
    assert held(client, chosen) == ("Moom", "Dining", "chosen", "chosen")
    assert {shown(client, tx)["service_id"] for tx in (plain, checked, chosen)} == {grab}

    undo(client, resp)
    assert held(client, plain) == ("Household", "Dining", "merchant", "merchant")
    assert held(client, checked) == ("Household", "Dining", "checked", "checked")
    assert {shown(client, tx)["service_id"] for tx in (plain, checked, chosen)} == {food}


def test_a_rule_edit_reaches_checked_fields_and_leaves_chosen_ones(client, conn, statement):
    air = merchant(conn, "Sample Air", "Household", "Travel")
    rule_id = conn.execute("INSERT INTO merchant_rules (pattern, service_id, match_type, confidence)"
                           " VALUES ('SAMPLE AIR', ?, 'contains', 'confirmed')", (air,)).lastrowid
    conn.commit()
    db.invalidate_rules_cache()
    plain = row(conn, statement, "SAMPLE AIR 1", service_id=air)
    checked = row(conn, statement, "SAMPLE AIR 2", service_id=air, sources=("checked", "checked"),
                  cat_source="manual")
    chosen = row(conn, statement, "SAMPLE AIR 3", book="Moom", service_id=air,
                 sources=("chosen", "checked"), cat_source="manual")

    resp = client.put(f"/api/rules/{rule_id}", json={"type_override_id": type_id(conn, "Transport")})

    assert resp.status_code == 200, resp.get_json()
    assert held(client, plain) == ("Household", "Transport", "merchant", "rule")
    assert held(client, checked) == ("Household", "Transport", "checked", "checked")
    assert held(client, chosen) == ("Moom", "Transport", "chosen", "checked")
    assert mark(client, plain) == "rule_override"

    undo(client, resp)
    assert held(client, plain) == ("Household", "Travel", "merchant", "merchant")
    assert held(client, chosen) == ("Moom", "Travel", "chosen", "checked")
    assert mark(client, plain) == "service_default"
    assert next(r for r in client.get("/api/rules").get_json() if r["id"] == rule_id)["type_override_id"] is None


def test_re_running_the_rules_reaches_checked_fields_and_leaves_chosen_ones(client, conn, statement):
    air = merchant(conn, "Sample Air", "Household", "Travel")
    conn.execute("INSERT INTO merchant_rules (pattern, service_id, match_type, confidence)"
                 " VALUES ('SAMPLE AIR', ?, 'contains', 'confirmed')", (air,))
    conn.commit()
    db.invalidate_rules_cache()
    checked = row(conn, statement, "SAMPLE AIR 2", type_name="Shopping", service_id=air,
                  sources=("checked", "checked"), cat_source="manual")
    chosen = row(conn, statement, "SAMPLE AIR 3", book="Moom", type_name="Shopping", service_id=air,
                 sources=("chosen", "chosen"), cat_source="manual")

    resp = client.post("/api/rules/recategorize")

    assert resp.status_code == 200, resp.get_json()
    assert held(client, checked) == ("Household", "Travel", "checked", "checked")
    assert held(client, chosen) == ("Moom", "Shopping", "chosen", "chosen")


# --- import and loan interest -------------------------------------------------------------


def test_an_import_gives_each_field_the_source_the_rules_give(client, conn):
    cafe = merchant(conn, "Sample Cafe", "Household", "Dining")
    market = merchant(conn, "Sample Market", "Household", "Shopping")
    conn.execute("INSERT INTO merchant_rules (pattern, service_id, match_type, confidence)"
                 " VALUES ('SAMPLE CAFE', ?, 'contains', 'confirmed')", (cafe,))
    conn.execute("INSERT INTO merchant_rules (pattern, service_id, match_type, confidence, type_override_id)"
                 " VALUES ('SAMPLE MARKET WHOLESALE', ?, 'contains', 'confirmed', ?)",
                 (market, type_id(conn, "Stock purchases")))
    conn.commit()
    db.invalidate_rules_cache()

    bring_in(client, "Sample Bank 0002", [
        ("2026-08-03", "SAMPLE CAFE 0042", 1500),
        ("2026-08-04", "SAMPLE MARKET WHOLESALE 9", 9000),
        ("2026-08-05", "SAMPLE CORNER STALL", 700),
    ])

    rows = {t["description"]: t for t in client.get("/api/transactions?per_page=50").get_json()["transactions"]}
    got = {d: (t["book_source"], t["type_source"], t["cat_source"]) for d, t in rows.items()}
    assert got["SAMPLE CAFE 0042"] == ("merchant", "merchant", "service_default")
    assert got["SAMPLE MARKET WHOLESALE 9"] == ("merchant", "rule", "rule_override")
    assert got["SAMPLE CORNER STALL"][:2] == (None, None)


def test_worked_out_loan_interest_is_derived_in_both_fields(client, conn):
    from test_loan_interest import OWED_AUG, derived, enter, setup
    made = setup(client, conn)

    enter(client, made["loan"], OWED_AUG, "2026-08-31")

    rows = derived(client)
    assert rows
    assert all((t["book_source"], t["type_source"]) == ("derived", "derived") for t in rows)


@needs_node
def test_yes_to_a_row_not_checked_checks_it_as_it_came_in_through_bulk_label():
    """"Yes, Shopping · Household" accepts what fin offered: bulk label, the
    row's own book and type, no chosen flag (so both are checked)."""
    import re
    found = re.search(r"^function mixedYesBody\(.*?^\}$", APP_JS, re.M | re.S)
    assert found, "mixedYesBody not found in static/app.js"
    code = found.group(0) + "\nconsole.log(JSON.stringify(mixedYesBody({id: 7, type_id: 5, book: 'Household'})));"
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30,
                         check=True, encoding="utf-8")
    assert json.loads(out.stdout) == {"rows": [{"tx_id": 7, "type_id": 5, "book": "Household"}]}
    assert "act('POST', '/api/transactions/bulk-label', mixedYesBody(row)" in APP_JS


# --- the expand step: a label written by a writer not yet switched --------------------------


def test_a_hand_set_row_with_no_sources_yet_is_neither_reached_nor_waiting(client, conn, statement, indigo):
    """The review list's label (personal-ledger 50) still writes cat_source
    alone: a row it marked manual reads as chosen until then."""
    svc, _ = indigo
    by_review = row(conn, statement, "INDIGOAIR 6E", book="Moom", service_id=svc,
                    sources=(None, None), cat_source="manual")
    conn.execute("UPDATE transactions SET book_source = NULL, type_source = NULL WHERE id = ?", (by_review,))
    conn.commit()

    assert by_review not in waiting(client)
    resp = client.put(f"/api/services/{svc}", json={"book": "Kalesh", "type_id": type_id(conn, "Transport"),
                                                   "past": "apply"})

    assert resp.status_code == 200
    assert held(client, by_review) == ("Moom", "Travel", None, None)
