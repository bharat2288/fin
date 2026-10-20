"""Chat says past or only new; Re-run every rule retired (fin-surfaces 51;
spec 24, cases 22-26).

Chat's merchant and rule tools (change_merchant, change_rule, add_rule,
delete_rule, merge_merchants) take `past` (apply | only_new) and the
preview's token. Without `past`, when past rows would change, the tool
writes nothing and answers with the preview's numbers for Claude to ask; with
it, the change is one entry `via` chat, undoable with every source restored.

Re-run every rule is gone (its route, its button, chat's rerun_rules). In its
place Lists › Rules counts the rows whose label differs from what the rules
give now (fields from the merchant, a rule or the wording only; checked and
chosen fields never count), and opens the same preview with Apply and Leave
them.

Each case is one request through fin's public route (the Flask test client,
or mcp_tools.call for chat). The database is the temporary one; every
merchant, wording and figure is invented. fin has no "Household goods" type:
Home stands in for it, as in fin-surfaces 49's tests.
"""

import json
import re
import subprocess

import pytest

import db
import mcp_tools
from test_book_and_type_switch import (  # noqa: F401 (statement is a fixture)
    merchant, rule, statement, type_id,
)
from test_client_seams import APP_JS, NODE, needs_node
from test_preview_merchant_change import amazon  # noqa: F401 (a fixture)
from test_preview_rules_and_merges import (  # noqa: F401 (fixtures)
    add_row, book_state, cafe, changed, entries, every_row, grab_food, held, indigo, rides,
)

CHAT_ACTOR = "chat:sample-subject"
PREVIEWED_TOOLS = ("change_merchant", "change_rule", "add_rule", "delete_rule", "merge_merchants")


def chat(tool, **args):
    return mcp_tools.call(tool, args, actor=CHAT_ACTOR)


def sources(conn, ids) -> dict:
    """Each row's (book, type id, book source, type source)."""
    marks = ",".join("?" * len(ids))
    return {r[0]: tuple(r[1:]) for r in conn.execute(
        f"SELECT id, book, type_id, book_source, type_source FROM transactions WHERE id IN ({marks})", list(ids))}


def ids_where(conn, svc, book_source, type_source) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT id FROM transactions WHERE service_id = ? AND book_source = ? AND type_source = ? ORDER BY id",
        (svc, book_source, type_source))]


# --- case 22: rows that differ from their rule ---------------------------------------------


@pytest.fixture
def drifted(conn, statement):
    """Sample Shop's rule SAMPLE SHOP gives Household · Home now. Three rows
    from the merchant still read Shopping (an older default); one checked
    and one chosen row read Shopping too and are never counted; two rows
    already say what the rule gives."""
    shop = merchant(conn, "Sample Shop", "Household", "Home")
    rule(conn, "SAMPLE SHOP", shop)
    stale = [add_row(conn, statement, f"SAMPLE SHOP {n}", 2000 + n, service_id=shop, type_name="Shopping",
                     day=f"2026-0{7 + n}-03") for n in range(3)]
    checked = add_row(conn, statement, "SAMPLE SHOP CHECKED", 900, service_id=shop, type_name="Shopping",
                      sources=("checked", "checked"))
    chosen = add_row(conn, statement, "SAMPLE SHOP CHOSEN", 800, service_id=shop, type_name="Shopping",
                     sources=("chosen", "chosen"))
    fine = [add_row(conn, statement, f"SAMPLE SHOP OK {n}", 500, service_id=shop, type_name="Home")
            for n in range(2)]
    return {"shop": shop, "stale": stale, "checked": checked, "chosen": chosen, "fine": fine}


def test_case_22_the_drift_count_names_the_three_rows_and_writes_nothing(client, conn, drifted):
    before, history_before = book_state(conn), entries(client)

    resp = client.get("/api/rules/drift")

    assert resp.status_code == 200, resp.get_json()
    p = resp.get_json()
    assert p["subject"]["kind"] == "drift"
    assert (p["count"], p["checked"], p["chosen"]) == (3, 0, 0)
    assert [x["id"] for x in p["examples"]] == list(reversed(drifted["stale"]))    # newest first
    assert all((x["now"], x["after"]) == ("Sample Shop · Shopping", "Sample Shop · Home") for x in p["examples"])
    assert p["token"]
    assert book_state(conn) == before and entries(client) == history_before


