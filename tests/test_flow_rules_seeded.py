"""fin-redesign 08: the hard-coded flow lists become rules seeded once (case 13, ADR-009, RUL-010).

The classifier held two lists the operator could not see or change: the
household's own-account names (flow.OWN_ALIAS_SEED), where a row naming one
was a transfer, and the loan merchants (flow.LOAN_MERCHANTS), where a row of
the merchant named exactly "Car Loan" or "UOB Home Loan" was a repayment of
its loan. They are rules now, in Lists › Rules, each stating what its rows
are: seeded once, by a marker as the default rules are, on a new book at its
first start and on a book from before by the conversion step
(convert_flow_rules.py). Maya, Shalini and Rahul Kale, and the "RK" short
forms, are not the household (RUL-010): they are not seeded, so a PayNow from
one of them waits for review like any person's. The payee wordings of six
bills the household only pays are seeded as spending, so a PayNow to one is
spending as it was before a rule stopped lifting the review hold (06).

Rows reach the book through fin's routes, as the operator's do; the parity
test reads every row of the fixture books with the classifier as it was on
main (tests/flow_before_seeds.py) and with fin's import now. Every figure is
invented; the names are the ones the code's lists held.
"""

import importlib
import sqlite3

import pytest

import app as fin_app
import conversion
import convert_account_kinds
import convert_flow_rules
import db
import money
from test_movements_and_review import BANK, CARD, bring_in, make_account, row, upload

flow_before = importlib.import_module("flow_before_seeds")

MAYA = "INWARD PAYNOW FROM MAYA KALE REF 0801"


def what(client, description: str) -> tuple:
    tx = row(client, description)
    return tx["flow_type"], tx["other_side_name"]


def rules(client) -> dict[str, dict]:
    return {r["pattern"]: r for r in client.get("/api/rules").get_json()}


@pytest.fixture
def bank(client) -> int:
    return make_account(client, BANK, "bank", last_four="0002")


# --- case 13 --------------------------------------------------------------------------


def test_case_13_a_new_paynow_from_maya_kale_waits_for_review_not_a_transfer(client, bank):
    bring_in(client, BANK, [(3, MAYA, -250.00), (4, "PAYNOW TO SURI BHARAT REF 0802", 900.00)])

    assert what(client, MAYA) == ("review", None)
    # The household's own name is still its own account, through its rule.
    assert what(client, "PAYNOW TO SURI BHARAT REF 0802") == ("transfer", None)
    assert row(client, "PAYNOW TO SURI BHARAT REF 0802")["service_name"] == "Suri Bharat"


def test_a_new_book_holds_the_flow_lists_as_rules_stating_their_flow(client):
    held = rules(client)

    assert {p: (held[p]["flow"], held[p]["min_amount"]) for p in db.OWN_ACCOUNT_RULES_PATTERNS} == {
        "SURI BHARAT": ("transfer", None),
        "MILI KALE": ("transfer", None),
        "KALESH INC": ("transfer", 0.01),
        "INDEPENDENT RESERVE": ("transfer", 0.01),
    }
    assert not {"MAYA KALE", "SHALINI KALE", "RAHUL KALE", "To: RK ", "From: RK "} & set(held)
    assert {p: held[p]["flow"] for p in db.SPENDING_PAYEES} == dict.fromkeys(db.SPENDING_PAYEES, "expense")


def test_a_deleted_flow_rule_stays_deleted_after_a_start(client, bank):
    rule = rules(client)["MILI KALE"]
    assert client.delete(f"/api/rules/{rule['id']}", json={"past": "only_new"}).status_code == 200

    db.init_db()

    assert "MILI KALE" not in rules(client)
    bring_in(client, BANK, [(5, "PAYNOW TO MILI KALE REF 0803", 120.00)])
    assert what(client, "PAYNOW TO MILI KALE REF 0803") == ("review", None)


