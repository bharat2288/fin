"""R1, GET /api/trend, through the Flask test client (fin-surfaces 39; spec 15,
Server routes R1, Testing Decisions By case R1).

Figures in through the shared seeding helpers (tests/trend_fixtures.py), and
figures out through the route. Every name, figure and date is invented; the
books are the mockup's stand-ins (Household, Larkspur). Today is pinned to
19 Oct 2026, so 12M is Nov 2025 to Oct 2026 and October is "so far".
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

import db
import rates
import trend_api
import trend_fixtures as tf

TODAY = datetime.date(2026, 10, 19)
HH, LK = "Household", "Larkspur"
FIXTURES = Path(__file__).parent / "fixtures" / "trend"


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(trend_api, "_today", lambda: TODAY)


@pytest.fixture(autouse=True)
def nothing_reaches_outside(monkeypatch):
    """Opening a trend never fetches (case 50): any reach outside fails."""
    def refuse(_url):
        pytest.fail("a trend read tried to reach the network")
    monkeypatch.setattr(rates, "_http_get", refuse)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def months(start: str, end: str) -> list[str]:
    out, (y, m) = [], (int(start[:4]), int(start[5:7]))
    while f"{y:04d}-{m:02d}" <= end:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def statements(conn, account: int, start: str, end: str, skip=()) -> dict[str, int]:
    return {m: tf.statement(conn, account, m) for m in months(start, end) if m not in skip}


def get(client, **params):
    response = client.get("/api/trend", query_string=params)
    return response.status_code, response.get_json()


def ok(client, **params) -> dict:
    status, body = get(client, **params)
    assert status == 200, body
    return body


def bucket(payload: dict, key: str) -> dict:
    return next(b for b in payload["buckets"] if b["key"] == key)


def books(payload: dict) -> dict:
    return {b["book"]: b for b in payload["books"]}


def merchant(conn, name: str, type_name: str | None = None) -> int:
    """A merchant by name: fin's own seeding already holds some (Amazon, Din
    Tai Fung); one of those is reused, with no rows of its own yet."""
    found = conn.execute("SELECT id FROM services WHERE name = ?", (name,)).fetchone()
    return found[0] if found else tf.merchant(conn, name, None, type_name)


# --- Seeds ------------------------------------------------------------------

# Grab, Household, 12M: month -> (expense rows, refund). Nets as the shared
# merchant fixture's Household segments; July is missing (case 31).
GRAB_HH_12M = {
    "2025-11": ([7000, 7000, 7000], 0),
    "2025-12": ([11500, 11500, 11400], 2400),
    "2026-01": ([11000, 11000], 0),
    "2026-02": ([11000, 11000], 1500),
    "2026-03": ([12250, 12250], 1500),
    "2026-04": ([10750, 10750], 0),
    "2026-05": ([12000, 12000], 0),
    "2026-06": ([11880, 11880], 1200),
    "2026-08": ([10400, 10400], 1800),
    "2026-09": ([10620, 10620], 1800),
    "2026-10": ([13000, 13000, 13000], 0),
}
# Larkspur's 9 Grab rows in 12M: S$ 488.40, the last on 3 Oct 2026.
GRAB_LK_12M = {"2025-11": 3700, "2025-12": 14800, "2026-01": 3700, "2026-03": 3700,
               "2026-05": 3700, "2026-07": 4200, "2026-08": 3700, "2026-09": 6100,
               "2026-10": 5240}


def seed_grab(conn, *, august: tuple | None = None) -> int:
    """Grab: Household on the Everyday card from Jan 2024 (July 2026's
    statement not imported), Larkspur on the Larkspur card from Nov 2025.
    Household nets S$ 2,088.00 Jan-Oct 2024, S$ 2,400.00 Nov 2024-Oct 2025
    and S$ 2,640.00 in 12M: S$ 7,128.00 over All."""
    everyday = tf.account(conn, "Everyday card")
    larkspur = tf.account(conn, "Larkspur card", owner=LK)
    ev = statements(conn, everyday, "2024-01", "2026-10", skip={"2026-07"})
    lk = statements(conn, larkspur, "2025-11", "2026-10")
    grab = merchant(conn, "Grab", "Transport")

    def hh(month, amount, day):
        tf.row(conn, ev[month], "GRAB RIDE", amount, service_id=grab,
               type_name="Transport", day=f"{month}-{day}")

    for m in months("2024-01", "2024-10"):
        hh(m, 20880, "15")
    for m in months("2024-11", "2025-10"):
        hh(m, 20000, "15")
    plan = dict(GRAB_HH_12M)
    if august is not None:
        plan["2026-08"] = august
    for m, (spent, back) in plan.items():
        for amount, day in zip(spent, ("02", "07", "12")):
            hh(m, amount, day)
        if back:
            hh(m, -back, "20")
    for m, amount in GRAB_LK_12M.items():
        tf.row(conn, lk[m], "GRAB RIDE", amount, service_id=grab, book=LK,
               type_name="Transport", day=f"{m}-03")
    return grab


def seed_dining(conn) -> dict:
    """Dining, Household 12M nets S$ 7,720.00: Grab Food, Din Tai Fung,
    PS.Cafe, Toast Box, Swiggy (rupees) and 14 small cafes. The Everyday
    card's June statement is not in; no rate is saved in June (the 31 May
    rate is before the month, so it is no rate for June). Larkspur has one
    Din Tai Fung dinner in September."""
    everyday = tf.account(conn, "Everyday card")
    rupee = tf.account(conn, "Rupee card", currency="INR")
    larkspur = tf.account(conn, "Larkspur card", owner=LK)
    ev = statements(conn, everyday, "2025-11", "2026-10", skip={"2026-06"})
    inr = statements(conn, rupee, "2026-05", "2026-10")
    lk = statements(conn, larkspur, "2025-11", "2026-10")
    sgd_months = [m for m in months("2025-11", "2026-10") if m != "2026-06"]
    dining = tf.type_id(conn, "Dining")
    ids = {}

    def spend(name, amounts):
        ids[name] = merchant(conn, name, "Dining")
        for i, amount in enumerate(amounts):
            m = sgd_months[i % len(sgd_months)]
            tf.row(conn, ev[m], name.upper(), amount, service_id=ids[name],
                   type_name="Dining", day=f"{m}-{10 + i // len(sgd_months):02d}")

    spend("Grab Food", [31000] * 8)
    spend("Din Tai Fung", [13000] * 13)
    spend("PS.Cafe", [12000] * 9)
    spend("Toast Box", [12000] * 7)
    for n in range(1, 15):
        spend(f"Cafe {n:02d}", [8000 if n < 14 else 7000])

    ids["Swiggy"] = merchant(conn, "Swiggy", "Dining")
    for m, paise in (("2026-05", 2_904_800), ("2026-06", 210_000),
                     ("2026-08", 200_000), ("2026-09", 345_000)):
        tf.row(conn, inr[m], "SWIGGY", paise, service_id=ids["Swiggy"],
               type_name="Dining", day=f"{m}-12")
    tf.seed_rate(conn, "INR/SGD", "2026-05-31", "0.0150")
    tf.seed_rate(conn, "INR/SGD", "2026-08-28", "0.0154")
    tf.seed_rate(conn, "INR/SGD", "2026-09-30", "0.0155")

    tf.row(conn, lk["2026-09"], "DIN TAI FUNG", 20000, service_id=ids["Din Tai Fung"],
           book=LK, type_name="Dining", day="2026-09-18")
    return {"dining": dining, **ids}


def small_merchant(conn, name, rows, *, type_name="Dining", account=None) -> int:
    """A merchant with `rows` [(day, amount_minor, book?)] on one S$ card,
    each on a statement of its own month."""
    card = account or tf.account(conn, f"{name} card")
    svc = merchant(conn, name, type_name)
    held: dict[str, int] = {}
    for spec in rows:
        day, amount = spec[0], spec[1]
        book = spec[2] if len(spec) > 2 else None
        type_of = spec[3] if len(spec) > 3 else type_name
        if day[:7] not in held:
            held[day[:7]] = tf.statement(conn, card, day[:7])
        tf.row(conn, held[day[:7]], name.upper(), amount, service_id=svc, book=book,
               type_name=type_of, day=day)
    return svc


# --- Shape ------------------------------------------------------------------

def assert_same_shape(answer: dict, pinned: dict):
    """The route answers in 24's fixture's keys, at every level."""
    assert set(answer) == set(pinned)
    assert set(answer["subject"]) == set(pinned["subject"])
    bucket_keys = set(pinned["buckets"][0])
    by_keys = set(next(iter(pinned["buckets"][0]["by"].values())))
    for b in answer["buckets"]:
        assert set(b) == bucket_keys
        for part in b["by"].values():
            assert set(part) == by_keys
            assert all(type(part[f]) is int for f in ("spent", "back", "net"))
            assert part["net"] == part["spent"] - part["back"]
    for book in answer["books"]:
        assert set(book) == set(pinned["books"][0])
    for key in answer["stack_keys"]:
        assert set(key) == set(pinned["stack_keys"][0])


def test_the_merchant_answer_has_the_fixture_shape(client, conn):
    grab = seed_grab(conn)
    answer = ok(client, subject="merchant", id=grab, span="12M")
    assert_same_shape(answer, fixture("trend_merchant"))
    assert answer["subject"] == {"kind": "merchant", "id": grab, "name": "Grab"}
    assert (answer["span"], answer["to"], answer["stack"], answer["currency"]) == \
        ("12M", "2026-10", "book", "SGD")


def test_the_type_answer_has_the_fixture_shape_with_its_rate_and_left_out(client, conn):
    ids = seed_dining(conn)
    answer = ok(client, subject="type", id=ids["dining"], span="12M")
    pinned = fixture("trend_type")
    assert_same_shape(answer, pinned)
    rated = [b for b in answer["buckets"] if b["rate"] is not None]
    assert rated and all(set(b["rate"]) == set(bucket(pinned, "2026-09")["rate"]) for b in rated)
    assert answer["left_out"] and all(set(i) == set(pinned["left_out"][0]) for i in answer["left_out"])
    assert set(answer["merchants"]) == set(pinned["merchants"])
    assert all(set(r) == set(pinned["merchants"]["rows"][0]) for r in answer["merchants"]["rows"])
    assert set(answer["merchants"]["others"]) == set(pinned["merchants"]["others"])


# --- Case 1, 15, 21, 22: each book its own, never added ---------------------

def test_cases_15_21_22_each_book_its_own_net_average_and_comparison(client, conn):
    grab = seed_grab(conn)
    answer = ok(client, subject="merchant", id=grab, span="12M")
    # The shared fixture's books, figure for figure.
    assert answer["books"] == fixture("trend_merchant")["books"]
    hh = books(answer)[HH]
    assert (hh["net"], hh["avg"], hh["months_counted"], hh["vs_prev_pct"]) == (264_000, 24_000, 11, 10)
    assert (hh["spent"], hh["back"], hh["rows"], hh["last_date"]) == (274_200, 10_200, 31, "2026-10-12")
    lk = books(answer)[LK]
    assert (lk["net"], lk["avg"], lk["months_counted"], lk["rows"]) == (48_840, 4_070, 12, 9)


def test_case_1_a_bar_carries_each_book_and_no_sum_of_them_appears(client, conn):
    grab = seed_grab(conn)
    answer = ok(client, subject="merchant", id=grab, span="12M")
    sep = bucket(answer, "2026-09")
    assert sep["by"][HH] == {"book": HH, "spent": 21_240, "back": 1_800, "net": 19_440, "inr_minor": None}
    assert sep["by"][LK]["net"] == 6_100
    text = json.dumps(answer)
    assert "25540" not in text and "313040" not in text  # Sep both books; 12M both books
    assert '"total"' not in text.lower() and "grand_total" not in text.lower()


def test_the_household_bars_match_the_shared_fixture_month_for_month(client, conn):
    grab = seed_grab(conn)
    answer = ok(client, subject="merchant", id=grab, span="12M")
    pinned = fixture("trend_merchant")
    for mine, theirs in zip(answer["buckets"], pinned["buckets"]):
        assert (mine["key"], mine["label"], mine["months"], mine["so_far"]) == \
            (theirs["key"], theirs["label"], theirs["months"], theirs["so_far"])
        assert mine["by"].get(HH) == theirs["by"].get(HH)
        assert mine["missing"] == theirs["missing"]


def test_case_13_taking_a_book_off_leaves_the_other_books_bars_and_average(client, conn):
    """The chips are the page's; what the route owes is each book on its own:
    every segment names its book, so Larkspur's going changes nothing of
    Household's, and Household carries the average the line is drawn at."""
    grab = seed_grab(conn)
    answer = ok(client, subject="merchant", id=grab, span="12M")
    for b in answer["buckets"]:
        assert all(part["book"] == key for key, part in b["by"].items())
    household = [b["by"][HH]["net"] for b in answer["buckets"] if HH in b["by"]]
    assert sum(household) == books(answer)[HH]["net"]
    assert books(answer)[HH]["avg"] == 24_000


