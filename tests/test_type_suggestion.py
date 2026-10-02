"""Type suggestion for merchants no rule knows.

Driven through the HTTP interface against the temporary database. No test
here reaches the network: the outside model is reached through one function,
`suggest.post`, which these tests replace, and every socket connection is
refused and recorded. Every merchant, figure and name here is invented, and
the key is a made-up word.
"""

import io
import json
import socket
from pathlib import Path

import pytest

import book_type
import db
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


@pytest.fixture
def statement(conn) -> int:
    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four)"
        " VALUES ('Sample Card 0001', 'Sample-0001', 'credit_card', '0001')"
    )
    account_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename)"
        " VALUES (?, '2026-04-01', 'sample.csv')",
        (account_id,),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def row(conn, statement_id: int, description: str, amount: float = 25.0, *,
        flow: str = "expense", service_id: int | None = None) -> int:
    conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_sgd,"
        " service_id, flow_type) VALUES (?, '2026-04-10', ?, ?, ?, ?)",
        (statement_id, description, amount, service_id, flow),
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
                    ParsedTransaction(date=day, description=description, amount_sgd=amount)
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
