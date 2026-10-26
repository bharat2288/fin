"""A company kept in rupees (the film's company, Mahavir): its line counts the
rupee money put into it, is valued at the month-end rate as a rupee bank
account is, and its revaluation is currency change, so the month check
leaves nothing unexplained. A company kept in S$ (Moom, Kalesh) has no
currency change and is as it was.

Every name, figure and rate is invented.
"""

from test_month_check import (
    RATE_AUGUST, RATE_JULY, RUPEE, bring_in, check, household, make_account, set_rate, sheet,
)

FILM = "Sample Film Company"
RUPEES_JULY = 10_000_000          # Rs 100,000.00 left in the rupee account at the end of July
PUT_IN_JULY = 3_000_000           # Rs 30,000.00 into the film in July
PUT_IN_AUGUST = 2_000_000         # Rs 20,000.00 more in August


def film_months(client) -> int:
    film = make_account(client, FILM, "company", currency="INR")
    make_account(client, RUPEE, "bank", currency="INR")
    make_account(client, "Moom", "company")
    set_rate(client, "2026-07-31", RATE_JULY)
    set_rate(client, "2026-08-31", RATE_AUGUST)
    bring_in(client, RUPEE, RUPEES_JULY + PUT_IN_JULY + 50_000, [
        ("2026-07-10", "NEFT DR SAMPLE FILM JUL", PUT_IN_JULY, "movement", {"names": film}),
        ("2026-07-14", "UPI-SAMPLE GROCER JUL", 50_000, "expense", household("Groceries")),
    ], closing_date="2026-07-31", currency="INR")
    bring_in(client, RUPEE, RUPEES_JULY, [
        ("2026-08-12", "NEFT DR SAMPLE FILM AUG", PUT_IN_AUGUST, "movement", {"names": film}),
    ], currency="INR")
    return film


def line(shown: dict, name: str) -> dict:
    return next(l for s in shown["sections"] for l in s["lines"] if l["name"] == name)


def test_a_rupee_company_counts_the_rupees_put_in_and_is_valued_at_the_month_end_rate(client):
    film_months(client)

    film = line(sheet(client), FILM)

    assert film["balance_minor"] == PUT_IN_JULY + PUT_IN_AUGUST
    assert film["rows_not_counted"] == 0
    # Rs 50,000.00 at 0.01534.
    assert film["value_minor"] == 76_700


def test_a_rupee_companys_revaluation_is_currency_change_and_nothing_is_unexplained(client):
    film_id = film_months(client)

    shown = check(client)
    change = {l["account_id"]: l for l in sheet(client)["currency_change"]["lines"]}

    # Rs 30,000.00 held at the end of July: S$ 456.00 then, S$ 460.20 now.
    assert change[film_id]["opening_minor"] == PUT_IN_JULY
    assert change[film_id]["change_minor"] == 46_020 - 45_600
    assert shown["unexplained_minor"] == 0
    assert shown["left_out"] == []


def test_a_company_kept_in_sgd_has_no_currency_change(client):
    film_months(client)

    names = {l["name"] for l in sheet(client)["currency_change"]["lines"]}

    assert "Moom" not in names
    assert FILM in names


def test_rupees_paid_in_from_an_account_left_out_of_the_check_cross_its_edge_at_the_month_end_rate(client):
    """A second rupee account (a shared account with no figure for our part
    at the end of July, or one whose first statement is August's) pays into
    the film: the rupees cross the check's edge valued at the month-end
    rate, never read as cents."""
    film = film_months(client)
    other = "Sample Second Rupee 0004"
    make_account(client, other, "bank", currency="INR", last_four="0004")
    bring_in(client, other, 5_000_000, [
        ("2026-08-20", "NEFT DR SAMPLE FILM AUG 2", 2_000_000, "movement", {"names": film}),
    ], currency="INR")

    shown = check(client)

    # Rs 20,000.00 at 0.01534.
    assert shown["outside_minor"] == 30_680
    assert shown["unexplained_minor"] == 0


def test_an_account_holding_a_figure_keeps_its_currency_until_the_figure_goes(client):
    """A figure is held in its account's currency: switching an S$ company
    holding one to rupees would read S$ 1,000.00 as Rs 1,000.00. Refused,
    with what to do; with the figure deleted the switch is made."""
    company = make_account(client, "Sample Mahavir", "company")
    resp = client.post("/api/anchors", json={"account_id": company, "amount": "1000.00", "date": "2026-07-31"})
    assert resp.status_code == 200, resp.get_json()

    refused = client.put(f"/api/accounts/{company}", json={"currency": "INR"})

    assert refused.status_code == 400
    assert "figure" in refused.get_json()["error"]
    accounts = {a["id"]: a for a in client.get("/api/accounts").get_json()}
    assert accounts[company]["currency"] == "SGD"

    anchor = client.get(f"/api/anchors?account_id={company}").get_json()
    anchor_id = (anchor if isinstance(anchor, list) else anchor["anchors"])[0]["id"]
    assert client.delete(f"/api/anchors/{anchor_id}").status_code == 200
    assert client.put(f"/api/accounts/{company}", json={"currency": "INR"}).status_code == 200
    assert {a["id"]: a for a in client.get("/api/accounts").get_json()}[company]["currency"] == "INR"
