"""Ingest parsed statements into the fin database.

Handles both PDF and CSV statement files. Auto-detects format.
Designed to be run by Claude during a chat session.

Usage:
    py ingest.py <path1> [path2] [path3] ...
    py ingest.py --commit <path1> [path2] ...   # parse + save to DB
"""

import re
import sqlite3
import sys
from pathlib import Path

from db import get_connection, init_db, categorize_transaction
from parse_dbs import parse_statement, ParsedTransaction, ParsedStatement

# Bank statement PayNow payee → category mapping
# These are identified by the "To:" field in bank statement descriptions
PAYNOW_RULES = [
    # (pattern_in_description, category_name, notes)
    ("SINGAPORE LIFE", "Insurance", "Life insurance premium"),
    ("INLAND REVENUE", "Other", "Tax payment (IRAS)"),
    ("SINGAPORE ISLAND", "Fitness", "SICC country club"),
    ("SICC", "Fitness", "SICC country club"),
    ("SITOH SIEW KIM", "Kids", "Nanny"),
    ("OSTEOPATHIC", "Medical", "Osteopath"),
    ("TERRA MEDICAL", "Medical", "Medical"),
    ("LIMITLESS WELLNESS", "Fitness", "Wellness"),
    ("VIN GOLF", "Fitness", "Golf"),
    ("RK", "Other", "Miscellaneous"),
    ("AMAC", "Home", "Appliance repair"),
    ("BJT", "Other", "Unknown"),
    ("FATELICIOUS", "Dining", "Snacks"),
    ("DREAMCORE", "Other", "PC parts"),
    ("JEREMY L", "Other", "Unknown"),
    ("LEC", "Other", "Unknown"),
]


def auto_parse(filepath: str) -> ParsedStatement:
    """Auto-detect file format (PDF or CSV) and parse."""
    path = Path(filepath)
    ext = path.suffix.lower()

    if ext == ".pdf":
        return parse_statement(filepath)
    elif ext == ".csv":
        from parse_dbs_csv import parse_csv
        return parse_csv(filepath)
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def categorize_bank_paynow(description: str) -> tuple[int | None, str | None]:
    """Match bank statement PayNow/transfer descriptions to categories.

    Returns (category_id, category_name) or (None, None) if no match.
    Requires a DB lookup, so we return the category name for later resolution.
    """
    desc_upper = description.upper()
    for pattern, cat_name, _ in PAYNOW_RULES:
        if pattern.upper() in desc_upper:
            return None, cat_name  # category_name, resolve to ID later
    return None, None


def ensure_account(conn: sqlite3.Connection, card_info: str, stmt_type: str) -> int:
    """Find or create an account from card info string."""
    if not card_info:
        card_info = "Unknown Account"

    existing = conn.execute(
        "SELECT id FROM accounts WHERE name = ?", (card_info,)
    ).fetchone()
    if existing:
        return existing[0]

    digits = re.findall(r"\d{4}", card_info)
    last_four = digits[-1] if digits else None

    conn.execute(
        "INSERT INTO accounts (name, short_name, type, last_four) VALUES (?, ?, ?, ?)",
        (card_info, card_info, stmt_type, last_four),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def ensure_statement(
    conn: sqlite3.Connection,
    account_id: int,
    statement_date: str,
    filename: str,
) -> int | None:
    """Create a statement record. Returns None if already imported."""
    existing = conn.execute(
        "SELECT id FROM statements WHERE account_id = ? AND statement_date = ?",
        (account_id, statement_date),
    ).fetchone()
    if existing:
        return None

    conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, ?)",
        (account_id, statement_date, filename),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def categorize_all(
    conn: sqlite3.Connection,
    transactions: list[ParsedTransaction],
) -> tuple[list[dict], list[dict]]:
    """Categorize all transactions. Returns (categorized, uncategorized)."""
    categorized = []
    uncategorized = []

    cats = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM categories").fetchall()}
    cats_by_name = {v: k for k, v in cats.items()}

    for tx in transactions:
        if tx.is_payment:
            continue

        # Skip internal transfers (bank→bank, bill payments to own CC)
        if tx.is_transfer:
            continue

        # Try merchant rules first
        cat_id, svc_id = categorize_transaction(tx.description, conn, amount=tx.amount_sgd)

        # For bank statements, try PayNow rules if merchant rules didn't match
        if cat_id is None and "PAYNOW" in tx.description.upper():
            _, cat_name = categorize_bank_paynow(tx.description)
            if cat_name:
                cat_id = cats_by_name.get(cat_name)

        entry = {
            "date": tx.date,
            "description": tx.description,
            "amount_sgd": tx.amount_sgd,
            "amount_foreign": tx.amount_foreign,
            "currency_foreign": tx.currency_foreign,
            "category_id": cat_id,
            "service_id": svc_id,
            "category_name": cats.get(cat_id, "???") if cat_id else None,
            "card_info": tx.card_info,
            "is_payment": tx.is_payment,
            "is_transfer": tx.is_transfer,
        }

        if cat_id:
            categorized.append(entry)
        else:
            uncategorized.append(entry)

    return categorized, uncategorized