# --- Case 20, 23, 53: All in quarters ---------------------------------------

def test_case_20_all_is_quarters_with_the_one_under_way(client, conn):
    grab = seed_grab(conn)
    answer = ok(client, subject="merchant", id=grab, span="All")
    keys = [b["key"] for b in answer["buckets"]]
    assert len(keys) == 12 and keys[0] == "2024-Q1" and keys[-1] == "2026-Q4"
    last = answer["buckets"][-1]
    assert last["months"] == ["2026-10"] and last["so_far"] is True
    hh = books(answer)[HH]
    assert (hh["net"], hh["months_counted"], hh["avg"]) == (712_800, 33, 21_600)


def test_case_23_no_previous_span_on_all_or_before_the_first_row(client, conn):
    grab = seed_grab(conn)
    assert all(b["vs_prev_pct"] is None for b in ok(client, subject="merchant", id=grab, span="All")["books"])
    # 2Y's previous span (Nov 2022 - Oct 2024) reaches before Grab's first row (Jan 2024).
    assert all(b["vs_prev_pct"] is None for b in ok(client, subject="merchant", id=grab, span="2Y")["books"])


def test_case_53_a_quarter_holding_a_missing_month(client, conn):
    grab = seed_grab(conn, august=([12500, 12500], 0))
    answer = ok(client, subject="merchant", id=grab, span="All")
    q3 = bucket(answer, "2026-Q3")
    assert q3["months"] == ["2026-07", "2026-08", "2026-09"]
    assert q3["by"][HH]["net"] == 44_440
    assert q3["missing"] == {HH: ["2026-07"]}
    assert books(answer)[HH]["months_counted"] == 33


