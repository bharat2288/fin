"""Database initialization and helpers for fin."""

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import account_kind
import book_type
import history
import label_source

# The book beside the code, as on the laptop; FIN_DB_PATH names another
# (the Railway volume). Read once, at import.
DEFAULT_DB_PATH = Path(__file__).parent / "fin.db"
DB_PATH_VARIABLE = "FIN_DB_PATH"


def configured_db_path(environ=os.environ) -> Path:
    """The book FIN_DB_PATH names, or the one beside the code when it is unset."""
    value = (environ.get(DB_PATH_VARIABLE) or "").strip()
    return Path(value) if value else DEFAULT_DB_PATH


DB_PATH = configured_db_path()
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Initial merchant rules based on known data: (pattern, label, match_type).
# The label is the merchant's type, by its key (book_type.py), with book
# Household, unless SEED_LABELS says otherwise.
DEFAULT_MERCHANT_RULES = [
    # Groceries
    ("TANGLIN MARKET", "Groceries", "contains"),
    ("LITTLE FARMS", "Groceries", "contains"),
    ("CS FRESH", "Groceries", "contains"),
    ("FAIRPRICE", "Groceries", "contains"),
    ("COLD STORAGE", "Groceries", "contains"),
    ("COLES", "Groceries", "startswith"),
    ("ALDI", "Groceries", "startswith"),
    ("MARKS & SPENCER", "Groceries", "contains"),
    ("M & S", "Groceries", "startswith"),
    ("FOOD PANDA", "Groceries", "contains"),
    ("FP*FOOD PANDA", "Groceries", "contains"),
    ("REDMART", "Groceries", "contains"),
    ("SHENG SIONG", "Groceries", "contains"),
    ("GIANT", "Groceries", "startswith"),
    ("SCOOP WHOLEFOODS", "Groceries", "contains"),
    ("PARAGON MARKET", "Groceries", "contains"),
    # Dining
    ("DIN TAI FUNG", "Dining", "contains"),
    ("CULINA", "Dining", "contains"),
    ("SUBWAY", "Dining", "contains"),
    ("NET*SUBWAY", "Dining", "contains"),
    ("YA KUN", "Dining", "contains"),
    ("TOAST BOX", "Dining", "contains"),
    ("MCDONALD", "Dining", "contains"),
    ("STARBUCKS", "Dining", "contains"),
    ("CAFFE BEVIAMO", "Dining", "contains"),
    ("GOURMET SARAWAK", "Dining", "contains"),
    ("TORI-Q", "Dining", "contains"),
    ("NESPRESSO", "Dining", "contains"),
    ("BACHA COFFEE", "Dining", "contains"),
    ("KOPI", "Dining", "contains"),
    ("VIET TASTE", "Dining", "contains"),
    ("TWO MEN BAGEL", "Dining", "contains"),
    ("CHICHA SAN CHEN", "Dining", "contains"),
    ("OLD CHANG KEE", "Dining", "contains"),
    ("PROJECT ACAI", "Dining", "contains"),
    ("MR COCONUT", "Dining", "contains"),
    ("ALLPRESS ESPRESSO", "Dining", "contains"),
    ("IMPERIAL TREASURE", "Dining", "contains"),
    ("JOLLIBEAN", "Dining", "contains"),
    ("7-ELEVEN", "Dining", "contains"),
    ("CHEERS", "Dining", "startswith"),
    ("SUPERBIG", "Dining", "contains"),
    ("GRANDHYATT", "Dining", "contains"),
    ("HYDRATE", "Dining", "startswith"),
    ("COCOBELLA", "Dining", "contains"),
    ("ASSEMBLY GROUND", "Dining", "contains"),
    ("GELATIAMO", "Dining", "contains"),
    ("DEARBORN", "Dining", "contains"),
    ("FATTENED CALF", "Dining", "contains"),
    # Transport
    ("BUS/MRT", "Transport", "contains"),
    ("GRAB*", "Transport", "startswith"),
    ("GRAB ", "Transport", "startswith"),
    ("UBER", "Transport", "startswith"),
    ("COMFORT", "Transport", "startswith"),
    ("GOJEK", "Transport", "contains"),
    ("TESLA MOTORS", "Transport", "contains"),
    ("KIGO CHARGING", "Transport", "contains"),
    ("CHARGEPLUS", "Transport", "contains"),
    ("SP DIGITAL PL-EV", "Transport", "contains"),
    ("SHELL ", "Transport", "startswith"),
    ("SPC ", "Transport", "startswith"),
    ("PARKING.SG", "Transport", "contains"),
    ("NETS FLASHPAY", "Transport", "contains"),
    ("NETS AUTO TOP UP", "Transport", "contains"),
    # Shopping
    ("TAKASHIMAYA", "Shopping", "contains"),
    ("ZARA", "Shopping", "startswith"),
    ("SHOPEE", "Shopping", "contains"),
    ("LAZADA", "Shopping", "contains"),
    ("2C2*LAZADA", "Shopping", "contains"),
    ("AMAZON", "Shopping", "contains"),
    ("KINOKUNIYA", "Shopping", "contains"),
    ("WH SMITH", "Shopping", "contains"),
    ("WHS ", "Shopping", "startswith"),
    ("TARGET", "Shopping", "startswith"),
    ("H M HENNES", "Shopping", "contains"),
    ("LONGCHAMP", "Shopping", "contains"),
    ("ONITSUKA", "Shopping", "contains"),
    ("DAISO", "Shopping", "contains"),
    ("KINDLE SVCS", "Shopping", "contains"),
    # Health & Beauty
    ("GUARDIAN", "Health & Beauty", "startswith"),
    ("WATSONS", "Health & Beauty", "startswith"),
    ("AESOP", "Health & Beauty", "startswith"),
    ("M SPA", "Health & Beauty", "contains"),
    ("MECCA", "Health & Beauty", "startswith"),
    # Medical
    ("MOUNT ALVERNIA", "Medical", "contains"),
    ("SINGHEALTH", "Medical", "contains"),
    ("MOUNT E ORCHARD", "Medical", "contains"),
    ("KINDER CLINIC", "Medical", "contains"),
    ("ALPHA WOMEN", "Medical", "contains"),
    ("PATRICIA YUEN DERMATOL", "Medical", "contains"),
    ("TERRA MEDICAL", "Medical", "contains"),
    ("OSTEOPATHIC", "Medical", "contains"),
    # Entertainment
    ("NETFLIX", "Entertainment", "contains"),
    ("SPOTIFY", "Entertainment", "contains"),
    ("YOUTUBE", "Entertainment", "contains"),
    ("HBO", "Entertainment", "contains"),
    ("TESLA PREMIUM", "Entertainment", "contains"),
    # Utilities
    ("SINGTEL", "Utilities", "contains"),
    ("MYSINGTELAPP", "Utilities", "contains"),
    ("MYREPUBLIC", "Utilities", "contains"),
    ("SP GROUP", "Utilities", "contains"),
    ("SP GAS", "Utilities", "contains"),
    ("SP DIGITAL PL-UTILITIE", "Utilities", "contains"),
    ("1PASSWORD", "Utilities", "contains"),
    # Subscriptions
    ("ANTHROPIC", "Subscriptions", "contains"),
    ("CLAUDE.AI", "Subscriptions", "contains"),
    ("OPENAI", "Subscriptions", "contains"),
    ("CHATGPT", "Subscriptions", "contains"),
    ("NOTION LABS", "Subscriptions", "contains"),
    ("TRADINGVIEW", "Subscriptions", "contains"),
    ("REMNOTE", "Subscriptions", "contains"),
    ("MICROSOFT 365", "Subscriptions", "contains"),
    ("OURA", "Subscriptions", "contains"),
    ("GOOGLE PLAY", "Subscriptions", "contains"),
    ("TWITTER", "Subscriptions", "contains"),
    ("RUNPOD", "Subscriptions", "contains"),
    ("GOOGLE*GOOGLE ONE", "Subscriptions", "contains"),
    ("GOOGLE ONE", "Subscriptions", "contains"),
    ("JASPER.AI", "Subscriptions", "contains"),
    ("SPENDEE", "Subscriptions", "contains"),
    # Insurance
    ("MANULIFE", "Insurance", "contains"),
    ("AIA", "Insurance", "startswith"),
    ("PRUDENTIAL", "Insurance", "contains"),
    ("GREAT EASTERN", "Insurance", "contains"),
    ("SINGAPORE LIFE", "Insurance", "contains"),
    # Loan/EMI
    ("HP HPR", "Loan/EMI", "contains"),
    # Travel
    ("DUTY FREE", "Travel", "contains"),
    ("HEINEMANN", "Travel", "contains"),
    ("FLYSCOOT", "Travel", "contains"),
    ("AUSTRALIANETA", "Travel", "contains"),
    # Pet
    ("GOODWOOF", "Pet", "contains"),
    ("PET LOVERS CENTRE", "Pet", "contains"),
    # Fitness
    ("VIVE ACTIVE", "Fitness", "contains"),
    ("BFT", "Fitness", "startswith"),
    ("BARRYSBOOTCAMP", "Fitness", "contains"),
    ("BARRY'S", "Fitness", "contains"),
    ("SICC", "Fitness", "contains"),
    ("SINGAPORE ISLAND COUNTRY", "Fitness", "contains"),
    ("SINGAPORE ISLAND COUNT", "Fitness", "contains"),
    ("VIN GOLF", "Fitness", "contains"),
    ("BUKIT TIMAH GOLF", "Fitness", "contains"),
    ("ORCHID COUNTRY CLUB", "Fitness", "contains"),
    ("HIDDEN CASTLE GOLF", "Fitness", "contains"),
    ("UPLAY VENTURES", "Fitness", "contains"),
    ("PING ", "Fitness", "startswith"),
    # Kids
    ("MOTHERS WORK", "Kids", "contains"),
    ("MOTHER WORK", "Kids", "contains"),
    ("PRAMFOX", "Kids", "contains"),
    # Education
    ("BRILLIANT", "Education", "contains"),
    ("WELCH LABS", "Education", "contains"),
    # Business (Moom)
    ("GOOGLE*ADS", "Moom", "contains"),
    ("LOYALTYLION", "Moom", "contains"),
    ("ALIBABA.COM", "Moom", "contains"),
    ("XERO", "Moom", "contains"),
    ("MOOM", "Moom", "contains"),
    ("KLAVIYO", "Moom", "contains"),
    ("SHOPIFY", "Moom", "contains"),
    ("IWG MANAGEMENT", "Moom", "contains"),
    # Home
    ("DYSON", "Home", "contains"),
    ("HOME 360", "Home", "contains"),
    ("SP SONNO", "Home", "contains"),
    # Dining (additional)
    ("FATELICIOUS", "Dining", "contains"),
    ("365 JUICES", "Dining", "contains"),
    ("PEEPAL BY ZED", "Dining", "contains"),
    # Education (additional)
    ("PARCHMENT-UNIV", "Education", "contains"),
    # Other / catch-all for known-but-uncategorized
    ("H KONCEPTS", "Other", "contains"),
    ("VOOVOO", "Other", "contains"),
    ("NEO EMPIRE", "Other", "contains"),
    ("SINGAPORE246", "Other", "startswith"),
    ("SINGAPORE618", "Other", "startswith"),
    ("OTT MB", "Other", "startswith"),
]


