"""R2, GET /api/trend/types: Types over time, one book's line per type
(fin-surfaces 40; spec 15, Server routes R2; cases 11, 39, 40, 42, 43, 50, 51,
55, 57, 63).

Figures in and out through the route (and, for case 51's read count, through
trend_types_api.types_over_time on the test connection). Seeded with
tests/trend_fixtures.py on a temporary database; every name and figure is
invented. Today is 19 Oct 2026, so 12M is Nov 2025 to Oct 2026.
"""

import datetime
import json
import math
import socket
import urllib.request
from pathlib import Path

import pytest

import rates
import trend_fixtures as tf
import trend_types_api
from test_household_spending_cards import cents

TODAY = datetime.date(2026, 10, 19)
FIXTURE = Path(__file__).parent / "fixtures" / "trend" / "trend_types.json"

# The 12M Household year (cases 39, 42, 55, 57). Months Nov 2025 .. Oct 2026;
# July 2026 has no Everyday card statement and no rows. Figures in cents.
YEAR = ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
        "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
FIGURES = {
    "Dining":    [80_800, 88_000, 80_800, 80_800, 80_800, 80_800,
                  56_000, 56_000, None, 56_000, 56_000, 56_000],
    "Groceries": [55_000, 55_000, 55_000, 55_000, 54_000, 54_000,
                  70_000, 52_000, None, 56_000, 72_000, 70_000],
    "Travel":    [80_000, 80_000, 80_000, 80_000, 80_000, 76_000,
                  20_000, 20_000, None, 20_000, 20_000, 20_000],
    "Education": [50_000] * 6 + [12_000, 12_000, None, 12_000, 12_000, 12_000],
    "Insurance": [None] * 6 + [30_000, 30_000, None, 30_000, 30_000, 30_000],
}
SMALL = ("Utilities", "Shopping", "Medical", "Home", "Kids", "Pet", "Entertainment")


@pytest.fixture(autouse=True)
def today_is_19_october(monkeypatch):
    monkeypatch.setattr(trend_types_api, "_today", lambda: TODAY)


