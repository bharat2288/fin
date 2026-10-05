"""A card row belongs to the statement it printed on (ruling 1).

A card's balance on a statement's closing day is that statement's closing
balance; a row printed on a later statement but dated before an earlier one
closed (a late posting) counts in its own statement's period, so it never
moves a period already closed, the statement it printed on ties, and the
month check is not thrown off in either month. Through the HTTP interface:
statements are uploaded and confirmed through a stand-in parser. Every name
and figure is invented; whole cents; owed is negative.
"""

import io

import book_type
import parsers
from parse_dbs import ParsedStatement, ParsedTransaction
from test_balance_sheet import lines
from test_month_check import check, sheet, types

CARD = "Sample Card 0001"
LATE = "SAMPLE LATE POSTING"


def import_statement(client, rows, *, closing_date, opening=None, kind="card") -> dict:
    """Upload and confirm one statement of (date, description, cents) rows,
    each labelled household groceries. With `opening` it states its balances
    and its closing is opening less the rows; without, it states none."""
    stated = opening is not None
    closing = opening - sum(a for _, _, a in rows) if stated else None

    def parse(_path):
        return [ParsedStatement(
            statement_type=kind, statement_date=closing_date, accounts=[CARD], filename="s.csv",
            transactions=[ParsedTransaction(date=d, description=n, amount_minor=a, card_info=CARD)
                          for d, n, a in rows],
            opening_minor=opening, closing_minor=closing,
            closing_date=closing_date if stated else None,
        )]

    parsers._PARSERS.insert(0, {"name": "Stand-in", "ext": ".csv",
                                "detect_fn": lambda _p: True, "parse_fn": parse})
    try:
        resp = client.post("/api/import/upload", data={"files": (io.BytesIO(b"x"), "s.csv")},
                           content_type="multipart/form-data")
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    preview = resp.get_json()
    assert resp.status_code == 200 and preview["errors"] == [], preview
    groceries = types(client)["Groceries"]
    for group in preview["groups"]:
        for tx in group["transactions"]:
            tx.update(_skip=False, flow_type="expense", book=book_type.DEFAULT_BOOK,
                      type_id=groceries, service_id=None)
    resp = client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [{"account": g["account"], "transactions": g["transactions"],
                    "statements": g.get("statements", [])} for g in preview["groups"]],
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def three_statements(client, *, august_rows=None):
    """Statements closing 15 June, 15 July and 15 August. The August one
    printed a row dated 10 July, before the July statement closed."""
    import_statement(client, [("2026-06-02", "SAMPLE JUNE SHOP", 4_000)],
                     closing_date="2026-06-15", opening=-100_000)
    import_statement(client, [("2026-06-20", "SAMPLE CAFE", 1_500), ("2026-07-03", "SAMPLE GROCER", 2_000)],
                     closing_date="2026-07-15", opening=-104_000)
    rows = august_rows or [("2026-07-10", LATE, 5_000), ("2026-07-25", "SAMPLE BAKERY", 700),
                           ("2026-08-04", "SAMPLE PHARMACY", 1_200)]
    return import_statement(client, rows, closing_date="2026-08-15", opening=-107_500)


def test_a_late_posting_row_ties_on_the_statement_it_printed_on(client):
    three_statements(client)

    shown = lines(sheet(client, "2026-08"))[CARD]
    assert shown["check"]["status"] == "ties", shown["check"]
    # The July statement still ties too: the late row is not in its period.
    assert lines(sheet(client, "2026-07"))[CARD]["check"]["status"] == "ties"


def test_a_balance_between_statements_counts_the_late_row_inside_its_own_period(client):
    three_statements(client)

    # 30 June: the June statement's balance less the July statement's rows
    # dated after 15 June up to the 30th. The late row is not there: it
    # printed on the August statement, whose period starts on 16 July.
    assert lines(sheet(client, "2026-06"))[CARD]["balance_minor"] == -104_000 - 1_500
    # 31 July: the July statement's balance less the August rows that count
    # by then: the late row (on 16 July, the first day of its period) and
    # the row of 25 July.
    assert lines(sheet(client, "2026-07"))[CARD]["balance_minor"] == -107_500 - 5_000 - 700
    # A balance on a closing day is that statement's closing balance.
    assert lines(sheet(client, "2026-08"))[CARD]["rests_on"]["date"] == "2026-08-15"


