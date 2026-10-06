"""fin — Personal Finance Tracker

Flask backend serving the 4-tab SPA (Dashboard, Import, History, Merchant Rules)
and API endpoints for statement parsing, labelling (book and type), and visualization.

Usage:
    py app.py                  # Start on port 8450
    py app.py --port 8450      # Explicit port
"""

import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

import access_gate
import account_kind
import anchors
import backup
import balance_sheet
import book_type
import card_balance
import db
import flow
import history
import loan_interest
import mcp_tools
import money
import month_check
import pairing
import rates
import review
import screens
import suggest
import tie
from db import first_rule, get_connection, init_db, invalidate_rules_cache, match_merchant, rule_label


@contextmanager
def get_db():
    """Context manager wrapping get_connection() for automatic cleanup."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

app = Flask(__name__, static_folder="static", static_url_path="/static")
# LOCAL_DEV: off unless a caller turns it on (python app.py, serve.py in
# local-dev); without the access gate's identity every request is then refused
# (fin-online D1, the second layer). BACKUP_STORE: the nightly backup's object
# store, set by serve.py; None means backups are not configured.
app.config.update(LOCAL_DEV=False, BACKUP_STORE=None)
app.wsgi_app = access_gate.RequireGateIdentity(app.wsgi_app, app.config)


def format_type_display(parent: str | None, child: str | None) -> str:
    """Format a type as 'Parent > Child', or the bare name with no parent."""
    return book_type.display_name(child, parent) if child else ""


# The joins that give a labelled table its type (ty) and that type's parent
# (tp). `{owner}` is the alias of the table carrying type_id.
_TYPE_JOINS = (
    "LEFT JOIN types ty ON {owner}.type_id = ty.id "
    "LEFT JOIN types tp ON ty.parent_id = tp.id"
)

# What the charts call spending rows nobody has given a type.
NO_TYPE_LABEL = "No type"
# The same rows, as the transaction list's type filter names them.
UNTYPED_FILTER = "__untyped__"


def book_expr(alias: str = "t") -> str:
    """SQL for the book a row is read as: its own, or the default when no one
    has placed it. Scope comes from here and from nowhere else."""
    return f"COALESCE({alias}.book, '{book_type.DEFAULT_BOOK}')"


def _requested_book(args) -> str | None:
    """The book the View filter asks for; None means every book."""
    book = (args.get("book") or "").strip()
    return book if book in book_type.BOOK_NAMES else None


class UnknownLabel(ValueError):
    """A writer was handed a book or a type that is not in its vocabulary."""


def _checked_book(value) -> str | None:
    """A book as a writer may store it: one of the declared books, or none."""
    if value is None or value == "":
        return None
    if value not in book_type.BOOK_NAMES:
        raise UnknownLabel(f"unknown book: {value!r}")
    return value


def _checked_type_id(conn, value) -> int | None:
    """A type as a writer may store it: the id of a spending type, or none."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnknownLabel(f"unknown type: {value!r}")
    row = conn.execute(
        "SELECT id FROM types WHERE id = ? AND kind = ?", (value, book_type.SPENDING)
    ).fetchone()
    if not row:
        raise UnknownLabel(f"unknown type: {value!r}")
    return value


def _checked_labels(conn, data: dict, book_key: str = "book", type_key: str = "type_id") -> None:
    """Refuse a request body whose book or type is not in the vocabulary."""
    if book_key in data:
        _checked_book(data[book_key])
    if type_key in data:
        _checked_type_id(conn, data[type_key])


def _proposed_book(conn, type_id: int | None) -> str | None:
    """The book a type proposes for a merchant no rule knows; None = ask."""
    if type_id is None:
        return None
    return book_type.proposed_book(book_type.spending_type_names(conn).get(type_id))


class BookNeeded(ValueError):
    """The type proposes no book, so the operator has to say whose it is."""


def _book_or_proposed(conn, book: str | None, type_id: int | None) -> str | None:
    """The book given, else the one the type proposes. A type that asks
    (Software & AI tools) with no book given is refused."""
    if book or type_id is None:
        return book
    proposed = _proposed_book(conn, type_id)
    if proposed is None:
        name = book_type.spending_type_names(conn).get(type_id)
        raise BookNeeded(f"book is required: {name} does not say whose spending it is")
    return proposed


def _build_match_condition(match_type: str) -> str:
    """Return SQL WHERE fragment for merchant rule matching."""
    if match_type == "contains":
        return "UPPER(description) LIKE '%' || ? || '%'"
    elif match_type == "startswith":
        return "UPPER(description) LIKE ? || '%'"
    return "UPPER(description) = ?"


_GENERIC_TRANSFER_PATTERNS = {
    "PAYNOW",
    "PAYNOW TRANSFER",
    "ICT PAYNOW",
    "ICT PAYNOW TRANSFER",
    "TOP-UP TO PAYLAH",
    "TOP UP TO PAYLAH",
    "MAXED OUT FROM PAYLAH",
    "TRANSFER",
    "BANK TRANSFER",
    "I-BANK",
}


def _normalize_pattern_text(text: str | None) -> str:
    """Normalize a rule pattern or description fragment for guard checks."""
    return re.sub(r"\s+", " ", (text or "").upper()).strip()


def _looks_transfer_like_description(description: str | None) -> bool:
    """Return True when a raw description is rail-like, not merchant-like."""
    normalized = _normalize_pattern_text(description)
    if not normalized:
        return False
    if any(token in normalized for token in ("PAYNOW", "PAYLAH", "I-BANK", ":IB")):
        return True
    if re.match(r"^FT\d+[A-Z0-9-]*", normalized):
        return True
    return False


def _is_generic_rule_pattern(pattern: str | None) -> bool:
    """Return True for reusable rules that are too generic to be safe."""
    normalized = _normalize_pattern_text(pattern)
    if not normalized:
        return False
    if normalized in _GENERIC_TRANSFER_PATTERNS:
        return True
    if re.fullmatch(r"FT\d+[A-Z0-9:-]*", normalized):
        return True
    if normalized.startswith("DBSC-") and "I-BANK" in normalized:
        return True
    return False


def _rule_pattern_error(pattern: str | None) -> str | None:
    """Return a user-facing validation error for unsafe rule patterns."""
    if _is_generic_rule_pattern(pattern):
        return (
            "Pattern is too generic for PayNow/transfer traffic. "
            "Leave it blank for a one-off resolution or use a specific counterparty name."
        )
    return None


def _paynow_fallback_label(description: str | None, conn) -> tuple[str | None, int | None]:
    """The (book, type_id) the PayNow payee wording gives a row no rule
    matches; (None, None) when the wording says nothing. The book is the one
    the type proposes."""
    if not description or "PAYNOW" not in description.upper():
        return None, None
    from ingest import paynow_type
    type_name = paynow_type(description)
    type_id = book_type.spending_type_ids(conn).get(type_name) if type_name else None
    if type_id is None:
        return None, None
    return book_type.proposed_book(type_name), type_id


class BadAmount(ValueError):
    """An amount sent to the API is not a number of whole minor units."""


def _minor_from_request(value, what: str = "amount", currency: str = "SGD") -> int:
    """A decimal amount from a request body as whole minor units of the
    currency its account is kept in. Refuses anything that is not a number,
    and a number finer than the minor unit: an amount is never rounded on its
    way in."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadAmount(f"{what} must be a number")
    try:
        whole = money.is_whole_minor(value, currency)
    except ArithmeticError:
        whole = False
    if not whole:
        raise BadAmount(f"{what} must be a whole number of {money.MINOR_UNIT_NAMES[currency]}")
    return money.to_minor(value, currency)


class CurrencyRefused(ValueError):
    """A currency is not one fin holds, or is not the one the account is kept in."""


def _checked_currency(value) -> str:
    """A currency as an account may be kept in: one with a declared minor unit."""
    if not isinstance(value, str) or value not in money.MINOR_UNIT_DIGITS:
        raise CurrencyRefused(
            f"unknown currency: {value!r}; an account is kept in one of "
            + ", ".join(sorted(money.MINOR_UNIT_DIGITS))
        )
    return value


def _account_currency(conn, account_name: str, stated=None) -> str:
    """The currency the rows of an import land in: the account's own when the
    name finds one, else the one the statement states, else SGD. A statement
    in a currency other than its account's is refused."""
    from ingest import find_account
    if stated is not None:
        _checked_currency(stated)
    held = conn.execute(
        "SELECT currency FROM accounts WHERE id = ?", (find_account(conn, account_name),)
    ).fetchone()
    if held is None:
        return stated or "SGD"
    own = held["currency"] or "SGD"
    if stated is not None and stated != own:
        raise CurrencyRefused(
            f"{mask_card_number(account_name)} is kept in {own}; the statement is in "
            f"{stated}. Nothing was imported."
        )
    return own


def _label_for(conn, description: str, amount_minor: int) -> dict:
    """What the rules, then the PayNow wording, make of a row: the result of
    match_merchant, with the fallback's type when no rule gave one. A merchant
    the rules did find is kept, and so is its book."""
    found = match_merchant(description, conn, amount_minor=amount_minor)
    if found["type_id"] is None:
        book, type_id = _paynow_fallback_label(description, conn)
        if type_id is not None:
            found.update(book=found["book"] or book, type_id=type_id, cat_source="fallback")
    return found


def _tie_line(conn, stmt, figures: dict) -> dict:
    """The tie line of a parsed statement as the import preview shows it:
    opening, what the rows add up to, closing and the difference, each in
    whole minor units and as text, and whether it ties."""
    account = stmt.accounts[0] if stmt.accounts else "Unknown"
    try:
        currency = _account_currency(conn, account, stmt.currency)
    except CurrencyRefused:
        currency = stmt.currency
    return {
        "file": stmt.filename,
        "account": mask_card_number(account),
        "currency": currency,
        "opening_date": stmt.opening_date,
        "closing_date": stmt.closing_date,
        "status": "ties" if figures["difference_minor"] == 0 else "off",
        **figures,
        "opening": anchors.format_amount(figures["opening_minor"], currency),
        "rows_sum": anchors.format_amount(figures["rows_minor"], currency),
        "closing": anchors.format_amount(figures["closing_minor"], currency),
        "difference": anchors.format_amount(abs(figures["difference_minor"]), currency),
    }


def _record_refused(conn, stmt, line: dict) -> None:
    """Keep a statement refused at upload (screens.py), and commit."""
    from ingest import find_account
    account = stmt.accounts[0] if stmt.accounts else "Unknown"
    screens.record_refused(
        conn,
        account_name=mask_card_number(account),
        account_id=find_account(conn, account),
        statement_date=stmt.closing_date or stmt.statement_date,
        currency=line["currency"],
        figures=line,
    )
    conn.commit()


class AnchorRefused(Exception):
    """A statement's closing balance differs from the anchor already held for
    its account and date."""


def _kind_from_account_name(account_name: str) -> str:
    """The kind an import gives an account it has to create, from its name."""
    name = account_name.lower()
    if "bank" in name or "one account" in name or "home" in name:
        return "bank"
    return "card"  # default; could detect from account name


def _account_facts(conn, tx_id) -> dict:
    """The kind and owner of the account a row is on, as the classifier reads them."""
    row = conn.execute(
        "SELECT a.type, a.owner FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id WHERE t.id = ?",
        (tx_id,),
    ).fetchone()
    return {"account_kind": row["type"], "account_owner": row["owner"]} if row else {}


def _classify_flow_for_tx(
    conn, description: str, amount_minor: int, *,
    flow_ctx=None, tx_id=None, service_id=None, labelled: bool = False,
) -> tuple[str, int | None]:
    """Classify a row using the shared flow model: (flow, its other side).

    tx_id says which account the row is on; service_id and labelled say what
    the merchant rules made of it."""
    if flow_ctx is None:
        flow_ctx = flow.build_context(conn)
    facts = {
        "description": description,
        "amount_minor": amount_minor,
        "service_id": service_id,
        "labelled": labelled,
        **_account_facts(conn, tx_id),
    }
    return flow.classify_row(facts, flow_ctx)


def _checked_other_side(conn, value, flow_name) -> int | None:
    """An other side as a writer may store it: the id of an account, on a row
    whose flow may name one; or none."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnknownLabel(f"unknown other side: {value!r}")
    if flow_name not in flow.NAMES_OTHER_SIDE:
        raise UnknownLabel(f"a row with flow {flow_name!r} names no other side")
    if not conn.execute("SELECT 1 FROM accounts WHERE id = ?", (value,)).fetchone():
        raise UnknownLabel(f"unknown other side: {value!r}")
    return value


def _expense_visibility_filter(service_alias: str = "svc") -> str:
    """SQL clause excluding services hidden from dashboard and expense tables."""
    return f"({service_alias}.exclude_from_expense_views IS NULL OR {service_alias}.exclude_from_expense_views = 0)"


def _crud_insert(conn, sql: str, params: tuple, entity: str, post_commit=None) -> tuple:
    """Execute an INSERT, commit, return (response, status_code).

    On success returns ({"id": N, "success": True}, 200).
    On failure returns ({"error": "..."}, 400).
    post_commit is called after commit if provided (e.g. cache invalidation).
    """
    try:
        cur = conn.execute(sql, params)
        new_id = cur.lastrowid
        conn.commit()
        if post_commit:
            post_commit()
        return jsonify({"id": new_id, "success": True}), 200
    except Exception as e:
        app.logger.warning("Failed to create %s: %s", entity, e)
        return jsonify({"error": f"Failed to create {entity}"}), 400


def _build_update_sets(data: dict | None, allowed: list[str]) -> tuple[list[str], list]:
    """Build SET clause fragments and params from allowed fields present in data."""
    if not data:
        return [], []
    sets = []
    params = []
    for field in allowed:
        if field in data:
            sets.append(f"{field} = ?")
            params.append(data[field])
    return sets, params


def _crud_update(table: str, entity_id: int, data: dict | None, allowed: list[str], post_commit=None):
    """Validate and execute a simple UPDATE by allowed field list.

    Returns Flask response tuple. Handles empty/no-fields-to-update errors.
    """
    sets, params = _build_update_sets(data, allowed)
    if not sets:
        return jsonify({"error": "No fields to update"}), 400
    with get_db() as conn:
        params.append(entity_id)
        conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        if post_commit:
            post_commit()
    return jsonify({"success": True})


def mask_card_number(text: str) -> str:
    """Replace full card numbers (4-4-4-4 or 16 digits) with masked version showing last 4."""
    # Pattern: 4 groups of 4 digits separated by dashes or spaces
    text = re.sub(
        r'\b(\d{4})[-\s](\d{4})[-\s](\d{4})[-\s](\d{4})\b',
        r'****-****-****-\4',
        text,
    )
    # Pattern: 16 consecutive digits
    text = re.sub(r'\b\d{12}(\d{4})\b', r'************\1', text)
    return text

# Max upload size: 10MB
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024


# ---------------------------------------------------------------------------
# The change history (history.py): every request that may write opens an
# entry, the triggers record the rows it changes, and the entry is closed
# when the response is ready. One write at a time.
# ---------------------------------------------------------------------------

_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# What each writing route did, in words, for the history's list.
_CHANGE_SUMMARIES = {
    "api_accounts_create": "Added an account",
    "api_accounts_update": "Changed an account",
    "api_accounts_delete": "Deleted an account",
    "api_anchors_create": "Entered a figure",
    "api_anchors_replace": "Corrected a figure",
    "api_anchors_delete": "Deleted a figure",
    "api_loan_interest": "Worked out loan interest",
    "api_services_bulk_rename": "Renamed merchants",
    "api_services_create": "Added a merchant",
    "api_services_update": "Changed a merchant",
    "api_services_merge": "Merged merchants",
    "api_services_delete": "Deleted a merchant",
    "api_update_transaction": "Changed a row's labels",
    "api_resolve_transaction": "Gave a row a merchant and type",
    "api_import_upload": "Read a statement for import",
    "api_import_confirm": "Imported a statement",
    "api_rules_create": "Added a merchant rule",
    "api_rules_update": "Changed a merchant rule",
    "api_rules_delete": "Deleted a merchant rule",
    "api_rules_recategorize": "Re-ran every merchant rule",
    "api_rates_fetch": "Fetched an exchange rate",
    "api_rates_overwrite": "Overwrote an exchange rate",
    "api_review_label": "Labelled a waiting transfer",
    "api_pair_matching": "Paired transfers between own accounts",
    "api_subscriptions_create": "Added a subscription",
    "api_subscriptions_update": "Changed a subscription",
    "api_subscriptions_delete": "Deleted a subscription",
    "api_subscriptions_enrich": "Filled in subscriptions",
    "api_history_undo": "Undid a change",
}


def _caller() -> tuple[str, str]:
    """Who is asking: (via, actor). The front door (serve.py when hosted, the
    chat tools in-process) puts them in the WSGI environ; nothing a client
    sends can set them. Anything else is the operator in fin's own screens."""
    via = request.environ.get("fin.via", history.VIA_APP)
    if via not in history.VIAS:
        via = history.VIA_APP
    actor = request.environ.get("fin.actor") or history.APP_ACTOR
    return via, str(actor)[:80]


@app.before_request
def _open_change_entry():
    if request.method not in _WRITE_METHODS:
        return None
    history.WRITE_LOCK.acquire()
    g.change_locked = True
    via, actor = _caller()
    with get_db() as conn:
        g.change_entry = history.open_entry(conn, via, actor, request.environ.get("fin.summary", ""))
    return None


def _close_change_entry() -> tuple[int | None, int]:
    entry_id = g.pop("change_entry", None)
    if entry_id is None:
        return None, 0
    summary = _CHANGE_SUMMARIES.get(request.endpoint or "", f"Changed something ({request.endpoint})")
    with get_db() as conn:
        count = history.close_entry(conn, entry_id, summary)
    return (entry_id if count else None), count


@app.after_request
def _finish_change_entry(response):
    entry_id, count = _close_change_entry()
    if entry_id is not None:
        response.headers["X-Fin-Change"] = str(entry_id)
        response.headers["X-Fin-Change-Rows"] = str(count)
    return response


@app.teardown_request
def _release_write_lock(exc):
    try:
        if "change_entry" in g:  # the request failed before a response
            _close_change_entry()
    finally:
        if g.pop("change_locked", False):
            history.WRITE_LOCK.release()


