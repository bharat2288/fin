"""Optional: run the Citi PDF parser over real statements on this machine.

Skipped unless FIN_STATEMENTS_DIR points at the statements root (folders like
"Citi Prestige ####", "Citi Checking ####", "UOB ...", "DBS ..." holding
YYYY-MM.pdf). Asserts aggregate properties only — never statement contents.
"""

import os
from pathlib import Path

import pytest

import parsers
import parse_citi_pdf

ROOT = os.environ.get("FIN_STATEMENTS_DIR")
pytestmark = pytest.mark.skipif(not ROOT, reason="FIN_STATEMENTS_DIR not set")


def _pdfs(prefix: str) -> list[Path]:
    if not ROOT:
        return []
    return sorted(
        pdf
        for folder in Path(ROOT).iterdir()
        if folder.is_dir() and folder.name.startswith(prefix)
        for pdf in folder.glob("*.pdf")
    )


@pytest.mark.parametrize("prefix,kind", [
    ("Citi Prestige", "credit_card"),
    ("Citi Rewards", "credit_card"),
    ("Citi Checking", "bank"),
])
def test_real_citi_statements_parse_and_reconcile(prefix, kind):
    pdfs = _pdfs(prefix)
    assert pdfs, f"no PDFs under {prefix}*"
    for pdf in pdfs:
        assert parse_citi_pdf.detect_citi_pdf(str(pdf)) == kind
        # Dispatch through the registry, as the upload route does. The parser
        # raises ValueError when rows don't reconcile to the statement totals.
        [stmt] = parsers.auto_detect_and_parse(str(pdf))
        assert stmt.statement_type == kind
        assert len(stmt.accounts) == 1
        assert all(t.card_info == stmt.accounts[0] for t in stmt.transactions)
        if kind == "credit_card":
            text = "\n".join(parse_citi_pdf._read_pages(str(pdf)))
            summary = parse_citi_pdf._card_summary(text)
            total = summary["previous"] + sum(t.amount_minor for t in stmt.transactions)
            assert total == summary["current"]


@pytest.mark.parametrize("prefix", ["UOB", "DBS"])
def test_real_non_citi_statements_not_claimed(prefix):
    pdfs = _pdfs(prefix)
    if not pdfs:
        pytest.skip(f"no {prefix} PDFs under FIN_STATEMENTS_DIR")
    claimed = [p for p in pdfs if parse_citi_pdf.detect_citi_pdf(str(p))]
    assert len(claimed) == 0