# --- Case 2, 3, 4: the line instead of a chart ------------------------------

def test_case_2_two_rows_one_month(client, conn):
    kopi = small_merchant(conn, "Kopi Lane", [("2026-03-21", 5200), ("2026-03-04", 3200)])
    answer = ok(client, subject="merchant", id=kopi, span="12M")
    assert answer["few"] == {"rows": 2, "first_month": "2026-03", "last_month": "2026-03",
                             "net": 8_400, "books": [{"book": HH, "net": 8_400}]}


def test_case_3_three_rows_one_month(client, conn):
    hana = small_merchant(conn, "Hana Florist",
                          [("2026-05-02", 4000), ("2026-05-09", 6500), ("2026-05-20", 2500)])
    few = ok(client, subject="merchant", id=hana, span="12M")["few"]
    assert (few["rows"], few["first_month"], few["last_month"], few["net"]) == (3, "2026-05", "2026-05", 13_000)


def test_case_4_two_rows_two_months_counted_over_all_time_not_the_span(client, conn):
    sato = small_merchant(conn, "Sato Dental", [("2026-02-09", 12000), ("2026-04-14", 12000)])
    for span in ("12M", "6M"):  # 6M (May - Oct) holds none of them
        few = ok(client, subject="merchant", id=sato, span=span)["few"]
        assert (few["rows"], few["first_month"], few["last_month"], few["net"]) == \
            (2, "2026-02", "2026-04", 24_000)


