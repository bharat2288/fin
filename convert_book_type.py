"""Conversion step: give existing rows, merchants, rules and subscriptions a
book and a type beside the category they still carry.

This is the expand half of the split. It adds the `types` table and the new
columns, fills them from the mapping table below, and removes nothing: every
category column keeps its value. The app reads book and type only, and will
not start on a database until this step and then retire_categories.py have
run on it. It runs only through the conversion runner (conversion.run_step).

The mapping is by category name, never by row count. A category the table
does not name is not guessed: its rows get no book and no type and are put on
the review list.

    python convert_book_type.py <path to database> [--rows]
"""

from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import book_type
import conversion

TYPED = "typed"                # the table gives a book and a type
REVIEW = "review"              # the table marks the category for review
NOT_SPENDING = "not-spending"  # not a spending type: the first axis decides


@dataclass(frozen=True)
class Outcome:
    kind: str
    book: str | None = None
    type: str | None = None  # a spending type's display name


def _typed(book: str, type_name: str, *categories: str) -> dict[str, Outcome]:
    return {c: Outcome(TYPED, book, type_name) for c in categories}


def _same_name(book: str, *categories: str) -> dict[str, Outcome]:
    return {c: Outcome(TYPED, book, c) for c in categories}


# Merchant-named Moom categories: the name becomes the merchant.
MERCHANT_NAMED = (
    "Moom > Klaviyo",
    "Moom > Canva",
    "Moom > Zendesk",
    "Moom > ManyChat",
    "Moom > Shopify",
    "Moom > Google Workspace",
    "Moom > Xero",
    "Moom > AI Software",
)

# Shopping and its children map to Household, Shopping, except the Amazon Web
# Services rows among them, which go to Kalesh, Software & AI tools.
SHOPPING = ("Shopping", "Shopping > Books", "Shopping > Online", "Shopping > Retail")
AMAZON_WEB_SERVICES = Outcome(TYPED, "Kalesh", "Software & AI tools")
# The full name, or AWS as a word of its own, in the row's description or its
# merchant's name.
_AMAZON_WEB_SERVICES_TEXT = re.compile(r"\bAMAZON WEB SERVICES\b|\bAWS\b", re.IGNORECASE)

# Today's category ('Parent > Child') to its book and type, as the type-list
# attachment's mapping table gives it.
CATEGORY_MAP: dict[str, Outcome] = {
    **_typed("Household", "Bank & government fees", "Admin > Bank Fees", "Admin > Government"),
    **_typed("Household", "Insurance", "Admin > Insurance"),
    **_typed("Household", "Tax", "Admin > Tax"),
    "Admin": Outcome(REVIEW, "Household"),
    "Other": Outcome(REVIEW, "Household"),
    "Personal": Outcome(REVIEW, "Household"),
    **_typed("Household", "Dining", "Dining", "Dining > Quick Service", "Dining > Coffee"),
    **_same_name(
        "Household",
        "Education", "Entertainment", "Groceries", "Health & Beauty", "Kids", "Pet",
        "Rent", "Travel", "Utilities", "Social Media", "Gifts & Donations",
    ),
    **_typed("Household", "Fitness", "Fitness", "Fitness > Gym & Classes"),
    **_typed("Household", "Fitness > Golf", "Fitness > Golf"),
    **_typed("Household", "Home", "Home"),
    **_typed("Household", "Repairs & maintenance", "Home > Maintenance"),
    **_typed("Household", "Medical", "Medical", "Medical > Hospital", "Medical > Specialist"),
    **_typed("Household", "Shopping", *SHOPPING),
    **_typed("Household", "Subscriptions", "Subscriptions"),
    **_typed(
        "Household", "Software & AI tools",
        "Subscriptions > AI Tools", "Subscriptions > Cloud & Storage", "Trading Tools",
    ),
    **_same_name(
        "Household",
        "Transport", "Transport > Rides", "Transport > Public Transit",
        "Transport > EV Charging", "Transport > Fuel",
    ),
    **_typed("Kalesh", "Software & AI tools", "Kalesh > Software"),
    **_typed("Kalesh", "People", "Kalesh > Payroll"),
    **_typed("Kalesh", "Professional services", "Kalesh > Accounting"),
    **_typed("Kalesh", "Bank & government fees", "Kalesh > Fees"),
    "Kalesh > Refunds": Outcome(NOT_SPENDING, "Kalesh"),
    # Not in the attachment's table; ruled on ticket 09 to map like bare Moom.
    "Kalesh": Outcome(REVIEW, "Kalesh"),
    "Moom": Outcome(REVIEW, "Moom"),
    **_typed("Moom", "Software & AI tools", *MERCHANT_NAMED),
    **_typed(
        "Moom", "Advertising",
        "Moom > Google Ads", "Moom > Facebook Ads", "Moom > TikTok Ads", "Moom > LinkedIn",
    ),
    **_typed("Moom", "Stock purchases", "Moom > Alibaba", "Moom > Shopee (Moom)"),
    **_typed("Moom", "Payment fees", "Moom > Stripe/Payments"),
    **_typed("Moom", "People", "Moom > Freelancers"),
    **_typed("Moom", "Office", "Moom > WeWork"),
    **_typed("Moom", "Equipment", "Moom > Equipment"),
    # Not a type: these rows become movements against their loan (ticket 05).
    "Loan/EMI": Outcome(NOT_SPENDING, "Household"),
}
# These and all their children leave the type list.
NOT_SPENDING_ROOTS = ("Credits", "Payments", "Transfers")


