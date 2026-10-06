"""Import that ties: every row, checked against the statement, all or nothing.

Driven through the HTTP interface: a statement is uploaded, the preview read,
and the preview confirmed. The statement is a stand-in parser returning
invented rows and balances, or synthetic page text for the Citi parser. Every
name and figure is invented.

The tie, for every account kind: the opening balance less the sum of the rows
(money out positive) equals the closing balance, in whole minor units. A
balance is as the household sees it, so a card's is negative while it is owed.
"""

import io

import pytest

import parse_citi_pdf
import parsers
from parse_dbs import ParsedStatement, ParsedTransaction
from previews import store_preview
from test_parse_citi_pdf import CARD_PAGES

BANK = "Sample Bank 0002"

# Opening 52,100.00; the rows take 10,899.50 out net; closing 41,200.50.
OPENING = 5210000
CLOSING = 4120050
ROWS = [
    ("2026-08-03", "SAMPLE GROCER", 14000),
    ("2026-08-05", "TOP-UP TO PAYLAH SAMPLE WALLET", 1000000),
    ("2026-08-12", "PAYMENT TO CITI CREDIT CARD SAMPLE", 150000),
    ("2026-08-20", "SAMPLE EMPLOYER SALARY", -90000),
    ("2026-08-28", "SAMPLE CAFE", 15950),
]


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self, **_kwargs):
        return self._text


class _FakePdf:
    def __init__(self, pages):
        self.pages = [_FakePage(text) for text in pages]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def close(self):
        pass


@pytest.fixture
def stand_in():
    """Install a stand-in parser returning one statement."""
    def install(rows, opening=None, closing=None, *, account=BANK, kind="bank",
                closing_date="2026-08-31", opening_date="2026-08-01"):
        def parse(_path):
            stated = opening is not None
            return [ParsedStatement(
                statement_type=kind,
                statement_date=closing_date,
                accounts=[account],
                filename="sample.csv",
                transactions=[
                    ParsedTransaction(date=on, description=description, amount_minor=amount,
                                      card_info=account)
                    for on, description, amount in rows
                ],
                opening_minor=opening,
                closing_minor=closing,
                opening_date=opening_date if stated else None,
                closing_date=closing_date if stated else None,
            )]

        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
        parsers._PARSERS.insert(0, {
            "name": "Stand-in", "ext": ".csv", "detect_fn": lambda _path: True, "parse_fn": parse,
        })

    yield install
    parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]


def upload(client, filename: str = "sample.csv") -> dict:
    resp = client.post(
        "/api/import/upload",
        data={"files": (io.BytesIO(b"stand-in"), filename)},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def confirm(client, preview: dict):
    """Send the preview back as the screen does."""
    return client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [
            {"account": g["account"], "transactions": g["transactions"],
             "statements": g["statements"]}
            for g in preview["groups"]
        ],
    })


def held(conn) -> dict:
    """How much of everything the ledger holds."""
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("accounts", "statements", "transactions", "anchors")
    }


def stored_rows(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "SELECT date, description, amount_minor FROM transactions ORDER BY date, id"
    )]


def anchors_of(client) -> list[dict]:
    return client.get("/api/anchors").get_json()


# --- P1: a statement that ties ---------------------------------------------------


def test_a_statement_that_ties_shows_opening_rows_and_closing_marked_as_tying(client, stand_in):
    stand_in(ROWS, OPENING, CLOSING)

    preview = upload(client)

    assert preview["errors"] == []
    [group] = preview["groups"]
    assert group["tie"] == "ties"
    [line] = group["statements"]
    assert line["status"] == "ties"
    assert (line["opening_minor"], line["rows_minor"], line["closing_minor"]) == (
        5210000, -1089950, 4120050,
    )
    assert line["difference_minor"] == 0
    assert line["rows"] == 5
    assert (line["opening"], line["rows_sum"], line["closing"]) == (
        "S$ 52,100.00", "S$ -10,899.50", "S$ 41,200.50",
    )
    assert (line["opening_date"], line["closing_date"]) == ("2026-08-01", "2026-08-31")
    # Conservation, to the cent: opening plus what the rows add up to is closing.
    assert line["opening_minor"] + line["rows_minor"] == line["closing_minor"]


