"""A transfer into Home Reno or a fixed deposit moves that account's balance
(ruling 4). Such an account is a bank account with no statement fin imports:
its balance is the figure typed for it plus the transfers and movements on
the household's other accounts that name it as their other side. Through the
HTTP interface; every name and figure is invented; whole cents."""

from test_balance_sheet import lines
from test_month_check import bring_in, check, enter, make_account, sheet

BANK = "Sample Bank 0002"
RENO = "Sample Reno Fund"
DEPOSIT = "Sample Fixed Deposit"


def build(client) -> dict:
    reno = make_account(client, RENO, "bank")
    deposit = make_account(client, DEPOSIT, "bank")
    enter(client, reno, "1000.00", "2026-07-31")
    enter(client, deposit, "20000.00", "2026-07-31")
    bring_in(client, BANK, 5_010_000, [
        ("2026-07-10", "SAMPLE BAKERY JUL", 10_000, "expense", {"book": "Household", "type": "Groceries"}),
    ], closing_date="2026-07-31")
    return {"reno": reno, "deposit": deposit}


def test_a_transfer_into_home_reno_moves_its_balance_and_leaves_nothing_unexplained(client):
    made = build(client)
    bring_in(client, BANK, 5_000_000, [
        ("2026-08-12", "FUNDS TRANSFER TO RENO", 20_000, "transfer", {"names": made["reno"]}),
    ])

    shown = lines(sheet(client, "2026-08"))

    assert shown[RENO]["balance_minor"] == 120_000
    assert shown[RENO]["since"] == "+ 1 transfer since"
    assert shown[BANK]["balance_minor"] == 4_980_000
    assert check(client)["unexplained_minor"] == 0


def test_money_back_out_of_a_fixed_deposit_lowers_it(client):
    made = build(client)
    # The deposit matures in part: money in to the bank, out of the deposit.
    bring_in(client, BANK, 5_000_000, [
        ("2026-08-20", "FIXED DEPOSIT PART WITHDRAWAL", -500_000, "movement", {"names": made["deposit"]}),
    ])

    shown = lines(sheet(client, "2026-08"))

    assert shown[DEPOSIT]["balance_minor"] == 1_500_000
    assert shown[BANK]["balance_minor"] == 5_500_000
    assert check(client)["unexplained_minor"] == 0


def test_a_transfer_dated_before_the_figure_is_already_in_it(client):
    made = build(client)
    bring_in(client, BANK, 5_000_000, [
        ("2026-08-12", "FUNDS TRANSFER TO RENO", 20_000, "transfer", {"names": made["reno"]}),
    ])
    enter(client, made["reno"], "1200.00", "2026-08-15")

    assert lines(sheet(client, "2026-08"))[RENO]["balance_minor"] == 120_000


def test_labelling_a_waiting_transfer_as_a_move_to_home_reno_moves_it(client):
    made = build(client)
    bring_in(client, BANK, 5_000_000, [
        ("2026-08-12", "FAST PAYMENT REF 771203 OTHR", 20_000, "review", {}),
    ])
    assert check(client)["unexplained_minor"] == -20_000
    (waiting,) = client.get("/api/transactions?per_page=50&flow=review").get_json()["transactions"]

    resp = client.post(f"/api/review/{waiting['id']}/label",
                       json={"choice": "own_account", "account_id": made["reno"]})

    assert resp.status_code == 200, resp.get_json()
    assert lines(sheet(client, "2026-08"))[RENO]["balance_minor"] == 120_000
    assert check(client)["unexplained_minor"] == 0
