"""A shared account and the flow "not ours" (fin-redesign 20; spec part 2,
cases 7 and 8; RUL-020).

An account another person keeps money on too (HDFC 6324) can be marked
shared. A row on it is "not ours" unless a rule states what it is, or it
moves money from one of the household's own accounts: it counts in no
spending, income or balance. The statement tie still checks every row on
it. The sheet counts the household's part from a figure the operator
supplies, plus the household's rows after it.

Marking, or unmarking, is one change in Changes and undone whole; it moves
the rows fin worked out, never a flow set by hand.

Every name, number and figure is invented.
"""

from __future__ import annotations

import io

import pytest

import parsers
from parse_dbs import ParsedStatement, ParsedTransaction

SHARED = "Sample Joint Savings 0007"
OWN = "Sample Own Savings 0003"
LOAN = "Loan from Sample Lender"

# Whole paise, positive for money out.
LENDER_IN = ("2026-09-02", "RTGS CR SAMPLE LENDER", -2000000000)      # ours: ₹2 crore in, a rule says
STUDIO_OUT = ("2026-09-02", "NEFT DR SAMPLE STUDIO LLP", 350000000)   # ours: ₹35 lakh out, a rule says
EMI = ("2026-09-05", "EMI SAMPLE HOME LOAN 0412", 5251100)            # hers
FD = ("2026-09-07", "SAMPLE FD BOOKING", 10000000)                    # hers
FUND = ("2026-09-10", "SIP SAMPLE MUTUAL FUND", 2000000)              # hers, a rule labels it but says no flow
OWN_IN = ("2026-09-12", "IMPS XXXX0003 SAMPLE OWN", -50000000)        # ours: from the household's own account
SEPTEMBER = [LENDER_IN, STUDIO_OUT, EMI, FD, FUND, OWN_IN]
AUGUST = [("2026-08-20", "EMI SAMPLE HOME LOAN 0412", 5251100)]

AUG_OPENING = 100000000
AUG_CLOSING = AUG_OPENING - 5251100
SEP_CLOSING = AUG_CLOSING - sum(r[2] for r in SEPTEMBER)


