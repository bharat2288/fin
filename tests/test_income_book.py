"""An income kind may belong to a book, and the book's Home card nets the
income of its kinds against its costs (fin-redesign 26, part 2; fin RUL-032,
RUL-034): Rental belongs to Orange Grove, so the rent counts on Orange
Grove's card against its MCST, fire policy and mortgage interest.

A row still carries a book only when it is spending or a refund: the rent
row itself has none. The kind names the book. With no book chosen, an
income kind is the household's, as before, and no card nets it. Every name
and figure here is invented.
"""

from __future__ import annotations

import pytest

import book_type
from test_client_seams import NODE, _function, needs_node
from tests.trend_fixtures import account, row, seed_rate, statement

GROVE = "Orange Grove"
KEY = GROVE.lower()
RENT = 1_020_000       # S$10,200.00 in, the month's rent
MCST = 120_000         # S$1,200.00
FIRE = 30_000          # S$300.00
GROCER = 50_000        # S$500.00, the household's own
SALARY = 900_000       # S$9,000.00 in, the household's


def cents(shown) -> int:
    return round(float(shown) * 100)


def kind_id(conn, name: str) -> int:
    return conn.execute("SELECT id FROM types WHERE kind = ? AND name = ?", (book_type.INCOME, name)).fetchone()[0]


def add_grove(client) -> None:
    resp = client.post("/api/books", json={"name": GROVE, "description": "The rented flat and its costs"})
    assert resp.status_code == 201, resp.get_json()


def belong(client, kind: int, book):
    return client.put(f"/api/types/{kind}/book", json={"book": book})


def cards(client, month: str = "2026-09") -> dict:
    return client.get(f"/api/dashboard/stat-cards?ref_month={month}").get_json()


def latest_entry(client) -> dict:
    return client.get("/api/history").get_json()["entries"][0]


@pytest.fixture
def grove(client, conn):
    """Orange Grove's September: its rent labelled Rental by hand, its MCST
    and fire policy filed under it; a salary and a grocer of the household's."""
    add_grove(client)
    bank = statement(conn, account(conn, "Sample Bank 0002", type="bank", last_four="0002"), "2026-09")
    card = statement(conn, account(conn, "Sample Card 0001", type="card", last_four="0001"), "2026-09")
    rent = row(conn, bank, "SAMPLE TENANT RENT", -RENT, flow="review", day="2026-09-01")
    pay = row(conn, bank, "SAMPLE EMPLOYER SALARY", -SALARY, flow="review", day="2026-09-25")
    for tx, kind in ((rent, "Rental"), (pay, "Salary")):
        resp = client.post(f"/api/review/{tx}/label", json={"choice": "income", "income_kind_id": kind_id(conn, kind)})
        assert resp.status_code == 200, resp.get_json()
    row(conn, card, "SAMPLE MCST", MCST, book=GROVE, type_name="Repairs & maintenance", day="2026-09-05")
    row(conn, card, "SAMPLE FIRE POLICY", FIRE, book=GROVE, type_name="Insurance", day="2026-09-06")
    row(conn, card, "SAMPLE GROCER", GROCER, type_name="Groceries", day="2026-09-07")
    return {"rent": rent, "rental": kind_id(conn, "Rental"), "salary": kind_id(conn, "Salary")}


def test_the_books_card_shows_its_costs_the_income_of_its_kinds_and_the_net(client, conn, grove):
    assert belong(client, grove["rental"], GROVE).status_code == 200

    sept = cards(client)

    assert cents(sept[KEY]) == MCST + FIRE
    assert cents(sept[f"{KEY}_income"]) == RENT
    assert cents(sept[f"{KEY}_net"]) == RENT - MCST - FIRE
    assert sept[f"{KEY}_income_kinds"] == ["Rental"]
    # The household's spending holds none of Orange Grove's costs.
    assert cents(sept["household"]) == GROCER


