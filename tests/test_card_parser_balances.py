"""DBS and UOB card statements hand over each card's previous balance and its
closing balance (M1: a DBS card's TOTAL, a UOB card's sub-total), so the card
ties and anchors like any statement with balances. A DBS card whose section is split by cardholder (the Vantage card;
ruling 3 pending) hands over none and is filed exactly as before.

Synthetic text shaped like pdfplumber's output: invented card numbers,
holders, merchants and figures; no PDF is on disk. Whole cents; owed is
negative once handed over.
"""

import io

import pytest

import parse_dbs
import parse_uob
import parsers
import tie
from test_parser_balances import _read

DBS_CARDS = "\n".join([
    "DBS Credit Cards",
    "Statement of Account",
    "STATEMENT DATE",
    "15 Aug 2026",
    "DBS SAMPLE VISA CARD NO.: 0000 0000 0000 1111",
    "PREVIOUS BALANCE 1,000.00",
    "20 JUL PAYMENT - DBS INTERNET/WIRELESS 1,000.00 CR",
    "25 JUL SAMPLE MERCHANT ONE 12.30",
    "USD 9.10",
    "05 AUG SAMPLE MERCHANT TWO 4.50",
    "SUB-TOTAL: 16.80",
    "TOTAL: 16.80",
    "DBS OTHER MASTERCARD CARD NO.: 0000 0000 0000 2222",
    "PREVIOUS BALANCE 50.00 CR",
    "30 JUL SAMPLE MERCHANT THREE 70.00",
    "SUB-TOTAL: 20.00",
    "TOTAL: 20.00",
    "GRAND TOTAL FOR ALL CARD ACCOUNTS: 36.80",
])

VISA = "DBS SAMPLE VISA 1111"
OTHER = "DBS OTHER MASTERCARD 2222"


def dbs(monkeypatch, text):
    return parse_dbs.by_card(_read(monkeypatch, parse_dbs, parse_dbs.parse_cc_statement, [text]))


def test_each_dbs_card_section_hands_over_its_balances_and_ties(monkeypatch):
    visa, other = dbs(monkeypatch, DBS_CARDS)

    assert (visa.accounts, other.accounts) == ([VISA], [OTHER])
    assert [t.amount_minor for t in visa.transactions] == [-100_000, 1_230, 450]
    assert visa.transactions[1].amount_foreign == 9.10
    # Owed is negative; a credit balance (CR) is money the bank holds for us.
    assert (visa.opening_minor, visa.closing_minor, visa.closing_date) == (-100_000, -1_680, "2026-08-15")
    assert (other.opening_minor, other.closing_minor) == (5_000, -2_000)
    assert tie.check(visa)["difference_minor"] == 0
    assert tie.check(other)["difference_minor"] == 0


def test_a_dbs_card_with_a_row_not_read_does_not_tie(monkeypatch):
    visa, _ = dbs(monkeypatch, DBS_CARDS.replace("05 AUG SAMPLE MERCHANT TWO 4.50\n", ""))

    with pytest.raises(tie.DoesNotTie) as excinfo:
        tie.check(visa)
    assert excinfo.value.figures["difference_minor"] == 450


def test_a_dbs_card_with_no_total_is_left_with_no_balance(monkeypatch):
    # Its SUB-TOTAL is not its closing balance, and the GRAND TOTAL after it
    # is not either.
    visa, rest = dbs(monkeypatch, DBS_CARDS.replace("\nTOTAL: 20.00\n", "\n"))

    assert visa.accounts == [VISA] and visa.closing_minor == -1_680
    assert rest.accounts == [OTHER]
    assert (rest.opening_minor, rest.closing_minor) == (None, None)
    assert tie.check(rest) is None


def test_the_dbs_parser_registry_entry_returns_one_statement_per_card(monkeypatch, tmp_path):
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(b"")
    monkeypatch.setattr(parse_dbs.pdfplumber, "open",
                        lambda _path: type("P", (), {"pages": [type("G", (), {"extract_text": lambda self: DBS_CARDS})()],
                                                      "close": lambda self: None})())

    parsed = parse_dbs.parse_statement(str(pdf))

    assert [s.accounts for s in parsed] == [[VISA], [OTHER]]


# --- a card's TOTAL, not its SUB-TOTAL, is its closing balance -------------------

# Rows print between a cardholder block's SUB-TOTAL and the card's TOTAL: an
# admin fee and its GST, a bill payment that takes the card into credit, a
# payment reversal. The TOTAL is in credit (CR).
AFTER_SUB_TOTAL = "\n".join([
    "DBS Credit Cards",
    "STATEMENT DATE",
    "15 Aug 2026",
    "DBS SAMPLE VISA CARD NO.: 0000 0000 0000 1111",
    "PREVIOUS BALANCE 200.00",
    "NEW TRANSACTIONS SAMPLE HOLDER",
    "20 JUL PAYMENT - DBS INTERNET/WIRELESS 200.00 CR",
    "25 JUL SAMPLE MERCHANT ONE 50.00",
    "SUB-TOTAL: 50.00",
    "01 AUG SAMPLE ADMIN FEE 100.00",
    "01 AUG GST 9.00",
    "05 AUG PAYMENT - DBS INTERNET/WIRELESS 300.00 CR",
    "07 AUG PAYMENT REVERSAL 20.00",
    "TOTAL: 121.00 CR",
    "GRAND TOTAL FOR ALL CARD ACCOUNTS: 121.00 CR",
])


