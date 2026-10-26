"""The film in its own book (fin-redesign 23; spec part 2, cases 8 and 9;
RUL-021).

The operator creates a book "Film" on Lists › Books and a company-kind
account named exactly Film in rupees beside it; Rajeev's money is a rupee
loan. Rules state what the film's rows are: the two payees' rows are
spending in the Film book, the lender's ₹2 crore is a movement into his
loan. On the shared account (HDFC 6324) a row is not ours unless a rule
states a flow, so each of these rules states one.

The film's costs then leave Household spending and raise the household's
money in the film on the sheet, as Moom's costs do; the loan holds what is
owed.

Every name, number and figure is invented.
"""

from __future__ import annotations

import io

import pytest

import parsers
from parse_dbs import ParsedStatement, ParsedTransaction
from test_movements_and_review import spending_type

SHARED = "Sample Joint Savings 0007"
OWN = "Sample Own Savings 0003"
LOAN = "Loan from Sample Lender"

# Whole paise, positive for money out.
LENDER_IN = ("2026-09-02", "RTGS CR SAMPLE LENDER", -2000000000)       # ₹2 crore in
DIRECTOR_OUT = ("2026-09-02", "NEFT DR SAMPLE DIRECTOR", 350000000)    # ₹35 lakh
STUDIO_OUT = ("2026-09-09", "NEFT DR SAMPLE STUDIO FILMS", 500000000)  # ₹50 lakh
EMI = ("2026-09-05", "EMI SAMPLE HOME LOAN 0412", 5251100)             # hers
SHARED_ROWS = [LENDER_IN, DIRECTOR_OUT, STUDIO_OUT, EMI]
# The film paid from the household's own rupee account too, and a cost of
# the household's own beside it.
OWN_DIRECTOR = ("2026-09-15", "NEFT DR SAMPLE DIRECTOR", 196000000)    # ₹19.6 lakh
OWN_GROCER = ("2026-09-16", "POS SAMPLE GROCER MUMBAI", 1000000)       # ₹10,000, the household's
OWN_ROWS = [OWN_DIRECTOR, OWN_GROCER]

FILM_COSTS = DIRECTOR_OUT[2] + STUDIO_OUT[2] + OWN_DIRECTOR[2]         # ₹1.046 crore
RATE = "0.0160"                                                        # S$ a rupee in September


def bring_in(client, account: str, rows, opening: int) -> None:
    """Upload and confirm one rupee bank statement that ties."""
    closing = opening - sum(r[2] for r in rows)

    def parse(_path):
        return [ParsedStatement(
            statement_type="bank", statement_date="2026-09-30", accounts=[account],
            filename="sample.csv", currency="INR",
            transactions=[ParsedTransaction(date=d, description=w, amount_minor=a, card_info=account)
                          for d, w, a in rows],
            opening_minor=opening, closing_minor=closing,
            opening_date="2026-09-01", closing_date="2026-09-30",
        )]
    parsers._PARSERS.insert(0, {"name": "Stand-in", "ext": ".csv", "detect_fn": lambda _p: True, "parse_fn": parse})
    try:
        resp = client.post("/api/import/upload", data={"files": (io.BytesIO(b"x"), "sample.csv")},
                           content_type="multipart/form-data")
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    assert resp.status_code == 200, resp.get_json()
    preview = resp.get_json()
    assert preview["errors"] == [], preview["errors"]
    resp = client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [{"account": g["account"], "transactions": g["transactions"],
                    "statements": g.get("statements", [])} for g in preview["groups"]],
    })
    assert resp.status_code == 200, resp.get_json()


