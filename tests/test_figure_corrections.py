"""A figure the operator typed can be corrected or deleted (ruling 2): the
balances and checks are worked out again from the figures that remain, and a
loan's interest is worked out again. A statement's balance cannot be changed
by hand. Through the HTTP interface against a temporary database; every name
and figure is invented. Amounts are whole cents."""

from test_balance_sheet import bring_in, enter, lines, make_account, sheet
from test_loan_interest import derived, row, statement, units

RENO = "Sample Reno Fund"
BANK = "Sample Bank 0002"


def figures(client, account_id: int) -> list[dict]:
    return client.get(f"/api/anchors?account_id={account_id}").get_json()


def test_a_typed_figure_can_be_replaced_and_the_balance_follows(client):
    reno = make_account(client, RENO, "bank")
    enter(client, reno, "1000.00", "2026-08-10")
    (held,) = figures(client, reno)

    resp = client.put(f"/api/anchors/{held['id']}", json={"amount": "1250.50", "note": "corrected"})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["anchor"]["amount_minor"] == 125_050
    assert lines(sheet(client, "2026-08"))[RENO]["balance_minor"] == 125_050
    (now,) = figures(client, reno)
    assert (now["amount_minor"], now["date"], now["note"]) == (125_050, "2026-08-10", "corrected")


def test_a_typed_figure_can_be_moved_to_another_day(client):
    reno = make_account(client, RENO, "bank")
    enter(client, reno, "1000.00", "2026-08-10")
    (held,) = figures(client, reno)

    assert client.put(f"/api/anchors/{held['id']}", json={"date": "2026-09-02"}).status_code == 200

    assert lines(sheet(client, "2026-08"))[RENO]["balance"] == "no figure"
    assert lines(sheet(client, "2026-09"))[RENO]["balance_minor"] == 100_000


def test_a_typed_figure_can_be_deleted_and_the_balance_rests_on_what_remains(client):
    reno = make_account(client, RENO, "bank")
    enter(client, reno, "1000.00", "2026-07-10")
    enter(client, reno, "1500.00", "2026-08-10")
    later = next(f for f in figures(client, reno) if f["date"] == "2026-08-10")

    resp = client.delete(f"/api/anchors/{later['id']}")

    assert resp.status_code == 200, resp.get_json()
    assert [f["date"] for f in figures(client, reno)] == ["2026-07-10"]
    assert lines(sheet(client, "2026-08"))[RENO]["balance_minor"] == 100_000


def test_a_wrong_early_figure_is_not_stuck_once_statements_arrive(client):
    # A bank account given a figure before its first statement: the
    # statement's check then runs against that figure.
    bank = make_account(client, BANK, "bank", last_four="0002")
    enter(client, bank, "900.00", "2026-07-31")
    bring_in(client, BANK, [("2026-08-05", "SAMPLE GROCER", 10_000)],
             opening=100_000, closing=90_000)
    assert lines(sheet(client, "2026-08"))[BANK]["check"]["status"] == "off"
    # It no longer takes a new figure, but the one typed is still listed
    # and can be put right.
    (listed,) = [a for a in client.get("/api/accounts").get_json() if a["id"] == bank]
    assert (listed["takes_a_figure"], listed["supplied_figures"]) == (False, 1)
    typed = next(f for f in figures(client, bank) if f["source"] == "supplied")

    assert client.put(f"/api/anchors/{typed['id']}", json={"amount": "1000.00"}).status_code == 200

    assert lines(sheet(client, "2026-08"))[BANK]["check"]["status"] == "ties"


def test_a_statement_balance_is_never_changed_or_deleted_by_hand(client):
    bring_in(client, BANK, [("2026-08-05", "SAMPLE GROCER", 10_000)],
             opening=100_000, closing=90_000)
    bank = next(a["id"] for a in client.get("/api/accounts").get_json() if a["name"] == BANK)
    (held,) = figures(client, bank)

    assert client.put(f"/api/anchors/{held['id']}", json={"amount": "1.00"}).status_code == 400
    assert client.delete(f"/api/anchors/{held['id']}").status_code == 400
    assert figures(client, bank) == [held]


