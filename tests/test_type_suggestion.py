"""Type suggestion for merchants no rule knows.

Driven through the HTTP interface against the temporary database. No test
here reaches the network: the outside model is reached through one function,
`suggest.post`, which these tests replace, and every socket connection is
refused and recorded. Every merchant, figure and name here is invented, and
the key is a made-up word.
"""

import io
import json
import logging
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import account_kind
import book_type
import db
import money
import parsers
import suggest
from parse_dbs import ParsedStatement, ParsedTransaction

NOT_A_KEY = "not-a-real-key"


# --- no network, ever --------------------------------------------------------


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


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(suggest.KEY_ENV, raising=False)


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(suggest.KEY_ENV, NOT_A_KEY)


# --- the outside model, replaced ---------------------------------------------


def spread(pick: str, top: float, **others: float) -> dict:
    """A probability for every option: `top` on the pick, the named others as
    given, and what is left shared evenly over the rest."""
    options = list(suggest.question()["criteria"])
    rest = [o for o in options if o != pick and o not in others]
    left = (1.0 - top - sum(others.values())) / len(rest)
    return {o: top if o == pick else others.get(o, left) for o in options}


class FakeModel:
    """Stands in for `suggest.post`: records each request and answers from a
    table keyed by the merchant string."""

    def __init__(self):
        self.requests = []
        self.keys = []
        self.answers = {}
        self.fail_on = set()
        self.returned_model = suggest.RETURNED_MODEL_ID

    def answer(self, merchant: str, pick: str, top: float, **others: float) -> None:
        self.answers[merchant] = (pick, spread(pick, top, **others))

    def __call__(self, body: dict, key: str) -> dict:
        self.requests.append(body)
        self.keys.append(key)
        merchant = body["state"]["merchant"]
        if merchant in self.fail_on:
            raise RuntimeError(f"upstream said no to {merchant} with key {key}")
        pick, probabilities = self.answers.get(
            merchant, (suggest.NONE_OF_THESE, spread(suggest.NONE_OF_THESE, 0.30))
        )
        n = len(probabilities)
        return {
            "model": self.returned_model,
            "answers": {"type": {
                "type": "choice",
                "choice": pick,
                "probabilities": probabilities,
                "confidence": (n * probabilities[pick] - 1) / (n - 1),
            }},
            "usage": {"input_tokens": 1000, "output_tokens": 4},
        }

    @property
    def asked(self) -> list:
        return [body["state"]["merchant"] for body in self.requests]


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr(suggest, "post", fake)
    return fake


# --- seeding and reading -----------------------------------------------------


def type_id(conn, name: str) -> int:
    return book_type.spending_type_ids(conn)[name]


def statement_on(conn, kind: str, name: str = "Sample Card 0001",
                 short_name: str = "Sample-0001", last_four: str = "0001") -> int:
    """A statement on a new account of the given kind."""
    account_id = conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four) VALUES (?, ?, ?, ?)",
        (name, short_name, kind, last_four),
    ).lastrowid
    statement_id = conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename)"
        " VALUES (?, '2026-04-01', 'sample.csv')",
        (account_id,),
    ).lastrowid
    conn.commit()
    return statement_id


@pytest.fixture
def statement(conn) -> int:
    """A statement on a card: the only kind of account whose rows are sent."""
    return statement_on(conn, "card")


def row(conn, statement_id: int, description: str, amount: float = 25.0, *,
        flow: str = "expense", service_id: int | None = None) -> int:
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor,"
        " service_id, flow_type) VALUES (?, '2026-04-10', ?, ?, ?, ?)",
        (statement_id, description, money.to_minor(amount), service_id, flow),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def shown(client, tx_id: int) -> dict:
    payload = client.get("/api/transactions?per_page=500").get_json()
    return next(t for t in payload["transactions"] if t["id"] == tx_id)


def answers(client) -> dict:
    """The stored answers, keyed by merchant string."""
    return {a["merchant"]: a for a in client.get("/api/suggestions/answers").get_json()}


def strings_file() -> Path:
    return Path(db.DB_PATH).parent / suggest.STRINGS_FILE


def prepare_and_send(client) -> dict:
    assert client.post("/api/suggestions/prepare").status_code == 200
    resp = client.post("/api/suggestions/send")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


@pytest.fixture
def fake_statement():
    """Register a parser that returns the given rows for any .csv upload."""
    def install(rows):
        def parse(_path):
            return [ParsedStatement(
                statement_type="credit_card",
                statement_date="2026-04-01",
                accounts=["Sample Card 0001"],
                filename="sample.csv",
                transactions=[
                    ParsedTransaction(date=day, description=description,
                                      amount_minor=money.to_minor(amount))
                    for day, description, amount in rows
                ],
            )]
        parsers._PARSERS.insert(0, {
            "name": "Sample Test CSV", "ext": ".csv",
            "detect_fn": lambda _path: True, "parse_fn": parse,
        })

    yield install
    parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Sample Test CSV"]


