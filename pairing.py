"""Pair matching: naming the other side of a move between household accounts.

A transfer between two of the household's own accounts, or a card payoff, is
two rows, one per statement. `match_pairs` finds the two and has each name
the other's account. The import calls it after a statement is saved and the
on-demand endpoint calls it; there is no other writer.

The rule (spec, Pair matching):
  - a row that may be paired is on a household bank account or card, is a
    transfer, a payment or waiting on the review list, names no other side,
    and was not set by hand;
  - its pair is such a row on a different household account, in the same
    currency, with the same amount to the minor unit and the opposite sign,
    dated within three days;
  - of several candidates the nearest date wins; an exact tie pairs nothing;
  - an amount that differs at all is not matched.

A row that was set by hand is never read as a candidate and never written.
A paired row names its other side, so it is not a candidate again: running
the match twice changes nothing.
"""

from __future__ import annotations

from datetime import date

import account_kind
import flow

# How far apart, in days, the two rows of a pair may be dated.
WINDOW_DAYS = 3
# The flows a row may have and still be looking for its pair.
PAIRABLE_FLOWS = ("transfer", "payment", flow.REVIEW)
# What a waiting row becomes when it finds its pair.
PAIRED_WITH_CARD = "payment"
PAIRED_FLOW = "transfer"


def _open_rows(conn) -> list[dict]:
    """The rows still looking for a pair, oldest first."""
    rows = conn.execute(
        "SELECT t.id, t.date, t.amount_minor, t.flow_type, "
        "       a.id AS account_id, a.type AS account_kind, "
        "       COALESCE(a.currency, 'SGD') AS currency "
        "FROM transactions t "
        "JOIN statements s ON t.statement_id = s.id "
        "JOIN accounts a ON s.account_id = a.id "
        "WHERE a.owner = ? AND a.type IN (?, ?) "
        "  AND t.flow_type IN (?, ?, ?) "
        "  AND COALESCE(t.flow_type_manual, 0) = 0 "
        "  AND t.other_side_id IS NULL "
        "  AND t.amount_minor IS NOT NULL AND t.amount_minor != 0 "
        "ORDER BY t.date, t.id",
        (account_kind.HOUSEHOLD, *account_kind.STATEMENT_KINDS, *PAIRABLE_FLOWS),
    ).fetchall()
    found = []
    for r in rows:
        try:
            day = date.fromisoformat(r["date"])
        except (TypeError, ValueError):
            continue  # a row with no readable date is nobody's pair
        found.append({**dict(r), "day": day})
    return found


def _nearest(row: dict, others: list[dict]) -> dict | None:
    """The one candidate nearest in date to `row`, or None when there is no
    candidate or two are equally near."""
    best = None
    best_gap = None
    tied = False
    for other in others:
        if other["account_id"] == row["account_id"]:
            continue
        gap = abs((other["day"] - row["day"]).days)
        if gap > WINDOW_DAYS:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap, tied = other, gap, False
        elif gap == best_gap:
            tied = True
    return None if tied else best


def _pairs_among(out_rows: list[dict], in_rows: list[dict]) -> list[tuple[dict, dict]]:
    """Pair rows of one amount and currency: money out against money in. Two
    rows are a pair when each is the other's one nearest candidate. A pair
    made leaves the pool, and what is left is looked at again, until a pass
    pairs nothing: the result is what a second run would also reach."""
    out_rows, in_rows = list(out_rows), list(in_rows)
    pairs = []
    while True:
        made = []
        for out in out_rows:
            back = _nearest(out, in_rows)
            if back is not None and _nearest(back, out_rows) is out:
                made.append((out, back))
        if not made:
            return pairs
        pairs.extend(made)
        out_rows = [r for r in out_rows if all(r is not o for o, _ in made)]
        in_rows = [r for r in in_rows if all(r is not b for _, b in made)]


def _paired_flow(row: dict, other: dict) -> str:
    """The flow a row has once paired: a transfer or payment keeps its flow; a
    waiting row becomes a payment when one side is a card, else a transfer."""
    if row["flow_type"] != flow.REVIEW:
        return row["flow_type"]
    if "card" in (row["account_kind"], other["account_kind"]):
        return flow.checked_flow(PAIRED_WITH_CARD)
    return flow.checked_flow(PAIRED_FLOW)


def match_pairs(conn) -> int:
    """Pair what can be paired and return how many pairs were made. Writes on
    `conn` and leaves the commit to the caller."""
    by_amount: dict[tuple, list[dict]] = {}
    for row in _open_rows(conn):
        by_amount.setdefault((row["currency"], abs(row["amount_minor"])), []).append(row)

    made = 0
    for rows in by_amount.values():
        out_rows = [r for r in rows if r["amount_minor"] > 0]
        in_rows = [r for r in rows if r["amount_minor"] < 0]
        for out, back in _pairs_among(out_rows, in_rows):
            for row, other in ((out, back), (back, out)):
                # The guard is repeated on the write: a row set by hand, or
                # already naming an other side, is not changed.
                conn.execute(
                    "UPDATE transactions SET flow_type = ?, other_side_id = ? "
                    "WHERE id = ? AND COALESCE(flow_type_manual, 0) = 0 AND other_side_id IS NULL",
                    (_paired_flow(row, other), other["account_id"], row["id"]),
                )
            made += 1
    return made