def test_case_22_apply_relabels_the_three_rows_in_one_change_and_undo_restores_them(client, conn, drifted):
    token = client.get("/api/rules/drift").get_json()["token"]
    before, history_before = every_row(conn), entries(client)
    home = type_id(conn, "Home")

    resp = client.post("/api/rules/drift", json={"past": "apply", "token": token})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["recategorized"] == 3
    after = every_row(conn)
    assert changed(before, after) == set(drifted["stale"])                   # the count is what Apply changed
    assert set(sources(conn, drifted["stale"]).values()) == {("Household", home, "merchant", "merchant")}
    new = entries(client)
    assert len(new) == len(history_before) + 1
    assert (new[0]["via"], new[0]["rows"]) == ("app", 3)
    assert client.get("/api/rules/drift").get_json()["count"] == 0          # 0: the line is not drawn

    undone = client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")
    assert undone.status_code == 200, undone.get_json()
    assert every_row(conn) == before


def test_case_22_checked_and_chosen_rows_are_never_counted(client, conn, drifted):
    p = client.get("/api/rules/drift").get_json()

    counted = {x["id"] for x in p["examples"]}
    assert drifted["checked"] not in counted and drifted["chosen"] not in counted
    client.post("/api/rules/drift", json={"past": "apply", "token": p["token"]})
    shopping = type_id(conn, "Shopping")
    assert sources(conn, [drifted["checked"], drifted["chosen"]]) == {
        drifted["checked"]: ("Household", shopping, "checked", "checked"),
        drifted["chosen"]: ("Household", shopping, "chosen", "chosen"),
    }


def test_case_22_a_field_chosen_stays_and_its_other_field_from_the_merchant_is_counted(client, conn, statement):
    shop = merchant(conn, "Sample Half Shop", "Household", "Home")
    rule(conn, "SAMPLE HALF SHOP", shop)
    half = add_row(conn, statement, "SAMPLE HALF SHOP 1", service_id=shop, book="Moom", type_name="Shopping",
                   sources=("chosen", "merchant"))

    p = client.get("/api/rules/drift").get_json()
    assert p["count"] == 1
    client.post("/api/rules/drift", json={"past": "apply", "token": p["token"]})

    assert sources(conn, [half])[half] == ("Moom", type_id(conn, "Home"), "chosen", "merchant")


def test_case_22_with_no_row_differing_the_count_is_zero(client, conn, statement):
    shop = merchant(conn, "Sample Even Shop", "Household", "Home")
    rule(conn, "SAMPLE EVEN SHOP", shop)
    add_row(conn, statement, "SAMPLE EVEN SHOP 1", service_id=shop, type_name="Home")

    p = client.get("/api/rules/drift").get_json()

    assert (p["count"], p["examples"]) == (0, [])


def test_case_22_apply_without_an_answer_previews_and_writes_nothing(client, conn, drifted):
    before, history_before = book_state(conn), entries(client)

    resp = client.post("/api/rules/drift", json={})

    assert resp.status_code == 409
    body = resp.get_json()
    assert body["preview"]["count"] == 3 and "3 rows" in body["error"]
    assert book_state(conn) == before and entries(client) == history_before


@pytest.mark.parametrize("answer", ["only_new", "yes"])
def test_case_22_leave_them_is_no_answer_to_send_and_writes_nothing(client, conn, drifted, answer):
    before, history_before = book_state(conn), entries(client)

    resp = client.post("/api/rules/drift", json={"past": answer})

    assert resp.status_code == 400
    assert "apply" in resp.get_json()["error"]
    assert book_state(conn) == before and entries(client) == history_before


def test_case_22_a_stale_drift_preview_is_refused(client, conn, drifted):
    token = client.get("/api/rules/drift").get_json()["token"]
    client.put(f"/api/transactions/{drifted['stale'][0]}", json={"book": "Kalesh"})
    before = book_state(conn)

    resp = client.post("/api/rules/drift", json={"past": "apply", "token": token})

    assert resp.status_code == 409 and resp.get_json()["stale"] is True
    assert "look again" in resp.get_json()["error"]
    assert book_state(conn) == before


