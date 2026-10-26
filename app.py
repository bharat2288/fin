"""fin — Personal Finance Tracker

Flask backend serving the 4-tab SPA (Dashboard, Import, History, Merchant Rules)
and API endpoints for statement parsing, labelling (book and type), and visualization.

Usage:
    py app.py                  # Start on port 8450
    py app.py --port 8450      # Explicit port
"""

import calendar
import functools
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from flask import Flask, Response, g, jsonify, request
from werkzeug.utils import secure_filename

import access_gate
import account_kind
import anchors
import backup
import balance_sheet
import book_prefill
import bills
import book_type
import card_balance
import chat_writes
import db
import expect_back
import flow
import history
import label_source
import loan_interest
import loan_terms
import mcp_tools
import money
import month_check
import name_wording
import pairing
import party
import rates
import review
import screens
import shared
import statement_period
import suggest
import tie
import trend_routes
import expect_back
import trend_series
import valuation
import expect_routes
import folio_link
import expectation_api
import refund_links
import relabel_preview
from scope import _HOUSEHOLD_ACCOUNT, _TYPE_JOINS, book_expr
from missing import missing_months
from db import first_rule, get_connection, get_db, init_db, invalidate_rules_cache, match_merchant, rule_label


app = Flask(__name__, static_folder="static", static_url_path="/static")
# LOCAL_DEV: off unless a caller turns it on (python app.py, serve.py in
# local-dev); without the access gate's identity every request is then refused
# (fin-online D1, the second layer). BACKUP_STORE: the nightly backup's object
# store, set by serve.py; None means backups are not configured.
app.config.update(LOCAL_DEV=False, BACKUP_STORE=None)
app.wsgi_app = access_gate.RequireGateIdentity(app.wsgi_app, app.config)
app.register_blueprint(trend_routes.bp)
app.register_blueprint(expect_routes.bp)
app.register_blueprint(folio_link.bp)


def format_type_display(parent: str | None, child: str | None) -> str:
    """Format a type as 'Parent > Child', or the bare name with no parent."""
    return book_type.display_name(child, parent) if child else ""


# What the charts call spending rows nobody has given a type.
NO_TYPE_LABEL = "No type"
# The same rows, as the transaction list's type filter names them.
UNTYPED_FILTER = "__untyped__"
# Which rows those are: spending and refund rows with no type. The list's
# filter and the Queue's guesses read both use it, so they list the same rows.
UNTYPED_ROWS = "(t.type_id IS NULL AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund'))"
# Which rows wait to be checked (Check each row, fin-surfaces 12): their
# merchant is set to check each row (services.review_each_time) and neither
# field of theirs is checked or chosen (label_source.waits_sql, personal-
# ledger 24). Only a typed spending or refund row: an untyped one is already
# the Queue's "no type" item and a transfer waiting for review its "This
# was…" item, so the same row is never listed twice. LABELLED_ROWS is the
# kind of row that can wait; CHECKABLE_ROWS is the row half: the rows that
# wait, or would once the flag is on. `svc` is the row's merchant. The
# Queue's step, the not-checked mark and the setting read them.
LABELLED_ROWS = "(COALESCE(t.flow_type, 'expense') IN ('expense', 'refund') AND t.type_id IS NOT NULL)"
CHECKABLE_ROWS = f"({label_source.waits_sql('t')} AND {LABELLED_ROWS})"
NOT_CHECKED_ROWS = f"(COALESCE(svc.review_each_time, 0) = 1 AND {CHECKABLE_ROWS})"


# Expect back (personal-ledger 23, ticket 30). A row whose own part is 0 (all
# of it expected back, or wholly placed against what is) is accounted for: it
# leaves the review list, the untyped and check-each-row lists and the Queue,
# whatever its flow, and its flow is left as it was.
def _left_to_label(predicate: str) -> str:
    """`predicate` narrowed to rows not accounted for."""
    return f"({predicate} AND {expect_back.own_sql('t')} != 0)"


def _untyped_rows() -> str:
    return _left_to_label(UNTYPED_ROWS)


def _not_checked_rows() -> str:
    return f"(COALESCE(svc.review_each_time, 0) = 1 AND {_left_to_label(CHECKABLE_ROWS)})"


def _waiting_rows() -> tuple[str, list]:
    """Which rows wait for review, and its parameters: the transfers with no
    label that have something left of their own, and also a movement naming
    a person that is partly placed or expected, with money left on it to
    label (the conversion's excess, case 43)."""
    on_record = ("(EXISTS (SELECT 1 FROM set_off so WHERE so.payment_row_id = t.id)"
                 " OR EXISTS (SELECT 1 FROM expectation e WHERE e.row_id = t.id))")
    person = "EXISTS (SELECT 1 FROM accounts o WHERE o.id = t.other_side_id AND o.type = 'person')"
    return (_left_to_label(f"(t.flow_type = ? OR (t.flow_type = ? AND {person} AND {on_record}))"),
            [flow.REVIEW, flow.MOVEMENT])


def _parts_pill(amount_minor: int, expected_minor: int, placed_minor: int, waiting_minor: int,
                currency: str) -> str | None:
    """What a row's pill says of its parts: 'S$ 200 expected back',
    'S$ 5,000 to pay back', 'S$ 40 set off', 'S$ 100 waiting for its row';
    None for a row with nothing on it."""
    words = []
    if expected_minor:
        words.append(f"{expect_back.short(abs(expected_minor), currency)} "
                     f"{'to pay back' if amount_minor < 0 else 'expected back'}")
    against = abs(placed_minor) - waiting_minor
    if against:
        words.append(f"{expect_back.short(against, currency)} set off")
    if waiting_minor:
        words.append(f"{expect_back.short(waiting_minor, currency)} waiting for its row")
    return " · ".join(words) or None


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
    return book_type.proposed_book(conn, type_id)


def _new_checks_each_row(conn, type_id: int | None) -> int:
    """A new merchant's Check each row flag, from its type's setting
    (type-relationships 02): 1 when the type says check each row."""
    return 1 if book_type.checks_each_row(conn, type_id) else 0


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


def _looks_transfer_like_description(description: str | None) -> bool:
    """Return True when a raw description is rail-like, not merchant-like."""
    normalized = db.normalize_pattern_text(description)
    if not normalized:
        return False
    if any(token in normalized for token in ("PAYNOW", "PAYLAH", "I-BANK", ":IB")):
        return True
    if re.match(r"^FT\d+[A-Z0-9-]*", normalized):
        return True
    return False


def _rule_pattern_error(pattern: str | None) -> str | None:
    """Return a user-facing validation error for unsafe rule patterns."""
    if db.is_generic_rule_pattern(pattern):
        return (
            "Pattern is too generic for PayNow/transfer traffic. "
            "Leave it blank for a one-off resolution or use a specific counterparty name."
        )
    return None


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
        "SELECT currency FROM accounts WHERE id = ?", (find_account(conn, account_name, stated),)
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
    """What the rules make of a row: the result of match_merchant. With each
    field's source (book_source, type_source): from the merchant or the rule
    that gave it; none for a field left empty. And `rule_facts`: the flow
    and other side the rule states, as classify_row reads them (none when it
    states none). The PayNow payee wordings are ordinary rules now
    (type-relationships 05); a row the wording labelled before keeps its
    wording source."""
    found = match_merchant(description, conn, amount_minor=amount_minor)
    rule = first_rule(description, conn, amount_minor) if found["service_id"] is not None else None
    book_source, type_source = label_source.rule_sources(rule) if rule else (None, None)
    found["book_source"] = label_source.given(found["book"], book_source or label_source.MERCHANT)
    found["type_source"] = label_source.given(found["type_id"], type_source or label_source.MERCHANT)
    # The flow and other side the rule states, if it states one (ADR-009).
    found["rule_facts"] = flow.rule_facts(rule)
    return found


def _tie_line(conn, stmt, figures: dict) -> dict:
    """The tie line of a parsed statement as the import preview shows it:
    opening, what the rows add up to (and money in and out apart), closing
    and the difference, each in whole minor units and as text, and whether
    it ties."""
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
        # The other accounts its printed statement carried (parse_dbs.by_account).
        "siblings": list(getattr(stmt, "siblings", [])),
        "status": "ties" if figures["difference_minor"] == 0 else "off",
        **figures,
        "opening": anchors.format_amount(figures["opening_minor"], currency),
        "rows_sum": anchors.format_amount(figures["rows_minor"], currency),
        "closing": anchors.format_amount(figures["closing_minor"], currency),
        "difference": anchors.format_amount(abs(figures["difference_minor"]), currency),
        # The rows kept apart, each a positive amount: money in and money out.
        "in_minor": -sum(t.amount_minor for t in stmt.transactions if t.amount_minor < 0),
        "out_minor": sum(t.amount_minor for t in stmt.transactions if t.amount_minor > 0),
    }


def _statement_results(conn, statement_lines: dict, errors: list, groups: list) -> list:
    """Each statement of an upload and what it came to: a statement that ties,
    one refused for being off, and an account's rows from a source that
    states no balance (not checked). Kept on the import (Past imports)."""
    from ingest import find_account

    def account_id(name: str, currency=None):
        try:
            return find_account(conn, name, currency)
        except Exception:
            return None

    out = []
    for name, lines in statement_lines.items():
        for line in lines:
            out.append({"account": mask_card_number(name), "account_id": account_id(name, line.get("currency")),
                        "date": line.get("closing_date"), "status": line["status"],
                        "difference_minor": line.get("difference_minor"), "currency": line.get("currency")})
    for e in errors:
        line = e.get("tie")
        if line:
            out.append({"account": line["account"], "account_id": account_id(line["account"], line.get("currency")),
                        "date": line.get("closing_date"), "status": "off",
                        "difference_minor": line.get("difference_minor"), "currency": line.get("currency")})
    for g in groups:
        if g["tie"] != "ties" and g["total"]:
            out.append({"account": g["account"], "account_id": account_id(g["account"], g.get("currency")),
                        "date": None, "status": "not_checked", "difference_minor": None,
                        "currency": g.get("currency")})
    return out


def _record_refused(conn, stmt, line: dict) -> None:
    """Keep a statement refused at upload (screens.py), and commit."""
    from ingest import find_account
    account = stmt.accounts[0] if stmt.accounts else "Unknown"
    screens.record_refused(
        conn,
        account_name=mask_card_number(account),
        account_id=find_account(conn, account, line["currency"]),
        statement_date=stmt.closing_date or stmt.statement_date,
        currency=line["currency"],
        figures=line,
    )
    conn.commit()


class AnchorRefused(Exception):
    """A statement's closing balance differs from the anchor already held for
    its account and date."""


def _loan_figure(conn, stmt) -> dict:
    """What a loan statement (parse_dbs_loan.py) will write, as the preview
    shows it and the confirm writes it: what is owed on its day, as a
    statement's figure on the loan account of its name, made a loan by the
    confirm when there is none. No row. `held` is whether that figure is held
    already (the confirm then writes nothing). Refused (ValueError), with
    nothing read from the file, when an account of that name is no loan, is
    kept in another currency, or holds a different figure for that day.

    A loan with hire-purchase terms (loan_terms.py) reads the statement's
    figure as the total of its remaining instalments: what is owed is the
    principal, that total (plus an instalment it took off ahead of its day)
    less the interest not yet earned, and the figure carries the working
    (printed_minor, ahead_minor, unearned_minor, instalments_left)."""
    account = conn.execute(
        "SELECT id, type FROM accounts WHERE name = ?", (stmt.account,)
    ).fetchone()
    if account is not None and account["type"] != loan_interest.LOAN:
        raise ValueError(
            f"{mask_card_number(stmt.account)} is a {account['type']} account; a loan "
            "statement's figure goes on a loan. Nothing was read from this file."
        )
    _account_currency(conn, stmt.account, stmt.currency)
    terms = None if account is None else loan_terms.held(conn, account["id"])
    worked = {} if terms is None else loan_terms.principal_owed(terms, stmt.owed_minor, stmt.statement_date)
    owed = worked.get("owed_minor", stmt.owed_minor)
    on_day = None if account is None else conn.execute(
        "SELECT amount FROM anchors WHERE account_id = ? AND date = ?",
        (account["id"], stmt.statement_date),
    ).fetchone()
    if on_day is not None and on_day["amount"] != -owed:
        raise ValueError(
            f"{mask_card_number(stmt.account)} already has a figure of "
            f"{anchors.format_amount(-on_day['amount'], stmt.currency)} for {stmt.statement_date}; "
            f"this statement {'comes to' if terms else 'states'} {anchors.format_amount(owed, stmt.currency)}. "
            "A different amount for the same date is refused; nothing was read from this file."
        )
    return {
        "account": stmt.account,
        "kind": loan_interest.LOAN,
        "date": stmt.statement_date,
        "owed_minor": owed,
        "currency": stmt.currency,
        "new_account": account is None,
        "held": on_day is not None,
        **worked,
    }


def _figure_now(conn, loan_id: int, figure: dict) -> int | None:
    """What a previewed loan figure comes to under the terms the loan holds
    now: with terms, principal owed as loan_terms.principal_owed works it
    (the printed total, plus an instalment taken off ahead of its day, less
    the interest not yet earned); the total as printed without; None when the
    terms refuse it."""
    printed = figure.get("printed_minor", figure["owed_minor"])
    terms = loan_terms.held(conn, loan_id)
    if terms is None:
        return printed
    try:
        return loan_terms.principal_owed(terms, printed, figure["date"])["owed_minor"]
    except ValueError:
        return None


def _wording_key(description: str) -> str:
    return " ".join((description or "").upper().split())


def _begins_with(longer: str, shorter: str) -> bool:
    """One wording is the other with more words after it."""
    return bool(shorter) and longer.startswith(shorter) and (
        len(longer) == len(shorter) or longer[len(shorter)] == " ")


# What an older DBS bank read added to an account's last row: the page
# footer, the next account's heading, the bank's messages.
PAGE_JUNK_MARKERS = (
    "TRANSACTION DETAILS AS OF", "ACCOUNT NO.", "MESSAGES FOR YOU", "BALANCE CARRIED FORWARD",
    "SUPPLEMENTARY RETIREMENT SCHEME", ".ON GER ZIB BSOP",
)


def _tidied_wording(held: str, printed: str) -> str | None:
    """The wording a held row takes from the statement printing it again: the
    printed wording, when the held one is it with an older read's page junk
    after it; else None (the held wording stands)."""
    mine, theirs = _wording_key(printed), _wording_key(held)
    if mine == theirs or not _begins_with(theirs, mine):
        return None
    rest = theirs[len(mine):]
    return printed if any(marker in rest for marker in PAGE_JUNK_MARKERS) else None


def _held_for_import(
    conn, import_counts, elsewhere: set[int] = frozenset(), printed_keys: set = frozenset(),
    either_sign: bool = False, others_rows: list = (),
) -> dict:
    """The rows already held that each of an import's rows is, by key
    (account, date, description, amount): [{id, printed, description, amount}],
    oldest first, as many as the import brings of that key at most.

    A row held with the same account, day and amount is the same row when
    its wording is the same, or when one wording begins with the other: a
    bank's CSV export and its PDF statement print one card purchase as
    "SHOPEE SINGAPORE MP SINGAPORE SG" and "SHOPEE SINGAPORE MP" ("SHOP" is
    not "SHOPEE"). Rows the same in wording are claimed first, and of the
    rest the row with fewest held rows it could be goes first, so a held row
    is never two rows' and a row that could only be one is not left out.

    Last, a row no held row of its own account is may be one held on an
    account in `elsewhere` on a month record (never one filed under a printed
    statement): an older read of a statement printing two accounts filed both
    accounts' rows on the first. With `either_sign` (this account is kept in
    another currency than the one it was found on) that row may be held with
    its sign turned: the older read had no balance in that currency to tell
    money in from money out by.

    Then a row of a printed statement (its key in `printed_keys`) is a row
    held on a month record of its own account with the same day and amount,
    whatever the wording, and a row with no statement one held under a
    printed statement: a bank's CSV export and its PDF statement word one
    transfer as "TOP-UP TO PAYLAH! : 97863267" and "Funds Transfer TOP-UP TO
    PAYLAH! : 97863267", a card bill payment as "BILL DBSC-…" and "Advice Bill
    Payment DBSC-…". Each prints every row of the account's day, so a row the
    one holds the other prints too. Of two rows alike in day and amount, the
    one sharing more words is paired first; a held row that a row of another
    account in the same upload words (`others_rows`: day, size, wording) is
    left for that account's misfiled pass. Before that, a printed row of a
    bank account is one held under another printed statement of it when the
    day, the amount and the letters of the wording are the same: two layouts
    of one bank's statement differ in digits and spacing only."""
    found = {key: [] for key in import_counts}
    claimed: set[int] = set()
    candidates = {}
    for key in import_counts:
        account_id, day, _description, amount = key
        if (account_id, day, amount) not in candidates:
            candidates[(account_id, day, amount)] = conn.execute(
                """SELECT t.id, t.description, s.printed, t.amount_minor FROM transactions t
                   JOIN statements s ON t.statement_id = s.id
                   WHERE s.account_id = ? AND t.date = ? AND t.amount_minor = ?
                   ORDER BY t.id""",
                (account_id, day, amount),
            ).fetchall()
    others = ", ".join("?" for _ in elsewhere)
    misfiled = {
        key: conn.execute(
            f"""SELECT t.id, t.description, s.printed, t.amount_minor FROM transactions t
                JOIN statements s ON t.statement_id = s.id
                WHERE s.account_id IN ({others}) AND NOT s.printed AND t.date = ?
                  AND t.amount_minor IN (?, ?)
                ORDER BY t.amount_minor != ?, t.id""",
            (*elsewhere, key[1], key[3], -key[3] if either_sign else key[3], key[3]),
        ).fetchall() if elsewhere else []
        for key in import_counts
    }

    def same(mine: str, theirs: str, loose: bool) -> bool:
        if not loose:
            return theirs == mine
        return _begins_with(theirs, mine) or _begins_with(mine, theirs)

    def could_be(key, loose: bool, rows=None) -> list:
        account_id, day, description, amount = key
        mine = _wording_key(description)
        return [row for row in (candidates[(account_id, day, amount)] if rows is None else rows[key])
                if same(mine, _wording_key(row["description"]), loose)]

    for loose, rows in ((False, None), (True, None), (True, misfiled)):
        keys = list(import_counts)
        if loose:
            keys.sort(key=lambda key: len(could_be(key, loose, rows)))
        for key in keys:
            for row in could_be(key, loose, rows):
                if len(found[key]) >= import_counts[key]:
                    break
                if row["id"] in claimed:
                    continue
                claimed.add(row["id"])
                found[key].append((row["id"], row["printed"], row["description"], row["amount_minor"]))

    # Two printed statements of one bank account over the same days, in
    # different layouts (a month's statement and a range statement), word a
    # row alike but for its digits, spacing and punctuation: "NETBANK, 573573
    # MUM" and "NETBANK,MUM". A printed row is one held under a printed
    # statement of its own bank account with the same day, amount and
    # letters. Each prints every row of the day, so a held row of the day is
    # one of the rows. A card statement does not: a row posted late prints
    # on the next statement under its own earlier day.
    def letters(text: str) -> str:
        return re.sub(r"[^A-Z]", "", (text or "").upper())

    ids = {key[0] for key in import_counts}
    banks = {
        row["id"] for row in conn.execute(
            f"SELECT id FROM accounts WHERE type = 'bank' AND id IN ({', '.join('?' for _ in ids)})", tuple(ids))
    } if ids else set()

    def printed_alike(key) -> list:
        mine = letters(key[2])
        return [row for row in candidates[(key[0], key[1], key[3])]
                if mine and row["printed"] and letters(row["description"]) == mine]

    for key in sorted((key for key in import_counts if key in printed_keys and key[0] in banks),
                      key=lambda k: len(printed_alike(k))):
        for row in printed_alike(key):
            if len(found[key]) >= import_counts[key]:
                break
            if row["id"] in claimed:
                continue
            claimed.add(row["id"])
            found[key].append((row["id"], row["printed"], row["description"], row["amount_minor"]))

    # The other kind of export: every pairing by day and amount, the ones
    # sharing the most words first, so a row is paired with its own wording
    # when two of a day are alike in amount.
    def words(text: str) -> set:
        return set(re.findall(r"[A-Z0-9]+", (text or "").upper()))

    def anothers(key, row) -> bool:
        theirs = _wording_key(row["description"])
        return any(
            day == key[1] and size == abs(key[3]) and (_begins_with(theirs, mine) or _begins_with(mine, theirs))
            for day, size, mine in others_rows
        )

    pairs = sorted(
        (-len(words(key[2]) & words(row["description"])), row["id"], n, key, row)
        for n, key in enumerate(import_counts)
        for row in candidates[(key[0], key[1], key[3])]
        if bool(row["printed"]) != (key in printed_keys) and not anothers(key, row)
    )
    for *_, key, row in pairs:
        if row["id"] in claimed or len(found[key]) >= import_counts[key]:
            continue
        claimed.add(row["id"])
        found[key].append((row["id"], row["printed"], row["description"], row["amount_minor"]))
    return {key: [{"id": i, "printed": p, "description": d, "amount": a} for i, p, d, a in sorted(rows)]
            for key, rows in found.items()}


def _kind_from_account_name(account_name: str) -> str:
    """The kind an import gives an account it has to create, from its name."""
    name = account_name.lower()
    if "bank" in name or "one account" in name or "home" in name:
        return "bank"
    # A DBS or POSB account number, as its statement prints it:
    # 272-814529-7, or its section in another currency: "100-018277-1 usd".
    if re.fullmatch(r"\d{3}-\d{5,6}-\d( [a-z]{3})?", name):
        return "bank"
    return "card"  # default; could detect from account name


def _account_facts(conn, tx_id) -> dict:
    """The account a row is on, its kind and owner, as the classifier reads them."""
    row = conn.execute(
        "SELECT a.id, a.type, a.owner FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id WHERE t.id = ?",
        (tx_id,),
    ).fetchone()
    return {"account_id": row["id"], "account_kind": row["type"], "account_owner": row["owner"]} if row else {}


