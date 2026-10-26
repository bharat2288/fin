"""A loan's worked-out interest goes to the book the operator chooses
(fin-redesign 26; fin RUL-032): Orange Grove is its own book, and the
mortgage's interest is its cost, not Household spending.

The choice is one change, undone whole: the loan's derived rows move to the
book in the same change. With no choice, or Household chosen, the interest
is Household spending as before (test_loan_interest.py). Every name and
figure here is invented.
"""

from __future__ import annotations

import pytest

from test_loan_interest import (
    AUG_INTEREST, JULY_INTEREST, OWED_AUG, cards, cents, derived, enter, make_account, setup,
)

GROVE = "Orange Grove"


def add_grove(client) -> None:
    resp = client.post("/api/books", json={"name": GROVE, "description": "The rented flat and its mortgage"})
    assert resp.status_code == 201, resp.get_json()


def choose(client, account_id: int, book):
    return client.put(f"/api/accounts/{account_id}/interest-book", json={"book": book})


def books_of_derived(client) -> list[str]:
    return [t["book"] for t in derived(client)]


def latest_entry(client) -> dict:
    return client.get("/api/history").get_json()["entries"][0]


def stored(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "SELECT t.id, t.date, t.amount_minor, t.book FROM transactions t ORDER BY t.id")] + [
        tuple(r) for r in conn.execute("SELECT account_id, book FROM interest_books ORDER BY account_id")]


def test_the_interest_moves_to_the_chosen_book_and_leaves_household_spending(client, conn):
    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")
    add_grove(client)

    resp = choose(client, made["loan"], GROVE)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["book"] == GROVE
    assert books_of_derived(client) == [GROVE, GROVE]
    july = cards(client, "2026-07")
    assert cents(july["household"]) == 0
    assert cents(july[GROVE.lower()]) == JULY_INTEREST
    # The loan's own figures do not move: interest is interest, whichever book.
    assert cents(july["loan_interest"]) == JULY_INTEREST
    assert cents(cards(client, "2026-08")[GROVE.lower()]) == AUG_INTEREST


def test_the_choice_is_one_change_and_undo_puts_the_interest_back_in_household(client, conn):
    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")
    add_grove(client)
    choose(client, made["loan"], GROVE)

    entry = latest_entry(client)
    assert entry["summary"] == "Chose the book a loan's interest goes to"
    assert client.post(f"/api/history/{entry['id']}/undo").status_code == 200

    assert books_of_derived(client) == ["Household", "Household"]
    assert cents(cards(client, "2026-07")["household"]) == JULY_INTEREST
    loan = next(a for a in client.get("/api/accounts").get_json() if a["id"] == made["loan"])
    assert loan["interest_book"] == "Household"


def test_interest_worked_out_after_the_choice_goes_to_the_chosen_book(client, conn):
    made = setup(client, conn)
    add_grove(client)
    assert choose(client, made["loan"], GROVE).status_code == 200
    assert derived(client) == []

    enter(client, made["loan"], OWED_AUG, "2026-08-31")

    assert books_of_derived(client) == [GROVE, GROVE]
    loan = next(a for a in client.get("/api/accounts").get_json() if a["id"] == made["loan"])
    assert loan["interest_book"] == GROVE


@pytest.mark.parametrize("back", [None, "Household"])
def test_choosing_household_or_none_puts_the_interest_back(client, conn, back):
    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")
    add_grove(client)
    choose(client, made["loan"], GROVE)

    resp = choose(client, made["loan"], back)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["book"] == "Household"
    assert books_of_derived(client) == ["Household", "Household"]
    assert conn.execute("SELECT COUNT(*) FROM interest_books").fetchone()[0] == 0


def test_choosing_the_book_it_already_goes_to_changes_nothing(client, conn):
    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")
    entries = len(client.get("/api/history").get_json()["entries"])

    resp = choose(client, made["loan"], "Household")

    assert resp.status_code == 200
    assert resp.get_json()["changed"] is False
    assert len(client.get("/api/history").get_json()["entries"]) == entries