def _key(path: str) -> str:
    """A category path as it is compared: case and outer spaces ignored."""
    return " > ".join(part.strip().casefold() for part in path.split(" > "))


_MAP = {_key(path): outcome for path, outcome in CATEGORY_MAP.items()}
_NOT_SPENDING_ROOTS = {_key(root) for root in NOT_SPENDING_ROOTS}
_SHOPPING = {_key(path) for path in SHOPPING}
_MERCHANT_NAMED = {_key(path) for path in MERCHANT_NAMED}

# The four tables that gain a book and a type, and the columns involved.
# Frozen: table and column names in the statements below come from here and
# nowhere else.
#   table, category column, book column, type column
TARGETS = (
    ("transactions", "category_id", "book", "type_id"),
    ("services", "category_id", "book", "type_id"),
    ("merchant_rules", "category_override_id", "book_override", "type_override_id"),
    ("subscriptions", "category_id", "book", "type_id"),
)

# For each table, the unlabelled entries under given categories with the two
# texts that can name their merchant. `{marks}` is a list of ? placeholders.
_MERCHANT_TEXTS_SQL = {
    "transactions": (
        "SELECT x.id, x.description, s.name FROM transactions x"
        " LEFT JOIN services s ON s.id = x.service_id"
        " WHERE x.category_id IN ({marks}) AND x.book IS NULL AND x.type_id IS NULL"
    ),
    "services": (
        "SELECT x.id, x.name, NULL FROM services x"
        " WHERE x.category_id IN ({marks}) AND x.book IS NULL AND x.type_id IS NULL"
    ),
    "merchant_rules": (
        "SELECT x.id, x.pattern, s.name FROM merchant_rules x"
        " LEFT JOIN services s ON s.id = x.service_id"
        " WHERE x.category_override_id IN ({marks})"
        " AND x.book_override IS NULL AND x.type_override_id IS NULL"
    ),
    "subscriptions": (
        "SELECT x.id, x.match_pattern, s.name FROM subscriptions x"
        " LEFT JOIN services s ON s.id = x.service_id"
        " WHERE x.category_id IN ({marks}) AND x.book IS NULL AND x.type_id IS NULL"
    ),
}


class RowsLeftUnplaced(Exception):
    """After the fill, some row was neither typed, nor outside spending, nor
    on the review list. The runner rolls the step back."""


# --- reading the categories ----------------------------------------------