# The PayNow payee wordings the import once read a type from when no rule
# knew the row (type-relationships 05), each made a merchant named from the
# wording, with a `contains` rule, its type (by key) and the type's usual
# book: once, at start (PAYNOW_SEED), so a rule the operator deletes stays
# deleted. Six more were already seed rules above (SINGAPORE LIFE,
# OSTEOPATHIC, TERRA MEDICAL, SICC, VIN GOLF, FATELICIOUS); the five that
# gave Other gave no type and were dropped, and so was AMAC (Home): four
# letters a `contains` rule would find inside other words (LAMACHI), where the
# old wording read it only on a PAYNOW row. The operator adds a longer one in
# Lists › Rules if it is wanted.
PAYNOW_WORDINGS = [
    ("CENTRAL PROVIDENT FUND BOARD", "Tax"),
    ("CPF VOLUNTARY CONTRIBUTIONS", "Tax"),
    ("INLAND REVENUE", "Tax"),
    ("SINGAPORE ISLAND", "Fitness"),
    ("SITOH SIEW KIM", "Kids"),
    ("LIMITLESS WELLNESS", "Fitness"),
]
# The marker (seeded_once) that the wordings were made rules on this book.
PAYNOW_SEED = "paynow-wordings"
# The marker that DEFAULT_MERCHANT_RULES were seeded on this book: once, at a
# new book's first start, so a default rule the operator deletes or rewords
# stays that way through every later start and deploy. A book that already
# holds rules was seeded by the starts before this marker existed, and gains
# only the marker. A default added to the list later reaches a new book only;
# one meant for existing books needs its own marker.
DEFAULT_RULES_SEED = "default-rules"

