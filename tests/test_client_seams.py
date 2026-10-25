"""Ticket 23: the client seams the trend pages plug into, with no visible change.

The seams live in static/app.js and nowhere else, so the functions are lifted out of the
real file and run under node (as test_suggest_pattern does); the rest is read off the files.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

NEW_JS = ["trend.js", "trend-bars.js", "trend-line.js", "merchant-trend.js", "type-page.js", "account-trend.js", "types-over-time.js"]
NEW_CSS = ["trend.css", "trend-bars.css", "trend-line.css"]


def _function(name):
    m = re.search(rf"^function {name}\(.*\}}$", APP_JS, re.M) or re.search(rf"^function {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _const(name):
    m = re.search(rf"^(?:const|let) {name} = .*?;$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _node(code):
    # The script goes in on stdin: on Windows a long -e argument passes the command-line limit.
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, check=True, encoding="utf-8")
    return json.loads(out.stdout)


PRELUDE = "const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;'); const icon = n => `<i ${n}></i>`; globalThis.window = globalThis;"


@needs_node
def test_registerRoute_adds_a_route_the_router_can_find():
    code = "\n".join([PRELUDE, r"const ROUTES = [[/^\/?$/, 'home', () => 'h']];", _function("registerRoute"),
        r"registerRoute(/^\/books\/type\/(\d+)$/, m => 'type ' + m[1]);",
        "const path = '/books/type/7'; const r = ROUTES.find(([re]) => re.test(path));",
        "console.log(JSON.stringify([ROUTES.length, r[1], r[2](path.match(r[0]))]));"])
    assert _node(code) == [2, "books", "type 7"]


@needs_node
def test_booksNav_lists_only_what_has_been_pushed():
    def nav(push):
        return _node("\n".join([PRELUDE, _const("booksNavExtra"), _function("booksNav"), push,
            "console.log(JSON.stringify(booksNav('sheet')));"]))
    base = nav("")
    assert "#/books/types" not in base and "#/books/type" not in base
    assert base.count("<a ") == 5
    more = nav("booksNavExtra.push(['types', '#/books/types', 'Types', 'x']);")
    assert more.count("<a ") == 6 and 'href="#/books/types"' in more


@needs_node
def test_pageLink_is_plain_text_for_type_until_it_is_turned_on():
    def link(kind, setup=""):
        return _node("\n".join([PRELUDE, _const("pageLinkKinds"), _function("pageLink"), setup,
            f"console.log(JSON.stringify(pageLink('{kind}', 7, 'A <b>')));"]))
    assert link("merchant") == '<a class="link" href="#/books/merchant/7">A &lt;b></a>'
    assert link("account") == '<a class="link" href="#/books/account/7">A &lt;b></a>'
    assert link("type") == "A &lt;b>"
    assert link("type", "pageLinkKinds.type = id => `#/books/type/${id}`;") == '<a class="link" href="#/books/type/7">A &lt;b></a>'
    assert link("nonsense") == "A &lt;b>"


def test_mount_slots_and_month_state_are_in_the_views():
    assert "window.mountMerchantTrend?.()" in APP_JS
    assert "window.mountAccountTrend?.()" in APP_JS
    assert re.search(r"const merchantPage = \{[^}]*month: null", APP_JS)
    assert re.search(r"const accountPage = \{[^}]*month: null", APP_JS)
    assert "&month=${" in APP_JS or "month=${" in APP_JS


def test_the_month_narrowing_is_off_by_default_in_the_transaction_reads():
    # no month set -> no month param on the URL
    assert re.search(r"merchantPage\.month|mp\.month", APP_JS)
    assert re.search(r"accountPage\.month", APP_JS)


def test_every_new_tag_follows_app_js():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    app = html.index('src="/static/app.js"')
    for f in NEW_JS:
        i = html.find(f'src="/static/{f}"')
        assert i > app, f"{f} missing or before app.js"
    # trend.js first: the others register into its namespace
    assert html.index('src="/static/trend.js"') < html.index('src="/static/trend-bars.js"') < html.index('src="/static/trend-line.js"')
    for f in NEW_CSS:
        assert f'href="/static/{f}"' in html


@pytest.mark.parametrize("name", NEW_JS + NEW_CSS)
def test_each_new_file_exists(name):
    assert (STATIC / name).is_file()


@needs_node
def test_trend_js_holds_the_namespace_and_registerMode_only():
    code = "globalThis.window = globalThis;" + (STATIC / "trend.js").read_text(encoding="utf-8") + """
    const seen = [];
    window.Trend.registerMode('bars', () => seen.push('b'));
    window.Trend.registerMode('line', () => seen.push('l'));
    console.log(JSON.stringify([Object.keys(window.Trend).sort(), Object.keys(window.Trend.modes || {}).sort()]));"""
    keys, modes = _node(code)
    assert "registerMode" in keys and modes == ["bars", "line"]


def test_reserved_blocks_in_the_stylesheets():
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")
    for n in ("spending-net", "home-net", "links"):
        assert f"/* ==== reserved: {n} ====" in styles
    trend = (STATIC / "trend.css").read_text(encoding="utf-8")
    for n in ("merchant", "account", "type", "types"):
        assert f"/* ==== reserved: {n} ====" in trend


# --- PR #31: a teach's preview and answer say the flows it moves and keeps --------------------
#
# A Queue teach (resolve) can ask only about its backfilled or taken rows'
# flows: the server's preview is then a merchant's with no book or type
# change. The drawer (previewParts) and the resolve's toast (saveResolve) are
# run as app.js has them; the drawer is also fed the route's own preview.

PREVIEW_PRELUDE = r"""
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const icon = n => '';
const SIGNS = { SGD: 'S$', INR: '₹' };
const digitsOf = cur => 2;
const formatters = {};
"""
PREVIEW_FUNCS = ["groupFor", "money", "day", "monthName", "plural", "bandedTable", "keptWords", "flowsLine",
                 "driftParts", "slotParts", "previewParts"]
PREVIEW_CONSTS = ["MONTHS", "LONG_MONTHS", "PREVIEW_FIELD", "PREVIEW_TITLE"]


def _preview_js():
    return "\n".join([PREVIEW_PRELUDE, *map(_const, PREVIEW_CONSTS), *map(_function, PREVIEW_FUNCS)])


def _text(html):
    return re.sub(r"<[^>]+>", "", html)


FLOW_LINE = {"id": 12, "date": "2026-08-04", "description": "SAMPLE HP AGREEMENT 1022", "amount_minor": 185000,
             "currency": "SGD", "now": "spending", "after": "a movement · Sample auto loan"}
KEPT_LINE = {"id": 13, "date": "2026-08-05", "description": "SAMPLE HP AGREEMENT 1023", "amount_minor": 185000,
             "currency": "SGD", "now": "spending", "would": "a movement · Sample auto loan",
             "why": "S$ 500 of this row is expected back; undo the mark first.",
             "because": "S$ 500 of it is expected back (undo that mark to move it)"}
# The kept row, as the drawer says it: named by date, amount and wording, and why in one sentence.
KEPT_SAID = ("5 Aug 2026 · S$ 1,850.00 · SAMPLE HP AGREEMENT 1023 stays spending, "
             "because S$ 500 of it is expected back (undo that mark to move it).")
FLOWS_ONLY = {
    "subject": {"kind": "merchant", "id": 4, "name": "Sample Auto Finance"},
    "change": [], "count": 1, "checked": 0, "chosen": 0, "money": None, "left_out": [], "months": None,
    "examples": [{"id": 12, "date": "2026-08-04", "amount_minor": 185000, "currency": "SGD",
                  "now": "no merchant", "after": "Sample Auto Finance · Transport", "source": None}],
    "others": [], "flows": [FLOW_LINE], "flows_kept": [KEPT_LINE], "token": "t0",
}


def _async_function(name):
    m = re.search(rf"^async function {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def drawn(preview):
    return _node(_preview_js() + f"\nconsole.log(JSON.stringify(previewParts({json.dumps(preview)})));")


@needs_node
def test_a_teach_preview_whose_only_question_is_flows_has_a_plain_title_and_lists_the_flows():
    got = drawn(FLOWS_ONLY)
    text = _text(got["body"])
    assert got["title"] == "Apply Sample Auto Finance to these rows?"
    assert got["eyebrow"] == "Sample Auto Finance"
    assert "’s" not in got["title"] and "’s" not in got["eyebrow"]
    assert "What the rows are: 1 spending → a movement · Sample auto loan." in text
    assert KEPT_SAID in text
    assert "stays spending:" not in text and "; undo" not in text      # one plain sentence, no run-on
    assert "<ul class=\"diff\"></ul>" not in got["body"]          # no empty list of changes


@needs_node
def test_a_teach_preview_naming_its_wording_is_titled_by_it_and_lists_the_flows():
    got = drawn({**FLOWS_ONLY, "change": [{"field": "wording", "from": None, "to": "SAMPLE HP AGREEMENT"}]})
    assert got["title"] == "Teach Sample Auto Finance the wording SAMPLE HP AGREEMENT?"
    assert "What the rows are: 1 spending → a movement · Sample auto loan." in _text(got["body"])


@needs_node
def test_a_merchant_or_merge_preview_with_a_label_change_lists_its_flows_too():
    merchant = drawn({**FLOWS_ONLY, "change": [{"field": "book", "from": "Household", "to": "Moom"}]})
    assert merchant["title"] == "Change Sample Auto Finance’s book to Moom?"
    merge = drawn({**FLOWS_ONLY, "subject": {"kind": "merge", "id": 4, "name": "Sample Auto Finance",
                                             "into": {"id": 5, "name": "Sample Finance"}},
                   "change": [{"field": "book", "from": "Household", "to": "Household"}]})
    assert merge["title"] == "Merge Sample Auto Finance into Sample Finance?"
    for got in (merchant, merge):
        text = _text(got["body"])
        assert "What the rows are: 1 spending → a movement · Sample auto loan." in text
        assert KEPT_SAID in text


@needs_node
def test_a_slot_preview_lists_flows_when_it_carries_them():
    slot = {"subject": {"kind": "slot", "id": "loan_interest", "name": "loan interest"}, "past_known": True,
            "count": 1, "examples": FLOWS_ONLY["examples"], "flows": [FLOW_LINE], "flows_kept": []}
    got = _node(_preview_js() + f"\nconsole.log(JSON.stringify(slotParts({json.dumps(slot)}, 'Interest', 'Fees')));")
    assert "What the rows are: 1 spending → a movement · Sample auto loan." in _text(got["body"])


@needs_node
def test_the_resolves_toast_counts_the_taught_rows_flow_and_the_rows_kept():
    code = "\n".join([_preview_js(), "function sentence(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }",
        _function("flowCounts"), _function("previewedWords"), _function("doneWords"), _function("resolvedWords"),
        _async_function("saveResolve"), r"""
