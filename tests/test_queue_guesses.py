"""The Queue's guesses in one read (fin-surfaces 04).

`GET /api/queue/guesses` hands the Queue and Home every row with no type,
grouped by merchant, with the stored route for each group. Driven through the
HTTP interface against the temporary database. Answers are stored directly,
as a send would have stored them; nothing here reaches the network. Every
merchant, figure and name is invented, and the key is a made-up word.
"""

import socket

import pytest

import app as fin_app
import book_type
import money
import suggest

NOT_A_KEY = "not-a-real-key"


@pytest.fixture(autouse=True)
def network(monkeypatch: pytest.MonkeyPatch) -> list:
    """Refuse and record every attempt to open a connection."""
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise OSError("tests make no network calls")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return attempts


@pytest.fixture(autouse=True)
def never_asked(monkeypatch: pytest.MonkeyPatch) -> list:
    """The model, replaced by something that records and refuses: the read
    must never ask it."""
    asked = []

    def refuse(body, key):
        asked.append(body)
        raise AssertionError("the guesses read asked the model")

    monkeypatch.setattr(suggest, "post", refuse)
    return asked


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(suggest.KEY_ENV, NOT_A_KEY)


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(suggest.KEY_ENV, raising=False)


# --- seeding -----------------------------------------------------------------


def statement_on(conn, kind: str, last_four: str) -> int:
    account_id = conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four) VALUES (?, ?, ?, ?)",
        (f"Sample {kind} {last_four}", f"Sample-{last_four}", kind, last_four),
    ).lastrowid
    statement_id = conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename)"
        " VALUES (?, '2026-04-01', 'sample.csv')",
        (account_id,),
    ).lastrowid
    conn.commit()
    return statement_id


@pytest.fixture
def card(conn) -> int:
    """A statement on a card: the only kind of account whose rows are asked about."""
    return statement_on(conn, "card", "0001")


@pytest.fixture
def bank(conn) -> int:
    return statement_on(conn, "bank", "0002")


def row(conn, statement_id: int, description: str, amount: float = 25.0, *,
        flow: str = "expense", service_id: int | None = None,
        type_id: int | None = None, commit: bool = True) -> int:
    tx = conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor,"
        " service_id, type_id, flow_type) VALUES (?, '2026-04-10', ?, ?, ?, ?, ?)",
        (statement_id, description, money.to_minor(amount), service_id, type_id, flow),
    ).lastrowid
    if commit:
        conn.commit()
    return tx


def type_id(conn, name: str) -> int:
    return book_type.spending_type_ids(conn)[name]


def spread(pick: str, top: float, **others: float) -> dict:
    options = list(suggest.question()["criteria"])
    rest = [o for o in options if o != pick and o not in others]
    left = (1.0 - top - sum(others.values())) / len(rest)
    return {o: top if o == pick else others.get(o, left) for o in options}


def answer(conn, merchant: str, pick: str, top: float, *, commit: bool = True,
           **others: float) -> None:
    """An answer held for a merchant string, as a send stores it."""
    suggest.store(conn, {
        "merchant": merchant, "model_id": suggest.MODEL_ID,
        "returned_model_id": suggest.RETURNED_MODEL_ID, "pick": pick,
        "probabilities": spread(pick, top, **others), "confidence": None,
        "input_tokens": 1000, "cost_usd": "0.000042", "latency_ms": 5,
    })
    if commit:
        conn.commit()


def guesses(client) -> list:
    resp = client.get("/api/queue/guesses")
    assert resp.status_code == 200
    return resp.get_json()["groups"]


def by_merchant(groups: list) -> dict:
    return {(g["merchant"], g["route"]): g for g in groups}


def per_row(client, tx_id: int) -> dict:
    resp = client.get(f"/api/transactions/{tx_id}/suggestion")
    assert resp.status_code == 200
    return resp.get_json()


# --- the shape and the grouping ----------------------------------------------


def test_rows_group_by_the_merchant_string_with_one_stored_route_each(client, conn, card, key):
    a = row(conn, card, "LANTERN NOODLE HOUSE 0042 SINGAPORE SG", 18.90)
    b = row(conn, card, "LANTERN NOODLE HOUSE 0077 SG", 12.40)
    c = row(conn, card, "2C2P*HARBOUR KITE SHOP 99812", 61.00)
    d = row(conn, card, "SQ *KOPI CORNER", 6.30)
    e = row(conn, card, "SQ *KOPI CORNER 0003", 6.30)
    answer(conn, "LANTERN NOODLE HOUSE", "Dining", 0.95)
    answer(conn, "HARBOUR KITE SHOP", "Shopping", 0.70, Dining=0.10, Travel=0.05)

    groups = guesses(client)

    assert len(groups) == 3
    lantern = next(g for g in groups if g["merchant"] == "LANTERN NOODLE HOUSE")
    assert sorted(lantern["tx_ids"]) == sorted([a, b])
    assert lantern["route"] == "prefill"
    assert [(t["name"], t["type_id"]) for t in lantern["types"]] == [("Dining", type_id(conn, "Dining"))]
    assert lantern["types"][0]["probability"] == pytest.approx(0.95)
    kite = next(g for g in groups if g["merchant"] == "HARBOUR KITE SHOP")
    assert (kite["tx_ids"], kite["route"]) == ([c], "top3")
    assert [t["name"] for t in kite["types"]] == ["Shopping", "Dining", "Travel"]
    # Case 13: a merchant with no stored answer is a blank group; the card
    # still says "This was…".
    kopi = next(g for g in groups if g["merchant"] == "KOPI CORNER")
    assert sorted(kopi["tx_ids"]) == sorted([d, e])
    assert (kopi["route"], kopi["types"]) == ("blank", [])


