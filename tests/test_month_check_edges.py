"""The month check counts only the accounts in net worth at both ends of the
month (M8): an account with no balance at one end is left out of both sides,
its rows are not counted, the check says so, and money moved between it and
an account in the check is stated apart rather than left unexplained.
Through the HTTP interface; every name and figure is invented; whole cents."""

import io

import parsers
from parse_dbs import ParsedStatement, ParsedTransaction
from test_month_check import (
    BANK, bring_in, check, enter, household, make_account, sgd_month, sheet, types,
)

CARD = "Sample Card 0001"


def no_balance(client, account: str, rows: list[tuple], *, kind: str = "card") -> None:
    """Upload and confirm rows from a source that states no balance, each
    (date, description, amount, flow, labels) labelled as the operator would."""
    def parse(_path):
        return [ParsedStatement(
            statement_type=kind, statement_date="2026-08-31", accounts=[account], filename="s.csv",
            transactions=[ParsedTransaction(date=d, description=n, amount_minor=a, card_info=account)
                          for d, n, a, _, _ in rows],
        )]

    parsers._PARSERS.insert(0, {"name": "Stand-in", "ext": ".csv",
                                "detect_fn": lambda _p: True, "parse_fn": parse})
    try:
        resp = client.post("/api/import/upload", data={"files": (io.BytesIO(b"x"), "s.csv")},
                           content_type="multipart/form-data")
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    preview = resp.get_json()
    labels = {n: (f, extra) for _, n, _, f, extra in rows}
    type_ids = types(client)
    for group in preview["groups"]:
        for tx in group["transactions"]:
            flow, extra = labels[tx["description"]]
            tx.update(_skip=False, flow_type=flow, book=extra.get("book"),
                      type_id=type_ids[extra["type"]] if "type" in extra else None,
                      other_side_id=extra.get("names"), service_id=None)
    resp = client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [{"account": g["account"], "transactions": g["transactions"],
                    "statements": g.get("statements", [])} for g in preview["groups"]],
    })
    assert resp.status_code == 200, resp.get_json()


def test_spending_on_a_card_with_no_balance_is_left_out_not_unexplained(client):
    sgd_month(client)
    no_balance(client, CARD, [("2026-08-12", "SAMPLE GROCER", 30_000, "expense", household("Groceries"))])

    shown = check(client)

    assert shown["spending_minor"] == 10_000
    assert shown["unexplained_minor"] == 0
    (left,) = shown["left_out"]
    assert left["name"] == CARD and "no balance" in left["why"]
    assert CARD in shown["note"]


def test_paying_off_a_card_left_out_is_money_moved_out_of_the_check(client):
    make_account(client, CARD, "card")
    card = next(a["id"] for a in client.get("/api/accounts").get_json() if a["name"] == CARD)
    bring_in(client, BANK, 5_010_000, [
        ("2026-07-10", "SAMPLE BAKERY JUL", 10_000, "expense", household("Groceries")),
    ], closing_date="2026-07-31")
    bring_in(client, BANK, 5_000_000, [
        ("2026-08-27", "PAYMENT TO SAMPLE CARD", 40_000, "payment", {"names": card}),
    ])

    shown = check(client)

    assert shown["outside_minor"] == -40_000
    assert shown["outside"] == "S$ -400.00"
    assert shown["unexplained_minor"] == 0
    assert [entry["name"] for entry in shown["left_out"]] == [CARD]


def test_the_first_month_an_account_has_a_balance_is_no_jump_in_net_worth(client):
    sgd_month(client)
    # The card's first statement balance is the end of August.
    bring_in(client, CARD, -50_000, [
        ("2026-08-14", "SAMPLE CAFE", 2_500, "expense", household("Groceries")),
    ], kind="card")

    shown = check(client)

    assert shown["actual_minor"] == sheet(client)["net_worth_minor"] - (-52_500)
    assert shown["spending_minor"] == 10_000
    assert shown["unexplained_minor"] == 0
    (left,) = shown["left_out"]
    assert left["name"] == CARD and left["why"] == "no balance on 2026-07-31"


def test_a_company_cost_on_a_card_left_out_moves_the_company_line_and_is_stated(client):
    moom = make_account(client, "Moom", "company")
    enter(client, moom, "0.00", "2026-06-30")
    sgd_month(client)
    no_balance(client, CARD, [("2026-08-11", "SMPL ADS", 12_345, "expense",
                               {"book": "Moom", "type": "Advertising"})])

    shown = check(client)

    assert shown["outside_minor"] == 12_345
    assert shown["unexplained_minor"] == 0


def test_a_complete_month_moves_nothing_across_the_edge(client):
    sgd_month(client)

    shown = check(client)

    assert (shown["outside_minor"], shown["left_out"], shown["unexplained_minor"]) == (0, [], 0)