def print_categorized_summary(
    categorized: list[dict],
    uncategorized: list[dict],
) -> None:
    """Print a summary grouped by category with totals."""
    by_category: dict[str, list[dict]] = {}
    for tx in categorized:
        cat = tx["category_name"]
        by_category.setdefault(cat, []).append(tx)

    cat_totals = [
        (cat, sum(t["amount_sgd"] for t in txs), txs)
        for cat, txs in by_category.items()
    ]
    cat_totals.sort(key=lambda x: x[1], reverse=True)

    total_categorized = sum(t["amount_sgd"] for t in categorized if t["amount_sgd"] > 0)
    total_uncategorized = sum(t["amount_sgd"] for t in uncategorized if t["amount_sgd"] > 0)
    grand_total = total_categorized + total_uncategorized

    print(f"\n{'='*70}")
    print(f"EXPENSE BREAKDOWN")
    print(f"{'='*70}")

    personal_total = sum(
        t["amount_sgd"]
        for t in categorized
        if t["amount_sgd"] > 0 and t["category_name"] != "Moom"
    )
    moom_total = sum(
        t["amount_sgd"]
        for t in categorized
        if t["amount_sgd"] > 0 and t["category_name"] == "Moom"
    )

    for cat_name, total, txs in cat_totals:
        expenses_in_cat = [t for t in txs if t["amount_sgd"] > 0]
        credits_in_cat = [t for t in txs if t["amount_sgd"] < 0]
        if not expenses_in_cat:
            continue
        cat_total = sum(t["amount_sgd"] for t in expenses_in_cat)
        pct = (cat_total / grand_total * 100) if grand_total > 0 else 0
        marker = " [BUSINESS]" if cat_name == "Moom" else ""
        print(f"\n  {cat_name}{marker}: SGD {cat_total:,.2f} ({pct:.1f}%)")
        print(f"  {'-'*50}")
        for tx in sorted(expenses_in_cat, key=lambda t: t["amount_sgd"], reverse=True):
            fx = f" ({tx['currency_foreign']} {tx['amount_foreign']:,.2f})" if tx["amount_foreign"] else ""
            print(f"    {tx['date']}  {tx['amount_sgd']:>10,.2f}  {tx['description'][:40]}{fx}")
        if credits_in_cat:
            for tx in credits_in_cat:
                print(f"    {tx['date']}  {tx['amount_sgd']:>10,.2f}  {tx['description'][:40]} [CREDIT]")

    print(f"\n{'='*70}")
    print(f"  Categorized:   SGD {total_categorized:>12,.2f}  ({len(categorized)} items)")
    if moom_total > 0:
        print(f"    Personal:    SGD {personal_total:>12,.2f}")
        print(f"    Moom:        SGD {moom_total:>12,.2f}")
    print(f"  Uncategorized: SGD {total_uncategorized:>12,.2f}  ({len(uncategorized)} items)")
    print(f"  TOTAL:         SGD {grand_total:>12,.2f}")
    print(f"{'='*70}")

    if uncategorized:
        unc_expenses = [t for t in uncategorized if t["amount_sgd"] > 0]
        unc_expenses.sort(key=lambda t: t["amount_sgd"], reverse=True)
        print(f"\n{'='*70}")
        print(f"UNCATEGORIZED ({len(unc_expenses)} expenses) -- need your input:")
        print(f"{'='*70}")
        for i, tx in enumerate(unc_expenses):
            fx = f" ({tx['currency_foreign']} {tx['amount_foreign']:,.2f})" if tx["amount_foreign"] else ""
            print(f"  [{i+1:>3}] {tx['date']}  {tx['amount_sgd']:>10,.2f}  {tx['description'][:50]}{fx}")
        unc_credits = [t for t in uncategorized if t["amount_sgd"] < 0]
        if unc_credits:
            print(f"\n  Credits (uncategorized):")
            for tx in unc_credits:
                print(f"        {tx['date']}  {tx['amount_sgd']:>10,.2f}  {tx['description'][:50]}")