@app.route("/api/history")
def api_history():
    """The change history, newest first: ?limit= (default 50), ?before=<id>
    for the page after. Each entry: when (UTC), via (app or chat), actor,
    summary, how many rows, and whether it undid or was undone by another."""
    try:
        limit = int(request.args.get("limit", 50))
        before = request.args.get("before")
        before = int(before) if before is not None else None
    except ValueError:
        return jsonify({"error": "limit and before must be whole numbers"}), 400
    with get_db() as conn:
        shown = history.entries(conn, limit, before)
        asked = history.asked_counts(conn, [e["id"] for e in shown])
        for e in shown:
            e["asked_first"] = e["id"] in asked
            e["asked_count"] = asked.get(e["id"])
        if request.args.get("blockers") == "1":
            # What would refuse each entry's undo, named, so the list can say
            # so before the operator taps it.
            for e in shown:
                later = history.blocker(conn, e["id"]) if e["undone_by"] is None else None
                e["blocked_by"] = None if later is None else {
                    "id": later["id"], "summary": later["summary"], "at": later["at"],
                    "via": later["via"], "actor": later["actor"],
                }
        return jsonify({"entries": shown, "many_rows": mcp_tools.MANY_ROWS,
                        **screens.since_looked(conn)})


def _change_currencies(conn, changes: list[dict]) -> None:
    """Give each changed row the currency its amounts are in: a row's and a
    figure's are their account's, an account's its own, a bill's its own.
    A statement removed by the same entry still names its account there."""
    def account_currency(account_id):
        if account_id is None:
            return None
        found = conn.execute("SELECT currency FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return (found[0] or "SGD") if found else None

    statement_account = {
        c["row_id"]: (c["after"] or c["before"] or {}).get("account_id")
        for c in changes if c["table"] == "statements"
    }
    for c in changes:
        row = c["after"] or c["before"] or {}
        currency = None
        if c["table"] == "transactions":
            sid = row.get("statement_id")
            account_id = statement_account.get(sid)
            if account_id is None and sid is not None:
                found = conn.execute("SELECT account_id FROM statements WHERE id = ?", (sid,)).fetchone()
                account_id = found[0] if found else None
            currency = account_currency(account_id)
        elif c["table"] in ("anchors", "statements"):
            currency = account_currency(row.get("account_id"))
        elif c["table"] in ("accounts", "subscriptions"):
            currency = row.get("currency")
        c["currency"] = currency or "SGD"


@app.route("/api/history/<int:entry_id>")
def api_history_entry(entry_id: int):
    """One entry with every row it changed, before and after, and the later
    change that would block undoing it, if any."""
    with get_db() as conn:
        shown = history.entry(conn, entry_id)
        if shown is None:
            return jsonify({"error": "no such change"}), 404
        shown["blocked_by"] = history.blocker(conn, entry_id) if shown["undone_by"] is None else None
        asked = history.asked_counts(conn, [entry_id])
        shown["asked_first"] = entry_id in asked
        shown["asked_count"] = asked.get(entry_id)
        _change_currencies(conn, shown["changes"])
    return jsonify(shown)


@app.route("/api/history/row/<table>/<int:row_id>")
def api_history_row(table: str, row_id: int):
    """The entries that changed one row, newest first."""
    if table not in history.TRACKED:
        return jsonify({"error": "unknown table"}), 404
    with get_db() as conn:
        return jsonify({"entries": history.row_entries(conn, table, row_id)})


@app.route("/api/history/<int:entry_id>/undo", methods=["POST"])
def api_history_undo(entry_id: int):
    """Undo one change: every row it changed goes back to how it was, as a
    new entry. Refused, changing nothing, when a later change touched the
    same rows (it is named: undo it first) or the change was already undone."""
    with get_db() as conn:
        try:
            done = history.undo(conn, entry_id, g.change_entry)
        except history.UndoRefused as e:
            return jsonify({"error": str(e)}), 409
        touched = {r[0] for r in conn.execute(
            "SELECT DISTINCT tbl FROM change_rows WHERE entry_id = ?", (entry_id,))}
    # The merchant-rule cache reads rules and their merchants: an undo that
    # wrote either back leaves it stale. The chat's undo runs this route too.
    if touched & _RULE_CACHE_TABLES:
        db.invalidate_rules_cache()
    return jsonify({"ok": True, **done})


_RULE_CACHE_TABLES = frozenset({"merchant_rules", "services"})


# ---------------------------------------------------------------------------
# What the screens keep (screens.py): the "Claude may write" switch, where the
# operator last looked in the history, and statements refused at upload.
# None is a change to the book: these writes leave no history entry.
# ---------------------------------------------------------------------------

def _from_chat() -> bool:
    return _caller()[0] == history.VIA_CHAT


_SCREENS_ONLY = "only fin's own screens can do that; nothing was changed"


def _chat_import_refused():
    """An import from the chat's side (the upload command, stamped via chat)
    while the operator has switched Claude's writes off: the refusal to
    return. None when the import may go ahead."""
    if not _from_chat():
        return None
    with get_db() as conn:
        if screens.claude_may_write(conn):
            return None
    return jsonify({"error": screens.IMPORTS_OFF}), 403


@app.route("/api/settings")
def api_settings():
    """The "Claude may write" switch and the history mark Home counts from."""
    with get_db() as conn:
        return jsonify({"claude_may_write": screens.claude_may_write(conn), **screens.since_looked(conn)})


@app.route("/api/settings/claude-write", methods=["PUT"])
def api_settings_claude_write():
    """Switch Claude's writes on or off. Body: on (true or false). Only from
    fin's own screens: a chat client can never set it."""
    if _from_chat():
        return jsonify({"error": _SCREENS_ONLY}), 403
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("on"), bool):
        return jsonify({"error": "on must be true or false"}), 400
    with get_db() as conn:
        screens.set_claude_may_write(conn, data["on"])
        return jsonify({"claude_may_write": screens.claude_may_write(conn)})


@app.route("/api/settings/claude-write/off", methods=["POST"])
def api_settings_claude_write_off():
    """Switch Claude's writes off: from fin's screens or from the chat (01:
    the switch "can only be turned off from chat's side", for a lost phone or
    odd behaviour). It only ever turns writes off; turning them back on is
    the PUT above, from fin's own screens only. Not a change to the book."""
    with get_db() as conn:
        screens.set_claude_may_write(conn, False)
        return jsonify({"claude_may_write": screens.claude_may_write(conn)})


@app.route("/api/changes/marks")
def api_changes_marks():
    """The quiet mark (01, "For 02" item 4): the rows the chat changed since
    the operator last looked, and the accounts whose balance-sheet line it
    touched, each with the newest such change (its id in the history)."""
    with get_db() as conn:
        return jsonify(screens.claude_marks(conn))


@app.route("/api/changes/looked", methods=["POST"])
def api_changes_looked():
    """The operator looked: Recent changes was opened, or "Looks right" was
    tapped on Home. Body: upto, the newest entry they saw (optional; the
    newest entry when left out). The mark never moves back."""
    if _from_chat():
        return jsonify({"error": _SCREENS_ONLY}), 403
    data = request.get_json(silent=True) or {}
    upto = data.get("upto")
    if upto is not None and (isinstance(upto, bool) or not isinstance(upto, int)):
        return jsonify({"error": "upto must be a change number"}), 400
    with get_db() as conn:
        screens.mark_looked(conn, upto)
        return jsonify(screens.since_looked(conn))


@app.route("/api/statements/refused")
def api_statements_refused():
    """Statements refused at upload that still stand (no file that ties has
    been imported for the same account and day), newest first; ?account_id=
    for one account's. Each with its tie line in whole minor units and
    whether it was set aside ("Known, leave it")."""
    account_id = request.args.get("account_id", type=int)
    with get_db() as conn:
        return jsonify({"refused": screens.refused(conn, account_id)})


@app.route("/api/statements/refused/<int:refused_id>/set-aside", methods=["POST"])
def api_statements_refused_set_aside(refused_id: int):
    """"Known, leave it": off Home and the queue's top, still marked refused
    on its account. Body: aside (default true); false brings it back."""
    if _from_chat():
        return jsonify({"error": _SCREENS_ONLY}), 403
    data = request.get_json(silent=True) or {}
    aside = data.get("aside", True)
    if not isinstance(aside, bool):
        return jsonify({"error": "aside must be true or false"}), 400
    with get_db() as conn:
        if not screens.set_aside(conn, refused_id, aside):
            return jsonify({"error": "no such refused statement"}), 404
    return jsonify({"ok": True, "id": refused_id, "set_aside": aside})


@app.route("/api/accounts/<int:acct_id>/ties")
def api_account_ties(acct_id: int):
    """One account's tie lines, newest first: for each statement balance
    after the first, the balance before it, what the rows between add up to,
    the balance stated and the difference (zero when it ties)."""
    with get_db() as conn:
        account = conn.execute("SELECT * FROM accounts WHERE id = ?", (acct_id,)).fetchone()
        if account is None:
            return jsonify({"error": "no such account"}), 404
        return jsonify({
            "account_id": acct_id,
            "currency": account["currency"] or "SGD",
            "ties": balance_sheet.tie_lines(conn, account),
        })


# ---------------------------------------------------------------------------
# Static file serving
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

@app.route("/api/types")
def api_types():
    """The type list as a flat list with parent info: spending types, or with
    ?kind=income the income kinds. Read-only: the list is declared in
    book_type.py and the table is filled from that declaration alone."""
    kind = book_type.INCOME if request.args.get("kind") == book_type.INCOME else book_type.SPENDING
    with get_db() as conn:
        rows = conn.execute(
            "SELECT t.id, t.kind, t.name, t.parent_id, p.name as parent_name, "
            "t.default_one_off, t.covers, t.not_for "
            "FROM types t LEFT JOIN types p ON t.parent_id = p.id "
            "WHERE t.kind = ? ORDER BY t.id",
            (kind,),
        ).fetchall()
    return jsonify([{
        "id": r["id"],
        "kind": r["kind"],
        "name": r["name"],
        "parent_id": r["parent_id"],
        "parent_name": r["parent_name"],
        "display_name": format_type_display(r["parent_name"], r["name"]),
        "default_one_off": r["default_one_off"],
        "covers": r["covers"],
        "not_for": r["not_for"],
        # The book this type proposes for a merchant no rule knows; null = ask.
        "proposed_book": (
            book_type.proposed_book(format_type_display(r["parent_name"], r["name"]))
            if kind == book_type.SPENDING else None
        ),
    } for r in rows])


@app.route("/api/books")
def api_books():
    """The books, from their one declaration."""
    return jsonify([
        {"name": name, "description": description} for name, description in book_type.BOOKS
    ])


@app.route("/api/flows")
def api_flows():
    """The flow list, from its one declaration."""
    return jsonify([
        {"name": name, "description": description} for name, description in flow.FLOWS
    ])


@app.route("/api/account-kinds")
def api_account_kinds():
    """The account kinds, the owners and the anchor sources, each from its
    one declaration."""
    return jsonify({
        "kinds": [
            {
                "name": name,
                "description": description,
                # Whether the enter-a-figure dialog offers accounts of this kind.
                "takes_a_figure": name in account_kind.SUPPLIED_FIGURE_KINDS,
                "has_statements": name in account_kind.STATEMENT_KINDS,
            }
            for name, description in account_kind.KINDS
        ],
        "owners": [
            {"name": name, "description": description}
            for name, description in account_kind.OWNERS
        ],
        "anchor_sources": [
            {"name": name, "description": description}
            for name, description in anchors.SOURCES
        ],
    })


@app.route("/api/accounts")
def api_accounts():
    """List all accounts. `type` is the account's kind; `anchor` is the latest
    anchor the account rests on, or null when it has no figure;
    `takes_a_figure` is whether the enter-a-figure dialog offers it for a new
    figure; `supplied_figures` is how many figures were typed for it, each of
    which the dialog can correct or delete."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, short_name, type, last_four, currency, status, owner FROM accounts ORDER BY name"
        ).fetchall()
        statement_counts = dict(conn.execute(
            "SELECT account_id, COUNT(*) FROM statements GROUP BY account_id"
        ).fetchall())
        supplied_counts = dict(conn.execute(
            "SELECT account_id, COUNT(*) FROM anchors WHERE source = ? GROUP BY account_id",
            (anchors.SUPPLIED,),
        ).fetchall())
        latest = {
            r["account_id"]: {"date": r["date"], "amount_minor": r["amount"], "source": r["source"]}
            for r in conn.execute(
                "SELECT a.account_id, a.date, a.amount, a.source FROM anchors a "
                "WHERE a.date = (SELECT MAX(b.date) FROM anchors b WHERE b.account_id = a.account_id)"
            )
        }
    result = []
    for r in rows:
        d = dict(r)
        d["name"] = mask_card_number(d["name"])
        d["short_name"] = mask_card_number(d["short_name"])
        d["anchor"] = latest.get(d["id"])
        d["takes_a_figure"] = account_kind.takes_a_figure(
            d["type"], statement_counts.get(d["id"], 0)
        )
        # Figures typed for it, which can be corrected or deleted even once
        # the account no longer takes a new one.
        d["supplied_figures"] = supplied_counts.get(d["id"], 0)
        result.append(d)
    return jsonify(result)


@app.route("/api/accounts", methods=["POST"])
def api_accounts_create():
    """Create a new account."""
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Account name is required"}), 400

    try:
        kind = account_kind.checked_kind(data.get("type", account_kind.DEFAULT_KIND))
        owner = account_kind.checked_owner(data.get("owner", account_kind.DEFAULT_OWNER))
        currency = _checked_currency(data.get("currency", "SGD"))
    except (account_kind.UnknownAccountValue, CurrencyRefused) as e:
        return jsonify({"error": str(e)}), 400

    with get_db() as conn:
        return _crud_insert(
            conn,
            "INSERT INTO accounts (name, short_name, type, last_four, currency, owner) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                data["name"],
                data.get("short_name", data["name"]),
                kind,
                data.get("last_four"),
                currency,
                owner,
            ),
            "account",
        )


@app.route("/api/accounts/<int:acct_id>", methods=["PUT"])
def api_accounts_update(acct_id):
    """Update an account."""
    data = request.get_json()
    try:
        if data and "type" in data:
            account_kind.checked_kind(data["type"])
        if data and "owner" in data:
            account_kind.checked_owner(data["owner"])
        if data and "currency" in data:
            _checked_currency(data["currency"])
    except (account_kind.UnknownAccountValue, CurrencyRefused) as e:
        return jsonify({"error": str(e)}), 400
    return _crud_update("accounts", acct_id, data,
                        ["name", "short_name", "type", "last_four", "currency", "status", "owner"])


@app.route("/api/accounts/<int:acct_id>", methods=["DELETE"])
def api_accounts_delete(acct_id):
    """Delete an account. Refuses if statements or anchors reference it, or a
    row names it as its other side."""
    with get_db() as conn:
        stmt_count = conn.execute(
            "SELECT COUNT(*) FROM statements WHERE account_id = ?", (acct_id,)
        ).fetchone()[0]
        if stmt_count > 0:
            return jsonify({
                "error": f"Cannot delete: {stmt_count} statement(s) reference this account"
            }), 400
        anchor_count = conn.execute(
            "SELECT COUNT(*) FROM anchors WHERE account_id = ?", (acct_id,)
        ).fetchone()[0]
        if anchor_count > 0:
            return jsonify({
                "error": f"Cannot delete: {anchor_count} figure(s) are held for this account"
            }), 400

        named_count = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE other_side_id = ?", (acct_id,)
        ).fetchone()[0]
        if named_count > 0:
            return jsonify({
                "error": f"Cannot delete: {named_count} row(s) name this account as their other side"
            }), 400

        conn.execute("DELETE FROM accounts WHERE id = ?", (acct_id,))
        conn.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Anchors: an account's balance on a date
# ---------------------------------------------------------------------------

def _anchor_payload(row) -> dict:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "account_name": mask_card_number(row["account_name"]),
        "kind": row["kind"],
        "currency": row["currency"] or "SGD",
        "date": row["date"],
        "amount_minor": row["amount"],
        "source": row["source"],
        "note": row["note"],
    }


_ANCHOR_SELECT = (
    "SELECT n.id, n.account_id, a.name AS account_name, a.type AS kind, a.currency, "
    "n.date, n.amount, n.source, n.note "
    "FROM anchors n JOIN accounts a ON a.id = n.account_id "
)


@app.route("/api/anchors")
def api_anchors():
    """Every anchor, newest first; with ?account_id= those of one account.
    `amount_minor` is whole minor units of the account's currency, signed as
    the household sees it: owned positive, owed negative."""
    account_id = request.args.get("account_id", type=int)
    with get_db() as conn:
        if account_id is None:
            rows = conn.execute(_ANCHOR_SELECT + "ORDER BY n.date DESC, n.id DESC").fetchall()
        else:
            rows = conn.execute(
                _ANCHOR_SELECT + "WHERE n.account_id = ? ORDER BY n.date DESC, n.id DESC",
                (account_id,),
            ).fetchall()
    return jsonify([_anchor_payload(r) for r in rows])


@app.route("/api/anchors", methods=["POST"])
def api_anchors_create():
    """Enter a figure: a supplied anchor for a loan, a holding, a company, a
    person, or a bank account that has no statement.

    Body: account_id, amount (text, in whole units of the account's currency:
    what is owed for a loan, what it is worth or the balance otherwise), date
    (YYYY-MM-DD), note. The same amount again for that account and date
    changes nothing; a different amount is refused.

    A figure for a loan that follows or precedes another works the interest
    out again (loan_interest.py). `worked_out` is the period that ends at the
    figure, null when there is none; `message` says it in words.
    """
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "the body must be a JSON object"}), 400
    account_id = data.get("account_id")
    if isinstance(account_id, bool) or not isinstance(account_id, int):
        return jsonify({"error": "account_id is required: the account the figure is for"}), 400
    note = data.get("note")
    if note is not None and not isinstance(note, str):
        return jsonify({"error": "note must be text"}), 400
    note = (note or "").strip() or None

    with get_db() as conn:
        account = conn.execute(
            "SELECT id, name, type, currency FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if account is None:
            return jsonify({"error": "no such account"}), 404
        kind = account["type"]
        statement_count = conn.execute(
            "SELECT COUNT(*) FROM statements WHERE account_id = ?", (account_id,)
        ).fetchone()[0]
        if not account_kind.takes_a_figure(kind, statement_count):
            return jsonify({
                "error": f"a {kind} account rests on its statement; a figure can be entered "
                         "for a loan, a holding, a company, a person, or a bank account "
                         "that has no statement"
            }), 400
        try:
            amount_minor = _typed_figure(account, data.get("amount"))
            on = anchors.checked_date(data.get("date"))
            held, created = anchors.record(
                conn, account_id, on, amount_minor, anchors.SUPPLIED, note
            )
        except anchors.InvalidAnchor as e:
            return jsonify({"error": str(e)}), 400
        except anchors.AnchorConflict as e:
            shown = e.existing["amount"]
            if kind in account_kind.OWED_KINDS:
                shown = -shown
            return jsonify({
                "error": f"{mask_card_number(account['name'])} already has a figure of "
                         f"{anchors.format_amount(shown, account['currency'])} for "
                         f"{e.existing['date']}; a different amount for the same date is refused"
            }), 409
        worked_out = message = None
        if kind == loan_interest.LOAN:
            loan_interest.derive(conn, account_id)
            worked_out, message = loan_interest.worked_out(
                conn, account_id, on, account["currency"]
            )
        conn.commit()
        row = conn.execute(_ANCHOR_SELECT + "WHERE n.id = ?", (held["id"],)).fetchone()
    return jsonify({
        "success": True,
        "created": created,
        "anchor": _anchor_payload(row),
        "worked_out": worked_out,
        "message": message,
    })


def _typed_figure(account, amount) -> int:
    """A typed figure as an anchor stores it: whole minor units of the
    account's currency, and for a loan what is owed, typed positive and
    stored negative."""
    amount_minor = anchors.to_minor_units(amount, account["currency"])
    if account["type"] in account_kind.OWED_KINDS:
        if amount_minor < 0:
            raise anchors.InvalidAnchor(
                "amount for a loan is what is owed, entered as a positive figure"
            )
        amount_minor = -amount_minor
    return amount_minor


def _figure_date_allowed(conn, account, on: str) -> None:
    """Refuse moving a typed figure for a bank account that holds statements
    onto a day its statements cover: as a new figure is refused once it has
    one, a corrected one may only sit before its first statement."""
    if account["type"] not in account_kind.SUPPLIED_UNTIL_STATEMENT_KINDS:
        return
    first = conn.execute(
        "SELECT MIN(d) FROM (SELECT statement_date AS d FROM statements WHERE account_id = ? "
        "UNION ALL SELECT date FROM anchors WHERE account_id = ? AND source = ?)",
        (account["id"], account["id"], anchors.STATEMENT),
    ).fetchone()[0]
    if first is not None and on >= first:
        raise anchors.InvalidAnchor(
            f"a {account['type']} account rests on its statements from {first}; "
            "a figure you entered can only be dated before that"
        )


def _anchor_account(conn, anchor_id: int):
    return conn.execute(
        "SELECT a.id, a.name, a.type, a.currency FROM anchors n "
        "JOIN accounts a ON a.id = n.account_id WHERE n.id = ?",
        (anchor_id,),
    ).fetchone()


@app.route("/api/anchors/<int:anchor_id>", methods=["PUT"])
def api_anchors_replace(anchor_id: int):
    """Correct a figure you entered: its amount, date or note; what is not
    sent stays as it is. The amount is typed as for a new figure (what is
    owed, positive, for a loan). A statement's balance is refused: it is the
    statement's fact. A date the account already has another figure or
    statement balance for is refused (409). Balances and checks are worked
    out on read, so they follow at once; for a loan the interest is worked
    out again, and `message` says what that came to."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "the body must be a JSON object"}), 400
    note_sent = "note" in data
    note = data.get("note")
    if note is not None and not isinstance(note, str):
        return jsonify({"error": "note must be text"}), 400
    with get_db() as conn:
        account = _anchor_account(conn, anchor_id)
        if account is None:
            return jsonify({"error": "no such figure"}), 404
        held = anchors.held_by_id(conn, anchor_id)
        try:
            amount_minor = (
                _typed_figure(account, data["amount"]) if "amount" in data else held["amount"]
            )
            on = data["date"] if "date" in data else held["date"]
            if "date" in data and on != held["date"]:
                _figure_date_allowed(conn, account, anchors.checked_date(on))
            replaced = anchors.replace(
                conn, anchor_id, on, amount_minor,
                ((note or "").strip() or None) if note_sent else held["note"],
            )
        except anchors.NotSupplied as e:
            return jsonify({"error": str(e)}), 400
        except anchors.InvalidAnchor as e:
            return jsonify({"error": str(e)}), 400
        except anchors.AnchorConflict as e:
            return jsonify({
                "error": f"{mask_card_number(account['name'])} already has a figure for "
                         f"{e.existing['date']}; correct or delete that one instead"
            }), 409
        message = None
        if account["type"] == loan_interest.LOAN:
            loan_interest.derive(conn, account["id"])
            _, message = loan_interest.worked_out(
                conn, account["id"], replaced["date"], account["currency"]
            )
        conn.commit()
        row = conn.execute(_ANCHOR_SELECT + "WHERE n.id = ?", (anchor_id,)).fetchone()
    return jsonify({"success": True, "anchor": _anchor_payload(row), "message": message})


