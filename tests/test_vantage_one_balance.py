"""A card split by cardholder, held as one bill (ruling 3, amended 2026-10-05):
the DBS Vantage card. Its rows stay on each cardholder's account; its one
balance, from PREVIOUS BALANCE and TOTAL, anchors and ties the card as a whole
on the main cardholder's account, and counts once in net worth.

Driven through the HTTP interface against a temporary database, with the
statement text shaped like pdfplumber's and a stand-in parser. Card numbers,
holders, merchants and figures are invented. Whole cents; owed is negative.
"""

import io

import pytest

import card_balance
import parse_dbs
import parsers
from parse_dbs import ParsedStatement, ParsedTransaction
from test_parser_balances import _read

MAIN, SUPPLEMENTARY = "Sample Infinite Card 9999", "Sample Infinite Card 9999 (OH)"


@pytest.fixture(autouse=True)
def holders(monkeypatch):
    monkeypatch.setattr(card_balance, "CARDHOLDER_ACCOUNTS", {
        ("9999", "SAMPLE HOLDER"): MAIN,
        ("9999", "OTHER HOLDER"): SUPPLEMENTARY,
    })
    monkeypatch.setattr(card_balance, "BALANCE_ACCOUNTS", {"9999": MAIN})


def statement(day: str, previous: str, blocks: list[str], total: str) -> str:
    return "\n".join([
        "DBS Credit Cards", "STATEMENT DATE", day,
        "DBS SAMPLE INFINITE CARD NO.: 0000 0000 0000 9999",
        f"PREVIOUS BALANCE {previous}",
        *blocks,
        f"TOTAL: {total}",
        f"GRAND TOTAL FOR ALL CARD ACCOUNTS: {total}",
    ])


# July: a bill payment and a transfer before the first block, both
# cardholders' blocks, a fee and its GST after the last SUB-TOTAL.
JULY = statement("15 Jul 2026", "300.00", [
    "20 JUN BILL PAYMENT - DBS INTERNET/WIRELESS 250.00 CR",
    "21 JUN INTER ACCOUNT TRANSFER 50.00 CR",
    "NEW TRANSACTIONS SAMPLE HOLDER",
    "28 JUN SAMPLE MERCHANT ONE 40.00",
    "SUB-TOTAL: 40.00",
    "NEW TRANSACTIONS OTHER HOLDER",
    "02 JUL SAMPLE MERCHANT TWO 60.00",
    "SUB-TOTAL: 60.00",
    "05 JUL SAMPLE ADMIN FEE 10.00",
    "05 JUL GST 0.90",
], "110.90")

# August: spending only, on both cardholders' accounts and the card's own.
AUGUST = statement("15 Aug 2026", "110.90", [
    "NEW TRANSACTIONS SAMPLE HOLDER",
    "20 JUL SAMPLE MERCHANT THREE 25.00",
    "SUB-TOTAL: 25.00",
    "NEW TRANSACTIONS OTHER HOLDER",
    "02 AUG SAMPLE MERCHANT FOUR 75.50",
    "SUB-TOTAL: 75.50",
    "05 AUG SAMPLE ADMIN FEE 10.00",
], "221.40")


def bring_in(client, monkeypatch, text: str) -> dict:
    statements = parse_dbs.by_card(_read(monkeypatch, parse_dbs, parse_dbs.parse_cc_statement, [text]))
    parsers._PARSERS.insert(0, {"name": "Stand-in", "ext": ".pdf",
                                "detect_fn": lambda _p: True, "parse_fn": lambda _p: statements})
    try:
        preview = client.post("/api/import/upload", data={"files": (io.BytesIO(b"x"), "s.pdf")},
                              content_type="multipart/form-data").get_json()
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    assert preview["errors"] == [], preview["errors"]
    resp = client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [{"account": g["account"], "transactions": g["transactions"],
                    "statements": g["statements"]} for g in preview["groups"]],
    })
    assert resp.status_code == 200, resp.get_json()
    return {"preview": preview, "confirm": resp.get_json()}


def rows_by_account(client) -> dict:
    out: dict = {}
    for tx in client.get("/api/transactions?per_page=100").get_json()["transactions"]:
        out.setdefault(tx["account_name"], []).append(tx["description"])
    return {name: sorted(rows) for name, rows in out.items()}


def lines(client, month: str) -> tuple[dict, dict]:
    shown = client.get(f"/api/balance-sheet?month={month}").get_json()
    return shown, {line["name"]: line for section in shown["sections"] for line in section["lines"]}