const toasts = [];
const toast = (t, o) => toasts.push(t);
const refreshFrame = () => {}, closeSheet = () => {}, rerender = () => {};
async function servicesList() { return []; }
async function send() { return { ok: true, data: {} }; }
let answer;
async function sendPreviewed() { return answer; }
(async () => {
    answer = { ok: true, change: 5, data: { backfilled: 0, backfilled_ids: [],
        flows: [ """ + json.dumps({**FLOW_LINE, "id": 11, "description": "SAMPLE HP AGREEMENT 1021"}) + r""" ],
        flows_kept: [ """ + json.dumps(KEPT_LINE) + r""" ] } };
    await saveResolve({ tx_id: 11, service_name: 'Sample Auto Finance', apply_scope: 'service_default' });
    answer = { ok: true, change: 6, data: { backfilled: 0, backfilled_ids: [], flows: [] } };
    await saveResolve({ tx_id: 11, service_name: 'Sample Auto Finance', apply_scope: 'service_default' });
    console.log(JSON.stringify(toasts));
})();
"""])
    moved, plain = _node(code)
    # Counts only: the rows themselves are the drawer's.
    assert moved == "Saved. 1 row is now a movement; 1 row kept as it is."
    assert plain == "Saved"


def test_every_resolve_toast_goes_through_the_one_wording():
    home = APP_JS[APP_JS.index("ACT['home-guess-yes'] ="):APP_JS.index("ACT['looks-right'] =")]
    assert "resolvedWords(r, 'Labelled')" in home
    assert "resolvedWords(r, 'Saved')" in _async_function("saveResolve")


@needs_node
def test_the_routes_flows_only_teach_preview_draws_with_its_wording(client):
    """The route's own preview, drawn: a teach whose only question is a
    backfilled row's flow names the wording taught, and the drawer says it."""
    from test_movements_and_review import BANK, bring_in, make_account, row
    from test_rule_keeps_review_hold import add_rule, make_merchant
    make_account(client, BANK, "bank", last_four="0002")
    loan = make_account(client, "Sample auto loan", "loan")
    svc = make_merchant(client, "Sample Auto Finance", "Transport")
    add_rule(client, "SAMPLE AUTO FINANCE", svc, flow="movement", other_side_id=loan)
    bring_in(client, BANK, [(3, "SAMPLE HP AGREEMENT 1021", 1850.00), (4, "SAMPLE HP AGREEMENT 1022", 1850.00)])
    first = row(client, "SAMPLE HP AGREEMENT 1021")["id"]

    asked = client.post("/api/transactions/resolve", json={
        "tx_id": first, "service_id": svc, "pattern": "SAMPLE HP AGREEMENT", "match_type": "contains",
        "apply_scope": "service_default"})

    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    assert preview["change"] == [{"field": "wording", "from": None, "to": "SAMPLE HP AGREEMENT"}]
    got = drawn(preview)
    assert got["title"] == "Teach Sample Auto Finance the wording SAMPLE HP AGREEMENT?"
    assert "What the rows are: 1 spending → a movement · Sample auto loan." in _text(got["body"])


