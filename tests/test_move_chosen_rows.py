"""Rows chosen by hand, moved by a rule only when the operator says so.

ADR-008 keeps the flow of a row whose book or type the operator chose when a
rule change, the drift's Apply, a teach's backfill or a chat plan would move
it. A choice made under the old handling may be stale, so every preview that
keeps such rows lists them apart (flows_chosen: date, amount, wording, now
and what it would be), and an Apply sent with `move_chosen: true` moves them
as it moves any other row: the flow and other side the rule gives, the book
and type fitted to that flow (label_source.fitted), in the same one change
entry, which one Undo takes back exactly. Without it, Apply keeps them as
before. The preview's token stamps the rows so kept, so an answer to a
preview whose rows have changed since is refused.

Rows reach the book through fin's routes, as the operator's do; chat through
mcp_tools.call; the drawer through node running static/app.js's own
functions. Every name, wording and figure is invented.
"""

import json
import re

import pytest

import db
from test_book_type_only_on_spending import chosen_by_hand, label_of, stored
from test_client_seams import needs_node
from test_movements_and_review import (  # noqa: F401 (ledger is a fixture)
    BANK, bring_in, ledger, make_account, row, spending_type,
)
from test_preview_rules_and_merges import PRELUDE, _const, _function, _node
from test_rule_flow_every_path import FINANCE, agreement, call, finance  # noqa: F401 (finance is a fixture)
from test_rule_flow_every_path import LOAN as AUTO_LOAN
from test_rule_flow_every_path import what as with_merchant
from test_rule_flow_limits import EXPECTED, FRIEND, mark
from test_rule_states_flow import LOAN, as_movement, car, instalment, rule_of, what  # noqa: F401 (car)

BY_HAND = "you chose its book and type"


def book_rows() -> list[tuple]:
    """Every row of the book, every column, as SQLite holds it."""
    conn = db.get_connection()
    try:
        return [tuple(r) for r in conn.execute("SELECT * FROM transactions ORDER BY id")]
    finally:
        conn.close()


def chosen_of(listed: list[dict]) -> list[tuple]:
    return [(k["id"], k["now"], k["would"], k["label_cleared"], k["because"]) for k in listed]


@pytest.fixture
def mixed(client, car) -> dict:
    """The car loan's rule about to state that its rows repay the loan, over
    three past instalments fin labelled: 08 moves, 07 the operator chose a
    book and type for, and 06 has S$ 500 expected back from a friend."""
    bring_in(client, BANK, [(7, instalment(8), 1850.00)])
    friend = make_account(client, FRIEND, "person")
    six, eight = row(client, instalment(6))["id"], row(client, instalment(8))["id"]
    mark(client, six, "500.00", friend)
    seven = chosen_by_hand(client, instalment(7), "Home")
    return {**car, "six": six, "seven": seven, "eight": eight}


def asked_preview(client, mixed) -> dict:
    asked = client.put(f"/api/rules/{mixed['rule']}", json=as_movement(mixed))
    assert asked.status_code == 409, asked.get_json()
    return asked.get_json()


# --- the preview -------------------------------------------------------------------------------


def test_a_rule_changes_preview_lists_the_rows_chosen_by_hand_apart_from_those_a_record_keeps(client, mixed):
    asked = asked_preview(client, mixed)
    preview = asked["preview"]

    assert [f["id"] for f in preview["flows"]] == [mixed["eight"]]
    assert chosen_of(preview["flows_chosen"]) == [
        (mixed["seven"], "spending", f"a movement · {LOAN}", "Household · Home", BY_HAND)]
    line = preview["flows_chosen"][0]
    assert (line["date"], line["amount_minor"], line["currency"], line["description"]) == (
        "2026-08-04", 185000, "SGD", instalment(7))
    # flows_kept is still every row Apply keeps without the opt-in, the record's row among them.
    assert {k["id"]: k["why"] for k in preview["flows_kept"]}[mixed["six"]] == EXPECTED
    assert mixed["six"] not in [k["id"] for k in preview["flows_chosen"]]
    assert (preview["count"], preview["count_with_chosen"]) == (1, 2)
    assert preview["flows_chosen"][0]["moves"] is False
    assert "move_chosen" in asked["error"]
    # Its reason names the opt-in, not a hand label.
    why = {k["id"]: k["why"] for k in preview["flows_kept"]}[mixed["seven"]]
    assert "move_chosen" in why and "say what it is by hand" not in why