def test_a_bill_paid_by_paynow_to_a_spending_payee_is_spending_as_before(client, bank):
    bring_in(client, BANK, [(6, "PAYNOW TO TERRA MEDICAL REF 0804", 180.00),
                            (7, "PAYNOW TO SINGAPORE LIFE REF 0805", 410.00)])

    assert what(client, "PAYNOW TO TERRA MEDICAL REF 0804") == ("expense", None)
    # A payee that also pays the household money is left to the operator.
    assert what(client, "PAYNOW TO SINGAPORE LIFE REF 0805") == ("review", None)


def test_a_name_in_the_retired_alias_variable_no_longer_makes_an_own_account(client, bank, monkeypatch):
    monkeypatch.setenv("FIN_OWN_ALIAS_OVERRIDES", "SAMPLE COUSIN;OTHER SAMPLE")

    bring_in(client, BANK, [(8, "INWARD PAYNOW FROM SAMPLE COUSIN REF 0806", -75.00)])

    assert what(client, "INWARD PAYNOW FROM SAMPLE COUSIN REF 0806") == ("review", None)


# --- a book from before: the conversion step ------------------------------------------


def book_from_before(tmp_path, monkeypatch):
    """A book as fin made it before the flow lists were rules: started with
    the seeding left out (no flow rule, no marker), with the accounts every
    conversion step before this one leaves (convert_account_kinds), as a file
    fin is pointed at."""
    path = tmp_path / "before.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    seed = db.seed_flow_rules
    monkeypatch.setattr(db, "seed_flow_rules", lambda conn: 0)
    db.invalidate_rules_cache()
    db.init_db()
    monkeypatch.setattr(db, "seed_flow_rules", seed)
    conn = sqlite3.connect(str(path))
    convert_account_kinds.STEP.apply(conn)
    conn.commit()
    conn.close()
    return path


def every_row(path) -> dict[str, list]:
    conn = sqlite3.connect(str(path))
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in tables}
    finally:
        conn.close()


def test_a_book_from_before_starts_only_once_the_flow_rules_step_has_run(tmp_path, monkeypatch):
    path = book_from_before(tmp_path, monkeypatch)

    with pytest.raises(db.DatabaseNotConverted, match="flow-rules"):
        db.init_db()

    before = every_row(path)
    report = conversion.run_step(path, convert_flow_rules.STEP)
    after = every_row(path)

    assert report["status"] == "applied" and report["backup"]
    assert after["transactions"] == before["transactions"]
    was = {r[0]: r for r in before["merchant_rules"]}
    now = {r[0]: r for r in after["merchant_rules"]}
    assert sorted(now[i][1] for i in set(now) - set(was)) == sorted(db.OWN_ACCOUNT_RULES_PATTERNS)
    # Of the rules there before, only the spending payees' changed: each states spending now, for money out.
    assert sorted(was[i][1] for i in was if was[i] != now[i]) == sorted(db.SPENDING_PAYEES)
    assert conversion.run_step(path, convert_flow_rules.STEP)["status"] == "already-applied"
    assert every_row(path) == after
    db.init_db()  # starts


# --- the parity test --------------------------------------------------------------------