# --- PR #31 v2: short flow names, the overrides tag, counts-only toasts -----------------------

SHORT_FLOWS = ["What fin works out", "spending", "income", "a refund", "a transfer", "a card payment", "a movement"]


@needs_node
def test_the_rule_flow_picker_names_each_flow_in_short_words():
    got = _node("\n".join([_const("RULE_FLOWS"), "console.log(JSON.stringify(RULE_FLOWS.map(f => f[1])));"]))
    assert got == SHORT_FLOWS
    sheet = APP_JS[APP_JS.index("ACT.rule = async"):APP_JS.index("ACT['rule-save'] =")]
    # Any help goes in one hint line under the picker and its other side, never in an option.
    picker = sheet[sheet.index('id="rl-flow"'):sheet.index('id="rl-min"')]
    assert picker.count('class="hint"') == 1


RULES_PRELUDE = PREVIEW_PRELUDE + r"""
const tag = (kind, text) => `[${kind}:${text}]`;
const r = { typeById: new Map([[3, { display_name: 'Transport' }]]) };
"""


def rule_sets(rule):
    base = {"book": "Household", "book_override": None, "type_override_id": None, "type_name": "Transport",
            "display_type": "Transport", "min_amount": None, "max_amount": None, "flow": None,
            "other_side_name": None, **rule}
    return _node("\n".join([RULES_PRELUDE, _const("RULE_FLOWS"), _function("toMinor"), _function("ruleSets"),
                            f"console.log(JSON.stringify(ruleSets({json.dumps(base)}, r)));"]))


