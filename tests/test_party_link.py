"""Parties as a link with a role (fin-redesign 09; spec case 12, the design
evaluation's §2, RUL-011, RUL-016).

A party is a link, not a new home: the merchant row is the party and its
rules are its aliases. A merchant gains a role from the vocabulary party.py
declares; a person, company, loan, holding or untracked account gains an
optional party, a merchant. Maya's and Jaison's person accounts join one
party (two person accounts, linked, not merged: RUL-011) and the company
joins its merchant: the operator's own links, each one change, through the
app's route or the chat's link_party tool. No figure moves.

The conversion step (convert_party_link.py) gives a book from before the two
columns, empty. Driven through the Flask test client, mcp_tools.call as
mcp_server calls it, the conversion runner and the snapshot diff. Every
name, wording and figure is invented.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import book_type
import conversion
import convert_account_kinds
import convert_flow_rules
import convert_party_link
import db
import fin_snapshot
import history
import account_kind
import mcp_tools
import party
from test_balance_sheet import bring_in, make_account
from test_client_seams import PRELUDE, _const, _function, _node, needs_node

ACTOR = "chat:sample-subject"
BANK = "Sample Bank 0002"
DAY = date(2026, 10, 25)


def call(tool_name, **args):
    return mcp_tools.call(tool_name, args, actor=ACTOR)


def entries() -> list[dict]:
    return call("history", limit=200)["result"]["entries"]


def state(conn) -> tuple:
    """Every table a party link or a role can reach, as it stands."""
    return tuple(
        [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
        for table in ("transactions", "services", "accounts", "merchant_rules", "expectation")
    )


def one_change_that_undoes_exactly(conn, answer: dict, before: tuple, count_before: int, rows: int = 1) -> None:
    """The write is one change in the history, the chat's, of `rows` rows,
    and Undo puts every table back as it was."""
    assert answer["ok"], answer
    shown = entries()
    assert len(shown) == count_before + 1
    assert (shown[0]["id"], shown[0]["via"], shown[0]["actor"]) == (answer["change"]["entry_id"], "chat", ACTOR)
    assert answer["change"]["rows"] == rows
    undone = call("undo", entry_id=answer["change"]["entry_id"])
    assert undone["ok"], undone
    assert state(conn) == before


def accounts() -> dict:
    return {a["name"]: a for a in call("accounts")["result"]}


def merchants() -> dict:
    return {m["name"]: m for m in call("merchants")["result"]}


@pytest.fixture
def placed(client):
    """Two person accounts, a company account, a bank, a loan, and a
    merchant that pays the company's people."""
    ids = {
        "a": make_account(client, "Sample Friend A", "person"),
        "b": make_account(client, "Sample Friend B", "person"),
        "co": make_account(client, "Sample Co", "company"),
        "bank": make_account(client, BANK, "bank"),
        "loan": make_account(client, "Sample Home Loan", "loan"),
    }
    made = call("create_merchant", name="Sample Co Health")
    assert made["ok"], made
    ids["co_merchant"] = made["result"]["id"]
    return ids


@pytest.fixture
def people(placed):
    """And a party for the two people to join."""
    ids = dict(placed)
    made = call("create_merchant", name="Sample Friends")
    assert made["ok"], made
    ids["friends"] = made["result"]["id"]
    return ids


# --- the vocabulary -----------------------------------------------------------------


def test_the_roles_are_declared_once_with_what_each_means():
    names = [name for name, _ in party.ROLES]
    assert party.ROLE_NAMES == tuple(names)
    assert len(set(names)) == len(names)
    assert {"merchant", "employer", "lender", "person", "company", "holding"} <= set(names)
    assert all(isinstance(said, str) and said for _, said in party.ROLES)


def test_the_kinds_that_take_a_party_are_the_five_the_spec_names():
    assert party.ACCOUNT_KINDS == ("loan", "holding", "company", "person", "untracked")
    # Read from the kinds' one home: every kind whose rows are not imported statements.
    assert party.ACCOUNT_KINDS == tuple(
        k for k in account_kind.KIND_NAMES if k not in account_kind.STATEMENT_KINDS)