@app.route("/api/anchors/<int:anchor_id>", methods=["DELETE"])
def api_anchors_delete(anchor_id: int):
    """Delete a figure you entered. A statement's balance is refused. The
    balances rest on the figures that remain; for a loan the interest is
    worked out again from them."""
    with get_db() as conn:
        account = _anchor_account(conn, anchor_id)
        if account is None:
            return jsonify({"error": "no such figure"}), 404
        try:
            anchors.remove(conn, anchor_id)
        except anchors.NotSupplied as e:
            return jsonify({"error": str(e)}), 400
        if account["type"] == loan_interest.LOAN:
            loan_interest.derive(conn, account["id"])
        conn.commit()
    return jsonify({"success": True, "id": anchor_id})


@app.route("/api/loan-interest", methods=["POST"])
def api_loan_interest():
    """Work the loan interest out again, on demand, for every loan: the same
    derivation entering a figure runs. Rows that already say what the figures
    and the instalments give are left as they are.

    Returns changed, the number of loans whose derived rows were replaced."""
    with get_db() as conn:
        changed = loan_interest.derive(conn)
        conn.commit()
    return jsonify({"changed": changed})


# ---------------------------------------------------------------------------
# Services API
# ---------------------------------------------------------------------------

@app.route("/api/services")
def api_services():
    """List all services with their book, type and transaction/rule counts."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.*, ty.name as type_name, tp.name as parent_type,
                   (SELECT COUNT(*) FROM transactions t WHERE t.service_id = s.id
                    AND COALESCE(t.flow_type, 'expense') NOT IN ('transfer', 'payment')) as txn_count,
                   (SELECT COUNT(*) FROM merchant_rules mr WHERE mr.service_id = s.id) as rule_count
            FROM services s
            """ + _TYPE_JOINS.format(owner="s") + """
            ORDER BY s.name
        """).fetchall()
        # Fetch all rule patterns grouped by service_id
        rule_rows = conn.execute(
            "SELECT service_id, pattern, match_type FROM merchant_rules WHERE service_id IS NOT NULL ORDER BY pattern"
        ).fetchall()
    rules_by_svc: dict[int, list[dict]] = {}
    for rr in rule_rows:
        rules_by_svc.setdefault(rr["service_id"], []).append(
            {"pattern": rr["pattern"], "match_type": rr["match_type"]}
        )

    result = []
    for r in rows:
        d = dict(r)
        d["display_type"] = format_type_display(d["parent_type"], d["type_name"])
        d["rules"] = rules_by_svc.get(d["id"], [])
        result.append(d)
    return jsonify(result)


@app.route("/api/services/bulk-rename", methods=["POST"])
def api_services_bulk_rename():
    """Bulk rename services. Body: { renames: [{id, name}, ...] }"""
    data = request.get_json()
    renames = data.get("renames", [])
    if not renames:
        return jsonify({"error": "No renames provided"}), 400

    with get_db() as conn:
        updated = 0
        errors = []
        for item in renames:
            svc_id = item.get("id")
            new_name = (item.get("name") or "").strip()
            if not svc_id or not new_name:
                continue
            try:
                conn.execute("UPDATE services SET name = ? WHERE id = ?", (new_name, svc_id))
                updated += 1
            except Exception as e:
                errors.append(f"{item.get('name')}: {e}")
        conn.commit()
    return jsonify({"updated": updated, "errors": errors})


@app.route("/api/services", methods=["POST"])
def api_services_create():
    """Create a new service."""
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Service name is required"}), 400
    with get_db() as conn:
        # A merchant carries a default book and type. With a type and no book
        # given, the book is the one the type proposes.
        try:
            type_id = _checked_type_id(conn, data.get("type_id"))
            book = _book_or_proposed(conn, _checked_book(data.get("book")), type_id)
        except (UnknownLabel, BookNeeded) as e:
            return jsonify({"error": str(e)}), 400
        return _crud_insert(
            conn,
            "INSERT INTO services (name, book, type_id, notes, exclude_from_expense_views, "
            "review_each_time) VALUES (?, ?, ?, ?, ?, ?)",
            (
                data["name"],
                book,
                type_id,
                data.get("notes"),
                data.get("exclude_from_expense_views", 0),
                1 if data.get("review_each_time") else 0,
            ),
            "service",
            post_commit=invalidate_rules_cache,
        )


@app.route("/api/services/<int:svc_id>", methods=["PUT"])
def api_services_update(svc_id):
    """Update a service. A change of its book or type is written to the
    transactions that take their label from it."""
    data = request.get_json()
    with get_db() as conn:
        sets, params = _build_update_sets(
            data,
            ["name", "book", "type_id", "notes", "is_one_off", "exclude_from_expense_views",
             "review_each_time"],
        )
        if not sets:
            return jsonify({"error": "Nothing to update"}), 400
        try:
            _checked_labels(conn, data)
        except UnknownLabel as e:
            return jsonify({"error": str(e)}), 400
        params.append(svc_id)
        try:
            was = conn.execute(
                "SELECT book, type_id FROM services WHERE id = ?", (svc_id,)
            ).fetchone()
            conn.execute(f"UPDATE services SET {', '.join(sets)} WHERE id = ?", params)

            # Relabel: when the merchant's default changes, rows that inherit
            # it take its book and type. Rows labelled by hand or by a rule
            # override keep theirs. A default sent back unchanged (a rename,
            # a note) relabels nothing.
            recategorized = 0
            if "book" in data or "type_id" in data:
                svc = conn.execute(
                    "SELECT book, type_id FROM services WHERE id = ?", (svc_id,)
                ).fetchone()
                if svc and tuple(svc) != tuple(was):
                    cur = conn.execute(
                        "UPDATE transactions SET book = ?, type_id = ? "
                        "WHERE service_id = ? AND (book IS NOT ? OR type_id IS NOT ?) "
                        "AND COALESCE(cat_source, 'auto') IN ('auto', 'service_default')",
                        (svc["book"], svc["type_id"], svc_id, svc["book"], svc["type_id"]),
                    )
                    recategorized = cur.rowcount

            conn.commit()
            invalidate_rules_cache()  # the rule cache holds each merchant's book and type
            return jsonify({"success": True, "recategorized": recategorized})
        except Exception as e:
            app.logger.warning("Failed to update service: %s", e)
            return jsonify({"error": "Failed to update service"}), 400


@app.route("/api/services/<int:svc_id>/merge", methods=["POST"])
def api_services_merge(svc_id):
    """Merge source service into target: reassign all FKs, delete source."""
    data = request.get_json()
    target_id = data.get("target_id")
    if not target_id or int(target_id) == svc_id:
        return jsonify({"error": "Invalid merge target"}), 400
    target_id = int(target_id)

    with get_db() as conn:
        # Verify both exist
        source = conn.execute("SELECT name FROM services WHERE id = ?", (svc_id,)).fetchone()
        target = conn.execute(
            "SELECT name, book, type_id FROM services WHERE id = ?", (target_id,)
        ).fetchone()
        if not source or not target:
            return jsonify({"error": "Service not found"}), 404

        # The rows being moved that inherit their merchant's default now
        # inherit the target's book and type. Rows labelled by hand or by a
        # rule override keep theirs (the rules move with them).
        relabelled = conn.execute(
            "UPDATE transactions SET book = ?, type_id = ? "
            "WHERE service_id = ? AND (book IS NOT ? OR type_id IS NOT ?) "
            "AND COALESCE(cat_source, 'auto') IN ('auto', 'service_default')",
            (target["book"], target["type_id"], svc_id, target["book"], target["type_id"]),
        ).rowcount

        # Reassign all references from source → target
        txn_count = conn.execute(
            "UPDATE transactions SET service_id = ? WHERE service_id = ?",
            (target_id, svc_id),
        ).rowcount
        rule_count = conn.execute(
            "UPDATE merchant_rules SET service_id = ? WHERE service_id = ?",
            (target_id, svc_id),
        ).rowcount
        sub_count = conn.execute(
            "UPDATE subscriptions SET service_id = ? WHERE service_id = ?",
            (target_id, svc_id),
        ).rowcount

        # Delete the now-orphaned source service
        conn.execute("DELETE FROM services WHERE id = ?", (svc_id,))
        conn.commit()
        invalidate_rules_cache()  # merge reassigns rule service_ids

    return jsonify({
        "success": True,
        "merged": {
            "source": source["name"],
            "target": target["name"],
            "transactions": txn_count,
            "rules": rule_count,
            "subscriptions": sub_count,
            "relabelled": relabelled,
        },
    })


@app.route("/api/services/<int:svc_id>", methods=["DELETE"])
def api_services_delete(svc_id):
    """Delete a service if no transactions/subscriptions reference it."""
    with get_db() as conn:
        refs = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE service_id = ?", (svc_id,)
        ).fetchone()[0]
        sub_refs = conn.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE service_id = ?", (svc_id,)
        ).fetchone()[0]
        if refs > 0 or sub_refs > 0:
            return jsonify({"error": f"Service has {refs} transactions and {sub_refs} subscriptions"}), 400
        conn.execute("DELETE FROM services WHERE id = ?", (svc_id,))
        conn.commit()
    return jsonify({"success": True})


@app.route("/api/services/<int:svc_id>/transactions")
def api_service_transactions(svc_id):
    """Get all transactions for a specific service."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.id, t.date, t.description, t.amount_minor,
                   t.amount_foreign, t.currency_foreign,
                   """ + book_expr("t") + """ as book,
                   t.type_id, ty.name as type_name, tp.name as parent_type
            FROM transactions t
            """ + _TYPE_JOINS.format(owner="t") + """
            WHERE t.service_id = ?
              AND COALESCE(t.flow_type, 'expense') NOT IN ('transfer', 'payment')
            ORDER BY t.date DESC
        """, (svc_id,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["amount_sgd"] = money.from_minor(d.pop("amount_minor"))
        d["display_type"] = format_type_display(d["parent_type"], d["type_name"])
        result.append(d)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Dashboard API
# ---------------------------------------------------------------------------

# Rows on an account kept in the currency the dashboard's figures are in. A
# row on an account in another currency (the rupee account) is whole minor
# units of that currency, and is never added to SGD cents as if it were SGD:
# it is held out of these sums. `s` is the row's statement.
_SGD_ACCOUNT = (
    "s.account_id IN (SELECT id FROM accounts WHERE COALESCE(currency, 'SGD') = 'SGD')"
)


# Rows on an account the household owns: what household spending is counted
# on (ruling 6). Takes the owner as its parameter.
_HOUSEHOLD_ACCOUNT = "s.account_id IN (SELECT id FROM accounts WHERE owner = ?)"


def _waiting_sides(conn, filters: str = "", params: tuple | list = ()) -> dict:
    """The transfers waiting for review, money out and money in apart: how
    many each side holds and what each adds up to in whole minor units, both
    as positive figures. `count` is both sides together and `net_minor` is
    money out less money in. `filters` narrows them by the row (t) or its
    statement (s). Every waiting row is counted; the totals are of the rows
    on SGD accounts."""
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN t.amount_minor < 0 THEN 0 ELSE 1 END), "
        f"SUM(CASE WHEN t.amount_minor >= 0 AND {_SGD_ACCOUNT} THEN t.amount_minor ELSE 0 END), "
        "SUM(CASE WHEN t.amount_minor < 0 THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN t.amount_minor < 0 AND {_SGD_ACCOUNT} THEN -t.amount_minor ELSE 0 END) "
        "FROM transactions t "
        f"LEFT JOIN statements s ON t.statement_id = s.id WHERE t.flow_type = ? {filters}",
        [flow.REVIEW, *params],
    ).fetchone()
    out_count, out_minor, in_count, in_minor = (value or 0 for value in row)
    return {
        "count": out_count + in_count,
        "net_minor": out_minor - in_minor,
        "out_count": out_count,
        "out_minor": out_minor,
        "in_count": in_count,
        "in_minor": in_minor,
    }


def _sides_payload(prefix: str, sides: dict) -> dict:
    """The two sides of what is waiting, as a payload states them."""
    return {
        f"{prefix}_out_count": sides["out_count"],
        f"{prefix}_out_total": money.from_minor(sides["out_minor"]),
        f"{prefix}_in_count": sides["in_count"],
        f"{prefix}_in_total": money.from_minor(sides["in_minor"]),
    }


def _waiting_for_review(conn, filters: str = "", params: tuple | list = ()) -> tuple[int, int]:
    """How many transfers are waiting for review, and what they add up to in
    whole minor units: money out less money in."""
    sides = _waiting_sides(conn, filters, params)
    return sides["count"], sides["net_minor"]