def _classify_flow_for_tx(
    conn, description: str, amount_minor: int, *,
    flow_ctx=None, tx_id=None, service_id=None, rule_facts: dict | None = None,
) -> tuple[str, int | None]:
    """Classify a row as it is imported, using the shared flow model: (flow,
    its other side).

    tx_id says which account the row is on; service_id is the merchant the
    rules gave it, and rule_facts the flow the rule states, if it states one
    (it says what the row is, ADR-009). A merchant or a type does not take a
    transfer off the review list: a rule that states no flow labels only."""
    if flow_ctx is None:
        flow_ctx = flow.build_context(conn)
    facts = {
        "description": description,
        "amount_minor": amount_minor,
        "service_id": service_id,
        **_account_facts(conn, tx_id),
        **(rule_facts or {}),
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


RULE_PERSON_REFUSED = (
    "A person is never a rule's other side: what is lent to or owed by a person is "
    "marked Expect some back… or Expect to pay back on the row."
)


RULE_ARCHIVED_REFUSED = (
    "An archived account is no rule's other side: a rule says what its new rows are, and no new row "
    "moves money to or from an account the household no longer uses."
)


def _held_side_kept(conn, flow_name, held_side) -> int | None:
    """The other side a rule keeps when its flow changes and no other side is
    sent: the one it holds, where the new flow allows an account of its kind
    (flow.RULE_FLOWS); else none, and a flow that needs one asks for it
    (_checked_rule_flow)."""
    if held_side is None or flow_name not in flow.RULE_FLOWS:
        return None
    account = conn.execute("SELECT type FROM accounts WHERE id = ?", (held_side,)).fetchone()
    return held_side if account is not None and account["type"] in flow.RULE_FLOWS[flow_name][0] else None


def _checked_rule_flow(conn, flow_name, other_side) -> tuple[str | None, int | None]:
    """The flow and other side a merchant rule may state (ADR-009), or
    (None, None): a rule that states no flow labels only. The flow is one of
    fin's, but review (flow.RULE_FLOWS); a transfer and a movement name their
    other side, and the other side is one of the household's accounts of a
    kind the flow allows (a person never is, nor an archived account); a
    flow that names none takes none. A bank is a card payment's other side
    as a card is: the rule's rows may be the card side (flow.FLOWS). Raises
    UnknownLabel; nothing is written."""
    if flow_name is None or flow_name == "":
        if other_side is not None:
            raise UnknownLabel("an other side goes with a flow: say what the rule's rows are")
        return None, None
    try:
        flow.checked_flow(flow_name)
    except flow.UnknownFlow as e:
        raise UnknownLabel(str(e)) from None
    if flow_name == flow.NOT_OURS:
        raise UnknownLabel("a rule cannot say its rows are not ours: that is what a row on a shared "
                           "account is while no rule says what it is (mark the account shared)")
    if flow_name not in flow.RULE_FLOWS:
        raise UnknownLabel("a rule cannot say a row waits for review: that is what a transfer is "
                           "while nothing says what it was")
    kinds, required = flow.RULE_FLOWS[flow_name]
    if other_side is None:
        if required:
            raise UnknownLabel(f"a rule that says its rows are a {flow_name} names its other side "
                               f"(other_side_id): one of the household's accounts of kind {' or '.join(kinds)}")
        return flow_name, None
    if isinstance(other_side, bool) or not isinstance(other_side, int):
        raise UnknownLabel(f"unknown other side: {other_side!r}")
    if not kinds:
        raise UnknownLabel(f"a rule that says its rows are {flow_name} names no other side")
    account = conn.execute("SELECT type, owner, status FROM accounts WHERE id = ?", (other_side,)).fetchone()
    if account is None:
        raise UnknownLabel(f"unknown other side: {other_side!r}")
    why = _rule_side_refusal(flow_name, account)
    if why is not None:
        raise UnknownLabel(why)
    return flow_name, other_side


def _rule_side_refusal(flow_name: str, account) -> str | None:
    """Why an account (its type, owner and status) may not be the other side
    of a rule that says its rows are `flow_name`, or None: a person never is,
    nor an archived account, nor an account of a kind the flow does not
    allow or not the household's (flow.RULE_FLOWS). _checked_rule_flow
    refuses a rule so, and api_accounts_update an account a rule names."""
    kinds = flow.RULE_FLOWS[flow_name][0]
    if account["type"] == "person":
        return RULE_PERSON_REFUSED
    if account["status"] == "archived":
        return RULE_ARCHIVED_REFUSED
    if account["type"] not in kinds or account["owner"] != account_kind.HOUSEHOLD:
        return f"a {flow_name}'s other side is one of the household's accounts of kind " + " or ".join(kinds)
    return None


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
# POSTs that write nothing to the book: no entry, no lock. A read sent as POST
# for its body (a list of row ids), and "back up now", which copies the book
# and writes its status to a file beside it, as the nightly run does (it holds
# neither): an entry open while it copies would be in the copy.
_READ_ONLY_POSTS = {"api_book_prefill", "api_backups_run"}

# What each writing route did, in words, for the history's list.
_CHANGE_SUMMARIES = {
    "api_accounts_create": "Added an account",
    "api_accounts_update": "Changed an account",
    # Named by the route: joined to a party, or taken off one (party.JOINED, party.LEFT).
    "api_accounts_party": "Changed an account's party",
    # Named by the route: marked shared, or not (shared.MARKED, shared.UNMARKED).
    "api_accounts_shared": "Changed whether an account is shared",
    "api_accounts_delete": "Deleted an account",
    "api_anchors_create": "Entered a figure",
    "api_anchors_replace": "Corrected a figure",
    "api_anchors_delete": "Deleted a figure",
    "api_loan_interest": "Worked out loan interest",
    "api_services_bulk_rename": "Renamed merchants",
    "api_services_create": "Added a merchant",
    "api_services_update": "Changed a merchant",
    "api_services_check_each_row": "Changed whether a merchant's rows are checked",
    "api_bulk_label": "Labelled rows by hand",
    "api_services_merge": "Merged merchants",
    "api_services_delete": "Deleted a merchant",
    "api_update_transaction": "Changed a row's labels",
    "api_resolve_transaction": "Gave a row a merchant and type",
    "api_plans_apply": "Applied a plan",
    "api_import_upload": "Read a statement for import",
    "api_import_confirm": "Imported a statement",
    "api_rules_create": "Added a merchant rule",
    "api_rules_update": "Changed a merchant rule",
    "api_rules_delete": "Deleted a merchant rule",
    "api_rules_drift_apply": "Relabelled the rows that differed from their rule",
    "api_rates_fetch": "Fetched an exchange rate",
    "api_rates_fetch_missing": "Fetched the missing exchange rates",
    "api_rates_overwrite": "Overwrote an exchange rate",
    # Any row, not only a transfer waiting: "Change what this was…" sends one too.
    "api_review_label": "Said what a row was",
    "api_pair_matching": "Paired transfers between own accounts",
    "api_subscriptions_create": "Added a subscription",
    "api_subscriptions_update": "Changed a subscription",
    "api_subscriptions_delete": "Deleted a subscription",
    "api_subscriptions_enrich": "Filled in subscriptions",
    "api_bills_currency": "Put a bill in its paying account's currency",
    "api_history_undo": "Undid a change",
    # Each names its type in its own summary; these are the fallback.
    "api_types_create": "Added a type",
    "api_types_change": "Changed a type",
    "api_type_slots_move": "Moved one of fin's own filings",
}
_CHANGE_SUMMARIES.update(expect_routes.SUMMARIES)
_CHANGE_SUMMARIES.update({f"{folio_link.bp.name}.{name}": words for name, words in folio_link.SUMMARIES.items()})


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
    if request.method not in _WRITE_METHODS or request.endpoint in _READ_ONLY_POSTS:
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
        # Who undid what, and what each change did in short, so a line can
        # say it in words (the walk's C3.1, C6.2, C9.1).
        by_id = {e["id"]: e for e in shown}
        for e in shown:
            e["first"] = history.brief(conn, e["id"])
            if e["first"]:
                _change_currencies(conn, [e["first"]])
            for key, other_id in (("undone_by_who", e["undone_by"]), ("undoes_who", e["undoes"])):
                other = by_id.get(other_id) if other_id else None
                if other_id and other is None:
                    found = conn.execute("SELECT via, actor, at FROM change_entries WHERE id = ?", (other_id,)).fetchone()
                    other = dict(found) if found else None
                e[key] = None if other is None else {"via": other["via"], "actor": other["actor"], "at": other["at"]}
        if request.args.get("blockers") == "1":
            # What would refuse each entry's undo, named, so the list can say
            # so before the operator taps it.
            for e in shown:
                later = history.blocker(conn, e["id"]) if e["undone_by"] is None else None
                e["blocked_by"] = None if later is None else {
                    "id": later["id"], "summary": later["summary"], "at": later["at"],
                    "via": later["via"], "actor": later["actor"],
                    "first": history.brief(conn, later["id"]),
                }
                if e["blocked_by"] and e["blocked_by"]["first"]:
                    _change_currencies(conn, [e["blocked_by"]["first"]])
        return jsonify({"entries": shown, "many_rows": mcp_tools.MANY_ROWS,
                        **screens.since_looked(conn)})


def _change_currencies(conn, changes: list[dict]) -> None:
    """Give each changed row the currency its amounts are in (and a bank
    row its account_id, from its statement): a row's and a
    figure's are their account's, an account's its own, a bill's its own.
    A statement removed by the same entry still names its account there.
    A type is also given its display name."""
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
            c["account_id"] = account_id
        elif c["table"] in ("anchors", "statements"):
            currency = account_currency(row.get("account_id"))
        elif c["table"] in ("accounts", "subscriptions"):
            currency = row.get("currency")
        elif c["table"] == "types":
            # A type is named as the screens name it ("Kids > Childcare"),
            # its parent read from the book: a type is never re-parented.
            parent = conn.execute(
                "SELECT name FROM types WHERE id = ?", (row.get("parent_id"),)
            ).fetchone() if row.get("parent_id") is not None else None
            c["display_name"] = format_type_display(parent[0] if parent else None, row.get("name"))
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
# The "Claude may write" switch (chat_writes.py)
# ---------------------------------------------------------------------------

CHAT_WRITES_NOT_BOOLEAN = "enabled must be true or false"
CHAT_WRITES_ON_ONLY_IN_APP = "Claude's writes can be turned back on only in fin's app."
CHAT_WRITES_NOT_SAVED = "The switch could not be saved; it is unchanged."


@app.route("/api/chat-writes", methods=["GET"])
def api_chat_writes():
    """Whether Claude may write: {enabled, changed_at, changed_by}, and
    unreadable: true when the switch's file could not be read (then off)."""
    return jsonify(chat_writes.read())


@app.route("/api/chat-writes", methods=["PUT"])
def api_chat_writes_set():
    """Turn Claude's writes on or off: {"enabled": true|false}, a JSON
    boolean. A chat client (or the upload credential, which writes as one)
    may turn them off, never on (fin-surfaces 01, Off switches); the gate
    keeps chat logins off app routes, and this route refuses it as well."""
    data = request.get_json(silent=True)
    on = data.get("enabled") if isinstance(data, dict) else None
    if not isinstance(on, bool):
        return jsonify({"error": CHAT_WRITES_NOT_BOOLEAN}), 400
    # Read raw, not through _caller (which reads anything unknown as the
    # app): only a request the front door stamped as the app turns it on.
    if on and request.environ.get(access_gate.VIA_ENVIRON_KEY, history.VIA_APP) != history.VIA_APP:
        return jsonify({"error": CHAT_WRITES_ON_ONLY_IN_APP}), 403
    _via, actor = _caller()
    try:
        state = chat_writes.write(on, actor)
    except OSError:
        app.logger.warning("the chat-writes switch could not be saved")
        return jsonify({"error": CHAT_WRITES_NOT_SAVED}), 500
    return jsonify(state)


# ---------------------------------------------------------------------------
# What the screens keep (screens.py): where the
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
    # The one switch (chat_writes.py): off, or unreadable, refuses.
    if chat_writes.enabled():
        return None
    return jsonify({"error": screens.IMPORTS_OFF}), 403


@app.route("/api/settings")
def api_settings():
    """The history mark Home counts from. The "Claude may write" switch is
    its own route, /api/chat-writes (chat_writes.py)."""
    with get_db() as conn:
        return jsonify(screens.since_looked(conn))


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

# The browser keeps fin's CSS and JS until a file changes (speed-pass 01). The
# page names each file with ?v=<a short hash of its bytes>; a request carrying
# the file's own version may be kept a year, since a changed file gets a new
# URL. The page itself, and a file asked for without its version (an old tab, a
# bookmark), are revalidated every time. The hashes are redone only when a
# file's size or modified time moves, so an edit under the running dev server
# (whose reloader watches .py files only) shows on the next reload. Kept by the
# browser only ("private"): the files sit behind the access gate when hosted.
# The repo's index.html stays plain; the versions are written in as it is served.

_STATIC_REF = re.compile(r'((?:src|href)="/static/)([^"?]+)(")')
_KEEP_A_YEAR = "private, max-age=31536000, immutable"


def _static_stamp(folder: str) -> tuple:
    """(name, size, modified ns) for every file in the folder: what the hashes are keyed on."""
    return tuple(sorted(
        (e.name, e.stat().st_size, e.stat().st_mtime_ns) for e in os.scandir(folder) if e.is_file()
    ))


def _static_versions(folder: str) -> dict[str, str]:
    return _hashed_versions(folder, _static_stamp(folder))


@functools.lru_cache(maxsize=4)
def _hashed_versions(folder: str, stamp: tuple) -> dict[str, str]:
    """{file name: first 10 hex of its sha256} for every file in the folder."""
    versions = {}
    for name, *_ in stamp:
        with open(os.path.join(folder, name), "rb") as f:
            versions[name] = hashlib.sha256(f.read()).hexdigest()[:10]
    return versions


def _versioned_page(folder: str) -> str:
    return _stamped_page(folder, _static_stamp(folder))


@functools.lru_cache(maxsize=4)
def _stamped_page(folder: str, stamp: tuple) -> str:
    versions = _hashed_versions(folder, stamp)
    with open(os.path.join(folder, "index.html"), encoding="utf-8") as f:
        html = f.read()

    def stamp(m):
        version = versions.get(m.group(2))
        return m.group(0) if version is None else f"{m.group(1)}{m.group(2)}?v={version}{m.group(3)}"

    return _STATIC_REF.sub(stamp, html)


@app.after_request
def _static_lifetime(response):
    # Kept a year only under the file's own version: a page from before a
    # deploy asking after it gets today's bytes, which must not be pinned.
    if request.endpoint == "static":
        name = (request.view_args or {}).get("filename")
        current = _static_versions(app.static_folder).get(name)
        kept = current is not None and request.args.get("v") == current
        response.headers["Cache-Control"] = _KEEP_A_YEAR if kept else "no-cache"
    return response


@app.route("/")
def index():
    page = _versioned_page(app.static_folder)
    response = Response(page, mimetype="text/html")
    response.headers["Cache-Control"] = "no-cache"
    response.set_etag(hashlib.sha256(page.encode("utf-8")).hexdigest()[:16])
    return response.make_conditional(request)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

_TYPES_SELECT = (
    "SELECT t.id, t.kind, t.name, t.parent_id, p.name as parent_name, "
    "t.default_one_off, t.covers, t.not_for, k.key, s.usual_book, s.check_each_row "
    "FROM types t LEFT JOIN types p ON t.parent_id = p.id "
    "LEFT JOIN type_keys k ON k.type_id = t.id "
    "LEFT JOIN type_settings s ON s.type_id = t.id "
)


def _type_shown(r) -> dict:
    """One type as the screens and the chat's types tool read it."""
    spending = r["kind"] == book_type.SPENDING
    shown = format_type_display(r["parent_name"], r["name"])
    usual_book, check_each_row = book_type.settings_of(r["usual_book"], r["check_each_row"], r["key"])
    return {
        "id": r["id"],
        "kind": r["kind"],
        "name": r["name"],
        "parent_id": r["parent_id"],
        "parent_name": r["parent_name"],
        "display_name": shown,
        "default_one_off": r["default_one_off"],
        "covers": r["covers"],
        "not_for": r["not_for"],
        # Its usual book: a book, or "ask" (type-relationships 02); and the
        # book it proposes for a merchant no rule knows, null = ask.
        "usual_book": usual_book if spending else None,
        "proposed_book": book_type.proposed(usual_book) if spending else None,
        # Whether a new merchant of this type starts as Check each row.
        "check_each_row": check_each_row if spending else False,
        # Its name cannot be changed here: Other's meaning is fixed, and an
        # income kind is set in code. Any other spending type is renamed in
        # the app (type-relationships 04).
        "locked": (r["key"] == book_type.NOT_PLACED) if spending else True,
        # A spending type with no key is not declared: it was added in fin.
        "added_in_fin": spending and r["key"] is None,
    }


@app.route("/api/types")
def api_types():
    """The type list as a flat list with parent info: spending types, or with
    ?kind=income the income kinds. A new book's list is filled from the
    declaration in book_type.py; once a book exists the table is the home of
    each type's wording, and the operator may add a spending type or fix one's
    wording here (fin RUL-002)."""
    kind = book_type.INCOME if request.args.get("kind") == book_type.INCOME else book_type.SPENDING
    with get_db() as conn:
        rows = conn.execute(_TYPES_SELECT + "WHERE t.kind = ? ORDER BY t.id", (kind,)).fetchall()
    return jsonify([_type_shown(r) for r in rows])


def _name_change(conn, words: str) -> None:
    """Name this request's change in Changes, unless its caller already named it."""
    entry_id = g.get("change_entry")
    if entry_id is not None:
        conn.execute("UPDATE change_entries SET summary = ? WHERE id = ? AND summary = ''", (words, entry_id))


@app.route("/api/types", methods=["POST"])
def api_types_create():
    """Add a spending type: name, parent_id (a top-level spending type, or
    none), one_off (the running / one-off default), covers, not_for. Refused,
    writing nothing, with the reason in words."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "send the type as JSON"}), 400
    with get_db() as conn:
        try:
            type_id = book_type.add_spending_type(conn, data)
        except book_type.TypeRefused as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 400
        r = conn.execute(_TYPES_SELECT + "WHERE t.id = ?", (type_id,)).fetchone()
        _name_change(conn, f"Added the type {format_type_display(r['parent_name'], r['name'])}")
        conn.commit()
    return jsonify({"type": _type_shown(r)}), 201


def _type_change_words(changed: dict) -> list[str]:
    """What a type change did, in the words Changes shows: the settings, then
    the wording."""
    words = []
    if "usual_book" in changed:
        book = changed["usual_book"]
        words.append("usual book: ask" if book == book_type.ASK else f"usual book {book}")
    if "one_off" in changed:
        words.append("one-off by default" if changed["one_off"] else "running by default")
    if "check_each_row" in changed:
        words.append("Check each row by default" if changed["check_each_row"] else "Every row the same by default")
    if words and "wording" in changed:
        words.append("wording fixed")
    return words


@app.route("/api/types/<int:type_id>", methods=["PUT"])
def api_types_change(type_id: int):
    """Change a spending type: its name, its wording (covers, not_for) and its
    settings (usual_book: a book or "ask"; one_off and check_each_row, true
    or false), any of them, as one change. Anything else is a code change
    and refused, as is an income kind and Other's name and settings; nothing
    is written. A name is checked as an added type's is; a rename keeps the
    type's id, so its merchants, rules and rows follow it, and Jev's stored
    answers are re-pointed to the new name (type-relationships 04). A
    settings change reaches new merchants only (fin RUL-006 Q3)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "send the change as JSON"}), 400
    with get_db() as conn:
        was = book_type.spending_type_names(conn).get(type_id)
        try:
            changed = book_type.change_type(conn, type_id, data)
        except book_type.NoSuchType:
            return jsonify({"error": "no such type"}), 404
        except book_type.TypeRefused as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 400
        r = conn.execute(_TYPES_SELECT + "WHERE t.id = ?", (type_id,)).fetchone()
        shown = format_type_display(r["parent_name"], r["name"])
        words = _type_change_words(changed)
        if "name" in changed and words:
            summary = f"Changed {shown}: renamed from {was}, {', '.join(words)}"
        elif "name" in changed:
            summary = f"Renamed {was} to {shown}" + (", wording fixed" if "wording" in changed else "")
        else:
            summary = f"Changed {shown}: {', '.join(words)}" if words else f"Fixed the wording of {shown}"
        _name_change(conn, summary)
        conn.commit()
    return jsonify({"type": _type_shown(r)})


@app.route("/api/types/slots")
def api_type_slots():
    """fin's own filings (type-relationships 03): {slots: [{slot, kind,
    files, type_id, display_type, says, past_known}]}, in declared order."""
    with get_db() as conn:
        return jsonify({"slots": book_type.slots(conn)})


SLOT_FIELDS = ("type_id", "past", "token")


def _slot_shown(conn, slot: str) -> dict:
    return next(s for s in book_type.slots(conn) if s["slot"] == slot)


@app.route("/api/types/slots/<slot>", methods=["PUT"])
def api_type_slots_move(slot: str):
    """Move one of fin's own filings to another type: body {type_id, past,
    token}. A type of the wrong kind, or Other, is refused, writing
    nothing. Where fin can tell the past rows it filed through the slot
    (loan interest), a move that reaches some answers with the preview
    (409) until `past` says apply (they move too) or only_new (they stay);
    where it cannot (a gift), the preview says so and only only_new is
    taken. One change entry either way. Returns {slot, recategorized}."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "send the move as JSON"}), 400
    unknown = [k for k in data if k not in SLOT_FIELDS]
    if unknown:
        return jsonify({"error": f"unknown field: {unknown[0]}"}), 400
    with get_db() as conn:
        try:
            shown = book_type.checked_slot_type(conn, slot, data.get("type_id"))
            past = relabel_preview.checked_answer(data.get("past"))
        except book_type.NoSuchSlot:
            return jsonify({"error": "no such slot"}), 404
        except book_type.NoSuchType:
            return jsonify({"error": "no such type"}), 400
        except (book_type.TypeRefused, relabel_preview.Refused) as e:
            return jsonify({"error": str(e)}), 400
        type_id = data["type_id"]
        if book_type.slot_type(conn, slot) == type_id:
            return jsonify({"slot": _slot_shown(conn, slot), "recategorized": 0})
        plan = relabel_preview.slot_change(conn, slot, type_id)
        words = book_type.slot_words(slot)
        if data.get("token") is not None and data["token"] != plan.token:
            return jsonify({"error": relabel_preview.stale(plan), "stale": True}), 409
        told = plan.extra["past_known"]
        if past is None and (plan.count or not told):
            return jsonify({"error": relabel_preview.slot_asked(plan, shown),
                            "preview": relabel_preview.summary(conn, plan)}), 409
        if past == relabel_preview.APPLY and not told:
            return jsonify({"error": f"fin cannot tell which past rows it filed as {words}: "
                                     f"only new rows can go under {shown}"}), 400
        book_type.move_slot(conn, slot, type_id)
        recategorized, _ = _write_plan(conn, plan, past)
        said = f"fin files {words} under {shown}"
        if recategorized:
            said += f", with its {recategorized} past row{'' if recategorized == 1 else 's'}"
        elif past == relabel_preview.ONLY_NEW:
            said += ", new rows only"
        _name_change(conn, said)
        conn.commit()
        return jsonify({"slot": _slot_shown(conn, slot), "recategorized": recategorized})


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
    which the dialog can correct or delete; `party_id` and `party` are the
    merchant it is joined to as its party (party.py), or null; `terms` is a
    loan's hire-purchase terms (loan_terms.shown), or null, and
    `terms_locked` why they cannot change now, or null; `shared` whether it
    is marked shared (shared.py)."""
    with get_db() as conn:
        terms = loan_terms.all_held(conn)
        rows = conn.execute(
            "SELECT a.id, a.name, a.short_name, a.type, a.last_four, a.currency, a.status, a.owner,"
            " a.party_id, s.name AS party FROM accounts a LEFT JOIN services s ON s.id = a.party_id"
            " ORDER BY a.name"
        ).fetchall()
        statement_counts = dict(conn.execute(
            "SELECT account_id, COUNT(*) FROM statements GROUP BY account_id"
        ).fetchall())
        supplied_counts = dict(conn.execute(
            "SELECT account_id, COUNT(*) FROM anchors WHERE source = ? GROUP BY account_id",
            (anchors.SUPPLIED,),
        ).fetchall())
        shared_ids = shared.ids(conn)
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
        ) or d["id"] in shared_ids
        # Figures typed for it, which can be corrected or deleted even once
        # the account no longer takes a new one.
        d["supplied_figures"] = supplied_counts.get(d["id"], 0)
        d["terms"] = loan_terms.shown(terms.get(d["id"]))
        # Marked shared (shared.py): its rows are not ours unless a rule says so.
        d["shared"] = d["id"] in shared_ids
        result.append(d)
    with get_db() as conn:
        # Why a loan's terms cannot change now (loan_terms.lock_refusal), or null.
        for d in result:
            d["terms_locked"] = loan_terms.lock_refusal(conn, d["id"]) if d["type"] == loan_terms.LOAN else None
    return jsonify(result)


@app.route("/api/accounts", methods=["POST"])
def api_accounts_create():
    """Create a new account. A name fin already holds, in any case and with
    spaces either side, is refused: an import and a rule find an account by
    its name, so a second one under it would split what lands there. A loan
    may be made with its hire-purchase terms (`terms`, loan_terms.checked), in
    the same change."""
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Account name is required"}), 400

    try:
        kind = account_kind.checked_kind(data.get("type", account_kind.DEFAULT_KIND))
        owner = account_kind.checked_owner(data.get("owner", account_kind.DEFAULT_OWNER))
        currency = _checked_currency(data.get("currency", "SGD"))
        terms = _account_terms(str(data["name"]).strip(), kind, currency, data.get("terms"))
    except (account_kind.UnknownAccountValue, CurrencyRefused, loan_terms.TermsRefused) as e:
        return jsonify({"error": str(e)}), 400

    name = str(data["name"]).strip()
    with get_db() as conn:
        held = conn.execute(
            "SELECT name, type FROM accounts WHERE LOWER(TRIM(name)) = LOWER(?) ORDER BY id",
            (name,),
        ).fetchone()
        if held is not None:
            return jsonify({"error": (
                f"fin already has an account named {mask_card_number(held['name'])}, "
                f"of kind {held['type']}; nothing was added"
            )}), 409
        if terms is not None:
            # As _crud_insert: a failed write is a refusal, never a crash.
            try:
                new_id = conn.execute(
                    "INSERT INTO accounts (name, short_name, type, last_four, currency, owner) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (name, data.get("short_name", name), kind, data.get("last_four"), currency, owner),
                ).lastrowid
                loan_terms.write(conn, new_id, terms)
                conn.commit()
            except Exception as e:
                conn.rollback()
                app.logger.warning("Failed to create account: %s", e)
                return jsonify({"error": "Failed to create account"}), 400
            return jsonify({"id": new_id, "success": True}), 200
        return _crud_insert(
            conn,
            "INSERT INTO accounts (name, short_name, type, last_four, currency, owner) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                name,
                data.get("short_name", name),
                kind,
                data.get("last_four"),
                currency,
                owner,
            ),
            "account",
        )


_ACCOUNT_FIELDS = ["name", "short_name", "type", "last_four", "currency", "status", "owner"]


def _account_terms(name: str, kind: str, currency: str, value) -> dict | None:
    """Hire-purchase terms as sent for an account (null: none), as stored.
    Refused (loan_terms.TermsRefused) on an account that is no loan."""
    if value is None:
        return None
    if kind != loan_terms.LOAN:
        raise loan_terms.TermsRefused(
            f"{mask_card_number(name)} is a {kind} account; hire-purchase terms go on a loan. "
            "Nothing was saved."
        )
    return loan_terms.checked(value, currency)


@app.route("/api/accounts/<int:acct_id>", methods=["PUT"])
def api_accounts_update(acct_id):
    """Update an account. `terms` sets a loan's hire-purchase terms, or null
    takes them off (loan_terms.write), in the same change as the rest."""
    data = request.get_json()
    if data and "terms" in data:
        return _account_update_with_terms(acct_id, data)
    try:
        if data and "type" in data:
            account_kind.checked_kind(data["type"])
        if data and "owner" in data:
            account_kind.checked_owner(data["owner"])
        if data and "currency" in data:
            _checked_currency(data["currency"])
    except (account_kind.UnknownAccountValue, CurrencyRefused) as e:
        return jsonify({"error": str(e)}), 400
    with get_db() as conn:
        why = (_ruled_account_refusal(conn, acct_id, data or {}) or _party_kind_refusal(conn, acct_id, data or {})
               or _shared_kind_refusal(conn, acct_id, data or {}))
    if not why and data and "type" in data and data["type"] != loan_terms.LOAN:
        with get_db() as conn:
            if loan_terms.held(conn, acct_id) is not None:
                why = ("This loan holds hire-purchase terms; take them off before making it a "
                       f"{data['type']} account. Nothing was saved.")
    if why:
        return jsonify({"error": why}), 400
    return _crud_update("accounts", acct_id, data, _ACCOUNT_FIELDS)


def _account_update_with_terms(acct_id: int, data: dict):
    """An account update that carries `terms`: the account's fields and its
    terms checked, then written together, or nothing."""
    fields = {k: data[k] for k in _ACCOUNT_FIELDS if k in data}
    try:
        if "type" in fields:
            account_kind.checked_kind(fields["type"])
        if "owner" in fields:
            account_kind.checked_owner(fields["owner"])
        if "currency" in fields:
            _checked_currency(fields["currency"])
    except (account_kind.UnknownAccountValue, CurrencyRefused) as e:
        return jsonify({"error": str(e)}), 400
    with get_db() as conn:
        held = conn.execute("SELECT name, type, currency FROM accounts WHERE id = ?", (acct_id,)).fetchone()
        if held is None:
            return jsonify({"error": "No such account"}), 404
        account = {**dict(held), **fields}
        try:
            terms = _account_terms(account["name"], account["type"], account["currency"], data["terms"])
        except loan_terms.TermsRefused as e:
            return jsonify({"error": str(e)}), 400
        why = (_ruled_account_refusal(conn, acct_id, fields) or _party_kind_refusal(conn, acct_id, fields)
               or _shared_kind_refusal(conn, acct_id, fields))
        if why:
            return jsonify({"error": why}), 400
        try:
            if fields:
                conn.execute(
                    f"UPDATE accounts SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                    (*fields.values(), acct_id),
                )
            # Terms the loan holds already change nothing, locked or not.
            if loan_terms.write(conn, acct_id, terms):
                # The terms are the loan's first figure: its interest follows.
                loan_interest.derive(conn, acct_id)
        except loan_terms.TermsRefused as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 400
        conn.commit()
    return jsonify({"success": True})


def _shared_kind_refusal(conn, acct_id: int, data: dict) -> str | None:
    """Why an account update would leave a shared account in a kind that
    cannot be shared (shared.kind_refusal), or None."""
    if "type" not in data or not shared.is_shared(conn, acct_id):
        return None
    why = shared.kind_refusal(data["type"])
    return f"This account is shared and {why}; mark it not shared first. Nothing was changed" if why else None


def _party_kind_refusal(conn, acct_id: int, data: dict) -> str | None:
    """Why an account update would leave an account joined to a party in a
    kind that takes none (party.ACCOUNT_KINDS), or None."""
    if "type" not in data:
        return None
    held = conn.execute("SELECT party_id FROM accounts WHERE id = ?", (acct_id,)).fetchone()
    if held is None or held["party_id"] is None:
        return None
    why = party.kind_refusal(data["type"])
    return f"{why}; take this account off its party first. Nothing was changed" if why else None


@app.route("/api/accounts/<int:acct_id>/party", methods=["PUT"])
def api_accounts_party(acct_id):
    """Join a person, company, loan, holding or untracked account to a party:
    a merchant (party.py). Body: a JSON object carrying service_id, a
    merchant's id, or null to take the account off its party; any other body
    is refused. Two accounts may name one party, each keeping its own balance
    (RUL-011). One change, undone whole; a refusal writes nothing."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "service_id" not in data:
        return jsonify({"error": "send service_id: a merchant's id, or null to take the account off its party; "
                                 "nothing was changed"}), 400
    service_id = data["service_id"]
    if service_id is not None and (not isinstance(service_id, int) or isinstance(service_id, bool)):
        return jsonify({"error": "service_id must be a merchant's id, or null to take the account off its party"}), 400
    with get_db() as conn:
        held = conn.execute("SELECT type, party_id FROM accounts WHERE id = ?", (acct_id,)).fetchone()
        if held is None:
            return jsonify({"error": "This account is no longer in fin; nothing was changed"}), 404
        if service_id is not None:
            why = party.kind_refusal(held["type"])
            if why:
                return jsonify({"error": f"{why}. Nothing was changed"}), 400
            if conn.execute("SELECT 1 FROM services WHERE id = ?", (service_id,)).fetchone() is None:
                return jsonify({"error": f"no merchant {service_id} in fin; nothing was changed"}), 400
        conn.execute("UPDATE accounts SET party_id = ? WHERE id = ?", (service_id, acct_id))
        _name_change(conn, party.JOINED if service_id is not None else party.LEFT)
        conn.commit()
    return jsonify({"success": True, "party_id": service_id})


@app.route("/api/accounts/<int:acct_id>/shared", methods=["PUT"])
def api_accounts_shared(acct_id):
    """Mark a bank account or a card shared, or not (shared.py, fin-redesign
    20). Body: {"shared": true or false}. In the same change the rows on it
    whose flow fin worked out are worked out again (_reflow_shared): on a
    shared account a row is not ours unless a rule says what it is or it
    moves money from one of the household's own accounts. A flow set by hand
    stays; a row a record of expect back rests on is kept, and named under
    `kept`. Answers how many rows moved (`moved`). One change, undone whole;
    a refusal writes nothing."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("shared"), bool):
        return jsonify({"error": "send shared: true or false; nothing was changed"}), 400
    with get_db() as conn:
        held = conn.execute("SELECT type FROM accounts WHERE id = ?", (acct_id,)).fetchone()
        if held is None:
            return jsonify({"error": "This account is no longer in fin; nothing was changed"}), 404
        if data["shared"]:
            why = shared.kind_refusal(held["type"])
            if why:
                return jsonify({"error": f"{why}. Nothing was changed"}), 400
        before = flow.build_context(conn)
        if not shared.mark(conn, acct_id, data["shared"]):
            return jsonify({"success": True, "shared": data["shared"], "moved": 0, "kept": []})
        moved, kept = _reflow_shared(conn, acct_id, before, flow.build_context(conn))
        _name_change(conn, shared.MARKED if data["shared"] else shared.UNMARKED)
        conn.commit()
    return jsonify({"success": True, "shared": data["shared"], "moved": moved, "kept": kept})


def _reflow_shared(conn, acct_id: int, before: "flow.ClassifierContext",
                   after: "flow.ClassifierContext") -> tuple[int, list[dict]]:
    """Work out again the flow of each row on the account that fin worked
    out under `before`, under `after` (the account marked shared, or not).
    Never a flow set by hand, nor one fin did not give (a pairing, a refund
    link); a row a hand label would refuse the move (a record of expect back
    resting on it, relabel_preview.move_refusal) is kept. A row leaving
    spending loses its book and type (label_source.fitted); one coming back
    takes the label its rules give it. Returns (rows moved, rows kept)."""
    rules = db.load_rules(conn)
    moved, kept = 0, []
    for held in conn.execute(
        f"SELECT {relabel_preview._HELD} FROM {relabel_preview._FROM} "
        "WHERE st.account_id = ? AND COALESCE(t.flow_type_manual, 0) = 0 ORDER BY t.id", (acct_id,),
    ).fetchall():
        rule = db.pick_rule(rules, held["description"] or "", held["amount_minor"])
        account = relabel_preview.held_account(held)
        was = flow.worked_out_again(held, held["service_id"], held["type_id"], rule, account, before)
        if (held["flow_type"], held["other_side_id"]) != was:
            continue
        now = flow.worked_out_again(held, held["service_id"], held["type_id"], rule, account, after)
        if now == was:
            continue
        flow_name, other_side = now
        other_kind = (conn.execute("SELECT type FROM accounts WHERE id = ?", (other_side,)).fetchone()
                      or [None])[0] if other_side is not None else None
        why = relabel_preview.move_refusal(
            conn, held, flow_name, held["book"] if flow.carries_label(flow_name) else None, other_kind)
        if why is not None:
            kept.append({"id": held["id"], "description": held["description"], "why": why})
            continue
        sets = {"flow_type": flow_name, "other_side_id": other_side}
        if flow.carries_label(flow_name) and not flow.carries_label(held["flow_type"]):
            found = _label_for(conn, held["description"] or "", held["amount_minor"])
            if found["service_id"] is not None and found["service_id"] == held["service_id"]:
                sets.update({k: found[k] for k in ("book", "type_id", "book_source", "type_source")})
        if _write_label(conn, held, label_source.fitted(held, sets)):
            moved += 1
    return moved, kept


def _ruled_account_refusal(conn, acct_id: int, data: dict) -> str | None:
    """Why an account update would leave a rule naming the account as its
    other side one it may not name (_rule_side_refusal: archived, retyped
    to a kind the rule's flow does not allow, a person, or no longer the
    household's), naming the rule, or None. Nothing is written then."""
    held = conn.execute("SELECT type, owner, status FROM accounts WHERE id = ?", (acct_id,)).fetchone()
    if held is None:
        return None
    account = {**dict(held), **{k: data[k] for k in ("type", "owner", "status") if k in data}}
    refused = [(r["pattern"], why) for r in conn.execute(
        "SELECT pattern, flow FROM merchant_rules WHERE other_side_id = ? AND flow IN "
        f"({','.join('?' * len(flow.RULE_FLOWS))}) ORDER BY pattern", (acct_id, *flow.RULE_FLOWS))
        if (why := _rule_side_refusal(r["flow"], account)) is not None]
    if not refused:
        return None
    patterns = [p for p, _ in refused]
    why = refused[0][1]
    named = (f"The rule {patterns[0]} names" if len(patterns) == 1
             else f"The rules {', '.join(patterns[:-1])} and {patterns[-1]} name")
    return f"{named} this account as its other side. {why[0].upper()}{why[1:]}{'' if why.endswith('.') else '.'}"