def upload(client) -> dict:
    resp = client.post(
        "/api/import/upload",
        data={"files": (io.BytesIO(b"sample"), "sample.csv")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_json()
    preview = resp.get_json()
    preview.pop("import_id")  # a counter: one higher on every upload
    return preview


SAMPLE_ROWS = [
    ("2026-04-03", "FAIRPRICE FINEST 0042", 61.40),
    ("2026-04-04", "LANTERN NOODLE HOUSE SINGAPORE SG", 18.90),
    ("2026-04-05", "PAYNOW TRANSFER TO SAMPLE PERSON", 250.00),
]


# --- P24: with no key and no network the import preview is unchanged ---------


def test_with_no_key_and_no_network_the_import_preview_is_unchanged_and_nothing_is_called(
    client, conn, fake_statement, network, monkeypatch
):
    fake_statement(SAMPLE_ROWS)

    # The preview with the feature on, and its model standing by to answer.
    standing_by = FakeModel()
    with pytest.MonkeyPatch.context() as feature_on:
        feature_on.setenv(suggest.KEY_ENV, NOT_A_KEY)
        feature_on.setattr(suggest, "post", standing_by)
        with_key = upload(client)

    # The preview with no key and the real `post` in place: anything that
    # tried to reach the model would open a connection, and be recorded.
    monkeypatch.delenv(suggest.KEY_ENV, raising=False)
    without_key = upload(client)

    assert without_key == with_key
    unknown = next(
        e for e in without_key["groups"][0]["transactions"]
        if e["description"].startswith("LANTERN")
    )
    assert (unknown["type_id"], unknown["type_name"], unknown["status"]) == (None, None, "untyped")
    assert without_key["stats"] == {
        "total": 3, "typed": 1, "untyped": 2, "skipped": 0, "review_each_time": 0,
    }
    assert standing_by.requests == []  # the upload never asks, key or no key
    assert network == []


def test_with_no_key_the_feature_is_off_and_refuses_without_writing_or_calling(
    client, conn, statement, no_key, network
):
    tx = row(conn, statement, "LANTERN NOODLE HOUSE")

    assert client.get("/api/suggestions/status").get_json()["enabled"] is False
    prepared = client.post("/api/suggestions/prepare")
    sent = client.post("/api/suggestions/send")

    assert prepared.status_code == 409 and sent.status_code == 409
    assert suggest.KEY_ENV in prepared.get_json()["error"]
    assert not strings_file().exists()
    assert client.get(f"/api/transactions/{tx}/suggestion").get_json()["route"] == "blank"
    assert network == []


def test_an_answer_stored_earlier_is_not_shown_once_the_key_is_gone(
    client, conn, statement, key, model, monkeypatch
):
    tx = row(conn, statement, "LANTERN NOODLE HOUSE")
    model.answer("LANTERN NOODLE HOUSE", "Dining", 0.95)
    prepare_and_send(client)
    assert client.get(f"/api/transactions/{tx}/suggestion").get_json()["route"] == "prefill"

    monkeypatch.delenv(suggest.KEY_ENV)

    assert client.get(f"/api/transactions/{tx}/suggestion").get_json() == {
        "route": "blank", "types": [], "merchant": None,
    }


# --- the code gate and the two steps -----------------------------------------


def test_prepare_writes_only_merchant_strings_that_pass_the_gate_and_sends_nothing(
    client, conn, statement, key, model
):
    known = conn.execute(
        "INSERT INTO services (name, book, type_id) VALUES ('Sample Grocer', 'Household', ?)",
        (type_id(conn, "Groceries"),),
    ).lastrowid
    row(conn, statement, "LANTERN NOODLE HOUSE 0042 SINGAPORE SG")
    row(conn, statement, "LANTERN NOODLE HOUSE 0077 SG")           # the same merchant
    row(conn, statement, "2C2P*HARBOUR KITE SHOP 99812")           # processor prefix
    row(conn, statement, "PAYPAL *MOSSY BOOKS 4029357733 GB")
    row(conn, statement, "PAYNOW TRANSFER TO SAMPLE PERSON")       # payee shape
    row(conn, statement, "FAST PAYMENT SAMPLE PERSON")
    row(conn, statement, "GIRO SAMPLE TOWN COUNCIL")
    row(conn, statement, "ATM WITHDRAWAL 0042")
    row(conn, statement, "ANNUAL FEE")                             # bare fee lines
    row(conn, statement, "GST")
    row(conn, statement, "LATE CHARGE")
    row(conn, statement, "PROGRAMME ADMIN FEE")
    row(conn, statement, "0042 99812 SG")                          # nothing readable
    row(conn, statement, "SAMPLE EMPLOYER PAYROLL", -4000.0, flow="income")
    row(conn, statement, "SAMPLE GROCER 0042", service_id=known)   # a rule knows it
    typed = row(conn, statement, "CORNER STALL")
    conn.execute("UPDATE transactions SET type_id = ? WHERE id = ?",
                 (type_id(conn, "Dining"), typed))
    conn.commit()

    resp = client.post("/api/suggestions/prepare")

    assert resp.status_code == 200, resp.get_json()
    expected = ["HARBOUR KITE SHOP", "LANTERN NOODLE HOUSE", "MOSSY BOOKS"]
    body = resp.get_json()
    assert (body["count"], body["merchants"]) == (3, expected)
    assert Path(body["file"]) == strings_file()
    lines = [
        line for line in strings_file().read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == expected
    assert model.requests == []  # the first click sends nothing


def test_send_is_refused_until_the_strings_have_been_prepared(
    client, conn, statement, key, model
):
    row(conn, statement, "LANTERN NOODLE HOUSE")

    resp = client.post("/api/suggestions/send")

    assert resp.status_code == 409
    assert model.requests == []


def test_send_asks_once_per_distinct_merchant_and_sends_only_the_string_and_the_question(
    client, conn, statement, key, model
):
    row(conn, statement, "LANTERN NOODLE HOUSE 0042 SINGAPORE SG", 18.90)
    row(conn, statement, "LANTERN NOODLE HOUSE 0077 SG", 1234.56)
    row(conn, statement, "PAYPAL *MOSSY BOOKS 4029357733 GB", 7.77)

    result = prepare_and_send(client)

    assert (result["sent"], result["stopped"]) == (2, None)
    assert model.asked == ["LANTERN NOODLE HOUSE", "MOSSY BOOKS"]
    assert set(model.keys) == {NOT_A_KEY}
    for body in model.requests:
        assert set(body) == {"state", "model", "questions"}
        assert set(body["state"]) == {"merchant"}
        assert body["model"] == suggest.MODEL_ID
        assert set(body["questions"]) == {"type"}
        sent_text = json.dumps(body)
        for never in ("0042", "0077", "4029357733", "18.9", "1234.56", "7.77",
                      "2026-04-10", "Sample Card", "0001", "Household", "Moom", "Kalesh"):
            assert never not in sent_text
    asked_over = model.requests[0]["questions"]["type"]
    assert asked_over["type"] == "choice"
    declared = [
        book_type.display_name(name, parent)
        for name, parent, *_ in book_type.SPENDING_TYPES if name != "Other"
    ]
    assert list(asked_over["criteria"]) == declared + [suggest.NONE_OF_THESE]
    assert asked_over["criteria"]["Dining"] == {
        "what": "restaurants, bars, fast food, food delivery, coffee shops, cafes, bakeries and drinks shops",
        "not_for": "supermarkets",
    }


def test_the_operator_can_strike_a_line_from_the_file_and_it_is_not_sent(
    client, conn, statement, key, model
):
    row(conn, statement, "LANTERN NOODLE HOUSE")
    row(conn, statement, "SAMPLE PERSON")  # a name: only the operator can tell
    assert client.post("/api/suggestions/prepare").status_code == 200
    kept = [
        line for line in strings_file().read_text(encoding="utf-8").splitlines()
        if line != "SAMPLE PERSON"
    ]
    strings_file().write_text("\n".join(kept + ["A LINE THE OPERATOR ADDED"]) + "\n", encoding="utf-8")

    resp = client.post("/api/suggestions/send")

    assert resp.status_code == 200
    assert model.asked == ["LANTERN NOODLE HOUSE"]  # nor anything fin did not put there


# --- the answer record and its reuse -----------------------------------------


def test_the_answer_is_stored_in_full_with_the_model_and_versions(
    client, conn, statement, key, model
):
    row(conn, statement, "LANTERN NOODLE HOUSE")
    model.answer("LANTERN NOODLE HOUSE", "Dining", 0.93)

    prepare_and_send(client)

    stored = answers(client)["LANTERN NOODLE HOUSE"]
    assert stored["pick"] == "Dining"
    assert stored["probabilities"] == spread("Dining", 0.93)
    assert sum(stored["probabilities"].values()) == pytest.approx(1.0)
    n = len(stored["probabilities"])
    assert stored["confidence"] == pytest.approx((n * 0.93 - 1) / (n - 1))
    assert (stored["model_id"], stored["returned_model_id"]) == (
        suggest.MODEL_ID, suggest.RETURNED_MODEL_ID,
    )
    assert (stored["cleaning_version"], stored["question_version"]) == (
        suggest.CLEANING_VERSION, suggest.QUESTION_VERSION,
    )
    assert stored["input_tokens"] == 1000
    assert stored["cost_usd"] == "0.000042"  # 1000 tokens at $0.042 a million
    assert isinstance(stored["latency_ms"], int) and stored["latency_ms"] >= 0
    assert (stored["chosen_type"], stored["suggestion_visible"]) == (None, None)


def test_a_stored_answer_is_reused_and_the_merchant_is_not_asked_again(
    client, conn, statement, key, model
):
    row(conn, statement, "LANTERN NOODLE HOUSE 0042")
    prepare_and_send(client)
    row(conn, statement, "LANTERN NOODLE HOUSE 0099 SG")  # a later import, same merchant
    row(conn, statement, "MOSSY BOOKS")

    again = client.post("/api/suggestions/prepare").get_json()
    result = client.post("/api/suggestions/send").get_json()

    assert again["merchants"] == ["MOSSY BOOKS"]
    assert result["sent"] == 1
    assert model.asked == ["LANTERN NOODLE HOUSE", "MOSSY BOOKS"]


def test_the_first_failed_call_stops_the_batch_and_says_only_what_kind_of_failure(
    client, conn, statement, key, model
):
    row(conn, statement, "HARBOUR KITE SHOP")
    row(conn, statement, "LANTERN NOODLE HOUSE")
    row(conn, statement, "MOSSY BOOKS")
    model.fail_on = {"LANTERN NOODLE HOUSE"}

    result = prepare_and_send(client)

    assert model.asked == ["HARBOUR KITE SHOP", "LANTERN NOODLE HOUSE"]  # the third never asked
    assert result["sent"] == 1
    assert result["stopped"] == "the call failed (RuntimeError)"
    assert NOT_A_KEY not in json.dumps(result) and "upstream" not in json.dumps(result)
    assert list(answers(client)) == ["HARBOUR KITE SHOP"]
    assert strings_file().exists()  # still there for the next try


def test_an_answer_from_a_different_model_id_stops_the_batch_and_is_never_shown(
    client, conn, statement, key, model
):
    tx = row(conn, statement, "LANTERN NOODLE HOUSE")
    row(conn, statement, "MOSSY BOOKS")
    model.answer("LANTERN NOODLE HOUSE", "Dining", 0.97)
    model.returned_model = "typesafe/jev-9.99-20991231"

    result = prepare_and_send(client)

    assert model.asked == ["LANTERN NOODLE HOUSE"]
    assert "typesafe/jev-9.99-20991231" in result["stopped"]
    assert answers(client)["LANTERN NOODLE HOUSE"]["returned_model_id"] == "typesafe/jev-9.99-20991231"
    assert client.get(f"/api/transactions/{tx}/suggestion").get_json()["route"] == "blank"


# --- routing into the resolve dialog -----------------------------------------


def suggestion(client, tx_id: int) -> dict:
    resp = client.get(f"/api/transactions/{tx_id}/suggestion")
    assert resp.status_code == 200
    return resp.get_json()


@pytest.mark.parametrize("top, route", [
    (0.97, "prefill"), (0.90, "prefill"), (0.89, "top3"), (0.50, "top3"), (0.49, "blank"),
])
def test_the_top_probability_decides_what_the_resolve_dialog_is_given(
    client, conn, statement, key, model, top, route
):
    tx = row(conn, statement, "LANTERN NOODLE HOUSE 0042 SG")
    model.answer("LANTERN NOODLE HOUSE", "Dining", top, Groceries=0.05, **{"Fitness > Golf": 0.03})
    prepare_and_send(client)

    got = suggestion(client, tx)

    assert got["route"] == route
    offered = [(t["name"], t["type_id"]) for t in got["types"]]
    if route == "prefill":
        assert offered == [("Dining", type_id(conn, "Dining"))]
        assert got["types"][0]["probability"] == pytest.approx(top)
    elif route == "top3":
        assert offered == [
            ("Dining", type_id(conn, "Dining")),
            ("Groceries", type_id(conn, "Groceries")),
            ("Fitness > Golf", type_id(conn, "Fitness > Golf")),
        ]
    else:
        assert offered == []


def test_none_of_these_is_blank_however_sure_and_is_never_one_of_the_three(
    client, conn, statement, key, model
):
    sure = row(conn, statement, "QQQ HOLDINGS")
    unsure = row(conn, statement, "LANTERN NOODLE HOUSE")
    model.answer("QQQ HOLDINGS", suggest.NONE_OF_THESE, 0.95)
    model.answer("LANTERN NOODLE HOUSE", "Dining", 0.55,
                 **{suggest.NONE_OF_THESE: 0.20, "Groceries": 0.10, "Travel": 0.05})
    prepare_and_send(client)

    assert suggestion(client, sure) == {"route": "blank", "types": [], "merchant": "QQQ HOLDINGS"}
    assert [t["name"] for t in suggestion(client, unsure)["types"]] == ["Dining", "Groceries", "Travel"]


def test_a_row_with_no_stored_answer_or_stopped_by_the_gate_gets_a_blank_dialog(
    client, conn, statement, key, model
):
    never_asked = row(conn, statement, "MOSSY BOOKS")
    payee = row(conn, statement, "PAYNOW TRANSFER TO SAMPLE PERSON")

    assert suggestion(client, never_asked)["route"] == "blank"
    assert suggestion(client, payee) == {"route": "blank", "types": [], "merchant": None}
    assert client.get("/api/transactions/999999/suggestion").status_code == 404
    assert model.requests == []  # opening the dialog never asks


# --- afterwards: what the operator chose -------------------------------------


def test_a_confirmed_suggestion_is_the_operators_label_and_the_record_says_it_was_seen(
    client, conn, statement, key, model
):
    tx = row(conn, statement, "LANTERN NOODLE HOUSE 0042 SG")
    model.answer("LANTERN NOODLE HOUSE", "Dining", 0.95)
    prepare_and_send(client)
    offered = suggestion(client, tx)["types"][0]["type_id"]

    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx,
        "service_name": "Lantern Noodle House",
        "type_id": offered,
        "apply_scope": "transaction",
        "suggestion_visible": True,
    })

    assert resp.status_code == 200, resp.get_json()
    labelled = shown(client, tx)
    assert (labelled["book"], labelled["display_type"], labelled["cat_source"]) == (
        "Household", "Dining", "manual",
    )
    stored = answers(client)["LANTERN NOODLE HOUSE"]
    assert (stored["chosen_type"], stored["suggestion_visible"]) == ("Dining", True)


def test_a_corrected_or_unseen_suggestion_records_the_type_the_operator_chose(
    client, conn, statement, key, model
):
    corrected = row(conn, statement, "LANTERN NOODLE HOUSE")
    unseen = row(conn, statement, "MOSSY BOOKS")
    model.answer("LANTERN NOODLE HOUSE", "Dining", 0.95)
    model.answer("MOSSY BOOKS", "Shopping", 0.30)  # below the line: nothing was shown
    prepare_and_send(client)

    for tx, name, chosen, visible in (
        (corrected, "Lantern Noodle House", "Groceries", True),
        (unseen, "Mossy Books", "Shopping", False),
    ):
        resp = client.post("/api/transactions/resolve", json={
            "tx_id": tx, "service_name": name, "type_id": type_id(conn, chosen),
            "apply_scope": "transaction", "suggestion_visible": visible,
        })
        assert resp.status_code == 200, resp.get_json()

    stored = answers(client)
    assert (stored["LANTERN NOODLE HOUSE"]["pick"], stored["LANTERN NOODLE HOUSE"]["chosen_type"],
            stored["LANTERN NOODLE HOUSE"]["suggestion_visible"]) == ("Dining", "Groceries", True)
    assert (stored["MOSSY BOOKS"]["chosen_type"], stored["MOSSY BOOKS"]["suggestion_visible"]) == (
        "Shopping", False,
    )


def test_the_first_choice_for_a_merchant_is_the_one_kept(client, conn, statement, key, model):
    first = row(conn, statement, "LANTERN NOODLE HOUSE 0042")
    second = row(conn, statement, "LANTERN NOODLE HOUSE 0077")
    model.answer("LANTERN NOODLE HOUSE", "Dining", 0.95)
    prepare_and_send(client)

    for tx, chosen, visible in ((first, "Dining", True), (second, "Travel", False)):
        client.post("/api/transactions/resolve", json={
            "tx_id": tx, "service_name": "Lantern Noodle House", "type_id": type_id(conn, chosen),
            "apply_scope": "transaction", "suggestion_visible": visible,
        })

    stored = answers(client)["LANTERN NOODLE HOUSE"]
    assert (stored["chosen_type"], stored["suggestion_visible"]) == ("Dining", True)


def test_the_suggestion_never_files_a_row_or_makes_a_merchant_or_a_rule(
    client, conn, statement, key, model
):
    tx = row(conn, statement, "LANTERN NOODLE HOUSE")
    model.answer("LANTERN NOODLE HOUSE", "Dining", 0.99)
    merchants = len(client.get("/api/services").get_json())
    rules = len(client.get("/api/rules").get_json())

    prepare_and_send(client)
    suggestion(client, tx)

    after = shown(client, tx)
    assert (after["type_id"], after["service_id"], after["cat_source"]) == (None, None, "auto")
    assert len(client.get("/api/services").get_json()) == merchants
    assert len(client.get("/api/rules").get_json()) == rules


# =============================================================================
# Gate proofs G1 to G9 (ticket 22)
# =============================================================================
# Each is driven through the batch's own endpoints. Where the proof is about
# what leaves, the stand-in sits one layer below `suggest.post`, at urllib's
# `urlopen`: the real `ask` builds the request and the real `post` serialises
# it, and the assertion is on those bytes or on how many requests there were.
# G9 is proved by the two tests above that confirm and correct a suggestion.


class Wire:
    """Stands in for `urllib.request.urlopen`. Keeps every request object it is
    handed, and answers through a FakeModel unless told to fail."""

    def __init__(self):
        self.model = FakeModel()
        self.sent = []
        self.failure = None
        self.raw_reply = None

    def __call__(self, request, timeout=None):
        self.sent.append(request)
        if self.failure is not None:
            raise self.failure
        if self.raw_reply is not None:
            return io.BytesIO(self.raw_reply)
        sent_key = request.get_header("Authorization").removeprefix("Bearer ")
        reply = self.model(json.loads(request.data), sent_key)
        return io.BytesIO(json.dumps(reply).encode("utf-8"))

    @property
    def bodies(self) -> list:
        """The serialised body of every request, as text."""
        return [request.data.decode("utf-8") for request in self.sent]

    @property
    def asked(self) -> list:
        return [json.loads(body)["state"]["merchant"] for body in self.bodies]


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    fake = Wire()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


def approve(*lines: str) -> None:
    """Write the strings file as if the operator had read it and left these
    lines in: the most a send could ever be allowed to take from it."""
    strings_file().write_text(suggest.FILE_HEADER + "\n".join(lines) + "\n", encoding="utf-8")


def send_with_everything_approved(client, description: str) -> dict:
    """Send with a file that approves the row's text raw, upper-cased and
    cleaned. Whatever stops the row then, it is the gate and not the file."""
    upper = " ".join(description.upper().split())
    approve(description, upper, suggest.clean(description))
    resp = client.post("/api/suggestions/send")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


# --- G1: forbidden content never leaves --------------------------------------


def test_g1_the_serialised_body_holds_only_the_merchant_string_the_model_id_and_the_question(
    client, conn, statement, key, wire
):
    # Two rows of one merchant, each printing a date, an amount, a card
    # fragment and a reference number in its own way; and one other row.
    for day, description, amount in (
        ("2026-04-17", "LANTERN NOODLE HOUSE 17 APR REF:88120457 XXXX-0001 SGD 4321.09 SINGAPORE SG", 4321.09),
        ("2026-04-18", "2C2P*LANTERN NOODLE HOUSE 18APR26 88120458 XXXX0001 4321.09 SG", 4321.09),
        ("2026-04-19", "MOSSY BOOKS 19/04 77003131", 12.34),
    ):
        conn.execute(
            "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type)"
            " VALUES (?, ?, ?, ?, 'expense')",
            (statement, day, description, money.to_minor(amount)),
        )
    conn.commit()

    result = prepare_and_send(client)

    assert (result["sent"], result["stopped"]) == (2, None)
    assert wire.asked == ["LANTERN NOODLE HOUSE", "MOSSY BOOKS"]
    request, body = wire.sent[0], wire.bodies[0]
    # The whole body, and nothing beside it.
    assert json.loads(body) == {
        "state": {"merchant": "LANTERN NOODLE HOUSE"},
        "model": suggest.MODEL_ID,
        "questions": {"type": suggest.question()},
    }
    asked_question = json.dumps(suggest.question())
    assert body.replace(asked_question, "<question>").replace(suggest.MODEL_ID, "<model>") == (
        '{"state": {"merchant": "LANTERN NOODLE HOUSE"}, "model": "<model>",'
        ' "questions": {"type": <question>}}'
    )
    # None of what the rows carried, in any of the ways it was printed.
    for never in (
        "4321.09", "4321", "12.34",                      # amounts
        "2026-04-17", "2026-04-18", "2026-04-19", "18APR26", "19/04", "2026-04-10",
        "0001", "XXXX", "Sample Card", "Sample-0001",    # the card
        "88120457", "88120458", "77003131", "REF",       # reference numbers
        "MOSSY", "sample.csv", "2026-04-01", *book_type.BOOK_NAMES,
    ):
        assert never not in body, never
    assert not re.search(r"\b(APR|SGD|SG)\b", body)
    # The question is the same for every merchant: it is built from no row.
    assert wire.bodies[1].replace("MOSSY BOOKS", "LANTERN NOODLE HOUSE") == body
    # Nothing rides outside the body either.
    assert request.full_url == suggest.ENDPOINT and request.get_method() == "POST"
    assert dict(request.header_items()) == {
        "Authorization": f"Bearer {NOT_A_KEY}", "Content-type": "application/json",
    }


# --- G2: forbidden rows never produce a request ------------------------------


def test_the_same_steps_do_send_an_ordinary_merchant(client, conn, statement, key, wire):
    """The control for the zero-request proofs below."""
    row(conn, statement, "LANTERN NOODLE HOUSE 0042 SG")

    result = send_with_everything_approved(client, "LANTERN NOODLE HOUSE 0042 SG")

    assert result["sent"] == 1 and wire.asked == ["LANTERN NOODLE HOUSE"]


PAYEE_AND_TRANSFER_ROWS = [
    ("PayNow", "PAYNOW TO SAMPLE PERSON"),
    ("PayNow", "PAYNOW-FAST SAMPLE PERSON OTHR 88120457"),
    ("PayNow", "PAY NOW SAMPLE PERSON"),
    ("FAST", "FAST PAYMENT SAMPLE PERSON"),
    ("FAST", "INCOMING FAST SAMPLE PERSON"),
    ("GIRO", "GIRO SAMPLE TOWN COUNCIL"),
    ("GIRO", "INTERBANK GIRO SAMPLE INSURER"),
    ("transfer", "FUNDS TRANSFER SAMPLE PERSON"),
    ("transfer", "TRF TO SAMPLE PERSON"),
    ("transfer", "TRANSFER 438-00000-1 SAMPLE PERSON"),
    ("ATM", "ATM WITHDRAWAL 0042"),
    ("ATM", "CASH WITHDRAWAL SAMPLE MALL"),
    ("card payment", "CARD PAYMENT"),
    ("card payment", "PAYMENT - THANK YOU"),
    ("card payment", "BILL PAYMENT - DBS INTERNET/WIRELESS"),
    ("card payment", "PAYMT THRU E-BANK/HOMEB/CYBERB"),
    ("card payment", "PAYMENT TO SAMPLE CREDIT CARD"),
    ("card payment", "CREDIT CARD PAYMENT 4417"),
    ("card payment", "SAMPLE BANK CARDS PAYMENT"),
    ("card payment", "CC PAYMENT SAMPLE BANK"),
]


@pytest.mark.parametrize("kind, description", PAYEE_AND_TRANSFER_ROWS)
def test_g2_a_payee_transfer_atm_or_card_payment_row_produces_no_request(
    client, conn, statement, key, wire, kind, description
):
    row(conn, statement, description)  # filed as spending: only its wording stops it

    assert client.post("/api/suggestions/prepare").get_json()["merchants"] == []
    send_with_everything_approved(client, description)

    assert wire.sent == []


@pytest.mark.parametrize("flow", ["income", "transfer", "payment", "refund", "movement", "review", None])
def test_g2_a_row_that_is_not_spending_produces_no_request(
    client, conn, statement, key, wire, flow
):
    # A merchant that is sent when the row is spending (the control above).
    # `movement` and `review` are not flow values on this lane; like a row
    # with no flow at all, they are stopped for not saying spending.
    row(conn, statement, "LANTERN NOODLE HOUSE 0042 SG", flow=flow)

    assert client.post("/api/suggestions/prepare").get_json()["merchants"] == []
    send_with_everything_approved(client, "LANTERN NOODLE HOUSE 0042 SG")

    assert wire.sent == []


FEE_AND_TAX_LINES = [
    ("GST", "GST"),
    ("GST", "GST @ 9%"),
    ("GST", "GOODS AND SERVICES TAX"),
    ("annual fee", "ANNUAL FEE"),
    ("annual fee", "CARD ANNUAL FEE"),
    ("annual fee", "ANNUAL MEMBERSHIP FEE"),
    ("late charge", "LATE CHARGE"),
    ("late charge", "LATE PAYMENT CHARGE"),
    ("programme admin fee", "PROGRAMME ADMIN FEE"),
    ("programme admin fee", "REWARDS PROGRAMME ADMIN FEE 0042"),
    ("service fee", "SERVICE FEE"),
    ("service fee", "SERVICE FEE 0042 SG"),
    ("fee", "FEE"),
    ("fee", "FEE 0042"),
    ("administrative fee", "ADMINISTRATIVE FEE"),
    ("administrative fee", "CARD ADMINISTRATIVE FEE 0042"),
    ("foreign currency fee", "FOREIGN CURRENCY TRANSACTION FEE"),
    ("foreign currency fee", "FOREIGN CURRENCY TRANSACTION FEE 88120457"),
]


@pytest.mark.parametrize("kind, description", FEE_AND_TAX_LINES)
def test_g2_a_bare_fee_or_tax_line_produces_no_request(
    client, conn, statement, key, wire, kind, description
):
    row(conn, statement, description)

    assert client.post("/api/suggestions/prepare").get_json()["merchants"] == []
    send_with_everything_approved(client, description)

    assert wire.sent == []


# --- the gate, tightened: only card purchases are ever sent ------------------


def other_account(conn, kind: str) -> int:
    return statement_on(conn, kind, "Sample Other 0002", "Sample-0002", "0002")


NOT_A_CARD = [kind for kind in account_kind.KIND_NAMES if kind != "card"] + ["credit_card", ""]


@pytest.mark.parametrize("description", [
    "TFR SAMPLE PERSON",              # transfer abbreviations no list of words knows
    "TT SAMPLE PERSON 88120457",
    "ITR SAMPLE PERSON",
    "SAMPLE PERSON",                  # a name alone
    "LANTERN NOODLE HOUSE 0042 SG",   # and wording that is sent from a card
])
def test_a_spending_row_on_a_bank_account_is_never_sent_whatever_its_wording(
    client, conn, key, wire, description
):
    tx = row(conn, other_account(conn, "bank"), description)

    assert client.post("/api/suggestions/prepare").get_json()["merchants"] == []
    assert not strings_file().exists()
    send_with_everything_approved(client, description)

    assert wire.sent == []
    assert suggestion(client, tx) == {"route": "blank", "types": [], "merchant": None}


@pytest.mark.parametrize("kind", NOT_A_CARD)
def test_only_an_account_whose_kind_is_card_has_its_rows_sent(client, conn, key, wire, kind):
    row(conn, other_account(conn, kind), "LANTERN NOODLE HOUSE 0042 SG")

    assert client.post("/api/suggestions/prepare").get_json()["merchants"] == []
    send_with_everything_approved(client, "LANTERN NOODLE HOUSE 0042 SG")

    assert wire.sent == []


def test_a_merchant_on_a_card_is_sent_and_the_bank_row_beside_it_is_not(
    client, conn, statement, key, wire
):
    bank = other_account(conn, "bank")
    row(conn, statement, "LANTERN NOODLE HOUSE 0042 SG")   # the card
    on_bank = row(conn, bank, "LANTERN NOODLE HOUSE 0077") # the same words, on the bank account
    row(conn, bank, "TFR SAMPLE PERSON")
    wire.model.answer("LANTERN NOODLE HOUSE", "Dining", 0.95)

    prepared = client.post("/api/suggestions/prepare").get_json()
    approve("LANTERN NOODLE HOUSE", "SAMPLE PERSON", "TFR SAMPLE PERSON")
    result = client.post("/api/suggestions/send").get_json()

    assert prepared["merchants"] == ["LANTERN NOODLE HOUSE"]
    assert (result["sent"], wire.asked) == (1, ["LANTERN NOODLE HOUSE"])
    assert all("SAMPLE PERSON" not in body for body in wire.bodies)
    # The answer the card row earned is not shown on the bank row, and a
    # choice made on the bank row is not recorded against it.
    assert suggestion(client, on_bank) == {"route": "blank", "types": [], "merchant": None}
    resp = client.post("/api/transactions/resolve", json={
        "tx_id": on_bank, "service_name": "Lantern Noodle House",
        "type_id": type_id(conn, "Travel"), "apply_scope": "transaction",
        "suggestion_visible": False,
    })
    assert resp.status_code == 200, resp.get_json()
    stored = answers(client)["LANTERN NOODLE HOUSE"]
    assert (stored["chosen_type"], stored["suggestion_visible"]) == (None, None)


# --- a strings file older than a day is not an approval ----------------------


def test_a_strings_file_older_than_24_hours_is_refused_and_send_says_to_prepare_again(
    client, conn, statement, key, wire, monkeypatch
):
    row(conn, statement, "LANTERN NOODLE HOUSE 0042 SG")
    assert client.post("/api/suggestions/prepare").status_code == 200
    written = strings_file().stat().st_mtime

    monkeypatch.setattr(suggest, "clock", lambda: written + 24 * 3600 + 1)
    stale = client.post("/api/suggestions/send")

    assert stale.status_code == 409
    assert "prepare" in stale.get_json()["error"].lower()
    assert "24 hours" in stale.get_json()["error"]
    assert wire.sent == [] and answers(client) == {}

    # Preparing again is what makes it an approval again.
    monkeypatch.setattr(suggest, "clock", lambda: written + 30 * 3600)
    assert client.post("/api/suggestions/prepare").status_code == 200
    again = strings_file().stat().st_mtime
    monkeypatch.setattr(suggest, "clock", lambda: again + 60)
    fresh = client.post("/api/suggestions/send")

    assert (fresh.status_code, fresh.get_json()["sent"]) == (200, 1)
    assert wire.asked == ["LANTERN NOODLE HOUSE"]


def test_a_strings_file_exactly_24_hours_old_is_still_an_approval(
    client, conn, statement, key, wire, monkeypatch
):
    row(conn, statement, "LANTERN NOODLE HOUSE 0042 SG")
    assert client.post("/api/suggestions/prepare").status_code == 200
    written = strings_file().stat().st_mtime

    monkeypatch.setattr(suggest, "clock", lambda: written + 24 * 3600)
    resp = client.post("/api/suggestions/send")

    assert (resp.status_code, resp.get_json()["sent"]) == (200, 1)
    assert wire.asked == ["LANTERN NOODLE HOUSE"]


# --- G3: a row a rule already matches ----------------------------------------


def test_g3_a_row_a_rule_already_matches_produces_no_request(client, conn, statement, key, wire):
    row(conn, statement, "LANTERN NOODLE HOUSE 0042 SG")
    row(conn, statement, "MOSSY BOOKS")
    # The rule is made after the row arrived, so the row itself still has no
    # merchant and no type: only the rules say it is known.
    known = conn.execute(
        "INSERT INTO services (name, book, type_id) VALUES ('Lantern Noodle House', 'Household', ?)",
        (type_id(conn, "Dining"),),
    ).lastrowid
    conn.commit()
    made = client.post("/api/rules", json={"pattern": "LANTERN NOODLE", "service_id": known})
    assert made.status_code in (200, 201), made.get_json()

    prepared = client.post("/api/suggestions/prepare").get_json()
    approve("LANTERN NOODLE HOUSE", "MOSSY BOOKS")
    client.post("/api/suggestions/send")

    assert prepared["merchants"] == ["MOSSY BOOKS"]
    assert wire.asked == ["MOSSY BOOKS"]


# --- G4: nothing readable left after cleaning --------------------------------


@pytest.mark.parametrize("description", [
    "0042 99812",            # digits only
    "88120457 SG",
    "AB 0042",
    "2C2P*",                 # a processor's prefix and nothing after it
    "PAYPAL *",
    "PAYPAL",
    "STRIPE",
    "OMISE 0042",
    "GLOBAL-E",
    "SQ *0042 SG",
    "STRIPE SINGAPORE",      # prefix and place
    "SINGAPORE SG",          # place only
])
def test_g4_a_row_with_nothing_readable_left_after_cleaning_produces_no_request(
    client, conn, statement, key, wire, description
):
    row(conn, statement, description)

    assert client.post("/api/suggestions/prepare").get_json()["merchants"] == []
    send_with_everything_approved(client, description)

    assert wire.sent == []


# --- G5: one request per distinct cleaned string -----------------------------


def test_g5_rows_that_clean_alike_cause_one_call_and_a_stored_answer_causes_none(
    client, conn, statement, key, wire
):
    row(conn, statement, "LANTERN NOODLE HOUSE 0042 SINGAPORE SG", 18.90)
    row(conn, statement, "2C2P*LANTERN NOODLE HOUSE 0077", 1234.56)
    row(conn, statement, "lantern  noodle house 31/03", 7.77)

    first = prepare_and_send(client)

    assert first["sent"] == 1
    assert wire.asked == ["LANTERN NOODLE HOUSE"]

    # A later row of the same merchant: the answer is already held under the
    # same string and model id. Even a file that approves it sends nothing.
    row(conn, statement, "LANTERN NOODLE HOUSE 0099 SG")
    assert client.post("/api/suggestions/prepare").get_json()["merchants"] == []
    send_with_everything_approved(client, "LANTERN NOODLE HOUSE 0099 SG")

    assert len(wire.sent) == 1
    held = answers(client)["LANTERN NOODLE HOUSE"]
    assert (held["model_id"], held["returned_model_id"]) == (suggest.MODEL_ID, suggest.RETURNED_MODEL_ID)


# --- G6: first live use -------------------------------------------------------


def test_g6_with_no_strings_file_the_batch_writes_the_strings_and_sends_nothing(
    client, conn, statement, key, wire
):
    row(conn, statement, "LANTERN NOODLE HOUSE 0042 SG")
    row(conn, statement, "MOSSY BOOKS")
    assert not strings_file().exists()

    # Sending first is refused: there is no file the operator could have read.
    refused = client.post("/api/suggestions/send")
    assert refused.status_code == 409
    assert wire.sent == [] and not strings_file().exists()

    # The batch's first step writes the strings, and still sends nothing.
    prepared = client.post("/api/suggestions/prepare")
    assert prepared.status_code == 200
    written = [
        line for line in strings_file().read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert written == ["LANTERN NOODLE HOUSE", "MOSSY BOOKS"]
    assert wire.sent == []
    assert answers(client) == {}

    # Once a batch has gone, the file is gone with it: the next batch starts
    # from a new file, and sending is refused again until there is one.
    assert client.post("/api/suggestions/send").get_json()["sent"] == 2
    row(conn, statement, "HARBOUR KITE SHOP")
    assert client.post("/api/suggestions/send").status_code == 409
    assert wire.asked == ["LANTERN NOODLE HOUSE", "MOSSY BOOKS"]


# --- G7: a failed call says its class and status, never the reply ------------

# Shaped like a key, made up here, and never whole in this file.
PLANTED_TOKEN = "sk-or-" + "v1-" + "0f" * 16
ECHO = json.dumps({"error": {
    "message": f"cannot judge LANTERN NOODLE HOUSE for Bearer {PLANTED_TOKEN}",
    "metadata": {"state": {"merchant": "LANTERN NOODLE HOUSE"}},
}}).encode("utf-8")


def http_error(status: int) -> Exception:
    return urllib.error.HTTPError(
        suggest.ENDPOINT, status, f"refused LANTERN NOODLE HOUSE {PLANTED_TOKEN}", {}, io.BytesIO(ECHO),
    )


@pytest.mark.parametrize("failure, raw_reply, said", [
    (lambda: http_error(402), None, "the call failed (HTTPError, HTTP 402)"),
    (lambda: http_error(500), None, "the call failed (HTTPError, HTTP 500)"),
    (lambda: urllib.error.URLError(f"no route for LANTERN NOODLE HOUSE {PLANTED_TOKEN}"), None,
     "the call failed (URLError)"),
    (None, ECHO, "the call failed (BadAnswer)"),               # a 200 that is no answer
    (None, b"<html>LANTERN NOODLE HOUSE " + PLANTED_TOKEN.encode() + b"</html>",
     "the call failed (JSONDecodeError)"),
])
def test_g7_a_failed_call_surfaces_the_error_class_and_status_and_never_the_reply(
    client, conn, statement, key, wire, caplog, capsys, failure, raw_reply, said
):
    row(conn, statement, "LANTERN NOODLE HOUSE 0042 SG")
    row(conn, statement, "MOSSY BOOKS")
    wire.failure = failure() if failure else None
    wire.raw_reply = raw_reply
    assert client.post("/api/suggestions/prepare").status_code == 200

    with caplog.at_level(logging.DEBUG):
        resp = client.post("/api/suggestions/send")

    assert resp.status_code == 200
    assert resp.get_json() == {"sent": 0, "stopped": said, "remaining": 2}
    assert len(wire.sent) == 1  # the first failure stops the batch
    printed = capsys.readouterr()
    logged = "\n".join(
        [caplog.text, printed.out, printed.err]
        + [repr(record.args) + repr(record.exc_info) for record in caplog.records]
    )
    assert said in logged  # the failure is logged, in the same words
    for where in (resp.get_data(as_text=True), logged):
        for never in ("LANTERN", "NOODLE", PLANTED_TOKEN, "0f0f", "sk-or", "Bearer",
                      "cannot judge", "refused", "no route", NOT_A_KEY):
            assert never not in where, never
    assert answers(client) == {}  # nothing of the reply is kept either


# --- G8: routing on the top probability, thresholds in one place -------------


@pytest.mark.parametrize("top, route", [
    (0.90, "prefill"), (0.8999, "top3"), (0.50, "top3"), (0.4999, "blank"),
])
def test_g8_the_boundaries_of_the_routing(client, conn, statement, key, wire, top, route):
    tx = row(conn, statement, "LANTERN NOODLE HOUSE 0042 SG")
    wire.model.answer("LANTERN NOODLE HOUSE", "Dining", top, Groceries=0.05, **{"Fitness > Golf": 0.03})
    prepare_and_send(client)

    got = suggestion(client, tx)

    assert got["route"] == route
    assert [t["name"] for t in got["types"]] == {
        "prefill": ["Dining"], "top3": ["Dining", "Groceries", "Fitness > Golf"], "blank": [],
    }[route]


@pytest.mark.parametrize("top", [0.4999, 0.50, 0.8999, 0.90, 0.99])
def test_g8_none_of_these_leaves_the_field_blank_at_any_probability(
    client, conn, statement, key, wire, top
):
    tx = row(conn, statement, "QQQ HOLDINGS")
    wire.model.answer("QQQ HOLDINGS", suggest.NONE_OF_THESE, top, Dining=0.0001)
    prepare_and_send(client)

    assert suggestion(client, tx) == {"route": "blank", "types": [], "merchant": "QQQ HOLDINGS"}


def test_g8_the_thresholds_are_read_from_one_place(client, conn, statement, key, wire, monkeypatch):
    assert (suggest.PREFILL_AT, suggest.OFFER_AT) == (0.90, 0.50)
    rows = {}
    for name, top in (("ALPHA STALL", 0.96), ("BRAVO STALL", 0.90), ("CEDAR STALL", 0.61),
                      ("DELTA STALL", 0.50)):
        rows[name] = row(conn, statement, name)
        wire.model.answer(name, "Dining", top, Groceries=0.02, Travel=0.01)
    prepare_and_send(client)

    def routes() -> list:
        return [suggestion(client, tx)["route"] for tx in rows.values()]

    assert routes() == ["prefill", "prefill", "top3", "top3"]
    # Move the two numbers, and nothing else: every answer follows them.
    monkeypatch.setattr(suggest, "PREFILL_AT", 0.95)
    monkeypatch.setattr(suggest, "OFFER_AT", 0.60)
    assert routes() == ["prefill", "top3", "top3", "blank"]
    # And no screen decides for itself: the page is handed the route.
    page = (Path(__file__).parent.parent / "static" / "app.js").read_text(encoding="utf-8")
    # The resolve step's suggestion, up to the end of its function (the
    # queue's "This was…" step; prepare-and-send went in fin-surfaces 02).
    start = page.index("async function showResolveSuggestion")
    dialog = page[start:page.index("\n}\n", start)]
    assert "suggestion.route === 'prefill'" in dialog and "suggestion.route === 'top3'" in dialog
    assert not re.search(r"probability\s*[<>]=?|[<>]=?\s*0?\.\d", dialog)
