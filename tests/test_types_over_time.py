"""Ticket 47: Types over time (static/types-over-time.js), figures in and figures out.

The page asks R2 (/api/trend/types) for one book at a time and mounts the trend card
(ticket 36) on that payload, with mode 'line' (ticket 38) for the lines and its own
'multiples' mode for the small charts. Its public functions are run here under node,
with app.js's real money and esc lifted out of the file and the real trend.js and
trend-line.js loaded, so the lines are the ones the card would draw from the payload
the page hands it. The SVG is seen in Chromium (ticket 48), never asserted here.
Every figure is invented, on the shape of tests/fixtures/trend/trend_types.json.
"""

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
FIXTURES = Path(__file__).parent / "fixtures" / "trend"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def _lift(pattern):
    m = re.search(pattern, APP_JS, re.M | re.S)
    assert m, pattern
    return m.group(0)


# The globals the page reads from app.js. registerRoute and booksNavExtra are recorders,
# so the test can see what the page registered; store answers what `stored` holds.
PRELUDE = "\n".join([
    "globalThis.window = globalThis;",
    _lift(r"^const SIGNS = .*?;$"), _lift(r"^const DIGITS = .*?;$"), _lift(r"^const formatters = .*?;$"),
    _lift(r"^function digitsOf\(.*?\}$"), _lift(r"^function groupFor\(.*?^\}$"),
    _lift(r"^function money\(.*?^\}$"), _lift(r"^function esc\(.*?^\}$"),
    "const routes = []; window.registerRoute = (re, view) => routes.push([re, view]);",
    "window.booksNavExtra = [];",
    "window.pageLinkKinds = { merchant: id => `#/books/merchant/${id}`, account: id => `#/books/account/${id}`, type: null };",
    "let stored = null; const store = { get: (k, f) => { if (stored === 'throw') throw new Error('private'); return stored === null ? f : stored; }, set: () => {} };",
    "const icon = n => `<i data-icon=\"${n}\"></i>`;",
    (STATIC / "trend.js").read_text(encoding="utf-8"),
    (STATIC / "trend-line.js").read_text(encoding="utf-8"),
    (STATIC / "types-over-time.js").read_text(encoding="utf-8"),
    "const T = window.TypesOverTime;",
])


def run(body):
    code = PRELUDE + "\n" + body
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def load(name="trend_types"):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def lines(payload, state=None):
    """The lines the card draws from the payload the page hands it: the card's view,
    then the line mode's model, as the card calls them."""
    return run(f"const p = {json.dumps(payload)};"
               f"console.log(JSON.stringify(window.Trend.lineModel(window.Trend.view(p, {{}}), {json.dumps(state or {})})));")


def line(model, name):
    return next(x for x in model["lines"] if x["name"] == name)


def case_39_payload():
    """Household, 12M, with case 39's four nets on the top four."""
    p = load()
    for t in p["types"]:
        t["net"] = {"Dining": 772000, "Groceries": 648000, "Travel": 576000, "Education": 360000}.get(t["name"], t["net"])
    return p


def twelve_types():
    """Case 42: twelve types with rows in the span, plus one with none. Dining tops at
    S$ 880.00 in December 2025; Insurance's own top is S$ 300.00."""
    p = load()
    base = p["types"][0]
    names = ["Dining", "Groceries", "Travel", "Education", "Insurance", "Utilities", "Gifts",
             "Transport", "Health", "Books", "Household goods", "Shopping"]
    types = []
    for i, name in enumerate(names):
        t = copy.deepcopy(base)
        t.update(id=100 + i, name=name, slot=i + 1 if i < 4 else None, net=100000 - i * 1000)
        for pt in t["points"]:
            pt.update(net=None if pt["missing"] else 10000 + i * 1000, estimate=None)
        types.append(t)
    dining = types[0]
    next(pt for pt in dining["points"] if pt["month"] == "2025-12")["net"] = 88000
    insurance = types[4]
    next(pt for pt in insurance["points"] if pt["month"] == "2026-03")["net"] = 30000
    quiet = copy.deepcopy(base)
    quiet.update(id=200, name="Pets", slot=None, net=0)
    for pt in quiet["points"]:
        pt.update(net=None, estimate=None)
    p["types"] = types + [quiet]
    return p


# --- the route and its tab ---------------------------------------------------------

@needs_node
def test_the_page_registers_its_route_and_its_tab():
    out = run("console.log(JSON.stringify({n: routes.length, types: routes[0][0].test('/books/types'),"
              " type: routes[0][0].test('/books/type/7'), nav: window.booksNavExtra}));")
    assert out["n"] == 1 and out["types"] is True and out["type"] is False
    assert len(out["nav"]) == 1
    key, href, label, _icon = out["nav"][0]
    assert (key, href, label) == ("types", "#/books/types", "Types over time")