@pytest.fixture
def refuse_outside(monkeypatch):
    """Case 50: the outside fails, the rates fetch first of all."""
    reached = []

    def refuse(*args, **kwargs):
        reached.append(args[0] if args else kwargs)
        raise OSError("no network in this test")

    monkeypatch.setattr(rates, "_http_get", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return reached


def seed_year(conn, copies: int = 1) -> dict:
    """Household's year on the Everyday card: a statement every month from Jan
    2025 to Sep 2026 except July 2026, October's rows on September's statement
    (no statement is due yet). A Gifts row in Jan 2025, before every span
    but 2Y and All. `copies` repeats every row, splitting its amount, for the
    ten-times check (case 51)."""
    card = tf.account(conn, "Everyday card")
    stmts = {}
    month = "2025-01"
    while month <= "2026-09":
        if month != "2026-07":
            stmts[month] = tf.statement(conn, card, month)
        y, m = int(month[:4]), int(month[5:7])
        month = f"{y + (m == 12):04d}-{m % 12 + 1:02d}"
    stmts["2026-10"] = stmts["2026-09"]

    def put(type_name, month, amount):
        share = amount // copies
        for i in range(copies):
            part = share + (amount - share * copies if i == 0 else 0)
            tf.row(conn, stmts[month], f"{type_name.upper()} {month}", part,
                   type_name=type_name, day=f"{month}-15")

    for type_name, figures in FIGURES.items():
        for month, amount in zip(YEAR, figures):
            if amount is not None:
                put(type_name, month, amount)
    for type_name in SMALL:
        put(type_name, "2026-09", 5_000)
    put("Gifts & Donations", "2025-01", 9_000)
    return {"card": card}


def get(client, query: str = "book=Household&span=12M&to=2026-10"):
    resp = client.get(f"/api/trend/types?{query}")
    return resp


def answer(client, query: str = "book=Household&span=12M&to=2026-10") -> dict:
    resp = get(client, query)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def by_name(payload: dict) -> dict:
    return {t["name"]: t for t in payload["types"]}


def points(line: dict) -> dict:
    return {p["month"]: p for p in line["points"]}


# --- the shape -------------------------------------------------------------


def test_the_answer_has_the_contract_fixtures_shape(client, conn):
    seed_year(conn)
    payload = answer(client)
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(payload) == set(fixture)
    line_keys, point_keys = set(fixture["types"][0]), set(fixture["types"][0]["points"][0])
    for line in payload["types"]:
        assert set(line) == line_keys
        assert [p["month"] for p in line["points"]] == payload["months"]
        assert type(line["net"]) is int
        for point in line["points"]:
            assert set(point) == point_keys
            for field in ("net", "estimate"):
                assert point[field] is None or type(point[field]) is int
    assert (payload["book"], payload["span"], payload["to"], payload["currency"]) == \
        ("Household", "12M", "2026-10", "SGD")
    assert payload["months"] == YEAR


# --- case 39: lines, one book ----------------------------------------------


def test_case_39_every_type_a_line_and_the_top_four_by_net_coloured(client, conn):
    seed_year(conn)
    payload = answer(client)
    lines = by_name(payload)
    assert {n: lines[n]["net"] for n in ("Dining", "Groceries", "Travel", "Education")} == {
        "Dining": 772_000, "Groceries": 648_000, "Travel": 576_000, "Education": 360_000}
    assert [lines[n]["id"] for n in ("Dining", "Groceries", "Travel", "Education")] == payload["top"]
    assert [lines[n]["slot"] for n in ("Dining", "Groceries", "Travel", "Education")] == [1, 2, 3, 4]
    assert [t["name"] for t in payload["types"]][:5] == [
        "Dining", "Groceries", "Travel", "Education", "Insurance"]
    assert all(t["slot"] is None for t in payload["types"] if t["id"] not in payload["top"])


def test_case_40_the_fifth_type_has_no_colour_to_be_promoted_into(client, conn):
    """Taking Travel's line off is the page's (ticket 47); the answer gives the
    fifth type no slot, so nothing on the page can promote it."""
    seed_year(conn)
    lines = by_name(answer(client))
    assert lines["Insurance"]["net"] == 150_000 and lines["Insurance"]["slot"] is None
    assert lines["Travel"]["slot"] == 3


# --- case 42: small multiples ----------------------------------------------


def test_case_42_twelve_types_with_rows_and_their_scales(client, conn):
    seed_year(conn)
    payload = answer(client)
    assert len(payload["types"]) == 12
    assert "Gifts & Donations" not in by_name(payload), "no rows in the span, no chart"
    assert all(type(t["id"]) is int for t in payload["types"]), "a tap opens the type page"

    def top(line):
        return max(p["net"] for p in line["points"] if p["net"] is not None)

    shared = max(top(t) for t in payload["types"])
    assert shared == 88_000
    assert points(by_name(payload)["Dining"])["2025-12"]["net"] == 88_000
    assert top(by_name(payload)["Insurance"]) == 30_000


# --- case 43: books never share a line -------------------------------------


def test_case_43_one_book_per_answer_and_no_line_mixes_books(client, conn):
    card = tf.account(conn, "Everyday card")
    company_card = tf.account(conn, "Moom card", owner="Moom")
    sep = tf.statement(conn, card, "2026-09")
    company_sep = tf.statement(conn, company_card, "2026-09")
    tf.row(conn, sep, "DINNER", 10_000, type_name="Dining", day="2026-09-10")
    tf.row(conn, sep, "CLIENT DINNER", 7_000, book="Moom", type_name="Dining", day="2026-09-11")
    tf.row(conn, company_sep, "TEAM LUNCH", 3_000, book="Moom", type_name="Dining", day="2026-09-12")

    household = answer(client)
    company = answer(client, "book=Moom&span=12M&to=2026-10")
    assert household["book"] == "Household" and company["book"] == "Moom"
    assert by_name(household)["Dining"]["net"] == 10_000
    assert by_name(company)["Dining"]["net"] == 10_000, "a company book counts every account"
    assert points(by_name(household)["Dining"])["2026-09"]["net"] == 10_000
    assert "Moom" not in json.dumps(household)


# --- case 55: a missing month ----------------------------------------------


def test_case_55_a_hollow_estimate_in_no_total(client, conn):
    seed_year(conn)
    payload = answer(client)
    assert payload["missing"] == {"Household": ["2026-07"]}
    groceries = by_name(payload)["Groceries"]
    july = points(groceries)["2026-07"]
    assert (july["net"], july["estimate"], july["missing"]) == (None, 54_000, True)
    assert groceries["net"] == 648_000
    assert sum(p["net"] for p in groceries["points"] if p["net"] is not None) == 648_000
    october = points(groceries)["2026-10"]
    assert october["so_far"] is True and october["missing"] is False
    assert all(p["estimate"] is None for t in payload["types"] for p in t["points"]
               if not p["missing"])


def test_case_55_missing_is_per_book_across_its_accounts_and_arrived_rows_count(client, conn):
    """The Everyday card's July is not in. Travel's rows are all on another
    card, whose July is in: Travel's July is still missing, because missing
    is counted against every account carrying the book's rows in the span.
    Rows that did arrive in July count in the totals; the drawn point is the
    estimate. A Moom card without August marks Moom's August, not
    Household's; an old card with rows only before the span marks nothing."""
    everyday = tf.account(conn, "Everyday card")
    second = tf.account(conn, "Second card")
    company = tf.account(conn, "Moom card", owner="Moom")
    old = tf.account(conn, "Old card")
    e = {m: tf.statement(conn, everyday, m) for m in ("2026-06", "2026-08", "2026-09")}
    s = {m: tf.statement(conn, second, m) for m in ("2026-06", "2026-07", "2026-08", "2026-09")}
    c = {m: tf.statement(conn, company, m) for m in ("2026-06", "2026-09")}
    tf.row(conn, tf.statement(conn, old, "2025-01"), "OLD SHOP", 4_000,
           type_name="Groceries", day="2025-01-10")
    tf.row(conn, e["2026-06"], "GROCER JUN", 52_000, type_name="Groceries", day="2026-06-10")
    tf.row(conn, e["2026-08"], "GROCER AUG", 56_000, type_name="Groceries", day="2026-08-10")
    tf.row(conn, s["2026-07"], "GROCER JUL", 3_000, type_name="Groceries", day="2026-07-10")
    for month, amount in (("2026-06", 10_000), ("2026-07", 12_000), ("2026-08", 14_000)):
        tf.row(conn, s[month], f"FLIGHT {month}", amount, type_name="Travel", day=f"{month}-12")
    tf.row(conn, c["2026-06"], "OFFICE LUNCH", 2_000, book="Moom", type_name="Dining", day="2026-06-05")

    payload = answer(client, "book=Household&span=6M&to=2026-10")
    assert payload["missing"] == {"Household": ["2026-07"]}
    lines = by_name(payload)
    groceries, travel = points(lines["Groceries"]), points(lines["Travel"])
    assert (groceries["2026-07"]["net"], groceries["2026-07"]["estimate"]) == (None, 54_000)
    assert lines["Groceries"]["net"] == 52_000 + 3_000 + 56_000
    assert (travel["2026-07"]["net"], travel["2026-07"]["estimate"], travel["2026-07"]["missing"]) \
        == (None, 12_000, True)
    assert lines["Travel"]["net"] == 36_000
    assert groceries["2026-05"]["missing"] is False, "before the first statement nothing is owed"
    assert groceries["2026-09"] == {"month": "2026-09", "net": 0, "estimate": None,
                                    "missing": False, "so_far": False}

    company_view = answer(client, "book=Moom&span=6M&to=2026-10")
    assert company_view["missing"] == {"Moom": ["2026-07", "2026-08"]}


def test_an_estimate_is_never_made_at_either_end(client, conn):
    """Case 34 for lines: the Everyday card's September is not in, so the last
    known month is August; September is open."""
    card = tf.account(conn, "Everyday card")
    for month, amount in (("2026-06", 50_000), ("2026-07", 60_000), ("2026-08", 70_000)):
        tf.row(conn, tf.statement(conn, card, month), f"GROCER {month}", amount,
               type_name="Groceries", day=f"{month}-10")
    sep = points(by_name(answer(client, "book=Household&span=6M&to=2026-10"))["Groceries"])["2026-09"]
    assert (sep["net"], sep["estimate"], sep["missing"]) == (None, None, True)


# --- case 57: colours follow the type --------------------------------------


def test_case_57_slots_stay_with_their_type_across_spans(client, conn):
    seed_year(conn)
    twelve = by_name(answer(client))
    six_payload = answer(client, "book=Household&span=6M&to=2026-10")
    six = by_name(six_payload)
    assert [t["name"] for t in six_payload["types"]][:4] == ["Groceries", "Dining", "Insurance", "Travel"]
    assert six_payload["top"] == [six[n]["id"] for n in ("Groceries", "Dining", "Insurance", "Travel")]
    for name in ("Dining", "Groceries", "Travel"):
        assert six[name]["slot"] == twelve[name]["slot"]
    assert six["Insurance"]["slot"] == twelve["Education"]["slot"] == 4
    assert six["Education"]["slot"] is None
    slots = [t["slot"] for t in six_payload["types"] if t["slot"] is not None]
    assert sorted(slots) == [1, 2, 3, 4]


def test_case_57_an_entrant_takes_the_lowest_free_slot(client, conn):
    """Two types leave the 12M top four on 6M (slots 2 and 4 free up); the
    entrant ranked higher on 6M takes slot 2, the next slot 4."""
    card = tf.account(conn, "Everyday card")
    stmts = {m: tf.statement(conn, card, m) for m in YEAR[:-1]}
    stmts["2026-10"] = stmts["2026-09"]
    early, late = YEAR[0], YEAR[-1]
    for name, amount, month in (("Dining", 90_000, early), ("Dining", 30_000, late),
                                ("Groceries", 80_000, early),
                                ("Travel", 50_000, early), ("Travel", 20_000, late),
                                ("Education", 60_000, early),
                                ("Insurance", 25_000, late), ("Utilities", 15_000, late)):
        tf.row(conn, stmts[month], f"{name} {month}", amount, type_name=name, day=f"{month}-03")
    twelve = by_name(answer(client))
    assert [twelve[n]["slot"] for n in ("Dining", "Groceries", "Travel", "Education")] == [1, 2, 3, 4]
    six = by_name(answer(client, "book=Household&span=6M&to=2026-10"))
    assert (six["Dining"]["slot"], six["Travel"]["slot"]) == (1, 3)
    assert (six["Insurance"]["slot"], six["Utilities"]["slot"]) == (2, 4)


# --- case 11: one figure, three places -------------------------------------


def test_case_11_household_september_ties_with_home_and_adds_the_rupees(client, conn):
    """Household, Sep 2026: S$ 3,900.00 net on S$ accounts, a hidden merchant
    and a refund inside it; Swiggy's ₹ 3,450 at 0.0155 makes Types over
    time's September S$ 3,953.48, and Home's tile the same (RUL-022)."""
    card = tf.account(conn, "Everyday card")
    rupee = tf.account(conn, "Rupee card", currency="INR")
    sep = tf.statement(conn, card, "2026-09")
    rupee_sep = tf.statement(conn, rupee, "2026-09")
    storage = tf.merchant(conn, "Tidy Storage", type_name="Home", hidden=True)
    tf.row(conn, sep, "SUPERMART", 300_000, type_name="Groceries", day="2026-09-03")
    tf.row(conn, sep, "SHOES", 60_000, type_name="Shopping", day="2026-09-04")
    tf.row(conn, sep, "SHOES REFUND", -25_000, type_name="Shopping", flow="refund", day="2026-09-20")
    tf.row(conn, sep, "TIDY STORAGE", 4_500, type_name="Home", service_id=storage, day="2026-09-05")
    tf.row(conn, sep, "NOODLE BAR", 50_500, type_name="Dining", day="2026-09-06")
    tf.row(conn, sep, "CLIENT LUNCH", 8_000, book="Moom", type_name="Dining", day="2026-09-07")
    tf.row(conn, rupee_sep, "SWIGGY", 345_000, type_name="Dining", day="2026-09-08")
    tf.seed_rate(conn, "INR/SGD", "2026-09-30", "0.0155")

    september = {t["name"]: points(t)["2026-09"]["net"] for t in answer(client)["types"]}
    assert september == {"Groceries": 300_000, "Shopping": 35_000, "Home": 4_500,
                         "Dining": 50_500 + 5_348}
    assert sum(september.values()) == 395_348
    home = cents(client.get("/api/dashboard/stat-cards?ref_month=2026-09").get_json()["household"])
    assert home == 395_348 == sum(september.values())


# --- case 50: opening a trend never fetches ---------------------------------


def test_case_50_a_no_rate_month_is_left_out_and_nothing_is_fetched(client, conn, refuse_outside):
    card = tf.account(conn, "Everyday card")
    rupee = tf.account(conn, "Rupee card", currency="INR")
    dollar = tf.account(conn, "Dollar card", currency="EUR")
    for month in ("2026-05", "2026-06", "2026-07", "2026-08", "2026-09"):
        tf.row(conn, tf.statement(conn, card, month), f"CAFE {month}", 10_000,
               type_name="Dining", day=f"{month}-09")
    tf.row(conn, tf.statement(conn, rupee, "2026-06"), "SWIGGY", 210_000,
           type_name="Dining", day="2026-06-14")
    tf.row(conn, tf.statement(conn, dollar, "2026-06"), "APP STORE", 4_000,
           type_name="Entertainment", day="2026-06-20")
    # May's rate is before June: no rate for June (ruling 4).
    tf.seed_rate(conn, "INR/SGD", "2026-05-31", "0.0150")
    saved = conn.execute("SELECT pair, date, rate FROM rates ORDER BY date").fetchall()

    for span in ("6M", "12M", "YTD", "2Y", "All"):
        payload = answer(client, f"book=Household&span={span}&to=2026-10")
        assert points(by_name(payload)["Dining"])["2026-06"]["net"] == 10_000
        assert payload["left_out"] == [
            {"kind": "no_rate_kept", "currency": "EUR", "amount_minor": 4_000,
             "month": "2026-06", "reason": "in EUR, a currency fin keeps no rate for"},
            {"kind": "no_rate", "currency": "INR", "amount_minor": 210_000,
             "month": "2026-06", "reason": "no saved rate for June 2026"},
        ]
    assert refuse_outside == []
    assert conn.execute("SELECT pair, date, rate FROM rates ORDER BY date").fetchall() == saved


# --- case 51: one read per chart --------------------------------------------


def _reads(conn, span: str) -> int:
    seen = []
    conn.set_trace_callback(seen.append)
    try:
        trend_types_api.types_over_time(conn, "Household", span, "2026-10", today=TODAY)
    finally:
        conn.set_trace_callback(None)
    return len(seen)


def test_case_51_one_read_whatever_the_span_and_the_volume(conn, tmp_path, monkeypatch):
    import db
    seed_year(conn)
    base_rows = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    once = {span: _reads(conn, span) for span in ("6M", "All")}
    assert once["6M"] == once["All"], "the series is one grouped query, not one per month"

    # Ten times the check book, about 1,300 rows, on a second temporary file.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ten_times.db")
    db.init_db()
    big = db.get_connection()
    try:
        seed_year(big, copies=math.ceil(1_300 / base_rows))
        assert big.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] >= 1_300
        assert _reads(big, "All") == once["All"]
        answer_ = trend_types_api.types_over_time(big, "Household", "12M", "2026-10", today=TODAY)
        assert {t["name"]: t["net"] for t in answer_["types"]}["Dining"] == 772_000
    finally:
        big.close()


# --- case 63: a bad query ---------------------------------------------------


@pytest.mark.parametrize("query, status, reason", [
    ("book=Household&span=9M", 400, "span must be one of 6M, 12M, YTD, 2Y, All"),
    ("book=Household&span=12M&to=2026-13", 400, "to must be a month like 2026-10"),
    ("book=Nobody&span=12M", 404, "book Nobody is not in fin"),
])
def test_case_63_a_bad_query_says_why(client, conn, query, status, reason):
    resp = get(client, query)
    assert resp.status_code == status
    assert resp.get_json() == {"error": reason}


def test_no_query_opens_household_on_12m_to_this_month(client, conn):
    seed_year(conn)
    payload = answer(client, "")
    assert (payload["book"], payload["span"], payload["to"]) == ("Household", "12M", "2026-10")