# --- The flow lists, as rules (fin-redesign 08, ADR-009) -----------------------
# Until 08 these were lists in flow.py the operator could not see or change.
# Each is a rule now, stating what its rows are, seeded once (FLOW_RULES_SEED):
# a new book at its first start, a book from before by the conversion step
# (convert_flow_rules.py). A rule the operator deletes or changes stays so.
#
# The household's own names: a row naming one is a move between the
# household's own accounts. (pattern, money out only). A transfer names its
# other side, but these name places fin keeps no statements for, as the list
# never named one; the operator names one by editing the rule. A person's name
# is never one (RUL-010): Maya, Shalini and Rahul Kale, and the "RK" short
# forms, the list held from June 2026, are not the household. Money in naming
# Kalesh Inc is the company's income, and money in naming Independent Reserve
# is a sale from the crypto holding: the classifier's own rules for receipts
# on the household's bank (flow.COMPANIES_PAYING_INCOME,
# flow.CAPITAL_SALE_SOURCES), so those two rules are for money out.
OWN_ACCOUNT_RULES = [
    ("SURI BHARAT", False),
    ("MILI KALE", False),
    ("KALESH INC", True),
    ("INDEPENDENT RESERVE", True),
]
OWN_ACCOUNT_RULES_PATTERNS = tuple(pattern for pattern, _ in OWN_ACCOUNT_RULES)
# The smallest payment out, in whole minor units: a rule for money out
# matches a row of this amount or more.
MONEY_OUT = 1
# The loan merchants: each rule of the merchant states that its rows repay
# the loan, a movement naming it, where the book holds both. Until 08 the
# classifier did this by the merchant's exact name. (merchant, loan account)
LOAN_MERCHANTS = (("UOB Home Loan", "UOB home loan"), ("Car Loan", "DBS auto loan"))
# Default rules for bills the household only ever pays, often by PayNow or
# FAST: stated as spending, so a payment to one is spending as it was before
# a rule stopped lifting the review hold (fin-redesign 06). Left out, waiting
# for the operator: a payee that also pays the household money (MANULIFE, AIA,
# PRUDENTIAL, GREAT EASTERN, SINGAPORE LIFE, SICC, the tax and CPF payees),
# since a rule that states spending says it of money back too; TESLA MOTORS,
# whose rule took a car's downpayment as spending; and SITOH SIEW KIM, a
# person's name.
SPENDING_PAYEES = (
    "TERRA MEDICAL",
    "OSTEOPATHIC",
    "SINGAPORE ISLAND COUNTRY",
    "VIN GOLF",
    "MOTHERS WORK",
    "FATELICIOUS",
)
# The marker that the flow lists were seeded as rules on this book.
FLOW_RULES_SEED = "flow-rules"
# Read no more (fin-redesign 08): own-account names the host once added to
# the list (flow.py read them from this variable). Each is a rule now, which
# the operator sees and changes in Lists › Rules; a person's name is never one
# (RUL-010). A start names the variable when it is still set.
RETIRED_ALIAS_VARIABLE = "FIN_OWN_ALIAS_OVERRIDES"

