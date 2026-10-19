"""Ticket 48: one figure across pages (fin-surfaces 15, cases 11, 52 and 51).

Case 11. Household's September net on S$ accounts is one figure on Home's tile,
on Books -> Spending's tile, and in the S$-account part of every type's
Household September on Types over time (R2) and on the trend (R1). The seed
puts a merchant hidden from expense views inside it (Tidy Storage, S$ 45.00)
and a rupee row beside it (Swiggy, Rs 3,450 at 0.0155 = S$ 53.48): the trend
pages read S$ 3,953.48 and name the rupee part; Home and Spending stay S$
accounts only.

Case 52. A company book counts every account: the trend, Spending and Types
over time read the company's S$ 1,480.00 net (S$ 1,160.00 on its own card,
S$ 320.00 paid from the household's), and Home's tile reads the S$ 320.00
the household paid, and says so.

Case 51. A chart is one read of the rows whatever the volume: All on Types
over time and on the trend, at 1x and 10x (about 1,300 rows).

The figures go in through tests/trend_fixtures.py and come out through the
real routes (Flask test client) and the real page code: Home's tiles and
Spending's tile are worked out in static/app.js, so those legs run it under
node with the harnesses of test_home_tiles_net and test_spending_net. The
spec's Larkspur is fin's company book Moom. Every name and figure is
invented; no real database is opened.
"""

from __future__ import annotations

import datetime

import pytest

import book_type
import db
import money
import trend_api
import trend_fixtures as tf
import trend_types_api
from test_home_tiles_net import company, household, words
from test_spending_net import needs_node, page, server, tile  # noqa: F401  (server is a fixture)

TODAY = datetime.date(2026, 10, 19)
SEP = "2026-09"
COMPANY = "Moom"  # the spec's Larkspur
HOUSEHOLD = book_type.DEFAULT_BOOK

# Household, Sep 2026, on S$ accounts the household owns, by type: (spent, refunded).
# Spent S$ 4,212.50, refunded S$ 312.50 -> net S$ 3,900.00.
HOUSEHOLD_SEP = {
    "Transport": (21240, 1800),
    "Groceries": (90450, 4450),
    "Shopping": (60000, 25000),
    "Repairs & maintenance": (4500, 0),   # Tidy Storage, hidden from expense views
    "Dining": (245060, 0),
}
RUPEES = 345000             # Swiggy, Dining, on the Rupee card (paise)
RUPEES_IN_SGD = 5348        # Rs 3,450 at 0.0155 = 53.475 -> S$ 53.48, half-even
# Moom, Sep 2026, by type: its own card's S$ 1,160.00 and the household's S$ 320.00.
COMPANY_SEP = {"Transport": 6100, "Software & AI tools": 109900, "Advertising": 32000}


@pytest.fixture(autouse=True)
def today(monkeypatch):
    """The trend routes read 19 Oct 2026; the page harnesses pin the same day."""
    monkeypatch.setattr(trend_api, "_today", lambda: TODAY)
    monkeypatch.setattr(trend_types_api, "_today", lambda: TODAY)


def seed(conn) -> None:
    everyday = tf.account(conn, "Everyday card")
    larkspur = tf.account(conn, "Larkspur card", owner=COMPANY)
    rupee = tf.account(conn, "Rupee card", currency="INR")
    ev, lk, rp = (tf.statement(conn, a, SEP) for a in (everyday, larkspur, rupee))

    rides = tf.merchant(conn, "Ride Co", HOUSEHOLD, "Transport")
    grocer = tf.merchant(conn, "Fresh Mart", HOUSEHOLD, "Groceries")
    gadget = tf.merchant(conn, "Gadget House", HOUSEHOLD, "Shopping")
    tidy = tf.merchant(conn, "Tidy Storage", HOUSEHOLD, "Repairs & maintenance", hidden=True)
    kitchen = tf.merchant(conn, "Lantern Kitchen", HOUSEHOLD, "Dining")
    swiggy = tf.merchant(conn, "Swiggy", HOUSEHOLD, "Dining")
    host = tf.merchant(conn, "Cloud Host", COMPANY, "Software & AI tools")
    ads = tf.merchant(conn, "Ad Network", COMPANY, "Advertising")

    def put(stmt, text, amount, svc, type_name, day, book=None):
        tf.row(conn, stmt, text, amount, service_id=svc, book=book, type_name=type_name,
               flow="refund" if amount < 0 else "expense", day=f"{SEP}-{day:02d}")

    put(ev, "RIDE CO", 21240, rides, "Transport", 3)
    put(ev, "RIDE CO REFUND", -1800, rides, "Transport", 22)
    put(ev, "FRESH MART", 90450, grocer, "Groceries", 10)
    put(ev, "FRESH MART REFUND", -4450, grocer, "Groceries", 24)
    put(ev, "GADGET HOUSE", 60000, gadget, "Shopping", 5)
    put(ev, "GADGET HOUSE RETURN", -25000, gadget, "Shopping", 20)
    put(ev, "TIDY STORAGE", 4500, tidy, "Repairs & maintenance", 7)
    put(ev, "LANTERN KITCHEN", 245060, kitchen, "Dining", 16)
    put(rp, "SWIGGY", RUPEES, swiggy, "Dining", 12)
    tf.seed_rate(conn, "INR/SGD", "2026-09-30", "0.0155")

    put(lk, "RIDE CO", 6100, rides, "Transport", 9, book=COMPANY)
    put(lk, "CLOUD HOST", 109900, host, "Software & AI tools", 1, book=COMPANY)
    put(ev, "AD NETWORK", 35000, ads, "Advertising", 11, book=COMPANY)
    put(ev, "AD NETWORK REFUND", -3000, ads, "Advertising", 18, book=COMPANY)