@needs_node
def test_the_rules_list_says_a_rules_flow_in_short_words_and_a_flow_alone_overrides_nothing():
    assert rule_sets({"flow": "movement", "other_side_name": "DBS auto loan"}) == \
        "Household · Transport · a movement · DBS auto loan"
    assert rule_sets({"flow": "transfer", "other_side_name": "Sample bank"}) == \
        "Household · Transport · a transfer · Sample bank"


@needs_node
@pytest.mark.parametrize("override", [{"book_override": "Moom", "book": "Moom"}, {"type_override_id": 3}])
def test_the_overrides_tag_is_only_for_a_rules_own_book_or_type(override):
    assert rule_sets(override).endswith(" [yours:overrides]")
    assert rule_sets({**override, "flow": "movement", "other_side_name": "DBS auto loan"}).endswith(
        " [yours:overrides]")


TOAST_FUNCS = ["flowCounts", "previewedWords", "doneWords", "resolvedWords", "previewedDone"]


def _toast_js():
    return "\n".join([_preview_js(), "function sentence(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }",
                      *map(_function, TOAST_FUNCS),
                      "const toasts = []; const toast = (t, o) => toasts.push([t, o]); const refreshFrame = () => {};"])


def moved_to(after, n, first_id=100):
    return [{**FLOW_LINE, "id": first_id + i, "after": after} for i in range(n)]


@needs_node
def test_a_saved_rules_toast_says_counts_only():
    answered = {"past": "apply", "count": 8, "flows": moved_to("a movement · DBS auto loan", 7)}
    code = _toast_js() + f"""
previewedDone({{ ok: true, change: 7, answered: {json.dumps(answered)},
                data: {{ recategorized: 8, flows_kept: [{json.dumps(KEPT_LINE)}] }} }}, 'Rule saved');
previewedDone({{ ok: true, change: 8, data: {{ recategorized: 0, flows_kept: [{json.dumps(KEPT_LINE)}] }} }}, 'Rule added');
previewedDone({{ ok: true, change: 9, answered: {{ past: 'apply', count: 3 }}, data: {{}} }}, 'Rule saved');
console.log(JSON.stringify(toasts));"""
    saved, added, plain = _node(code)
    assert saved == ["Rule saved. 7 rows are now a movement; 1 row kept as it is.", {"undo": 7}]
    assert added == ["Rule added. 1 row kept as it is.", {"undo": 8}]
    # No flow moved or kept: the toast is what it was.
    assert plain[0] == "Rule saved, and 3 past rows changed. One change in Changes, with Undo."