# Rule patterns too generic to be safe on PayNow and transfer traffic.
_GENERIC_TRANSFER_PATTERNS = {
    "PAYNOW",
    "PAYNOW TRANSFER",
    "ICT PAYNOW",
    "ICT PAYNOW TRANSFER",
    "TOP-UP TO PAYLAH",
    "TOP UP TO PAYLAH",
    "MAXED OUT FROM PAYLAH",
    "TRANSFER",
    "BANK TRANSFER",
    "I-BANK",
}


def normalize_pattern_text(text: str | None) -> str:
    """Normalize a rule pattern or description fragment for guard checks."""
    return re.sub(r"\s+", " ", (text or "").upper()).strip()


def is_generic_rule_pattern(pattern: str | None) -> bool:
    """Return True for reusable rules that are too generic to be safe."""
    normalized = normalize_pattern_text(pattern)
    if not normalized:
        return False
    if normalized in _GENERIC_TRANSFER_PATTERNS:
        return True
    if re.fullmatch(r"FT\d+[A-Z0-9:-]*", normalized):
        return True
    if normalized.startswith("DBSC-") and "I-BANK" in normalized:
        return True
    return False

# Seed labels that are not a Household type of the same name: (book, type).
# These are what the book and type conversion makes of the old categories of
# the same names: no type, left for the operator to place.
SEED_LABELS = {
    "Moom": ("Moom", None),
    "Other": ("Household", None),
    "Loan/EMI": ("Household", None),
}