# --- case 24: the remembered span; case 43: one book at a time -----------------------

@needs_node
def test_the_request_follows_the_remembered_span_and_names_one_book():
    out = run("const a = T.request('Household');"
              "stored = '2Y'; const b = T.request('Larkspur');"
              "stored = 'throw'; const c = T.request('Tessel & Co');"
              "stored = '9M'; const d = T.request('Household');"
              "console.log(JSON.stringify([a, b, c, d]));")
    assert out == ["/api/trend/types?book=Household&span=12M", "/api/trend/types?book=Larkspur&span=2Y",
                   "/api/trend/types?book=Tessel%20%26%20Co&span=12M", "/api/trend/types?book=Household&span=12M"]


@needs_node
def test_case_43_the_book_picker_shows_one_book_and_household_first():
    books = [{"name": "Household"}, {"name": "Larkspur"}, {"name": "Tessel"}]
    out = run(f"const b = {json.dumps(books)};"
              "console.log(JSON.stringify([T.bookChoices(b, null), T.bookChoices(b, 'Larkspur'), T.bookChoices(b, 'Gone'),"
              " T.bookChoices([{name: 'Larkspur'}, {name: 'Tessel'}], null)]));")
    first, lark, gone, no_household = out
    assert [c["name"] for c in first if c["pressed"]] == ["Household"]
    assert [c["name"] for c in lark if c["pressed"]] == ["Larkspur"]
    assert [c["name"] for c in gone if c["pressed"]] == ["Household"]
    assert [c["name"] for c in no_household if c["pressed"]] == ["Larkspur"]


@needs_node
def test_case_43_the_card_is_mounted_on_the_one_books_payload_with_mode_line():
    p = load()
    out = run(f"const p = {json.dumps(p)}; const seen = [];"
              "window.Trend.card = (el, payload, opts) => { seen.push([payload.book, payload.types.length, opts.mode]); return {}; };"
              "T.show({}, p, {multiples: false}); T.show({}, p, {multiples: true});"
              "const legend = window.Trend.legend(p);"
              "console.log(JSON.stringify({seen, legend}));")
    assert out["seen"] == [["Household", 7, "line"], ["Household", 7, "multiples"]]
    # No book chips on this page: Larkspur is never a line beside Household's.
    assert out["legend"] == []


# --- cases 39, 40, 41, 44, 55, 57: the lines the card draws from R2 -------------------

@needs_node
def test_case_39_every_type_a_line_the_top_four_coloured_with_their_nets():
    m = lines(case_39_payload())
    assert len(m["lines"]) == 7
    assert [(x["name"], x["colour"], x["net_text"]) for x in m["lines"][:4]] == [
        ("Dining", "s1", "S$ 7,720.00"), ("Groceries", "s2", "S$ 6,480.00"),
        ("Travel", "s3", "S$ 5,760.00"), ("Education", "s4", "S$ 3,600.00")]
    assert {x["colour"] for x in m["lines"][4:]} == {"grey"}


@needs_node
def test_case_40_a_line_taken_off_does_not_promote_the_fifth():
    m = lines(case_39_payload(), {"off": [9]})          # Travel's chip pressed
    assert line(m, "Travel")["off"] is True
    assert line(m, "Insurance")["colour"] == "grey"
    assert m["any_off"] is True
    back = lines(case_39_payload(), {"off": []})       # Show every line
    assert back["any_off"] is False and not any(x["off"] for x in back["lines"])


@needs_node
def test_case_41_picking_groceries_raises_it_and_dims_the_rest():
    m = lines(case_39_payload(), {"picked": 8})
    assert line(m, "Groceries")["picked"] is True and line(m, "Groceries")["dim"] is False
    assert all(x["dim"] for x in m["lines"] if x["name"] != "Groceries")


@needs_node
def test_case_44_a_month_of_refunds_dips_below_zero():
    p = load()
    gifts = next(t for t in p["types"] if t["name"] == "Gifts")
    next(pt for pt in gifts["points"] if pt["month"] == "2026-03")["net"] = -8000
    m = lines(p)
    assert next(pt for pt in line(m, "Gifts")["points"] if pt["month"] == "2026-03")["value"] == -8000
    assert m["scale"]["lo"] < 0