def test_a_row_the_gate_stops_groups_by_its_description_upper_cased_and_trimmed(
    client, conn, card, bank, key
):
    a = row(conn, card, "  PayNow Transfer To Sample Person  ", 250.0)
    b = row(conn, card, "PAYNOW TRANSFER TO SAMPLE PERSON", 80.0)
    giro = row(conn, bank, "giro sample utilities ")

    groups = by_merchant(guesses(client))

    assert sorted(groups[("PAYNOW TRANSFER TO SAMPLE PERSON", "blank")]["tx_ids"]) == sorted([a, b])
    assert groups[("GIRO SAMPLE UTILITIES", "blank")]["tx_ids"] == [giro]
    assert all(g["types"] == [] for g in groups.values())


def test_the_read_covers_every_row_with_no_type_and_only_those(client, conn, card, key):
    untyped = [
        row(conn, card, "LANTERN NOODLE HOUSE", 18.90),
        row(conn, card, "MOSSY BOOKS REFUND", -12.00, flow="refund"),
        row(conn, card, "QQQ HOLDINGS", 5.00, flow=None),
    ]
    row(conn, card, "FAIRPRICE FINEST", 61.40, type_id=type_id(conn, "Groceries"))
    row(conn, card, "PAYMENT RECEIVED THANK YOU", -400.0, flow="payment")
    row(conn, card, "TRANSFER TO OWN ACCOUNT", 100.0, flow="review")
    hidden = conn.execute(
        "INSERT INTO services (name, exclude_from_expense_views) VALUES ('Hidden Sample', 1)"
    ).lastrowid
    row(conn, card, "HIDDEN SAMPLE SHOP", 9.0, service_id=hidden)

    listed = client.get("/api/transactions?types=__untyped__&per_page=500").get_json()
    read = [tx for g in guesses(client) for tx in g["tx_ids"]]

    assert sorted(read) == sorted(untyped)
    assert sorted(read) == sorted(t["id"] for t in listed["transactions"])


# --- the same route as the per-row read --------------------------------------


@pytest.fixture
def mixed_book(conn, card, bank) -> list:
    """Rows whose routes differ for every reason the per-row read has."""
    service = conn.execute("INSERT INTO services (name) VALUES ('Sample Noodles')").lastrowid
    rows = [
        row(conn, card, "LANTERN NOODLE HOUSE 0042 SG"),               # prefill
        row(conn, card, "LANTERN NOODLE HOUSE 0077 SG", service_id=service),  # merchant set
        row(conn, bank, "LANTERN NOODLE HOUSE"),                         # stopped: a bank row
        row(conn, card, "HARBOUR KITE SHOP"),                            # top3
        row(conn, card, "QQQ HOLDINGS"),                                 # none of these
        row(conn, card, "MOSSY BOOKS"),                                  # no stored answer
        row(conn, card, "PAYNOW TRANSFER TO SAMPLE PERSON"),             # stopped: a payee
    ]
    answer(conn, "LANTERN NOODLE HOUSE", "Dining", 0.95)
    answer(conn, "HARBOUR KITE SHOP", "Shopping", 0.70, Dining=0.10, Travel=0.05)
    answer(conn, "QQQ HOLDINGS", suggest.NONE_OF_THESE, 0.95)
    return rows


@pytest.mark.parametrize("with_key", [True, False], ids=["key", "no key"])
def test_a_rows_route_through_the_read_equals_its_route_through_the_per_row_read(
    client, mixed_book, monkeypatch, with_key
):
    if with_key:
        monkeypatch.setenv(suggest.KEY_ENV, NOT_A_KEY)
    else:
        monkeypatch.delenv(suggest.KEY_ENV, raising=False)

    groups = guesses(client)
    seen = {tx: g for g in groups for tx in g["tx_ids"]}

    assert sorted(seen) == sorted(mixed_book)
    for tx in mixed_book:
        one = per_row(client, tx)
        assert (seen[tx]["route"], seen[tx]["types"]) == (one["route"], one["types"]), tx
    routes = [seen[tx]["route"] for tx in mixed_book]
    if with_key:
        assert routes == ["prefill", "blank", "blank", "top3", "blank", "blank", "blank"]
    else:
        assert set(routes) == {"blank"}


