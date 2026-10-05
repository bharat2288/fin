"""The rupee account, through the HTTP interface against a temporary database.

An HDFC statement is uploaded and confirmed as the operator would, with
pdfplumber replaced by synthetic page text; rates are fetched through a
stand-in for the one function that reaches outside, or typed in. Nothing here
opens a real statement, makes a network call or holds a real password. Every
name, number, figure and rate is invented.

Amounts are whole minor units: paise on the rupee account, cents in SGD. A
row's amount is positive for money out.
"""

import io
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException

import parse_hdfc
import rates

HDFC = "HDFC Bank Savings 1234"
HEADER = "Date Narration Chq./Ref.No. ValueDt WithdrawalAmt. DepositAmt. ClosingBalance"

# Opening Rs 1,00,000.00 on 1 April; Rs 1,23,500.50 at the end of May;
# closing Rs 1,23,810.75 on 30 June.
STATEMENT = ["\n".join([
    "HDFC BANK LIMITED",
    "SAMPLE HOLDER",
    "Account No : 00000000001234",
    "From : 01/04/2026 To : 30/06/2026",
    HEADER,
    "02/04/26 UPI-SAMPLE GROCER-PAYMENT 0000000000000001 02/04/26 1,500.00 98,500.00",
    "15/04/26 NEFT CR-SAMPLE EMPLOYER 0000000000000002 15/04/26 25,000.50 1,23,500.50",
    "01/06/26 INTEREST PAID TILL 31-MAY-2026 0000000000000003 31/05/26 310.25 1,23,810.75",
    "STATEMENT SUMMARY :-",
    "Opening Balance Dr Count Cr Count Debits Credits Closing Bal",
    "1,00,000.00 1 2 1,500.00 25,310.75 1,23,810.75",
])]

OPENING, END_OF_MAY, CLOSING = 10000000, 12350050, 12381075
SECRET = "invented-pass-phrase"


class FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self, **_kwargs):
        return self._text


class FakePdf:
    def __init__(self, pages):
        self.pages = [FakePage(text) for text in pages]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def close(self):
        pass


# --- helpers --------------------------------------------------------------------


@pytest.fixture(autouse=True)
def nothing_reaches_outside(monkeypatch):
    """No test here may make a network call or see a password it did not set."""

    def refuse(_url):
        pytest.fail("a test tried to reach the network")

    monkeypatch.setattr(rates, "_http_get", refuse)
    monkeypatch.delenv(parse_hdfc.PASSWORD_VARIABLE, raising=False)


def upload(client, monkeypatch, pages=STATEMENT, locked_with=None) -> dict:
    def opener(_path, password=None, **_kwargs):
        if locked_with is not None and password != locked_with:
            raise PdfminerException(PDFPasswordIncorrect())
        return FakePdf(pages)

    monkeypatch.setattr(parse_hdfc.pdfplumber, "open", opener)
    resp = client.post(
        "/api/import/upload",
        data={"files": (io.BytesIO(b"stand-in"), "sample.pdf")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def confirm(client, preview: dict):
    return client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": preview["groups"],
    })


def bring_in(client, monkeypatch) -> None:
    preview = upload(client, monkeypatch)
    assert preview["errors"] == [], preview["errors"]
    resp = confirm(client, preview)
    assert resp.status_code == 200, resp.get_json()