def _account_records_refusal(conn, acct_id: int) -> str | None:
    """Why deleting an account would leave a record of expect back pointing at
    nothing, in the operator's words, or None: the oldest of its rows that an
    expectation, a set-off, a refund link or a set-aside rests on; else, for a
    person, the first record that names them."""
    held = conn.execute(
        "SELECT t.id, t.description FROM transactions t JOIN statements s ON s.id = t.statement_id "
        "WHERE s.account_id = ? AND ("
        " EXISTS (SELECT 1 FROM expectation e WHERE e.row_id = t.id)"
        " OR EXISTS (SELECT 1 FROM set_off so WHERE so.payment_row_id = t.id)"
        " OR EXISTS (SELECT 1 FROM refund_link r WHERE r.refund_row_id = t.id OR r.purchase_row_id = t.id)"
        " OR EXISTS (SELECT 1 FROM dismissal d WHERE d.row_id = t.id OR d.purchase_row_id = t.id)"
        ") ORDER BY t.date, t.id LIMIT 1",
        (acct_id,),
    ).fetchone()
    if held is not None:
        row_id, description = held["id"], held["description"]
        marked = conn.execute(
            "SELECT id, direction, currency FROM expectation WHERE row_id = ?", (row_id,)
        ).fetchone()
        if marked is not None:
            what = "expected back" if marked["direction"] == expect_back.BACK else "to pay back"
            back = expect_back.set_off_minor(conn, marked["id"])
            if back:
                return (f"{description} is {what} with {expect_back.short(back, marked['currency'])} "
                        "set against it. Unlink those and undo the mark first.")
            return f"{description} is {what}. Undo the mark first."
        placed = conn.execute(
            "SELECT so.amount_minor, COALESCE(a.currency, 'SGD') AS currency, "
            "COALESCE(it.description, 'Opening figure · ' || p.name, 'its row, when it comes') AS item "
            "FROM set_off so JOIN transactions t ON t.id = so.payment_row_id "
            "JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id "
            "LEFT JOIN expectation e ON e.id = so.expectation_id "
            "LEFT JOIN transactions it ON it.id = e.row_id LEFT JOIN accounts p ON p.id = e.person_id "
            "WHERE so.payment_row_id = ? ORDER BY so.id",
            (row_id,),
        ).fetchall()
        if placed:
            parts = [f"{expect_back.short(p['amount_minor'], p['currency'])} set against {p['item']}" for p in placed]
            listed = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
            return f"{description} has {listed}. Unlink {'it' if len(parts) == 1 else 'those'} first."
        if conn.execute("SELECT 1 FROM refund_link WHERE refund_row_id = ? OR purchase_row_id = ?",
                        (row_id, row_id)).fetchone():
            return f"{description} is linked as a refund. Unlink it first."
        return f"{description} was set aside. Undo that change first."

    person = conn.execute("SELECT name FROM accounts WHERE id = ? AND type = 'person'", (acct_id,)).fetchone()
    if person is None:
        return None
    name = person["name"]
    item = "COALESCE(it.description, 'Opening figure · ' || p.name)"
    if conn.execute("SELECT 1 FROM expectation WHERE person_id = ?", (acct_id,)).fetchone():
        return f"{name} has an opening figure expected back or to pay back. Undo that change first."
    named = conn.execute(
        f"SELECT {item} FROM expectation_name n JOIN expectation e ON e.id = n.expectation_id "
        "LEFT JOIN transactions it ON it.id = e.row_id LEFT JOIN accounts p ON p.id = e.person_id "
        "WHERE n.person_id = ? ORDER BY e.id LIMIT 1",
        (acct_id,),
    ).fetchone()
    if named is not None:
        return f"{name} is named on {named[0]}. Take the name off it first."
    waiting = conn.execute(
        "SELECT t.description FROM set_off so JOIN transactions t ON t.id = so.payment_row_id "
        "WHERE so.person_id = ? ORDER BY so.id LIMIT 1",
        (acct_id,),
    ).fetchone()
    if waiting is not None:
        return f"{name} is named on {waiting[0]}, waiting for its row. Unlink it first."
    given = conn.execute(
        f"SELECT {item} FROM let_go l JOIN expectation e ON e.id = l.expectation_id "
        "LEFT JOIN transactions it ON it.id = e.row_id LEFT JOIN accounts p ON p.id = e.person_id "
        "WHERE l.to_whom_id = ? ORDER BY l.id LIMIT 1",
        (acct_id,),
    ).fetchone()
    if given is not None:
        return f"{name} is named on the let-go of {given[0]}. Undo the let-go first."
    learnt = conn.execute("SELECT wording FROM sender_wording WHERE person_id = ? ORDER BY id LIMIT 1",
                          (acct_id,)).fetchone()
    if learnt is not None:
        return f"{name} is learnt from the wording '{learnt[0]}'. Undo that change first."
    return None


