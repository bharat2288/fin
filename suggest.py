"""Type suggestion for merchants no rule knows.

A row no rule matched may be offered a type by an outside judgment model
(Jev, reached through OpenRouter). It is a suggestion only: the operator
confirms or corrects it in the resolve dialog, and nothing here files a row,
makes a merchant or makes a rule.

What leaves the machine is one cleaned merchant string and the type list's
own descriptions. Never an amount, a date, an account or card number, another
row, or a book.

The feature is off unless the key is in the environment. Nothing here runs
inside an upload. The network is reached through `post` alone, and the
library that does it is loaded only there.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from decimal import Decimal

import book_type
import db

# The name of the environment variable holding the key. The operator's
# launcher sets it; the value is never printed, logged or stored.
KEY_ENV = "OPENROUTER_API_KEY"

ENDPOINT = "https://openrouter.ai/api/v1/systemone"
TIMEOUT_SECONDS = 30.0
# The model id asked for, and the id that answers to it. A different returned
# id means a different model: answers stored under it are never shown, and the
# thresholds below no longer stand.
MODEL_ID = "typesafe/jev-1.13"
RETURNED_MODEL_ID = "typesafe/jev-1.13-20260917"
# List price per input token, in US dollars; output tokens are free.
USD_PER_INPUT_TOKEN = Decimal("0.042") / 1_000_000

# Change one of these when the cleaning or the question text changes: stored
# answers are kept, and reused only for the versions that produced them.
CLEANING_VERSION = "c2"
QUESTION_VERSION = "q1"

# Routing on the top probability. Provisional, from the answers seen on
# 2026-10-02; parameters, not law.
PREFILL_AT = 0.90   # at or above: the type is pre-filled
OFFER_AT = 0.50     # at or above: the top three are offered; below: blank

NONE_OF_THESE = "none_of_these"
# `Other` means "not placed yet", which says nothing about a merchant.
NOT_ASKED = ("Other",)

# The file the cleaned strings are written to for the operator to read before
# anything is sent. It sits beside the database.
STRINGS_FILE = "suggestion-strings.txt"
FILE_HEADER = (
    "# Merchant names fin would send for a type suggestion. Nothing has been sent.\n"
    "# Each line below would be sent on its own, with the type list and nothing else.\n"
    "# Delete any line that is a person's name or that you do not want sent, save,\n"
    "# then press Send in fin.\n"
)


# --- the code gate: no model in it ------------------------------------------

# A payee or transfer shape: not a merchant, and it may carry a person's name.
_PAYEE_SHAPE = re.compile(
    r"PAY ?NOW|PAYLAH|\bFAST\b|\bIBG\b|GIRO|TRANSFER|\bTRF\b|INWARD|REMITT|\bATM\b"
    r"|\bCASH\b|\bFUNDS\b|I-BANK|:IB\b|\bMEP\b|\bFT\d"
)
# Bank advice, card payment and bare fee or tax lines: for rules, not merchants.
_BANK_LINE = re.compile(
    r"^(ADVICE|BILL PAYMENT|PAYMENT\b|PAYMT\b|CARD PAYMENT|SERVICE CHARGE|MISC DEBIT"
    r"|BUSINESS ADVANCE|INTEREST|CASH REBATE|SALARY|DIVIDEND|GST\b|FINANCE CHARGE"
    r"|CCY CONVERSION FEE)"
    r"|\b(ANNUAL (MEMBERSHIP )?FEE|LATE (PAYMENT )?(CHARGE|FEE)|ADMIN(ISTRATION)? FEE|OVERLIMIT FEE"
    r"|GOODS (AND|&) SERVICES TAX|((CREDIT )?CARDS?|CC) (PAYMENT|PAYMT))\b"
)
# Payment processors that print their own name before the merchant's.
_PROCESSORS = "2C2P|2C2|PAYPAL|PP|STRIPE|SQUARE|SQ|OMISE|GLOBAL-E|GLOBALE|ADYEN|SUMUP|ATOME|SMP|PYU"
_PROCESSOR_PREFIX = re.compile(
    rf"^(?:(?:{_PROCESSORS})"
    r"\s*[*/]\s*"
    r"|(?:2C2P|PAYPAL|STRIPE|OMISE|GLOBAL-E|GLOBALE)\s+(?=\S))"
)
_PLACES = (
    "SINGAPORE|SGP|SGD|SG|SIN|USA|US|GBR|GB|UK|LONDON|AUS|AU|SYDNEY|IND|IN|HKG|HK"
    "|JPN|JP|TOKYO|MYS|MY|THA|TH|BANGKOK|IDN|ID|NLD|NL|IRL|IE|DUBLIN|CAN|CA|DEU|DE|FRA|FR"
)
_TRAILING_PLACE = re.compile(rf"(\s+({_PLACES}))+$")
# What is left names no merchant: a processor alone, or places alone.
_NO_MERCHANT = re.compile(rf"^(({_PROCESSORS})|({_PLACES})( ({_PLACES}))*)$")
# A month beside a number is part of a date, not of a name.
_MONTHS = frozenset(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP SEPT OCT NOV DEC JANUARY FEBRUARY MARCH APRIL"
    " JUNE JULY AUGUST SEPTEMBER OCTOBER NOVEMBER DECEMBER".split()
)


def _has_digit(token: str) -> bool:
    return re.search(r"\d", token) is not None


def clean(description: str | None) -> str:
    """The merchant string left once everything that is not the merchant's
    name is taken out: the processor's prefix, every token containing a digit,
    a month standing beside one, stray punctuation, and trailing country and
    city codes."""
    text = " ".join((description or "").upper().split())
    text = _PROCESSOR_PREFIX.sub("", text)
    tokens = text.split()
    dated = {
        i for i, token in enumerate(tokens)
        if token in _MONTHS and any(_has_digit(near) for near in tokens[max(i - 1, 0):i + 2])
    }
    text = " ".join(
        token for i, token in enumerate(tokens) if not _has_digit(token) and i not in dated
    )
    text = re.sub(r"[^A-Z&'*/.\- ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _TRAILING_PLACE.sub("", text).strip(" *-/.")


def merchant_string(description: str | None, flow_type: str | None) -> str | None:
    """The string that may be sent for a row, or None when the gate stops it.

    A row passes only if it says it is spending, has no payee or transfer
    shape, is not a bank or fee line, and has readable text left after
    cleaning. A row with no flow at all has not said it is spending.
    """
    if flow_type != "expense":
        return None
    upper = " ".join((description or "").upper().split())
    if _PAYEE_SHAPE.search(upper):
        return None
    merchant = clean(description)
    if len(re.sub(r"[^A-Z]", "", merchant)) < 3:
        return None
    if _BANK_LINE.search(merchant) or _NO_MERCHANT.match(merchant):
        return None
    return merchant


# --- the question ------------------------------------------------------------


def question() -> dict:
    """The one pick-one question: every type but `Other`, plus none of these,
    each described by what it covers and what it is not for. From the type
    list's one declaration; it names no merchant."""
    criteria = {}
    for name, parent, _default, covers, not_for in book_type.SPENDING_TYPES:
        if name in NOT_ASKED:
            continue
        described = {"what": covers}
        if not_for:
            described["not_for"] = not_for
        criteria[book_type.display_name(name, parent)] = described
    criteria[NONE_OF_THESE] = {"what": "The merchant fits none of the types listed"}
    return {
        "type": "choice",
        "instructions": {
            "question": "What kind of spending is a purchase at the business named in `merchant`?",
            "focus": "Judge what the business sells. Ignore who paid and where it is.",
        },
        "criteria": criteria,
    }


