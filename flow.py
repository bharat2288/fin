"""Flow-type classifier (ADR v2).

Single source of truth for transaction economic role. Parsers call this
post-parse; backfill calls it over historical rows. Never called twice on
the same row.
"""

import os
import re
from dataclasses import dataclass, field


FLOW_TYPES = ("expense", "income", "transfer", "payment", "refund")

# Seed aliases for own-counterparty detection. Substring-matched against
# uppercased description. Augmented by account short_names + masked account
# refs at classifier context construction time.
OWN_ALIAS_SEED = (
    "SURI BHARAT",
    "MILI KALE",
    "KALESH INC",
    # Extended Kale family — classified as transfer, not income/expense
    "SHALINI KALE",
    "MAYA KALE",
    "RAHUL KALE",
    # Shortened PayNow variants (bordered with space/colon to avoid false positives)
    "To: RK ",
    "From: RK ",
    "TO: RK ",
    "FROM: RK ",
    # Own crypto-exchange account — remittances to/from it are own-account moves
    "INDEPENDENT RESERVE",
)

REFUND_KEYWORDS = ("CASH REBATE", "REBATE", "REFUND", "REVERSAL")
PAYLAH_TOPUP_MARKERS = ("TOP-UP TO PAYLAH", "TOP UP TO PAYLAH")
# Dashed statement-ref forms of own accounts whose master name stores the
# number undashed (e.g. Kalesh Inc 0725605300 appears as 072-560530-0:IB).
CURATED_INTERNAL_BANK_REFS = ("438-59169-9", "072-560530-0")
# Card payoffs by fixed statement wording, where no DBSC- ref is printed.
# Sign-gated: the bank side only ever pays out (positive), the card side only
# ever receives (negative). Bank-side wordings name the card issuer as payee,
# so they are contains-matched; card-side wordings are generic, so they are
# prefix-anchored. The DBS card side of an iBanking payoff prints only
# "BILL PAYMENT - DBS INTERNET/WIRELESS" (its ref sits on the next line), so
# it carries no card digits for rule 1 to match and lives here instead.
CARD_PAYOFF_OUTFLOW_MARKERS = ("PAYMENT TO CITI CREDIT CARD", "BILL PAYMENT MBK-UOB CARDS")
CARD_PAYOFF_INFLOW_PREFIXES = (
    "PAYMENT - ATM/INTERNET",
    "PAYMT THRU E-BANK/HOMEB/CYBERB",
    "BILL PAYMENT - DBS INTERNET/WIRELESS",
)
ACCOUNT_REF_RE = re.compile(r"\b\d{3}-\d{5,9}-\d\b")
LOCAL_ALIAS_SPLIT_RE = re.compile(r"[\n,;]+")


def _load_local_own_aliases() -> tuple[str, ...]:
    """Load private own-counterparty aliases from the local environment."""
    raw = os.environ.get("FIN_OWN_ALIAS_OVERRIDES", "")
    return tuple(alias.strip() for alias in LOCAL_ALIAS_SPLIT_RE.split(raw) if alias.strip())


@dataclass
class ClassifierContext:
    """Immutable inputs for classify_flow, built once per classification run."""

    own_aliases: tuple[str, ...] = field(default_factory=tuple)
    linked_cc_patterns: tuple[str, ...] = field(default_factory=tuple)
    owned_bank_refs: tuple[str, ...] = field(default_factory=tuple)


def _extract_bank_refs(text: str | None) -> tuple[str, ...]:
    """Extract masked/unmasked bank refs embedded in account display names."""
    if not text:
        return tuple()
    return tuple(match.group(0) for match in ACCOUNT_REF_RE.finditer(text.upper()))


def build_context(conn) -> ClassifierContext:
    """Build context from the live accounts master."""
    aliases: list[str] = [*OWN_ALIAS_SEED, *_load_local_own_aliases()]
    linked: list[str] = []
    bank_refs: set[str] = set(CURATED_INTERNAL_BANK_REFS)

    for row in conn.execute(
        "SELECT name, short_name, last_four, type FROM accounts WHERE status = 'active'"
    ).fetchall():
        for ref in _extract_bank_refs(row["name"]):
            bank_refs.add(ref)
        short = (row["short_name"] or "").upper()
        if short:
            aliases.append(short)
        last4 = (row["last_four"] or "").strip()
        if row["type"] == "credit_card" and last4:
            # e.g., "DBSC-{16-digit}" where last 4 == last_four
            linked.append(f"DBSC-%{last4}")  # placeholder; real match via fn
            linked.append(last4)  # bare last_four is also useful
        # masked style: "XXXX{last_four}"
        if last4:
            aliases.append(f"XXXX{last4}")

    return ClassifierContext(
        own_aliases=tuple(aliases),
        linked_cc_patterns=tuple(linked),
        owned_bank_refs=tuple(sorted(bank_refs)),
    )