@app.route("/api/accounts/<int:acct_id>", methods=["DELETE"])
def api_accounts_delete(acct_id):
    """Delete an account. Refuses if statements or anchors reference it, or a
    row or a rule names it as its other side; first, in named words, if a
    record of expect back rests on one of its rows or names it as a person."""
    with get_db() as conn:
        why = _account_records_refusal(conn, acct_id)
        if why:
            return jsonify({"error": why}), 400
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
        # A rule that states a flow may name the account as its rows' other
        # side (ADR-009): it is not deleted from under the rule.
        ruled = [r["pattern"] for r in conn.execute(
            "SELECT pattern FROM merchant_rules WHERE other_side_id = ? ORDER BY pattern", (acct_id,))]
        if len(ruled) == 1:
            return jsonify({"error": f"Cannot delete: the rule {ruled[0]} names this account as its other side"}), 400
        if ruled:
            return jsonify({"error": f"Cannot delete: the rules {', '.join(ruled[:-1])} and {ruled[-1]} name "
                                     "this account as their other side"}), 400

        # A loan's terms go with it, in the same change.
        loan_terms.write(conn, acct_id, None)
        folio_link.take_off(conn, "account", acct_id)
        shared.mark(conn, acct_id, False)
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
        # A shared account's figure is the household's part (shared.py).
        if not (account_kind.takes_a_figure(kind, statement_count) or shared.is_shared(conn, account_id)):
            return jsonify({
                "error": f"a {kind} account rests on its statement; a figure can be entered "
                         "for a loan, a holding, a company, a person, a shared account (the "
                         "household's part), or a bank account that has no statement"
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
# A row's label and its sources (personal-ledger 24): every write of a row's
# book or type goes through _write_label.
# ---------------------------------------------------------------------------

# The columns a label write may set; nothing else is ever named in its SQL.
_LABEL_COLUMNS = frozenset({
    "book", "type_id", "service_id", "book_source", "type_source", "flow_type",
    "other_side_id", "flow_type_manual",
})
# What a label write reads of a row first.
_LABEL_HELD = ("t.id, t.description, t.amount_minor, t.book, t.type_id, t.service_id, "
               "t.book_source, t.type_source, t.flow_type, t.flow_type_manual, t.other_side_id")


def _write_label(conn, held, sets: dict) -> bool:
    """Write `sets` to the row `held` (as _LABEL_HELD reads it). Returns
    whether anything changed. A row it takes out of refund leaves its
    purchase in the same change, as a hand label's does (refund_links.drop;
    a rule's preview kept any row the drop would refuse,
    relabel_preview.move_refusal)."""
    if not set(sets) <= _LABEL_COLUMNS:
        raise ValueError(f"not a label column: {sorted(set(sets) - _LABEL_COLUMNS)}")
    sets = {k: v for k, v in sets.items() if held[k] != v}
    if not sets:
        return False
    if held["flow_type"] == "refund" and sets.get("flow_type", "refund") != "refund":
        refund_links.drop(conn, held["id"])
    conn.execute(
        f"UPDATE transactions SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
        (*sets.values(), held["id"]),
    )
    return True


def _would_change(held, sets: dict) -> bool:
    """Whether writing `sets` to the row `held` changes anything."""
    return any(held[k] != v for k, v in sets.items())


def _teach_fills(conn, service_id: int | None, tx_id: int, pattern: str, match_type: str,
                 book, type_id, taught: tuple) -> list[tuple]:
    """The rows a teach (resolve) labels itself, whatever the preview's
    answer: those its wording matches that have no merchant, or have this
    merchant (`service_id`) and still no type, with the label columns each
    takes, the merchant aside. They take
    the taught label: from the rule for the field it overrides, from the
    merchant for the other (`taught`, label_source.rule_sources). A field
    chosen or worked out keeps its own, as every later change leaves it (spec
    24; an empty field reads its own source, as the rule preview reads it),
    and a checked one stays checked; a row with no field to reach is left out
    and keeps its merchant too (fin-surfaces 54). The backfill writes these
    rows, and a preview counts the merchant's among them, so the two read
    the same list. Their flows are _teach_flows'. Never the row taught
    (`tx_id`), with or without a merchant: step 3 writes it, so a count of
    these rows is of the others ("2 other rows took the same label")."""
    fill_where = (
        "(service_id IS NULL OR (service_id = ? AND type_id IS NULL)) AND id != ? "
        f"AND {_build_match_condition(match_type)}"
    )
    given = {"book": (book, taught[0]), "type_id": (type_id, taught[1])}
    out = []
    for held in conn.execute(
        f"SELECT {_LABEL_HELD} FROM transactions t WHERE {fill_where}", (service_id, tx_id, pattern.upper())
    ).fetchall():
        reached = [(field, column) for field, column in label_source.FIELDS.items()
                   if label_source.reachable(held[column])]
        if not reached:
            continue
        sets = {}
        for field, column in reached:
            value, source = given[field]
            sets[field] = value
            sets[column] = label_source.reached(_source(held, column), value, source)
        out.append((held, sets))
    return out


# A merchant a teach creates has no id until it is saved; the flow its rows
# would have reads only that they have one (flow.worked_out_again).
_MERCHANT_TO_BE = -1


def _teach_flows(conn, service_id: int | None, tx_id: int, words: list[str], match_type: str, book, type_id,
                 taught: tuple, rules: tuple) -> tuple[dict, list]:
    """The flows a teach's backfill (_teach_fills, each wording's in turn)
    would move, worked out before anything is written: {row id: the flow
    and other side Apply writes} and the rows whose flow it keeps
    (relabel_preview.Kept). `rules` is the rules before the teach and as it
    leaves them (relabel_preview.taught_rules): where the rule that labels a
    row, before or after, states a flow, its flow is worked out again as a
    rule's Apply works it out (_fill_flow). A teach that moves any asks
    first, and writes them only on past_rows apply. The row taught is not
    one of them: its flow is the teach's own (_teach, step 3)."""
    merchant = service_id if service_id is not None else _MERCHANT_TO_BE
    moves, kept, seen = {}, [], {tx_id}
    for wording in words:
        for held, sets in _teach_fills(conn, service_id, tx_id, wording, match_type, book, type_id, taught):
            if held["id"] in seen:
                continue
            seen.add(held["id"])
            writes, keeps = _fill_flow(conn, held, {"service_id": merchant, **sets}, *rules)
            if writes:
                moves[held["id"]] = writes
            if keeps is not None:
                kept.append(keeps)
    return moves, kept


def _taken_flows(conn, plan, service_id: int | None, tx_id: int, rules: tuple) -> None:
    """The flows the rows a taught wording takes from other merchants
    (relabel_preview.taking) would move, worked out as the backfill's are
    (_fill_flow, with `rules` as _teach_flows reads them): each row's Apply
    carries them, Only new rows leaves them, the rows whose flow it keeps
    are the plan's kept, and its token stamps them, so the preview lists
    every one (flows, flows_kept) and Apply writes exactly those. The row
    taught is not one of them: its flow is the teach's own (_teach, step 3)."""
    merchant = service_id if service_id is not None else _MERCHANT_TO_BE
    for p in plan.rows:
        label = {k: p.apply[k] for k in ("book", "type_id") if k in p.apply}
        if p.held["id"] == tx_id:
            # Its label as step 3 leaves it, so its example says so: none
            # where the flow step 3 gives it is no one's spending.
            writes, _ = _fill_flow(conn, p.held, {"service_id": merchant, **label}, *rules, keep_chosen=False)
            fitted = label_source.fitted(p.held, {**p.apply, **writes})
            p.apply = {k: v for k, v in fitted.items() if k not in writes}
            continue
        writes, keeps = _fill_flow(conn, p.held, {"service_id": merchant, **label}, *rules)
        # Each answer's write fitted to the flow it leaves the row
        # (label_source.fitted): Apply's with the flow it moves.
        labelled = p.apply
        p.apply = label_source.fitted(p.held, {**p.apply, **writes})
        p.only_new = label_source.fitted(p.held, p.only_new)
        if keeps is not None:
            if keeps.by_hand:
                # Its write on move_chosen: the flow it would take, fitted as Apply's is.
                keeps.moved = relabel_preview.Planned(p.held, label_source.fitted(p.held, {**labelled, **keeps.would}),
                                                      p.only_new, checked=p.checked)
            plan.kept.append(keeps)
    plan.token = relabel_preview._stamp(plan.token, relabel_preview._stamp_flows(plan))


def _fill_flow(conn, held, sets: dict, before_rules: list[dict], after_rules: list[dict],
               keep_chosen: bool = True) -> tuple[dict, object]:
    """The flow and other side a teach's backfill would write to a row besides
    its label (`sets`), where a rule that states a flow labels it, before the
    teach or after, and the row kept or None: what a rule's Apply writes
    (relabel_preview.flow_writes: never a flow set by hand, one fin did not
    work out, or a move a hand label would refuse, a record resting on the
    row; nor, but for the row taught (`keep_chosen` False), the flow of a
    row whose book or type was chosen)."""
    description, amount = held["description"] or "", held["amount_minor"]
    before = db.pick_rule(before_rules, description, amount)
    after = db.pick_rule(after_rules, description, amount)
    if not (flow.stated(before) or flow.stated(after)):
        return {}, None
    return relabel_preview.flow_writes(conn, held, sets, before, after, flow.build_context(conn),
                                       _account_facts(conn, held["id"]), keep_chosen=keep_chosen)


def _source(held, column: str) -> str | None:
    """A held row's source for one field, as label_source reads it; none
    for an empty field with no source."""
    field = next(f for f, c in label_source.FIELDS.items() if c == column)
    if held[field] is None and held[column] is None:
        return None
    return label_source.source_of(held[column])


def _accept_past(conn, where: str, params: tuple) -> int:
    """The rows `where` picks (over transactions t) accepted as they stand
    ("Not now, only new rows"): each field from fin becomes checked; the
    values are unchanged. Returns the rows changed."""
    changed = 0
    for held in conn.execute(
        f"SELECT {_LABEL_HELD} FROM transactions t WHERE {where} AND {label_source.from_fin_sql('t')}", params
    ).fetchall():
        changed += _write_label(conn, held, {
            column: label_source.accepted(_source(held, column)) for column in label_source.FIELDS.values()
        })
    return changed


# ---------------------------------------------------------------------------
# Services API
# ---------------------------------------------------------------------------

def _merchants(conn, svc_id: int | None = None) -> list[dict]:
    """Merchants with their book, type, rules and counts; one when svc_id is given."""
    one = "WHERE s.id = ?" if svc_id is not None else ""
    rows = conn.execute("""
        SELECT s.*, ty.name as type_name, tp.name as parent_type,
               (SELECT COUNT(*) FROM transactions t WHERE t.service_id = s.id
                AND COALESCE(t.flow_type, 'expense') NOT IN ('transfer', 'payment')) as txn_count,
               (SELECT COUNT(*) FROM merchant_rules mr WHERE mr.service_id = s.id) as rule_count,
               (SELECT COUNT(*) FROM transactions t WHERE t.service_id = s.id
                AND """ + _left_to_label(CHECKABLE_ROWS) + """) as rows_to_check
        FROM services s
        """ + _TYPE_JOINS.format(owner="s") + f"""
        {one}
        ORDER BY s.name
    """, (svc_id,) if svc_id is not None else ()).fetchall()
    # Fetch the rule patterns grouped by service_id
    rule_rows = conn.execute(
        "SELECT service_id, pattern, match_type FROM merchant_rules WHERE service_id IS NOT NULL"
        + (" AND service_id = ?" if svc_id is not None else "") + " ORDER BY pattern",
        (svc_id,) if svc_id is not None else (),
    ).fetchall()
    rules_by_svc: dict[int, list[dict]] = {}
    for rr in rule_rows:
        rules_by_svc.setdefault(rr["service_id"], []).append(
            {"pattern": rr["pattern"], "match_type": rr["match_type"]}
        )

    # The accounts joined to each merchant as their party (party.py).
    joined: dict[int, list[dict]] = {}
    for a in conn.execute(
        "SELECT id, name, type, party_id FROM accounts WHERE party_id IS NOT NULL"
        + (" AND party_id = ?" if svc_id is not None else "") + " ORDER BY name, id",
        (svc_id,) if svc_id is not None else (),
    ):
        joined.setdefault(a["party_id"], []).append({"id": a["id"], "name": a["name"], "type": a["type"]})

    result = []
    for r in rows:
        d = dict(r)
        d["display_type"] = format_type_display(d["parent_type"], d["type_name"])
        d["rules"] = rules_by_svc.get(d["id"], [])
        d["party_accounts"] = joined.get(d["id"], [])
        result.append(d)
    return result


@app.route("/api/services")
def api_services():
    """List all services with their book, type and transaction/rule counts."""
    with get_db() as conn:
        return jsonify(_merchants(conn))


@app.route("/api/services/<int:svc_id>")
def api_service(svc_id):
    """One merchant, as its page shows it: the list's facts, plus its spending
    and refund rows' span (rows, first and last date, accounts paid from)."""
    with get_db() as conn:
        found = _merchants(conn, svc_id)
        if not found:
            return jsonify({"error": "This merchant is no longer in fin"}), 404
        span = conn.execute(
            "SELECT COUNT(*) AS rows, MIN(t.date) AS first_date, MAX(t.date) AS last_date,"
            " COUNT(DISTINCT s.account_id) AS accounts"
            " FROM transactions t JOIN statements s ON s.id = t.statement_id"
            " WHERE t.service_id = ? AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund')",
            (svc_id,),
        ).fetchone()
    return jsonify({**found[0], **dict(span)})


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
    """Create a new service (chat's create_merchant is this route). A name
    fin already holds, in any case and with spaces either side, is refused:
    a teach and a plan find a merchant by its name, so a second one under it
    would split its rows."""
    data = request.get_json()
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name.strip():
        return jsonify({"error": "Service name is required"}), 400
    name = name.strip()
    with get_db() as conn:
        held = conn.execute(
            "SELECT name FROM services WHERE UPPER(TRIM(name)) = UPPER(?) ORDER BY id", (name,)
        ).fetchone()
        if held is not None:
            return jsonify({"error": f"fin already has a merchant named {held['name']}; nothing was added"}), 409
        # A merchant carries a default book and type. With a type and no book
        # given, the book is the one the type proposes.
        try:
            type_id = _checked_type_id(conn, data.get("type_id"))
            book = _book_or_proposed(conn, _checked_book(data.get("book")), type_id)
            role = party.checked_role(data.get("role"))
        except (UnknownLabel, BookNeeded, party.Refused) as e:
            return jsonify({"error": str(e)}), 400
        # Left unsaid, Check each row is the type's (type-relationships 02).
        each_row = data["review_each_time"] if "review_each_time" in data else _new_checks_each_row(conn, type_id)
        return _crud_insert(
            conn,
            "INSERT INTO services (name, book, type_id, notes, exclude_from_expense_views, "
            "review_each_time, role) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                book,
                type_id,
                data.get("notes"),
                data.get("exclude_from_expense_views", 0),
                1 if each_row else 0,
                role,
            ),
            "service",
            post_commit=invalidate_rules_cache,
        )


def _previewed_answer(conn, plan, answer: str | None, token, answer_key: str = "past",
                      move_chosen: bool = False):
    """The preview's gate on a change that may reach past rows (fin-surfaces
    21): the response a route returns instead of writing, or None to go on.
    A token from an earlier preview that no longer holds is refused whatever
    is left to change (case 20); with rows to change, or rows it keeps only
    because their book or type was chosen by hand (Plan.by_hand, which the
    operator may move), and no answer, the preview itself (409, the body the
    drawer and chat read). `move_chosen` moves those rows only on the token
    of a preview that listed them: sent without one, the preview."""
    if token is not None and token != plan.token:
        return jsonify({"error": relabel_preview.stale(plan), "stale": True}), 409
    if (plan.count or plan.by_hand) and (answer is None or (move_chosen and token is None)):
        return jsonify({
            "error": relabel_preview.asked(plan, answer_key),
            "preview": relabel_preview.summary(conn, plan),
        }), 409
    return None


def _kept_said(conn, plan, moved=()) -> dict:
    """What a rule route says, once written, of the rows whose flow the rule
    would move but keeps, a record of expect back resting on each: none
    where there are none. A change that moves no other row asks nothing, so
    this is where they are named (relabel_preview.flow_writes); those kept
    only because their book or type was chosen by hand also apart
    (flows_chosen), as the preview lists them, with those `move_chosen`
    moved (`moved`, each saying so: never moved unseen, RUL-019)."""
    by_hand = [*relabel_preview.chosen_lines(conn, plan.kept),
               *relabel_preview.chosen_lines(conn, list(moved), moves=True)]
    return {**({"flows_kept": relabel_preview.flows_kept(conn, plan)} if plan.kept else {}),
            **({"flows_chosen": by_hand} if by_hand else {})}


def _write_plan(conn, plan, answer: str | None) -> tuple[int, int]:
    """The plan the preview counted, as answered: apply writes each row's new
    values (its sources as they were; a rule's rows with the flow and other
    side the preview worked out, relabel_preview.flow_writes); only_new
    marks each kept field checked, values left. Nothing else is written, so
    the preview's count is the rows Apply changes.
    Returns (rows whose label changed, rows marked checked)."""
    relabelled = checked = 0
    for p in plan.rows:
        if answer == relabel_preview.APPLY:
            relabelled += _write_label(conn, p.held, p.apply)
        elif answer == relabel_preview.ONLY_NEW:
            checked += _write_label(conn, p.held, p.only_new)
    # A row that now names a loan, or no longer does, changes what was paid.
    if answer == relabel_preview.APPLY and any(relabel_preview.moves_flow(p) for p in plan.rows):
        loan_interest.derive(conn)
    return relabelled, checked


class BadWording(ValueError):
    """A teach's other wordings are refused: the whole teach writes nothing."""


MAX_OTHER_WORDINGS = 20   # a card adds a few; the cap keeps a request from making hundreds of rules


def _other_wordings(value, pattern: str, apply_scope: str) -> list[str]:
    """The other wordings a teach's rule also recognises (queue-naming C):
    each one more `contains` rule for the merchant, saved with the pattern in
    the one change. Refused, every one, before anything is written: a wording
    that is empty, there twice (the pattern's among them, ignoring case), or
    refused as a pattern is; and any at all without a rule to go with."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise BadWording("wordings must be a list of the other wordings the rule recognises")
    if value and (apply_scope not in {"rule", "service_default"} or not pattern):
        raise BadWording("Other wordings go with a rule: not with this row only")
    if len(value) > MAX_OTHER_WORDINGS:
        raise BadWording(f"At most {MAX_OTHER_WORDINGS} other wordings in one save")
    out, seen = [], {pattern.upper()}
    for wording in value:
        if not isinstance(wording, str) or not wording.strip():
            raise BadWording("Say each wording the rule recognises")
        wording = wording.strip()
        if wording.upper() in seen:
            raise BadWording(f"The wording {wording} is there twice")
        why = _rule_pattern_error(wording)
        if why:
            raise BadWording(why)
        seen.add(wording.upper())
        out.append(wording)
    return out


def _taught_overrides(apply_scope: str, book, type_id, merchant_book) -> dict:
    """The overrides a rule taught by resolve carries: with a rule override,
    the type, and the book only where it is not the merchant's; none for the
    merchant's default."""
    if apply_scope != "rule":
        return {"book_override": None, "type_override_id": None}
    return {"book_override": book if book != merchant_book else None, "type_override_id": type_id}


def _write_taken(conn, plan, answer: str | None, service_id: int) -> int:
    """The rows another merchant held that a taught wording takes
    (relabel_preview.taking), as answered: apply moves each to the merchant
    taught with its label, sources as they were; only_new leaves each with
    its merchant, the fields fin gave checked. Returns the rows written."""
    written = 0
    for p in plan.rows:
        if answer == relabel_preview.APPLY:
            written += _write_label(conn, p.held, {**p.apply, "service_id": service_id})
        elif answer == relabel_preview.ONLY_NEW:
            written += _write_label(conn, p.held, p.only_new)
    return written


@app.route("/api/rules/reach")
def api_rules_reach():
    """The rows a wording taught as a merchant's rule would label (the rule
    shown when teaching, fin-surfaces 53): {pattern, count, only_this,
    others: [{service_id, name, count}]}. `tx_id`, once or more, is the row
    or rows being taught (only_this: the wording matches one of them and no
    other row); `service_id`, or
    `service_name` for one fin holds by that name, is the merchant taught
    (others: the rows another merchant holds that it would take, a chosen
    field's row left out). The count is the preview's (relabel_preview);
    it writes nothing."""
    pattern = (request.args.get("pattern") or "").strip()
    match_type = request.args.get("match_type") or "contains"
    if not pattern:
        return jsonify({"error": "Say the wording the rule recognises"}), 400
    if match_type not in relabel_preview.MATCH_TYPES:
        return jsonify({"error": f"match_type must be one of {', '.join(relabel_preview.MATCH_TYPES)}"}), 400
    pattern_error = _rule_pattern_error(pattern)
    if pattern_error:
        return jsonify({"error": pattern_error}), 400
    tx_ids = request.args.getlist("tx_id", type=int)
    service_id = request.args.get("service_id", type=int)
    name = (request.args.get("service_name") or "").strip()
    with get_db() as conn:
        if service_id is None and name:
            found = conn.execute("SELECT id FROM services WHERE UPPER(name) = ?", (name.upper(),)).fetchone()
            service_id = found[0] if found else None
        reach = relabel_preview.wording_reach(conn, pattern, match_type, service_id)
        return jsonify({
            "pattern": reach.pattern,
            "count": reach.count,
            "only_this": reach.only(tx_ids),
            "others": reach.others_named(relabel_preview.kept),
        })


@app.route("/api/services/<int:svc_id>", methods=["PUT"])
def api_services_update(svc_id):
    """Update a service. A change of its book or type that would relabel
    past rows goes through the preview (relabel_preview): without `past`
    nothing is written and the preview comes back (409); `past: apply`
    writes the merchant and the rows the preview counted, keeping their
    sources; `past: only_new` writes the merchant and marks each kept field
    of those rows checked. `token` (the preview's) is refused once the
    merchant, its rules or its rows have changed since. A rename, a note, or
    a change that reaches no row saves as it is. `role` is the part it plays
    as a party (party.ROLES; null or "" says none), and moves no row.
    Returns {success, recategorized, checked}."""
    data = request.get_json()
    with get_db() as conn:
        try:
            if isinstance(data, dict) and "role" in data:
                data = {**data, "role": party.checked_role(data["role"])}
        except party.Refused as e:
            return jsonify({"error": str(e)}), 400
        sets, params = _build_update_sets(
            data,
            ["name", "book", "type_id", "notes", "is_one_off", "exclude_from_expense_views",
             "review_each_time", "role"],
        )
        if not sets:
            return jsonify({"error": "Nothing to update"}), 400
        try:
            _checked_labels(conn, data)
            past = relabel_preview.checked_answer(data.get("past"))
        except (UnknownLabel, relabel_preview.Refused) as e:
            return jsonify({"error": str(e)}), 400
        params.append(svc_id)
        try:
            was = conn.execute(
                "SELECT book, type_id FROM services WHERE id = ?", (svc_id,)
            ).fetchone()
            if was is None:
                return jsonify({"error": "This merchant is no longer in fin"}), 404
            # What the merchant's rows would take: the book or type that
            # changes. A default sent back unchanged relabels nothing.
            moved = {field: data[field] for field in ("book", "type_id")
                     if field in data and data[field] != was[field]}
            plan = relabel_preview.merchant_change(conn, svc_id, moved)
            refused = _previewed_answer(conn, plan, past, data.get("token"))
            if refused is not None:
                return refused

            conn.execute(f"UPDATE services SET {', '.join(sets)} WHERE id = ?", params)
            recategorized, checked = _write_plan(conn, plan, past)
            conn.commit()
            invalidate_rules_cache()  # the rule cache holds each merchant's book and type
            return jsonify({"success": True, "recategorized": recategorized, "checked": checked})
        except Exception as e:
            app.logger.warning("Failed to update service: %s", e)
            return jsonify({"error": "Failed to update service"}), 400


# Turning Check each row on offers once to bring the merchant's past rows to
# the Queue: `leave` accepts them as they are (each field from fin becomes
# checked, so none waits), and `queue` leaves them waiting. Off clears the
# flag only: its rows keep their labels and wait no longer.
CHECK_EACH_ROW_PAST = ("leave", "queue")


@app.route("/api/services/<int:svc_id>/check-each-row", methods=["POST"])
def api_services_check_each_row(svc_id):
    """Set a merchant to Check each row, or back to Every row the same.
    Body: {on: bool, past: 'leave' | 'queue'} (past only with on; leave when
    left out). One change entry, undone whole: the flag and, with leave, the
    rows marked checked. Returns {success, on, checked, waiting}."""
    data = request.get_json(silent=True) or {}
    on = data.get("on")
    past = data.get("past") or "leave"
    if not isinstance(on, bool):
        return jsonify({"error": "on must be true or false"}), 400
    if past not in CHECK_EACH_ROW_PAST:
        return jsonify({"error": f"past must be one of {', '.join(CHECK_EACH_ROW_PAST)}"}), 400
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM services WHERE id = ?", (svc_id,)).fetchone():
            return jsonify({"error": "This merchant is no longer in fin"}), 404
        conn.execute("UPDATE services SET review_each_time = ? WHERE id = ?", (1 if on else 0, svc_id))
        theirs = f"SELECT t.id FROM transactions t WHERE t.service_id = ? AND {CHECKABLE_ROWS}"
        checked = 0
        if on and past == "leave":
            checked = _accept_past(conn, f"t.service_id = ? AND {LABELLED_ROWS}", (svc_id,))
        waiting = len(conn.execute(theirs, (svc_id,)).fetchall()) if on else 0
        conn.commit()
        invalidate_rules_cache()  # the rule cache holds each merchant's flag
    return jsonify({"success": True, "on": on, "checked": checked, "waiting": waiting})


# fin suggests Check each row (fin-surfaces 14) once the operator has moved
# this many of an Every row the same merchant's rows to another book.
BOOK_VARIES_AFTER = 2


@app.route("/api/services/<int:svc_id>/book-varies")
def api_services_book_varies(svc_id):
    """Whether fin asks to check each row of this merchant: {suggest, moved,
    to_book}. moved counts its spending and refund rows whose book is chosen
    and is not its own (a checked book is not counted); to_book is that book
    when they all share one. suggest is true for an Every row the same
    merchant with BOOK_VARIES_AFTER moved
    rows, unless the operator said No (screens.py keeps that). It writes nothing."""
    with get_db() as conn:
        svc = conn.execute("SELECT book, review_each_time FROM services WHERE id = ?", (svc_id,)).fetchone()
        if not svc:
            return jsonify({"error": "This merchant is no longer in fin"}), 404
        books = conn.execute(
            f"SELECT {book_expr()} AS book, COUNT(*) AS n FROM transactions t WHERE t.service_id = ?"
            " AND " + _left_to_label(label_source.book_varies_sql("t")) +
            " AND COALESCE(t.flow_type, 'expense') IN ('expense', 'refund')"
            f" AND {book_expr()} != ? GROUP BY 1",
            (svc_id, svc["book"]),
        ).fetchall()
        declined = svc_id in screens.declined_check_each_row(conn)
    moved = sum(b["n"] for b in books)
    return jsonify({
        "suggest": not svc["review_each_time"] and moved >= BOOK_VARIES_AFTER and not declined,
        "moved": moved,
        "to_book": books[0]["book"] if len(books) == 1 else None,
    })


@app.route("/api/services/<int:svc_id>/book-varies/decline", methods=["POST"])
def api_services_book_varies_decline(svc_id):
    """No to "check each row from now on?": fin does not ask again for this
    merchant. Kept in app_settings, so not a change to the book (no Undo)."""
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM services WHERE id = ?", (svc_id,)).fetchone():
            return jsonify({"error": "This merchant is no longer in fin"}), 404
        screens.decline_check_each_row(conn, svc_id)
    return jsonify({"success": True})


@app.route("/api/services/<int:svc_id>/merge", methods=["POST"])
def api_services_merge(svc_id):
    """Merge source service into target: reassign all FKs, delete source.

    The rows it moves that the target's book and type would relabel go
    through the preview (relabel_preview.merge_change): without `past`
    nothing is written and the preview comes back (409); `past: apply`
    gives them the target's labels (a chosen field, and one a rule override
    sets, keep their own); `past: only_new` moves them keeping their book
    and type, each field from fin checked. `token` is refused once either
    merchant, the source's rules or its rows changed since the preview."""
    data = request.get_json(silent=True) or {}
    target_id = data.get("target_id")
    if not target_id or int(target_id) == svc_id:
        return jsonify({"error": "Invalid merge target"}), 400
    target_id = int(target_id)
    try:
        past = relabel_preview.checked_answer(data.get("past"))
    except relabel_preview.Refused as e:
        return jsonify({"error": str(e)}), 400

    with get_db() as conn:
        # Verify both exist
        source = conn.execute("SELECT name FROM services WHERE id = ?", (svc_id,)).fetchone()
        target = conn.execute(
            "SELECT name, book, type_id FROM services WHERE id = ?", (target_id,)
        ).fetchone()
        if not source or not target:
            return jsonify({"error": "Service not found"}), 404

        plan = relabel_preview.merge_change(conn, svc_id, target_id)
        refused = _previewed_answer(conn, plan, past, data.get("token"))
        if refused is not None:
            return refused

        # Reassign all references from source → target
        txn_count = conn.execute(
            "UPDATE transactions SET service_id = ? WHERE service_id = ?",
            (target_id, svc_id),
        ).rowcount
        rule_count = conn.execute(
            "UPDATE merchant_rules SET service_id = ? WHERE service_id = ?",
            (target_id, svc_id),
        ).rowcount
        invalidate_rules_cache()  # the moved rules are the target's now
        relabelled, _ = _write_plan(conn, plan, past)
        sub_count = conn.execute(
            "UPDATE subscriptions SET service_id = ? WHERE service_id = ?",
            (target_id, svc_id),
        ).rowcount
        # The accounts joined to the source as their party join the target.
        party_count = conn.execute(
            "UPDATE accounts SET party_id = ? WHERE party_id = ?",
            (target_id, svc_id),
        ).rowcount

        # The source's folio link moves to the target, unless it has its own.
        folio_link.carry(conn, "party", svc_id, target_id)
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
            "accounts": party_count,
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
        joined = [r["name"] for r in conn.execute(
            "SELECT name FROM accounts WHERE party_id = ? ORDER BY name, id", (svc_id,))]
        if joined:
            names = joined[0] if len(joined) == 1 else f"{', '.join(joined[:-1])} and {joined[-1]}"
            return jsonify({"error": (
                f"{names} {'is' if len(joined) == 1 else 'are'} joined to this merchant as "
                f"{'its' if len(joined) == 1 else 'their'} party; take "
                f"{'it' if len(joined) == 1 else 'them'} off first. Nothing was changed"
            )}), 400
        folio_link.take_off(conn, "party", svc_id)
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

def _waiting_sides(conn, filters: str = "", params: tuple | list = ()) -> dict:
    """The transfers waiting for review, money out and money in apart: how
    many each side holds and what each adds up to in whole minor units, both
    as positive figures. `count` is both sides together and `net_minor` is
    money out less money in. `filters` narrows them by the row (t) or its
    statement (s). Every waiting row is counted; the totals are in S$, a row
    in another currency at the rate of its own day and one with no rate
    adding nothing (valuation.py). Once switched, the rows are _waiting_rows' and each adds
    what it has left of its own, never what is expected or placed on it."""
    rows, rows_params = _waiting_rows()
    own = expect_back.own_sql("t")
    # In S$ on the one path (valuation.py): a foreign row with no rate adds nothing.
    valued = f"sgd_value({own}, (SELECT currency FROM accounts WHERE id = s.account_id), t.date)"
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN t.amount_minor < 0 THEN 0 ELSE 1 END), "
        f"SUM(CASE WHEN t.amount_minor >= 0 THEN {valued} ELSE 0 END), "
        "SUM(CASE WHEN t.amount_minor < 0 THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN t.amount_minor < 0 THEN -{valued} ELSE 0 END) "
        "FROM transactions t "
        f"LEFT JOIN statements s ON t.statement_id = s.id WHERE {rows} {filters}",
        [*rows_params, *params],
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
    Override with ?ref_month=YYYY-MM. ?history=N (up to 24) adds history:
    each book's card and loan_principal for the N months ending with it.

    Respects: book, exclude_one_off, account_id

    The cards:
      household, household_rows   spending net of refunds in the Household
                                  book (a refund counts in the month it
                                  arrives), on accounts the household owns,
                                  archived or not, hidden merchants included
                                  (ruling 6). No company's row and no row
                                  waiting for review is in it.
      held_out_count, _total      the month's transfers waiting for review:
                                  what the household figure is held short of.
                                  _total is money out less money in;
                                  held_out_out_count, _out_total and
                                  held_out_in_count, _in_total state the two
                                  sides apart, each as a positive figure.
      moom, kalesh                each company's costs paid from accounts the
                                  household owns, net of refunds. Beside the
                                  headline, never added into it.
      <book>_out, _back, _out_rows,  the breakdown of each book's net card
      _back_rows, avg_<book>_out     (the chip reads it): money out, and the
                                     refunds back that came off it.
      <book>_missing                 no row in the month and an account that
                                     carried the book's rows should have sent
                                     a statement for it and has not
                                     (missing.py): not a real zero. The
                                     current month is never missing.
      waiting, waiting_total      every transfer waiting, whenever dated: the
                                  review list, with waiting_out_count,
                                  _out_total, waiting_in_count, _in_total
                                  the same way. untyped counts the month's
                                  spending rows with no type, less any whose
                                  own part is nothing (accounted for).
      loan_principal              the month's loan instalments less the
                                  interest worked out for it (loan_interest.py):
                                  not spending. loan_instalments and
                                  loan_interest are its two parts. cash_out is
                                  household plus loan_principal.
    spend is the View filter's figure (every book, or the one asked for, on
    any account), as the charts and the list show it; no card shows it.

    Expect back (ticket 29): every figure sums each row's own part (its amount
    less what is expected back on it or placed from it) plus the let-gos
    counted in the month, as trend_series.counted_lines gives them, and every
    count is of rows; a row whose own part is nothing is in no figure and no
    count. While expect_back's gate is off that is the rows' bare amounts.
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

    # The breakdown of each book's net card: money out, and money back in
    # refunds (a negative row), the part of the net that came off it.
    # Expect back (ticket 29): every sum reads counted lines
    # (trend_series.counted_lines): a row at its own part, and a let-go as a
    # line of its own on the day it counts. The sides split on the line's
    # sign; the counts are of rows (t.id), never of let-gos.
    side_sums = "".join(
        f"SUM(CASE WHEN {book_expr('t')} = ? AND a.owner = ? AND amount_minor > 0 THEN amount_minor ELSE 0 END), "
        f"SUM(CASE WHEN {book_expr('t')} = ? AND a.owner = ? AND amount_minor < 0 THEN -amount_minor ELSE 0 END), "
        f"COUNT(CASE WHEN {book_expr('t')} = ? AND a.owner = ? AND amount_minor > 0 THEN t.id END), "
        f"COUNT(CASE WHEN {book_expr('t')} = ? AND a.owner = ? AND amount_minor < 0 THEN t.id END), "
        for _ in books
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
            for name, _ in books:
                params += [name, account_kind.HOUSEHOLD] * 4
            params += [book_type.DEFAULT_BOOK, account_kind.HOUSEHOLD, start, end] + extra_params
            row = conn.execute(f"""
                SELECT
                    {book_sums}
                    {paid_sums}
                    {side_sums}
                    SUM(amount_minor),
                    COUNT(CASE WHEN t.type_id IS NULL THEN t.id END),
                    COUNT(t.id),
                    COUNT(CASE WHEN {book_expr('t')} = ? AND a.owner = ? THEN t.id END)
                FROM {trend_series.counted_from_sgd()}
                LEFT JOIN services svc ON t.service_id = svc.id
                LEFT JOIN accounts a ON s.account_id = a.id
                WHERE t.flow_type IN ('expense', 'refund')
                  AND t.date >= ? AND t.date <= ?
                  {extra_filters}
            """, params).fetchone()
            n = len(books)
            result = {key: row[i] or 0 for i, (_, key) in enumerate(books)}
            for i, (_, key) in enumerate(companies):
                result[f"paid_{key}"] = row[n + i] or 0
            n += len(companies)
            for _, key in books:
                result[f"out_{key}"] = row[n] or 0
                result[f"back_{key}"] = row[n + 1] or 0
                result[f"out_rows_{key}"] = row[n + 2] or 0
                result[f"back_rows_{key}"] = row[n + 3] or 0
                n += 4
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
            + [f"out_{key}" for _, key in books]
        }

        # A book with no row in the month while an account that carried its
        # rows in the year before should have sent a statement for the month
        # and has not: the card is missing, not a real zero (Spending hatches
        # the same month).
        ref_key = f"{ref_y:04d}-{ref_m:02d}"
        year_ago = f"{ref_y - 1:04d}-{ref_m:02d}-01"
        missing = {}
        for name, key in books:
            if ref_data[f"out_rows_{key}"] or ref_data[f"back_rows_{key}"]:
                continue
            carried = conn.execute(f"""
                SELECT DISTINCT s.account_id FROM transactions t
                JOIN statements s ON t.statement_id = s.id
                JOIN accounts a ON s.account_id = a.id
                WHERE {book_expr('t')} = ? AND a.owner = ?
                  AND t.flow_type IN ('expense', 'refund')
                  AND (a.status = 'active' OR a.status IS NULL)
                  AND a.type IN (?, ?)
                  AND t.date >= ? AND t.date < ?
            """, (name, account_kind.HOUSEHOLD, *account_kind.STATEMENT_KINDS, year_ago, f"{ref_key}-01")).fetchall()
            # Whether one of them should have sent a statement for the month
            # and has not is the trend's rule (missing.py, S3): the current
            # month is "so far", never missing.
            if missing_months(conn, [c[0] for c in carried], [ref_key], date.today()):
                missing[key] = True

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
        # Spending lines in another currency the month has no rate for: left
        # out of every figure above (valuation.py), never guessed.
        ref_first = f"{ref_y:04d}-{ref_m:02d}-01"
        ref_last = f"{ref_y:04d}-{ref_m:02d}-{calendar.monthrange(ref_y, ref_m)[1]:02d}"
        unvalued = valuation.unvalued_count(conn, ref_first, ref_last, extra_filters, extra_params,
                                            owner=account_kind.HOUSEHOLD)

        # ?history=N (at most 24): each card's figure for the N months ending
        # with the reference month, oldest first, worked out as the card is.
        history = []
        try:
            n_history = max(0, min(int(request.args.get("history") or 0), 24))
        except ValueError:
            n_history = 0
        hy, hm = ref_y, ref_m
        for _ in range(n_history):
            data = ref_data if (hy, hm) == (ref_y, ref_m) else query_month(hy, hm)
            month_loan = loan_interest.month_figures(conn, f"{hy:04d}-{hm:02d}")
            entry = {"month": f"{hy:04d}-{hm:02d}",
                     "loan_principal": money.from_minor(month_loan["principal_minor"])}
            for name, key in books:
                entry[key] = money.from_minor(data[key if name == book_type.DEFAULT_BOOK else f"paid_{key}"])
                entry[f"out_{key}"] = money.from_minor(data[f"out_{key}"])
            history.insert(0, entry)
            hm -= 1
            if hm == 0:
                hm, hy = 12, hy - 1

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
        "unvalued_count": unvalued,
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
    if n_history:
        payload["history"] = history
    for name, key in books:
        shown = key if name == book_type.DEFAULT_BOOK else f"paid_{key}"
        payload[key] = money.from_minor(ref_data[shown])
        payload[f"avg_{key}"] = money.from_minor(averages[shown])
        payload[f"{key}_out"] = money.from_minor(ref_data[f"out_{key}"])
        payload[f"{key}_back"] = money.from_minor(ref_data[f"back_{key}"])
        payload[f"{key}_out_rows"] = ref_data[f"out_rows_{key}"]
        payload[f"{key}_back_rows"] = ref_data[f"back_rows_{key}"]
        payload[f"avg_{key}_out"] = money.from_minor(averages[f"out_{key}"])
        payload[f"{key}_missing"] = bool(missing.get(key))
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

    Each row counts its own part and each let-go counts in its own type on
    its own day (trend_series.counted_lines; ticket 29).
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
            FROM {trend_series.counted_from_sgd()}
            {_TYPE_JOINS.format(owner="t")}
            LEFT JOIN services svc ON t.service_id = svc.id
            WHERE t.flow_type IN ('expense', 'refund') {filters}
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

    Each row counts its own part and each let-go counts in its own type on
    its own day (trend_series.counted_lines; ticket 29); count is of rows.
    """
    filters, params = _build_filters(request.args, hide=False)
    type_expr = _type_group_expr(request.args)

    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT
                {type_expr} as type,
                SUM(t.amount_minor) as total,
                COUNT(t.id) as count
            FROM {trend_series.counted_from_sgd()}
            {_TYPE_JOINS.format(owner="t")}
            LEFT JOIN services svc ON t.service_id = svc.id
            WHERE t.flow_type IN ('expense', 'refund') {filters}
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

def _listed_rows(args) -> tuple[str, list]:
    """The WHERE fragments and parameters of a list of rows, from its filters
    (/api/transactions and the Rows page's summary read the same). Raises
    flow.UnknownFlow for a flow that is not declared."""
    # A row asked for by id, or by its merchant, is shown whatever its
    # merchant's hiding says. every=true is the Rows page (ticket 19): every
    # row whatever its merchant's hiding, and a flow is the rows of that flow,
    # review included.
    every = args.get("every") == "true"
    hide = (not every and args.get("flow") != flow.REVIEW and args.get("tx_id") is None
            and args.get("service_id") is None
            and args.get("include_hidden") != "true")
    filters, params = _build_filters(args, hide=hide)
    # Only spending and refund rows carry a book: on the Rows page a book
    # leaves the other flows out, where a bare row would read as Household.
    if every and _requested_book(args):
        filters += f" AND COALESCE(t.flow_type, 'expense') IN ({','.join('?' * len(flow.LABELLED))})"
        params.extend(flow.LABELLED)

    flow_filter = args.get("flow")
    if flow_filter:
        flow.checked_flow(flow_filter)
        if flow_filter == flow.REVIEW and not every:
            # The review list lists exactly what the waiting count counts.
            rows_waiting, waiting_params = _waiting_rows()
            filters += f" AND {rows_waiting}"
            params.extend(waiting_params)
        else:
            filters += " AND COALESCE(t.flow_type, 'expense') = ?"
            params.append(flow_filter)

    # tx_id: one row, as a row's own sheet reads it.
    tx_id = args.get("tx_id", type=int)
    if tx_id is not None:
        filters += " AND t.id = ?"
        params.append(tx_id)

    # look=each: the rows waiting in the Queue's Check each row step (not
    # checked yet). look=mixed is its name from before the words changed.
    if args.get("look") in ("each", "mixed"):
        filters += f" AND {_not_checked_rows()}"

    # service_id: one merchant's rows, as its page reads them.
    service_id = args.get("service_id", type=int)
    if service_id is not None:
        filters += " AND t.service_id = ?"
        params.append(service_id)

    # party_id: a party's rows. Its merchant's own rows, and the rows on an
    # account linked to it or naming one as their other side (party.py).
    party_id = args.get("party_id", type=int)
    if party_id is not None:
        filters += (" AND (t.service_id = ?"
                    " OR s.account_id IN (SELECT id FROM accounts WHERE party_id = ?)"
                    " OR t.other_side_id IN (SELECT id FROM accounts WHERE party_id = ?))")
        params.extend([party_id] * 3)

    # Type filter (from chart selection or multi-select dropdown): type names,
    # a parent taking its sub-types with it. __untyped__ is the list of rows
    # with no type, which holds spending and refund rows only: a transfer, a
    # payment or income carries no type and is not waiting for one.
    types_str = args.get("types")
    if types_str:
        type_list = [t.strip() for t in types_str.split(",") if t.strip()]
        conds = []
        if UNTYPED_FILTER in type_list:
            type_list.remove(UNTYPED_FILTER)
            conds.append(_untyped_rows())
        if type_list:
            placeholders = ",".join("?" * len(type_list))
            conds.append(
                f"(ty.name IN ({placeholders}) OR COALESCE(tp.name, ty.name) IN ({placeholders}))"
            )
            params.extend(type_list * 2)
        if conds:
            filters += f" AND ({' OR '.join(conds)})"

    # Chart-driven date narrowing (adds to dashboard date filter)
    chart_start = args.get("chart_start")
    if chart_start:
        filters += " AND t.date >= ?"
        params.append(chart_start)
    chart_end = args.get("chart_end")
    if chart_end:
        filters += " AND t.date <= ?"
        params.append(chart_end)

    # expense_only: the spending and refund rows. A row all of whose amount
    # is expected back stays in it, reading S$ 0 with its pill (case 2): what
    # the list's sums add is each row's counted_minor, never the bare amount.
    if args.get("expense_only") == "true":
        filters += " AND t.flow_type IN ('expense', 'refund')"

    month = args.get("month")
    if month:
        filters += " AND strftime('%Y-%m', t.date) = ?"
        params.append(month)

    search = args.get("search")
    if search:
        filters += " AND (t.description LIKE ? OR svc.name LIKE ? OR ty.name LIKE ? OR tp.name LIKE ?)"
        search_param = f"%{search}%"
        params.extend([search_param] * 4)

    return filters, params


@app.route("/api/transactions")
def api_transactions():
    """Paginated transaction list with filters.

    Query params: start, end, book, exclude_one_off, types, account_id, month,
                  page, per_page, search, flow, include_hidden

    include_hidden=true lists the rows of merchants hidden from expense views
    too (default: they stay out).

    flow=review is the review list: the transfers waiting for a label. It
    hides no merchant, so it lists every row the waiting count counts.
    """
    try:
        filters, params = _listed_rows(request.args)
    except flow.UnknownFlow as e:
        return jsonify({"error": str(e)}), 400
    tx_id = request.args.get("tx_id", type=int)

    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    offset = (page - 1) * per_page

    # Sort column — whitelist valid columns to prevent SQL injection. Each is
    # (what comes last both ways, the column): an empty cell is last whichever
    # way the column runs (spec case 11), and text sorts ignoring case.
    sort_col = request.args.get("sort", "date")
    sort_dir = request.args.get("sort_dir", "desc").upper()
    own = expect_back.own_sql("t")  # by what each row counts: its own part once switched
    valid_sorts = {
        "date": (None, "t.date"),
        "description": (None, "t.description COLLATE NOCASE"),
        "type": ("ty.name IS NULL", "ty.name COLLATE NOCASE"),
        "service": ("svc.name IS NULL", "svc.name COLLATE NOCASE"),
        "account": (None, "a.name COLLATE NOCASE"),
        "amount": (None, own),
        # An account page's two money columns: a row on the other side is the
        # empty cell, so last; money in is a negative amount, read as its size.
        "amount_in": ("t.amount_minor >= 0", f"-{own}"),
        "amount_out": ("t.amount_minor < 0", own),
    }
    if sort_col not in valid_sorts:
        sort_col = "date"
    last_col, order_col = valid_sorts[sort_col]
    if sort_dir not in ("ASC", "DESC"):
        sort_dir = "DESC"
    # The id breaks any tie, so a page boundary neither repeats nor skips a row:
    # by date, rows of one day keep the order they came in, read either way.
    ties = (f"t.id {sort_dir}",) if sort_col == "date" else ("t.date DESC", "t.id DESC")
    order_by = ", ".join(c for c in (last_col, f"{order_col} {sort_dir}", *ties) if c)

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
                t.book_source, t.type_source, t.book as own_book,
                t.is_one_off, COALESCE(t.flow_type, 'expense') as flow_type, t.flow_type_manual,
                t.notes,
                t.other_side_id, oa.name as other_side_name,
                a.name as account_name, s.account_id,
                COALESCE(a.currency, 'SGD') as currency,
                t.service_id,
                svc.name as service_name,
                COALESCE(svc.review_each_time, 0) as review_each_time,
                CASE WHEN {_not_checked_rows()} THEN 1 ELSE 0 END as not_checked,
                rp.expected_minor, rp.placed_minor, rp.own_minor,
                sgd_value(rp.own_minor, a.currency, t.date) AS counted_sgd_minor,
                sgd_value(t.amount_minor, a.currency, t.date) AS sgd_minor,
                COALESCE(svc.exclude_from_expense_views, 0) AS hidden,
                (SELECT COALESCE(SUM(so.amount_minor), 0) FROM set_off so
                 WHERE so.payment_row_id = t.id AND so.expectation_id IS NULL) as waiting_minor
            FROM transactions t
            {type_joins}
            LEFT JOIN services svc ON t.service_id = svc.id
            JOIN statements s ON t.statement_id = s.id
            JOIN accounts a ON s.account_id = a.id
            LEFT JOIN accounts oa ON t.other_side_id = oa.id
            JOIN row_parts rp ON rp.row_id = t.id
            WHERE 1=1 {filters}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()

    txns = []
    for r in rows:
        tx = dict(r)
        # The amount in the account's own currency, which `currency` names:
        # the key is the old one, and holds rupees for a rupee account.
        tx["amount_sgd"] = money.from_minor(tx["amount_minor"], tx["currency"])
        # Its parts (spec 23): what is expected and placed on it, and its own
        # part, each in whole minor units with the row's sign. counted_minor
        # is what a sum of rows adds: the own part. The pill words them; the
        # client works nothing out.
        tx["counted_minor"] = tx["own_minor"]
        # The own part in S$ on the one path (valuation.py): at the rate of
        # the row's day; None for a row in another currency with no rate.
        tx["expect_pill"] = _parts_pill(tx["amount_minor"], tx["expected_minor"], tx["placed_minor"],
                                        tx.pop("waiting_minor"), tx["currency"])
        tx["display_type"] = format_type_display(r["parent_type"], r["type"])
        tx["not_checked"] = bool(tx["not_checked"])
        # Its merchant is hidden from spending: the Rows page lists it, and says so.
        tx["hidden"] = bool(tx["hidden"])
        # Where each field came from, as the marks show it (fin-surfaces 52).
        tx["sources"] = label_source.shown_sources({**tx, "book": tx.pop("own_book")})
        if tx.get("account_name"):
            tx["account_name"] = mask_card_number(tx["account_name"])
        if tx.get("other_side_name"):
            tx["other_side_name"] = mask_card_number(tx["other_side_name"])
        txns.append(tx)

    # One row, as its sheet reads it: who checked or chose each field, and
    # when, from the change history.
    if tx_id is not None and txns:
        with get_db() as conn:
            for tx in txns:
                tx["set_by"] = label_source.set_by(conn, tx["id"], tx["sources"])

    return jsonify({
        "transactions": txns,
        "total": count_row["cnt"],
        "page": page,
        "per_page": per_page,
        "pages": (count_row["cnt"] + per_page - 1) // per_page,
    })


@app.route("/api/rows/summary")
def api_rows_summary():
    """What the Rows page's filters hold (ticket 19), read with the list's own
    filters (`_listed_rows`, every=true): the rows of each flow under the other
    filters (the flow chips), and for the flow picked the rows, money in and
    money out in S$ on the one path (never a spending figure: a transfer counts
    on both sides), the rows in another currency with no rate (left out of the
    S$ figures, never guessed), and the rows waiting for a label as the Queue
    counts them, whatever the flow picked. Also the months
    the book holds, newest first, and the parties there are to pick."""
    args = request.args.copy()
    args["every"] = "true"
    picked = args.pop("flow", None)
    try:
        if picked:
            flow.checked_flow(picked)
        others, other_params = _listed_rows(args)
    except flow.UnknownFlow as e:
        return jsonify({"error": str(e)}), 400
    filters, params = others, list(other_params)
    if picked:
        filters += " AND COALESCE(t.flow_type, 'expense') = ?"
        params.append(picked)

    type_joins = _TYPE_JOINS.format(owner="t")
    rows_from = f"""FROM transactions t
            {type_joins}
            LEFT JOIN services svc ON t.service_id = svc.id
            JOIN statements s ON t.statement_id = s.id
            JOIN accounts a ON s.account_id = a.id"""
    with get_db() as conn:
        flows = {r["flow"]: r["n"] for r in conn.execute(
            f"SELECT COALESCE(t.flow_type, 'expense') AS flow, COUNT(*) AS n {rows_from} "
            f"WHERE 1=1 {others} GROUP BY 1", other_params)}
        figures = conn.execute(f"""
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(CASE WHEN v < 0 THEN -v ELSE 0 END), 0) AS in_minor,
                   COALESCE(SUM(CASE WHEN v > 0 THEN v ELSE 0 END), 0) AS out_minor,
                   SUM(CASE WHEN v IS NULL THEN 1 ELSE 0 END) AS unvalued
            FROM (SELECT sgd_value(t.amount_minor, a.currency, t.date) AS v
                  {rows_from} WHERE 1=1 {filters})""", params).fetchone()
        # Waiting: the Queue's rows (what its count counts), under the filters
        # other than the flow, so a flow picked never hides them.
        rows_waiting, waiting_params = _waiting_rows()
        waiting = conn.execute(f"SELECT COUNT(*) {rows_from} WHERE 1=1 {others} AND {rows_waiting}",
                               list(other_params) + waiting_params).fetchone()[0]
        months = [r[0] for r in conn.execute(
            "SELECT DISTINCT strftime('%Y-%m', date) FROM transactions ORDER BY 1 DESC")]
        # A party: the merchant of a row, or the party of the account a row is
        # on or names, among the rows the other filters hold (any party).
        party_args = args.copy()
        party_args.pop("party_id", None)
        pool, pool_params = _listed_rows(party_args)
        parties = [dict(r) for r in conn.execute(f"""
            SELECT p.id, p.name, COUNT(DISTINCT x.id) AS rows FROM (
                SELECT t.id, t.service_id AS pid {rows_from} WHERE 1=1 {pool}
                UNION ALL SELECT t.id, a.party_id {rows_from} WHERE 1=1 {pool}
                UNION ALL SELECT t.id, oa.party_id {rows_from}
                    JOIN accounts oa ON oa.id = t.other_side_id WHERE 1=1 {pool}) x
            JOIN services p ON p.id = x.pid
            GROUP BY p.id ORDER BY p.name COLLATE NOCASE""", pool_params * 3)]

    return jsonify({
        "flows": flows,
        "rows": figures["n"],
        "in_sgd_minor": figures["in_minor"],
        "out_sgd_minor": figures["out_minor"],
        "unvalued": figures["unvalued"] or 0,
        "waiting": waiting,
        "months": months,
        "latest_month": months[0] if months else None,
        "parties": parties,
    })


# ---------------------------------------------------------------------------
# Rows that carry records (personal-ledger 23: flow writers respect the
# records). An expectation sits, and a set-off's payment row is, only where
# nothing else on the sheet already counts the row (expect_back's eligibility
# rule). A label that would make such a row a move between the household's own
# accounts, a company's cost or a movement naming a non-person would count its
# money twice, so it is refused, naming the record; labelling the row's own
# part (spending, income, a gift) stays allowed. Since the contract no label
# or flow edit names a person as a movement's other side at all
# (review.PERSON_MOVEMENT_REFUSED). A rule moves such a row's flow only where
# a hand label could: its Apply, a teach and the drift keep the flow where the
# label would be refused, and the rule's preview lists it kept, in the
# refusal's words (expect_back.label_refusal, relabel_preview.flow_writes).
# ---------------------------------------------------------------------------

def _label_refusal(conn, tx_id: int, flow_name: str | None, book: str | None,
                   other_kind: str | None) -> str | None:
    """Why a label may not make this row what it would then be (its flow, book
    and other side's kind after the write), or None
    (expect_back.label_refusal, which a rule's flow keeps to as well)."""
    return expect_back.label_refusal(conn, tx_id, flow_name, book, other_kind)


def _relabel_refusal(conn, tx_id: int, book: str | None) -> str | None:
    """A label that changes only a row's book (and type): refused when the new
    book is a company's and a record rests on the row."""
    held = conn.execute(
        "SELECT t.flow_type, o.type AS other_kind FROM transactions t "
        "LEFT JOIN accounts o ON o.id = t.other_side_id WHERE t.id = ?", (tx_id,)
    ).fetchone()
    if held is None:
        return None
    return _label_refusal(conn, tx_id, held["flow_type"], book, held["other_kind"])


@app.route("/api/transactions/<int:tx_id>", methods=["PUT"])
def api_update_transaction(tx_id: int):
    """Update a transaction's notes, book, type, or one-off flag. A row may
    override both the book and the type its merchant gives it. Each of book
    and type sent becomes chosen (label_source); a field not sent keeps its
    source. Chat's set_label is this route.

    With `expectation` (the mark route's body: {direction, all, amount, names,
    new_names, note}) the row is also marked as expected back, or to pay back,
    in the same change (fin-redesign 12: chat's mark_expect_back with a book
    or type): the label is written first, so the mark's own guards read the
    row as labelled, and a refused mark writes neither."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "No valid fields to update"}), 400

    sets, values = _build_update_sets(data, ["notes", "is_one_off"])
    labels = {field: data[field] or None for field in label_source.FIELDS if field in data}
    expectation = data.get("expectation")
    if expectation is not None and not isinstance(expectation, dict):
        return jsonify({"error": "send the mark as JSON"}), 400

    if not sets and not labels and expectation is None:
        return jsonify({"error": "No valid fields to update"}), 400

    with get_db() as conn:
        try:
            _checked_labels(conn, data)
        except UnknownLabel as e:
            return jsonify({"error": str(e)}), 400
        if "book" in data:
            why = _relabel_refusal(conn, tx_id, data["book"])
            if why:
                return jsonify({"error": why}), 400
        if sets:
            conn.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE id = ?", (*values, tx_id))
        held = conn.execute(f"SELECT {_LABEL_HELD} FROM transactions t WHERE t.id = ?", (tx_id,)).fetchone()
        labelled = False
        if held is not None and labels:
            chosen = {label_source.FIELDS[f]: label_source.given(v, label_source.CHOSEN) for f, v in labels.items()}
            labelled = _write_label(conn, held, {**labels, **chosen})
        if expectation is None:
            conn.commit()
            return jsonify({"ok": True, "id": tx_id})
        # The mark commits the label with it, or rolls both back.
        try:
            marked = expectation_api.write_mark(conn, tx_id, expectation)
        except expectation_api.NotFound as e:
            return jsonify({"error": str(e)}), 404
        except expect_back.Refused as e:
            return jsonify({"error": str(e)}), 400
        entry_id = g.get("change_entry")
        if labelled and entry_id is not None:
            conn.execute("UPDATE change_entries SET summary = summary || ' and labelled it' "
                         "WHERE id = ? AND summary != ''", (entry_id,))
            conn.commit()
    return jsonify({"ok": True, "id": tx_id, **marked})


@app.route("/api/transactions/bulk-label", methods=["POST"])
def api_bulk_label():
    """Labels on many rows at once, each with its own book and type, as their
    lines show them: {rows: [{tx_id, book?, type_id?}], chosen?}. Each field
    sent is checked where it is the row's own value and chosen where it
    differs; with chosen true (Set book), chosen whatever it is. A row's book
    or type left out (or null) keeps its value and its source. One
    transaction, one change entry. Refused whole, writing nothing, on any
    unknown book, type or row. Returns {success, labelled, ids}."""
    data = request.get_json(silent=True) or {}
    lines = data.get("rows")
    if not isinstance(lines, list) or not lines:
        return jsonify({"error": "rows must be a list of {tx_id, book, type_id}"}), 400
    chosen = data.get("chosen", False)
    if not isinstance(chosen, bool):
        return jsonify({"error": "chosen must be true or false"}), 400
    with get_db() as conn:
        seen = set()
        for line in lines:
            tx_id = line.get("tx_id") if isinstance(line, dict) else None
            if isinstance(tx_id, bool) or not isinstance(tx_id, int):
                return jsonify({"error": "every row needs its tx_id"}), 400
            if tx_id in seen:
                return jsonify({"error": f"row {tx_id} is listed twice"}), 400
            seen.add(tx_id)
            try:
                _checked_labels(conn, line)
            except UnknownLabel as e:
                return jsonify({"error": str(e)}), 400
        ids = list(seen)
        found = set()
        for i in range(0, len(ids), 900):
            chunk = ids[i:i + 900]
            found.update(r[0] for r in conn.execute(
                f"SELECT id FROM transactions WHERE id IN ({','.join('?' * len(chunk))})", chunk
            ))
        gone = [i for i in ids if i not in found]
        if gone:
            return jsonify({"error": f"{len(gone)} of these rows {'is' if len(gone) == 1 else 'are'} "
                                     "no longer in fin (changed elsewhere); nothing was saved"}), 400
        for line in lines:
            book = line.get("book")
            if book not in (None, ""):
                why = _relabel_refusal(conn, line["tx_id"], book)
                if why:
                    return jsonify({"error": why}), 400
        _label_lines(conn, lines, chosen)
        conn.commit()
    return jsonify({"success": True, "labelled": len(ids), "ids": ids})


def _label_lines(conn, lines: list[dict], chosen: bool) -> None:
    """Bulk-label's write, its lines already checked: each field sent is
    checked where it is the row's own value and chosen where it differs (or
    chosen whatever it is, with `chosen`). Not committed."""
    for line in lines:
        held = conn.execute(
            f"SELECT {_LABEL_HELD} FROM transactions t WHERE t.id = ?", (line["tx_id"],)
        ).fetchone()
        sets = {}
        for field, column in label_source.FIELDS.items():
            sent = line.get(field)
            if sent in (None, ""):
                continue
            sets[field] = sent
            sets[column] = label_source.bulk(field, sent, held[field], _source(held, column), chosen)
        _write_label(conn, held, sets)


def _resolved_sources(held, apply_scope: str, book, type_id, merchant_book) -> tuple:
    """The (book source, type source) resolve gives its own row: for this row
    only, chosen where the value changes (a field left as it was keeps its
    own); a rule override from the rule for the field it sets, from the
    merchant for the other; the merchant's default from the merchant."""
    if apply_scope == "transaction":
        return tuple(
            label_source.changed_or_kept(
                field, value, held[field] if held else None, _source(held, column) if held else None,
                label_source.CHOSEN,
            )
            for (field, column), value in zip(label_source.FIELDS.items(), (book, type_id))
        )
    if apply_scope == "rule":
        book_source, type_source = label_source.rule_sources(
            {"book_override": book if book != merchant_book else None, "type_override_id": type_id})
    else:
        book_source = type_source = label_source.MERCHANT
    return label_source.given(book, book_source), label_source.given(type_id, type_source)


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
        wordings: other wordings the rule also recognises, each one more
            'contains' rule for the merchant in the same change; one refused
            refuses the whole request (_other_wordings)
        match_type: 'contains' (default) or 'startswith'
        apply_scope: where the label applies —
            'transaction'     this row only (a row may override both)
            'rule'            a rule override for the pattern
            'service_default' the merchant's default (the default)
        check_each_row: the merchant setting, in the same transaction
            (service_default only; left out, the merchant keeps its own).
            true is Check each row: the flag, and the rows the new rule
            reaches wait to be checked. false is Every row the same: the flag
            is cleared, labels kept.
        past: with check_each_row true, the operator's answer to the offer
            for a merchant fin already held as Every row the same: 'leave'
            marks its past rows checked (labels unchanged), in the same
            change; 'queue', or left out, leaves them waiting.
        past_rows, token: the answer to the preview (409) a teach asks with
            when it reaches past rows: a merchant's new default, rows another
            merchant holds, or a backfilled row whose flow it would move
            (preview.flows; flows_kept the rows whose flow it keeps). Only
            'apply' writes those flows.

    Answers flows: the row's own flow change, where the teach moves it (a
    merchant whose rules state a flow, for a row labelled for itself), and
    flows_kept where a record resting on a row keeps its flow.

    Flow:
        1. Find existing service by service_id or name, or create new one
        2. Create merchant rule linking pattern → service (→ its book and type)
        3. Update the transaction with service_id + book + type_id
        4. Backfill any other NULL-service transactions matching the new rule
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    with get_db() as conn:
        try:
            taught, _ = _teach(conn, data)
        except TeachRefused as e:
            conn.rollback()
            return jsonify(e.body), e.status
        conn.commit()
    invalidate_rules_cache()
    return jsonify(taught)