# --- the one way out ---------------------------------------------------------


def key() -> str | None:
    """The key, from the environment; None means the feature is off."""
    return os.environ.get(KEY_ENV) or None


def post(body: dict, api_key: str) -> dict:
    """Send one request and return the decoded reply. The only function here
    that reaches the network; tests replace it."""
    import urllib.request

    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as reply:
        return json.loads(reply.read().decode("utf-8"))


class BadAnswer(Exception):
    """The reply did not have the shape of an answer to the question."""


def ask(merchant: str, api_key: str) -> dict:
    """One request for one merchant string; the full answer as a record."""
    asked = question()
    body = {"state": {"merchant": merchant}, "model": MODEL_ID, "questions": {"type": asked}}
    started = time.perf_counter()
    reply = post(body, api_key)
    latency_ms = round((time.perf_counter() - started) * 1000)
    try:
        answer = reply["answers"]["type"]
        pick = answer["choice"]
        probabilities = {str(k): float(v) for k, v in answer["probabilities"].items()}
        returned = str(reply["model"])
        confidence = answer.get("confidence")
        tokens = (reply.get("usage") or {}).get("input_tokens")
    except (KeyError, TypeError, ValueError, AttributeError):
        raise BadAnswer() from None
    if pick not in asked["criteria"] or set(probabilities) != set(asked["criteria"]):
        raise BadAnswer()
    return {
        "merchant": merchant,
        "model_id": MODEL_ID,
        "returned_model_id": returned,
        "pick": pick,
        "probabilities": probabilities,
        "confidence": float(confidence) if confidence is not None else None,
        "input_tokens": int(tokens) if tokens is not None else None,
        "cost_usd": (
            format((int(tokens) * USD_PER_INPUT_TOKEN).normalize(), "f")
            if tokens is not None else None
        ),
        "latency_ms": latency_ms,
    }


