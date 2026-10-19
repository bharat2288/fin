"""Ticket 33: Home's book tiles read net (fin-surfaces 15, cases 10, 11 (Home's leg), 12, 52).

The figures go in through the real stat-cards route on a seeded book, and out through
the real Home tile function in static/app.js, lifted out of the file and run under node
(as test_client_seams does). The test reads the tiles' words and figures, never their markup
beyond the few classes the stylesheet keys on.
"""

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import book_type
from tests.trend_fixtures import account, merchant, row, seed_rate, statement

APP_JS = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

REF = "2026-09"
COMPANY = "Moom"  # stands in for the spec's Larkspur: a declared company book
OLD_WORDS = ("not taken off", "never taken off", "not in the share")

# The Household book's nets, October 2025 to September 2026, as (spent, refunded) in cents.
# March is below zero (a refund and no spend). Sep: 4,212.50 spent, 312.50 back -> 3,900.00.
HOUSEHOLD_MONTHS = {
    "2025-10": (280000, 0), "2025-11": (290000, 0), "2025-12": (330000, 0), "2026-01": (300000, 0),
    "2026-02": (310000, 0), "2026-03": (0, 5000), "2026-04": (340000, 0), "2026-05": (360000, 0),
    "2026-06": (370000, 0), "2026-07": (410000, 0), "2026-08": (360000, 10000), "2026-09": (421250, 31250),
}


def _seed(conn):
    """The check book in miniature: a household card, a company's own card, a rupee card."""
    everyday = account(conn, "Everyday card")
    larkspur_card = account(conn, "Larkspur card", owner=COMPANY)
    rupee_card = account(conn, "Rupee card", currency="INR")
    tidy = merchant(conn, "Tidy Storage", hidden=True)  # hidden from expense views, inside the figure
    sep = None
    for month, (spent, back) in HOUSEHOLD_MONTHS.items():
        st = statement(conn, everyday, month)
        sep = st if month == REF else sep
        if month == REF:
            # 4,212.50 = 3,800.00 + 367.50 + the hidden merchant's 45.00
            row(conn, st, "SAMPLE SHOP", 380000, day=f"{month}-05")
            row(conn, st, "SAMPLE GROCER", 36750, day=f"{month}-09")
            row(conn, st, "TIDY STORAGE", 4500, service_id=tidy, day=f"{month}-12")
        elif spent:
            row(conn, st, "SAMPLE SHOP", spent, day=f"{month}-10")
        if back:
            row(conn, st, "SAMPLE REFUND", -back, flow="refund", day=f"{month}-20")
    st = sep  # the company's costs, paid from the household card
    row(conn, st, "SAMPLE ADS", 35000, book=COMPANY, day=f"{REF}-06")
    row(conn, st, "SAMPLE ADS REFUND", -3000, book=COMPANY, flow="refund", day=f"{REF}-18")
    # 1,160.00 on the company's own card: a company book counts it, Home's tile does not.
    row(conn, statement(conn, larkspur_card, REF), "SAMPLE HOSTING", 116000, book=COMPANY, day=f"{REF}-08")
    # A rupee row: Home stays S$ accounts only (case 11), even with a rate saved.
    swiggy = merchant(conn, "Swiggy", type_name="Dining")
    row(conn, statement(conn, rupee_card, REF), "SWIGGY", 345000, service_id=swiggy, day=f"{REF}-15")
    seed_rate(conn, "INR/SGD", f"{REF}-30", "0.0155")


@pytest.fixture
def cards(client, conn):
    _seed(conn)
    got = client.get(f"/api/dashboard/stat-cards?ref_month={REF}&history=12")
    assert got.status_code == 200
    return got.get_json()


# --- lifting the real tile function out of static/app.js --------------------------------------


