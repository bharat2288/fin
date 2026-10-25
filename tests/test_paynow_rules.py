"""The PayNow wordings become ordinary rules (type-relationships 05, cases 18 to 21).

The import once typed a PayNow row no rule knew from a fixed list of payee
wordings (ingest.PAYNOW_RULES, read by app._paynow_fallback_label). Each of
those wordings that names a real type (not Other) and passes fin's own
generic-pattern check is now a merchant named from the wording, with a
`contains` rule, its type and the type's usual book, made once at start: a
marker records it, so a rule the operator deletes stays deleted. Rows the
wording labelled before keep their labels and their source; the import has no
wording fallback left.

The book from before this effort is tests/fixtures/book_before_type_keys.sql
(made by fin's code at main f759090); everything else is driven through the
HTTP interface against a temporary book. Every row, figure and name here is
invented; the wordings are the ones the code held.
"""

import sqlite3

import db
import history
from test_movements_and_review import BANK, bring_in, ledger, row  # noqa: F401 (fixtures)
from test_type_keys import book_before, every_row, kept_every_row

# What the start makes of the wordings, written out by hand from the list the
# import held: (pattern, merchant, type). Book Household for every one: each
# type's usual book. Left out: the five typed Other, and AMAC, a four-letter
# wording that a `contains` rule would find inside any other word.
MADE = {
    ("CENTRAL PROVIDENT FUND BOARD", "Central Provident Fund Board", "Tax"),
    ("CPF VOLUNTARY CONTRIBUTIONS", "Cpf Voluntary Contributions", "Tax"),
    ("SINGAPORE LIFE", "Singapore Life", "Insurance"),
    ("INLAND REVENUE", "Inland Revenue", "Tax"),
    ("SINGAPORE ISLAND", "Singapore Island", "Fitness"),
    ("SICC", "Sicc", "Fitness"),
    ("SITOH SIEW KIM", "Sitoh Siew Kim", "Kids"),
    ("OSTEOPATHIC", "Osteopathic", "Medical"),
    ("TERRA MEDICAL", "Terra Medical", "Medical"),
    ("LIMITLESS WELLNESS", "Limitless Wellness", "Fitness"),
    ("VIN GOLF", "Vin Golf", "Fitness"),
    ("FATELICIOUS", "Fatelicious", "Dining"),
}
# The wordings typed Other, as patterns only; the one that was a person's name
# is not repeated here.
LEFT_OUT = {"RK", "BJT", "DREAMCORE", "LEC", "AMAC"}
# Six of them were already seed rules (db.DEFAULT_MERCHANT_RULES), so a book
# from before holds them; the start adds the other seven.
ALREADY = {"SINGAPORE LIFE", "OSTEOPATHIC", "TERRA MEDICAL", "SICC", "VIN GOLF", "FATELICIOUS"}


def rules(client) -> dict[str, dict]:
    return {r["pattern"]: r for r in client.get("/api/rules").get_json()}


def paynow_rules(path) -> set[tuple]:
    """(pattern, merchant, type, book, match) of every rule on a wording."""
    conn = sqlite3.connect(str(path))
    try:
        return {tuple(r) for r in conn.execute(
            "SELECT mr.pattern, s.name, t.name, s.book, mr.match_type FROM merchant_rules mr"
            " JOIN services s ON s.id = mr.service_id LEFT JOIN types t ON t.id = s.type_id")
            if r[0] in {p for p, *_ in MADE} | LEFT_OUT}
    finally:
        conn.close()


# --- case 18: a book from before gains the rules, once ---------------------------


def test_case_18_a_book_from_before_gains_the_wording_rules_once_and_changes_no_row(
        tmp_path, monkeypatch, client):
    path = book_before(tmp_path, monkeypatch)
    before = every_row(path)
    every = {(p, m, t, "Household", "contains") for p, m, t in MADE}
    assert paynow_rules(path) == {r for r in every if r[0] in ALREADY}

    db.init_db()
    first = every_row(path)
    db.init_db()  # a second start adds nothing

    assert every_row(path) == first
    kept_every_row(before, first)
    assert paynow_rules(path) == every
    assert len(first["merchant_rules"]) - len(before["merchant_rules"]) == len(MADE) - len(ALREADY)
    conn = sqlite3.connect(str(path))
    try:
        assert history.has_shape(conn)
    finally:
        conn.close()


def test_case_18_a_wording_rule_deleted_stays_deleted_after_a_start(client):
    wellness = rules(client)["LIMITLESS WELLNESS"]

    resp = client.delete(f"/api/rules/{wellness['id']}", json={"past": "only_new"})
    assert resp.status_code == 200, resp.get_json()
    db.init_db()

    assert "LIMITLESS WELLNESS" not in rules(client)
    assert "INLAND REVENUE" in rules(client)


# --- case 19: a wording that is Other or generic is not made a rule -------------


def test_case_19_a_generic_wording_or_one_typed_other_is_not_made_a_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PAYNOW_WORDINGS", [
        ("PAYNOW TRANSFER", "Tax"),       # fin's generic-pattern check refuses it
        ("SAMPLE PAYEE", "Other"),        # Other has no type to give
        ("SAMPLE GOLF", "Fitness"),
    ])
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fresh.db")
    db.invalidate_rules_cache()

    db.init_db()

    conn = sqlite3.connect(str(db.DB_PATH))
    try:
        held = {r[0] for r in conn.execute("SELECT pattern FROM merchant_rules")}
    finally:
        conn.close()
    assert "SAMPLE GOLF" in held
    assert not {"PAYNOW TRANSFER", "SAMPLE PAYEE"} & held


