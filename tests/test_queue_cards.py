"""The Queue's labelling work as cards, read on the server (chat-queue-read 01).

`GET /api/queue/cards` gives what the Queue draws for the rows waiting for a
label, card by card: each merchant group of rows with no type (its rows, Jev's
guess, the suggested name and wording, the prefilled book and why, the held
merchant the name matches), the rows under Check each row, and the transfers
waiting. Driven through the Flask test client against a temporary book; Jev's
answers are stored as a send stores them, and nothing reaches the network.
Every wording, merchant and figure here is invented.
"""

import pytest

import app as fin_app
import money
import suggest
from test_book_and_type_switch import merchant, type_id
from test_preview_merchant_change import add_row
from test_queue_guesses import NOT_A_KEY, answer, statement_on


@pytest.fixture(autouse=True)
def never_asked(monkeypatch: pytest.MonkeyPatch) -> list:
    """Jev, replaced by something that records and refuses: the read never asks."""
    asked = []

    def refuse(body, key):
        asked.append(body)
        raise AssertionError("the cards read asked the model")

    monkeypatch.setattr(suggest, "post", refuse)
    return asked


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(suggest.KEY_ENV, NOT_A_KEY)


@pytest.fixture
def card(conn) -> int:
    return statement_on(conn, "card", "0001")


def untyped(conn, statement_id: int, description: str, amount_minor: int, day: str) -> int:
    """A row with no merchant, type or book, waiting in the Queue."""
    return add_row(conn, statement_id, description, amount_minor, service_id=None, book=None,
                   type_name=None, day=day)


def cards(client) -> dict:
    resp = client.get("/api/queue/cards")
    assert resp.status_code == 200
    return resp.get_json()


def group_of(payload: dict, tx: int) -> dict:
    return next(g for g in payload["untyped"]["groups"] if tx in [r["id"] for r in g["rows"]])


@pytest.fixture
def book(conn, card) -> dict:
    """Two Indigoair rows Jev is sure are Travel, a Rhode Skin row Jev is
    sure is Health & Beauty, and a PayNow."""
    ids = {
        "indigo_old": untyped(conn, card, "INDIGOAIR 312-4567890 MUMBAI", 41250, "2026-09-02"),
        "indigo_new": untyped(conn, card, "INDIGOAIR 312-9988776 MUMBAI", 38800, "2026-09-20"),
        "rhode": untyped(conn, card, "RHODE SKIN 8812 SINGAPORE", 5323, "2026-09-11"),
        "paynow": untyped(conn, card, "PAYNOW TO K TAN", 5000, "2026-09-12"),
    }
    answer(conn, "INDIGOAIR MUMBAI", "Travel", 1.0)
    answer(conn, "RHODE SKIN", "Health & Beauty", 0.99)
    return ids


# --- case 1: one card per merchant group ----------------------------------------


def test_case_1_each_group_carries_its_rows_guess_name_wording_and_book(client, conn, book, key):
    payload = cards(client)

    indigo = group_of(payload, book["indigo_old"])
    # Its rows, oldest first, with their dates, amounts and account.
    assert [(r["id"], r["date"], r["amount_minor"], r["amount"], r["currency"], r["account"])
            for r in indigo["rows"]] == [
        (book["indigo_old"], "2026-09-02", 41250, 412.50, "SGD", "Sample card 0001"),
        (book["indigo_new"], "2026-09-20", 38800, 388.00, "SGD", "Sample card 0001"),
    ]
    assert indigo["row_count"] == 2
    assert indigo["merchant"] == "INDIGOAIR MUMBAI"
    assert indigo["route"] == "prefill"
    assert [(t["name"], t["type_id"], t["probability"]) for t in indigo["types"]] == [
        ("Travel", type_id(conn, "Travel"), pytest.approx(1.0)),
    ]
    # The name and wording the card draws, from its oldest row.
    assert (indigo["suggested_name"], indigo["suggested_wording"]) == ("Indigoair", "INDIGOAIR")
    # No history and a household card: the book is Travel's.
    assert indigo["prefill"] == {"book": "Household", "reason": "type", "hint": ""}
    assert indigo["held"] is None

    rhode = group_of(payload, book["rhode"])
    assert (rhode["merchant"], rhode["route"]) == ("RHODE SKIN", "prefill")
    assert [(t["name"], t["probability"]) for t in rhode["types"]] == [("Health & Beauty", pytest.approx(0.99))]
    assert (rhode["suggested_name"], rhode["suggested_wording"]) == ("Rhode Skin", "RHODE SKIN")