def make_account(client, name: str, kind: str, **extra) -> int:
    resp = client.post("/api/accounts", json={"name": name, "type": kind, "currency": "INR", **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def add_rule(client, pattern: str, merchant: str, book: str, type_name: str, **stated) -> dict:
    """A merchant and its rule; past rows change too, on the preview's token."""
    kind = spending_type(client, type_name)
    svc = client.post("/api/services", json={"name": merchant, "book": book, "type_id": kind})
    assert svc.status_code in (200, 201), svc.get_json()
    body = {"pattern": pattern, "service_id": svc.get_json()["id"], **stated}
    resp = client.post("/api/rules", json=body)
    shown = resp.get_json()
    if resp.status_code != 200 and "token" in (shown.get("preview") or shown):
        token = (shown.get("preview") or shown)["token"]
        resp = client.post("/api/rules", json={**body, "past": "apply", "token": token})
        shown = resp.get_json()
    assert resp.status_code == 200, shown
    return shown


@pytest.fixture
def film(client, conn) -> dict:
    """The book, its company account, the loan, the shared account and the
    household's own rupee account, with September's rows already held: the
    film's rows came in before the book existed."""
    made = {
        "shared": make_account(client, SHARED, "bank", last_four="0007"),
        "own": make_account(client, OWN, "bank", last_four="0003"),
    }
    assert client.put(f"/api/accounts/{made['shared']}/shared", json={"shared": True}).status_code == 200
    bring_in(client, SHARED, SHARED_ROWS, 3000000000)
    bring_in(client, OWN, OWN_ROWS, 500000000)
    assert client.put("/api/rates", json={"currency": "INR", "date": "2026-09-01", "rate": RATE}).status_code == 200

    resp = client.post("/api/books", json={"name": "Film", "description": "costs of the film, whichever account paid them"})
    assert resp.status_code == 201, resp.get_json()
    made["company"] = make_account(client, "Film", "company")
    made["loan"] = make_account(client, LOAN, "loan")
    return made


def household(client) -> float:
    return client.get("/api/dashboard/stat-cards?ref_month=2026-09").get_json()["household"]


def flows(conn) -> dict:
    return {r["description"] + " " + r["date"]: (r["flow_type"], r["book"], r["other_side_id"]) for r in conn.execute(
        "SELECT description, date, flow_type, book, other_side_id FROM transactions")}


def state_the_film(client, film) -> None:
    add_rule(client, "SAMPLE DIRECTOR", "Sample Director", "Film", "People", flow="expense")
    add_rule(client, "SAMPLE STUDIO FILMS", "Sample Studio Films", "Film", "Professional services", flow="expense")
    add_rule(client, "SAMPLE LENDER", "Sample Lender", "Household", "Other",
             flow="movement", other_side_id=film["loan"])


def test_the_film_book_is_listed_and_owns_a_company_account(client, film):
    assert "Film" in [b["name"] for b in client.get("/api/books").get_json()]
    accounts = {a["name"]: a for a in client.get("/api/accounts").get_json()}
    assert (accounts["Film"]["type"], accounts["Film"]["currency"]) == ("company", "INR")
    assert (accounts[LOAN]["type"], accounts[LOAN]["currency"]) == ("loan", "INR")


def test_rules_that_state_a_flow_take_the_films_rows_out_of_not_ours_and_into_the_film(client, conn, film):
    before = flows(conn)
    assert before[f"{DIRECTOR_OUT[1]} {DIRECTOR_OUT[0]}"][0] == "not_ours"
    assert before[f"{LENDER_IN[1]} {LENDER_IN[0]}"][0] == "not_ours"

    state_the_film(client, film)

    after = flows(conn)
    assert after[f"{DIRECTOR_OUT[1]} {DIRECTOR_OUT[0]}"] == ("expense", "Film", None)
    assert after[f"{STUDIO_OUT[1]} {STUDIO_OUT[0]}"] == ("expense", "Film", None)
    assert after[f"{OWN_DIRECTOR[1]} {OWN_DIRECTOR[0]}"] == ("expense", "Film", None)
    assert after[f"{LENDER_IN[1]} {LENDER_IN[0]}"] == ("movement", None, film["loan"])
    # Hers stays hers; the household's grocer stays the household's.
    assert after[f"{EMI[1]} {EMI[0]}"][0] == "not_ours"
    assert after[f"{OWN_GROCER[1]} {OWN_GROCER[0]}"][1] in (None, "Household")


def test_the_films_costs_leave_household_spending_and_count_in_the_film_book(client, conn, film):
    conn.execute("UPDATE transactions SET flow_type = 'expense', book = 'Household' WHERE description = ?",
                 (OWN_GROCER[1],))
    conn.commit()
    # Before the rules the shared account's film rows are not ours; the one
    # paid from the household's own account is Household spending.
    with_film = household(client)

    state_the_film(client, film)

    cards = client.get("/api/dashboard/stat-cards?ref_month=2026-09").get_json()
    # Only the grocer is the household's: ₹10,000 at 0.0160.
    assert cards["household"] == pytest.approx(160.00)
    assert with_film == pytest.approx(160.00 + OWN_DIRECTOR[2] / 100 * 0.0160)
    assert cards["film"] == pytest.approx(FILM_COSTS / 100 * 0.0160)


def test_the_sheet_holds_the_households_money_in_the_film_and_what_is_owed_to_the_lender(client, film):
    # Nothing was owed the day before the money came in.
    resp = client.post("/api/anchors", json={"account_id": film["loan"], "amount": "0.00", "date": "2026-09-01"})
    assert resp.status_code in (200, 201), resp.get_json()
    state_the_film(client, film)

    shown = client.get("/api/balance-sheet?month=2026-09").get_json()
    lines = {l["name"]: l for s in shown["sections"] for l in s["lines"]}
    film_line = lines["Film"]
    assert film_line["currency"] == "INR"
    assert film_line["made_of"]["paid_for_minor"] == FILM_COSTS
    assert film_line["rows_not_counted"] == 0
    assert lines[LOAN]["balance_minor"] == LENDER_IN[2]


def test_a_rule_that_states_no_flow_leaves_the_films_row_on_the_shared_account_not_ours(client, conn, film):
    # The trap the film's rules must avoid: a rule that only labels does not
    # say the row is the household's.
    add_rule(client, "SAMPLE STUDIO FILMS", "Sample Studio Films", "Film", "Professional services")

    assert flows(conn)[f"{STUDIO_OUT[1]} {STUDIO_OUT[0]}"] == ("not_ours", None, None)


def test_the_lenders_one_row_can_be_said_by_hand_to_move_money_into_his_loan(client, conn, film):
    # When the lender's wording also matches rows of hers, the one row is
    # labelled on its own rather than by a rule.
    tx_id = conn.execute("SELECT id FROM transactions WHERE description = ?", (LENDER_IN[1],)).fetchone()[0]

    resp = client.post(f"/api/review/{tx_id}/label", json={"choice": "loan_repayment", "account_id": film["loan"]})

    assert resp.status_code == 200, resp.get_json()
    assert flows(conn)[f"{LENDER_IN[1]} {LENDER_IN[0]}"] == ("movement", None, film["loan"])
