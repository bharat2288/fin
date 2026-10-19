"""Ticket 38: the trend line mode (static/trend-line.js), figures in and figures out.

The line mode draws two shapes: an account's month-end balance with its money in and
out (the R3 shape, tests/fixtures/trend/balances_*.json) and one book's type lines (the
R2 shape, trend_types.json). What it draws is decided by one public function,
``Trend.lineModel(payload, state)``, run here under node with the real app.js money and
esc lifted out of the file (as test_client_seams does). The SVG is seen in Chromium,
never asserted here. Every figure is invented.
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
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

PRE_2026 = "Balances start on 1 Jan 2026, as on the balance sheet"
NO_BALANCE = "No balance yet: import a statement or add an anchor"


def _lift(pattern):
    m = re.search(pattern, APP_JS, re.M | re.S)
    assert m, pattern
    return m.group(0)


PRELUDE = "\n".join([
    "globalThis.window = globalThis;",
    _lift(r"^const SIGNS = .*?;$"), _lift(r"^const DIGITS = .*?;$"), _lift(r"^const formatters = .*?;$"),
    _lift(r"^function digitsOf\(.*?\}$"), _lift(r"^function groupFor\(.*?^\}$"),
    _lift(r"^function money\(.*?^\}$"), _lift(r"^function esc\(.*?^\}$"),
    (STATIC / "trend.js").read_text(encoding="utf-8"),
    (STATIC / "trend-line.js").read_text(encoding="utf-8"),
])


def model(payload, state=None):
    code = PRELUDE + "\n" + (
        f"const out = window.Trend.lineModel({json.dumps(payload)}, {json.dumps(state or {})});"
        "console.log(JSON.stringify(out));")
    run = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def svg(payload, state=None, width=1366):
    code = PRELUDE + "\n" + (
        f"const m = window.Trend.lineModel({json.dumps(payload)}, {json.dumps(state or {})});"
        f"console.log(JSON.stringify(window.Trend.lineSvg(m, {width})));")
    run = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def month(payload, m):
    return next(x for x in payload["months"] if x["month"] == m)


def point(mdl, m):
    return next(p for p in mdl["points"] if p["month"] == m)


def line(mdl, name):
    return next(x for x in mdl["lines"] if x["name"] == name)


def lpoint(ln, m):
    return next(p for p in ln["points"] if p["month"] == m)


def months_between(first, last):
    y, n = map(int, first.split("-"))
    out = []
    while True:
        cur = f"{y}-{n:02d}"
        out.append(cur)
        if cur == last:
            return out
        n += 1
        if n == 13:
            y, n = y + 1, 1


def balance_month(m, balance=None, estimate=None, missing=False, so_far=False, note=None, inn=1100000, out=1050000):
    return {"month": m, "balance": balance, "estimate": estimate, "in": inn, "out": out,
            "missing": missing, "so_far": so_far, "rate": None, "note": note}


# --- the balance line ------------------------------------------------------------

def test_case_32_an_estimate_is_a_hollow_dot_on_dashed_bridges_and_never_a_figure():
    p = load("balances_sgd")
    m = model(p)
    july = point(m, "2026-07")
    assert july == {"month": "2026-07", "value": 5320000, "estimate": True}   # S$ 53,200.00
    assert point(m, "2026-06") == {"month": "2026-06", "value": 5230000, "estimate": False}
    assert point(m, "2026-08") == {"month": "2026-08", "value": 5410000, "estimate": False}
    assert m["dashed"] == [["2026-06", "2026-07", "2026-08"]]
    assert all("2026-07" not in run for run in m["solid"])
    bars = {b["month"]: b for b in m["bars"]}
    assert bars["2026-07"]["hatched"] is True and bars["2026-06"]["hatched"] is False
    assert (bars["2026-07"]["in"], bars["2026-07"]["out"]) == (1100000, 1050000)
    # The head card's figure is the payload's known month end, never the estimate.
    assert m["head"] == {"balance": 5500000, "date": "2026-09-30", "text": "S$ 55,000.00"}
    assert "53,200" not in json.dumps(m["head"])


def test_case_33_two_missing_months_lie_on_the_straight_line():
    p = load("balances_sgd")
    month(p, "2026-07").update(balance=None, estimate=5320000, missing=True)
    month(p, "2026-08").update(balance=None, estimate=5410000, missing=True)
    m = model(p)
    assert [point(m, x)["value"] for x in ("2026-06", "2026-07", "2026-08", "2026-09")] == [5230000, 5320000, 5410000, 5500000]
    assert [point(m, x)["estimate"] for x in ("2026-07", "2026-08")] == [True, True]
    assert m["dashed"] == [["2026-06", "2026-07", "2026-08", "2026-09"]]


def test_case_34_the_ends_are_left_open_even_if_an_estimate_is_offered():
    p = load("balances_sgd")
    # September due and not in, with no known month end after it.
    month(p, "2026-09").update(balance=None, estimate=5500000, missing=True)
    # The first month in the span missing too.
    month(p, "2025-11").update(balance=None, estimate=4900000, missing=True, note=None)
    m = model(p)
    assert point(m, "2026-09")["value"] is None
    assert point(m, "2025-11")["value"] is None
    assert all("2026-09" not in run and "2025-11" not in run for run in m["dashed"] + m["solid"])
    assert m["dashed"] == [["2026-06", "2026-07", "2026-08"]]
    assert point(m, "2026-10")["value"] is None                      # October: so far, no point
    assert {b["month"]: b for b in m["bars"]}["2026-10"]["so_far"] is True


def test_case_34_a_missing_month_with_no_estimate_is_a_gap_not_a_bridge():
    p = load("balances_sgd")
    month(p, "2026-07").update(estimate=None)
    m = model(p)
    assert point(m, "2026-07")["value"] is None
    assert m["dashed"] == []
    assert ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"] in m["solid"]


def test_case_35_no_anchor_yet_is_a_gap_with_a_note_never_zero():
    months = months_between("2026-01", "2026-10")
    rows = []
    for i, x in enumerate(months):
        if x in ("2026-01", "2026-02"):
            rows.append(balance_month(x))
        elif x == "2026-10":
            rows.append(balance_month(x, so_far=True))
        else:
            rows.append(balance_month(x, balance=5000000 + i * 10000))
    p = {"account": {"id": 40, "name": "Late savings", "currency": "SGD", "type": "bank", "part_of": None},
         "span": "YTD", "to": "2026-10", "cur": "SGD", "months": rows,
         "head": {"balance": 5080000, "date": "2026-09-30"}, "note": None}
    m = model(p)
    assert point(m, "2026-01")["value"] is None and point(m, "2026-02")["value"] is None
    assert m["empty"] is None
    assert m["gaps"][0] == {"months": ["2026-01", "2026-02"], "note": "No balance before the first anchor"}
    assert m["solid"][0][0] == "2026-03"
    assert m["scale"]["lo"] > 0                       # the axis is not dragged to S$ 0


def test_case_56_before_the_balance_sheet_starts():
    months = months_between("2024-11", "2026-10")
    rows = []
    for i, x in enumerate(months):
        if x < "2026-01":
            rows.append(balance_month(x, note=PRE_2026, estimate=4000000 if x == "2025-12" else None))
        elif x == "2026-10":
            rows.append(balance_month(x, so_far=True))
        else:
            rows.append(balance_month(x, balance=5000000 + i * 5000))
    p = {"account": {"id": 31, "name": "Everyday savings", "currency": "SGD", "type": "bank", "part_of": None},
         "span": "2Y", "to": "2026-10", "cur": "SGD", "months": rows,
         "head": {"balance": 5000000, "date": "2026-09-30"}, "note": None}
    m = model(p)
    assert len(m["bars"]) == 24 and all(b["in"] == 1100000 for b in m["bars"])
    assert m["solid"][0][0] == "2026-01"
    assert m["dashed"] == []                           # no estimate bridges into it
    assert m["gaps"][0] == {"months": months_between("2024-11", "2025-12"), "note": PRE_2026}
    assert all(point(m, x)["value"] is None for x in months_between("2024-11", "2025-12"))


def test_case_61_no_anchor_at_all_replaces_the_line_and_keeps_the_bars():
    p = load("balances_sgd")
    for x in p["months"]:
        x.update(balance=None, estimate=None, note=None)
    p["head"] = {"balance": None, "date": None}
    m = model(p)
    assert m["empty"] == NO_BALANCE
    assert all(pt["value"] is None for pt in m["points"])
    assert m["solid"] == [] and m["dashed"] == []
    assert len(m["bars"]) == 12 and {b["month"]: b for b in m["bars"]}["2026-09"]["in"] == 1155000
    assert m["head"] is None


def test_a_rupee_account_formats_in_its_own_currency_with_lakhs():
    m = model(load("balances_inr"))
    assert m["cur"] == "INR"
    assert m["head"]["text"] == "₹ 5,20,000.00"
    assert m["note"] == "S$ at each month's end-of-month rate"


# --- the type lines -----------------------------------------------------------------

def test_type_lines_take_their_colour_from_their_slot():
    m = model(load("trend_types"))
    assert [(x["name"], x["colour"]) for x in m["lines"][:5]] == [
        ("Dining", "s1"), ("Groceries", "s2"), ("Travel", "s3"), ("Education", "s4"), ("Insurance", "grey")]
    g = line(m, "Groceries")
    assert lpoint(g, "2026-07") == {"month": "2026-07", "value": 54000, "estimate": True}
    assert g["dashed"] == [["2026-06", "2026-07", "2026-08"]]
    # Dining's July is missing with no estimate: a gap, never S$ 0.
    assert lpoint(line(m, "Dining"), "2026-07")["value"] is None
    assert line(m, "Dining")["dashed"] == []


def test_a_chip_takes_a_line_off_the_scale_follows_and_the_fifth_is_not_promoted():
    p = load("trend_types")
    whole = model(p)
    off = model(p, {"off": [7, 9]})                    # Dining and Travel off
    assert line(off, "Dining")["off"] is True and line(off, "Travel")["off"] is True
    assert line(off, "Insurance")["colour"] == "grey"   # not coloured in Travel's place
    assert line(off, "Groceries")["colour"] == "s2"
    assert off["scale"]["hi"] < whole["scale"]["hi"]   # Dining's S$ 640.00 no longer sets the top
    assert off["any_off"] is True and whole["any_off"] is False


def test_case_41_picking_a_line_out_raises_it_and_dims_the_rest():
    m = model(load("trend_types"), {"picked": 8})
    assert line(m, "Groceries")["picked"] is True and line(m, "Groceries")["dim"] is False
    assert all(x["dim"] for x in m["lines"] if x["name"] != "Groceries")
    # The legend keeps its order (walk finding: the picked chip moved to the end of the
    # legend); the drawing alone puts the picked line last, so on top.
    p = load("trend_types")
    assert [x["name"] for x in m["lines"]] == [t["name"] for t in p["types"]]
    series = re.findall(r'<g class="(trend-line__series[^"]*)"', svg(p, {"picked": 8}))
    assert "trend-line__series--picked" in series[-1]
    chips = re.findall(r'class="trend-line__chip[^"]*" data-id="(\d+)"', svg(p, {"picked": 8}))
    assert [int(c) for c in chips] == [t["id"] for t in p["types"]]
    # A grey line picked out takes a colour so it can be read.
    ins = model(load("trend_types"), {"picked": 11})
    assert line(ins, "Insurance")["colour"] == "picked"


def test_case_44_a_type_below_zero_dips_below_the_axis():
    p = load("trend_types")
    g = next(t for t in p["types"] if t["name"] == "Groceries")
    next(x for x in g["points"] if x["month"] == "2026-08")["net"] = -46000   # refunds beat spend
    m = model(p)
    assert lpoint(line(m, "Groceries"), "2026-08")["value"] == -46000
    assert m["scale"]["lo"] <= -46000 < 0 < m["scale"]["hi"]
    assert m["scale"]["zero"] is True


def test_the_drawing_has_no_unknown_figures_at_both_widths():
    for name, state in (("balances_sgd", {}), ("balances_inr", {}), ("trend_types", {"picked": 8, "off": [9]})):
        for width in (1366, 390):
            out = svg(load(name), state, width)
            assert "NaN" not in out and "undefined" not in out


def test_case_29_the_rupee_hover_names_the_rate_day_and_drops_whole_paise():
    # Walk finding: the hover read "Rate 0.0155 (2026-09-30)" and "₹ 5,20,000.00" while the
    # card's summary and head read "₹ 5,20,000" and every other hover names "30 Sep 2026".
    p = load("balances_inr")
    k = [m["month"] for m in p["months"]].index("2026-09")
    code = PRELUDE + "\n" + (
        f"const m = window.Trend.lineModel({json.dumps(p)}, {{}});"
        f"console.log(JSON.stringify(window.Trend.lineTip(m, {k})));")
    run = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    tip = re.sub(r"<[^>]+>", " ", json.loads(run.stdout))
    tip = re.sub(r"\s+", " ", tip)
    assert "Balance at month end ₹ 5,20,000 " in tip
    assert "Money in ₹ 62,400 " in tip and "Money out ₹ 48,750 " in tip
    assert "Rate 0.0155 (30 Sep 2026)" in tip
    assert "2026-09-30" not in tip and ".00" not in tip
