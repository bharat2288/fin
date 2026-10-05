"""Flow-type classifier (ADR v2).

Single source of truth for transaction economic role. Parsers call this
post-parse; backfill calls it over historical rows. Never called twice on
the same row.
"""

import os
import re
from dataclasses import dataclass, field

import account_kind

# The flow list: what a row is, economically. Declared here, once, with a
# plain description for each member; every writer of a flow validates against
# this declaration through `checked_flow`. (name, description).
FLOWS = (
    ("expense", "spending: money that left and was consumed. Whose it is, is the row's book"),
    ("income", "money the household earned or was given"),
    ("transfer", "a move between two of the household's own bank and card accounts"),
    ("payment", "a card payoff: the bank side or the card side of paying a card bill"),
    ("refund", "spending that came back; it reduces spending in its month"),
    ("movement", "money that became, or came from, something the household owns or is owed "
                 "outside its bank and card accounts: a loan, a holding, a company, a person"),
    ("review", "a bank transfer nobody has labelled yet; held out of spending until it is"),
)

FLOW_TYPES = tuple(name for name, _ in FLOWS)
MOVEMENT = "movement"
REVIEW = "review"
# The flows whose rows may name their other side: an account of any kind.
NAMES_OTHER_SIDE = ("movement", "transfer", "payment")


class UnknownFlow(ValueError):
    """A writer was handed a flow that is not in the declared list."""


def checked_flow(value) -> str:
    """A flow as a writer may store it: one of the declared flows."""
    if not isinstance(value, str) or value not in FLOW_TYPES:
        raise UnknownFlow(f"unknown flow: {value!r}")
    return value


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
# --- Rules that need to know the account a row is on ---------------------------
# They apply to rows on a household bank account only (ticket 05).

# A transfer or payee shape: wording a bank gives money sent to, or received
# from, a named party or account rather than a merchant. Contains-matched
# against the uppercased description.
TRANSFER_SHAPE_MARKERS = ("PAYNOW", "FAST PAYMENT", "TELEGRAPHIC TRANSFER", "I-BANK", ":IB")
TRANSFER_SHAPE_REF_RE = re.compile(r"^(TRF )?FT\d+")
# The bank's own wording for pay: a receipt carrying it is income.
SALARY_MARKERS = ("SALARY",)
# A receipt from this source is a capital sale: a movement out of the holding
# named. (wording, the holding's account name)
CAPITAL_SALE_SOURCES = (("INDEPENDENT RESERVE", "Crypto held outside fin"),)
# Every receipt from this company is income (its monthly payments).
COMPANIES_PAYING_INCOME = ("KALESH",)
# A PayNow receipt from this company pays the household back: a movement
# naming the company. (wording, the company's account name)
COMPANIES_PAYING_BACK = (("MOOM", "Moom"),)
PAYING_BACK_RAIL = "PAYNOW"
# A row of this merchant is a loan instalment: a movement naming the loan.
# (the merchant's name, the loan's account name)
LOAN_MERCHANTS = (("UOB Home Loan", "UOB home loan"), ("Car Loan", "DBS auto loan"))
# Receipts on a household rupee account (HDFC) that are not income. Each is
# a default; the operator can overturn any row. Matched against the
# description uppercased with every space taken out (_compact), on money
# received on a household bank account: pdfplumber reads HDFC's narrations
# with no spaces ("NEFTCR-...", "IBFDPREMATPRINCIPAL-..."), so the spaced
# and the unspaced forms both match.
#   - An own SGD->INR remittance: "NEFT CR-<IFSC>-<NAME>-<NAME>-<REF>(<REF>)
#     FAMILY EXPENSE/SAVINGS", sent through DBS, so the IFSC is a DBS Bank
#     India branch's (DBSS0 and six more). The routing code alone is enough;
#     the purpose wording usually rides with it. It is one leg of a
#     conversion whose other leg is on the household's SGD account; ruling
#     5's spread counts it once both legs name each other. Pair matching
#     pairs only rows in one currency, and no rule knows the SGD leg's
#     wording, so neither leg can be labelled automatically: the rupee leg
#     waits on the review list, where the operator labels it (and the SGD
#     leg) with the other side, never income.
OWN_REMITTANCE_RE = re.compile(r"^NEFTCR-DBSS0[A-Z0-9]{6}(?![A-Z0-9])")
OWN_REMITTANCE_FLOW = REVIEW
#   - A fixed deposit closed early pays its principal back: money coming back
#     from something the household owns, a movement. Its interest ("IB FD
#     PREMAT INT PAID", "INTEREST PAID TILL") stays income.
FD_PRINCIPAL_MARKERS = ("FDPREMATPRINCIPAL",)
#   - A mutual-fund redemption ("RTGS CR-... REDEMPTION A/C-...") is a
#     holding sold, but investment holdings are not in the ledger yet (ledger
#     ticket 02): it waits on the review list.
REDEMPTION_PREFIX, REDEMPTION_MARKER = "RTGSCR", "REDEMPTION"
# Funds transfers and NEFT receipts from family members ("IB FUNDS TRANSFER
# CR", "NEFT CR" from another bank) are income or gifts (ruling 01): no rule
# here.