@app.route("/api/dashboard/stat-cards")
def api_dashboard_stat_cards():
    """Stat cards: single month spend + delta vs 3-month rolling average.

    Auto-picks reference month using the 15th rule:
      - If today >= 15th, ref = previous month
      - If today < 15th, ref = two months ago
    Override with ?ref_month=YYYY-MM.

    Respects: book, exclude_one_off, account_id

    The cards:
      household, household_rows   spending and refunds in the Household book,
                                  on accounts the household owns, archived
                                  or not, hidden merchants included (ruling
                                  6). No company's row and no row waiting
                                  for review is in it.
      held_out_count, _total      the month's transfers waiting for review:
                                  what the household figure is held short of.
                                  _total is money out less money in;
                                  held_out_out_count, _out_total and
                                  held_out_in_count, _in_total state the two
                                  sides apart, each as a positive figure.
      moom, kalesh                each company's costs paid from accounts the
                                  household owns. Beside the headline, never
                                  added into it.
      waiting, waiting_total      every transfer waiting, whenever dated: the
                                  review list, with waiting_out_count,
                                  _out_total, waiting_in_count, _in_total
                                  the same way. untyped counts the month's
                                  spending rows with no type.
      loan_principal              the month's loan instalments less the
                                  interest worked out for it (loan_interest.py):
                                  not spending. loan_instalments and
                                  loan_interest are its two parts. cash_out is
                                  household plus loan_principal.
    spend is the View filter's figure (every book, or the one asked for, on
    any account), as the charts and the list show it; no card shows it.
    """
    # Determine reference month
    ref_month_param = request.args.get("ref_month")
    if ref_month_param:
        ref_y, ref_m = int(ref_month_param[:4]), int(ref_month_param[5:7])
    else:
        today = date.today()
        ref_y, ref_m = today.year, today.month
        if today.day >= 15:
            ref_m -= 1
            if ref_m == 0:
                ref_m, ref_y = 12, ref_y - 1
        else:
            ref_m -= 2
            if ref_m <= 0:
                ref_m, ref_y = ref_m + 12, ref_y - 1

    # 3-month avg: the 3 months before ref_month
    avg_months = []
    ay, am = ref_y, ref_m
    for _ in range(3):
        am -= 1
        if am == 0:
            am, ay = 12, ay - 1
        avg_months.append((ay, am))
    avg_months.reverse()  # chronological order

    book = _requested_book(request.args)
    account_id = request.args.get("account_id")
    exclude_one_off = request.args.get("exclude_one_off") == "true"

    extra_filters = ""
    extra_params = []
    # The account filter alone: what narrows the transfers waiting for review.
    account_filter = ""
    account_params = []
    # No merchant is hidden from a sum: hiding changes what a list shows,
    # never the figure (ruling 6).
    if exclude_one_off:
        # Exclude both transaction-level and service-level one-offs
        extra_filters += " AND t.is_one_off = 0 AND (svc.is_one_off IS NULL OR svc.is_one_off = 0)"
    if account_id:
        try:
            account_params.append(int(account_id))
            account_filter = " AND s.account_id = ?"
            extra_filters += account_filter
            extra_params += account_params
        except (ValueError, TypeError):
            pass

    # One total per declared book, keyed by the book's name in lower case.
    # The Household book's is household spending: on accounts the household
    # owns only (ruling 6). A row with no book on a company's own account is
    # not the household's money.
    books = [(name, name.lower()) for name in book_type.BOOK_NAMES]
    book_sums = "".join(
        f"SUM(CASE WHEN {book_expr('t')} = ?"
        + (" AND a.owner = ?" if name == book_type.DEFAULT_BOOK else "")
        + " THEN amount_minor ELSE 0 END), "
        for name, _ in books
    )
    # A company's card: its costs paid from the household's own accounts. What
    # the company paid from an account it owns is not the household's money.
    companies = [(name, key) for name, key in books if name != book_type.DEFAULT_BOOK]
    paid_sums = "".join(
        f"SUM(CASE WHEN {book_expr('t')} = ? AND a.owner = ? THEN amount_minor ELSE 0 END), "
        for _ in companies
    )

    with get_db() as conn:
        def query_month(y: int, m: int) -> dict:
            """Query spend totals for a single month, in whole minor units."""
            start = f"{y:04d}-{m:02d}-01"
            if m == 12:
                end_d = date(y + 1, 1, 1) - timedelta(days=1)
            else:
                end_d = date(y, m + 1, 1) - timedelta(days=1)
            end = end_d.strftime("%Y-%m-%d")

            params = []
            for name, _ in books:
                params += [name, account_kind.HOUSEHOLD] if name == book_type.DEFAULT_BOOK else [name]
            for name, _ in companies:
                params += [name, account_kind.HOUSEHOLD]
            params += [book_type.DEFAULT_BOOK, account_kind.HOUSEHOLD, start, end] + extra_params
            row = conn.execute(f"""
                SELECT
                    {book_sums}
                    {paid_sums}
                    SUM(amount_minor),
                    COUNT(CASE WHEN t.type_id IS NULL THEN 1 END),
                    COUNT(*),
                    COUNT(CASE WHEN {book_expr('t')} = ? AND a.owner = ? THEN 1 END)
                FROM transactions t
                LEFT JOIN services svc ON t.service_id = svc.id
                JOIN statements s ON t.statement_id = s.id
                LEFT JOIN accounts a ON s.account_id = a.id
                WHERE t.flow_type IN ('expense', 'refund')
                  AND {_SGD_ACCOUNT}
                  AND t.date >= ? AND t.date <= ?
                  {extra_filters}
            """, params).fetchone()
            n = len(books)
            result = {key: row[i] or 0 for i, (_, key) in enumerate(books)}
            for i, (_, key) in enumerate(companies):
                result[f"paid_{key}"] = row[n + i] or 0
            n += len(companies)
            result["total"] = row[n] or 0
            result["untyped"] = row[n + 1] or 0
            result["tx_count"] = row[n + 2] or 0
            result["household_rows"] = row[n + 3] or 0
            return result

        # Query reference month
        ref_data = query_month(ref_y, ref_m)

        # Query 3 prior months for rolling average
        avg_data = [query_month(y, m) for y, m in avg_months]
        n = len([d for d in avg_data if d["tx_count"] > 0]) or 1  # only months with data
        averages = {
            key: money.mean_minor(sum(d[key] for d in avg_data), n)
            for key in ["total"] + [key for _, key in books] + [f"paid_{key}" for _, key in companies]
        }

        # The transfers dated in the reference month that nobody has labelled:
        # what the household figure is waiting on.
        held_out = _waiting_sides(
            conn,
            " AND strftime('%Y-%m', t.date) = ?" + account_filter,
            [f"{ref_y:04d}-{ref_m:02d}"] + account_params,
        )
        # Everything waiting, whenever it is dated: the review list's own count.
        waiting = _waiting_sides(conn)
        loan = loan_interest.month_figures(conn, f"{ref_y:04d}-{ref_m:02d}")

    # Pick which spend to feature based on filter
    featured = book.lower() if book else "total"
    spend = money.from_minor(ref_data[featured])
    avg_spend = money.from_minor(averages[featured])

    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    ref_label = f"{month_names[ref_m]} {ref_y}"

    payload = {
        "ref_month": f"{ref_y:04d}-{ref_m:02d}",
        "ref_label": ref_label,
        "spend": spend,
        "untyped": ref_data["untyped"],
        "tx_count": ref_data["tx_count"],
        "household_rows": ref_data["household_rows"],
        "held_out_count": held_out["count"],
        "held_out_total": money.from_minor(held_out["net_minor"]),
        **_sides_payload("held_out", held_out),
        "waiting": waiting["count"],
        "waiting_total": money.from_minor(waiting["net_minor"]),
        **_sides_payload("waiting", waiting),
        "avg_spend": avg_spend,
        "avg_months": n,
        "loan_instalments": money.from_minor(loan["instalments_minor"]),
        "loan_interest": money.from_minor(loan["interest_minor"]),
        "loan_principal": money.from_minor(loan["principal_minor"]),
        "cash_out": money.from_minor(ref_data["household"] + loan["principal_minor"]),
    }
    for name, key in books:
        shown = key if name == book_type.DEFAULT_BOOK else f"paid_{key}"
        payload[key] = money.from_minor(ref_data[shown])
        payload[f"avg_{key}"] = money.from_minor(averages[shown])
    return jsonify(payload)


def _type_group_expr(args) -> str:
    """SQL naming the type a chart groups a row under. group_parent=true (the
    default) rolls a sub-type up into its parent."""
    if args.get("group_parent", "true") == "true":
        return "COALESCE(tp.name, ty.name)"
    return "ty.name"


@app.route("/api/dashboard/monthly")
def api_dashboard_monthly():
    """Spending by type over time for stacked bar chart.

    Query params: start, end, book, exclude_one_off, granularity, group_parent
    granularity: 'monthly' (default), 'weekly', 'quarterly'
    """
    filters, params = _build_filters(request.args, hide=False)
    granularity = request.args.get("granularity", "monthly")

    # Choose time bucket SQL expression
    if granularity == "weekly":
        # ISO week: "2025-W42"
        time_bucket = "strftime('%Y', t.date) || '-W' || printf('%02d', (strftime('%j', t.date) - 1) / 7 + 1)"
    elif granularity == "quarterly":
        time_bucket = "strftime('%Y', t.date) || '-Q' || ((CAST(strftime('%m', t.date) AS INTEGER) - 1) / 3 + 1)"
    else:
        time_bucket = "strftime('%Y-%m', t.date)"

    type_expr = _type_group_expr(request.args)

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT
                {time_bucket} as period,
                {type_expr} as type,
                SUM(t.amount_minor) as total
            FROM transactions t
            {_TYPE_JOINS.format(owner="t")}
            LEFT JOIN services svc ON t.service_id = svc.id
            JOIN statements s ON t.statement_id = s.id
            WHERE t.flow_type IN ('expense', 'refund') AND {_SGD_ACCOUNT} {filters}
            GROUP BY period, type
            ORDER BY period, total DESC
        """, params).fetchall()

    # Structure: {period: {type: total, ...}, ...}, added up in minor units
    minor = {}
    for r in rows:
        period = r["period"]
        if period not in minor:
            minor[period] = {}
        name = r["type"] or NO_TYPE_LABEL
        minor[period][name] = minor[period].get(name, 0) + r["total"]

    return jsonify({
        period: {name: money.from_minor(total) for name, total in by_type.items()}
        for period, by_type in minor.items()
    })


@app.route("/api/dashboard/types")
def api_dashboard_types():
    """Type totals for donut chart. One type serves every book.

    Query params: start, end, book, exclude_one_off, group_parent
    """
    filters, params = _build_filters(request.args, hide=False)
    type_expr = _type_group_expr(request.args)

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT
                {type_expr} as type,
                SUM(t.amount_minor) as total,
                COUNT(*) as count
            FROM transactions t
            {_TYPE_JOINS.format(owner="t")}
            LEFT JOIN services svc ON t.service_id = svc.id
            JOIN statements s ON t.statement_id = s.id
            WHERE t.flow_type IN ('expense', 'refund') AND {_SGD_ACCOUNT} {filters}
            GROUP BY type
            ORDER BY total DESC
        """, params).fetchall()

    return jsonify([{
        "type": r["type"] or NO_TYPE_LABEL,
        "total": money.from_minor(r["total"]),
        "count": r["count"],
    } for r in rows])


# ---------------------------------------------------------------------------
# Transactions API
# ---------------------------------------------------------------------------