def test_a_row_whose_merchant_is_set_groups_apart_from_the_asked_rows(client, conn, mixed_book, key):
    prefill, merchant_set, bank_row = mixed_book[:3]

    lantern = [g for g in guesses(client) if g["merchant"] == "LANTERN NOODLE HOUSE"]

    assert sorted((g["route"], sorted(g["tx_ids"])) for g in lantern) == [
        ("blank", sorted([merchant_set, bank_row])), ("prefill", [prefill]),
    ]


# --- a stored answer naming a type that is gone --------------------------------


def stale_answer(conn, merchant: str, probabilities: dict) -> None:
    """An answer held from before a type was renamed or removed: its pick
    names a type the book no longer has."""
    suggest.store(conn, {
        "merchant": merchant, "model_id": suggest.MODEL_ID,
        "returned_model_id": suggest.RETURNED_MODEL_ID, "pick": next(iter(probabilities)),
        "probabilities": probabilities, "confidence": None,
        "input_tokens": 1000, "cost_usd": "0.000042", "latency_ms": 5,
    })
    conn.commit()


def test_a_stored_pick_naming_a_type_that_is_gone_reads_blank_and_spares_the_rest(
    client, conn, card, key
):
    gone = row(conn, card, "LANTERN NOODLE HOUSE 0042 SG", 18.90)
    half = row(conn, card, "HARBOUR KITE SHOP", 61.00)
    kept = row(conn, card, "MOSSY BOOKS 0003", 9.00)
    stale_answer(conn, "LANTERN NOODLE HOUSE", {"Eating out": 0.95, "Dining": 0.03, "Travel": 0.02})
    stale_answer(conn, "HARBOUR KITE SHOP", {"Eating out": 0.60, "Shopping": 0.25, "Travel": 0.15})
    answer(conn, "MOSSY BOOKS", "Shopping", 0.95)

    groups = {g["merchant"]: g for g in guesses(client)}

    assert (groups["LANTERN NOODLE HOUSE"]["route"], groups["LANTERN NOODLE HOUSE"]["types"]) == ("blank", [])
    # Three offered, one of them gone: the two left are still offered.
    assert groups["HARBOUR KITE SHOP"]["route"] == "top3"
    assert [t["name"] for t in groups["HARBOUR KITE SHOP"]["types"]] == ["Shopping", "Travel"]
    assert groups["MOSSY BOOKS"]["route"] == "prefill"
    assert [(t["name"], t["type_id"]) for t in groups["MOSSY BOOKS"]["types"]] == [("Shopping", type_id(conn, "Shopping"))]
    seen = {tx: g for g in groups.values() for tx in g["tx_ids"]}
    for tx in (gone, half, kept):
        one = per_row(client, tx)
        assert (seen[tx]["route"], seen[tx]["types"]) == (one["route"], one["types"]), tx


# --- it writes nothing and never asks -----------------------------------------


def test_the_read_writes_nothing_and_never_asks_the_model(
    client, conn, mixed_book, key, never_asked, network
):
    def state():
        return (
            [tuple(r) for r in conn.execute("SELECT * FROM change_entries ORDER BY id")],
            [tuple(r) for r in conn.execute("SELECT * FROM change_rows ORDER BY rowid")],
            [tuple(r) for r in conn.execute("SELECT * FROM suggestion_answers ORDER BY id")],
            [tuple(r) for r in conn.execute("SELECT * FROM transactions ORDER BY id")],
        )

    before = state()
    guesses(client)
    guesses(client)

    assert state() == before
    assert never_asked == [] and network == []


# --- case 24: hundreds of rows, one read --------------------------------------


def test_case_24_one_read_covers_800_untyped_rows_in_a_few_queries(
    client, conn, card, key, monkeypatch
):
    rows = []
    for m in range(400):
        name = f"SAMPLE STALL {chr(65 + m % 26)}{chr(65 + m // 26 % 26)}{chr(65 + m // 676)}"
        rows.append(row(conn, card, f"{name} 0042 SG", 10 + m, commit=False))
        rows.append(row(conn, card, f"{name} 0077 SG", 5 + m, commit=False))
        if m % 2 == 0:
            answer(conn, name, "Dining", 0.95 if m % 4 == 0 else 0.70, commit=False)
    conn.commit()

    statements = []
    real = fin_app.get_connection

    def traced():
        opened = real()
        opened.set_trace_callback(statements.append)
        return opened

    monkeypatch.setattr(fin_app, "get_connection", traced)
    groups = guesses(client)

    assert len(groups) == 400
    assert sorted(tx for g in groups for tx in g["tx_ids"]) == sorted(rows)
    assert all(len(g["tx_ids"]) == 2 for g in groups)
    routes = [g["route"] for g in groups]
    assert (routes.count("prefill"), routes.count("top3"), routes.count("blank")) == (100, 100, 200)
    reads = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(reads) <= 5, reads