def test_the_month_check_is_not_thrown_off_in_either_month(client):
    three_statements(client)

    july, august = check(client, "2026-07"), check(client, "2026-08")

    assert (july["unexplained_minor"], august["unexplained_minor"]) == (0, 0)
    # The late row is July's spending: it counts on 16 July.
    assert july["spending_minor"] == 2_000 + 5_000 + 700
    assert august["spending_minor"] == 1_200


def test_a_row_held_from_a_source_with_no_balance_is_filed_under_the_statement_that_printed_it(client):
    # The August rows first arrive from an export that states no balance,
    # filed by their own month; then the statement that printed them.
    import_statement(client, [("2026-06-02", "SAMPLE JUNE SHOP", 4_000)],
                     closing_date="2026-06-15", opening=-100_000)
    import_statement(client, [("2026-06-20", "SAMPLE CAFE", 1_500), ("2026-07-03", "SAMPLE GROCER", 2_000)],
                     closing_date="2026-07-15", opening=-104_000)
    august = [("2026-07-10", LATE, 5_000), ("2026-08-04", "SAMPLE PHARMACY", 1_200)]
    import_statement(client, august, closing_date="2026-08-15")

    result = import_statement(client, august, closing_date="2026-08-15", opening=-107_500)

    assert (result["transactions_saved"], result["duplicates_skipped"], result["rows_refiled"]) == (0, 2, 2)
    assert lines(sheet(client, "2026-08"))[CARD]["check"]["status"] == "ties"
    assert lines(sheet(client, "2026-07"))[CARD]["check"]["status"] == "ties"


def test_a_statement_closing_on_the_1st_never_shares_a_record_with_the_month_record(client, conn):
    # A month record for August (dated the 1st) already holds a row from a
    # source with no balance; then a statement closing on 1 August arrives:
    # the month record moves to a free day of its month, keeping its row, and
    # a later row from such a source finds it there.
    import_statement(client, [("2026-08-20", "SAMPLE AUGUST SHOP", 900)], closing_date="2026-08-31")
    import_statement(client, [("2026-07-05", "SAMPLE CAFE", 1_000)],
                     closing_date="2026-07-01", opening=-50_000)
    import_statement(client, [("2026-07-20", "SAMPLE GROCER", 2_000)],
                     closing_date="2026-08-01", opening=-51_000)
    import_statement(client, [("2026-08-25", "SAMPLE LATER SHOP", 300)], closing_date="2026-08-31")

    records = conn.execute(
        "SELECT statement_date, printed, (SELECT COUNT(*) FROM transactions t WHERE t.statement_id = s.id) "
        "FROM statements s ORDER BY statement_date"
    ).fetchall()
    assert [tuple(r) for r in records] == [
        ("2026-07-01", 1, 1),
        ("2026-08-01", 1, 1),
        ("2026-08-02", 0, 2),
    ]
    # 31 August: the 1 August balance less the month record's two rows.
    assert lines(sheet(client, "2026-08"))[CARD]["balance_minor"] == -53_000 - 900 - 300


def test_the_first_statement_after_conversion_never_reaches_into_a_closed_period(client, conn):
    # Converted history: the June rows sit on a month record, and the May and
    # June statement balances are anchored, as convert_printed_statements
    # leaves a database. No record is printed yet.
    import_statement(client, [("2026-06-02", "SAMPLE JUNE SHOP", 1_000)], closing_date="2026-06-30")
    card = conn.execute("SELECT id FROM accounts WHERE name = ?", (CARD,)).fetchone()[0]
    conn.executemany(
        "INSERT INTO anchors (account_id, date, amount, source) VALUES (?, ?, ?, 'statement')",
        [(card, "2026-05-15", -10_000), (card, "2026-06-15", -11_000)],
    )
    conn.commit()
    assert lines(sheet(client, "2026-06"))[CARD]["check"]["status"] == "ties"

    # The first printed statement: a late row dated 10 June, before the June
    # statement closed, and a row of 1 July.
    import_statement(client, [("2026-06-10", LATE, 500), ("2026-07-01", "SAMPLE GROCER", 2_000)],
                     closing_date="2026-07-15", opening=-11_000)

    assert lines(sheet(client, "2026-06"))[CARD]["check"]["status"] == "ties"
    assert lines(sheet(client, "2026-06"))[CARD]["balance_minor"] == -11_000 - 500
    assert lines(sheet(client, "2026-07"))[CARD]["check"]["status"] == "ties"