def describe_failure(exc: Exception) -> str:
    """The kind of failure and the HTTP status, never its text: an error body
    can echo what was sent, and a message can carry the key."""
    status = getattr(exc, "code", None)
    kind = type(exc).__name__
    return f"the call failed ({kind}, HTTP {status})" if isinstance(status, int) else f"the call failed ({kind})"


# --- the local answer store --------------------------------------------------

_CURRENT = "model_id = ? AND returned_model_id = ? AND cleaning_version = ? AND question_version = ?"
_CURRENT_VALUES = (MODEL_ID, RETURNED_MODEL_ID, CLEANING_VERSION, QUESTION_VERSION)


def store(conn: sqlite3.Connection, record: dict) -> None:
    """Keep an answer in full. An answer already held for the same merchant,
    model and versions is replaced."""
    conn.execute(
        "INSERT OR REPLACE INTO suggestion_answers (merchant, model_id, returned_model_id,"
        " cleaning_version, question_version, pick, probabilities, confidence,"
        " input_tokens, cost_usd, latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (record["merchant"], record["model_id"], record["returned_model_id"],
         CLEANING_VERSION, QUESTION_VERSION, record["pick"],
         json.dumps(record["probabilities"]), record["confidence"],
         record["input_tokens"], record["cost_usd"], record["latency_ms"]),
    )


def stored_answer(conn: sqlite3.Connection, merchant: str) -> sqlite3.Row | None:
    """The answer held for a merchant string from the pinned model and the
    current cleaning and question; None when there is none to reuse."""
    return conn.execute(
        f"SELECT * FROM suggestion_answers WHERE merchant = ? AND {_CURRENT}",
        (merchant, *_CURRENT_VALUES),
    ).fetchone()


def unanswered(conn: sqlite3.Connection) -> list[str]:
    """The distinct merchant strings of rows with no type and no merchant that
    no rule matches, that pass the gate and have no stored answer, in
    alphabetical order. The rules are asked here, not read off the row: a rule
    made since the row arrived knows it all the same."""
    rows = conn.execute(
        "SELECT description, flow_type, amount_sgd FROM transactions"
        " WHERE type_id IS NULL AND service_id IS NULL"
    ).fetchall()
    merchants = {
        merchant_string(r["description"], r["flow_type"]) for r in rows
        if db.match_merchant(r["description"] or "", conn, r["amount_sgd"])["service_id"] is None
    }
    merchants.discard(None)
    held = {
        r["merchant"] for r in conn.execute(
            f"SELECT merchant FROM suggestion_answers WHERE {_CURRENT}", _CURRENT_VALUES
        )
    }
    return sorted(merchants - held)


def route(pick: str, probabilities: dict) -> tuple[str, list[tuple[str, float]]]:
    """What the resolve dialog is given: ('prefill', [the type]),
    ('top3', [three types]) or ('blank', [])."""
    top = probabilities.get(pick, 0.0)
    if pick == NONE_OF_THESE or top < OFFER_AT:
        return "blank", []
    if top >= PREFILL_AT:
        return "prefill", [(pick, top)]
    ranked = sorted(
        ((name, p) for name, p in probabilities.items() if name != NONE_OF_THESE),
        key=lambda item: -item[1],
    )
    return "top3", ranked[:3]


def record_choice(conn: sqlite3.Connection, description: str | None, flow_type: str | None,
                  type_id: int | None, visible: bool) -> None:
    """Afterwards: the type the operator chose for a merchant that has a
    stored answer, and whether the suggestion was on screen when they chose.
    The first choice is the one kept. No commit: the caller owns it."""
    merchant = merchant_string(description, flow_type)
    if merchant is None or type_id is None:
        return
    conn.execute(
        "UPDATE suggestion_answers SET chosen_type_id = ?, suggestion_visible = ?,"
        f" chosen_at = datetime('now') WHERE merchant = ? AND {_CURRENT}"
        " AND chosen_type_id IS NULL",
        (type_id, 1 if visible else 0, merchant, *_CURRENT_VALUES),
    )