@needs_node
def test_a_toast_counts_rows_relabelled_beyond_the_flows_and_each_flow_reached():
    answered = {"past": "apply", "count": 12,
                "flows": moved_to("a movement · DBS auto loan", 7) + moved_to("a transfer · Sample bank", 2, 200)}
    code = _toast_js() + f"""
previewedDone({{ ok: true, change: 7, answered: {json.dumps(answered)}, data: {{ flows_kept: [{json.dumps(KEPT_LINE)}] }} }}, 'Rule saved');
console.log(JSON.stringify(toasts[0][0]));"""
    assert _node(code) == ("Rule saved, and 12 past rows changed. "
                           "7 rows are now a movement and 2 a transfer; 1 row kept as it is.")


@needs_node
def test_the_toast_after_the_drawer_comes_from_the_drawers_own_flows():
    """sendPreviewed carries the preview's flows to its answer: the toast
    counts what the drawer listed and Apply wrote."""
    code = "\n".join([_toast_js(), _async_function("sendPreviewed"), r"""
const preview = { count: 2, token: 't', flows: [ """ + json.dumps(FLOW_LINE) + ", " + json.dumps({**FLOW_LINE, "id": 14}) + r""" ] };
let calls = 0;
async function send() { calls += 1; return calls === 1 ? { ok: false, status: 409, data: { preview } } : { ok: true, status: 200, change: 4, data: {} }; }
async function askPreview() { return 'apply'; }
(async () => {
    const r = await sendPreviewed('POST', '/api/rules', {});
    previewedDone(r, 'Rule saved');
    console.log(JSON.stringify(toasts[0][0]));
})();
"""])
    assert _node(code) == "Rule saved. 2 rows are now a movement."


@needs_node
def test_a_teachs_two_toasts_agree_on_the_rows_it_labelled(client):
    """The route's own answer, toasted as saveResolve toasts it: the taught
    row and one more are now a movement, one is kept, and the second toast
    counts the two other rows, never the taught row itself."""
    from test_movements_and_review import BANK, bring_in, make_account, row
    from test_rule_keeps_review_hold import add_rule, make_merchant
    from test_rule_flow_every_path import mark
    make_account(client, BANK, "bank", last_four="0002")
    friend = make_account(client, "Sample Friend", "person")
    loan = make_account(client, "Sample auto loan", "loan")
    svc = make_merchant(client, "Sample Auto Finance", "Transport")
    add_rule(client, "SAMPLE AUTO FINANCE", svc, flow="movement", other_side_id=loan)
    bring_in(client, BANK, [(3, "SAMPLE HP AGREEMENT 1021", 1850.00), (4, "SAMPLE HP AGREEMENT 1022", 1850.00),
                            (5, "SAMPLE HP AGREEMENT 1023", 1850.00)])
    first = row(client, "SAMPLE HP AGREEMENT 1021")["id"]
    mark(client, row(client, "SAMPLE HP AGREEMENT 1023")["id"], friend)
    teach = {"tx_id": first, "service_id": svc, "service_name": "Sample Auto Finance",
             "pattern": "SAMPLE HP AGREEMENT", "match_type": "contains", "apply_scope": "service_default"}
    asked = client.post("/api/transactions/resolve", json=teach)
    assert asked.status_code == 409, asked.get_json()
    preview = asked.get_json()["preview"]
    answer = client.post("/api/transactions/resolve", json={**teach, "past_rows": "apply", "token": preview["token"]})
    assert answer.status_code == 200, answer.get_json()
    assert first not in answer.get_json()["backfilled_ids"]

    code = "\n".join([_toast_js(), _async_function("sendPreviewed"), _async_function("saveResolve"), r"""
const preview = """ + json.dumps(preview) + r""", answer = """ + json.dumps(answer.get_json()) + r""";
const closeSheet = () => {}, rerender = () => {};
async function servicesList() { return [{ id: """ + str(svc) + r""", name: 'Sample Auto Finance' }]; }
let calls = 0;
async function send() { calls += 1; return calls === 1 ? { ok: false, status: 409, data: { preview } } : { ok: true, status: 200, change: 4, data: answer }; }
async function askPreview() { return 'apply'; }
(async () => {
    await saveResolve(""" + json.dumps(teach) + r""");
    console.log(JSON.stringify(toasts.map(t => t[0])));
})();
"""])
    assert _node(code) == ["Saved. 2 rows are now a movement; 1 row kept as it is.", "2 other rows took the same label"]