def test_the_preview_is_one_group_that_ties_with_each_row_on_its_cardholders_account(client, monkeypatch):
    statements = parse_dbs.by_card(_read(monkeypatch, parse_dbs, parse_dbs.parse_cc_statement, [JULY]))
    parsers._PARSERS.insert(0, {"name": "Stand-in", "ext": ".pdf",
                                "detect_fn": lambda _p: True, "parse_fn": lambda _p: statements})
    try:
        preview = client.post("/api/import/upload", data={"files": (io.BytesIO(b"x"), "s.pdf")},
                              content_type="multipart/form-data").get_json()
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]

    (group,) = preview["groups"]
    assert (group["account"], group["tie"]) == (MAIN, "ties")
    assert [(t["description"], t["account"]) for t in group["transactions"]] == [
        ("BILL PAYMENT - DBS INTERNET/WIRELESS", MAIN),
        ("INTER ACCOUNT TRANSFER", MAIN),
        ("SAMPLE MERCHANT ONE", MAIN),
        ("SAMPLE MERCHANT TWO", SUPPLEMENTARY),
        ("SAMPLE ADMIN FEE", MAIN),
        ("GST", MAIN),
    ]


def test_the_card_imports_its_rows_split_and_anchors_once_on_the_balance_account(client, monkeypatch):
    done = bring_in(client, monkeypatch, JULY)

    assert done["confirm"]["anchors_written"] == 1
    # The payments before the first block and the fees after the last
    # SUB-TOTAL are on the balance account; no third account is made.
    assert rows_by_account(client) == {
        MAIN: sorted(["BILL PAYMENT - DBS INTERNET/WIRELESS", "INTER ACCOUNT TRANSFER",
                      "SAMPLE MERCHANT ONE", "SAMPLE ADMIN FEE", "GST"]),
        SUPPLEMENTARY: ["SAMPLE MERCHANT TWO"],
    }
    held = {a["account_name"]: (a["date"], a["amount_minor"]) for a in client.get("/api/anchors").get_json()}
    assert held == {MAIN: ("2026-07-15", -11_090)}

    # Importing it again adds no row and no anchor.
    again = bring_in(client, monkeypatch, JULY)["confirm"]
    assert (again["transactions_saved"], again["anchors_written"]) == (0, 0)


def test_the_balance_sheet_counts_the_card_once_and_it_ties_across_both_accounts(client, monkeypatch):
    bring_in(client, monkeypatch, JULY)
    bring_in(client, monkeypatch, AUGUST)

    shown, by_name = lines(client, "2026-08")

    main, part = by_name[MAIN], by_name[SUPPLEMENTARY]
    assert main["balance_minor"] == -22_140
    # The August statement's rows on both accounts carry July's TOTAL to August's.
    assert main["check"]["status"] == "ties"
    assert (part["balance_minor"], part["in_total"], part["counted_in"]) == (None, False, main["account_id"])
    assert shown["net_worth_minor"] == -22_140
    assert SUPPLEMENTARY not in [entry["name"] for entry in shown["left_out"]]


def test_the_month_check_stays_balanced_with_the_other_cardholders_spending_in_it(client, monkeypatch):
    bring_in(client, monkeypatch, JULY)
    bring_in(client, monkeypatch, AUGUST)

    check = client.get("/api/balance-sheet?month=2026-08").get_json()["month_check"]

    assert check["unexplained_minor"] == 0
    # The rows dated in August, on the other cardholder's account and the
    # card's own (the 20 Jul row on the August statement counts in July).
    assert check["spending_minor"] == 7_550 + 1_000
    assert check["left_out"] == []


def test_the_csv_export_files_each_cardholder_on_the_same_accounts(monkeypatch):
    def row(day, description, amount):
        return ParsedTransaction(date=day, description=description, amount_minor=amount)

    main_only = ParsedStatement("credit_card", "2026-08-15", accounts=["DBS Vantage Card 1111"],
                                transactions=[row("2026-08-01", "SAMPLE MERCHANT ONE", 4_000)])
    combined = ParsedStatement("credit_card", "2026-08-15", accounts=["DBS Vantage Card 9999"],
                               transactions=[row("2026-08-01", "SAMPLE MERCHANT ONE", 4_000),
                                             row("2026-08-02", "SAMPLE MERCHANT TWO", 6_000)])

    split = parsers.handle_vantage_split([main_only, combined])

    assert {s.accounts[0]: [t.description for t in s.transactions] for s in split} == {
        MAIN: ["SAMPLE MERCHANT ONE"], SUPPLEMENTARY: ["SAMPLE MERCHANT TWO"],
    }
    assert {t.card_info for s in split for t in s.transactions} == {MAIN, SUPPLEMENTARY}