def test_the_rent_row_itself_carries_no_book(client, conn, grove):
    belong(client, grove["rental"], GROVE)

    held = conn.execute("SELECT flow_type, book, type_id FROM transactions WHERE id = ?", (grove["rent"],)).fetchone()

    assert tuple(held) == ("income", None, grove["rental"])


def test_a_book_no_kind_belongs_to_nets_nothing(client, conn, grove):
    sept = cards(client)

    assert cents(sept[KEY]) == MCST + FIRE
    assert f"{KEY}_income" not in sept and f"{KEY}_net" not in sept
    assert "household_income" not in sept


def test_only_the_kinds_that_belong_count(client, conn, grove):
    belong(client, grove["rental"], GROVE)

    # The salary is the household's: it is in no book's income.
    assert cents(cards(client)[f"{KEY}_income"]) == RENT


def test_the_choice_is_one_change_undone_whole_and_the_types_list_says_it(client, conn, grove):
    belong(client, grove["rental"], GROVE)
    entry = latest_entry(client)
    assert entry["summary"] == "Chose the book an income kind belongs to"
    kinds = {t["name"]: t["book"] for t in client.get("/api/types?kind=income").get_json()}
    assert kinds["Rental"] == GROVE and kinds["Salary"] == "Household"

    assert client.post(f"/api/history/{entry['id']}/undo").status_code == 200

    assert f"{KEY}_income" not in cards(client)
    kinds = {t["name"]: t["book"] for t in client.get("/api/types?kind=income").get_json()}
    assert kinds["Rental"] == "Household"


@pytest.mark.parametrize("back", [None, "Household", ""])
def test_household_or_none_gives_the_kind_back_to_the_household(client, conn, grove, back):
    belong(client, grove["rental"], GROVE)

    resp = belong(client, grove["rental"], back)

    assert resp.status_code == 200 and resp.get_json()["book"] == "Household"
    assert conn.execute("SELECT COUNT(*) FROM income_books").fetchone()[0] == 0
    assert f"{KEY}_income" not in cards(client)


def test_choosing_the_book_it_already_belongs_to_changes_nothing(client, conn, grove):
    entries = len(client.get("/api/history").get_json()["entries"])

    resp = belong(client, grove["rental"], "Household")

    assert resp.status_code == 200 and resp.get_json()["changed"] is False
    assert len(client.get("/api/history").get_json()["entries"]) == entries


def stored(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute("SELECT type_id, book FROM income_books ORDER BY type_id")]


def test_a_refusal_names_why_and_writes_nothing(client, conn, grove):
    dining = book_type.spending_type_ids(conn)["Dining"]
    before = stored(conn)

    unknown = belong(client, grove["rental"], "Nowhere")
    spending = belong(client, dining, GROVE)
    bad = client.put(f"/api/types/{grove['rental']}/book", json={"book": 3})
    gone = belong(client, 99999, GROVE)

    assert (unknown.status_code, unknown.get_json()["error"]) == (
        400, "Nowhere is not a book; add it on Lists › Books first. Nothing was changed")
    assert (spending.status_code, spending.get_json()["error"]) == (
        400, "only an income kind belongs to a book this way; a spending type's book is its usual book. "
             "Nothing was changed")
    assert (bad.status_code, bad.get_json()["error"]) == (
        400, "send book: a book's name, or null for Household; nothing was changed")
    assert gone.status_code == 404
    assert stored(conn) == before


def test_a_book_an_income_kind_belongs_to_cannot_have_its_add_undone(client, conn):
    add_grove(client)
    added = latest_entry(client)
    belong(client, kind_id(conn, "Rental"), GROVE)

    resp = client.post(f"/api/history/{added['id']}/undo")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "something is filed under the book Orange Grove; change that first"