def test_case_19_the_wordings_typed_other_leave_the_code():
    assert {p for p, _ in db.PAYNOW_WORDINGS} == {p for p, *_ in MADE} - ALREADY


# --- case 20: past rows keep their labels and their source ----------------------


def test_case_20_a_row_the_wording_labelled_keeps_its_label_and_source(tmp_path, monkeypatch, client):
    path = book_before(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(path))
    try:
        fitness = conn.execute("SELECT id FROM types WHERE kind = 'spending' AND name = 'Fitness'").fetchone()[0]
        # As the import's wording fallback wrote it: no merchant, both from the wording.
        conn.execute(
            "INSERT INTO transactions (statement_id, date, description, flow_type, book, type_id,"
            " amount_minor, book_source, type_source) VALUES (1, '2026-09-24', 'PAYNOW TO LIMITLESS WELLNESS REF 77',"
            " 'expense', 'Household', ?, 8800, 'wording', 'wording')", (fitness,))
        conn.commit()
    finally:
        conn.close()
    before = every_row(path)

    db.init_db()

    kept_every_row(before, every_row(path))
    tx = row(client, "PAYNOW TO LIMITLESS WELLNESS REF 77")
    assert (tx["service_id"], tx["book"], tx["display_type"], tx["book_source"], tx["type_source"]) == (
        None, "Household", "Fitness", "wording", "wording")


def test_case_20_rows_the_wording_labelled_are_not_counted_as_drift_after_the_start(
        tmp_path, monkeypatch, client):
    """The rules' drift count leaves a field the wording gave alone, as it
    leaves a checked or chosen one: whether a rule now gives that wording or
    the wording was dropped (typed Other), the row keeps its label, and Apply
    has nothing to write (review F1)."""
    path = book_before(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(path))
    try:
        ids = {name: i for name, i in conn.execute("SELECT name, id FROM types WHERE kind = 'spending'")}
        # As the import's wording fallback wrote them: no merchant, both from the wording.
        for day, description, type_name in (
                ("2026-09-02", "PAYNOW TO CENTRAL PROVIDENT FUND BOARD REF 11", "Tax"),
                ("2026-09-03", "PAYNOW TO AMAC SERVICES REF 12", "Home"),
                ("2026-09-04", "PAYNOW TO DREAMCORE REF 13", "Other"),
                ("2026-09-05", "PAYNOW TO SAMPLE LEC REF 14", "Other")):
            conn.execute(
                "INSERT INTO transactions (statement_id, date, description, flow_type, book, type_id,"
                " amount_minor, book_source, type_source) VALUES (1, ?, ?, 'expense', 'Household', ?, 5000,"
                " 'wording', 'wording')", (day, description, ids[type_name]))
        conn.commit()
    finally:
        conn.close()

    db.init_db()

    assert client.get("/api/rules/drift").get_json()["count"] == 0
    resp = client.post("/api/rules/drift", json={"past": "apply"})
    assert resp.status_code == 200, resp.get_json()
    tx = row(client, "PAYNOW TO CENTRAL PROVIDENT FUND BOARD REF 11")
    assert (tx["service_id"], tx["display_type"], tx["type_source"]) == (None, "Tax", "wording")


# --- case 19 and review F2: a short wording is not made a rule ------------------


def test_a_card_row_with_amac_inside_a_word_is_not_labelled_home(client, ledger):
    bring_in(client, BANK, [(5, "CARD PURCHASE LAMACHI RESTAURANT SG", 42.00)])

    tx = row(client, "CARD PURCHASE LAMACHI RESTAURANT SG")
    assert tx["service_name"] != "Amac"
    assert tx["display_type"] != "Home"


# --- case 21: a new PayNow row is labelled by its rule --------------------------


def test_case_21_a_new_paynow_row_with_a_wording_is_labelled_by_its_rule(client, ledger):
    bring_in(client, BANK, [(4, "PAYNOW TO LIMITLESS WELLNESS REF 31", 120.00)])

    tx = row(client, "PAYNOW TO LIMITLESS WELLNESS REF 31")
    # A PayNow transfer waits for review (fin-redesign 06), named by its rule;
    # while it waits it is no one's spending, so it carries the merchant and
    # no book or type (fin-redesign 05). The label is the merchant's.
    assert (tx["flow_type"], tx["service_name"], tx["display_type"]) == ("review", "Limitless Wellness", "")
    assert (tx["type_id"], tx["book_source"], tx["type_source"]) == (None, None, None)
    merchant = {s["name"]: s for s in client.get("/api/services").get_json()}["Limitless Wellness"]
    assert (merchant["book"], merchant["display_type"]) == ("Household", "Fitness")


def test_case_21_with_its_rule_deleted_a_wording_no_longer_types_a_row(client, ledger):
    wellness = rules(client)["LIMITLESS WELLNESS"]
    assert client.delete(f"/api/rules/{wellness['id']}", json={"past": "only_new"}).status_code == 200

    bring_in(client, BANK, [(4, "PAYNOW TO LIMITLESS WELLNESS REF 32", 120.00)])

    tx = row(client, "PAYNOW TO LIMITLESS WELLNESS REF 32")
    assert (tx["service_id"], tx["type_id"]) == (None, None)