def test_the_chat_offers_exactly_the_declared_roles():
    for tool in ("change_merchant", "create_merchant"):
        role = mcp_tools.BY_NAME[tool].input_schema["properties"]["role"]
        assert role["enum"] == ["", *party.ROLE_NAMES], tool


# --- a merchant's role --------------------------------------------------------------


def test_the_chat_gives_a_merchant_a_role_as_one_change_that_undoes(people, conn):
    before, count = state(conn), len(entries())

    answer = call("change_merchant", service_id=people["co_merchant"], role="employer")

    assert answer["ok"], answer
    assert merchants()["Sample Co Health"]["role"] == "employer"
    assert entries()[0]["summary"] == "Changed a merchant"
    one_change_that_undoes_exactly(conn, answer, before, count)
    assert merchants()["Sample Co Health"]["role"] is None


def test_an_empty_role_says_none_again(people, conn):
    assert call("change_merchant", service_id=people["co_merchant"], role="company")["ok"]

    answer = call("change_merchant", service_id=people["co_merchant"], role="")

    assert answer["ok"], answer
    assert merchants()["Sample Co Health"]["role"] is None


def test_a_role_outside_the_vocabulary_is_refused_and_writes_nothing(people, client, conn):
    before, count = state(conn), len(entries())

    resp = client.put(f"/api/services/{people['co_merchant']}", json={"role": "landlord"})

    assert resp.status_code == 400
    assert "unknown role: 'landlord'" in resp.get_json()["error"]
    assert call("change_merchant", service_id=people["co_merchant"], role="landlord")["ok"] is False
    assert (state(conn), len(entries())) == (before, count)


def test_a_merchant_is_made_with_its_role(client, conn):
    answer = call("create_merchant", name="Sample Lender", role="lender")

    assert answer["ok"], answer
    assert merchants()["Sample Lender"]["role"] == "lender"
    resp = client.post("/api/services", json={"name": "Sample Landlord", "role": "landlord"})
    assert resp.status_code == 400 and "unknown role" in resp.get_json()["error"]
    assert "Sample Landlord" not in merchants()


# --- an account's party -------------------------------------------------------------


def test_two_person_accounts_join_one_party_each_one_change_that_undoes(people, conn):
    """RUL-011: one party, two person accounts, linked, not merged."""
    assert call("change_merchant", service_id=people["friends"], role="person")["ok"]
    before, count = state(conn), len(entries())

    first = call("link_party", account_id=people["a"], service_id=people["friends"])

    assert first["ok"], first
    assert entries()[0]["summary"] == "Joined an account to a party"
    one_change_that_undoes_exactly(conn, first, before, count)

    assert call("link_party", account_id=people["a"], service_id=people["friends"])["ok"]
    assert call("link_party", account_id=people["b"], service_id=people["friends"])["ok"]
    listed = accounts()
    assert {n: (listed[n]["party_id"], listed[n]["party"]) for n in ("Sample Friend A", "Sample Friend B")} == {
        "Sample Friend A": (people["friends"], "Sample Friends"),
        "Sample Friend B": (people["friends"], "Sample Friends"),
    }
    # Linked, not merged: two accounts still, each its own.
    assert listed["Sample Friend A"]["id"] != listed["Sample Friend B"]["id"]
    assert merchants()["Sample Friends"]["party_accounts"] == [
        {"id": people["a"], "name": "Sample Friend A", "type": "person"},
        {"id": people["b"], "name": "Sample Friend B", "type": "person"},
    ]
    assert listed[BANK]["party_id"] is None and merchants()["Sample Co Health"]["party_accounts"] == []


def test_the_company_joins_its_merchant(people, conn):
    answer = call("link_party", account_id=people["co"], service_id=people["co_merchant"])

    assert answer["ok"], answer
    assert accounts()["Sample Co"]["party_id"] == people["co_merchant"]
    assert [a["name"] for a in merchants()["Sample Co Health"]["party_accounts"]] == ["Sample Co"]


