"""The chat's relabel reach (fin redesign ticket 11; spec cases 20 to 23 and 26).

Through the label_review tool it already had, with no write path of its own,
the chat can say any row was a refund that keeps its type, a card payment, or
a move into or out of a holding; and say the same of many rows behind one
preview, its token and expected_count. A person is still no transfer's other
side. create_merchant adds a merchant over the app's own route. Driven through
mcp_tools.call as mcp_server calls it, against a temporary book. Every name
and figure is invented.
"""

from __future__ import annotations

import pytest

import db
import mcp_tools
import review
from test_balance_sheet import bring_in, make_account

ACTOR = "chat:sample-subject"
CARD = "Sample Card 0001"
BANK = "Sample Bank 0002"
MONTH = "2026-08"


def call(tool_name, **args):
    return mcp_tools.call(tool_name, args, actor=ACTOR)


def type_id(client, name: str) -> int:
    return next(t["id"] for t in client.get("/api/types").get_json() if t["name"] == name)


def row_id(client, description: str, amount_minor: int) -> int:
    rows = client.get("/api/transactions", query_string={"search": description, "per_page": 200}).get_json()
    [found] = [r["id"] for r in rows["transactions"] if r["amount_minor"] == amount_minor]
    return found


def held(conn, tx_id: int) -> dict:
    return dict(conn.execute(
        "SELECT flow_type, flow_type_manual, other_side_id, book, type_id, service_id, "
        "book_source, type_source FROM transactions WHERE id = ?", (tx_id,)).fetchone())


