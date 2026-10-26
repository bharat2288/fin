"""The bank's own rate on a row, and rates fetched on import (fin RUL-022;
the chat brief of 26 Oct 2026, item 5).

A row in another currency whose wording prints the bank's rate, "SGD@
0.7400000" on a DBS US$ row or "SGD500.0@74.52" on an RFX remittance into
rupees (units of the row's currency for one S$), is valued at that rate, not
the day's ECB rate. A printed rate is used only beside a saved ECB rate for
its day that it is within WORDING_BAND of: a rate read the wrong way up, or
off a balance line, is no rate for the row, which keeps the ECB value. A
row a confirmed pair holds keeps the pair's rate. Nothing is stored: the
value is worked out when read.

An import that brings rows in another currency fetches the ECB rate for each
of their days that has none, as its own change after the import's, so
undoing the import never takes away a rate other rows use. A fetch that
fails leaves the import as it is and says so.

Every name, number and figure is invented.
"""

import io
import json

import pytest

import parsers
import rates
import valuation
from parse_dbs import ParsedStatement, ParsedTransaction
from test_cross_currency_pairs import make_account, make_row
from test_import_that_ties import confirm

USD_ECB = "1.3500"      # S$ for one US$ on 8 Jan 2025
INR_ECB = "0.0135"      # S$ for one rupee on 14 Aug 2026 (74.07 rupees a dollar)


def save_rate(conn, pair, day, rate):
    conn.execute("INSERT INTO rates (pair, date, rate, source, fetched_at) VALUES (?, ?, ?, 'test ECB', "
                 "'2026-10-26 00:00:00')", (pair, day, rate))
    conn.commit()


def listed(client, row_id):
    rows = client.get(f"/api/transactions?tx_id={row_id}&every=true").get_json()["transactions"]
    return next(r for r in rows if r["id"] == row_id)


@pytest.fixture
def usd(conn):
    return make_account(conn, "Sample DBS 100-000001-1 USD", "USD")


@pytest.fixture
def inr(conn):
    return make_account(conn, "Sample HDFC 0009", "INR")


# --- reading the rate ---------------------------------------------------------------


@pytest.mark.parametrize("wording, per_sgd", [
    ("Advice Funds Transfer 200-000002-2 : I-BANK SGD@ 0.7400000", "0.7400000"),
    ("Advice Funds Transfer I-BANK SGD@0.7382253", "0.7382253"),
    ("INWARD REMIT RFX SGD500.0@74.52 SAMPLE", "74.52"),
    ("INWARD REMIT RFXSGD35,000.0@73.20", "73.20"),
    ("INWARD REMIT RFX SGD500.0 @ 74.52", "74.52"),
])
def test_a_printed_rate_is_read_as_units_of_the_rows_currency_for_one_dollar(wording, per_sgd):
    assert str(valuation.wording_rate(wording)) == per_sgd


@pytest.mark.parametrize("wording", [
    "POS SAMPLE STORE MUMBAI", "SAMPLE@MAIL SGD", "SGD@ 0", None, "",
])
def test_a_wording_with_no_rate_has_none(wording):
    assert valuation.wording_rate(wording) is None


# --- valuing a row at it ------------------------------------------------------------


def test_a_dollar_row_is_valued_at_the_banks_rate_in_its_wording(client, conn, usd):
    save_rate(conn, "USD/SGD", "2025-01-08", USD_ECB)
    row = make_row(conn, usd, "2025-01-08", "Advice Funds Transfer 200-000002-2 : I-BANK SGD@ 0.7400000",
                   500000, flow_type="transfer")

    shown = listed(client, row)

    # US$5,000.00 at 0.74 a dollar: S$6,756.76 (at ECB's 1.35 it would be S$6,750.00).
    assert shown["sgd_minor"] == 675676
    assert shown["real_rate"] == {"per_sgd": "0.74", "currency": "USD", "source": "wording",
                                  "pair_row_id": None}