def test_no_row_of_a_statement_is_skipped_by_default(client, stand_in):
    stand_in(ROWS, OPENING, CLOSING)

    preview = upload(client)

    rows = preview["groups"][0]["transactions"]
    assert {"transfer", "payment"} <= {tx["flow_type"] for tx in rows}
    assert all(tx["_skip"] is False for tx in rows)
    assert preview["stats"]["skipped"] == 0
    assert preview["groups"][0]["skipped"] == 0


def test_confirming_a_statement_that_ties_imports_every_row_and_writes_its_anchor(
    client, conn, stand_in
):
    stand_in(ROWS, OPENING, CLOSING)

    resp = confirm(client, upload(client))

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert (body["transactions_saved"], body["anchors_written"]) == (5, 1)
    assert stored_rows(conn) == ROWS

    [anchor] = anchors_of(client)
    assert (anchor["account_name"], anchor["date"], anchor["amount_minor"], anchor["source"]) == (
        BANK, "2026-08-31", 4120050, "statement",
    )
    # The rows now held carry the statement's opening balance to its anchor.
    assert OPENING - sum(amount for _, _, amount in stored_rows(conn)) == anchor["amount_minor"]


def test_a_statement_with_no_rows_that_ties_writes_its_anchor(client, conn, stand_in):
    stand_in([], 120000, 120000)

    preview = upload(client)
    [group] = preview["groups"]
    assert (group["total"], group["statements"][0]["status"]) == (0, "ties")
    resp = confirm(client, preview)

    assert resp.status_code == 200, resp.get_json()
    assert held(conn)["transactions"] == 0
    assert [(a["date"], a["amount_minor"]) for a in anchors_of(client)] == [("2026-08-31", 120000)]


def test_a_card_statement_ties_and_its_amount_owed_is_anchored_negative(
    client, conn, monkeypatch
):
    """The Citi card bill through the real parser: previous balance 500.00,
    current balance 1,020.00 owed."""
    monkeypatch.setattr(parse_citi_pdf.pdfplumber, "open", lambda _path: _FakePdf(CARD_PAGES))

    preview = upload(client, "2026-06.pdf")

    assert preview["errors"] == []
    [group] = preview["groups"]
    [line] = group["statements"]
    assert (line["status"], line["opening_minor"], line["rows_minor"], line["closing_minor"]) == (
        "ties", -50000, -52000, -102000,
    )
    assert group["total"] == 5
    assert all(tx["_skip"] is False for tx in group["transactions"])

    resp = confirm(client, preview)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["transactions_saved"] == 5
    # The payment that the default skip used to leave out is held.
    assert ("2026-05-12", "PAYMENT-ATM/INTERNET", -50000) in stored_rows(conn)
    [anchor] = anchors_of(client)
    assert (anchor["date"], anchor["amount_minor"], anchor["source"], anchor["kind"]) == (
        "2026-06-05", -102000, "statement", "card",
    )
    assert -50000 - sum(amount for _, _, amount in stored_rows(conn)) == -102000


# --- P5: a statement that does not tie --------------------------------------------


@pytest.mark.parametrize("closing, difference", [(CLOSING + 1, -1), (CLOSING - 1, 1)])
def test_a_statement_off_by_one_cent_is_refused_and_nothing_is_written(
    client, conn, stand_in, closing, difference
):
    before = held(conn)
    stand_in(ROWS, OPENING, closing)

    preview = upload(client)

    assert preview["groups"] == []
    assert preview["stats"]["total"] == 0
    [refusal] = preview["errors"]
    assert refusal["file"] == "sample.csv"
    assert "do not add up to its own closing balance" in refusal["error"]
    line = refusal["tie"]
    assert line["status"] == "off"
    assert (line["opening_minor"], line["rows_minor"], line["closing_minor"]) == (
        5210000, -1089950, closing,
    )
    assert line["difference_minor"] == difference
    assert line["difference"] == "S$ 0.01"
    assert line["opening_minor"] + line["rows_minor"] - line["closing_minor"] == difference

    # There is nothing to confirm, and confirming the empty preview writes nothing.
    resp = confirm(client, preview)
    assert resp.get_json()["transactions_saved"] == 0
    assert held(conn) == before


