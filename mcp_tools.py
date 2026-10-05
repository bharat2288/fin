"""The tools a chat client uses on fin over MCP (fin-surfaces 01).

The tool list is the permission list: a chat client reaches fin through these
tools and nothing else. Each tool runs one of fin's own routes in-process, so
it is checked exactly as the app's screens are checked, and every write lands
in the change history stamped as the client's (history.py), undoable from
fin. There is no write path here of its own.

Rulings this module carries:
- Reads return rows, balances and the month check (Q1). Never a file name, a
  server path or a credential: HIDDEN_KEYS are taken out of every answer, and
  no tool reads a file, the environment or a setting.
- Writes land at once, no confirm step (Q3). A write that would change more
  than MANY_ROWS rows runs only when the call says how many it expects:
  without that, or with a different count, it is put back and the count is
  returned, for Claude to tell the operator and ask.
- With the "Claude may write" switch off in fin (screens.py), every write
  tool is refused and changes nothing; reads still work. No tool sets it.
- No tool imports a statement, changes or deletes an imported row, edits an
  account other than adding a company or a person, sends merchants to the
  type-suggestion service, runs a conversion or touches subscriptions.
  Statements reach the hosted fin through the upload command (fin_upload.py).

The MCP server (mcp_server.py) registers TOOLS and calls call(); the actor is
the client the chat gate verified, labelled as folio labels it: `chat:<sub>`,
the token's subject (access_gate.AccessVerifier), or `chat` without one. There
is no default actor: every call names who asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import access_gate
import db
import history
import screens

MANY_ROWS = 20

# Keys taken out of every answer, at any depth.
HIDDEN_KEYS = frozenset({"file", "filename", "filenames", "path", "file_path"})

FIXED_ERROR = "fin could not do that just now; nothing was changed"
UNDO_NOT_CHAT = (
    "the chat can undo only a change made from the chat; nothing was changed. "
    "The operator can undo this one in fin's history."
)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    write: bool
    # args -> (method, path, query or json body)
    request: Callable[[dict], tuple[str, str, dict | None]]


def _schema(properties: dict | None = None, required: tuple = ()) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


_S = {"type": "string"}
_I = {"type": "integer"}
_B = {"type": "boolean"}
_MONTH = {"type": "string", "description": "YYYY-MM"}
_DAY = {"type": "string", "description": "YYYY-MM-DD"}
_EXPECTED = {
    "type": "integer",
    "description": f"For a change over {MANY_ROWS} rows: the row count the operator agreed to.",
}


def _pick(args: dict, *names: str) -> dict:
    return {k: args[k] for k in names if k in args and args[k] is not None}


def _get(path: str, *names: str):
    return lambda a: ("GET", path, _pick(a, *names))


# --- reads -------------------------------------------------------------------

_READS = [
    Tool(
        "balance_sheet",
        "The household's balance sheet at the end of a month: every account, loan, holding, "
        "company and person with its balance, the anchor it rests on (date, statement or "
        "typed figure), whether its rows tie; net worth and what it leaves out; currency "
        "change; and the month check (net-worth change against income less spending plus "
        "currency change, the unexplained difference, the transfers waiting for review).",
        _schema({"month": _MONTH}),
        False,
        _get("/api/balance-sheet", "month"),
    ),
    Tool(
        "spending_cards",
        "One month's spending figures: household spending (Household book, household-owned "
        "accounts), each company's costs paid from household accounts, loan principal repaid, "
        "and what is held out waiting for review (money out and in apart), against the "
        "three-month average.",
        _schema({"ref_month": _MONTH, "book": _S, "account_id": _I, "exclude_one_off": _B}),
        False,
        _get("/api/dashboard/stat-cards", "ref_month", "book", "account_id", "exclude_one_off"),
    ),
    Tool(
        "spending_by_type",
        "Spending totals by type over a date range, for one book or all.",
        _schema({"start": _DAY, "end": _DAY, "book": _S, "exclude_one_off": _B, "group_parent": _B}),
        False,
        _get("/api/dashboard/types", "start", "end", "book", "exclude_one_off", "group_parent"),
    ),
    Tool(
        "spending_by_month",
        "Spending by type per month (or week or quarter) over a date range.",
        _schema({"start": _DAY, "end": _DAY, "book": _S, "granularity": _S, "group_parent": _B}),
        False,
        _get("/api/dashboard/monthly", "start", "end", "book", "granularity", "group_parent"),
    ),
    Tool(
        "transactions",
        "Rows, newest first unless sorted: date, the bank's description, amount, account, "
        "book, type, flow, other side, merchant, note. Filter by date range, month, account, "
        "book, types (comma-separated ids), flow (e.g. review, movement, expense), or search text.",
        _schema({
            "start": _DAY, "end": _DAY, "month": _MONTH, "account_id": _I, "book": _S,
            "types": _S, "flow": _S, "search": _S, "sort": _S, "sort_dir": _S,
            "page": _I, "per_page": _I,
        }),
        False,
        _get("/api/transactions", "start", "end", "month", "account_id", "book", "types", "flow",
             "search", "sort", "sort_dir", "page", "per_page"),
    ),
    Tool(
        "review_summary",
        "How many transfers wait for a label and what they add up to, and the choices a label "
        "can be (with what each needs). The rows themselves: transactions with flow=review.",
        _schema(),
        False,
        _get("/api/review"),
    ),
    Tool(
        "accounts",
        "Every account: kind, owner, currency, last four digits only, latest anchor, and "
        "whether it takes a typed figure.",
        _schema(),
        False,
        _get("/api/accounts"),
    ),
    Tool(
        "figures",
        "Anchors (statement balances and typed figures), for one account or all.",
        _schema({"account_id": _I}),
        False,
        _get("/api/anchors", "account_id"),
    ),
    Tool(
        "merchants",
        "Every merchant with its default book and type, and its row and rule counts.",
        _schema(),
        False,
        _get("/api/services"),
    ),
    Tool(
        "rules",
        "Every merchant rule: pattern, merchant, and the book and type it gives a row.",
        _schema(),
        False,
        _get("/api/rules"),
    ),
    Tool(
        "types",
        "The type list (one list for every book, with sub-types and what each covers).",
        _schema(),
        False,
        _get("/api/types"),
    ),
    Tool("books", "The books (Household, Moom, Kalesh).", _schema(), False, _get("/api/books")),
    Tool("flows", "The flows a row can have, with what each means.", _schema(), False, _get("/api/flows")),
    Tool(
        "coverage",
        "Which account-months have statements imported, for the last six months.",
        _schema(),
        False,
        _get("/api/statements/coverage"),
    ),
    Tool(
        "rates",
        "Saved exchange rates, newest first.",
        _schema({"currency": _S}),
        False,
        _get("/api/rates", "currency"),
    ),
    Tool(
        "history",
        "The change history, newest first: who (fin or a chat client), when, what, how many rows, "
        "and whether it was undone.",
        _schema({"limit": _I, "before": _I}),
        False,
        _get("/api/history", "limit", "before"),
    ),
    Tool(
        "change",
        "One change from the history with every row it changed, before and after, and what "
        "would block undoing it.",
        _schema({"entry_id": _I}, ("entry_id",)),
        False,
        lambda a: ("GET", f"/api/history/{int(a['entry_id'])}", None),
    ),
]


# --- writes ------------------------------------------------------------------

def _w(name, description, properties, required, request):
    properties = {**properties, "expected_count": _EXPECTED}
    return Tool(name, description, _schema(properties, required), True, request)


def _account_body(a: dict) -> dict:
    return {"name": a["name"], "type": a["kind"]}


_WRITES = [
    _w(
        "set_label",
        "Set one row's book and/or type (type_id from the types tool). Marked as set by hand: "
        "no rule changes it afterwards.",
        {"tx_id": _I, "book": _S, "type_id": _I, "is_one_off": _B},
        ("tx_id",),
        lambda a: ("PUT", f"/api/transactions/{int(a['tx_id'])}", _pick(a, "book", "type_id", "is_one_off")),
    ),
    _w(
        "set_note",
        "Set one row's note.",
        {"tx_id": _I, "notes": _S},
        ("tx_id", "notes"),
        lambda a: ("PUT", f"/api/transactions/{int(a['tx_id'])}", _pick(a, "notes")),
    ),
    _w(
        "label_review",
        "Say what a transfer waiting for review was. choice: spending (type_id; book where the "
        "type proposes none), gift, own_account (account_id), company (account_id), "
        "loan_to_person (account_id, or person: a name; a new name creates the person), "
        "loan_repayment (account_id of a loan), income (income_kind_id).",
        {"tx_id": _I, "choice": _S, "type_id": _I, "book": _S, "account_id": _I,
         "person": _S, "income_kind_id": _I},
        ("tx_id", "choice"),
        lambda a: ("POST", f"/api/review/{int(a['tx_id'])}/label",
                   _pick(a, "choice", "type_id", "book", "account_id", "person", "income_kind_id")),
    ),
    _w(
        "enter_figure",
        "Type a figure for an account with no statement (a loan: what is owed, positive; a "
        "holding: what it is worth; a company, person or bank account: its balance). amount is "
        "text in whole units of the account's currency, e.g. \"1250.00\".",
        {"account_id": _I, "amount": _S, "date": _DAY, "note": _S},
        ("account_id", "amount", "date"),
        lambda a: ("POST", "/api/anchors", _pick(a, "account_id", "amount", "date", "note")),
    ),
    _w(
        "correct_figure",
        "Correct a typed figure: its amount, date or note. A statement's balance cannot be changed.",
        {"anchor_id": _I, "amount": _S, "date": _DAY, "note": _S},
        ("anchor_id",),
        lambda a: ("PUT", f"/api/anchors/{int(a['anchor_id'])}", _pick(a, "amount", "date", "note")),
    ),
    _w(
        "delete_figure",
        "Delete a typed figure. A statement's balance cannot be deleted.",
        {"anchor_id": _I},
        ("anchor_id",),
        lambda a: ("DELETE", f"/api/anchors/{int(a['anchor_id'])}", None),
    ),
    _w(
        "add_rule",
        "Add a merchant rule: rows whose description matches the pattern take the merchant "
        "(service_id) and its book and type, or the rule's own overrides.",
        {"pattern": _S, "service_id": _I, "match_type": _S, "book_override": _S,
         "type_override_id": _I, "priority": _I},
        ("pattern", "service_id"),
        lambda a: ("POST", "/api/rules", _pick(a, "pattern", "service_id", "match_type",
                                              "book_override", "type_override_id", "priority")),
    ),
    _w(
        "change_rule",
        "Change a merchant rule.",
        {"rule_id": _I, "pattern": _S, "service_id": _I, "match_type": _S, "book_override": _S,
         "type_override_id": _I, "priority": _I},
        ("rule_id",),
        lambda a: ("PUT", f"/api/rules/{int(a['rule_id'])}",
                   _pick(a, "pattern", "service_id", "match_type", "book_override",
                         "type_override_id", "priority")),
    ),
    _w(
        "delete_rule",
        "Delete a merchant rule.",
        {"rule_id": _I},
        ("rule_id",),
        lambda a: ("DELETE", f"/api/rules/{int(a['rule_id'])}", None),
    ),
    _w(
        "rerun_rules",
        "Re-run every merchant rule over the rows. Rows set by hand are left alone.",
        {},
        (),
        lambda a: ("POST", "/api/rules/recategorize", {}),
    ),
    _w(
        "change_merchant",
        "Change a merchant: its name, default book and type (its rows that take the default "
        "follow), note, one-off, hidden from spending views, or review each time.",
        {"service_id": _I, "name": _S, "book": _S, "type_id": _I, "notes": _S, "is_one_off": _B,
         "exclude_from_expense_views": _B, "review_each_time": _B},
        ("service_id",),
        lambda a: ("PUT", f"/api/services/{int(a['service_id'])}",
                   _pick(a, "name", "book", "type_id", "notes", "is_one_off",
                         "exclude_from_expense_views", "review_each_time")),
    ),
    _w(
        "merge_merchants",
        "Merge one merchant into another: its rows and rules move to the target.",
        {"source_id": _I, "target_id": _I},
        ("source_id", "target_id"),
        lambda a: ("POST", f"/api/services/{int(a['source_id'])}/merge", _pick(a, "target_id")),
    ),
    _w(
        "add_company_or_person",
        "Add a company or a person, so movements can name them as their other side.",
        {"kind": {"type": "string", "enum": ["company", "person"]}, "name": _S},
        ("kind", "name"),
        lambda a: ("POST", "/api/accounts", _account_body(a)),
    ),
    _w(
        "pair_transfers",
        "Pair moves between household accounts (same amount, opposite sign, within three "
        "days). Never changes a row set by hand.",
        {},
        (),
        lambda a: ("POST", "/api/pair-matching", {}),
    ),
    _w(
        "fetch_rate",
        "Fetch and save the European Central Bank's rate for a currency on a day.",
        {"currency": _S, "date": _DAY},
        ("currency", "date"),
        lambda a: ("POST", "/api/rates/fetch", _pick(a, "currency", "date")),
    ),
    _w(
        "set_rate",
        "Enter a rate for a day in place of the saved one (SGD for one unit, decimal text).",
        {"currency": _S, "date": _DAY, "rate": _S},
        ("currency", "date", "rate"),
        lambda a: ("PUT", "/api/rates", _pick(a, "currency", "date", "rate")),
    ),
    _w(
        "undo",
        "Undo one change made from the chat. Refused, naming it, when a later change touched "
        "the same rows: undo that one first. A change made in fin itself is the operator's to undo.",
        {"entry_id": _I},
        ("entry_id",),
        lambda a: ("POST", f"/api/history/{int(a['entry_id'])}/undo", {}),
    ),
]

TOOLS = _READS + _WRITES
BY_NAME = {t.name: t for t in TOOLS}


class ToolRefused(Exception):
    """The call was not made: an unknown tool or arguments it does not take."""


def _hidden_out(value):
    if isinstance(value, dict):
        return {k: _hidden_out(v) for k, v in value.items() if k not in HIDDEN_KEYS}
    if isinstance(value, list):
        return [_hidden_out(v) for v in value]
    return value


def _checked(tool: Tool, args: dict) -> dict:
    args = dict(args or {})
    schema = tool.input_schema
    unknown = set(args) - set(schema["properties"])
    if unknown:
        raise ToolRefused(f"{tool.name} does not take {', '.join(sorted(unknown))}")
    missing = [k for k in schema["required"] if args.get(k) is None]
    if missing:
        raise ToolRefused(f"{tool.name} needs {', '.join(missing)}")
    for k, v in args.items():
        kind = schema["properties"][k].get("type")
        ok = {
            "integer": isinstance(v, int) and not isinstance(v, bool),
            "string": isinstance(v, str),
            "boolean": isinstance(v, bool),
        }.get(kind, True)
        if not ok:
            raise ToolRefused(f"{k} must be a{'n' if kind == 'integer' else ''} {kind}")
        allowed = schema["properties"][k].get("enum")
        if allowed and v not in allowed:
            raise ToolRefused(f"{k} must be one of {', '.join(allowed)}")
    return args


def _send(client, method: str, path: str, payload, environ: dict):
    if method == "GET":
        query = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in (payload or {}).items()}
        return client.get(path, query_string=query, environ_base=environ)
    return client.open(path, method=method, json=payload, environ_base=environ)


def _answer(resp) -> dict:
    if resp.status_code >= 500:
        return {"ok": False, "error": FIXED_ERROR}
    try:
        body = resp.get_json(silent=True)
    except Exception:
        body = None
    if resp.status_code >= 400:
        message = body.get("error") if isinstance(body, dict) else None
        return {"ok": False, "error": message if isinstance(message, str) else FIXED_ERROR}
    return {"ok": True, "result": _hidden_out(body)}


def call(name: str, args: dict | None, actor: str) -> dict:
    """Run one tool for a chat client. Returns {"ok": True, "result": ...}
    (and "change": the history entry and its row count, for a write that
    changed something), {"ok": False, "error": ...}, or, for a write over
    MANY_ROWS rows without the agreed count, {"ok": False, "stopped": True,
    "would_change_rows": n} with nothing changed."""
    import app as fin_app  # the Flask app; imported here so importing TOOLS is cheap

    tool = BY_NAME.get(name)
    if tool is None:
        return {"ok": False, "error": f"no tool named {name!r}"}
    try:
        args = _checked(tool, args or {})
        expected = args.pop("expected_count", None)
        method, path, payload = tool.request(args)
    except ToolRefused as e:
        return {"ok": False, "error": str(e)}
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "error": f"{name} was given arguments it cannot use"}

    # The chat identity, where the Flask app's second layer (access_gate.
    # RequireGateIdentity) takes it: an environ key no HTTP request can set.
    # It stamps the write via='chat', actor=the client the chat gate verified.
    environ = {access_gate.CHAT_CALL_ENVIRON_KEY: access_gate.chat_call_identity(actor)}
    client = fin_app.app.test_client()
    if not tool.write:
        try:
            return _answer(_send(client, method, path, payload, environ))
        except Exception:
            return {"ok": False, "error": FIXED_ERROR}

    with history.WRITE_LOCK:
        # The operator's off switch (01): with it off every write is refused
        # before anything runs, and reads go on working.
        conn = db.get_connection()
        try:
            writes_on = screens.claude_may_write(conn)
        finally:
            conn.close()
        if not writes_on:
            return {"ok": False, "error": screens.WRITES_OFF}
        if name == "undo":
            conn = db.get_connection()
            try:
                undoing = conn.execute(
                    "SELECT via FROM change_entries WHERE id = ?", (int(args["entry_id"]),)
                ).fetchone()
                # An undo writes back exactly the rows its entry changed, so
                # its count is known before anything is written: ask first.
                rows = history.row_count(conn, int(args["entry_id"]))
            finally:
                conn.close()
            # The chat undoes only what the chat did (a default the operator
            # may overturn): an import, an account or a subscription is not
            # the chat's to take back. Fin's own undo is not limited.
            if undoing is not None and undoing["via"] != history.VIA_CHAT:
                return {"ok": False, "error": UNDO_NOT_CHAT}
            if rows > MANY_ROWS and expected != rows:
                return _stopped(name, rows)
        try:
            resp = _send(client, method, path, payload, environ)
        except Exception:
            # The type and text stay here: they can carry row contents.
            return {"ok": False, "error": FIXED_ERROR}
        answer = _answer(resp)
        entry_id = resp.headers.get("X-Fin-Change")
        if entry_id is None:
            return answer
        rows = int(resp.headers.get("X-Fin-Change-Rows", "0"))
        if rows > MANY_ROWS and expected != rows:
            conn = db.get_connection()
            try:
                history.discard(conn, int(entry_id))
            except Exception:
                # The change stands: say so, and where it is. The type and
                # text stay here: they can carry row contents.
                return {
                    "ok": False,
                    "change": {"entry_id": int(entry_id), "rows": rows},
                    "error": (
                        f"this changed {rows} rows, over the {MANY_ROWS} that need the operator's "
                        f"count, and the change could not be put back: it stands as change "
                        f"{entry_id}. Tell the operator; they can undo it from fin's history."
                    ),
                }
            finally:
                conn.close()
                db.invalidate_rules_cache()
            return _stopped(name, rows)
        answer["change"] = {"entry_id": int(entry_id), "rows": rows}
        return answer


def _stopped(name: str, rows: int) -> dict:
    return {
        "ok": False,
        "stopped": True,
        "would_change_rows": rows,
        "error": (
            f"this would change {rows} rows, so nothing was changed. Tell the operator "
            f"the count, and if they agree call {name} again with expected_count={rows}."
        ),
    }