def test_three_rows_across_two_months_draw_a_chart(client, conn):
    svc = small_merchant(conn, "Corner Deli",
                         [("2026-08-02", 1000), ("2026-08-09", 1000), ("2026-09-01", 1000)])
    assert ok(client, subject="merchant", id=svc, span="12M")["few"] is None


def test_few_never_adds_books_together(client, conn):
    svc = small_merchant(conn, "Print Hut", [("2026-03-02", 1000), ("2026-03-09", 2500, LK)])
    few = ok(client, subject="merchant", id=svc, span="12M")["few"]
    assert few["net"] is None
    assert few["books"] == [{"book": HH, "net": 1_000}, {"book": LK, "net": 2_500}]


# --- Case 17, 18: stack by --------------------------------------------------

def test_case_18_grab_two_books_one_type_has_no_control_and_stacks_by_book(client, conn):
    grab = seed_grab(conn)
    answer = ok(client, subject="merchant", id=grab, span="12M")
    assert answer["stack_options"] == [] and answer["stack"] == "book"
    assert [k["key"] for k in answer["stack_keys"]] == [HH, LK]


def test_case_18_kaya_bakery_one_book_three_types_stacks_by_type(client, conn):
    kaya = small_merchant(conn, "Kaya Bakery", [
        ("2026-08-02", 1500, None, "Dining"), ("2026-08-09", 2500, None, "Groceries"),
        ("2026-09-01", 3000, None, "Shopping")])
    answer = ok(client, subject="merchant", id=kaya, span="12M")
    assert answer["stack_options"] == [] and answer["stack"] == "type"
    assert sorted(k["label"] for k in answer["stack_keys"]) == ["Dining", "Groceries", "Shopping"]
    assert all(k["id"] is not None for k in answer["stack_keys"])