def test_a_rule_change_that_would_move_only_rows_chosen_by_hand_asks_first(client, car):
    seven = chosen_by_hand(client, instalment(7), "Home")
    six = chosen_by_hand(client, instalment(6), "Home")
    before = book_rows()

    asked = client.put(f"/api/rules/{car['rule']}", json=as_movement(car))

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    assert (preview["count"], preview["count_with_chosen"]) == (0, 2)
    assert [k["id"] for k in preview["flows_chosen"]] == [seven, six]
    said = asked.get_json()["error"]
    assert "0 past rows" not in said
    assert ("No other past rows change; 2 rows the operator chose by hand would move only if you send "
            "move_chosen") in said
    assert book_rows() == before
    assert rule_of(client, car["rule"])["flow"] is None


# --- Apply, without and with the opt-in -----------------------------------------------------


def test_apply_without_the_opt_in_keeps_the_rows_chosen_by_hand(client, mixed):
    preview = asked_preview(client, mixed)["preview"]
    seven = stored(instalment(7))

    resp = client.put(f"/api/rules/{mixed['rule']}",
                      json={**as_movement(mixed), "past": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert stored(instalment(7)) == seven
    assert what(client, instalment(8)) == ("movement", LOAN, False)
    assert what(client, instalment(6)) == ("expense", None, False)
    assert [k["id"] for k in resp.get_json()["flows_chosen"]] == [mixed["seven"]]


def test_apply_with_the_opt_in_moves_them_and_empties_their_book_and_type(client, mixed):
    preview = asked_preview(client, mixed)["preview"]

    resp = client.put(f"/api/rules/{mixed['rule']}", json={
        **as_movement(mixed), "past": "apply", "token": preview["token"], "move_chosen": True})

    assert resp.status_code == 200, resp.get_json()
    assert what(client, instalment(7)) == ("movement", LOAN, False)
    assert label_of(instalment(7)) == (None, None, None, None)
    assert stored(instalment(7))["service_id"] == mixed["finance"]
    assert what(client, instalment(8)) == ("movement", LOAN, False)
    # The record's row is kept whatever the opt-in: a hand label would be refused.
    assert what(client, instalment(6)) == ("expense", None, False)
    answer = resp.get_json()
    assert answer["recategorized"] == 2
    assert [k["id"] for k in answer["flows_kept"]] == [mixed["six"]]
    # Never moved unseen (RUL-019): the answer still names the row chosen by hand, as moved.
    assert [(k["id"], k["moves"]) for k in answer["flows_chosen"]] == [(mixed["seven"], True)]


def test_the_opt_in_is_one_change_and_undo_puts_every_row_back_sources_too(client, mixed):
    preview = asked_preview(client, mixed)["preview"]
    before = book_rows()
    entries = client.get("/api/history").get_json()["entries"]

    resp = client.put(f"/api/rules/{mixed['rule']}", json={
        **as_movement(mixed), "past": "apply", "token": preview["token"], "move_chosen": True})
    assert resp.status_code == 200, resp.get_json()
    assert label_of(instalment(7)) == (None, None, None, None)
    assert len(client.get("/api/history").get_json()["entries"]) == len(entries) + 1

    undone = client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")

    assert undone.status_code == 200, undone.get_json()
    assert book_rows() == before
    assert label_of(instalment(7)) == ("Household", spending_type(client, "Home"), "chosen", "chosen")
    assert rule_of(client, mixed["rule"])["flow"] is None


# --- the token --------------------------------------------------------------------------------


def test_an_answer_to_a_preview_whose_rows_chosen_by_hand_changed_is_refused(client, mixed):
    preview = asked_preview(client, mixed)["preview"]
    chosen_by_hand(client, instalment(8), "Home")
    before = book_rows()

    resp = client.put(f"/api/rules/{mixed['rule']}", json={
        **as_movement(mixed), "past": "apply", "token": preview["token"], "move_chosen": True})

    assert resp.status_code == 409 and resp.get_json()["stale"]
    assert book_rows() == before


def test_the_opt_in_without_the_previews_token_is_asked_first(client, mixed):
    before = book_rows()

    resp = client.put(f"/api/rules/{mixed['rule']}",
                      json={**as_movement(mixed), "past": "apply", "move_chosen": True})

    assert resp.status_code == 409, resp.get_json()
    assert [k["id"] for k in resp.get_json()["preview"]["flows_chosen"]] == [mixed["seven"]]
    assert book_rows() == before


@pytest.mark.parametrize("body", [{"past": "only_new"}, {"past": "apply", "move_chosen": "yes"}])
def test_the_opt_in_goes_only_with_apply_and_is_true_or_false(client, mixed, body):
    preview = asked_preview(client, mixed)["preview"]
    before = book_rows()

    resp = client.put(f"/api/rules/{mixed['rule']}", json={
        **as_movement(mixed), "move_chosen": True, **body, "token": preview["token"]})

    assert resp.status_code == 400, resp.get_json()
    assert book_rows() == before


# --- a teach's backfill (the teach drawer) ------------------------------------------------------


@pytest.fixture
def backfill(client, finance) -> dict:
    """Three agreements no merchant holds yet: 1021 is taught for the
    finance merchant (its rule says its rows repay the loan); the operator
    chose 1022's book; 1023 fin would move."""
    bring_in(client, BANK, [(3, agreement(1021), 1850.00), (4, agreement(1022), 1850.00),
                            (5, agreement(1023), 1850.00)])
    first, second, third = (row(client, agreement(n))["id"] for n in (1021, 1022, 1023))
    resp = client.put(f"/api/transactions/{second}", json={"book": "Moom"})
    assert resp.status_code == 200, resp.get_json()
    assert label_of(agreement(1022))[0::2] == ("Moom", "chosen")
    return {**finance, "first": first, "second": second, "third": third,
            "teach": {"tx_id": first, "service_id": finance["svc"], "pattern": "SAMPLE HP AGREEMENT",
                      "match_type": "contains", "apply_scope": "service_default"}}


def test_a_teachs_preview_lists_a_backfilled_row_chosen_by_hand_and_the_opt_in_moves_it(client, backfill):
    asked = client.post("/api/transactions/resolve", json=backfill["teach"])

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    assert [f["id"] for f in preview["flows"]] == [backfill["third"]]
    assert [(k["id"], k["would"], k["because"]) for k in preview["flows_chosen"]] == [
        (backfill["second"], f"a movement · {AUTO_LOAN}", "you chose its book")]
    before = book_rows()

    resp = client.post("/api/transactions/resolve", json={
        **backfill["teach"], "past_rows": "apply", "token": preview["token"], "move_chosen": True})

    assert resp.status_code == 200, resp.get_json()
    assert with_merchant(client, agreement(1022)) == ("movement", AUTO_LOAN, FINANCE)
    assert label_of(agreement(1022)) == (None, None, None, None)
    assert with_merchant(client, agreement(1023)) == ("movement", AUTO_LOAN, FINANCE)
    undone = client.post(f"/api/history/{resp.headers['X-Fin-Change']}/undo")
    assert undone.status_code == 200, undone.get_json()
    assert book_rows() == before


def test_a_teach_without_the_opt_in_keeps_the_backfilled_row_chosen_by_hand(client, backfill):
    preview = client.post("/api/transactions/resolve", json=backfill["teach"]).get_json()["preview"]

    resp = client.post("/api/transactions/resolve", json={
        **backfill["teach"], "past_rows": "apply", "token": preview["token"]})

    assert resp.status_code == 200, resp.get_json()
    assert with_merchant(client, agreement(1022)) == ("expense", None, FINANCE)
    assert label_of(agreement(1022))[0::2] == ("Moom", "chosen")
    assert [k["id"] for k in resp.get_json()["flows_chosen"]] == [backfill["second"]]


# --- chat ---------------------------------------------------------------------------------------


def test_chat_change_rule_takes_the_same_opt_in(client, mixed):
    asked = call("change_rule", rule_id=mixed["rule"], **as_movement(mixed))
    assert not asked["ok"]
    assert [k["id"] for k in asked["preview"]["flows_chosen"]] == [mixed["seven"]]

    done = call("change_rule", rule_id=mixed["rule"], **as_movement(mixed), past="apply",
                token=asked["preview"]["token"], move_chosen=True)

    assert done["ok"], done
    assert what(client, instalment(7)) == ("movement", LOAN, False)
    assert label_of(instalment(7)) == (None, None, None, None)


def test_a_chat_plan_lists_a_row_chosen_by_hand_and_moves_it_only_with_the_opt_in(client, backfill):
    items = [{"tx_ids": [backfill["first"]], "service_id": backfill["svc"], "pattern": "SAMPLE HP AGREEMENT",
              "past_rows": "apply"}]

    shown = call("plan_preview", items=items)

    assert shown["ok"], shown
    [item] = shown["result"]["items"]
    assert [(k["id"], k["moves"]) for k in item["flows_chosen"]] == [(backfill["second"], False)]
    assert backfill["second"] not in [f["id"] for f in item["flows"]]
    # A token for the items without the opt-in is not one for the items with it.
    opted = [{**items[0], "move_chosen": True}]
    stale = call("plan_apply", items=opted, token=shown["result"]["token"])
    assert not stale["ok"]
    assert with_merchant(client, agreement(1022))[0] == "expense"

    moving = call("plan_preview", items=opted)
    assert moving["ok"], moving
    [item] = moving["result"]["items"]
    assert backfill["second"] in [f["id"] for f in item["flows"]]
    # Still listed apart, saying it moves because the opt-in was sent (RUL-019).
    assert [(k["id"], k["moves"]) for k in item["flows_chosen"]] == [(backfill["second"], True)]
    applied = call("plan_apply", items=opted, token=moving["result"]["token"])

    assert applied["ok"], applied
    assert with_merchant(client, agreement(1022)) == ("movement", AUTO_LOAN, FINANCE)
    assert label_of(agreement(1022)) == (None, None, None, None)


def test_the_chat_tools_say_the_opt_in():
    import mcp_tools
    tools = {t.name: t for t in mcp_tools.TOOLS}
    for name in ("add_rule", "change_rule", "delete_rule"):
        assert "move_chosen" in tools[name].input_schema["properties"], name
        assert "move_chosen" in tools[name].description, name
    item = tools["plan_preview"].input_schema["properties"]["items"]["items"]["properties"]
    assert item["move_chosen"]["type"] == "boolean"
    assert "move_chosen" in tools["plan_preview"].description


# --- the drawer -----------------------------------------------------------------------------------


CHOSEN_LINE = {"id": 7, "date": "2026-08-04", "amount_minor": 185000, "currency": "SGD",
               "description": "HP SAMPLE MOTOR FINANCE 07", "now": "spending", "would": "a movement · Sample car loan",
               "label_cleared": "Household · Home", "because": BY_HAND, "why": "You chose this row's book and type."}
DRAWER = {
    "subject": {"kind": "rule", "id": 3, "name": "HPR900001X", "verb": "edit",
                "merchant": {"id": 2, "name": "Sample Motor Finance"}},
    "change": [{"field": "flow", "label": "What its rows are", "from": "what fin works out", "to": "a movement"}],
    "count": 1, "count_with_chosen": 3, "checked": 0, "chosen": 0, "money": None, "left_out": [], "months": None,
    "examples": [], "others": [], "token": "t0",
    "flows": [{"id": 8, "date": "2026-08-07", "amount_minor": 185000, "currency": "SGD", "description": "HP 08",
               "now": "spending", "after": "a movement · Sample car loan", "label_cleared": "Household · Transport"}],
    "flows_kept": [CHOSEN_LINE, {**CHOSEN_LINE, "id": 9, "description": "HP SAMPLE MOTOR FINANCE 09"},
                   {"id": 6, "date": "2026-08-03", "amount_minor": 185000, "currency": "SGD",
                    "description": "HP SAMPLE MOTOR FINANCE 06", "now": "spending", "would": "a movement · Sample car loan",
                    "why": EXPECTED, "because": "S$ 500 of it is expected back (undo that mark to move it)"}],
    "flows_chosen": [CHOSEN_LINE, {**CHOSEN_LINE, "id": 9, "description": "HP SAMPLE MOTOR FINANCE 09"}],
}


def drawer(sample) -> dict:
    return _node("\n".join([PRELUDE, _function("monthName"), _function("bandedTable"), _const("PREVIEW_FIELD"),
                            _const("PREVIEW_TITLE"), _function("keptWords"), _function("flowsLine"),
                            _function("exampleCell"), _function("previewParts"),
                            f"console.log(JSON.stringify(previewParts({json.dumps(sample)})));"]))


@needs_node
def test_the_drawer_groups_the_rows_chosen_by_hand_with_an_unticked_box():
    got = drawer(DRAWER)
    body = got["body"]

    assert "You chose these by hand" in body
    box = body[body.index('<input type="checkbox" id="pv-move-chosen"'):]
    box = box[:box.index(">") + 1]
    assert "checked" not in box.replace('type="checkbox"', "")
    assert "Also move these 2 rows you chose by hand" in body
    # Each by date, amount and wording, apart from the row a record keeps.
    assert "4 Aug 2026 · S$ 1,850.00 · HP SAMPLE MOTOR FINANCE 07" in body
    kept_part = body[:body.index("You chose these by hand")]
    assert "HP SAMPLE MOTOR FINANCE 06 stays spending, because S$ 500" in kept_part
    assert "HP SAMPLE MOTOR FINANCE 07" not in kept_part
    # The app's plain words, never the stored names: "a movement", not "movement"; no "expense" or "chosen".
    words = re.sub(r"<[^>]+>", " ", body)
    assert not re.search(r"\bexpense\b|\bchosen\b|(?<!a )\bmovement\b", words), words
    assert 'data-with="3"' in got["foot"] and "Apply to 1 past row" in got["foot"]


@needs_node
def test_ticking_the_box_says_what_apply_will_do_and_apply_sends_the_opt_in():
    code = "\n".join([
        PRELUDE,
        "const els = { '#pv-apply': { dataset: { count: '1', with: '3', unit: 'past row' }, textContent: '' },",
        "              '#pv-count': { dataset: { count: '1', with: '3' }, textContent: '' } };",
        "const $ = s => els[s] || null;",
        _function("tickMoveChosen"),
        "const out = [];",
        "tickMoveChosen({ checked: true }); out.push([els['#pv-apply'].textContent, els['#pv-count'].textContent]);",
        "tickMoveChosen({ checked: false }); out.push([els['#pv-apply'].textContent, els['#pv-count'].textContent]);",
        # The request body, through sendPreviewed itself: first the preview, then the answer.
        "const sent = [];",
        f"const P = {json.dumps(DRAWER)};",
        "const send = async (m, u, b) => { sent.push(b); return sent.length % 2 === 1 ? { status: 409, data: { preview: P } } : { ok: true, status: 200, data: {} }; };",
        "const previewParts = () => ({});",
        # The drawer's Apply, pressed with the box ticked or not, as ACT['preview-answer'] keeps it.
        "let tick = true; const askPreview = async p => { p.moveChosen = tick; return 'apply'; };",
        _function("sendPreviewed"),
        "(async () => {",
        "  const a = await sendPreviewed('PUT', '/api/rules/3', { flow: 'movement' });",
        "  tick = false; const b = await sendPreviewed('PUT', '/api/rules/3', { flow: 'movement' });",
        "  console.log(JSON.stringify({ out, sent, counts: [a.answered.count, b.answered.count],",
        "    moved: [a.answered.flows.length, b.answered.flows.length] }));",
        "})();",
    ])
    got = _node(code)

    assert got["out"] == [["Apply to 3 past rows", "This changes 3 past rows"],
                          ["Apply to 1 past row", "This changes 1 past row"]]
    assert got["sent"][1] == {"flow": "movement", "past": "apply", "token": "t0", "move_chosen": True}
    assert got["sent"][3] == {"flow": "movement", "past": "apply", "token": "t0"}
    assert got["counts"] == [3, 1] and got["moved"] == [3, 1]


ONLY_HAND = {**DRAWER, "count": 0, "count_with_chosen": 2, "flows": [], "flows_kept": DRAWER["flows_chosen"]}


@needs_node
def test_a_drawer_whose_only_rows_were_chosen_by_hand_never_says_it_changes_0_rows():
    got = drawer(ONLY_HAND)
    q = re.search(r'<p class="q" id="pv-count"[^>]*>(.*?)</p>', got["body"]).group(1)

    assert q == "No other past rows change. You chose 2 rows below by hand; tick to move them too."
    assert "0 past rows" not in got["body"]
    # No examples to show: no empty examples table under the group.
    assert "Examples, newest first" not in got["body"]
    # Nor does Apply say it applies to 0 rows: unticked, it saves without moving them.
    assert "Save without moving them" in got["foot"] and "0 past rows" not in got["foot"]
    drift = _node("\n".join([PRELUDE, _function("bandedTable"), _function("keptWords"), _function("flowsLine"),
                             _function("exampleCell"), _function("driftParts"),
                             f"console.log(JSON.stringify(driftParts({json.dumps({**ONLY_HAND, 'subject': {'kind': 'drift'}})})));"]))
    assert "Apply without moving them" in drift["foot"] and "0 rows" not in drift["foot"]
    assert 'data-with="2"' in drift["foot"]
    one = drawer({**ONLY_HAND, "count_with_chosen": 1, "flows_chosen": DRAWER["flows_chosen"][:1]})
    assert "You chose 1 row below by hand; tick to move it too." in one["body"]


@needs_node
def test_ticking_the_box_over_only_rows_chosen_by_hand_says_the_rows_and_unticking_says_none_change():
    zero = "No other past rows change. You chose 2 rows below by hand; tick to move them too."
    code = "\n".join([
        PRELUDE,
        "const els = { '#pv-apply': { dataset: { count: '0', with: '2', unit: 'past row', zero: 'Save without moving them' }, textContent: '' },",
        f"              '#pv-count': {{ dataset: {{ count: '0', with: '2', zero: {json.dumps(zero)} }}, textContent: '' }} }};",
        "const $ = s => els[s] || null;",
        _function("tickMoveChosen"),
        "const out = [];",
        "tickMoveChosen({ checked: true }); out.push(els['#pv-count'].textContent, els['#pv-apply'].textContent);",
        "tickMoveChosen({ checked: false }); out.push(els['#pv-count'].textContent, els['#pv-apply'].textContent);",
        "console.log(JSON.stringify(out));",
    ])

    assert _node(code) == ["This changes 2 past rows", "Apply to 2 past rows", zero, "Save without moving them"]


@needs_node
def test_the_group_shows_ten_rows_then_how_many_more_and_the_box_names_them_all():
    many = [{**CHOSEN_LINE, "id": 100 + n, "description": f"HP SAMPLE MOTOR FINANCE {n:02d}"} for n in range(12)]
    got = drawer({**DRAWER, "flows_chosen": many, "flows_kept": many, "count_with_chosen": 13})
    group = got["body"][got["body"].index("You chose these by hand"):]

    assert group.count("HP SAMPLE MOTOR FINANCE") == 10
    assert "HP SAMPLE MOTOR FINANCE 09" in group and "HP SAMPLE MOTOR FINANCE 10" not in group
    assert "<li>and 2 more rows</li>" in group
    assert "Also move these 12 rows you chose by hand" in group