def test_the_rows_after_a_sub_total_are_the_cards_and_it_closes_on_its_total(monkeypatch):
    (visa,) = dbs(monkeypatch, AFTER_SUB_TOTAL)

    assert [(t.description, t.amount_minor, t.card_info) for t in visa.transactions][2:] == [
        ("SAMPLE ADMIN FEE", 10_000, VISA), ("GST", 900, VISA),
        ("PAYMENT - DBS INTERNET/WIRELESS", -30_000, VISA), ("PAYMENT REVERSAL", 2_000, VISA),
    ]
    # Owed negative; the TOTAL in CR is a credit the bank holds for us.
    assert (visa.opening_minor, visa.closing_minor) == (-20_000, 12_100)
    assert tie.check(visa)["difference_minor"] == 0


def test_the_grand_total_for_all_cards_is_not_a_cards_total(monkeypatch):
    text = AFTER_SUB_TOTAL.replace("TOTAL: 121.00 CR\nGRAND", "GRAND")
    whole = _read(monkeypatch, parse_dbs, parse_dbs.parse_cc_statement, [text])

    assert [s["total"] for s in whole.card_sections] == [None]
    assert parse_dbs.by_card(whole) == [whole]


# --- a card split by cardholder: ruling 3 pending -------------------------------

SPLIT = "\n".join([
    "DBS Credit Cards",
    "STATEMENT DATE",
    "15 Aug 2026",
    "DBS SAMPLE INFINITE CARD NO.: 0000 0000 0000 9999",
    "PREVIOUS BALANCE 300.00",
    "NEW TRANSACTIONS SAMPLE HOLDER",
    "20 JUL PAYMENT - DBS INTERNET/WIRELESS 300.00 CR",
    "28 JUL SAMPLE MERCHANT ONE 40.00",
    "NEW TRANSACTIONS OTHER HOLDER",
    "02 AUG SAMPLE MERCHANT TWO 60.00",
    "SUB-TOTAL: 60.00",
    "05 AUG SAMPLE ADMIN FEE 10.00",
    "TOTAL: 110.00",
    "DBS SAMPLE VISA CARD NO.: 0000 0000 0000 1111",
    "PREVIOUS BALANCE 0.00",
    "25 JUL SAMPLE MERCHANT THREE 12.30",
    "SUB-TOTAL: 12.30",
    "TOTAL: 12.30",
    "GRAND TOTAL FOR ALL CARD ACCOUNTS: 122.30",
])
MAIN, SUPPLEMENTARY = "Sample Infinite Card 9999", "Sample Infinite Card 9999 (OH)"


@pytest.fixture
def holders(monkeypatch):
    monkeypatch.setattr(parse_dbs, "DBS_SUPPLEMENTARY_CARDHOLDER_MAP", {
        ("9999", "SAMPLE HOLDER"): MAIN,
        ("9999", "OTHER HOLDER"): SUPPLEMENTARY,
    })


def test_a_card_split_by_cardholder_hands_over_no_balance_and_is_filed_as_before(monkeypatch, holders):
    whole = _read(monkeypatch, parse_dbs, parse_dbs.parse_cc_statement, [SPLIT])
    before = [(t.date, t.description, t.amount_minor, t.card_info) for t in whole.transactions
              if t.card_info != VISA]

    visa, rest = parse_dbs.by_card(whole)

    assert visa.accounts == [VISA] and tie.check(visa)["difference_minor"] == 0
    # The split card: no balance, its rows filed by cardholder as before.
    assert (rest.opening_minor, rest.closing_minor) == (None, None)
    assert [(t.date, t.description, t.amount_minor, t.card_info) for t in rest.transactions] == before
    assert {t.card_info for t in rest.transactions} == {MAIN, SUPPLEMENTARY}
    assert VISA not in rest.accounts and {MAIN, SUPPLEMENTARY} <= set(rest.accounts)


def test_switched_to_one_bill_the_split_card_ties_on_its_previous_balance_and_total(monkeypatch, holders):
    # Ruling 3 pending: the switch is off. Switched on, every cardholder's
    # rows, and the fee after the last SUB-TOTAL, are the card's own, and the
    # card hands over one balance.
    monkeypatch.setattr(parse_dbs, "SPLIT_CARD_ONE_BALANCE", True)
    whole = _read(monkeypatch, parse_dbs, parse_dbs.parse_cc_statement, [SPLIT])

    card, visa = parse_dbs.by_card(whole)

    assert card.accounts == ["DBS SAMPLE INFINITE 9999"]
    assert [t.amount_minor for t in card.transactions] == [-30_000, 4_000, 6_000, 1_000]
    assert (card.opening_minor, card.closing_minor) == (-30_000, -11_000)
    assert tie.check(card)["difference_minor"] == 0
    assert visa.accounts == [VISA] and tie.check(visa)["difference_minor"] == 0