def test_case_1_a_name_matching_a_held_merchant_ignoring_case_names_it(client, conn, book, key):
    held = merchant(conn, "RHODE skin", "Moom", "Health & Beauty")

    rhode = group_of(cards(client), book["rhode"])

    assert rhode["suggested_name"] == "Rhode Skin"
    assert rhode["held"] == {
        "id": held, "name": "RHODE skin", "type_id": type_id(conn, "Health & Beauty"),
        "type": "Health & Beauty", "book": "Moom",
    }
    assert group_of(cards(client), book["indigo_old"])["held"] is None


def test_case_1_a_company_card_prefills_its_company_and_says_why(client, conn, key):
    company = statement_on(conn, "card", "0042")
    conn.execute("UPDATE accounts SET owner = 'Moom' WHERE id = (SELECT account_id FROM statements WHERE id = ?)",
                 (company,))
    conn.commit()
    tx = untyped(conn, company, "RHODE SKIN 8812 SINGAPORE", 5323, "2026-09-11")
    answer(conn, "RHODE SKIN", "Health & Beauty", 0.99)

    rhode = group_of(cards(client), tx)

    assert rhode["prefill"] == {"book": "Moom", "reason": "account", "hint": "Moom card"}
    assert rhode["rows"][0]["prefill"] == {"book": "Moom", "reason": "account"}


# --- case 2: a PayNow names nothing --------------------------------------------


def test_case_2_a_paynow_has_no_name_no_wording_and_route_blank(client, book, key):
    paynow = group_of(cards(client), book["paynow"])

    assert (paynow["suggested_name"], paynow["suggested_wording"]) == ("", "")
    assert (paynow["route"], paynow["types"]) == ("blank", [])
    assert paynow["held"] is None


# --- case 3: Check each row ------------------------------------------------------


def test_case_3_rows_under_check_each_row_carry_merchant_type_usual_and_own_book(client, conn, card, key):
    svc = merchant(conn, "Sample Travelsite", "Moom", "Travel")
    conn.execute("UPDATE services SET review_each_time = 1 WHERE id = ?", (svc,))
    conn.commit()
    waits = add_row(conn, card, "SAMPLE TRAVELSITE BKG", 21000, service_id=svc, book="Kalesh",
                    type_name="Travel", day="2026-09-14")
    add_row(conn, card, "SAMPLE TRAVELSITE BKG", 9000, service_id=svc, book="Moom", type_name="Travel",
            sources=("chosen", "chosen"), day="2026-09-01")  # checked: not waiting

    each = cards(client)["check_each"]

    assert each["total"] == 1 and each["shown"] == ""
    [line] = each["rows"]
    assert (line["id"], line["date"], line["amount_minor"], line["account"]) == (
        waits, "2026-09-14", 21000, "Sample card 0001")
    assert (line["merchant"], line["service_id"], line["type"], line["type_id"]) == (
        "Sample Travelsite", svc, "Travel", type_id(conn, "Travel"))
    assert (line["usual_book"], line["book"]) == ("Moom", "Kalesh")


# --- case 4: the transfers waiting ------------------------------------------------