@pytest.mark.parametrize("body, kind, words", [
    ({"book": "Nowhere"}, "loan", "Nowhere is not a book; add it on Lists › Books first. Nothing was changed"),
    ({"book": 7}, "loan", "send book: a book's name, or null for Household; nothing was changed"),
    ({}, "loan", "send book: a book's name, or null for Household; nothing was changed"),
    ({"book": "Household"}, "bank",
     "only a loan has interest worked out; this is a bank account. Nothing was changed"),
])
def test_a_refusal_names_why_and_writes_nothing(client, conn, body, kind, words):
    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")
    target = made["loan"] if kind == "loan" else made["bank"]
    before = stored(conn)

    resp = client.put(f"/api/accounts/{target}/interest-book", json=body)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == words
    assert stored(conn) == before


def test_an_account_no_longer_in_fin_is_refused(client):
    resp = choose(client, 999, "Household")
    assert resp.status_code == 404


def test_a_loan_someone_else_owes_is_refused(client, conn):
    loan = make_account(client, "Sample Company Loan", "loan", owner="Household")
    conn.execute("UPDATE accounts SET owner = 'Moom' WHERE id = ?", (loan,))
    conn.commit()

    resp = choose(client, loan, "Household")

    assert resp.status_code == 400
    assert resp.get_json()["error"] == (
        "only the household's own loans have interest worked out. Nothing was changed")


def test_a_book_a_loans_interest_goes_to_cannot_have_its_add_undone(client, conn):
    made = setup(client, conn)
    add_grove(client)
    added = latest_entry(client)
    choose(client, made["loan"], GROVE)
    before = stored(conn)

    resp = client.post(f"/api/history/{added['id']}/undo")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "something is filed under the book Orange Grove; change that first"
    assert stored(conn) == before


def test_the_loan_sheet_offers_every_book_with_the_one_it_goes_to_chosen():
    import json
    import shutil
    import subprocess

    from test_client_seams import _function

    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    code = "\n".join([
        _function("esc"), _function("interestBookHTML"),
        "const books = [{name: 'Household'}, {name: 'Moom'}, {name: 'Orange Grove'}];",
        "console.log(JSON.stringify(interestBookHTML(books, 'Orange Grove')));",
    ])
    out = subprocess.run(["node", "-"], input=code, capture_output=True, text=True, timeout=30,
                         check=True, encoding="utf-8")
    html = json.loads(out.stdout)

    assert "Interest goes to" in html
    assert 'data-held="Orange Grove"' in html
    assert '<option value="Orange Grove" selected>' in html
    assert '<option value="Household">' in html


def test_chat_chooses_the_book_as_the_app_does_in_one_change_it_can_undo(client, conn):
    import mcp_tools

    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")
    add_grove(client)

    done = mcp_tools.call("set_interest_book", {"account_id": made["loan"], "book": GROVE},
                          actor="chat:sample-subject")

    assert done["ok"], done
    assert books_of_derived(client) == [GROVE, GROVE]
    assert latest_entry(client)["summary"] == "Chose the book a loan's interest goes to"
    assert mcp_tools.call("undo", {"entry_id": done["change"]["entry_id"]}, actor="chat:sample-subject")["ok"]
    assert books_of_derived(client) == ["Household", "Household"]


def test_chat_leaving_the_book_out_puts_the_interest_back_in_household(client, conn):
    import mcp_tools

    made = setup(client, conn)
    enter(client, made["loan"], OWED_AUG, "2026-08-31")
    add_grove(client)
    choose(client, made["loan"], GROVE)

    done = mcp_tools.call("set_interest_book", {"account_id": made["loan"]}, actor="chat:sample-subject")

    assert done["ok"], done
    assert books_of_derived(client) == ["Household", "Household"]