def make_account(client, name: str, kind: str, **extra) -> int:
    resp = client.post("/api/accounts", json={"name": name, "type": kind, "currency": "INR", **extra})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def bring_in(client, rows, opening: int, closing: int, closing_date: str) -> None:
    """Upload and confirm one rupee statement on the shared account, with its
    stated balances."""
    def parse(_path):
        return [ParsedStatement(
            statement_type="bank", statement_date=closing_date, accounts=[SHARED],
            filename="sample.csv", currency="INR",
            transactions=[ParsedTransaction(date=d, description=w, amount_minor=a, card_info=SHARED)
                          for d, w, a in rows],
            opening_minor=opening, closing_minor=closing,
            opening_date=closing_date[:8] + "01", closing_date=closing_date,
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


def share(client, account_id: int, shared: bool = True):
    return client.put(f"/api/accounts/{account_id}/shared", json={"shared": shared})


def flows(conn) -> dict:
    return {r["description"]: r["flow_type"] for r in conn.execute(
        "SELECT description, flow_type FROM transactions WHERE date >= '2026-09-01'")}


HERS = {EMI[1]: "not_ours", FD[1]: "not_ours", FUND[1]: "not_ours"}
OURS = {LENDER_IN[1]: "movement", STUDIO_OUT[1]: "expense", OWN_IN[1]: "transfer"}


@pytest.fixture
def accounts(client, conn) -> dict:
    """The shared account, the household's own rupee account, the loan, and
    the rules: the lender's rows move money into the loan, the studio's are
    spending; the fund's rule labels its rows Household and states no flow."""
    from test_movements_and_review import spending_type
    made = {
        "shared": make_account(client, SHARED, "bank", last_four="0007"),
        "own": make_account(client, OWN, "bank", last_four="0003"),
        "loan": make_account(client, LOAN, "loan"),
    }
    kind = spending_type(client, "Shopping")
    for name, pattern, stated in (
        ("Sample Lender", "SAMPLE LENDER", {"flow": "movement", "other_side_id": made["loan"]}),
        ("Sample Studio", "SAMPLE STUDIO", {"flow": "expense"}),
        ("Sample Mutual Fund", "SAMPLE MUTUAL FUND", {}),
    ):
        svc = client.post("/api/services", json={"name": name, "book": "Household", "type_id": kind}).get_json()["id"]
        resp = client.post("/api/rules", json={"pattern": pattern, "service_id": svc, **stated})
        assert resp.status_code == 200, resp.get_json()
    return made


def test_rows_on_a_shared_account_are_not_ours_unless_a_rule_or_an_own_account_says_so(client, conn, accounts):
    assert share(client, accounts["shared"]).status_code == 200

    bring_in(client, SEPTEMBER, AUG_CLOSING, SEP_CLOSING, "2026-09-30")

    assert flows(conn) == {**HERS, **OURS}
    # A row that is not ours carries no book and no type.
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE flow_type = 'not_ours' "
                        "AND (book IS NOT NULL OR type_id IS NOT NULL)").fetchone()[0] == 0


def test_marking_an_account_shared_moves_the_rows_fin_worked_out_in_one_change_and_undo_puts_them_back(
        client, conn, accounts):
    bring_in(client, SEPTEMBER, AUG_CLOSING, SEP_CLOSING, "2026-09-30")
    before = flows(conn)
    assert before[EMI[1]] == "expense" and before[FUND[1]] == "expense"

    resp = share(client, accounts["shared"])

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["moved"] == 3
    assert flows(conn) == {**HERS, **OURS}
    entry = client.get("/api/history").get_json()["entries"][0]
    assert entry["summary"] == "Marked an account shared"
    assert client.post(f"/api/history/{entry['id']}/undo").status_code == 200
    assert flows(conn) == before
    assert client.get("/api/accounts").get_json()[0]["shared"] is False


def test_a_flow_set_by_hand_stays_when_the_account_is_marked_shared(client, conn, accounts):
    bring_in(client, SEPTEMBER, AUG_CLOSING, SEP_CLOSING, "2026-09-30")
    conn.execute("UPDATE transactions SET flow_type = 'income', flow_type_manual = 1 WHERE description = ?", (FD[1],))
    conn.commit()

    assert share(client, accounts["shared"]).get_json()["moved"] == 2

    assert flows(conn)[FD[1]] == "income"


def test_a_row_that_is_not_ours_counts_in_no_spending(client, conn, accounts):
    assert client.put("/api/rates", json={"currency": "INR", "date": "2026-09-01", "rate": "0.0150"}).status_code == 200
    bring_in(client, SEPTEMBER, AUG_CLOSING, SEP_CLOSING, "2026-09-30")

    def september() -> float:
        got = client.get("/api/dashboard/monthly?start=2026-09-01&end=2026-09-30").get_json()
        return round(sum(got.get("2026-09", {}).values()), 2)

    # Until the account is shared her EMI, FD and fund count as spending too.
    assert september() == pytest.approx(round((350000000 + 5251100 + 10000000 + 2000000) * 0.0150 / 100, 2))

    share(client, accounts["shared"])

    # Only the studio's ₹35 lakh: S$52,500.00.
    assert september() == pytest.approx(52500.00)


def test_the_statement_tie_still_checks_every_row_hers_included(client, conn, accounts):
    share(client, accounts["shared"])
    bring_in(client, AUGUST, AUG_OPENING, AUG_CLOSING, "2026-08-31")
    bring_in(client, SEPTEMBER, AUG_CLOSING, SEP_CLOSING, "2026-09-30")
    # The household's part on 1 Sep, between the two statements: a figure,
    # never tied to them nor they to it.
    assert client.post("/api/anchors", json={"account_id": accounts["shared"], "amount": "300000.00",
                                             "date": "2026-09-01"}).status_code == 200

    ties = {t["date"]: t for t in client.get(f"/api/accounts/{accounts['shared']}/ties").get_json()["ties"]}
    sheet_line = next(l for s in client.get("/api/balance-sheet?month=2026-09").get_json()["sections"]
                      for l in s["lines"] if l["account_id"] == accounts["shared"])

    assert ties["2026-09-30"]["status"] == "ties"
    assert ties["2026-09-30"]["rows"] == len(SEPTEMBER)
    assert ties["2026-09-30"]["opening_minor"] == AUG_CLOSING
    assert ties["2026-09-01"]["status"] == "your figure" and ties["2026-09-01"]["part"] is True
    assert sheet_line["check"]["status"] == "ties"


def test_the_sheet_counts_our_part_from_the_figure_supplied_and_our_rows_after_it(client, conn, accounts):
    share(client, accounts["shared"])
    bring_in(client, AUGUST, AUG_OPENING, AUG_CLOSING, "2026-08-31")
    bring_in(client, SEPTEMBER, AUG_CLOSING, SEP_CLOSING, "2026-09-30")

    def line() -> dict:
        shown = client.get("/api/balance-sheet?month=2026-09").get_json()
        return next(l for s in shown["sections"] for l in s["lines"] if l["account_id"] == accounts["shared"])

    # No figure for our part yet: the statement balance is the whole account's, not ours.
    assert line()["balance_minor"] is None
    assert "shared" in line()["left_out"]

    assert client.post("/api/anchors", json={"account_id": accounts["shared"], "amount": "300000.00",
                                             "date": "2026-01-01"}).status_code == 200

    # ₹3,00,000 + ₹2 crore in + ₹5 lakh in - ₹35 lakh out; her rows move nothing.
    got = line()
    assert got["balance_minor"] == 30000000 + 2000000000 + 50000000 - 350000000
    assert got["shared"] is True and got["rests_on"]["source"] == "supplied"


def test_unmarking_works_the_rows_out_again_with_the_labels_their_rules_give(client, conn, accounts):
    bring_in(client, SEPTEMBER, AUG_CLOSING, SEP_CLOSING, "2026-09-30")
    before = {r["description"]: tuple(r) for r in conn.execute(
        "SELECT description, flow_type, book, type_id FROM transactions")}
    share(client, accounts["shared"])

    resp = share(client, accounts["shared"], False)

    assert resp.status_code == 200 and resp.get_json()["moved"] == 3
    assert {r["description"]: tuple(r) for r in conn.execute(
        "SELECT description, flow_type, book, type_id FROM transactions")} == before
    assert client.get("/api/history").get_json()["entries"][0]["summary"] == "Marked an account not shared"


def test_not_ours_is_refused_on_an_account_that_is_not_shared(client, conn, accounts):
    bring_in(client, SEPTEMBER, AUG_CLOSING, SEP_CLOSING, "2026-09-30")
    tx = conn.execute("SELECT id FROM transactions WHERE description = ?", (EMI[1],)).fetchone()[0]
    entries = len(client.get("/api/history").get_json()["entries"])

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx, "service_name": "Sample Bank", "apply_scope": "transaction", "flow_type": "not_ours"})

    assert resp.status_code == 400
    assert "shared" in resp.get_json()["error"]
    assert flows(conn)[EMI[1]] == "expense"
    assert len(client.get("/api/history").get_json()["entries"]) == entries