def test_case_18_hana_one_book_one_type_stacks_by_book(client, conn):
    hana = small_merchant(conn, "Hana Florist", [
        ("2026-05-02", 4000), ("2026-06-09", 6500), ("2026-07-20", 2500)])
    answer = ok(client, subject="merchant", id=hana, span="12M")
    assert answer["stack_options"] == [] and answer["stack"] == "book"


def test_case_18_amazon_offers_book_or_type_and_opens_on_book(client, conn):
    amazon = small_merchant(conn, "Amazon", [
        ("2026-07-02", 62000, None, "Home"), ("2026-08-09", 9500, None, "Groceries"),
        ("2026-08-12", 3000, None, "Home"), ("2026-08-20", -59000, None, "Home"),
        ("2026-09-01", 4500, None, "Shopping"), ("2026-09-03", 8000, LK, "Shopping")])
    answer = ok(client, subject="merchant", id=amazon, span="12M")
    assert answer["stack_options"] == ["book", "type"] and answer["stack"] == "book"

    by_type = ok(client, subject="merchant", id=amazon, span="12M", stack="type")
    assert by_type["stack"] == "type" and by_type["stack_options"] == ["book", "type"]
    labels = {k["key"]: k["label"] for k in by_type["stack_keys"]}
    aug = bucket(by_type, "2026-08")
    named = {(part["book"], labels[key]): part["net"] for key, part in aug["by"].items()}
    assert named == {(HH, "Groceries"): 9_500, (HH, "Home"): -56_000}
    # The same net whichever way the bars are stacked (spec, quantity contract).
    for b_book, b_type in zip(answer["buckets"], by_type["buckets"]):
        for book in (HH, LK):
            assert sum(p["net"] for p in b_type["by"].values() if p["book"] == book) == \
                (b_book["by"][book]["net"] if book in b_book["by"] else 0)
    assert by_type["books"] == answer["books"]


def test_case_17_dining_by_merchant_is_the_top_four_and_other(client, conn):
    ids = seed_dining(conn)
    plain = ok(client, subject="type", id=ids["dining"], span="12M")
    assert plain["stack_options"] == ["book", "merchant"] and plain["stack"] == "book"

    answer = ok(client, subject="type", id=ids["dining"], span="12M", stack="merchant")
    household = [k for k in answer["stack_keys"]
                 if any(b["by"].get(k["key"], {}).get("book") == HH for b in answer["buckets"])]
    assert [k["label"] for k in household] == [
        "Grab Food", "Din Tai Fung", "PS.Cafe", "Toast Box", "Other (15 merchants)"]
    assert [k["id"] for k in household] == [
        ids["Grab Food"], ids["Din Tai Fung"], ids["PS.Cafe"], ids["Toast Box"], None]
    other = household[-1]["key"]
    assert sum(b["by"][other]["net"] for b in answer["buckets"] if other in b["by"]) == \
        52_000 + 111_000  # Swiggy and the 14 cafes
    assert books(answer)[HH]["net"] == 772_000