def test_without_a_merchant_the_account_leaves_its_party(people, conn):
    assert call("link_party", account_id=people["co"], service_id=people["co_merchant"])["ok"]
    before, count = state(conn), len(entries())

    answer = call("link_party", account_id=people["co"])

    assert answer["ok"], answer
    assert accounts()["Sample Co"]["party_id"] is None
    assert entries()[0]["summary"] == "Took an account off its party"
    one_change_that_undoes_exactly(conn, answer, before, count)


@pytest.mark.parametrize("body", [{}, {"merchant": 1}, [1], "1", None])
def test_a_party_body_without_service_id_is_refused_and_writes_nothing(people, client, conn, body):
    """Only a JSON object carrying service_id says what to do; null takes the
    account off its party. Anything else is refused, even for a joined account."""
    assert call("link_party", account_id=people["co"], service_id=people["co_merchant"])["ok"]
    before, count = state(conn), len(entries())

    resp = (client.put(f"/api/accounts/{people['co']}/party", data="not json", content_type="text/plain")
            if body is None else client.put(f"/api/accounts/{people['co']}/party", json=body))

    assert resp.status_code == 400, resp.get_json()
    assert "service_id" in resp.get_json()["error"]
    assert (state(conn), len(entries())) == (before, count)
    assert accounts()["Sample Co"]["party_id"] == people["co_merchant"]


@pytest.mark.parametrize("account, merchant, status, error", [
    ("bank", "friends", 400, "a bank account has no party"),
    ("a", None, 400, "no merchant 987654 in fin"),
    (None, "friends", 404, "This account is no longer in fin"),
])
def test_a_link_fin_cannot_hold_is_refused_and_writes_nothing(people, client, conn, account, merchant, status, error):
    before, count = state(conn), len(entries())
    account_id = people[account] if account else 987654
    service_id = people[merchant] if merchant else 987654

    resp = client.put(f"/api/accounts/{account_id}/party", json={"service_id": service_id})

    assert resp.status_code == status, resp.get_json()
    assert error in resp.get_json()["error"]
    answer = call("link_party", account_id=account_id, service_id=service_id)
    assert answer["ok"] is False and error in answer["error"], answer
    assert (state(conn), len(entries())) == (before, count)


def test_an_account_joined_to_a_party_cannot_become_a_bank(people, client, conn):
    assert call("link_party", account_id=people["loan"], service_id=people["co_merchant"])["ok"]
    before = state(conn)

    resp = client.put(f"/api/accounts/{people['loan']}", json={"type": "bank"})

    assert resp.status_code == 400
    assert "a bank account has no party" in resp.get_json()["error"]
    assert state(conn) == before
    # Another kind that takes a party is fine.
    assert client.put(f"/api/accounts/{people['loan']}", json={"type": "holding"}).status_code == 200


def test_merging_a_party_moves_its_accounts_to_the_target(people, client, conn):
    assert call("link_party", account_id=people["a"], service_id=people["friends"])["ok"]

    resp = client.post(f"/api/services/{people['friends']}/merge", json={"target_id": people["co_merchant"]})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["merged"]["accounts"] == 1
    assert accounts()["Sample Friend A"]["party_id"] == people["co_merchant"]


def test_a_merchant_an_account_is_joined_to_is_not_deleted(people, client, conn):
    assert call("link_party", account_id=people["co"], service_id=people["co_merchant"])["ok"]
    before = state(conn)

    resp = client.delete(f"/api/services/{people['co_merchant']}")

    assert resp.status_code == 400
    assert "Sample Co is joined to this merchant as its party" in resp.get_json()["error"]
    assert state(conn) == before


def test_undoing_the_party_merchant_after_a_link_is_refused(people, conn):
    """The merchant made, then an account joined to it: undoing the making
    would leave the account joined to nothing, so it is refused whole."""
    made = call("create_merchant", name="Sample Cousins")
    assert call("link_party", account_id=people["b"], service_id=made["result"]["id"])["ok"]
    before = state(conn)

    answer = call("undo", entry_id=made["change"]["entry_id"])

    assert answer["ok"] is False
    assert state(conn) == before


# --- case 12: the links move no number ------------------------------------------------