# --- reading each page's figure -------------------------------------------------


def stat_cards(client) -> dict:
    got = client.get(f"/api/dashboard/stat-cards?ref_month={SEP}&history=12")
    assert got.status_code == 200
    return got.get_json()


def types_over_time(client, book: str) -> dict[str, int]:
    """Types over time (R2): each type's September point for one book."""
    got = client.get(f"/api/trend/types?book={book}&span=12M&to=2026-10")
    assert got.status_code == 200, got.get_json()
    return {t["name"]: p["net"] for t in got.get_json()["types"]
            for p in t["points"] if p["month"] == SEP and p["net"] is not None}


def trend(client, conn, type_name: str, book: str) -> dict:
    """The trend (R1) of one type, stacked by book: the book's September segment
    and the bar's rate."""
    got = client.get(f"/api/trend?subject=type&id={tf.type_id(conn, type_name)}&span=12M&to=2026-10&stack=book")
    assert got.status_code == 200, got.get_json()
    bar = next(b for b in got.get_json()["buckets"] if b["months"] == [SEP])
    return {**bar["by"][book], "rate": bar["rate"]}


# --- case 11: one figure, three places -------------------------------------------


def test_case_11_the_trend_pages_read_the_s_dollar_figure_and_name_the_rupees(client, conn):
    seed(conn)
    home = money.to_minor(stat_cards(client)["household"])
    lines = types_over_time(client, HOUSEHOLD)
    bars = {name: trend(client, conn, name, HOUSEHOLD) for name in HOUSEHOLD_SEP}

    # Every type's Household September is one figure on the two trend pages.
    assert lines == {name: bar["net"] for name, bar in bars.items()}
    # Types over time reads S$ 3,953.48: the S$ accounts' S$ 3,900.00 and the rupees.
    assert sum(lines.values()) == 395_348
    # The trend names the rupee part: Rs 3,450 in Dining at 0.0155, the S$ 53.48 inside it.
    assert {name: bar["inr_minor"] for name, bar in bars.items() if bar["inr_minor"]} == {"Dining": RUPEES}
    assert bars["Dining"]["rate"]["text"] == "0.0155"
    assert lines["Dining"] - RUPEES_IN_SGD == HOUSEHOLD_SEP["Dining"][0]
    # Take the rupees off and every page states Home's figure, hidden merchant inside.
    assert sum(lines.values()) - RUPEES_IN_SGD == home == 390_000
    for name, (spent, back) in HOUSEHOLD_SEP.items():
        if name != "Dining":
            assert (bars[name]["spent"], bars[name]["back"], bars[name]["net"]) == (spent, back, spent - back)


@needs_node
def test_case_11_home_and_spending_draw_the_same_s_dollar_figure(client, conn, server):
    seed(conn)
    home_tile = words(household(stat_cards(client)))
    spending = tile(page(server)["html"])

    assert "S$ 3,900.00" in home_tile and "after S$ 312.50 refunds" in home_tile
    assert spending["figure"] == "S$ 3,900.00"
    # Spending stays on S$ accounts: the rupees are named beside the figure, not in it.
    assert spending["other"] and "₹ 3,450.00" in spending["other"]
    assert sum(types_over_time(client, HOUSEHOLD).values()) - RUPEES_IN_SGD == money.to_minor("3900.00")


# --- case 52: one figure for a company book -----------------------------------------


def test_case_52_the_trend_pages_count_every_account_of_a_company_book(client, conn):
    seed(conn)
    lines = types_over_time(client, COMPANY)
    bars = {name: trend(client, conn, name, COMPANY)["net"] for name in COMPANY_SEP}

    assert lines == bars == COMPANY_SEP
    assert sum(lines.values()) == 148_000
    # Home's tile is what the household paid, beside the company's own figure.
    assert money.to_minor(stat_cards(client)[COMPANY.lower()]) == 32_000