@needs_node
def test_the_home_card_says_the_income_and_the_net():
    import json
    import subprocess

    code = "\n".join([
        _function("esc"), _function("plural"), _function("bookIncomeLine"),
        "const money = (minor, cur) => 'S$ ' + (minor / 100).toFixed(2);",
        "const toMinor = v => Math.round(Number(v) * 100);",
        "const cards = {'orange grove_income': '10200.00', 'orange grove_net': '8700.00',"
        " 'orange grove_income_kinds': ['Rental'], 'orange grove_income_unvalued': 0};",
        "const missing = {'orange grove_income': '10200.00', 'orange grove_income_kinds': ['Rental'],"
        " 'orange grove_income_unvalued': 1};",
        "console.log(JSON.stringify([bookIncomeLine(cards, 'orange grove'), bookIncomeLine(cards, 'moom'),"
        " bookIncomeLine(missing, 'orange grove')]));",
    ])
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30,
                         check=True, encoding="utf-8")
    line, none, no_net = json.loads(out.stdout)

    assert line == "Rental in <b>S$ 10200.00</b> · net <b>S$ 8700.00</b> after its costs."
    assert none == ""
    assert no_net == "Rental in <b>S$ 10200.00</b> (1 row with no rate left out)."


# --- review findings -------------------------------------------------------------


def test_rent_on_an_account_the_household_does_not_own_is_not_counted(client, conn, grove):
    """As the card's costs are those the household's own accounts paid."""
    belong(client, grove["rental"], GROVE)
    company = statement(conn, account(conn, "Sample Moom Bank 0005", type="bank", owner="Moom"), "2026-09")
    other = row(conn, company, "SAMPLE TENANT RENT", -RENT, flow="review", day="2026-09-02")
    assert client.post(f"/api/review/{other}/label",
                       json={"choice": "income", "income_kind_id": grove["rental"]}).status_code == 200

    assert cents(cards(client)[f"{KEY}_income"]) == RENT


def test_rent_in_another_currency_counts_at_its_rate_and_is_named_without_one(client, conn, grove):
    belong(client, grove["rental"], GROVE)
    rupees = statement(conn, account(conn, "Sample Rupee Bank 0009", type="bank", currency="INR"), "2026-09")
    flat = row(conn, rupees, "SAMPLE INDIA TENANT", -5_000_000, flow="review", day="2026-09-10")  # ₹50,000
    assert client.post(f"/api/review/{flat}/label",
                       json={"choice": "income", "income_kind_id": grove["rental"]}).status_code == 200

    before = cards(client)
    assert cents(before[f"{KEY}_income"]) == RENT
    assert before[f"{KEY}_income_unvalued"] == 1

    seed_rate(conn, "INR/SGD", "2026-09-10", "0.0155")
    after = cards(client)
    assert cents(after[f"{KEY}_income"]) == RENT + 77_500   # ₹50,000 at 0.0155 is S$775.00
    assert after[f"{KEY}_income_unvalued"] == 0


def test_a_month_with_no_statement_says_no_net(client, conn, grove):
    belong(client, grove["rental"], GROVE)
    # Orange Grove's costs came from a card in June, so July's card, with no
    # statement in from it, is missing; the rent alone is no net.
    card = statement(conn, account(conn, "Sample Card 0003", type="card", last_four="0003"), "2026-06")
    row(conn, card, "SAMPLE MCST", MCST, book=GROVE, type_name="Repairs & maintenance", day="2026-06-05")
    bank = statement(conn, account(conn, "Sample Bank 0004", type="bank", last_four="0004"), "2026-07")
    rent = row(conn, bank, "SAMPLE TENANT RENT", -RENT, flow="review", day="2026-07-01")
    client.post(f"/api/review/{rent}/label", json={"choice": "income", "income_kind_id": grove["rental"]})

    july = cards(client, "2026-07")

    assert july[f"{KEY}_missing"] is True
    assert cents(july[f"{KEY}_income"]) == RENT
    assert f"{KEY}_net" not in july