@needs_node
def test_case_55_the_missing_july_is_drawn_as_its_estimate_and_counted_nowhere():
    m = lines(load())
    g = line(m, "Groceries")
    july = next(pt for pt in g["points"] if pt["month"] == "2026-07")
    assert july == {"month": "2026-07", "value": 54000, "estimate": True}
    assert g["dashed"] == [["2026-06", "2026-07", "2026-08"]]
    # The legend total is the server's net, which has no estimate in it.
    assert g["net_text"] == "S$ 5,940.00"
    assert m["missing"] == ["2026-07"]


@needs_node
def test_case_57_colours_follow_the_type_across_spans():
    six = load()
    slots = {"Groceries": 2, "Dining": 1, "Insurance": 4, "Travel": 3, "Education": None}
    for t in six["types"]:
        t["slot"] = slots.get(t["name"], t["slot"])
    order = ["Groceries", "Dining", "Insurance", "Travel"]
    six["types"].sort(key=lambda t: order.index(t["name"]) if t["name"] in order else 9)
    m = lines(six)
    assert {x["name"]: x["colour"] for x in m["lines"]} == {
        "Groceries": "s2", "Dining": "s1", "Insurance": "s4", "Travel": "s3",
        "Education": "grey", "Utilities": "grey", "Gifts": "grey"}


# --- case 42: small multiples --------------------------------------------------------

@needs_node
def test_case_42_small_multiples_one_per_type_with_rows_shared_or_own_scale():
    p = twelve_types()
    out = run(f"const p = {json.dumps(p)};"
              "console.log(JSON.stringify([T.multiples(p, 'shared'), T.multiples(p, 'own')]));")
    shared, own = out
    assert len(shared) == 12 and "Pets" not in [c["name"] for c in shared]
    assert {c["top"] for c in shared} == {88000}
    assert {c["top_text"] for c in shared} == {"S$ 880.00"}
    by = {c["name"]: c for c in own}
    assert by["Insurance"]["top"] == 30000 and by["Insurance"]["top_text"] == "S$ 300.00"
    assert by["Dining"]["top"] == 88000
    assert [c["colour"] for c in shared[:5]] == ["s1", "s2", "s3", "s4", "grey"]


@needs_node
def test_case_42_a_small_chart_below_zero_keeps_its_zero_line():
    p = twelve_types()
    next(pt for pt in p["types"][6]["points"] if pt["month"] == "2026-02")["net"] = -5000
    out = run(f"const p = {json.dumps(p)}; console.log(JSON.stringify(T.multiples(p, 'own')));")
    gifts = next(c for c in out if c["name"] == "Gifts")
    assert gifts["bottom"] == -5000 and gifts["top"] == 16000


@needs_node
def test_case_42_a_tap_opens_the_type_page_once_type_pages_are_on():
    p = twelve_types()
    out = run(f"const p = {json.dumps(p)};"
              "const before = T.multiples(p, 'shared').map(c => c.href);"
              "window.pageLinkKinds.type = id => `#/books/type/${id}`;"
              "const after = T.multiples(p, 'shared').map(c => c.href);"
              "console.log(JSON.stringify([before, after]));")
    before, after = out
    assert set(before) == {None}
    assert after[0] == "#/books/type/100" and len(after) == 12


# --- case 58: the legend's link icon -----------------------------------------------

@needs_node
def test_case_58_each_legend_type_carries_a_link_to_its_page():
    p = load()
    out = run(f"const p = {json.dumps(p)};"
              "const off = T.links(p);"
              "window.pageLinkKinds.type = id => `#/books/type/${id}`;"
              "console.log(JSON.stringify([off, T.links(p)]));")
    off, on = out
    assert off == []           # type pages not on yet: no link that lands on nothing
    assert on[:2] == [{"id": 7, "name": "Dining", "href": "#/books/type/7"},
                      {"id": 8, "name": "Groceries", "href": "#/books/type/8"}]
    assert len(on) == 7


# --- the stylesheet: the types block only --------------------------------------------

def test_the_types_block_holds_only_this_pages_classes():
    css = (STATIC / "trend.css").read_text(encoding="utf-8")
    block = re.search(r"/\* ==== reserved: types ==== \*/(.*?)/\* ==== end: types ==== \*/", css, re.S)
    assert block, "the types block is in trend.css"
    rules = re.sub(r"/\*.*?\*/", "", block.group(1), flags=re.S)
    selectors = [s.strip() for chunk in re.findall(r"([^{}]+)\{", rules) for s in chunk.split(",")]
    selectors = [s for s in selectors if s and not s.startswith("@")]
    assert selectors, "the types block is filled"
    assert all(s.startswith(".trend-types") for s in selectors), selectors