def test_a_rule_cannot_say_its_rows_are_not_ours(client, conn, accounts):
    svc = client.post("/api/services", json={"name": "Sample Bank"}).get_json()["id"]

    resp = client.post("/api/rules", json={"pattern": "SAMPLE FD", "service_id": svc, "flow": "not_ours"})

    assert resp.status_code == 400
    assert "shared" in resp.get_json()["error"]


def test_only_a_bank_account_or_a_card_can_be_shared(client, conn, accounts):
    resp = share(client, accounts["loan"])

    assert resp.status_code == 400
    assert "bank" in resp.get_json()["error"]
    assert next(a for a in client.get("/api/accounts").get_json() if a["id"] == accounts["loan"])["shared"] is False


def test_a_shared_account_keeps_a_kind_that_can_be_shared(client, conn, accounts):
    share(client, accounts["shared"])

    resp = client.put(f"/api/accounts/{accounts['shared']}", json={"type": "holding"})

    assert resp.status_code == 400
    assert "shared" in resp.get_json()["error"]


def test_the_account_list_says_which_account_is_shared(client, conn, accounts):
    share(client, accounts["shared"])

    shown = {a["id"]: a["shared"] for a in client.get("/api/accounts").get_json()}

    assert shown[accounts["shared"]] is True and shown[accounts["own"]] is False