# (account, day, description, amount): every list the classifier held, both
# ways where money can go both ways, on the household's bank, a card and the
# company's own bank, and rows no list knows.
COMPANY_BANK = "Sample Company Bank 0003"
ROWS = [
    (BANK, 1, "PAYNOW TO SURI BHARAT REF 0901", 900.00),
    (BANK, 2, "INWARD PAYNOW FROM SURI BHARAT REF 0902", -1200.00),
    (BANK, 3, "TRANSFER OF FUND TRF SURI BHARAT I-BANK", 300.00),
    (BANK, 4, "PAYNOW TO MILI KALE REF 0903", 450.00),
    (BANK, 5, "INWARD PAYNOW FROM MILI KALE REF 0904", -80.00),
    (BANK, 6, "FAST PAYMENT TO KALESH INC REF 0905", 2000.00),
    (BANK, 7, "INWARD PAYNOW FROM KALESH INC REF 0906", -6500.00),
    (BANK, 8, "Advice Remittance Transfer of Funds 0016RF0000001 INDEPENDENT RESERVE", 5000.00),
    (BANK, 9, "INWARD CREDIT INDEPENDENT RESERVE SG REF 0907", -42000.00),
    (BANK, 10, MAYA, -250.00),
    (BANK, 11, "INWARD PAYNOW FROM SHALINI KALE REF 0908", -300.00),
    (BANK, 12, "PAYNOW TO RAHUL KALE REF 0909", 60.00),
    (BANK, 13, "PAYNOW TRANSFER To: RK OTHER REF 0910", 40.00),
    (BANK, 14, "INWARD PAYNOW From: RK OTHER REF 0911", -40.00),
    (BANK, 15, "SAMPLE HOME LOAN INSTALMENT 09", 8800.00),
    (BANK, 16, "SAMPLE CAR LOAN INSTALMENT 09", 2000.00),
    (BANK, 17, "PAYNOW TO TERRA MEDICAL REF 0912", 180.00),
    (BANK, 18, "FAST PAYMENT TO SINGAPORE ISLAND COUNTRY CLUB", 650.00),
    (BANK, 19, "PAYNOW TO SINGAPORE LIFE REF 0913", 410.00),
    (BANK, 20, "INWARD PAYNOW FROM MANULIFE REF 0914", -1500.00),
    (BANK, 21, "GIRO SALARY MOOM PTE LTD", -9000.00),
    (BANK, 22, "INWARD PAYNOW FROM MOOM PTE LTD REF 0915", -18250.75),
    (BANK, 23, "PAYMENT TO CITI CREDIT CARD", 1500.00),
    (BANK, 24, "TOP-UP TO PAYLAH! REF 0916", 50.00),
    (BANK, 25, "PAYNOW TO SAMPLE STRANGER REF 0917", 25.00),
    (BANK, 26, "NETS SAMPLE GROCER 0042", 64.10),
    (BANK, 27, "NEFTCR-DBSS0IN0811-SURIBHARAT-SURIBHARAT-0000(0000)FAMILYEXPENSE", -1000.00),
    (BANK, 28, "NEFT CR-DBSS0IN0811-SURI BHARAT-SURI BHARAT-0001 FAMILY EXPENSE", -1000.00),
    (BANK, 29, "PAYNOW TO LIMITLESS WELLNESS REF 0920 MILI KALE", 220.00),
    (CARD, 1, "FATELICIOUS SAMPLE OUTLET", 48.00),
    (CARD, 2, "FATELICIOUS SAMPLE OUTLET REFUND", -48.00),
    (CARD, 3, "SAMPLE SHOP REFUND", -20.00),
    (CARD, 4, "SURI BHARAT SAMPLE CARD CREDIT", -10.00),
    (CARD, 5, "PAYMENT - ATM/INTERNET", -1500.00),
    (COMPANY_BANK, 1, "INWARD PAYNOW FROM KALESH INC REF 0918", -700.00),
    (COMPANY_BANK, 2, "PAYNOW TO INDEPENDENT RESERVE REF 0919", 900.00),
]