def test_a_missed_row_shows_how_far_off_the_statement_is(client, stand_in):
    # The 140.00 grocer row was not read.
    stand_in(ROWS[1:], OPENING, CLOSING)

    preview = upload(client)

    line = preview["errors"][0]["tie"]
    assert (line["rows"], line["rows_sum"], line["difference"]) == (4, "S$ -10,759.50", "S$ 140.00")
    assert line["difference_minor"] == 14000


def test_a_refused_statement_does_not_stop_another_file_in_the_same_upload(client, stand_in):
    """Two statements in one upload: the one that ties is offered, the other refused."""
    def parse(_path):
        return [
            ParsedStatement(
                statement_type="bank", statement_date="2026-08-31", accounts=[BANK],
                filename="good.csv",
                transactions=[ParsedTransaction(date="2026-08-03", description="SAMPLE GROCER",
                                                amount_minor=14000, card_info=BANK)],
                opening_minor=100000, closing_minor=86000,
                opening_date="2026-08-01", closing_date="2026-08-31",
            ),
            ParsedStatement(
                statement_type="bank", statement_date="2026-08-31", accounts=["Sample Bank 0003"],
                filename="bad.csv",
                transactions=[ParsedTransaction(date="2026-08-04", description="SAMPLE CAFE",
                                                amount_minor=500, card_info="Sample Bank 0003")],
                opening_minor=100000, closing_minor=99000,
                opening_date="2026-08-01", closing_date="2026-08-31",
            ),
        ]

    parsers._PARSERS.insert(0, {
        "name": "Stand-in", "ext": ".csv", "detect_fn": lambda _path: True, "parse_fn": parse,
    })
    try:
        preview = upload(client)
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]

    assert [g["account"] for g in preview["groups"]] == [BANK]
    assert [e["tie"]["difference_minor"] for e in preview["errors"]] == [500]
    assert preview["errors"][0]["tie"]["account"] == "Sample Bank 0003"


def test_confirm_refuses_a_statement_whose_rows_no_longer_tie(client, conn, stand_in):
    """The screen offers no way to leave a row of such a statement out; a
    confirm that does so anyway is refused whole."""
    before = held(conn)
    stand_in(ROWS, OPENING, CLOSING)

    skipped = upload(client)
    skipped["groups"][0]["transactions"][0]["_skip"] = True
    dropped = upload(client)
    del dropped["groups"][0]["transactions"][-1]
    a_cent_out = upload(client)
    a_cent_out["groups"][0]["transactions"][0]["amount_sgd"] = 140.01
    # A row dropped or a cent changed in the stored preview: the confirm's own
    # tie check still guards the facts it writes.
    store_preview(dropped)
    store_preview(a_cent_out)

    for doctored in (skipped, dropped, a_cent_out):
        resp = confirm(client, doctored)
        assert resp.status_code == 400
        assert "do not add up to its own closing balance" in resp.get_json()["error"]
    assert held(conn) == before


@pytest.mark.parametrize("field, value", [
    ("closing_minor", 41200.5), ("closing_minor", None), ("opening_minor", "5210000"),
    ("closing_minor", True), ("closing_date", "2026-02-30"), ("closing_date", None),
])
def test_confirm_refuses_a_statement_whose_balances_are_not_whole_cents_on_a_real_day(
    client, conn, stand_in, field, value
):
    before = held(conn)
    stand_in(ROWS, OPENING, CLOSING)
    preview = upload(client)
    preview["groups"][0]["statements"][0][field] = value
    store_preview(preview)

    resp = confirm(client, preview)

    assert resp.status_code == 400
    assert held(conn) == before


def test_confirm_refuses_a_row_naming_a_statement_its_account_does_not_have(
    client, conn, stand_in
):
    before = held(conn)
    stand_in(ROWS, OPENING, CLOSING)
    preview = upload(client)
    preview["groups"][0]["transactions"][0]["statement"] = 4
    store_preview(preview)

    resp = confirm(client, preview)

    assert resp.status_code == 400
    assert held(conn) == before