def test_case_22_the_drift_takes_a_row_no_merchant_holds_that_a_rule_knows(client, conn, statement):
    """A row imported before its rule existed: no merchant, no type."""
    taxi = merchant(conn, "Sample Drift Taxi", "Household", "Transport")
    loose = add_row(conn, statement, "SAMPLE DRIFT TAXI 1", 1500)
    rule(conn, "SAMPLE DRIFT TAXI", taxi)

    p = client.get("/api/rules/drift").get_json()
    assert p["count"] == 1 and p["examples"][0]["now"] == "no merchant"
    client.post("/api/rules/drift", json={"past": "apply", "token": p["token"]})

    assert held(conn, [loose])[loose] == (taxi, "Household", type_id(conn, "Transport"), "merchant", "merchant")


def test_case_22_the_drift_asks_the_rule_that_wins_in_match_order(client, conn, statement):
    """Priority, then the longest wording, amount limits and an exact
    wording are honoured, as an import labels a row."""
    plain = merchant(conn, "Sample Duo", "Household", "Shopping")
    vip = merchant(conn, "Sample Duo Vip", "Moom", "Office")
    exact = merchant(conn, "Sample Duo Exact", "Kalesh", "Travel")
    for pattern, svc, extra in (("SAMPLE DUO", plain, ""), ("SAMPLE DUO VIP", vip, ", max_amount_minor = 5000"),
                                ("SAMPLE DUO X", exact, ", match_type = 'exact', priority = 5")):
        rid = rule(conn, pattern, svc)
        conn.execute(f"UPDATE merchant_rules SET confidence = 'confirmed'{extra} WHERE id = ?", (rid,))
    conn.commit()
    db.invalidate_rules_cache()
    to_vip = add_row(conn, statement, "SAMPLE DUO VIP 1", 4000, service_id=plain, type_name="Shopping")
    over_limit = add_row(conn, statement, "SAMPLE DUO VIP 2", 9000, service_id=plain, type_name="Shopping")
    to_exact = add_row(conn, statement, "SAMPLE DUO X", 100, service_id=plain, type_name="Shopping")

    p = client.get("/api/rules/drift").get_json()
    assert {x["id"] for x in p["examples"]} == {to_vip, to_exact}
    client.post("/api/rules/drift", json={"past": "apply", "token": p["token"]})

    rows = held(conn, [to_vip, over_limit, to_exact])
    assert rows[to_vip][:3] == (vip, "Moom", type_id(conn, "Office"))
    assert rows[over_limit][:3] == (plain, "Household", type_id(conn, "Shopping"))
    assert rows[to_exact][:3] == (exact, "Kalesh", type_id(conn, "Travel"))


def test_the_drift_count_runs_within_a_second_over_fifty_thousand_rows(client, conn, statement):
    import time

    shop = merchant(conn, "Sample Volume Shop", "Household", "Home")
    rule(conn, "SAMPLE VOLUME SHOP", shop)
    for n in range(40):
        rule(conn, f"SAMPLE OTHER WORDING {n}", shop)
    home, shopping = type_id(conn, "Home"), type_id(conn, "Shopping")
    conn.executemany(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id,"
        " service_id, flow_type, book_source, type_source) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(statement, f"20{24 + i % 3}-{1 + i % 12:02d}-{1 + i % 28:02d}", f"SAMPLE VOLUME SHOP {i}", 500 + i % 900,
          "Household", shopping if i % 50 == 0 else home, shop, "expense",
          "merchant", ("merchant", "checked", "chosen")[i % 3]) for i in range(50_000)])
    conn.commit()

    started = time.process_time()
    p = client.get("/api/rules/drift").get_json()
    took = time.process_time() - started

    assert p["count"] == len([i for i in range(50_000) if i % 50 == 0 and i % 3 == 0])
    assert took < 1.0, f"the drift count took {took:.2f}s of CPU"


# --- case 23: Re-run every rule is gone ----------------------------------------------------


def test_case_23_rerun_rules_is_not_a_tool_and_its_route_is_gone(client):
    assert "rerun_rules" not in mcp_tools.BY_NAME
    assert client.post("/api/rules/recategorize", json={}).status_code in (404, 405)
    assert "/api/rules/recategorize" not in APP_JS and "Re-run every rule" not in APP_JS


def test_case_23_asking_claude_to_re_run_gives_the_drift_count(client, conn, drifted):
    before, history_before = book_state(conn), entries(client)

    got = chat("rule_drift")

    assert got["ok"] is True, got
    assert got["result"]["count"] == 3 and len(got["result"]["examples"]) == 3
    assert "re-run" in mcp_tools.BY_NAME["rule_drift"].description.lower()
    assert mcp_tools.BY_NAME["rule_drift"].write is False
    assert book_state(conn) == before and entries(client) == history_before