@app.route("/api/transactions")
def api_transactions():
    """Paginated transaction list with filters.

    Query params: start, end, book, exclude_one_off, types, account_id, month,
                  page, per_page, search, flow

    flow=review is the review list: the transfers waiting for a label. It
    hides no merchant, so it lists every row the waiting count counts.
    """
    # A row asked for by id is shown whatever its merchant's hiding says.
    hide = request.args.get("flow") != flow.REVIEW and request.args.get("tx_id") is None
    filters, params = _build_filters(request.args, hide=hide)

    flow_filter = request.args.get("flow")
    if flow_filter:
        try:
            flow.checked_flow(flow_filter)
        except flow.UnknownFlow as e:
            return jsonify({"error": str(e)}), 400
        filters += " AND COALESCE(t.flow_type, 'expense') = ?"
        params.append(flow_filter)

    # tx_id: one row, as a row's own sheet reads it.
    tx_id = request.args.get("tx_id", type=int)
    if tx_id is not None:
        filters += " AND t.id = ?"
        params.append(tx_id)

    # look=mixed: rows of a mixed merchant (looked at each time) that nobody
    # has set by hand yet: the queue's "is this type right?" items. Only a
    # typed spending or refund row: an untyped one is already the queue's
    # "no type" item and a transfer waiting for review its "This was…" item,
    # so the same row is never listed twice.
    if request.args.get("look") == "mixed":
        filters += (
            " AND COALESCE(svc.review_each_time, 0) = 1 AND COALESCE(t.cat_source, 'auto') != 'manual'"
            " AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund') AND t.type_id IS NOT NULL"
        )

    # Type filter (from chart selection or multi-select dropdown): type names,
    # a parent taking its sub-types with it. __untyped__ is the list of rows
    # with no type, which holds spending and refund rows only: a transfer, a
    # payment or income carries no type and is not waiting for one.
    types_str = request.args.get("types")
    if types_str:
        type_list = [t.strip() for t in types_str.split(",") if t.strip()]
        conds = []
        if UNTYPED_FILTER in type_list:
            type_list.remove(UNTYPED_FILTER)
            conds.append(
                "(t.type_id IS NULL AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund'))"
            )
        if type_list:
            placeholders = ",".join("?" * len(type_list))
            conds.append(
                f"(ty.name IN ({placeholders}) OR COALESCE(tp.name, ty.name) IN ({placeholders}))"
            )
            params.extend(type_list * 2)
        if conds:
            filters += f" AND ({' OR '.join(conds)})"

    # Chart-driven date narrowing (adds to dashboard date filter)
    chart_start = request.args.get("chart_start")
    if chart_start:
        filters += " AND t.date >= ?"
        params.append(chart_start)
    chart_end = request.args.get("chart_end")
    if chart_end:
        filters += " AND t.date <= ?"
        params.append(chart_end)

    if request.args.get("expense_only") == "true":
        filters += " AND t.flow_type IN ('expense', 'refund')"

    month = request.args.get("month")
    if month:
        filters += " AND strftime('%Y-%m', t.date) = ?"
        params.append(month)

    search = request.args.get("search")
    if search:
        filters += " AND (t.description LIKE ? OR svc.name LIKE ? OR ty.name LIKE ? OR tp.name LIKE ?)"
        search_param = f"%{search}%"
        params.extend([search_param] * 4)

    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    offset = (page - 1) * per_page

    # Sort column — whitelist valid columns to prevent SQL injection
    sort_col = request.args.get("sort", "date")
    sort_dir = request.args.get("sort_dir", "desc").upper()
    valid_sorts = {
        "date": "t.date",
        "description": "t.description",
        "type": "ty.name",
        "service": "svc.name",
        "account": "a.name",
        "amount": "t.amount_minor",
    }
    order_col = valid_sorts.get(sort_col, "t.date")
    if sort_dir not in ("ASC", "DESC"):
        sort_dir = "DESC"

    type_joins = _TYPE_JOINS.format(owner="t")
    with get_db() as conn:
        # Count total (type joins needed for the type filter and the search)
        count_row = conn.execute(f"""
            SELECT COUNT(*) as cnt
            FROM transactions t
            {type_joins}
            LEFT JOIN services svc ON t.service_id = svc.id
            JOIN statements s ON t.statement_id = s.id
            WHERE 1=1 {filters}
        """, params).fetchone()

        # Fetch page — include the parent type for "Parent > Sub" display
        rows = conn.execute(
            f"""
            SELECT
                t.id, t.date, t.description, t.amount_minor,
                t.amount_foreign, t.currency_foreign,
                {book_expr("t")} as book,
                t.type_id, ty.name as type, tp.name as parent_type,
                t.cat_source,
                t.is_one_off, COALESCE(t.flow_type, 'expense') as flow_type, t.flow_type_manual,
                t.notes,
                t.other_side_id, oa.name as other_side_name,
                a.name as account_name, s.account_id,
                COALESCE(a.currency, 'SGD') as currency,
                t.service_id,
                svc.name as service_name,
                COALESCE(svc.review_each_time, 0) as review_each_time
            FROM transactions t
            {type_joins}
            LEFT JOIN services svc ON t.service_id = svc.id
            JOIN statements s ON t.statement_id = s.id
            JOIN accounts a ON s.account_id = a.id
            LEFT JOIN accounts oa ON t.other_side_id = oa.id
            WHERE 1=1 {filters}
            ORDER BY {order_col} {sort_dir}, t.date DESC
            LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()

    txns = []
    for r in rows:
        tx = dict(r)
        # The amount in the account's own currency, which `currency` names:
        # the key is the old one, and holds rupees for a rupee account.
        tx["amount_sgd"] = money.from_minor(tx.pop("amount_minor"), tx["currency"])
        tx["display_type"] = format_type_display(r["parent_type"], r["type"])
        if tx.get("account_name"):
            tx["account_name"] = mask_card_number(tx["account_name"])
        if tx.get("other_side_name"):
            tx["other_side_name"] = mask_card_number(tx["other_side_name"])
        txns.append(tx)

    return jsonify({
        "transactions": txns,
        "total": count_row["cnt"],
        "page": page,
        "per_page": per_page,
        "pages": (count_row["cnt"] + per_page - 1) // per_page,
    })


@app.route("/api/transactions/<int:tx_id>", methods=["PUT"])
def api_update_transaction(tx_id: int):
    """Update a transaction's notes, book, type, or one-off flag. A row may
    override both the book and the type its merchant gives it."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    sets, values = _build_update_sets(data, ["notes", "book", "type_id", "is_one_off"])

    # If the book, the type or the service is being changed, mark as manual
    if "book" in data or "type_id" in data or "service_id" in data:
        sets.append("cat_source = 'manual'")

    if not sets:
        return jsonify({"error": "No valid fields to update"}), 400

    with get_db() as conn:
        try:
            _checked_labels(conn, data)
        except UnknownLabel as e:
            return jsonify({"error": str(e)}), 400
        values.append(tx_id)
        conn.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()
    return jsonify({"ok": True, "id": tx_id})


@app.route("/api/transactions/resolve", methods=["POST"])
def api_resolve_transaction():
    """Resolve a transaction with no type: find-or-create service, create rule, update tx.

    Accepts:
        tx_id: transaction ID to resolve
        service_name: existing or new service name
        service_id: existing service ID (optional — if provided, service_name ignored for lookup)
        book, type_id: the label. Left out, an existing service's own are
            used; for a new service the type is required and the book is the
            one the type proposes (Software & AI tools proposes none: say it).
        pattern: merchant rule pattern (auto-suggested from description)
        match_type: 'contains' (default) or 'startswith'
        apply_scope: where the label applies —
            'transaction'     this row only (a row may override both)
            'rule'            a rule override for the pattern
            'service_default' the merchant's default (the default)

    Flow:
        1. Find existing service by service_id or name, or create new one
        2. Create merchant rule linking pattern → service (→ its book and type)
        3. Update the transaction with service_id + book + type_id
        4. Backfill any other NULL-service transactions matching the new rule
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    tx_id = data.get("tx_id")
    service_name = (data.get("service_name") or "").strip()
    service_id = data.get("service_id")
    pattern = (data.get("pattern") or "").strip()
    match_type = data.get("match_type", "contains")
    apply_scope = data.get("apply_scope") or "service_default"

    if not tx_id:
        return jsonify({"error": "tx_id required"}), 400
    if not service_name and not service_id:
        return jsonify({"error": "service_name or service_id required"}), 400
    if apply_scope == "rule" and not pattern:
        return jsonify({"error": "pattern required for rule override"}), 400
    pattern_error = _rule_pattern_error(pattern)
    if pattern and apply_scope in {"rule", "service_default"} and pattern_error:
        return jsonify({"error": pattern_error}), 400
    flow_type = data.get("flow_type")
    if flow_type is not None:
        try:
            flow.checked_flow(flow_type)
        except flow.UnknownFlow as e:
            return jsonify({"error": str(e)}), 400

    with get_db() as conn:
        try:
            book = _checked_book(data.get("book"))
            type_id = _checked_type_id(conn, data.get("type_id"))
        except UnknownLabel as e:
            return jsonify({"error": str(e)}), 400

        try:
            # Step 1: Resolve service — find existing or create new
            if service_id:
                svc = conn.execute(
                    "SELECT id, book, type_id FROM services WHERE id = ?", (service_id,)
                ).fetchone()
                if not svc:
                    return jsonify({"error": f"Service ID {service_id} not found"}), 404
            else:
                # Look up by name (case-insensitive)
                svc = conn.execute(
                    "SELECT id, book, type_id FROM services WHERE UPPER(name) = ?",
                    (service_name.upper(),),
                ).fetchone()

            if svc:
                service_id = svc["id"]
                # Whatever the request leaves out is the merchant's own.
                if type_id is None:
                    type_id = svc["type_id"]
                if book is None:
                    book = svc["book"]
                book = _book_or_proposed(conn, book, type_id)
                # Service-default resolution can update the merchant's default.
                if apply_scope == "service_default" and (
                    (book, type_id) != (svc["book"], svc["type_id"])
                ):
                    conn.execute(
                        "UPDATE services SET book = ?, type_id = ? WHERE id = ?",
                        (book, type_id, service_id),
                    )
                merchant_book = book if apply_scope == "service_default" else svc["book"]
            else:
                # Create new service — the type is required
                if not type_id:
                    return jsonify({"error": "type_id required for new service"}), 400
                book = _book_or_proposed(conn, book, type_id)
                conn.execute(
                    "INSERT INTO services (name, book, type_id) VALUES (?, ?, ?)",
                    (service_name, book, type_id),
                )
                service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                merchant_book = book

            # Step 2: Create merchant rule if pattern provided (optional for PayNow/transfers)
            rule_id = None
            backfilled = 0
            if pattern and apply_scope in {"rule", "service_default"}:
                rule_exists = conn.execute(
                    "SELECT id FROM merchant_rules WHERE UPPER(pattern) = ?",
                    (pattern.upper(),),
                ).fetchone()
                # A rule override carries the type, and the book only where it
                # is not the merchant's.
                override_type_id = type_id if apply_scope == "rule" else None
                override_book = (
                    book if apply_scope == "rule" and book != merchant_book else None
                )
                if not rule_exists:
                    conn.execute(
                        "INSERT INTO merchant_rules (pattern, service_id, book_override, "
                        "type_override_id, match_type, confidence) "
                        "VALUES (?, ?, ?, ?, ?, 'confirmed')",
                        (pattern, service_id, override_book, override_type_id, match_type),
                    )
                    rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                else:
                    rule_id = rule_exists["id"]
                    conn.execute(
                        "UPDATE merchant_rules SET service_id = ?, book_override = ?, "
                        "type_override_id = ? WHERE id = ?",
                        (service_id, override_book, override_type_id, rule_id),
                    )

                # Backfill other transactions matching this pattern with NULL service
                match_cond = _build_match_condition(match_type)
                cur = conn.execute(
                    f"UPDATE transactions SET service_id = ?, book = ?, type_id = ?, cat_source = ? "
                    f"WHERE service_id IS NULL AND {match_cond}",
                    (
                        service_id,
                        book,
                        type_id,
                        "rule_override" if apply_scope == "rule" else "service_default",
                        pattern.upper(),
                    ),
                )
                backfilled = cur.rowcount

            # Step 3: Update the target transaction with explicit provenance.
            # flow_type override is independent of the label (ADR v2):
            #   - if caller supplies flow_type, set it + mark flow_type_manual=1
            #   - otherwise leave a hand-set flow alone and re-derive the rest
            tx_cat_source = {
                "transaction": "manual",
                "rule": "rule_override",
                "service_default": "service_default",
            }.get(apply_scope, "manual")
            tx_row = conn.execute(
                "SELECT description, amount_minor, flow_type_manual FROM transactions WHERE id = ?",
                (tx_id,),
            ).fetchone()
            label = (book, type_id, service_id, tx_cat_source)
            if flow_type is not None:
                conn.execute(
                    "UPDATE transactions SET book = ?, type_id = ?, service_id = ?, cat_source = ?, "
                    "flow_type = ?, flow_type_manual = 1 WHERE id = ?",
                    (*label, flow_type, tx_id),
                )
            elif tx_row and not tx_row["flow_type_manual"]:
                flow_type, other_side = _classify_flow_for_tx(
                    conn, tx_row["description"], tx_row["amount_minor"],
                    tx_id=tx_id, service_id=service_id, labelled=True,
                )
                conn.execute(
                    "UPDATE transactions SET book = ?, type_id = ?, service_id = ?, cat_source = ?, "
                    "flow_type = ?, other_side_id = ? WHERE id = ?",
                    (*label, flow_type, other_side, tx_id),
                )
            else:
                conn.execute(
                    "UPDATE transactions SET book = ?, type_id = ?, service_id = ?, cat_source = ? "
                    "WHERE id = ?",
                    (*label, tx_id),
                )

            # Afterwards, for a merchant the model was asked about: the type
            # the operator chose and whether the suggestion was on screen.
            suggest.record_choice(
                conn, tx_id, type_id, data.get("suggestion_visible") is True,
            )

            conn.commit()
            invalidate_rules_cache()
            return jsonify({
                "success": True,
                "service_id": service_id,
                "rule_id": rule_id,
                "book": book,
                "type_id": type_id,
                "backfilled": backfilled,
            })
        except BookNeeded as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            conn.rollback()
            app.logger.warning("Failed to resolve transaction: %s", e)
            return jsonify({"error": "Failed to resolve transaction"}), 400


def _build_filters(args, hide: bool = True) -> tuple[str, list]:
    """Build SQL WHERE clause fragments from common query params.

    The Household view is the household's spending: the Household book on
    accounts the household owns (ruling 6). `hide` leaves out the merchants
    hidden from expense views; it is for what a list shows, never for a sum:
    hiding a merchant changes what is shown, not the figure."""
    filters = ""
    params = []

    start = args.get("start")
    if start:
        filters += " AND t.date >= ?"
        params.append(start)

    end = args.get("end")
    if end:
        filters += " AND t.date <= ?"
        params.append(end)

    book = _requested_book(args)
    if book:
        filters += f" AND {book_expr('t')} = ?"
        params.append(book)
        if book == book_type.DEFAULT_BOOK:
            filters += f" AND {_HOUSEHOLD_ACCOUNT}"
            params.append(account_kind.HOUSEHOLD)

    exclude_one_off = args.get("exclude_one_off")
    if exclude_one_off == "true":
        # Exclude both transaction-level and service-level one-offs
        filters += " AND t.is_one_off = 0 AND (svc.is_one_off IS NULL OR svc.is_one_off = 0)"

    if hide:
        filters += f" AND {_expense_visibility_filter('svc')}"

    account_id = args.get("account_id")
    if account_id:
        try:
            filters += " AND s.account_id = ?"
            params.append(int(account_id))
        except (ValueError, TypeError):
            pass

    return filters, params


# ---------------------------------------------------------------------------
# Type suggestion (suggest.py)
# ---------------------------------------------------------------------------
# Two steps, both started by the operator from the list of rows with no type:
# prepare writes the cleaned merchant strings to a local file and sends
# nothing; send asks the outside model about the lines still in that file.
# Neither runs inside an upload, and both refuse when there is no key.

def _suggestion_strings_path():
    """The file of strings waiting to be read, beside the database."""
    return db.DB_PATH.parent / suggest.STRINGS_FILE


def _suggestions_off():
    return jsonify({
        "error": f"Type suggestion is off: {suggest.KEY_ENV} is not set. "
                 "Start fin through the launcher that sets it."
    }), 409


@app.route("/api/suggestions/status")
def api_suggestions_status():
    """Whether the feature is on. It is on only when the key is present."""
    return jsonify({"enabled": suggest.key() is not None, "key_variable": suggest.KEY_ENV})


@app.route("/api/suggestions/prepare", methods=["POST"])
def api_suggestions_prepare():
    """Step one: write the strings that would be sent to a local file for the
    operator to read. Nothing leaves the machine."""
    if suggest.key() is None:
        return _suggestions_off()
    with get_db() as conn:
        merchants = suggest.unanswered(conn)
    path = _suggestion_strings_path()
    if merchants:
        path.write_text(suggest.FILE_HEADER + "\n".join(merchants) + "\n", encoding="utf-8")
    return jsonify({"count": len(merchants), "merchants": merchants, "file": str(path)})


@app.route("/api/suggestions/send", methods=["POST"])
def api_suggestions_send():
    """Step two: one request per line the operator left in the file, each
    answer stored in full. The first failed call stops the batch."""
    api_key = suggest.key()
    if api_key is None:
        return _suggestions_off()
    path = _suggestion_strings_path()
    if not path.is_file():
        return jsonify({
            "error": "Nothing has been prepared. Prepare the list and read it before sending."
        }), 409
    if suggest.approval_expired(path.stat().st_mtime, suggest.clock()):
        return jsonify({
            "error": "The prepared list is more than 24 hours old, so it is not sent. "
                     "Prepare the list again and read it before sending."
        }), 409
    left_in_file = {
        line.strip() for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    sent = 0
    stopped = None
    with get_db() as conn:
        # Only strings fin itself would have written: a line added by hand,
        # or one already answered, is not sent.
        to_send = [m for m in suggest.unanswered(conn) if m in left_in_file]
        for merchant in to_send:
            try:
                record = suggest.ask(merchant, api_key)
            except Exception as exc:
                stopped = suggest.describe_failure(exc)
                app.logger.warning("Type suggestion stopped: %s", stopped)
                break
            suggest.store(conn, record)
            conn.commit()
            if record["returned_model_id"] != suggest.RETURNED_MODEL_ID:
                stopped = (
                    f"a different model answered ({record['returned_model_id']}, expected "
                    f"{suggest.RETURNED_MODEL_ID}); its answer is kept and not shown"
                )
                break
            sent += 1

    if stopped is None:
        # Every line was dealt with. The file is fin's own, written by prepare
        # under this fixed name; the next batch starts from a fresh one.
        path.unlink()
    return jsonify({"sent": sent, "stopped": stopped, "remaining": len(to_send) - sent})


@app.route("/api/suggestions/answers")
def api_suggestions_answers():
    """Every stored answer in full, with what the operator chose afterwards."""
    with get_db() as conn:
        type_names = book_type.spending_type_names(conn)
        rows = conn.execute("SELECT * FROM suggestion_answers ORDER BY merchant, id").fetchall()
    return jsonify([{
        "merchant": r["merchant"],
        "model_id": r["model_id"],
        "returned_model_id": r["returned_model_id"],
        "cleaning_version": r["cleaning_version"],
        "question_version": r["question_version"],
        "pick": r["pick"],
        "probabilities": json.loads(r["probabilities"]),
        "confidence": r["confidence"],
        "input_tokens": r["input_tokens"],
        "cost_usd": r["cost_usd"],
        "latency_ms": r["latency_ms"],
        "asked_at": r["asked_at"],
        "chosen_type_id": r["chosen_type_id"],
        "chosen_type": type_names.get(r["chosen_type_id"]),
        "suggestion_visible": (
            None if r["suggestion_visible"] is None else bool(r["suggestion_visible"])
        ),
        "chosen_at": r["chosen_at"],
    } for r in rows])


@app.route("/api/transactions/<int:tx_id>/suggestion")
def api_transaction_suggestion(tx_id: int):
    """What the resolve dialog is given for a row: a type to pre-fill, three
    to offer, or nothing. Read from the stored answers; it never asks."""
    blank = {"route": "blank", "types": [], "merchant": None}
    with get_db() as conn:
        tx = conn.execute(
            "SELECT service_id FROM transactions WHERE id = ?", (tx_id,)
        ).fetchone()
        if not tx:
            return jsonify({"error": "Transaction not found"}), 404
        if suggest.key() is None or tx["service_id"] is not None:
            return jsonify(blank)
        merchant = suggest.row_merchant(conn, tx_id)
        if merchant is None:
            return jsonify(blank)
        answer = suggest.stored_answer(conn, merchant)
        type_ids = book_type.spending_type_ids(conn)
    if answer is None:
        return jsonify({**blank, "merchant": merchant})
    where, offered = suggest.route(answer["pick"], json.loads(answer["probabilities"]))
    return jsonify({
        "route": where,
        "merchant": merchant,
        "types": [
            {"type_id": type_ids[name], "name": name, "probability": probability}
            for name, probability in offered
        ],
    })


# ---------------------------------------------------------------------------
# Import API
# ---------------------------------------------------------------------------

def _upload_name(sent: str, index: int, folder: str) -> str:
    """A name to save an uploaded file under inside `folder`: the name the
    browser sent made safe (no folder part, no "..", no absolute path), with
    a made-up one, keeping a safe extension, when nothing safe is left, and a
    numbered one when the batch already holds that name. The parser registry
    chooses by extension, so the extension is kept."""
    # The stem and the extension are made safe apart: a stem with no ASCII
    # letter in it ("выписка") must not take the extension with it.
    sent_stem, sent_ext = os.path.splitext(os.path.basename((sent or "").replace("\\", "/")))
    ext = secure_filename(sent_ext.lstrip("."))
    stem = secure_filename(sent_stem) or f"upload_{index}"
    name = stem + (f".{ext}" if ext else "")
    stem, ext = os.path.splitext(name)
    candidate, n = name, 1
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = f"{stem}_{n}{ext}"
        n += 1
    return candidate


@app.route("/api/import/upload", methods=["POST"])
def api_import_upload():
    """Accept statement files, parse, label with book and type, return preview.

    Accepts multipart form data with one or more files.
    Returns grouped preview by account with each row's label status.

    A statement whose source states its balances is checked: opening less the
    sum of its rows must equal closing, exactly. One that ties puts its tie
    line (opening, what the rows add up to, closing) in its account group's
    `statements`, and each of its rows names it by position in `statement`.
    One that does not is refused: none of its rows is in the preview, and its
    entry in `errors` carries the same line under `tie`, with the difference.
    A source with no balance is not checked. No row is skipped by default.
    Refused (403) from the chat's side (the upload command) while the
    "Claude may write" switch is off; the operator's own import still works.
    """
    refused = _chat_import_refused()
    if refused is not None:
        return refused
    if "files" not in request.files:
        return jsonify({"error": "No files uploaded"}), 400

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    all_groups = {}  # account_name -> list of transaction dicts
    errors = []
    filenames = []

    # Save uploaded files to temp dir for parsing
    with get_db() as conn, tempfile.TemporaryDirectory() as tmpdir:
        saved_paths = []
        for f in files:
            if not f.filename:
                continue
            # The name the browser sent is shown, never used as a path: the
            # file is saved inside the temporary folder under a safe name.
            save_path = os.path.join(tmpdir, _upload_name(f.filename, len(saved_paths), tmpdir))
            f.save(save_path)
            saved_paths.append((save_path, f.filename))
            filenames.append(f.filename)

        # Detect and parse each file via parser registry
        from parsers import auto_detect_and_parse, handle_vantage_split
        parsed_statements = []
        for save_path, filename in saved_paths:
            try:
                stmts = auto_detect_and_parse(save_path)
            except Exception as e:
                errors.append({"file": filename, "error": str(e)})
                continue
            # The one check of rows against the statement's own balances. A
            # statement that does not tie goes no further.
            for stmt in stmts:
                try:
                    # A statement in a currency its account is not kept in
                    # goes no further either.
                    for name in stmt.accounts[:1]:
                        _account_currency(conn, name, stmt.currency)
                    tie.check(stmt)
                except tie.DoesNotTie as e:
                    line = _tie_line(conn, stmt, e.figures)
                    errors.append({"file": filename, "error": str(e), "tie": line})
                    # Kept so the queue and the account page say so until a
                    # file that ties is imported (screens.py). No file name.
                    _record_refused(conn, stmt, line)
                except ValueError as e:
                    errors.append({"file": filename, "error": str(e)})
                else:
                    parsed_statements.append(stmt)

        # Handle Vantage MK/BS split if both exports present
        parsed_statements = handle_vantage_split(parsed_statements)

        # Post-parse: classify flow_type for every transaction (ADR v2).
        # Single shared classifier — parsers are fact-extractors only.
        flow_ctx = flow.build_context(conn)
        from ingest import find_account
        account_facts = {}  # account name -> the kind and owner the classifier reads

        # Group transactions by account and label each with book and type
        type_names = book_type.spending_type_names(conn)
        svcs = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM services").fetchall()}
        statement_lines = {}  # account name -> tie lines of its statements that carry balances
        currencies = {}  # account name -> the currency its rows are in

        for stmt in parsed_statements:
            # A statement with balances is one account's: its tie line goes on
            # that account's group, which exists even when it has no rows.
            statement_ref = None
            statement_account = None
            ties = tie.check(stmt)
            if ties is not None:
                statement_account = stmt.accounts[0] if stmt.accounts else "Unknown"
                lines = statement_lines.setdefault(statement_account, [])
                statement_ref = len(lines)
                lines.append(_tie_line(conn, stmt, ties))
                all_groups.setdefault(statement_account, [])
                currencies.setdefault(
                    statement_account, _account_currency(conn, statement_account, stmt.currency)
                )

            for tx in stmt.transactions:
                account = tx.card_info or stmt.accounts[0] if stmt.accounts else "Unknown"
                group_account = account
                if statement_account is not None:
                    # A statement's rows are checked together, in its one
                    # group. A card split by cardholder (card_balance.py)
                    # keeps each row on its cardholder's account inside it.
                    group_account = statement_account
                    if card_balance.part_of_names().get(tx.card_info) != statement_account:
                        account = statement_account

                # Merchant rules first; for bank statements, then the PayNow wording
                found = _label_for(conn, tx.description, tx.amount_minor)
                type_id = found["type_id"]
                svc_id = found["service_id"]

                # The account the row will be on: the one the name finds, or
                # the one confirm will create for it.
                if account not in account_facts:
                    held = conn.execute(
                        "SELECT type, owner FROM accounts WHERE id = ?",
                        (find_account(conn, account),),
                    ).fetchone()
                    account_facts[account] = {
                        "account_kind": held["type"] if held else _kind_from_account_name(account),
                        "account_owner": held["owner"] if held else account_kind.DEFAULT_OWNER,
                    }
                if account not in currencies:
                    try:
                        currencies[account] = _account_currency(conn, account, stmt.currency)
                    except CurrencyRefused:
                        currencies[account] = stmt.currency

                # Classify flow_type post-parse, from the row's own wording,
                # the account it is on and what the merchant rules made of it
                tx.flow_type, other_side_id = flow.classify_row(
                    {
                        "description": tx.description,
                        "amount_minor": tx.amount_minor,
                        "service_id": svc_id,
                        "labelled": bool(type_id or svc_id),
                        **account_facts[account],
                    },
                    flow_ctx,
                )

                entry = {
                    "date": tx.date,
                    "description": tx.description,
                    "amount_sgd": money.from_minor(tx.amount_minor, currencies[account]),
                    "amount_foreign": tx.amount_foreign,
                    "currency_foreign": tx.currency_foreign,
                    "book": found["book"],
                    "type_id": type_id,
                    "type_name": type_names.get(type_id) if type_id else None,
                    "service_id": svc_id,
                    "service_name": svcs.get(svc_id) if svc_id else None,
                    "cat_source": found["cat_source"],
                    # A mixed merchant: recognised, given its default type,
                    # and flagged for a look each time.
                    "review_each_time": found["review_each_time"],
                    "flow_type": tx.flow_type,
                    "other_side_id": other_side_id,
                    "account": account,
                    "status": "typed" if type_id else (
                        tx.flow_type
                        if tx.flow_type in ("transfer", "payment", flow.MOVEMENT, flow.REVIEW)
                        else "untyped"
                    ),
                    # Every row is imported: nothing is skipped by default.
                    "_skip": False,
                    # Which of its account's statements (by position in the
                    # group's `statements`) the row was checked against.
                    "statement": statement_ref,
                }

                all_groups.setdefault(group_account, []).append(entry)

    # Build response
    groups = []
    total = 0
    typed = 0
    untyped = 0
    skipped = 0
    to_review = 0

    for account_name, txns in all_groups.items():
        group_typed = sum(1 for t in txns if t["status"] == "typed")
        group_untyped = sum(1 for t in txns if t["status"] == "untyped")
        group_skip = sum(1 for t in txns if t["_skip"])

        groups.append({
            "account": mask_card_number(account_name),
            # The currency the account is kept in: the rows' amounts, under
            # their old key amount_sgd, and the balances are in it.
            "currency": currencies.get(account_name, "SGD"),
            "transactions": txns,
            "typed": group_typed,
            "untyped": group_untyped,
            "skipped": group_skip,
            "total": len(txns),
            "statements": statement_lines.get(account_name, []),
            "tie": (
                "ties"
                if account_name in statement_lines
                and all(t["statement"] is not None for t in txns)
                else "not_checked"
            ),
        })

        total += len(txns)
        typed += group_typed
        untyped += group_untyped
        skipped += group_skip
        to_review += sum(1 for t in txns if t["review_each_time"])

    # Save preview to batch_imports and fetch services list in one connection
    with get_db() as conn:
        conn.execute(
            "INSERT INTO batch_imports (filenames, accounts, status, total_lines, categorized_lines) VALUES (?, ?, 'preview', ?, ?)",
            (
                json.dumps(filenames),
                json.dumps(list(all_groups.keys())),
                total,
                typed,
            ),
        )
        conn.commit()
        import_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        services_rows = conn.execute(
            "SELECT s.id, s.name, s.book, s.type_id, s.review_each_time "
            "FROM services s ORDER BY s.name"
        ).fetchall()
        type_names = book_type.spending_type_names(conn)
    services_list = [
        {
            "id": r["id"],
            "name": r["name"],
            "book": r["book"],
            "type_id": r["type_id"],
            "type_name": type_names.get(r["type_id"], ""),
            "review_each_time": r["review_each_time"],
        }
        for r in services_rows
    ]

    return jsonify({
        "import_id": import_id,
        "groups": groups,
        "stats": {
            "total": total,
            "typed": typed,
            "untyped": untyped,
            "skipped": skipped,
            "review_each_time": to_review,
        },
        "errors": errors,
        "filenames": filenames,
        "services": services_list,
    })


@app.route("/api/import/confirm", methods=["POST"])
def api_import_confirm():
    """Commit previewed transactions to the database.

    Expects JSON body:
    {
        "import_id": int,
        "groups": [
            {
                "account": "account name",
                "transactions": [
                    {
                        "date": "YYYY-MM-DD",
                        "description": "...",
                        "amount_sgd": 123.45,
                        "amount_foreign": null,
                        "currency_foreign": null,
                        "book": "Household",
                        "type_id": 5,
                        "flow_type": "expense",
                        "other_side_id": null,
                        "_skip": false,
                        "statement": 0
                    }, ...
                ],
                "statements": [
                    {"opening_minor": 5210000, "closing_minor": 4120050,
                     "closing_date": "YYYY-MM-DD"}, ...
                ]
            }, ...
        ],
        "new_services": [
            {"name": "Merchant", "book": "Household", "type_id": 5, "description": "MERCHANT 0042"}, ...
        ],
        "new_rules": [
            {"pattern": "MERCHANT", "service_id": 7, "match_type": "contains"}, ...
        ]
    }

    A book, a type or a flow that is not in its vocabulary, or an other side
    that is no account, refuses the whole import before anything is written,
    and so does an amount that is missing or is not a whole number of cents.
    The amount arrives as the decimal the preview showed and is stored as
    whole minor units.

    A group's `statements` are the tie lines the upload gave it. Each is
    checked again over the rows that name it and are about to be written:
    opening less their sum must equal closing, exactly, so a row of such a
    statement cannot be left out. Its closing balance is then written as a
    statement anchor; the same balance already held changes nothing, and a
    different one for that account and date refuses the import (409).

    All or nothing: every write of one confirm is one transaction, and any
    failure rolls all of it back.
    """
    refused = _chat_import_refused()
    if refused is not None:
        return refused
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    import_id = data.get("import_id")
    groups = data.get("groups", [])
    new_rules = data.get("new_rules", [])
    new_services = data.get("new_services", [])

    with get_db() as conn:
        try:
            for labelled in [tx for g in groups for tx in g.get("transactions", [])] + new_services:
                _checked_labels(conn, labelled)
            for tx in [tx for g in groups for tx in g.get("transactions", [])]:
                if tx.get("flow_type") is not None:
                    flow.checked_flow(tx["flow_type"])
                _checked_other_side(conn, tx.get("other_side_id"), tx.get("flow_type"))
            # The currency each group's account is kept in, by the group
            # itself: the account's own, or for a new account the one the
            # upload stated.
            currency_of = {
                id(g): _account_currency(conn, g.get("account") or "", g.get("currency"))
                for g in groups
            }
            # Each row's amount in whole minor units, keyed by the row itself.
            minor_of = {
                id(tx): _minor_from_request(tx.get("amount_sgd"), currency=currency_of[id(g)])
                for g in groups for tx in g.get("transactions", [])
            }
            # A new merchant's book is the one given, or the one its type
            # proposes; a type that proposes none has to be given one.
            for ns in new_services:
                _book_or_proposed(conn, ns.get("book") or None, ns.get("type_id") or None)
        except (UnknownLabel, BookNeeded, BadAmount, CurrencyRefused, flow.UnknownFlow) as e:
            return jsonify({"error": str(e)}), 400

    # The shared check, over the rows as they will be written.
    try:
        for g in groups:
            lines = g.get("statements") or []
            if not isinstance(lines, list) or not all(isinstance(line, dict) for line in lines):
                raise ValueError("statements must be a list of tie lines")
            rows_of = [[] for _ in lines]
            for tx in g.get("transactions", []):
                ref = tx.get("statement")
                if ref is None:
                    continue
                if isinstance(ref, bool) or not isinstance(ref, int) or not 0 <= ref < len(lines):
                    raise ValueError("a row names a statement its account does not have")
                if not tx.get("_skip", False):
                    rows_of[ref].append(minor_of[id(tx)])
            for line, amounts in zip(lines, rows_of):
                anchors.checked_date(line.get("closing_date"))
                tie.checked(line.get("opening_minor"), line.get("closing_minor"), amounts)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    total_saved = 0
    total_duplicates = 0
    accounts_created = []
    rules_skipped_generic = 0
    anchors_written = 0
    rows_refiled = 0

    with get_db() as conn:
        try:
            for group in groups:
                account_name = group["account"]
                txns = group["transactions"]
                statement_lines = group.get("statements") or []

                # Filter out skipped transactions
                active_txns = [t for t in txns if not t.get("_skip", False)]
                if not active_txns and not statement_lines:
                    continue

                # Ensure account exists
                from ingest import ensure_account, ensure_named_account, ensure_statement
                stmt_type = _kind_from_account_name(account_name)

                account_id = ensure_account(conn, account_name, stmt_type, currency_of[id(group)])
                accounts_created.append(account_name)

                # A row of a statement that states its balances is filed under
                # that statement as printed, dated by its closing day: which
                # statement a row printed on is what a card's balance and its
                # check go by (ruling 1), whatever the row's own date. A row
                # from a source with no balance goes on a record for its own
                # calendar month, so the coverage matrix reflects each month
                # (a multi-month CSV, e.g. Citi Oct-Dec, makes three).
                #
                # A card split by cardholder (card_balance.py) is one group:
                # a row naming a cardholder's account that is part of this
                # one's balance is filed on that account, under its own record
                # of the same printed statement. Any other row is this one's.
                parts = card_balance.part_of_names()

                def target_name(tx) -> str:
                    named = tx.get("account")
                    return named if parts.get(named) == account_name else account_name

                account_ids = {account_name: account_id}
                for name in sorted({target_name(tx) for tx in active_txns} - {account_name}):
                    account_ids[name] = ensure_named_account(
                        conn, name, _kind_from_account_name(name), currency_of[id(group)]
                    )
                printed_ids = {
                    name: [
                        ensure_statement(
                            conn, held_id, line["closing_date"], f"import_{import_id}_{name[:30]}", printed=True,
                        )[0]
                        for line in statement_lines
                    ]
                    for name, held_id in account_ids.items()
                }
                month_stmt_ids: dict[tuple[str, str], int] = {}
                for tx in active_txns:
                    if tx.get("statement") is not None:
                        continue
                    ym = tx["date"][:7] if tx.get("date") else datetime.now().strftime("%Y-%m")
                    name = target_name(tx)
                    if (name, ym) not in month_stmt_ids:
                        sid, _ = ensure_statement(
                            conn, account_ids[name], f"{ym}-01", f"import_{import_id}_{name[:30]}"
                        )
                        month_stmt_ids[(name, ym)] = sid

                def statement_of(tx) -> int:
                    name = target_name(tx)
                    if tx.get("statement") is not None:
                        return printed_ids[name][tx["statement"]]
                    ym = tx["date"][:7] if tx.get("date") else datetime.now().strftime("%Y-%m")
                    return month_stmt_ids[(name, ym)]

                # --- Deduplication ---
                # Group import transactions by (date, description, amount) to handle
                # genuine same-day repeats (e.g. two identical coffees).
                # For each group, count how many already exist in DB for this account.
                # Only insert (import_count - existing_count), minimum 0.
                from collections import Counter

                import_counts = Counter()
                import_by_key = {}  # key -> [list of tx dicts]
                for tx in active_txns:
                    key = (account_ids[target_name(tx)], tx["date"], tx["description"], minor_of[id(tx)])
                    import_counts[key] += 1
                    import_by_key.setdefault(key, []).append(tx)

                duplicates_skipped = 0
                for key, import_count in import_counts.items():
                    row_account_id, date_val, desc_val, amount_val = key

                    # Existing matches for this account, and whether each is
                    # already filed under a printed statement.
                    held_rows = conn.execute(
                        """SELECT t.id, s.printed FROM transactions t
                           JOIN statements s ON t.statement_id = s.id
                           WHERE s.account_id = ?
                             AND t.date = ? AND t.description = ? AND t.amount_minor = ?
                           ORDER BY t.id""",
                        (row_account_id, date_val, desc_val, amount_val),
                    ).fetchall()
                    existing = len(held_rows)

                    # Only insert the net-new ones
                    to_insert = max(0, import_count - existing)
                    skipped = import_count - to_insert
                    duplicates_skipped += skipped

                    # A row already held on a month record (imported before
                    # from a source with no balance, or before rows were filed
                    # by statement) that this statement printed is filed
                    # under it now.
                    on_month_records = [r["id"] for r in held_rows if not r["printed"]]
                    printed_again = [
                        tx for tx in import_by_key[key][to_insert:] if tx.get("statement") is not None
                    ]
                    for held_id, tx in zip(on_month_records, printed_again):
                        conn.execute(
                            "UPDATE transactions SET statement_id = ? WHERE id = ?",
                            (statement_of(tx), held_id),
                        )
                        rows_refiled += 1

                    # Link each transaction to its statement record
                    for tx in import_by_key[key][:to_insert]:
                        statement_id = statement_of(tx)
                        # A waiting row the operator gave a type or a merchant
                        # in the preview is labelled: it no longer waits.
                        tx_flow = tx.get("flow_type")
                        if tx_flow == flow.REVIEW and (tx.get("type_id") or tx.get("service_id")):
                            tx_flow = "income" if amount_val < 0 else "expense"
                        cur = conn.execute(
                            "INSERT INTO transactions "
                            "(statement_id, date, description, amount_minor, amount_foreign, "
                            "currency_foreign, book, type_id, service_id, "
                            "is_one_off, cat_source, flow_type, other_side_id) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                statement_id,
                                tx["date"],
                                tx["description"],
                                amount_val,
                                tx.get("amount_foreign"),
                                tx.get("currency_foreign"),
                                tx.get("book") or None,
                                tx.get("type_id") or None,
                                tx.get("service_id"),
                                1 if tx.get("is_one_off") else 0,
                                tx.get("cat_source"),
                                tx_flow,
                                tx.get("other_side_id"),
                            ),
                        )
                        if tx_flow is None:
                            # A row sent with no flow takes the classifier's,
                            # read with the account it is on: a row is never
                            # saved without one (a start does not fill it in).
                            labelled = bool(tx.get("type_id") or tx.get("service_id"))
                            conn.execute(
                                "UPDATE transactions SET flow_type = ?, other_side_id = ? WHERE id = ?",
                                (*_classify_flow_for_tx(
                                    conn, tx["description"], amount_val, tx_id=cur.lastrowid,
                                    service_id=tx.get("service_id"), labelled=labelled,
                                ), cur.lastrowid),
                            )
                        total_saved += 1

                total_duplicates += duplicates_skipped

                # Each statement that tied anchors the account at its closing
                # balance. The same balance again is no second anchor.
                for line in statement_lines:
                    try:
                        _, written = anchors.record(
                            conn, account_id, line["closing_date"], line["closing_minor"],
                            anchors.STATEMENT,
                            f"import {import_id}" if import_id is not None else None,
                        )
                    except anchors.AnchorConflict as e:
                        currency = conn.execute(
                            "SELECT currency FROM accounts WHERE id = ?", (account_id,)
                        ).fetchone()["currency"]
                        raise AnchorRefused(
                            f"{mask_card_number(account_name)} already has a balance of "
                            f"{anchors.format_amount(e.existing['amount'], currency)} for "
                            f"{e.existing['date']}; this statement states "
                            f"{anchors.format_amount(line['closing_minor'], currency)}. "
                            "A different amount for the same date is refused; nothing was imported."
                        ) from None
                    anchors_written += 1 if written else 0

            # Create new services submitted from the preview
            # Each entry: {name, book, type_id, description} — description becomes the merchant rule
            services_created = 0
            for ns in new_services:
                svc_name = (ns.get("name") or "").strip()
                desc = (ns.get("description") or "").strip()
                if not svc_name:
                    continue
                # Dedup: skip if service already exists
                existing = conn.execute(
                    "SELECT id FROM services WHERE UPPER(name) = ?", (svc_name.upper(),)
                ).fetchone()
                if existing:
                    svc_id = existing["id"]
                else:
                    # The new merchant's default book and type. With no book
                    # sent, the type proposes it (checked before any write).
                    svc_type_id = ns.get("type_id") or None
                    svc_book = _book_or_proposed(conn, ns.get("book") or None, svc_type_id)
                    conn.execute(
                        "INSERT INTO services (name, book, type_id) VALUES (?, ?, ?)",
                        (svc_name, svc_book, svc_type_id),
                    )
                    svc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    services_created += 1
                # Create merchant rule from description → service
                if desc:
                    pattern = desc.upper().strip()
                    if _looks_transfer_like_description(desc) or _is_generic_rule_pattern(pattern):
                        rules_skipped_generic += 1
                    else:
                        rule_exists = conn.execute(
                            "SELECT id FROM merchant_rules WHERE UPPER(pattern) = ?", (pattern,)
                        ).fetchone()
                        if not rule_exists:
                            conn.execute(
                                "INSERT INTO merchant_rules (pattern, service_id, match_type, confidence) "
                                "VALUES (?, ?, 'contains', 'confirmed')",
                                (pattern, svc_id),
                            )
                # Backfill service_id on transactions in this import that match this description
                conn.execute(
                    "UPDATE transactions SET service_id = ? WHERE UPPER(description) = ? AND service_id IS NULL",
                    (svc_id, desc.upper()),
                )

            # Save new merchant rules
            rules_added = 0
            for rule in new_rules:
                try:
                    # Rules require service_id — skip if not provided
                    if not rule.get("service_id"):
                        continue
                    pattern_error = _rule_pattern_error(rule.get("pattern"))
                    if pattern_error:
                        rules_skipped_generic += 1
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO merchant_rules (pattern, service_id, match_type, confidence) "
                        "VALUES (?, ?, ?, 'confirmed')",
                        (rule["pattern"], rule["service_id"], rule.get("match_type", "contains")),
                    )
                    rules_added += 1
                except Exception as e:
                    app.logger.warning("Failed to insert rule '%s': %s", rule.get("pattern"), e)

            # Pair the moves between household accounts this import completed.
            pairing.match_pairs(conn)
            # Rows that name a loan change what was paid between its figures.
            loan_interest.derive(conn)

            # Update batch_imports record
            result_summary = {
                "transactions_saved": total_saved,
                "duplicates_skipped": total_duplicates,
                "accounts": accounts_created,
                "rules_added": rules_added,
                "services_created": services_created,
                "rules_skipped_generic": rules_skipped_generic,
                "anchors_written": anchors_written,
                "rows_refiled": rows_refiled,
            }
            conn.execute(
                "UPDATE batch_imports SET status = 'committed', result_json = ? WHERE id = ?",
                (json.dumps(result_summary), import_id),
            )
            # The one commit: rows, accounts, statement records, anchors,
            # merchants and rules land together or not at all.
            conn.commit()
            invalidate_rules_cache()

            return jsonify({
                "success": True,
                "transactions_saved": total_saved,
                "duplicates_skipped": total_duplicates,
                "rules_added": rules_added,
                "accounts": accounts_created,
                "rules_skipped_generic": rules_skipped_generic,
                "anchors_written": anchors_written,
                "rows_refiled": rows_refiled,
            })

        except AnchorRefused as e:
            conn.rollback()
            conn.execute(
                "UPDATE batch_imports SET status = 'failed', result_json = ? WHERE id = ?",
                (json.dumps({"error": str(e)}), import_id),
            )
            conn.commit()
            return jsonify({"error": str(e)}), 409

        except Exception as e:
            conn.rollback()
            conn.execute(
                "UPDATE batch_imports SET status = 'failed', result_json = ? WHERE id = ?",
                (json.dumps({"error": str(e)}), import_id),
            )
            conn.commit()
            app.logger.warning("Import failed: %s", e)
            return jsonify({"error": "Import failed"}), 500


@app.route("/api/import/history")
def api_import_history():
    """List past imports."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM batch_imports ORDER BY created_at DESC"
        ).fetchall()

    result = []
    for r in rows:
        accounts_raw = json.loads(r["accounts"]) if r["accounts"] else []
        result.append({
            "id": r["id"],
            "filenames": json.loads(r["filenames"]) if r["filenames"] else [],
            "accounts": [mask_card_number(a) for a in accounts_raw],
            "status": r["status"],
            "total_lines": r["total_lines"],
            "categorized_lines": r["categorized_lines"],
            "result": json.loads(r["result_json"]) if r["result_json"] else None,
            "created_at": r["created_at"],
        })
    return jsonify(result)