# --- Case 26, 27, 28, 62, 64: one currency ----------------------------------

def test_cases_26_27_rupees_in_sgd_at_the_month_end_rate(client, conn):
    ids = seed_dining(conn)
    answer = ok(client, subject="merchant", id=ids["Swiggy"], span="12M")
    sep, aug, may = bucket(answer, "2026-09"), bucket(answer, "2026-08"), bucket(answer, "2026-05")
    assert sep["by"][HH] == {"book": HH, "spent": 5_348, "back": 0, "net": 5_348, "inr_minor": 345_000}
    assert sep["rate"] == {"pair": "INR/SGD", "date": "2026-09-30", "exact": True, "text": "0.0155"}
    assert aug["by"][HH]["net"] == 3_080 and aug["by"][HH]["inr_minor"] == 200_000
    assert aug["rate"] == {"pair": "INR/SGD", "date": "2026-08-28", "exact": False, "text": "0.0154"}
    assert may["by"][HH]["net"] == 43_572
    assert books(answer)[HH]["net"] == 52_000
    assert bucket(answer, "2026-04")["rate"] is None


def test_case_28_a_month_with_no_saved_rate_is_marked_and_nothing_is_fetched(client, conn):
    ids = seed_dining(conn)
    saved = conn.execute("SELECT * FROM rates ORDER BY pair, date").fetchall()
    answer = ok(client, subject="type", id=ids["dining"], span="12M", to="2026-10", stack="book")
    june = bucket(answer, "2026-06")
    assert june["no_rate"] is True and june["rate"] is None
    assert june["by"][HH]["inr_minor"] == 210_000
    assert {"kind": "no_rate", "currency": "INR", "amount_minor": 210_000, "month": "2026-06",
            "reason": "no saved rate for June 2026"} in answer["left_out"]
    assert [b["key"] for b in answer["buckets"] if b["no_rate"]] == ["2026-06"]
    # The 31 May rate is before June: no rate, never guessed; and nothing saved.
    assert [tuple(r) for r in conn.execute("SELECT * FROM rates ORDER BY pair, date")] == \
        [tuple(r) for r in saved]


def test_case_64_missing_and_no_rate_in_one_month(client, conn):
    ids = seed_dining(conn)
    june = bucket(ok(client, subject="type", id=ids["dining"], span="12M"), "2026-06")
    assert june["missing"] == {HH: ["2026-06"]} and june["no_rate"] is True


def test_case_62_a_currency_fin_keeps_no_rate_for_is_left_out_of_every_figure(client, conn):
    everyday = tf.account(conn, "Everyday card")
    usd = tf.account(conn, "Travel card", currency="USD")
    svc = merchant(conn, "Duty Free", "Shopping")
    for m in ("2026-08", "2026-09", "2026-10"):
        tf.row(conn, tf.statement(conn, everyday, m), "DUTY FREE", 1000, service_id=svc,
               type_name="Shopping", day=f"{m}-05")
    tf.row(conn, tf.statement(conn, usd, "2026-09"), "DUTY FREE", 4000, service_id=svc,
           type_name="Shopping", day="2026-09-07")
    answer = ok(client, subject="merchant", id=svc, span="12M")
    assert answer["left_out"] == [{"kind": "no_rate_kept", "currency": "USD", "amount_minor": 4000,
                                   "month": "2026-09",
                                   "reason": "in USD, a currency fin keeps no rate for"}]
    assert bucket(answer, "2026-09")["by"][HH]["net"] == 1000
    assert bucket(answer, "2026-09")["no_rate"] is False
    assert books(answer)[HH]["net"] == 3000


# --- Case 36: the merchants table --------------------------------------------

DINING_TABLE = [("Grab Food", 248_000, 32, None), ("Din Tai Fung", 169_000, 22, None),
                ("PS.Cafe", 108_000, 14, None), ("Toast Box", 84_000, 11, None),
                ("Swiggy", 52_000, 7, "INR")]


def test_case_36_dining_names_every_merchant_at_five_percent_and_the_rest_as_others(client, conn):
    ids = seed_dining(conn)
    table = ok(client, subject="type", id=ids["dining"], span="12M")["merchants"]
    assert (table["book"], table["net"]) == (HH, 772_000)
    assert [(r["name"], r["net"], r["share_pct"], r["paid_in"]) for r in table["rows"]] == DINING_TABLE
    assert [r["id"] for r in table["rows"]] == [ids[n] for n, *_ in DINING_TABLE]
    assert table["others"] == {"count": 14, "net": 111_000, "share_pct": 14}