class DatabaseNotConverted(Exception):
    """The database has not been through every conversion step (conversion.CHAIN).
    Nothing was changed."""


def _needs_converting(conn: sqlite3.Connection) -> bool:
    """Whether the database holds anything the app cannot serve until the
    conversion steps have run: the category tree, float amounts, accounts
    with no owner, rows that cannot name their other side or their printed
    statement, rows with no flow, or a change history without every tracked
    table and its triggers (history.has_shape). The expect-back records
    (personal-ledger 23) are tracked tables, so a book from before them reads
    as needing the change-history step again, which makes them; and a person's
    loan rows or opening figure not yet converted into expectations reads as
    needing the expect-back step (convert_expect_back.py), checked here by
    name, last, once the shape it reads is there; and rows with no book or
    type source columns read as needing the label-source step
    (convert_label_source.py), and rows still holding the one mark those two
    replaced as needing its retirement (retire_single_mark.py), and rules
    that cannot state a flow as needing the rule-flow step
    (convert_rule_flow.py), and a book whose flow lists were never seeded as
    rules as needing the flow-rules step (convert_flow_rules.py), read from
    its marker (FLOW_RULES_SEED). Each is read
    from the book's shape, never from what its rows hold, but expect-back's,
    which reads its record first, and flow-rules', its marker: nothing the
    operator does afterwards reads as a conversion still to run."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "transactions" not in tables:
        return False  # a new database
    tx_cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
    rule_cols = {r[1] for r in conn.execute("PRAGMA table_info(merchant_rules)")}
    return (
        "categories" in tables
        or "category_id" in tx_cols
        or "book" not in tx_cols
        or "amount_sgd" in tx_cols
        or "min_amount" in rule_cols
        or "max_amount" in rule_cols
        or "owner" not in {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}
        or "other_side_id" not in tx_cols
        or "printed" not in {r[1] for r in conn.execute("PRAGMA table_info(statements)")}
        or conn.execute("SELECT 1 FROM transactions WHERE flow_type IS NULL LIMIT 1").fetchone()
        is not None
        or not history.has_shape(conn)
        or _expect_back_pending(conn)
        or not {"book_source", "type_source"} <= tx_cols
        or label_source.RETIRED_MARK in tx_cols
        or not {"flow", "other_side_id"} <= rule_cols
        or not flow_rules_seeded(conn)
    )


def flow_rules_seeded(conn: sqlite3.Connection) -> bool:
    """Whether the flow lists were seeded as rules on this book (its marker)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'seeded_once'").fetchone():
        return False
    return conn.execute("SELECT 1 FROM seeded_once WHERE seed = ?", (FLOW_RULES_SEED,)).fetchone() is not None