# --- case 24: change_merchant with no answer, past rows affected ---------------------------


def test_case_24_change_merchant_without_past_writes_nothing_and_answers_with_the_preview(
        client, conn, amazon):
    before, history_before = book_state(conn), entries(client)

    got = chat("change_merchant", service_id=amazon, type_id=type_id(conn, "Home"))

    assert got["ok"] is False
    p = got["preview"]
    assert (p["count"], p["checked"], p["chosen"]) == (212, 40, 9)
    assert len(p["examples"]) == 3 and p["token"]
    assert "212 past rows" in got["error"]
    assert "past: apply" in got["error"] and "past: only_new" in got["error"]
    assert book_state(conn) == before and entries(client) == history_before


def test_case_24_with_past_only_new_it_saves_as_only_new_rows_in_one_entry_via_chat(client, conn, amazon):
    home, shopping = type_id(conn, "Home"), type_id(conn, "Shopping")
    plain = ids_where(conn, amazon, "merchant", "merchant")
    checked = ids_where(conn, amazon, "checked", "checked")
    chosen = ids_where(conn, amazon, "merchant", "chosen")
    token = chat("change_merchant", service_id=amazon, type_id=home)["preview"]["token"]
    before, history_before = book_state(conn), entries(client)

    got = chat("change_merchant", service_id=amazon, type_id=home, past="only_new", token=token)

    assert got["ok"] is True, got
    assert conn.execute("SELECT type_id FROM services WHERE id = ?", (amazon,)).fetchone()[0] == home
    assert set(sources(conn, plain).values()) == {("Household", shopping, "merchant", "checked")}
    assert set(sources(conn, checked).values()) == {("Household", shopping, "checked", "checked")}
    assert set(sources(conn, chosen).values()) == {("Household", shopping, "merchant", "chosen")}
    new = entries(client)
    assert len(new) == len(history_before) + 1
    assert (new[0]["via"], new[0]["actor"]) == ("chat", CHAT_ACTOR)
    assert new[0]["rows_by_table"] == {"services": 1, "transactions": 172}
    assert got["change"] == {"entry_id": new[0]["id"], "rows": 173}

    # Undo (the chat's own, with the count it asks over twenty rows).
    undone = chat("undo", entry_id=new[0]["id"], expected_count=173)
    assert undone["ok"] is True, undone
    assert book_state(conn) == before


def test_case_24_with_past_apply_the_212_rows_change_keeping_their_sources(client, conn, amazon):
    home = type_id(conn, "Home")
    plain = ids_where(conn, amazon, "merchant", "merchant")
    checked = ids_where(conn, amazon, "checked", "checked")
    chosen = ids_where(conn, amazon, "merchant", "chosen")
    token = chat("change_merchant", service_id=amazon, type_id=home)["preview"]["token"]
    before = every_row(conn)

    got = chat("change_merchant", service_id=amazon, type_id=home, past="apply", token=token)

    assert got["ok"] is True, got
    assert changed(before, every_row(conn)) == set(plain) | set(checked)        # the preview's 212
    assert set(sources(conn, plain).values()) == {("Household", home, "merchant", "merchant")}
    assert set(sources(conn, checked).values()) == {("Household", home, "checked", "checked")}
    assert {v[1] for v in sources(conn, chosen).values()} == {type_id(conn, "Shopping")}
    assert entries(client)[0]["via"] == "chat"


def test_an_answer_with_the_previews_token_stands_on_the_count_the_operator_was_asked(client, conn, amazon):
    """The preview is the ask: its token, sent back with past, carries the
    operator's yes to the count, as expected_count does, and is recorded as
    asked first."""
    home = type_id(conn, "Home")
    token = chat("change_merchant", service_id=amazon, type_id=home)["preview"]["token"]

    got = chat("change_merchant", service_id=amazon, type_id=home, past="only_new", token=token)

    one = client.get(f"/api/history/{got['change']['entry_id']}").get_json()
    assert one["asked_first"] is True


def test_an_answer_with_no_preview_behind_it_still_asks_the_count_over_twenty_rows(client, conn, amazon):
    """past given unasked ("unless you had already said"): over twenty rows
    the chat still stops for the count, and nothing stands."""
    home = type_id(conn, "Home")
    before, history_before = book_state(conn), entries(client)

    got = chat("change_merchant", service_id=amazon, type_id=home, past="only_new")

    assert got.get("stopped") is True and got["would_change_rows"] == 173
    assert book_state(conn) == before and entries(client) == history_before
    done = chat("change_merchant", service_id=amazon, type_id=home, past="only_new", expected_count=173)
    assert done["ok"] is True, done