def _categories(conn: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    """Each category id to (its 'Parent > Child' path, its own name)."""
    rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, name, parent_id FROM categories")}
    out = {}
    for cat_id, (name, parent_id) in rows.items():
        parts, seen = [name], {cat_id}
        # Walk up to the root; `seen` stops a loop in bad data.
        while parent_id in rows and parent_id not in seen:
            seen.add(parent_id)
            parent_name, parent_id = rows[parent_id]
            parts.append(parent_name)
        out[cat_id] = (" > ".join(reversed(parts)), name)
    return out


def _outcome(path: str) -> Outcome | None:
    """What the table says of a category path; None when it does not name it."""
    key = _key(path)
    if key.split(" > ")[0] in _NOT_SPENDING_ROOTS:
        return Outcome(NOT_SPENDING)
    return _MAP.get(key)


def _marks(ids) -> str:
    return ", ".join("?" for _ in ids)


# --- the step -------------------------------------------------------------


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _has_shape(conn: sqlite3.Connection) -> bool:
    if not _columns(conn, "types"):
        return False
    return all(
        {book_col, type_col} <= _columns(conn, table)
        for table, _, book_col, type_col in TARGETS
    )


def _add_shape(conn: sqlite3.Connection) -> None:
    conn.execute(book_type.TYPES_TABLE_SQL)
    for table, _, book_col, type_col in TARGETS:
        present = _columns(conn, table)
        if book_col not in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {book_col} TEXT")
        if type_col not in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {type_col} INTEGER REFERENCES types(id)")


def _pending(conn: sqlite3.Connection) -> int:
    """How many entries the table can label that carry no label yet, plus the
    rows of merchant-named categories that still name no merchant."""
    categories = _categories(conn)
    labelled = [
        cat_id for cat_id, (path, _) in categories.items()
        if (o := _outcome(path)) and (o.book or o.type)
    ]
    merchant_named = [
        cat_id for cat_id, (path, _) in categories.items() if _key(path) in _MERCHANT_NAMED
    ]
    count = 0
    for table, cat_col, book_col, type_col in TARGETS:
        count += conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {cat_col} IN ({_marks(labelled)})"
            f" AND {book_col} IS NULL AND {type_col} IS NULL",
            labelled,
        ).fetchone()[0]
    for table in ("transactions", "subscriptions"):
        count += conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE category_id IN ({_marks(merchant_named)})"
            " AND service_id IS NULL",
            merchant_named,
        ).fetchone()[0]
    return count


def _categories_retired(conn: sqlite3.Connection) -> bool:
    """Whether the category tree is gone (retire_categories.py has run, or the
    database was created after it): there is nothing left to map from."""
    return not _columns(conn, "categories")


def _is_applied(conn: sqlite3.Connection) -> bool:
    if not (_has_shape(conn) and book_type.types_are_seeded(conn)):
        return False
    return _categories_retired(conn) or _pending(conn) == 0


def _apply(conn: sqlite3.Connection) -> None:
    _add_shape(conn)
    book_type.seed_types(conn)
    type_ids = book_type.spending_type_ids(conn)
    categories = _categories(conn)

    # Only an entry with no book and no type is written, so a label already
    # there (from an earlier run, or set since) is never overwritten.

    # The Amazon Web Services exception first, so the general fill below
    # leaves those entries alone.
    shopping = [cat_id for cat_id, (path, _) in categories.items() if _key(path) in _SHOPPING]
    aws = (AMAZON_WEB_SERVICES.book, type_ids[AMAZON_WEB_SERVICES.type])
    for table, _, book_col, type_col in TARGETS:
        found = conn.execute(
            _MERCHANT_TEXTS_SQL[table].format(marks=_marks(shopping)), shopping
        ).fetchall()
        for entry_id, text, merchant in found:
            if any(t and _AMAZON_WEB_SERVICES_TEXT.search(t) for t in (text, merchant)):
                conn.execute(
                    f"UPDATE {table} SET {book_col} = ?, {type_col} = ? WHERE id = ?",
                    (*aws, entry_id),
                )

    for cat_id, (path, name) in categories.items():
        outcome = _outcome(path)
        if outcome is None or not (outcome.book or outcome.type):
            continue
        label = (outcome.book, type_ids[outcome.type] if outcome.type else None)
        for table, cat_col, book_col, type_col in TARGETS:
            conn.execute(
                f"UPDATE {table} SET {book_col} = ?, {type_col} = ?"
                f" WHERE {cat_col} = ? AND {book_col} IS NULL AND {type_col} IS NULL",
                (*label, cat_id),
            )
        if _key(path) in _MERCHANT_NAMED:
            _name_the_merchant(conn, cat_id, name.strip(), label)

    counts = summary(conn)
    placed = counts["typed"] + counts["not_spending"] + counts["review"]
    if counts["unplaced"] or placed != counts["rows"]:
        raise RowsLeftUnplaced()