def _matches_linked_cc(description: str, linked_cc_patterns: tuple[str, ...]) -> bool:
    """Detect a linked-CC payoff.

    Current form: description contains 'DBSC-' followed by digits ending
    in a known credit-card last_four. Broad enough for future bank patterns.
    """
    up = description.upper()
    for pat in linked_cc_patterns:
        if pat.startswith("DBSC-"):
            # "DBSC-%XXXX" sentinel — check that DBSC- + any digits ending in last4 appear
            last4 = pat.split("%", 1)[1]
            if "DBSC-" in up:
                # scan for DBSC-<digits> and confirm one of them ends with last4
                for m in re.finditer(r"DBSC-(\d{8,20})", up):
                    if m.group(1).endswith(last4):
                        return True
    return False


def _matches_card_payoff_wording(description: str, amount: int) -> bool:
    """Detect a card payoff by fixed wording from either side of the movement.

    A reversed or dishonoured payoff carries the same wording with the
    opposite sign; it is not an own-money credit, so it falls through.
    """
    up = " ".join(description.upper().split())
    if amount > 0:
        return any(marker in up for marker in CARD_PAYOFF_OUTFLOW_MARKERS)
    if amount < 0:
        return up.startswith(CARD_PAYOFF_INFLOW_PREFIXES)
    return False


def _matches_own_alias(description: str, own_aliases: tuple[str, ...]) -> bool:
    up = description.upper()
    return any(a.upper() in up for a in own_aliases if a)


def _looks_like_refund(description: str) -> bool:
    """A refund is known by its wording. Nothing a row is filed under makes
    one: no type is a refund, and a refund the wording does not show is a flow
    the operator sets by hand."""
    up = description.upper()
    return any(k in up for k in REFUND_KEYWORDS)


def _matches_known_transfer_rail(description: str, owned_bank_refs: tuple[str, ...]) -> bool:
    """Match narrow, reviewed transfer rails without swallowing real spend."""
    up = description.upper()

    if any(marker in up for marker in PAYLAH_TOPUP_MARKERS):
        return True

    if up.startswith("MEP ") and not up.startswith("MEP CHG "):
        return True

    if any(ref in up for ref in owned_bank_refs):
        if ":IB" in up or up.startswith("FT") or up.startswith("TRF FT") or " I-BANK" in up:
            return True

    return False


def classify_flow(facts: dict, ctx: ClassifierContext) -> str:
    """Return one of FLOW_TYPES.

    facts: {date, description, amount_minor}. The amount is in whole minor
    units; only its sign is read.
    """
    description = facts.get("description", "") or ""
    amount = facts["amount_minor"] or 0

    # 1. Linked-CC payoff (most specific form of own-endpoint movement)
    if _matches_linked_cc(description, ctx.linked_cc_patterns):
        return "payment"

    # 1b. Card payoff by fixed wording (non-DBS cards, and the DBS card-side
    #     credit that prints no card digits). Sits with rule 1 so payoff
    #     keeps beating own-alias and transfer rails, as the DBS form does.
    #     Its sign gate makes it disjoint from rule 2's keyword path: a
    #     reversal of a payoff has the opposite sign and falls through.
    if _matches_card_payoff_wording(description, amount):
        return "payment"

    # 2. Refund check BEFORE transfer, so a rebate from a known merchant
    #    isn't swallowed as a generic transfer. Refunds are always inflows.
    if amount < 0 and _looks_like_refund(description):
        return "refund"

    # 3. Own-counterparty movement (non-CC)
    if _matches_own_alias(description, ctx.own_aliases):
        return "transfer"

    # 4. Narrow reviewed transfer rails that are not merchant spend.
    if _matches_known_transfer_rail(description, ctx.owned_bank_refs):
        return "transfer"

    # 5. Remaining inflow = income
    if amount < 0:
        return "income"

    # 6. Default: outflow = expense
    return "expense"