def test_moving_a_figure_onto_a_day_already_held_is_refused(client):
    reno = make_account(client, RENO, "bank")
    enter(client, reno, "1000.00", "2026-07-10")
    enter(client, reno, "1500.00", "2026-08-10")
    earlier = next(f for f in figures(client, reno) if f["date"] == "2026-07-10")

    resp = client.put(f"/api/anchors/{earlier['id']}", json={"date": "2026-08-10"})

    assert resp.status_code == 409
    assert sorted(f["amount_minor"] for f in figures(client, reno)) == [100_000, 150_000]


def test_unknown_figure_and_bad_amount_are_refused(client):
    reno = make_account(client, RENO, "bank")
    enter(client, reno, "1000.00", "2026-07-10")
    (held,) = figures(client, reno)

    assert client.put("/api/anchors/9999", json={"amount": "1.00"}).status_code == 404
    assert client.delete("/api/anchors/9999").status_code == 404
    assert client.put(f"/api/anchors/{held['id']}", json={"amount": "1.001"}).status_code == 400
    assert client.put(f"/api/anchors/{held['id']}", json={"date": "2026-02-30"}).status_code == 400
    assert figures(client, reno) == [held]


def test_correcting_or_deleting_a_loan_figure_works_the_interest_out_again(client, conn):
    loan = make_account(client, "Sample Home Loan", "loan")
    bank = make_account(client, BANK, "bank", last_four="0002")
    stmt = statement(conn, bank)
    row(conn, stmt, "SAMPLE LOAN INSTALMENT", 880_000, "2026-07-15", other_side=loan)
    client.post("/api/anchors", json={"account_id": loan, "amount": units(91_000_000), "date": "2026-06-30"})
    client.post("/api/anchors", json={"account_id": loan, "amount": units(90_500_000), "date": "2026-07-31"})
    assert sum(int(round(t["amount_sgd"] * 100)) for t in derived(client)) == 380_000
    later = next(f for f in figures(client, loan) if f["date"] == "2026-07-31")

    # What was owed is typed positive, as for a new figure.
    resp = client.put(f"/api/anchors/{later['id']}", json={"amount": units(90_400_000)})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["anchor"]["amount_minor"] == -90_400_000
    assert "Interest" in resp.get_json()["message"]
    assert sum(int(round(t["amount_sgd"] * 100)) for t in derived(client)) == 280_000

    assert client.delete(f"/api/anchors/{later['id']}").status_code == 200
    assert derived(client) == []


def test_a_typed_bank_figure_cannot_be_moved_onto_the_days_its_statements_cover(client):
    bank = make_account(client, BANK, "bank", last_four="0002")
    enter(client, bank, "1000.00", "2026-07-31")
    bring_in(client, BANK, [("2026-08-05", "SAMPLE GROCER", 10_000)],
             opening=100_000, closing=90_000)
    typed = next(f for f in figures(client, bank) if f["source"] == "supplied")

    resp = client.put(f"/api/anchors/{typed['id']}", json={"date": "2026-09-15", "amount": "5.00"})

    assert resp.status_code == 400
    assert next(f for f in figures(client, bank) if f["id"] == typed["id"]) == typed
    # Earlier is fine.
    assert client.put(f"/api/anchors/{typed['id']}", json={"date": "2026-07-30"}).status_code == 200


def test_a_body_that_is_not_an_object_is_refused_not_a_crash(client):
    reno = make_account(client, RENO, "bank")
    enter(client, reno, "1000.00", "2026-08-10")
    (held,) = figures(client, reno)

    assert client.post("/api/anchors", json=[reno, "1.00"]).status_code == 400
    assert client.put(f"/api/anchors/{held['id']}", json=["1.00"]).status_code == 400
    assert figures(client, reno) == [held]