def _name_the_merchant(conn: sqlite3.Connection, cat_id: int, name: str, label: tuple) -> None:
    """Give the rows and subscriptions of a merchant-named category that name
    no merchant the merchant their category named. One is made only if needed
    and none of that name exists; an entry that names a merchant keeps it."""
    bare = sum(
        conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE category_id = ? AND service_id IS NULL",
            (cat_id,),
        ).fetchone()[0]
        for table in ("transactions", "subscriptions")
    )
    if not bare:
        return
    merchant = conn.execute(
        "SELECT id FROM services WHERE UPPER(name) = UPPER(?) ORDER BY id LIMIT 1", (name,)
    ).fetchone()
    if merchant:
        merchant_id = merchant[0]
    else:
        merchant_id = conn.execute(
            "INSERT INTO services (name, category_id, book, type_id) VALUES (?, ?, ?, ?)",
            (name, cat_id, *label),
        ).lastrowid
    for table in ("transactions", "subscriptions"):
        conn.execute(
            f"UPDATE {table} SET service_id = ? WHERE category_id = ? AND service_id IS NULL",
            (merchant_id, cat_id),
        )


def _invariant(before: dict, after: dict) -> list[str]:
    """Nothing moves, except that merchants may be added: at most one for
    each merchant-named category."""
    was, now = before["rows"].get("services", 0), after["rows"].get("services", 0)
    problems = conversion.unchanged(
        {**before, "rows": {**before["rows"], "services": now}}, after
    )
    if not 0 <= now - was <= len(MERCHANT_NAMED):
        problems.append(
            f"row count of services changed: {was} -> {now}; at most "
            f"{len(MERCHANT_NAMED)} merchants may be added and none removed"
        )
    return problems


STEP = conversion.Step(
    name="book-and-type", apply=_apply, is_applied=_is_applied, invariant=_invariant
)


# --- what the conversion did ----------------------------------------------


# The flows a type is for. A row of any other flow (income, a transfer, a
# payment) is not waiting for a type and is never listed for review. A row
# with no flow yet is read as spending, as the app reads it.
SPENDING_FLOWS = ("expense", "refund")


def _placement(
    outcome: Outcome | None, has_category: bool, book: str | None, flow: str | None
) -> tuple[str, str]:
    """Where a row with no type stands: (bucket, reason)."""
    bucket, reason = _placement_by_category(outcome, has_category, book)
    if bucket == "review" and (flow or "expense") not in SPENDING_FLOWS:
        return "not_spending", ""
    return bucket, reason


def _placement_by_category(
    outcome: Outcome | None, has_category: bool, book: str | None
) -> tuple[str, str]:
    if not has_category:
        return "review", "no category"
    if outcome is None:
        return "review", "category not in the mapping table"
    if outcome.kind == NOT_SPENDING:
        return "not_spending", ""
    if outcome.kind == REVIEW:
        return "review", "marked for review"
    # The table gives this category a type. With no book either, the row was
    # never filled; with a book, its type was taken off since.
    return ("unplaced", "") if book is None else ("review", "no type")