def seed_flow_rules(conn: sqlite3.Connection) -> int:
    """Seed the flow lists as rules, once, and mark the book: the own-account
    rules (each on the merchant named from its pattern, made with no book or
    type when there is none, unless a rule with its pattern is already
    there), the loan merchants' rules, and the spending payees' default rules,
    where each still states no flow. Changes no row. The number of rules
    added; no commit. A new book's first start and the flow-rules conversion
    step both seed through this."""
    added = 0
    for pattern, money_out in OWN_ACCOUNT_RULES:
        added += _seed_rule(conn, pattern, "contains", None, None,
                            flow="transfer", min_amount_minor=MONEY_OUT if money_out else None)
    for merchant, loan in LOAN_MERCHANTS:
        service = conn.execute("SELECT id FROM services WHERE UPPER(name) = ?", (merchant.upper(),)).fetchone()
        # As the classifier named the loan: the older of two of the name.
        account = conn.execute(
            "SELECT id FROM accounts WHERE name = ? AND type = 'loan' AND owner = ? ORDER BY id LIMIT 1",
            (loan, account_kind.HOUSEHOLD),
        ).fetchone()
        if service and account:
            conn.execute(
                "UPDATE merchant_rules SET flow = 'movement', other_side_id = ?"
                " WHERE service_id = ? AND flow IS NULL",
                (account[0], service[0]),
            )
    for pattern in SPENDING_PAYEES:
        conn.execute(
            "UPDATE merchant_rules SET flow = 'expense' WHERE pattern = ? AND match_type = 'contains'"
            " AND flow IS NULL AND other_side_id IS NULL",
            (pattern,),
        )
    conn.execute("INSERT INTO seeded_once (seed) VALUES (?)", (FLOW_RULES_SEED,))
    return added


def _expect_back_pending(conn: sqlite3.Connection) -> bool:
    """Whether the expect-back step still has a person's line to convert:
    never once the book records the step ran (conversion.recorded)."""
    import convert_expect_back  # imported here: it imports this module

    return not convert_expect_back.STEP.is_applied(conn)


def refusal(conn: sqlite3.Connection, path) -> str:
    """The one message a start on an unconverted database gives: every step
    still to run, in order, and the one command that runs them."""
    import conversion

    # Every step, when none reads as waiting: convert_all skips those applied.
    waiting = conversion.not_applied(conn) or [name for name, _ in conversion.CHAIN]
    modules = dict(conversion.CHAIN)
    steps = "\n".join(f"  {n}. {name} ({modules[name]}.py)" for n, name in enumerate(waiting, 1))
    return (
        "fin will not start: this database has not been through every conversion step, "
        "and fin does not convert a database as it starts. Nothing was changed.\n"
        f"Steps still to run, in order:\n{steps}\n"
        "Run them all with this one command (each step backs the database up first):\n"
        f"  python convert_all.py {path}"
    )