def save_transactions(
    conn: sqlite3.Connection,
    statement_id: int,
    transactions: list[dict],
) -> int:
    """Save transactions to the database. Returns count saved."""
    count = 0
    for tx in transactions:
        conn.execute(
            "INSERT INTO transactions "
            "(statement_id, date, description, amount_sgd, amount_foreign, "
            "currency_foreign, category_id, is_payment, is_transfer, is_anomaly) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                statement_id,
                tx["date"],
                tx["description"],
                tx["amount_sgd"],
                tx.get("amount_foreign"),
                tx.get("currency_foreign"),
                tx.get("category_id"),
                1 if tx.get("is_payment") else 0,
                1 if tx.get("is_transfer") else 0,
                1 if tx.get("is_anomaly") else 0,
            ),
        )
        count += 1
    conn.commit()
    return count


def add_merchant_rule(
    conn: sqlite3.Connection,
    pattern: str,
    category_name: str,
    match_type: str = "contains",
) -> None:
    """Add a new merchant rule."""
    cat = conn.execute(
        "SELECT id FROM categories WHERE name = ?", (category_name,)
    ).fetchone()
    if not cat:
        raise ValueError(f"Unknown category: {category_name}")

    conn.execute(
        "INSERT OR REPLACE INTO merchant_rules (pattern, category_id, match_type, confidence) "
        "VALUES (?, ?, ?, 'confirmed')",
        (pattern, cat[0], match_type),
    )
    conn.commit()


def commit_statements(filepaths: list[str]) -> None:
    """Parse, categorize, and save all statements to the database.

    This is the full pipeline: parse → categorize → store.
    Call after reviewing uncategorized items and adding merchant rules.
    """
    conn = get_connection()
    total_saved = 0

    for filepath in filepaths:
        stmt = auto_parse(filepath)
        print(f"\nCommitting: {stmt.filename} ({stmt.statement_type}, {stmt.statement_date})")

        # Create account + statement records
        for acct_name in stmt.accounts:
            account_id = ensure_account(conn, acct_name, stmt.statement_type)
            statement_id = ensure_statement(conn, account_id, stmt.statement_date, stmt.filename)

            if statement_id is None:
                print(f"  SKIPPED: already imported (account={acct_name}, date={stmt.statement_date})")
                continue

            # Categorize and save
            cat, uncat = categorize_all(conn, [
                tx for tx in stmt.transactions if tx.card_info == acct_name or not tx.card_info
            ])
            all_txns = cat + uncat
            count = save_transactions(conn, statement_id, all_txns)
            total_saved += count
            print(f"  Saved {count} transactions ({len(cat)} categorized, {len(uncat)} uncategorized)")

    conn.close()
    print(f"\nTotal: {total_saved} transactions committed to database")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py ingest.py [--commit] <file1> [file2] ...")
        sys.exit(1)

    init_db()

    # Check for --commit flag
    args = sys.argv[1:]
    do_commit = False
    if args[0] == "--commit":
        do_commit = True
        args = args[1:]

    if do_commit:
        commit_statements(args)
    else:
        # Preview mode: parse and show categorization, don't save
        conn = get_connection()
        all_categorized = []
        all_uncategorized = []

        for filepath in args:
            stmt = auto_parse(filepath)
            print(f"{stmt.filename}: {stmt.statement_type} | {stmt.accounts[0][:40] if stmt.accounts else '?'} | {len(stmt.transactions)} txns")
            cat, uncat = categorize_all(conn, stmt.transactions)
            all_categorized.extend(cat)
            all_uncategorized.extend(uncat)

        print(f"\nTotal: {len(all_categorized)} categorized, {len(all_uncategorized)} uncategorized")
        print_categorized_summary(all_categorized, all_uncategorized)
        conn.close()