def test_a_body_that_says_neither_is_refused(client, conn, accounts):
    resp = client.put(f"/api/accounts/{accounts['shared']}/shared", json={"shared": "yes"})

    assert resp.status_code == 400


def test_the_account_page_charts_our_part(client, conn, accounts, monkeypatch):
    import datetime

    import account_balances_api
    monkeypatch.setattr(account_balances_api, "today", lambda: datetime.date(2026, 10, 26))
    share(client, accounts["shared"])
    bring_in(client, AUGUST, AUG_OPENING, AUG_CLOSING, "2026-08-31")
    bring_in(client, SEPTEMBER, AUG_CLOSING, SEP_CLOSING, "2026-09-30")
    assert client.post("/api/anchors", json={"account_id": accounts["shared"], "amount": "300000.00",
                                             "date": "2026-01-01"}).status_code == 200

    got = client.get(f"/api/accounts/{accounts['shared']}/balances?span=12M&to=2026-09").get_json()
    september = next(m for m in got["months"] if m["month"] == "2026-09")

    # Her EMI, FD and fund are in neither; the lender's and our own money in, the studio out.
    assert (september["in"], september["out"]) == (2000000000 + 50000000, 350000000)
    assert september["balance"] == 30000000 + 2000000000 + 50000000 - 350000000


def test_an_import_cannot_say_a_row_is_not_ours_on_an_account_that_is_not_shared(client, conn, accounts):
    def parse(_path):
        return [ParsedStatement(statement_type="bank", statement_date="2026-09-30", accounts=[OWN],
                                filename="sample.csv", currency="INR",
                                transactions=[ParsedTransaction(date=EMI[0], description=EMI[1],
                                                                amount_minor=EMI[2], card_info=OWN)])]
    parsers._PARSERS.insert(0, {"name": "Stand-in", "ext": ".csv", "detect_fn": lambda _p: True, "parse_fn": parse})
    try:
        preview = client.post("/api/import/upload", data={"files": (io.BytesIO(b"x"), "sample.csv")},
                              content_type="multipart/form-data").get_json()
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    for group in preview["groups"]:
        for tx in group["transactions"]:
            tx["flow_type"] = "not_ours"

    resp = client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [{"account": g["account"], "transactions": g["transactions"],
                    "statements": g.get("statements", [])} for g in preview["groups"]],
    })

    assert resp.status_code == 400 and "shared" in resp.get_json()["error"]
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


# --- review fixes -------------------------------------------------------------------


def test_a_row_a_record_of_expect_back_rests_on_is_kept_and_named(client, conn, accounts):
    bring_in(client, SEPTEMBER, AUG_CLOSING, SEP_CLOSING, "2026-09-30")
    rid = conn.execute("SELECT id FROM transactions WHERE description = ?", (EMI[1],)).fetchone()[0]
    conn.execute("INSERT INTO expectation (row_id, direction, amount_minor, currency) VALUES (?, 'back', 100000, 'INR')", (rid,))
    conn.commit()

    got = share(client, accounts["shared"]).get_json()

    assert [k["id"] for k in got["kept"]] == [rid] and got["moved"] == 2
    assert flows(conn)[EMI[1]] == "expense"


def test_a_transfer_waiting_for_a_label_waits_again_once_the_account_is_not_shared(client, conn, accounts):
    from test_movements_and_review import spending_type
    svc = client.post("/api/services", json={"name": "Sample Aunt", "book": "Household",
                                              "type_id": spending_type(client, "Shopping")}).get_json()["id"]
    assert client.post("/api/rules", json={"pattern": "SAMPLE AUNT", "service_id": svc}).status_code == 200
    row = ("2026-09-15", "PAYNOW TRANSFER SAMPLE AUNT", 300000)
    bring_in(client, [row], AUG_CLOSING, AUG_CLOSING - 300000, "2026-09-30")
    assert flows(conn)[row[1]] == "review"

    share(client, accounts["shared"])
    assert flows(conn)[row[1]] == "not_ours"
    share(client, accounts["shared"], False)

    assert flows(conn)[row[1]] == "review"