def test_a_stale_token_through_chat_is_refused_and_says_why(client, conn, amazon):
    home = type_id(conn, "Home")
    token = chat("change_merchant", service_id=amazon, type_id=home)["preview"]["token"]
    client.put(f"/api/services/{amazon}", json={"book": "Moom", "past": "only_new"})
    before, history_before = book_state(conn), entries(client)

    got = chat("change_merchant", service_id=amazon, type_id=home, past="apply", token=token)

    assert got["ok"] is False and got.get("stale") is True
    assert "changed since this preview; look again" in got["error"]
    assert book_state(conn) == before and entries(client) == history_before


def test_an_answer_that_is_not_apply_or_only_new_is_refused_by_chat_before_anything_runs(client, conn, amazon):
    before, history_before = book_state(conn), entries(client)

    got = chat("change_merchant", service_id=amazon, type_id=type_id(conn, "Home"), past="yes")

    assert got == {"ok": False, "error": "past must be one of apply, only_new"}
    assert book_state(conn) == before and entries(client) == history_before


# --- case 25: change_merchant with no past rows affected -----------------------------------


def test_case_25_change_merchant_with_no_past_row_affected_saves_without_past(client, conn, statement):
    shop = merchant(conn, "Sample Kept Store", "Household", "Shopping")
    kept = add_row(conn, statement, "SAMPLE KEPT STORE 1", service_id=shop, type_name="Shopping",
                   sources=("chosen", "chosen"))

    got = chat("change_merchant", service_id=shop, type_id=type_id(conn, "Home"))

    assert got["ok"] is True, got
    assert conn.execute("SELECT type_id FROM services WHERE id = ?", (shop,)).fetchone()[0] == type_id(conn, "Home")
    assert sources(conn, [kept])[kept][1] == type_id(conn, "Shopping")
    entry = entries(client)[0]
    assert (entry["via"], entry["actor"], entry["rows"]) == ("chat", CHAT_ACTOR, 1)


# --- case 26: change_rule, add_rule, delete_rule, merge_merchants ---------------------------


# Each tool, the fixture it runs on, its call, the rows its preview counts,
# and the rows Apply changes (a merge also moves its chosen rows).
RULE_CASES = {
    "change_rule": ("indigo", lambda f: {"rule_id": f["rule"], "pattern": "INDIGO"},
                    lambda f: f["theirs"] + [f["loose"]], lambda f: f["theirs"] + [f["loose"]]),
    "add_rule": ("rides", lambda f: {"pattern": "TAXI", "service_id": f["taxi"], "priority": 1},
                 lambda f: f["loose"] + f["grabs"], lambda f: f["loose"] + f["grabs"]),
    "delete_rule": ("cafe", lambda f: {"rule_id": f["rule"]},
                    lambda f: f["to_orchard"] + f["to_none"], lambda f: f["to_orchard"] + f["to_none"]),
    "merge_merchants": ("grab_food", lambda f: {"source_id": f["food"], "target_id": f["grab"]},
                        lambda f: f["plain"], lambda f: f["plain"] + f["chosen"]),
}


@pytest.fixture
def rule_case(request):
    """(args, rows the preview counts, rows Apply changes) for the tool."""
    name, args, counted, moved = RULE_CASES[request.param]
    fx = request.getfixturevalue(name)
    return request.param, args(fx), counted(fx), moved(fx)


@pytest.mark.parametrize("rule_case", list(RULE_CASES), indirect=True)
def test_case_26_without_past_the_tool_writes_nothing_and_answers_with_the_preview(client, conn, rule_case):
    tool, args, counted, _moved = rule_case
    before, history_before = book_state(conn), entries(client)

    got = chat(tool, **args)

    assert got["ok"] is False
    assert got["preview"]["count"] == len(counted)
    assert got["preview"]["examples"] and got["preview"]["token"]
    assert f"{len(counted)} past rows" in got["error"] and "past: only_new" in got["error"]
    assert book_state(conn) == before and entries(client) == history_before