def _refuse_unconverted(conn: sqlite3.Connection) -> None:
    """Stop before touching a database the conversion steps have not finished.

    Conversions of existing rows go through the conversion runner, behind a
    backup, and never happen here.
    """
    if _needs_converting(conn):
        raise DatabaseNotConverted(refusal(conn, DB_PATH))


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    """Context manager wrapping get_connection() for automatic cleanup."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Initialize the database schema and seed data.

    Safe to call multiple times: seeding skips what is already there.
    Refuses a database that has not been through the book and type conversions.
    """
    conn = get_connection()
    try:
        _refuse_unconverted(conn)
    except DatabaseNotConverted:
        conn.close()
        raise

    # Run schema (CREATE IF NOT EXISTS is safe to re-run)
    schema_sql = SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)

    # Every step reads as applied (the refusal above), so nothing is left for
    # the expect-back step: record it, so that an Unlink, an Undo of the mark
    # or a person figure later never reads as a conversion still to run.
    import convert_expect_back  # imported here: it imports this module

    convert_expect_back.record_if_applied(conn)
    conn.commit()

    # An entry a crash left open would record the seeding below as its own.
    history.close_left_open(conn)
    conn.commit()

    # The type list, from its one declaration, and each declared type's
    # settings where it has none yet (type-relationships 02), and the type
    # each of fin's own filings goes under (type-relationships 03).
    book_type.seed_types(conn)
    book_type.seed_settings(conn)
    book_type.seed_slots(conn)
    conn.commit()
    # By key: a seed rule still finds its type once the type is renamed.
    type_ids = book_type.keyed_ids(conn)

    # Seed merchant rules, once (DEFAULT_RULES_SEED); a pattern already there is skipped
    # Rules require service_id (service-centric model)
    added = 0
    if not conn.execute("SELECT 1 FROM seeded_once WHERE seed = ?", (DEFAULT_RULES_SEED,)).fetchone():
        if not conn.execute("SELECT 1 FROM merchant_rules LIMIT 1").fetchone():
            for pattern, label, match_type in DEFAULT_MERCHANT_RULES:
                book, type_name = SEED_LABELS.get(label, (book_type.DEFAULT_BOOK, label))
                added += _seed_rule(conn, pattern, match_type, book, type_ids[type_name] if type_name else None)
        conn.execute("INSERT INTO seeded_once (seed) VALUES (?)", (DEFAULT_RULES_SEED,))
    # The PayNow wordings, once: a start after the marker adds none back.
    if not conn.execute("SELECT 1 FROM seeded_once WHERE seed = ?", (PAYNOW_SEED,)).fetchone():
        for pattern, key in PAYNOW_WORDINGS:
            if key == book_type.NOT_PLACED or is_generic_rule_pattern(pattern):
                continue  # no type to give, or a pattern too generic to be a rule
            type_id = type_ids[key]
            added += _seed_rule(conn, pattern, "contains", book_type.proposed_book(conn, type_id), type_id)
        conn.execute("INSERT INTO seeded_once (seed) VALUES (?)", (PAYNOW_SEED,))
    # The flow lists as rules, once: a book from before reaches here only
    # once the flow-rules step has seeded them (the refusal above).
    if not flow_rules_seeded(conn):
        added += seed_flow_rules(conn)
    conn.commit()
    if added > 0:
        print(f"Added {added} new merchant rules")
    if os.environ.get(RETIRED_ALIAS_VARIABLE, "").strip():
        print(f"{RETIRED_ALIAS_VARIABLE} is set and no longer read: an own-account name is a rule "
              "now (Lists › Rules), stating its rows are a transfer")

    total_types = conn.execute(
        "SELECT COUNT(*) FROM types WHERE kind = ?", (book_type.SPENDING,)
    ).fetchone()[0]
    total_rules = conn.execute("SELECT COUNT(*) FROM merchant_rules").fetchone()[0]
    print(f"Database ready: {total_types} types, {total_rules} merchant rules")

    conn.close()


def _seed_rule(conn: sqlite3.Connection, pattern: str, match_type: str,
               book: str | None, type_id: int | None, *,
               flow: str | None = None, min_amount_minor: int | None = None) -> int:
    """A seed rule, unless one with its pattern is already there: on the
    merchant named from the pattern ("GRAB" → "Grab"), made with this book and
    type when there is none, stating `flow` when one is given, and for money
    of at least `min_amount_minor` only when that is. 1 when the rule was
    added, else 0; no commit."""
    if conn.execute("SELECT id FROM merchant_rules WHERE pattern = ?", (pattern,)).fetchone():
        return 0
    svc_name = pattern.title()
    svc = conn.execute(
        "SELECT id FROM services WHERE UPPER(name) = ?", (svc_name.upper(),)
    ).fetchone()
    if svc:
        svc_id = svc[0]
    else:
        svc_id = conn.execute(
            "INSERT INTO services (name, book, type_id) VALUES (?, ?, ?)",
            (svc_name, book, type_id),
        ).lastrowid
    conn.execute(
        "INSERT INTO merchant_rules (pattern, service_id, match_type, confidence, flow, min_amount_minor) "
        "VALUES (?, ?, ?, 'auto', ?, ?)",
        (pattern, svc_id, match_type, flow, min_amount_minor),
    )
    return 1


# --- Rule engine cache ---
# In-memory cache of merchant rules, invalidated on any rule CRUD.
# Single-process Flask app, so module-level state is safe.
_rules_cache: list[dict] | None = None


def invalidate_rules_cache() -> None:
    """Clear the cached rules. Call after any merchant_rules INSERT/UPDATE/DELETE."""
    global _rules_cache
    _rules_cache = None


