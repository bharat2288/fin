"""One-shot repair: fix mis-signed bank-PDF transactions (2026-06-11 sweep).

The DBS/UOB bank PDF parsers guessed direction by keyword and defaulted to
"withdrawal", so deposits imported from PDFs (Home/BS Jan-Sep 2025, UOB One
Jan 2025 - Feb 2026) carry positive amounts. Parsers now derive direction
from the running-balance delta; this script re-parses the source PDFs,
matches DB rows by (date, |amount|), flips signs that disagree, then
re-derives flow_type for all non-manual rows with the current classifier.

Usage: python repair_bank_signs.py [--commit]
Without --commit it reports what it would change.
"""

import sqlite3
import sys
from collections import defaultdict

from flow import build_context, classify_flow
from parsers import auto_detect_and_parse

DB_PATH = "fin.db"
GD = "G:/My Drive/Personal Docs/Statements"

# account_id -> (folder, list of YYYY-MM PDF basenames)
PDF_SOURCES = {
    1: (f"{GD}/DBS Home 2771", [f"2025-{m:02d}" for m in range(1, 10)]),
    23: (f"{GD}/DBS BS 2763", [f"2025-{m:02d}" for m in range(1, 10)]),
    21: (
        f"{GD}/UOB One 3392 MK",
        [f"2025-{m:02d}" for m in range(1, 13)] + ["2026-01", "2026-02"],
    ),
}


def parsed_rows_for_account(folder: str, months: list[str]) -> dict:
    """Re-parse source PDFs -> {(date, abs_amount): [signed amounts]}."""
    lookup = defaultdict(list)
    for ym in months:
        path = f"{folder}/{ym}.pdf"
        try:
            stmts = auto_detect_and_parse(path)
        except FileNotFoundError:
            print(f"  ! missing source file {path}")
            continue
        for s in stmts:
            for t in s.transactions:
                lookup[(t.date, round(abs(t.amount_sgd), 2))].append(t.amount_sgd)
    return lookup


def main() -> None:
    commit = "--commit" in sys.argv
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    sign_fixes = []
    unmatched = []

    for account_id, (folder, months) in PDF_SOURCES.items():
        lookup = parsed_rows_for_account(folder, months)
        db_rows = conn.execute(
            """
            SELECT t.id, t.date, t.description, t.amount_sgd, t.flow_type,
                   t.flow_type_manual
            FROM transactions t
            JOIN statements s ON s.id = t.statement_id
            WHERE s.account_id = ?
              AND strftime('%Y-%m', s.statement_date) IN ({})
            ORDER BY t.date, t.id
            """.format(",".join("?" * len(months))),
            (account_id, *months),
        ).fetchall()

        for row in db_rows:
            key = (row["date"], round(abs(row["amount_sgd"]), 2))
            candidates = lookup.get(key)
            if not candidates:
                unmatched.append(row)
                continue
            parsed_amount = candidates.pop(0)  # consume for same-day repeats
            if (parsed_amount < 0) != (row["amount_sgd"] < 0):
                sign_fixes.append((row, parsed_amount))

    print(f"Sign flips needed: {len(sign_fixes)}  |  unmatched DB rows: {len(unmatched)}")
    for row, new_amount in sign_fixes:
        manual = " [FLOW MANUAL]" if row["flow_type_manual"] else ""
        print(
            f"  id={row['id']:5d} {row['date']} {row['amount_sgd']:>12,.2f} -> "
            f"{new_amount:>12,.2f} ({row['flow_type']}){manual} {row['description'][:55]}"
        )
    for row in unmatched:
        print(f"  ? unmatched id={row['id']} {row['date']} {row['amount_sgd']:,.2f} {row['description'][:55]}")

    if not commit:
        print("\nDry run - rerun with --commit to apply.")
        return

    for row, new_amount in sign_fixes:
        conn.execute(
            "UPDATE transactions SET amount_sgd = ? WHERE id = ?",
            (new_amount, row["id"]),
        )
    conn.commit()

    # Re-derive flow_type for every non-manual row with the current classifier
    ctx = build_context(conn)
    cats = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM categories")}
    reflowed = 0
    for row in conn.execute(
        """SELECT id, description, amount_sgd, category_id, flow_type
           FROM transactions WHERE COALESCE(flow_type_manual, 0) = 0"""
    ).fetchall():
        new_flow = classify_flow(
            {
                "description": row["description"],
                "amount_sgd": row["amount_sgd"],
                "category_name": cats.get(row["category_id"]),
            },
            ctx,
        )
        if new_flow != row["flow_type"]:
            conn.execute(
                "UPDATE transactions SET flow_type = ? WHERE id = ?",
                (new_flow, row["id"]),
            )
            reflowed += 1
    conn.commit()
    print(f"\nApplied {len(sign_fixes)} sign fixes; re-derived flow_type on {reflowed} rows.")


if __name__ == "__main__":
    main()
