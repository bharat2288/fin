"""One path from a row's amount to S$ (RUL-022: worked out when read).

A rupee row counts in every S$ spending figure at the rate of its own day:
the latest saved on or before it, and not before the first of its month. A
rupee row with no such rate is left out of the sum, never guessed, and the
dashboard says how many were. Rates are fetched for the days rupee rows are
on, in one request per currency, only when asked.

Every name, number and figure is invented.
"""

import io
import json

import pytest

import parsers
import rates
from parse_dbs import ParsedStatement, ParsedTransaction
from test_import_that_ties import confirm

SGD_CARD = "Sample Card 0001"
RUPEE_BANK = "Sample Bank Savings 0009"

# ₹10,000.00 on 10 Aug, ₹5,000.00 on 14 Aug, ₹2,000.00 on 3 Sep.
RUPEE_ROWS = [
    ("2026-08-10", "POS SAMPLE STORE MUMBAI", 1000000),
    ("2026-08-14", "POS SAMPLE CAFE MUMBAI", 500000),
    ("2026-09-03", "POS SAMPLE BOOKS MUMBAI", 200000),
]


def bring_in(client, account, rows, currency):
    def parse(_path):
        return [ParsedStatement(
            statement_type="bank" if currency != "SGD" else "credit_card", statement_date=rows[-1][0],
            accounts=[account], filename="sample.csv", currency=currency,
            transactions=[ParsedTransaction(date=d, description=w, amount_minor=a, card_info=account)
                          for d, w, a in rows],
        )]
    parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    parsers._PARSERS.insert(0, {"name": "Stand-in", "ext": ".csv", "detect_fn": lambda _p: True, "parse_fn": parse})
    try:
        resp = client.post("/api/import/upload", data={"files": (io.BytesIO(b"x"), "sample.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200, resp.get_json()
        assert confirm(client, resp.get_json()).status_code == 200
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]


def set_rate(client, on, rate):
    assert client.put("/api/rates", json={"currency": "INR", "date": on, "rate": rate}).status_code == 200


@pytest.fixture
def book(client, conn):
    """S$100.00 of Household spending on a card on 5 Aug, and the rupee rows
    as Household spending on a rupee bank account."""
    bring_in(client, SGD_CARD, [("2026-08-05", "SAMPLE GROCER SINGAPORE", 10000)], "SGD")
    bring_in(client, RUPEE_BANK, RUPEE_ROWS, "INR")
    conn.execute("UPDATE transactions SET flow_type = 'expense', book = 'Household'")
    conn.commit()
    # S$0.0150 a rupee from 7 Aug, S$0.0160 from 12 Aug.
    set_rate(client, "2026-08-07", "0.0150")
    set_rate(client, "2026-08-12", "0.0160")


def monthly(client) -> dict:
    return client.get("/api/dashboard/monthly?start=2026-08-01&end=2026-09-30").get_json()


def test_the_monthly_figure_counts_rupee_rows_at_the_rate_of_their_own_day(client, book):
    by_month = monthly(client)

    # S$100.00 + ₹10,000 at 0.0150 (S$150.00) + ₹5,000 at 0.0160 (S$80.00).
    assert sum(by_month["2026-08"].values()) == pytest.approx(330.00)


def test_a_rupee_row_with_no_rate_in_its_month_is_left_out_and_counted_as_such(client, book):
    by_month = monthly(client)
    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-09").get_json()

    # September has no rate of its own: August's is no rate for it.
    assert "2026-09" not in by_month
    assert cards["unvalued_count"] == 1
    assert cards["household"] == 0


def test_the_type_totals_and_the_household_card_count_the_same(client, book):
    types = client.get("/api/dashboard/types?start=2026-08-01&end=2026-08-31").get_json()
    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-08").get_json()

    assert sum(t["total"] for t in types) == pytest.approx(330.00)
    assert cards["household"] == pytest.approx(330.00)
    assert cards["unvalued_count"] == 0


def test_a_corrected_rate_corrects_the_month_that_uses_it(client, book):
    set_rate(client, "2026-08-12", "0.0170")

    assert sum(monthly(client)["2026-08"].values()) == pytest.approx(335.00)


def test_fetching_saves_a_rate_for_each_day_a_rupee_row_has_none(client, conn, book, monkeypatch):
    asked = []

    def served(url):
        asked.append(url)
        # Business days only: 3 Sep is a Thursday, its own rate.
        return json.dumps({"amount": 1.0, "base": "INR", "rates": {
            "2026-08-27": {"SGD": 0.0158}, "2026-09-03": {"SGD": 0.0157},
        }})
    monkeypatch.setattr(rates, "_http_get", served)

    resp = client.post("/api/rates/fetch-missing")

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["saved"] == 1
    assert len(asked) == 1 and "2026-08-27..2026-09-03" in asked[0] and "base=INR" in asked[0]
    held = rates.saved(conn, "INR/SGD", "2026-09-03")
    assert held["rate"] == "0.0157"
    # ₹2,000 at 0.0157: S$31.40.
    assert sum(monthly(client)["2026-09"].values()) == pytest.approx(31.40)


def test_a_weekend_day_takes_the_business_day_before_it_and_says_so(client, conn, monkeypatch):
    bring_in(client, RUPEE_BANK, [("2026-08-08", "POS SAMPLE STORE MUMBAI", 100000)], "INR")
    monkeypatch.setattr(rates, "_http_get", lambda url: json.dumps(
        {"rates": {"2026-08-06": {"SGD": 0.0151}, "2026-08-07": {"SGD": 0.0152}}}))

    client.post("/api/rates/fetch-missing")

    held = rates.saved(conn, "INR/SGD", "2026-08-08")  # a Saturday
    assert held["rate"] == "0.0152" and "2026-08-07" in held["source"]


def test_a_failed_fetch_saves_nothing_and_says_so(client, conn, book, monkeypatch):
    def down(_url):
        raise OSError("unreachable")
    monkeypatch.setattr(rates, "_http_get", down)

    resp = client.post("/api/rates/fetch-missing")

    assert resp.status_code == 502
    assert rates.saved(conn, "INR/SGD", "2026-09-03") is None


def test_one_currency_failing_keeps_the_rates_of_the_others_and_says_which(client, conn, book, monkeypatch):
    bring_in(client, "Sample Dollar Savings 0007", [("2026-09-04", "SAMPLE STORE NEW YORK", 5000)], "USD")
    conn.execute("UPDATE transactions SET flow_type = 'expense', book = 'Household'")
    conn.commit()

    def served(url):
        if "base=USD" in url:
            raise OSError("unreachable")
        return json.dumps({"rates": {"2026-09-03": {"SGD": 0.0157}}})
    monkeypatch.setattr(rates, "_http_get", served)

    resp = client.post("/api/rates/fetch-missing")

    # The rupee rate is saved and counts; the dollar one is named as failed.
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["saved"] == 1 and resp.get_json()["failed"] == ["USD"]
    assert rates.saved(conn, "INR/SGD", "2026-09-03")["rate"] == "0.0157"
    assert sum(monthly(client)["2026-09"].values()) == pytest.approx(31.40)
    assert client.get("/api/dashboard/stat-cards?ref_month=2026-09").get_json()["unvalued_count"] == 1
    # The change list names what was done in words.
    assert client.get("/api/history").get_json()["entries"][0]["summary"] == "Fetched the missing exchange rates"


def test_a_row_dated_oddly_is_not_asked_for(client, conn, book, monkeypatch):
    conn.execute("UPDATE transactions SET date = '03/09/2026' WHERE amount_minor = 200000")
    conn.commit()
    monkeypatch.setattr(rates, "_http_get", lambda url: pytest.fail(f"asked {url}"))

    resp = client.post("/api/rates/fetch-missing")

    assert resp.status_code == 200 and resp.get_json()["saved"] == 0


def test_rows_far_apart_are_asked_for_in_spans_and_a_rate_too_old_is_not_used(client, conn, monkeypatch):
    # ₹ rows on 2 Jan and 10 Aug: two spans, two requests. The source answers
    # 10 Aug's span with 20 Jul only, three weeks old: no rate for 10 Aug.
    bring_in(client, RUPEE_BANK, [("2026-01-02", "POS SAMPLE STORE MUMBAI", 100000),
                                  ("2026-08-10", "POS SAMPLE CAFE MUMBAI", 100000)], "INR")
    asked = []

    def served(url):
        asked.append(url)
        if "2026-08-10" in url:
            return json.dumps({"rates": {"2026-07-20": {"SGD": 0.0150}}})
        return json.dumps({"rates": {"2026-01-02": {"SGD": 0.0149}}})
    monkeypatch.setattr(rates, "_http_get", served)

    resp = client.post("/api/rates/fetch-missing")

    assert len(asked) == 2
    assert resp.get_json()["saved"] == 1 and resp.get_json()["currencies"] == ["INR"]
    assert rates.saved(conn, "INR/SGD", "2026-01-02")["rate"] == "0.0149"
    assert rates.saved(conn, "INR/SGD", "2026-08-10") is None