# The kinds of account a rule above may name as an other side.
RULE_OTHER_SIDE_KINDS = ("loan", "holding", "company")

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
    # Household accounts a rule may name as an other side: {account name: id}.
    other_sides: dict = field(default_factory=dict)
    # Loan merchants: {the merchant's service id: the loan's account id}.
    loan_merchants: dict = field(default_factory=dict)


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

    # Only a bank account or a card is an own-account endpoint. A holding, a
    # loan, a company or a person is never an alias: "Car" sits inside "CARD".
    for row in conn.execute(
        "SELECT name, short_name, last_four, type FROM accounts"
        " WHERE status = 'active' AND type IN (?, ?)",
        account_kind.STATEMENT_KINDS,
    ).fetchall():
        for ref in _extract_bank_refs(row["name"]):
            bank_refs.add(ref)
        short = (row["short_name"] or "").upper()
        if short:
            aliases.append(short)
        last4 = (row["last_four"] or "").strip()
        if row["type"] == "card" and last4:
            # e.g., "DBSC-{16-digit}" where last 4 == last_four
            linked.append(f"DBSC-%{last4}")  # placeholder; real match via fn
            linked.append(last4)  # bare last_four is also useful
        # masked style: "XXXX{last_four}"
        if last4:
            aliases.append(f"XXXX{last4}")

    # Where two accounts share a name, the older one is the one named.
    other_sides = {
        row["name"]: row["id"]
        for row in conn.execute(
            "SELECT id, name FROM accounts WHERE owner = ? AND type IN (?, ?, ?) ORDER BY id DESC",
            (account_kind.HOUSEHOLD, *RULE_OTHER_SIDE_KINDS),
        ).fetchall()
    }
    loan_merchants = {}
    for merchant, loan in LOAN_MERCHANTS:
        service = conn.execute(
            "SELECT id FROM services WHERE UPPER(name) = ?", (merchant.upper(),)
        ).fetchone()
        if service and loan in other_sides:
            loan_merchants[service["id"]] = other_sides[loan]

    return ClassifierContext(
        own_aliases=tuple(aliases),
        linked_cc_patterns=tuple(linked),
        owned_bank_refs=tuple(sorted(bank_refs)),
        other_sides=other_sides,
        loan_merchants=loan_merchants,
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


def _on_household_bank(facts: dict) -> bool:
    return (
        facts.get("account_kind") == "bank"
        and facts.get("account_owner") == account_kind.HOUSEHOLD
    )


def _has_transfer_shape(description: str) -> bool:
    up = " ".join(description.upper().split())
    return any(m in up for m in TRANSFER_SHAPE_MARKERS) or bool(TRANSFER_SHAPE_REF_RE.match(up))


def _named_other_side(facts: dict, ctx: ClassifierContext) -> int | None:
    """The account a rule names as a row's other side: the loan of a loan
    merchant, or for money received on a household bank account the holding
    it was sold from or the company paying the household back."""
    if facts.get("service_id") in ctx.loan_merchants:
        return ctx.loan_merchants[facts["service_id"]]
    if not _on_household_bank(facts):
        return None
    up = (facts.get("description", "") or "").upper()
    for wording, holding in CAPITAL_SALE_SOURCES:
        if wording in up:
            return ctx.other_sides.get(holding)
    if any(wording in up for wording in COMPANIES_PAYING_INCOME):
        return None
    for wording, company in COMPANIES_PAYING_BACK:
        if wording in up and PAYING_BACK_RAIL in up:
            return ctx.other_sides.get(company)
    return None


def _ledger_rule(facts: dict, ctx: ClassifierContext, received: bool) -> str | None:
    """The flow the ledger's own rules give a row, or None when none knows it:
    a loan instalment is a movement; on a household bank account, pay and a
    company's monthly payments are income, and a capital sale or a company
    paying the household back is a movement."""
    if facts.get("service_id") in ctx.loan_merchants:
        return MOVEMENT
    if not (received and _on_household_bank(facts)):
        return None
    up = (facts.get("description", "") or "").upper()
    if any(marker in up for marker in SALARY_MARKERS):
        return "income"
    if _named_other_side(facts, ctx) is not None:
        return MOVEMENT
    if any(wording in up for wording in COMPANIES_PAYING_INCOME):
        return "income"
    return None


def _compact(text: str) -> str:
    """Uppercased with every space taken out (as parse_hdfc._compact)."""
    return "".join(text.split()).upper()


def _rupee_receipt_rule(facts: dict, received: bool) -> str | None:
    """The flow a receipt on a household bank account gets from the rupee
    rules above, or None when none knows it."""
    if not (received and _on_household_bank(facts)):
        return None
    up = _compact(facts.get("description", "") or "")
    if OWN_REMITTANCE_RE.match(up):
        return OWN_REMITTANCE_FLOW
    if any(marker in up for marker in FD_PRINCIPAL_MARKERS):
        return MOVEMENT
    if up.startswith(REDEMPTION_PREFIX) and REDEMPTION_MARKER in up:
        return REVIEW
    return None


def classify_row(facts: dict, ctx: ClassifierContext) -> tuple[str, int | None]:
    """Return (flow, the account id of the row's other side or None)."""
    flow = classify_flow(facts, ctx)
    return flow, (_named_other_side(facts, ctx) if flow == MOVEMENT else None)


def classify_flow(facts: dict, ctx: ClassifierContext) -> str:
    """Return one of FLOW_TYPES.

    facts: {date, description, amount_minor} and, where the caller knows them:
      account_kind, account_owner — the account the row is on;
      service_id — the merchant a rule gave the row;
      labelled — whether a rule or the payee wording gave it a merchant or a type.
    Without these the rules that need them do not apply. The amount is in
    whole minor units; only its sign is read.
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

    # 2b. The ledger's own rules. Before the own-alias rule: two of the
    #     sources they know are aliases.
    known = _ledger_rule(facts, ctx, received=amount < 0)
    if known:
        return known

    # 2c. Rupee receipts that are not income: an own remittance from SGD, a
    #     fixed deposit's principal, a fund redemption. Before the own-alias
    #     rule: a remittance names the household's own people.
    known = _rupee_receipt_rule(facts, received=amount < 0)
    if known:
        return known

    # 3. Own-counterparty movement (non-CC)
    if _matches_own_alias(description, ctx.own_aliases):
        return "transfer"

    # 4. Narrow reviewed transfer rails that are not merchant spend.
    if _matches_known_transfer_rail(description, ctx.owned_bank_refs):
        return "transfer"

    # 4b. A transfer on a household bank account that nothing above knows and
    #     no merchant rule labelled waits for the operator.
    if _on_household_bank(facts) and not facts.get("labelled") and _has_transfer_shape(description):
        return REVIEW

    # 5. Remaining inflow = income
    if amount < 0:
        return "income"

    # 6. Default: outflow = expense
    return "expense"