def state(conn) -> tuple:
    """Every table a label can reach, as it stands."""
    return tuple(
        [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
        for table in ("transactions", "refund_link", "set_off", "dismissal", "services", "accounts")
    )


def entries() -> list[dict]:
    return call("history", limit=200)["result"]["entries"]


def household(client) -> float:
    return client.get(f"/api/dashboard/stat-cards?ref_month={MONTH}").get_json()["household"]


def one_change_that_undoes_exactly(conn, answer: dict, before: tuple, count_before: int) -> None:
    """The write is one change in the history, the chat's, and Undo puts
    every table back as it was."""
    assert answer["ok"], answer
    shown = entries()
    assert len(shown) == count_before + 1
    assert (shown[0]["id"], shown[0]["via"], shown[0]["actor"]) == (answer["change"]["entry_id"], "chat", ACTOR)
    rows = answer["change"]["rows"]
    undone = call("undo", entry_id=answer["change"]["entry_id"],
                  **({"expected_count": rows} if rows > mcp_tools.MANY_ROWS else {}))
    assert undone["ok"], undone
    assert state(conn) == before


@pytest.fixture
def trip(client):
    """A flight bought on a card and its fare credited back, which arrived as
    income carrying the airline's type; a card payment's reversal that
    counts as spending; and a payout from an exchange waiting on the bank."""
    travel = type_id(client, "Travel")
    airline = client.post("/api/services", json={"name": "Sample Air", "type_id": travel}).get_json()["id"]
    assert client.post("/api/rules", json={"pattern": "SAMPLE AIR", "service_id": airline}).status_code == 200
    bring_in(client, CARD, [
        ("2026-08-03", "SAMPLE AIR BOOKING 4471", 94800),
        ("2026-08-20", "SAMPLE AIR BOOKING 4471", -94800),
        ("2026-08-21", "PAYMENT REVERSAL", 1342415),
    ], kind="card")
    bring_in(client, BANK, [("2026-08-05", "INWARD TELEGRAPHIC TRANSFER SAMPLE EXCHANGE PTY", -2645500)])

    class Trip:
        pass

    t = Trip()
    t.client, t.travel, t.airline = client, travel, airline
    t.purchase = row_id(client, "SAMPLE AIR BOOKING", 94800)
    t.credit = row_id(client, "SAMPLE AIR BOOKING", -94800)
    t.reversal = row_id(client, "PAYMENT REVERSAL", 1342415)
    t.payout = row_id(client, "SAMPLE EXCHANGE", -2645500)
    # The credit as the book holds case 20's row: imported before income
    # stopped taking a merchant's label (fin-redesign 05), it carries the
    # airline's book and type, as its purchase does.
    conn = db.get_connection()
    conn.execute("UPDATE transactions SET book = p.book, type_id = p.type_id, book_source = p.book_source,"
                 " type_source = p.type_source FROM (SELECT book, type_id, book_source, type_source"
                 " FROM transactions WHERE id = ?) AS p WHERE id = ?", (t.purchase, t.credit))
    conn.commit()
    conn.close()
    return t


# --- the tool keeps what it took ------------------------------------------------


def test_label_review_keeps_every_parameter_it_took_and_adds_only_optional_ones():
    schema = mcp_tools.BY_NAME["label_review"].input_schema
    took = {"tx_id": "integer", "choice": "string", "type_id": "integer", "book": "string",
            "account_id": "integer", "income_kind_id": "integer", "expected_count": "integer"}

    assert schema["required"] == ["tx_id", "choice"]
    assert {k: schema["properties"][k]["type"] for k in took} == took
    assert set(schema["properties"]) - set(took) == {"tx_ids", "token"}


def test_the_review_list_the_app_offers_is_unchanged(client):
    listed = client.get("/api/review").get_json()

    assert [c["name"] for c in listed["choices"]] == [
        "spending", "gift", "own_account", "company", "loan_repayment", "income"]


# --- case 20: a card credit that arrived as income is a refund keeping its type ---


def test_a_card_credit_that_arrived_as_income_becomes_a_refund_keeping_its_type_and_links_to_its_purchase(trip, conn):
    assert held(conn, trip.credit)["flow_type"] == "income"
    spent = household(trip.client)
    before, count = state(conn), len(entries())

    answer = call("label_review", tx_id=trip.credit, choice="refund")

    assert answer["ok"], answer
    tx = held(conn, trip.credit)
    assert (tx["flow_type"], tx["flow_type_manual"], tx["other_side_id"], tx["book"], tx["type_id"],
            tx["service_id"]) == ("refund", 1, None, "Household", trip.travel, trip.airline)
    assert (tx["book_source"], tx["type_source"]) == ("chosen", "chosen")
    link = trip.client.get(f"/api/refund-links?row={trip.credit}").get_json()
    assert link["purchase"]["id"] == trip.purchase
    # The fare came back: the month's household spending nets it.
    assert household(trip.client) == pytest.approx(spent - 948.00)
    one_change_that_undoes_exactly(conn, answer, before, count)


def test_a_refund_given_a_type_takes_it_and_one_whose_type_is_an_income_kind_keeps_none(trip, conn):
    salary = trip.client.get("/api/types?kind=income").get_json()
    salary_id = next(t["id"] for t in salary if t["name"] == "Salary")
    assert call("label_review", tx_id=trip.payout, choice="income", income_kind_id=salary_id)["ok"]

    kept = call("label_review", tx_id=trip.payout, choice="refund")
    assert kept["ok"], kept
    assert (held(conn, trip.payout)["type_id"], held(conn, trip.payout)["flow_type"]) == (None, "refund")

    shopping = type_id(trip.client, "Shopping")
    given = call("label_review", tx_id=trip.payout, choice="refund", type_id=shopping)
    assert given["ok"], given
    assert (held(conn, trip.payout)["type_id"], held(conn, trip.payout)["book"]) == (shopping, "Household")


def test_a_refund_relabelled_as_something_else_drops_its_link(trip, conn):
    assert call("label_review", tx_id=trip.credit, choice="refund")["ok"]
    assert trip.client.get(f"/api/refund-links?row={trip.credit}").get_json()["refund_link"] is not None
    before, count = state(conn), len(entries())

    answer = call("label_review", tx_id=trip.credit, choice="card_payment")

    assert answer["ok"], answer
    assert trip.client.get(f"/api/refund-links?row={trip.credit}").get_json()["refund_link"] is None
    # Dropped, not turned down: no dismissal is written for the pair.
    assert conn.execute("SELECT COUNT(*) FROM dismissal").fetchone()[0] == 0
    one_change_that_undoes_exactly(conn, answer, before, count)
    assert trip.client.get(f"/api/refund-links?row={trip.credit}").get_json()["purchase"]["id"] == trip.purchase


def test_refunds_labelled_together_link_each_to_its_purchase_on_the_previews_token(trip, conn):
    bring_in(trip.client, CARD, [("2026-08-06", "SAMPLE AIR SEAT 2210", 12000),
                                 ("2026-08-22", "SAMPLE AIR SEAT 2210", -12000)], kind="card")
    seat = row_id(trip.client, "SAMPLE AIR SEAT", -12000)
    token = call("label_review", tx_id=trip.credit, tx_ids=[seat], choice="refund")["preview"]["token"]

    answer = call("label_review", tx_id=trip.credit, tx_ids=[seat], choice="refund", token=token)

    assert answer["ok"], answer
    assert {trip.client.get(f"/api/refund-links?row={r}").get_json()["purchase"]["id"]
            for r in (trip.credit, seat)} == {trip.purchase, row_id(trip.client, "SAMPLE AIR SEAT", 12000)}


def expectation_on(conn, tx_id: int) -> int:
    return conn.execute("SELECT id FROM expectation WHERE row_id = ?", (tx_id,)).fetchone()[0]


def test_a_refund_to_pay_back_is_not_taken_out_of_refund_until_the_mark_is_undone(trip, conn):
    # The purchase was paid back in full by a friend, so the fare credited
    # later is money to pass on: the operator chose To pay back on it.
    assert call("mark_expect_back", tx_id=trip.purchase, direction="back", all=True,
                new_names=["Sample Friend"])["ok"]
    bring_in(trip.client, BANK, [("2026-08-10", "INWARD PAYNOW SAMPLE FRIEND REF 0001", -94800)])
    paid = row_id(trip.client, "SAMPLE FRIEND REF 0001", -94800)
    settled = call("set_off", tx_id=paid, placements=[
        {"expectation_id": expectation_on(conn, trip.purchase), "amount": "948.00"}])
    assert settled["ok"], settled
    assert call("label_review", tx_id=trip.credit, choice="refund")["ok"]
    link = trip.client.get(f"/api/refund-links?row={trip.credit}").get_json()["refund_link"]["id"]
    assert trip.client.post(f"/api/refund-links/{link}/pay-back").status_code == 200
    before, count = state(conn), len(entries())

    answer = call("label_review", tx_id=trip.credit, choice="spending", type_id=trip.travel)

    # In Unlink's words: the refund stays a refund, linked and to pay back.
    assert answer == {"ok": False,
                      "error": "SAMPLE AIR BOOKING 4471 is to pay back since; undo the mark on it first"}
    assert (state(conn), len(entries())) == (before, count)


def test_a_refund_with_no_type_of_its_own_takes_its_purchases_book_and_type_when_linked(trip, conn):
    assert call("label_review", tx_id=trip.credit, choice="card_payment")["ok"]
    assert (held(conn, trip.credit)["book"], held(conn, trip.credit)["type_id"]) == (None, None)

    answer = call("label_review", tx_id=trip.credit, choice="refund")

    assert answer["ok"], answer
    assert trip.client.get(f"/api/refund-links?row={trip.credit}").get_json()["purchase"]["id"] == trip.purchase
    tx = held(conn, trip.credit)
    assert (tx["flow_type"], tx["book"], tx["type_id"]) == ("refund", "Household", trip.travel)
    # From the purchase, as the linker gives it: not the operator's choice.
    assert "chosen" not in (tx["book_source"], tx["type_source"])


def test_dropping_a_refunds_link_takes_the_set_off_it_placed_and_undo_puts_both_back(trip, conn):
    # A friend owes part of the fare; the refund, once linked, closes it.
    assert call("mark_expect_back", tx_id=trip.purchase, direction="back", amount="400.00",
                new_names=["Sample Friend"])["ok"]
    assert call("label_review", tx_id=trip.credit, choice="refund")["ok"]
    placed = conn.execute("SELECT id, amount_minor, source FROM set_off WHERE payment_row_id = ?",
                          (trip.credit,)).fetchall()
    assert [(p["amount_minor"], p["source"]) for p in placed] == [(40000, "refund")]
    before, count = state(conn), len(entries())

    answer = call("label_review", tx_id=trip.credit, choice="card_payment")

    assert answer["ok"], answer
    assert conn.execute("SELECT COUNT(*) FROM set_off WHERE payment_row_id = ?", (trip.credit,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM refund_link").fetchone()[0] == 0
    one_change_that_undoes_exactly(conn, answer, before, count)


def test_a_refund_two_purchases_fit_stays_unlinked_and_is_asked(trip, conn):
    bring_in(trip.client, CARD, [("2026-08-04", "SAMPLE AIR BOOKING 4471", 94800)], kind="card")

    answer = call("label_review", tx_id=trip.credit, choice="refund")

    assert answer["ok"], answer
    assert trip.client.get(f"/api/refund-links?row={trip.credit}").get_json()["refund_link"] is None
    [ask] = trip.client.get("/api/refund-links/asks").get_json()["asks"]
    assert (ask["kind"], ask["refund"]["id"], len(ask["purchases"])) == ("which", trip.credit, 2)


@pytest.mark.parametrize("order", ["oldest first", "newest first"])
def test_refunds_labelled_together_link_oldest_first_whatever_order_they_are_named(trip, conn, order):
    # One purchase both credits fit: the older one takes it, as the linker does.
    bring_in(trip.client, CARD, [("2026-08-25", "SAMPLE AIR BOOKING 4471", -94800)], kind="card")
    later = [r["id"] for r in trip.client.get("/api/transactions", query_string={
        "search": "SAMPLE AIR BOOKING", "per_page": 50}).get_json()["transactions"]
        if r["amount_minor"] == -94800 and r["id"] != trip.credit][0]
    named = [trip.credit, later] if order == "oldest first" else [later, trip.credit]
    token = call("label_review", tx_id=named[0], tx_ids=named[1:], choice="refund")["preview"]["token"]

    answer = call("label_review", tx_id=named[0], tx_ids=named[1:], choice="refund", token=token)

    assert answer["ok"], answer
    assert trip.client.get(f"/api/refund-links?row={trip.credit}").get_json()["purchase"]["id"] == trip.purchase
    assert trip.client.get(f"/api/refund-links?row={later}").get_json()["refund_link"] is None


def test_the_preview_shows_the_count_the_chat_is_asked_for(trip, conn):
    bring_in(trip.client, CARD, [("2026-08-06", "SAMPLE AIR SEAT 2210", 12000),
                                 ("2026-08-22", "SAMPLE AIR SEAT 2210", -12000)], kind="card")
    seat = row_id(trip.client, "SAMPLE AIR SEAT", -12000)
    preview = call("label_review", tx_id=trip.credit, tx_ids=[seat], choice="refund")["preview"]

    answer = call("label_review", tx_id=trip.credit, tx_ids=[seat], choice="refund", token=preview["token"])

    # Two rows labelled, and two refund links made: four changes, as the
    # history and the chat's count read them.
    assert (preview["rows"], preview["changes"]) == (2, 4)
    assert answer["change"]["rows"] == preview["changes"]


# --- case 21: a payment reversal is a card payment --------------------------------


def test_a_payment_reversal_becomes_a_card_payment_with_no_book_or_type(trip, conn):
    spent = household(trip.client)
    before, count = state(conn), len(entries())

    answer = call("label_review", tx_id=trip.reversal, choice="card_payment")

    assert answer["ok"], answer
    tx = held(conn, trip.reversal)
    assert (tx["flow_type"], tx["flow_type_manual"], tx["other_side_id"], tx["book"], tx["type_id"]) == (
        "payment", 1, None, None, None)
    assert household(trip.client) == pytest.approx(spent - 13424.15)
    one_change_that_undoes_exactly(conn, answer, before, count)


# --- case 22: a payout from a holding names the holding --------------------------


def test_a_payout_from_a_holding_is_a_movement_naming_it_and_no_longer_refused(trip, conn):
    exchange = make_account(trip.client, "Sample Exchange", "holding")
    # A holding is still no own bank account or card.
    assert not call("label_review", tx_id=trip.payout, choice="own_account", account_id=exchange)["ok"]
    before, count = state(conn), len(entries())

    answer = call("label_review", tx_id=trip.payout, choice="holding", account_id=exchange)

    assert answer["ok"], answer
    assert answer["result"]["other_side_name"] == "Sample Exchange"
    tx = held(conn, trip.payout)
    assert (tx["flow_type"], tx["flow_type_manual"], tx["other_side_id"], tx["book"], tx["type_id"]) == (
        "movement", 1, exchange, None, None)
    one_change_that_undoes_exactly(conn, answer, before, count)


@pytest.mark.parametrize("kind", ["company", "loan", "bank"])
def test_the_holding_choice_names_only_a_holding(trip, conn, kind):
    other = make_account(trip.client, f"Sample Other {kind}", kind)
    before, count = state(conn), len(entries())

    answer = call("label_review", tx_id=trip.payout, choice="holding", account_id=other)

    assert not answer["ok"]
    assert answer["error"].endswith("of kind holding")
    assert (state(conn), len(entries())) == (before, count)


# --- case 23: a person is no transfer's other side --------------------------------


@pytest.mark.parametrize("choice", ["own_account", "holding"])
def test_a_person_as_the_other_side_is_refused_as_today_and_writes_nothing(trip, conn, choice):
    friend = make_account(trip.client, "Sample Friend", "person")
    before, count = state(conn), len(entries())

    answer = call("label_review", tx_id=trip.payout, choice=choice, account_id=friend)

    assert answer == {"ok": False, "error": review.PERSON_MOVEMENT_REFUSED}
    assert (state(conn), len(entries())) == (before, count)


# --- many rows behind one preview (case 26) ----------------------------------------


@pytest.fixture
def credits(client):
    """Twenty-seven credits from one shop on a card, each arrived as income
    carrying the shop's type."""
    shopping = type_id(client, "Shopping")
    shop = client.post("/api/services", json={"name": "Sample Shop", "type_id": shopping}).get_json()["id"]
    rule = client.post("/api/rules", json={"pattern": "SAMPLE SHOP", "service_id": shop}).get_json()["id"]
    bring_in(client, CARD, [(f"2026-08-{n:02d}", f"SAMPLE SHOP CREDIT {n:02d}", -(1000 + n)) for n in range(1, 28)],
             kind="card")
    ids = [row_id(client, f"SAMPLE SHOP CREDIT {n:02d}", -(1000 + n)) for n in range(1, 28)]
    # As the book holds such credits: imported before income stopped taking
    # a merchant's label (fin-redesign 05), each carries the shop's type.
    conn = db.get_connection()
    conn.executemany("UPDATE transactions SET book = 'Household', type_id = ?, book_source = 'merchant',"
                     " type_source = 'merchant' WHERE id = ?", [(shopping, i) for i in ids])
    conn.commit()
    conn.close()
    return {"ids": ids, "rule": rule, "shop": shop, "shopping": shopping}


def test_a_batch_without_a_token_answers_with_its_preview_and_writes_nothing(credits, conn):
    first, *more = credits["ids"][:3]
    before, count = state(conn), len(entries())

    answer = call("label_review", tx_id=first, tx_ids=more, choice="refund")

    assert answer["ok"] is False and answer["preview"]["rows"] == 3
    assert answer["preview"]["token"]
    [line] = [x for x in answer["preview"]["lines"] if x["id"] == first]
    assert (line["was"]["flow_type"], line["now"]["flow_type"], line["now"]["type"]) == ("income", "refund", "Shopping")
    assert (state(conn), len(entries())) == (before, count)


def test_a_batch_with_its_token_is_one_change_and_one_undo(credits, conn):
    first, *more = credits["ids"][:3]
    token = call("label_review", tx_id=first, tx_ids=more, choice="refund")["preview"]["token"]
    before, count = state(conn), len(entries())

    answer = call("label_review", tx_id=first, tx_ids=more, choice="refund", token=token)

    assert answer["ok"], answer
    assert answer["change"]["rows"] == 3
    assert {held(conn, i)["flow_type"] for i in credits["ids"][:3]} == {"refund"}
    one_change_that_undoes_exactly(conn, answer, before, count)


def test_a_relabel_applied_with_a_token_gone_stale_after_a_rule_changed_is_refused_and_writes_nothing(client, credits, conn):
    first, *more = credits["ids"][:3]
    token = call("label_review", tx_id=first, tx_ids=more, choice="refund")["preview"]["token"]
    # Mid-preview, the shop's rule now names another merchant: the merchant
    # link is on every row, income too (an income row takes no rule's type,
    # fin-redesign 05).
    other = client.post("/api/services", json={"name": "Sample Other Shop",
                                               "type_id": type_id(client, "Groceries")}).get_json()["id"]
    asked = call("change_rule", rule_id=credits["rule"], service_id=other)
    changed = call("change_rule", rule_id=credits["rule"], service_id=other, past="apply",
                   token=asked["preview"]["token"], expected_count=27)
    assert changed["ok"], changed
    before, count = state(conn), len(entries())

    answer = call("label_review", tx_id=first, tx_ids=more, choice="refund", token=token)

    assert answer["ok"] is False and answer.get("stale") is True
    assert (state(conn), len(entries())) == (before, count)


def test_a_batch_of_27_rows_without_expected_count_is_put_back_and_with_it_stands(credits, conn):
    first, *more = credits["ids"]
    preview = call("label_review", tx_id=first, tx_ids=more, choice="card_payment")["preview"]
    token = preview["token"]
    # The number the operator is asked for is the one the preview shows.
    assert (preview["changes"], preview["expected_count"]) == (27, 27)
    before, count = state(conn), len(entries())

    stopped = call("label_review", tx_id=first, tx_ids=more, choice="card_payment", token=token)

    assert (stopped["ok"], stopped.get("stopped"), stopped.get("would_change_rows")) == (False, True, 27)
    assert (state(conn), len(entries())) == (before, count)

    answer = call("label_review", tx_id=first, tx_ids=more, choice="card_payment", token=token, expected_count=27)

    assert answer["ok"], answer
    assert {held(conn, i)["flow_type"] for i in credits["ids"]} == {"payment"}
    assert entries()[0]["asked_first"] is True
    one_change_that_undoes_exactly(conn, answer, before, count)


def test_a_batch_with_one_row_refused_writes_nothing(credits, conn):
    first, second, third = credits["ids"][:3]
    # Money in marked to pay back may not become a card payment: it would count twice.
    marked = call("mark_expect_back", tx_id=third, direction="pay", all=True, new_names=["Sample Friend"])
    assert marked["ok"], marked
    before, count = state(conn), len(entries())

    answer = call("label_review", tx_id=first, tx_ids=[second, third], choice="card_payment")

    assert answer["ok"] is False and "undo the mark first" in answer["error"]
    assert (state(conn), len(entries())) == (before, count)


@pytest.mark.parametrize("more, error", [
    ([7, 7], "a row is named twice"),
    (["7"], "tx_ids must be a list of row ids"),
])
def test_a_batch_naming_its_rows_wrongly_is_refused(credits, conn, more, error):
    before = state(conn)

    answer = call("label_review", tx_id=credits["ids"][0], tx_ids=more, choice="refund")

    assert answer["ok"] is False and error in answer["error"]
    assert state(conn) == before


# --- create_merchant ----------------------------------------------------------------


def test_create_merchant_adds_a_merchant_with_its_book_and_type_and_undoes(client, conn):
    dining = type_id(client, "Dining")
    before, count = state(conn), len(entries())

    answer = call("create_merchant", name="Sample Bakery", type_id=dining)

    assert answer["ok"], answer
    assert answer["change"]["rows"] == 1
    made = {m["name"]: m for m in call("merchants")["result"]}["Sample Bakery"]
    assert (made["id"], made["book"], made["type_id"]) == (answer["result"]["id"], "Household", dining)
    assert entries()[0]["summary"] == "Added a merchant"
    one_change_that_undoes_exactly(conn, answer, before, count)


@pytest.mark.parametrize("args, error", [
    ({"name": " sample BAKERY "}, "fin already has a merchant named Sample Bakery"),
    ({"name": "Sample Florist", "book": "Acme"}, "unknown book: 'Acme'"),
    ({"name": "Sample Florist", "type_id": 987654}, "unknown type: 987654"),
    ({"name": "   "}, "Service name is required"),
])
def test_create_merchant_refuses_what_the_route_refuses_and_adds_nothing(client, conn, args, error):
    assert call("create_merchant", name="Sample Bakery")["ok"]
    before, count = state(conn), len(entries())

    answer = call("create_merchant", **args)

    assert answer["ok"] is False and error in answer["error"], answer
    assert (state(conn), len(entries())) == (before, count)