# The only rows whose flow differs, each with why: (old, new).
NAMED = {
    # RUL-010: not the household, so a person's PayNow, which waits.
    MAYA: ("transfer", "review"),
    "INWARD PAYNOW FROM SHALINI KALE REF 0908": ("transfer", "review"),
    "PAYNOW TO RAHUL KALE REF 0909": ("transfer", "review"),
    "PAYNOW TRANSFER To: RK OTHER REF 0910": ("transfer", "review"),
    "INWARD PAYNOW From: RK OTHER REF 0911": ("transfer", "review"),
    # Fin-redesign 06: a rule that states no flow labels only, and the hold
    # keeps a transfer it names (a payee that also pays the household money is
    # not seeded as spending).
    "PAYNOW TO SINGAPORE LIFE REF 0913": ("expense", "review"),
    "INWARD PAYNOW FROM MANULIFE REF 0914": ("income", "review"),
    # The company's own rule is for money out; money in naming it is the
    # company's income on the household's bank (the classifier's own rule),
    # and elsewhere money in, as any receipt.
    "INWARD PAYNOW FROM KALESH INC REF 0918": ("transfer", "income"),
    # The own-account rule comes before the classifier's rupee rules too: an
    # own remittance whose narration names the household with its spaces is a
    # transfer, not waiting; the run-together form HDFC's statements are
    # usually read in still waits.
    "NEFT CR-DBSS0IN0811-SURI BHARAT-SURI BHARAT-0001 FAMILY EXPENSE": ("review", "transfer"),
    # A longer rule wins a row over the own-account rule, as any two rules
    # matching one row go: the payee's rule states no flow, so the row waits
    # where the household's name in it once made it a transfer.
    "PAYNOW TO LIMITLESS WELLNESS REF 0920 MILI KALE": ("transfer", "review"),
}