def make_account(client, name: str, kind: str, **extra) -> int:
    resp = client.post("/api/accounts", json={"name": name, "type": kind, **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def enter(client, account_id: int, amount: str, on: str):
    return client.post("/api/anchors", json={"account_id": account_id, "amount": amount, "date": on})


def set_rate(client, on: str, rate: str):
    return client.put("/api/rates", json={"currency": "INR", "date": on, "rate": rate})


def served(monkeypatch, answers: dict, calls: list | None = None) -> None:
    """Stand in for the public source: `answers` maps the day asked for to
    (rate text, the day the rate is of)."""

    def fetch(base, quote, on):
        if calls is not None:
            calls.append((base, quote, on))
        return answers[on]

    monkeypatch.setattr(rates, "fetch_reference_rate", fetch)


def fetch(client, on: str):
    return client.post("/api/rates/fetch", json={"currency": "INR", "date": on})


def sheet(client, month: str) -> dict:
    resp = client.get(f"/api/balance-sheet?month={month}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def lines(shown: dict) -> dict:
    return {line["name"]: line for section in shown["sections"] for line in section["lines"]}


def section(shown: dict, name: str) -> dict:
    return next(s for s in shown["sections"] if s["name"] == name)


def in_sgd(paise: int, rate: str) -> int:
    """Rupees in paise as SGD cents at a rate: decimal, half-even to the cent."""
    exact = Decimal(paise) / 100 * Decimal(rate) * 100
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


# --- the import: a household bank account in rupees -----------------------------


def test_the_preview_shows_the_statement_in_rupees_and_that_it_ties(client, monkeypatch):
    preview = upload(client, monkeypatch)

    assert preview["errors"] == []
    (group,) = preview["groups"]
    assert (group["account"], group["currency"], group["tie"]) == (HDFC, "INR", "ties")
    (line,) = group["statements"]
    assert (line["opening_minor"], line["rows_minor"], line["closing_minor"]) == (
        OPENING, 2381075, CLOSING,
    )
    assert (line["opening"], line["rows_sum"], line["closing"]) == (
        "Rs 100,000.00", "Rs 23,810.75", "Rs 123,810.75",
    )
    assert line["difference_minor"] == 0 and line["closing_date"] == "2026-06-30"


def test_the_import_creates_a_household_bank_account_in_rupees(client, monkeypatch):
    bring_in(client, monkeypatch)

    (account,) = [a for a in client.get("/api/accounts").get_json() if a["name"] == HDFC]
    assert (account["type"], account["owner"], account["currency"]) == ("bank", "Household", "INR")
    assert account["anchor"] == {"date": "2026-06-30", "amount_minor": CLOSING, "source": "statement"}


def test_every_row_lands_in_paise_and_carries_the_opening_to_the_closing(client, monkeypatch):
    bring_in(client, monkeypatch)

    listed = client.get("/api/transactions?per_page=500").get_json()["transactions"]
    rows = sorted((tx["date"], tx["amount_sgd"]) for tx in listed if tx["account_name"] == HDFC)
    assert rows == [("2026-04-02", 1500.0), ("2026-04-15", -25000.5), ("2026-06-01", -310.25)]
    assert {tx["currency"] for tx in listed if tx["account_name"] == HDFC} == {"INR"}
    assert OPENING - (150000 - 2500050 - 31025) == CLOSING


def test_importing_the_statement_again_adds_no_row_and_no_second_anchor(client, monkeypatch):
    bring_in(client, monkeypatch)

    again = confirm(client, upload(client, monkeypatch)).get_json()

    assert (again["transactions_saved"], again["duplicates_skipped"], again["anchors_written"]) == (0, 3, 0)


def test_a_rupee_statement_is_refused_for_an_account_held_in_another_currency(client, monkeypatch):
    make_account(client, HDFC, "bank")  # SGD, the default

    preview = upload(client, monkeypatch)

    assert preview["groups"] == []
    (refusal,) = preview["errors"]
    assert "SGD" in refusal["error"] and "INR" in refusal["error"]


def test_confirm_refuses_a_currency_that_is_not_the_accounts(client, monkeypatch):
    preview = upload(client, monkeypatch)
    make_account(client, HDFC, "bank")  # created in SGD between preview and confirm

    resp = confirm(client, preview)

    assert resp.status_code == 400
    assert "SGD" in resp.get_json()["error"] and "INR" in resp.get_json()["error"]
    assert client.get("/api/transactions").get_json()["transactions"] == []


def test_confirm_refuses_a_currency_fin_does_not_hold(client, monkeypatch):
    preview = upload(client, monkeypatch)
    preview["groups"][0]["currency"] = "XXX"

    resp = confirm(client, preview)

    assert resp.status_code == 400
    assert [a for a in client.get("/api/accounts").get_json() if a["name"] == HDFC] == []


# --- the password ---------------------------------------------------------------


def test_a_locked_statement_with_no_password_set_is_refused_naming_the_variable(client, monkeypatch):
    preview = upload(client, monkeypatch, locked_with=SECRET)

    assert preview["groups"] == []
    (refusal,) = preview["errors"]
    assert "FIN_HDFC_PDF_PASSWORD" in refusal["error"]


def test_a_locked_statement_opens_with_the_password_from_the_environment(client, monkeypatch):
    monkeypatch.setenv(parse_hdfc.PASSWORD_VARIABLE, SECRET)

    preview = upload(client, monkeypatch, locked_with=SECRET)

    assert preview["errors"] == []
    assert preview["groups"][0]["statements"][0]["closing_minor"] == CLOSING
    assert SECRET not in str(preview)


def test_a_wrong_password_is_refused_without_echoing_it(client, monkeypatch):
    monkeypatch.setenv(parse_hdfc.PASSWORD_VARIABLE, "another-invented-phrase")

    preview = upload(client, monkeypatch, locked_with=SECRET)

    (refusal,) = preview["errors"]
    assert "FIN_HDFC_PDF_PASSWORD" in refusal["error"]
    assert "another-invented-phrase" not in str(preview) and SECRET not in str(preview)


# --- typed figures agree with printed ones --------------------------------------


def test_a_figure_typed_for_a_rupee_account_is_whole_paise(client):
    rupee = make_account(client, "Sample Rupee Savings", "bank", currency="INR")

    resp = enter(client, rupee, "1,00,000.50", "2026-06-30")

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["anchor"]["amount_minor"] == 10000050
    assert enter(client, rupee, "1.005", "2026-07-31").status_code == 400


def test_an_account_in_a_currency_with_no_declared_minor_unit_is_refused(client):
    resp = client.post("/api/accounts", json={"name": "Sample", "type": "bank", "currency": "XXX"})
    assert resp.status_code == 400
    assert "XXX" in resp.get_json()["error"]

    rupee = make_account(client, "Sample Rupee Savings", "bank", currency="INR")
    resp = client.put(f"/api/accounts/{rupee}", json={"currency": "XXX"})
    assert resp.status_code == 400


# --- saved rates ----------------------------------------------------------------


def test_a_fetched_rate_is_saved_with_its_source_and_read_from_the_saved_copy_after(client, monkeypatch):
    calls = []
    served(monkeypatch, {"2026-06-30": ("0.01534", "2026-06-30")}, calls)

    first = fetch(client, "2026-06-30")
    assert first.status_code == 200, first.get_json()
    saved = first.get_json()
    assert saved["created"] is True
    assert (saved["rate"]["pair"], saved["rate"]["date"], saved["rate"]["rate"]) == (
        "INR/SGD", "2026-06-30", "0.01534",
    )
    assert "European Central Bank" in saved["rate"]["source"]
    assert saved["rate"]["fetched_at"]

    # The source now fails, and would say something else if it answered.
    def unreachable(*_args):
        pytest.fail("the saved copy was not used")

    monkeypatch.setattr(rates, "fetch_reference_rate", unreachable)
    second = fetch(client, "2026-06-30").get_json()
    assert second["created"] is False and second["rate"] == saved["rate"]
    assert calls == [("INR", "SGD", "2026-06-30")]
    assert client.get("/api/rates?currency=INR").get_json() == [saved["rate"]]


def test_a_weekend_takes_the_prior_business_days_rate_and_the_source_says_which(client, monkeypatch):
    served(monkeypatch, {"2026-05-31": ("0.0152", "2026-05-29")})  # a Sunday

    saved = fetch(client, "2026-05-31").get_json()["rate"]

    assert (saved["date"], saved["rate"]) == ("2026-05-31", "0.0152")
    assert "2026-05-29" in saved["source"]


def test_a_fetch_that_fails_saves_nothing_and_says_so_plainly(client, monkeypatch):
    def fails(*_args):
        raise OSError("connection refused by sample-host token=abc")

    monkeypatch.setattr(rates, "fetch_reference_rate", fails)

    resp = fetch(client, "2026-06-30")

    assert resp.status_code == 502
    assert "token" not in resp.get_json()["error"]
    assert client.get("/api/rates").get_json() == []


def test_a_rate_that_is_not_a_positive_decimal_is_never_saved(client, monkeypatch):
    for bad in ("0", "-0.01", "NaN", "abc", "", "1e-2"):
        served(monkeypatch, {"2026-06-30": (bad, "2026-06-30")})
        assert fetch(client, "2026-06-30").status_code == 502, bad
        assert set_rate(client, "2026-06-30", bad).status_code == 400, bad
    assert set_rate(client, "2026-06-30", 0.0153).status_code == 400  # a float is refused
    assert client.get("/api/rates").get_json() == []


def test_a_rate_is_refused_for_a_day_that_is_not_one_or_has_not_come(client, monkeypatch):
    served(monkeypatch, {})
    assert fetch(client, "2026-02-30").status_code == 400
    assert fetch(client, "2999-01-01").status_code == 400
    assert set_rate(client, "30/06/2026", "0.0153").status_code == 400
    assert client.put("/api/rates", json={"currency": "XXX", "date": "2026-06-30", "rate": "1"}).status_code == 400
    assert client.get("/api/rates").get_json() == []


def test_the_operator_can_overwrite_a_rate(client, monkeypatch):
    served(monkeypatch, {"2026-06-30": ("0.01534", "2026-06-30")})
    fetch(client, "2026-06-30")

    resp = set_rate(client, "2026-06-30", "0.0155")

    assert resp.status_code == 200, resp.get_json()
    (saved,) = client.get("/api/rates").get_json()
    assert (saved["date"], saved["rate"], saved["source"]) == ("2026-06-30", "0.0155", "entered by the operator")
    # And fetching again keeps what the operator entered.
    assert fetch(client, "2026-06-30").get_json()["rate"]["rate"] == "0.0155"


def test_the_fetch_asks_the_public_source_for_the_day_and_keeps_the_decimal_text(monkeypatch):
    asked = []

    def answer(url):
        asked.append(url)
        return '{"amount":1.0,"base":"INR","date":"2026-05-29","rates":{"SGD":0.01520}}'

    monkeypatch.setattr(rates, "_http_get", answer)

    assert rates.fetch_reference_rate("INR", "SGD", "2026-05-31") == ("0.01520", "2026-05-29")
    assert asked == ["https://api.frankfurter.dev/v1/2026-05-31?base=INR&symbols=SGD"]


# --- the balance sheet: rupees, their SGD value, the rate and its date -----------


def test_with_no_saved_rate_the_account_shows_rupees_only_and_is_left_out_saying_so(client, monkeypatch):
    sgd = make_account(client, "Sample Bank 0002", "bank")
    enter(client, sgd, "50000.00", "2026-06-30")
    bring_in(client, monkeypatch)

    shown = sheet(client, "2026-06")
    line = lines(shown)[HDFC]

    assert (line["balance_minor"], line["balance"]) == (CLOSING, "Rs 123,810.75")
    assert (line["value_minor"], line["value"], line["rate"]) == (None, None, None)
    assert line["in_total"] is False
    assert "no saved INR/SGD rate" in line["left_out"]
    assert shown["net_worth_minor"] == 5000000
    assert HDFC in shown["note"] and "no saved INR/SGD rate" in shown["note"]


def test_a_rate_saved_only_for_a_later_day_is_not_used(client, monkeypatch):
    bring_in(client, monkeypatch)
    set_rate(client, "2026-07-01", "0.0153")

    line = lines(sheet(client, "2026-06"))[HDFC]

    assert line["value_minor"] is None and line["in_total"] is False


def test_with_the_days_rate_it_shows_rupees_sgd_and_the_rate_and_joins_the_total(client, monkeypatch):
    sgd = make_account(client, "Sample Bank 0002", "bank")
    enter(client, sgd, "50000.00", "2026-06-30")
    bring_in(client, monkeypatch)
    set_rate(client, "2026-06-30", "0.01534")

    shown = sheet(client, "2026-06")
    line = lines(shown)[HDFC]

    # Rs 123,810.75 x 0.01534 = S$ 1,899.256905
    assert in_sgd(CLOSING, "0.01534") == 189926
    assert (line["balance_minor"], line["balance"]) == (CLOSING, "Rs 123,810.75")
    assert (line["value_minor"], line["value"]) == (189926, "S$ 1,899.26")
    assert line["rate"]["rate"] == "0.01534" and line["rate"]["date"] == "2026-06-30"
    assert line["rate"]["exact"] is True
    assert line["rate"]["text"] == "1 INR = S$ 0.01534, rate of 2026-06-30"
    assert line["in_total"] is True and line["left_out"] is None
    # It joins the total: SGD cash plus the rupees' SGD value, to the cent.
    assert section(shown, "cash")["total_minor"] == 5000000 + 189926
    assert shown["net_worth_minor"] == 5000000 + 189926
    assert shown["note"] is None


def test_the_sgd_value_is_rounded_half_even_to_the_cent(client):
    rupee = make_account(client, "Sample Rupee Savings", "bank", currency="INR")
    enter(client, rupee, "1250.00", "2026-06-30")
    set_rate(client, "2026-06-30", "0.0153")

    line = lines(sheet(client, "2026-06"))["Sample Rupee Savings"]

    # Rs 1,250.00 x 0.0153 = S$ 19.125 exactly: half-even gives 19.12.
    assert line["value_minor"] == 1912


def test_with_no_rate_for_the_day_the_latest_earlier_one_is_used_and_it_says_so(client, monkeypatch):
    bring_in(client, monkeypatch)
    set_rate(client, "2026-06-20", "0.0150")
    set_rate(client, "2026-06-26", "0.0153")

    line = lines(sheet(client, "2026-06"))[HDFC]

    assert line["value_minor"] == in_sgd(CLOSING, "0.0153") == 189430
    assert line["rate"]["date"] == "2026-06-26" and line["rate"]["exact"] is False
    assert "2026-06-26" in line["rate"]["text"]
    assert "latest saved on or before 2026-06-30" in line["rate"]["text"]
    assert line["in_total"] is True


def test_an_overwritten_rate_corrects_the_figure_already_shown(client, monkeypatch):
    bring_in(client, monkeypatch)
    set_rate(client, "2026-06-30", "0.01534")
    assert lines(sheet(client, "2026-06"))[HDFC]["value_minor"] == 189926

    set_rate(client, "2026-06-30", "0.0153")

    assert lines(sheet(client, "2026-06"))[HDFC]["value_minor"] == 189430


# --- currency change (P18) ------------------------------------------------------


@pytest.fixture
def rupees_through_june(client, monkeypatch) -> None:
    """The rupee account with a balance at the end of May and of June, and a
    rate saved for each: May's fetched on a Sunday, June's on the day."""
    rupee = make_account(client, HDFC, "bank", currency="INR")
    assert enter(client, rupee, "100000.00", "2026-03-31").status_code == 200
    bring_in(client, monkeypatch)
    served(monkeypatch, {
        "2026-05-31": ("0.0152", "2026-05-29"),
        "2026-06-30": ("0.01534", "2026-06-30"),
    })
    assert fetch(client, "2026-05-31").status_code == 200
    assert fetch(client, "2026-06-30").status_code == 200


def test_a_rupee_balance_in_sgd_uses_the_saved_rate_for_its_date(client, rupees_through_june):
    may, june = sheet(client, "2026-05"), sheet(client, "2026-06")

    assert lines(may)[HDFC]["balance_minor"] == END_OF_MAY
    assert lines(may)[HDFC]["value_minor"] == in_sgd(END_OF_MAY, "0.0152") == 187721
    assert lines(may)[HDFC]["rate"]["date"] == "2026-05-31"
    assert lines(june)[HDFC]["balance_minor"] == CLOSING
    assert lines(june)[HDFC]["value_minor"] == in_sgd(CLOSING, "0.01534") == 189926
    assert lines(june)[HDFC]["check"]["status"] == "ties"


def test_currency_change_is_the_opening_balance_revalued_from_the_start_rate_to_the_end_rate(
    client, rupees_through_june,
):
    change = sheet(client, "2026-06")["currency_change"]

    at_start = in_sgd(END_OF_MAY, "0.0152")    # S$ 1,877.21
    at_end = in_sgd(END_OF_MAY, "0.01534")     # S$ 1,894.50
    assert (at_start, at_end) == (187721, 189450)
    assert (change["minor"], change["text"]) == (at_end - at_start, "S$ 17.29")
    assert (change["from"], change["to"]) == ("2026-05-31", "2026-06-30")
    (line,) = change["lines"]
    assert (line["name"], line["opening_minor"], line["opening"]) == (
        HDFC, END_OF_MAY, "Rs 123,500.50",
    )
    assert (line["start"]["rate"], line["end"]["rate"]) == ("0.0152", "0.01534")
    assert (line["at_start_minor"], line["at_end_minor"], line["change_minor"]) == (187721, 189450, 1729)
    assert change["note"] is None


def test_the_change_in_sgd_value_is_currency_change_plus_the_months_rows_at_the_end_rate(
    client, rupees_through_june,
):
    """Conservation, to the cent: nothing of the month's move in SGD value is
    lost between the revaluation and the rupees that came in."""
    may, june = sheet(client, "2026-05"), sheet(client, "2026-06")
    value_may, value_june = lines(may)[HDFC]["value_minor"], lines(june)[HDFC]["value_minor"]
    change = june["currency_change"]["minor"]

    # June's rows: Rs 310.25 of interest came in.
    assert CLOSING - END_OF_MAY == 31025
    rows_at_end_rate = value_june - in_sgd(END_OF_MAY, "0.01534")
    assert rows_at_end_rate == 476
    assert value_june - value_may == change + rows_at_end_rate == 2205
    # And the rupee account is all of net worth here, so net worth moved by the same.
    assert june["net_worth_minor"] - may["net_worth_minor"] == 2205


def test_with_no_rupee_account_currency_change_is_nothing(client):
    sgd = make_account(client, "Sample Bank 0002", "bank")
    enter(client, sgd, "50000.00", "2026-06-30")

    change = sheet(client, "2026-06")["currency_change"]

    assert (change["minor"], change["lines"], change["note"]) == (0, [], None)


def test_currency_change_is_not_guessed_when_a_rate_or_the_opening_balance_is_missing(client, monkeypatch):
    bring_in(client, monkeypatch)            # anchored on 30 June only: no balance at the end of May
    set_rate(client, "2026-06-30", "0.01534")

    change = sheet(client, "2026-06")["currency_change"]

    assert change["minor"] is None and change["text"] is None
    (line,) = change["lines"]
    assert line["change_minor"] is None and "no balance" in line["why"]
    assert HDFC in change["note"]

    # July opens on June's balance, and June's rate is the latest saved for
    # both ends of July: the same balance at the same rate, so no change.
    july = sheet(client, "2026-07")["currency_change"]
    assert (july["minor"], july["lines"][0]["opening_minor"]) == (0, CLOSING)
    assert july["lines"][0]["end"]["exact"] is False


def test_currency_change_is_not_guessed_when_the_start_has_no_rate(client, monkeypatch):
    rupee = make_account(client, HDFC, "bank", currency="INR")
    enter(client, rupee, "100000.00", "2026-03-31")
    bring_in(client, monkeypatch)
    set_rate(client, "2026-06-30", "0.01534")   # nothing saved on or before 31 May

    change = sheet(client, "2026-06")["currency_change"]

    assert change["minor"] is None
    (line,) = change["lines"]
    assert line["opening_minor"] == END_OF_MAY and line["change_minor"] is None
    assert line["why"] == "no saved INR/SGD rate on or before 2026-05-31"


# --- rupees are never added to SGD figures as if they were SGD ------------------


def test_rupee_rows_are_held_out_of_the_sgd_spending_figures(client, monkeypatch):
    bring_in(client, monkeypatch)
    listed = client.get("/api/transactions?per_page=500").get_json()["transactions"]
    flows = {tx["description"]: tx["flow_type"] for tx in listed}
    # The April payment is spending or waiting for review: either way a figure
    # the dashboard would add up.
    assert flows["UPI-SAMPLE GROCER-PAYMENT"] in ("expense", "review")

    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-04").get_json()
    assert (cards["spend"], cards["household"]) == (0, 0)
    assert (cards["held_out_total"], cards["waiting_total"]) == (0, 0)
    assert client.get("/api/dashboard/monthly?start=2026-04-01&end=2026-06-30").get_json() == {}
    assert client.get("/api/dashboard/types?start=2026-04-01&end=2026-06-30").get_json() == []
    # The rows themselves are still listed, in rupees.
    assert len([tx for tx in listed if tx["currency"] == "INR"]) == 3
