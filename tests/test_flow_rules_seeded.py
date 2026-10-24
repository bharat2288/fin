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
    # Of the rules there before, only the spending payees' changed: each states spending now.
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
    # A rule that states spending says so of money back from its merchant too
    # (ADR-009: a rule before the classifier's refund wording).
    "FATELICIOUS SAMPLE OUTLET REFUND": ("refund", "expense"),
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