def _function(name):
    m = re.search(rf"^function {name}\(.*\}}$", APP_JS, re.M) or re.search(rf"^function {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _const(name):
    m = re.search(rf"^const {name} = .*?;$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def tiles(cards):
    """Home's book tiles, as the page would draw them from this payload."""
    code = "\n".join([
        "const icon = n => `<i ${n}></i>`;",
        *[_const(c) for c in ("SIGNS", "DIGITS", "formatters", "MONTHS", "LONG_MONTHS")],
        *[_function(f) for f in ("esc", "digitsOf", "groupFor", "money", "toMinor", "monthName", "plural",
                                 "homeBars", "homeVsAverage", "homeTile", "homeBookTiles")],
        "const { cards, books } = JSON.parse(require('fs').readFileSync(0, 'utf8'));",
        "console.log(JSON.stringify(homeBookTiles(cards, books)));",
    ])
    books = [{"name": name} for name in book_type.BOOK_NAMES]
    out = subprocess.run([NODE, "-e", code], input=json.dumps({"cards": cards, "books": books}),
                         capture_output=True, text=True, timeout=30, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def words(tile_html):
    """What a reader sees: the tags gone, the entities read."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", tile_html)).split())


def household(cards):
    found = [t for t in tiles(cards) if "is-household" in t]
    assert len(found) == 1
    return found[0]


def company(cards):
    found = [t for t in tiles(cards) if f"{COMPANY}, its costs" in t]
    assert len(found) == 1
    return found[0]


def bars(tile_html):
    """The tile's small bars, oldest first: (class list, height percent, title)."""
    inner = re.search(r'<div class="home-bars[^"]*"[^>]*>(.*?)</div>', tile_html, re.S).group(1)
    return [(m.group(1).split(), int(m.group(2)), m.group(3)) for m in
            re.finditer(r'<span class="([^"]*)" style="--h:(\d+)%" title="([^"]*)"></span>', inner)]


# --- case 10: Home's book tile ----------------------------------------------------------------


@needs_node
def test_the_household_tile_reads_net_of_refunds(cards):
    text = words(household(cards))

    assert "S$ 3,900.00" in text           # was S$ 4,212.50, money out
    assert "S$ 4,212.50" not in text
    assert "after S$ 312.50 refunds" in text


@needs_node
def test_the_bars_are_net_months_and_a_month_below_zero_hangs_under_the_axis(cards):
    drawn = bars(household(cards))

    assert len(drawn) == 12
    assert [t for _, _, t in drawn][0] == "Oct 2025" and [t for _, _, t in drawn][-1] == "Sep 2026"
    # Net, not money out: August is 3,600.00 less a 100.00 refund = 3,500.00, tallest July's 4,100.00.
    heights = {t: h for _, h, t in drawn}
    assert heights["Aug 2026"] == round(3500 / 4100 * 100)
    assert heights["Sep 2026"] == round(3900 / 4100 * 100)
    # March's net is -50.00: marked below the axis, and only March.
    below = [t for c, _, t in drawn if "is-neg" in c]
    assert len(below) == 1 and below[0].startswith("Mar 2026") and "below zero" in below[0]
    assert "is-current" in drawn[-1][0]


@needs_node
def test_a_book_with_no_month_below_zero_draws_no_mark(cards):
    cards = json.loads(json.dumps(cards))
    for h in cards["history"]:
        h["household"] = max(h["household"], 0)

    assert not [t for c, _, t in bars(household(cards)) if "is-neg" in c]


@needs_node
def test_the_average_is_the_net_average_of_the_three_prior_months(cards):
    # Jun 3,700.00, Jul 4,100.00, Aug 3,500.00 (3,600.00 less a 100.00 refund) -> 3,766.67
    text = words(household(cards))

    assert "S$ 3,766.67" in text
    assert "above its recent average" in text and "4%" in text   # 3,900.00 against 3,766.67


# --- case 11 (Home's leg) -----------------------------------------------------------------------


@needs_node
def test_home_stays_on_s_dollar_accounts_with_a_hidden_merchant_inside_and_a_rupee_row_outside(cards):
    # The seed holds Tidy Storage (hidden, S$ 45.00) and Swiggy (₹ 3,450, rate saved): the figure is
    # still the S$-account net, S$ 3,900.00, so Home, Spending and Types over time can be tied by it.
    assert cards["household"] == 3900.00
    assert "S$ 3,900.00" in words(household(cards))


# --- case 52: a company book ----------------------------------------------------------------


@needs_node
def test_the_company_tile_reads_what_the_household_paid_and_says_so(cards):
    text = words(company(cards))

    assert "S$ 320.00" in text             # 350.00 out less 30.00 back, on the household card
    assert "S$ 1,160.00" not in text and "S$ 1,480.00" not in text   # its own card is not Home's figure
    assert "paid from household accounts" in text
    assert "after S$ 30.00 refunds" in text


# --- case 12: the old words leave -----------------------------------------------------------


@needs_node
def test_the_old_words_are_not_in_either_tile(cards):
    for tile in (household(cards), company(cards)):
        shown = words(tile).lower()
        assert not [w for w in OLD_WORDS if w in shown]


def test_the_old_words_are_not_in_the_home_tile_code():
    code = "\n".join(_function(f) for f in ("homeTile", "homeBookTiles", "homeBars", "homeVsAverage"))
    assert not [w for w in OLD_WORDS if w in code.lower()]


# --- unchanged behaviour ------------------------------------------------------------------


@needs_node
def test_a_book_with_no_statement_for_the_month_is_still_marked_not_zero(cards):
    cards = json.loads(json.dumps(cards))
    cards["household_missing"] = True
    text = words(household(cards))

    assert "no statement for Sep 2026" in text
    assert 'href="#/books/import"' in household(cards)