@pytest.fixture
def lent(client, placed):
    """A dinner paid for a friend, S$1,240 expected back, S$380 paid back:
    S$860 open."""
    bring_in(client, BANK, [
        ("2026-08-03", "SAMPLE BISTRO GROUP DINNER", 124_000),
        ("2026-08-20", "PAYNOW FROM SAMPLE FRIEND A", -38_000),
    ], closing_date="2026-08-31")
    rows = {r["amount_minor"]: r["id"] for r in client.get(
        "/api/transactions", query_string={"per_page": 50}).get_json()["transactions"]}
    marked = call("mark_expect_back", tx_id=rows[124_000], direction="back", all=True, names=[placed["a"]])
    assert marked["ok"], marked
    [item] = call("expected_back", direction="back")["result"]["items"]
    paid = call("set_off", tx_id=rows[-38_000], placements=[{"expectation_id": item["id"], "amount": "380.00"}])
    assert paid["ok"], paid
    return item["id"]


def test_case_12_the_party_links_move_no_number(placed, lent, client, temp_db):
    """What the operator does on the book: a party for the two people (there
    is no merchant for them yet), the two person accounts joined to it and the
    company joined to its merchant. Every figure stays; what the snapshot keeps
    moves by the new party merchant and by the entries that added the three
    accounts, whose undo now waits on the later links."""
    page = client.get(f"/api/expectations/{lent}").get_json()
    held = page["expectation"]
    assert (held["amount_minor"], held["open_minor"]) == (124_000, 86_000)
    added = {e["id"]: e for e in entries()}
    before = json.loads(json.dumps(fin_snapshot.snap(temp_db), sort_keys=True))

    made = call("create_merchant", name="Sample Friends", role="person")
    assert made["ok"], made
    friends = made["result"]["id"]
    for account, merchant in (("a", friends), ("b", friends), ("co", placed["co_merchant"])):
        assert call("link_party", account_id=placed[account], service_id=merchant)["ok"]

    after = json.loads(json.dumps(fin_snapshot.snap(temp_db), sort_keys=True))
    passed, result = fin_snapshot.diff(before, after)
    kept = "kept: merchants, accounts, figures, types, history"
    assert {name for name, found in result.items() if found} == {kept}, result
    # The entries that added the three accounts (make_account, in fin itself).
    made_accounts = sorted(i for i, e in added.items() if e["summary"] == "Added an account")[:3]
    assert sorted(path for path, _was, _now in result[kept]) == sorted(
        [f"merchants.{friends}", "meta.table_rows.services"] + [f"history.{i}" for i in made_accounts])
    assert dict((p, (w, n)) for p, w, n in result[kept])["meta.table_rows.services"] == (
        before["meta"]["table_rows"]["services"], before["meta"]["table_rows"]["services"] + 1)
    for i in made_accounts:
        assert client.get(f"/api/history/{i}").get_json()["blocked_by"]["summary"] == "Joined an account to a party"
    assert client.get(f"/api/expectations/{lent}").get_json() == page
    assert call("expected_back", expectation_id=lent)["result"] == page


# --- the conversion step ------------------------------------------------------------


ADDED = {"services": ("role",), "accounts": ("party_id",)}