# ---------------------------------------------------------------------------
# Statement Coverage
# ---------------------------------------------------------------------------


@app.route("/api/statements/coverage")
def api_statements_coverage():
    """Return 6-month coverage matrix: active accounts × recent months.

    Shows which accounts have statements imported for each month,
    and which are missing. Uses statement_date month as the key.
    """
    months_count = int(request.args.get("months", 6))

    # Build list of last N months (YYYY-MM format)
    today = datetime.now()
    months = []
    y, m = today.year, today.month
    for _ in range(months_count):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()  # oldest first

    with get_db() as conn:
        # Get active accounts
        accounts = conn.execute(
            "SELECT id, short_name, type FROM accounts "
            "WHERE (status = 'active' OR status IS NULL) AND type IN (?, ?) "
            "ORDER BY type, short_name",
            account_kind.STATEMENT_KINDS,
        ).fetchall()
        accounts = [dict(a) for a in accounts]

        # Get all statements for active accounts in the date range
        min_month = months[0] + "-01"
        statements = conn.execute(
            "SELECT s.account_id, s.statement_date, s.filename, s.imported_at "
            "FROM statements s "
            "JOIN accounts a ON s.account_id = a.id "
            "WHERE (a.status = 'active' OR a.status IS NULL) "
            "  AND a.type IN (?, ?) "
            "  AND s.statement_date >= ? "
            "ORDER BY s.statement_date",
            (*account_kind.STATEMENT_KINDS, min_month),
        ).fetchall()

    # Build matrix: {account_id: {month: {imported, date, filename}}}
    matrix = {}
    for acct in accounts:
        matrix[acct["id"]] = {}
        for m in months:
            matrix[acct["id"]][m] = {"imported": False}

    for stmt in statements:
        acct_id = stmt["account_id"]
        # Extract YYYY-MM from statement_date
        stmt_month = stmt["statement_date"][:7]
        if acct_id in matrix and stmt_month in matrix[acct_id]:
            matrix[acct_id][stmt_month] = {
                "imported": True,
                "date": stmt["imported_at"],
                "filename": stmt["filename"],
            }

    # Summary targets the previous month (most recent closed billing cycle)
    # On March 12th, you want to know if Feb is fully covered, not March
    pm_y, pm_m = today.year, today.month - 1
    if pm_m == 0:
        pm_m = 12
        pm_y -= 1
    target_month = f"{pm_y:04d}-{pm_m:02d}"
    covered = sum(
        1 for acct in accounts if matrix[acct["id"]].get(target_month, {}).get("imported")
    )

    return jsonify({
        "months": months,
        "accounts": accounts,
        "matrix": matrix,
        "summary": {
            "target_month": target_month,
            "covered": covered,
            "total": len(accounts),
        },
    })