def test_a_rupee_remittance_is_valued_at_its_rfx_rate(client, conn, inr):
    save_rate(conn, "INR/SGD", "2026-08-14", INR_ECB)
    row = make_row(conn, inr, "2026-08-14", "INWARD REMIT RFX SGD500.0@74.52 SAMPLE", -3726000)

    # ₹37,260.00 at 74.52 a dollar is S$500.00 to the cent (ECB's 0.0135 says S$503.01).
    assert listed(client, row)["sgd_minor"] == -50000


def test_spending_figures_count_the_row_at_the_banks_rate(client, conn, usd):
    save_rate(conn, "USD/SGD", "2025-01-08", USD_ECB)
    make_row(conn, usd, "2025-01-08", "SAMPLE STORE NEW YORK FX SGD@0.7400000", 7400, flow_type="expense")
    conn.execute("UPDATE transactions SET book = 'Household'")
    conn.commit()

    by_month = client.get("/api/dashboard/monthly?start=2025-01-01&end=2025-01-31").get_json()

    # US$74.00 at 0.74 a dollar: S$100.00.
    assert sum(by_month["2025-01"].values()) == pytest.approx(100.00)


def test_a_rate_read_the_wrong_way_up_is_no_rate_for_the_row(client, conn, usd):
    save_rate(conn, "USD/SGD", "2025-01-31", USD_ECB)
    # A balance line's rate (S$ for one US$) run on into a row's wording.
    row = make_row(conn, usd, "2025-01-31", "Interest Indicative in SGD @ 1.350000000 1.28", -25)

    shown = listed(client, row)

    # US$0.25 at ECB's 1.35: S$0.34; the printed figure is not taken as 1/1.35 a dollar.
    assert shown["sgd_minor"] == -34
    assert shown["real_rate"] is None


def test_a_printed_rate_with_no_saved_rate_in_its_month_values_nothing(client, conn, usd):
    row = make_row(conn, usd, "2025-01-08", "Advice Funds Transfer I-BANK SGD@ 0.7400000", 500000,
                   flow_type="transfer")

    # Nothing to check it against: left unvalued, as any row with no rate is.
    assert listed(client, row)["sgd_minor"] is None


def test_a_row_in_sgd_is_its_own_amount_whatever_its_wording(client, conn):
    sgd = make_account(conn, "Sample DBS 100-000001-1")
    save_rate(conn, "USD/SGD", "2025-01-08", USD_ECB)
    row = make_row(conn, sgd, "2025-01-08", "Advice Funds Transfer USD5,000.00 SGD@ 0.7400000", -675676)

    shown = listed(client, row)

    assert (shown["sgd_minor"], shown["real_rate"]) == (-675676, None)


def test_reading_it_stores_nothing(client, conn, usd):
    save_rate(conn, "USD/SGD", "2025-01-08", USD_ECB)
    row = make_row(conn, usd, "2025-01-08", "Advice Funds Transfer I-BANK SGD@ 0.7400000", 500000,
                   flow_type="transfer")
    before = [tuple(r) for r in conn.execute("SELECT * FROM transactions ORDER BY id")]
    held = [tuple(r) for r in conn.execute("SELECT * FROM rates ORDER BY id")]

    listed(client, row)
    client.get("/api/dashboard/monthly?start=2025-01-01&end=2025-01-31")

    assert [tuple(r) for r in conn.execute("SELECT * FROM transactions ORDER BY id")] == before
    assert [tuple(r) for r in conn.execute("SELECT * FROM rates ORDER BY id")] == held
    assert client.get("/api/history").get_json()["entries"] == []


# --- rates fetched on import ----------------------------------------------------------

RUPEE_BANK = "Sample Bank Savings 0009"
RUPEE_ROWS = [
    ("2026-08-10", "POS SAMPLE STORE MUMBAI", 1000000),
    ("2026-08-14", "POS SAMPLE CAFE MUMBAI", 500000),
]