@pytest.mark.parametrize("rule_case", list(RULE_CASES), indirect=True)
def test_case_26_with_past_apply_it_is_one_entry_via_chat_and_undo_restores_it(client, conn, rule_case):
    tool, args, _counted, moved = rule_case
    token = chat(tool, **args)["preview"]["token"]
    before, history_before = book_state(conn), entries(client)

    got = chat(tool, **args, past="apply", token=token)

    assert got["ok"] is True, got
    assert changed(before[0], every_row(conn)) == set(moved)
    new = entries(client)
    assert len(new) == len(history_before) + 1
    assert (new[0]["via"], new[0]["actor"]) == ("chat", CHAT_ACTOR)

    undone = chat("undo", entry_id=new[0]["id"], expected_count=new[0]["rows"])
    assert undone["ok"] is True, undone
    assert book_state(conn) == before


@pytest.mark.parametrize("rule_case", list(RULE_CASES), indirect=True)
def test_case_26_with_past_only_new_the_rows_keep_their_values(client, conn, rule_case):
    tool, args, counted, _moved = rule_case
    token = chat(tool, **args)["preview"]["token"]
    before = every_row(conn)

    got = chat(tool, **args, past="only_new", token=token)

    assert got["ok"] is True, got
    after = every_row(conn)
    # Values stay (a merge still moves its rows to the new merchant).
    assert all(before[i][:2] == after[i][:2] for i in counted)
    assert entries(client)[0]["via"] == "chat"


def test_each_previewed_tool_takes_past_and_the_token_and_says_to_ask():
    for name in PREVIEWED_TOOLS:
        tool = mcp_tools.BY_NAME[name]
        props = tool.input_schema["properties"]
        assert props["past"]["enum"] == ["apply", "only_new"], name
        assert props["token"]["type"] == "string", name
        assert "past" not in tool.input_schema["required"], name
        assert "ask the operator" in tool.description.lower(), name


# --- the screen: Lists › Rules draws the count, the drawer asks Apply or Leave them ---------


def _function(name):
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

DRIFT = {
    "subject": {"kind": "drift", "id": None, "name": "the rules"},
    "change": [], "count": 3, "checked": 0, "chosen": 0, "money": None, "left_out": [], "months": None,
    "examples": [
        {"id": 1, "date": "2026-10-03", "amount_minor": 4590, "currency": "SGD", "now": "Amazon · Shopping",
         "after": "Amazon · Home", "source": "merchant"},
        {"id": 2, "date": "2026-09-21", "amount_minor": 1290, "currency": "SGD", "now": "no merchant",
         "after": "Grab · Transport", "source": None},
    ],
    "others": [], "token": "t0",
}


def text_of(html) -> str:
    return re.sub(r"<[^>]+>", "", html)


@needs_node
def test_the_drift_drawer_reads_as_mocked_with_leave_them_and_apply():
    got = _node("\n".join([PRELUDE, _function("monthName"), _function("bandedTable"), _const("PREVIEW_FIELD"),
                           _const("PREVIEW_TITLE"), _function("driftParts"), _function("previewParts"),
                           f"console.log(JSON.stringify(previewParts({json.dumps(DRIFT)})));"]))
    text = text_of(got["body"])
    assert got["title"] == "3 rows differ from their rule" and got["eyebrow"] == "Rules"
    assert "Rows you checked or chose are not counted." in text
    assert "Amazon · Shopping → Amazon · Home" in text and "no merchant → Grab · Transport" in text
    assert "This changes" not in text and "See all" not in text
    assert re.findall(r">([^<>]+)</button>", got["foot"]) == ["Leave them", "Apply to 3 rows"]
    assert 'data-act="close-sheet"' in got["foot"] and 'data-past="apply"' in got["foot"]
    assert "only_new" not in got["foot"]


@needs_node
def test_the_rules_card_draws_the_drift_line_only_above_zero():
    def line(count):
        return _node("\n".join([PRELUDE, _function("driftLine"),
                                f"console.log(JSON.stringify(driftLine({{count: {count}}})));"]))
    assert line(0) == ""
    three = line(3)
    assert text_of(three) == "3 rows differ from what their rule says now ›"
    assert 'class="btn sm ghost"' in three and 'data-act="rule-drift"' in three
    assert text_of(line(1)) == "1 row differs from what its rule says now ›"


def test_the_drift_apply_goes_back_with_the_previews_token():
    start = APP_JS.index("ACT['rule-drift'] =")
    body = APP_JS[start:APP_JS.index("\nACT", start + 1)]
    assert "'/api/rules/drift'" in body and "askPreview(" in body
    assert "past: 'apply'" in body and "token: p.token" in body
