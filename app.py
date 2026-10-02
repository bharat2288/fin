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

from flask import Flask, jsonify, request, send_from_directory

import account_kind
import anchors
import book_type
import flow
import review
from db import get_connection, init_db, invalidate_rules_cache, match_merchant, rule_label


@contextmanager
def get_db():
    """Context manager wrapping get_connection() for automatic cleanup."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

app = Flask(__name__, static_folder="static", static_url_path="/static")


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


def _label_for(conn, description: str, amount_sgd: float) -> dict:
    """What the rules, then the PayNow wording, make of a row: the result of
    match_merchant, with the fallback's type when no rule gave one. A merchant
    the rules did find is kept, and so is its book."""
    found = match_merchant(description, conn, amount=amount_sgd)
    if found["type_id"] is None:
        book, type_id = _paynow_fallback_label(description, conn)
        if type_id is not None:
            found.update(book=found["book"] or book, type_id=type_id, cat_source="fallback")
    return found


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
    conn, description: str, amount_sgd: float, *,
    flow_ctx=None, tx_id=None, service_id=None, labelled: bool = False,
) -> tuple[str, int | None]:
    """Classify a row using the shared flow model: (flow, its other side).

    tx_id says which account the row is on; service_id and labelled say what
    the merchant rules made of it."""
    if flow_ctx is None:
        flow_ctx = flow.build_context(conn)
    facts = {
        "description": description,
        "amount_sgd": amount_sgd,
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
    anchor the account rests on, or null when it has no figure."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, short_name, type, last_four, currency, status, owner FROM accounts ORDER BY name"
        ).fetchall()
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
    except account_kind.UnknownAccountValue as e:
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
                data.get("currency", "SGD"),
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
    except account_kind.UnknownAccountValue as e:
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
    """Enter a figure: a supplied anchor for a loan, a holding, a company or a
    person.

    Body: account_id, amount (text, in whole units of the account's currency:
    what is owed for a loan, what it is worth or the balance otherwise), date
    (YYYY-MM-DD), note. The same amount again for that account and date
    changes nothing; a different amount is refused.
    """
    data = request.get_json(silent=True) or {}
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
        if kind not in account_kind.SUPPLIED_FIGURE_KINDS:
            return jsonify({
                "error": f"a {kind} account rests on its statement; a figure can be entered "
                         "for a loan, a holding, a company or a person"
            }), 400
        try:
            amount_minor = anchors.to_minor_units(data.get("amount"))
            on = anchors.checked_date(data.get("date"))
            if kind in account_kind.OWED_KINDS:
                if amount_minor < 0:
                    raise anchors.InvalidAnchor(
                        "amount for a loan is what is owed, entered as a positive figure"
                    )
                amount_minor = -amount_minor
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
        conn.commit()
        row = conn.execute(_ANCHOR_SELECT + "WHERE n.id = ?", (held["id"],)).fetchone()
    return jsonify({"success": True, "created": created, "anchor": _anchor_payload(row)})


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
            SELECT t.id, t.date, t.description, t.amount_sgd,
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
        d["display_type"] = format_type_display(d["parent_type"], d["type_name"])
        result.append(d)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Dashboard API
# ---------------------------------------------------------------------------

@app.route("/api/dashboard/stat-cards")
def api_dashboard_stat_cards():
    """Stat cards: single month spend + delta vs 3-month rolling average.

    Auto-picks reference month using the 15th rule:
      - If today >= 15th, ref = previous month
      - If today < 15th, ref = two months ago
    Override with ?ref_month=YYYY-MM.

    Respects: book, exclude_one_off, account_id
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
    extra_filters += f" AND {_expense_visibility_filter('svc')}"
    if exclude_one_off:
        # Exclude both transaction-level and service-level one-offs
        extra_filters += " AND t.is_one_off = 0 AND (svc.is_one_off IS NULL OR svc.is_one_off = 0)"
    if account_id:
        try:
            extra_filters += " AND s.account_id = ?"
            extra_params.append(int(account_id))
        except (ValueError, TypeError):
            pass

    # One total per declared book, keyed by the book's name in lower case.
    books = [(name, name.lower()) for name in book_type.BOOK_NAMES]
    book_sums = "".join(
        f"SUM(CASE WHEN {book_expr('t')} = ? THEN amount_sgd ELSE 0 END), " for _ in books
    )

    with get_db() as conn:
        def query_month(y: int, m: int) -> dict:
            """Query spend totals for a single month."""
            start = f"{y:04d}-{m:02d}-01"
            if m == 12:
                end_d = date(y + 1, 1, 1) - timedelta(days=1)
            else:
                end_d = date(y, m + 1, 1) - timedelta(days=1)
            end = end_d.strftime("%Y-%m-%d")

            params = [name for name, _ in books] + [start, end] + extra_params
            row = conn.execute(f"""
                SELECT
                    {book_sums}
                    SUM(amount_sgd),
                    COUNT(CASE WHEN t.type_id IS NULL THEN 1 END),
                    COUNT(*)
                FROM transactions t
                LEFT JOIN services svc ON t.service_id = svc.id
                JOIN statements s ON t.statement_id = s.id
                WHERE t.flow_type IN ('expense', 'refund')
                  AND t.date >= ? AND t.date <= ?
                  {extra_filters}
            """, params).fetchone()
            n = len(books)
            result = {key: round(row[i] or 0, 2) for i, (_, key) in enumerate(books)}
            result["total"] = round(row[n] or 0, 2)
            result["untyped"] = row[n + 1] or 0
            result["tx_count"] = row[n + 2] or 0
            return result

        # Query reference month
        ref_data = query_month(ref_y, ref_m)

        # Query 3 prior months for rolling average
        avg_data = [query_month(y, m) for y, m in avg_months]
        n = len([d for d in avg_data if d["tx_count"] > 0]) or 1  # only months with data
        averages = {
            key: round(sum(d[key] for d in avg_data) / n, 2)
            for key in ["total"] + [key for _, key in books]
        }

    # Pick which spend to feature based on filter
    featured = book.lower() if book else "total"
    spend = ref_data[featured]
    avg_spend = averages[featured]

    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    ref_label = f"{month_names[ref_m]} {ref_y}"

    payload = {
        "ref_month": f"{ref_y:04d}-{ref_m:02d}",
        "ref_label": ref_label,
        "spend": spend,
        "untyped": ref_data["untyped"],
        "tx_count": ref_data["tx_count"],
        "avg_spend": avg_spend,
        "avg_months": n,
    }
    for _, key in books:
        payload[key] = ref_data[key]
        payload[f"avg_{key}"] = averages[key]
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
    filters, params = _build_filters(request.args)
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
                SUM(t.amount_sgd) as total
            FROM transactions t
            {_TYPE_JOINS.format(owner="t")}
            LEFT JOIN services svc ON t.service_id = svc.id
            JOIN statements s ON t.statement_id = s.id
            WHERE t.flow_type IN ('expense', 'refund') {filters}
            GROUP BY period, type
            ORDER BY period, total DESC
        """, params).fetchall()

    # Structure: {period: {type: total, ...}, ...}
    result = {}
    for r in rows:
        period = r["period"]
        if period not in result:
            result[period] = {}
        name = r["type"] or NO_TYPE_LABEL
        result[period][name] = round(result[period].get(name, 0) + r["total"], 2)

    return jsonify(result)


@app.route("/api/dashboard/types")
def api_dashboard_types():
    """Type totals for donut chart. One type serves every book.

    Query params: start, end, book, exclude_one_off, group_parent
    """
    filters, params = _build_filters(request.args)
    type_expr = _type_group_expr(request.args)

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT
                {type_expr} as type,
                SUM(t.amount_sgd) as total,
                COUNT(*) as count
            FROM transactions t
            {_TYPE_JOINS.format(owner="t")}
            LEFT JOIN services svc ON t.service_id = svc.id
            JOIN statements s ON t.statement_id = s.id
            WHERE t.flow_type IN ('expense', 'refund') {filters}
            GROUP BY type
            ORDER BY total DESC
        """, params).fetchall()

    return jsonify([{
        "type": r["type"] or NO_TYPE_LABEL,
        "total": round(r["total"], 2),
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

    flow=review is the review list: the transfers waiting for a label.
    """
    filters, params = _build_filters(request.args)

    flow_filter = request.args.get("flow")
    if flow_filter:
        try:
            flow.checked_flow(flow_filter)
        except flow.UnknownFlow as e:
            return jsonify({"error": str(e)}), 400
        filters += " AND COALESCE(t.flow_type, 'expense') = ?"
        params.append(flow_filter)

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
        "amount": "t.amount_sgd",
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
                t.id, t.date, t.description, t.amount_sgd,
                t.amount_foreign, t.currency_foreign,
                {book_expr("t")} as book,
                t.type_id, ty.name as type, tp.name as parent_type,
                t.cat_source,
                t.is_one_off, COALESCE(t.flow_type, 'expense') as flow_type, t.flow_type_manual,
                t.notes,
                t.other_side_id, oa.name as other_side_name,
                a.name as account_name,
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
                "SELECT description, amount_sgd, flow_type_manual FROM transactions WHERE id = ?",
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
                    conn, tx_row["description"], tx_row["amount_sgd"],
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


def _build_filters(args) -> tuple[str, list]:
    """Build SQL WHERE clause fragments from common query params."""
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

    exclude_one_off = args.get("exclude_one_off")
    if exclude_one_off == "true":
        # Exclude both transaction-level and service-level one-offs
        filters += " AND t.is_one_off = 0 AND (svc.is_one_off IS NULL OR svc.is_one_off = 0)"

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
# Import API
# ---------------------------------------------------------------------------

@app.route("/api/import/upload", methods=["POST"])
def api_import_upload():
    """Accept statement files, parse, label with book and type, return preview.

    Accepts multipart form data with one or more files.
    Returns grouped preview by account with each row's label status.
    """
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
            safe_name = f.filename
            save_path = os.path.join(tmpdir, safe_name)
            f.save(save_path)
            saved_paths.append((save_path, safe_name))
            filenames.append(safe_name)

        # Detect and parse each file via parser registry
        from parsers import auto_detect_and_parse, handle_vantage_split
        parsed_statements = []
        for save_path, filename in saved_paths:
            try:
                stmts = auto_detect_and_parse(save_path)
                parsed_statements.extend(stmts)
            except Exception as e:
                errors.append({"file": filename, "error": str(e)})

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

        for stmt in parsed_statements:
            for tx in stmt.transactions:
                account = tx.card_info or stmt.accounts[0] if stmt.accounts else "Unknown"

                # Merchant rules first; for bank statements, then the PayNow wording
                found = _label_for(conn, tx.description, tx.amount_sgd)
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

                # Classify flow_type post-parse, from the row's own wording,
                # the account it is on and what the merchant rules made of it
                tx.flow_type, other_side_id = flow.classify_row(
                    {
                        "description": tx.description,
                        "amount_sgd": tx.amount_sgd,
                        "service_id": svc_id,
                        "labelled": bool(type_id or svc_id),
                        **account_facts[account],
                    },
                    flow_ctx,
                )

                entry = {
                    "date": tx.date,
                    "description": tx.description,
                    "amount_sgd": tx.amount_sgd,
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
                    # Default-skip: transfers + CC payments (non-spend events)
                    "_skip": tx.flow_type in ("transfer", "payment"),
                }

                if account not in all_groups:
                    all_groups[account] = []
                all_groups[account].append(entry)

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
            "transactions": txns,
            "typed": group_typed,
            "untyped": group_untyped,
            "skipped": group_skip,
            "total": len(txns),
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
                        "_skip": false
                    }, ...
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
    that is no account, refuses the whole import before anything is written.
    """
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
            # A new merchant's book is the one given, or the one its type
            # proposes; a type that proposes none has to be given one.
            for ns in new_services:
                _book_or_proposed(conn, ns.get("book") or None, ns.get("type_id") or None)
        except (UnknownLabel, BookNeeded, flow.UnknownFlow) as e:
            return jsonify({"error": str(e)}), 400

    total_saved = 0
    total_duplicates = 0
    accounts_created = []
    rules_skipped_generic = 0

    with get_db() as conn:
        try:
            for group in groups:
                account_name = group["account"]
                txns = group["transactions"]

                # Filter out skipped transactions
                active_txns = [t for t in txns if not t.get("_skip", False)]
                if not active_txns:
                    continue

                # Ensure account exists
                from ingest import ensure_account, ensure_statement
                stmt_type = _kind_from_account_name(account_name)

                account_id = ensure_account(conn, account_name, stmt_type)
                accounts_created.append(account_name)

                # Create per-month statement records so coverage matrix reflects each month.
                # A multi-month CSV (e.g. Citi Oct-Dec) creates 3 statement records.
                filename = f"import_{import_id}_{account_name[:30]}"
                month_stmt_ids: dict[str, int] = {}
                for tx in active_txns:
                    ym = tx["date"][:7] if tx.get("date") else datetime.now().strftime("%Y-%m")
                    if ym not in month_stmt_ids:
                        stmt_date = f"{ym}-01"
                        sid, _ = ensure_statement(conn, account_id, stmt_date, filename)
                        month_stmt_ids[ym] = sid

                # --- Deduplication ---
                # Group import transactions by (date, description, amount) to handle
                # genuine same-day repeats (e.g. two identical coffees).
                # For each group, count how many already exist in DB for this account.
                # Only insert (import_count - existing_count), minimum 0.
                from collections import Counter

                import_counts = Counter()
                import_by_key = {}  # key -> [list of tx dicts]
                for tx in active_txns:
                    key = (tx["date"], tx["description"], tx["amount_sgd"])
                    import_counts[key] += 1
                    import_by_key.setdefault(key, []).append(tx)

                duplicates_skipped = 0
                for key, import_count in import_counts.items():
                    date_val, desc_val, amount_val = key

                    # Count existing matches for this account
                    existing = conn.execute(
                        """SELECT COUNT(*) FROM transactions t
                           JOIN statements s ON t.statement_id = s.id
                           WHERE s.account_id = ?
                             AND t.date = ? AND t.description = ? AND t.amount_sgd = ?""",
                        (account_id, date_val, desc_val, amount_val),
                    ).fetchone()[0]

                    # Only insert the net-new ones
                    to_insert = max(0, import_count - existing)
                    skipped = import_count - to_insert
                    duplicates_skipped += skipped

                    # Link each transaction to its month's statement record
                    for tx in import_by_key[key][:to_insert]:
                        tx_month = tx["date"][:7] if tx.get("date") else datetime.now().strftime("%Y-%m")
                        statement_id = month_stmt_ids[tx_month]
                        # A waiting row the operator gave a type or a merchant
                        # in the preview is labelled: it no longer waits.
                        tx_flow = tx.get("flow_type")
                        if tx_flow == flow.REVIEW and (tx.get("type_id") or tx.get("service_id")):
                            tx_flow = "income" if tx["amount_sgd"] < 0 else "expense"
                        conn.execute(
                            "INSERT INTO transactions "
                            "(statement_id, date, description, amount_sgd, amount_foreign, "
                            "currency_foreign, book, type_id, service_id, "
                            "is_one_off, cat_source, flow_type, other_side_id) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                statement_id,
                                tx["date"],
                                tx["description"],
                                tx["amount_sgd"],
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
                        total_saved += 1

                total_duplicates += duplicates_skipped
                conn.commit()

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
            conn.commit()

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
            conn.commit()
            invalidate_rules_cache()

            # Update batch_imports record
            result_summary = {
                "transactions_saved": total_saved,
                "duplicates_skipped": total_duplicates,
                "accounts": accounts_created,
                "rules_added": rules_added,
                "services_created": services_created,
                "rules_skipped_generic": rules_skipped_generic,
            }
            conn.execute(
                "UPDATE batch_imports SET status = 'committed', result_json = ? WHERE id = ?",
                (json.dumps(result_summary), import_id),
            )
            conn.commit()

            return jsonify({
                "success": True,
                "transactions_saved": total_saved,
                "duplicates_skipped": total_duplicates,
                "rules_added": rules_added,
                "accounts": accounts_created,
                "rules_skipped_generic": rules_skipped_generic,
            })

        except Exception as e:
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

@app.route("/api/rules")
def api_rules():
    """List all merchant rules with their service, and the book and type each
    gives a row: its own override where it has one, else the merchant's."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT mr.id, mr.pattern, mr.match_type, mr.confidence,
                   mr.priority, mr.min_amount, mr.max_amount,
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
    return jsonify([{
        "id": r["id"],
        "pattern": r["pattern"],
        "match_type": r["match_type"],
        "confidence": r["confidence"],
        "priority": r["priority"],
        "min_amount": r["min_amount"],
        "max_amount": r["max_amount"],
        "service_id": r["service_id"],
        "service_name": r["service_name"],
        "book_override": r["book_override"],
        "type_override_id": r["type_override_id"],
        "book": r["book"],
        "type_id": r["type_id"],
        "type_name": r["type_name"],
        "parent_type": r["parent_type"],
        "display_type": format_type_display(r["parent_type"], r["type_name"]),
    } for r in rows])


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
        except UnknownLabel as e:
            return jsonify({"error": str(e)}), 400
        return _crud_insert(
            conn,
            "INSERT INTO merchant_rules (pattern, service_id, book_override, type_override_id, "
            "match_type, confidence, priority, min_amount, max_amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["pattern"],
                data["service_id"],
                book_override,
                type_override_id,
                data.get("match_type", "contains"),
                "confirmed",
                data.get("priority", 0),
                data.get("min_amount"),
                data.get("max_amount"),
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
         "priority", "min_amount", "max_amount"],
    )
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
                SELECT id, description, amount_sgd, flow_type_manual
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
                            conn, tx["description"], tx["amount_sgd"], flow_ctx=flow_ctx,
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
            SELECT id, description, amount_sgd, book, type_id, service_id, cat_source,
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
            found = _label_for(conn, tx["description"], tx["amount_sgd"])
            new_label = (found["book"], found["type_id"], found["service_id"], found["cat_source"])
            old_flow = (tx["flow_type"], tx["other_side_id"])
            new_flow = old_flow
            if not tx["flow_type_manual"]:
                if flow_ctx is None:
                    flow_ctx = flow.build_context(conn)
                new_flow = _classify_flow_for_tx(
                    conn, tx["description"], tx["amount_sgd"], flow_ctx=flow_ctx,
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
# Review list: the transfers waiting for a label
# ---------------------------------------------------------------------------

@app.route("/api/review")
def api_review():
    """How many transfers are waiting, and the choices a label can be, from
    their one declaration. The waiting rows themselves are the transaction
    list with flow=review."""
    with get_db() as conn:
        waiting = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE flow_type = ?", (flow.REVIEW,)
        ).fetchone()[0]
    return jsonify({
        "waiting": waiting,
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
            "SELECT t.id, t.amount_sgd, s.account_id FROM transactions t "
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
                if tx["amount_sgd"] < 0:
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


# ---------------------------------------------------------------------------
# Subscriptions API
# ---------------------------------------------------------------------------

# FX rate cache
_fx_cache = {"rate": 1.35, "fetched_at": None}


def _get_usd_sgd_rate() -> float:
    """Get current USD→SGD rate with hourly caching."""
    import time
    now = time.time()
    if _fx_cache["fetched_at"] and (now - _fx_cache["fetched_at"]) < 3600:
        return _fx_cache["rate"]
    try:
        import urllib.request
        with urllib.request.urlopen("https://open.er-api.com/v6/latest/USD", timeout=5) as resp:
            data = json.loads(resp.read())
            rate = data["rates"]["SGD"]
            _fx_cache["rate"] = rate
            _fx_cache["fetched_at"] = now
            return rate
    except Exception:
        return _fx_cache["rate"]  # fallback


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
                   t.id as tx_id, t.date as tx_date, t.amount_sgd,
                   ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY t.date DESC) as rn
            FROM subscriptions s
            JOIN transactions t
                ON UPPER(t.description) LIKE '%' || UPPER(s.match_pattern) || '%'
            LEFT JOIN services svc ON s.service_id = svc.id
            WHERE s.match_pattern IS NOT NULL AND s.match_pattern != ''
              AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund')
              AND """ + book_expr("t") + " = " + book_expr("svc") + """
        )
        SELECT sub_id, tx_id, tx_date, amount_sgd
        FROM matched WHERE rn = 1
        """).fetchall()
        latest_tx = {r["sub_id"]: dict(r) for r in latest_tx_rows}

        # Batch enrichment: monthly sums per subscription for 90-day rolling avg
        # Same book boundary as latest tx query.
        monthly_rows = conn.execute("""
            SELECT s.id as sub_id,
                   SUBSTR(t.date, 1, 7) as ym,
                   SUM(t.amount_sgd) as month_total
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

    # Build lookup: sub_id → [{ym, month_total}, ...] ordered by ym DESC
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
                d["tx_amount"] = round(tx["amount_sgd"], 2)
                d["tx_id"] = tx["tx_id"]
            d["tx_months_90d"] = len(monthly_sums)
            if monthly_sums:
                totals = [m["month_total"] for m in monthly_sums]
                d["tx_avg_90d"] = round(sum(totals) / len(totals), 2)

        # Last paid amount: latest month's sum (handles split payments)
        if pat and d.get("tx_months_90d"):
            d["tx_amount"] = round(monthly_sums[0]["month_total"], 2)

        # Billed = configured amount per cycle (source of truth)
        amt = d.get("amount") or 0
        cur = d.get("currency") or "SGD"

        # Monthly equivalent: use 90d avg if variable (>10% variance), else configured
        d["is_variable"] = False
        if d["tx_avg_90d"] and d["tx_months_90d"] >= 2:
            totals = [m["month_total"] for m in monthly_sums]
            mn, mx = min(totals), max(totals)
            if mx > mn * 1.10:
                d["is_variable"] = True
                d["monthly_sgd"] = round(d["tx_avg_90d"], 2)
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
                SELECT t.date, t.amount_sgd
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


@app.route("/api/fx-rate")
def api_fx_rate():
    """Get current USD→SGD exchange rate."""
    return jsonify({"usd_sgd": _get_usd_sgd_rate()})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="fin — Personal Finance Tracker")
    parser.add_argument("--port", type=int, default=8450)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    init_db()
    print(f"fin running at http://localhost:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)