def query(path: Path, sql: str, params=()) -> list[tuple]:
    conn = sqlite3.connect(str(path))
    try:
        return [tuple(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def dump(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def old_schema() -> str:
    """Today's schema without the two columns this step adds."""
    lines = history.without_history(db.SCHEMA_PATH.read_text()).splitlines()
    kept = []
    for line in lines:
        if line.strip().startswith(("role TEXT", "party_id INTEGER")):
            # The column before it ended in a comma for it.
            kept[-1] = kept[-1].replace(",", "", 1)
            continue
        kept.append(line)
    assert len(kept) == len(lines) - 2
    return "\n".join(kept)


@pytest.fixture
def old(tmp_path: Path) -> Path:
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(old_schema())
    for statement in history.schema_statements(leave_out=ADDED):
        conn.execute(statement)
    conn.executemany("INSERT INTO accounts (id, name, short_name, type) VALUES (?, ?, ?, ?)", [
        (1, BANK, "b", "bank"), (2, "Sample Friend A", "a", "person"), (3, "Sample Co", "c", "company")])
    conn.execute("INSERT INTO statements (id, account_id, statement_date) VALUES (1, 1, '2026-08-31')")
    conn.execute("INSERT INTO services (id, name, book) VALUES (1, 'Sample Co Health', 'Household')")
    conn.execute("INSERT INTO merchant_rules (id, pattern, service_id) VALUES (1, 'SMPL CO HEALTH', 1)")
    conn.executemany(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, service_id, flow_type)"
        " VALUES (1, ?, ?, ?, ?, ?)",
        [("2026-08-03", "SMPL CO HEALTH SALARY", -500_000, 1, "income"),
         ("2026-08-14", "PAYNOW TO SAMPLE FRIEND A", 12_000, None, "review")],
    )
    # The steps before this one have been applied.
    book_type.seed_types(conn)
    convert_account_kinds.STEP.apply(conn)
    convert_flow_rules.STEP.apply(conn)
    conn.commit()
    conn.close()
    return path


def convert(path: Path, **kwargs) -> dict:
    return conversion.run_step(path, convert_party_link.STEP, today=DAY, **kwargs)


def test_the_step_adds_role_and_party_empty_and_moves_no_row(old):
    every = {t: f"SELECT * FROM {t} ORDER BY id" for t in ("transactions", "services", "accounts", "merchant_rules")}
    before = {t: query(old, sql) for t, sql in every.items()}

    report = convert(old)

    assert report["status"] == "applied"
    assert query(old, "SELECT DISTINCT role FROM services") == [(None,)]
    assert query(old, "SELECT DISTINCT party_id FROM accounts") == [(None,)]
    after = {t: query(old, sql) for t, sql in every.items()}
    assert after["transactions"] == before["transactions"]
    assert after["merchant_rules"] == before["merchant_rules"]
    # Every row as it was, with the new column, empty, last.
    assert [r[:-1] for r in after["services"]] == before["services"]
    assert [r[:-1] for r in after["accounts"]] == before["accounts"]


def test_a_converted_book_has_the_shape_of_a_new_one(old, tmp_path):
    new = tmp_path / "new.db"
    conn = sqlite3.connect(str(new))
    conn.executescript(db.SCHEMA_PATH.read_text())
    conn.close()

    convert(old)

    for table in ADDED:
        shape = f"SELECT name, type, \"notnull\", dflt_value FROM pragma_table_info('{table}')"
        assert query(old, shape) == query(new, shape), table
        triggers = (f"SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = '{table}'"
                    " ORDER BY name")
        assert query(old, triggers) == query(new, triggers), table


def test_the_history_records_a_role_and_a_party_once_converted(old):
    convert(old)
    conn = sqlite3.connect(str(old))
    try:
        entry = history.open_entry(conn, history.VIA_APP, history.APP_ACTOR)
        conn.execute("UPDATE services SET role = 'employer' WHERE id = 1")
        conn.execute("UPDATE accounts SET party_id = 1 WHERE id = 3")
        history.close_entry(conn, entry)
        conn.commit()
        changed = {tbl: (json.loads(before), json.loads(after)) for tbl, before, after in conn.execute(
            "SELECT tbl, before, after FROM change_rows WHERE entry_id = ?", (entry,))}
    finally:
        conn.close()
    assert (changed["services"][0]["role"], changed["services"][1]["role"]) == (None, "employer")
    assert (changed["accounts"][0]["party_id"], changed["accounts"][1]["party_id"]) == (None, 1)


def test_run_twice_changes_nothing_and_writes_one_backup(old):
    convert(old)
    after_first = dump(old)

    again = convert(old)

    assert again["status"] == "already-applied"
    assert dump(old) == after_first
    assert len(list(old.parent.glob("*.bak"))) == 1


def test_it_refuses_without_its_backup(old):
    before = dump(old)

    with pytest.raises(conversion.ConversionRefused, match="backup copy is missing"):
        convert(old, backup=lambda source, target: None)

    assert dump(old) == before
    assert Path(convert(old)["backup"]).name == "ledger.db.pre-party-link-20261025.bak"


def test_the_app_will_not_start_on_a_book_from_before_the_step(old, monkeypatch):
    before = dump(old)
    monkeypatch.setattr(db, "DB_PATH", old)

    with pytest.raises(db.DatabaseNotConverted, match="convert_party_link.py"):
        db.init_db()

    assert dump(old) == before


def test_the_app_starts_once_converted(old, monkeypatch):
    convert(old)
    monkeypatch.setattr(db, "DB_PATH", old)

    db.init_db()

    assert query(old, "SELECT id, name, role FROM services WHERE id = 1") == [(1, "Sample Co Health", None)]


def test_the_step_runs_last_in_the_chain_after_flow_rules():
    names = [name for name, _ in conversion.CHAIN]
    assert names[-1] == convert_party_link.NAME
    assert names[-2] == "flow-rules"
    assert conversion.chain_step(dict(conversion.CHAIN)[convert_party_link.NAME]) is convert_party_link.STEP
    later = conversion.added_after("flow-rules")
    assert all(set(columns) <= later.get(table, set()) for table, columns in ADDED.items())


def test_the_command(old, capsys):
    assert convert_party_link.main([str(old)]) == 0
    said = capsys.readouterr().out
    assert "step party-link: applied" in said
    assert "Sample" not in said
    assert convert_party_link.main([str(old)]) == 0
    assert "step party-link: already-applied" in capsys.readouterr().out


# --- the Changes screen says a link in words -------------------------------------------


@needs_node
def test_a_link_reads_in_changes_as_the_party_by_name():
    """A change joining an account to a party names the party, as a merchant
    is named (static/app.js, run under node)."""
    code = "\n".join([
        PRELUDE, _const("FIELD_WORDS"), _const("EMPTY_WORDS"), _const("FLOW_WORDS"),
        _function("isEmpty"), _function("emptyText"), _function("valueText"),
        "const ctx = {r: {accountById: new Map(), typeById: new Map()}, services: [{id: 7, name: 'Sample Friends'}],"
        " gone: new Map([[8, 'Sample Cousins']])};",
        "console.log(JSON.stringify([FIELD_WORDS.party_id, valueText('accounts', 'party_id', 7, ctx),"
        " valueText('accounts', 'party_id', 8, ctx), valueText('accounts', 'party_id', null, ctx),"
        " emptyText('party_id'), FIELD_WORDS.role, emptyText('role')]));",
    ])
    assert _node(code) == ["party", "Sample Friends", "Sample Cousins, since removed", "", "no party", "role",
                           "no role"]


@needs_node
def test_a_link_and_an_unlink_title_their_change_in_words():
    """The Changes list titles a party change by what it did, not as
    "changed the account"."""
    code = "\n".join([
        PRELUDE, "const day = s => s; const money = v => String(v);",
        _const("SKIP_FIELDS"), _const("FIELD_WORDS"), _const("EMPTY_WORDS"), _const("FLOW_WORDS"),
        _function("isEmpty"), _function("changedFields"), _function("valueText"), _function("rowName"),
        _function("accountOf"), _function("changeWords"),
        "const ctx = {r: {accountById: new Map(), typeById: new Map()}, services: [{id: 7, name: 'Sample Co Health'}],"
        " gone: new Map()};",
        "const row = p => ({id: 3, name: 'Sample Co', type: 'company', party_id: p});",
        "const change = (b, a) => ({table: 'accounts', op: 'update', row_id: 3, before: row(b), after: row(a)});",
        "console.log(JSON.stringify([changeWords(change(null, 7), ctx), changeWords(change(7, null), ctx),"
        " changeWords({table: 'accounts', op: 'update', row_id: 3, before: {name: 'Sample Co', status: 'active'},"
        " after: {name: 'Sample Co', status: 'archived'}}, ctx)]));",
    ])
    assert _node(code) == ["joined the account Sample Co to the party Sample Co Health",
                           "took the account Sample Co off the party Sample Co Health",
                           "changed the account Sample Co"]
