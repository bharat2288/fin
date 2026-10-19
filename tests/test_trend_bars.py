"""Ticket 37: the trend card's bars (static/trend-bars.js).

The drawer splits in two: Trend.bars.model(payload, opts) works out every figure
the bars show (segment extents, the refund cap, the hatch, the hover lines, what
a click does), and the SVG is drawn from that model. These tests run the real
trend.js and trend-bars.js under node, with the real `money` and `esc` lifted out
of static/app.js, and read figures in and out through the model. They never read
the SVG's structure. Payloads are in R1's shape: the shared fixtures where they
carry the case, built here where they do not. Every figure is invented.
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
FIXTURES = ROOT / "tests" / "fixtures" / "trend"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

# fin's money() puts the sign after the symbol ("S$ −560.00"), on every screen; the
# spec's case text writes "−S$ 560.00". The house formatter is kept (ticket 37 deviation).
MINUS = "−"
HATCH_TEXT = "No statement in yet for this month on a card this page uses. Import to fill it."


def _lift(pattern):
    m = re.search(pattern, APP_JS, re.M | re.S)
    assert m, pattern
    return m.group(0)


# The real formatters from app.js: trend-bars.js declares them and copies none.
PRELUDE = "\n".join([
    "globalThis.window = globalThis;",
    _lift(r"^const SIGNS = .*?;$"),
    _lift(r"^const DIGITS = .*?;$"),
    _lift(r"^const formatters = .*?;$"),
    _lift(r"^function digitsOf\(.*?\}$"),
    _lift(r"^function groupFor\(.*?^\}$"),
    _lift(r"^function money\(.*?^\}$"),
    _lift(r"^function esc\(.*?^\}$"),
    (STATIC / "trend.js").read_text(encoding="utf-8"),
    (STATIC / "trend-bars.js").read_text(encoding="utf-8"),
])


def _node(body):
    # The script goes in on stdin: on Windows a long -e argument passes the command-line limit.
    out = subprocess.run([NODE, "-"], input=PRELUDE + "\n" + body, capture_output=True, text=True,
                         timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def model(payload, opts=None):
    return _node(f"console.log(JSON.stringify(window.Trend.bars.model({json.dumps(payload)}, {json.dumps(opts or {})})));")


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def bucket(m, key):
    return next(b for b in m["buckets"] if b["key"] == key)


def seg(b, key):
    return next(s for s in b["segments"] if s["key"] == key)


def cell(book, spent, back, inr=None):
    return {"book": book, "spent": spent, "back": back, "net": spent - back, "inr_minor": inr}


def month_bucket(month, label, by, missing=None, so_far=False, rate=None, no_rate=False):
    return {"key": month, "label": label, "months": [month], "so_far": so_far, "missing": missing or {},
            "no_rate": no_rate, "rate": rate, "by": by}


def payload(buckets, stack_keys, books, stack="book", **extra):
    p = {"subject": {"kind": "merchant", "id": 1, "name": "Sample"}, "span": "12M", "to": "2026-10",
         "stack": stack, "currency": "SGD", "buckets": buckets,
         "books": books, "stack_keys": [{"key": k, "label": k, "id": None} for k in stack_keys],
         "stack_options": [], "left_out": [], "few": None, "empty": None}
    p.update(extra)
    return p


def book_line(book, net, avg, months):
    return {"book": book, "net": net, "spent": net, "back": 0, "avg": avg, "months_counted": months,
            "vs_prev_pct": None, "rows": 10, "last_date": "2026-10-12"}


# ---------------------------------------------------------------------------
# The mode registers itself
# ---------------------------------------------------------------------------

def test_bars_register_as_a_trend_mode():
    assert _node("console.log(JSON.stringify([typeof window.Trend.modes.bars, typeof window.Trend.bars.model]))") \
        == ["function", "function"]


# ---------------------------------------------------------------------------
# Case 5: the cap and the hover
# ---------------------------------------------------------------------------

def test_case_5_the_cap_reaches_what_was_spent_and_the_hover_reads_spent_refunded_net():
    sep = bucket(model(fixture("trend_merchant")), "2026-09")
    household = seg(sep, "Household")
    assert (household["y0"], household["y1"]) == (0, 19440)
    assert household["cap"] == [19440, 21240]
    assert sep["hover"]["lines"][:2] == [
        "Household · spent S$ 212.40 · refunded S$ 18.00 · net S$ 194.40",
        "Larkspur · net S$ 61.00",
    ]
    assert seg(sep, "Larkspur")["cap"] is None


def test_case_1_no_grand_total_in_the_hover():
    sep = bucket(model(fixture("trend_merchant")), "2026-09")
    assert not any("255.40" in line for line in sep["hover"]["lines"])


# ---------------------------------------------------------------------------
# Case 6: a refund larger than the month's spend
# ---------------------------------------------------------------------------

AMAZON_TYPES = ["Groceries", "Household goods", "Books"]


def amazon_by_type():
    return payload([
        month_bucket("2026-07", "Jul 2026", {"Household goods": cell("Household", 62000, 0),
                                             "Groceries": cell("Household", 8000, 0)}),
        month_bucket("2026-08", "Aug 2026", {"Groceries": cell("Household", 9500, 0),
                                             "Household goods": cell("Household", 3000, 59000)}),
    ], AMAZON_TYPES, [book_line("Household", 23500, 11750, 2)], stack="type")


def test_case_6_by_type_the_refund_goes_below_the_line_and_groceries_stays_above():
    m = model(amazon_by_type())
    aug = bucket(m, "2026-08")
    assert (seg(aug, "Groceries")["y0"], seg(aug, "Groceries")["y1"]) == (0, 9500)
    goods = seg(aug, "Household goods")
    assert (goods["y0"], goods["y1"]) == (-56000, 0)
    assert m["min"] <= -56000
    assert f"Household goods · spent S$ 30.00 · refunded S$ 590.00 · net S$ {MINUS}560.00" in aug["hover"]["lines"]


def test_case_6_by_book_august_is_below_the_line_and_july_keeps_its_purchase():
    p = payload([
        month_bucket("2026-07", "Jul 2026", {"Household": cell("Household", 70000, 0)}),
        month_bucket("2026-08", "Aug 2026", {"Household": cell("Household", 12500, 59000)}),
    ], ["Household"], [book_line("Household", 23500, 11750, 2)])
    m = model(p)
    aug = seg(bucket(m, "2026-08"), "Household")
    assert (aug["y0"], aug["y1"]) == (-46500, 0)
    jul = seg(bucket(m, "2026-07"), "Household")
    assert (jul["y0"], jul["y1"]) == (0, 70000)
    assert f"Household · spent S$ 125.00 · refunded S$ 590.00 · net S$ {MINUS}465.00" in bucket(m, "2026-08")["hover"]["lines"]


# ---------------------------------------------------------------------------
# Case 20: All, in quarters, one under way
# ---------------------------------------------------------------------------

def quarters_payload():
    buckets = []
    for year in (2024, 2025, 2026):
        for q in range(1, 5):
            first = (q - 1) * 3 + 1
            months = [f"{year}-{m:02d}" for m in range(first, first + 3)]
            if (year, q) == (2026, 4):
                months = ["2026-10"]
            missing = {"Household": ["2026-07"]} if (year, q) == (2026, 3) else {}
            net = 44440 if (year, q) == (2026, 3) else 64800
            buckets.append({"key": f"{year}-Q{q}", "label": f"Q{q} {year}", "months": months,
                            "so_far": (year, q) == (2026, 4), "missing": missing,
                            "no_rate": False, "rate": None,
                            "by": {"Household": cell("Household", net, 0)}})
    return payload(buckets, ["Household"], [book_line("Household", 712800, 21600, 33)], span="All")


def test_case_20_twelve_quarter_bars_and_the_one_under_way_is_outlined():
    m = model(quarters_payload())
    assert [b["key"] for b in m["buckets"]][0] == "2024-Q1" and len(m["buckets"]) == 12
    q4 = bucket(m, "2026-Q4")
    assert q4["outlined"] is True
    assert "1 month of this quarter so far" in q4["hover"]["lines"]
    assert not any(b["outlined"] for b in m["buckets"] if b["key"] != "2026-Q4")


def test_case_20_one_book_showing_draws_the_average_times_three():
    m = model(quarters_payload())
    assert m["avg_line"] == 64800


def test_the_average_line_takes_the_servers_factor_when_it_sends_one():
    p = quarters_payload()
    p["line_factor"] = 3
    assert model(p)["avg_line"] == 64800


def test_two_books_showing_draw_no_average_line_and_one_does():
    p = fixture("trend_merchant")
    assert model(p)["avg_line"] is None
    assert model(p, {"books": ["Household"]})["avg_line"] == 24000


# ---------------------------------------------------------------------------
# Case 25: a bar narrows the rows
# ---------------------------------------------------------------------------

def test_case_25_a_month_bar_narrows_to_its_month():
    sep = bucket(model(fixture("trend_merchant")), "2026-09")
    assert sep["click"] == {"kind": "narrow", "months": ["2026-09"], "label": "September 2026"}
    assert seg(sep, "Household")["click"] == sep["click"]


def test_case_25_a_quarter_bar_narrows_to_its_months():
    q1 = bucket(model(quarters_payload()), "2025-Q1")
    assert q1["click"]["months"] == ["2025-01", "2025-02", "2025-03"]
    assert seg(q1, "Household")["click"]["months"] == ["2025-01", "2025-02", "2025-03"]


def test_case_25_a_hatched_segment_goes_to_import_and_the_other_book_narrows():
    jul = bucket(model(fixture("trend_merchant")), "2026-07")
    assert seg(jul, "Household")["click"] == {"kind": "import"}
    assert seg(jul, "Larkspur")["click"]["months"] == ["2026-07"]


def test_case_25_the_type_page_bar_narrows_too():
    sep = bucket(model(fixture("trend_type")), "2026-09")
    assert sep["click"]["months"] == ["2026-09"]


# ---------------------------------------------------------------------------
# Cases 26, 27: rupees in S$ at the month-end rate
# ---------------------------------------------------------------------------

def test_case_26_the_hover_names_the_month_end_rate():
    sep = bucket(model(fixture("trend_type")), "2026-09")
    assert "Includes ₹ 3,450 at the month-end rate 0.0155 (30 Sep 2026)" in sep["hover"]["lines"]


def test_case_27_an_earlier_rate_is_named_by_its_date():
    aug = bucket(model(fixture("trend_type")), "2026-08")
    line = next(x for x in aug["hover"]["lines"] if x.startswith("Includes"))
    assert "₹ 2,000" in line and "rate of 28 Aug 2026" in line and "0.0154" in line
    assert "month-end" not in line


def test_rupees_group_in_lakhs():
    p = payload([month_bucket("2026-09", "Sep 2026", {"Household": cell("Household", 192975, 0, inr=12450000)},
                              rate={"pair": "INR/SGD", "date": "2026-09-30", "exact": True, "text": "0.0155"})],
                ["Household"], [book_line("Household", 192975, 192975, 1)])
    lines = bucket(model(p), "2026-09")["hover"]["lines"]
    assert "Includes ₹ 1,24,500 at the month-end rate 0.0155 (30 Sep 2026)" in lines


# ---------------------------------------------------------------------------
# Case 31: hatched segment
# ---------------------------------------------------------------------------

def test_case_31_the_missing_book_is_hatched_and_the_other_drawn_as_usual():
    jul = bucket(model(fixture("trend_merchant")), "2026-07")
    household = seg(jul, "Household")
    assert household["hatch"] == 1 and household["glyph"] == "⧗" and household["placeholder"] is True
    larkspur = seg(jul, "Larkspur")
    assert larkspur["hatch"] == 0 and (larkspur["y0"], larkspur["y1"]) == (0, 4200)
    assert jul["warn"] is True
    assert jul["hover"]["lines"].count(HATCH_TEXT) == 1
    assert "Larkspur · net S$ 42.00" in jul["hover"]["lines"]
    assert jul["hover"]["import"] is True
    assert bucket(model(fixture("trend_merchant")), "2026-06")["warn"] is False


def test_case_31_the_placeholder_is_never_a_figure_in_the_hover():
    jul = bucket(model(fixture("trend_merchant")), "2026-07")
    assert not any(line.startswith("Household") for line in jul["hover"]["lines"])


def test_case_31_with_the_missing_book_off_the_month_is_not_marked():
    jul = bucket(model(fixture("trend_merchant"), {"books": ["Larkspur"]}), "2026-07")
    assert jul["warn"] is False and HATCH_TEXT not in jul["hover"]["lines"]
    assert [s["key"] for s in jul["segments"]] == ["Larkspur"]


# ---------------------------------------------------------------------------
# Case 53: a quarter holding a missing month
# ---------------------------------------------------------------------------

def test_case_53_a_part_hatched_quarter():
    m = model(quarters_payload())
    q3 = bucket(m, "2026-Q3")
    household = seg(q3, "Household")
    assert (household["y0"], household["y1"]) == (0, 44440)
    assert abs(household["hatch"] - 1 / 3) < 1e-9 and household["placeholder"] is False
    assert "1 month in this quarter not imported" in q3["hover"]["lines"]
    assert HATCH_TEXT not in q3["hover"]["lines"]
    assert q3["hover"]["import"] is True
    assert household["click"]["months"] == ["2026-08", "2026-09"]
    assert q3["click"]["months"] == ["2026-08", "2026-09"]
    assert q3["click"]["label"] == "August and September 2026"
    assert m["avg_line"] == 64800


# ---------------------------------------------------------------------------
# Case 64 (and 28): missing and no rate in one month
# ---------------------------------------------------------------------------

def test_case_64_hatched_beats_the_no_rate_mark_and_the_hover_gives_both_reasons():
    jun = bucket(model(fixture("trend_type")), "2026-06")
    household = seg(jun, "Household")
    assert household["hatch"] == 1 and household["placeholder"] is False
    assert (household["y0"], household["y1"]) == (0, 62000)
    assert jun["no_rate_mark"] is False
    lines = jun["hover"]["lines"]
    nr = "₹ 2,100 not counted: no saved rate for June 2026. Fetch it."
    assert lines.index(HATCH_TEXT) < lines.index(nr)
    assert jun["hover"]["fetch"] == "2026-06"
    assert not any(line.startswith("Includes") for line in lines)


def test_case_28_a_no_rate_month_that_is_not_missing_carries_the_mark():
    p = copy.deepcopy(fixture("trend_type"))
    bucket(p, "2026-06")["missing"] = {}
    jun = bucket(model(p), "2026-06")
    assert jun["no_rate_mark"] is True
    assert "₹ 2,100 not counted: no saved rate for June 2026. Fetch it." in jun["hover"]["lines"]
    assert seg(jun, "Household")["click"]["months"] == ["2026-06"]


# ---------------------------------------------------------------------------
# The scale and the draw
# ---------------------------------------------------------------------------

def test_the_scale_takes_the_caps_and_the_line():
    m = model(fixture("trend_merchant"), {"books": ["Household"]})
    assert m["max"] >= max(b["by"]["Household"]["spent"] for b in fixture("trend_merchant")["buckets"] if "Household" in b["by"])
    assert m["min"] == 0


def test_draw_writes_an_svg_and_calls_back_on_click():
    # A minimal DOM stand-in: draw() builds markup and wires one delegated listener.
    out = _node(r"""
    const handlers = {};
    const el = { clientWidth: 640, innerHTML: '', style: {},
      addEventListener(t, f) { handlers[t] = f; },
      querySelector() { return { hidden: true, innerHTML: '', style: {}, textContent: '' }; } };
    const seen = [];
    const p = """ + json.dumps(fixture("trend_merchant")) + r""";
    window.Trend.modes.bars(el, p, { onMonth: (months, label) => seen.push(['narrow', months, label]),
                                     onImport: () => seen.push(['import']) });
    const target = (b, s) => ({ closest: sel => sel === '[data-seg]' && s !== undefined ? { dataset: { b: String(b), seg: String(s) } }
                                     : sel === '[data-b]' ? { dataset: { b: String(b) } } : null });
    handlers.click({ target: target(10, 0) });
    const hatched = window.Trend.bars.model(p).buckets[8].segments.findIndex(s => s.placeholder);
    handlers.click({ target: target(8, hatched) });
    console.log(JSON.stringify({ svg: el.innerHTML.includes('<svg'), seen }));
    """)
    assert out["svg"] is True
    assert out["seen"][0] == ["narrow", ["2026-09"], "September 2026"]
    assert out["seen"][1] == ["import"]

def test_the_card_hides_a_book_through_books_on():
    # The card (trend.js) passes the view with books_on and no opts.books; a book the
    # card has hidden must not be drawn.
    p = fixture("trend_merchant")
    shown = [b["book"] for b in p["books"]]
    assert len(shown) > 1
    p["books_on"] = shown[:1]
    m = model(p)
    keys = {s["key"] for b in m["buckets"] for s in b["segments"] if not s.get("placeholder")}
    assert keys and all(shown[0] in k or k == shown[0] for k in keys), keys


# ---------------------------------------------------------------------------
# Cases 16, 17: coloured by the type (or merchant), with Other its own tone.
# Walk finding: stack keys are per (book, key), and colouring by their position
# gave one type two colours across books and wrapped the fifth key (Other) onto
# the first merchant's colour.
# ---------------------------------------------------------------------------

def keyed(keys):
    return [{"key": k, "label": label, "id": i} for k, label, i in keys]


def test_case_16_one_type_keeps_one_colour_across_books():
    p = payload([month_bucket("2026-08", "Aug 2026", {
        "Household|type:22": cell("Household", 2000, 0), "Household|type:30": cell("Household", 3000, 0),
        "Household|type:14": cell("Household", 9500, 0), "Moom|type:22": cell("Moom", 1500, 0)})],
        [], [book_line("Household", 14500, 14500, 1), book_line("Moom", 1500, 1500, 1)], stack="type")
    p["stack_keys"] = keyed([("Household|type:22", "Entertainment", 22), ("Household|type:30", "Home", 30),
                             ("Household|type:14", "Groceries", 14), ("Moom|type:22", "Entertainment", 22)])
    aug = bucket(model(p), "2026-08")
    tones = {s["key"]: s["tone"] for s in aug["segments"]}
    assert tones == {"Household|type:22": "c1", "Household|type:30": "c2", "Household|type:14": "c3",
                     "Moom|type:22": "c1"}
    assert _node(f"console.log(JSON.stringify(window.Trend.bars.key({json.dumps(p)})))") == [
        {"label": "Entertainment", "tone": "c1"}, {"label": "Home", "tone": "c2"},
        {"label": "Groceries", "tone": "c3"}]


def test_case_17_other_takes_its_own_tone_and_never_the_first_merchants():
    names = ["Grab Food", "Din Tai Fung", "PS.Cafe", "Toast Box"]
    keys = [(f"Household|merchant:{i}", n, i) for i, n in enumerate(names, 1)]
    keys += [("Household|other", "Other (15 merchants)", None), ("Moom|merchant:2", "Din Tai Fung", 2)]
    by = {k: cell(k.split("|")[0], 1000, 0) for k, _, _ in keys}
    p = payload([month_bucket("2026-09", "Sep 2026", by)], [],
                [book_line("Household", 5000, 5000, 1), book_line("Moom", 1000, 1000, 1)], stack="merchant")
    p["stack_keys"] = keyed(keys)
    tones = {s["key"]: s["tone"] for s in bucket(model(p), "2026-09")["segments"]}
    assert tones["Household|merchant:1"] == "c1" and tones["Household|other"] == "other"
    assert tones["Moom|merchant:2"] == tones["Household|merchant:2"] == "c2"
    assert [k["tone"] for k in _node(f"console.log(JSON.stringify(window.Trend.bars.key({json.dumps(p)})))")]         == ["c1", "c2", "c3", "c4", "other"]


def test_stacked_by_book_the_tones_follow_the_books_and_there_is_no_key():
    p = fixture("trend_merchant")
    sep = bucket(model(p), "2026-09")
    assert [(s["key"], s["tone"]) for s in sep["segments"]] == [("Household", "c1"), ("Larkspur", "c2")]
    assert _node(f"console.log(JSON.stringify(window.Trend.bars.key({json.dumps(p)})))") == []


# ---------------------------------------------------------------------------
# Walk finding (390): on 12M bars the forced last tick sat beside the one before it
# and the two read "SepOct"; "Nov 25" and "Jan 26" touched too. Ticks are 11px
# monospace (0.6em a character), so their widths are known without a browser.
# ---------------------------------------------------------------------------

def drawn_ticks(payload, width):
    svg = _node(r"""
    const el = { clientWidth: """ + str(width) + r""", innerHTML: '', style: {},
      addEventListener() {}, querySelector() { return { hidden: true, innerHTML: '', style: {} }; } };
    window.Trend.modes.bars(el, """ + json.dumps(payload) + r""", {});
    console.log(JSON.stringify(el.innerHTML));""")
    return [(float(x), t) for x, t in re.findall(r'<text class="trend-bars__tick[^"]*" x="([^"]+)"[^>]*>([^<]*)</text>', svg)]


def test_bar_ticks_never_run_together_at_phone_width_and_the_last_bar_keeps_its_tick():
    p = fixture("trend_merchant")
    for width in (330, 350, 450, 640, 1200):
        ticks = drawn_ticks(p, width)
        texts = [t for _, t in ticks]
        assert texts[-1].startswith("Oct"), (width, texts)
        # July is missing for Household: its tick, in the warning colour, is never thinned out.
        assert any(t.startswith("Jul") and t.endswith("⧗") for t in texts), (width, texts)
        # The first tick carries its year, and so does the first one of 2026.
        assert re.search(r" 2[56]$", texts[0]), (width, texts)
        assert next(t for t in texts if not t.startswith(("Nov", "Dec"))).split(" ")[1] == "26", (width, texts)
        for (xa, a), (xb, b) in zip(ticks, ticks[1:]):
            assert xb - xa >= (len(a) + len(b)) * 6.6 / 2 + 2, (width, a, b, texts)
    # Wide enough, every month keeps its tick, the year on November and on January.
    assert [t for _, t in drawn_ticks(p, 1200)] == [
        "Nov 25", "Dec", "Jan 26", "Feb", "Mar", "Apr", "May", "Jun", "Jul ⧗", "Aug", "Sep", "Oct"]