def test_case_36_the_table_follows_the_book_picked(client, conn):
    ids = seed_dining(conn)
    answer = ok(client, subject="type", id=ids["dining"], span="12M", book=LK)
    table = answer["merchants"]
    assert (table["book"], table["net"]) == (LK, 20_000)
    assert [(r["name"], r["share_pct"]) for r in table["rows"]] == [("Din Tai Fung", 100)]
    assert table["others"] is None
    assert [b["book"] for b in answer["books"]] == [HH, LK]  # the picker's choices


def test_case_36_five_percent_exactly_is_named_and_just_under_is_an_other(client, conn):
    card = tf.account(conn, "Everyday card")
    held = statements(conn, card, "2026-08", "2026-09")
    for name, amount, month in (("Big Mart", 9_010, "2026-08"), ("Corner Mart", 500, "2026-09"),
                                ("Tiny Mart", 490, "2026-09")):
        svc = merchant(conn, name, "Groceries")
        tf.row(conn, held[month], name.upper(), amount, service_id=svc,
               type_name="Groceries", day=f"{month}-04")
    table = ok(client, subject="type", id=tf.type_id(conn, "Groceries"), span="12M")["merchants"]
    assert [(r["name"], r["share_pct"]) for r in table["rows"]] == [("Big Mart", 90), ("Corner Mart", 5)]
    assert table["others"] == {"count": 1, "net": 490, "share_pct": 5}  # 4.9%, shown rounded


def _three_marts(conn):
    card = tf.account(conn, "Everyday card")
    held = statements(conn, card, "2026-08", "2026-09")
    for name, amount, month in (("Big Mart", 9_010, "2026-08"), ("Corner Mart", 500, "2026-09"),
                                ("Tiny Mart", 490, "2026-09")):
        svc = merchant(conn, name, "Groceries")
        tf.row(conn, held[month], name.upper(), amount, service_id=svc,
               type_name="Groceries", day=f"{month}-04")
    return tf.type_id(conn, "Groceries")


def test_case_25_a_bar_narrows_the_merchants_table_to_its_months(client, conn):
    groceries = _three_marts(conn)
    table = ok(client, subject="type", id=groceries, span="12M", months="2026-09")["merchants"]
    assert table["net"] == 990
    assert [(r["name"], r["net"], r["share_pct"]) for r in table["rows"]] == [("Corner Mart", 500, 51), ("Tiny Mart", 490, 49)]
    assert table["others"] is None


def test_case_25_a_quarter_narrows_the_table_to_all_its_months(client, conn):
    groceries = _three_marts(conn)
    table = ok(client, subject="type", id=groceries, span="12M", months="2026-08,2026-09")["merchants"]
    assert table["net"] == 10_000


def test_months_outside_the_span_are_refused(client, conn):
    groceries = _three_marts(conn)
    for bad in ("2027-01", "2026-13", "Sep"):
        status, body = get(client, subject="type", id=groceries, span="12M", months=bad)
        assert status == 400 and "months" in body["error"], (bad, body)


def test_a_merchant_answer_carries_no_merchants_table(client, conn):
    grab = seed_grab(conn)
    assert "merchants" not in ok(client, subject="merchant", id=grab, span="12M")


# --- Case 59, 60: no rows in the span ----------------------------------------

def test_case_59_no_rows_in_the_span(client, conn):
    pale = small_merchant(conn, "Pale Ale Co", [
        ("2026-01-09", 8000), ("2026-02-11", 7000), ("2026-03-04", 8000), ("2026-03-22", 8000)])
    six = ok(client, subject="merchant", id=pale, span="6M")
    assert six["empty"] == {"last_date": "2026-03-22"} and six["books"] == []
    assert [b["key"] for b in six["buckets"]] == months("2026-05", "2026-10")
    twelve = ok(client, subject="merchant", id=pale, span="12M")
    assert twelve["empty"] is None and books(twelve)[HH]["net"] == 31_000