def summary(conn: sqlite3.Connection) -> dict:
    """Counts over all rows of a converted database. Every row is in exactly
    one of typed, not_spending, review, unplaced; unplaced is 0 once the step
    has run."""
    categories = _categories(conn)
    counts = {"rows": 0, "typed": 0, "not_spending": 0, "review": 0, "unplaced": 0}
    counts["rows"] = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    counts["typed"] = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE type_id IS NOT NULL"
    ).fetchone()[0]
    for cat_id, book, flow, n in conn.execute(
        "SELECT category_id, book, flow_type, COUNT(*) FROM transactions WHERE type_id IS NULL"
        " GROUP BY category_id, book, flow_type"
    ):
        path = categories.get(cat_id, (None, None))[0]
        bucket, _ = _placement(_outcome(path) if path else None, path is not None, book, flow)
        counts[bucket] += n

    shopping = [cat_id for cat_id, (path, _) in categories.items() if _key(path) in _SHOPPING]
    counts["amazon_web_services_rows"] = conn.execute(
        f"SELECT COUNT(*) FROM transactions WHERE category_id IN ({_marks(shopping)})"
        " AND book = ? AND type_id = (SELECT id FROM types WHERE kind = ? AND name = ?)",
        (*shopping, AMAZON_WEB_SERVICES.book, book_type.SPENDING, AMAZON_WEB_SERVICES.type),
    ).fetchone()[0]
    return counts


def review_list(conn: sqlite3.Connection) -> list[dict]:
    """The spending and refund rows left without a type for the operator to
    place, oldest id first, each with the reason it is listed."""
    categories = _categories(conn)
    listed = []
    for row_id, day, description, amount, cat_id, book, flow in conn.execute(
        "SELECT id, date, description, amount_sgd, category_id, book, flow_type FROM transactions"
        " WHERE type_id IS NULL ORDER BY id"
    ):
        path = categories.get(cat_id, (None, None))[0]
        bucket, reason = _placement(
            _outcome(path) if path else None, path is not None, book, flow
        )
        if bucket == "review":
            listed.append({
                "id": row_id,
                "date": day,
                "description": description,
                "amount_sgd": amount,
                "category": path,
                "reason": reason,
            })
    return listed


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--rows"]
    if len(args) != 1:
        print("usage: python convert_book_type.py <path to database> [--rows]")
        return 2
    try:
        report = conversion.run_step(args[0], STEP)
    except (conversion.ConversionRefused, conversion.ConversionFailed) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print(f"step {report['step']}: {report['status']}")
    if report["backup"]:
        print(f"backup: {report['backup']}")
        print(f"rows before: {report['before']['rows']}")
        print(f"rows after:  {report['after']['rows']}")
        print(f"account totals (cents) before: {report['before']['account_totals_cents']}")
        print(f"account totals (cents) after:  {report['after']['account_totals_cents']}")

    conn = sqlite3.connect(f"{Path(args[0]).resolve().as_uri()}?mode=ro", uri=True)
    try:
        if _categories_retired(conn):
            print("the categories are retired from this database: nothing to convert")
            return 0
        counts = summary(conn)
        listed = review_list(conn)
    finally:
        conn.close()
    print(
        f"{counts['rows']} rows: {counts['typed']} typed, "
        f"{counts['not_spending']} not spending, {counts['review']} for review"
    )
    print(f"Amazon Web Services rows moved from Shopping: {counts['amazon_web_services_rows']}")
    groups: dict[tuple[str, str], int] = {}
    for row in listed:
        where = (row["reason"], row["category"] or "")
        groups[where] = groups.get(where, 0) + 1
    print("review list:")
    for (reason, category), n in sorted(groups.items()):
        print(f"  {n:5d}  {reason}" + (f": {category}" if category else ""))
    if "--rows" in argv:
        for row in listed:
            print(
                f"  #{row['id']} {row['date']} {row['amount_sgd']:.2f} "
                f"{row['description']} [{row['category'] or 'no category'}]"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