def upload(client, account, rows, currency):
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
        done = confirm(client, resp.get_json())
        assert done.status_code == 200, done.get_json()
        return done
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]


@pytest.fixture
def source(monkeypatch):
    """The rate source, answering ₹ rates for 10 and 14 Aug; what it was asked."""
    asked = []

    def served(url):
        asked.append(url)
        return json.dumps({"rates": {"2026-08-10": {"SGD": 0.015}, "2026-08-14": {"SGD": 0.016}}})
    monkeypatch.setattr(rates, "_http_get", served)
    return asked


def entries(client):
    return client.get("/api/history").get_json()["entries"]


def test_an_import_of_rupee_rows_fetches_their_days_rates_in_one_request(client, conn, source):
    done = upload(client, RUPEE_BANK, RUPEE_ROWS, "INR")

    assert len(source) == 1 and "base=INR" in source[0]
    assert rates.saved(conn, "INR/SGD", "2026-08-10")["rate"] == "0.015"
    assert rates.saved(conn, "INR/SGD", "2026-08-14")["rate"] == "0.016"
    assert done.get_json()["rates"] == {"saved": 2, "currencies": [], "failed": []}


def test_the_rates_are_their_own_change_after_the_imports(client, conn, source):
    done = upload(client, RUPEE_BANK, RUPEE_ROWS, "INR")

    first, second = entries(client)[1], entries(client)[0]
    # The import's Undo (the toast's change) is the import's, not the rates'.
    assert int(done.headers["X-Fin-Change"]) == first["id"]
    assert second["summary"] == "Fetched the exchange rates for the imported rows"
    assert first["id"] < second["id"]


def test_undoing_the_import_keeps_the_rates(client, conn, source):
    done = upload(client, RUPEE_BANK, RUPEE_ROWS, "INR")

    undone = client.post(f"/api/history/{done.headers['X-Fin-Change']}/undo")

    assert undone.status_code == 200, undone.get_json()
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert rates.saved(conn, "INR/SGD", "2026-08-10")["rate"] == "0.015"


def test_a_failed_fetch_keeps_the_import_and_says_so(client, conn, monkeypatch):
    def down(_url):
        raise OSError("unreachable")
    monkeypatch.setattr(rates, "_http_get", down)

    done = upload(client, RUPEE_BANK, RUPEE_ROWS, "INR")

    assert done.get_json()["transactions_saved"] == 2
    assert done.get_json()["rates"] == {"saved": 0, "currencies": ["INR"], "failed": ["INR"]}
    assert rates.saved(conn, "INR/SGD", "2026-08-10") is None
    # Only the import is in Changes: a fetch that saved nothing is no change.
    assert [e["id"] for e in entries(client)] == [int(done.headers["X-Fin-Change"])]


def test_days_already_rated_are_not_asked_for(client, conn, monkeypatch):
    save_rate(conn, "INR/SGD", "2026-08-10", "0.0150")
    save_rate(conn, "INR/SGD", "2026-08-14", "0.0160")
    monkeypatch.setattr(rates, "_http_get", lambda url: pytest.fail(f"asked {url}"))

    done = upload(client, RUPEE_BANK, RUPEE_ROWS, "INR")

    assert done.get_json()["rates"] == {"saved": 0, "currencies": [], "failed": []}


def test_an_import_in_sgd_asks_for_nothing_even_with_older_rows_short_of_a_rate(client, conn, inr, monkeypatch):
    # A rupee row of another account with no rate: the Home notice's to fetch.
    make_row(conn, inr, "2026-07-02", "POS SAMPLE OLD", 100000, flow_type="expense")
    monkeypatch.setattr(rates, "_http_get", lambda url: pytest.fail(f"asked {url}"))

    done = upload(client, "Sample Card 0001", [("2026-08-05", "SAMPLE GROCER SINGAPORE", 10000)], "SGD")

    assert done.get_json()["rates"] == {"saved": 0, "currencies": [], "failed": []}