# --- P9: importing again ---------------------------------------------------------


def test_importing_the_same_statement_again_adds_no_row_and_no_second_anchor(
    client, conn, stand_in
):
    stand_in(ROWS, OPENING, CLOSING)
    confirm(client, upload(client))
    before = held(conn)
    rows_before = stored_rows(conn)
    anchors_before = anchors_of(client)

    resp = confirm(client, upload(client))

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert (body["transactions_saved"], body["duplicates_skipped"], body["anchors_written"]) == (
        0, 5, 0,
    )
    assert held(conn) == before
    assert stored_rows(conn) == rows_before
    assert anchors_of(client) == anchors_before


def test_an_overlapping_statement_adds_only_its_new_rows_and_its_own_anchor(
    client, conn, stand_in
):
    stand_in(ROWS, OPENING, CLOSING)
    confirm(client, upload(client))

    # From the 20th of August to the 10th of September: two rows already held.
    later = ROWS[3:] + [("2026-09-04", "SAMPLE BOOK SHOP", 4050)]
    opening = OPENING - sum(amount for _, _, amount in ROWS[:3])
    closing = CLOSING - 4050
    stand_in(later, opening, closing, closing_date="2026-09-10", opening_date="2026-08-20")
    resp = confirm(client, upload(client))

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert (body["transactions_saved"], body["duplicates_skipped"], body["anchors_written"]) == (
        1, 2, 1,
    )
    assert stored_rows(conn) == ROWS + [("2026-09-04", "SAMPLE BOOK SHOP", 4050)]
    assert [(a["date"], a["amount_minor"]) for a in anchors_of(client)] == [
        ("2026-09-10", 4116000), ("2026-08-31", 4120050),
    ]
    # Nothing counted twice: every row held, taken from the first opening
    # balance, is the last closing balance.
    assert OPENING - sum(amount for _, _, amount in stored_rows(conn)) == 4116000


def test_a_month_already_held_gains_its_missing_rows_and_its_anchor(client, conn, stand_in):
    """A month imported before, without its transfer and its card payment and
    from a source with no balance, is imported again from its statement."""
    spending_only = [row for row in ROWS if row[1] not in (ROWS[1][1], ROWS[2][1])]
    stand_in(spending_only)
    confirm(client, upload(client))
    assert held(conn)["anchors"] == 0

    stand_in(ROWS, OPENING, CLOSING)
    resp = confirm(client, upload(client))

    body = resp.get_json()
    assert (body["transactions_saved"], body["duplicates_skipped"], body["anchors_written"]) == (
        2, 3, 1,
    )
    assert stored_rows(conn) == ROWS
    assert held(conn)["accounts"] == 1
    assert [a["amount_minor"] for a in anchors_of(client)] == [CLOSING]


def test_a_different_closing_balance_for_a_date_already_anchored_is_refused_whole(
    client, conn, stand_in
):
    stand_in(ROWS, OPENING, CLOSING)
    confirm(client, upload(client))
    before = held(conn)
    rows_before = stored_rows(conn)

    # Another reading of the same day: one more row, and so another closing balance.
    stand_in(ROWS + [("2026-08-30", "SAMPLE BAKERY", 1000)], OPENING, CLOSING - 1000)
    resp = confirm(client, upload(client))

    assert resp.status_code == 409
    error = resp.get_json()["error"]
    assert "S$ 41,200.50" in error and "S$ 41,190.50" in error and "2026-08-31" in error
    assert held(conn) == before
    assert stored_rows(conn) == rows_before
    assert [a["amount_minor"] for a in anchors_of(client)] == [CLOSING]


# --- a source with no balance ------------------------------------------------------


def test_a_source_with_no_balance_imports_as_before_and_is_not_checked(client, conn, stand_in):
    stand_in(ROWS)

    preview = upload(client)

    assert preview["errors"] == []
    [group] = preview["groups"]
    assert (group["tie"], group["statements"]) == ("not_checked", [])
    assert all(tx["statement"] is None for tx in group["transactions"])

    # Its skip control still works.
    group["transactions"][0]["_skip"] = True
    resp = confirm(client, preview)

    assert resp.status_code == 200, resp.get_json()
    assert (resp.get_json()["transactions_saved"], resp.get_json()["anchors_written"]) == (4, 0)
    assert stored_rows(conn) == ROWS[1:]
    assert anchors_of(client) == []


