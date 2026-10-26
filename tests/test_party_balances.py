"""A party's two balances and their net (fin-redesign 21; spec part 2, cases
5 and 6; RUL-020).

A counterparty may hold two balances with the household: what the household
owes them (a loan-kind account joined to their party, such as Owed to
Namrata) and the household's money they hold (the household's part of a
shared account joined to their party, such as HDFC 6324). Their party page
shows each, in its own currency and in S$, and the net. The pattern holds
for any party.

Every name, number and figure is invented.
"""

from __future__ import annotations

import pytest

from test_balance_sheet import bring_in, enter, make_account

BANK = "Sample Bank 0002"
PAID = "FAST PAYMENT SAMPLE FRIEND"
SHARED = "Sample Joint Savings 0007"


@pytest.fixture
def friend(client, conn) -> dict:
    """The party, the loan owed to them (S$100,000.00 on 23 Aug, S$20,000.00
    paid to them on 30 Aug) and the shared rupee account (our part
    ₹10,00,000.00 on 1 Jan), at S$0.0150 a rupee."""
    party = client.post("/api/services", json={"name": "Sample Friend"}).get_json()["id"]
    loan = make_account(client, "Owed to Sample Friend", "loan")
    enter(client, loan, "100000.00", "2026-08-23")
    make_account(client, BANK, "bank", last_four="0002")
    bring_in(client, BANK, [("2026-08-30", PAID, 2000000)], names={PAID: loan})
    held = make_account(client, SHARED, "bank", currency="INR", last_four="0007")
    assert client.put(f"/api/accounts/{held}/shared", json={"shared": True}).status_code == 200
    enter(client, held, "1000000.00", "2026-01-01")
    assert client.put("/api/rates", json={"currency": "INR", "date": "2026-09-01", "rate": "0.0150"}).status_code == 200
    return {"party": party, "loan": loan, "held": held}


def join(client, account_id: int, party_id):
    return client.put(f"/api/accounts/{account_id}/party", json={"service_id": party_id})


def balances(client, party_id: int, month: str = "2026-09") -> dict:
    resp = client.get(f"/api/services/{party_id}/balances?month={month}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def test_a_shared_account_joins_its_party_and_an_account_not_shared_still_cannot(client, conn, friend):
    plain = make_account(client, "Sample Bank 0005", "bank")

    assert join(client, friend["held"], friend["party"]).status_code == 200
    assert join(client, plain, friend["party"]).status_code == 400


def test_the_party_page_shows_what_we_owe_them_what_of_ours_they_hold_and_the_net(client, conn, friend):
    join(client, friend["loan"], friend["party"])
    join(client, friend["held"], friend["party"])

    got = balances(client, friend["party"])

    lines = {line["account_id"]: line for line in got["lines"]}
    # S$100,000.00 owed less the S$20,000.00 paid them.
    assert lines[friend["loan"]]["balance_minor"] == -8000000
    assert lines[friend["loan"]]["side"] == "owed"
    # ₹10,00,000.00 of ours at S$0.0150: S$15,000.00.
    assert (lines[friend["held"]]["balance_minor"], lines[friend["held"]]["value_minor"]) == (100000000, 1500000)
    assert lines[friend["held"]]["side"] == "held"
    assert got["net_minor"] == -6500000
    assert got["net"] == "we owe S$ 65,000.00, net"


def test_a_balance_with_no_rate_is_left_out_of_the_net_and_said(client, conn, friend):
    join(client, friend["loan"], friend["party"])
    join(client, friend["held"], friend["party"])

    got = balances(client, friend["party"], "2026-08")

    assert got["net_minor"] == -8000000
    assert [entry["name"] for entry in got["left_out"]] == [SHARED]


def test_a_party_with_no_balances_has_none_and_no_net(client, conn, friend):
    got = balances(client, friend["party"])

    assert got["lines"] == [] and got["net_minor"] is None


def test_a_shared_account_joined_to_a_party_stays_shared_until_it_leaves_the_party(client, conn, friend):
    join(client, friend["held"], friend["party"])

    resp = client.put(f"/api/accounts/{friend['held']}/shared", json={"shared": False})

    assert resp.status_code == 400 and "party" in resp.get_json()["error"]
    assert next(a for a in client.get("/api/accounts").get_json() if a["id"] == friend["held"])["shared"] is True