@needs_node
def test_case_52_spending_reads_the_company_figure_and_home_says_its_figure_is_the_household_part(
        client, conn, server):
    seed(conn)
    spending = tile(page(server, book=COMPANY)["html"])
    home_tile = words(company(stat_cards(client)))

    assert spending["figure"] == "S$ 1,480.00"
    assert money.to_minor(spending["figure"].removeprefix("S$ ").replace(",", "")) == \
        sum(types_over_time(client, COMPANY).values())
    assert "S$ 320.00" in home_tile and "paid from household accounts" in home_tile
    assert "S$ 1,480.00" not in home_tile


# --- case 51: one read per chart, at 1x and 10x ----------------------------------------


def seed_volume(conn, copies: int) -> None:
    """Rows from Jan 2024 to Oct 2026 on a household card, a company card and a
    rupee card: 4 rows a month, 136 at 1x, 1,360 at 10x."""
    everyday = tf.account(conn, "Everyday card")
    larkspur = tf.account(conn, "Larkspur card", owner=COMPANY)
    rupee = tf.account(conn, "Rupee card", currency="INR")
    rides = tf.merchant(conn, "Ride Co", HOUSEHOLD, "Transport")
    kitchen = tf.merchant(conn, "Lantern Kitchen", HOUSEHOLD, "Dining")
    swiggy = tf.merchant(conn, "Swiggy", HOUSEHOLD, "Dining")
    year, mon = 2024, 1
    while (year, mon) <= (2026, 10):
        month = f"{year:04d}-{mon:02d}"
        ev, lk, rp = (tf.statement(conn, a, month) for a in (everyday, larkspur, rupee))
        tf.seed_rate(conn, "INR/SGD", tf.month_end(month), "0.0155")
        for _ in range(copies):
            tf.row(conn, ev, "RIDE CO", 2000, service_id=rides, type_name="Transport", day=f"{month}-03")
            tf.row(conn, ev, "LANTERN KITCHEN", 3000, service_id=kitchen, type_name="Dining", day=f"{month}-09")
            tf.row(conn, lk, "RIDE CO", 1000, service_id=rides, book=COMPANY, type_name="Transport", day=f"{month}-12")
            tf.row(conn, rp, "SWIGGY", 100000, service_id=swiggy, type_name="Dining", day=f"{month}-15")
        year, mon = (year + 1, 1) if mon == 12 else (year, mon + 1)


def test_case_51_one_read_of_the_rows_per_chart_at_one_and_ten_times(client, conn, tmp_path, monkeypatch):
    seen: list[str] = []
    opened = db.get_connection

    def tracing():
        c = opened()
        c.set_trace_callback(seen.append)
        return c

    def read(book_path) -> dict:
        """Per chart: (reads of the rows, statements run, a figure)."""
        monkeypatch.setattr(db, "DB_PATH", book_path)
        charts = {
            "Types over time, All": "/api/trend/types?book=Household&span=All",
            "the trend of a type, All": f"/api/trend?subject=type&id={dining}&span=All",
            "the trend of a merchant, All": f"/api/trend?subject=merchant&id={rides}&span=All",
        }
        out = {}
        for name, url in charts.items():
            seen.clear()
            got = client.get(url)
            assert got.status_code == 200, got.get_json()
            body = got.get_json()
            figure = (body["types"][0]["net"] if "types" in body
                      else next(b for b in body["books"] if b["book"] == HOUSEHOLD)["net"])
            out[name] = (sum("FROM transactions" in s for s in seen), len(seen), figure)
        return out

    seed_volume(conn, 1)
    dining, rides = tf.type_id(conn, "Dining"), conn.execute(
        "SELECT id FROM services WHERE name = 'Ride Co'").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 136
    monkeypatch.setattr(db, "get_connection", tracing)
    once = read(db.DB_PATH)

    monkeypatch.setattr(db, "get_connection", opened)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ten_times.db")
    db.init_db()
    big = db.get_connection()
    try:
        seed_volume(big, 10)
        assert big.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1_360
        assert tf.type_id(big, "Dining") == dining
        assert big.execute("SELECT id FROM services WHERE name = 'Ride Co'").fetchone()[0] == rides
    finally:
        big.close()
    monkeypatch.setattr(db, "get_connection", tracing)
    ten = read(db.DB_PATH)

    for chart, (reads, statements, figure) in once.items():
        big_reads, big_statements, big_figure = ten[chart]
        assert reads == big_reads, chart
        assert reads <= 2, (chart, reads)
        assert statements == big_statements, f"{chart}: the statements run grew with the rows"
        assert big_figure == 10 * figure, f"{chart}: the 10x book was not the one read"