def _old_flows(path) -> dict[str, str]:
    """Each row as the classifier on main read it at import: the rules the
    book held then (none states a flow), a row any of them labels taken off
    the review list, and the code's own lists."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        held_rules = db.load_rules(conn)
        ctx = flow_before.build_context(conn)
        out = {}
        for r in conn.execute(
            "SELECT t.description, t.amount_minor, a.type, a.owner FROM transactions t"
            " JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id"
        ):
            rule = db.pick_rule(held_rules, r["description"], r["amount_minor"])
            service_id = rule["service_id"] if rule else None
            type_id = db.rule_label(rule)[1] if rule else None
            out[r["description"]] = flow_before.classify_row({
                "description": r["description"], "amount_minor": r["amount_minor"],
                "account_kind": r["type"], "account_owner": r["owner"],
                "service_id": service_id, "labelled": bool(type_id or service_id),
            }, ctx)[0]
        return out
    finally:
        conn.close()


def _every_row(path) -> list[tuple[str, str, int, str]]:
    """(account, kind, amount in minor units, description) of every row."""
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT a.name, a.type, t.amount_minor, t.description FROM transactions t"
            " JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id"
            " ORDER BY t.id").fetchall()
    finally:
        conn.close()


def test_case_13_every_row_of_the_fixture_books_classifies_as_before_but_the_named_ones(tmp_path, monkeypatch):
    # The book as main made it: the accounts, the two loan merchants with a
    # rule each, and the rows, as the operator had them.
    path = book_from_before(tmp_path, monkeypatch)
    fin_app.app.config["TESTING"] = True
    with fin_app.app.test_client() as client:
        make_account(client, BANK, "bank", last_four="0002")
        make_account(client, CARD, "card", last_four="0001")
        make_account(client, COMPANY_BANK, "bank", last_four="0003", owner="Kalesh")
        for name, pattern in (("UOB Home Loan", "SAMPLE HOME LOAN"), ("Car Loan", "SAMPLE CAR LOAN")):
            svc = client.post("/api/services", json={"name": name}).get_json()["id"]
            assert client.post("/api/rules", json={"pattern": pattern, "service_id": svc}).status_code == 200
        for account, kind in ((BANK, "bank"), (CARD, "credit_card"), (COMPANY_BANK, "bank")):
            bring_in(client, account, [(d, desc, amt) for acc, d, desc, amt in ROWS if acc == account], kind)
        old = _old_flows(path)
        stored = _every_row(path)
        assert len(stored) == len(ROWS)

        assert conversion.run_step(path, convert_flow_rules.STEP)["status"] == "applied"
        db.invalidate_rules_cache()

        # Each row as fin's import reads it now: the same rows uploaded again,
        # in the preview (nothing is saved).
        new = {}
        for account, kind in {(a, k) for a, k, _, _ in stored}:
            preview = upload(client, account, [
                (1, desc, money.from_minor(amt, "SGD")) for a, _, amt, desc in stored if a == account
            ], "credit_card" if kind == "card" else "bank")
            for group in preview["groups"]:
                new.update({tx["description"]: tx["flow_type"] for tx in group["transactions"]})

    assert set(new) == set(old)
    differing = {d: (old[d], new[d]) for d in old if old[d] != new[d]}
    assert differing == NAMED
    # And the ones each list was for, as before.
    assert {d: new[d] for d in (
        "PAYNOW TO SURI BHARAT REF 0901", "INWARD PAYNOW FROM SURI BHARAT REF 0902",
        "FAST PAYMENT TO KALESH INC REF 0905", "INWARD PAYNOW FROM KALESH INC REF 0906",
        "Advice Remittance Transfer of Funds 0016RF0000001 INDEPENDENT RESERVE",
        "INWARD CREDIT INDEPENDENT RESERVE SG REF 0907",
        "SAMPLE HOME LOAN INSTALMENT 09", "SAMPLE CAR LOAN INSTALMENT 09",
        "PAYNOW TO TERRA MEDICAL REF 0912", "FAST PAYMENT TO SINGAPORE ISLAND COUNTRY CLUB",
    )} == {
        "PAYNOW TO SURI BHARAT REF 0901": "transfer", "INWARD PAYNOW FROM SURI BHARAT REF 0902": "transfer",
        "FAST PAYMENT TO KALESH INC REF 0905": "transfer", "INWARD PAYNOW FROM KALESH INC REF 0906": "income",
        "Advice Remittance Transfer of Funds 0016RF0000001 INDEPENDENT RESERVE": "transfer",
        "INWARD CREDIT INDEPENDENT RESERVE SG REF 0907": "movement",
        "SAMPLE HOME LOAN INSTALMENT 09": "movement", "SAMPLE CAR LOAN INSTALMENT 09": "movement",
        "PAYNOW TO TERRA MEDICAL REF 0912": "expense", "FAST PAYMENT TO SINGAPORE ISLAND COUNTRY CLUB": "expense",
    }


def test_a_seeded_own_account_rule_can_be_edited_as_the_rules_screen_sends_it(client):
    rule = rules(client)["MILI KALE"]

    # The screen sends what the rule states with every edit: a transfer, no
    # other side named, as it was seeded.
    resp = client.put(f"/api/rules/{rule['id']}", json={
        "pattern": "MILI KALE SAMPLE", "service_id": rule["service_id"], "match_type": "contains",
        "flow": "transfer", "other_side_id": None, "past": "only_new",
    })

    assert resp.status_code == 200, resp.get_json()
    assert (rules(client)["MILI KALE SAMPLE"]["flow"], rules(client)["MILI KALE SAMPLE"]["other_side_id"]) == (
        "transfer", None)
    # Saying it again with no other side, on a rule that stated none, is refused.
    other = rules(client)["NETFLIX"]
    assert client.put(f"/api/rules/{other['id']}", json={"flow": "transfer", "other_side_id": None}).status_code == 400


# --- review fixes ------------------------------------------------------------------------
# The merchant's stated flow: the flow and other side every one of a
# merchant's rules states, where it has rules, each states one, they agree and
# none is limited by amount. A loan merchant's rules say its rows repay the
# loan (the seed), so a wording taught onto it, or a row labelled with it,
# repays the loan as the row of a loan merchant did when the list was code.

INSTALMENT = "FAST PAYMENT SAMPLE HP AGREEMENT 0921"


@pytest.fixture
def car_loan(client) -> dict:
    """A household bank, the car loan and its merchant, whose one rule says
    its rows repay the loan, and an instalment no rule knows, waiting."""
    from test_movements_and_review import spending_type
    bank = make_account(client, BANK, "bank", last_four="0002")
    loan = make_account(client, "Sample auto loan", "loan")
    svc = client.post("/api/services", json={
        "name": "Car Loan (Hire Purchase)", "book": "Household", "type_id": spending_type(client, "Transport"),
    }).get_json()["id"]
    assert client.post("/api/rules", json={"pattern": "SAMPLE CAR LOAN", "service_id": svc, "flow": "movement",
                                           "other_side_id": loan}).status_code == 200
    bring_in(client, BANK, [(9, INSTALMENT, 1850.00)])
    assert what(client, INSTALMENT) == ("review", None)
    return {"bank": bank, "loan": loan, "svc": svc, "tx": row(client, INSTALMENT)["id"]}


def test_a_wording_taught_onto_a_loan_merchant_repays_the_loan(client, car_loan):
    resp = client.post("/api/transactions/resolve", json={
        "tx_id": car_loan["tx"], "service_id": car_loan["svc"], "pattern": "SAMPLE HP AGREEMENT",
        "match_type": "contains", "apply_scope": "service_default",
    })

    assert resp.status_code == 200, resp.get_json()
    assert what(client, INSTALMENT) == ("movement", "Sample auto loan")
    taught = rules(client)["SAMPLE HP AGREEMENT"]
    assert (taught["flow"], taught["other_side_id"]) == ("movement", car_loan["loan"])
    # The next instalment is the loan's at import, by the taught rule.
    bring_in(client, BANK, [(10, "FAST PAYMENT SAMPLE HP AGREEMENT 1021", 1850.00)])
    assert what(client, "FAST PAYMENT SAMPLE HP AGREEMENT 1021") == ("movement", "Sample auto loan")


def test_a_row_labelled_with_a_loan_merchant_for_itself_repays_the_loan(client, car_loan):
    resp = client.post("/api/transactions/resolve", json={
        "tx_id": car_loan["tx"], "service_id": car_loan["svc"], "apply_scope": "transaction",
    })

    assert resp.status_code == 200, resp.get_json()
    assert what(client, INSTALMENT) == ("movement", "Sample auto loan")
    assert row(client, INSTALMENT)["flow_type_manual"] == 0
    assert "SAMPLE HP AGREEMENT" not in rules(client)


def test_a_row_set_by_hand_keeps_its_flow_when_labelled_with_a_loan_merchant(client, car_loan):
    from test_movements_and_review import spending_type
    assert client.post(f"/api/review/{car_loan['tx']}/label", json={
        "choice": "spending", "type_id": spending_type(client, "Transport")}).status_code == 200

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": car_loan["tx"], "service_id": car_loan["svc"], "apply_scope": "transaction",
    })

    assert resp.status_code == 200, resp.get_json()
    assert what(client, INSTALMENT) == ("expense", None)


def test_a_merchant_whose_rules_disagree_states_no_flow_for_a_new_wording(client, car_loan):
    assert client.post("/api/rules", json={"pattern": "SAMPLE CAR FEE", "service_id": car_loan["svc"]}).status_code == 200

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": car_loan["tx"], "service_id": car_loan["svc"], "pattern": "SAMPLE HP AGREEMENT",
        "match_type": "contains", "apply_scope": "service_default",
    })

    assert resp.status_code == 200, resp.get_json()
    assert rules(client)["SAMPLE HP AGREEMENT"]["flow"] is None
    assert what(client, INSTALMENT) == ("review", None)


def test_the_seed_finds_a_loan_merchant_whose_name_goes_on_after_the_listed_one(tmp_path, monkeypatch):
    path = book_from_before(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(path))
    loans = dict(conn.execute("SELECT name, id FROM accounts WHERE type = 'loan'").fetchall())
    named = {}
    for name, pattern in (("Car Loan (Hire Purchase)", "SAMPLE HP HPR"), ("UOB Home Loan Repayment", "SAMPLE MORTGAGE"),
                          ("Car Loans Elsewhere", "SAMPLE NOT A LOAN"), ("Car Loan", "SAMPLE CAR LOAN")):
        svc = conn.execute("INSERT INTO services (name) VALUES (?)", (name,)).lastrowid
        named[pattern] = conn.execute("INSERT INTO merchant_rules (pattern, service_id, match_type) VALUES (?, ?, 'contains')",
                                      (pattern, svc)).lastrowid
    conn.commit()
    conn.close()

    conversion.run_step(path, convert_flow_rules.STEP)

    conn = sqlite3.connect(str(path))
    said = {p: tuple(conn.execute("SELECT flow, other_side_id FROM merchant_rules WHERE id = ?", (i,)).fetchone())
            for p, i in named.items()}
    conn.close()
    assert said == {
        "SAMPLE HP HPR": ("movement", loans["DBS auto loan"]),
        "SAMPLE MORTGAGE": ("movement", loans["UOB home loan"]),
        "SAMPLE NOT A LOAN": (None, None),
        "SAMPLE CAR LOAN": ("movement", loans["DBS auto loan"]),
    }


def test_the_flow_rules_step_records_nothing_into_an_entry_the_book_left_open(tmp_path, monkeypatch):
    import history
    path = book_from_before(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(path))
    entry = history.open_entry(conn, history.VIA_APP, history.APP_ACTOR)
    conn.close()

    assert conversion.run_step(path, convert_flow_rules.STEP)["status"] == "applied"

    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM change_rows WHERE entry_id = ?", (entry,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM change_entries WHERE open = 1").fetchone()[0] == 0
    finally:
        conn.close()


def test_an_edit_that_keeps_an_archived_other_side_is_refused(client):
    loan = make_account(client, "Sample old loan", "loan")
    svc = client.post("/api/services", json={"name": "Sample Lender"}).get_json()["id"]
    rule_id = client.post("/api/rules", json={"pattern": "SAMPLE LENDER", "service_id": svc, "flow": "movement",
                                              "other_side_id": loan}).get_json()["id"]
    # A book written before an account a rule names could not be archived.
    conn = db.get_connection()
    conn.execute("UPDATE accounts SET status = 'archived' WHERE id = ?", (loan,))
    conn.commit()
    conn.close()

    resp = client.put(f"/api/rules/{rule_id}", json={
        "pattern": "SAMPLE LENDER 2", "service_id": svc, "match_type": "contains",
        "flow": "movement", "other_side_id": loan, "past": "only_new",
    })

    assert resp.status_code == 400
    assert "archived" in resp.get_json()["error"]
    assert "SAMPLE LENDER" in rules(client)


def test_the_seed_states_its_flow_on_the_operators_rule_of_its_name_and_never_seeds_a_duplicate(
        tmp_path, monkeypatch, capsys):
    path = book_from_before(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(path))
    for pattern, flow_name, least in (("Mili Kale", None, None), ("suri bharat", "expense", None),
                                      ("Kalesh Inc", None, None), ("independent reserve", None, 1)):
        svc = conn.execute("INSERT INTO services (name) VALUES (?)", (f"Sample {pattern}",)).lastrowid
        conn.execute("INSERT INTO merchant_rules (pattern, service_id, match_type, flow, min_amount_minor)"
                     " VALUES (?, ?, 'contains', ?, ?)", (pattern, svc, flow_name, least))
    conn.commit()
    conn.close()

    assert convert_flow_rules.main([str(path)]) == 0

    conn = sqlite3.connect(str(path))
    held = {p: (f, m) for p, f, m in conn.execute(
        "SELECT pattern, flow, min_amount_minor FROM merchant_rules"
        " WHERE UPPER(pattern) IN ('MILI KALE', 'SURI BHARAT', 'KALESH INC', 'INDEPENDENT RESERVE')")}
    conn.close()
    assert held == {
        "Mili Kale": ("transfer", None),          # stated on the operator's rule
        "suri bharat": ("expense", None),         # it states its own: left
        "Kalesh Inc": (None, None),               # for money in too: a transfer would take the company's income
        "independent reserve": ("transfer", 1),   # for money out, as the seed is
    }
    # The printout names the seeded names it left as the operator has them.
    left = " ".join(line for line in capsys.readouterr().out.splitlines() if line.startswith("left "))
    assert "SURI BHARAT" in left and "KALESH INC" in left
    assert "MILI KALE" not in left and "INDEPENDENT RESERVE" not in left


def test_money_in_from_the_crypto_exchange_is_a_transfer_where_no_holding_takes_it(client, bank):
    bring_in(client, BANK, [(11, "INWARD CREDIT INDEPENDENT RESERVE SG REF 0922", -4200.00)])

    assert what(client, "INWARD CREDIT INDEPENDENT RESERVE SG REF 0922") == ("transfer", None)


def test_a_rule_naming_a_rows_own_account_as_its_other_side_does_not_say_what_that_row_is(client, bank):
    card = make_account(client, CARD, "card", last_four="0001")
    svc = client.post("/api/services", json={"name": "Sample Own Move"}).get_json()["id"]
    bring_in(client, BANK, [(12, "PAYNOW TO SAMPLE OWN MOVE REF 0923", 300.00)])
    bring_in(client, CARD, [(12, "SAMPLE OWN MOVE CARD CREDIT", -300.00)], "credit_card")
    body = {"pattern": "SAMPLE OWN MOVE", "service_id": svc, "flow": "transfer", "other_side_id": bank}

    preview = client.post("/api/rules", json=body).get_json()["preview"]
    assert [(f["description"], f["after"]) for f in preview.get("flows", [])] == [
        ("SAMPLE OWN MOVE CARD CREDIT", f"a transfer · {BANK}")]
    assert client.post("/api/rules", json={**body, "past": "apply", "token": preview["token"]}).status_code == 200

    assert what(client, "PAYNOW TO SAMPLE OWN MOVE REF 0923") == ("review", None)
    assert what(client, "SAMPLE OWN MOVE CARD CREDIT") == ("transfer", BANK)
    # And at import: the row on the account the rule names falls through to the classifier.
    bring_in(client, BANK, [(13, "PAYNOW TO SAMPLE OWN MOVE REF 1023", 300.00)])
    assert what(client, "PAYNOW TO SAMPLE OWN MOVE REF 1023") == ("review", None)


def test_a_row_labelled_for_itself_keeps_a_transfer_an_older_list_gave_it(client, bank):
    from test_movements_and_review import spending_type
    description = "INWARD PAYNOW FROM SAMPLE KIN REF 0924"
    bring_in(client, BANK, [(14, description, -500.00)])
    tx_id = row(client, description)["id"]
    # As the own-alias list on main stored it: a transfer fin gave, not by hand.
    conn = db.get_connection()
    conn.execute("UPDATE transactions SET flow_type = 'transfer', flow_type_manual = 0 WHERE id = ?", (tx_id,))
    conn.commit()
    conn.close()

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx_id, "service_name": "Sample kin gifts", "type_id": spending_type(client, "Transport"),
        "book": "Household", "apply_scope": "transaction",
    })

    assert resp.status_code == 200, resp.get_json()
    assert what(client, description) == ("transfer", None)
    assert row(client, description)["service_name"] == "Sample kin gifts"


def test_a_spending_payees_rule_is_for_money_out_so_its_refund_is_no_spending(client):
    held = rules(client)
    assert {p: held[p]["min_amount"] for p in db.SPENDING_PAYEES} == dict.fromkeys(db.SPENDING_PAYEES, 0.01)
    make_account(client, CARD, "card", last_four="0001")

    bring_in(client, CARD, [(15, "FATELICIOUS SAMPLE OUTLET", 48.00),
                            (16, "FATELICIOUS SAMPLE OUTLET REFUND", -48.00)], "credit_card")

    assert what(client, "FATELICIOUS SAMPLE OUTLET") == ("expense", None)
    assert what(client, "FATELICIOUS SAMPLE OUTLET REFUND") == ("refund", None)