def test_case_60_a_type_with_no_rows_in_the_span_leaves_out_the_table(client, conn):
    small_merchant(conn, "Flower Fund", [
        ("2025-12-05", 5000), ("2026-01-08", 5000), ("2026-02-14", 5000)],
        type_name="Gifts & Donations")
    gifts = tf.type_id(conn, "Gifts & Donations")
    answer = ok(client, subject="type", id=gifts, span="6M")
    assert answer["empty"] == {"last_date": "2026-02-14"}
    assert answer["merchants"] is None
    assert answer["subject"]["name"] == "Gifts & Donations"


# --- Case 49, 63: failure shapes ----------------------------------------------

def test_case_49_a_merchant_or_type_deleted_elsewhere_answers_404(client, conn):
    gone = merchant(conn, "Closed Shop", "Dining")
    conn.execute("DELETE FROM services WHERE id = ?", (gone,))
    conn.commit()
    status, body = get(client, subject="merchant", id=gone, span="12M")
    assert status == 404 and body["error"] == "This merchant is no longer in fin"
    status, body = get(client, subject="type", id=99_999, span="12M")
    assert status == 404 and body["error"] == "This type is no longer in fin"


@pytest.mark.parametrize("params, status, reason", [
    ({"span": "9M"}, 400, "span must be one of 6M, 12M, YTD, 2Y, All"),
    ({"to": "2026-13"}, 400, "to must be a month like 2026-10"),
    ({"subject": "account"}, 400, "subject must be one of merchant, type"),
    ({"id": "abc"}, 400, "id must be a whole number"),
    ({"id": None}, 400, "id must be a whole number"),
    ({"stack": "merchant"}, 400, "stack must be one of book, type"),
    ({"book": "Tessel"}, 400, "book must be one of Household, Larkspur"),
    ({"id": 99_999}, 404, "This merchant is no longer in fin"),
])
def test_case_63_a_bad_query_answers_with_its_reason(client, conn, params, status, reason):
    grab = seed_grab(conn)
    query = {"subject": "merchant", "id": grab, "span": "12M", **params}
    query = {k: v for k, v in query.items() if v is not None}
    got, body = get(client, **query)
    assert (got, body) == (status, {"error": reason})


def test_the_default_span_is_12m_ending_this_month(client, conn):
    grab = seed_grab(conn)
    answer = ok(client, subject="merchant", id=grab)
    assert (answer["span"], answer["to"]) == ("12M", "2026-10") and len(answer["buckets"]) == 12


# --- Case 51: one read per chart ----------------------------------------------

def counted_statements(monkeypatch) -> list[str]:
    """Every SQL statement the route's own connection runs."""
    seen: list[str] = []
    opened = db.get_connection

    def tracing():
        c = opened()
        c.set_trace_callback(seen.append)
        return c

    monkeypatch.setattr(db, "get_connection", tracing)
    return seen


def test_case_51_one_grouped_read_whatever_the_span_and_at_ten_times(client, conn, monkeypatch):
    card = tf.account(conn, "Everyday card")
    stmts = statements(conn, card, "2024-01", "2026-10")
    small = merchant(conn, "Small Bites", "Dining")
    bulk = merchant(conn, "Bulk Bites", "Dining")
    for m, stmt in stmts.items():
        tf.row(conn, stmt, "SMALL", 1000, service_id=small, type_name="Dining", day=f"{m}-05")
        tf.row(conn, stmt, "SMALL", 500, service_id=small, book=LK, type_name="Dining", day=f"{m}-06")
        for i in range(39):  # about 1,300 rows over 34 months
            tf.row(conn, stmt, "BULK", 1000 + i, service_id=bulk, book=(LK if i % 3 else None),
                   type_name="Dining", day=f"{m}-{1 + i % 28:02d}")
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE service_id = ?", (bulk,)).fetchone()[0] >= 1300

    seen = counted_statements(monkeypatch)
    counts = {}
    for name, svc in (("small", small), ("bulk", bulk)):
        for span in ("6M", "All"):
            seen.clear()
            ok(client, subject="merchant", id=svc, span=span)
            counts[(name, span)] = len(seen)
    assert len(set(counts.values())) == 1, counts
    reads = [s for s in seen if "FROM transactions" in s]
    assert len(reads) <= 2, reads