def test_case_4_transfers_waiting_as_the_summary_and_the_review_list_give_them(client, conn, card, key):
    out = add_row(conn, card, "TRANSFER TO 0099887766", 25000, service_id=None, book=None, type_name=None,
                  flow="review", day="2026-09-05")
    back = add_row(conn, card, "TRANSFER FROM 0099887766", -4000, service_id=None, book=None, type_name=None,
                   flow="review", day="2026-09-06")

    transfers = cards(client)["transfers"]
    summary = client.get("/api/review").get_json()
    listed = client.get("/api/transactions?flow=review&per_page=500").get_json()

    for k in ("waiting", "waiting_total", "waiting_out_count", "waiting_out_total",
              "waiting_in_count", "waiting_in_total"):
        assert transfers[k] == summary[k], k
    assert transfers["waiting"] == 2 and transfers["total"] == 2
    assert sorted(r["id"] for r in transfers["rows"]) == sorted(t["id"] for t in listed["transactions"]) == sorted(
        [out, back])
    first = next(r for r in transfers["rows"] if r["id"] == out)
    assert (first["date"], first["amount_minor"], first["account"]) == ("2026-09-05", 25000, "Sample card 0001")
    # A transfer is not a row with no type: it is listed once, here.
    assert all(r["id"] not in (out, back) for g in cards(client)["untyped"]["groups"] for r in g["rows"])


# --- case 5: Jev off ------------------------------------------------------------------


def test_case_5_with_jev_off_groups_come_back_blank_and_nothing_is_asked(
    client, conn, book, monkeypatch, never_asked
):
    monkeypatch.delenv(suggest.KEY_ENV, raising=False)

    groups = cards(client)["untyped"]["groups"]

    assert sorted(r["id"] for g in groups for r in g["rows"]) == sorted(book.values())
    assert all((g["route"], g["types"]) == ("blank", []) for g in groups)
    # The name and wording are fin's own and stand without Jev.
    indigo = group_of(cards(client), book["indigo_old"])
    assert (indigo["suggested_name"], indigo["suggested_wording"]) == ("Indigoair", "INDIGOAIR")
    assert never_asked == []


def test_the_read_writes_nothing(client, conn, book, key):
    def state():
        return [
            [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("transactions", "services", "merchant_rules", "change_entries", "suggestion_answers")
        ]

    before = state()
    cards(client)

    assert state() == before


# --- case 8: a large Queue ---------------------------------------------------------------


def test_case_8_two_thousand_rows_with_no_type_come_back_cut_and_say_so(client, conn, card, key, monkeypatch):
    for m in range(1000):
        name = f"SAMPLE STALL {chr(65 + m % 26)}{chr(65 + m // 26 % 26)}{chr(65 + m // 676)}"
        for n in range(2):
            conn.execute(
                "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type)"
                " VALUES (?, '2026-09-10', ?, ?, 'expense')",
                (card, f"{name} 00{n}2 SG", money.to_minor(10 + m)),
            )
    conn.commit()

    statements = []
    real = fin_app.get_connection

    def traced():
        opened = real()
        opened.set_trace_callback(statements.append)
        return opened

    monkeypatch.setattr(fin_app, "get_connection", traced)
    payload = cards(client)

    queue = payload["untyped"]
    shown = sum(len(g["rows"]) for g in queue["groups"])
    assert shown == fin_app.QUEUE_ROW_CAP < 2000
    assert queue["total"] == 2000
    assert queue["shown"] == f"showing {fin_app.QUEUE_ROW_CAP} of 2000"
    assert payload["cut"] == [f"rows with no type: showing {fin_app.QUEUE_ROW_CAP} of 2000"]
    # The biggest come first, as the Queue orders them.
    assert queue["groups"][0]["rows"][0]["amount_minor"] == money.to_minor(1009)
    reads = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(reads) <= 15, reads


def test_a_small_queue_is_not_cut(client, book, key):
    payload = cards(client)

    assert payload["untyped"]["total"] == 4 and payload["untyped"]["shown"] == ""
    assert payload["cut"] == []