# --- all or nothing -----------------------------------------------------------------


def test_a_confirm_that_fails_part_way_leaves_nothing_of_the_statement(client, conn, stand_in):
    """The second row cannot be stored. The account the import made, its
    statement records, the first row and the anchor all go with it."""
    before = held(conn)
    stand_in(ROWS, OPENING, CLOSING)
    preview = upload(client)
    preview["groups"][0]["transactions"][1]["description"] = None
    store_preview(preview)

    resp = confirm(client, preview)

    assert resp.status_code == 500
    assert held(conn) == before
    assert anchors_of(client) == []
    [last] = client.get("/api/import/history").get_json()[:1]
    assert last["status"] == "failed"


def test_a_failure_in_a_later_account_takes_the_earlier_one_with_it(client, conn):
    """One confirm, two statements: the second fails, so neither is written."""
    def parse(_path):
        return [
            ParsedStatement(
                statement_type="bank", statement_date="2026-08-31", accounts=[account],
                filename="sample.csv",
                transactions=[ParsedTransaction(date="2026-08-03", description="SAMPLE GROCER",
                                                amount_minor=14000, card_info=account)],
                opening_minor=100000, closing_minor=86000,
                opening_date="2026-08-01", closing_date="2026-08-31",
            )
            for account in (BANK, "Sample Bank 0003")
        ]

    before = held(conn)
    parsers._PARSERS.insert(0, {
        "name": "Stand-in", "ext": ".csv", "detect_fn": lambda _path: True, "parse_fn": parse,
    })
    try:
        preview = upload(client)
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    assert len(preview["groups"]) == 2
    preview["groups"][1]["transactions"][0]["description"] = None
    store_preview(preview)

    resp = confirm(client, preview)

    assert resp.status_code == 500
    assert held(conn) == before


# --- Past imports keep what each statement came to (walk B10.2) ---------------


def test_past_imports_say_what_each_statement_came_to(client, stand_in):
    stand_in(ROWS, OPENING, CLOSING)
    assert confirm(client, upload(client)).status_code == 200
    stand_in(ROWS, OPENING, CLOSING + 1, closing_date="2026-09-30", opening_date="2026-09-01")
    upload(client)

    # Both land in the same second here: tell them apart by their order.
    imported, refused = sorted(client.get("/api/import/history").get_json(), key=lambda b: b["id"])

    assert imported["status"] == "committed"
    (tied,) = imported["statements"]
    assert (tied["account"], tied["date"], tied["status"], tied["difference_minor"]) == (
        BANK, "2026-08-31", "ties", 0,
    )
    assert tied["account_id"] is not None
    assert imported["result"]["transactions_saved"] == len(ROWS)
    assert refused["status"] == "preview"
    (off,) = refused["statements"]
    assert (off["status"], off["date"], off["account_id"]) == ("off", "2026-09-30", tied["account_id"])
    assert abs(off["difference_minor"]) == 1


def test_a_source_with_no_balance_is_kept_as_not_checked(client, stand_in):
    stand_in(ROWS)
    upload(client)

    (past,) = client.get("/api/import/history").get_json()

    (unchecked,) = past["statements"]
    assert (unchecked["account"], unchecked["status"], unchecked["date"]) == (BANK, "not_checked", None)


def test_a_tie_line_keeps_money_in_and_out_apart_tied_or_refused(client, stand_in):
    stand_in(ROWS, OPENING, CLOSING)
    (tied,) = upload(client)["groups"][0]["statements"]
    assert (tied["in_minor"], tied["out_minor"]) == (90000, 14000 + 1000000 + 150000 + 15950)
    assert tied["opening_minor"] + tied["in_minor"] - tied["out_minor"] == tied["closing_minor"]
    stand_in(ROWS, OPENING, CLOSING + 1)
    (refused,) = upload(client)["errors"]
    assert (refused["tie"]["in_minor"], refused["tie"]["out_minor"]) == (tied["in_minor"], tied["out_minor"])
