"""Backfill flow_type for all existing transactions (ADR v2).

Runs the shared classify_flow() over every row. Ignores dirty is_payment /
is_transfer flags (v1 review flagged them as unreliable — Cash Rebate rows
carried is_payment=1 in live data). Writes a review-queue CSV for rows
where the classifier's answer diverges from the legacy flag.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from flow import build_context, classify_flow


def backfill(conn: sqlite3.Connection, review_csv_path: Path | None = None) -> dict:
    """Populate flow_type for every row with NULL flow_type.

    Returns a summary dict:
        {rows_scanned, rows_written, rows_review, by_flow_type}
    """
    ctx = build_context(conn)

    # Legacy is_payment/is_transfer columns may or may not exist — grep-gate drop
    tx_cols = {row["name"] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    has_legacy = "is_payment" in tx_cols and "is_transfer" in tx_cols

    legacy_sel = "t.is_payment, t.is_transfer," if has_legacy else "0 as is_payment, 0 as is_transfer,"
    rows = conn.execute(
        f"""
        SELECT t.id, t.description, t.amount_sgd, {legacy_sel}
               c.name AS category_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.flow_type IS NULL
        """
    ).fetchall()

    writes: list[tuple[str, int]] = []
    review: list[dict] = []
    counts: dict[str, int] = {}

    for r in rows:
        facts = {
            "description": r["description"],
            "amount_sgd": r["amount_sgd"],
            "category_name": r["category_name"],
        }
        ft = classify_flow(facts, ctx)
        counts[ft] = counts.get(ft, 0) + 1
        writes.append((ft, r["id"]))

        # Review queue: legacy flag disagrees with new classifier (only if legacy cols exist)
        if has_legacy:
            legacy_says_payment = bool(r["is_payment"])
            legacy_says_transfer = bool(r["is_transfer"])
            disagrees = (
                (legacy_says_payment and ft != "payment")
                or (legacy_says_transfer and ft != "transfer")
            )
            if disagrees:
                review.append({
                    "id": r["id"],
                    "description": r["description"],
                    "amount_sgd": r["amount_sgd"],
                    "category_name": r["category_name"] or "",
                    "classifier_says": ft,
                    "legacy_is_payment": int(legacy_says_payment),
                    "legacy_is_transfer": int(legacy_says_transfer),
                })

    conn.executemany("UPDATE transactions SET flow_type = ? WHERE id = ?", writes)
    conn.commit()

    if review_csv_path is not None and review:
        review_csv_path.parent.mkdir(parents=True, exist_ok=True)
        with review_csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(review[0].keys()))
            writer.writeheader()
            writer.writerows(review)

    return {
        "rows_scanned": len(rows),
        "rows_written": len(writes),
        "rows_review": len(review),
        "by_flow_type": counts,
    }


if __name__ == "__main__":
    from db import get_connection

    conn = get_connection()
    try:
        result = backfill(
            conn,
            review_csv_path=Path(__file__).parent / "specs/samples/flow-type-backfill-review.csv",
        )
        print("Backfill complete:")
        for k, v in result.items():
            print(f"  {k}: {v}")
    finally:
        conn.close()