# ---------------------------------------------------------------------------
# Merchant Rules CRUD
# ---------------------------------------------------------------------------

# A rule's amount thresholds, as the API names them, and the columns that
# hold them in whole minor units. Frozen: the column names in the statements
# below come from here and nowhere else.
_THRESHOLD_COLUMNS = {"min_amount": "min_amount_minor", "max_amount": "max_amount_minor"}


def _threshold_shown(minor: int | None) -> float | None:
    return None if minor is None else money.from_minor(minor)


def _threshold_minor(data: dict, field: str) -> int | None:
    """A threshold from a request body in whole minor units; None when it is
    left out or cleared. Raises BadAmount for anything else that is not a
    whole number of cents."""
    value = data.get(field)
    if value is None or value == "":
        return None
    return _minor_from_request(value, field)


@app.route("/api/rules")
def api_rules():
    """List all merchant rules with their service, and the book and type each
    gives a row: its own override where it has one, else the merchant's."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT mr.id, mr.pattern, mr.match_type, mr.confidence,
                   mr.priority, mr.min_amount_minor, mr.max_amount_minor,
                   mr.service_id,
                   mr.book_override, mr.type_override_id,
                   s.name as service_name,
                   COALESCE(mr.book_override, s.book) as book,
                   COALESCE(mr.type_override_id, s.type_id) as type_id,
                   ty.name as type_name,
                   tp.name as parent_type
             FROM merchant_rules mr
             JOIN services s ON mr.service_id = s.id
             LEFT JOIN types ty ON COALESCE(mr.type_override_id, s.type_id) = ty.id
             LEFT JOIN types tp ON ty.parent_id = tp.id
            ORDER BY COALESCE(tp.name, ty.name), ty.name, mr.priority DESC, mr.pattern
        """).fetchall()
        labelled = _rows_labelled_by_rule(conn)
    return jsonify([{
        "id": r["id"],
        "pattern": r["pattern"],
        "match_type": r["match_type"],
        "confidence": r["confidence"],
        "priority": r["priority"],
        "min_amount": _threshold_shown(r["min_amount_minor"]),
        "max_amount": _threshold_shown(r["max_amount_minor"]),
        "service_id": r["service_id"],
        "service_name": r["service_name"],
        "book_override": r["book_override"],
        "type_override_id": r["type_override_id"],
        "book": r["book"],
        "type_id": r["type_id"],
        "type_name": r["type_name"],
        "parent_type": r["parent_type"],
        "display_type": format_type_display(r["parent_type"], r["type_name"]),
        "rows_labelled": labelled.get(r["id"], 0),
    } for r in rows])


def _rows_labelled_by_rule(conn) -> dict[int, int]:
    """How many rows each rule labels now: every row whose label came from
    the rules (not by hand), counted against the rule that wins for it, as
    a re-run would find it. Transfers and card payments are not counted."""
    counts: dict[int, int] = {}
    for tx in conn.execute(
        "SELECT description, amount_minor FROM transactions "
        "WHERE COALESCE(flow_type, 'expense') NOT IN ('transfer', 'payment') "
        "AND COALESCE(cat_source, 'auto') IN ('auto', 'service_default', 'rule_override', 'fallback')"
    ):
        rule = first_rule(tx["description"] or "", conn, tx["amount_minor"])
        if rule is not None:
            counts[rule["id"]] = counts.get(rule["id"], 0) + 1
    return counts


@app.route("/api/rules", methods=["POST"])
def api_rules_create():
    """Add a new merchant rule. Requires service_id: book and type come from
    the service unless the rule overrides them."""
    data = request.get_json()
    if not data or "pattern" not in data or "service_id" not in data:
        return jsonify({"error": "pattern and service_id required"}), 400
    pattern_error = _rule_pattern_error(data.get("pattern"))
    if pattern_error:
        return jsonify({"error": pattern_error}), 400

    with get_db() as conn:
        try:
            book_override = _checked_book(data.get("book_override"))
            type_override_id = _checked_type_id(conn, data.get("type_override_id"))
            min_minor = _threshold_minor(data, "min_amount")
            max_minor = _threshold_minor(data, "max_amount")
        except (UnknownLabel, BadAmount) as e:
            return jsonify({"error": str(e)}), 400
        return _crud_insert(
            conn,
            "INSERT INTO merchant_rules (pattern, service_id, book_override, type_override_id, "
            "match_type, confidence, priority, min_amount_minor, max_amount_minor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["pattern"],
                data["service_id"],
                book_override,
                type_override_id,
                data.get("match_type", "contains"),
                "confirmed",
                data.get("priority", 0),
                min_minor,
                max_minor,
            ),
            "rule",
            post_commit=invalidate_rules_cache,
        )


@app.route("/api/rules/<int:rule_id>", methods=["PUT"])
def api_rules_update(rule_id):
    """Update a merchant rule, then write its book and type to its rows."""
    data = request.get_json()
    if "pattern" in (data or {}):
        pattern_error = _rule_pattern_error(data.get("pattern"))
        if pattern_error:
            return jsonify({"error": pattern_error}), 400
    sets, params = _build_update_sets(
        data,
        ["pattern", "service_id", "book_override", "type_override_id", "match_type",
         "priority"],
    )
    try:
        for field, column in _THRESHOLD_COLUMNS.items():
            if field in (data or {}):
                sets.append(f"{column} = ?")
                params.append(_threshold_minor(data, field))
    except BadAmount as e:
        return jsonify({"error": str(e)}), 400
    if not sets:
        return jsonify({"error": "No fields to update"}), 400

    with get_db() as conn:
        try:
            _checked_labels(conn, data, "book_override", "type_override_id")
        except UnknownLabel as e:
            return jsonify({"error": str(e)}), 400
        params.append(rule_id)
        conn.execute(f"UPDATE merchant_rules SET {', '.join(sets)} WHERE id = ?", params)

        # Relabel: re-run this specific rule against transactions
        # Fetch the updated rule + the merchant's default book and type
        rule = conn.execute(
            "SELECT mr.pattern, mr.match_type, mr.service_id, mr.book_override, "
            "mr.type_override_id, s.book, s.type_id "
            "FROM merchant_rules mr JOIN services s ON mr.service_id = s.id "
            "WHERE mr.id = ?", (rule_id,)
        ).fetchone()
        recategorized = 0
        if rule:
            pattern_upper = rule["pattern"].upper()
            match_cond = _build_match_condition(rule["match_type"])
            book, type_id, source = rule_label(rule)
            flow_ctx = None
            rows = conn.execute(
                f"""
                SELECT id, description, amount_minor, flow_type_manual
                FROM transactions
                WHERE {match_cond}
                  AND COALESCE(flow_type, 'expense') NOT IN ('transfer', 'payment')
                  AND COALESCE(cat_source, 'auto') IN ('auto', 'service_default', 'rule_override', 'fallback')
                """,
                (pattern_upper,),
            ).fetchall()
            for tx in rows:
                params = [book, type_id, rule["service_id"], source]
                sql = "UPDATE transactions SET book = ?, type_id = ?, service_id = ?, cat_source = ?"
                if not tx["flow_type_manual"]:
                    if flow_ctx is None:
                        flow_ctx = flow.build_context(conn)
                    params.extend(
                        _classify_flow_for_tx(
                            conn, tx["description"], tx["amount_minor"], flow_ctx=flow_ctx,
                            tx_id=tx["id"], service_id=rule["service_id"], labelled=True,
                        )
                    )
                    sql += ", flow_type = ?, other_side_id = ?"
                sql += " WHERE id = ?"
                params.append(tx["id"])
                conn.execute(sql, params)
                recategorized += 1

        conn.commit()
        invalidate_rules_cache()
    return jsonify({"success": True, "recategorized": recategorized})


@app.route("/api/rules/<int:rule_id>", methods=["DELETE"])
def api_rules_delete(rule_id):
    """Delete a merchant rule."""
    with get_db() as conn:
        conn.execute("DELETE FROM merchant_rules WHERE id = ?", (rule_id,))
        conn.commit()
        invalidate_rules_cache()
    return jsonify({"success": True})


@app.route("/api/rules/recategorize", methods=["POST"])
def api_rules_recategorize():
    """Re-run all merchant rules against existing transactions.

    Each row takes the book and type its rule gives it (the merchant's
    default, or the rule's override). Rows labelled by hand are left alone,
    and a flow set by hand keeps its flow and its other side.
    """
    with get_db() as conn:
        # Skip manually resolved transactions — only re-run on rows the rules labelled
        rows = conn.execute("""
            SELECT id, description, amount_minor, book, type_id, service_id, cat_source,
                   COALESCE(flow_type, 'expense') AS flow_type, flow_type_manual, other_side_id
            FROM transactions
            WHERE COALESCE(flow_type, 'expense') NOT IN ('transfer', 'payment')
              AND COALESCE(cat_source, 'auto') IN ('auto', 'service_default', 'rule_override', 'fallback')
        """).fetchall()

        skipped = conn.execute("""
            SELECT COUNT(*) FROM transactions
            WHERE COALESCE(flow_type, 'expense') NOT IN ('transfer', 'payment') AND cat_source = 'manual'
        """).fetchone()[0]

        updated = 0
        unchanged = 0
        flow_ctx = None
        for tx in rows:
            found = _label_for(conn, tx["description"], tx["amount_minor"])
            new_label = (found["book"], found["type_id"], found["service_id"], found["cat_source"])
            old_flow = (tx["flow_type"], tx["other_side_id"])
            new_flow = old_flow
            if not tx["flow_type_manual"]:
                if flow_ctx is None:
                    flow_ctx = flow.build_context(conn)
                new_flow = _classify_flow_for_tx(
                    conn, tx["description"], tx["amount_minor"], flow_ctx=flow_ctx,
                    tx_id=tx["id"], service_id=found["service_id"],
                    labelled=bool(found["type_id"] or found["service_id"]),
                )
            old_label = (tx["book"], tx["type_id"], tx["service_id"], tx["cat_source"])
            if new_label != old_label or new_flow != old_flow:
                conn.execute(
                    "UPDATE transactions SET book = ?, type_id = ?, service_id = ?, cat_source = ?, "
                    "flow_type = ?, other_side_id = ? WHERE id = ?",
                    (*new_label, *new_flow, tx["id"]),
                )
                updated += 1
            else:
                unchanged += 1

        conn.commit()
    return jsonify({"updated": updated, "unchanged": unchanged, "skipped_manual": skipped})


# ---------------------------------------------------------------------------
# Balance sheet: what the household owns and owes at a month's end
# ---------------------------------------------------------------------------

@app.route("/api/balance-sheet")
def api_balance_sheet():
    """The household's balance sheet at the end of ?month=YYYY-MM (this month
    when none is given): its bank, card, loan and holding accounts in
    sections, each line with its balance in its own currency, the anchor it
    rests on, the rows since and its check; a line in another currency with
    its SGD value and the saved rate that gives it; then the household's money
    in each company and with each person, each line with what it is made of
    (opening figure, paid for it, capital, paid back); section totals and net
    worth in SGD, saying what is left out; the month's currency change; how
    many transfers wait for review; and the month check (month_check.py):
    net worth at the previous month-end, plus income, less spending, plus
    currency change, against net worth at the month-end, the difference
    unexplained, beside the month's transfers waiting for review and the
    lines whose check is not "ties".
    A month before January 2026 is refused: nothing is shown before then."""
    month = request.args.get("month")
    if month is None:
        month = date.today().strftime("%Y-%m")
    with get_db() as conn:
        try:
            shown = balance_sheet.sheet(conn, month, mask_card_number)
        except balance_sheet.NotShown as e:
            return jsonify({"error": str(e)}), 400
        shown["review_waiting"] = _waiting_for_review(conn)[0]
        # The transfers dated in the month that wait for a label: what the
        # month's unexplained figure may be made of.
        waiting = _waiting_sides(conn, " AND strftime('%Y-%m', t.date) = ?", [month])
        shown["month_check"] = month_check.check(conn, month, shown, mask_card_number, waiting)
    return jsonify(shown)


# ---------------------------------------------------------------------------
# Saved rates: what a rupee was worth in SGD on a date
# ---------------------------------------------------------------------------

@app.route("/api/rates")
def api_rates():
    """Every saved rate, newest first; with ?currency=INR that currency's.
    `pair` reads "INR/SGD": SGD for one rupee. `rate` is decimal text."""
    currency = request.args.get("currency")
    with get_db() as conn:
        try:
            pair = rates.pair_for(currency) if currency is not None else None
        except rates.InvalidRate as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(rates.listed(conn, pair))


@app.route("/api/rates/fetch", methods=["POST"])
def api_rates_fetch():
    """Fetch the European Central Bank's reference rate for a day and save it.

    Body: currency (INR), date (YYYY-MM-DD). A rate already saved for that day
    is returned as it is and the source is not asked: fin reads its saved
    copy. A weekend or holiday takes the prior business day's rate, and the
    saved source says which day that is. A failed fetch saves nothing (502).
    """
    data = request.get_json(silent=True) or {}
    with get_db() as conn:
        try:
            held, created = rates.ensure(conn, data.get("currency"), data.get("date"))
        except rates.InvalidRate as e:
            return jsonify({"error": str(e)}), 400
        except rates.FetchFailed as e:
            return jsonify({"error": str(e)}), 502
        conn.commit()
    return jsonify({"success": True, "created": created, "rate": held})


@app.route("/api/rates", methods=["PUT"])
def api_rates_overwrite():
    """Enter a rate for a day, in place of any saved for it.

    Body: currency (INR), date (YYYY-MM-DD), rate (decimal text: SGD for one
    unit of the currency). Every SGD figure is worked out on read, so a
    corrected rate corrects the months that use it.
    """
    data = request.get_json(silent=True) or {}
    with get_db() as conn:
        try:
            held = rates.overwrite(conn, data.get("currency"), data.get("date"), data.get("rate"))
        except rates.InvalidRate as e:
            return jsonify({"error": str(e)}), 400
        conn.commit()
    return jsonify({"success": True, "rate": held})


# ---------------------------------------------------------------------------
# Review list: the transfers waiting for a label
# ---------------------------------------------------------------------------

@app.route("/api/review")
def api_review():
    """How many transfers are waiting and what they add up to (money out less
    money in, and the two sides apart), and the choices a label can be, from
    their one declaration. The waiting rows themselves are the transaction
    list with flow=review."""
    with get_db() as conn:
        waiting = _waiting_sides(conn)
    return jsonify({
        "waiting": waiting["count"],
        "waiting_total": money.from_minor(waiting["net_minor"]),
        **_sides_payload("waiting", waiting),
        "choices": [
            {
                "name": c.name,
                "label": c.label,
                "flow": c.flow,
                "asks": c.asks,
                "kinds": list(c.kinds),
                "description": c.description,
            }
            for c in review.CHOICES
        ],
    })


def _label_account(conn, choice, account_id, row_account_id) -> int:
    """The account a label names as the other side: one the household holds,
    of a kind the choice allows, and not the account the row is on."""
    if isinstance(account_id, bool) or not isinstance(account_id, int):
        raise review.LabelRefused(f"account_id is required: {choice.label} names an account")
    account = conn.execute(
        "SELECT id, type, owner FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if (
        account is None
        or account["type"] not in choice.kinds
        or account["owner"] != account_kind.HOUSEHOLD
    ):
        raise review.LabelRefused(
            f"{choice.label} names one of the household's accounts of kind "
            + " or ".join(choice.kinds)
        )
    if account["id"] == row_account_id:
        raise review.LabelRefused("the other side cannot be the account the row is on")
    return account["id"]


def _typed_as(conn, kind: str, name: str) -> int:
    return conn.execute(
        "SELECT id FROM types WHERE kind = ? AND name = ? AND parent_id IS NULL", (kind, name)
    ).fetchone()["id"]


