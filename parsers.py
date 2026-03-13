"""Parser registry — auto-detection and dispatch for statement files.

Each parser registers a detect function and parse function.
Adding a new bank/format is a single `register()` call.
"""

from pathlib import Path


# Registry: list of {ext, detect_fn, parse_fn, name}
# detect_fn(filepath) -> bool (or truthy)
# parse_fn(filepath) -> ParsedStatement | list[ParsedStatement]
_PARSERS: list[dict] = []


def register(name: str, ext: str, detect_fn, parse_fn):
    """Register a parser.

    Args:
        name: Human-readable parser name (e.g., "UOB PDF")
        ext: File extension this parser handles (".pdf", ".csv")
        detect_fn: Function(filepath) -> bool. Returns True if this parser can handle the file.
                   Use `None` for catch-all/fallback parsers (tried last).
        parse_fn: Function(filepath) -> ParsedStatement or list[ParsedStatement]
    """
    _PARSERS.append({
        "name": name,
        "ext": ext.lower(),
        "detect_fn": detect_fn,
        "parse_fn": parse_fn,
    })


def auto_detect_and_parse(filepath: str) -> list:
    """Auto-detect file format and parse.

    Tries registered parsers for the file's extension, in registration order.
    Parsers with detect_fn=None are tried last (fallback).

    Always returns a list of ParsedStatement objects.
    """
    path = Path(filepath)
    ext = path.suffix.lower()

    # Filter parsers by extension
    candidates = [p for p in _PARSERS if p["ext"] == ext]
    if not candidates:
        raise ValueError(f"Unsupported file format: {ext}")

    # Try parsers with detect functions first, then fallbacks (detect_fn=None)
    with_detect = [p for p in candidates if p["detect_fn"] is not None]
    fallbacks = [p for p in candidates if p["detect_fn"] is None]

    for parser in with_detect:
        if parser["detect_fn"](filepath):
            result = parser["parse_fn"](filepath)
            # Normalize to list
            return result if isinstance(result, list) else [result]

    # No detector matched — try fallback parsers
    for parser in fallbacks:
        result = parser["parse_fn"](filepath)
        return result if isinstance(result, list) else [result]

    raise ValueError(f"No parser could handle file: {path.name}")


def handle_vantage_split(statements: list) -> list:
    """Handle DBS Vantage MK/BS cardholder split.

    If both a BS-only export and MK+BS combined export are detected,
    cross-reference to tag each transaction as MK or BS.
    """
    # Find Vantage statements by account name
    vantage_combined = []
    other = []

    for stmt in statements:
        if not stmt.accounts:
            other.append(stmt)
            continue

        acct = stmt.accounts[0].upper()
        if "VANTAGE" in acct:
            vantage_combined.append(stmt)
        else:
            other.append(stmt)

    # If we have exactly 2 Vantage groups with different card numbers, split
    if len(vantage_combined) >= 2:
        by_card = {}
        for stmt in vantage_combined:
            card = stmt.accounts[0] if stmt.accounts else "unknown"
            if card not in by_card:
                by_card[card] = []
            by_card[card].append(stmt)

        if len(by_card) == 2:
            cards = list(by_card.keys())
            count0 = sum(len(s.transactions) for s in by_card[cards[0]])
            count1 = sum(len(s.transactions) for s in by_card[cards[1]])

            if count0 < count1:
                bs_card, combined_card = cards[0], cards[1]
            else:
                bs_card, combined_card = cards[1], cards[0]

            # Build BS fingerprint set
            bs_fingerprints = set()
            for stmt in by_card[bs_card]:
                for tx in stmt.transactions:
                    bs_fingerprints.add((tx.date, tx.description, tx.amount_sgd))

            # Tag combined transactions
            from parse_dbs import ParsedTransaction, ParsedStatement
            mk_txns = []
            bs_txns = []

            for stmt in by_card[combined_card]:
                for tx in stmt.transactions:
                    fp = (tx.date, tx.description, tx.amount_sgd)
                    if fp in bs_fingerprints:
                        bs_txns.append(tx)
                        bs_fingerprints.discard(fp)
                    else:
                        mk_txns.append(tx)

            # Create split statements
            combined_name = by_card[combined_card][0].accounts[0]
            mk_name = combined_name.replace(
                combined_name.split()[-1],
                combined_name.split()[-1] + " (MK)",
            )
            bs_name = combined_name.replace(
                combined_name.split()[-1],
                combined_name.split()[-1] + " (BS)",
            )

            for tx in mk_txns:
                tx.card_info = mk_name
            for tx in bs_txns:
                tx.card_info = bs_name

            mk_stmt = ParsedStatement(
                statement_type="credit_card",
                statement_date=by_card[combined_card][0].statement_date,
                accounts=[mk_name],
                filename="vantage_mk_split",
                transactions=mk_txns,
            )
            bs_stmt = ParsedStatement(
                statement_type="credit_card",
                statement_date=by_card[combined_card][0].statement_date,
                accounts=[bs_name],
                filename="vantage_bs_split",
                transactions=bs_txns,
            )

            return other + [mk_stmt, bs_stmt]

    # No split needed
    return other + vantage_combined


# ---------------------------------------------------------------------------
# Register built-in parsers
# ---------------------------------------------------------------------------

def _register_builtins():
    """Register all built-in parsers. Called once at import time."""

    # PDF parsers — order matters: specific detectors first, DBS as fallback
    from parse_uob import detect_uob_pdf, parse_uob_pdf
    register("UOB PDF", ".pdf", detect_fn=detect_uob_pdf, parse_fn=parse_uob_pdf)

    from parse_dbs import parse_statement as parse_dbs_pdf
    register("DBS PDF", ".pdf", detect_fn=None, parse_fn=parse_dbs_pdf)  # fallback

    # CSV parsers — Citi has a detector, DBS CSV is the fallback
    from parse_citi_csv import detect_citi_csv, parse_citi_csv
    register("Citi CSV", ".csv", detect_fn=detect_citi_csv, parse_fn=parse_citi_csv)

    from parse_dbs_csv import parse_csv as parse_dbs_csv
    register("DBS CSV", ".csv", detect_fn=None, parse_fn=parse_dbs_csv)  # fallback


_register_builtins()