class TeachRefused(Exception):
    """A teach was refused: `body` and `status` are the route's answer. The
    caller rolls back, so nothing of it stands."""

    def __init__(self, body: dict, status: int = 400):
        super().__init__(body.get("error"))
        self.body, self.status = body, status


def _teach(conn, data: dict, *, record_choice: bool = True,
           plan_token: bool = False) -> tuple[dict, "relabel_preview.Plan | None"]:
    """The resolve route's teach (its body, `data`) on `conn`, not committed:
    the caller commits, so a plan can run many teaches in one change
    (chat-plan-apply 01). Returns what the route answers and the preview's
    plan the teach was asked about (None where nothing asked). Raises
    TeachRefused, whatever it wrote then the caller's to roll back.
    `record_choice` False leaves the suggestion's record of the operator's
    choice alone: a plan's teach is not one, and Undo would not take it back.
    `plan_token` True: a plan's item, whose move_chosen stands on the plan's
    own token (its items stamped), not on a teach's preview token."""
    tx_id = data.get("tx_id")
    service_name = (data.get("service_name") or "").strip()
    service_id = data.get("service_id")
    pattern = (data.get("pattern") or "").strip()
    match_type = data.get("match_type", "contains")
    apply_scope = data.get("apply_scope") or "service_default"

    if not tx_id:
        raise TeachRefused({"error": "tx_id required"})
    if not service_name and not service_id:
        raise TeachRefused({"error": "service_name or service_id required"})
    if apply_scope == "rule" and not pattern:
        raise TeachRefused({"error": "pattern required for rule override"})
    check_each_row = data.get("check_each_row")
    if check_each_row is not None and not isinstance(check_each_row, bool):
        raise TeachRefused({"error": "check_each_row must be true or false"})
    if check_each_row is not None and apply_scope != "service_default":
        raise TeachRefused({"error": "Check each row is a merchant setting: it goes with every row from this merchant"})
    past = data.get("past")
    if past is not None and (check_each_row is not True or past not in CHECK_EACH_ROW_PAST):
        raise TeachRefused({"error": f"past goes with check_each_row true, as one of {', '.join(CHECK_EACH_ROW_PAST)}"})
    try:
        past_rows = relabel_preview.checked_answer(data.get("past_rows"), "past_rows")
        move_chosen = relabel_preview.checked_move_chosen(data.get(relabel_preview.MOVE_CHOSEN), past_rows)
    except relabel_preview.Refused as e:
        raise TeachRefused({"error": str(e)})
    pattern_error = _rule_pattern_error(pattern)
    if pattern and apply_scope in {"rule", "service_default"} and pattern_error:
        raise TeachRefused({"error": pattern_error})
    try:
        wordings = _other_wordings(data.get("wordings"), pattern, apply_scope)
    except BadWording as e:
        raise TeachRefused({"error": str(e)})
    # Every wording the teach's rules recognise: the pattern, then the others.
    taught_words = [pattern, *wordings] if pattern and apply_scope in {"rule", "service_default"} else []
    flow_type = data.get("flow_type")
    if flow_type is not None:
        try:
            flow.checked_flow(flow_type)
        except flow.UnknownFlow as e:
            raise TeachRefused({"error": str(e)})
        why = shared.not_ours_refusal(conn, _account_facts(conn, tx_id).get("account_id"), flow_type)
        if why:
            raise TeachRefused({"error": why})

    try:
        book = _checked_book(data.get("book"))
        type_id = _checked_type_id(conn, data.get("type_id"))
    except UnknownLabel as e:
        raise TeachRefused({"error": str(e)})
    # A flow edit that would make a row naming a person a movement names
    # the person as a movement's other side: refused since the contract
    # (personal-ledger 23), before anything is written.
    if flow_type == flow.MOVEMENT and conn.execute(
        "SELECT 1 FROM transactions t JOIN accounts o ON o.id = t.other_side_id "
        "WHERE t.id = ? AND o.type = 'person'", (tx_id,)
    ).fetchone():
        raise TeachRefused({"error": review.PERSON_MOVEMENT_REFUSED})

    try:
        # Step 1: Resolve service — find existing or create new
        if service_id:
            svc = conn.execute(
                "SELECT id, book, type_id, review_each_time FROM services WHERE id = ?", (service_id,)
            ).fetchone()
            if not svc:
                raise TeachRefused({"error": f"Service ID {service_id} not found"}, 404)
        else:
            # Look up by name (case-insensitive)
            svc = conn.execute(
                "SELECT id, book, type_id, review_each_time FROM services WHERE UPPER(name) = ?",
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
            merchant_book = book if apply_scope == "service_default" else svc["book"]
        else:
            # Create new service — the type is required
            if not type_id:
                raise TeachRefused({"error": "type_id required for new service"})
            book = _book_or_proposed(conn, book, type_id)
            merchant_book = book

        # Service-default resolution can update the merchant's default,
        # and then its past rows go through the preview, as the merchant
        # sheet's do (fin-surfaces 21); so does a wording that would take
        # rows another merchant holds (fin-surfaces 53). Without past_rows
        # nothing is written and the preview comes back, one question for
        # both. Before any write, so a refusal leaves the book as it was.
        merchant_plan = taken_plan = None
        if svc and apply_scope == "service_default" and (
            (book, type_id) != (svc["book"], svc["type_id"])
        ):
            moved = {f: v for f, v in (("book", book), ("type_id", type_id)) if v != svc[f]}
            merchant_plan = relabel_preview.merchant_change(conn, service_id, moved, leave_out={tx_id},
                                                             teaching=True)
        # The rules before the teach and as it leaves them: a backfilled row,
        # or one the wording takes from another merchant, that a rule
        # stating a flow labels takes its flow as a rule's Apply gives it,
        # and a teach that would move any asks first (_teach_flows,
        # _taken_flows): they are written only on past_rows apply.
        rules_before = db.load_rules(conn)
        # The merchant's stated flow (flow.merchant_stated): a rule the teach
        # adds states it, as does a wording's rule it moves here from another
        # merchant, and a row labelled for itself takes it (step 3).
        merchant_said = flow.merchant_stated(rules_before, service_id) if svc else None
        taught_rules = (rules_before, relabel_preview.taught_rules(
            rules_before, taught_words, match_type, merchant_said, service_id if svc else None))
        if taught_words:
            reach = relabel_preview.together([
                relabel_preview.wording_reach(conn, w, match_type, service_id if svc else None) for w in taught_words])
            if reach.others:
                shown_name = conn.execute(
                    "SELECT name FROM services WHERE id = ?", (service_id,)).fetchone()[0] if svc else service_name
                taken_plan = relabel_preview.taking(
                    conn, reach, shown_name, {"book": book, "type_id": type_id},
                    label_source.rule_sources(_taught_overrides(apply_scope, book, type_id, merchant_book)))
                _taken_flows(conn, taken_plan, service_id if svc else None, tx_id, taught_rules)
        plan = relabel_preview.joined(merchant_plan, taken_plan)
        taught = label_source.rule_sources(_taught_overrides(apply_scope, book, type_id, merchant_book))
        fill_flows, fill_kept = _teach_flows(conn, service_id, tx_id, taught_words, match_type,
                                             book, type_id, taught, taught_rules)
        # The row taught as it is before the teach writes anything: its flow
        # is worked out again only where it is still fin's (step 3).
        target_held = conn.execute(f"SELECT {_LABEL_HELD} FROM transactions t WHERE t.id = ?", (tx_id,)).fetchone()
        # A backfilled row kept only because its book or type was chosen by
        # hand is asked about too: the operator may move it (move_chosen).
        fill_by_hand = [k for k in fill_kept if k.by_hand]
        if plan is None and (fill_flows or fill_by_hand):
            name = conn.execute("SELECT name FROM services WHERE id = ?", (service_id,)).fetchone()[0] \
                if svc else service_name
            # Its rows are the merchant's to be: shown as moving to it, as a
            # taught wording's rows are (relabel_preview._example), and the
            # wording taught is its change, as a taught wording's is
            # (relabel_preview.taking): the drawer is titled by it.
            plan = relabel_preview.Plan(subject={"kind": "merchant", "id": service_id, "name": name},
                                        change={}, called=name, moves_to=name,
                                        wording=" · ".join(taught_words))
        if plan is not None:
            # Where the teach asks anyway, the merchant's own rows still
            # waiting for a type that it labels itself in the same change,
            # whatever the answer (its backfill), are counted too, as the
            # merchant sheet counts them (case 13): so the count is the
            # past rows the change writes. Rows with no merchant are the
            # teach's own, waiting as the row taught is, never counted
            # (case 29), but for one whose flow it would move: the preview
            # lists every such row (flows), and those it keeps (flows_kept).
            if (plan.count or fill_flows or fill_by_hand) and taught_words:
                fills = {}
                for wording in taught_words:
                    for held, sets in _teach_fills(conn, service_id, tx_id, wording, match_type, book, type_id,
                                                   taught):
                        sets = {"service_id": service_id, **sets}
                        if held["id"] in fills:
                            continue
                        if held["id"] in fill_flows or (svc and held["service_id"] == service_id
                                                        and _would_change(held, label_source.fitted(held, sets))):
                            fills[held["id"]] = sets
                plan = relabel_preview.with_fills(conn, plan, fills, fill_flows, fill_kept)
            refused = _previewed_answer(conn, plan, past_rows, data.get("token"), "past_rows",
                                        move_chosen and not plan_token)
            if refused is not None:
                raise TeachRefused(refused[0].get_json(), refused[1])
        hand_moved = []
        if move_chosen:
            # Answered with move_chosen on the preview's token: the rows kept
            # only because their book or type was chosen move as the others
            # do, the backfill's with the flow it would have written, the
            # rows a wording takes as their moved write (_taken_flows).
            # Listed still, as moving (flows_chosen): never moved unseen (RUL-019).
            hand_moved = [*fill_by_hand, *(taken_plan.by_hand if taken_plan is not None else ())]
            for k in fill_by_hand:
                fill_flows[k.held["id"]] = k.would
            fill_kept = [k for k in fill_kept if not k.by_hand]
            if taken_plan is not None:
                taken_plan = relabel_preview.with_chosen(taken_plan)
        if merchant_plan is not None:
            conn.execute(
                "UPDATE services SET book = ?, type_id = ? WHERE id = ?",
                (book, type_id, service_id),
            )
            _write_plan(conn, merchant_plan, past_rows)
        if not svc:
            conn.execute(
                "INSERT INTO services (name, book, type_id, review_each_time) VALUES (?, ?, ?, ?)",
                (service_name, book, type_id, _new_checks_each_row(conn, type_id)),
            )
            service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        if taken_plan is not None:
            _write_taken(conn, taken_plan, past_rows, service_id)

        # The setting, as taught: Check each row, or Every row the same.
        # A merchant fin already held, newly set to check each row, has
        # its past rows answered by the screen's offer (past): leave marks
        # them checked, as the setting route's "Not now" does; queue, or no
        # answer, leaves them waiting. Before the backfill, so the rows
        # this save labels wait whatever the answer.
        if past == "leave" and svc and not svc["review_each_time"]:
            _accept_past(conn, f"t.service_id = ? AND t.id != ? AND {LABELLED_ROWS}", (service_id, tx_id))
        if check_each_row is not None:
            conn.execute("UPDATE services SET review_each_time = ? WHERE id = ?",
                         (1 if check_each_row else 0, service_id))

        # Step 2: Create a merchant rule for each wording, the pattern's
        # first (none for a PayNow/transfer taught without one).
        # A rule override carries the type, and the book only where it
        # is not the merchant's. A new rule states the merchant's stated
        # flow and other side, where its rules agree on one.
        rule_ids = []
        backfilled_ids = []
        overrides = _taught_overrides(apply_scope, book, type_id, merchant_book)
        override_type_id, override_book = overrides["type_override_id"], overrides["book_override"]
        # The flows the backfill writes: on past_rows apply only, those the
        # preview listed (_teach_flows).
        flows_written = fill_flows if past_rows == relabel_preview.APPLY else {}
        # Rows taken from another merchant whose flow Apply moved (_taken_flows).
        moved_flow = past_rows == relabel_preview.APPLY and taken_plan is not None and any(
            relabel_preview.moves_flow(p) for p in taken_plan.rows)
        for wording in taught_words:
            rule_exists = conn.execute(
                "SELECT id, service_id FROM merchant_rules WHERE UPPER(pattern) = ?",
                (wording.upper(),),
            ).fetchone()
            if not rule_exists:
                conn.execute(
                    "INSERT INTO merchant_rules (pattern, service_id, book_override, "
                    "type_override_id, match_type, confidence, flow, other_side_id) "
                    "VALUES (?, ?, ?, ?, ?, 'confirmed', ?, ?)",
                    (wording, service_id, override_book, override_type_id, match_type,
                     *(merchant_said or (None, None))),
                )
                rule_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            else:
                rule_ids.append(rule_exists["id"])
                conn.execute(
                    "UPDATE merchant_rules SET service_id = ?, book_override = ?, "
                    "type_override_id = ? WHERE id = ?",
                    (service_id, override_book, override_type_id, rule_exists["id"]),
                )
                # A rule moved here from another merchant states this
                # merchant's stated flow, or none: never the old merchant's
                # (relabel_preview.taught_rules, as the preview read it).
                if rule_exists["service_id"] != service_id:
                    conn.execute("UPDATE merchant_rules SET flow = ?, other_side_id = ? WHERE id = ?",
                                 (*(merchant_said or (None, None)), rule_exists["id"]))

            # Backfill the other rows matching this wording that have no
            # merchant, or have this merchant but still no type: the
            # rows a preview, where one was asked, counted as the teach's
            # own (_teach_fills), with the flows it listed on apply.
            for held, sets in _teach_fills(conn, service_id, tx_id, wording, match_type, book, type_id, taught):
                flows = flows_written.get(held["id"], {})
                # Fitted to the flow it leaves the row (label_source.fitted).
                if _write_label(conn, held, label_source.fitted(held, {"service_id": service_id, **sets, **flows})):
                    backfilled_ids.append(held["id"])
                    moved_flow = moved_flow or any(held[k] != v for k, v in flows.items())
        rule_id = rule_ids[0] if rule_ids else None
        backfilled = len(backfilled_ids)

        # Step 3: Update the target transaction with explicit provenance.
        # flow_type override is independent of the label (ADR v2):
        #   - if caller supplies flow_type, set it + mark flow_type_manual=1
        #   - otherwise work the flow out again as a rule's Apply does
        #     (relabel_preview.flow_writes, with the rule that labelled the
        #     row before the teach and the one that labels it now, or, for a
        #     row labelled for itself, the merchant's stated flow where it has
        #     one): never a flow set by hand or one fin did not work out, and
        #     kept where a hand label would refuse the move, a record resting
        #     on the row (a chosen label does not keep it: teaching this row
        #     is the operator's own write on it)
        tx_row = conn.execute(
            f"SELECT {_LABEL_HELD} FROM transactions t WHERE t.id = ?", (tx_id,),
        ).fetchone()
        label = dict(zip(("book", "type_id", "service_id", "book_source", "type_source"),
                         (book, type_id, service_id,
                          *_resolved_sources(tx_row, apply_scope, book, type_id, merchant_book))))
        kept = [*fill_kept, *(taken_plan.kept if taken_plan is not None else ())]
        moved_lines = []
        if flow_type is not None:
            sets = {**label, "flow_type": flow_type, "flow_type_manual": 1}
        elif tx_row:
            description, amount = tx_row["description"] or "", tx_row["amount_minor"]
            now = db.pick_rule(db.load_rules(conn), description, amount)
            if not taught_words and merchant_said is not None:
                now = {"flow": merchant_said[0], "other_side_id": merchant_said[1]}
            flows, keeps = relabel_preview.flow_writes(
                conn, target_held, {"service_id": service_id, "type_id": type_id, "book": book},
                db.pick_rule(rules_before, description, amount), now,
                flow.build_context(conn), _account_facts(conn, tx_id),
                keep_chosen=False)            # the row taught: the operator's own write
            if keeps is not None:
                kept.append(keeps)
            # Out of refund, the row leaves its purchase, as _write_label's rows do.
            if target_held["flow_type"] == "refund" and flows.get("flow_type", "refund") != "refund":
                refund_links.drop(conn, tx_id)
            sets = {**label, **flows}
            moved_flow = moved_flow or bool(flows)
            # The answer names the row's flow change: none moves unsaid.
            if flows:
                moved_lines.append((target_held, flows))
        else:
            sets = label
        # The label goes only on a spending or refund row: the row taught
        # keeps its merchant whatever its flow, and a flow the teach moves
        # out of spending empties its book and type (label_source.fitted).
        if tx_row:
            sets = label_source.fitted(tx_row, sets)
        if sets:
            conn.execute(f"UPDATE transactions SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                         (*sets.values(), tx_id))
        # A row that now names a loan, or no longer does, changes what was paid.
        if moved_flow:
            loan_interest.derive(conn)

        # Afterwards, for a merchant the model was asked about: the type
        # the operator chose and whether the suggestion was on screen.
        if record_choice:
            suggest.record_choice(
                conn, tx_id, type_id, data.get("suggestion_visible") is True,
            )

        # The row's book and type as stored: none on a row that is no one's
        # spending, whatever the teach was sent (label_source.fitted).
        stored = conn.execute("SELECT book, type_id FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        return {
            "success": True,
            "service_id": service_id,
            "rule_id": rule_id,
            "rule_ids": rule_ids,
            "book": stored["book"] if stored else book,
            "type_id": stored["type_id"] if stored else type_id,
            "backfilled": backfilled,
            "backfilled_ids": backfilled_ids,
            "flows": relabel_preview.moved_lines(conn, moved_lines),
            **({"flows_kept": relabel_preview.kept_lines(conn, kept)} if kept else {}),
            **({"flows_chosen": [*relabel_preview.chosen_lines(conn, kept),
                                 *relabel_preview.chosen_lines(conn, hand_moved, moves=True)]}
               if hand_moved or any(k.by_hand for k in kept) else {}),
        }, plan
    except TeachRefused:
        raise
    except BookNeeded as e:
        raise TeachRefused({"error": str(e)}) from None
    except Exception as e:
        app.logger.warning("Failed to resolve transaction: %s", e)
        raise TeachRefused({"error": "Failed to resolve transaction"}) from None


# ---------------------------------------------------------------------------
# A plan: a batch of Queue teaches, previewed, then applied as one change
# (chat-plan-apply 01). Each item is what a Queue card's save sends resolve,
# its rows as tx_ids; the apply runs those teaches and nothing else.
# ---------------------------------------------------------------------------

MAX_PLAN_ITEMS = 20
# What an item takes: resolve's body for a card, its rows in place of its row.
_PLAN_ITEM_KEYS = frozenset({
    "tx_ids", "service_name", "service_id", "pattern", "wordings", "match_type",
    "type_id", "book", "check_each_row", "past", "past_rows", relabel_preview.MOVE_CHOSEN,
})
_PLAN_ITEM_TEXT = ("service_name", "pattern", "match_type", "book", "past", "past_rows")
PLAN_STALE = "the book changed since the preview: preview again"
# Columns a write stamps with the clock: left out of the token, so a preview
# and its apply a second later stamp the same.
_CLOCK_COLUMNS = frozenset({"created_at", "made_at"})
# What a row is, as a plan's preview lists its moves.
_FLOW_COLUMNS = ("flow_type", "other_side_id")
# The ids an item names, held to what SQLite stores (a signed 64-bit integer).
_PLAN_ITEM_IDS = ("service_id", "type_id")
_SQLITE_INT = range(-2 ** 63, 2 ** 63)


class PlanRefused(ValueError):
    """A plan the routes cannot take as sent; nothing was run."""


def _plan_items(data) -> list[dict]:
    """The items as sent, checked for shape before anything runs: 1 to
    MAX_PLAN_ITEMS, each with its own rows (at most QUEUE_ROW_CAP, as many as
    the Queue shows), no row in two items, every id one SQLite can hold."""
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_PLAN_ITEMS:
        raise PlanRefused(f"items must be a list of 1 to {MAX_PLAN_ITEMS} merchants' teaches")
    seen = set()
    for n, item in enumerate(items):
        if not isinstance(item, dict):
            raise PlanRefused("each item is one merchant's teach")
        unknown = set(item) - _PLAN_ITEM_KEYS
        if unknown:
            raise PlanRefused(f"an item does not take {', '.join(sorted(unknown))}")
        said = [k for k in _PLAN_ITEM_TEXT if item.get(k) is not None and not isinstance(item[k], str)]
        if said:
            raise PlanRefused(f"{said[0]} must be text")
        if item.get("match_type") not in (None, *relabel_preview.MATCH_TYPES):
            raise PlanRefused(f"match_type must be one of {', '.join(relabel_preview.MATCH_TYPES)}")
        rows = item.get("tx_ids")
        if not isinstance(rows, list) or not rows or not all(
                isinstance(i, int) and not isinstance(i, bool) for i in rows):
            raise PlanRefused("each item needs tx_ids, the rows it labels")
        if len(rows) > QUEUE_ROW_CAP:
            raise PlanRefused(f"item {n}: at most {QUEUE_ROW_CAP} rows, as many as the Queue shows")
        out = next((tx for tx in rows if tx not in _SQLITE_INT), None)
        if out is not None:
            raise PlanRefused(f"item {n}: row id {out} is out of range")
        out = next((k for k in _PLAN_ITEM_IDS if isinstance(item.get(k), int) and item[k] not in _SQLITE_INT), None)
        if out is not None:
            raise PlanRefused(f"item {n}: {out} is out of range")
        for tx in rows:
            if tx in seen:
                raise PlanRefused(f"row {tx} is in the plan twice")
            seen.add(tx)
    return items


def _plan_item_name(conn, item: dict) -> str:
    """The merchant an item names: its name, or the held merchant's."""
    name = item.get("service_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    svc = item.get("service_id")
    found = conn.execute("SELECT name FROM services WHERE id = ?", (svc,)).fetchone() if isinstance(svc, int) else None
    return found[0] if found else "a merchant"


def _teach_item(conn, item: dict) -> tuple[int, "relabel_preview.Plan | None", list]:
    """One item's teach, as a Queue card's save makes it (static/app.js
    saveResolve): resolve on its first row with the wording and the other
    wordings; each of its other rows the rules did not reach, this row only;
    taught as Check each row, its rows then checked with their book. Rows
    another merchant holds that a wording reaches change only on past_rows
    apply; left unanswered, only new rows take it. A wording that matches no
    row is refused, as the card refuses it. Returns the merchant, the
    preview's plan, and the rows its teach kept only because their book or
    type was chosen by hand (flows_chosen; move_chosen with past_rows apply
    moves them). Not committed; raises TeachRefused."""
    tx_ids = item["tx_ids"]
    marks = ",".join("?" * len(tx_ids))
    held = {r[0] for r in conn.execute(f"SELECT id FROM transactions WHERE id IN ({marks})", tx_ids)}
    gone = [tx for tx in tx_ids if tx not in held]
    if gone:
        raise TeachRefused({"error": f"row {gone[0]} is no longer in fin"})
    pattern = item.get("pattern") if isinstance(item.get("pattern"), str) else ""
    body = {k: v for k, v in item.items() if k != "tx_ids"}
    body.update(tx_id=tx_ids[0], apply_scope="service_default" if pattern.strip() else "transaction")
    if body["apply_scope"] == "service_default":
        if body.get("past_rows") is None:
            body["past_rows"] = relabel_preview.ONLY_NEW
        others = item.get("wordings") if isinstance(item.get("wordings"), list) else []
        for wording in [pattern, *others]:
            if isinstance(wording, str) and wording.strip() and not relabel_preview.wording_reach(
                    conn, wording, item.get("match_type") or "contains").count:
                raise TeachRefused({"error": f"The wording {wording.strip()} matches no row"})
    taught, plan = _teach(conn, body, record_choice=False, plan_token=True)
    service_id, label = taught["service_id"], {"book": taught["book"], "type_id": taught["type_id"]}
    for tx in tx_ids[1:]:
        if conn.execute("SELECT type_id FROM transactions WHERE id = ?", (tx,)).fetchone()[0] is None:
            _teach(conn, {"tx_id": tx, "service_id": service_id, "apply_scope": "transaction", **label},
                   record_choice=False)
    if item.get("check_each_row") is True:
        # Checked with their book where they are spending or refunds: a row
        # the teach moved out of spending carries no label to check.
        lines = []
        for tx in tx_ids:
            svc_now, flow_now = conn.execute(
                "SELECT service_id, flow_type FROM transactions WHERE id = ?", (tx,)).fetchone()
            if svc_now == service_id and flow.carries_label(flow_now):
                lines.append({"tx_id": tx, **label})
        for line in lines:
            why = _relabel_refusal(conn, line["tx_id"], line["book"])
            if why:
                raise TeachRefused({"error": why})
        _label_lines(conn, lines, chosen=False)
    return service_id, plan, taught.get("flows_chosen", [])


def _stable(data: str | None) -> tuple | None:
    """A recorded row as the token stamps it: every field but the clock's."""
    if data is None:
        return None
    return tuple(sorted((k, v) for k, v in json.loads(data).items() if k not in _CLOCK_COLUMNS))


def _run_plan(conn, items: list[dict]) -> dict:
    """Every item's teach, in order, on `conn` and not committed: what the
    preview shows is what the apply writes, read back from the rows this
    request's change entry recorded. Each item runs in its own savepoint, so
    a refused one is taken back whole and named, and the rest still run: the
    preview names every refusal. The caller commits or rolls back.

    The token stamps the items as sent, each teach's preview token (the
    rows it read), and every row the plan changes, before and after: a
    preview's token holds for its apply only while the same items would make
    the same change to the same rows, so a row changed since, or a second
    apply of the same plan, is refused (relabel_preview._stamp)."""
    entry_id = g.get("change_entry")
    if entry_id is None:
        raise RuntimeError("a plan runs inside a request's change entry")
    if not conn.in_transaction:
        conn.execute("BEGIN")   # so releasing an item's savepoint never commits
    shown, refusals, stamped = [], [], []
    # Every row the plan changes, the rows it labels (another merchant's row a
    # wording reaches, left unanswered, only takes a checked mark), and the
    # merchants it labels them with: a new one by its id, so two items for one
    # merchant are one.
    rows, labelled_rows, merchants = set(), set(), set()
    for n, item in enumerate(items):
        name = _plan_item_name(conn, item)
        mark = conn.execute("SELECT COALESCE(MAX(id), 0) FROM change_rows").fetchone()[0]
        conn.execute("SAVEPOINT plan_item")
        try:
            service_id, plan, by_hand = _teach_item(conn, item)
        except TeachRefused as e:
            conn.execute("ROLLBACK TO plan_item")
            refusals.append({"item": n, "merchant": name, "error": e.body.get("error") or "refused"})
            continue
        finally:
            conn.execute("RELEASE plan_item")
            invalidate_rules_cache()   # it may hold this item's rules, not yet committed
        changes = conn.execute(
            "SELECT tbl, row_id, op, before, after FROM change_rows WHERE entry_id = ? AND id > ? ORDER BY id",
            (entry_id, mark),
        ).fetchall()
        stamped.append((n, plan.token if plan is not None else None,
                        [(c["tbl"], c["row_id"], c["op"], _stable(c["before"]), _stable(c["after"])) for c in changes]))
        labelled, was, flows = {}, {}, {}
        for c in changes:
            if c["tbl"] != "transactions":
                continue
            rows.add(c["row_id"])
            before = json.loads(c["before"]) if c["before"] else {}
            after = json.loads(c["after"]) if c["after"] else {}
            # The row as it stood before this item: a row that held a type
            # shows its label, so the yes sees the plan overwrite it; and its
            # flow and other side then and after the item, so the preview
            # lists every row whose flow the item moves.
            if c["row_id"] not in was:
                was[c["row_id"]] = None if before.get("type_id") is None else {
                    k: before.get(k) for k in ("service_id", "book", "type_id", "book_source", "type_source")}
                # With its label: the line says what a move out of spending empties.
                flows[c["row_id"]] = {"id": c["row_id"],
                                      **{k: before.get(k) for k in (*_FLOW_COLUMNS, "book", "type_id")}}
            if c["op"] == "update":
                flows[c["row_id"]]["writes"] = {k: after.get(k) for k in _FLOW_COLUMNS}
            if after.get("service_id") == service_id:
                labelled[c["row_id"]] = {**{k: after.get(k) for k in ("id", "date", "description", "amount_minor")},
                                         "was": was[c["row_id"]]}
        labelled_rows.update(labelled)
        merchants.add(service_id)
        new = any(c["tbl"] == "services" and c["op"] == "insert" and c["row_id"] == service_id for c in changes)
        preview = None
        if plan is not None and plan.count:
            preview = {k: v for k, v in relabel_preview.summary(conn, plan).items() if k != "token"}
        shown.append({
            "item": n,
            "merchant": name,
            "new_merchant": new,
            # A merchant the plan makes has no id until it is applied.
            "service_id": None if new else service_id,
            "rows": sorted(labelled.values(), key=lambda r: (r["date"] or "", r["id"])),
            "count": len(labelled),
            "overlaps": list(plan.others) if plan is not None else [],
            # The answer the past rows take: unanswered, only new rows.
            "past_rows": (item.get("past_rows") or relabel_preview.ONLY_NEW) if preview else None,
            "preview": preview,
            # Every row whose flow or other side the item moves, the rows
            # labelled for themselves among them: the token stamps each.
            "flows": relabel_preview.moved_lines(conn, [
                (f, f["writes"]) for f in flows.values()
                if "writes" in f and any(f[k] != f["writes"][k] for k in _FLOW_COLUMNS)]),
            # The rows it keeps only because their book or type was chosen by
            # hand: move_chosen (with past_rows apply) moves them too.
            "flows_chosen": by_hand,
        })
    token = None if refusals else relabel_preview._stamp(
        "plan", json.dumps(items, sort_keys=True), stamped)
    return {"items": shown, "refusals": refusals, "merchants": len(merchants), "rows": len(labelled_rows),
            "rows_checked": len(rows - labelled_rows), "token": token}


@app.route("/api/plans/preview", methods=["POST"])
def api_plans_preview():
    """A plan's preview, {items: [...]}: per merchant the rows it would
    label (each with `was`, the label the row held, or null), the rows of
    another merchant a wording would reach (and, where past rows would
    change, the preview's numbers), any refusal by merchant, the merchants
    and rows labelled, the other rows only checked (rows_checked), and one
    token for the apply (none while anything is refused). The plan runs as
    the apply would, then is rolled back: it writes nothing."""
    try:
        items = _plan_items(request.get_json(silent=True))
    except PlanRefused as e:
        return jsonify({"error": str(e)}), 400
    with get_db() as conn:
        try:
            shown = _run_plan(conn, items)
        finally:
            conn.rollback()
            invalidate_rules_cache()
    return jsonify(shown)


@app.route("/api/plans/apply", methods=["POST"])
def api_plans_apply():
    """Apply a previewed plan, {items: [...], token}: the same items as the
    preview, its token. The plan runs again and is written as one change
    entry, "Applied a plan: N merchants, M rows", which one Undo takes back.
    Refused, writing nothing, when any item is refused (400, the refusals by
    merchant) or the token is not the one these items make on the book now
    (409: a row changed since the preview, or the plan was applied already)."""
    data = request.get_json(silent=True)
    try:
        items = _plan_items(data)
    except PlanRefused as e:
        return jsonify({"error": str(e)}), 400
    token = data.get("token")
    if not isinstance(token, str) or not token:
        return jsonify({"error": "apply takes the preview's token: preview the plan first"}), 400
    with get_db() as conn:
        try:
            shown = _run_plan(conn, items)
            if shown["refusals"]:
                conn.rollback()
                return jsonify({"error": "nothing was saved: an item was refused",
                                "refusals": shown["refusals"]}), 400
            if shown["token"] != token:
                conn.rollback()
                return jsonify({"error": PLAN_STALE, "stale": True}), 409
            via, _ = _caller()
            said = ", ".join(f"{n} {word}{'' if n == 1 else 's'}"
                             for n, word in ((shown["merchants"], "merchant"), (shown["rows"], "row")))
            _name_change(conn, f"{'Chat applied' if via == history.VIA_CHAT else 'Applied'} a plan: {said}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            invalidate_rules_cache()
    return jsonify({"success": True, "merchants": shown["merchants"], "rows": shown["rows"],
                    "rows_checked": shown["rows_checked"], "items": shown["items"]})


@app.route("/api/book-prefill", methods=["POST"])
def api_book_prefill():
    """The book each row's picker shows first, and why: {tx_ids: [...]} ->
    {prefill: {tx_id: {book, reason, account, account_kind}}}. reason is
    `last` (this merchant's last hand-set book on this account), `account` (a
    company's own account) or `type` (the type's book; null means it asks).
    One read for every row on screen; it writes nothing."""
    data = request.get_json(silent=True)
    ids = data.get("tx_ids") if isinstance(data, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        return jsonify({"error": "tx_ids must be a list of row ids"}), 400
    with get_db() as conn:
        found = book_prefill.book_prefills(conn, ids)
    for hit in found.values():
        hit["account"] = mask_card_number(hit["account"] or "")
    return jsonify({"prefill": found})


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
    # The list itself is the operator's to read, in the app or in the file;
    # the file's path is not returned (hosted, it would name the volume).
    return jsonify({"count": len(merchants), "merchants": merchants})


@app.route("/api/suggestions/send", methods=["POST"])
def api_suggestions_send():
    """Step two: one request per line the operator left in the file, each
    answer stored in full. The first failed call stops the batch.

    An optional JSON body `{"merchants": [...]}` is the operator's ticks in
    the app's list (the hosted fin, where the file cannot be opened): only
    names both still in the file and ticked are sent. A name fin did not
    prepare is never sent, whatever is ticked."""
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
    data = request.get_json(silent=True)
    if data is not None:
        ticked = data.get("merchants") if isinstance(data, dict) else None
        if not isinstance(ticked, list) or not all(isinstance(m, str) for m in ticked):
            return jsonify({"error": "merchants must be a list of names"}), 400
        left_in_file &= {m.strip() for m in ticked}

    sent = 0
    stopped = None
    with get_db() as conn:
        # Only strings fin itself would have written: a line added by hand,
        # or one already answered, is not sent.
        to_send = [m for m in suggest.unanswered(conn) if m in left_in_file]
        for merchant in to_send:
            try:
                record = suggest.ask(merchant, api_key, conn)
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
    where, offered = _suggested(answer, type_ids)
    return jsonify({"route": where, "merchant": merchant, "types": offered})


def _suggested(answer, type_ids: dict) -> tuple[str, list]:
    """A stored answer as the screens are given it: the route and the types
    it offers. No answer is blank. A type the answer names that the book no
    longer has is left out (a rename re-points the answers, so this is one
    taken out by Undo); an answer left with none is blank."""
    if answer is None:
        return "blank", []
    where, offered = suggest.route(answer["pick"], json.loads(answer["probabilities"]))
    types = [
        {"type_id": type_ids[name], "name": name, "probability": probability}
        for name, probability in offered if name in type_ids
    ]
    return (where, types) if types else ("blank", [])


# A row's parts as the transaction list reads them, over `row_parts rp`: its
# own part (what the Queue shows and sorts by) and what its pill words.
_PARTS_COLUMNS = """rp.own_minor, rp.expected_minor, rp.placed_minor,
               (SELECT COALESCE(SUM(so.amount_minor), 0) FROM set_off so
                WHERE so.payment_row_id = t.id AND so.expectation_id IS NULL) AS waiting_minor"""


def _untyped_groups(conn, on: bool) -> list[dict]:
    """Every row with no type, grouped by merchant with the stored guess for
    each group: {merchant, route, types, rows}, the biggest row's group first.
    A group is the merchant string the gate would send, or the row's
    description, upper-cased and trimmed, when the gate stops the row (a
    PayNow, a GIRO). A row gets the route the per-row read gives it: blank
    with no key, for a row whose merchant is set, or for a row the gate
    stops, so those rows group apart from asked rows of the same string."""
    rows = conn.execute(f"""
        SELECT t.id, t.date, t.description, t.amount_minor, t.flow_type, t.service_id,
               {book_expr('t')} AS book, a.type AS account_kind, a.name AS account_name,
               COALESCE(a.currency, 'SGD') AS currency, {_PARTS_COLUMNS}
        FROM transactions t
        LEFT JOIN services svc ON t.service_id = svc.id
        JOIN statements s ON t.statement_id = s.id
        JOIN accounts a ON s.account_id = a.id
        JOIN row_parts rp ON rp.row_id = t.id
        WHERE {_untyped_rows()} AND {_expense_visibility_filter('svc')}
        ORDER BY t.amount_minor DESC, t.id
    """).fetchall()
    held = suggest.stored_answers(conn) if on else {}
    type_ids = book_type.spending_type_ids(conn) if on else {}
    groups = {}
    for r in rows:
        merchant = suggest.merchant_string(r["description"], r["flow_type"], r["account_kind"])
        asked = on and merchant is not None and r["service_id"] is None
        name = merchant if merchant is not None else (r["description"] or "").strip().upper()
        group = groups.get((name, asked))
        if group is None:
            where, offered = _suggested(held.get(name), type_ids) if asked else ("blank", [])
            group = groups[(name, asked)] = {"merchant": name, "route": where, "types": offered, "rows": []}
        group["rows"].append(r)
    return list(groups.values())


@app.route("/api/queue/guesses")
def api_queue_guesses():
    """Every row with no type, grouped by merchant, with the stored guess for
    each group (_untyped_groups): what the Queue and Home draw, in one read
    instead of one per row. Read from the stored answers; it never asks and
    writes nothing."""
    with get_db() as conn:
        groups = _untyped_groups(conn, suggest.key() is not None)
    return jsonify({"groups": [{
        "merchant": g["merchant"], "route": g["route"], "types": g["types"],
        "tx_ids": [r["id"] for r in g["rows"]],
    } for g in groups]})


# The Queue read whole for chat (chat-queue-read 01): no list in it holds more
# than this many rows; past it the list says "showing N of M", as the Queue does.
# Chat reads all three lists in one answer, so the biggest come and the rest
# wait until those are labelled.
QUEUE_ROW_CAP = 100


def _queue_row(r) -> dict:
    """A row as a Queue card lists it: its date, words, amount and the own part
    the Queue shows (with the pill saying why they differ), account and book."""
    return {
        "id": r["id"], "date": r["date"], "description": r["description"],
        "amount_minor": r["amount_minor"], "counted_minor": r["own_minor"],
        "expect_pill": _parts_pill(r["amount_minor"], r["expected_minor"], r["placed_minor"],
                                   r["waiting_minor"], r["currency"]),
        "currency": r["currency"], "account": mask_card_number(r["account_name"] or ""), "book": r["book"],
    }


def _queue_card_groups(conn, on: bool) -> list[dict]:
    """The rows with no type as the Queue draws their cards, each group
    {merchant, route, types, side, rows}: rows Jev has a guess for (sure or
    three) one card per merchant string, any other row one card per its
    description, upper-cased and trimmed (merchantCards), each split by side,
    money out or in (sureGroups, laneCards). The biggest row's card first, by
    the own part the Queue sorts on. /api/queue/guesses keeps its own groups."""
    placed = [(g, r) for g in _untyped_groups(conn, on) for r in g["rows"]]
    placed.sort(key=lambda p: (p[1]["own_minor"], p[1]["date"] or "", p[1]["id"]), reverse=True)
    cards = {}
    for g, r in placed:
        guessed = g["route"] != "blank"
        name = g["merchant"] if guessed else (r["description"] or "").strip().upper()
        side = "in" if r["amount_minor"] < 0 else "out"
        card = cards.get((side, guessed, name))
        if card is None:
            card = cards[(side, guessed, name)] = {
                "merchant": name, "route": g["route"], "types": g["types"], "side": side, "rows": []}
        card["rows"].append(r)
    return list(cards.values())


def _queue_list(conn, where: str, params: list, *, hide: bool, limit: int = QUEUE_ROW_CAP,
                after: tuple | None = None) -> tuple[list, int]:
    """The rows `where` picks, the biggest own part first as the Queue sorts
    them, up to `limit`, and how many it picks in all. `hide` leaves out
    the merchants hidden from expense views, as the Queue's list of them does.
    `after` (a page's cursor, _queue_cursor) starts past that row."""
    if hide:
        where = f"{where} AND {_expense_visibility_filter('svc')}"
    joins = """FROM transactions t
        LEFT JOIN services svc ON t.service_id = svc.id
        LEFT JOIN types ty ON t.type_id = ty.id
        LEFT JOIN types tp ON ty.parent_id = tp.id
        JOIN statements s ON t.statement_id = s.id
        JOIN accounts a ON s.account_id = a.id"""
    total = conn.execute(f"SELECT COUNT(*) {joins} WHERE {where}", params).fetchone()[0]
    rows = conn.execute(f"""
        SELECT t.id, t.date, t.description, t.amount_minor, {book_expr('t')} AS book,
               a.name AS account_name, COALESCE(a.currency, 'SGD') AS currency,
               t.service_id, svc.name AS service_name, svc.book AS service_book, t.type_id,
               ty.name AS type, tp.name AS parent_type, {_PARTS_COLUMNS}
        {joins}
        JOIN row_parts rp ON rp.row_id = t.id
        WHERE {where}{" AND (rp.own_minor, t.date, t.id) < (?, ?, ?)" if after else ""}
        ORDER BY rp.own_minor DESC, t.date DESC, t.id DESC
        LIMIT ?
    """, [*params, *(after or ()), limit]).fetchall()
    return rows, total


def _shown_of(shown: int, total: int) -> str:
    return f"showing {shown} of {total}" if total > shown else ""


def _prefill_hint(p: dict) -> str:
    """Where a prefilled book came from, in the Queue's words (prefillHint);
    none when it is the type's."""
    if p["reason"] == book_prefill.LAST:
        return f"{p['book']} last time on {mask_card_number(p['account'] or '')}"
    if p["reason"] == book_prefill.ACCOUNT:
        return f"{p['book']} {'card' if p['account_kind'] == 'card' else 'account'}"
    return ""


def _group_prefill(rows: list, prefills: dict, top_type_id: int | None, proposes: dict) -> dict:
    """A card's prefilled book and why, as the Queue's sure card fills it
    (sharedPrefill): every row's prefill when they all name one book from
    history or a company's account; else the book Jev's sure type proposes;
    else none, and the book is asked."""
    got = [prefills.get(r["id"]) for r in rows]
    if got and all(p and p["book"] and p["reason"] != book_prefill.TYPE and p["book"] == got[0]["book"]
                   for p in got):
        hints = list(dict.fromkeys(_prefill_hint(p) for p in got))[:2]
        return {"book": got[0]["book"], "reason": got[0]["reason"], "hint": "; ".join(hints)}
    return {"book": proposes.get(top_type_id), "reason": book_prefill.TYPE, "hint": ""}


def _card_rows(rows: list, prefills: dict, prefill: dict) -> tuple[dict, list]:
    """A card's rows as chat reads them, saying nothing twice: the currency
    and account every row shares are said once, on the card (None there when
    they differ, and each row says its own), and a row names its own prefilled
    book only when it is from history or a company's account and differs from
    the card's."""
    listed = [_queue_row(r) for r in rows]
    shared = {k: listed[0][k] if all(x[k] == listed[0][k] for x in listed) else None
              for k in ("currency", "account")}
    for x in listed:
        for k, v in shared.items():
            if v is not None:
                del x[k]
        own = prefills.get(x["id"])
        if own and own["book"] and own["reason"] != book_prefill.TYPE and (
                own["book"], own["reason"]) != (prefill["book"], prefill["reason"]):
            x["prefill"] = {"book": own["book"], "reason": own["reason"]}
    return shared, listed


def _queue_cards(conn, shown: list) -> list[dict]:
    """The cards chat reads for `shown`, each (group, its rows oldest first,
    the rows listed): its rows, Jev's guess, the name and wording fin
    suggests (from the first row: it names the merchant the card's save
    makes), the prefilled book and why, and the merchant fin holds by that
    name."""
    # The merchant a name finds, ignoring case: the first by name, as the
    # card takes it from the merchants list.
    held = {}
    for svc in conn.execute(
        "SELECT svc.id, svc.name, svc.type_id, ty.name AS type, svc.book,"
        " COALESCE(svc.review_each_time, 0) AS review_each_time"
        " FROM services svc LEFT JOIN types ty ON ty.id = svc.type_id ORDER BY svc.name, svc.id"
    ):
        held.setdefault(svc["name"].lower(), {**dict(svc), "review_each_time": bool(svc["review_each_time"])})
    proposes = book_type.proposed_books(conn)
    prefills = book_prefill.book_prefills(conn, [r["id"] for _, _, listed in shown for r in listed])
    cards = []
    for g, rows, listed in shown:
        name = name_wording.suggest_name(rows[0]["description"])
        top = g["types"][0]["type_id"] if g["route"] == "prefill" and g["types"] else None
        prefill = _group_prefill(listed, prefills, top, proposes)
        shared, card_rows = _card_rows(listed, prefills, prefill)
        cards.append({
            "merchant": g["merchant"], "route": g["route"], "types": g["types"], "side": g["side"],
            "suggested_name": name,
            "suggested_wording": name_wording.suggest_pattern(rows[0]["description"]),
            "prefill": prefill,
            "held": held.get(name.lower()) if name else None,
            **shared,
            "row_count": len(rows),
            "rows": card_rows,
        })
    return cards


def _check_each_row(r) -> dict:
    """A row waiting under Check each row, as chat reads it: its merchant,
    type and usual book beside the row's own."""
    return {
        **_queue_row(r), "merchant": r["service_name"], "service_id": r["service_id"],
        "type": r["type"], "display_type": format_type_display(r["parent_type"], r["type"]),
        "type_id": r["type_id"], "usual_book": r["service_book"] or r["book"],
    }


@app.route("/api/queue/cards")
def api_queue_cards():
    """The Queue's labelling work as its cards hold it, for chat to read: the
    rows with no type in their merchant groups (each with Jev's guess, the
    name and wording fin suggests, the prefilled book and why, and the
    merchant fin holds by that name), the rows waiting under Check each row,
    and the transfers waiting for a label. Each list holds at most
    QUEUE_ROW_CAP rows and says when it is cut. Read from what is stored; it
    never asks Jev and writes nothing.

    With section (and limit, after), one pile a page at a time instead
    (_queue_page): the bare read's one budget is filled by the biggest cards,
    so a small card waits behind them (fin-redesign item 27)."""
    if any(k in request.args for k in ("section", "limit", "after")):
        return _queue_page()
    on = suggest.key() is not None
    with get_db() as conn:
        groups = _queue_card_groups(conn, on)
        each, each_total = _queue_list(conn, _not_checked_rows(), [], hide=True)
        waiting_where, waiting_params = _waiting_rows()
        transfers, transfers_total = _queue_list(conn, waiting_where, waiting_params, hide=False)
        waiting = _waiting_sides(conn)
        # The groups up to the cap, the rows of each oldest first: the first
        # names the merchant the card's save makes.
        room, shown = QUEUE_ROW_CAP, []
        for g in groups:
            if room <= 0:
                break
            rows = sorted(g["rows"], key=lambda r: (r["date"] or "", r["id"]))
            shown.append((g, rows, rows[:room]))
            room -= len(rows[:room])
        cards = _queue_cards(conn, shown)

    untyped_total = sum(len(g["rows"]) for g in groups)
    lists = {
        "untyped": {"groups": cards, "total": untyped_total,
                    "shown": _shown_of(QUEUE_ROW_CAP - room, untyped_total)},
        "check_each": {"rows": [_check_each_row(r) for r in each], "total": each_total,
                       "shown": _shown_of(len(each), each_total)},
        "transfers": {
            "waiting": waiting["count"], "waiting_total": money.from_minor(waiting["net_minor"]),
            **_sides_payload("waiting", waiting),
            "rows": [_queue_row(r) for r in transfers], "total": transfers_total,
            "shown": _shown_of(len(transfers), transfers_total),
        },
    }
    # The Queue's own words for a list it cut short.
    cut = [f"{what}: {lists[key]['shown']}" for key, what in (
        ("transfers", "transfers waiting"), ("untyped", "rows with no type"), ("check_each", "rows to check"),
    ) if lists[key]["shown"]]
    return jsonify({**lists, "cut": cut})


# The Queue's piles, as chat pages them (fin-redesign item 27): the rows with
# no type (untyped), split by Jev's guess as the Queue draws them (sure: the
# sure card; pick: Pick a type; no_guess: No guess, every card with Jev off),
# then Check each row and the transfers waiting. mcp_tools' queue tool names
# the same list; a test holds the two together.
QUEUE_SECTIONS = ("untyped", "sure", "pick", "no_guess", "check_each", "transfers")
_QUEUE_PILE_ROUTE = {"sure": "prefill", "pick": "top3", "no_guess": "blank"}


def _queue_key(r) -> tuple:
    """Where a row stands in the Queue's order, the biggest own part first:
    a card stands where its biggest row does."""
    return (r["own_minor"], r["date"] or "", r["id"])


def _queue_cursor(r) -> str:
    return "{}:{}:{}".format(*_queue_key(r))


def _queue_after(text: str) -> tuple:
    own, day, row_id = text.split(":")
    return (int(own), day, int(row_id))


def _queue_page():
    """One pile of the Queue, a page at a time, for chat to reach every card:
    section (QUEUE_SECTIONS), limit (the rows a page holds, 1 to
    QUEUE_ROW_CAP, QUEUE_ROW_CAP when not given) and after (the cursor the
    page before gave). A page of cards ends at a card's end, so no card is
    split across pages; a card over the limit comes alone with its first
    rows, and its row_count says how many it holds. next is the cursor for
    the page after, None on the last. The cursor is where the page's last
    card or row stands in the Queue's order, so a card labelled whole between
    pages moves no other card. A card stands where its biggest row does: one
    that gains a bigger row can jump behind the cursor, and one whose biggest
    row alone is labelled can come again, so chat pages again from the start
    when the pile moved (the tool says so)."""
    section = request.args.get("section")
    if section is None:
        return jsonify({"error": "limit and after go with a section"}), 400
    if section not in QUEUE_SECTIONS:
        return jsonify({"error": f"section must be one of {', '.join(QUEUE_SECTIONS)}"}), 400
    try:
        limit = int(request.args.get("limit", QUEUE_ROW_CAP))
    except ValueError:
        limit = 0
    if not 1 <= limit <= QUEUE_ROW_CAP:
        return jsonify({"error": f"limit is a number of rows from 1 to {QUEUE_ROW_CAP}"}), 400
    after = None
    if request.args.get("after") is not None:
        try:
            after = _queue_after(request.args["after"])
        except ValueError:
            return jsonify({"error": "after is not a cursor the queue gave"}), 400

    with get_db() as conn:
        if section in ("check_each", "transfers"):
            where, params, hide = ((_not_checked_rows(), [], True) if section == "check_each"
                                   else (*_waiting_rows(), False))
            rows, total = _queue_list(conn, where, params, hide=hide, limit=limit + 1, after=after)
            listed = rows[:limit]
            shown = _check_each_row if section == "check_each" else _queue_row
            return jsonify({
                "section": section, "rows": [shown(r) for r in listed], "total": total,
                "next": _queue_cursor(listed[-1]) if len(rows) > limit else None,
            })

        groups = _queue_card_groups(conn, suggest.key() is not None)
        if section in _QUEUE_PILE_ROUTE:
            groups = [g for g in groups if g["route"] == _QUEUE_PILE_ROUTE[section]]
        # A card's first row in the list is its biggest (_queue_card_groups).
        left = [g for g in groups if after is None or _queue_key(g["rows"][0]) < after]
        page, used = [], 0
        for g in left:
            rows = sorted(g["rows"], key=lambda r: (r["date"] or "", r["id"]))
            if page and used + len(rows) > limit:
                break
            page.append((g, rows, rows[:limit]))
            used += len(rows[:limit])
        cards = _queue_cards(conn, page)
    return jsonify({
        "section": section, "groups": cards, "cards": len(groups),
        "total": sum(len(g["rows"]) for g in groups),
        "next": _queue_cursor(page[-1][0]["rows"][0]) if len(page) < len(left) else None,
    })


@app.route("/api/queue/summary")
def api_queue_summary():
    """How much waits in each of the Queue's piles (QUEUE_SECTIONS), as
    cards and rows, and whether Jev guesses at all (with Jev off every card
    with no type is no_guess): counts only, for chat to see the whole Queue
    before it pages a pile. A card under Check each row is a merchant's rows;
    a transfer is its own card. Read from what is stored; it never asks Jev
    and writes nothing."""
    on = suggest.key() is not None
    with get_db() as conn:
        groups = _queue_card_groups(conn, on)
        each = conn.execute(f"""
            SELECT COUNT(*), COUNT(DISTINCT t.service_id) FROM transactions t
            LEFT JOIN services svc ON t.service_id = svc.id
            WHERE {_not_checked_rows()} AND {_expense_visibility_filter('svc')}
        """).fetchone()
        waiting_where, waiting_params = _waiting_rows()
        _, transfers = _queue_list(conn, waiting_where, waiting_params, hide=False, limit=0)

    def pile(gs):
        return {"cards": len(gs), "rows": sum(len(g["rows"]) for g in gs)}

    return jsonify({
        "jev": on,
        "untyped": pile(groups),
        **{name: pile([g for g in groups if g["route"] == route]) for name, route in _QUEUE_PILE_ROUTE.items()},
        "check_each": {"cards": each[1], "rows": each[0]},
        "transfers": {"cards": transfers, "rows": transfers},
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


# What an import preview holds as facts: the confirm writes these as the
# upload read them, and takes from its request only the choices below.
_PREVIEW_ROW_FACTS = (
    "date", "description", "amount_sgd", "amount_foreign", "currency_foreign", "account", "statement",
)
_CONFIRM_ROW_CHOICES = (
    "book", "type_id", "service_id", "flow_type", "other_side_id", "_skip", "is_one_off",
)
PREVIEW_NOT_OPEN = "This preview is no longer open. Upload the statement again."
PREVIEW_MISMATCH = "The import does not match its preview; nothing was imported. Upload the statement again."


def _stored_preview(groups: list[dict], statements: list | None = None, figures: list | None = None) -> str:
    """The facts of an upload's preview, as the response gives them, for its
    batch_imports row: per group (in order) its account, currency and tie
    lines, and per row (in order) its facts. With `statements`, what each
    statement in the files came to (_statement_results), which the confirm
    carries into its summary and Past imports shows. With `figures`, the loan
    figures its loan statements state (_loan_figure), which the confirm
    writes as they are."""
    preview = {
        "groups": [
            {
                "account": g["account"],
                "currency": g["currency"],
                "statements": g["statements"],
                "transactions": [{k: tx.get(k) for k in _PREVIEW_ROW_FACTS} for tx in g["transactions"]],
            }
            for g in groups
        ]
    }
    if statements is not None:
        preview["statements"] = statements
    if figures:
        preview["figures"] = figures
    # Serialised as the response is, so what is stored is what was shown.
    return app.json.dumps(preview)


class PreviewRefused(Exception):
    """A confirm that does not answer an open preview: (message, status)."""


def _confirmed_groups(conn, import_id, sent_groups) -> list[dict]:
    """The groups a confirm writes: the open preview's stored facts, with the
    request's choices per row. Raises PreviewRefused when the preview is not
    open (409) or the request does not have its shape (400)."""
    if isinstance(import_id, bool) or not isinstance(import_id, int):
        raise PreviewRefused(PREVIEW_NOT_OPEN, 409)
    held = conn.execute(
        "SELECT status, result_json FROM batch_imports WHERE id = ?", (import_id,)
    ).fetchone()
    if held is None or held["status"] != "preview" or not held["result_json"]:
        raise PreviewRefused(PREVIEW_NOT_OPEN, 409)
    try:
        stored = json.loads(held["result_json"])["groups"]
    except (ValueError, KeyError, TypeError):
        raise PreviewRefused(PREVIEW_NOT_OPEN, 409) from None
    if not isinstance(sent_groups, list) or len(sent_groups) != len(stored):
        raise PreviewRefused(PREVIEW_MISMATCH, 400)
    out = []
    for kept, sent in zip(stored, sent_groups):
        if not isinstance(sent, dict) or sent.get("account") != kept["account"]:
            raise PreviewRefused(PREVIEW_MISMATCH, 400)
        sent_rows = sent.get("transactions")
        if not isinstance(sent_rows, list) or len(sent_rows) != len(kept["transactions"]):
            raise PreviewRefused(PREVIEW_MISMATCH, 400)
        rows = []
        for facts, chosen in zip(kept["transactions"], sent_rows):
            if not isinstance(chosen, dict):
                raise PreviewRefused(PREVIEW_MISMATCH, 400)
            rows.append({**{k: chosen[k] for k in _CONFIRM_ROW_CHOICES if k in chosen}, **facts})
        out.append({
            "account": kept["account"],
            "currency": kept["currency"],
            "statements": kept["statements"],
            "transactions": rows,
        })
    return out


def _confirmed_figures(conn, import_id) -> list[dict]:
    """The loan figures a confirm writes: the open preview's, as the upload
    read them. Call after _confirmed_groups has found the preview open."""
    held = conn.execute(
        "SELECT result_json FROM batch_imports WHERE id = ?", (import_id,)
    ).fetchone()
    figures = json.loads(held["result_json"]).get("figures") or []
    if not isinstance(figures, list) or not all(isinstance(f, dict) for f in figures):
        raise PreviewRefused(PREVIEW_NOT_OPEN, 409)
    return figures


ROWS_FROM_NOT_A_DAY = "Rows from must be a day written as YYYY-MM-DD; nothing was read."


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
    A loan statement (the DBS car-loan summary) has no rows: what it states
    is owed goes in `figures` (_loan_figure), and the confirm writes it on
    the loan as a statement's figure.
    With `rows_from` (a day, YYYY-MM-DD) every row before that day is left
    out, after the statement's own check: a statement over the day comes in
    from it, its opening the balance on the day before (tie.from_day), and
    one that ends before it comes in not at all. `rows_from` in the reply
    counts what was left out.
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
    # Rows from a day: every row before it is left out (tie.from_day).
    rows_from = (request.form.get("rows_from") or "").strip() or None
    if rows_from is not None:
        try:
            if date.fromisoformat(rows_from).isoformat() != rows_from:
                raise ValueError
        except ValueError:
            return jsonify({"error": ROWS_FROM_NOT_A_DAY}), 400
    left_out = {"rows": 0, "statements": 0}

    all_groups = {}  # account_name -> list of transaction dicts
    figures = []  # what each loan statement states is owed (_loan_figure)
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
        from parse_dbs_loan import LoanStatement
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
                if isinstance(stmt, LoanStatement):
                    # A loan statement has no rows: its figure is all of it.
                    try:
                        figures.append(_loan_figure(conn, stmt))
                    except ValueError as e:
                        errors.append({"file": filename, "error": str(e)})
                    continue
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
                    if rows_from is not None:
                        stmt, dropped = tie.from_day(stmt, rows_from)
                        left_out["rows"] += dropped
                        if stmt is None:
                            left_out["statements"] += 1
                            continue
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
                        "SELECT id, type, owner FROM accounts WHERE id = ?",
                        (find_account(conn, account, stmt.currency),),
                    ).fetchone()
                    account_facts[account] = {
                        "account_id": held["id"] if held else None,
                        "account_kind": held["type"] if held else _kind_from_account_name(account),
                        "account_owner": held["owner"] if held else account_kind.DEFAULT_OWNER,
                    }
                if account not in currencies:
                    try:
                        currencies[account] = _account_currency(conn, account, stmt.currency)
                    except CurrencyRefused:
                        currencies[account] = stmt.currency

                # Classify flow_type post-parse, from the row's own wording,
                # the account it is on and the merchant the rules gave it. A
                # rule that states a flow says what the row is; one that
                # states none labels only: a transfer it names still waits.
                tx.flow_type, other_side_id = flow.classify_row(
                    {
                        "description": tx.description,
                        "amount_minor": tx.amount_minor,
                        "service_id": svc_id,
                        **account_facts[account],
                        **found["rule_facts"],
                    },
                    flow_ctx,
                )
                # A row that is no one's spending comes in with its merchant
                # and no book or type (label_source.fitted).
                book_given = found["book"]
                if not flow.carries_label(tx.flow_type):
                    book_given = type_id = None

                entry = {
                    "date": tx.date,
                    "description": tx.description,
                    "amount_sgd": money.from_minor(tx.amount_minor, currencies[account]),
                    "amount_foreign": tx.amount_foreign,
                    "currency_foreign": tx.currency_foreign,
                    "book": book_given,
                    "type_id": type_id,
                    "type_name": type_names.get(type_id) if type_id else None,
                    "service_id": svc_id,
                    "service_name": svcs.get(svc_id) if svc_id else None,
                    # A mixed merchant: recognised, given its default type,
                    # and flagged for a look each time.
                    "review_each_time": found["review_each_time"],
                    "flow_type": tx.flow_type,
                    "other_side_id": other_side_id,
                    "account": account,
                    # A row that waits is waiting, whatever type a rule gave it.
                    "status": flow.REVIEW if tx.flow_type == flow.REVIEW else "typed" if type_id else (
                        "untyped" if flow.carries_label(tx.flow_type) else tx.flow_type
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

    # Save preview to batch_imports and fetch services list in one connection.
    # The preview's facts stay on the server: confirm writes these, never the
    # facts a request sends back (_stored_preview).
    with get_db() as conn:
        # What each statement in the files came to, kept with the import so
        # Past imports can say it: ties, off by (refused) or not checked.
        results = _statement_results(conn, statement_lines, errors, groups)
        conn.execute(
            "INSERT INTO batch_imports (filenames, accounts, status, total_lines, categorized_lines, result_json) "
            "VALUES (?, ?, 'preview', ?, ?, ?)",
            (
                json.dumps(filenames),
                json.dumps(list(all_groups.keys()) + [f["account"] for f in figures]),
                total,
                typed,
                _stored_preview(groups, results, figures),
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
        "figures": figures,
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
        # What a "rows from" day left out: rows, and statements wholly before it.
        "rows_from": {"day": rows_from, **left_out} if rows_from is not None else None,
    })


def _imported_sources(conn, tx: dict, amount_minor: int) -> tuple:
    """Each field's source for an imported row: what the rules give it now
    (from the merchant, a rule, or the wording) where the confirm writes the
    value the rules give; a value the request brings that the rules do not
    give is chosen. None for an empty field."""
    found = _label_for(conn, tx["description"] or "", amount_minor)
    out = []
    for field, column in label_source.FIELDS.items():
        value = tx.get(field) or None
        if value is None:
            out.append(None)
        elif value == found[field] and found[column] is not None:
            out.append(found[column])
        else:
            out.append(label_source.CHOSEN)
    return tuple(out)


def _chosen_in_preview(conn, tx: dict, amount_minor: int) -> bool:
    """Whether the operator gave a waiting row a type or a merchant in the
    preview: one the rules do not give it. That labels it, and it no longer
    waits. The rules' own merchant and type do not: a rule that states no
    flow labels only (ADR-009). A type on money in is none it can keep (it
    would be income, which carries no type, fin-redesign 05): it lifts
    nothing, and the confirm drops it."""
    found = _label_for(conn, tx["description"] or "", amount_minor)
    fields = ("service_id",) if amount_minor < 0 else ("type_id", "service_id")
    return any(tx.get(field) and tx.get(field) != found[field] for field in fields)


def _unheld(amount_minor: int) -> str:
    """The flow of a waiting row the operator labelled in the preview:
    money in is income, money out is spending."""
    return "income" if amount_minor < 0 else "expense"


@app.route("/api/import/confirm", methods=["POST"])
def api_import_confirm():
    """Commit an upload's preview to the database.

    The request brings choices, never facts. The facts are the preview the
    upload stored on its batch_imports row (result_json): each group's
    account, currency and statement tie lines, and each row's date,
    description, amount, foreign amount and currency, account and statement.
    Whatever a request sends for those is ignored. From each row it takes
    only: book, type_id, service_id, flow_type, other_side_id, _skip and
    is_one_off; and new_services and new_rules. Each field's source is what
    the rules give it now, or chosen (_imported_sources).

    Expects JSON body (the preview's groups echoed back with choices made):
    {
        "import_id": int,
        "groups": [
            {
                "account": "account name, as the preview gave it",
                "transactions": [
                    {"book": "Household", "type_id": 5, "service_id": null,
                     "flow_type": "expense", "other_side_id": null,
                     "_skip": false, "is_one_off": false,
                     ...the row's facts may be echoed back and are ignored}, ...
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

    An import_id that names no preview, or one already confirmed or failed,
    is refused (409) and nothing is written: upload the statement again. The
    groups must be the preview's, in its order, each with its account and
    its number of rows (rows by position); anything else is refused (400)
    and nothing is written. A confirmed preview cannot be confirmed again.

    A book, a type or a flow that is not in its vocabulary, or an other side
    that is no account, refuses the whole import before anything is written,
    and so does an amount that is missing or is not a whole number of cents.
    The amount arrives as the decimal the preview showed and is stored as
    whole minor units.

    A group's `statements` are the tie lines the upload stored. Each is
    checked again over the rows that name it and are about to be written:
    opening less their sum must equal closing, exactly, so a row of such a
    statement cannot be left out. Its closing balance is then written as a
    statement anchor; the same balance already held changes nothing, and a
    different one for that account and date refuses the import (409).

    The preview's `figures` (a loan statement's, _loan_figure) are written as
    the upload read them, whatever the request sends: each as a statement's
    figure on its loan account (made, of kind loan, when there is none), what
    is owed stored negative. The same figure again changes nothing; a
    different one for that day, or an account of that name that is no loan,
    refuses the import (409).

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
    new_rules = data.get("new_rules", [])
    new_services = data.get("new_services", [])

    with get_db() as conn:
        # The facts are the preview's as the upload read them; the request
        # brings only its choices.
        try:
            groups = _confirmed_groups(conn, import_id, data.get("groups"))
            figures = _confirmed_figures(conn, import_id)
        except PreviewRefused as e:
            message, status = e.args
            return jsonify({"error": message}), status
        try:
            for labelled in [tx for g in groups for tx in g.get("transactions", [])] + new_services:
                _checked_labels(conn, labelled)
            for g in groups:
                for tx in g.get("transactions", []):
                    if tx.get("flow_type") is not None:
                        flow.checked_flow(tx["flow_type"])
                    _checked_other_side(conn, tx.get("other_side_id"), tx.get("flow_type"))
                    # Not ours only on a shared account (shared.py): a new account is none.
                    if tx.get("flow_type") == flow.NOT_OURS:
                        import ingest
                        why = shared.not_ours_refusal(
                            conn, ingest.find_account(conn, tx.get("account") or g.get("account") or "", g.get("currency")),
                            flow.NOT_OURS)
                        if why:
                            raise UnknownLabel(why)
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
                # The opening day is stored as the period's start
                # (statement_period.py): checked as the closing day is.
                if line.get("opening_date") is not None:
                    anchors.checked_date(line["opening_date"])
                tie.checked(line.get("opening_minor"), line.get("closing_minor"), amounts)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    total_saved = 0
    total_duplicates = 0
    accounts_created = []
    rules_skipped_generic = 0
    anchors_written = 0
    figures_written = 0
    rows_refiled = 0
    wordings_tidied = 0

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
                from ingest import ensure_account, ensure_named_account, ensure_statement, find_account
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
                            period_start=line.get("opening_date"),
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
                # The other accounts the same printed statement carried (a BS
                # statement: the Multiplier and Home Reno), where an older
                # read may have filed this account's rows.
                siblings = {
                    find_account(conn, name)
                    for line in statement_lines for name in line.get("siblings") or []
                } - {None, account_id}
                printed_keys = {
                    key for key, rows in import_by_key.items() if rows[0].get("statement") is not None
                }
                # The rows of the other accounts the same file printed, by day
                # and size: a row an older read filed here that one of them
                # words is that account's to claim, never this one's by day
                # and amount.
                files = {line.get("file") for line in statement_lines} - {None}
                others_rows = [
                    (tx["date"], abs(minor_of[id(tx)]), _wording_key(tx["description"]))
                    for other in groups if other is not group
                    and files & {line.get("file") for line in other.get("statements") or []}
                    for tx in other["transactions"]
                ]
                # A section in another currency than its siblings' (DBS Home's
                # USD) was read by older reads without a balance to sign by.
                either_sign = any(
                    (conn.execute("SELECT currency FROM accounts WHERE id = ?", (i,)).fetchone()["currency"] or "SGD")
                    != currency_of[id(group)]
                    for i in siblings
                )
                held_by_key = _held_for_import(conn, import_counts, siblings, printed_keys, either_sign, others_rows)
                # The page junk an older read left is a DBS bank statement's.
                account_kinds = {
                    i: conn.execute("SELECT type FROM accounts WHERE id = ?", (i,)).fetchone()["type"]
                    for i in {key[0] for key in import_counts}
                }
                for key, import_count in import_counts.items():
                    row_account_id, date_val, desc_val, amount_val = key

                    # Existing matches for this account, and whether each is
                    # already filed under a printed statement.
                    held_rows = held_by_key[key]
                    existing = len(held_rows)

                    # Only insert the net-new ones
                    to_insert = max(0, import_count - existing)
                    skipped = import_count - to_insert
                    duplicates_skipped += skipped

                    # A row already held on a month record (imported before
                    # from a source with no balance, or before rows were filed
                    # by statement) that this statement printed is filed
                    # under it now.
                    on_month_records = [r for r in held_rows if not r["printed"]]
                    printed_again = [
                        tx for tx in import_by_key[key][to_insert:] if tx.get("statement") is not None
                    ]
                    for held, tx in zip(on_month_records, printed_again):
                        conn.execute(
                            "UPDATE transactions SET statement_id = ? WHERE id = ?",
                            (statement_of(tx), held["id"]),
                        )
                        rows_refiled += 1
                        if held["amount"] != amount_val:
                            # Held with its sign turned: it takes the
                            # statement's, and what it was is read again on
                            # the account it is on now; its labels were for
                            # money going the other way.
                            worked_out = _classify_flow_for_tx(
                                conn, desc_val, amount_val, tx_id=held["id"],
                                rule_facts=_label_for(conn, desc_val or "", amount_val)["rule_facts"],
                            )
                            conn.execute(
                                "UPDATE transactions SET amount_minor = ?, flow_type = ?, other_side_id = ?, "
                                "flow_type_manual = 0, book = NULL, type_id = NULL, book_source = NULL, "
                                "type_source = NULL WHERE id = ?",
                                (amount_val, *worked_out, held["id"]),
                            )

                    # A held row an older read gave the page's junk after its
                    # own wording takes the wording printed.
                    for held in held_rows if account_kinds.get(row_account_id) == "bank" else []:
                        tidied = _tidied_wording(held["description"], desc_val)
                        if tidied is not None:
                            conn.execute(
                                "UPDATE transactions SET description = ? WHERE id = ?", (tidied, held["id"])
                            )
                            wordings_tidied += 1

                    # Link each transaction to its statement record
                    for tx in import_by_key[key][:to_insert]:
                        statement_id = statement_of(tx)
                        tx_flow = tx.get("flow_type")
                        if tx_flow == flow.REVIEW and _chosen_in_preview(conn, tx, amount_val):
                            tx_flow = _unheld(amount_val)
                        # Book and type only on a spending or refund row
                        # (label_source.fitted): a row of any other flow
                        # keeps its merchant and comes in with neither.
                        if tx_flow is not None and not flow.carries_label(tx_flow):
                            tx = {**tx, "book": None, "type_id": None}
                        book_source, type_source = _imported_sources(conn, tx, amount_val)
                        cur = conn.execute(
                            "INSERT INTO transactions "
                            "(statement_id, date, description, amount_minor, amount_foreign, "
                            "currency_foreign, book, type_id, service_id, "
                            "is_one_off, flow_type, other_side_id, book_source, type_source) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                                tx_flow,
                                tx.get("other_side_id"),
                                book_source,
                                type_source,
                            ),
                        )
                        if tx_flow is None:
                            # A row sent with no flow takes the classifier's,
                            # read with the account it is on: a row is never
                            # saved without one (a start does not fill it in).
                            worked_out = _classify_flow_for_tx(
                                conn, tx["description"], amount_val, tx_id=cur.lastrowid,
                                service_id=tx.get("service_id"),
                                rule_facts=_label_for(conn, tx["description"] or "", amount_val)["rule_facts"],
                            )
                            if worked_out[0] == flow.REVIEW and _chosen_in_preview(conn, tx, amount_val):
                                worked_out = (_unheld(amount_val), None)
                            # Fitted to the flow it is given, as above.
                            emptied = "" if flow.carries_label(worked_out[0]) else (
                                ", book = NULL, type_id = NULL, book_source = NULL, type_source = NULL")
                            conn.execute(
                                f"UPDATE transactions SET flow_type = ?, other_side_id = ?{emptied} WHERE id = ?",
                                (*worked_out, cur.lastrowid),
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

            # What each loan statement states is owed: the loan's figure, as
            # a statement's, dated its day. No row and no statement record.
            from ingest import ensure_named_account
            figure_days: dict = {}
            for figure in figures:
                loan_id = ensure_named_account(
                    conn, figure["account"], loan_interest.LOAN, figure["currency"]
                )
                kind = conn.execute("SELECT type FROM accounts WHERE id = ?", (loan_id,)).fetchone()["type"]
                if kind != loan_interest.LOAN:
                    raise AnchorRefused(
                        f"{mask_card_number(figure['account'])} is a {kind} account; a loan "
                        "statement's figure goes on a loan. Nothing was imported."
                    )
                # What the figure comes to under the terms the loan holds now
                # (loan_terms.py): the preview's, or the confirm is refused.
                if _figure_now(conn, loan_id, figure) != figure["owed_minor"]:
                    raise AnchorRefused(
                        f"{mask_card_number(figure['account'])}'s hire-purchase terms changed since this "
                        "file was read, so the figure shown is not what would be written. Read the file "
                        "again. Nothing was imported."
                    )
                try:
                    _, written = anchors.record(
                        conn, loan_id, figure["date"], -figure["owed_minor"], anchors.STATEMENT,
                        f"import {import_id}",
                    )
                except anchors.AnchorConflict as e:
                    raise AnchorRefused(
                        f"{mask_card_number(figure['account'])} already has a figure of "
                        f"{anchors.format_amount(-e.existing['amount'], figure['currency'])} for "
                        f"{e.existing['date']}; this statement states "
                        f"{anchors.format_amount(figure['owed_minor'], figure['currency'])}. "
                        "A different amount for the same date is refused; nothing was imported."
                    ) from None
                figures_written += 1 if written else 0
                if written:
                    figure_days.setdefault(loan_id, (figure["account"], figure["currency"], set()))[2].add(
                        figure["date"])
                if figure["account"] not in accounts_created:
                    accounts_created.append(figure["account"])

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
                        "INSERT INTO services (name, book, type_id, review_each_time) VALUES (?, ?, ?, ?)",
                        (svc_name, svc_book, svc_type_id, _new_checks_each_row(conn, svc_type_id)),
                    )
                    svc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    services_created += 1
                # Create merchant rule from description → service
                if desc:
                    pattern = desc.upper().strip()
                    if _looks_transfer_like_description(desc) or db.is_generic_rule_pattern(pattern):
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
            # With hire-purchase terms, every figure of a loan this import
            # wrote a figure on must fit their schedule (loan_interest.misfit).
            for loan_id, (name, currency, _days) in figure_days.items():
                why = loan_interest.misfit(conn, loan_id, mask_card_number(name), currency)
                if why:
                    raise AnchorRefused(f"{why} Nothing was imported.")
            # A figure this import wrote that fell by more than the instalments
            # naming the loan paid would be negative interest: the instalments
            # are not yet filed as its repayments.
            for loan_id, (name, currency, days) in figure_days.items():
                for period in loan_interest.periods(conn, loan_id):
                    if period["negative"] and (period["from"] in days or period["to"] in days):
                        raise AnchorRefused(
                            f"{mask_card_number(name)} fell by "
                            f"{anchors.format_amount(period['fell_minor'], currency)} between "
                            f"{loan_interest.day_shown(period['from'])} and "
                            f"{loan_interest.day_shown(period['to'])}, more than the "
                            f"{anchors.format_amount(period['paid_minor'], currency)} of instalments "
                            "naming it: label the instalments as repayments of the loan first. "
                            "Nothing was imported."
                        )
            # Refunds this import brought in link to their purchases, in this change.
            refund_links.link_on_import(conn)

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
                "wordings_tidied": wordings_tidied,
            }
            if figures:
                result_summary["figures_written"] = figures_written
            # The statements' results the upload kept stay with the import.
            held = conn.execute(
                "SELECT result_json FROM batch_imports WHERE id = ?", (import_id,)
            ).fetchone()
            try:
                kept = json.loads(held["result_json"]) if held and held["result_json"] else {}
            except ValueError:
                kept = {}
            if isinstance(kept, dict) and "statements" in kept:
                result_summary["statements"] = kept["statements"]
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
                "figures_written": figures_written,
                "rows_refiled": rows_refiled,
                "wordings_tidied": wordings_tidied,
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
    from ingest import find_account
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM batch_imports ORDER BY created_at DESC"
        ).fetchall()
        found = {}

        def account_of(entry: dict):
            # An account the import itself created was not there at upload.
            if entry.get("account_id") is None and entry.get("account"):
                if entry["account"] not in found:
                    found[entry["account"]] = find_account(conn, entry["account"], entry.get("currency"))
                return {**entry, "account_id": found[entry["account"]]}
            return entry

        results = {}
        for r in rows:
            kept = json.loads(r["result_json"]) if r["result_json"] else None
            if isinstance(kept, dict) and isinstance(kept.get("statements"), list):
                kept = {**kept, "statements": [account_of(e) for e in kept["statements"]]}
            results[r["id"]] = kept

    shown = []
    for r in rows:
        accounts_raw = json.loads(r["accounts"]) if r["accounts"] else []
        result = results[r["id"]]
        shown.append({
            "id": r["id"],
            "filenames": json.loads(r["filenames"]) if r["filenames"] else [],
            "accounts": [mask_card_number(a) for a in accounts_raw],
            "status": r["status"],
            "total_lines": r["total_lines"],
            "categorized_lines": r["categorized_lines"],
            # While open, result_json holds the preview's facts: not shown.
            "result": result if r["status"] != "preview" else None,
            # Each statement's result (ties, off, not_checked), or None for
            # an import made before fin kept them.
            "statements": result.get("statements") if isinstance(result, dict) else None,
            "created_at": r["created_at"],
        })
    return jsonify(shown)


# ---------------------------------------------------------------------------
# Statement Coverage
# ---------------------------------------------------------------------------


@app.route("/api/statements/coverage")
def api_statements_coverage():
    """Return 6-month coverage matrix: active accounts × recent months.

    Shows which accounts have statements imported for each month,
    and which are missing. A statement marks the month it closes in and every
    month its period contains whole (statement_period.held_months): one
    covering July and August marks both.
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
            "SELECT s.account_id, s.statement_date, s.period_start, s.printed, s.filename, s.imported_at "
            "FROM statements s "
            "JOIN accounts a ON s.account_id = a.id "
            "WHERE (a.status = 'active' OR a.status IS NULL) "
            "  AND a.type IN (?, ?) "
            # A statement holds no month after the one it closes in, so one
            # dated before the first month shown holds none of them.
            "  AND s.statement_date >= ? "
            "ORDER BY s.statement_date",
            (*account_kind.STATEMENT_KINDS, min_month),
        ).fetchall()
        held_of = [
            statement_period.held_months(
                conn, stmt["account_id"], stmt["statement_date"], stmt["period_start"], stmt["printed"]
            )
            for stmt in statements
        ]

    # Build matrix: {account_id: {month: {imported, date, filename}}}
    matrix = {}
    for acct in accounts:
        matrix[acct["id"]] = {}
        for m in months:
            matrix[acct["id"]][m] = {"imported": False}

    for stmt, held in zip(statements, held_of):
        acct_id = stmt["account_id"]
        for stmt_month in held:
            if acct_id not in matrix or stmt_month not in matrix[acct_id]:
                continue
            # A month shows the statement dated in it, as before periods; a
            # month none is dated in shows the first whose period holds it.
            if stmt_month == stmt["statement_date"][:7] or not matrix[acct_id][stmt_month]["imported"]:
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
    gives a row: its own override where it has one, else the merchant's; and
    the flow and other side each states (none: it labels only, ADR-009)."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT mr.id, mr.pattern, mr.match_type, mr.confidence,
                   mr.priority, mr.min_amount_minor, mr.max_amount_minor,
                   mr.service_id,
                   mr.book_override, mr.type_override_id,
                   mr.flow, mr.other_side_id, os.name as other_side_name,
                   s.name as service_name,
                   COALESCE(mr.book_override, s.book) as book,
                   COALESCE(mr.type_override_id, s.type_id) as type_id,
                   ty.name as type_name,
                   tp.name as parent_type
             FROM merchant_rules mr
             JOIN services s ON mr.service_id = s.id
             LEFT JOIN accounts os ON os.id = mr.other_side_id
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
        "flow": r["flow"],
        "other_side_id": r["other_side_id"],
        "other_side_name": mask_card_number(r["other_side_name"]) if r["other_side_name"] else None,
        "rows_labelled": labelled.get(r["id"], 0),
    } for r in rows])


def _rows_labelled_by_rule(conn) -> dict[int, int]:
    """How many rows each rule labels now (_rows_by_rule)."""
    return {rule_id: len(rows) for rule_id, rows in _rows_by_rule(conn).items()}


def _rows_by_rule(conn) -> dict[int, list[int]]:
    """The rows each rule labels now: every row a later change may reach
    (label_source: a field from fin or checked), held against the rule that
    wins for it, as a re-run would find it. Transfers and card payments are
    not counted. The baseline snapshot (fin_snapshot.py) reads it too."""
    rows: dict[int, list[int]] = {}
    for tx in conn.execute(
        "SELECT id, description, amount_minor FROM transactions t "
        "WHERE COALESCE(flow_type, 'expense') NOT IN ('transfer', 'payment') "
        f"AND {label_source.any_reachable_sql('t')} ORDER BY id"
    ):
        rule = first_rule(tx["description"] or "", conn, tx["amount_minor"])
        if rule is not None:
            rows.setdefault(rule["id"], []).append(tx["id"])
    return rows


@app.route("/api/rules", methods=["POST"])
def api_rules_create():
    """Add a new merchant rule. Requires service_id: book and type come from
    the service unless the rule overrides them. It may state what its rows
    are, `flow` and `other_side_id` (_checked_rule_flow, ADR-009): its new
    rows take them, and Apply gives them to its past rows but one whose flow
    was set by hand; the preview lists every row whose flow or other side
    would change (`flows`).

    A rule that would relabel past rows (the rows it would win, whose label
    would then change) goes through the preview (relabel_preview.rule_change):
    without `past` nothing is written and the preview comes back (409);
    `past: apply` labels those rows by it; `past: only_new` adds the rule and
    marks their fields checked, values left. `token` is refused once the
    rules or those rows changed since the preview. Returns {id, success,
    recategorized, checked}."""
    data = request.get_json(silent=True)
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
            rule_flow, other_side = _checked_rule_flow(conn, data.get("flow"), data.get("other_side_id"))
            past = relabel_preview.checked_answer(data.get("past"))
            move_chosen = relabel_preview.checked_move_chosen(data.get(relabel_preview.MOVE_CHOSEN), past)
        except (UnknownLabel, BadAmount, relabel_preview.Refused) as e:
            return jsonify({"error": str(e)}), 400
        proposed = {
            "pattern": data["pattern"],
            "service_id": data["service_id"],
            "book_override": book_override,
            "type_override_id": type_override_id,
            "match_type": data.get("match_type", "contains"),
            "priority": data.get("priority", 0),
            "min_amount_minor": min_minor,
            "max_amount_minor": max_minor,
            "flow": rule_flow,
            "other_side_id": other_side,
        }
        try:
            plan = relabel_preview.rule_change(conn, None, proposed)
        except relabel_preview.Gone as e:
            return jsonify({"error": str(e)}), 400
        refused = _previewed_answer(conn, plan, past, data.get("token"), move_chosen=move_chosen)
        if refused is not None:
            return refused
        moved = plan.by_hand if move_chosen else []
        if move_chosen:
            plan = relabel_preview.with_chosen(plan)
        try:
            new_id = conn.execute(
                "INSERT INTO merchant_rules (pattern, service_id, book_override, type_override_id, "
                "match_type, confidence, priority, min_amount_minor, max_amount_minor, flow, other_side_id) "
                "VALUES (?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?, ?)",
                (proposed["pattern"], proposed["service_id"], book_override, type_override_id,
                 proposed["match_type"], proposed["priority"], min_minor, max_minor, rule_flow, other_side),
            ).lastrowid
            recategorized, checked = _write_plan(conn, plan, past)
            kept = _kept_said(conn, plan, moved)
            conn.commit()
        except Exception as e:
            app.logger.warning("Failed to create rule: %s", e)
            return jsonify({"error": "Failed to create rule"}), 400
        finally:
            invalidate_rules_cache()
    return jsonify({"id": new_id, "success": True, "recategorized": recategorized, "checked": checked, **kept})


# What a rule edit may set, as the API names it, and the column that holds
# it. Frozen: the column names in the edit's statement come from here only.
_RULE_EDIT_COLUMNS = {
    "pattern": "pattern", "service_id": "service_id", "book_override": "book_override",
    "type_override_id": "type_override_id", "match_type": "match_type", "priority": "priority",
    "flow": "flow", "other_side_id": "other_side_id",
    **_THRESHOLD_COLUMNS,
}


def _rule_gone(conn, rule_id: int):
    """The refusal for a rule fin no longer holds, or None."""
    if conn.execute("SELECT 1 FROM merchant_rules WHERE id = ?", (rule_id,)).fetchone() is None:
        return jsonify({"error": "This rule is no longer in fin"}), 404
    return None


@app.route("/api/rules/<int:rule_id>", methods=["PUT"])
def api_rules_update(rule_id):
    """Update a merchant rule. The rows whose winning rule, after the edit,
    differs from before, or whose label from that rule changes, go through
    the preview (relabel_preview.rule_change): so a rule's priority and
    amount limits are honoured, and a row another rule wins is left alone.
    Without `past` nothing is written and the preview comes back (409);
    `past: apply` relabels them (from fin takes the new winner's source,
    checked stays checked, a chosen field keeps its own); `past: only_new`
    saves the rule and marks their fields checked. `token` is refused once
    the rules or those rows changed since the preview. The rows whose flow
    Apply keeps only because their book or type was chosen by hand are
    listed apart (flows_chosen, ADR-008); `move_chosen: true`, with `past:
    apply` and the preview's token, moves them too in the same change
    (relabel_preview.with_chosen). A new `flow` or
    `other_side_id` is checked with the other as the rule would then hold it
    (_checked_rule_flow); a flow changed without its other side keeps the
    one held only where the new flow allows it (_held_side_kept), so a flow
    cleared clears its other side. Returns
    {success, recategorized, checked}."""
    data = request.get_json(silent=True) or {}
    if "pattern" in data:
        pattern_error = _rule_pattern_error(data.get("pattern"))
        if pattern_error:
            return jsonify({"error": pattern_error}), 400
    proposed = {}
    try:
        for field, column in _RULE_EDIT_COLUMNS.items():
            if field in data:
                proposed[column] = (_threshold_minor(data, field) if field in _THRESHOLD_COLUMNS
                                    else data[field])
        past = relabel_preview.checked_answer(data.get("past"))
        move_chosen = relabel_preview.checked_move_chosen(data.get(relabel_preview.MOVE_CHOSEN), past)
    except (BadAmount, relabel_preview.Refused) as e:
        return jsonify({"error": str(e)}), 400
    if not proposed:
        return jsonify({"error": "No fields to update"}), 400

    with get_db() as conn:
        try:
            _checked_labels(conn, data, "book_override", "type_override_id")
        except UnknownLabel as e:
            return jsonify({"error": str(e)}), 400
        gone = _rule_gone(conn, rule_id)
        if gone is not None:
            return gone
        try:
            if "flow" in proposed or "other_side_id" in proposed:
                held = conn.execute("SELECT flow, other_side_id FROM merchant_rules WHERE id = ?",
                                    (rule_id,)).fetchone()
                stated = proposed.get("flow", held["flow"]) or None
                if "other_side_id" in proposed:
                    side = proposed["other_side_id"]
                else:
                    side = _held_side_kept(conn, stated, held["other_side_id"])
                # One held state is kept unchecked, as held: a transfer that
                # names no other side, as a seeded own-account rule states it
                # (db.OWN_ACCOUNT_RULES); the screen sends it with every edit.
                # Anything else sent is checked, a side held already too.
                if (stated, side) == (held["flow"], held["other_side_id"]) == ("transfer", None):
                    proposed["flow"], proposed["other_side_id"] = stated, side
                else:
                    proposed["flow"], proposed["other_side_id"] = _checked_rule_flow(conn, stated, side)
        except UnknownLabel as e:
            return jsonify({"error": str(e)}), 400
        try:
            plan = relabel_preview.rule_change(conn, rule_id, proposed)
        except relabel_preview.Gone as e:
            return jsonify({"error": str(e)}), 400
        refused = _previewed_answer(conn, plan, past, data.get("token"), move_chosen=move_chosen)
        if refused is not None:
            return refused
        moved = plan.by_hand if move_chosen else []
        if move_chosen:
            plan = relabel_preview.with_chosen(plan)
        try:
            conn.execute(
                f"UPDATE merchant_rules SET {', '.join(f'{c} = ?' for c in proposed)} WHERE id = ?",
                (*proposed.values(), rule_id),
            )
            recategorized, checked = _write_plan(conn, plan, past)
            kept = _kept_said(conn, plan, moved)
            conn.commit()
        finally:
            invalidate_rules_cache()
    return jsonify({"success": True, "recategorized": recategorized, "checked": checked, **kept})


@app.route("/api/rules/<int:rule_id>", methods=["DELETE"])
def api_rules_delete(rule_id):
    """Delete a merchant rule. The rows it labels whose label would then
    change (by the rules left, or to no merchant, waiting in the Queue) go
    through the preview: without `past` nothing is written and the preview
    comes back (409); `past: apply` relabels them; `past: only_new` deletes
    the rule and the rows keep their labels, checked. Body (optional):
    {past, token}. Returns {success, recategorized, checked}."""
    data = request.get_json(silent=True) or {}
    try:
        past = relabel_preview.checked_answer(data.get("past"))
        move_chosen = relabel_preview.checked_move_chosen(data.get(relabel_preview.MOVE_CHOSEN), past)
    except relabel_preview.Refused as e:
        return jsonify({"error": str(e)}), 400
    with get_db() as conn:
        gone = _rule_gone(conn, rule_id)
        if gone is not None:
            return gone
        plan = relabel_preview.rule_change(conn, rule_id, None)
        refused = _previewed_answer(conn, plan, past, data.get("token"), move_chosen=move_chosen)
        if refused is not None:
            return refused
        moved = plan.by_hand if move_chosen else []
        if move_chosen:
            plan = relabel_preview.with_chosen(plan)
        try:
            conn.execute("DELETE FROM merchant_rules WHERE id = ?", (rule_id,))
            recategorized, checked = _write_plan(conn, plan, past)
            kept = _kept_said(conn, plan, moved)
            conn.commit()
        finally:
            invalidate_rules_cache()
    return jsonify({"success": True, "recategorized": recategorized, "checked": checked, **kept})


# Re-run every rule is retired (fin-surfaces 21 and 51): in its place the
# drift count, the rows whose label differs from what the rules give now,
# read on Lists › Rules and applied through the same preview.


@app.route("/api/rules/drift")
def api_rules_drift():
    """The rows that differ from what their rule says now
    (relabel_preview.drift): the preview the drawer and chat read, {count,
    examples, token, ...}. Fields from the merchant or a rule only; a
    checked or chosen field, or one the PayNow wording gave before its
    wordings were rules, is never counted. It writes nothing."""
    with get_db() as conn:
        return jsonify(relabel_preview.summary(conn, relabel_preview.drift(conn)))


@app.route("/api/rules/drift", methods=["POST"])
def api_rules_drift_apply():
    """Apply to the rows that differ from their rule: {past: 'apply',
    token}. Each takes what the rules give now, in one change; its flow is
    worked out again where it is still the one fin worked out
    (relabel_preview.flow_writes: a flow set by hand keeps its own, and so
    does a row a record of expect back rests on, and one whose book or type
    was chosen by hand, but with `move_chosen: true`, on the preview's
    token). Without `past` the preview comes back (409) and nothing is
    written; there is no other answer (Leave them writes nothing, so it is
    never sent). `token` is refused once the rules or those rows changed
    since the preview. Returns {success, recategorized}."""
    data = request.get_json(silent=True) or {}
    past = data.get("past")
    if past is not None and past != relabel_preview.APPLY:
        return jsonify({"error": f"past must be {relabel_preview.APPLY}: leaving the rows as they are "
                                 "writes nothing, so there is nothing to send"}), 400
    try:
        move_chosen = relabel_preview.checked_move_chosen(data.get(relabel_preview.MOVE_CHOSEN), past)
    except relabel_preview.Refused as e:
        return jsonify({"error": str(e)}), 400
    with get_db() as conn:
        plan = relabel_preview.drift(conn)
        if data.get("token") is not None and data["token"] != plan.token:
            return jsonify({"error": relabel_preview.stale(plan), "stale": True}), 409
        if (plan.count or plan.by_hand) and (past is None or (move_chosen and data.get("token") is None)):
            return jsonify({"error": relabel_preview.drift_asked(plan),
                            "preview": relabel_preview.summary(conn, plan)}), 409
        if move_chosen:
            plan = relabel_preview.with_chosen(plan)
        recategorized, _ = _write_plan(conn, plan, past)
        conn.commit()
    return jsonify({"success": True, "recategorized": recategorized})


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


@app.route("/api/rates/fetch-missing", methods=["POST"])
def api_rates_fetch_missing():
    """Fetch and save the European Central Bank rate for every day a row in
    another currency is on that no saved rate values (valuation.py), one
    request per currency. A weekend or holiday takes the business day's
    before it, and the saved source says which. A rate already saved is kept.

    Answers {saved, currencies (still short of a rate), failed}. A currency
    whose fetch failed saves nothing; the others' rates are kept. 502 only
    when a fetch failed and nothing at all was saved.
    """
    with get_db() as conn:
        done = valuation.fetch_missing(conn)
        conn.commit()
    return jsonify(done), 502 if done["failed"] and not done["saved"] else 200


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
    if account is not None and account["type"] == "person":
        raise review.LabelRefused(review.PERSON_MOVEMENT_REFUSED)
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


class _NoSuchRow(LookupError):
    """A row a label names is not in fin; nothing was written."""


def _label_row(conn, choice, data: dict, tx_id: int) -> dict:
    """Say what one row was, on `conn` and not committed: the choice's flow
    (set by hand), its other side, and its book and type each chosen.
    Raises _NoSuchRow, or a refusal (UnknownLabel, BookNeeded,
    review.LabelRefused, expect_back.Refused) with nothing to keep."""
    tx = conn.execute(
        "SELECT t.id, t.amount_minor, t.flow_type, t.book, t.type_id, t.service_id, s.account_id FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id WHERE t.id = ?",
        (tx_id,),
    ).fetchone()
    if tx is None:
        raise _NoSuchRow(tx_id)

    flow_name = flow.checked_flow(choice.flow)
    book = type_id = other_side = None
    sources = {"book_source": label_source.CHOSEN, "type_source": label_source.CHOSEN}
    if choice.asks == review.ASKS_TYPE:
        type_id = _checked_type_id(conn, data.get("type_id"))
        if type_id is None:
            raise review.LabelRefused("type_id is required: spending takes a type")
        book = _book_or_proposed(conn, _checked_book(data.get("book")), type_id)
    elif choice.name == "gift":
        # Positive is money out: a gift given. Otherwise one received.
        if tx["amount_minor"] < 0:
            flow_name = "income"
            type_id = book_type.slot_type(conn, book_type.GIFT_RECEIVED_SLOT)
        else:
            type_id = book_type.slot_type(conn, book_type.GIFT_GIVEN_SLOT)
            book = book_type.DEFAULT_BOOK
    elif choice.name == "refund":
        # A refund keeps the row's book and type (spending that came back
        # counts against what it was), or takes the type given. An income
        # kind is no spending type: the refund keeps none, and takes its
        # merchant's book and type instead (case 20, keeping Travel: a
        # credit that came in as income carries no label of its own).
        given = _checked_type_id(conn, data.get("type_id"))
        spending = lambda type_ref: type_ref is not None and conn.execute(
            "SELECT 1 FROM types WHERE id = ? AND kind = ?", (type_ref, book_type.SPENDING)).fetchone() is not None
        merchant = conn.execute("SELECT book, type_id FROM services WHERE id = ?",
                                (tx["service_id"],)).fetchone() if tx["service_id"] is not None else None
        if given is not None:
            type_id = given
            book = _book_or_proposed(conn, _checked_book(data.get("book")), given)
        elif not spending(tx["type_id"]) and merchant is not None and spending(merchant["type_id"]):
            type_id = merchant["type_id"]
            book = _checked_book(data.get("book")) or merchant["book"] or book_type.DEFAULT_BOOK
        else:
            type_id = tx["type_id"] if spending(tx["type_id"]) else None
            given_book = _checked_book(data.get("book"))
            book = given_book or tx["book"]
            if type_id is None:
                # No type worth keeping: the refund waits for its purchase's
                # book and type, which the linker gives a row not labelled by
                # hand, or for its merchant's. Neither is the operator's
                # choice, so neither is marked chosen; a book sent is.
                sources = {"book_source": label_source.CHOSEN if given_book else None, "type_source": None}
    elif choice.asks == review.ASKS_INCOME_KIND:
        kind_id = data.get("income_kind_id")
        if isinstance(kind_id, bool) or not isinstance(kind_id, int) or not conn.execute(
            "SELECT 1 FROM types WHERE id = ? AND kind = ?", (kind_id, book_type.INCOME)
        ).fetchone():
            raise review.LabelRefused("income_kind_id is required: one of the income kinds")
        type_id = kind_id
    elif choice.asks == review.ASKS_ACCOUNT:
        other_side = _label_account(conn, choice, data.get("account_id"), tx["account_id"])
    # A card payment asks nothing: no other side, no book, no type.

    # A row that stops being a refund leaves its purchase (its link and the
    # set-offs the link placed), before what rests on it is weighed.
    if tx["flow_type"] == "refund" and flow_name != "refund":
        refund_links.drop(conn, tx_id)
    # A row a record rests on keeps to what the sheet counts once.
    other_kind = (conn.execute(
        "SELECT type FROM accounts WHERE id = ?", (other_side,)).fetchone() or [None])[0]
    why = _label_refusal(conn, tx_id, flow_name, book, other_kind)
    if why:
        raise review.LabelRefused(why)

    # Saying what it was is a deliberate act: the book and the type are each
    # chosen, one it leaves empty too (a movement, a gift received's book),
    # so no merchant or rule fills it later (spec 24, case 7), and the flow
    # is set by hand (personal-ledger 50). With the single mark retired
    # (personal-ledger 51) the field's own source is the one place that says
    # so.
    held = conn.execute(f"SELECT {_LABEL_HELD} FROM transactions t WHERE t.id = ?", (tx_id,)).fetchone()
    _write_label(conn, held, {
        "flow_type": flow_name, "flow_type_manual": 1, "other_side_id": other_side,
        "book": book, "type_id": type_id, **sources,
    })
    return {"id": tx_id, "flow_type": flow_name, "other_side_id": other_side}


def _label_rows(tx_id: int, more) -> list[int]:
    """The rows a label names: the row in its path, then tx_ids (at most
    QUEUE_ROW_CAP in all, each once, every id one SQLite can hold). Raises
    review.LabelRefused."""
    if more is None:
        more = []
    if not isinstance(more, list) or not all(
            isinstance(i, int) and not isinstance(i, bool) and i in _SQLITE_INT for i in more):
        raise review.LabelRefused("tx_ids must be a list of row ids")
    rows = [tx_id, *more]
    if len(set(rows)) != len(rows):
        raise review.LabelRefused("a row is named twice: name each once")
    if len(rows) > QUEUE_ROW_CAP:
        raise review.LabelRefused(f"at most {QUEUE_ROW_CAP} rows at once, as many as the Queue shows")
    return rows


def _label_preview_lines(conn, rows: list[int], mark: int) -> tuple[list[dict], list[tuple]]:
    """What this request's change entry recorded since `mark`: each named
    row as it was and as it would be (flow, other side, book, type, in
    words), and every recorded change as the token stamps it."""
    changes = conn.execute(
        "SELECT tbl, row_id, op, before, after FROM change_rows WHERE entry_id = ? AND id > ? ORDER BY id",
        (g.get("change_entry"), mark),
    ).fetchall()
    types = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM types")}
    accounts = {r[0]: mask_card_number(r[1]) for r in conn.execute("SELECT id, name FROM accounts")}

    def said(value: dict) -> dict:
        return {"flow_type": value.get("flow_type"), "other_side": accounts.get(value.get("other_side_id")),
                "book": value.get("book"), "type": types.get(value.get("type_id"))}

    first = {}
    for c in changes:
        if c["tbl"] == "transactions" and c["row_id"] in rows and c["row_id"] not in first:
            first[c["row_id"]] = json.loads(c["before"]) if c["before"] else {}
    lines = []
    for tx_id in rows:
        if tx_id not in first:
            continue  # it already was what the label says
        now = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        was = first[tx_id]
        lines.append({"id": tx_id, "date": now["date"], "description": now["description"],
                      "amount_minor": now["amount_minor"], "was": said(was), "now": said(dict(now))})
    stamped = [(c["tbl"], c["row_id"], c["op"], _stable(c["before"]), _stable(c["after"])) for c in changes]
    return lines, stamped


@app.route("/api/review/<int:tx_id>/label", methods=["POST"])
def api_review_label(tx_id: int):
    """Say what a row was: a waiting transfer, or any row. Writes the
    choice's flow (set by hand) and its other side, and its book and type
    each chosen, so no merchant change, rule or re-run reaches them
    afterwards.

    Body: choice, and what the choice asks for (see /api/review):
        spending        type_id; book where the type proposes none
        gift            nothing: spending when given, income when received
        own_account     account_id of a household bank account, card or untracked account
        company         account_id of a company
        loan_repayment  account_id of a loan
        income          income_kind_id
    and the chat's reach beyond the review list (review.MORE_CHOICES):
        refund          nothing: it keeps its book and type; or type_id (and
                        book where the type proposes none). It links to its
                        purchase when exactly one fits (refund_links, oldest
                        first). With no spending type to keep, its merchant's
                        book and type, chosen (case 20); with no merchant's
                        either, the purchase's, not marked chosen
        card_payment    nothing: no other side, no book, no type
        holding         account_id of a holding

    A row that stops being a refund drops its refund link, refused while
    it is To pay back (Unlink's words). A person is no
    movement's other side (personal-ledger 23, the contract):
    "loan_to_person", or a person's account under any choice, is refused in
    review.PERSON_MOVEMENT_REFUSED's words and writes nothing.

    Many rows (fin redesign ticket 11): tx_ids, more rows the same label
    says the same of, in the same change. A label naming more than one row
    writes nothing without `token`: it answers 409 with a preview (each
    row as it was and as it would be, the rows, the changes the history and
    the chat's count read, the expected_count over MANY_ROWS, and the
    token). Sent back
    with that token, it writes, as one change, only while the label would
    make the same change to the same rows (a row changed since, by a rule or
    anything else, is refused stale: preview again). A refusal on any row
    writes nothing.
    """
    data = request.get_json(silent=True) or {}
    if data.get("choice") in review.RETIRED:
        return jsonify({"error": review.PERSON_MOVEMENT_REFUSED}), 400
    choice = review.BY_NAME.get(data.get("choice")) if isinstance(data.get("choice"), str) else None
    if choice is None:
        return jsonify({"error": f"unknown choice: {data.get('choice')!r}"}), 400
    try:
        rows = _label_rows(tx_id, data.get("tx_ids"))
    except review.LabelRefused as e:
        return jsonify({"error": str(e)}), 400
    token = data.get("token")
    if token is not None and (not isinstance(token, str) or not token):
        return jsonify({"error": "token is the preview's token"}), 400
    previewed = len(rows) > 1 or token is not None

    with get_db() as conn:
        mark = conn.execute("SELECT COALESCE(MAX(id), 0) FROM change_rows").fetchone()[0]
        try:
            labelled = [_label_row(conn, choice, data, row) for row in rows]
            # Refunds find their purchases as the linker finds them: oldest
            # first, one that fits, until nothing more links.
            if choice.flow == "refund":
                refund_links.link_refunds(conn, only=frozenset(rows))
            # A row that now names a loan, or no longer does, changes what was paid.
            loan_interest.derive(conn)
        except _NoSuchRow as e:
            conn.rollback()
            return jsonify({"error": "no such row" if len(rows) == 1 else f"no such row: {e.args[0]}"}), 404
        except (UnknownLabel, BookNeeded, review.LabelRefused, expect_back.Refused, refund_links.Refused) as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 400
        if previewed:
            lines, stamped = _label_preview_lines(conn, rows, mark)
            asked = {**{k: v for k, v in data.items() if k != "token"}, "tx_id": tx_id}
            made = relabel_preview._stamp("label", json.dumps(asked, sort_keys=True, default=str), stamped)
            changes = len({(tbl, row_id) for tbl, row_id, *_ in stamped})
            if token is None:
                conn.rollback()
                return jsonify({
                    "error": (f"this says what {len(rows)} rows were, so nothing was changed: show the "
                              "operator the preview, and on their yes call again with its token"),
                    # changes: the rows of every table the change writes,
                    # as the history and the chat's count read them (refund
                    # links and their set-offs too); over MANY_ROWS it is the
                    # expected_count the call then takes.
                    "preview": {"rows": len(lines), "changes": changes,
                                "expected_count": changes if changes > mcp_tools.MANY_ROWS else None,
                                "lines": lines, "token": made},
                }), 409
            if token != made:
                conn.rollback()
                return jsonify({"error": PLAN_STALE, "stale": True}), 409
        conn.commit()
        named = conn.execute("SELECT name FROM accounts WHERE id = ?", (labelled[0]["other_side_id"],)).fetchone()
    answer = {
        **labelled[0],
        "success": True,
        "other_side_name": mask_card_number(named["name"]) if named else None,
    }
    if len(rows) > 1:
        answer["ids"] = rows
    return jsonify(answer)


# How many of the latest labels a transfer's guess looks back over, how many
# must agree before fin offers one, and what share of them.
REVIEW_GUESS_LOOK_BACK = 10
REVIEW_GUESS_AT_LEAST = 2
REVIEW_GUESS_SHARE = 0.6


def _label_as_choice(row, gifts: tuple[int, int]) -> tuple | None:
    """A row labelled by hand, read back as the review choice that would
    label it the same way: (choice, the label's body). None when no choice
    writes what the row holds. `gifts`: the types a gift given and a gift
    received are filed under now (their slots)."""
    flow_name, kind = row["flow_type"], row["other_kind"]
    if flow_name == "expense":
        if row["type_id"] == gifts[0]:
            return "gift", {}
        if row["type_id"] is None:
            return None
        return "spending", {"type_id": row["type_id"], **({"book": row["book"]} if row["book"] else {})}
    if flow_name == "income":
        if row["type_id"] == gifts[1]:
            return "gift", {}
        return ("income", {"income_kind_id": row["type_id"]}) if row["type_id"] is not None else None
    if row["other_side_id"] is None:
        return None
    # A movement naming a person reads as no choice: "a loan to a person"
    # retired at the contract, so a past loan is never guessed again.
    for choice in review.CHOICES:
        if choice.flow == flow_name and choice.asks == review.ASKS_ACCOUNT and kind in choice.kinds:
            return choice.name, {"account_id": row["other_side_id"]}
    return None


@app.route("/api/review/<int:tx_id>/suggestion")
def api_review_suggestion(tx_id: int):
    """fin's guess for a waiting transfer: how the latest rows with the same
    text, going the same way, were labelled by hand. Offered only when at
    least REVIEW_GUESS_AT_LEAST of them agree and they are at least
    REVIEW_GUESS_SHARE of those looked at. Read-only; it asks nobody.

    Returns guess: null, or {choice, body (what /label takes), type, book,
    other_side, agree, of, probability}."""
    with get_db() as conn:
        tx = conn.execute(
            "SELECT id, description, amount_minor FROM transactions WHERE id = ?", (tx_id,)
        ).fetchone()
        if tx is None:
            return jsonify({"error": "no such row"}), 404
        rows = conn.execute(
            "SELECT t.flow_type, t.other_side_id, t.type_id, t.book, a.type AS other_kind, "
            "a.name AS other_name, ty.name AS type_name, p.name AS parent_name "
            "FROM transactions t LEFT JOIN accounts a ON a.id = t.other_side_id "
            "LEFT JOIN types ty ON ty.id = t.type_id LEFT JOIN types p ON p.id = ty.parent_id "
            f"WHERE t.id != ? AND {_left_to_label('t.flow_type_manual = 1')} AND t.flow_type != ? "
            "AND UPPER(TRIM(t.description)) = UPPER(TRIM(?)) AND (t.amount_minor < 0) = (? < 0) "
            "ORDER BY t.date DESC, t.id DESC LIMIT ?",
            (tx_id, flow.REVIEW, tx["description"], tx["amount_minor"], REVIEW_GUESS_LOOK_BACK),
        ).fetchall()
        gifts = (book_type.slot_type(conn, book_type.GIFT_GIVEN_SLOT),
                 book_type.slot_type(conn, book_type.GIFT_RECEIVED_SLOT))
    tally: dict = {}
    for r in rows:
        read = _label_as_choice(r, gifts)
        if read is None:
            continue
        key = (read[0], json.dumps(read[1], sort_keys=True))
        if key not in tally:
            tally[key] = {"count": 0, "row": r, "choice": read[0], "body": read[1]}
        tally[key]["count"] += 1
    if not rows or not tally:
        return jsonify({"guess": None})
    top = max(tally.values(), key=lambda t: t["count"])
    share = top["count"] / len(rows)
    if top["count"] < REVIEW_GUESS_AT_LEAST or share < REVIEW_GUESS_SHARE:
        return jsonify({"guess": None})
    r = top["row"]
    return jsonify({"guess": {
        "choice": top["choice"],
        "body": {"choice": top["choice"], **top["body"]},
        "type": format_type_display(r["parent_name"], r["type_name"]) if r["type_name"] else None,
        "book": r["book"],
        "other_side": mask_card_number(r["other_name"]) if r["other_name"] else None,
        "agree": top["count"],
        "of": len(rows),
        "probability": round(share, 2),
    }})


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


def _bill_body(data, conn, held=None):
    """The checked fields of a bill write (bills.py): its cadence kept as the
    bill holds it when it means the same, and its currency the paying
    account's. Raises bills.BillRefused; nothing is written then."""
    if not isinstance(data, dict):
        raise bills.BillRefused("No fields to update")
    body = {k: v for k, v in data.items() if k not in ("every", "frequency", "periods", "currency")}
    body.update(bills.checked_cadence(
        data, held["frequency"] if held else None, held["periods"] if held else None))
    account_id = data["account_id"] if "account_id" in data else (held["account_id"] if held else None)
    paying = conn.execute("SELECT currency FROM accounts WHERE id = ?", (account_id,)).fetchone() if account_id else None
    if held is None:
        given = data.get("currency")
        if paying is not None and given not in (None, paying["currency"]):
            raise bills.BillRefused(
                f"a bill's currency is its paying account's ({paying['currency']}); nothing was added")
        body["currency"] = paying["currency"] if paying is not None else (given or "SGD")
    else:
        current = held["currency"] or "SGD"
        # A save never changes a bill's currency: that is the previewed fix's
        # (POST /api/bills/<id>/currency), which shows the amount it becomes.
        if "currency" in data and data["currency"] != current:
            raise bills.BillRefused(
                "a bill's currency changes only through its previewed fix (open the bill, Change it); nothing was saved")
        # Nor does moving it make it differ from its paying account's.
        if paying is not None and account_id != held["account_id"] and paying["currency"] != current:
            raise bills.BillRefused(
                f"that account is kept in {paying['currency']} and this bill in {current}: "
                "a bill is in its paying account's currency; nothing was saved")
    return body


def _bills_read() -> list[dict]:
    with get_db() as conn:
        return bills.read(conn, _get_usd_sgd_rate(), bills.today())


@app.route("/api/subscriptions")
def api_subscriptions():
    """Every bill as the Bills screen, the Queue and Home read it (bills.py,
    the one home of the missed-bill rule): with its cadence, its monthly
    share, its state (missed, awaiting statement, on track, paused, stopped)
    and whether its currency is its paying account's."""
    return jsonify(_bills_read())


@app.route("/api/bills")
def api_bills():
    """The bills as the chat reads them (bills.brief), with how many are in
    each state; ?state= keeps one state's."""
    shown = _bills_read()
    wanted = request.args.get("state")
    if wanted is not None and wanted not in bills.STATES:
        return jsonify({"error": f"state is one of {', '.join(bills.STATES)}"}), 400
    return jsonify({
        "bills": [bills.brief(b) for b in shown if wanted in (None, b["state"])],
        "counts": bills.counts(shown),
        "rule": (f"A due day is paid by a payment seen from {bills.EARLY_DAYS} days before it, and late "
                 f"{bills.GRACE_DAYS} days after. A bill is missed when a late due day its paying account's "
                 "statements cover (those days) has no payment since, however long ago; when only later due days "
                 "are late and not yet covered it is awaiting_statement: waiting_due is the oldest such due day "
                 "and waiting_on the month of the statement that would show it. A bill paid from no account, or from one fin holds no statements for, "
                 "is judged against today alone (judged_by: today)."),
    })


@app.route("/api/subscriptions", methods=["POST"])
def api_subscriptions_create():
    """Add a new bill. It takes its book and type from its merchant and its
    currency from its paying account."""
    data = request.get_json()
    if not data or not data.get("service_id"):
        return jsonify({"error": "service_id is required"}), 400

    with get_db() as conn:
        try:
            body = _bill_body({"frequency": "monthly", **data}, conn)
        except bills.BillRefused as e:
            return jsonify({"error": str(e)}), 400
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
                body["currency"],
                body["frequency"],
                body.get("periods", 1),
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
    """Update a bill. Its cadence (`every`) keeps the frequency and periods it
    holds when they mean the same; a frequency fin does not know, or a
    currency other than the paying account's, is refused."""
    data = request.get_json()
    with get_db() as conn:
        held = conn.execute("SELECT frequency, periods, currency, account_id FROM subscriptions WHERE id = ?",
                            (sub_id,)).fetchone()
        if held is None:
            return jsonify({"error": "No such bill"}), 404
        try:
            body = _bill_body(data, conn, held)
        except bills.BillRefused as e:
            return jsonify({"error": str(e)}), 400
    return _crud_update("subscriptions", sub_id, body, [
        "service_id", "amount", "currency", "frequency",
        "periods", "account_id", "last_paid", "renewal_date", "status",
        "link", "notes", "match_pattern",
    ])


@app.route("/api/bills/<int:sub_id>/currency", methods=["POST"])
def api_bills_currency(sub_id):
    """Put a flagged bill in its paying account's currency (bills.currency_fix).
    Without the preview's token nothing is written: the answer is the preview
    (409). With it, and an amount if the operator gave one, the bill changes
    as one change; a token the bill no longer matches is refused."""
    data = request.get_json(silent=True) or {}
    with get_db() as conn:
        try:
            preview = bills.currency_fix(conn, sub_id, _get_usd_sgd_rate(),
                                         *([data["amount"]] if "amount" in data else []))
        except LookupError:
            return jsonify({"error": "No such bill"}), 404
        except bills.BillRefused as e:
            return jsonify({"error": str(e)}), 400
        token = data.get("token")
        if token is None:
            return jsonify({"preview": preview}), 409
        if token != preview["token"]:
            return jsonify({"error": "The bill changed since the preview; nothing was changed. Look again.",
                            "preview": preview}), 409
        conn.execute("UPDATE subscriptions SET currency = ?, amount = ? WHERE id = ?",
                     (preview["after"]["currency"], preview["after"]["amount"], sub_id))
        conn.commit()
    return jsonify({"success": True, "bill": preview["after"]})


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
                new_renewal = bills.advance_renewal(
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