@app.route("/api/review/<int:tx_id>/label", methods=["POST"])
def api_review_label(tx_id: int):
    """Say what a waiting transfer was. Writes the choice's flow and its other
    side as a manual label, which no rule and no re-run changes afterwards.

    Body: choice, and what the choice asks for (see /api/review):
        spending        type_id; book where the type proposes none
        gift            nothing: spending when given, income when received
        own_account     account_id of a household bank account or card
        company         account_id of a company
        loan_to_person  account_id of a person, or person: a name. A name
                        nobody has yet creates that person's account.
        loan_repayment  account_id of a loan
        income          income_kind_id
    """
    data = request.get_json(silent=True) or {}
    choice = review.BY_NAME.get(data.get("choice")) if isinstance(data.get("choice"), str) else None
    if choice is None:
        return jsonify({"error": f"unknown choice: {data.get('choice')!r}"}), 400

    with get_db() as conn:
        tx = conn.execute(
            "SELECT t.id, t.amount_minor, s.account_id FROM transactions t "
            "JOIN statements s ON t.statement_id = s.id WHERE t.id = ?",
            (tx_id,),
        ).fetchone()
        if tx is None:
            return jsonify({"error": "no such row"}), 404

        flow_name = flow.checked_flow(choice.flow)
        book = type_id = other_side = new_person = None
        try:
            if choice.asks == review.ASKS_TYPE:
                type_id = _checked_type_id(conn, data.get("type_id"))
                if type_id is None:
                    raise review.LabelRefused("type_id is required: spending takes a type")
                book = _book_or_proposed(conn, _checked_book(data.get("book")), type_id)
            elif choice.name == "gift":
                # Positive is money out: a gift given. Otherwise one received.
                if tx["amount_minor"] < 0:
                    flow_name = "income"
                    type_id = _typed_as(conn, book_type.INCOME, review.GIFT_RECEIVED_KIND)
                else:
                    type_id = _typed_as(conn, book_type.SPENDING, review.GIFT_GIVEN_TYPE)
                    book = book_type.DEFAULT_BOOK
            elif choice.asks == review.ASKS_INCOME_KIND:
                kind_id = data.get("income_kind_id")
                if isinstance(kind_id, bool) or not isinstance(kind_id, int) or not conn.execute(
                    "SELECT 1 FROM types WHERE id = ? AND kind = ?", (kind_id, book_type.INCOME)
                ).fetchone():
                    raise review.LabelRefused("income_kind_id is required: one of the income kinds")
                type_id = kind_id
            elif choice.asks == review.ASKS_PERSON and data.get("account_id") is None:
                person = data.get("person")
                person = person.strip() if isinstance(person, str) else ""
                if not person:
                    raise review.LabelRefused("person is required: who the money was lent to")
                held = conn.execute(
                    "SELECT id FROM accounts WHERE UPPER(name) = ? AND type = ? AND owner = ? "
                    "ORDER BY id LIMIT 1",
                    (person.upper(), "person", account_kind.HOUSEHOLD),
                ).fetchone()
                if held:
                    other_side = held["id"]
                else:
                    new_person = person
            else:
                other_side = _label_account(conn, choice, data.get("account_id"), tx["account_id"])
        except (UnknownLabel, BookNeeded, review.LabelRefused) as e:
            return jsonify({"error": str(e)}), 400

        # Everything asked for is there: the person's account, then the label,
        # in one transaction.
        if new_person is not None:
            other_side = conn.execute(
                "INSERT INTO accounts (name, short_name, type, owner) VALUES (?, ?, ?, ?)",
                (new_person, new_person, account_kind.checked_kind("person"), account_kind.HOUSEHOLD),
            ).lastrowid
        conn.execute(
            "UPDATE transactions SET flow_type = ?, flow_type_manual = 1, other_side_id = ?, "
            "book = ?, type_id = ?, cat_source = 'manual' WHERE id = ?",
            (flow_name, other_side, book, type_id, tx_id),
        )
        # A row that now names a loan, or no longer does, changes what was paid.
        loan_interest.derive(conn)
        conn.commit()
        named = conn.execute("SELECT name FROM accounts WHERE id = ?", (other_side,)).fetchone()
    return jsonify({
        "success": True,
        "id": tx_id,
        "flow_type": flow_name,
        "other_side_id": other_side,
        "other_side_name": mask_card_number(named["name"]) if named else None,
        "created_account": new_person is not None,
    })


@app.route("/api/pair-matching", methods=["POST"])
def api_pair_matching():
    """Pair the moves between household accounts, on demand: the same match
    the import runs (pairing.py). A row set by hand is never changed, and a
    second run pairs nothing more.

    Returns paired, the number of pairs made, and waiting, the transfers
    still on the review list."""
    with get_db() as conn:
        paired = pairing.match_pairs(conn)
        conn.commit()
        waiting = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE flow_type = ?", (flow.REVIEW,)
        ).fetchone()[0]
    return jsonify({"paired": paired, "waiting": waiting})


# ---------------------------------------------------------------------------
# Subscriptions API
# ---------------------------------------------------------------------------

# The USD→SGD rate the subscriptions are shown at: a fixed stand-in until the
# operator fetches one (POST /api/fx-rate). Only a click fetches: no GET route
# reaches outside fin.
_fx_cache = {"rate": 1.35, "fetched_at": None}


def _get_usd_sgd_rate() -> float:
    """The USD→SGD rate held now: the last one fetched on request, or the
    stand-in. Never fetches."""
    return _fx_cache["rate"]


def _fetch_usd_sgd_rate() -> float:
    """Fetch the current USD→SGD rate and hold it. Called only by the POST
    route a click sends; on failure the held rate is kept."""
    import time
    import urllib.request

    try:
        with urllib.request.urlopen("https://open.er-api.com/v6/latest/USD", timeout=5) as resp:
            rate = float(json.loads(resp.read())["rates"]["SGD"])
    except Exception:
        return _fx_cache["rate"]
    _fx_cache["rate"] = rate
    _fx_cache["fetched_at"] = time.time()
    return rate


def _monthly_equivalent(amount: float, frequency: str, periods: int,
                        currency: str = "SGD", fx_rate: float = 1.0) -> float:
    """Convert billed amount to monthly SGD equivalent."""
    periods = periods or 1
    amount_sgd = amount * fx_rate if currency == "USD" else amount
    if frequency == "yearly":
        return amount_sgd / (12 * periods)
    elif frequency == "half-yearly":
        return amount_sgd / (6 * periods)
    elif frequency == "quarterly":
        return amount_sgd / (3 * periods)
    elif frequency == "biweekly":
        return amount_sgd * (52 / 2) / (12 * periods)  # ~2.17x per month
    elif frequency == "weekly":
        return amount_sgd * 52 / (12 * periods)  # ~4.33x per month
    return amount_sgd / periods  # monthly


@app.route("/api/subscriptions")
def api_subscriptions():
    """List all subscriptions with transaction enrichment. A subscription
    carries no label of its own: its book and type are its merchant's."""
    fx_rate = _get_usd_sgd_rate()

    # Compute 90-day cutoff for rolling averages
    cutoff_90d = (date.today() - timedelta(days=90)).isoformat()

    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.*,
                   """ + book_expr("svc") + """ as merchant_book,
                   svc.type_id as merchant_type_id,
                   ty.name as type_name, tp.name as parent_type,
                   a.short_name as account_short_name, a.name as account_name,
                   svc.name as service_name
            FROM subscriptions s
            LEFT JOIN accounts a ON s.account_id = a.id
            LEFT JOIN services svc ON s.service_id = svc.id
            """ + _TYPE_JOINS.format(owner="svc") + """
            ORDER BY
                CASE s.status WHEN 'active' THEN 0 ELSE 1 END,
                s.renewal_date
        """).fetchall()

        # Batch enrichment: latest tx per subscription (replaces 2 queries per sub)
        # A subscription only matches transactions in its merchant's book.
        latest_tx_rows = conn.execute("""
        WITH matched AS (
            SELECT s.id as sub_id,
                   t.id as tx_id, t.date as tx_date, t.amount_minor,
                   ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY t.date DESC) as rn
            FROM subscriptions s
            JOIN transactions t
                ON UPPER(t.description) LIKE '%' || UPPER(s.match_pattern) || '%'
            LEFT JOIN services svc ON s.service_id = svc.id
            WHERE s.match_pattern IS NOT NULL AND s.match_pattern != ''
              AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund')
              AND """ + book_expr("t") + " = " + book_expr("svc") + """
        )
        SELECT sub_id, tx_id, tx_date, amount_minor
        FROM matched WHERE rn = 1
        """).fetchall()
        latest_tx = {r["sub_id"]: dict(r) for r in latest_tx_rows}

        # Batch enrichment: monthly sums per subscription for 90-day rolling avg
        # Same book boundary as latest tx query.
        monthly_rows = conn.execute("""
            SELECT s.id as sub_id,
                   SUBSTR(t.date, 1, 7) as ym,
                   SUM(t.amount_minor) as month_total
            FROM subscriptions s
            JOIN transactions t
                ON UPPER(t.description) LIKE '%' || UPPER(s.match_pattern) || '%'
            LEFT JOIN services svc ON s.service_id = svc.id
            WHERE s.match_pattern IS NOT NULL AND s.match_pattern != ''
              AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund')
              AND t.date >= ?
              AND """ + book_expr("t") + " = " + book_expr("svc") + """
            GROUP BY s.id, SUBSTR(t.date, 1, 7)
            ORDER BY s.id, ym DESC
        """, (cutoff_90d,)).fetchall()

    # Build lookup: sub_id → [{ym, month_total}, ...] ordered by ym DESC.
    # A month_total is in whole minor units, and so is every figure worked
    # out from the rows below until it is put on the payload.
    monthly_by_sub: dict[int, list[dict]] = {}
    for r in monthly_rows:
        monthly_by_sub.setdefault(r["sub_id"], []).append(
            {"ym": r["ym"], "month_total": r["month_total"]}
        )

    result = []
    for r in rows:
        d = dict(r)
        # Book and type are the merchant's, never the subscription's own.
        d["book"] = d.pop("merchant_book")
        d["type_id"] = d.pop("merchant_type_id")
        sub_id = d["id"]
        pat = (d["match_pattern"] or "").upper()
        monthly_sums = monthly_by_sub.get(sub_id, []) if pat else []

        # Enrich from batch lookups (defaults, then override if matched)
        d.update(tx_last_paid=None, tx_amount=None, tx_id=None,
                 tx_avg_90d=None, tx_months_90d=0)
        if pat:
            tx = latest_tx.get(sub_id)
            if tx:
                d["tx_last_paid"] = tx["tx_date"]
                d["tx_amount"] = money.from_minor(tx["amount_minor"])
                d["tx_id"] = tx["tx_id"]
            d["tx_months_90d"] = len(monthly_sums)
            if monthly_sums:
                totals = [m["month_total"] for m in monthly_sums]
                d["tx_avg_90d"] = money.from_minor(money.mean_minor(sum(totals), len(totals)))

        # Last paid amount: latest month's sum (handles split payments)
        if pat and d.get("tx_months_90d"):
            d["tx_amount"] = money.from_minor(monthly_sums[0]["month_total"])

        # Billed = configured amount per cycle (source of truth)
        amt = d.get("amount") or 0
        cur = d.get("currency") or "SGD"

        # Monthly equivalent: use 90d avg if variable (>10% variance), else configured
        d["is_variable"] = False
        if d["tx_avg_90d"] and d["tx_months_90d"] >= 2:
            totals = [m["month_total"] for m in monthly_sums]
            mn, mx = min(totals), max(totals)
            if mx * 100 > mn * 110:
                d["is_variable"] = True
                d["monthly_sgd"] = d["tx_avg_90d"]
        if not d["is_variable"]:
            d["monthly_sgd"] = round(_monthly_equivalent(amt, d["frequency"], d["periods"], cur, fx_rate), 2)

        d["display_type"] = format_type_display(d["parent_type"], d["type_name"])
        d["fx_rate"] = fx_rate

        # Anchor-based renewal: advance from renewal_date anchor until future
        effective_last_paid = d.get("tx_last_paid") or d.get("last_paid")
        d["computed_renewal"] = _advance_renewal(
            d.get("renewal_date"), effective_last_paid, d["frequency"], d["periods"]
        )

        result.append(d)

    return jsonify(result)


@app.route("/api/subscriptions", methods=["POST"])
def api_subscriptions_create():
    """Add a new subscription. It takes its book and type from its service."""
    data = request.get_json()
    if not data or not data.get("service_id"):
        return jsonify({"error": "service_id is required"}), 400

    with get_db() as conn:
        # Derive match_pattern from service name if not provided
        match_pattern = data.get("match_pattern")
        if not match_pattern:
            svc = conn.execute("SELECT name FROM services WHERE id = ?", (data["service_id"],)).fetchone()
            match_pattern = svc["name"].upper() if svc else ""

        return _crud_insert(
            conn,
            "INSERT INTO subscriptions "
            "(service_id, amount, currency, "
            "frequency, periods, account_id, last_paid, renewal_date, status, "
            "link, notes, match_pattern) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["service_id"],
                data.get("amount", 0),
                data.get("currency", "SGD"),
                data.get("frequency", "monthly"),
                data.get("periods", 1),
                data.get("account_id"),
                data.get("last_paid"),
                data.get("renewal_date"),
                data.get("status", "active"),
                data.get("link"),
                data.get("notes"),
                match_pattern,
            ),
            "subscription",
        )


@app.route("/api/subscriptions/<int:sub_id>", methods=["PUT"])
def api_subscriptions_update(sub_id):
    """Update a subscription."""
    return _crud_update("subscriptions", sub_id, request.get_json(), [
        "service_id", "amount", "currency", "frequency",
        "periods", "account_id", "last_paid", "renewal_date", "status",
        "link", "notes", "match_pattern",
    ])


@app.route("/api/subscriptions/<int:sub_id>", methods=["DELETE"])
def api_subscriptions_delete(sub_id):
    """Delete a subscription."""
    with get_db() as conn:
        conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
        conn.commit()
    return jsonify({"success": True})


@app.route("/api/subscriptions/enrich", methods=["POST"])
def api_subscriptions_enrich():
    """Update all subscriptions with latest transaction data.

    Also auto-advances renewal_date when last_paid is past the current renewal.
    """
    with get_db() as conn:
        subs = conn.execute(
            "SELECT id, match_pattern, frequency, periods, renewal_date, currency "
            "FROM subscriptions WHERE match_pattern IS NOT NULL"
        ).fetchall()
        updated = 0
        renewals_advanced = 0
        for s in subs:
            tx = conn.execute("""
                SELECT t.date
                FROM transactions t
                WHERE UPPER(t.description) LIKE '%' || ? || '%'
                  AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund')
                ORDER BY t.date DESC LIMIT 1
            """, (s["match_pattern"].upper(),)).fetchone()
            if tx:
                new_renewal = _advance_renewal(
                    s["renewal_date"], tx["date"], s["frequency"], s["periods"]
                )
                # Update last_paid and renewal only — never overwrite the user's
                # configured billed amount (amount is the source of truth, set manually)
                conn.execute(
                    "UPDATE subscriptions SET last_paid = ?, renewal_date = ? WHERE id = ?",
                    (tx["date"], new_renewal, s["id"]),
                )
                updated += 1
                if new_renewal != s["renewal_date"]:
                    renewals_advanced += 1
        conn.commit()
    return jsonify({"success": True, "updated": updated, "renewals_advanced": renewals_advanced})


def _advance_renewal(
    current_renewal: str | None,
    last_paid: str | None,
    frequency: str,
    periods: int,
) -> str | None:
    """Anchor-based renewal: advance renewal_date forward by frequency until it's
    in the future relative to TODAY (not last_paid).

    This prevents payment delays from skewing the renewal calendar.
    If no renewal anchor exists, seed from last_paid + one cycle.
    """
    today = date.today()

    if not current_renewal:
        if not last_paid:
            return None
        # No anchor — seed from last_paid + one cycle
        renewal = _add_billing_period(date.fromisoformat(last_paid), frequency, periods)
    else:
        renewal = date.fromisoformat(current_renewal)

    # Step forward until renewal is in the future
    while renewal <= today:
        renewal = _add_billing_period(renewal, frequency, periods)

    return renewal.isoformat()


def _add_billing_period(d: date, frequency: str, periods: int) -> date:
    """Add one billing period to a date."""
    periods = periods or 1
    if frequency == "yearly":
        return d.replace(year=d.year + periods)
    elif frequency == "half-yearly":
        month = d.month + (6 * periods)
        year = d.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(d.day, 28)
        return d.replace(year=year, month=month, day=day)
    elif frequency == "quarterly":
        month = d.month + (3 * periods)
        year = d.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(d.day, 28)
        return d.replace(year=year, month=month, day=day)
    elif frequency == "biweekly":
        return d + timedelta(weeks=2 * periods)
    elif frequency == "weekly":
        return d + timedelta(weeks=1 * periods)
    else:  # monthly
        month = d.month + periods
        year = d.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(d.day, 28)
        return d.replace(year=year, month=month, day=day)


@app.route("/api/fx-rate", methods=["GET", "POST"])
def api_fx_rate():
    """The USD→SGD rate held now (GET), or fetched now and held (POST, sent
    by a click)."""
    if request.method == "POST":
        return jsonify({"usd_sgd": _fetch_usd_sgd_rate(), "fetched": _fx_cache["fetched_at"] is not None})
    return jsonify({"usd_sgd": _get_usd_sgd_rate(), "fetched": _fx_cache["fetched_at"] is not None})


# ---------------------------------------------------------------------------
# Backups (fin-online D3): the status and "back up now", app gate only
# ---------------------------------------------------------------------------

@app.route("/api/backups/status")
def api_backups_status():
    """Last attempt, last success, last error; the warning the app shell shows
    when backups are not configured (hosted only: never in local-dev) or none
    succeeded in 36 hours."""
    return jsonify(backup.backup_status(
        db.DB_PATH,
        configured=app.config["BACKUP_STORE"] is not None,
        local_dev=bool(app.config.get("LOCAL_DEV")),
    ))


@app.route("/api/backups/run", methods=["POST"])
def api_backups_run():
    store = app.config["BACKUP_STORE"]
    if store is None:
        message, status = backup.BACKUP_ERRORS["not_configured"]
        return jsonify({"error": message}), status
    try:
        return jsonify(backup.run_backup(db.DB_PATH, store))
    except backup.BackupFailed as failed:
        return jsonify({"error": failed.message, "code": failed.code}), failed.status


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="fin — Personal Finance Tracker")
    parser.add_argument("--port", type=int, default=8450)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    try:
        init_db()
    except db.DatabaseNotConverted as refused:
        # A plain message, not a traceback: it says what to run.
        print(refused, file=sys.stderr)
        return 1
    print(f"fin running at http://localhost:{args.port}")
    # The laptop path: loopback only, no access gate in front (fin-online D1).
    app.config["LOCAL_DEV"] = True
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