def test_our_part_never_shares_a_day_with_a_statement_balance(client, conn, accounts):
    share(client, accounts["shared"])
    bring_in(client, SEPTEMBER, AUG_CLOSING, SEP_CLOSING, "2026-09-30")

    on_closing = client.post("/api/anchors", json={"account_id": accounts["shared"], "amount": "1000",
                                                   "date": "2026-09-30"})
    made = client.post("/api/anchors", json={"account_id": accounts["shared"], "amount": "1000", "date": "2026-10-02"})
    moved = client.put(f"/api/anchors/{made.get_json()['anchor']['id']}", json={"date": "2026-10-05"})
    onto_closing = client.put(f"/api/anchors/{made.get_json()['anchor']['id']}", json={"date": "2026-09-30"})

    assert on_closing.status_code == 400 and "statement balance" in on_closing.get_json()["error"]
    # A figure on a shared account may be dated after its statements, on a day none closes.
    assert moved.status_code == 200, moved.get_json()
    assert onto_closing.status_code == 400


def test_a_statement_closing_on_the_day_of_our_part_is_refused_whole(client, conn, accounts):
    share(client, accounts["shared"])
    assert client.post("/api/anchors", json={"account_id": accounts["shared"], "amount": "1000",
                                             "date": "2026-09-30"}).status_code == 200
    def parse(_path):
        return [ParsedStatement(
            statement_type="bank", statement_date="2026-09-30", accounts=[SHARED], filename="sample.csv",
            currency="INR", transactions=[ParsedTransaction(date=EMI[0], description=EMI[1], amount_minor=EMI[2],
                                                             card_info=SHARED)],
            opening_minor=AUG_CLOSING, closing_minor=AUG_CLOSING - EMI[2],
            opening_date="2026-09-01", closing_date="2026-09-30")]
    parsers._PARSERS.insert(0, {"name": "Stand-in", "ext": ".csv", "detect_fn": lambda _p: True, "parse_fn": parse})
    try:
        preview = client.post("/api/import/upload", data={"files": (io.BytesIO(b"x"), "sample.csv")},
                              content_type="multipart/form-data").get_json()
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]

    resp = client.post("/api/import/confirm", json={
        "import_id": preview["import_id"],
        "groups": [{"account": g["account"], "transactions": g["transactions"],
                    "statements": g.get("statements", [])} for g in preview["groups"]],
    })

    assert resp.status_code != 200 and "household's part" in resp.get_json()["error"]
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_a_label_chosen_by_hand_moves_and_the_answer_says_so(client, conn, accounts):
    from test_movements_and_review import spending_type
    bring_in(client, SEPTEMBER, AUG_CLOSING, SEP_CLOSING, "2026-09-30")
    conn.execute("UPDATE transactions SET book = 'Household', type_id = ?, book_source = 'chosen', type_source = 'chosen' "
                 "WHERE description = ?", (spending_type(client, "Shopping"), FD[1]))
    conn.commit()

    got = share(client, accounts["shared"]).get_json()

    assert got["moved"] == 3 and got["chosen"] == 1
    assert flows(conn)[FD[1]] == "not_ours"


def test_a_card_held_as_one_balance_with_its_cardholders_cannot_be_shared(client, conn, accounts, monkeypatch):
    import card_balance
    card = make_account(client, "Sample Card 0011", "card")
    monkeypatch.setattr(card_balance, "members", lambda _conn, account_id: [account_id, account_id + 100]
                        if account_id == card else [account_id])

    resp = share(client, card)

    assert resp.status_code == 400 and "one balance" in resp.get_json()["error"]


def test_the_figure_dialog_on_a_shared_account_asks_for_our_part():
    from test_loan_terms import _const, _function, _node, needs_node  # noqa: F401
    import shutil
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    code = "\n".join([
        _const("SIGNS"), "const OWED_KINDS = ['loan'];", _function("figureLabel"), _function("figureEmptyLine"),
        "const shared = {type: 'bank', currency: 'INR', shared: true};",
        "const plain = {type: 'bank', currency: 'INR', shared: false};",
        "console.log(JSON.stringify([figureLabel(shared), figureLabel(plain), figureEmptyLine(shared)]));",
    ])
    ours, whole, empty = _node(code)

    assert ours.startswith("Our part now, in ₹")
    assert whole == "The balance now, in ₹"
    assert "our part" in empty