def test_a_statement_holding_only_the_split_card_is_returned_unchanged(monkeypatch, holders):
    only = SPLIT.split("DBS SAMPLE VISA")[0].rstrip()
    whole = _read(monkeypatch, parse_dbs, parse_dbs.parse_cc_statement, [only])

    assert parse_dbs.by_card(whole) == [whole]


# --- UOB card -------------------------------------------------------------------

UOB_CARD = "\n".join([
    "United Overseas Bank Limited",
    "Credit Card Statement Summary",
    "Statement Date 15 AUG 2026",
    "UOB PREFERRED 0000-0000-0000-3333 SAMPLE HOLDER",
    "Post Trans Description of Transaction Transaction Amount",
    "PREVIOUS BALANCE 500.00",
    "18 JUL 17 JUL PAYMT THRU E-BANK/HOMEB/CYBERB 500.00CR",
    "22 JUL 21 JUL SAMPLE MERCHANT ONE 25.40",
    "Ref No. : 00000000000000000000000",
    "01 AUG 01 AUG MEMBERSHIP FEE 196.20",
    "01 AUG 01 AUG MEMBERSHIP FEE WAIVER 196.20CR",
    "ADD UNI$ 30",
    "SUB TOTAL 25.40",
    "TOTAL BALANCE FOR UOB PREFERRED 25.40",
])


def uob(monkeypatch, text):
    return _read(monkeypatch, parse_uob, parse_uob.parse_uob_cc_pdf, [text])


def test_the_uob_card_hands_over_its_balances_and_ties(monkeypatch):
    stmt = uob(monkeypatch, UOB_CARD)

    assert (stmt.opening_minor, stmt.closing_minor, stmt.closing_date) == (-50_000, -2_540, "2026-08-15")
    assert tie.check(stmt)["difference_minor"] == 0


def test_the_uob_card_reads_a_membership_fee_and_its_waiver_as_rows(monkeypatch):
    stmt = uob(monkeypatch, UOB_CARD)

    assert [(t.description, t.amount_minor) for t in stmt.transactions][-2:] == [
        ("MEMBERSHIP FEE", 19_620), ("MEMBERSHIP FEE WAIVER", -19_620),
    ]


def test_a_uob_card_one_cent_off_its_sub_total_does_not_tie(monkeypatch):
    stmt = uob(monkeypatch, UOB_CARD.replace("SUB TOTAL 25.40", "SUB TOTAL 25.41"))

    with pytest.raises(tie.DoesNotTie) as excinfo:
        tie.check(stmt)
    assert excinfo.value.figures["difference_minor"] == 1


def test_a_uob_card_with_no_previous_balance_states_none(monkeypatch):
    stmt = uob(monkeypatch, UOB_CARD.replace("PREVIOUS BALANCE 500.00\n", ""))

    assert (stmt.opening_minor, stmt.closing_minor, stmt.closing_date) == (None, None, None)
    assert tie.check(stmt) is None


# --- through the import: each card anchors on its own statement ------------------


def test_a_dbs_card_statement_imports_and_anchors_each_card(client, monkeypatch):
    statements = dbs(monkeypatch, DBS_CARDS)
    parsers._PARSERS.insert(0, {"name": "Stand-in", "ext": ".pdf",
                                "detect_fn": lambda _p: True, "parse_fn": lambda _p: statements})
    try:
        resp = client.post("/api/import/upload", data={"files": (io.BytesIO(b"x"), "s.pdf")},
                           content_type="multipart/form-data")
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    preview = resp.get_json()
    assert preview["errors"] == []
    assert {g["account"]: g["tie"] for g in preview["groups"]} == {VISA: "ties", OTHER: "ties"}

    resp = client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [{"account": g["account"], "transactions": g["transactions"],
                    "statements": g["statements"]} for g in preview["groups"]],
    })

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["anchors_written"] == 2
    held = {a["account_name"]: (a["date"], a["amount_minor"]) for a in client.get("/api/anchors").get_json()}
    assert held == {VISA: ("2026-08-15", -1_680), OTHER: ("2026-08-15", -2_000)}


@pytest.mark.parametrize("section", [
    # a month in which the split card printed no row
    ["PREVIOUS BALANCE 500.00", "TOTAL: 500.00"],
    # a holder the map does not name: the rows fall to the card itself
    ["PREVIOUS BALANCE 500.00", "NEW TRANSACTIONS UNNAMED HOLDER",
     "02 AUG SAMPLE MERCHANT TWO 60.00", "SUB-TOTAL: 60.00", "TOTAL: 560.00"],
])
def test_the_split_card_hands_over_no_balance_whatever_its_rows(monkeypatch, holders, section):
    text = "\n".join(["DBS Credit Cards", "STATEMENT DATE", "15 Aug 2026",
                      "DBS SAMPLE INFINITE CARD NO.: 0000 0000 0000 9999", *section])
    whole = _read(monkeypatch, parse_dbs, parse_dbs.parse_cc_statement, [text])

    (only,) = parse_dbs.by_card(whole)

    assert (only.opening_minor, only.closing_minor) == (None, None)