def load_rules(conn: sqlite3.Connection) -> list[dict]:
    """Every rule in match order, read now (not the cache): priority, then
    the longest pattern, then the oldest rule. Each with its merchant's
    default book and type and its Check each row flag, and the flow and
    other side it states (flow.stated)."""
    # A book the conversion steps before rule-flow read has no flow on its
    # rules yet: each states none (convert_rule_flow.py).
    stating = {"flow", "other_side_id"} <= {r[1] for r in conn.execute("PRAGMA table_info(merchant_rules)")}
    rows = conn.execute(
        "SELECT mr.id, mr.pattern, mr.match_type, "
        "       mr.priority, mr.min_amount_minor, mr.max_amount_minor, "
        "       mr.service_id, mr.book_override, mr.type_override_id, "
        + ("mr.flow, mr.other_side_id, " if stating else "NULL AS flow, NULL AS other_side_id, ") +
        "       s.book, s.type_id, s.review_each_time "
        "FROM merchant_rules mr "
        "JOIN services s ON mr.service_id = s.id"
    ).fetchall()
    return sorted((dict(r) for r in rows), key=match_order)


def match_order(rule) -> tuple:
    """A rule's place in match order (a priority left empty reads as 0; a
    rule not yet added, id None, after every rule it ties with). One key, so
    the rules fin holds and the rules a proposed change would leave sort
    alike."""
    return (-(rule["priority"] or 0), -len(rule["pattern"]),
            rule["id"] is None, rule["id"] or 0)


def _get_rules(conn: sqlite3.Connection) -> list[dict]:
    """Return cached rules list, loading from DB on first call or after invalidation."""
    global _rules_cache
    if _rules_cache is None:
        _rules_cache = load_rules(conn)
    return _rules_cache


def rule_label(rule) -> tuple[str | None, int | None]:
    """The (book, type_id) a rule gives a row.

    A rule may override the merchant's type, its book, or both; whatever it
    does not override is the merchant's default. Where each came from is
    label_source.rule_sources.
    """
    return (
        rule["book_override"] or rule["book"],
        rule["type_override_id"] or rule["type_id"],
    )


# What match_merchant returns when no rule knows the description.
NO_MATCH = {
    "book": None,
    "type_id": None,
    "service_id": None,
    "review_each_time": False,
}


def match_merchant(
    description: str,
    conn: sqlite3.Connection,
    amount_minor: int | None = None,
) -> dict:
    """Match a transaction description to a merchant using merchant rules.

    Rules are sorted by priority DESC, then pattern length DESC (most specific first).
    Amount-conditional rules (min_amount_minor / max_amount_minor) only match if
    the transaction's amount, in whole minor units, falls within the range.

    Returns {book, type_id, service_id, review_each_time}; every
    value is empty when no rule matches. Book and type are the merchant's
    defaults unless the rule overrides them.
    """
    rule = first_rule(description, conn, amount_minor)
    if rule is None:
        return dict(NO_MATCH)
    book, type_id = rule_label(rule)
    return {
        "book": book,
        "type_id": type_id,
        "service_id": rule["service_id"],
        "review_each_time": bool(rule["review_each_time"]),
    }


def first_rule(
    description: str,
    conn: sqlite3.Connection,
    amount_minor: int | None = None,
) -> dict | None:
    """The rule that labels a description, the first in match order
    (priority, then the longest pattern), or None when no rule knows it.
    Its "id" is the merchant_rules row."""
    return pick_rule(_get_rules(conn), description, amount_minor)


def pick_rule(rules: list[dict], description: str, amount_minor: int | None = None) -> dict | None:
    """The first of `rules` (in match order) that labels a description, as
    first_rule picks among the rules fin holds: the preview asks it of the
    rules as a proposed change would leave them."""
    desc_upper = (description or "").upper()
    for rule in rules:
        pattern = rule["pattern"].upper()
        match_type = rule["match_type"]
        if match_type == "exact":
            matched = desc_upper == pattern
        elif match_type == "startswith":
            matched = desc_upper.startswith(pattern)
        elif match_type == "contains":
            matched = pattern in desc_upper
        else:
            matched = False
        if not matched:
            continue
        # Amount conditions (if set on the rule); with no amount given, an
        # amount-conditional rule never matches.
        if amount_minor is not None:
            if rule["min_amount_minor"] is not None and amount_minor < rule["min_amount_minor"]:
                continue
            if rule["max_amount_minor"] is not None and amount_minor > rule["max_amount_minor"]:
                continue
        elif rule["min_amount_minor"] is not None or rule["max_amount_minor"] is not None:
            continue
        return rule
    return None


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
